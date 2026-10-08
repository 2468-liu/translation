import unittest
from pathlib import Path

from server.caption_window import fit_caption_width, wrap_caption


class CaptionLayoutTests(unittest.TestCase):
    def test_caption_wraps_to_at_most_two_lines(self):
        lines = wrap_caption("这是一个很长的实时字幕句子", 180, lambda text: len(text) * 20)

        self.assertLessEqual(len(lines), 2)
        self.assertTrue("".join(lines).startswith("这是一个很长的实时字幕"))

    def test_long_caption_scrolls_to_the_latest_two_lines(self):
        lines = wrap_caption("第一行第二行第三行", 60, lambda text: len(text) * 20)

        self.assertEqual(lines, ["第二行", "第三行"])

    def test_short_caption_stays_on_one_line(self):
        self.assertEqual(wrap_caption("短句", 180, lambda text: len(text) * 20), ["短句"])

    def test_latin_words_wrap_without_splitting_words(self):
        lines = wrap_caption("watch the next scene", 180, lambda text: len(text) * 10)

        self.assertEqual(lines, ["watch the next", "scene"])

    def test_width_never_auto_expands(self):
        self.assertEqual(fit_caption_width(1800, 760, 1920, locked=True), 760)
        self.assertEqual(fit_caption_width(2400, 760, 1200, locked=False), 760)

    def test_empty_caption_has_no_lines(self):
        self.assertEqual(wrap_caption("", 400, lambda text: len(text) * 20), [])

    def test_caption_window_shows_without_activation(self):
        source = (Path(__file__).resolve().parents[1] / "server" / "caption_window.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("WS_EX_NOACTIVATE", source)
        self.assertIn("SW_SHOWNOACTIVATE", source)
        self.assertIn("self._strip_frame()\n        if ctypes:", source)
        self.assertNotIn("SetForegroundWindow", source)

    def test_hold_time_uses_speech_pace_bands(self):
        from server.caption_window import speech_hold_factor

        self.assertAlmostEqual(speech_hold_factor(0.7), 0.62, places=2)
        self.assertAlmostEqual(speech_hold_factor(1.0), 0.80, places=2)
        self.assertAlmostEqual(speech_hold_factor(1.35), 0.98, places=2)

    def test_local_inference_prefers_gpu(self):
        from server.config import ASR_COMPUTE_TYPE, ASR_DEVICE, LLAMA_NGL_DEFAULT

        self.assertEqual(ASR_DEVICE, "cuda")
        self.assertEqual(ASR_COMPUTE_TYPE, "float16")
        self.assertEqual(LLAMA_NGL_DEFAULT, 99)

    def test_no_removed_translation_engine_is_referenced(self):
        """The NLLB/CTranslate2 translator is gone; nothing may reach for it.

        It was a `USE_NLLB = False` branch in stream_translation with a whole
        engine module behind it and models that were no longer on disk, so it
        could never run and could only be mistaken for a live path. The
        assertion is on the source rather than on an import because the failure
        mode was a hardcoded constant, not a missing module.
        """
        import server.config as config
        import server.translate as translate

        self.assertFalse(hasattr(config, "USE_NLLB"))
        self.assertFalse(hasattr(config, "MT_DIR"))
        for module in (config, translate):
            source = Path(module.__file__).read_text(encoding="utf-8-sig")
            self.assertNotIn("nllb", source.lower())

    def test_llama_gpu_layers_scale_with_vram(self):
        from server.config import resolve_llama_runtime

        self.assertEqual(resolve_llama_runtime(4096), (60, 1024))
        self.assertEqual(resolve_llama_runtime(6000), (99, 1536))
        self.assertEqual(resolve_llama_runtime(8192), (99, 1536))
        self.assertEqual(resolve_llama_runtime(12000), (99, 1536))


if __name__ == "__main__":
    unittest.main()
