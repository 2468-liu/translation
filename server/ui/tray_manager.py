"""System tray icon, menus, styling, and session enumeration."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Optional

from PyQt6.QtCore import QPoint, QRectF, Qt
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QIcon,
    QPainter,
    QPixmap,
)
from PyQt6.QtWidgets import QMenu, QWidget

from .. import config

try:
    import ctypes
except ImportError:
    ctypes = None

from ..translate import _LANG_NAME

_LANG = _LANG_NAME

TRAY_MENU_STYLE = """
    QMenu {
        background-color: #1e1e24;
        color: #f3f4f6;
        border: 1px solid rgba(255, 255, 255, 0.15);
        border-radius: 8px;
        padding: 4px;
        margin: 0px;
        font-size: 13px;
    }
    QMenu::item {
        padding: 6px 24px 6px 20px;
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
"""


def find_app_icon_path() -> Path | None:
    candidates = [
        config.ROOT / "assets" / "icon.png",
        config.ROOT / "assets" / "icon.ico",
        Path.cwd() / "assets" / "icon.png",
        Path.cwd() / "assets" / "icon.ico",
        Path("D:/TranslationDesktop/assets/icon.png"),
        Path("D:/TranslationDesktop/assets/icon.ico"),
    ]
    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).resolve().parent
        candidates.insert(0, app_dir / "assets" / "icon.png")
        candidates.insert(1, app_dir / "assets" / "icon.ico")
        candidates.insert(2, app_dir / "_internal" / "assets" / "icon.png")
    for p in candidates:
        if p.exists():
            return p
    return None


def create_tray_icon() -> QIcon:
    p = find_app_icon_path()
    if p:
        return QIcon(str(p))
    pix = QPixmap(32, 32)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(QColor(59, 130, 246))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRectF(2, 2, 28, 28), 6.0, 6.0)
    font = QFont()
    font.setFamilies(["Microsoft YaHei", "Noto Sans CJK SC", "Source Han Sans CN", "Segoe UI", "sans-serif"])
    font.setPointSize(14)
    font.setWeight(QFont.Weight.Bold)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality)
    painter.setFont(font)
    painter.drawText(QRectF(0, 0, 32, 32), Qt.AlignmentFlag.AlignCenter, "译")
    painter.end()
    return QIcon(pix)


def show_tray_menu_native(menu: QMenu, window_id: int = 0, pos: Optional[QPoint] = None) -> None:
    """Show tray menu using Qt's popup modal tracking loop.

    Avoids calling SetForegroundWindow on application desktop windows so that the
    Windows 11 notification overflow drawer (NotifyIconOverflowWindow) remains open
    and properly anchored while the user browses tray options.
    """
    if pos is None:
        pos = QCursor.pos()

    try:
        menu.exec(pos)
    except Exception:
        pass

