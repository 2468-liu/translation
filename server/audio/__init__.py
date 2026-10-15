"""Audio pipeline package: Loopback capture, VAD endpointing, and adaptive acoustic enhancement."""

from __future__ import annotations

from ..audio_enhancer import AudioEnhancer
from ..loopback import run_loopback
from ..process_loopback import ProcessLoopbackManager
from ..vad import (
    FRAME,
    SAMPLE_RATE,
    UtteranceSplitter,
    apply_partial_interval,
    audio_model_tuning,
    cinema_tuning,
    energy_vad,
    silero_vad,
    whisper_tuning,
)

__all__ = [
    "AudioEnhancer",
    "run_loopback",
    "ProcessLoopbackManager",
    "UtteranceSplitter",
    "FRAME",
    "SAMPLE_RATE",
    "apply_partial_interval",
    "cinema_tuning",
    "whisper_tuning",
    "audio_model_tuning",
    "energy_vad",
    "silero_vad",
]
