"""Deep Integration Tests for Qwen3-ASR and 360-Ball Desktop Pet Interaction."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch
import pytest
from PyQt6.QtCore import QAbstractAnimation, QPoint, Qt
from PyQt6.QtWidgets import QApplication

from server import config
from server.qt_caption_window import QtCaptionWindow
from server.qt_pet_widget import DesktopPetWidget


@pytest.fixture
def pet_and_coordinator(qtbot):
    """Fixture providing an initialized coordinator window and desktop pet."""
    window = QtCaptionWindow(scale=1.0)
    qtbot.addWidget(window)
    assert window.pet_widget is not None
    qtbot.addWidget(window.pet_widget)
    window.pet_widget.show()
    window.show()
    yield window, window.pet_widget
    window.close()
    window.pet_widget.close()


class TestPet360BallInteraction:
    """Validate 360-ball magnetic snapping, auto-docking, and full hiding."""

    def test_snap_to_edge_initiates_smooth_animation(self, qtbot, pet_and_coordinator):
        window, pet = pet_and_coordinator
        screen = QApplication.primaryScreen()
        assert screen is not None
        geom = screen.availableGeometry()

        # Place pet near left edge
        pet.move(geom.left() + 20, geom.top() + 100)
        pet._snap_to_screen_edge()

        assert pet._pos_anim is not None
        assert pet._dock_side == "left"

        # Wait for the animation to actually finish, not for a wall-clock guess.
        #
        # This used to be `qtbot.wait(260)` against a 220ms animation -- a 40ms
        # margin, which is an assumption about how fast this machine happens to be,
        # not a property of the code. It held alone and failed in the full suite:
        #
        #     assert pet._pos_anim is not None     ok
        #     assert pet._dock_side == "left"      ok
        #     E   assert 5 == (0 + 4)              one pixel short of the dock point
        #
        # So the assertion was firing a frame early, and how early depended on what
        # else had run before it. Waiting on the animation's own state removes the
        # guess instead of widening the tolerance, which keeps the exact endpoint
        # check -- the contract is "docks to left edge + 4", not "gets near it".
        #
        # waitUntil rather than waitSignal because _snap_to_screen_edge starts the
        # animation synchronously, so the finished signal can be missed.
        qtbot.waitUntil(
            lambda: pet._pos_anim.state() == QAbstractAnimation.State.Stopped,
            timeout=2000,
        )
        assert pet.x() == geom.left() + 4

    def test_auto_dock_collapse_and_hover_expand(self, qtbot, pet_and_coordinator):
        window, pet = pet_and_coordinator
        screen = QApplication.primaryScreen()
        assert screen is not None
        geom = screen.availableGeometry()

        # Dock to right edge
        pet.move(geom.right() - pet.width() - 10, geom.top() + 100)
        pet._snap_to_screen_edge()
        qtbot.wait(260)
        assert pet._dock_side == "right"
        assert pet._is_docked is False

        # Trigger dock collapse
        pet._collapse_to_dock()
        qtbot.wait(300)
        assert pet._is_docked is True
        # Part of it should be off-screen
        assert pet.x() > geom.right() - pet.width()

        # Simulate mouse hover enter -> expand back
        class DummyEnterEvent:
            pass
        pet.enterEvent(DummyEnterEvent())
        qtbot.wait(220)
        assert pet._is_docked is False
        assert pet.x() == geom.right() - pet.width() - 4

    def test_hover_close_button_completely_hides_pet(self, qtbot, pet_and_coordinator):
        window, pet = pet_and_coordinator
        assert pet.isVisible() is True

        # Simulate clicking the hover close [x] at top-right
        pet._is_hovered = True
        pet._is_docked = False
        pet.hide_completely()

        assert pet.isVisible() is False
        cfg = config.load_desktop_config()
        assert cfg.get("show_pet") is False

        # Restore from tray
        window._toggle_pet_visibility(True)
        assert pet.isVisible() is True
        cfg = config.load_desktop_config()
        assert cfg.get("show_pet") is True


class TestAsrBackendTrayIntegration:
    """Validate ASR Backend and Cadence UI synchronization."""

    def test_asr_backend_switch_emits_config_change(self, qtbot, pet_and_coordinator):
        window, pet = pet_and_coordinator
        emitted_configs = []
        window.config_changed.connect(lambda cfg: emitted_configs.append(cfg))

        # Switch to nemotron
        window.set_asr_backend_ui("nemotron")
        window.asr_nemo_act.trigger()

        qtbot.waitUntil(lambda: any(cfg.get("asr_backend") == "nemotron" for cfg in emitted_configs))

        # Switch back to qwen3
        window.set_asr_backend_ui("qwen3")
        window.asr_qwen_act.trigger()
        qtbot.waitUntil(lambda: any(cfg.get("asr_backend") == "qwen3" for cfg in emitted_configs))

    def test_cadence_750ms_selectable_in_tray(self, qtbot, pet_and_coordinator):
        window, pet = pet_and_coordinator
        emitted_configs = []
        window.config_changed.connect(lambda cfg: emitted_configs.append(cfg))

        # Find 750ms action in cad_group
        act_750 = next((a for a in window.cad_group.actions() if a.data() == 750), None)
        assert act_750 is not None
        act_750.trigger()

        assert window.partial_interval_ms == 750
        qtbot.waitUntil(lambda: any(cfg.get("partial_interval_ms") == 750 for cfg in emitted_configs))
