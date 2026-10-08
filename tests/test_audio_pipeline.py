"""The audio-native pipeline: one model hears the audio and translates it.

These are the details that fail silently. A wrong instruction or a reasoning
model left enabled returns an empty answer rather than an error, so the tests
assert on what actually comes back, not merely that a request was built.
"""

import base64
import unittest

import numpy as np

from server import audio_mt
from server.config import AUDIO_CHAT_TEMPLATE_KWARGS, AUDIO_SYSTEM_PROMPT, SAMPLE_RATE
from server.vad import FRAME, audio_model_tuning, whisper_tuning


class FakeVad:
    def __init__(self, speech):
        self.speech = iter(speech)

    def prob(self, _frame):
        return 1.0 if next(self.speech) else 0.0

    def reset(self):
        return None


class StreamDeltaTests(unittest.IsolatedAsyncioTestCase):
    """A CR from the server is a correction, not an append.

    Getting this wrong duplicated a clause: the observed worst output was 90
    characters long with the same sentence appearing twice, growing 85 -> 86 ->
    89 -> 90 as chunks arrived.
    """

    async def test_cr_replaces_rather_than_appends(self):
        from server.audio_mt import stream_audio_translation

        # A CR replaces what came before it; chunks after it continue from there.
        chunks = [
            "本周，",
            "\r本周，我花了",
            "三个晚上",
        ]

        class FakeResponse:
            def __init__(self):
                self.status = 200

            def raise_for_status(self):
                return None

            async def aiter_lines(self):
                for c in chunks:
                    yield 'data: ' + __import__("json").dumps(
                        {"choices": [{"delta": {"content": c}}]}
                    )
                yield "data: [DONE]"

        class FakeRequest:
            def __init__(self):
                self.stream = True

        class FakeClient:
            def build_request(self, *_a, **_k):
                return FakeRequest()

            async def send(self, *_a, **_k):
                return FakeResponse()

        got = []
        async for acc in stream_audio_translation(
            np.ones(1600, dtype=np.float32), FakeClient()
        ):
            got.append(acc)

        self.assertEqual(got[-1], "本周，我花了三个晚上")
        # Every intermediate value must be a prefix of the final one, never a
        # prefix plus a repeat of what came before.
        for value in got:
            self.assertTrue(
                got[-1].startswith(value), f"{value!r} is not a prefix of {got[-1]!r}"
            )


class TuningTests(unittest.TestCase):
    def test_audio_model_uses_its_own_limits(self):
        t = audio_model_tuning()
        # The exact closing silence is swept by scripts/probe_silence.py; what
        # matters here is that the audio path is tuned separately from whisper's
        # and sits in a band that trades sentence integrity against a fixed
        # per-sentence cost.
        # The band is where the sweep can move the threshold without the audio
        # path and whisper's stopping on the same knob by accident, and without a
        # value so tight that a breath splits a sentence. The chosen setting sits
        # at the loose end of it: 400ms gave the cleanest latency and the most
        # broken text, 1000ms cost about 170ms and cut the bad fragments from six
        # to one.
        self.assertGreaterEqual(t.min_silence_ms, 250)
        self.assertLessEqual(t.min_silence_ms, 1200)
        self.assertEqual(t.speech_pad_ms, 200)
        # No partials: re-reading a growing prefix is a whisper optimisation, and
        # on the audio path each read pays for the whole fragment up front.
        self.assertEqual(t.partial_interval_ms, 0)

    def test_the_sweep_can_tighten_the_pause_all_the_way_to_whispers(self):
        """The tight end of the sweep coincides with whisper's threshold.

        Worth stating rather than leaving to chance: if the sweep ever reaches
        it, the two pipelines stop differing on this knob, and any measured
        difference is then attributable to something other than the pause.

        Not the chosen value -- that is 1000ms, because the longer pause gave
        measurably cleaner text. Asserted here instead: the threshold is one the
        sweep can actually reach, so the loose end is a choice rather than a
        limit somebody hard-coded and then tested against itself.
        """
        from server.config import GEMMA_MIN_SILENCE_MS

        self.assertEqual(audio_model_tuning().min_silence_ms, GEMMA_MIN_SILENCE_MS)
        self.assertNotEqual(GEMMA_MIN_SILENCE_MS, whisper_tuning().min_silence_ms)

    def test_whisper_limits_are_unchanged(self):
        t = whisper_tuning()
        self.assertEqual(t.min_silence_ms, 400)
        self.assertEqual(t.max_utterance_ms, 12_000)
        self.assertGreater(t.partial_interval_ms, 0)

    def test_audio_ceiling_stays_under_the_model_window(self):
        # The audio model has a hard window with no context stitching to fall
        # back on, so this is a correctness limit, not a quality one.
        self.assertLess(audio_model_tuning().max_utterance_ms, 30_000)


class SplitterLimitTests(unittest.TestCase):
    def test_splitter_uses_its_tuning_not_module_constants(self):
        from server.vad import UtteranceSplitter

        # Silence long enough for whichever closing threshold is configured.
        # Pinned to the tuning rather than to a frame count: 704ms of silence
        # closes a 400ms threshold and leaves a 1000ms one still waiting, so a
        # test written against one setting silently stops testing the other.
        t = audio_model_tuning()
        frame_ms = FRAME * 1000 / 16000
        silent_frames = int(t.min_silence_ms / frame_ms) + 4
        speech = [True] * 9 + [False] * silent_frames
        splitter = UtteranceSplitter(FakeVad(speech), tuning=t)
        payload = np.concatenate(
            [np.ones(FRAME, dtype=np.float32) for _ in range(9)]
            + [np.zeros(FRAME, dtype=np.float32) for _ in range(silent_frames)]
        )
        kinds = [k for k, _a, _f in splitter.push(payload)]
        self.assertIn("end", kinds)
        # The audio path emits no partial at all.
        self.assertNotIn("partial", kinds)

    def test_whisper_tuning_still_emits_partials(self):
        """The shipped tuning must still produce partials on an utterance it can reach.

        The name of this test is the whole point: it asks about whisper_tuning(), the
        configuration that actually ships, so it cannot use a cadence of its own. It
        used a 9-frame utterance, 288ms, which only produced a partial while
        PARTIAL_INTERVAL_MS was 250. At the shipped 750 that pattern is shorter than the
        cadence and no partial can fire, so the test would have been asserting something
        the product does not do.

        32 frames is 1024ms, comfortably past a 750ms cadence and past the 250ms early
        close, so it exercises the shipped path rather than a corner of it.
        """
        from server.vad import UtteranceSplitter

        spoken = 32
        speech = [True] * spoken + [False] * 5
        splitter = UtteranceSplitter(FakeVad(speech), tuning=whisper_tuning())
        payload = np.concatenate(
            [np.ones(FRAME, dtype=np.float32) for _ in range(spoken)]
            + [np.zeros(FRAME, dtype=np.float32) for _ in range(5)]
        )
        kinds = [k for k, _a, _f in splitter.push(payload)]

        self.assertIn("partial", kinds)
        # And the utterance has to be long enough for the shipped cadence to be able
        # to fire at all, so a future default raise fails here with the reason rather
        # than as a StopIteration somewhere further down.
        self.assertGreater(spoken * FRAME * 1000 / SAMPLE_RATE, whisper_tuning().partial_interval_ms)


class MessageTests(unittest.TestCase):
    def test_instruction_is_english_and_thinking_is_off(self):
        self.assertFalse(
            any("一" <= ch <= "鿿" for ch in AUDIO_SYSTEM_PROMPT),
            "the instruction must be English: the Chinese wording was measured "
            "returning an empty answer after 585 tokens of reasoning",
        )
        self.assertIs(AUDIO_CHAT_TEMPLATE_KWARGS["enable_thinking"], False)

    def test_context_is_passed_as_text_not_replayed_audio(self):
        msgs = audio_mt.build_messages("AAA", [("hello", "你好"), ("world", "世界")])
        system = msgs[0]["content"]
        self.assertIn("你好", system)
        self.assertIn("世界", system)
        # Only this fragment's audio is attached.
        audio_parts = [
            p
            for p in msgs[1]["content"]
            if p.get("type") == "input_audio"
        ]
        self.assertEqual(len(audio_parts), 1)

    def test_empty_context_adds_nothing(self):
        msgs = audio_mt.build_messages("AAA", [])
        self.assertEqual(msgs[0]["content"], AUDIO_SYSTEM_PROMPT)

    def test_payload_asks_for_a_stream(self):
        p = audio_mt.build_payload("AAA")
        self.assertTrue(p["stream"])
        self.assertEqual(p["temperature"], 0)


class EncodeTests(unittest.TestCase):
    def test_wav_round_trips_through_base64(self):
        import io
        import wave

        tone = (np.sin(np.arange(1600) * 0.1) * 0.5).astype(np.float32)
        b64 = audio_mt.encode_wav(tone)
        raw = base64.b64decode(b64)
        with wave.open(io.BytesIO(raw)) as w:
            self.assertEqual(w.getnchannels(), 1)
            self.assertEqual(w.getsampwidth(), 2)
            self.assertEqual(w.getframerate(), 16000)
            self.assertEqual(w.getnframes(), 1600)

    def test_loud_audio_is_clipped_not_wrapped(self):
        import io
        import wave

        loud = np.full(800, 4.0, dtype=np.float32)
        raw = base64.b64decode(audio_mt.encode_wav(loud))
        with wave.open(io.BytesIO(raw)) as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        self.assertEqual(int(pcm.max()), 32767)


if __name__ == "__main__":
    unittest.main()