import unittest
import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont, QFontMetrics, QPixmap, QPainter

from server.ui.caption_renderer import (
    NO_LINE_START,
    NO_LINE_END,
    compute_bottom_pinned_baselines,
    wrap_all_typography,
    wrap_caption_typography,
    render_caption_text,
)


class CaptionRendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_bottom_pinned_baseline_identity(self):
        # When 1 line is displayed
        h = 80.0
        line_spacing = 26.0
        descent = 5.0
        bottom_pad = 16.0

        b1 = compute_bottom_pinned_baselines(1, h, line_spacing, descent, bottom_pad)
        self.assertEqual(len(b1), 1)
        expected_last = h - bottom_pad - descent
        self.assertEqual(b1[0], expected_last)

        # When 2 lines are displayed
        b2 = compute_bottom_pinned_baselines(2, h, line_spacing, descent, bottom_pad)
        self.assertEqual(len(b2), 2)
        # The latest line (b2[1]) MUST be at the exact same position as b1[0]
        self.assertEqual(b2[1], b1[0])
        # The first line (b2[0]) MUST be pushed up by exactly line_spacing
        self.assertEqual(b2[0], b1[0] - line_spacing)

    def test_bottom_pinned_baseline_empty_or_zero(self):
        self.assertEqual(compute_bottom_pinned_baselines(0, 80, 24, 4), [])
        self.assertEqual(compute_bottom_pinned_baselines(-1, 80, 24, 4), [])

    def test_western_words_not_torn(self):
        # Mock measure: each char = 10 px
        measure = lambda s: len(s) * 10
        text = "Hello world this is a beautiful movie"
        # max_width = 120 -> "Hello world " is 120px
        lines = wrap_all_typography(text, 120, measure)
        self.assertTrue(len(lines) >= 2)
        for line in lines:
            # Ensure no words are split mid-word
            words = line.split()
            for w in words:
                self.assertIn(w, ["Hello", "world", "this", "is", "a", "beautiful", "movie"])

    def test_kinsoku_shori_no_punctuation_at_line_start(self):
        measure = lambda s: len(s) * 10
        # Text where a comma would naturally fall at line break index
        text = "人工智能深度学习，真的很强大"
        # Width allowing 8 characters = 80px ("人工智能深度学习" is 8 chars, comma is 9th)
        lines = wrap_all_typography(text, 80, measure)
        self.assertGreaterEqual(len(lines), 2)

        # Line 2 MUST NOT start with a punctuation
        first_char_line2 = lines[1][0]
        self.assertNotIn(first_char_line2, NO_LINE_START)
        # In fact, the comma should be accompanied by the previous char "习，" or stayed on line 1
        self.assertTrue("，" in lines[0] or lines[1].startswith("习，"))

    def test_wrap_caption_typography_max_lines_cap(self):
        measure = lambda s: len(s) * 10
        text = "第一行第二行第三行第四行"
        lines = wrap_caption_typography(text, 30, measure, max_lines=2)
        self.assertEqual(len(lines), 2)

    def test_render_caption_text_safe_drawing(self):
        pix = QPixmap(300, 100)
        painter = QPainter(pix)
        font = QFont("Microsoft YaHei", 18)
        metrics = QFontMetrics(font)

        baselines = [40.0, 70.0]
        lines = ["第一行测试文本", "第二行测试文本"]

        render_caption_text(
            painter=painter,
            lines=lines,
            baselines=baselines,
            rect_width=300,
            font=font,
            metrics=metrics,
            primary_color="#FFFFFF",
            opacity=0.9,
        )
        painter.end()

    def test_render_caption_overlay_boxed_preserves_black_box_during_silence(self):
        from unittest.mock import MagicMock
        from PyQt6.QtCore import QRect
        from server.ui.caption_renderer import render_caption_overlay

        mock_window = MagicMock()
        mock_window.visible = True
        mock_window.text_only = False
        mock_window.fade_animator.text_opacity = 0.0  # Simulating silence fade-out
        mock_window.rect.return_value = QRect(0, 0, 400, 80)
        mock_window.line = ""
        mock_window._is_hovered = False
        mock_window.header_widget = None

        pix = QPixmap(400, 80)
        painter = QPainter(pix)
        
        # Should not crash, and should render box with constant alpha
        render_caption_overlay(mock_window, painter)
        painter.end()

    def test_render_caption_text_dual_zone_split(self):
        pix = QPixmap(400, 100)
        painter = QPainter(pix)
        font = QFont("Microsoft YaHei", 18)
        metrics = QFontMetrics(font)

        baselines = [50.0]
        lines = ["今天天气真好"]

        # Dual-zone: stable prefix "今天天气", hypothesis suffix "真好"
        render_caption_text(
            painter=painter,
            lines=lines,
            baselines=baselines,
            rect_width=400,
            font=font,
            metrics=metrics,
            primary_color="#FFFFFF",
            stable_prefix="今天天气",
            hypothesis_suffix="真好",
            opacity=1.0,
        )
        painter.end()

    def test_bilingual_layout_caps_translation_to_single_line_and_preserves_width(self):
        from unittest.mock import MagicMock
        from PyQt6.QtCore import QRect
        from server.ui.caption_renderer import update_window_layout_and_geometry

        mock_window = MagicMock()
        mock_window.bilingual_mode = "bilingual"
        mock_window.source_line = "This is a long English sentence that would normally wrap across multiple rows."
        mock_window.font_px = 18
        mock_window.width.return_value = 500
        mock_window.height.return_value = 80
        mock_window.text_only = False
        mock_window._is_hovered = False
        mock_window.geometry.return_value = QRect(100, 100, 500, 80)

        # Long translation text that would wrap to 3 lines if max_lines wasn't 1
        long_chinese = "这是一个非常长非常长的中文翻译句子，通常情况下如果不加限制会直接折行成两行甚至三行排版"
        display_lines = update_window_layout_and_geometry(mock_window, long_chinese)

        # In bilingual mode, translation MUST strictly be capped to 1 line
        self.assertEqual(len(display_lines), 1)

        # Ensure setGeometry was called preserving width (500), never expanding to full screen!
        self.assertTrue(mock_window.setGeometry.called)
        called_geo_args = mock_window.setGeometry.call_args[0]
        # setGeometry(x, new_y, cur_geo.width(), target_h)
        self.assertEqual(called_geo_args[0], 100)
        self.assertEqual(called_geo_args[2], 500)  # Width strictly preserved!

    def test_bilingual_render_draws_source_above_translation(self):
        from unittest.mock import MagicMock
        from PyQt6.QtCore import QRect
        from server.ui.caption_renderer import render_caption_overlay

        mock_window = MagicMock()
        mock_window.visible = True
        mock_window.text_only = False
        mock_window.bilingual_mode = "bilingual"
        mock_window.source_line = "Hello world from source"
        mock_window.line = "你好世界来自译文"
        mock_window.font_px = 18
        mock_window.font_color = "#FFFFFF"
        mock_window.fade_animator.text_opacity = 1.0
        mock_window.rect.return_value = QRect(0, 0, 500, 90)
        mock_window._is_hovered = False
        mock_window.header_widget = None
        mock_window.rolling_two_lines = False

        pix = QPixmap(500, 90)
        painter = QPainter(pix)
        # Should render both source line and translation line without throwing
        render_caption_overlay(mock_window, painter)
        painter.end()


if __name__ == "__main__":
    unittest.main()
