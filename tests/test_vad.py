import unittest

import numpy as np

from server.vad import FRAME, UtteranceSplitter


class FakeVad:
    def __init__(self, speech):
        self.speech = iter(speech)

    def prob(self, _frame):
        return 1.0 if next(self.speech) else 0.0

    def reset(self):
        return None


class VadSuppressionTests(unittest.TestCase):
    def test_non_speech_frames_are_suppressed_inside_utterance(self):
        speech = [True] * 9 + [False] * 13
        splitter = UtteranceSplitter(FakeVad(speech))
        payload = np.concatenate(
            [np.ones(FRAME, dtype=np.float32) for _ in range(9)]
            + [np.full(FRAME, 0.4, dtype=np.float32) for _ in range(13)]
        )

        events = splitter.push(payload)
        end_audio = next(audio for kind, audio in events if kind == "end")

        assert np.max(np.abs(end_audio[: 9 * FRAME])) > 0.9
        assert np.max(np.abs(end_audio[9 * FRAME :])) == 0.0

    def test_speech_frame_is_not_suppressed(self):
        splitter = UtteranceSplitter(FakeVad([True] * 9))
        events = splitter.push(np.ones(FRAME * 9, dtype=np.float32))

        partial_audio = next(audio for kind, audio in events if kind == "partial")
        assert np.max(np.abs(partial_audio)) > 0.9


if __name__ == "__main__":
    unittest.main()
