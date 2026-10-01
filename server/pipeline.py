"""Per-connection audio session: VAD -> ASR -> streamed translation."""

from __future__ import annotations

import asyncio
import json

import httpx
import numpy as np

from .asr import AsrEngine
from .config import CONTEXT_SENTENCES, PARTIAL_STABLE_REPEAT, SAMPLE_RATE
from .translate import stream_translation
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
        self.job_id = 0
        self.warned_translate = False
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=2.0))
        self.meter_samples = 0
        self.meter_peak = 0.0
        self.translate_task: asyncio.Task | None = None
        self.pending_translate: tuple[str, bool] | None = None
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
        for kind, audio in events:
            if kind == "start":
                self.last_text = ""
                self.stable = 0
                self.last_translated = ""
                messages.append({"type": "segment_start"})
            elif kind in ("partial", "end") and audio is not None and audio.size:
                self.jobs.put_nowait((kind, audio))
        return messages

    async def close(self) -> None:
        task = self.translate_task
        if task and not task.done():
            task.cancel()
        await self.client.aclose()

    async def worker(self, send) -> None:
        try:
            while True:
                kind, audio = await self.jobs.get()
                try:
                    if kind == "partial":
                        kind, audio = self._latest(kind, audio)
                    await self._run(kind, audio, send)
                except Exception as exc:
                    print(f"pipeline error: {exc}")
                finally:
                    self.jobs.task_done()
        except asyncio.CancelledError:
            return

    def _latest(self, kind: str, audio: np.ndarray) -> tuple[str, np.ndarray]:
        while kind == "partial":
            try:
                nxt, nxt_audio = self.jobs.get_nowait()
            except asyncio.QueueEmpty:
                break
            self.jobs.task_done()
            kind, audio = nxt, nxt_audio
        return kind, audio

    async def _run(self, kind: str, audio: np.ndarray, send) -> None:
        text, detected, prob = await asyncio.to_thread(self.asr.transcribe, audio, self.language)
        if detected and not self.language_locked and prob >= 0.5:
            self.language = detected
            if kind == "end":
                self.language_locked = True
        if not text:
            return
        final = kind == "end"
        await send({
            "type": "asr",
            "text": text,
            "lang": self.language or detected,
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
        if text == self.last_translated and not final:
            return
        self.last_translated = text
        self._schedule_translate(text, final, send)

    def _schedule_translate(self, text: str, final: bool, send) -> None:
        if self.translate_task and not self.translate_task.done():
            self.pending_translate = (text, final)
            return
        self.job_id += 1
        job = self.job_id
        self.translate_task = asyncio.create_task(self._translate_job(text, final, job, send))

    async def _translate_job(self, text: str, final: bool, job: int, send) -> None:
        try:
            await self._translate(text, final, job, send)
        finally:
            pending = self.pending_translate
            self.pending_translate = None
            self.translate_task = None
            if pending and pending[0] != text:
                self._schedule_translate(pending[0], pending[1], send)

    async def _translate(self, text: str, final: bool, job: int, send) -> None:
        if self.api_error:
            if not self.warned_translate:
                self.warned_translate = True
                await send({"type": "status", "level": "warn", "message": self.api_error})
            return
        acc = ""
        try:
            async for piece in stream_translation(
                text, self.language, self.context, self.client, self.api
            ):
                if job != self.job_id:
                    return
                if piece.startswith("\r"):
                    acc = piece[1:]
                else:
                    acc += piece
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
        if job != self.job_id:
            return
        self.last_translation = acc
        if self._caption_accepts(acc):
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


async def emit_json(ws, payload: dict) -> None:
    from .caption_window import update_caption

    update_caption(payload)
    await ws.send(json.dumps(payload, ensure_ascii=False))
