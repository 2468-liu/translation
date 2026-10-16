"""Facade module for QtCaptionWindow backward compatibility.

All core UI implementations have been structured into the `server.ui` package:
- `server.ui.caption_window`: QtCaptionWindow QWidget implementation
- `server.ui.punct_gate`: calculate_text_similarity and diff decision logic
- `server.ui.window_resizer`: _HeaderWidget and edge drag detection
- `server.ui.win32_topmost`: Win32 HWND_TOPMOST 64-bit pinning and frame styling
- `server.ui.tray_manager`: System tray icons, menus, and process enumeration
"""

from __future__ import annotations

from server.ui import (
    GWL_EXSTYLE,
    HWND_TOPMOST,
    SWP_NOACTIVATE,
    SWP_NOMOVE,
    SWP_NOSIZE,
    SWP_SHOWWINDOW,
    WS_EX_NOACTIVATE,
    WS_EX_TOOLWINDOW,
    WS_EX_TOPMOST,
    QtCaptionWindow,
    _HeaderWidget,
    calculate_edge_at,
    calculate_text_similarity,
    create_tray_icon as _create_tray_icon,
    decide_final_action,
    find_app_icon_path as _find_app_icon_path,
    pin_window_topmost,
    show_tray_menu_native,
    strip_window_frame,
)

__all__ = [
    "QtCaptionWindow",
    "_HeaderWidget",
    "calculate_text_similarity",
    "decide_final_action",
    "calculate_edge_at",
    "pin_window_topmost",
    "strip_window_frame",
    "HWND_TOPMOST",
    "GWL_EXSTYLE",
    "WS_EX_TOOLWINDOW",
    "WS_EX_TOPMOST",
    "WS_EX_NOACTIVATE",
    "SWP_NOSIZE",
    "SWP_NOMOVE",
    "SWP_NOACTIVATE",
    "SWP_SHOWWINDOW",
    "_find_app_icon_path",
    "_create_tray_icon",
    "show_tray_menu_native",
]

if __name__ == "__main__":
    from server.ui.caption_window import main
    main()
