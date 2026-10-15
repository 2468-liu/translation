import os
import sys
import unittest
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication

from desktop_app import DesktopApp, _kill_port, _pids_on_port


class DesktopBackendSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication(sys.argv)

    @patch("sys.platform", "win32")
    @patch("desktop_app._pids_on_port")
    @patch("desktop_app.subprocess.run")
    def test_kill_port_kills_listening_pids(self, mock_run, mock_pids):
        mock_pids.return_value = [1234, 5678]
        pids = _kill_port(8099)
        self.assertEqual(pids, [1234, 5678])
        self.assertEqual(mock_run.call_count, 2)
        mock_run.assert_any_call(
            ["taskkill.exe", "/PID", "1234", "/T", "/F"],
            capture_output=True,
            creationflags=mock_run.call_args[1].get("creationflags"),
            check=False,
        )
        mock_run.assert_any_call(
            ["taskkill.exe", "/PID", "5678", "/T", "/F"],
            capture_output=True,
            creationflags=mock_run.call_args[1].get("creationflags"),
            check=False,
        )

    @patch("sys.platform", "win32")
    @patch("desktop_app.QLocalServer")
    @patch("desktop_app.QtCaptionWindow")
    @patch("desktop_app.Session")
    @patch("desktop_app.ProcessLoopbackManager")
    @patch("desktop_app._ensure_llama_server")
    @patch("desktop_app._ensure_qwen_server")
    @patch("desktop_app.build_asr")
    @patch("desktop_app._kill_port")
    def test_switch_to_nemotron_seamlessly_docks_on_unified_pipeline(
        self, mock_kill_port, mock_build_asr, mock_qwen_srv, mock_llama_srv, mock_loopback, mock_session, mock_win, mock_local_srv
    ):
        with patch("desktop_app.config.load_desktop_config", return_value={"asr_backend": "qwen3"}):
            app = DesktopApp(app=self.qapp)
            app.qwen_proc = MagicMock()
            app._current_asr_backend = "qwen3"

            mock_nemotron_asr = MagicMock()
            mock_build_asr.return_value = mock_nemotron_asr

            app._switch_asr_backend("nemotron", cadence=240)

            # 1. Must kill Qwen server to free port 8099 and reclaim GPU VRAM
            mock_kill_port.assert_called_with(8099)
            self.assertIsNone(app.qwen_proc)

            # 2. Must mount Nemotron into session via switch_asr on the SAME pipeline
            app.session.switch_asr.assert_called_with(mock_nemotron_asr, 240)
            self.assertEqual(app._current_asr_backend, "nemotron")

            # 3. Must update cadence to 240ms and enforce block mode
            app.window.set_cadence_ui.assert_called_with(240)
            app.window.set_asr_backend_ui.assert_called_with("nemotron")
            app.window.set_block_by_punct.assert_called_with(True)

    @patch("sys.platform", "win32")
    @patch("desktop_app.QLocalServer")
    @patch("desktop_app.QtCaptionWindow")
    @patch("desktop_app.Session")
    @patch("desktop_app.ProcessLoopbackManager")
    @patch("desktop_app._ensure_llama_server")
    @patch("desktop_app._ensure_qwen_server")
    @patch("desktop_app.build_asr")
    @patch("desktop_app._kill_port")
    @patch("desktop_app._is_llama_ready")
    def test_switch_to_qwen3_kills_stale_port_and_launches_server(
        self, mock_is_ready, mock_kill_port, mock_build_asr, mock_qwen_srv, mock_llama_srv, mock_loopback, mock_session, mock_win, mock_local_srv
    ):
        with (
            patch("desktop_app.config.load_desktop_config", return_value={"asr_backend": "nemotron"}),
            patch("server.asr.nemotron._resolve_nemotron_model_path"),
            patch("server.asr.nemotron._resolve_nemo_speech_dll"),
        ):
            app = DesktopApp(app=self.qapp)
            app._current_asr_backend = "nemotron"
            old_nemo_asr = MagicMock()
            app.session.asr = old_nemo_asr

            mock_is_ready.side_effect = [False, True]  # first False (stale/not ready), then True (ready after launch)
            mock_new_qproc = MagicMock()
            mock_qwen_srv.return_value = mock_new_qproc
            mock_qwen_asr = MagicMock()
            mock_build_asr.return_value = mock_qwen_asr

            app._switch_asr_backend("qwen3")

            # 1. Must close old ASR
            old_nemo_asr.close.assert_called_once()

            # 2. Must kill stale port 8099
            mock_kill_port.assert_called_with(8099)

            # 3. Must ensure qwen server launched and docked
            mock_qwen_srv.assert_called_once()
            self.assertEqual(app.qwen_proc, mock_new_qproc)
            self.assertEqual(app.session.asr, mock_qwen_asr)
            self.assertEqual(app._current_asr_backend, "qwen3")

            # 4. Must update cadence to 1000ms
            app.window.set_cadence_ui.assert_called_with(1000)
            app.window.set_asr_backend_ui("qwen3")

    @patch("sys.platform", "linux")
    @patch("desktop_app.QLocalServer")
    @patch("desktop_app.QtCaptionWindow")
    @patch("desktop_app.Session")
    @patch("desktop_app.ProcessLoopbackManager")
    @patch("desktop_app._ensure_llama_server")
    @patch("desktop_app._ensure_qwen_server")
    @patch("desktop_app.build_asr")
    def test_switch_to_nemotron_on_linux_is_blocked_safely(
        self, mock_build_asr, mock_qwen_srv, mock_llama_srv, mock_loopback, mock_session, mock_win, mock_local_srv
    ):
        with patch("desktop_app.config.load_desktop_config", return_value={"asr_backend": "qwen3"}):
            app = DesktopApp(app=self.qapp)
            app._current_asr_backend = "qwen3"

            app._switch_asr_backend("nemotron")

            # Must remain on qwen3
            self.assertEqual(app._current_asr_backend, "qwen3")
            app.window.set_asr_backend_ui.assert_called_with("qwen3")

    @patch("sys.platform", "linux")
    @patch("desktop_app.QLocalServer")
    @patch("desktop_app.QtCaptionWindow")
    @patch("desktop_app.Session")
    @patch("desktop_app.ProcessLoopbackManager")
    @patch("desktop_app._ensure_llama_server")
    @patch("desktop_app._ensure_qwen_server")
    @patch("desktop_app.build_asr")
    def test_startup_with_nemotron_on_linux_auto_recovers_to_qwen3(
        self, mock_build_asr, mock_qwen_srv, mock_llama_srv, mock_loopback, mock_session, mock_win, mock_local_srv
    ):
        with patch("desktop_app.config.load_desktop_config", return_value={"asr_backend": "nemotron"}):
            app = DesktopApp(app=self.qapp)

            # Must auto-recover to qwen3
            self.assertEqual(app._current_asr_backend, "qwen3")
            self.assertTrue(hasattr(app, "_startup_warning"))
            self.assertIn("Windows", app._startup_warning)
    @patch("desktop_app.QLocalServer")
    @patch("desktop_app.QtCaptionWindow")
    @patch("desktop_app.Session")
    @patch("desktop_app.ProcessLoopbackManager")
    @patch("desktop_app._ensure_llama_server")
    @patch("desktop_app._ensure_qwen_server")
    @patch("desktop_app.build_asr")
    def test_switch_to_nemotron_missing_user_declines_download_rolls_back(
        self, mock_build_asr, mock_qwen_srv, mock_llama_srv, mock_loopback, mock_session, mock_win, mock_local_srv
    ):
        with (
            patch("desktop_app.config.load_desktop_config", return_value={"asr_backend": "qwen3"}),
            patch("server.asr.nemotron.check_nemotron_status", return_value=(False, "缺少模型权重")),
            patch.dict(os.environ, {"TRANSLATION_FORCE_INTERACTIVE": "1"}, clear=False),
            patch("PyQt6.QtWidgets.QMessageBox.question") as mock_question,
        ):
            from PyQt6.QtWidgets import QMessageBox
            mock_question.return_value = QMessageBox.StandardButton.No

            app = DesktopApp(app=self.qapp)
            app._current_asr_backend = "qwen3"

            app._switch_asr_backend("nemotron")

            mock_question.assert_called_once()
            self.assertEqual(app._current_asr_backend, "qwen3")
            app.window.set_asr_backend_ui.assert_called_with("qwen3")
            app.window.command_received.emit.assert_called_with(
                "toast", "已取消切换，保持使用 Qwen3-ASR 通用模型", mock_win.return_value.command_received.emit.call_args[0][2], mock_win.return_value.command_received.emit.call_args[0][3], False
            )

    @patch("desktop_app.QLocalServer")
    @patch("desktop_app.QtCaptionWindow")
    @patch("desktop_app.Session")
    @patch("desktop_app.ProcessLoopbackManager")
    @patch("desktop_app._ensure_llama_server")
    @patch("desktop_app._ensure_qwen_server")
    @patch("desktop_app.build_asr")
    @patch("subprocess.Popen")
    def test_switch_to_nemotron_missing_user_accepts_download_launches_script(
        self, mock_popen, mock_build_asr, mock_qwen_srv, mock_llama_srv, mock_loopback, mock_session, mock_win, mock_local_srv
    ):
        with (
            patch("desktop_app.config.load_desktop_config", return_value={"asr_backend": "qwen3"}),
            patch("server.asr.nemotron.check_nemotron_status", return_value=(False, "缺少模型权重")),
            patch.dict(os.environ, {"TRANSLATION_FORCE_INTERACTIVE": "1"}, clear=False),
            patch("PyQt6.QtWidgets.QMessageBox.question") as mock_question,
        ):
            from PyQt6.QtWidgets import QMessageBox
            mock_question.return_value = QMessageBox.StandardButton.Yes

            app = DesktopApp(app=self.qapp)
            app._current_asr_backend = "qwen3"

            app._switch_asr_backend("nemotron")

            mock_question.assert_called_once()
            mock_popen.assert_called_once()
            cmd_args = mock_popen.call_args[0][0]
            self.assertIn("setup_nemotron.py", cmd_args[1])
            self.assertIn("--auto", cmd_args)
            self.assertEqual(app._current_asr_backend, "qwen3")
            app.window.set_asr_backend_ui.assert_called_with("qwen3")


if __name__ == "__main__":
    unittest.main()

