"""Floating drawer widget for peering back at recent dialogue history."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
)
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class HistoryDrawerWidget(QWidget):
    """Semi-transparent floating drawer that peeks recent dialogue history."""

    def __init__(self, parent_caption_window: QWidget):
        super().__init__()
        self.caption_win = parent_caption_window
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._history: list[dict[str, str]] = []
        self._max_items = 30
        self._fade_anim: Optional[QPropertyAnimation] = None

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide_drawer)

        self._init_ui()

    def _init_ui(self):
        self.setFixedSize(540, 320)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(8)

        # 1. Header Bar
        header = QWidget(self)
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(4, 0, 4, 0)

        title_lbl = QLabel("📜 历史对白记录 (最近30条)", header)
        title_lbl.setStyleSheet("color: #38bdf8; font-weight: bold; font-size: 13px;")

        close_btn = QPushButton("✕", header)
        close_btn.setFixedSize(22, 22)
        close_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.1);
                color: #cbd5e1;
                border: none;
                border-radius: 11px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background: #ef4444;
                color: #ffffff;
            }
        """)
        close_btn.clicked.connect(self.hide_drawer)

        h_layout.addWidget(title_lbl)
        h_layout.addStretch()
        h_layout.addWidget(close_btn)
        main_layout.addWidget(header)

        # 2. Scroll Area for Dialogs
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background: rgba(255, 255, 255, 0.05);
                width: 6px;
                border-radius: 3px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.25);
                border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(56, 189, 248, 0.6);
            }
        """)

        self.list_container = QWidget()
        self.list_container.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(4, 4, 4, 4)
        self.list_layout.setSpacing(10)
        self.list_layout.addStretch()

        self.scroll_area.setWidget(self.list_container)
        main_layout.addWidget(self.scroll_area)

    def add_entry(self, target_text: str, source_text: str = ""):
        """Records a completed sentence into history buffer."""
        cleaned_tgt = str(target_text or "").strip()
        cleaned_src = str(source_text or "").strip()
        if not cleaned_tgt:
            return

        ts = time.strftime("%H:%M:%S")
        entry = {
            "time": ts,
            "target": cleaned_tgt,
            "source": cleaned_src,
        }
        self._history.append(entry)
        if len(self._history) > self._max_items:
            self._history.pop(0)

        # Refresh UI card item
        item_card = QWidget(self.list_container)
        item_card.setStyleSheet("""
            QWidget {
                background-color: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 6px;
                padding: 6px;
            }
        """)
        c_layout = QVBoxLayout(item_card)
        c_layout.setContentsMargins(6, 6, 6, 6)
        c_layout.setSpacing(4)

        time_lbl = QLabel(f"⏱ {ts}", item_card)
        time_lbl.setStyleSheet("color: #64748b; font-size: 11px; font-weight: bold; border: none;")
        c_layout.addWidget(time_lbl)

        tgt_lbl = QLabel(cleaned_tgt, item_card)
        tgt_lbl.setWordWrap(True)
        tgt_lbl.setStyleSheet("color: #f8fafc; font-size: 13px; font-weight: 500; border: none;")
        c_layout.addWidget(tgt_lbl)

        if cleaned_src:
            src_lbl = QLabel(cleaned_src, item_card)
            src_lbl.setWordWrap(True)
            src_lbl.setStyleSheet("color: #94a3b8; font-size: 11px; font-style: italic; border: none;")
            c_layout.addWidget(src_lbl)

        # Insert before stretch item at end
        count = self.list_layout.count()
        self.list_layout.insertWidget(max(0, count - 1), item_card)

        # Prune old Qt widget cards from layout to prevent UI memory leaks
        while self.list_layout.count() > self._max_items + 1:
            old_item = self.list_layout.takeAt(0)
            if old_item:
                old_w = old_item.widget()
                if old_w:
                    old_w.deleteLater()

        # Auto scroll to bottom
        QTimer.singleShot(20, lambda: self.scroll_area.verticalScrollBar().setValue(
            self.scroll_area.verticalScrollBar().maximum()
        ))

    def show_drawer(self):
        """Anchors drawer cleanly above the caption window."""
        if not self.caption_win:
            return

        cap_geo = self.caption_win.geometry()
        x = cap_geo.x() + (cap_geo.width() - self.width()) // 2
        y = cap_geo.y() - self.height() - 8
        if y < 10:
            # If off-screen top, dock below caption
            y = cap_geo.y() + cap_geo.height() + 8

        self.move(x, y)
        self.setWindowOpacity(1.0)
        self.show()
        self.raise_()
        self._hide_timer.start(8000)

    def hide_drawer(self):
        if self._fade_anim is None:
            self._fade_anim = QPropertyAnimation(self, b"windowOpacity")
            self._fade_anim.setDuration(220)
            self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._fade_anim.finished.connect(self.hide)

        self._fade_anim.stop()
        self._fade_anim.setStartValue(self.windowOpacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.start()

    def enterEvent(self, event):
        self._hide_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hide_timer.start(2500)
        super().leaveEvent(event)

    def paintEvent(self, event):
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = self.rect()

            path = QPainterPath()
            path.addRoundedRect(QRectF(rect.adjusted(1, 1, -1, -1)), 12.0, 12.0)

            # Dark sleek frosted acrylic background
            painter.fillPath(path, QColor(18, 24, 38, 242))
            painter.strokePath(path, QPen(QColor(255, 255, 255, 26), 1.2))
        except Exception as e:
            print(f"[HistoryDrawer] paintEvent error: {e}")
