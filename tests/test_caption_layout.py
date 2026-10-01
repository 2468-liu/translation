import unittest

from server.caption_window import fit_caption_width, wrap_caption


class CaptionLayoutTests(unittest.TestCase):
    def test_caption_wraps_to_at_most_two_lines(self):
        lines = wrap_caption("这是一个很长的实时字幕句子", 180, lambda text: len(text) * 20)

        self.assertLessEqual(len(lines), 2)
        self.assertTrue("".join(lines).startswith("这是一个很长的实时字幕"))

    def test_manual_width_never_auto_expands(self):
        self.assertEqual(fit_caption_width(1800, 760, 1920, locked=True), 760)

    def test_auto_width_is_screen_bounded(self):
        self.assertEqual(fit_caption_width(2400, 760, 1200, locked=False), 1120)

    def test_empty_caption_has_no_lines(self):
        self.assertEqual(wrap_caption("", 400, lambda text: len(text) * 20), [])


if __name__ == "__main__":
    unittest.main()
