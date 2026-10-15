"""Unit tests for Chromium-style LCP Monotonic Freeze and Two-Line Rolling Buffer."""

import unittest
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from server.ui.caption_window import QtCaptionWindow
from server.ui.punct_gate import (
    ChromiumRollingBuffer,
    get_lcp_length,
    resolve_streaming_monotonic,
)


class ChromiumLcpMonotonicTests(unittest.TestCase):
    def test_lcp_length_calculation(self):
        self.assertEqual(get_lcp_length("hello world", "hello there"), 6)
        self.assertEqual(get_lcp_length("abc", "xyz"), 0)
        self.assertEqual(get_lcp_length("test", "test"), 4)
        self.assertEqual(get_lcp_length("", "anything"), 0)

    def test_streaming_monotonic_forward_growth(self):
        action, text = resolve_streaming_monotonic("Hello", "Hello world")
        self.assertEqual(action, "grow")
        self.assertEqual(text, "Hello world")

    def test_streaming_monotonic_anti_flicker_contraction(self):
        # Beam search temporarily drops tail characters -> hold, do not retract on screen!
        action, text = resolve_streaming_monotonic("Hello world", "Hello wor")
        self.assertEqual(action, "drop")
        self.assertEqual(text, "Hello world")

    def test_streaming_monotonic_tail_update(self):
        # Tail character revision with high common prefix
        action, text = resolve_streaming_monotonic("实时字幕识别一", "实时字幕识别二")
        self.assertEqual(action, "tail_update")
        self.assertEqual(text, "实时字幕识别二")

    def test_rolling_buffer_commit_and_stream(self):
        buf = ChromiumRollingBuffer()
        self.assertEqual(buf.get_composite_text(), "")

        # 1. First sentence commits
        buf.commit("这是第一句已完成的字幕。")
        self.assertEqual(buf.anchor_line, "这是第一句已完成的字幕。")
        self.assertEqual(buf.active_line, "")
        self.assertEqual(buf.get_composite_text(), "这是第一句已完成的字幕。")

        # 2. Second sentence streams in
        action, composite = buf.update_streaming("第二句正在输入")
        self.assertEqual(action, "grow")
        self.assertEqual(buf.anchor_line, "这是第一句已完成的字幕。")
        self.assertEqual(buf.active_line, "第二句正在输入")
        self.assertIn("这是第一句已完成的字幕。", composite)
        self.assertIn("第二句正在输入", composite)
        self.assertEqual(len(buf.get_lines_for_render()), 2)

        # 3. Second sentence commits -> rolls into anchor
        buf.commit("第二句已完成。")
        self.assertEqual(buf.anchor_line, "第二句已完成。")
        self.assertEqual(buf.active_line, "")
        self.assertEqual(buf.get_composite_text(), "第二句已完成。")


class QtCaptionWindowChromiumTests(unittest.TestCase):
    def setUp(self):
        self.window = QtCaptionWindow(scale=1.0, block_by_punct=False, rolling_two_lines=False)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        app.processEvents()
        self.window = None

    def test_anti_flicker_prevents_streaming_contraction(self):
        # 1. Normal growth
        res1 = self.window.set_text("今天天气真好", is_final=False)
        self.assertEqual(res1, "grow")
        self.assertEqual(self.window.line, "今天天气真好")

        # 2. Transient beam search contraction (shorter prefix)
        res2 = self.window.set_text("今天天气", is_final=False)
        # Should drop the contraction to avoid visual stutter/shrinkage!
        self.assertEqual(res2, "drop")
        self.assertEqual(self.window.line, "今天天气真好")

    def test_rolling_two_lines_integration(self):
        with patch("server.config.save_desktop_config"):
            self.window.set_rolling_two_lines(True)
            self.assertTrue(self.window.rolling_two_lines)

            # First utterance finalized
            self.window.set_text("上一句已定稿。", is_final=True)
            self.assertEqual(self.window.rolling_buffer.anchor_line, "上一句已定稿。")

            # Second utterance streams
            self.window.set_text("正在输入新内容", is_final=False)
            self.assertIn("上一句已定稿。", self.window.line)

            # Clear clears both lines
            self.window.clear()
            self.assertEqual(self.window.rolling_buffer.anchor_line, "")
            self.assertEqual(self.window.rolling_buffer.active_line, "")
            self.assertEqual(self.window.line, "")


if __name__ == "__main__":
    unittest.main()
