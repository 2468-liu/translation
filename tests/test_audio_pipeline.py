"""The audio-native pipeline: one model hears the audio and translates it.

These are the details that fail silently. A wrong instruction or a reasoning
model left enabled returns an empty answer rather than an error, so the tests
assert on what actually comes back, not merely that a request was built.
"""

import asyncio
import base64
import unittest

import numpy as np

from server import audio_mt
from server.config import AUDIO_CHAT_TEMPLATE_KWARGS, AUDIO_SYSTEM_PROMPT
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
        self.assertEqual(t.min_silence_ms, 700)
        self.assertLessEqual(t.max_utterance_ms, 26_000)
        self.assertEqual(t.speech_pad_ms, 200)
        # No partials: re-reading a growing prefix is a whisper optimisation, and
        # on the audio path each read pays for the whole fragment up front.
        self.assertEqual(t.partial_interval_ms, 0)

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

        speech = [True] * 9 + [False] * 22  # 288ms speech, then 704ms silence
        splitter = UtteranceSplitter(FakeVad(speech), tuning=audio_model_tuning())
        payload = np.concatenate(
            [np.ones(FRAME, dtype=np.float32) for _ in range(9)]
            + [np.zeros(FRAME, dtype=np.float32) for _ in range(22)]
        )
        kinds = [k for k, _a, _f in splitter.push(payload)]
        self.assertIn("end", kinds)
        # The audio path emits no partial at all.
        self.assertNotIn("partial", kinds)

    def test_whisper_tuning_still_emits_partials(self):
        from server.vad import UtteranceSplitter

        speech = [True] * 9 + [False] * 5
        splitter = UtteranceSplitter(FakeVad(speech), tuning=whisper_tuning())
        payload = np.concatenate(
            [np.ones(FRAME, dtype=np.float32) for _ in range(9)]
            + [np.zeros(FRAME, dtype=np.float32) for _ in range(5)]
        )
        kinds = [k for k, _a, _f in splitter.push(payload)]
        self.assertIn("partial", kinds)


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