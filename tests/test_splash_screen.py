import sys
import unittest
from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import Qt

from server.ui.splash_screen import SplashScreen


class SplashScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.splash = SplashScreen()

    def tearDown(self):
        if self.splash:
            self.splash.close()

    def test_splash_screen_instantiation_and_flags(self):
        self.assertIsNotNone(self.splash)
        self.assertEqual(self.splash.width(), 460)
        self.assertEqual(self.splash.height(), 226)

        flags = self.splash.windowFlags()
        self.assertTrue(flags & Qt.WindowType.FramelessWindowHint)
        self.assertTrue(flags & Qt.WindowType.WindowStaysOnTopHint)
        self.assertTrue(flags & Qt.WindowType.SplashScreen)
        self.assertTrue(flags & Qt.WindowType.Tool)

        self.assertTrue(self.splash.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        self.assertGreaterEqual(self.splash._progress, 0.02)
        self.assertTrue(len(self.splash._status_text) > 0)
        self.assertTrue(self.splash._anim_timer.isActive())

    def test_set_progress_and_clamping(self):
        self.splash.set_progress(0.42, "正在装载模型...")
        self.assertAlmostEqual(self.splash._progress, 0.42, places=2)
        self.assertEqual(self.splash._status_text, "正在装载模型...")

        # Clamping upper bound
        self.splash.set_progress(1.85, "已完成")
        self.assertEqual(self.splash._progress, 1.0)
        self.assertEqual(self.splash._status_text, "已完成")

        # Clamping lower bound
        self.splash.set_progress(-0.5, "重置")
        self.assertEqual(self.splash._progress, 0.02)
        self.assertEqual(self.splash._status_text, "重置")

    def test_set_status_only(self):
        curr_progress = self.splash._progress
        self.splash.set_status("单独修改文本状态")
        self.assertEqual(self.splash._progress, curr_progress)
        self.assertEqual(self.splash._status_text, "单独修改文本状态")

    def test_anim_shimmer_tick(self):
        initial_phase = self.splash._shimmer_phase
        self.splash._on_anim_tick()
        self.assertNotEqual(self.splash._shimmer_phase, initial_phase)

    def test_paint_event_executes_safely(self):
        # Trigger paint event explicitly without raising
        self.splash.repaint()

    def test_finish_and_close_with_target_window(self):
        target = QWidget()
        self.assertFalse(target.isVisible())

        self.splash.finish_and_close(target_window=target, duration_ms=50)

        # Target window should be shown
        self.assertTrue(target.isVisible())
        self.assertFalse(self.splash._anim_timer.isActive())
        self.assertIsNotNone(self.splash._fade_anim)
        self.assertTrue(self.splash._fade_anim.state() != 0)  # QAbstractAnimation.State.Running

        target.close()


if __name__ == "__main__":
    unittest.main()
