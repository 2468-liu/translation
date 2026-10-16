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


if __name__ == "__main__":
    unittest.main()
