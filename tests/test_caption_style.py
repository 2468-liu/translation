import unittest

from server.ws_server import style_font_color


class CaptionStyleTests(unittest.TestCase):
    def test_accepts_hex_caption_color(self):
        self.assertEqual(style_font_color({"type": "style", "color": "#12aBcD"}), "#12aBcD")

    def test_rejects_invalid_caption_color(self):
        self.assertIsNone(style_font_color({"type": "style", "color": "red"}))
        self.assertIsNone(style_font_color({"type": "style", "color": "#12345"}))


if __name__ == "__main__":
    unittest.main()