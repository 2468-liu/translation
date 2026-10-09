import unittest

from server.caption_window import _Caption, set_dismiss_timeout
from server.ws_server import style_font_color


class CaptionStyleTests(unittest.TestCase):
    def test_accepts_hex_caption_color(self):
        self.assertEqual(style_font_color({"type": "style", "color": "#12aBcD"}), "#12aBcD")

    def test_rejects_invalid_caption_color(self):
        self.assertIsNone(style_font_color({"type": "style", "color": "red"}))
        self.assertIsNone(style_font_color({"type": "style", "color": "#12345"}))

    def test_dismiss_timeout_clamps_values(self):
        win = _Caption.__new__(_Caption)
        win.dismiss_ms = 5000
        win._dismiss_job = None
        win.line = ""
        win.visible = False

        win.apply_dismiss_timeout(8000)
        self.assertEqual(win.dismiss_ms, 8000)

        win.apply_dismiss_timeout(-500)
        self.assertEqual(win.dismiss_ms, 0)

        win.apply_dismiss_timeout(50000)
        self.assertEqual(win.dismiss_ms, 30000)


if __name__ == "__main__":
    unittest.main()