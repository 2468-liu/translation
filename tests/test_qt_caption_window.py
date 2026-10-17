"""Unit tests for PyQt6-based subtitle overlay window (QtCaptionWindow)."""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

if sys.platform.startswith("linux"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("GDK_BACKEND", "x11")
    os.environ.setdefault("QT_LOGGING_RULES", "*.debug=false;qt.qpa.*=false;qt.text.font.*=false")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

# Ensure QApplication instance exists for widget testing
app = QApplication.instance() or QApplication([])

from server.qt_caption_window import QtCaptionWindow


class QtCaptionWindowTests(unittest.TestCase):
    def setUp(self):
        self.window = QtCaptionWindow(scale=1.0, block_by_punct=False)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        app.processEvents()
        self.window = None

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
        self.assertEqual(res, "grow")
        self.assertEqual(self.window.line, "Hello world")

        # 2. Growth
        res = self.window.set_text("Hello world, welcome", is_final=False)
        self.assertEqual(res, "grow")
        self.assertEqual(self.window.line, "Hello world, welcome")

        # 3. Same
        res = self.window.set_text("Hello world, welcome", is_final=False)
        self.assertEqual(res, "drop")

    def test_command_dispatcher(self):
        self.window._handle_command("text", "New speech text", 0.0, 0.0, False)
        self.assertEqual(self.window.line, "New speech text")

        self.window._handle_command("status", "en", 0.0, 0.0, False)
        self.assertEqual(self.window.status, "英语 → 中文")

        self.window._handle_command("clear", None, 0.0, 0.0, False)
        self.assertEqual(self.window.line, "")

        self.window._handle_command("text_only", True, 0.0, 0.0, False)
        self.assertTrue(self.window.text_only)

    def test_block_mode_punct_boundary(self):
        self.window._block_by_punct = True
        # Without punctuation: returns block_wait, does not display immediately
        res = self.window.set_text("你好世界", is_final=False)
        self.assertEqual(res, "block_wait")
        self.assertEqual(self.window.line, "")

        # With punctuation: boundary detected, applies line
        res = self.window.set_text("你好世界，这是测试。", is_final=False)
        self.assertEqual(res, "block")
        self.assertEqual(self.window.line, "你好世界，这是测试。")
        self.assertTrue(self.window.visible)

    def test_block_mode_final_draws(self):
        self.window._block_by_punct = True
        res = self.window.set_text("最终完整句子", is_final=True)
        self.assertEqual(res, "final")
        self.assertEqual(self.window.line, "最终完整句子")
    def test_clear_and_subsequent_text_resumes_display(self):
        # In text_only mode: clear withdraws, but next text must show window again (closed must NOT be True)
        self.window.text_only = True
        self.window.show()
        self.window.set_text("Initial speech", is_final=False)
        self.window.clear()
        self.assertFalse(self.window.visible)
        self.assertFalse(self.window.closed)

        # Incoming new text must re-show the window
        res = self.window.set_text("Resumed speech", is_final=False)
        self.assertEqual(res, "grow")
        self.assertEqual(self.window.line, "Resumed speech")
        self.assertTrue(self.window.visible)
        self.assertTrue(self.window.isVisible())

    def test_dismiss_timeout_and_subsequent_text_resumes_display(self):
        # In text_only mode: timeout withdraws, but next text must show window again
        self.window.text_only = True
        self.window.show()
        self.window.set_text("Active speech", is_final=False)
        self.window._on_dismiss_timeout()
        self.assertFalse(self.window.visible)
        self.assertFalse(self.window.closed)

        # Incoming new text wakes up window
        res = self.window.set_text("Next phrase", is_final=False)
        self.assertEqual(res, "grow")
        self.assertEqual(self.window.line, "Next phrase")
        self.assertTrue(self.window.visible)
        self.assertTrue(self.window.isVisible())

    def test_explicit_hide_requires_arm(self):
        self.window.show()
        self.window.set_text("Visible text", is_final=False)
        self.window.hide()
        self.assertTrue(self.window.closed)
        self.assertFalse(self.window.visible)

        # Text while closed is suppressed (window stays invisible)
        self.window.set_text("Ignored text", is_final=False)
        self.assertFalse(self.window.visible)
        self.assertFalse(self.window.isVisible())

        # Arming re-enables display
        self.window.arm()
        self.assertFalse(self.window.closed)
        self.window.set_text("Accepted text", is_final=False)
        self.assertEqual(self.window.line, "Accepted text")
        self.assertTrue(self.window.visible)

    def test_horizontal_edge_detection(self):
        w = self.window.width()
        geo = self.window.geometry()
        from PyQt6.QtCore import QPoint

        # Left edge
        left_pt = QPoint(geo.x() + 5, geo.y() + 20)
        self.assertEqual(self.window._get_edge_at(left_pt), "left")

        # Right edge
        right_pt = QPoint(geo.x() + w - 5, geo.y() + 20)
        self.assertEqual(self.window._get_edge_at(right_pt), "right")

        # Body center
        center_pt = QPoint(geo.x() + w // 2, geo.y() + 20)
        self.assertEqual(self.window._get_edge_at(center_pt), "")

    def test_maximize_and_reset_width(self):
        # Maximize width
        self.window._maximize_width()
        expected_max_w = max(360, self.window.screen_width - 60)
        self.assertEqual(self.window.width(), expected_max_w)

        # Reset width
        self.window._reset_default_width()
        self.assertLess(self.window.width(), expected_max_w)
        self.assertGreaterEqual(self.window.width(), 360)

    def test_double_click_edge_toggles_fullscreen_width(self):
        from PyQt6.QtCore import QPointF
        from PyQt6.QtGui import QMouseEvent
        from PyQt6.QtCore import Qt

        self.window.resize(500, self.window.height())
        geo = self.window.geometry()
        right_pos = QPointF(float(geo.x() + self.window.width() - 5), float(geo.y() + 20))
        event = QMouseEvent(
            QMouseEvent.Type.MouseButtonDblClick,
            right_pos,
            right_pos,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.window.mouseDoubleClickEvent(event)
        expected_max_w = max(360, self.window.screen_width - 60)
        self.assertEqual(self.window.width(), expected_max_w)

        # Double click again resets to standard width
        right_pos2 = QPointF(float(self.window.geometry().x() + self.window.width() - 5), float(geo.y() + 20))
        event2 = QMouseEvent(
            QMouseEvent.Type.MouseButtonDblClick,
            right_pos2,
            right_pos2,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.window.mouseDoubleClickEvent(event2)
        self.assertLess(self.window.width(), expected_max_w)

    def test_set_block_by_punct_toggles_and_persists(self):
        self.window.set_block_by_punct(True)
        self.assertTrue(self.window._block_by_punct)

        self.window.set_block_by_punct(False)
        self.assertFalse(self.window._block_by_punct)

        self.window.set_block_by_punct(True)
        self.assertTrue(self.window._block_by_punct)

    def test_punct_gate_handles_punctuation_instantly(self):
        self.window.set_block_by_punct(True)
        # Without punctuation -> waits
        action1 = self.window.set_text("你好世界", is_final=False)
        self.assertEqual(action1, "block_wait")

        # With punctuation -> immediate block
        action2 = self.window.set_text("你好世界，很高兴认识你。", is_final=False)
        self.assertEqual(action2, "block")
        self.assertEqual(self.window.line, "你好世界，很高兴认识你。")

        # Final -> immediate final
        action3 = self.window.set_text("你好世界，很高兴认识你。明天见！", is_final=True)
        self.assertEqual(action3, "final")
        self.assertEqual(self.window.line, "你好世界，很高兴认识你。明天见！")

    def test_paint_event_when_hovered_does_not_crash(self):
        # Must execute cleanly without NameError: QPointF or any other exceptions
        self.window.visible = True
        self.window.text_only = False
        self.window._is_hovered = True
        self.window.line = "测试渲染与拖拽边框"
        self.window.repaint()

        # Also test transparent text-only hover mode
        self.window.text_only = True
        self.window.repaint()

    def test_horizontal_drag_resizing(self):
        from PyQt6.QtCore import QPointF, Qt
        from PyQt6.QtGui import QMouseEvent

        w0 = self.window.width()
        geo = self.window.geometry()

        # Press on right edge
        edge_pt = QPointF(float(geo.x() + w0 - 5), float(geo.y() + 20))
        press_event = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            edge_pt,
            edge_pt,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.window.mousePressEvent(press_event)
        self.assertEqual(self.window._resize_mode, "right")

        # Move rightwards by +100px
        drag_pt = QPointF(float(geo.x() + w0 - 5 + 100), float(geo.y() + 20))
        move_event = QMouseEvent(
            QMouseEvent.Type.MouseMove,
            drag_pt,
            drag_pt,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.window.mouseMoveEvent(move_event)
        self.assertEqual(self.window.width(), min(w0 + 100, self.window.screen_width - 20))

        # Release mouse
        release_event = QMouseEvent(
            QMouseEvent.Type.MouseButtonRelease,
            drag_pt,
            drag_pt,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.window.mouseReleaseEvent(release_event)
        self.assertIsNone(self.window._resize_mode)

    def test_safe_close_tray_menu_closes_submenus_and_main_menu(self):
        self.assertIsNotNone(self.window._tray_menu)
        self.window._safe_close_tray_menu()
        self.assertFalse(self.window._tray_menu.isVisible())

    def test_tray_activated_context_calls_show_tray_menu(self):
        from PyQt6.QtWidgets import QSystemTrayIcon
        with patch.object(self.window, "_show_tray_menu") as mock_show:
            self.window._on_tray_activated(QSystemTrayIcon.ActivationReason.Context)
            mock_show.assert_called_once()

    def test_final_high_similarity_triggers_diff(self):
        self.window._block_by_punct = False
        self.window.set_text("正在测试实时字幕识别", is_final=False)
        self.assertEqual(self.window.line, "正在测试实时字幕识别")
        res = self.window.set_text("正在测试实时字幕识别！", is_final=True)
        self.assertEqual(res, "final")
        self.assertEqual(self.window._trace_action, "final_diff")
        self.assertEqual(self.window.line, "正在测试实时字幕识别！")

    def test_final_low_similarity_triggers_overwrite(self):
        self.window._block_by_punct = False
        self.window.set_text("正在测试实时字幕识别", is_final=False)
        res = self.window.set_text("彻底推翻重新翻译的一句话", is_final=True)
        self.assertEqual(res, "final")
        self.assertEqual(self.window._trace_action, "final_overwrite")
        self.assertEqual(self.window.line, "彻底推翻重新翻译的一句话")

    def test_punct_gate_final_similarity_branches(self):
        self.window._block_by_punct = True
        self.window.set_text("语音识别初稿草稿，", is_final=False)
        self.assertEqual(self.window.line, "语音识别初稿草稿，")
        res_high = self.window.set_text("语音识别初稿草稿。", is_final=True)
        self.assertEqual(res_high, "final")
        self.assertEqual(self.window._trace_action, "final_diff")

        res_low = self.window.set_text("完全不同的新句子。", is_final=True)
        self.assertEqual(res_low, "final")
        self.assertEqual(self.window._trace_action, "final_overwrite")

    def test_pin_invokes_safely(self):
        self.window.visible = True
        self.window._pin()

    def test_set_status_multilingual(self):
        self.window.set_status("en", "zh")
        self.assertEqual(self.window.status, "英语 → 中文")
        self.assertEqual(self.window.status_label.text(), "英语 → 中文")

        self.window.set_status("ja", "fr")
        self.assertEqual(self.window.status, "日语 → 法语")

        self.window.set_status("auto", "ja")
        self.assertEqual(self.window.status, "自动 → 日语")

        self.window.set_status("auto", "zh")
        self.assertEqual(self.window.status, "")

        self.window.set_status("yue", "en")
        self.assertEqual(self.window.status, "粤语 → 英语")

        self.window.set_status("tl", "zh")
        self.assertEqual(self.window.status, "菲律宾语 → 中文")

        self.window.set_status("已暂停")
        self.assertEqual(self.window.status, "已暂停")
        self.assertEqual(self.window.status_label.text(), "⏸ 已暂停")

    def test_caption_window_right_click_shows_context_menu_not_tray_menu(self):
        from PyQt6.QtGui import QContextMenuEvent, QMouseEvent
        from PyQt6.QtCore import QPointF, QPoint

        with patch.object(self.window, "_show_context_menu") as mock_ctx, \
             patch.object(self.window, "_show_tray_menu") as mock_tray:
            # 1. Test mousePressEvent with RightButton
            press_evt = QMouseEvent(
                QMouseEvent.Type.MouseButtonPress,
                QPointF(50.0, 30.0),
                QPointF(50.0, 30.0),
                Qt.MouseButton.RightButton,
                Qt.MouseButton.RightButton,
                Qt.KeyboardModifier.NoModifier,
            )
            self.window.mousePressEvent(press_evt)
            mock_ctx.assert_called_once()
            mock_tray.assert_not_called()

            # 2. Test contextMenuEvent
            mock_ctx.reset_mock()
            ctx_evt = QContextMenuEvent(
                QContextMenuEvent.Reason.Mouse,
                QPoint(50, 30),
                QPoint(50, 30),
            )
            self.window.contextMenuEvent(ctx_evt)
            mock_ctx.assert_called_once()
            mock_tray.assert_not_called()

            # 3. Test header widget right-click
            mock_ctx.reset_mock()
            header_evt = QMouseEvent(
                QMouseEvent.Type.MouseButtonPress,
                QPointF(50.0, 10.0),
                QPointF(50.0, 10.0),
                Qt.MouseButton.RightButton,
                Qt.MouseButton.RightButton,
                Qt.KeyboardModifier.NoModifier,
            )
            self.window.header_widget.mousePressEvent(header_evt)
            mock_ctx.assert_called_once()
            mock_tray.assert_not_called()

    def test_context_menu_has_all_expected_actions(self):
        ctx_menu = self.window._context_menu
        self.assertIsNotNone(ctx_menu)
        top_action_texts = [a.text() for a in ctx_menu.actions() if a.text()]

        # Verify core top-level actions
        self.assertTrue(any("识别与翻译" in t for t in top_action_texts), "Missing capture toggle")
        self.assertTrue(any("电影追剧" in t for t in top_action_texts), "Missing movie Qwen3 action")
        self.assertTrue(any("演讲对话" in t for t in top_action_texts), "Missing speech Qwen3 action")
        self.assertTrue(any("极速同传" in t for t in top_action_texts), "Missing stream Nemotron action")
        self.assertTrue(any("双行滚动字幕" in t for t in top_action_texts), "Missing rolling two lines direct toggle")
        self.assertTrue(any("双语同显模式" in t for t in top_action_texts), "Missing bilingual direct toggle")
        self.assertTrue(any("音频源" in t for t in top_action_texts), "Missing audio source menu")
        self.assertTrue(any("源语言" in t for t in top_action_texts), "Missing source lang menu")
        self.assertTrue(any("目标语言" in t for t in top_action_texts), "Missing target lang menu")
        self.assertTrue(any("字幕外观设置" in t for t in top_action_texts), "Missing appearance menu")
        self.assertTrue(any("隐藏字幕" in t for t in top_action_texts), "Missing hide caption")
        self.assertTrue(any("桌面挂件" in t for t in top_action_texts), "Missing pet toggle")
        self.assertTrue(any("清空当前字幕" in t for t in top_action_texts), "Missing clear caption")
        self.assertTrue(any("设置模型路径" in t for t in top_action_texts), "Missing model path")
        self.assertTrue(any("查看对话历史记录" in t for t in top_action_texts), "Missing peek history")
        self.assertTrue(any("退出程序" in t for t in top_action_texts), "Missing exit app")

        # Verify Qwen3 & NV engine choices exist across direct actions and advanced cadence submenu
        qwen_ms = [a.data().get("ms") for a in self.window._qwen_actions]
        self.assertIn(1000, qwen_ms)
        self.assertIn(750, qwen_ms)
        self.assertIn(500, qwen_ms)
        self.assertIn(250, qwen_ms)

        nv_ms = [a.data().get("ms") for a in self.window._nv_actions]
        self.assertIn(80, nv_ms)
        self.assertIn(160, nv_ms)
        self.assertIn(320, nv_ms)
        self.assertIn(560, nv_ms)
        self.assertIn(1120, nv_ms)

        # Verify appearance sub-menu items (only visual geometry, font, color; no punct or nested bilingual remnants)
        style_act = next(a for a in ctx_menu.actions() if "字幕外观设置" in a.text())
        self.assertIsNotNone(style_act.menu())
        style_texts = [a.text() for a in style_act.menu().actions() if a.text()]
        self.assertTrue(any("只有字" in t for t in style_texts))
        self.assertTrue(any("铺满全屏宽度" in t for t in style_texts))
        self.assertTrue(any("恢复标准宽度" in t for t in style_texts))
        self.assertTrue(any("字体大小" in t for t in style_texts))
        self.assertTrue(any("文字颜色" in t for t in style_texts))
        self.assertFalse(any("标点" in t for t in style_texts), "Leftover punct chunking residue in style menu")
        self.assertFalse(any("双语" in t for t in style_texts), "Leftover nested bilingual residue in style menu")

    def test_tray_menu_has_minimal_actions_with_settings_entry(self):
        tray_menu = self.window._tray_menu
        self.assertIsNotNone(tray_menu)
        action_texts = [a.text() for a in tray_menu.actions() if a.text()]
        self.assertTrue(any("隐藏字幕" in t for t in action_texts))
        self.assertTrue(any("字幕功能设置" in t for t in action_texts))
        self.assertTrue(any("退出程序" in t for t in action_texts))

    def test_monotonic_prefix_freezing_in_window(self):
        self.window._block_by_punct = False

        # 1. Partial slice 1
        self.window.set_text("今天", is_final=False)
        self.assertEqual(self.window.line, "今天")

        # 2. Partial slice 2 confirms "今天" and extends to "今天天气"
        self.window.set_text("今天天气", is_final=False)
        self.assertEqual(self.window.stable_prefix, "今天")
        self.assertEqual(self.window.hypothesis_suffix, "天气")
        self.assertEqual(self.window.line, "今天天气")

        # 3. Final commit completes utterance and resets zones
        self.window.set_text("今天天气真好", is_final=True)
        self.assertEqual(self.window.stable_prefix, "")
        self.assertEqual(self.window.hypothesis_suffix, "")
        self.assertEqual(self.window.line, "今天天气真好")

    def test_punct_gate_recovers_fallback_for_new_sentence(self):
        self.window._block_by_punct = True
        self.window.line = "第一句已有内容"
        self.window._clause_fell_back = True

        # Second sentence arrives without terminal punctuation mark
        res = self.window.set_text("第二句刚开始说话", is_final=False)
        self.assertEqual(res, "block_wait")
        self.assertFalse(self.window._clause_fell_back, "_clause_fell_back must be reset for new sentence")
        self.assertEqual(self.window._clause_pending, "第二句刚开始说话")

        # After clause timeout, _flush_clause must render it cleanly
        self.window._flush_clause()
        self.assertEqual(self.window.line, "第二句刚开始说话")

    def test_history_drawer_paint_event_no_typeerror(self):
        from PyQt6.QtGui import QPaintEvent
        drawer = self.window.history_drawer
        drawer.add_entry("测试对白译文", "Test dialogue source")
        drawer.resize(500, 300)
        # Calling paintEvent directly should not throw TypeError
        paint_evt = QPaintEvent(drawer.rect())
        drawer.paintEvent(paint_evt)

    def test_prefix_freezer_transition_to_new_utterance_without_final(self):
        self.window._block_by_punct = False
        self.window.prefix_freezer.clear()

        # Utterance 1 streams and freezes
        self.window.set_text("Hello world this is sentence one", is_final=False)
        self.window.prefix_freezer.frozen_prefix = "Hello world this is sentence one"

        # Utterance 2 starts streaming with disjoint text
        res = self.window.set_text("Yes I agree with you", is_final=False)
        self.assertIn(res, ("char", "grow"))
        self.assertEqual(self.window.line, "Yes I agree with you")

    def test_caption_paint_and_text_perf_emission(self):
        import time
        from server import perf
        emitted = []
        with patch.object(perf, "emit", side_effect=lambda *args, **kwargs: emitted.append((args, kwargs))):
            t0 = time.time() - 0.05
            t1 = time.time() - 0.02
            self.window._handle_command("text", "测试上屏绘制与性能埋点", t0, t1, True)
            
            # Check caption.text
            text_events = [e for e in emitted if e[0] == ("caption", "text")]
            self.assertEqual(len(text_events), 1)
            self.assertIn("action", text_events[0][1])

            # Check caption.paint
            paint_events = [e for e in emitted if e[0] == ("caption", "paint")]
            self.assertEqual(len(paint_events), 1)
            paint_kw = paint_events[0][1]
            self.assertIn("sig", paint_kw)
            self.assertEqual(len(paint_kw["sig"]), 12)
            self.assertGreater(paint_kw["since_send"], 0)
            self.assertGreater(paint_kw["since_recv"], 0)

    def test_caption_dwell_emitted_on_sentence_swap_and_dismiss(self):
        import time
        from server import perf
        self.window._block_by_punct = True
        emitted = []
        with patch.object(perf, "emit", side_effect=lambda *args, **kwargs: emitted.append((args, kwargs))):
            # Sentence 1 arrives and draws
            self.window.set_text("第一句话已经上屏。", is_final=False)
            self.assertEqual(self.window.line, "第一句话已经上屏。")

            # Simulate pause then Sentence 2 arrives (swaps sentence 1)
            time.sleep(0.02)
            self.window.set_text("第二句话替换了第一句。", is_final=False)
            self.assertEqual(self.window.line, "第二句话替换了第一句。")

            # Check that caption.dwell was emitted for sentence 1
            dwell_events = [e for e in emitted if e[0] == ("caption", "dwell")]
            self.assertGreaterEqual(len(dwell_events), 1)
            first_dwell = dwell_events[0][1]
            self.assertGreater(first_dwell["gap"], 0)
            self.assertEqual(first_dwell["lost"], len("第一句话已经上屏。"))
            self.assertEqual(first_dwell["shown"], len("第二句话替换了第一句。"))

            # Test dismiss timeout records dwell for sentence 2
            emitted.clear()
            self.window._on_dismiss_timeout()
            dismiss_dwell = [e for e in emitted if e[0] == ("caption", "dwell")]
            self.assertEqual(len(dismiss_dwell), 1)
            self.assertEqual(dismiss_dwell[0][1]["why"], "dismiss")
            self.assertEqual(dismiss_dwell[0][1]["lost"], len("第二句话替换了第一句。"))
            self.assertEqual(dismiss_dwell[0][1]["shown"], 0)

    def test_module_entrypoint_exists(self):
        import server.qt_caption_window as qt_facade
        from server.ui.caption_window import main as ui_main
        # Module must export or be able to invoke main
        import inspect
        src = inspect.getsource(qt_facade)
        self.assertIn('if __name__ == "__main__":', src)
        self.assertIn('from server.ui.caption_window import main', src)

    def test_engine_switch_unified_block_by_punct(self):
        """Verify engine switch to Nemotron maintains unified block_by_punct=True."""
        with patch("server.asr.nemotron.check_nemotron_status", return_value=(True, "就绪")):
            self.window.menu_manager.apply_engine_cadence("nemotron", 160)
            self.assertTrue(self.window._block_by_punct)
            self.window.menu_manager.apply_engine_cadence("qwen3", 1000)
            self.assertTrue(self.window._block_by_punct)

    def test_begin_segment_safeguards_dismiss_timer(self):
        """begin_segment must not indefinitely strand captions if new segment has no text."""
        self.window.line = "现有字幕正在显示"
        self.window.dismiss_ms = 5000
        self.window._start_dismiss_timer()
        self.assertTrue(self.window._dismiss_timer.isActive())

        # New audio onset arrives
        self.window.begin_segment()
        # Main dismiss timer is cancelled, but safety timer MUST be armed so subtitle dismisses if no words follow!
        self.assertFalse(self.window._dismiss_timer.isActive())
        self.assertTrue(self.window._segment_safety_timer.isActive())

    def test_dismiss_timeout_full_cleanup(self):
        """_on_dismiss_timeout must completely clean up all buffers and pending states."""
        self.window.line = "将要消散的字幕"
        self.window.display_buffer.push_new_sentence("将要消散的字幕")
        self.window.rolling_buffer.commit("将要消散的字幕")
        self.window._clause_pending = "残留片段"
        self.window.source_line = "source"

        self.window._on_dismiss_timeout()

        self.assertEqual(self.window.line, "")
        self.assertEqual(self.window.source_line, "")
        self.assertEqual(self.window._clause_pending, "")
        self.assertEqual(self.window.display_buffer.line1, "")
        self.assertEqual(self.window.display_buffer.line2, "")
        self.assertEqual(self.window.rolling_buffer.anchor_line, "")
        self.assertEqual(self.window.rolling_buffer.active_line, "")

    def test_clean_labels_no_verbose_brackets_or_hardcoded_languages(self):
        """Verify rolling two lines and bilingual actions have clean labels without tack-on brackets."""
        rolling_act = getattr(self.window, "_rolling_two_lines_act", None)
        bilingual_act = getattr(self.window, "_bilingual_act", None)
        self.assertIsNotNone(rolling_act)
        self.assertIsNotNone(bilingual_act)

        # No (Google同款) or (中英对照) or trailing explanatory brackets
        self.assertEqual(rolling_act.text().strip(), "📜 双行滚动字幕")
        self.assertEqual(bilingual_act.text().strip(), "🔤 双语同显模式")
        self.assertNotIn("中英对照", bilingual_act.text())
        self.assertNotIn("Google", rolling_act.text())

    def test_bilingual_and_rolling_two_lines_mutual_exclusion(self):
        """Toggling bilingual mode must disable rolling two lines and vice-versa."""
        menu_builder = self.window.menu_manager

        # 1. Turn on rolling two lines
        self.window.set_rolling_two_lines(True)
        self.assertTrue(self.window.rolling_two_lines)

        # 2. Turn on bilingual mode -> rolling two lines MUST be forced False
        menu_builder._toggle_bilingual(True)
        self.assertEqual(self.window.bilingual_mode, "bilingual")
        self.assertFalse(self.window.rolling_two_lines)
        self.assertFalse(self.window._rolling_two_lines_act.isChecked())

        # 3. Turning rolling two lines back on -> bilingual mode MUST be switched to trans_only
        self.window.set_rolling_two_lines(True)
        self.assertTrue(self.window.rolling_two_lines)
        self.assertEqual(self.window.bilingual_mode, "trans_only")
        self.assertFalse(self.window._bilingual_act.isChecked())


if __name__ == "__main__":
    unittest.main()


