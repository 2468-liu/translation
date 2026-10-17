"""Tests for Nemotron dynamic menu, interactive provisioning dialog, and rollback."""

import os
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

from server.ui.caption_menu import CaptionMenuBuilder, NV_CADENCES
from server.asr.nemotron import check_nemotron_status


class DummyCaptionWindow(QWidget):
    command_received = pyqtSignal(str, object, float, float, bool)
    config_changed = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.source_lang = "auto"
        self.target_lang = "zh"
        self.asr_backend = "qwen3"
        self.partial_interval_ms = 1000
        self.rolling_two_lines = True
        self.bilingual_mode = "trans_only"
        self.current_audio_pid = 0
        self.text_only = False
        self.font_px = 20
        self.font_color = "#ffffff"
        self.dismiss_ms = 3000
        self.pet_widget = None

        self._on_toggle_capture_triggered = MagicMock()
        self._on_rolling_two_lines_toggled = MagicMock()
        self._on_quit_app_triggered = MagicMock()
        self._on_text_only_toggled = MagicMock()
        self._on_maximize_width_triggered = MagicMock()
        self._on_reset_width_triggered = MagicMock()
        self._on_toggle_visibility_triggered = MagicMock()
        self._on_toggle_pet_triggered = MagicMock()
        self._on_show_pet_toggled = MagicMock()
        self._on_clear_triggered = MagicMock()
        self.show_history_drawer = MagicMock()

        self.received_commands = []
        self.command_received.connect(lambda *args: self.received_commands.append(args))

    def set_asr_backend_ui(self, backend: str):
        self.asr_backend = backend

    def set_cadence_ui(self, ms: int):
        self.partial_interval_ms = ms

    def set_block_by_punct(self, punct: bool):
        pass

    def _paint(self):
        pass


class NemotronMenuProvisioningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.mock_win = DummyCaptionWindow()

    def test_nemotron_menu_is_enabled_across_all_platforms(self):
        builder = CaptionMenuBuilder(self.mock_win)
        menu = builder.build_context_menu()
        self.assertIsNotNone(menu)
        self.assertEqual(len(self.mock_win._nv_actions), 5)
        for act in self.mock_win._nv_actions:
            self.assertTrue(act.isEnabled())
            self.assertTrue(act.isCheckable())
            data = act.data()
            self.assertEqual(data.get("backend"), "nemotron")
            self.assertIn(data.get("ms"), [80, 160, 320, 560, 1120])

    def test_nemotron_tray_menu_is_enabled_across_all_platforms(self):
        builder = CaptionMenuBuilder(self.mock_win)
        tray_menu = builder.build_tray_menu()
        self.assertIsNotNone(tray_menu)
        self.assertEqual(len(self.mock_win._tray_nv_actions), 5)
        for act in self.mock_win._tray_nv_actions:
            self.assertTrue(act.isEnabled())
            self.assertTrue(act.isCheckable())
            data = act.data()
            self.assertEqual(data.get("backend"), "nemotron")
            self.assertIn(data.get("ms"), [80, 160, 320, 560, 1120])

    def test_nemotron_missing_user_declines_download_rolls_back(self):
        builder = CaptionMenuBuilder(self.mock_win)
        builder.build_context_menu()

        with (
            patch("server.asr.nemotron.check_nemotron_status", return_value=(False, "缺少模型权重")),
            patch.dict(os.environ, {"TRANSLATION_FORCE_INTERACTIVE": "1"}, clear=False),
            patch("PyQt6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.No) as mock_q,
        ):
            builder.apply_engine_cadence("nemotron", 160)

            mock_q.assert_called_once()
            # Must emit rollback toast
            self.assertTrue(len(self.mock_win.received_commands) > 0)
            toast_cmds = [c for c in self.mock_win.received_commands if c[0] == "toast"]
            self.assertTrue(len(toast_cmds) > 0)
            self.assertIn("已取消切换", toast_cmds[0][1])

    def test_nemotron_missing_user_accepts_download_launches_script(self):
        builder = CaptionMenuBuilder(self.mock_win)
        builder.build_context_menu()

        with (
            patch("server.asr.nemotron.check_nemotron_status", return_value=(False, "缺少模型权重")),
            patch.dict(os.environ, {"TRANSLATION_FORCE_INTERACTIVE": "1"}, clear=False),
            patch("PyQt6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes) as mock_q,
            patch("server.ui.download_dialog.ModelDownloadDialog") as mock_dialog_cls,
        ):
            mock_dlg = MagicMock()
            mock_dialog_cls.return_value = mock_dlg

            builder.apply_engine_cadence("nemotron", 160)

            mock_q.assert_called_once()
            mock_dialog_cls.assert_called_once_with(parent=self.mock_win, target_model="nemotron")
            mock_dlg.start_download.assert_called_once()

    def test_check_nemotron_status_returns_tuple(self):
        ready, msg = check_nemotron_status()
        self.assertIsInstance(ready, bool)
        self.assertIsInstance(msg, str)


if __name__ == "__main__":
    unittest.main()
