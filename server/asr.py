"""faster-whisper wrapper. CPU int8 so the 4GB GPU stays with the translator."""

from __future__ import annotations

import re
import threading

import numpy as np

from .config import (
    ASR_BEAM_SIZE,
    ASR_COMPUTE_TYPE,
    ASR_CPU_THREADS,
    ASR_DEVICE,
    ASR_FALLBACK_DIR,
    ASR_MODEL_DIR,
)

_HALLUCINATIONS = {
    "thanks for watching",
    "thank you for watching",
    "thanks for listening",
    "subtitles by the amara.org community",
    "subscribe to my channel",
    "please subscribe",
}


def is_hallucination(text: str) -> bool:
    normalized = re.sub(r"[\s\.\,\!\?。！？…'\"“”]+", " ", text).strip().lower()
    if not normalized:
        return True
    if normalized in _HALLUCINATIONS:
        return True
    return False


def _model_dir() -> str:
    if (ASR_MODEL_DIR / "model.bin").exists():
        return str(ASR_MODEL_DIR)
    if (ASR_FALLBACK_DIR / "model.bin").exists():
        return str(ASR_FALLBACK_DIR)
    raise FileNotFoundError(
        f"ASR model missing. Run: python scripts/download_models.py (expected {ASR_MODEL_DIR / 'model.bin'})"
    )


class AsrEngine:
    def __init__(self):
        from faster_whisper import WhisperModel

        path = _model_dir()
        print(f"ASR loading {path} device={ASR_DEVICE} compute={ASR_COMPUTE_TYPE}")
        try:
            self.model = WhisperModel(
                path,
                device=ASR_DEVICE,
                compute_type=ASR_COMPUTE_TYPE,
                cpu_threads=ASR_CPU_THREADS,
            )
        except Exception as exc:
            if ASR_DEVICE == "cpu":
                raise
            print(f"ASR {ASR_DEVICE} failed ({exc}); using cpu int8")
            self.model = WhisperModel(
                path,
                device="cpu",
                compute_type="int8",
                cpu_threads=ASR_CPU_THREADS,
            )
        self._lock = threading.Lock()

    def transcribe(self, audio: np.ndarray, language: str | None) -> tuple[str, str | None, float]:
        if audio.size < 1600:
            return "", language, 0.0
        with self._lock:
            segments, info = self.model.transcribe(
                audio,
                beam_size=ASR_BEAM_SIZE,
                vad_filter=False,
                language=language,
                condition_on_previous_text=False,
                temperature=0.0,
                without_timestamps=True,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
            detected = info.language or language
            probability = float(info.language_probability or 0.0)
        if is_hallucination(text):
            return "", detected, probability
        return text, detected, probability
