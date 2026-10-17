"""Interactive Model Download Dialog with Embedded Console Terminal and Auto-Refresh.

Provides visual real-time terminal output, stream progress tracking, and hot-switch
signals when models or engine components are being provisioned (e.g. NVIDIA Nemotron 3.5 ASR).
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QProcess, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from server import config


class ModelDownloadDialog(QDialog):
    """Modern dark-themed interactive model download modal with real-time terminal log."""

    download_finished = pyqtSignal(bool, str)  # (success, model_name)

    def __init__(self, parent: Optional[QWidget] = None, target_model: str = "nemotron"):
        super().__init__(parent)
        self.target_model = target_model
        self.proc: Optional[QProcess] = None
        self._is_success = False

        self.setWindowTitle("模型组件一键下载与配置")
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(580, 380)

        # Center on screen or parent
        if parent:
            geo = parent.geometry()
            self.move(
                geo.x() + (geo.width() - self.width()) // 2,
                geo.y() + (geo.height() - self.height()) // 2,
            )
        else:
            screen = QApplication.primaryScreen()
            if screen:
                geom = screen.availableGeometry()
                self.move(
                    geom.x() + (geom.width() - self.width()) // 2,
                    geom.y() + (geom.height() - self.height()) // 2,
                )

        self._init_ui()

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(0)

        # Card container with dark fluent aesthetic
        card = QWidget(self)
        card.setStyleSheet("""
            QWidget {
                background-color: #0b111e;
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 12px;
            }
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 16)
        card_layout.setSpacing(10)

        has_model = False
        try:
            from server.asr.nemotron import _resolve_nemotron_model_path
            _resolve_nemotron_model_path()
            has_model = True
        except Exception:
            has_model = False

        # Header Title
        title_box = QHBoxLayout()
        header_text = "🚀 原生推理引擎自动配置 (Nemotron 3.5 ASR)" if has_model else "🚀 极速模型下载与自动配置 (Nemotron 3.5 ASR)"
        title_lbl = QLabel(header_text, card)
        title_lbl.setStyleSheet("color: #f8fafc; font-size: 14px; font-weight: bold; border: none;")
        title_box.addWidget(title_lbl)
        card_layout.addLayout(title_box)

        # Subtitle description
        if has_model:
            desc_text = (
                "✅ 已检测到挂载/本地的 Nemotron 3.5 GGUF 权重模型！\n"
                f"跳过 700MB 下载，仅需下载 {'Windows' if sys.platform == 'win32' else 'Linux'} 原生极速推理引擎 (约 16MB)，下载后自动热生效。"
            )
        else:
            desc_text = (
                "正在拉取 NVIDIA 官方 Nemotron 3.5 GGUF 权重与原生推理引擎动态库 (约 700MB)\n"
                "下载完成后将自动热刷新并无缝切换至极速同传模式，无需重启客户端。"
            )
        desc_lbl = QLabel(desc_text, card)
        desc_lbl.setStyleSheet("color: #94a3b8; font-size: 11px; line-height: 1.4; border: none;")
        desc_lbl.setWordWrap(True)
        card_layout.addWidget(desc_lbl)

        # Progress bar
        self.progress_bar = QProgressBar(card)
        self.progress_bar.setFixedHeight(14)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #1e293b;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 7px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284c7, stop:1 #38bdf8);
                border-radius: 6px;
            }
        """)
        card_layout.addWidget(self.progress_bar)

        # Status text (speed, percent, filename)
        self.status_lbl = QLabel("正在初始化下载连接与多镜像探测...", card)
        self.status_lbl.setStyleSheet("color: #38bdf8; font-size: 11px; border: none;")
        card_layout.addWidget(self.status_lbl)

        # Embedded Console Terminal
        self.terminal = QPlainTextEdit(card)
        self.terminal.setReadOnly(True)
        self.terminal.setMaximumBlockCount(1000)
        font = QFont()
        font.setFamilies(["Consolas", "Ubuntu Mono", "DejaVu Sans Mono", "Courier New", "monospace"])
        font.setPixelSize(11)
        self.terminal.setFont(font)
        self.terminal.setStyleSheet("""
            QPlainTextEdit {
                background-color: #050811;
                color: #a7f3d0;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 6px;
                padding: 6px;
                font-family: Consolas, "Ubuntu Mono", monospace;
            }
        """)
        card_layout.addWidget(self.terminal, stretch=1)

        # Buttons row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.btn_hide = QPushButton("🔕 隐藏到后台继续下载", card)
        self.btn_hide.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                color: #cbd5e1;
                font-size: 11px;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                padding: 6px 14px;
            }
            QPushButton:hover {
                background-color: #334155;
                color: #ffffff;
            }
        """)
        self.btn_hide.clicked.connect(self.hide)
        btn_row.addWidget(self.btn_hide)

        btn_row.addStretch()

        self.btn_action = QPushButton("取消下载", card)
        self.btn_action.setStyleSheet("""
            QPushButton {
                background-color: #ef4444;
                color: #ffffff;
                font-size: 11px;
                font-weight: bold;
                border: none;
                border-radius: 6px;
                padding: 6px 16px;
            }
            QPushButton:hover {
                background-color: #dc2626;
            }
        """)
        self.btn_action.clicked.connect(self._on_action_clicked)
        btn_row.addWidget(self.btn_action)

        card_layout.addLayout(btn_row)
        root_layout.addWidget(card)

    def start_download(self) -> None:
        """Start the background download process via QProcess."""
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            return

        script_py = config.ROOT / "scripts" / "setup_nemotron.py"
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyRead.connect(self._on_ready_read)
        self.proc.finished.connect(self._on_finished)

        args = [str(script_py), "--auto", "--models-dir", str(config.MODELS_DIR)]
        self.append_log(f"$ {sys.executable} {' '.join(args)}")
        self.proc.start(sys.executable, args)
        self.show()
        self.raise_()

    def append_log(self, text: str) -> None:
        """Append log line to embedded terminal and scroll to bottom."""
        clean_text = text.rstrip("\r\n")
        if not clean_text:
            return
        self.terminal.appendPlainText(clean_text)
        self.terminal.moveCursor(QTextCursor.MoveOperation.End)

    def _on_ready_read(self) -> None:
        if not self.proc:
            return
        raw = self.proc.readAll().data().decode("utf-8", errors="replace")
        for line in raw.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            self.append_log(line_str)

            # Match download progress e.g. "-> 145.2 MB / 707.7 MB (20.5%)"
            m = re.search(r'(\d+\.?\d*)\s*MB\s*/\s*(\d+\.?\d*)\s*MB\s*\(\s*(\d+\.?\d*)%\s*\)', line_str)
            if m:
                down_mb = float(m.group(1))
                total_mb = float(m.group(2))
                pct = float(m.group(3))
                self.progress_bar.setValue(int(pct))
                self.status_lbl.setText(f"正在下载: {down_mb:.1f} MB / {total_mb:.1f} MB ({pct:.1f}%)")
            elif "正在下载" in line_str:
                self.status_lbl.setText(line_str.replace("[Nemotron-Setup]", "").strip())
            elif "解压" in line_str:
                self.status_lbl.setText("正在解压推理引擎包...")
                self.progress_bar.setValue(92)
            elif "✅" in line_str:
                self.status_lbl.setText(line_str.replace("[Nemotron-Setup]", "").strip())

    def _on_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        if exit_code == 0:
            self._is_success = True
            self.progress_bar.setValue(100)
            self.status_lbl.setText("✅ Nemotron 极速端侧引擎下载完成！正在自动装载生效...")
            self.status_lbl.setStyleSheet("color: #10b981; font-size: 11px; font-weight: bold; border: none;")
            self.btn_action.setText("完成")
            self.btn_action.setStyleSheet("""
                QPushButton {
                    background-color: #10b981;
                    color: #ffffff;
                    font-size: 11px;
                    font-weight: bold;
                    border: none;
                    border-radius: 6px;
                    padding: 6px 16px;
                }
                QPushButton:hover {
                    background-color: #059669;
                }
            """)
            self.download_finished.emit(True, self.target_model)
            # Auto close smoothly after 1.5s
            QTimer.singleShot(1500, self.accept)
        else:
            self._is_success = False
            self.status_lbl.setText(f"❌ 下载未完成 (退出码: {exit_code})，请查看控制台日志")
            self.status_lbl.setStyleSheet("color: #ef4444; font-size: 11px; font-weight: bold; border: none;")
            self.btn_action.setText("关闭")
            self.btn_action.setStyleSheet("""
                QPushButton {
                    background-color: #475569;
                    color: #ffffff;
                    font-size: 11px;
                    border: none;
                    border-radius: 6px;
                    padding: 6px 16px;
                }
            """)
            self.download_finished.emit(False, self.target_model)

    def _on_action_clicked(self) -> None:
        if self._is_success or (self.proc and self.proc.state() == QProcess.ProcessState.NotRunning):
            self.accept()
            return

        # Cancel download
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self.append_log("⚠️ 用户手动取消下载进程。")
            self.proc.terminate()
            QTimer.singleShot(500, lambda: self.proc.kill() if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning else None)
        self.reject()
