"""Per-connection audio session: VAD -> ASR -> streamed translation."""

from __future__ import annotations

import asyncio

import httpx
import numpy as np

from . import perf
from .asr import AsrEngine
from .config import CONTEXT_SENTENCES, PARTIAL_STABLE_REPEAT, SAMPLE_RATE
from .translate import collapse_translation_repeats, stream_translation
from .vad import UtteranceSplitter


class Session:
    def __init__(self, asr: AsrEngine):
        self.asr = asr
        self.vad = UtteranceSplitter()
        self.jobs: asyncio.Queue = asyncio.Queue()
        self.language: str | None = None
        self.language_locked = False
        self.context: list[tuple[str, str]] = []
        self.last_text = ""
        self.stable = 0
        self.last_translated = ""
        self.last_translation = ""
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
        self.context.clear()
        self.shown_translation = ""
        self.shown_source = ""
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
            if final and self.last_translation:
                # The closing event still has to reach whoever is recording, even
                # though there is nothing new to translate. Re-send what is
                # already on screen; the caption window reads it as the same
                # line and drops it, so this costs one packet and no work.
                await send(
                    {
                        "type": "translation",
                        "text": self.last_translation,
                        "final": True,
                    }
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
        # Nothing downstream reads `final`; it exists so a recorder can tell an
        # utterance apart from a mid-sentence piece. `_caption_accepts` is False
        # here for almost every job, because the streaming loop already put the
        # same text on screen, so without this the final message is never sent.
        if final or self._caption_accepts(acc):
            self.shown_translation = acc
            self.shown_source = text
            await send({"type": "translation", "text": acc, "final": final})
        if final and acc.strip():
            self._remember(text, acc)

    def _caption_accepts(self, acc: str) -> bool:
        shown = self.shown_translation
        if not acc or acc == shown:
            return False
        if shown and len(acc) < len(shown) and shown.startswith(acc):
            return False
        return True

    def _remember(self, source: str, translation: str) -> None:
        cleaned = translation.strip()
        src = source.strip()
        if not cleaned or not src:
            return
        if self.context and self.context[-1] == (src, cleaned):
            return
        self.context.append((src, cleaned))
        del self.context[:-CONTEXT_SENTENCES]
