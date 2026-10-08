import unittest

import numpy as np

from server.config import SAMPLE_RATE
from server.vad import (
    EARLY_END_SILENCE_MS,
    FRAME,
    MAX_UTTERANCE_MS,
    SOFT_CUT_SILENCE_MS,
    SOFT_CUT_SPEECH_MS,
    VAD_SPEECH_PAD_MS,
    Tuning,
    UtteranceSplitter,
)


class FakeVad:
    def __init__(self, speech):
        self.speech = iter(speech)

    def prob(self, _frame):
        return 1.0 if next(self.speech) else 0.0

    def reset(self):
        return None


def _frames(pattern) -> np.ndarray:
    """One FRAME of ones per spoken step, one frame of silence per quiet step."""
    return np.concatenate(
        [
            np.ones(FRAME, dtype=np.float32)
            if spoken
            else np.zeros(FRAME, dtype=np.float32)
            for spoken in pattern
        ]
    )


def _payload(speech_frames: int, quiet_frames: int) -> np.ndarray:
    return _frames([True] * speech_frames + [False] * quiet_frames)


def _kinds(events):
    return [(kind, flag) for kind, _audio, flag in events]


class VadSuppressionTests(unittest.TestCase):
    def test_non_speech_frames_are_suppressed_inside_utterance(self):
        speech = [True] * 9 + [False] * 13
        splitter = UtteranceSplitter(FakeVad(speech))
        payload = np.concatenate(
            [np.ones(FRAME, dtype=np.float32) for _ in range(9)]
            + [np.full(FRAME, 0.4, dtype=np.float32) for _ in range(13)]
        )

        events = splitter.push(payload)
        end_audio = next(audio for kind, audio, _flag in events if kind == "end")

        assert np.max(np.abs(end_audio[: 9 * FRAME])) > 0.9
        assert np.max(np.abs(end_audio[9 * FRAME :])) == 0.0

    def test_speech_frame_is_not_suppressed(self):
        splitter = UtteranceSplitter(FakeVad([True] * 9))
        events = splitter.push(np.ones(FRAME * 9, dtype=np.float32))

        partial_audio = next(audio for kind, audio, _flag in events if kind == "partial")
        assert np.max(np.abs(partial_audio)) > 0.9


class VadEarlyCloseTests(unittest.TestCase):
    def test_early_partial_precedes_the_closing_end(self):
        """One partial during the150ms pause, and the end may reuse its text."""
        speech = [True] * 9 + [False] * 13
        splitter = UtteranceSplitter(FakeVad(speech))

        events = splitter.push(_payload(9, 13))
        flags = _kinds(events)

        early = [i for i, (kind, flag) in enumerate(flags) if kind == "partial" and flag]
        end = [i for i, (kind, _flag) in enumerate(flags) if kind == "end"]
        assert early and end, flags
        assert early[0] < end[0]
        assert flags[end[0]][1] is True

    def test_no_early_partial_without_a_pause(self):
        splitter = UtteranceSplitter(FakeVad([True] * 9))
        events = splitter.push(_payload(9, 0))
        assert [flag for kind, _audio, flag in events if kind == "partial"] == [False]

    def test_end_audio_keeps_only_a_short_release(self):
        speech = [True] * 9 + [False] * 13
        splitter = UtteranceSplitter(FakeVad(speech))

        events = splitter.push(_payload(9, 13))
        end_audio = next(audio for kind, audio, _flag in events if kind == "end")

        tail_samples = int(SAMPLE_RATE * VAD_SPEECH_PAD_MS / 1000)
        # The utterance carried13 frames (416ms) of closing silence.
        assert end_audio.size < 9 * FRAME + 13 * FRAME
        assert end_audio.size - 1 - int(np.nonzero(end_audio)[0][-1]) <= tail_samples

    def test_early_partial_starts_before_the_pause_is_over(self):
        speech = [True] * 9 + [False] * 13
        splitter = UtteranceSplitter(FakeVad(speech))

        events = splitter.push(_payload(9, 13))
        early_audio = next(
            audio for kind, audio, flag in events if kind == "partial" and flag
        )
        # It must arrive while the pause is still running, not after the400ms close.
        frame_ms = FRAME * 1000 / SAMPLE_RATE
        assert EARLY_END_SILENCE_MS <= 13 * frame_ms
        assert early_audio.size <= 9 * FRAME + int(SAMPLE_RATE * VAD_SPEECH_PAD_MS / 1000)

    def test_a_resumed_pause_gets_a_fresh_early_partial(self):
        """Speech that comes back must flush again on the next pause.

        Without the reset the first early partial would latch the flag, no
        later pause would flush, and the closing end would reuse text that
        stops at the first pause.
        """
        speech = [True] * 12 + [False] * 6 + [True] * 4 + [False] * 13
        splitter = UtteranceSplitter(FakeVad(speech))

        flags = _kinds(splitter.push(_frames(speech)))

        assert [kind for kind, _flag in flags].count("start") == 1
        assert [flag for kind, flag in flags if kind == "partial" and flag] == [True, True]
        assert flags[-1] == ("end", True)


class VadSoftCutTests(unittest.TestCase):
    def test_long_speech_closes_on_a_200ms_pause(self):
        """Past SOFT_CUT_SPEECH_MS a short pause already ends the utterance."""
        pause = [False] * (SOFT_CUT_SILENCE_MS // (FRAME * 1000 // SAMPLE_RATE) + 1)
        long_speech = SOFT_CUT_SPEECH_MS // (FRAME * 1000 // SAMPLE_RATE) + 1
        short_speech = long_speech // 2

        def ends(sample: int) -> bool:
            pattern = [True] * sample + pause
            return any(kind == "end" for kind, _a, _f in
                       UtteranceSplitter(FakeVad(pattern)).push(_frames(pattern)))

        assert ends(long_speech)
        assert not ends(short_speech)

    def test_short_speech_still_waits_for_the_full_close(self):
        pause = [False] * 7  # 224ms: enough for a soft cut, not for a normal close
        short = [True] * 100 + pause
        events = UtteranceSplitter(FakeVad(short)).push(_frames(short))
        assert not any(kind == "end" for kind, _a, _f in events)

        longer = [True] * 100 + [False] * 13  # 416ms
        events = UtteranceSplitter(FakeVad(longer)).push(_frames(longer))
        assert any(kind == "end" for kind, _a, _f in events)

    def test_utterance_is_capped(self):
        pattern = [True] * (MAX_UTTERANCE_MS // (FRAME * 1000 // SAMPLE_RATE) + 25)
        events = UtteranceSplitter(FakeVad(pattern)).push(_frames(pattern))

        flags = _kinds(events)
        assert any(kind == "end" for kind, _flag in flags)
        assert len([kind for kind, _flag in flags if kind == "start"]) >= 2


class VadMergeShortUtteranceTests(unittest.TestCase):
    """#6: a too-short utterance is held for a neighbour, not thrown away.

    The gate used to be a drop: the closing branch called ``_drop()``
    unconditionally, so every burst under ``min_speech_ms`` was discarded after
    the splitter had already announced its close. On the movie clip this project
    uses, raising that gate to 1.0s would have discarded 5.4s of speech that
    carries real transcription text, so the intended behaviour is LiveTranslate's:
    keep it and let the next speech resume it.
    """

    @staticmethod
    def _tuning(**over):
        # merge_window_ms is the point of these tests, so it is set explicitly
        # rather than left to Tuning's default. It was left out at first, and
        # when the shipped default moved to 0 the whole class silently ran with
        # merging switched off while still asserting that merging happened.
        base = dict(
            min_silence_ms=400,
            max_utterance_ms=12_000,
            speech_pad_ms=VAD_SPEECH_PAD_MS,
            early_end_ms=EARLY_END_SILENCE_MS,
            soft_cut_speech_ms=SOFT_CUT_SPEECH_MS,
            soft_cut_silence_ms=SOFT_CUT_SILENCE_MS,
            partial_interval_ms=250,
            name="test",
            merge_window_ms=2000,
        )
        base.update(over)
        return Tuning(**base)

    def test_short_utterance_merges_into_the_next_one(self):
        # 5 frames of speech = 160ms: under the gate, so it cannot stand alone.
        # Then a 416ms pause closes it; another 30 frames of speech follow.
        pattern = [True] * 5 + [False] * 13 + [True] * 30 + [False] * 13
        splitter = UtteranceSplitter(FakeVad(pattern), tuning=self._tuning(min_speech_ms=1000))

        events = splitter.push(_frames(pattern))
        ends = [audio for kind, audio, _f in events if kind == "end"]

        assert len(ends) == 1, f"expected one merged fragment, got {len(ends)}"
        spoken = int(np.count_nonzero(ends[0]))
        # Both bursts are in it: 5 + 30 frames, minus the trimmed trailing silence.
        assert spoken >= 35 * FRAME - int(SAMPLE_RATE * VAD_SPEECH_PAD_MS / 1000) - 1

    def test_a_held_fragment_is_announced_as_held(self):
        """A held fragment emits `held`, so the merge can be counted.

        Without the event, "kept for a neighbour" and "discarded" differ only by
        the absence of an end event, which is not something a reader of a perf
        log can count -- the same trap as reading `vad.end` line counts as an
        event count (STATE.md, falsified hypotheses).
        """
        from server import perf

        seen: list[tuple[str, dict]] = []
        original = perf.emit
        perf.emit = lambda source, event, **fields: seen.append((event, fields)) if source == "vad" else None
        try:
            pattern = [True] * 5 + [False] * 13 + [True] * 30 + [False] * 13
            splitter = UtteranceSplitter(FakeVad(pattern), tuning=self._tuning(min_speech_ms=1000))
            splitter.push(_frames(pattern))
        finally:
            perf.emit = original

        held = [f for e, f in seen if e == "held"]
        assert held, f"no held event was emitted; saw {[e for e, _ in seen]}"
        assert held[0]["speech_ms"] == 160.0
        assert held[0]["merge_window_ms"] == 2000

    def test_held_fragment_closes_on_its_own_after_the_merge_window(self):
        # 5 frames of speech (160ms) then a long silence: the neighbour never
        # comes, so the merge window expires and the fragment must still be
        # emitted rather than waiting forever.
        lone = [True] * 5 + [False] * 45
        events = UtteranceSplitter(FakeVad(lone), tuning=self._tuning(
            min_speech_ms=1000, merge_window_ms=1000)).push(_frames(lone))
        assert any(kind == "end" for kind, _a, _f in events), "a lone short burst must still be emitted"

    def test_merge_window_zero_restores_the_old_drop(self):
        # The second burst is long enough to stand alone (1600ms) so the only
        # thing under test is what happened to the first one. With the window at
        # zero the short one is discarded, which is the pre-#6 behaviour and the
        # kill switch for the whole feature.
        pattern = [True] * 5 + [False] * 13 + [True] * 50 + [False] * 13
        splitter = UtteranceSplitter(FakeVad(pattern), tuning=self._tuning(
            min_speech_ms=1000, merge_window_ms=0))
        events = splitter.push(_frames(pattern))
        ends = [audio for kind, audio, _f in events if kind == "end"]
        assert len(ends) == 1, f"expected only the long burst to close, got {len(ends)}"
        # The survivor is the long burst alone: 1600ms of speech, not 1760ms.
        assert int(np.count_nonzero(ends[0])) == 50 * FRAME

    def test_default_tuning_still_keeps_short_bursts_standalone(self):
        # 160ms is under the shipped 250ms gate, so with the shipped settings it is
        # still discarded -- #6 changes the policy, not this default.
        pattern = [True] * 5 + [False] * 13
        events = UtteranceSplitter(FakeVad(pattern)).push(_frames(pattern))
        assert not any(kind == "end" for kind, _a, _f in events)


class VadDeliveredFlagTests(unittest.TestCase):
    """A `vad.end` line says whether an end event followed it.

    The closing emit sits above the min_speech_ms test, on purpose, so the reason
    is recorded while it is still true. The cost is that a line reading `vad.end`
    does not mean a segment: below the gate the fragment is logged and dropped,
    and with a merge window it is logged and then held, which is not a closing at
    all. On e_3min's #6 arm 13 of 37 lines were that second kind, and REPORT 22's
    merge column was computed over all 37.

    The count was being recovered as `vad.end - vad.held`, which is only valid on
    an arm with a merge window -- vad.held is not emitted otherwise, so on the
    default arm that subtraction uses a field that does not exist. These tests pin
    the flag on both arms so the number is in the log rather than in a formula
    somebody has to remember the precondition of.
    """

    @staticmethod
    def _ends(pattern, **tuning_over):
        from server import perf

        ends: list[tuple[str, dict]] = []
        original = perf.emit
        perf.emit = (
            lambda source, event, **fields: ends.append((event, fields))
            if source == "vad" and event == "end"
            else None
        )
        try:
            UtteranceSplitter(FakeVad(pattern), tuning=Tuning(**tuning_over)).push(_frames(pattern))
        finally:
            perf.emit = original
        return ends

    @staticmethod
    def _tuning(**over):
        base = dict(
            min_silence_ms=400,
            max_utterance_ms=12_000,
            speech_pad_ms=VAD_SPEECH_PAD_MS,
            early_end_ms=EARLY_END_SILENCE_MS,
            soft_cut_speech_ms=SOFT_CUT_SPEECH_MS,
            soft_cut_silence_ms=SOFT_CUT_SILENCE_MS,
            partial_interval_ms=250,
            name="test",
        )
        base.update(over)
        return base

    def test_a_held_fragment_logs_an_end_that_says_it_was_not_delivered(self):
        ends = self._ends(
            [True] * 5 + [False] * 13 + [True] * 30 + [False] * 13,
            **self._tuning(min_speech_ms=1000, merge_window_ms=2000),
        )
        assert ends, "the short burst should still be logged, that is the point"
        assert [e["delivered"] for _w, e in ends] == [0, 1], (
            f"first line is the held burst and must read delivered=0; got {ends}"
        )

    def test_a_dropped_fragment_also_reads_delivered_zero(self):
        ends = self._ends(
            [True] * 5 + [False] * 13 + [True] * 50 + [False] * 13,
            **self._tuning(min_speech_ms=1000, merge_window_ms=0),
        )
        assert [e["delivered"] for _w, e in ends] == [0, 1], (
            "with merging off the short burst is dropped, which is also delivered=0"
        )

    def test_the_flag_exists_on_the_default_arm_with_no_merge_window(self):
        # The subtraction `vad.end - vad.held` is only computable where held is
        # emitted at all. This is the arm where it is not, so the flag is the only
        # thing a reader has -- hence it must be present here too.
        ends = self._ends([True] * 30 + [False] * 13, **self._tuning(min_speech_ms=250))
        assert ends and all("delivered" in e for _w, e in ends), (
            f"every vad.end line needs the field; got {ends}"
        )
        assert all(e["delivered"] == 1 for _w, e in ends)


if __name__ == "__main__":
    unittest.main()
