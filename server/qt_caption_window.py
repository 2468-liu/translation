"""PyQt6-based high-fidelity realtime subtitle overlay window.

Provides true alpha-channel translucent rendering, anti-aliased font rendering with
crisp drop shadow, smooth resizing, draggable positioning, and differentiated auto-dismiss.
"""

from __future__ import annotations

import json
import os
import queue
import socket
import sys
import threading
import time
from typing import Optional

from PyQt6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import perf
from .caption_window import (
    CAPTION_PORT,
    HOLD_ABS_MAX_MS,
    HOLD_ABS_MIN_MS,
    SETTLE_MS,
    STATE_PORT,
    classify_caption,
    clamped_font_px,
    flush_caption,
    hold_ms,
    introduces_boundary,
    pace_scale,
    step_caption,
    swap_deadline,
    wrap_all,
    wrap_caption,
)
from .config import BLOCK_OUTPUT, _env_int

try:
    import ctypes
except ImportError:
    ctypes = None

HWND_TOPMOST = -1
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040


class QtCaptionWindow(QWidget):
    """Modern frameless, translucent, topmost caption window."""

    command_received = pyqtSignal(str, object, float, float, bool)

    def __init__(self, scale: float = 1.0):
        super().__init__()
        self.scale = scale
        self.text_only = False
        self.visible = False
        self.closed = False
        self.line = ""
        self.status = ""
        self.font_px = 20
        self.font_color = "#ffffff"
        self.dismiss_ms = _env_int("TRANSLATION_CAPTION_DISMISS_MS", 5000)

        # State machine fields matching server.caption_window logic
        self._broadcast_last = ""
        self._growth_stopped_at = 0.0
        self._settle_until = 0.0
        self._pending = ""
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._flush_pending)

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self._on_dismiss_timeout)

        self._gaps: list[float] = []
        self._last_segment_at = 0.0
        self._next_utterance_at = 0.0
        self._trace_sent = 0.0
        self._trace_recv = 0.0
        self._trace_action = ""
        self._defer_t0 = 0.0
        self._defer_text = ""
        self._defer_chars = 0
        self._segment_seq = 0
        self._wait_segment = 0
        self._block_by_punct = bool(BLOCK_OUTPUT)
        self._clause_pending = ""
        self._clause_fell_back = False
        self._clause_t0 = 0.0
        self._clause_timer = QTimer(self)
        self._clause_timer.setSingleShot(True)
        self._clause_timer.timeout.connect(self._flush_clause)

        self._line_since = 0.0
        self._pending_since = 0.0
        self._drawn_text = ""
        self._cadence_applied = 0
        self._is_hovered = False

        # Drag and resize
        self._drag_pos: Optional[QPoint] = None
        self._resizing = False
        self._resize_start_x = 0
        self._resize_start_w = 0

        self._init_window()
        self._init_ui()
        self.command_received.connect(self._handle_command)

        self._pin_timer = QTimer(self)
        self._pin_timer.timeout.connect(self._pin)
        self._pin_timer.start(500)

    def _init_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setMouseTracking(True)

        # Geometry
        screen = QApplication.primaryScreen()
        screen_geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        self.screen_width = screen_geo.width()
        self.screen_height = screen_geo.height()

        self.win_width = max(round(760 * self.scale), 420)
        self.win_width = min(self.win_width, max(420, self.screen_width - 80))
        self.win_height = 80

        init_x = max(0, (self.screen_width - self.win_width) // 2)
        init_y = max(0, self.screen_height - self.win_height - 120)
        self.setGeometry(init_x, init_y, self.win_width, self.win_height)

    def _init_ui(self):
        self.header_widget = QWidget(self)
        self.header_widget.setFixedHeight(26)
        self.header_layout = QHBoxLayout(self.header_widget)
        self.header_layout.setContentsMargins(10, 2, 8, 0)
        self.header_layout.setSpacing(6)

        self.status_label = QLabel("", self.header_widget)
        self.status_label.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 11px;")

        self.text_only_cb = QCheckBox("只有字", self.header_widget)
        self.text_only_cb.setChecked(self.text_only)
        self.text_only_cb.setStyleSheet("""
            QCheckBox {
                color: rgba(255, 255, 255, 0.85);
                font-size: 11px;
                spacing: 4px;
            }
            QCheckBox::indicator {
                width: 12px;
                height: 12px;
                border-radius: 2px;
                border: 1px solid rgba(255, 255, 255, 0.4);
                background: rgba(0, 0, 0, 0.5);
            }
            QCheckBox::indicator:checked {
                background: #3b82f6;
                border-color: #60a5fa;
            }
        """)
        self.text_only_cb.toggled.connect(self.apply_text_only)

        self.close_btn = QPushButton("×", self.header_widget)
        self.close_btn.setFixedSize(18, 18)
        self.close_btn.setStyleSheet("""
            QPushButton {
                color: rgba(255, 255, 255, 0.8);
                font-size: 14px;
                font-weight: bold;
                border: none;
                border-radius: 9px;
                background: transparent;
                padding-bottom: 2px;
            }
            QPushButton:hover {
                color: #ffffff;
                background: rgba(239, 68, 68, 0.8);
            }
        """)
        self.close_btn.clicked.connect(self.hide)

        self.header_layout.addWidget(self.status_label)
        self.header_layout.addStretch()
        self.header_layout.addWidget(self.text_only_cb)
        self.header_layout.addWidget(self.close_btn)

        self.header_widget.hide()

    def hwnd(self) -> int:
        return int(self.winId())

    def _strip_frame(self) -> None:
        if not ctypes:
            return
        hwnd = self.hwnd()
        user32 = ctypes.windll.user32
        get_long = user32.GetWindowLongPtrW
        set_long = user32.SetWindowLongPtrW
        get_long.restype = ctypes.c_ssize_t
        get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
        set_long.restype = ctypes.c_ssize_t
        set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]

        exstyle = get_long(hwnd, GWL_EXSTYLE)
        extra = WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE
        set_long(hwnd, GWL_EXSTYLE, exstyle | extra)
        user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )

    def _pin(self) -> None:
        if ctypes and self.isVisible():
            ctypes.windll.user32.SetWindowPos(
                self.hwnd(),
                HWND_TOPMOST,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )

    def apply_text_only(self, enabled: bool) -> None:
        self.text_only = bool(enabled)
        if self.text_only_cb.isChecked() != self.text_only:
            self.text_only_cb.blockSignals(True)
            self.text_only_cb.setChecked(self.text_only)
            self.text_only_cb.blockSignals(False)
        self._strip_frame()
        self._broadcast_state()
        self.update()

    def apply_font(self, px: int) -> None:
        px = clamped_font_px(px, self.font_px)
        if px == self.font_px:
            return
        self.font_px = px
        self._broadcast_state()
        self._paint()

    def apply_color(self, color: str) -> None:
        if not color or color == self.font_color:
            return
        self.font_color = color
        self._broadcast_state()
        self.update()

    def apply_dismiss_timeout(self, ms: int) -> None:
        clamped = max(0, min(30000, int(ms)))
        if clamped == self.dismiss_ms:
            return
        self.dismiss_ms = clamped
        self._broadcast_state()

    def _on_dismiss_timeout(self) -> None:
        self.line = ""
        self._pending = ""
        self._clause_pending = ""
        self._drawn_text = ""
        if self.text_only:
            self.visible = False
            self.hide()
        else:
            self.update()
        self._broadcast_state()

    def _broadcast_state(self, force: bool = False) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            state = {
                "type": "state",
                "text_only": bool(self.text_only),
                "font_px": int(self.font_px),
                "font_color": str(self.font_color),
                "dismiss_timeout": int(self.dismiss_ms),
                "visible": bool(self.visible),
            }
            body = json.dumps(state)
            if not force and body == self._broadcast_last:
                return
            sock.sendto(body.encode("utf-8"), ("127.0.0.1", STATE_PORT))
            self._broadcast_last = body
        except OSError:
            pass
        finally:
            sock.close()

    def arm(self) -> None:
        self.closed = False
        if not self.visible:
            self.show()

    def show(self) -> None:
        super().show()
        self.visible = True
        self.closed = False
        self._strip_frame()
        self._broadcast_state()
        self._paint()

    def hide(self) -> None:
        self.visible = False
        self.closed = True
        super().hide()
        self._broadcast_state()

    def set_status(self, lang: str) -> None:
        self.status = lang or ""
        self.status_label.setText(self.status)
        self.update()

    def clear(self) -> None:
        self.line = ""
        self._pending = ""
        self._clause_pending = ""
        self._drawn_text = ""
        self.update()

    def begin_segment(self) -> None:
        now = time.monotonic()
        if self._last_segment_at > 0:
            gap = now - self._last_segment_at
            self._gaps.append(gap)
            if len(self._gaps) > 8:
                self._gaps.pop(0)
        self._last_segment_at = now
        self._segment_seq += 1

    def _pace(self) -> float:
        return pace_scale(self._gaps)

    def show_source(self, text: str) -> None:
        if not self.line and text:
            self.line = text
            self._paint()

    def _paint(self) -> None:
        body = self.line or ("实时字幕" if self.visible else "")
        if not body:
            self.update()
            return

        font = QFont("Microsoft YaHei", self.font_px)
        metrics = QFontMetrics(font)
        wrap_w = max(240, self.width() - 48)

        display_lines = wrap_caption(body, wrap_w, metrics.horizontalAdvance)
        line_h = metrics.lineSpacing()
        lines = len(display_lines)
        height = max(70, 30 + lines * line_h + 12)

        if height != self.height():
            cur_geo = self.geometry()
            delta = height - cur_geo.height()
            new_y = max(0, cur_geo.y() - delta)
            self.setGeometry(cur_geo.x(), new_y, cur_geo.width(), height)

        self._drawn_text = "\n".join(display_lines)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        rect = self.rect()

        # 1. Background
        if not self.text_only:
            painter.setPen(QPen(QColor(255, 255, 255, 28), 1.0))
            painter.setBrush(QColor(16, 16, 20, 225))
            painter.drawRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), 10.0, 10.0)

        # Header positioning
        self.header_widget.setGeometry(0, 0, rect.width(), 26)
        if not self.text_only and self._is_hovered:
            self.header_widget.show()
        else:
            self.header_widget.hide()

        # 2. Subtitle Text
        body = self.line or ("实时字幕" if self.visible else "")
        if not body:
            return

        font = QFont("Microsoft YaHei", self.font_px)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        wrap_w = max(240, rect.width() - 48)
        lines = wrap_caption(body, wrap_w, metrics.horizontalAdvance)

        line_h = metrics.lineSpacing()
        start_y = 28 if (not self.text_only and self._is_hovered) else 18

        for i, line_text in enumerate(lines):
            line_w = metrics.horizontalAdvance(line_text)
            x = (rect.width() - line_w) / 2.0
            y = start_y + i * line_h + metrics.ascent()

            # Text shadow / outline for readability
            path = QPainterPath()
            path.addText(x, y, font, line_text)

            # Draw outer glow / shadow
            painter.setPen(QPen(QColor(0, 0, 0, 200), 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

            # Draw crisp fill text
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self.font_color))
            painter.drawPath(path)

    def enterEvent(self, event):
        self._is_hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._is_hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self._resizing = True
            self._resize_start_x = event.globalPosition().toPoint().x()
            self._resize_start_w = self.width()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
        elif self._resizing and event.buttons() & Qt.MouseButton.RightButton:
            delta_x = event.globalPosition().toPoint().x() - self._resize_start_x
            new_w = max(420, min(self.screen_width - 80, self._resize_start_w + delta_x * 2))
            cur_geo = self.geometry()
            center_x = cur_geo.x() + cur_geo.width() // 2
            new_x = max(0, center_x - new_w // 2)
            self.setGeometry(new_x, cur_geo.y(), new_w, cur_geo.height())
            self._paint()
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = None
        self._resizing = False
        event.accept()

    # State Machine & Timing logic
    def set_text(self, text: str, is_final: bool = False) -> str:
        if not text:
            return ""
        if self.closed:
            self.closed = False
        if not self.visible:
            self.show()

        if self._block_by_punct:
            text = self._punct_gate(text, is_final)
            if not text:
                return ""

        if not self.line:
            self._apply_line(text, action="first")
            self._arm_hold()
            return "first"

        kind = classify_caption(self.line, text)
        if kind in ("same", "stale"):
            if is_final and self.dismiss_ms > 0:
                self._dismiss_timer.start(self.dismiss_ms)
            return kind

        if kind in ("grow", "replace"):
            self._apply_line(text, action=kind)
            self._arm_hold()
            return kind

        # kind == "swap"
        now = time.monotonic()
        deadline = self._swap_deadline()
        if now >= deadline:
            self._apply_line(text, action="swap")
            self._arm_hold()
            return "swap"

        self._pending = text
        self._arm_hold()
        return "deferred"

    def _punct_gate(self, cleaned: str, is_final: bool = False) -> str:
        if is_final:
            self._clause_timer.stop()
            self._clause_pending = ""
            self._clause_fell_back = False
            return cleaned
        if introduces_boundary(self._drawn_text, cleaned):
            self._clause_timer.stop()
            self._clause_pending = ""
            self._clause_fell_back = False
            return cleaned
        self._clause_pending = cleaned
        if not self._clause_timer.isActive():
            self._clause_t0 = time.monotonic()
            self._clause_timer.start(400)
        return ""

    def _flush_clause(self) -> None:
        pending = self._clause_pending
        self._clause_pending = ""
        self._clause_fell_back = True
        if pending:
            self.set_text(pending, is_final=False)

    def _swap_deadline(self) -> float:
        drawn_at = self._line_since if self._line_since > 0.0 else time.monotonic()
        return swap_deadline(
            text=self.line,
            previous_drawn="",
            pace=self._pace(),
            line_since=self._line_since,
            growth_stopped_at=self._growth_stopped_at,
            settle_ms=SETTLE_MS,
            drawn_at=drawn_at,
        )

    def _arm_hold(self) -> None:
        if self.dismiss_ms > 0:
            self._dismiss_timer.start(self.dismiss_ms)
        if not self._pending:
            self._hold_timer.stop()
            return
        deadline = self._swap_deadline()
        rem_ms = max(10, int((deadline - time.monotonic()) * 1000.0))
        self._hold_timer.start(rem_ms)

    def _flush_pending(self) -> None:
        pending = self._pending
        self._pending = ""
        if pending:
            self._apply_line(pending, action="flush")
            self._arm_hold()

    def _apply_line(self, text: str, action: str = "") -> None:
        self.line = text
        self._growth_stopped_at = time.monotonic()
        self._line_since = time.monotonic()
        self._paint()

    def _handle_command(self, kind: str, value, sent_at: float, recv_at: float, final: bool):
        if kind == "text":
            self.set_text(str(value or ""), is_final=final)
        elif kind == "status":
            self.set_status(str(value or ""))
        elif kind == "clear":
            self.clear()
        elif kind == "hide":
            self.hide()
        elif kind == "arm":
            self.arm()
        elif kind == "text_only":
            self.apply_text_only(bool(value))
        elif kind == "font_size":
            self.apply_font(int(value))
        elif kind == "font_color":
            self.apply_color(str(value))
        elif kind == "dismiss_timeout":
            self.apply_dismiss_timeout(int(value))
        elif kind == "source":
            self.show_source(str(value))
        elif kind == "segment":
            self.begin_segment()
        elif kind == "ask_state":
            self._broadcast_state(force=True)
        elif kind == "raise":
            self.show()
            self.raise_()


def _qt_udp_listener(window: QtCaptionWindow, sock: socket.socket):
    while True:
        try:
            data, _addr = sock.recvfrom(65535)
            recv_at = time.time()
            message = json.loads(data.decode("utf-8"))
            kind = message.get("kind")
            sent_at = message.get("sentAt", 0.0)
            final = bool(message.get("final"))
            window.command_received.emit(kind, message.get("value"), sent_at, recv_at, final)
        except Exception:
            continue


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", CAPTION_PORT))
    except OSError:
        print(f"Caption port {CAPTION_PORT} already in use.")
        return

    app = QApplication.instance() or QApplication(sys.argv)
    window = QtCaptionWindow()
    window.show()

    listener_thread = threading.Thread(target=_qt_udp_listener, args=(window, sock), daemon=True)
    listener_thread.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
