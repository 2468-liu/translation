"""Floating Desktop Mascot / Pet Widget for Realtime Translation.

Provides an intuitive, friendly on-screen control companion for users
who prefer direct desktop interactions over taskbar system tray icons.

Acts as a sibling child in the parent-child synchronization architecture:
- User clicks Pet -> propagates intent to Parent Coordinator.
- Parent Coordinator updates system state and synchronizes both Pet and Tray.
- User clicks Tray -> propagates intent to Parent Coordinator -> synchronizes Pet.

Interaction & UX:
- 360-Ball Magnetic Edge Snapping via smooth QPropertyAnimation (OutCubic).
- Auto-Dock Collapse: collapses into a mini tab after 2s idle near edge; expands on hover.
- Complete Hide: hover [×] close button + right-click '完全隐藏' + system tray restore.
"""

from __future__ import annotations

import math
import time
from typing import Optional

from PyQt6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

from . import config
from .ui.win32_topmost import pin_window_topmost, unpin_window_topmost


class DesktopPetWidget(QWidget):
    """Compact translucent floating desktop mascot with 360-ball interaction."""

    request_toggle_capture = pyqtSignal()
    request_toggle_caption_visibility = pyqtSignal()
    request_context_menu = pyqtSignal(QPoint)
    request_hide_pet = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None, coordinator: Optional[object] = None):
        super().__init__(parent)
        self.coordinator = coordinator

        # Internal state
        self._is_capturing = True
        self._caption_visible = True
        self._text_only = False
        self._is_audio_active = False
        self._bubble_text: Optional[str] = None
        self._bubble_timer = QTimer(self)
        self._bubble_timer.setSingleShot(True)
        self._bubble_timer.timeout.connect(self._hide_bubble)

        # Pulse timer for breathing animation when capturing
        self._pulse_phase = 0.0
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._on_pulse_tick)
        self._pulse_timer.start(80)

        # Dragging & click detection
        self._drag_start_pos: Optional[QPoint] = None
        self._drag_start_window_pos: Optional[QPoint] = None
        self._is_dragging = False
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._on_single_click_timeout)

        # 360-Ball Magnetic Snapping & Auto-Dock Collapse
        self._pos_anim: Optional[QPropertyAnimation] = None
        self._is_hovered = False
        self._is_docked = False
        self._dock_side: Optional[str] = None  # "left" | "right" | None
        self._dock_timer = QTimer(self)
        self._dock_timer.setSingleShot(True)
        self._dock_timer.timeout.connect(self._collapse_to_dock)

        # 60-Second Inactive Non-Intrusive Sleep (Demote Z-order & Translucent Fade)
        cfg = config.load_desktop_config()
        self._idle_sleep_enabled = bool(cfg.get("pet_auto_sleep", True))
        self._idle_timeout_ms = int(cfg.get("pet_sleep_timeout_ms", 60_000))
        self._is_sleeping = False
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._enter_idle_sleep)
        self._opacity_anim: Optional[QPropertyAnimation] = None

        self._init_window()
        self._restore_position()
        self.update_tool_tip()
        if self._idle_sleep_enabled:
            self._reset_idle_timer()

    def _init_window(self):
        self.setFixedSize(88, 88)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setMouseTracking(True)

    def _restore_position(self):
        cfg = config.load_desktop_config()
        x = cfg.get("pet_x")
        y = cfg.get("pet_y")
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            def_x = geom.width() - 110
            def_y = geom.height() - 140
            final_x = int(x) if x is not None else def_x
            final_y = int(y) if y is not None else def_y
            final_x = max(geom.left() + 10, min(geom.right() - 95, final_x))
            final_y = max(geom.top() + 10, min(geom.bottom() - 95, final_y))
            self.move(final_x, final_y)

    def _save_position(self):
        # Only save position when not collapsed into edge
        if not self._is_docked:
            cfg = config.load_desktop_config()
            cfg["pet_x"] = self.x()
            cfg["pet_y"] = self.y()
            config.save_desktop_config(cfg)

    def update_tool_tip(self):
        cap_action = "隐藏" if self._caption_visible else "显示"
        stat = "正在听音同传" if self._is_capturing else "已暂停"
        mode = "只有字" if getattr(self, "_text_only", False) else "黑底框"
        sleep_hint = " [💤 休眠避让中·触碰即醒]" if getattr(self, "_is_sleeping", False) else ""
        self.setToolTip(
            f"🐾 桌面翻译伴侣 ({stat} | {mode}{sleep_hint})\n"
            f"• 单击：{'暂停' if self._is_capturing else '开启'}识别与翻译\n"
            f"• 双击：{cap_action}字幕窗口\n"
            f"• 拖拽：按住鼠标左键自由移动，靠边自动吸附收起\n"
            f"• 右键：打开控制菜单（可完全隐藏）"
        )

    # --- Bidirectional Synchronisation API (Called by Parent Coordinator) ---

    def sync_text_only_state(self, is_text_only: bool, notify_bubble: bool = True):
        target = bool(is_text_only)
        if getattr(self, "_text_only", False) == target and not notify_bubble:
            return
        self._text_only = target
        self.update_tool_tip()
        if notify_bubble:
            self.show_bubble("🔤 模式：只有字" if self._text_only else "⬛ 模式：黑框")
        self.update()

    def sync_capture_state(self, is_capturing: bool, notify_bubble: bool = True):
        if self._is_capturing == is_capturing and not notify_bubble:
            return
        self._is_capturing = bool(is_capturing)
        self.update_tool_tip()
        if notify_bubble:
            self.show_bubble("▶ 正在听音" if self._is_capturing else "⏸ 已暂停")
        self.update()

    def sync_caption_visibility(self, is_visible: bool, notify_bubble: bool = False):
        self._caption_visible = bool(is_visible)
        self.update_tool_tip()
        if notify_bubble:
            self.show_bubble("🖥 字幕已显示" if self._caption_visible else "🖥 字幕已隐藏")
        self.update()

    def sync_audio_activity(self, is_active: bool):
        if self._is_audio_active != is_active:
            self._is_audio_active = is_active
            self.update()

    def show_bubble(self, text: str, duration_ms: int = 1600):
        if self._is_docked:
            return
        self._bubble_text = text
        self._bubble_timer.start(duration_ms)
        self.update()

    def _hide_bubble(self):
        self._bubble_text = None
        self.update()

    def _on_pulse_tick(self):
        if self._is_capturing:
            self._pulse_phase = (self._pulse_phase + 0.15) % 6.283
            self.update()

    # --- 360-Ball Magnetic Edge Snapping & Docking Animations ---

    def _animate_to_pos(self, target_pos: QPoint, duration_ms: int = 220):
        if self._pos_anim is None:
            self._pos_anim = QPropertyAnimation(self, b"pos")
            self._pos_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._pos_anim.stop()
        self._pos_anim.setDuration(duration_ms)
        self._pos_anim.setStartValue(self.pos())
        self._pos_anim.setEndValue(target_pos)
        self._pos_anim.start()

    def _snap_to_screen_edge(self):
        screen = QApplication.primaryScreen()
        if not screen:
            return
        geom = screen.availableGeometry()
        x = self.x()
        y = self.y()

        # Vertical clamping
        y = max(geom.top() + 6, min(geom.bottom() - self.height() - 6, y))

        # Horizontal magnetic snap
        dist_left = abs(x - geom.left())
        dist_right = abs(geom.right() - (x + self.width()))
        threshold = 80

        if dist_left < threshold:
            target_x = geom.left() + 4
            self._dock_side = "left"
        elif dist_right < threshold:
            target_x = geom.right() - self.width() - 4
            self._dock_side = "right"
        else:
            target_x = max(geom.left() + 6, min(geom.right() - self.width() - 6, x))
            self._dock_side = None

        target_pos = QPoint(target_x, y)
        self._animate_to_pos(target_pos, duration_ms=220)
        self._is_docked = False

        if self._dock_side and not self._is_hovered:
            self._dock_timer.start(2000)

    def _collapse_to_dock(self):
        if not self._dock_side or self._is_hovered or self._is_dragging:
            return
        screen = QApplication.primaryScreen()
        if not screen:
            return
        geom = screen.availableGeometry()
        visible_tab_width = 30

        if self._dock_side == "left":
            target_x = geom.left() - self.width() + visible_tab_width
        elif self._dock_side == "right":
            target_x = geom.right() - visible_tab_width + 1
        else:
            return

        self._is_docked = True
        self._animate_to_pos(QPoint(target_x, self.y()), duration_ms=250)
        self.update()

    def showEvent(self, event):
        self._reset_idle_timer()
        try:
            super().showEvent(event)
        except Exception:
            pass

    def hideEvent(self, event):
        if hasattr(self, "_idle_timer") and self._idle_timer.isActive():
            self._idle_timer.stop()
        try:
            super().hideEvent(event)
        except Exception:
            pass

    def enterEvent(self, event):
        self._is_hovered = True
        if self._dock_timer.isActive():
            self._dock_timer.stop()

        if self._is_sleeping:
            self._wake_from_idle_sleep()
        else:
            self._reset_idle_timer()

        if self._is_docked and self._dock_side:
            screen = QApplication.primaryScreen()
            if screen:
                geom = screen.availableGeometry()
                if self._dock_side == "left":
                    target_x = geom.left() + 4
                else:
                    target_x = geom.right() - self.width() - 4
                self._is_docked = False
                self._animate_to_pos(QPoint(target_x, self.y()), duration_ms=180)
        self.update()
        try:
            super().enterEvent(event)
        except (TypeError, Exception):
            pass

    def leaveEvent(self, event):
        self._is_hovered = False
        if self._dock_side and not self._is_docked and not self._is_dragging:
            self._dock_timer.start(2000)
        self._reset_idle_timer()
        self.update()
        try:
            super().leaveEvent(event)
        except (TypeError, Exception):
            pass

    # --- Mouse Events (Click, Drag, DoubleClick, Close [x]) ---

    def mousePressEvent(self, event):
        if self._is_sleeping:
            self._wake_from_idle_sleep()
        else:
            self._reset_idle_timer()

        if event.button() == Qt.MouseButton.LeftButton:
            # Check if clicked on hover close button [x]
            if self._is_hovered and not self._is_docked:
                close_rect = QRectF(self.width() - 24, 4, 20, 20)
                if close_rect.contains(event.position()):
                    self.hide_completely()
                    event.accept()
                    return

            self._drag_start_pos = event.globalPosition().toPoint()
            self._drag_start_window_pos = self.pos()
            self._is_dragging = False
            if self._pos_anim and self._pos_anim.state() == QPropertyAnimation.State.Running:
                self._pos_anim.stop()
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())
            event.accept()

    def mouseMoveEvent(self, event):
        if self._is_sleeping:
            self._wake_from_idle_sleep()
        else:
            self._reset_idle_timer()

        if self._drag_start_pos is not None:
            cur_pos = event.globalPosition().toPoint()
            diff = cur_pos - self._drag_start_pos
            if diff.manhattanLength() > 5:
                self._is_dragging = True
                if self._click_timer.isActive():
                    self._click_timer.stop()
                if self._dock_timer.isActive():
                    self._dock_timer.stop()
                self._is_docked = False
                self.move(self._drag_start_window_pos + diff)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._reset_idle_timer()
        if event.button() == Qt.MouseButton.LeftButton:
            if getattr(self, "_ignore_next_release", False):
                self._ignore_next_release = False
                self._drag_start_pos = None
                self._drag_start_window_pos = None
                event.accept()
                return

            if self._is_dragging:
                self._is_dragging = False
                self._snap_to_screen_edge()
                self._save_position()
            else:
                self._click_timer.start(220)
            self._drag_start_pos = None
            self._drag_start_window_pos = None
            event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._ignore_next_release = True
            if self._click_timer.isActive():
                self._click_timer.stop()
            try:
                if self.coordinator and hasattr(self.coordinator, "toggle_caption_visibility"):
                    self.coordinator.toggle_caption_visibility(from_source="pet")
                else:
                    self.request_toggle_caption_visibility.emit()
            except Exception as e:
                print(f"[DesktopPetWidget] Double-click handler error: {e}")
            event.accept()

    def _on_single_click_timeout(self):
        try:
            if self.coordinator and hasattr(self.coordinator, "toggle_capture"):
                self.coordinator.toggle_capture(from_source="pet")
            else:
                self.request_toggle_capture.emit()
        except Exception as e:
            print(f"[DesktopPetWidget] Single-click handler error: {e}")

    # --- Non-Intrusive Idle Sleep & Z-Order Management ---

    def _reset_idle_timer(self):
        if not getattr(self, "_idle_sleep_enabled", True) or not self.isVisible():
            if hasattr(self, "_idle_timer") and self._idle_timer.isActive():
                self._idle_timer.stop()
            return
        if hasattr(self, "_idle_timer"):
            self._idle_timer.start(self._idle_timeout_ms)

    def _animate_opacity(self, target_opacity: float, duration_ms: int = 350):
        if self._opacity_anim is None:
            self._opacity_anim = QPropertyAnimation(self, b"windowOpacity")
            self._opacity_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._opacity_anim.stop()
        self._opacity_anim.setDuration(duration_ms)
        self._opacity_anim.setStartValue(self.windowOpacity())
        self._opacity_anim.setEndValue(float(target_opacity))
        self._opacity_anim.start()

    def _enter_idle_sleep(self):
        """1 minute idle reached: demote to normal Z-order and fade to ghost translucent state."""
        if not self._idle_sleep_enabled or self._is_hovered or self._is_dragging or self._is_docked:
            return
        self._is_sleeping = True
        try:
            unpin_window_topmost(int(self.winId()))
        except Exception:
            pass
        self._animate_opacity(0.25, duration_ms=450)
        self.update_tool_tip()
        self.update()

    def _wake_from_idle_sleep(self):
        """Wake up from idle sleep: restore topmost Z-order and 100% opacity."""
        if not self._is_sleeping:
            self._reset_idle_timer()
            return
        self._is_sleeping = False
        try:
            pin_window_topmost(int(self.winId()))
        except Exception:
            pass
        self._animate_opacity(1.0, duration_ms=200)
        self._reset_idle_timer()
        self.update_tool_tip()
        self.update()

    def set_idle_sleep_enabled(self, enabled: bool):
        self._idle_sleep_enabled = bool(enabled)
        cfg = config.load_desktop_config()
        cfg["pet_auto_sleep"] = self._idle_sleep_enabled
        config.save_desktop_config(cfg)
        if not self._idle_sleep_enabled:
            if self._is_sleeping:
                self._wake_from_idle_sleep()
            if hasattr(self, "_idle_timer") and self._idle_timer.isActive():
                self._idle_timer.stop()
        else:
            self._reset_idle_timer()

    def hide_completely(self):
        """Completely hide the pet widget with state persistence and tray synchronization."""
        print("[DesktopPetWidget] Hiding pet widget completely...")
        if self._idle_timer.isActive():
            self._idle_timer.stop()
        if self._dock_timer.isActive():
            self._dock_timer.stop()
        if self.coordinator and hasattr(self.coordinator, "_toggle_pet_visibility"):
            self.coordinator._toggle_pet_visibility(False)
        else:
            self.hide()
            cfg = config.load_desktop_config()
            cfg["show_pet"] = False
            config.save_desktop_config(cfg)
            self.request_hide_pet.emit()

        if self.coordinator and hasattr(self.coordinator, "command_received"):
            self.coordinator.command_received.emit(
                "toast", "桌面伴侣挂件已隐藏，随时可通过托盘图标右键菜单重新开启", time.time(), time.time(), False
            )

    def _show_context_menu(self, global_pos: QPoint):
        # Create pet-specific context menu with quick Hide action
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: rgba(15, 23, 42, 0.95);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 8px;
                padding: 4px;
                color: #f1f5f9;
                font-family: 'Microsoft YaHei';
                font-size: 13px;
            }
            QMenu::item {
                padding: 6px 20px 6px 16px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #3b82f6;
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background-color: rgba(255, 255, 255, 0.1);
            }
        """)

        hide_act = menu.addAction("❌ 完全隐藏桌面挂件")
        hide_act.triggered.connect(self.hide_completely)

        idle_act = menu.addAction("💤 闲置1分钟自动沉浸休眠 (不挡视频)")
        idle_act.setCheckable(True)
        idle_act.setChecked(self._idle_sleep_enabled)
        idle_act.toggled.connect(self.set_idle_sleep_enabled)

        menu.addSeparator()

        # Add common controls from coordinator if available
        if self.coordinator and hasattr(self.coordinator, "toggle_capture"):
            cap_stat = "暂停识别与翻译" if self._is_capturing else "开启识别与翻译"
            toggle_cap_act = menu.addAction(f"⏸ {cap_stat}")
            toggle_cap_act.triggered.connect(lambda: self.coordinator.toggle_capture(from_source="pet"))

        if self.coordinator and hasattr(self.coordinator, "toggle_caption_visibility"):
            vis_stat = "隐藏字幕窗口" if self._caption_visible else "显示字幕窗口"
            toggle_vis_act = menu.addAction(f"🖥 {vis_stat}")
            toggle_vis_act.triggered.connect(lambda: self.coordinator.toggle_caption_visibility(from_source="pet"))

        if self.coordinator and hasattr(self.coordinator, "clear"):
            clear_act = menu.addAction("🧹 清空当前字幕")
            clear_act.triggered.connect(self.coordinator.clear)

        menu.addSeparator()

        if self.coordinator and hasattr(self.coordinator, "_quit_app"):
            quit_act = menu.addAction("🚪 退出程序")
            quit_act.triggered.connect(self.coordinator._quit_app)

        hwnd = 0
        try:
            import ctypes
            import sys
            if sys.platform == "win32":
                hwnd = int(self.winId())
                ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

        try:
            menu.exec(global_pos)
        finally:
            if hwnd:
                try:
                    import ctypes
                    ctypes.windll.user32.PostMessageW(hwnd, 0, 0, 0)
                except Exception:
                    pass

    # --- High Fidelity Translucent Mascot Rendering ---

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        # 1. Render Floating Speech Bubble (if active and not collapsed)
        if self._bubble_text and not self._is_docked:
            self._paint_bubble(painter)

        # 2. Render Pet Mascot Body & Headphones
        pet_rect = QRectF(14, 20, 60, 60)
        self._paint_mascot(painter, pet_rect)

        # 3. Render Hover Close Button [x] if hovered and expanded
        if self._is_hovered and not self._is_docked:
            self._paint_close_button(painter)

    def _paint_bubble(self, painter: QPainter):
        text = self._bubble_text or ""
        font = QFont("Microsoft YaHei", 9, QFont.Weight.Bold)
        painter.setFont(font)

        bubble_rect = QRectF(4, 2, 80, 16)
        path = QPainterPath()
        path.addRoundedRect(bubble_rect, 8, 8)

        painter.fillPath(path, QColor(15, 23, 42, 220))
        border_col = QColor(56, 189, 248, 200) if self._is_capturing else QColor(245, 158, 11, 200)
        painter.strokePath(path, QPen(border_col, 1.2))

        painter.setPen(border_col)
        painter.drawText(bubble_rect, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_close_button(self, painter: QPainter):
        btn_rect = QRectF(self.width() - 22, 5, 16, 16)
        path = QPainterPath()
        path.addEllipse(btn_rect)
        painter.fillPath(path, QColor(239, 68, 68, 220))
        painter.strokePath(path, QPen(QColor(255, 255, 255, 180), 1.0))

        # Draw 'x' inside
        cx, cy = btn_rect.center().x(), btn_rect.center().y()
        x_pen = QPen(QColor(255, 255, 255, 240), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(x_pen)
        painter.drawLine(int(cx - 3.5), int(cy - 3.5), int(cx + 3.5), int(cy + 3.5))
        painter.drawLine(int(cx + 3.5), int(cy - 3.5), int(cx - 3.5), int(cy + 3.5))

    def _paint_mascot(self, painter: QPainter, rect: QRectF):
        cx = rect.center().x()
        cy = rect.center().y()

        pulse_alpha = int(180 + 60 * math.sin(self._pulse_phase)) if self._is_capturing else 70
        halo_color = QColor(56, 189, 248, pulse_alpha) if self._is_capturing else QColor(148, 163, 184, 80)

        # 1. Outer breathing halo
        halo_path = QPainterPath()
        halo_rect = rect.adjusted(-2, -2, 2, 2)
        halo_path.addRoundedRect(halo_rect, 19, 19)
        painter.strokePath(halo_path, QPen(halo_color, 2.0))

        # 2. Main Squircle Body
        body_path = QPainterPath()
        body_path.addRoundedRect(rect, 17, 17)
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, QColor(24, 32, 47, 240))
        grad.setColorAt(1.0, QColor(15, 23, 42, 240))
        painter.fillPath(body_path, grad)

        # 3. Headphone Band (Arch over head)
        hp_pen = QPen(halo_color, 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(hp_pen)
        painter.drawArc(int(cx - 20), int(cy - 23), 40, 26, 30 * 16, 120 * 16)

        # 4. Headphone Earcups (Left & Right)
        earcup_w, earcup_h = 7.0, 15.0
        left_ec = QRectF(cx - 24, cy - 8, earcup_w, earcup_h)
        path_l = QPainterPath()
        path_l.addRoundedRect(left_ec, 3.5, 3.5)
        painter.fillPath(path_l, halo_color)

        right_ec = QRectF(cx + 17, cy - 8, earcup_w, earcup_h)
        path_r = QPainterPath()
        path_r.addRoundedRect(right_ec, 3.5, 3.5)
        painter.fillPath(path_r, halo_color)

        # 5. Mascot Eyes & Expression
        painter.setPen(Qt.PenStyle.NoPen)
        eye_color = QColor(56, 189, 248) if self._is_capturing else QColor(148, 163, 184)
        painter.setBrush(eye_color)

        if self._is_capturing:
            eye_pen = QPen(eye_color, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(eye_pen)
            painter.drawArc(int(cx - 13), int(cy - 6), 9, 8, 30 * 16, 120 * 16)
            painter.drawArc(int(cx + 4), int(cy - 6), 9, 8, 30 * 16, 120 * 16)

            sw_pen = QPen(QColor(125, 211, 252, 220), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(sw_pen)
            sw_height = 8.0 if self._is_audio_active else 4.0
            painter.drawLine(int(cx - 4), int(cy + 9), int(cx - 4), int(cy + 9 - sw_height * 0.6))
            painter.drawLine(int(cx), int(cy + 10), int(cx), int(cy + 10 - sw_height))
            painter.drawLine(int(cx + 4), int(cy + 9), int(cx + 4), int(cy + 9 - sw_height * 0.6))
        else:
            sleep_pen = QPen(eye_color, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(sleep_pen)
            painter.drawLine(int(cx - 12), int(cy - 2), int(cx - 5), int(cy - 2))
            painter.drawLine(int(cx + 5), int(cy - 2), int(cx + 12), int(cy - 2))
            painter.drawArc(int(cx - 4), int(cy + 4), 8, 5, 200 * 16, 140 * 16)

        # 6. Status Dot Badge
        badge_cx = rect.right() - 2
        badge_cy = rect.top() + 2
        badge_rect = QRectF(badge_cx - 5, badge_cy - 5, 10, 10)
        badge_col = QColor(34, 197, 94) if self._is_capturing else QColor(245, 158, 11)
        painter.setPen(QPen(QColor(15, 23, 42), 1.5))
        painter.setBrush(badge_col)
        painter.drawEllipse(badge_rect)
