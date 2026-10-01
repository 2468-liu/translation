import unittest

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

    def test_width_never_auto_expands(self):
        self.assertEqual(fit_caption_width(1800, 760, 1920, locked=True), 760)
        self.assertEqual(fit_caption_width(2400, 760, 1200, locked=False), 760)

    def test_empty_caption_has_no_lines(self):
        self.assertEqual(wrap_caption("", 400, lambda text: len(text) * 20), [])


if __name__ == "__main__":
    unittest.main()
