import unittest

from server.translate import collapse_translation_repeats


class TranslationCleanupTests(unittest.TestCase):
    def test_collapses_pathological_single_character_repetition(self):
        self.assertEqual(collapse_translation_repeats("啊啊啊啊啊啊"), "啊")

    def test_collapses_pathological_phrase_repetition(self):
        self.assertEqual(collapse_translation_repeats("非常好非常好非常好非常好非常好"), "非常好")

    def test_preserves_normal_repetition(self):
        text = "他非常非常认真地完成了工作。"

        self.assertEqual(collapse_translation_repeats(text), text)


if __name__ == "__main__":
    unittest.main()