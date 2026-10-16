"""Common utilities, audio byte serialization, and telemetry constants for ASR."""

from __future__ import annotations

import io
import wave as wavemod
import numpy as np

from ..config import SAMPLE_RATE

# A canonical wav header is 44 bytes. _wav_bytes writes a fixed-layout one, and the
# audio_ms figure for the truncation event is derived from the payload length.
WAV_HEADER_BYTES = 44

# How much of a dropped ASR response the truncation event carries.
HEAD_CHARS = 80
TAIL_CHARS = 40


class AsrEngine:
    """Base interface for ASR recognition engines."""

    def transcribe(self, audio: np.ndarray, language: str | None = None) -> tuple[str, str | None, float]:
        raise NotImplementedError

    def reset_state(self) -> None:
        """Reset internal buffers, connection pools, or transient state on sustained silence."""
        pass

    def close(self) -> None:
        """Release underlying engine resources."""
        pass


def _pcm_bytes(audio: np.ndarray) -> bytes:
    """The 16-bit PCM on its own, without a container.

    Factorised because the prefix property has to be testable. The bytes must be a
    pure function of the samples and a longer clip must start with a shorter one.
    """
    clipped = np.clip(audio, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


def _wav_bytes(audio: np.ndarray) -> bytes:
    """The PCM in a real wav container, which is what the request must carry."""
    buf = io.BytesIO()
    with wavemod.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(_pcm_bytes(audio))
    return buf.getvalue()


def usage_tokens(body: dict) -> int:
    """completion_tokens, or -1 when the server left usage out.

    -1 rather than 0 because a missing count and a count of zero are different
    findings: one means the server is not reporting, the other means it generated
    nothing.
    """
    try:
        return int(body["usage"]["completion_tokens"])
    except (KeyError, TypeError, ValueError):
        return -1
