"""Unit tests for UI language selection, synchronization, and persistence."""

import json
import sys
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

from server.ui.caption_window import QtCaptionWindow
from server.ui.caption_menu import CaptionMenuBuilder


class UiLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.win = QtCaptionWindow()

    def tearDown(self):
        self.win.close()

    def test_apply_language_commands(self):
        emitted_configs = []
        self.win.config_changed.connect(lambda d: emitted_configs.append(d))

        # Switch source language to English
        self.win._handle_command("language", "en", 0.0, 0.0, False)
        self.assertEqual(self.win.source_lang, "en")
        self.assertTrue(any(c.get("language") == "en" for c in emitted_configs))

        # Switch target language to Japanese
        self.win._handle_command("target_language", "ja", 0.0, 0.0, False)
        self.assertEqual(self.win.target_lang, "ja")
        self.assertTrue(any(c.get("target_language") == "ja" for c in emitted_configs))

    def test_set_status_does_not_corrupt_source_lang(self):
        self.win.source_lang = "en"
        # Warning/error messages should not overwrite source_lang
        self.win.set_status("[GPU] VRAM high warning")
        self.assertEqual(self.win.source_lang, "en")
        self.win.set_status("正在连接服务...")
        self.assertEqual(self.win.source_lang, "en")

    def test_menu_builder_sync_lang_actions(self):
        builder = CaptionMenuBuilder(self.win)
        builder.build_context_menu()
        builder.build_tray_menu()

        self.win._handle_command("language", "ja", 0.0, 0.0, False)
        builder._sync_lang_actions()

        # Check context menu action for source lang
        ja_src = [a for a in getattr(self.win, "src_group").actions() if a.data() == "ja"]
        self.assertTrue(len(ja_src) > 0)
        self.assertTrue(ja_src[0].isChecked())

        # Check target language
        self.win._handle_command("target_language", "en", 0.0, 0.0, False)
        builder._sync_lang_actions()
        en_tgt = [a for a in getattr(self.win, "tgt_group").actions() if a.data() == "en"]
        self.assertTrue(len(en_tgt) > 0)
        self.assertTrue(en_tgt[0].isChecked())

    def test_is_nemo_supported_whitelist(self):
        from server.asr.text_filters import is_nemo_supported
        # Supported by Nemotron
        self.assertTrue(is_nemo_supported("auto"))
        self.assertTrue(is_nemo_supported("en"))
        self.assertTrue(is_nemo_supported("zh"))
        self.assertTrue(is_nemo_supported("zh-CN"))
        self.assertTrue(is_nemo_supported("ja"))
        self.assertTrue(is_nemo_supported("ko"))
        self.assertTrue(is_nemo_supported("es"))
        self.assertTrue(is_nemo_supported("fr"))
        self.assertTrue(is_nemo_supported("de"))
        self.assertTrue(is_nemo_supported("ru"))
        self.assertTrue(is_nemo_supported("ar"))

        # Outside Nemotron whitelist (dialects & regionals supported by Qwen3)
        self.assertFalse(is_nemo_supported("yue"))  # Cantonese
        self.assertFalse(is_nemo_supported("zh-tw"))  # Traditional Chinese
        self.assertFalse(is_nemo_supported("th"))  # Thai
        self.assertFalse(is_nemo_supported("id"))  # Indonesian
        self.assertFalse(is_nemo_supported("ms"))  # Malay
        self.assertFalse(is_nemo_supported("bo"))  # Tibetan
        self.assertFalse(is_nemo_supported("ug"))  # Uyghur

    def test_unsupported_language_auto_switches_from_nemotron_to_qwen(self):
        builder = CaptionMenuBuilder(self.win)
        builder.build_context_menu()
        self.win.set_asr_backend_ui("nemotron")
        self.assertEqual(self.win.asr_backend, "nemotron")

        # Mock action for Cantonese ("yue")
        received_commands = []
        self.win.command_received.connect(lambda *args: received_commands.append(args))

        mock_act = MagicMock()
        mock_act.data.return_value = "yue"
        mock_act.text.return_value = "粤语 (Cantonese)"

        builder._on_source_lang_changed(mock_act)

        # Engine should automatically switch to qwen3 with 1000ms cadence
        self.assertEqual(self.win.asr_backend, "qwen3")
        self.assertEqual(self.win.partial_interval_ms, 1000)
        self.assertEqual(self.win.source_lang, "yue")

        # Toast notification should be emitted
        toasts = [c for c in received_commands if c[0] == "toast"]
        self.assertTrue(len(toasts) > 0)
        self.assertIn("粤语", toasts[0][1])
        self.assertIn("Qwen3", toasts[0][1])

    def test_supported_language_keeps_nemotron_engine(self):
        builder = CaptionMenuBuilder(self.win)
        builder.build_context_menu()
        self.win.set_asr_backend_ui("nemotron")
        self.assertEqual(self.win.asr_backend, "nemotron")

        mock_act = MagicMock()
        mock_act.data.return_value = "en"
        mock_act.text.return_value = "英语 (English)"

        builder._on_source_lang_changed(mock_act)

        # Engine should stay on nemotron
        self.assertEqual(self.win.asr_backend, "nemotron")
        self.assertEqual(self.win.source_lang, "en")


if __name__ == "__main__":
    unittest.main()
