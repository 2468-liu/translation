"""Speech gate. Prefers Silero ONNX; falls back to RMS energy."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import perf
from .config import (
    SAMPLE_RATE,
    SILERO_ONNX,
    VAD_MERGE_WINDOW_MS,
    VAD_MIN_SILENCE_MS,
    VAD_MIN_SPEECH_MS,
    VAD_PREROLL_MAX_MS,
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
        min_speech_ms: int = VAD_MIN_SPEECH_MS,
        merge_window_ms: int = VAD_MERGE_WINDOW_MS,
        preroll_max_ms: int = VAD_PREROLL_MAX_MS,
    ):
        self.min_silence_ms = min_silence_ms
        self.max_utterance_ms = max_utterance_ms
        self.speech_pad_ms = speech_pad_ms
        self.early_end_ms = early_end_ms
        self.soft_cut_speech_ms = soft_cut_speech_ms
        self.soft_cut_silence_ms = soft_cut_silence_ms
        self.partial_interval_ms = partial_interval_ms
        # Leading pre-roll cap, in ms. 0 disables it. See _take_preroll for why the
        # gap it is clamped to is measured in detector frames and not by energy, and
        # why the frames it contributes reach neither speech_ms nor silence_ms.
        self.preroll_max_ms = preroll_max_ms
        # Below this much speech an utterance is too short to stand alone. What
        # happens to it next is merge_window_ms's business, not this value's.
        self.min_speech_ms = min_speech_ms
        # Silence allowed while holding a too-short utterance open for a
        # neighbour. 0 = never hold, i.e. discard it the way this used to.
        self.merge_window_ms = merge_window_ms
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
        # Rolling raw frames for the leading pre-roll, and the run of consecutive
        # non-speech frames -- the gap the pre-roll is clamped to. The run is
        # counted on every non-speech frame and reset when speech starts, so it
        # always describes the stretch immediately before the current utterance
        # start, including across a held fragment, which is the resumed path where a
        # gap and an utterance boundary can otherwise fall out of step. Frame length
        # comes from FRAME rather than from self.frame_ms, which is not assigned yet
        # at this point.
        frame_ms = FRAME / SAMPLE_RATE * 1000
        self._prebuffer: list[np.ndarray] = []
        self._prebuffer_max = max(1, int((self.tuning.preroll_max_ms + frame_ms) / frame_ms))
        self.silence_run_frames = 0
        self._silence_run_before = 0
        self.preroll_ms = 0.0
        self.since_partial_ms = 0
        self.early_partial = False
        self.frame_ms = FRAME * 1000 / SAMPLE_RATE
        self._outside_silence_ms = 0.0
        self._unreported_silence = 0.0
        # Silence accumulated since a too-short utterance was held open for a
        # neighbour. Kept apart from self.silence_ms because that counter drives
        # the ordinary closing rule, and letting it keep growing would make the
        # same utterance close itself again on the next frame.
        self._held_silence_ms = 0.0
        # True while a closed utterance is being kept for a neighbour instead of
        # being dropped. Distinct from early_partial on purpose: an utterance
        # held before any early partial existed has no partial text to reuse, and
        # keying the hold off early_partial made those two cases indistinguishable
        # -- which is what the merge test caught.
        self._holding = False

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
        self._held_silence_ms = 0.0
        self._holding = False
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
            # Rolling pre-buffer for the leading pre-roll. Kept before the branch
            # so it holds the frames that preceded this decision, which is the
            # only place they exist: once a frame is classified it is either
            # appended to the utterance or zeroed, and neither keeps the raw
            # samples.
            self._prebuffer.append(frame)
            if len(self._prebuffer) > self._prebuffer_max:
                del self._prebuffer[0]
            if not speech:
                # Counted here rather than in the non-speech branch below, because
                # that branch only runs while an utterance is open. The gap a
                # pre-roll is clamped to is the silence *between* utterances, which
                # is exactly the stretch this branch never sees -- so counting there
                # measured the pause inside the previous utterance instead, and the
                # clamp then keyed off a quantity unrelated to the gap. With that
                # wrong, raising the cap from 800ms to 1200ms changed nothing at all,
                # byte for byte, which is what gave it away.
                self.silence_run_frames += 1
            if speech:
                if not self.in_speech:
                    self.in_speech = True
                    self.speech_ms = 0
                    self.silence_ms = 0
                    self.since_partial_ms = 0
                    self.early_partial = False
                    self._silence_run_before = self.silence_run_frames
                    preroll = self._take_preroll()
                    self.utterance = list(preroll)
                    self.preroll_ms = len(preroll) * self.frame_ms
                    if self.preroll_ms:
                        perf.emit(
                            "vad",
                            "preroll",
                            preroll_ms=round(self.preroll_ms, 1),
                            gap_ms=round(self._silence_run_before * self.frame_ms, 1),
                            cap_ms=self.tuning.preroll_max_ms,
                            clamped=1 if self._silence_run_before * self.frame_ms
                            < self.tuning.preroll_max_ms else 0,
                        )
                    self._unreported_silence = 0.0
                    self._held_silence_ms = 0.0
                    self._holding = False
                    self._close_outside_silence()
                    events.append(("start", None, False))
                resumed = self.silence_ms > 0
                # Reset on every speech frame, not only when a new utterance opens.
                # What a pre-roll needs is the silence immediately before the
                # utterance's *first* speech frame. Resetting only at utterance
                # start let the counter accumulate across a long utterance's
                # internal pauses, so the "gap" it reported included the previous
                # sentence's silences and was therefore never shorter than the cap:
                # the clamp never bound once in four dense materials, and 41 to 89
                # percent of segment starts landed inside the preceding segment.
                # The value is only consumed at utterance start, above.
                self.silence_run_frames = 0
                if resumed:
                    # The pause ended without closing the utterance. Report it now,
                    # before the counter is zeroed, or it is lost.
                    self._note_silence_run(self.silence_ms, inside=1)
                self.utterance.append(frame)
                self.speech_ms += self.frame_ms
                self.silence_ms = 0
                self._held_silence_ms = 0.0
                self._holding = False
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
                        delivered=1,
                    )
                    events.append(("end", self._take(), self.early_partial))
                elif (
                    t.partial_interval_ms
                    and self.speech_ms >= t.min_speech_ms
                    and self.since_partial_ms >= t.partial_interval_ms
                ):
                    self.since_partial_ms = 0
                    events.append(("partial", self._copy(), False))
            elif self.in_speech:
                self.utterance.append(frame * NON_SPEECH_GAIN)
                self.silence_ms += self.frame_ms
                # Only counts while an utterance is being held open, so it cannot
                # be confused with the ordinary closing silence above.
                if self._holding:
                    self._held_silence_ms += self.frame_ms
                self.since_partial_ms += self.frame_ms
                t = self.tuning
                if (
                    t.merge_window_ms > 0
                    and self._holding
                    and self._held_silence_ms >= t.merge_window_ms
                ):
                    # The neighbour never came. Close it as its own fragment,
                    # which is the outcome that used to happen immediately; the
                    # merge window only buys the chance of a better one.
                    perf.emit(
                        "vad",
                        "end",
                        why="held_timeout",
                        speech_ms=round(self.speech_ms, 1),
                        silence_ms=round(self.silence_ms, 1),
                        early_partial=1,
                        delivered=1,
                    )
                    events.append(("end", self._take(), True))
                    continue
                if (
                    t.partial_interval_ms
                    and not self.early_partial
                    and t.early_end_ms <= self.silence_ms < t.min_silence_ms
                    and self.speech_ms >= t.min_speech_ms
                    and self.since_partial_ms >= t.partial_interval_ms
                ):
                    # The words stopped; only the closing silence is missing.
                    self.early_partial = True
                    self.since_partial_ms = 0
                    events.append(("partial", self._trim(self._copy()), True))
                if not self._holding and (
                    self.silence_ms >= t.min_silence_ms or (
                        self.speech_ms >= t.soft_cut_speech_ms
                        and self.silence_ms >= t.soft_cut_silence_ms
                    )
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
                    # delivered= says whether an end event follows this line.
                    #
                    # It is not redundant with the min_speech_ms test, because this
                    # emit sits ABOVE it -- that placement is deliberate, so the
                    # closing reason is recorded while it is still true. The cost is
                    # that a line reading `vad.end` does not mean a segment: a
                    # fragment below the gate is logged and then dropped, and with a
                    # merge window it is logged and then HELD, which is not a closing
                    # at all. On e_3min's #6 arm that was 13 of 37 lines.
                    #
                    # Without this flag the count has to be recovered as
                    # `vad.end - vad.held`, and that arithmetic is only valid on an
                    # arm with a merge window -- vad.held is not emitted at all
                    # otherwise, so subtracting it on the default arm would use a
                    # field that does not exist. A number in the log beats a formula
                    # someone has to remember the precondition of.
                    perf.emit(
                        "vad",
                        "end",
                        why=why,
                        speech_ms=round(self.speech_ms, 1),
                        silence_ms=round(self.silence_ms, 1),
                        early_partial=1 if self.early_partial else 0,
                        delivered=1 if self.speech_ms >= t.min_speech_ms else 0,
                    )
                    self._note_silence_run(self.silence_ms, inside=1)
                    if self.speech_ms >= t.min_speech_ms:
                        events.append(("end", self._trim(self._copy()), self.early_partial))
                        self._drop()
                    elif t.merge_window_ms > 0:
                        # Too short to stand alone: hold it for a neighbour
                        # instead of discarding it.
                        #
                        # This used to be an unconditional `_drop()`, which is
                        # how a 250-1000ms burst of real speech disappeared
                        # before anything downstream saw it. The audio stays in
                        # self.utterance and the next speech frame resumes it
                        # through the `resumed` path above.
                        #
                        # `self._holding` then suppresses this whole closing
                        # block until the fragment is either resumed or the merge
                        # window expires. It has to: `silence_ms` was just zeroed
                        # for the hold, so it climbs back to min_silence_ms a few
                        # frames later and the block would fire again -- first
                        # holding a second time, then taking this branch's
                        # discard path, so the fragment was never emitted and the
                        # merge window never got a chance to expire. The merge
                        # tests caught that as three holds and no end event.
                        self.silence_ms = 0
                        self.since_partial_ms = 0
                        self._held_silence_ms = 0.0
                        self._holding = True
                        perf.emit(
                            "vad",
                            "held",
                            speech_ms=round(self.speech_ms, 1),
                            merge_window_ms=t.merge_window_ms,
                            why="short",
                        )
                    else:
                        # Merging switched off: the fragment is discarded, which
                        # is exactly what this did before #6.
                        self._drop()
            elif not self.in_speech:
                # Outside an utterance a silence run is still a pause in the
                # material, and on a TTS clip these are the long ones the closing
                # threshold exists for.
                self._outside_silence_ms += self.frame_ms
        return events

    def flush(self) -> list[tuple[str, np.ndarray | None, bool]]:
        self._close_outside_silence()
        # A held fragment still waiting for a neighbour is a real utterance when
        # the stream ends. Judging it by min_speech_ms here would throw it away at
        # the last moment, which is the failure mode #6 exists to remove.
        held = self._holding and self.utterance
        if self.in_speech and (self.speech_ms >= self.tuning.min_speech_ms or held) and self.utterance:
            perf.emit(
                "vad",
                "end",
                why="flush",
                speech_ms=round(self.speech_ms, 1),
                silence_ms=round(self.silence_ms, 1),
                early_partial=1 if self.early_partial else 0,
                delivered=1,
            )
            self._note_silence_run(self.silence_ms, inside=1)
            return [("end", self._trim(self._copy()), self.early_partial)]
        self._drop()
        return []

    def _take_preroll(self) -> list[np.ndarray]:
        """Leading frames to prepend, at raw gain, and no longer than the gap.

        Three decisions, each forced by something measurable.

        Gain 1.0, not NON_SPEECH_GAIN. A pre-roll frame is by definition one the
        detector called non-speech, and NON_SPEECH_GAIN is 0.0, so sharing its rule
        would fill the pre-roll with silence and lose exactly the words the pre-roll
        exists to recover.

        Neither speech_ms nor silence_ms is touched. speech_ms so that max_utterance
        and min_speech still measure speech rather than padding -- counting it there
        would let one frame of music plus 800ms of pre-roll read as 832ms of speech
        and pass the 250ms gate, which is the gate's whole job. silence_ms because
        the close condition is silence_ms >= 400, or speech_ms >= 5000 and
        silence_ms >= 200: a pre-roll counted there satisfies the second term the
        moment the segment opens, so long utterances get cut at exactly 5.000s with
        no pause at all and why=soft_cut climbs.

        The gap is measured in detector non-speech frames, not by energy, because
        the frames being recovered are the ones the detector was late on. An energy
        measure would see the music bed under every clause, report a gap near zero,
        and clamp the pre-roll away before it did anything.
        """
        cap = self.tuning.preroll_max_ms
        if cap <= 0:
            return []
        want = min(int(cap / self.frame_ms), self._silence_run_before)
        if want <= 0:
            return []
        return self._prebuffer[-want:]

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
        self._held_silence_ms = 0.0
        self._holding = False

