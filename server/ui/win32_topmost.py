"""Win32 native window management, 64-bit topmost pinning, and style constants."""

from __future__ import annotations

import sys

try:
    import ctypes
    if sys.platform == "win32":
        from ctypes import wintypes
        _user32 = ctypes.windll.user32
        _user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        _user32.SetWindowPos.restype = wintypes.BOOL
        HWND_TOPMOST = wintypes.HWND(-1)
        HWND_NOTOPMOST = wintypes.HWND(-2)
    else:
        _user32 = None
        HWND_TOPMOST = -1
        HWND_NOTOPMOST = -2
except (ImportError, AttributeError):
    ctypes = None
    _user32 = None
    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040


def pin_window_topmost(hwnd: int) -> bool:
    """Pin the specified HWND topmost without activating or resizing."""
    if not ctypes or sys.platform != "win32" or not hwnd:
        return False
    try:
        topmost = HWND_TOPMOST if isinstance(HWND_TOPMOST, ctypes.c_void_p) else -1
        return bool(_user32.SetWindowPos(
            hwnd,
            topmost,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        ))
    except Exception:
        return False


def unpin_window_topmost(hwnd: int) -> bool:
    """Demote the specified HWND from topmost to normal Z-order (HWND_NOTOPMOST)."""
    if not ctypes or sys.platform != "win32" or not hwnd:
        return False
    try:
        notopmost = HWND_NOTOPMOST if isinstance(HWND_NOTOPMOST, ctypes.c_void_p) else -2
        return bool(_user32.SetWindowPos(
            hwnd,
            notopmost,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        ))
    except Exception:
        return False



def strip_window_frame(hwnd: int, is_visible: bool = False) -> bool:
    """Strip native window frame and re-assert topmost ordering."""
    if not ctypes or sys.platform != "win32" or not hwnd:
        return False
    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
    if is_visible:
        flags |= SWP_SHOWWINDOW
    try:
        topmost = HWND_TOPMOST if isinstance(HWND_TOPMOST, ctypes.c_void_p) else -1
        return bool(_user32.SetWindowPos(hwnd, topmost, 0, 0, 0, 0, flags))
    except Exception:
        return False
