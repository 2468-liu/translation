"""Header dragging toolbar and window edge resizing calculations."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QWidget

if TYPE_CHECKING:
    from .caption_window import QtCaptionWindow

try:
    import ctypes
except ImportError:
    ctypes = None


class _HeaderWidget(QWidget):
    """Header toolbar widget that forwards drag events to native Windows HTCAPTION."""

    def __init__(self, parent: "QtCaptionWindow"):
        super().__init__(parent)
        self.caption_window = parent
        self.setMouseTracking(True)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            if pos.x() <= 18 or pos.x() >= self.width() - 18:
                self.caption_window.mousePressEvent(event)
                return
            if ctypes and sys.platform == "win32":
                ctypes.windll.user32.ReleaseCapture()
                ctypes.windll.user32.SendMessageW(self.caption_window.hwnd(), 0x00A1, 2, 0)
                self.caption_window._save_layout_config()
                event.accept()
                return
        elif event.button() == Qt.MouseButton.RightButton:
            if hasattr(self.caption_window, "_show_context_menu"):
                self.caption_window._show_context_menu(event.globalPosition().toPoint())
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        pos = event.position().toPoint()
        if pos.x() <= 18 or pos.x() >= self.width() - 18 or getattr(self.caption_window, "_resize_mode", None):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self.caption_window.mouseMoveEvent(event)

    def leaveEvent(self, event):
        if not getattr(self.caption_window, "_resize_mode", None):
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        self.caption_window.mouseReleaseEvent(event)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.caption_window.mouseDoubleClickEvent(event)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


def calculate_edge_at(window: QWidget, global_pos: QPoint, margin: int = 18) -> str:
    """Determine whether the mouse is on the left or right edge for resizing."""
    local_pos = window.mapFromGlobal(global_pos)
    y = local_pos.y()
    if not (-5 <= y <= window.height() + 5):
        return ""
    w = window.width()
    x = local_pos.x()
    if x <= margin:
        return "left"
    elif x >= w - margin:
        return "right"
    return ""


class WindowResizeController:
    """Manages window dragging, edge resizing, and width state persistence."""

    def __init__(self, window: "QtCaptionWindow"):
        self.win = window
        self.drag_pos: Optional[QPoint] = None
        self.resize_mode: Optional[str] = None
        self.resize_start_x: int = 0
        self.resize_start_geo: Optional[QRect] = None

    def handle_mouse_press(self, event: QMouseEvent) -> bool:
        if event.button() == Qt.MouseButton.LeftButton:
            edge = calculate_edge_at(self.win, event.globalPosition().toPoint())
            if edge:
                self.resize_mode = edge
                self.win._resize_mode = edge
                self.resize_start_x = event.globalPosition().toPoint().x()
                self.resize_start_geo = self.win.geometry()
                self.win.setCursor(Qt.CursorShape.SizeHorCursor)
                event.accept()
                return True
            if ctypes and sys.platform == "win32":
                ctypes.windll.user32.ReleaseCapture()
                ctypes.windll.user32.SendMessageW(self.win.hwnd(), 0x00A1, 2, 0)
                self.save_layout_config()
                event.accept()
                return True
            self.drag_pos = event.globalPosition().toPoint() - self.win.frameGeometry().topLeft()
            event.accept()
            return True
        elif event.button() == Qt.MouseButton.RightButton:
            self.win._show_context_menu(event.globalPosition().toPoint())
            event.accept()
            return True
        return False

    def handle_mouse_move(self, event: QMouseEvent) -> bool:
        # 1. Edge resize dragging
        if self.resize_mode and self.resize_start_geo and (event.buttons() & Qt.MouseButton.LeftButton):
            delta_x = event.globalPosition().toPoint().x() - self.resize_start_x
            min_w = 360
            max_w = max(min_w, self.win.screen_width - 20)

            if self.resize_mode == "right":
                new_w = max(min_w, min(max_w, self.resize_start_geo.width() + delta_x))
                new_x = self.resize_start_geo.x()
            else:  # "left"
                new_w = self.resize_start_geo.width() - delta_x
                if new_w < min_w:
                    new_w = min_w
                    new_x = self.resize_start_geo.right() - min_w + 1
                elif new_w > max_w:
                    new_w = max_w
                    new_x = max(0, self.resize_start_geo.right() - max_w + 1)
                else:
                    new_x = self.resize_start_geo.x() + delta_x

            self.win.setGeometry(new_x, self.resize_start_geo.y(), new_w, self.win.height())
            self.win.win_width = new_w
            self.win.update()
            event.accept()
            return True

        # 2. Non-Windows fallback dragging
        if self.drag_pos and (event.buttons() & Qt.MouseButton.LeftButton):
            self.win.move(event.globalPosition().toPoint() - self.drag_pos)
            event.accept()
            return True

        # 3. Hover edge cursor switching
        edge = calculate_edge_at(self.win, event.globalPosition().toPoint())
        if edge or self.resize_mode:
            self.win.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.win.setCursor(Qt.CursorShape.ArrowCursor)
        return False

    def handle_mouse_release(self, event: QMouseEvent) -> bool:
        was_resizing = bool(self.resize_mode)
        if self.resize_mode:
            self.resize_mode = None
            self.win._resize_mode = None
            self.resize_start_geo = None
            self.save_layout_config()
        if self.drag_pos:
            self.drag_pos = None
            self.save_layout_config()

        if was_resizing:
            self.win._paint()

        edge = calculate_edge_at(self.win, event.globalPosition().toPoint())
        if edge:
            self.win.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.win.setCursor(Qt.CursorShape.ArrowCursor)
        event.accept()
        return True

    def handle_mouse_double_click(self, event: QMouseEvent) -> bool:
        if event.button() == Qt.MouseButton.LeftButton:
            full_w = max(360, self.win.screen_width - 60)
            if self.win.width() >= full_w - 50:
                self.reset_default_width()
            else:
                self.maximize_width()
            event.accept()
            return True
        return False

    def save_layout_config(self):
        try:
            from server import config
            geo = self.win.geometry()
            cfg = config.load_desktop_config()
            cfg["caption_width"] = geo.width()
            cfg["caption_x"] = geo.x()
            config.save_desktop_config(cfg)
        except Exception as e:
            print(f"[WindowResizeController] Failed to save layout config: {e}")

    def maximize_width(self):
        target_w = max(360, self.win.screen_width - 60)
        target_x = max(0, (self.win.screen_width - target_w) // 2)
        geo = self.win.geometry()
        self.win.setGeometry(target_x, geo.y(), target_w, geo.height())
        self.win.win_width = target_w
        self.save_layout_config()
        self.win._paint()

    def reset_default_width(self):
        target_w = max(round(760 * self.win.scale), 420)
        target_w = min(target_w, max(420, self.win.screen_width - 80))
        target_x = max(0, (self.win.screen_width - target_w) // 2)
        geo = self.win.geometry()
        self.win.setGeometry(target_x, geo.y(), target_w, geo.height())
        self.win.win_width = target_w
        self.save_layout_config()
        self.win._paint()
