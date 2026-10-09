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

    def test_dismiss_behavior_in_text_only_and_boxed_modes(self):
        # 1. In text-only mode: window withdraws completely
        win_text = _Caption.__new__(_Caption)
        win_text._dismiss_job = None
        win_text._pending = ""
        win_text.line = "Hello"
        win_text._drawn_text = "Hello"
        win_text.text_only = True
        win_text.visible = True
        withdraw_called = []
        win_text.root = type("MockRoot", (), {"withdraw": lambda self=None: withdraw_called.append(True)})()

        win_text._on_dismiss_timeout()
        self.assertEqual(win_text.line, "")
        self.assertFalse(win_text.visible)
        self.assertEqual(withdraw_called, [True])

        # 2. In normal black box mode: text clears on canvas, window stays visible (no withdraw)
        win_box = _Caption.__new__(_Caption)
        win_box._dismiss_job = None
        win_box._pending = ""
        win_box.line = "Hello"
        win_box._drawn_text = "Hello"
        win_box.text_only = False
        win_box.visible = True
        delete_called = []
        win_box.canvas = type("MockCanvas", (), {"delete": lambda self=None, tag="": delete_called.append(tag)})()
        box_withdraw_called = []
        win_box.root = type("MockRoot", (), {"withdraw": lambda self=None: box_withdraw_called.append(True)})()

        win_box._on_dismiss_timeout()
        self.assertEqual(win_box.line, "")
        self.assertTrue(win_box.visible)
        self.assertEqual(delete_called, ["all"])
        self.assertEqual(box_withdraw_called, [], "black box must not withdraw to prevent flashing")


if __name__ == "__main__":
    unittest.main()