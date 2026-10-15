"""PyQt6-based high-fidelity realtime subtitle overlay window.

Provides true alpha-channel translucent rendering, anti-aliased font rendering with
crisp drop shadow, smooth resizing, draggable positioning, and differentiated auto-dismiss.
Aligns 100% with server.caption_window timing, block-mode punct gate, and state machine.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Optional

def _signature(text: str) -> str:
    """Short stable digest of a line, for matching probes against a recording."""
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:12]

try:
    import ctypes
except ImportError:
    ctypes = None

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QIcon,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .. import config, perf
from ..caption_timing import (
    CAPTION_PORT,
    CLAUSE_MAX_MS,
    HOLD_ABS_MAX_MS,
    HOLD_ABS_MIN_MS,
    SETTLE_MS,
    STATE_PORT,
    _collapse_repeats,
    caption_action,
    classify_caption,
    clamped_font_px,
    flush_caption,
    hold_ms,
    introduces_boundary,
    pace_scale,
    punctuation_marks,
    step_caption,
    swap_deadline,
    wrap_all,
    wrap_caption,
    _lcs_len,
)
from ..config import BLOCK_OUTPUT, _env_int
from ..qt_pet_widget import DesktopPetWidget

from .win32_topmost import (
    HWND_TOPMOST,
    GWL_EXSTYLE,
    WS_EX_TOOLWINDOW,
    WS_EX_TOPMOST,
    WS_EX_NOACTIVATE,
    SWP_NOSIZE,
    SWP_NOMOVE,
    SWP_NOACTIVATE,
    SWP_SHOWWINDOW,
    pin_window_topmost,
    strip_window_frame,
)
from .punct_gate import (
    CaptionDisplayBuffer,
    ChromiumRollingBuffer,
    calculate_text_similarity,
    decide_final_action,
    get_lcp_length,
)
from .window_resizer import _HeaderWidget, calculate_edge_at, WindowResizeController
from .tray_manager import (
    _LANG,
    TRAY_MENU_STYLE,
    find_app_icon_path as _find_app_icon_path,
    create_tray_icon as _create_tray_icon,
    show_tray_menu_native,
)
from .caption_renderer import (
    compute_bottom_pinned_baselines,
    render_caption_text,
    wrap_caption_typography,
    render_caption_overlay,
    update_window_layout_and_geometry,
)
from .caption_menu import CaptionMenuBuilder
from .caption_animator import CaptionFadeAnimator
from .history_drawer import HistoryDrawerWidget
from .prefix_freezer import MonotonicPrefixFreezer




class QtCaptionWindow(QWidget):
    """Modern frameless, translucent, topmost caption window."""

    command_received = pyqtSignal(str, object, float, float, bool)
    config_changed = pyqtSignal(dict)

    def __init__(
        self,
        scale: float = 1.0,
        block_by_punct: Optional[bool] = None,
        rolling_two_lines: Optional[bool] = None,
    ):
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

        self._segment_safety_timer = QTimer(self)
        self._segment_safety_timer.setSingleShot(True)
        self._segment_safety_timer.timeout.connect(self._on_dismiss_timeout)

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
        if block_by_punct is not None:
            self._block_by_punct = bool(block_by_punct)
        else:
            _cfg = config.load_desktop_config()
            if "block_by_punct" in _cfg:
                self._block_by_punct = bool(_cfg["block_by_punct"])
            else:
                self._block_by_punct = bool(BLOCK_OUTPUT)
        if rolling_two_lines is not None:
            self.rolling_two_lines = bool(rolling_two_lines)
        else:
            _env_roll = os.environ.get("TRANSLATION_ROLLING_TWO_LINES")
            if _env_roll is not None:
                self.rolling_two_lines = _env_roll.strip().lower() in ("1", "true", "yes")
            elif "pytest" in sys.modules or "unittest" in sys.modules:
                self.rolling_two_lines = False
            else:
                _cfg = config.load_desktop_config()
                self.rolling_two_lines = bool(_cfg.get("rolling_two_lines", True))
        self.rolling_buffer = ChromiumRollingBuffer()
        self.display_buffer = CaptionDisplayBuffer()
        self.prefix_freezer = MonotonicPrefixFreezer()
        self.stable_prefix: str = ""
        self.hypothesis_suffix: str = ""
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
        self._resize_mode: Optional[str] = None
        self._resize_start_x: int = 0
        self._resize_start_geo: Optional[QRect] = None

        # Standalone settings state
        self.source_lang = "auto"
        self.target_lang = "zh"
        cfg_init = config.load_desktop_config()
        self.asr_backend: str = str(cfg_init.get("asr_backend", "qwen3"))
        self.partial_interval_ms: int = int(cfg_init.get("partial_interval_ms", 1000 if self.asr_backend == "qwen3" else 160))
        self.current_audio_pid = 0
        self._is_capturing: bool = True
        self.source_line: str = ""
        self.bilingual_mode: str = str(cfg_init.get("bilingual_mode", "trans_only"))
        self._tray_icon: Optional[QSystemTrayIcon] = None
        self._tray_menu: Optional[QMenu] = None
        self._text_only_act: Optional[QAction] = None
        self.pet_widget: Optional[DesktopPetWidget] = None

        # Modular Controllers
        self.fade_animator = CaptionFadeAnimator(self)
        self.history_drawer = HistoryDrawerWidget(self)
        self.resizer = WindowResizeController(self)
        self.menu_manager = CaptionMenuBuilder(self)

        self._init_window()
        self._init_ui()
        self._init_pet_widget()
        self._init_tray_and_menus()
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

        cfg = config.load_desktop_config()
        saved_w = cfg.get("caption_width")
        if saved_w and isinstance(saved_w, (int, float)):
            self.win_width = max(360, min(self.screen_width - 20, int(saved_w)))
        else:
            self.win_width = max(round(760 * self.scale), 420)
            self.win_width = min(self.win_width, max(420, self.screen_width - 80))
        self.win_height = 80

        saved_x = cfg.get("caption_x")
        if saved_x is not None and isinstance(saved_x, (int, float)) and 0 <= int(saved_x) <= self.screen_width - 100:
            init_x = int(saved_x)
        else:
            init_x = max(0, (self.screen_width - self.win_width) // 2)

        init_y = max(0, self.screen_height - self.win_height - 120)
        self.setGeometry(init_x, init_y, self.win_width, self.win_height)
        self.setWindowIcon(_create_tray_icon())

    def _init_ui(self):
        self.header_widget = _HeaderWidget(self)
        self.header_widget.setFixedHeight(26)
        self.header_layout = QHBoxLayout(self.header_widget)
        self.header_layout.setContentsMargins(10, 2, 8, 0)
        self.header_layout.setSpacing(6)

        self.status_label = QLabel("", self.header_widget)
        self.status_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
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
        self.text_only_cb.toggled.connect(lambda chk: self.apply_text_only(chk, from_source="caption_box"))

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
        strip_window_frame(self.hwnd(), self.visible)

    def _pin(self) -> None:
        if (hasattr(self, "_context_menu") and self._context_menu and self._context_menu.isVisible()) or (
            hasattr(self, "_tray_menu") and self._tray_menu and self._tray_menu.isVisible()
        ):
            return
        if self.visible and self.isVisible():
            pin_window_topmost(self.hwnd())

    def _safe_close_tray_menu(self):
        self.menu_manager.safe_close_menus()

    def _show_context_menu(self, pos: Optional[QPoint] = None):
        self.menu_manager.show_context_menu(pos)

    def _show_tray_menu(self, pos: Optional[QPoint] = None):
        self.menu_manager.show_tray_menu(pos)

    def _init_tray_and_menus(self):
        self._context_menu = self.menu_manager.build_context_menu()
        self._tray_menu = self.menu_manager.build_tray_menu()

        is_test = bool(os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("TRANSLATION_TEST_MODE") or "unittest" in sys.modules)
        if QSystemTrayIcon.isSystemTrayAvailable() and not is_test:
            self._tray_icon = QSystemTrayIcon(_create_tray_icon(), self)
            self._update_tray_tooltip()
            self._tray_icon.setContextMenu(self._tray_menu)
            self._tray_icon.activated.connect(self._on_tray_activated)
            self._tray_icon.show()
        else:
            self._tray_icon = None

    def closeEvent(self, event):
        if getattr(self, "_tray_icon", None):
            try:
                self._tray_icon.hide()
            except Exception:
                pass
        super().closeEvent(event)

    def _update_tray_tooltip(self):
        if not getattr(self, "_tray_icon", None):
            return
        asr_name = "Qwen3-ASR" if getattr(self, "asr_backend", "qwen3") == "qwen3" else "Nemotron-ASR"
        mode_str = "双行滚动" if getattr(self, "rolling_two_lines", True) else "单行显示"
        cad_str = f"{getattr(self, 'partial_interval_ms', 1000)}ms"
        preset = getattr(self.menu_manager, "get_current_preset", lambda: "movie")()
        preset_names = {"movie": "电影追剧", "speech": "演讲对话", "stream": "极速同传"}
        preset_str = preset_names.get(preset, "电影追剧")
        tip = (
            f"实时AI桌面字幕 [{preset_str}]\n"
            f"• 状态: {asr_name} | {cad_str}切片 | {mode_str}\n"
            f"• 左键单击: 显示/隐藏字幕\n"
            f"• 右键单击: 快捷菜单"
        )
        self._tray_icon.setToolTip(tip)

    def show_history_drawer(self):
        self.menu_manager.safe_close_menus()
        self.history_drawer.show_drawer()

    def _on_toggle_capture_triggered(self):
        self.menu_manager.safe_close_menus()
        QTimer.singleShot(10, lambda: self.toggle_capture(from_source="tray"))

    def _on_toggle_visibility_triggered(self):
        self.menu_manager.safe_close_menus()
        QTimer.singleShot(10, self._toggle_visibility)

    def _on_show_pet_toggled(self, checked: bool):
        self.menu_manager.safe_close_menus()
        self._toggle_pet_visibility(checked)

    def _on_clear_triggered(self):
        self.menu_manager.safe_close_menus()
        QTimer.singleShot(10, self.clear)

    def _on_choose_model_directory_triggered(self):
        self.menu_manager.safe_close_menus()
        QTimer.singleShot(50, self.menu_manager._on_choose_model_directory)

    def _on_quit_app_triggered(self):
        self.menu_manager.safe_close_menus()
        QTimer.singleShot(10, self._quit_app)

    def _on_maximize_width_triggered(self):
        self.menu_manager.safe_close_menus()
        QTimer.singleShot(10, self.resizer.maximize_width)

    def _on_reset_width_triggered(self):
        self.menu_manager.safe_close_menus()
        QTimer.singleShot(10, self.resizer.reset_default_width)

    def _on_text_only_toggled(self, checked: bool):
        self.menu_manager.safe_close_menus()
        self.apply_text_only(checked, from_source="tray")

    def _on_block_punct_toggled(self, checked: bool):
        self.menu_manager.safe_close_menus()
        self.set_block_by_punct(checked)

    def _on_rolling_two_lines_toggled(self, checked: bool):
        self.menu_manager.safe_close_menus()
        self.set_rolling_two_lines(checked)

    def set_block_by_punct(self, enabled: bool) -> None:
        self._block_by_punct = bool(enabled)
        cfg = config.load_desktop_config()
        cfg["block_by_punct"] = self._block_by_punct
        config.save_desktop_config(cfg)
        if hasattr(self, "_block_punct_act") and self._block_punct_act:
            self._block_punct_act.blockSignals(True)
            self._block_punct_act.setChecked(self._block_by_punct)
            self._block_punct_act.blockSignals(False)
        if hasattr(self, "_tray_block_punct_act") and self._tray_block_punct_act:
            self._tray_block_punct_act.blockSignals(True)
            self._tray_block_punct_act.setChecked(self._block_by_punct)
            self._tray_block_punct_act.blockSignals(False)
        self.config_changed.emit({"block_by_punct": self._block_by_punct})

    def set_rolling_two_lines(self, enabled: bool) -> None:
        self.rolling_two_lines = bool(enabled)
        cfg = config.load_desktop_config()
        cfg["rolling_two_lines"] = self.rolling_two_lines
        config.save_desktop_config(cfg)
        if hasattr(self, "_rolling_two_lines_act") and self._rolling_two_lines_act:
            self._rolling_two_lines_act.blockSignals(True)
            self._rolling_two_lines_act.setChecked(self.rolling_two_lines)
            self._rolling_two_lines_act.blockSignals(False)
        if hasattr(self, "_tray_rolling_two_lines_act") and self._tray_rolling_two_lines_act:
            self._tray_rolling_two_lines_act.blockSignals(True)
            self._tray_rolling_two_lines_act.setChecked(self.rolling_two_lines)
            self._tray_rolling_two_lines_act.blockSignals(False)
        self._update_tray_tooltip()
        self.config_changed.emit({"rolling_two_lines": self.rolling_two_lines})
        self._paint()

    def set_cadence_ui(self, ms: int):
        self.partial_interval_ms = ms
        for grp in (getattr(self, "cad_group", None), getattr(self, "tray_cad_group", None)):
            if grp:
                for act in grp.actions():
                    if act.data() == ms:
                        grp.blockSignals(True)
                        act.setChecked(True)
                        grp.blockSignals(False)
                        break
        self._update_tray_tooltip()

    def set_asr_backend_ui(self, backend: str):
        self.asr_backend = backend
        for grp in (getattr(self, "asr_group", None), getattr(self, "tray_asr_group", None)):
            if grp:
                for act in grp.actions():
                    if act.data() == backend:
                        grp.blockSignals(True)
                        act.setChecked(True)
                        grp.blockSignals(False)
                        break
        self._update_tray_tooltip()

    def _on_tray_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._toggle_visibility()
        elif reason in (
            QSystemTrayIcon.ActivationReason.Context,
            QSystemTrayIcon.ActivationReason.MiddleClick,
        ):
            if hasattr(self, "_tray_menu") and self._tray_menu and not self._tray_menu.isVisible():
                self._show_tray_menu(QCursor.pos())

    def _init_pet_widget(self):
        try:
            self.pet_widget = DesktopPetWidget(parent=None, coordinator=self)
            self.pet_widget.sync_text_only_state(self.text_only, notify_bubble=False)
            cfg = config.load_desktop_config()
            if cfg.get("show_pet", False):
                self.pet_widget.show()
                self.pet_widget.raise_()
            else:
                self.pet_widget.hide()
        except Exception as e:
            print(f"[QtCaptionWindow] Pet widget init error: {e}")
            self.pet_widget = None

    def toggle_capture(self, from_source: str = "internal"):
        target_state = not getattr(self, "_is_capturing", True)
        self.set_capturing(target_state, from_source=from_source)

    def set_capturing(self, active: bool, from_source: str = "internal"):
        self._is_capturing = bool(active)

        # 1. Update Tray and Context Menus
        if getattr(self, "_toggle_capture_act", None):
            self._toggle_capture_act.setText("⏸ 暂停识别与翻译" if self._is_capturing else "▶ 开启识别与翻译")
        if getattr(self, "_tray_toggle_capture_act", None):
            self._tray_toggle_capture_act.setText("⏸ 暂停识别与翻译" if self._is_capturing else "▶ 开启识别与翻译")

        # 2. Synchronize Pet Widget (Child 2)
        if getattr(self, "pet_widget", None):
            self.pet_widget.sync_capture_state(self._is_capturing, notify_bubble=(from_source != "init"))

        # 3. Update Status and notify Backend pipeline
        if self._is_capturing:
            self.set_status(self.source_lang)
        else:
            self.set_status("已暂停")
        self.config_changed.emit({"is_capturing": self._is_capturing})

    def _toggle_capture(self):
        self.toggle_capture(from_source="tray")

    def toggle_caption_visibility(self, from_source: str = "internal"):
        if self.isVisible() and self.visible:
            self.withdraw()
            is_vis = False
        else:
            self.show()
            self.raise_()
            is_vis = True

        # Synchronize Tray and Context Menu Actions
        if getattr(self, "_toggle_visibility_act", None):
            self._toggle_visibility_act.setText("隐藏字幕" if is_vis else "显示字幕")
        if getattr(self, "_context_toggle_visibility_act", None):
            self._context_toggle_visibility_act.setText("隐藏字幕" if is_vis else "显示字幕")

        # Synchronize Pet Widget (Child 2)
        if getattr(self, "pet_widget", None):
            self.pet_widget.sync_caption_visibility(is_vis, notify_bubble=(from_source == "pet"))

    def _toggle_visibility(self):
        self.toggle_caption_visibility(from_source="tray")

    def _toggle_pet_visibility(self, checked: bool):
        if getattr(self, "pet_widget", None):
            if checked:
                self.pet_widget.show()
                self.pet_widget.raise_()
            else:
                self.pet_widget.hide()
        cfg = config.load_desktop_config()
        cfg["show_pet"] = bool(checked)
        config.save_desktop_config(cfg)
        if getattr(self, "_show_pet_act", None) and self._show_pet_act.isChecked() != checked:
            self._show_pet_act.blockSignals(True)
            self._show_pet_act.setChecked(checked)
            self._show_pet_act.blockSignals(False)

    def _quit_app(self):
        self.hide()
        if getattr(self, "pet_widget", None):
            self.pet_widget.close()
            self.pet_widget.deleteLater()
        app = QApplication.instance()
        if app:
            app.quit()

    def apply_text_only(self, enabled: bool, from_source: str = "internal") -> None:
        target = bool(enabled)
        # 1. Idempotency guard: prevent recursion and redundant state propagation
        if hasattr(self, "text_only") and self.text_only == target and from_source not in ("init", "force"):
            return
        self.text_only = target

        # 2. Synchronize Child 1: Caption Frame Checkbox (blockSignals to avoid recursive triggers)
        if hasattr(self, "text_only_cb") and self.text_only_cb:
            if self.text_only_cb.isChecked() != self.text_only:
                self.text_only_cb.blockSignals(True)
                self.text_only_cb.setChecked(self.text_only)
                self.text_only_cb.blockSignals(False)

        # 3. Synchronize Child 2: System Tray Action (blockSignals to avoid recursive triggers)
        if getattr(self, "_text_only_act", None):
            if self._text_only_act.isChecked() != self.text_only:
                self._text_only_act.blockSignals(True)
                self._text_only_act.setChecked(self.text_only)
                self._text_only_act.blockSignals(False)

        # 4. Synchronize Child 3: Desktop Pet Widget
        if getattr(self, "pet_widget", None):
            self.pet_widget.sync_text_only_state(
                self.text_only,
                notify_bubble=(from_source in ("caption_box", "tray", "pet")),
            )

        # 5. Visual styling & state broadcast
        self._strip_frame()
        self._broadcast_state()
        self.update()

        # 6. Persistent configuration & config_changed signal
        if from_source != "init":
            cfg = config.load_desktop_config()
            cfg["text_only"] = self.text_only
            config.save_desktop_config(cfg)
            self.config_changed.emit({"text_only": self.text_only})

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
        try:
            val = int(ms)
        except (TypeError, ValueError):
            return
        self.dismiss_ms = max(0, min(30000, val))
        self._dismiss_timer.stop()
        if self.line and self.visible and self.dismiss_ms > 0:
            self._start_dismiss_timer()
        self._broadcast_state()

    def _start_dismiss_timer(self) -> None:
        """Start auto-hide timer with dynamic TTL based on character count and human reading speed."""
        if self.line and self.dismiss_ms > 0:
            char_count = len(self.line.strip())
            # Human reading baseline: 1500ms eye saccade + 220ms per character
            human_read_ttl = 1500 + int(char_count * 220)
            base_dismiss = int(self.dismiss_ms)
            effective_ttl = max(base_dismiss, min(25000, human_read_ttl))
            self._dismiss_timer.start(effective_ttl)

    def _on_dismiss_timeout(self) -> None:
        self._dismiss_timer.stop()
        self._segment_safety_timer.stop()
        self._pending = ""
        self._clause_pending = ""

        def _finish_dismiss():
            if hasattr(self, "rolling_buffer") and self.rolling_buffer:
                self.rolling_buffer.clear()
            if hasattr(self, "display_buffer") and self.display_buffer:
                self.display_buffer.reset()
            if hasattr(self, "prefix_freezer") and self.prefix_freezer:
                self.prefix_freezer.clear()
            self._pending = ""
            self._clause_pending = ""
            self.source_line = ""
            self.stable_prefix = ""
            self.hypothesis_suffix = ""
            now = time.monotonic()
            if self._line_since and self.line:
                gap = now - self._line_since
                perf.emit(
                    "caption",
                    "dwell",
                    gap=round(gap * 1000.0, 1),
                    lost=len(self.line),
                    shown=0,
                    why="dismiss",
                    waiting=0.0,
                    idle=round(gap * 1000.0, 1),
                )
            self._line_since = 0.0
            self._prev_chars = 0
            self._pending_since = 0.0
            self.line = ""
            self._drawn_text = ""
            if self.text_only:
                self.visible = False
                self.withdraw()
            else:
                self.visible = True
                self.fade_animator.wake_immediate()
                self.update()
                self._pin()
            self._broadcast_state()
            perf.emit("caption", "dismiss", why="timeout")

        if self.text_only:
            if "pytest" in sys.modules or "unittest" in sys.modules or not self.isVisible():
                _finish_dismiss()
            else:
                self.fade_animator.fade_out_silence(
                    duration_ms=320,
                    text_only=True,
                    on_finished=_finish_dismiss,
                )
        else:
            if "pytest" in sys.modules or "unittest" in sys.modules or not self.isVisible():
                _finish_dismiss()
            else:
                self.fade_animator.fade_out_silence(
                    duration_ms=320,
                    text_only=False,
                    on_finished=_finish_dismiss,
                )

    def _broadcast_state(self, force: bool = False) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            val = {
                "textOnly": bool(self.text_only),
                "fontPx": int(self.font_px),
                "fontColor": str(self.font_color),
                "dismissTimeout": int(self.dismiss_ms),
                "visible": bool(self.visible),
            }
            state = {
                "kind": "state",
                "type": "state",
                "value": val,
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

    def activate_existing(self) -> None:
        """Wake up existing caption window to the foreground and unhide if needed."""
        self.closed = False
        self.visible = True
        self.show()
        self.raise_()
        self.activateWindow()
        if ctypes and sys.platform == "win32":
            try:
                ctypes.windll.user32.ShowWindow(self.hwnd(), 4)  # SW_SHOWNOACTIVATE
                ctypes.windll.user32.SetForegroundWindow(self.hwnd())
            except Exception:
                pass
        self._paint()
        if getattr(self, "pet_widget", None):
            self.pet_widget.show()
            self.pet_widget.raise_()
        self.command_received.emit("toast", "程序已在运行中", time.time(), time.time(), False)
        if self._tray_icon and self._tray_icon.isVisible() and self._tray_icon.supportsMessages():
            try:
                self._tray_icon.showMessage(
                    "实时AI翻译",
                    "程序已在后台运行中，已为您唤醒置顶字幕。",
                    QSystemTrayIcon.MessageIcon.Information,
                    2500,
                )
            except Exception:
                pass

    def show(self) -> None:
        super().show()
        self.visible = True
        self.closed = False
        if hasattr(self, "fade_animator") and self.fade_animator:
            self.fade_animator.wake_immediate()
        if self.windowOpacity() < 0.99:
            self.setWindowOpacity(1.0)
        if ctypes and sys.platform == "win32":
            ctypes.windll.user32.ShowWindow(self.hwnd(), 4)  # SW_SHOWNOACTIVATE
        if getattr(self, "pet_widget", None):
            self.pet_widget.sync_caption_visibility(True)
        if getattr(self, "_toggle_visibility_act", None):
            self._toggle_visibility_act.setText("隐藏字幕")
        self._strip_frame()
        self._broadcast_state()
        self._paint()

    def withdraw(self) -> None:
        """Temporarily withdraw window without marking it permanently closed.

        Matches Tkinter root.withdraw() lifecycle: allows subsequent captions
        to seamlessly re-show the overlay.
        """
        self.visible = False
        super().hide()
        if ctypes and sys.platform == "win32":
            ctypes.windll.user32.ShowWindow(self.hwnd(), 0)  # SW_HIDE
        if getattr(self, "pet_widget", None):
            self.pet_widget.sync_caption_visibility(False)
        if getattr(self, "_toggle_visibility_act", None):
            self._toggle_visibility_act.setText("显示字幕")
        self._broadcast_state()

    def hide(self) -> None:
        """Explicitly dismiss window (user close [x] or 'hide' command).

        Marks closed = True until explicit re-arm.
        """
        self.visible = False
        self.closed = True
        if hasattr(self, "rolling_buffer") and self.rolling_buffer:
            self.rolling_buffer.clear()
        now = time.monotonic()
        if self._line_since and self.line:
            gap = now - self._line_since
            perf.emit(
                "caption",
                "dwell",
                gap=round(gap * 1000.0, 1),
                lost=len(self.line),
                shown=0,
                why="hide",
                waiting=0.0,
                idle=round(gap * 1000.0, 1),
            )
        self._line_since = 0.0
        self._prev_chars = 0
        self._pending_since = 0.0
        self.line = ""
        self.status = ""
        self._pending = ""
        self._drawn_text = ""
        self._dismiss_timer.stop()
        self._segment_safety_timer.stop()
        self._hold_timer.stop()
        self._clause_timer.stop()
        super().hide()
        if ctypes and sys.platform == "win32":
            ctypes.windll.user32.ShowWindow(self.hwnd(), 0)  # SW_HIDE
        if getattr(self, "pet_widget", None):
            self.pet_widget.sync_caption_visibility(False)
        if getattr(self, "_toggle_visibility_act", None):
            self._toggle_visibility_act.setText("显示字幕")
        self._broadcast_state()

    def set_status(self, lang: Optional[str] = None, target_lang: Optional[str] = None) -> None:
        if lang == "已暂停":
            self.status = "已暂停"
            self.status_label.setText("⏸ 已暂停")
            self.update()
            return
        if lang is not None:
            self.source_lang = lang
        if target_lang is not None:
            self.target_lang = target_lang

        src = (self.source_lang or "").strip().lower()
        tgt = (self.target_lang or "zh").strip().lower()

        src_name = _LANG.get(src, "") or (src if src and src != "auto" else "")
        tgt_name = _LANG.get(tgt, "") or "中文"

        if not src or src == "auto":
            self.status = f"自动 → {tgt_name}" if tgt_name not in ("中文", "简体中文") else ""
        elif src == tgt or (src.startswith("zh") and tgt.startswith("zh")):
            self.status = src_name
        else:
            self.status = f"{src_name} → {tgt_name}"
        self.status_label.setText(self.status)
        self.update()

    def clear(self) -> None:
        if hasattr(self, "rolling_buffer") and self.rolling_buffer:
            self.rolling_buffer.clear()
        if hasattr(self, "display_buffer") and self.display_buffer:
            self.display_buffer.reset()
        if hasattr(self, "prefix_freezer") and self.prefix_freezer:
            self.prefix_freezer.clear()
        now = time.monotonic()
        if self._line_since and self.line:
            gap = now - self._line_since
            perf.emit(
                "caption",
                "dwell",
                gap=round(gap * 1000.0, 1),
                lost=len(self.line),
                shown=0,
                why="clear",
                waiting=0.0,
                idle=round(gap * 1000.0, 1),
            )
        self._line_since = 0.0
        self._prev_chars = 0
        self._pending_since = 0.0
        self.stable_prefix = ""
        self.hypothesis_suffix = ""
        self.line = ""
        self.source_line = ""
        self._pending = ""
        self._clause_pending = ""
        self._drawn_text = ""
        self._dismiss_timer.stop()
        self._segment_safety_timer.stop()
        self._hold_timer.stop()
        self._clause_timer.stop()
        if self.text_only:
            self.visible = False
            self.withdraw()
        else:
            self.visible = True
            self.update()
        self._broadcast_state()

    def begin_segment(self) -> None:
        self.fade_animator.wake_immediate()
        now = time.monotonic()
        self._dismiss_timer.stop()
        if self.line and self.dismiss_ms > 0:
            char_count = len(self.line.strip())
            safety_ttl = max(int(self.dismiss_ms), 1500 + int(char_count * 220), 8000)
            self._segment_safety_timer.start(safety_ttl)
        if hasattr(self, "prefix_freezer") and self.prefix_freezer:
            self.prefix_freezer.clear()
        self.stable_prefix = ""
        self.hypothesis_suffix = ""
        if self._last_segment_at > 0:
            gap = now - self._last_segment_at
            self._gaps.append(gap)
            if len(self._gaps) > 8:
                self._gaps.pop(0)
        self._last_segment_at = now
        self._segment_seq += 1
        if self._block_by_punct:
            if self._clause_pending and not self._clause_fell_back:
                self._flush_clause()
            else:
                self._cancel_clause()

    def _pace(self) -> float:
        return pace_scale(self._gaps)

    def show_source(self, text: str) -> None:
        if not self.line and text:
            self.line = text
            self._paint()

    def _note_pending(self, pending: str) -> None:
        """Stamp when text first became drawable in the current gap."""
        if pending and not self._pending_since:
            self._pending_since = time.monotonic()

    def _open_wait(self, pending: str) -> None:
        """Start, or move, the window in which this sentence is kept off screen."""
        if not self._defer_t0:
            perf.emit("caption", "defer", chars=len(pending))
            self._wait_segment = self._segment_seq
        self._defer_t0 = time.monotonic() if not self._defer_t0 else self._defer_t0
        self._defer_text = pending
        self._defer_chars = len(pending)

    def _close_wait(self, pending: str, action: str) -> None:
        """Close the wait once nothing is pending any more."""
        if not self._defer_t0 or pending:
            return
        perf.emit(
            "caption",
            "hold",
            wait=round((time.monotonic() - self._defer_t0) * 1000.0, 1),
            action=action,
            chars=self._defer_chars,
            shown=len(self.line),
        )
        self._defer_t0 = 0.0
        self._defer_text = ""
        self._defer_chars = 0

    def _report_dwell(self, now: float) -> None:
        """Emit how long the previous sentence stayed up, and what filled the gap."""
        if not (self._line_since and self._trace_action in ("swap", "final_overwrite")):
            self._pending_since = 0.0
            return
        waited = (now - self._pending_since) if self._pending_since else 0.0
        gap = now - self._line_since
        waited = max(0.0, waited)
        perf.emit(
            "caption",
            "dwell",
            gap=round(gap * 1000.0, 1),
            lost=getattr(self, "_prev_chars", 0),
            shown=len(self.line),
            why="held" if self._pending_since else "empty",
            waiting=round(waited * 1000.0, 1),
            idle=round(max(0.0, gap - waited) * 1000.0, 1),
        )
        self._pending_since = 0.0

    def _paint(self) -> None:
        if not self.line:
            self.update()
            return
        display_lines = update_window_layout_and_geometry(self, self.line)
        now = time.monotonic()
        self._report_dwell(now)
        if self._trace_action in ("grow", "swap", "block", "final_overwrite"):
            self._line_since = now
        elif not self._line_since:
            self._line_since = now
        if self._trace_sent:
            display_body = "\n".join(display_lines) if display_lines else self.line
            sig = _signature(display_body)
            since_send = round(perf.wall_ms(self._trace_sent), 1) if self._trace_sent else 0.0
            since_recv = round(perf.wall_ms(self._trace_recv), 1) if self._trace_recv else -1.0
            perf.emit(
                "caption",
                "paint",
                action=self._trace_action or "paint",
                since_send=since_send,
                since_recv=since_recv,
                chars=len(display_body),
                lines=len(display_lines) if display_lines else 1,
                dropped=0,
                held=1 if getattr(self, "_held_paint", False) else 0,
                sig=sig,
            )
            self._trace_sent = 0.0
            self._trace_recv = 0.0
            self._trace_action = ""

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0:
            self.history_drawer.show_drawer()
            event.accept()
        elif delta < 0:
            self.history_drawer.hide_drawer()
            event.accept()
        else:
            super().wheelEvent(event)

    def paintEvent(self, _event):
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            render_caption_overlay(self, painter)
        except Exception as e:
            print(f"[QtCaptionWindow] Error in paintEvent: {e}")

    def _get_edge_at(self, global_pos: QPoint) -> str:
        return calculate_edge_at(self, global_pos)

    def enterEvent(self, event):
        self._is_hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._is_hovered = False
        if not getattr(self, "_resize_mode", None):
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if not self.resizer.handle_mouse_press(event):
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if not self.resizer.handle_mouse_move(event):
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if not self.resizer.handle_mouse_release(event):
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if not self.resizer.handle_mouse_double_click(event):
            super().mouseDoubleClickEvent(event)

    def _save_layout_config(self):
        self.resizer.save_layout_config()

    def _maximize_width(self):
        self.resizer.maximize_width()

    def _reset_default_width(self):
        self.resizer.reset_default_width()

    def contextMenuEvent(self, event):
        self._show_context_menu(event.globalPos())
        event.accept()


    # State Machine & Timing logic
    def set_text(self, text: str, is_final: bool = False) -> str:
        if self.closed:
            return "stay"
        self.fade_animator.wake_immediate()
        cleaned = _collapse_repeats((text or "").strip())
        if not cleaned:
            return "empty"

        if self._block_by_punct and not self.rolling_two_lines:
            return self._punct_gate(cleaned, is_final)

        if is_final:
            cleaned = self.prefix_freezer.commit_final(cleaned)
            self.stable_prefix = ""
            self.hypothesis_suffix = ""
            self.history_drawer.add_entry(cleaned, getattr(self, "source_line", ""))
            if self.rolling_two_lines:
                self.rolling_buffer.commit(cleaned)
                display_text = self.display_buffer.push_new_sentence(cleaned)
                self.line = display_text
                self._apply_line_diff(self.line, "final_diff")
                return "final"
            if self.line:
                if cleaned != self.line:
                    sim = calculate_text_similarity(self.line, cleaned)
                    if sim >= 0.75:
                        self._apply_line_diff(cleaned, "final_diff")
                        return "final"
                    else:
                        self._apply_line(cleaned, "final_overwrite")
                        return "final"
                else:
                    if not self.visible or not self.isVisible():
                        self.show()
                    if self.line and self.dismiss_ms > 0:
                        self._start_dismiss_timer()
                    return "final"
            else:
                self._apply_line(cleaned, "final_overwrite")
                return "final"

        # Transient contraction protection: Anti-flicker (never retract or shrink on screen during streaming)
        is_completed_line = bool(self.line and self.line[-1] in ("。", "！", "？", ".", "!", "?", "…"))
        is_settled = (time.monotonic() - getattr(self, "_line_since", 0.0)) > 0.8
        if self.line and not is_final and not is_completed_line and not is_settled and self.line.startswith(cleaned):
            if self.line and self.dismiss_ms > 0 and not self._dismiss_timer.isActive():
                self._start_dismiss_timer()
            return "drop"

        # Monotonic Prefix Freezing (Google Live Caption Architecture)
        stable, hypo = self.prefix_freezer.update(cleaned, time.monotonic())
        self.stable_prefix = stable
        self.hypothesis_suffix = hypo
        display_text = self.prefix_freezer.get_display_text()

        if self.rolling_two_lines:
            action, composite = self.rolling_buffer.update_streaming(display_text)
            if action != "drop":
                self.line = composite
                self._apply_line(self.line, action)
            else:
                if self.line and self.dismiss_ms > 0 and not self._dismiss_timer.isActive():
                    self._start_dismiss_timer()
            return action

        if display_text == self.line:
            if self.line and self.dismiss_ms > 0 and not self._dismiss_timer.isActive():
                self._start_dismiss_timer()
            return "drop"

        prev_line = self.line
        if not prev_line or display_text.startswith(prev_line):
            self._apply_line(display_text, "grow")
            return "grow"
        self._apply_line(display_text, "char")
        return "char"

    def _punct_gate(self, cleaned: str, is_final: bool = False) -> str:
        now = time.monotonic()
        if not self._clause_t0:
            self._clause_t0 = now
        if is_final:
            self._cancel_clause()
            if hasattr(self, "prefix_freezer") and self.prefix_freezer:
                cleaned = self.prefix_freezer.commit_final(cleaned)
            self.stable_prefix = ""
            self.hypothesis_suffix = ""
            self.history_drawer.add_entry(cleaned, getattr(self, "source_line", ""))
            if self.rolling_two_lines:
                self.rolling_buffer.commit(cleaned)
                display_text = self.display_buffer.push_new_sentence(cleaned)
                self.line = display_text
                self._apply_line_diff(self.line, "final_diff")
                return "final"
            if self.line:
                if cleaned != self.line:
                    sim = calculate_text_similarity(self.line, cleaned)
                    if sim >= 0.75:
                        # High-similarity diff: preserve visual anchor, smooth tail/punctuation commit
                        self._apply_line_diff(cleaned, "final_diff")
                        return "final"
                    else:
                        # Low-similarity overwrite: clean atomic swap for revised sentence
                        self._apply_line(cleaned, "final_overwrite")
                        return "final"
                else:
                    if not self.visible or not self.isVisible():
                        self.show()
                    if self.line and self.dismiss_ms > 0:
                        self._start_dismiss_timer()
                    return "final"
            else:
                self._apply_line(cleaned, "final_overwrite")
                return "final"
        is_new_sentence = (classify_caption(self.line, cleaned) == "sentence")
        has_boundary = (
            introduces_boundary(self.line, cleaned)
            or (is_new_sentence and bool(punctuation_marks(cleaned)))
        )
        if has_boundary:
            self._cancel_clause()
            target_text = cleaned
            if self.rolling_two_lines and self.line and is_new_sentence:
                self.rolling_buffer.commit(cleaned)
                target_text = self.rolling_buffer.get_composite_text()
            action = "swap" if (bool(self.line) and is_new_sentence) else "block"
            self._apply_line(target_text, action)
            return action
        if is_new_sentence:
            # New sentence starting without boundary mark yet: reset prior clause fallback state!
            self._clause_fell_back = False
            self._clause_t0 = now
        self._clause_pending = cleaned
        if self._clause_fell_back:
            self._apply_line(cleaned, "grow")
            return "grow"
        self._clause_timer.stop()
        wait = max(50, int(round((self._clause_t0 + CLAUSE_MAX_MS / 1000.0 - now) * 1000)))
        self._clause_timer.start(wait)
        return "block_wait"

    def _cancel_clause(self) -> None:
        self._clause_timer.stop()
        self._clause_t0 = 0.0
        self._clause_pending = ""
        self._clause_fell_back = False

    def _flush_clause(self) -> None:
        text = self._clause_pending
        started = self._clause_t0
        self._clause_fell_back = True
        if not text or not started:
            return
        is_new_sentence = (classify_caption(self.line, text) == "sentence")
        action = "swap" if (bool(self.line) and is_new_sentence) else "block"
        self._apply_line(text, action)

    def _swap_deadline(self) -> float:
        return swap_deadline(
            self._growth_stopped_at,
            self._settle_until,
            self.line,
            self._pace(),
            self._next_utterance_at,
        )

    def _arm_hold(self) -> None:
        self._hold_timer.stop()
        if not self._pending:
            return
        wait = int(round((self._swap_deadline() - time.monotonic()) * 1000))
        self._hold_timer.start(max(50, wait))

    def _flush_pending(self) -> None:
        action, line, pending, anchor, settle, _deadline = flush_caption(
            self.line,
            self._pending,
            self._growth_stopped_at,
            self._settle_until,
            time.monotonic(),
            self._pace(),
            self._next_utterance_at,
        )
        self._pending = pending
        self._growth_stopped_at = anchor
        self._settle_until = settle
        self._close_wait(pending, action)
        if action in ("grow", "swap"):
            self._next_utterance_at = 0.0
        if action in ("grow", "char", "swap"):
            self._apply_line(line, action)
        if action == "wait" or self._pending:
            self._arm_hold()

    def _apply_line(self, text: str, action: str = "") -> None:
        self._dismiss_timer.stop()
        self._segment_safety_timer.stop()
        if action:
            self._trace_action = action
        self._prev_chars = len(self.line)
        self.line = (text or "").strip()
        self._growth_stopped_at = time.monotonic()

        act = caption_action(self.closed, self.line, self.visible)
        if act == "withdraw":
            # Sticky Caption / Zero-Blank: Do not unmap/withdraw Win32 window between utterances
            if not self.text_only:
                self.visible = True
                self.update()
            return
        if act == "stay":
            return
        if act == "paint":
            self._paint()
        else:
            self.show()

        if self.line and self.dismiss_ms > 0:
            self._start_dismiss_timer()

    def _apply_line_diff(self, text: str, action: str = "final_diff") -> None:
        """Apply high-similarity diff patch to prevent flicker.

        Preserves existing layout and bounding geometry if height doesn't change,
        avoiding jarring redraws when final frame only adds punctuation or tail fixes.
        """
        self._dismiss_timer.stop()
        self._segment_safety_timer.stop()
        if action:
            self._trace_action = action
        self._prev_chars = len(self.line)
        self.line = (text or "").strip()
        self._growth_stopped_at = time.monotonic()

        act = caption_action(self.closed, self.line, self.visible)
        if act == "withdraw":
            # Sticky Caption / Zero-Blank: Do not unmap/withdraw Win32 window between utterances
            if not self.text_only:
                self.visible = True
                self.update()
            return
        if act == "stay":
            return

        if act == "show" or not self.visible or not self.isVisible():
            self.show()
        else:
            self._paint()

        if self.line and self.dismiss_ms > 0:
            self._start_dismiss_timer()

    def _handle_command(self, kind: str, value, sent_at: float, recv_at: float, final: bool):
        if kind == "text":
            self._trace_sent = sent_at if isinstance(sent_at, (int, float)) else 0.0
            self._trace_recv = recv_at if isinstance(recv_at, (int, float)) else 0.0
            self._trace_action = ""
            action = self.set_text(str(value or ""), is_final=final)
            if action in ("empty", "drop"):
                self._trace_sent = 0.0
                self._trace_recv = 0.0
                self._trace_action = ""
            else:
                self._trace_action = action
            perf.emit(
                "caption",
                "text",
                action=action,
                net=round(perf.wall_ms(sent_at), 1) if sent_at else -1.0,
                pump=round(perf.wall_ms(recv_at), 1) if recv_at else -1.0,
                depth=0,
                final=1 if final else 0,
                chars=len(str(value or "")),
            )
        elif kind == "note":
            self._trace_sent = sent_at if isinstance(sent_at, (int, float)) else 0.0
            self._trace_recv = recv_at if isinstance(recv_at, (int, float)) else 0.0
            self._trace_action = "note"
            self.show_source(str(value or ""))
        elif kind == "status":
            self.set_status(str(value or ""))
        elif kind == "clear":
            self.clear()
        elif kind == "hide":
            self.hide()
        elif kind == "arm":
            self.arm()
        elif kind in ("style", "text_only"):
            self.apply_text_only(bool(value), from_source="external")
        elif kind in ("font", "font_size"):
            self.apply_font(int(value))
        elif kind in ("color", "font_color"):
            self.apply_color(str(value))
        elif kind in ("dismiss", "dismiss_timeout"):
            self.apply_dismiss_timeout(int(value))
        elif kind == "source":
            self.source_line = str(value or "")
            self.show_source(str(value))
        elif kind == "segment":
            self.begin_segment()
        elif kind in ("state?", "ask_state"):
            self._broadcast_state(force=True)
        elif kind in ("toast", "notify"):
            self.status_label.setText(str(value or ""))
            self.update()
        elif kind == "raise":
            self.activate_existing()
        elif kind in ("pulse", "audio_active"):
            if getattr(self, "pet_widget", None):
                self.pet_widget.sync_audio_activity(bool(value))



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
        print(f"Caption port {CAPTION_PORT} already in use.", flush=True)
        return

    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    window = QtCaptionWindow()
    # Window starts withdrawn/hidden, exactly matching Tkinter root.withdraw()
    print("caption window ready", flush=True)

    listener_thread = threading.Thread(target=_qt_udp_listener, args=(window, sock), daemon=True)
    listener_thread.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
