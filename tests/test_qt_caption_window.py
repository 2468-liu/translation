"""Unit tests for PyQt6-based subtitle overlay window (QtCaptionWindow)."""

import json
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

# Ensure QApplication instance exists for widget testing
app = QApplication.instance() or QApplication([])

from server.qt_caption_window import QtCaptionWindow


class QtCaptionWindowTests(unittest.TestCase):
    def setUp(self):
        self.window = QtCaptionWindow(scale=1.0)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()

    def test_initial_state(self):
        self.assertFalse(self.window.text_only)
        self.assertFalse(self.window.visible)
        self.assertEqual(self.window.font_px, 20)
        self.assertEqual(self.window.font_color, "#ffffff")
        self.assertEqual(self.window.dismiss_ms, 5000)

    def test_apply_text_only_toggles_state(self):
        self.window.apply_text_only(True)
        self.assertTrue(self.window.text_only)
        self.assertTrue(self.window.text_only_cb.isChecked())

        self.window.apply_text_only(False)
        self.assertFalse(self.window.text_only)
        self.assertFalse(self.window.text_only_cb.isChecked())

    def test_apply_font_clamps_values(self):
        self.window.apply_font(28)
        self.assertEqual(self.window.font_px, 28)

        # Clamping
        self.window.apply_font(100)
        self.assertLessEqual(self.window.font_px, 48)

        self.window.apply_font(5)
        self.assertGreaterEqual(self.window.font_px, 14)

    def test_apply_color(self):
        self.window.apply_color("#ffdd00")
        self.assertEqual(self.window.font_color, "#ffdd00")

    def test_apply_dismiss_timeout(self):
        self.window.apply_dismiss_timeout(8000)
        self.assertEqual(self.window.dismiss_ms, 8000)

        self.window.apply_dismiss_timeout(-100)
        self.assertEqual(self.window.dismiss_ms, 0)

        self.window.apply_dismiss_timeout(60000)
        self.assertEqual(self.window.dismiss_ms, 30000)

    def test_dismiss_behavior_in_text_only_mode(self):
        self.window.text_only = True
        self.window.show()
        self.window.line = "Subtitles text"
        self.assertTrue(self.window.isVisible())

        self.window._on_dismiss_timeout()
        self.assertEqual(self.window.line, "")
        self.assertFalse(self.window.isVisible())

    def test_dismiss_behavior_in_boxed_mode(self):
        self.window.text_only = False
        self.window.show()
        self.window.line = "Subtitles text"
        self.assertTrue(self.window.isVisible())

        self.window._on_dismiss_timeout()
        self.assertEqual(self.window.line, "")
        # Black box stays visible, text cleared
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window.visible)

    def test_clear_in_text_only_and_boxed_modes(self):
        # 1. In text only mode: clear hides window
        self.window.text_only = True
        self.window.show()
        self.window.line = "Some speech text"
        self.window.clear()
        self.assertEqual(self.window.line, "")
        self.assertFalse(self.window.visible)
        self.assertFalse(self.window.isVisible())

        # 2. In boxed mode: clear leaves box visible, text empty
        self.window.text_only = False
        self.window.show()
        self.window.line = "Some speech text"
        self.window.clear()
        self.assertEqual(self.window.line, "")
        self.assertTrue(self.window.visible)
        self.assertTrue(self.window.isVisible())

    def test_begin_segment_cancels_dismiss_timer(self):
        self.window.show()
        self.window.line = "Active text"
        self.window._dismiss_timer.start(5000)
        self.assertTrue(self.window._dismiss_timer.isActive())

        self.window.begin_segment()
        self.assertFalse(self.window._dismiss_timer.isActive())

    def test_apply_dismiss_timeout_rearmer(self):
        self.window.show()
        self.window.line = "Active text"
        self.window.apply_dismiss_timeout(6000)
        self.assertEqual(self.window.dismiss_ms, 6000)
        self.assertTrue(self.window._dismiss_timer.isActive())

    def test_set_text_lifecycle(self):
        # 1. First line
        res = self.window.set_text("Hello world", is_final=False)
        self.assertEqual(res, "first")
        self.assertEqual(self.window.line, "Hello world")

        # 2. Growth
        res = self.window.set_text("Hello world, welcome", is_final=False)
        self.assertEqual(res, "grow")
        self.assertEqual(self.window.line, "Hello world, welcome")

        # 3. Same
        res = self.window.set_text("Hello world, welcome", is_final=False)
        self.assertEqual(res, "same")

    def test_command_dispatcher(self):
        self.window._handle_command("text", "New speech text", 0.0, 0.0, False)
        self.assertEqual(self.window.line, "New speech text")

        self.window._handle_command("status", "英语", 0.0, 0.0, False)
        self.assertEqual(self.window.status, "英语")

        self.window._handle_command("clear", None, 0.0, 0.0, False)
        self.assertEqual(self.window.line, "")

        self.window._handle_command("text_only", True, 0.0, 0.0, False)
        self.assertTrue(self.window.text_only)


if __name__ == "__main__":
    unittest.main()

