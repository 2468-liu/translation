"""Per-connection audio session: VAD -> ASR -> streamed translation."""

from __future__ import annotations

import asyncio

import httpx
import numpy as np

from . import perf
from .asr import AsrEngine
from .audio_mt import (
    chinese_share,
    looks_untranslated,
    stream_audio_translation as audio_stream,
    chinese_share,
)
from .config import (
    AUDIO_CHAT_TEMPLATE_KWARGS,
    AUDIO_MAX_TOKENS,
    AUDIO_MODEL_URL,
    CONTEXT_SENTENCES,
    PARTIAL_STABLE_REPEAT,
    SAMPLE_RATE,
)
from .translate import (
    collapse_caption_repeats,
    collapse_translation_repeats,
    stream_translation,
)
from .vad import UtteranceSplitter, audio_model_tuning, whisper_tuning


class Session:
    """One audio connection's work.

    Two shapes, chosen by whether ``asr`` is given:

    * whisper + translation model (main): the VAD's partials feed the
      recogniser, and the text feeds a text-to-text translator.
    * audio-native model (gemma-audio branch): there is no recogniser and no text
      model. Each closed fragment's audio is translated in one pass, and the VAD
      runs with limits sized for that model's audio window.

    Keeping both in one class rather than two means the WebSocket protocol, the
    caption window and the perf probes stay identical, so the two branches can be
    measured against each other with the same tooling.
    """

    # Defaults on the class, not only in __init__. A field initialised in one
    # reset path and read in another raises AttributeError the first time the
    # other path runs, which is how three replays of the audio arm produced no
    # translations at all: _last_audio_final was set in reset_for_media and read
    # on a session that had never been reset.
    _last_audio_final: str = ""
    shown_translation: str = ""
    shown_source: str = ""

    def __init__(self, asr: AsrEngine | None = None, tuning=None):
        self.audio_native = asr is None
        self.asr = asr
        if tuning is None:
            tuning = audio_model_tuning() if self.audio_native else whisper_tuning()
        self.vad = UtteranceSplitter(tuning=tuning)
        self.jobs: asyncio.Queue = asyncio.Queue()
        self.language: str | None = None
        self.language_locked = False
        self.context: list[tuple[str, str]] = []
        self.last_text = ""
        self.stable = 0
        self.last_translated = ""
        self.last_translation = ""
        # Which source text last_translation belongs to, plus a closing event that
        # arrived while that text was still being translated. The source is what
        # makes reuse safe: while a job is in flight, last_translation still holds
        # the PREVIOUS sentence's text.
        self.last_translation_src = ""
        self.pending_final_src = ""
        # Text of the early closing partial (vad.EARLY_END_SILENCE_MS). Only set
        # once that partial has actually been transcribed, so the closing end
        # event can reuse it instead of recognising the same audio twice.
        self._partial_result: tuple[str, str | None, float] | None = None
        self.job_id = 0
        self.warned_translate = False
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=2.0))
        self.meter_samples = 0
        self.meter_peak = 0.0
        self.translate_task: asyncio.Task | None = None
        self.pending_translate: tuple[str, bool, str | None, int] | None = None
        self.media_generation = 0
        self.shown_translation = ""
        self.shown_source = ""
        self.api: dict | None = None
        self.api_error = ""

    def set_api(self, data: dict) -> None:
        enabled = bool(data.get("apiEnabled"))
        url = str(data.get("apiUrl") or "").strip()
        model = str(data.get("apiModel") or "").strip()
        key = str(data.get("apiKey") or "").strip()
        self.api_error = ""
        if not enabled:
            self.api = None
        elif not url or not model:
            self.api = None
            self.api_error = "外部接口要填地址和模型名。"
        else:
            self.api = {"enabled": True, "url": url, "key": key, "model": model}
        self.warned_translate = False

    def set_language(self, language: str | None) -> None:
        if language and language != "auto":
            self.language = language
            self.language_locked = True
        else:
            self.language = None
            self.language_locked = False

    def _asr_language(self) -> str | None:
        return self.language if self.language_locked else None

    def reset_for_media(self) -> None:
        self.media_generation += 1
        self.job_id += 1
        task = self.translate_task
        if task and not task.done():
            task.cancel()
        self.translate_task = None
        self.pending_translate = None
        self.vad.reset()
        self.last_text = ""
        self.stable = 0
        self.last_translated = ""
        self.last_translation = ""
        self._partial_result = None
        self.last_translation = ""
        self.last_translation_src = ""
        self.pending_final_src = ""
        self.context.clear()
        self.shown_translation = ""
        self.shown_source = ""
        # The previous fragment's finished line, so the model handing it back is
        # recognised as a repeat rather than captioned as a new sentence.
        self._last_audio_final = ""
        if not self.language_locked:
            self.language = None

    def push_pcm(self, payload: bytes) -> list[dict]:
        if not payload:
            return []
        samples = np.frombuffer(payload, dtype=np.int16).astype(np.float32) / 32768.0
        self._meter(samples)
        return self._collect(self.vad.push(samples))

    def _meter(self, samples: np.ndarray) -> None:
        self.meter_samples += samples.size
        if samples.size:
            self.meter_peak = max(self.meter_peak, float(np.abs(samples).max()))
        if self.meter_samples >= SAMPLE_RATE * 2:
            print(f"audio in: {self.meter_samples / SAMPLE_RATE:.1f}s peak={self.meter_peak:.4f}")
            self.meter_samples = 0
            self.meter_peak = 0.0

    def flush(self) -> list[dict]:
        return self._collect(self.vad.flush())

    def _collect(self, events) -> list[dict]:
        messages = []
        for kind, audio, flag in events:
            if kind == "start":
                self.last_text = ""
                self.stable = 0
                self.last_translated = ""
                self._partial_result = None
                messages.append({"type": "segment_start"})
            elif kind in ("partial", "end") and audio is not None and audio.size:
                backlog = self.jobs.qsize()
                self.jobs.put_nowait((kind, audio, perf.mono(), bool(flag)))
                perf.emit(
                    "pipeline",
                    "enqueue",
                    kind=kind,
                    qlen=backlog,
                    audio_ms=audio.size * 1000.0 / SAMPLE_RATE,
                    early=int(bool(flag)) if kind == "partial" else 0,
                    reuse=int(bool(flag)) if kind == "end" else 0,
                )
        return messages

    async def close(self) -> None:
        task = self.translate_task
        if task and not task.done():
            task.cancel()
        await self.client.aclose()

    async def worker(self, send) -> None:
        try:
            while True:
                kind, audio, queued_at, reuse = await self.jobs.get()
                try:
                    if kind == "partial":
                        kind, audio, queued_at, reuse = self._latest(
                            kind, audio, queued_at, reuse
                        )
                    await self._run(
                        kind, audio, send, perf.ms(queued_at), self.jobs.qsize(), reuse
                    )
                except Exception as exc:
                    print(f"pipeline error: {exc}")
                finally:
                    self.jobs.task_done()
        except asyncio.CancelledError:
            return

    def _latest(
        self, kind: str, audio: np.ndarray, queued_at: float, reuse: bool
    ) -> tuple[str, np.ndarray, float, bool]:
        """Skip stale partials; log each one so the discarded ASR work is visible.

        The early closing partial is never skipped: the end event that follows
        it reuses its text, so dropping it would cost two ASR calls instead of
        one and lose the head start it was emitted for.
        """
        while kind == "partial" and not reuse:
            try:
                nxt, nxt_audio, nxt_at, nxt_reuse = self.jobs.get_nowait()
            except asyncio.QueueEmpty:
                break
            self.jobs.task_done()
            perf.emit("pipeline", "drop", kind=kind, qwait=perf.ms(queued_at))
            kind, audio, queued_at, reuse = nxt, nxt_audio, nxt_at, nxt_reuse
        return kind, audio, queued_at, reuse

    async def _run(
        self,
        kind: str,
        audio: np.ndarray,
        send,
        qwait: float = 0.0,
        qlen: int = 0,
        reuse: bool = False,
    ) -> None:
        generation = self.media_generation
        if self.audio_native:
            await self._run_audio_native(kind, audio, send, qwait, qlen, generation)
            return
        text, detected, prob = "", None, 0.0
        asr_ms = 0.0
        if kind == "end" and reuse and self._partial_result is not None:
            # The early partial at 150ms of silence already recognised this
            # utterance; its audio differs only in trailing silence.
            text, detected, prob = self._partial_result
            perf.emit(
                "pipeline",
                "reuse",
                kind=kind,
                audio_ms=audio.size * 1000.0 / SAMPLE_RATE,
                chars=len(text),
            )
        else:
            t_asr = perf.mono()
            text, detected, prob = await asyncio.to_thread(
                self.asr.transcribe, audio, self._asr_language()
            )
            asr_ms = perf.ms(t_asr)
            perf.emit(
                "pipeline",
                "asr",
                kind=kind,
                qwait=qwait,
                qlen=qlen,
                asr=asr_ms,
                audio_ms=audio.size * 1000.0 / SAMPLE_RATE,
                chars=len(text),
                lang=detected or "",
            )
            # Remember it only for the early partial, and only if there is
            # something to reuse.
            if kind == "partial" and reuse and text:
                self._partial_result = (text, detected, prob)
            else:
                self._partial_result = None
        if generation != self.media_generation:
            perf.emit("pipeline", "stale", reason="media", kind=kind)
            return
        current_language = self.language if self.language_locked else detected
        if detected and not self.language_locked and kind == "end":
            self.language = detected
        if not text:
            return
        final = kind == "end"
        await send({
            "type": "asr",
            "text": text,
            "lang": current_language,
            "prob": round(prob, 3),
            "final": final,
        })
        if text == self.last_text:
            self.stable += 1
        else:
            self.last_text = text
            self.stable = 1
        should_translate = final or self.stable >= PARTIAL_STABLE_REPEAT
        if not should_translate:
            return
        if text == self.last_translated:
            if final:
                if self.last_translation_src == text and self.last_translation:
                    # This sentence's translation already finished: re-send what
                    # is on screen. The caption window reads it as the same line
                    # and drops it, so this costs one packet and no work.
                    await send(
                        {
                            "type": "translation",
                            "text": self.last_translation,
                            "final": True,
                        }
                    )
                else:
                    # Still streaming. last_translation belongs to the previous
                    # sentence, so sending it now would label that text as this
                    # sentence's final and the caption window would swap the
                    # current line back to it. Remember the request and send the
                    # real text when the job finishes.
                    self.pending_final_src = text
                    perf.emit(
                        "pipeline",
                        "promote",
                        chars=len(text),
                        held=self.last_translation_src or "-",
                    )
            return
        self.last_translated = text
        self._schedule_translate(text, final, current_language, generation, send)

    def _schedule_translate(
        self, text: str, final: bool, language: str | None, generation: int, send
    ) -> None:
        if self.translate_task and not self.translate_task.done():
            self.pending_translate = (text, final, language, generation)
            perf.emit("pipeline", "defer", chars=len(text), final=final)
            return
        self.job_id += 1
        job = self.job_id
        self.translate_task = asyncio.create_task(
            self._translate_job(text, final, language, generation, job, send)
        )

    async def _translate_job(
        self,
        text: str,
        final: bool,
        language: str | None,
        generation: int,
        job: int,
        send,
    ) -> None:
        try:
            await self._translate(text, final, language, generation, job, send)
        finally:
            pending = self.pending_translate
            self.pending_translate = None
            self.translate_task = None
            if pending and pending[0] != text and pending[3] == self.media_generation:
                self._schedule_translate(pending[0], pending[1], pending[2], pending[3], send)

    async def _translate(
        self,
        text: str,
        final: bool,
        language: str | None,
        generation: int,
        job: int,
        send,
    ) -> None:
        if self.api_error:
            if not self.warned_translate:
                self.warned_translate = True
                await send({"type": "status", "level": "warn", "message": self.api_error})
            return
        acc = ""
        started_at = perf.mono()
        ttft_ms = None
        pieces = 0
        try:
            async for piece in stream_translation(
                text, language, self.context, self.client, self.api
            ):
                if ttft_ms is None:
                    # First chunk back from the translation server.
                    ttft_ms = perf.ms(started_at)
                    perf.emit(
                        "pipeline",
                        "ttft",
                        ttft=ttft_ms,
                        chars=len(text),
                        final=final,
                        job=job,
                    )
                pieces += 1
                if generation != self.media_generation or job != self.job_id:
                    return
                if piece.startswith("\r"):
                    acc = piece[1:]
                else:
                    acc += piece
                acc = collapse_translation_repeats(acc)
                if not self._caption_accepts(acc):
                    continue
                self.shown_translation = acc
                self.shown_source = text
                await send({"type": "translation", "text": acc, "final": False})
        except httpx.HTTPError as exc:
            if not self.warned_translate:
                self.warned_translate = True
                response = getattr(exc, "response", None)
                code = f" {response.status_code}" if response is not None else ""
                if self.api:
                    message = f"外部接口失败{code}。检查地址、密钥和模型名。"
                else:
                    message = f"翻译服务未启动（{exc.__class__.__name__}）。先运行 scripts/start.ps1 里的 llama-server。"
                await send({"type": "status", "level": "warn", "message": message})
            return
        except Exception as exc:
            if not self.warned_translate:
                self.warned_translate = True
                await send({
                    "type": "status",
                    "level": "warn",
                    "message": f"翻译失败（{exc.__class__.__name__}）",
                })
            return
        finally:
            if ttft_ms is not None:
                perf.emit(
                    "pipeline",
                    "ttotal",
                    total=perf.ms(started_at),
                    ttft=ttft_ms,
                    pieces=pieces,
                    chars=len(text),
                    final=final,
                    job=job,
                )
        if generation != self.media_generation or job != self.job_id:
            return
        self.last_translation = acc
        self.last_translation_src = text
        # A closing event that arrived while this job was streaming is answered
        # here, with the text that actually belongs to it.
        promote = self.pending_final_src == text
        if promote:
            self.pending_final_src = ""
        # Nothing downstream reads `final`; it exists so a recorder can tell an
        # utterance apart from a mid-sentence piece. `_caption_accepts` is False
        # here for almost every job, because the streaming loop already put the
        # same text on screen, so without this the final message is never sent.
        if final or promote or self._caption_accepts(acc):
            self.shown_translation = acc
            self.shown_source = text
            await send(
                {"type": "translation", "text": acc, "final": bool(final or promote)}
            )
        if (final or promote) and acc.strip():
            self._remember(text, acc)

    async def _run_audio_native(
        self,
        kind: str,
        audio: np.ndarray,
        send,
        qwait: float,
        qlen: int,
        generation: int,
    ) -> None:
        """One audio fragment straight through the model that hears it.

        There is no transcript, so there is nothing to send as ``asr`` and
        nothing to hold back for a later ``translation``: the model's output is
        the translation, and it streams, so every chunk goes to the caption as it
        arrives.
        """
        final = kind == "end"
        # The preceding lines go in as text context. Their audio is not replayed:
        # it would be re-translated, and re-hearing it costs more prompt tokens
        # than the words it would add.
        context = list(self.context[-CONTEXT_SENTENCES:])
        started = perf.mono()
        sent_any = False
        accepted: list[str] = []
        try:
            async for piece in audio_stream(audio, self.client, context):
                acc = collapse_translation_repeats(piece)
                if not acc or not self._caption_accepts(acc):
                    continue
                # Nothing goes to the caption until the fragment is done and the
                # text has cleared the checks below. On this path a partial is a
                # guess about audio the model has finished hearing, and the
                # guesses that reach the screen are the ones that get held,
                # revised and overwritten.
                accepted.append(acc)
                sent_any = True
        except Exception as exc:  # network, model load, malformed stream
            perf.emit("audio", "error", kind=kind, err=type(exc).__name__)
            if not self.warned_translate:
                self.warned_translate = True
                await send(
                    {
                        "type": "status",
                        "level": "warn",
                        "message": f"音频翻译失败（{exc.__class__.__name__}）。检查 llama-server 是否带 --mmproj 启动。",
                    }
                )
            return
        if not sent_any:
            # Silence, music, or a fragment the model had nothing to say about.
            # Saying so matters: otherwise the caption simply freezes and the gap
            # looks like a stall rather than an empty segment.
            perf.emit(
                "audio",
                "empty",
                kind=kind,
                total=perf.ms(started),
                audio_ms=audio.size * 1000.0 / SAMPLE_RATE,
                qwait=qwait,
                qlen=qlen,
            )
        if generation != self.media_generation:
            return

        acc = self._settle_audio_text(accepted)
        if acc:
            self._last_audio_final = acc
        if acc:
            self.shown_translation = acc
            self.shown_source = ""
            # Stream the line as it grows. There is no separate final step: the
            # model has already heard the whole fragment, so its output is the
            # last word on the subject.
            for step in self._growth_steps(acc):
                await send({"type": "translation", "text": step, "final": final})
        elif sent_any:
            perf.emit("audio", "rejected", kind=kind, chars=len(accepted[-1]))

        if final:
            self.last_text = ""
            self._remember("", self.shown_translation)

    def _caption_accepts(self, acc: str) -> bool:
        shown = self.shown_translation
        if not acc or acc == shown:
            return False
        if shown and len(acc) < len(shown) and shown.startswith(acc):
            return False
        return True

    @staticmethod
    def _growth_steps(text: str) -> list[str]:
        """The line in the few steps a viewer can read as it arrives.

        One packet per token made the caption window redraw 500 times for a
        single sentence, which is why the audio path used to measure 114
        characters a second on screen. This path's whole fragment arrives in
        about 130ms, so there is nothing to pace: a handful of steps is enough to
        look like text arriving rather than a block appearing.
        """
        length = len(text)
        if length <= 24:
            return [text]
        steps = 4
        return [text[: length * (i + 1) // steps] for i in range(steps)]

    def _settle_audio_text(self, accepted: list[str]) -> str:
        """The fragment's finished line, or nothing if it should not be shown.

        Three checks, each answering a failure the logs actually contain. The
        model transcribes the English instead of translating it -- reproducibly,
        the same two fragments in every run -- so a line that is mostly Latin
        letters is dropped rather than captioned. It repeats a clause until the
        fragment ends, so a line that has looped is cut at the loop. And it can
        hand back the previous fragment's line, which the caption would then
        display as though it were new.
        """
        if not accepted:
            return ""
        text = accepted[-1]
        if looks_untranslated(text):
            perf.emit("audio", "untranslated", ratio=round(chinese_share(text), 3))
            return ""
        trimmed = collapse_caption_repeats(text)
        if trimmed != text:
            perf.emit("audio", "looped", before=len(text), after=len(trimmed))
        if trimmed and trimmed == self._last_audio_final:
            perf.emit("audio", "repeat_final")
            return ""
        return trimmed

    def _remember(self, source: str, translation: str) -> None:
        cleaned = translation.strip()
        src = source.strip()
        if not cleaned or not src:
            return
        if self.context and self.context[-1] == (src, cleaned):
            return
        self.context.append((src, cleaned))
        del self.context[:-CONTEXT_SENTENCES]
