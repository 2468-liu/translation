"""Modern sleek splash screen for application startup loading transition."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Optional

from PyQt6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
)
from PyQt6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QApplication, QWidget

from .tray_manager import find_app_icon_path


def _make_splash_font(size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont()
    if sys.platform == "win32":
        font.setFamilies(["Microsoft YaHei", "PingFang SC", "Segoe UI", "sans-serif"])
    else:
        font.setFamilies([
            "Noto Sans CJK SC",
            "Source Han Sans CN",
            "WenQuanYi Micro Hei",
            "Ubuntu Sans",
            "Ubuntu",
            "sans-serif",
        ])
    font.setPointSize(size)
    font.setWeight(weight)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality)
    return font


class SplashScreen(QWidget):
    """Sleek dark fluent splash transition screen displayed immediately upon app launch."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedSize(460, 226)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.SplashScreen
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)

        self._progress: float = 0.05
        self._status_text: str = "正在准备运行环境与硬件加速..."
        self._shimmer_phase: float = 0.0

        # Shimmer wave animation timer
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._on_anim_tick)
        self._anim_timer.start(40)

        self._fade_anim: Optional[QPropertyAnimation] = None

        self._load_icon_pixmap()
        self._center_on_screen()

    def _load_icon_pixmap(self):
        icon_path = find_app_icon_path()
        if icon_path and Path(icon_path).exists():
            try:
                pix = QPixmap(str(icon_path))
                if not pix.isNull():
                    self._icon_pixmap = pix.scaled(
                        52, 52,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    return
            except Exception:
                pass
        self._icon_pixmap = None

    def _center_on_screen(self):
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            x = geom.x() + (geom.width() - self.width()) // 2
            y = geom.y() + (geom.height() - self.height()) // 2
            self.move(x, y)

    def _on_anim_tick(self):
        self._shimmer_phase = (self._shimmer_phase + 0.035) % 1.0
        self.update()

    def set_progress(self, progress: float, status_text: str = ""):
        """Update progress bar (0.0 to 1.0) and status description."""
        self._progress = max(0.02, min(1.0, float(progress)))
        if status_text:
            self._status_text = str(status_text)
        self.update()
        app = QApplication.instance()
        if app:
            app.processEvents()

    def set_status(self, status_text: str):
        """Update status description only."""
        self._status_text = str(status_text)
        self.update()
        app = QApplication.instance()
        if app:
            app.processEvents()

    def finish_and_close(self, target_window: Optional[QWidget] = None, duration_ms: int = 320):
        """Fade out smoothly and close cleanly."""
        if target_window is not None:
            try:
                target_window.show()
                target_window.raise_()
            except Exception:
                pass

        if self._anim_timer.isActive():
            self._anim_timer.stop()

        if self._fade_anim is None:
            self._fade_anim = QPropertyAnimation(self, b"windowOpacity")
            self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._fade_anim.stop()
        self._fade_anim.setDuration(duration_ms)
        self._fade_anim.setStartValue(self.windowOpacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.finished.connect(self.close)
        self._fade_anim.start()
        app = QApplication.instance()
        if app:
            app.processEvents()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        w, h = float(self.width()), float(self.height())
        rect = QRectF(0, 0, w, h)

        # 1. Background Rounded Card
        card_path = QPainterPath()
        card_path.addRoundedRect(rect, 14.0, 14.0)

        bg_grad = QLinearGradient(0, 0, 0, h)
        bg_grad.setColorAt(0.0, QColor(22, 31, 48, 250))
        bg_grad.setColorAt(1.0, QColor(11, 17, 30, 252))
        painter.fillPath(card_path, bg_grad)

        # 1.1 Outer Subtle Border
        border_pen = QPen(QColor(255, 255, 255, 28), 1.2)
        painter.strokePath(card_path, border_pen)

        # 1.2 Top Highlight Accent Line
        accent_pen = QPen(QColor(56, 189, 248, 70), 1.5)
        top_accent_path = QPainterPath()
        top_accent_path.moveTo(18, 1)
        top_accent_path.lineTo(w - 18, 1)
        painter.strokePath(top_accent_path, accent_pen)

        # 2. App Icon
        icon_rect = QRectF(28, 28, 52, 52)
        if self._icon_pixmap:
            icon_clip = QPainterPath()
            icon_clip.addRoundedRect(icon_rect, 10.0, 10.0)
            painter.save()
            painter.setClipPath(icon_clip)
            painter.drawPixmap(icon_rect.toRect(), self._icon_pixmap)
            painter.restore()
            painter.strokePath(icon_clip, QPen(QColor(255, 255, 255, 40), 1.0))
        else:
            logo_path = QPainterPath()
            logo_path.addRoundedRect(icon_rect, 10.0, 10.0)
            logo_grad = QLinearGradient(icon_rect.topLeft(), icon_rect.bottomRight())
            logo_grad.setColorAt(0.0, QColor(37, 99, 235))
            logo_grad.setColorAt(1.0, QColor(59, 130, 246))
            painter.fillPath(logo_path, logo_grad)
            painter.setPen(QColor(255, 255, 255))
            font_logo = _make_splash_font(20, QFont.Weight.Bold)
            painter.setFont(font_logo)
            painter.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, "译")

        # 3. Titles
        painter.setPen(QColor(248, 250, 252))
        title_font = _make_splash_font(15, QFont.Weight.Bold)
        painter.setFont(title_font)
        painter.drawText(QRectF(94, 28, 330, 28), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "AI 实时双语字幕")

        painter.setPen(QColor(148, 163, 184))
        sub_font = _make_splash_font(9, QFont.Weight.Normal)
        painter.setFont(sub_font)
        painter.drawText(
            QRectF(94, 58, 330, 22),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            "离线流式同传 · 影视级降噪 (Qwen3 / Nemotron + Index-Translate)",
        )

        # 4. Status Text & Percentage
        painter.setPen(QColor(56, 189, 248))
        status_font = _make_splash_font(10, QFont.Weight.Medium)
        painter.setFont(status_font)
        painter.drawText(
            QRectF(28, 114, 340, 22),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._status_text,
        )

        pct_text = f"{int(self._progress * 100)}%"
        painter.setPen(QColor(100, 116, 139))
        pct_font = _make_splash_font(10, QFont.Weight.Bold)
        painter.setFont(pct_font)
        painter.drawText(
            QRectF(370, 114, 62, 22),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            pct_text,
        )

        # 5. Progress Bar
        bar_bg_rect = QRectF(28, 142, 404, 7)
        bar_bg_path = QPainterPath()
        bar_bg_path.addRoundedRect(bar_bg_rect, 3.5, 3.5)
        painter.fillPath(bar_bg_path, QColor(30, 41, 59, 200))

        fill_w = max(6.0, 404.0 * self._progress)
        bar_fill_rect = QRectF(28, 142, fill_w, 7)
        bar_fill_path = QPainterPath()
        bar_fill_path.addRoundedRect(bar_fill_rect, 3.5, 3.5)

        fill_grad = QLinearGradient(28, 142, 28 + fill_w, 142)
        fill_grad.setColorAt(0.0, QColor(2, 132, 199))
        fill_grad.setColorAt(0.7, QColor(56, 189, 248))
        fill_grad.setColorAt(1.0, QColor(129, 140, 248))
        painter.fillPath(bar_fill_path, fill_grad)

        # Shimmer highlight wave
        shimmer_x = 28 + fill_w * self._shimmer_phase
        if shimmer_x <= 28 + fill_w:
            shimmer_grad = QLinearGradient(shimmer_x - 30, 142, shimmer_x + 30, 142)
            shimmer_grad.setColorAt(0.0, QColor(255, 255, 255, 0))
            shimmer_grad.setColorAt(0.5, QColor(255, 255, 255, 90))
            shimmer_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.save()
            painter.setClipPath(bar_fill_path)
            painter.fillRect(QRectF(shimmer_x - 30, 142, 60, 7), shimmer_grad)
            painter.restore()

        # 6. Bottom Helper Footer
        painter.setPen(QColor(71, 85, 105))
        footer_font = _make_splash_font(8, QFont.Weight.Normal)
        painter.setFont(footer_font)
        painter.drawText(
            QRectF(28, 172, 404, 22),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            "⚡ 启动中... 正在为您检测并调度本地模型引擎与音频回环",
        )
