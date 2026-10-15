"""UI package: Subtitle overlay, pet widget coordination, window resizing, tray manager, and topmost pinning."""

from __future__ import annotations

from .caption_window import QtCaptionWindow
from .caption_renderer import (
    NO_LINE_END,
    NO_LINE_START,
    compute_bottom_pinned_baselines,
    render_caption_text,
    wrap_all_typography,
    wrap_caption_typography,
)
from .caption_menu import CaptionMenuBuilder
from .caption_animator import CaptionFadeAnimator
from .history_drawer import HistoryDrawerWidget
from .punct_gate import (
    DIFF_SIMILARITY_THRESHOLD,
    CaptionDisplayBuffer,
    ChromiumRollingBuffer,
    calculate_text_similarity,
    decide_final_action,
    get_lcp_length,
    resolve_streaming_monotonic,
)
from .splash_screen import SplashScreen
from .tray_manager import (
    TRAY_MENU_STYLE,
    create_tray_icon,
    find_app_icon_path,
    show_tray_menu_native,
)
from .win32_topmost import (
    GWL_EXSTYLE,
    HWND_NOTOPMOST,
    HWND_TOPMOST,
    SWP_NOACTIVATE,
    SWP_NOMOVE,
    SWP_NOSIZE,
    SWP_SHOWWINDOW,
    WS_EX_NOACTIVATE,
    WS_EX_TOOLWINDOW,
    WS_EX_TOPMOST,
    pin_window_topmost,
    strip_window_frame,
    unpin_window_topmost,
)
from .window_resizer import _HeaderWidget, calculate_edge_at

__all__ = [
    "QtCaptionWindow",
    "compute_bottom_pinned_baselines",
    "wrap_all_typography",
    "wrap_caption_typography",
    "render_caption_text",
    "NO_LINE_START",
    "NO_LINE_END",
    "CaptionMenuBuilder",
    "CaptionFadeAnimator",
    "HistoryDrawerWidget",
    "calculate_text_similarity",
    "decide_final_action",
    "DIFF_SIMILARITY_THRESHOLD",
    "get_lcp_length",
    "resolve_streaming_monotonic",
    "ChromiumRollingBuffer",
    "_HeaderWidget",
    "calculate_edge_at",
    "pin_window_topmost",
    "unpin_window_topmost",
    "strip_window_frame",
    "HWND_TOPMOST",
    "HWND_NOTOPMOST",
    "GWL_EXSTYLE",
    "WS_EX_TOOLWINDOW",
    "WS_EX_TOPMOST",
    "WS_EX_NOACTIVATE",
    "SWP_NOSIZE",
    "SWP_NOMOVE",
    "SWP_NOACTIVATE",
    "SWP_SHOWWINDOW",
    "find_app_icon_path",
    "create_tray_icon",
    "show_tray_menu_native",
    "TRAY_MENU_STYLE",
    "SplashScreen",
]
