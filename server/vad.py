"""Speech gate. Prefers Silero ONNX; falls back to RMS energy."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .config import (
    SAMPLE_RATE,
    SILERO_ONNX,
    VAD_MIN_SILENCE_MS,
    VAD_MIN_SPEECH_MS,
    VAD_THRESHOLD,
    PARTIAL_INTERVAL_MS,
)

FRAME = 512  # 32ms at 16kHz
MAX_UTTERANCE_MS = 12_000
NON_SPEECH_GAIN = 0.0
# An utterance only closes after 400ms of silence, but the words are already
# over after the first pause. Emitting one extra partial as soon as the pause
# reaches 150ms hands the tail to ASR 250ms earlier, and the closing event
# then reuses that text instead of transcribing the same audio again.
EARLY_END_SILENCE_MS = 150
# Once five seconds of speech are in, a200ms pause is already a sentence
# boundary; holding out for 400ms only delays the closing events, and it caps
# how long one recogniser call can grow.
SOFT_CUT_SPEECH_MS = 5_000
SOFT_CUT_SILENCE_MS = 200
# Silence frames are zeroed (NON_SPEECH_GAIN), so the tail can be cut back to
# a120ms release: whisper still hears the last word, the buffer is shorter.
VAD_SPEECH_PAD_MS = 120


class _Silero:
    def __init__(self, path: Path):
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, 64), dtype=np.float32)

    def prob(self, frame: np.ndarray) -> float:
        chunk = frame.reshape(1, -1).astype(np.float32)
        packed = np.concatenate([self.context, chunk], axis=1)
        outputs = self.session.run(
            None,
            {
                "input": packed,
                "state": self.state,
                "sr": np.array(SAMPLE_RATE, dtype=np.int64),
            },
        )
        self.state = outputs[1]
        self.context = packed[:, -64:]
        return float(np.asarray(outputs[0]).reshape(-1)[0])

    def reset(self) -> None:
        self.state.fill(0)
        self.context.fill(0)


class _Energy:
    def prob(self, frame: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(np.square(frame)) + 1e-10))
        # Map typical speech RMS (~0.02-0.1) onto 0..1 around the Silero threshold.
        return max(0.0, min(1.0, (rms - 0.008) / 0.04))

    def reset(self) -> None:
        return None


_VAD = None


def load_vad():
    global _VAD
    if _VAD is not None:
        return _VAD
    if SILERO_ONNX.exists() and SILERO_ONNX.stat().st_size > 100_000:
        try:
            _VAD = _Silero(SILERO_ONNX)
            print(f"VAD: silero {SILERO_ONNX}")
            return _VAD
        except Exception as exc:
            print(f"VAD: silero failed ({exc}), using energy")
    else:
        print("VAD: energy (silero onnx missing)")
    _VAD = _Energy()
    return _VAD


class UtteranceSplitter:
    """Turn a PCM stream into start / partial / end events.

    Every event is ``(kind, audio, flag)``. For a partial the flag marks the
    early closing partial emitted at ``EARLY_END_SILENCE_MS``; for an end it
    says that partial already holds the whole utterance, so the session may
    reuse that text instead of running ASR on the same audio a second time.
    """

    def __init__(self, engine=None):
        self.engine = engine if engine is not None else load_vad()
        self.pending = np.zeros(0, dtype=np.float32)
        self.utterance: list[np.ndarray] = []
        self.in_speech = False
        self.speech_ms = 0
        self.silence_ms = 0
        self.since_partial_ms = 0
        self.early_partial = False
        self.frame_ms = FRAME * 1000 / SAMPLE_RATE

    def reset(self) -> None:
        self.pending = np.zeros(0, dtype=np.float32)
        self.utterance = []
        self.in_speech = False
        self.speech_ms = 0
        self.silence_ms = 0
        self.since_partial_ms = 0
        self.early_partial = False
        self.engine.reset()

    def push(self, samples: np.ndarray) -> list[tuple[str, np.ndarray | None, bool]]:
        if samples.size == 0:
            return []
        self.pending = np.concatenate([self.pending, samples])
        events: list[tuple[str, np.ndarray | None, bool]] = []
        while self.pending.size >= FRAME:
            frame = self.pending[:FRAME]
            self.pending = self.pending[FRAME:]
            speech = self.engine.prob(frame) >= VAD_THRESHOLD
            if speech:
                if not self.in_speech:
                    self.in_speech = True
                    self.speech_ms = 0
                    self.silence_ms = 0
                    self.since_partial_ms = 0
                    self.early_partial = False
                    self.utterance = []
                    events.append(("start", None, False))
                resumed = self.silence_ms > 0
                self.utterance.append(frame)
                self.speech_ms += self.frame_ms
                self.silence_ms = 0
                if resumed:
                    # Speech came back before the utterance closed, so the early
                    # partial only covered part of the sentence: never reuse it.
                    self.early_partial = False
                self.since_partial_ms += self.frame_ms
                if self.speech_ms >= MAX_UTTERANCE_MS:
                    events.append(("end", self._take(), self.early_partial))
                elif (
                    self.speech_ms >= VAD_MIN_SPEECH_MS
                    and self.since_partial_ms >= PARTIAL_INTERVAL_MS
                ):
                    self.since_partial_ms = 0
                    events.append(("partial", self._copy(), False))
            elif self.in_speech:
                self.utterance.append(frame * NON_SPEECH_GAIN)
                self.silence_ms += self.frame_ms
                self.since_partial_ms += self.frame_ms
                if (
                    not self.early_partial
                    and EARLY_END_SILENCE_MS <= self.silence_ms < VAD_MIN_SILENCE_MS
                    and self.speech_ms >= VAD_MIN_SPEECH_MS
                    and self.since_partial_ms >= PARTIAL_INTERVAL_MS
                ):
                    # The words stopped; only the closing silence is missing.
                    self.early_partial = True
                    self.since_partial_ms = 0
                    events.append(("partial", self._trim(self._copy()), True))
                if self.silence_ms >= VAD_MIN_SILENCE_MS or (
                    self.speech_ms >= SOFT_CUT_SPEECH_MS
                    and self.silence_ms >= SOFT_CUT_SILENCE_MS
                ):
                    if self.speech_ms >= VAD_MIN_SPEECH_MS:
                        events.append(("end", self._trim(self._copy()), self.early_partial))
                    self._drop()
        return events

    def flush(self) -> list[tuple[str, np.ndarray | None, bool]]:
        if self.in_speech and self.speech_ms >= VAD_MIN_SPEECH_MS and self.utterance:
            return [("end", self._trim(self._copy()), self.early_partial)]
        self._drop()
        return []

    def _copy(self) -> np.ndarray:
        if not self.utterance:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self.utterance)

    def _take(self) -> np.ndarray:
        audio = self._trim(self._copy())
        self._drop()
        return audio

    def _trim(self, audio: np.ndarray) -> np.ndarray:
        """Drop the zeroed trailing silence, keep a short release for the last word."""
        if audio.size == 0:
            return audio
        nz = np.nonzero(audio)[0]
        if nz.size == 0:
            return audio
        keep = int(nz[-1]) + int(SAMPLE_RATE * VAD_SPEECH_PAD_MS / 1000)
        return audio[:keep] if keep < audio.size else audio

    def _drop(self) -> None:
        self.utterance = []
        self.in_speech = False
        self.speech_ms = 0
        self.silence_ms = 0
        self.since_partial_ms = 0
        self.early_partial = False
