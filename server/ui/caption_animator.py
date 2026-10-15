"""Silence timeout smooth fade-out and breath-like dismissal controller."""

from __future__ import annotations

from typing import Callable, Optional
from PyQt6.QtCore import QEasingCurve, QObject, QPropertyAnimation, pyqtProperty
from PyQt6.QtWidgets import QWidget


class CaptionFadeAnimator(QObject):
    """Controls smooth breathing fade-in and silence-triggered fade-out transitions."""

    def __init__(self, target_widget: QWidget):
        super().__init__(target_widget)
        self.widget = target_widget
        self._text_opacity: float = 1.0
        self._fade_anim: Optional[QPropertyAnimation] = None

    @pyqtProperty(float)
    def text_opacity(self) -> float:
        return self._text_opacity

    @text_opacity.setter
    def text_opacity(self, value: float):
        self._text_opacity = max(0.0, min(1.0, float(value)))
        self.widget.update()

    def wake_immediate(self):
        """Immediately restores full opacity with 0 latency upon new speech arrival."""
        if self._fade_anim and self._fade_anim.state() != 0:
            self._fade_anim.stop()
        self._text_opacity = 1.0
        if self.widget.windowOpacity() < 0.99:
            self.widget.setWindowOpacity(1.0)
        self.widget.update()

    def fade_out_silence(
        self,
        duration_ms: int = 350,
        text_only: bool = False,
        on_finished: Optional[Callable[[], None]] = None,
    ):
        """Smoothly fades out during silence timeout."""
        if self._fade_anim is None:
            # For text_only, animate windowOpacity; for boxed, animate text_opacity
            prop = b"windowOpacity" if text_only else b"text_opacity"
            target_obj = self.widget if text_only else self
            self._fade_anim = QPropertyAnimation(target_obj, prop)
            self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Update target property if mode changed
        prop = b"windowOpacity" if text_only else b"text_opacity"
        target_obj = self.widget if text_only else self
        self._fade_anim.setTargetObject(target_obj)
        self._fade_anim.setPropertyName(prop)

        self._fade_anim.stop()
        self._fade_anim.setDuration(duration_ms)
        start_val = self.widget.windowOpacity() if text_only else self._text_opacity
        self._fade_anim.setStartValue(start_val)
        self._fade_anim.setEndValue(0.0)

        # Disconnect old callbacks
        try:
            self._fade_anim.finished.disconnect()
        except Exception:
            pass

        if on_finished:
            self._fade_anim.finished.connect(on_finished)

        self._fade_anim.start()
