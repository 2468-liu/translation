"""The ASR backend switch, and the Qwen3 reply framing it needs.

The switch exists so a bad run costs one environment variable rather than a revert,
so what matters here is that the two engines are indistinguishable from the
pipeline's side and that a mis-set value fails loudly instead of falling back.
"""

import unittest
from unittest.mock import patch

from server import asr as asr_mod
from server.config import ASR_BACKEND


class BackendSelectionTests(unittest.TestCase):
    def test_default_is_whisper(self):
        # The engine swap was measured but not adopted: the run that decides it has
        # not happened yet, so the default must stay where it was. Changing this
        # default without a recorded run is how a measured-but-unadopted change
        # becomes the default by accident.
        self.assertEqual(ASR_BACKEND, "whisper")

    def test_whisper_backend_builds_the_whisper_engine(self):
        with patch.object(asr_mod, "AsrEngine") as engine:
            self.assertIs(asr_mod.build_asr(), engine.return_value)

    def test_qwen3_backend_builds_the_qwen3_engine(self):
        with (
            patch.object(asr_mod.config, "ASR_BACKEND", "qwen3"),
            patch.object(asr_mod, "Qwen3AsrEngine") as engine,
        ):
            self.assertIs(asr_mod.build_asr(), engine.return_value)

    def test_unknown_backend_fails_loudly(self):
        # Silently falling back to whisper would produce a run whose name says one
        # thing and whose logs say another, which is the same failure shape as the
        # twelve "MTP on" arms that all ran with MTP off.
        with patch.object(asr_mod.config, "ASR_BACKEND", "whisper-large-v3"):
            with self.assertRaises(ValueError) as ctx:
                asr_mod.build_asr()
        self.assertIn("whisper", str(ctx.exception))
        self.assertIn("qwen3", str(ctx.exception))


class Qwen3ReplyFramingTests(unittest.TestCase):
    """The model prefixes its answer with the language and a marker.

    Left unparsed, the pipeline captions "language English" as if it were speech, so
    these pin the split rather than the model's format.
    """

    def test_marker_is_stripped_and_language_read(self):
        text, lang, confidence = asr_mod.parse_qwen3_asr(
            "language English<asr_text>I think you should go with your instincts.")
        self.assertEqual(text, "I think you should go with your instincts.")
        self.assertEqual(lang, "en")
        self.assertEqual(confidence, 1.0)

    def test_a_reply_without_the_marker_still_yields_text(self):
        text, lang, _ = asr_mod.parse_qwen3_asr("just some text")
        self.assertEqual(text, "just some text")
        self.assertIsNone(lang)

    def test_an_unknown_language_name_is_not_guessed(self):
        # Setting the recogniser's language from a name we do not recognise would
        # silently force every later call onto the wrong decoder.
        _, lang, _ = asr_mod.parse_qwen3_asr("language Klingon<asr_text>hello")
        self.assertIsNone(lang)

    def test_chinese_and_japanese_names_map(self):
        self.assertEqual(asr_mod.parse_qwen3_asr("language Chinese<asr_text>x")[1], "zh")
        self.assertEqual(asr_mod.parse_qwen3_asr("language Japanese<asr_text>x")[1], "ja")

    def test_colon_prefix_stripped_and_language_read(self):
        text, lang, conf = asr_mod.parse_qwen3_asr("language: Japanese\nこんにちは")
        self.assertEqual(text, "こんにちは")
        self.assertEqual(lang, "ja")
        self.assertEqual(conf, 1.0)

    def test_language_code_mappings_are_comprehensive(self):
        self.assertEqual(asr_mod._CODE_TO_FULL_LANG["ja"], "Japanese")
        self.assertEqual(asr_mod._CODE_TO_FULL_LANG["zh"], "Chinese")
        self.assertEqual(asr_mod._CODE_TO_FULL_LANG["en"], "English")
        self.assertEqual(asr_mod._CODE_TO_FULL_LANG["ko"], "Korean")
        self.assertEqual(asr_mod._CODE_TO_FULL_LANG["fr"], "French")
        self.assertEqual(asr_mod._CODE_TO_FULL_LANG["de"], "German")
        self.assertEqual(asr_mod._CODE_TO_FULL_LANG["es"], "Spanish")
        self.assertEqual(asr_mod._CODE_TO_FULL_LANG["ru"], "Russian")

    def test_empty_answer_scores_zero(self):
        _, _, confidence = asr_mod.parse_qwen3_asr("language English<asr_text>")
        self.assertEqual(confidence, 0.0)

    def test_a_framed_hallucination_is_still_caught(self):
        # The hallucination filter is what keeps music from becoming "thanks for
        # watching", and the framing must not let text slip past it.
        raw = "language English<asr_text>Thanks for watching!"
        text, _, _ = asr_mod.parse_qwen3_asr(raw)
        self.assertTrue(asr_mod.is_hallucination(text))


class PcmEncodingTests(unittest.TestCase):
    """The bytes have to be identical between calls for any prefix cache to hit.

    That cache does not help us today -- llama.cpp retains one prompt and our
    partials grow past it every time -- so this is not a speed claim. It is pinned
    because it would be a silent correctness issue for any future incremental mode,
    and because the probe that established it also produced a clean false negative
    when it compared wav containers instead of PCM payloads: the 44-byte header
    carries the data length, so every clip differs in the first bytes and a
    prefix test fails on the container before it reaches the audio.
    """

    def test_pcm_bytes_are_a_pure_function_of_the_samples(self):
        import numpy as np

        audio = np.linspace(-1.0, 1.0, 4000, dtype=np.float32)
        first = asr_mod._pcm_bytes(audio)
        self.assertEqual(first, asr_mod._pcm_bytes(audio.copy()))
        self.assertNotEqual(first, asr_mod._pcm_bytes(audio * 0.5))

    def test_a_longer_clip_starts_with_the_shorter_one(self):
        import numpy as np

        short = np.linspace(-0.5, 0.5, 2000, dtype=np.float32)
        long = np.concatenate([short, np.linspace(0.5, 0.9, 2000).astype(np.float32)])
        self.assertTrue(
            asr_mod._pcm_bytes(long).startswith(asr_mod._pcm_bytes(short)))

    def test_clipping_is_applied_before_quantisation(self):
        import numpy as np

        loud = np.array([2.0, -2.0, 0.0], dtype=np.float32)
        self.assertEqual(
            asr_mod._pcm_bytes(loud),
            asr_mod._pcm_bytes(np.array([1.0, -1.0, 0.0], dtype=np.float32)),
        )


if __name__ == "__main__":
    unittest.main()
