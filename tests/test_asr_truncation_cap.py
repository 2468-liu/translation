"""A capped Qwen3-ASR call: what gets dropped, what gets counted, what still passes.

The cap exists because the model sometimes never emits an end token. Measured on
samples/lecture_mix.wav: 5 of 149 calls ran 30 to 33 seconds and generated 3912 to
3951 eval tokens against a normal 7 to 46, stopping only because 184 prompt tokens
plus 3912 filled the server's -c 4096. The text went on screen and 74 seconds passed
with nothing after "你真的，你真的".

Three things follow from that, and each is a separate guarantee:

  the cap is sent          a payload without max_tokens is the pre-fix request, and
                           the failure it causes is silent, so its absence is the bug
  a capped reply is        dropped whole rather than trimmed. Half a clause on screen
  dropped, not trimmed      is worse than none: the caption has already swallowed it
                           and a repeated fragment reads as recognition failure
  the drop is counted      asr.truncated, with the token count, because a bound that
                           is invisible cannot be reviewed later

The fake reply stands in for a runaway. It cannot be reproduced -- six hypotheses were
tested and none held, same-length audio differing 70x -- so the shape of the server's
response is constructed instead, and the test asserts on the decision rather than on
the model.
"""

from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from server import asr as asr_mod
from server import config


class FakeReply:
    def __init__(self, finish_reason: str, content: str, tokens: int, include_usage: bool = True):
        self._body = {"choices": [{"message": {"content": content},
                                   "finish_reason": finish_reason}]}
        if include_usage:
            self._body["usage"] = {"completion_tokens": tokens}

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._body


def audio(seconds: float = 2.0) -> np.ndarray:
    return np.zeros(int(config.SAMPLE_RATE * seconds), dtype=np.float32)


class FakeHttpx:
    """Stands in for the httpx module transcribe() imports at call time.

    Patching the attribute on the asr module does nothing, because the import is
    inside the function and rebinds a local name on every call. sys.modules is the
    only place that reaches it, and that is worth stating here because the first
    version of this test patched the attribute, passed vacuously, and failed on every
    assertion once the fake client was actually reached.
    """

    Timeout = staticmethod(lambda value: value)

    def __init__(self, post):
        self._post = post
        self.last_payload: dict | None = None

    def Client(self, **_kw):
        client = MagicMock()
        client.post.side_effect = self._post
        client.__enter__ = lambda self: self
        client.__exit__ = lambda self, *a: False
        return client


def run(post) -> tuple:
    fake = FakeHttpx(post)

    def wrapped(url, json):
        fake.last_payload = json
        return post(url, json)

    fake._post = wrapped
    with patch.dict(sys.modules, {"httpx": fake}):
        result = asr_mod.Qwen3AsrEngine().transcribe(audio(), None)
    return result, fake.last_payload


def call(reply: FakeReply) -> tuple:
    def post(url, json):
        return reply

    result, _payload = run(post)
    return result


class CapIsSentTests(unittest.TestCase):
    def test_payload_carries_max_tokens(self):
        """Without it the request is the one that produced 30-second runaways."""
        seen: dict = {}

        def post(url, json):
            seen.update(json)
            return FakeReply("stop", "language English<asr_text>hello", 2)

        _result, payload = run(post)
        self.assertIsNotNone(payload, "the client was never reached")
        self.assertIn("max_tokens", payload, "the cap is not in the request")
        self.assertEqual(payload["max_tokens"], config.QWEN3_ASR_MAX_TOKENS)

    def test_cap_is_configurable(self):
        self.assertGreater(config.QWEN3_ASR_MAX_TOKENS, 65,
                           "normal output measured up to 65 tokens on a 14s clip")


class TruncatedReplyTests(unittest.TestCase):
    def test_truncated_reply_is_dropped_whole(self):
        text, detected, confidence = call(
            FakeReply("length", "language zh<asr_text>你真的，" * 200, 128))
        self.assertEqual(text, "", "a truncated response reached the pipeline")
        self.assertEqual(confidence, 0.0)
        self.assertIsNone(detected)

    def test_truncation_emits_one_event_with_the_token_count(self):
        with patch.object(asr_mod.perf, "emit") as emit:
            call(FakeReply("length", "language zh<asr_text>你真的，", 128))
        events = [c for c in emit.call_args_list if c.args[1] == "truncated"]
        self.assertEqual(len(events), 1, "the drop left no trace")
        self.assertEqual(emit.call_args.args[0], "asr")
        self.assertEqual(emit.call_args.kwargs["tokens"], 128)
        self.assertEqual(emit.call_args.kwargs["cap"], config.QWEN3_ASR_MAX_TOKENS)

    def test_missing_usage_reports_minus_one_not_zero(self):
        """A server that stopped reporting is a different finding from one that
        generated nothing, and merging them would make the counter quietly wrong."""
        self.assertEqual(asr_mod.usage_tokens({}), -1)
        self.assertEqual(asr_mod.usage_tokens({"usage": {}}), -1)
        self.assertEqual(asr_mod.usage_tokens({"usage": {"completion_tokens": "7"}}), 7)


class NormalReplyTests(unittest.TestCase):
    """The cap must not touch the path that works, which is 97% of calls."""

    def test_finished_reply_still_transcribes(self):
        text, detected, confidence = call(
            FakeReply("stop", "language English<asr_text>you call it a day", 9))
        self.assertEqual(text, "you call it a day")
        self.assertEqual(detected, "en")

    def test_long_finished_reply_is_not_mistaken_for_truncation(self):
        """A real 14s clip measured 65 tokens, so a long reply that finished must
        pass. Dropping on length alone would throw away real speech."""
        text, _detected, _ = call(
            FakeReply("stop", "language English<asr_text>" + "word " * 60, 65))
        self.assertTrue(text.startswith("word"), "a finished long reply was dropped")

    def test_no_truncation_event_on_the_normal_path(self):
        with patch.object(asr_mod.perf, "emit") as emit:
            call(FakeReply("stop", "language English<asr_text>hello", 2))
        self.assertFalse([c for c in emit.call_args_list if c.args[1] == "truncated"])


class AudioMsTests(unittest.TestCase):
    def test_header_constant_matches_what_wav_bytes_writes(self):
        """audio_ms on the truncation event is derived from the payload length, so a
        wrong header constant would report every clip 44ms shorter than it is."""
        wav = asr_mod._wav_bytes(audio(2.0))
        self.assertEqual(len(wav) % 2, 0)
        # 2.0s of int16 mono is 64000 bytes, so the payload is that plus a header.
        # The mod-4 tell is not a header check -- 64000 is divisible by 4 and 44 is too,
        # so both a payload with a header and one without land on 0. The first version
        # of this asserted len % 4 == 2 on the theory that a 44-byte header shifted the
        # remainder, and it failed against correct code.
        self.assertEqual(len(wav), 64000 + asr_mod.WAV_HEADER_BYTES)
        self.assertEqual((len(wav) - asr_mod.WAV_HEADER_BYTES) // 2,
                         int(config.SAMPLE_RATE * 2.0))

    def test_short_audio_is_not_sent(self):
        engine = asr_mod.Qwen3AsrEngine()
        self.assertEqual(engine.transcribe(np.zeros(800, dtype=np.float32), "en")[0], "")


if __name__ == "__main__":
    unittest.main()