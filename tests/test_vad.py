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


if __name__ == "__main__":
    unittest.main()
