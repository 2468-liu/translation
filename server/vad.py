"""Speech gate. Prefers Silero ONNX; falls back to RMS energy."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import perf
from .config import (
    SAMPLE_RATE,
    SILERO_ONNX,
    VAD_MIN_SILENCE_MS,
    VAD_MIN_SPEECH_MS,
    VAD_THRESHOLD,
    PARTIAL_INTERVAL_MS,
    GEMMA_MAX_UTTERANCE_MS,
    GEMMA_MIN_SILENCE_MS,
    GEMMA_SOFT_CUT_SILENCE_MS,
    GEMMA_SOFT_CUT_SPEECH_MS,
    GEMMA_VAD_SPEECH_PAD_MS,
)

FRAME = 512  # 32ms at 16kHz
# Set by the caller. The whisper pipeline and the audio-native model have
# different limits, and picking the wrong one is either a wasted long context
# (whisper stitches, so it tolerates 30s) or a hard failure (the audio model
# does not). See build_tuning().
MAX_UTTERANCE_MS = 12_000
NON_SPEECH_GAIN = 0.0

# Silence runs are binned rather than counted in total, because the question the
# total cannot answer is "what would a different threshold have done". Bins are
# chosen so that each threshold the project has ever set -- 150, 200, 400, 700,
# 1000 ms -- falls on a boundary, and every run lands in exactly one bin whether
# or not it went on to close an utterance. A run that closed a segment and a run
# that did not are the same kind of event for this purpose.
#
# The upper edge of each bin is exclusive, so a 200ms pause lands in 100-200 and a
# 1000ms pause in 700-1000 rather than in the bin named after it.
SILENCE_BINS_MS = (100, 200, 300, 400, 700, 1000)


def _silence_bin(ms: float) -> str:
    if ms < SILENCE_BINS_MS[0]:
        return "lt100"
    for edge in SILENCE_BINS_MS:
        if ms < edge:
            return f"lt{edge}"
    return f"ge{SILENCE_BINS_MS[-1]}"
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


class Tuning:
    """The cut parameters, which differ by downstream model.

    Kept as one object so a pipeline states its limits in one place instead of
    relying on module constants that whichever import ran last happened to set.
    """

    def __init__(
        self,
        *,
        min_silence_ms: int,
        max_utterance_ms: int,
        speech_pad_ms: int,
        early_end_ms: int,
        soft_cut_speech_ms: int,
        soft_cut_silence_ms: int,
        partial_interval_ms: int,
        name: str = "",
    ):
        self.min_silence_ms = min_silence_ms
        self.max_utterance_ms = max_utterance_ms
        self.speech_pad_ms = speech_pad_ms
        self.early_end_ms = early_end_ms
        self.soft_cut_speech_ms = soft_cut_speech_ms
        self.soft_cut_silence_ms = soft_cut_silence_ms
        self.partial_interval_ms = partial_interval_ms
        self.name = name


def whisper_tuning() -> Tuning:
    """Limits for whisper, which re-reads fragments and stitches context."""
    return Tuning(
        min_silence_ms=VAD_MIN_SILENCE_MS,
        max_utterance_ms=MAX_UTTERANCE_MS,
        speech_pad_ms=VAD_SPEECH_PAD_MS,
        early_end_ms=EARLY_END_SILENCE_MS,
        soft_cut_speech_ms=SOFT_CUT_SPEECH_MS,
        soft_cut_silence_ms=SOFT_CUT_SILENCE_MS,
        partial_interval_ms=_effective_partial_interval(),
        name="whisper",
    )


# The partial cadence can be changed while a video is playing, so the value the
# tuning is built from is not the constant. Without this the alternative is
# editing config.py, restarting the service and replaying -- which cannot answer
# the question being asked, because comparing two runs from memory is not the
# same as switching while watching.
#
# A module global rather than an env var: an env var is read once at import, and
# the point is to change it after that. The constant stays as the default so a
# fresh process behaves exactly as before.
_PARTIAL_INTERVAL_OVERRIDE: int | None = None


def _effective_partial_interval() -> int:
    return _PARTIAL_INTERVAL_OVERRIDE or PARTIAL_INTERVAL_MS


def set_partial_interval(ms: int | None) -> None:
    """Set the cadence for tunings built from now on."""
    global _PARTIAL_INTERVAL_OVERRIDE
    _PARTIAL_INTERVAL_OVERRIDE = int(ms) if ms else None


def apply_partial_interval(splitter: "UtteranceSplitter", ms: int) -> None:
    """Change the cadence on a splitter that is already running.

    Mutating the existing Tuning rather than replacing the splitter, because the
    splitter holds the utterance being accumulated: a new one would drop whatever
    is in flight, and the audio would have to come round again.
    """
    splitter.tuning.partial_interval_ms = int(ms)


def audio_model_tuning() -> Tuning:
    """Limits for a model that hears and translates in one pass.

    Longer closing silence to cut at sentence boundaries, a much lower ceiling
    than whisper's 30s window, and no early-close partial: the audio path pays
    for the whole fragment up front, so a 150ms head start on a re-read is worth
    much less than not cutting the sentence in half.
    """
    return Tuning(
        min_silence_ms=GEMMA_MIN_SILENCE_MS,
        max_utterance_ms=GEMMA_MAX_UTTERANCE_MS,
        speech_pad_ms=GEMMA_VAD_SPEECH_PAD_MS,
        early_end_ms=GEMMA_MIN_SILENCE_MS,
        soft_cut_speech_ms=GEMMA_SOFT_CUT_SPEECH_MS,
        soft_cut_silence_ms=GEMMA_SOFT_CUT_SILENCE_MS,
        partial_interval_ms=0,
        name="audio-model",
    )


class UtteranceSplitter:
    """Turn a PCM stream into start / partial / end events.

    Every event is ``(kind, audio, flag)``. For a partial the flag marks the
    early closing partial emitted at ``EARLY_END_SILENCE_MS``; for an end it
    says that partial already holds the whole utterance, so the session may
    reuse that text instead of running ASR on the same audio a second time.
    """

    def __init__(self, engine=None, tuning: "Tuning | None" = None):
        self.engine = engine if engine is not None else load_vad()
        self.tuning = tuning if tuning is not None else whisper_tuning()
        self.pending = np.zeros(0, dtype=np.float32)
        self.utterance: list[np.ndarray] = []
        self.in_speech = False
        self.speech_ms = 0
        self.silence_ms = 0
        self.since_partial_ms = 0
        self.early_partial = False
        self.frame_ms = FRAME * 1000 / SAMPLE_RATE
        self._outside_silence_ms = 0.0
        self._unreported_silence = 0.0

    def _note_silence_run(self, ms: float, inside: int) -> None:
        """Emit one silence run, whether or not it closed an utterance.

        Runs inside an utterance are reported when they end, because a run that
        never ends is not a pause, it is a monologue. The length is passed in
        rather than read from state so the value cannot be the wrong one: the
        earlier reconstruction of these numbers from overlapping intervals is what
        produced a segment length twice the true median.
        """
        if ms <= 0:
            return
        perf.emit(
            "vad",
            "silence",
            bin=_silence_bin(ms),
            ms=round(ms, 1),
            inside_utterance=inside,
        )

    def _close_outside_silence(self) -> None:
        """A pause between utterances, which is where a clip's long silences are."""
        ms = self._outside_silence_ms
        self._outside_silence_ms = 0.0
        self._note_silence_run(ms, inside=0)

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
                    self._unreported_silence = 0.0
                    self._close_outside_silence()
                    events.append(("start", None, False))
                resumed = self.silence_ms > 0
                if resumed:
                    # The pause ended without closing the utterance. Report it now,
                    # before the counter is zeroed, or it is lost.
                    self._note_silence_run(self.silence_ms, inside=1)
                self.utterance.append(frame)
                self.speech_ms += self.frame_ms
                self.silence_ms = 0
                if resumed:
                    # Speech came back before the utterance closed, so the early
                    # partial only covered part of the sentence: never reuse it.
                    self.early_partial = False
                self.since_partial_ms += self.frame_ms
                t = self.tuning
                if self.speech_ms >= t.max_utterance_ms:
                    perf.emit(
                        "vad",
                        "end",
                        why="max_length",
                        speech_ms=round(self.speech_ms, 1),
                        silence_ms=round(self.silence_ms, 1),
                        early_partial=1 if self.early_partial else 0,
                    )
                    events.append(("end", self._take(), self.early_partial))
                elif (
                    t.partial_interval_ms
                    and self.speech_ms >= VAD_MIN_SPEECH_MS
                    and self.since_partial_ms >= t.partial_interval_ms
                ):
                    self.since_partial_ms = 0
                    events.append(("partial", self._copy(), False))
            elif self.in_speech:
                self.utterance.append(frame * NON_SPEECH_GAIN)
                self.silence_ms += self.frame_ms
                self.since_partial_ms += self.frame_ms
                t = self.tuning
                if (
                    t.partial_interval_ms
                    and not self.early_partial
                    and t.early_end_ms <= self.silence_ms < t.min_silence_ms
                    and self.speech_ms >= VAD_MIN_SPEECH_MS
                    and self.since_partial_ms >= t.partial_interval_ms
                ):
                    # The words stopped; only the closing silence is missing.
                    self.early_partial = True
                    self.since_partial_ms = 0
                    events.append(("partial", self._trim(self._copy()), True))
                if self.silence_ms >= t.min_silence_ms or (
                    self.speech_ms >= t.soft_cut_speech_ms
                    and self.silence_ms >= t.soft_cut_silence_ms
                ):
                    # Why it closed, and the run that closed it, recorded here
                    # while both are still true.
                    #
                    # Reconstructing this afterwards is what produced a wrong
                    # answer already: a real recording was reported as having 8.2s
                    # segments when the median was 1.37s, because the 8.2 came
                    # from dividing wall-clock by an event count that excluded
                    # everything the reuse path handled. The two were different
                    # populations and the division spanned both.
                    if self.silence_ms >= t.min_silence_ms:
                        why = "silence"
                    else:
                        why = "soft_cut"
                    perf.emit(
                        "vad",
                        "end",
                        why=why,
                        speech_ms=round(self.speech_ms, 1),
                        silence_ms=round(self.silence_ms, 1),
                        early_partial=1 if self.early_partial else 0,
                    )
                    self._note_silence_run(self.silence_ms, inside=1)
                    if self.speech_ms >= VAD_MIN_SPEECH_MS:
                        events.append(("end", self._trim(self._copy()), self.early_partial))
                    self._drop()
            elif not self.in_speech:
                # Outside an utterance a silence run is still a pause in the
                # material, and on a TTS clip these are the long ones the closing
                # threshold exists for.
                self._outside_silence_ms += self.frame_ms
        return events

    def flush(self) -> list[tuple[str, np.ndarray | None, bool]]:
        self._close_outside_silence()
        if self.in_speech and self.speech_ms >= VAD_MIN_SPEECH_MS and self.utterance:
            perf.emit(
                "vad",
                "end",
                why="flush",
                speech_ms=round(self.speech_ms, 1),
                silence_ms=round(self.silence_ms, 1),
                early_partial=1 if self.early_partial else 0,
            )
            self._note_silence_run(self.silence_ms, inside=1)
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
        pad = int(SAMPLE_RATE * self.tuning.speech_pad_ms / 1000)
        keep = int(nz[-1]) + pad
        return audio[:keep] if keep < audio.size else audio

    def _drop(self) -> None:
        self.utterance = []
        self.in_speech = False
        self.speech_ms = 0
        self.silence_ms = 0
        self.since_partial_ms = 0
        self.early_partial = False

