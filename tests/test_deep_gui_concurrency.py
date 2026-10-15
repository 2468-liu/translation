"""Deep GUI and Concurrency Testing Suite (Criterion: Crash-Free & State Monotonicity).

Inspired by Telegram Desktop / OBS Studio UI test specifications:
1. Crash-Free Criterion: No unhandled exceptions in Qt slots during rapid user gestures.
2. State Monotonicity Criterion: Strict bidirectional synchronization between 1 Parent and 3 Children without signal feedback recursion.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QApplication

from server.qt_caption_window import QtCaptionWindow
from server.qt_pet_widget import DesktopPetWidget


@pytest.fixture
def desktop_window(qtbot):
    """Fixture providing an initialized, monitored QtCaptionWindow."""
    window = QtCaptionWindow(scale=1.0)
    qtbot.addWidget(window)
    if window.pet_widget:
        qtbot.addWidget(window.pet_widget)
        window.pet_widget.show()
    window.show()
    yield window
    window.close()
    if window.pet_widget:
        window.pet_widget.close()


class TestGuiCrashFreeCriterion:
    """Crash-Free Criterion: Zero unhandled exceptions allowed in slots and event filters."""

    def test_pet_rapid_gestures_no_unhandled_exceptions(self, qtbot, desktop_window, monkeypatch):
        """Simulate rapid, chaotic user gestures (clicks, double-clicks, drags, right-clicks)."""
        pet = desktop_window.pet_widget
        assert pet is not None

        from PyQt6.QtWidgets import QMenu
        monkeypatch.setattr(QMenu, "exec", lambda *args, **kwargs: None)

        with qtbot.capture_exceptions() as exceptions:
            # 1. Rapid left clicks & double clicks
            qtbot.mouseClick(pet, Qt.MouseButton.LeftButton)
            qtbot.mouseDClick(pet, Qt.MouseButton.LeftButton)
            qtbot.wait(50)

            # 2. Rapid context menu right clicks
            qtbot.mouseClick(pet, Qt.MouseButton.RightButton)
            qtbot.wait(50)

            # 3. Rapid drag gesture
            qtbot.mousePress(pet, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
            qtbot.mouseMove(pet, pos=QPoint(45, 45))
            qtbot.mouseRelease(pet, Qt.MouseButton.LeftButton, pos=QPoint(45, 45))

            # 4. Wait for debounce/single-click timers to fire
            qtbot.wait(300)

        assert len(exceptions) == 0, f"Unhandled exception detected during rapid gestures: {exceptions}"

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
    """State Monotonicity Criterion: 1 Parent Coordinator + 3 Children must synchronize strictly and avoid signal loops."""

    def test_capture_state_synchronization_from_all_sources(self, qtbot, desktop_window):
        """Verify capture state converges monotonically across Window, Tray, and Pet."""
        pet = desktop_window.pet_widget
        tray_act = desktop_window._toggle_capture_act

        # 1. Trigger from Pet
        desktop_window.toggle_capture(from_source="pet")
        assert desktop_window._is_capturing is False
        assert pet._is_capturing is False
        assert "开启" in tray_act.text()

        # 2. Trigger from Tray
        desktop_window._toggle_capture()
        assert desktop_window._is_capturing is True
        assert pet._is_capturing is True
        assert "暂停" in tray_act.text()

        # 3. Direct programmatic call
        desktop_window.set_capturing(False, from_source="internal")
        assert desktop_window._is_capturing is False
        assert pet._is_capturing is False
        assert "开启" in tray_act.text()

    def test_text_only_mode_no_signal_recursion(self, qtbot, desktop_window):
        """Verify text-only mode changes do not cause recursive signal ping-pong storms."""
        cb = desktop_window.text_only_cb
        tray_act = desktop_window._text_only_act
        pet = desktop_window.pet_widget

        signal_emissions = []
        desktop_window.config_changed.connect(lambda cfg: signal_emissions.append(cfg))

        # Checkbox toggle (Child 1)
        cb.setChecked(True)
        qtbot.wait(50)

        assert desktop_window.text_only is True
        assert tray_act.isChecked() is True
        assert pet._text_only is True
        # Filter for text_only updates
        text_only_signals = [c for c in signal_emissions if "text_only" in c]
        # Must be exactly 1 emission (no circular echo loops)
        assert len(text_only_signals) == 1

        # Tray toggle (Child 2)
        tray_act.setChecked(False)
        qtbot.wait(50)

        assert desktop_window.text_only is False
        assert cb.isChecked() is False
        assert pet._text_only is False
        text_only_signals = [c for c in signal_emissions if "text_only" in c]
        assert len(text_only_signals) == 2

    def test_caption_visibility_tri_child_alignment(self, qtbot, desktop_window):
        """Verify caption window visibility syncs with Pet and Tray."""
        pet = desktop_window.pet_widget
        vis_act = desktop_window._toggle_visibility_act

        # Initial: visible
        assert desktop_window.isVisible() is True
        assert pet._caption_visible is True
        assert "隐藏" in vis_act.text()

        # Double click from Pet hides caption window
        desktop_window.toggle_caption_visibility(from_source="pet")
        assert desktop_window.isVisible() is False
        assert pet._caption_visible is False
        assert "显示" in vis_act.text()

        # Tray action restores caption window
        desktop_window._toggle_visibility()
        assert desktop_window.isVisible() is True
        assert pet._caption_visible is True
        assert "隐藏" in vis_act.text()
