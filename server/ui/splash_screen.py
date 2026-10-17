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
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

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
    def prompt_missing_models(self, title: str = "未检测到本地大模型目录", tried_candidates: list[Path] | None = None) -> Optional[Path]:
        """Display an interactive dialog when models are missing or unmounted,
        allowing the user to manually select the models directory.
        """
        from server import config

        self.set_progress(0.15, "⚠️ 未检测到模型目录，等待用户指定...")
        app = QApplication.instance()
        if app:
            app.processEvents()

        dialog = QDialog(self)
        dialog.setWindowTitle("未检测到大模型存放目录")
        dialog.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        dialog.setFixedSize(500, 240)
        # Center over splash screen
        dialog.move(
            self.x() + (self.width() - dialog.width()) // 2,
            self.y() + (self.height() - dialog.height()) // 2
        )

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(0)

        # Container card with dark fluent styling
        card = QWidget(dialog)
        card.setStyleSheet("""
            QWidget {
                background-color: #0b111e;
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 12px;
            }
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(22, 18, 22, 18)
        card_layout.setSpacing(12)

        title_lbl = QLabel(f"⚠️ {title}", card)
        title_lbl.setStyleSheet("color: #f8fafc; font-size: 15px; font-weight: bold; border: none;")
        card_layout.addWidget(title_lbl)

        desc_text = (
            "系统未能在默认位置检测到模型文件（可能未挂载数据盘或路径不同）。\n"
            "若您的模型存放在外部硬盘、/mnt/DATA、/media/... 或自定义目录，\n"
            "请点击下方按钮选择存放文件夹 (包含 Qwen-ASR / Index-Translate)："
        )
        desc_lbl = QLabel(desc_text, card)
        desc_lbl.setStyleSheet("color: #94a3b8; font-size: 12px; line-height: 1.5; border: none;")
        desc_lbl.setWordWrap(True)
        card_layout.addWidget(desc_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        btn_browse = QPushButton("📁 手动选择模型目录...", card)
        btn_browse.setStyleSheet("""
            QPushButton {
                background-color: #0284c7;
                color: #ffffff;
                font-size: 12px;
                font-weight: bold;
                border: none;
                border-radius: 6px;
                padding: 7px 16px;
            }
            QPushButton:hover {
                background-color: #0369a1;
            }
        """)

        btn_rescan = QPushButton("🔄 重新扫描", card)
        btn_rescan.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                color: #cbd5e1;
                font-size: 12px;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 6px;
                padding: 7px 14px;
            }
            QPushButton:hover {
                background-color: #334155;
                color: #ffffff;
            }
        """)

        btn_exit = QPushButton("退出", card)
        btn_exit.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #64748b;
                font-size: 12px;
                border: none;
                border-radius: 6px;
                padding: 7px 12px;
            }
            QPushButton:hover {
                color: #ef4444;
            }
        """)

        btn_row.addWidget(btn_browse)
        btn_row.addWidget(btn_rescan)
        btn_row.addStretch()
        btn_row.addWidget(btn_exit)
        card_layout.addLayout(btn_row)

        layout.addWidget(card)

        selected_dir: list[Optional[Path]] = [None]

        def on_browse():
            from server.config import normalize_models_dir, set_models_dir
            start_dir = None
            if config.MODELS_DIR and config.MODELS_DIR.exists():
                start_dir = str(config.MODELS_DIR)
            elif sys.platform != "win32":
                for m in ("/mnt/d/TranslationDesktop/models", "/mnt/d/models", "/mnt/d", "/media", "/DATA", "/mnt"):
                    if Path(m).exists():
                        start_dir = m
                        break
            elif Path("D:/TranslationDesktop/models").exists():
                start_dir = "D:/TranslationDesktop/models"
            elif Path("D:/").exists():
                start_dir = "D:/"
            if not start_dir:
                start_dir = str(Path.home())

            folder = QFileDialog.getExistingDirectory(
                dialog,
                "请选择模型存放文件夹 (支持包含 Index-Translate / Qwen-ASR / Nemotron 的根目录或直接包含模型的文件夹)",
                start_dir,
            )
            if folder:
                p = Path(folder).resolve()
                norm_p = normalize_models_dir(p)
                set_models_dir(norm_p)
                selected_dir[0] = norm_p
                dialog.accept()

        def on_rescan():
            from server.config import _has_valid_model_suite, set_models_dir
            p = config.resolve_models_dir()
            if _has_valid_model_suite(p):
                set_models_dir(p)
                selected_dir[0] = p
                dialog.accept()
            else:
                title_lbl.setText("⚠️ 重新扫描仍未发现有效模型文件，请手动选择目录")

        def on_exit():
            dialog.reject()
            sys.exit(0)

        btn_browse.clicked.connect(on_browse)
        btn_rescan.clicked.connect(on_rescan)
        btn_exit.clicked.connect(on_exit)

        dialog.exec()

        if selected_dir[0]:
            p = selected_dir[0]
            config.set_models_dir(p)
            self.set_progress(0.20, f"已绑定模型目录: {p.name}，正在装载...")
            return p
        return None

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
