import sys
import unittest
from PyQt6.QtWidgets import QApplication

from server.qt_caption_window import QtCaptionWindow
from server.qt_pet_widget import DesktopPetWidget


class DesktopPetSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.window = QtCaptionWindow(scale=1.0)
        self.pet = self.window.pet_widget

    def tearDown(self):
        if self.pet:
            self.pet.close()
        self.window.close()

    def test_pet_widget_instantiated_and_linked(self):
        self.assertIsNotNone(self.pet)
        self.assertIsInstance(self.pet, DesktopPetWidget)
        self.assertIs(self.pet.coordinator, self.window)

    def test_child_pet_click_syncs_to_parent_and_sibling_tray(self):
        # Initial state: capturing is True
        self.assertTrue(self.window._is_capturing)
        self.assertTrue(self.pet._is_capturing)
        self.assertIn("暂停", self.window._toggle_capture_act.text())

        # Child 2 (Pet) requests toggle capture
        received_configs = []
        self.window.config_changed.connect(lambda c: received_configs.append(c))

        self.window.toggle_capture(from_source="pet")

        # Parent state updated
        self.assertFalse(self.window._is_capturing)
        # Sibling Child 1 (Tray) updated
        self.assertIn("开启", self.window._toggle_capture_act.text())
        # Child 2 (Pet) updated
        self.assertFalse(self.pet._is_capturing)
        self.assertEqual(self.pet._bubble_text, "⏸ 已暂停")
        self.assertEqual(received_configs, [{"is_capturing": False}])

        # Child 2 (Pet) toggles back
        self.window.toggle_capture(from_source="pet")
        self.assertTrue(self.window._is_capturing)
        self.assertIn("暂停", self.window._toggle_capture_act.text())
        self.assertTrue(self.pet._is_capturing)
        self.assertEqual(self.pet._bubble_text, "▶ 正在听音")

    def test_child_tray_click_syncs_to_parent_and_sibling_pet(self):
        # Child 1 (Tray) triggered
        self.window._toggle_capture()

        # Both parent and sibling pet must reflect paused state
        self.assertFalse(self.window._is_capturing)
        self.assertFalse(self.pet._is_capturing)
        self.assertEqual(self.pet._bubble_text, "⏸ 已暂停")
        self.assertIn("开启", self.window._toggle_capture_act.text())

        # Sibling tray clicks again
        self.window._toggle_capture()
        self.assertTrue(self.window._is_capturing)
        self.assertTrue(self.pet._is_capturing)
        self.assertEqual(self.pet._bubble_text, "▶ 正在听音")

    def test_pet_double_click_syncs_caption_visibility_to_tray(self):
        self.window.show()
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.pet._caption_visible)
        self.assertEqual(self.window._toggle_visibility_act.text(), "隐藏字幕")

        # Pet double click: hide caption window
        self.window.toggle_caption_visibility(from_source="pet")
        self.assertFalse(self.window.isVisible())
        self.assertFalse(self.pet._caption_visible)
        self.assertEqual(self.window._toggle_visibility_act.text(), "显示字幕")
        self.assertEqual(self.pet._bubble_text, "🖥 字幕已隐藏")

        # Pet double click again: show caption window
        self.window.toggle_caption_visibility(from_source="pet")
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.pet._caption_visible)
        self.assertEqual(self.window._toggle_visibility_act.text(), "隐藏字幕")
        self.assertEqual(self.pet._bubble_text, "🖥 字幕已显示")

    def test_tray_menu_pet_toggle_controls_pet_visibility(self):
        self.window._show_pet_act.setChecked(True)
        self.assertTrue(self.pet.isVisible())

        # Uncheck in menu
        self.window._show_pet_act.setChecked(False)
        self.assertFalse(self.pet.isVisible())

        # Re-check in menu
        self.window._show_pet_act.setChecked(True)
        self.assertTrue(self.pet.isVisible())

    def test_text_only_tri_child_synchronization(self):
        # Initial: text_only is False
        self.assertFalse(self.window.text_only)
        self.assertFalse(self.window.text_only_cb.isChecked())
        self.assertFalse(self.window._text_only_act.isChecked())
        self.assertFalse(self.pet._text_only)

        # 1. Child 1 (Caption Frame Checkbox) toggles on
        self.window.text_only_cb.setChecked(True)

        # Parent state updated
        self.assertTrue(self.window.text_only)
        # Child 2 (Tray action) synchronized
        self.assertTrue(self.window._text_only_act.isChecked())
        # Child 3 (Pet widget) synchronized
        self.assertTrue(self.pet._text_only)
        self.assertEqual(self.pet._bubble_text, "🔤 模式：只有字")
        self.assertIn("只有字", self.pet.toolTip())

        # 2. Child 2 (Tray menu action) toggles off
        self.window._text_only_act.setChecked(False)

        # Parent state updated
        self.assertFalse(self.window.text_only)
        # Child 1 (Caption Frame Checkbox) synchronized
        self.assertFalse(self.window.text_only_cb.isChecked())
        # Child 3 (Pet widget) synchronized
        self.assertFalse(self.pet._text_only)
        self.assertEqual(self.pet._bubble_text, "⬛ 模式：黑框")
        self.assertIn("黑底框", self.pet.toolTip())

        # 3. Idempotent re-application must not trigger echo loops or duplicate bubbles
        self.window.apply_text_only(False, from_source="caption_box")
        self.assertFalse(self.window.text_only)

    def test_pet_single_click_timeout_toggles_capture(self):
        # Initial: is_capturing is True
        self.assertTrue(self.window._is_capturing)
        # Call timeout callback directly (as triggered by single click after 220ms)
        self.pet._on_single_click_timeout()
        self.assertFalse(self.window._is_capturing)
        self.assertFalse(self.pet._is_capturing)
        self.assertEqual(self.window.status_label.text(), "⏸ 已暂停")

        # Single click again resumes
        self.pet._on_single_click_timeout()
        self.assertTrue(self.window._is_capturing)
        self.assertTrue(self.pet._is_capturing)

    def test_session_has_reset_method_and_clears_state(self):
        from unittest.mock import MagicMock
        from server.pipeline import Session
        mock_asr = MagicMock()
        mock_asr.is_transcribing = False
        sess = Session(mock_asr)
        sess.last_text = "test speech"
        self.assertTrue(hasattr(sess, "reset"))
        # Calling reset() must succeed without AttributeError
        sess.reset()
        self.assertEqual(sess.last_text, "")

    def test_pet_idle_sleep_demotes_z_order_and_fades_opacity(self):
        self.pet.show()
        self.assertFalse(self.pet._is_sleeping)
        self.pet._enter_idle_sleep()
        self.assertTrue(self.pet._is_sleeping)
        self.assertIn("休眠避让中", self.pet.toolTip())

    def test_pet_wake_from_idle_sleep_restores_state(self):
        self.pet.show()
        self.pet._enter_idle_sleep()
        self.assertTrue(self.pet._is_sleeping)
        self.pet._wake_from_idle_sleep()
        self.assertFalse(self.pet._is_sleeping)
        self.assertNotIn("休眠避让中", self.pet.toolTip())

    def test_pet_set_idle_sleep_disabled_and_enabled(self):
        self.pet.show()
        self.pet.set_idle_sleep_enabled(False)
        self.assertFalse(self.pet._idle_sleep_enabled)
        self.assertFalse(self.pet._idle_timer.isActive())
        self.pet.set_idle_sleep_enabled(True)
        self.assertTrue(self.pet._idle_sleep_enabled)
        self.assertTrue(self.pet._idle_timer.isActive())


if __name__ == "__main__":
    unittest.main()
