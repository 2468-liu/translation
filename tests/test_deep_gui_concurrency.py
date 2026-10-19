"""Deep GUI and Concurrency Testing Suite (Criterion: Crash-Free & State Monotonicity).

Inspired by Telegram Desktop / OBS Studio UI test specifications:
1. Crash-Free Criterion: No unhandled exceptions in Qt slots during rapid user gestures.
2. State Monotonicity Criterion: Strict bidirectional synchronization between Parent Window and Tray without signal feedback recursion.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QApplication

from server.qt_caption_window import QtCaptionWindow


@pytest.fixture
def desktop_window(qtbot):
    """Fixture providing an initialized, monitored QtCaptionWindow."""
    window = QtCaptionWindow(scale=1.0)
    qtbot.addWidget(window)
    window.show()
    yield window
    window.close()


class TestGuiCrashFreeCriterion:
    """Crash-Free Criterion: Zero unhandled exceptions allowed in slots and event filters."""

    def test_tray_menu_actions_rapid_firing_no_exceptions(self, qtbot, desktop_window):
        """Rapidly trigger various tray menu actions without exceptions."""
        with qtbot.capture_exceptions() as exceptions:
            desktop_window._toggle_capture()
            desktop_window._toggle_visibility()
            desktop_window.apply_text_only(True, from_source="tray")
            desktop_window.apply_text_only(False, from_source="tray")
            desktop_window.apply_font(24)
            desktop_window.apply_color("#67e8f9")
            desktop_window.clear()
            qtbot.wait(100)

        assert len(exceptions) == 0, f"Tray menu trigger generated exceptions: {exceptions}"


class TestStateMonotonicityCriterion:
    """State Monotonicity Criterion: Parent Coordinator + Tray must synchronize strictly and avoid signal loops."""

    def test_capture_state_synchronization_from_all_sources(self, qtbot, desktop_window):
        """Verify capture state converges monotonically across Window and Tray."""
        tray_act = desktop_window._toggle_capture_act

        # 1. Trigger from internal toggle
        desktop_window.toggle_capture(from_source="internal")
        assert desktop_window._is_capturing is False
        assert "开启" in tray_act.text()

        # 2. Trigger from Tray
        desktop_window._toggle_capture()
        assert desktop_window._is_capturing is True
        assert "暂停" in tray_act.text()

        # 3. Direct programmatic call
        desktop_window.set_capturing(False, from_source="internal")
        assert desktop_window._is_capturing is False
        assert "开启" in tray_act.text()

    def test_text_only_mode_no_signal_recursion(self, qtbot, desktop_window):
        """Verify text-only mode changes do not cause recursive signal ping-pong storms."""
        cb = desktop_window.text_only_cb
        tray_act = desktop_window._text_only_act

        signal_emissions = []
        desktop_window.config_changed.connect(lambda cfg: signal_emissions.append(cfg))

        # Checkbox toggle (Child 1)
        cb.setChecked(True)
        qtbot.wait(50)

        assert desktop_window.text_only is True
        assert tray_act.isChecked() is True
        # Filter for text_only updates
        text_only_signals = [c for c in signal_emissions if "text_only" in c]
        # Must be exactly 1 emission (no circular echo loops)
        assert len(text_only_signals) == 1

        # Tray toggle (Child 2)
        tray_act.setChecked(False)
        qtbot.wait(50)

        assert desktop_window.text_only is False
        assert cb.isChecked() is False
        text_only_signals = [c for c in signal_emissions if "text_only" in c]
        assert len(text_only_signals) == 2

    def test_caption_visibility_alignment(self, qtbot, desktop_window):
        """Verify caption window visibility syncs with Tray."""
        vis_act = desktop_window._toggle_visibility_act

        # Initial: visible
        assert desktop_window.isVisible() is True
        assert "隐藏" in vis_act.text()

        # Toggle hide
        desktop_window._toggle_visibility()
        assert desktop_window.isVisible() is False
        assert "显示" in vis_act.text()

        # Tray action restores caption window
        desktop_window._toggle_visibility()
        assert desktop_window.isVisible() is True
        assert "隐藏" in vis_act.text()
