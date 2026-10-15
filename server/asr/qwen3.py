"""Qwen3-ASR over HTTP, served by llama-server."""

from __future__ import annotations

import base64
import threading
import numpy as np

from .. import config, perf
from ..config import (
    QWEN3_ASR_URL,
    SAMPLE_RATE,
)
from .base import (
    HEAD_CHARS,
    TAIL_CHARS,
    WAV_HEADER_BYTES,
    _wav_bytes,
    usage_tokens,
)
from .text_filters import (
    format_qwen3_prompt,
    is_hallucination,
    parse_qwen3_asr,
)


class Qwen3AsrEngine:
    """Qwen3-ASR over HTTP, served by a second llama-server.

    Same interface as AsrEngine so the pipeline cannot tell them apart, and the
    switch between them is one environment variable.
    """

    def __init__(self):
        slots = int(getattr(config, "QWEN3_ASR_SLOTS", 2))
        self._sem = threading.Semaphore(slots)
        self._client_lock = threading.Lock()
        self._client = None
        print(f"ASR loading Qwen3-ASR via {QWEN3_ASR_URL} (backend=qwen3, slots={slots})")

    def _get_client(self):
        import httpx
        with self._client_lock:
            if self._client is None or getattr(self._client, "is_closed", False):
                kwargs = {
                    "timeout": getattr(httpx, "Timeout", lambda x: x)(120.0),
                }
                if hasattr(httpx, "Limits"):
                    kwargs["limits"] = httpx.Limits(max_keepalive_connections=10, max_connections=20)
                self._client = httpx.Client(**kwargs)
            return self._client

    def transcribe(self, audio: np.ndarray, language: str | None) -> tuple[str, str | None, float]:
        if audio.size < 1600:
            return "", language, 0.0

        wav = _wav_bytes(audio)
        lang_prompt = format_qwen3_prompt(language)

        payload = {
            "model": "qwen3-asr",
            "temperature": 0,
            "max_tokens": config.QWEN3_ASR_MAX_TOKENS,
            "messages": [{"role": "user", "content": [
                {"type": "input_audio", "input_audio": {
                    "data": base64.b64encode(wav).decode("ascii"),
                    "format": "wav"}},
                {"type": "text", "text": lang_prompt},
            ]}],
        }
        with self._sem:
            client = self._get_client()
            try:
                reply = client.post(f"{QWEN3_ASR_URL}/v1/chat/completions", json=payload)
                reply.raise_for_status()
                body = reply.json()
            except Exception as e:
                print(f"[Qwen3AsrEngine] HTTP post error to {QWEN3_ASR_URL}: {e}")
                with self._client_lock:
                    if self._client is not None:
                        try:
                            self._client.close()
                        except Exception:
                            pass
                        self._client = None
                return "", language, 0.0
        choice = body["choices"][0]
        raw = choice["message"]["content"]

        if choice.get("finish_reason") == "length":
            truncated = usage_tokens(body)
            head = raw[:HEAD_CHARS].replace("\n", " ")
            tail = raw[-TAIL_CHARS:].replace("\n", " ")
            perf.emit("asr", "truncated", chars=len(raw), tokens=truncated,
                      audio_ms=int(len(wav) * 1000 / (SAMPLE_RATE * 2)) - WAV_HEADER_BYTES,
                      cap=config.QWEN3_ASR_MAX_TOKENS,
                      head=head, tail=tail)
            print(f"ASR truncated at {truncated} tokens (cap "
                  f"{config.QWEN3_ASR_MAX_TOKENS}); dropped; head={head!r}")
            return "", language, 0.0

        text, detected, confidence = parse_qwen3_asr(raw)
        if is_hallucination(text):
            return "", detected, confidence
        return text, detected, confidence

    def close(self):
        with self._lock:
            if getattr(self, "_client", None) is not None:
                if hasattr(self._client, "close"):
                    try:
                        self._client.close()
                    except Exception:
                        pass
                self._client = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

