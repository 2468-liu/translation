"""Caption context menu builder, action handlers, and language catalog.

Decouples all menu building, cascading language groups, CADENCE presets,
audio source process filtering, and settings callbacks from the main window.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtGui import QAction, QActionGroup, QCursor
from PyQt6.QtWidgets import QFileDialog, QMenu, QMessageBox, QWidget

if TYPE_CHECKING:
    from .caption_window import QtCaptionWindow

from .. import config
from .tray_manager import TRAY_MENU_STYLE, show_tray_menu_native

POPULAR_SRC: list[tuple[str, str]] = [
    ("🌐 自动识别 (Auto Detect)", "auto"),
    ("英语 (English)", "en"),
    ("日语 (日本語)", "ja"),
    ("韩语 (한국어)", "ko"),
    ("俄语 (Русский)", "ru"),
    ("普通话 (Chinese)", "zh"),
    ("德语 (Deutsch)", "de"),
    ("法语 (Français)", "fr"),
    ("西班牙语 (Español)", "es"),
]

POPULAR_TGT: list[tuple[str, str]] = [
    ("简体中文 (Simplified Chinese)", "zh"),
    ("英语 (English)", "en"),
    ("日语 (日本語)", "ja"),
    ("韩语 (한국어)", "ko"),
    ("繁体中文 (Traditional Chinese)", "zh-tw"),
    ("俄语 (Русский)", "ru"),
    ("法语 (Français)", "fr"),
    ("德语 (Deutsch)", "de"),
    ("西班牙语 (Español)", "es"),
]

ASIA_LANGS: list[tuple[str, str]] = [
    ("粤语 (Cantonese)", "yue"),
    ("越南语 (Tiếng Việt)", "vi"),
    ("泰语 (ไทย)", "th"),
    ("印尼语 (Bahasa Indonesia)", "id"),
    ("马来语 (Bahasa Melayu)", "ms"),
    ("印地语 (हिन्दी)", "hi"),
    ("高棉语 (ភាសាខ្មែរ)", "km"),
    ("缅甸语 (မြန်မာစာ)", "my"),
    ("老挝语 (ພາສາລາວ)", "lo"),
    ("菲律宾语 (Tagalog)", "tl"),
    ("孟加拉语 (বাংলা)", "bn"),
    ("泰卢固语 (తెలుగు)", "te"),
    ("马拉地语 (मराठी)", "mr"),
    ("泰米尔语 (தமிழ்)", "ta"),
    ("古吉拉特语 (ગુજરાતી)", "gu"),
    ("乌尔都语 (اردو)", "ur"),
]

EUROPE_LANGS: list[tuple[str, str]] = [
    ("意大利语 (Italiano)", "it"),
    ("葡萄牙语 (Português)", "pt"),
    ("荷兰语 (Nederlands)", "nl"),
    ("波兰语 (Polski)", "pl"),
    ("瑞典语 (Svenska)", "sv"),
    ("捷克语 (Čeština)", "cs"),
    ("匈牙利语 (Magyar)", "hu"),
    ("罗马尼亚语 (Română)", "ro"),
    ("乌克兰语 (Українська)", "uk"),
    ("希腊语 (Ελληνικά)", "el"),
    ("克罗地亚语 (Hrvatski)", "hr"),
    ("挪威语 (Norsk)", "nb"),
    ("丹麦语 (Dansk)", "da"),
    ("芬兰语 (Suomi)", "fi"),
    ("斯洛伐克语 (Slovenčina)", "sk"),
    ("保加利亚语 (Български)", "bg"),
    ("爱沙尼亚语 (Eesti)", "et"),
    ("斯洛文尼亚语 (Slovenščina)", "sl"),
    ("立陶宛语 (Lietuvių)", "lt"),
    ("拉脱维亚语 (Latviešu)", "lv"),
    ("土耳其语 (Türkçe)", "tr"),
]

MIDEAST_LANGS: list[tuple[str, str]] = [
    ("阿拉伯语 (العربية)", "ar"),
    ("波斯语 (فارسی)", "fa"),
    ("希伯来语 (עברית)", "he"),
]

REGIONAL_LANGS: list[tuple[str, str]] = [
    ("藏语 (བོད་སྐད)", "bo"),
    ("维吾尔语 (ئۇيغۇرچە)", "ug"),
    ("哈萨克语 (Қазақша)", "kk"),
    ("蒙古语 (Монгол хэл)", "mn"),
    ("格鲁吉亚语 (ქართული)", "ka"),
    ("阿塞拜疆语 (Azərbaycan)", "az"),
    ("乌兹别克语 (Oʻzbekcha)", "uz"),
]

CADENCES: list[tuple[str, int]] = [
    ("🎬 电影追剧 (推荐·1000ms·静音省电)", 1000),
    ("⚡ 演讲对话 (平衡·500ms·快速响应)", 500),
    ("🎙️ 极速同传 (实时·160ms·逐字流式)", 160),
]

QWEN_CADENCES: list[tuple[str, int]] = [
    ("🎬 1000ms (推荐·整句原子直出·抗高密度对白)", 1000),
    ("⚖️ 750ms (平衡·整句完整连贯)", 750),
    ("⚡ 500ms (快速·适合演讲对话)", 500),
    ("⏱️ 250ms (高频切块·试探刷新)", 250),
]

NV_CADENCES: list[tuple[str, int]] = [
    ("⚡ 最低延迟 80ms (Lowest latency·极致响应)", 80),
    ("🚀 快速 160ms (Fast·官方推荐·平衡首选)", 160),
    ("⚖️ 均衡 320ms (Balanced·模型默认·通用流式)", 320),
    ("🎯 高准确率 560ms (Accurate·高保真低容错)", 560),
    ("💎 最高准确率 1120ms (Most accurate·1.12s高精转录)", 1120),
]


def _is_testing_env() -> bool:
    if os.environ.get("TRANSLATION_FORCE_INTERACTIVE") == "1":
        return False
    return bool(
        os.environ.get("TRANSLATION_TEST_MODE")
        or os.environ.get("PYTEST_CURRENT_TEST")
        or "unittest" in sys.modules
    )


class CaptionMenuBuilder:
    """Builds and wires context menus and callbacks for QtCaptionWindow."""

    def __init__(self, window: QtCaptionWindow):
        self.win = window
        self.context_menu: Optional[QMenu] = None
        self.tray_menu: Optional[QMenu] = None

    def build_context_menu(self) -> QMenu:
        menu = QMenu(self.win)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1a1a24;
                color: #e2e8f0;
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 8px;
                padding: 6px;
                font-family: 'Microsoft YaHei', 'Noto Sans CJK SC', 'Source Han Sans CN', 'WenQuanYi Micro Hei', 'Ubuntu Sans', 'Segoe UI', sans-serif;
                font-size: 13px;
            }
            QMenu::item {
                padding: 6px 28px 6px 14px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #2e384d;
                color: #38bdf8;
            }
            QMenu::separator {
                height: 1px;
                background-color: rgba(255, 255, 255, 0.1);
                margin: 4px 8px;
            }
        """)

        # 0. Primary Toggle
        self.win._toggle_capture_act = menu.addAction("⏸ 暂停识别与翻译")
        self.win._toggle_capture_act.triggered.connect(self.win._on_toggle_capture_triggered)

        menu.addSeparator()

        # 0.1 Direct Engine & Cadence Switch (一行ASR，一行NV，下级目录是ms)
        self.win.engine_cadence_group = QActionGroup(self.win)
        self.win.scene_group = self.win.engine_cadence_group  # Compatibility alias

        qwen_menu = menu.addMenu("🧠 通用大模型 ASR (Qwen3)")
        self.win._qwen_actions = []
        for label, ms in QWEN_CADENCES:
            act = qwen_menu.addAction(label)
            act.setCheckable(True)
            act.setData({"backend": "qwen3", "ms": ms})
            self.win.engine_cadence_group.addAction(act)
            self.win._qwen_actions.append(act)

        nv_menu = menu.addMenu("⚡ 端侧极速流式 NV (Nemotron)")
        self.win._nv_actions = []
        for label, ms in NV_CADENCES:
            act = nv_menu.addAction(label)
            act.setCheckable(True)
            act.setData({"backend": "nemotron", "ms": ms})
            self.win.engine_cadence_group.addAction(act)
            self.win._nv_actions.append(act)

        self.win.engine_cadence_group.triggered.connect(self._on_engine_cadence_triggered)

        # Backward compatibility aliases for scene presets
        self.win._scene_movie_act = self.win._qwen_actions[0]   # 1000ms
        self.win._scene_speech_act = self.win._qwen_actions[2]  # 500ms
        self.win._scene_stream_act = next((a for a in self.win._nv_actions if a.data().get("ms") == 160), self.win._nv_actions[0])    # 160ms

        # 2. Direct Core Toggles
        self.win._rolling_two_lines_act = menu.addAction("📜 双行滚动字幕 (Google同款·抗高密度对白)")
        self.win._rolling_two_lines_act.setCheckable(True)
        self.win._rolling_two_lines_act.setChecked(getattr(self.win, "rolling_two_lines", True))
        self.win._rolling_two_lines_act.toggled.connect(self.win._on_rolling_two_lines_toggled)

        self.win._bilingual_act = menu.addAction("🔤 双语同显模式 (中英对照)")
        self.win._bilingual_act.setCheckable(True)
        self.win._bilingual_act.setChecked(getattr(self.win, "bilingual_mode", "trans_only") == "bilingual")
        self.win._bilingual_act.toggled.connect(self._on_context_bilingual_toggled)

        menu.addSeparator()

        # 3. Audio Source (Process-Tree Loopback / Global)
        self.win.audio_menu = menu.addMenu("🎧 监听音频源")
        self.win.audio_group = QActionGroup(self.win)
        self.win.audio_menu.aboutToShow.connect(self._refresh_audio_menu)
        self.win.audio_group.triggered.connect(self._on_audio_source_changed)

        # 4. Source Language
        src_menu = menu.addMenu("🌐 识别源语言")
        self.win.src_group = QActionGroup(self.win)
        self._populate_lang_group(src_menu, self.win.src_group, POPULAR_SRC, self.win.source_lang)
        src_menu.addSeparator()
        self._add_lang_submenu(src_menu, self.win.src_group, "🌏 亚洲与东南亚", ASIA_LANGS, self.win.source_lang)
        self._add_lang_submenu(src_menu, self.win.src_group, "🏛️ 欧洲语系", EUROPE_LANGS, self.win.source_lang)
        self._add_lang_submenu(src_menu, self.win.src_group, "🏜️ 中东与南亚", MIDEAST_LANGS, self.win.source_lang)

        sub_src_reg = src_menu.addMenu("🏔️ 少数民族与方言")
        act_tw = sub_src_reg.addAction("繁体中文 (Traditional Chinese)")
        act_tw.setCheckable(True)
        act_tw.setChecked("zh-tw" == self.win.source_lang)
        act_tw.setData("zh-tw")
        self.win.src_group.addAction(act_tw)
        self._populate_lang_group(sub_src_reg, self.win.src_group, REGIONAL_LANGS, self.win.source_lang)
        self.win.src_group.triggered.connect(self._on_source_lang_changed)

        # 5. Target Language
        tgt_menu = menu.addMenu("🎯 翻译目标语言")
        self.win.tgt_group = QActionGroup(self.win)
        self._populate_lang_group(tgt_menu, self.win.tgt_group, POPULAR_TGT, self.win.target_lang)
        tgt_menu.addSeparator()
        self._add_lang_submenu(tgt_menu, self.win.tgt_group, "🌏 亚洲与东南亚", ASIA_LANGS, self.win.target_lang)
        self._add_lang_submenu(tgt_menu, self.win.tgt_group, "🏛️ 欧洲语系", EUROPE_LANGS, self.win.target_lang)
        self._add_lang_submenu(tgt_menu, self.win.tgt_group, "🏜️ 中东与南亚", MIDEAST_LANGS, self.win.target_lang)
        self._add_lang_submenu(tgt_menu, self.win.tgt_group, "🏔️ 少数民族与方言", REGIONAL_LANGS, self.win.target_lang)
        self.win.tgt_group.triggered.connect(self._on_target_lang_changed)

        menu.addSeparator()

        # 6. Appearance & Styles
        style_menu = menu.addMenu("🎨 字幕外观设置")
        self.win._text_only_act = style_menu.addAction("只有字 (无背景框)")
        self.win._text_only_act.setCheckable(True)
        self.win._text_only_act.setChecked(self.win.text_only)
        self.win._text_only_act.toggled.connect(self.win._on_text_only_toggled)

        style_menu.addSeparator()
        self.win._max_width_act = style_menu.addAction("📺 铺满全屏宽度 (单行不换行)")
        self.win._max_width_act.triggered.connect(self.win._on_maximize_width_triggered)
        self.win._reset_width_act = style_menu.addAction("📐 恢复标准宽度")
        self.win._reset_width_act.triggered.connect(self.win._on_reset_width_triggered)

        style_menu.addSeparator()
        font_menu = style_menu.addMenu("字体大小")
        font_group = QActionGroup(self.win)
        for sz in (16, 18, 20, 24, 28, 32):
            act = font_menu.addAction(f"{sz} px")
            act.setCheckable(True)
            act.setChecked(sz == self.win.font_px)
            act.setData(sz)
            font_group.addAction(act)
        font_group.triggered.connect(self._on_font_changed)

        color_menu = style_menu.addMenu("文字颜色")
        color_group = QActionGroup(self.win)
        colors = [
            ("纯白", "#ffffff"),
            ("暖黄", "#fef08a"),
            ("青绿", "#67e8f9"),
            ("浅粉", "#fbcfe8"),
        ]
        for name, hex_code in colors:
            act = color_menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(hex_code.lower() == self.win.font_color.lower())
            act.setData(hex_code)
            color_group.addAction(act)
        color_group.triggered.connect(self._on_color_changed)

        menu.addSeparator()

        # 5. Hide Caption
        self.win._context_toggle_visibility_act = menu.addAction("隐藏字幕")
        self.win._context_toggle_visibility_act.triggered.connect(self.win._on_toggle_visibility_triggered)

        # 6. Pet Widget toggle
        self.win._show_pet_act = self.win._pet_toggle_act = menu.addAction("🐾 显示桌面挂件")
        self.win._pet_toggle_act.setCheckable(True)
        has_pet = bool(getattr(self.win, "pet_widget", None) and self.win.pet_widget.isVisible())
        self.win._pet_toggle_act.setChecked(has_pet)
        self.win._pet_toggle_act.toggled.connect(self.win._on_show_pet_toggled)

        # 7. Clear Caption
        clear_act = menu.addAction("清空当前字幕")
        clear_act.triggered.connect(self.win._on_clear_triggered)

        # 8. Set Model Directory
        models_dir_name = config.MODELS_DIR.name if hasattr(config, "MODELS_DIR") else "models"
        self.win._model_dir_act = menu.addAction(f"📁 设置模型路径 (当前: {models_dir_name})")
        self.win._model_dir_act.triggered.connect(self._on_choose_model_directory)

        # 9. Peek History
        hist_act = menu.addAction("📜 查看对话历史记录 (滚轮向上)")
        hist_act.triggered.connect(self.win.show_history_drawer)

        menu.addSeparator()
        exit_act = menu.addAction("退出程序")
        exit_act.triggered.connect(self.win._on_quit_app_triggered)

        self.context_menu = menu
        return menu

    def build_tray_menu(self) -> QMenu:
        menu = QMenu()
        menu.setStyleSheet(TRAY_MENU_STYLE)

        # 0. Primary Toggles
        self.win._tray_toggle_visibility_act = menu.addAction("隐藏字幕")
        self.win._tray_toggle_visibility_act.triggered.connect(self.win._on_toggle_visibility_triggered)
        self.win._toggle_visibility_act = self.win._tray_toggle_visibility_act

        self.win._tray_toggle_capture_act = menu.addAction("⏸ 暂停识别与翻译")
        self.win._tray_toggle_capture_act.triggered.connect(self.win._on_toggle_capture_triggered)

        menu.addSeparator()

        # 1. Direct Engine & Cadence Switch in Tray (一行ASR，一行NV，下级目录是ms)
        self.win.tray_engine_cadence_group = QActionGroup(self.win)
        self.win.tray_scene_group = self.win.tray_engine_cadence_group  # Compatibility alias

        tray_qwen_menu = menu.addMenu("🧠 通用大模型 ASR (Qwen3)")
        self.win._tray_qwen_actions = []
        for label, ms in QWEN_CADENCES:
            act = tray_qwen_menu.addAction(label)
            act.setCheckable(True)
            act.setData({"backend": "qwen3", "ms": ms})
            self.win.tray_engine_cadence_group.addAction(act)
            self.win._tray_qwen_actions.append(act)

        tray_nv_menu = menu.addMenu("⚡ 端侧极速流式 NV (Nemotron)")
        self.win._tray_nv_actions = []
        for label, ms in NV_CADENCES:
            act = tray_nv_menu.addAction(label)
            act.setCheckable(True)
            act.setData({"backend": "nemotron", "ms": ms})
            self.win.tray_engine_cadence_group.addAction(act)
            self.win._tray_nv_actions.append(act)

        self.win.tray_engine_cadence_group.triggered.connect(self._on_engine_cadence_triggered)

        # Backward compatibility aliases for tray scene presets
        self.win._tray_scene_movie = self.win._tray_qwen_actions[0]   # 1000ms
        self.win._tray_scene_speech = self.win._tray_qwen_actions[2]  # 500ms
        self.win._tray_scene_stream = next((a for a in self.win._tray_nv_actions if a.data().get("ms") == 160), self.win._tray_nv_actions[0])    # 160ms

        menu.addSeparator()

        # 2. 字幕排版与双语 (快捷直达)
        self.win._tray_rolling_two_lines_act = menu.addAction("📜 双行滚动字幕 (Google同款·抗高密度对白)")
        self.win._tray_rolling_two_lines_act.setCheckable(True)
        self.win._tray_rolling_two_lines_act.setChecked(getattr(self.win, "rolling_two_lines", False))
        self.win._tray_rolling_two_lines_act.toggled.connect(self.win._on_rolling_two_lines_toggled)

        self.win._tray_bilingual_act = menu.addAction("🔤 双语同显模式 (中英对照)")
        self.win._tray_bilingual_act.setCheckable(True)
        self.win._tray_bilingual_act.setChecked(getattr(self.win, "bilingual_mode", "trans_only") == "bilingual")
        self.win._tray_bilingual_act.toggled.connect(self._on_tray_bilingual_toggled)

        menu.addSeparator()

        # 3. 完整设置 & 退出
        tray_ctrl_act = menu.addAction("⚙️ 字幕功能设置 (完整菜单)")
        tray_ctrl_act.triggered.connect(lambda: self.show_context_menu())

        self.win._tray_model_dir_act = menu.addAction(f"📁 设置模型路径 (当前: {Path(config.MODELS_DIR).name})")
        self.win._tray_model_dir_act.triggered.connect(self._on_choose_model_directory)

        menu.addSeparator()
        tray_exit_act = menu.addAction("退出程序")
        tray_exit_act.triggered.connect(self.win._on_quit_app_triggered)

        self.tray_menu = menu
        return menu

    def show_context_menu(self, pos: Optional[QPoint] = None) -> None:
        menu = self.context_menu or getattr(self.win, "_context_menu", None)
        if not menu:
            return
        if pos is None:
            pos = QCursor.pos()
        if menu.isVisible():
            self.safe_close_menus()
            return

        self._sync_preset_actions(self.get_current_preset())
        self._sync_lang_actions()
        if getattr(self.win, "_toggle_capture_act", None):
            is_cap = getattr(self.win, "_is_capturing", True)
            self.win._toggle_capture_act.setText("⏸ 暂停识别与翻译" if is_cap else "▶ 开启识别与翻译")
        if getattr(self.win, "_show_pet_act", None):
            has_pet = bool(getattr(self.win, "pet_widget", None) and self.win.pet_widget.isVisible())
            self.win._show_pet_act.blockSignals(True)
            self.win._show_pet_act.setChecked(has_pet)
            self.win._show_pet_act.blockSignals(False)
        if getattr(self.win, "_context_toggle_visibility_act", None):
            is_vis = self.win.isVisible() and self.win.visible
            self.win._context_toggle_visibility_act.setText("隐藏字幕" if is_vis else "显示字幕")
        if getattr(self.win, "_rolling_two_lines_act", None):
            self.win._rolling_two_lines_act.blockSignals(True)
            self.win._rolling_two_lines_act.setChecked(getattr(self.win, "rolling_two_lines", False))
            self.win._rolling_two_lines_act.blockSignals(False)
        if getattr(self.win, "_bilingual_act", None):
            self.win._bilingual_act.blockSignals(True)
            self.win._bilingual_act.setChecked(getattr(self.win, "bilingual_mode", "trans_only") == "bilingual")
            self.win._bilingual_act.blockSignals(False)
        if getattr(self.win, "_model_dir_act", None):
            self.win._model_dir_act.setText(f"📁 设置模型路径 (当前: {Path(config.MODELS_DIR).name})")

        show_tray_menu_native(menu, int(self.win.winId()), pos)

    def show_tray_menu(self, pos: Optional[QPoint] = None) -> None:
        menu = self.tray_menu or getattr(self.win, "_tray_menu", None)
        if not menu:
            return
        if pos is None:
            pos = QCursor.pos()
        if menu.isVisible():
            self.safe_close_menus()
            return
        self._sync_preset_actions(self.get_current_preset())
        self._sync_lang_actions()
        if hasattr(self.win, "_tray_toggle_visibility_act") and self.win._tray_toggle_visibility_act:
            is_vis = self.win.isVisible() and self.win.visible
            self.win._tray_toggle_visibility_act.setText("隐藏字幕" if is_vis else "显示字幕")
        if getattr(self.win, "_tray_toggle_capture_act", None):
            is_cap = getattr(self.win, "_is_capturing", True)
            self.win._tray_toggle_capture_act.setText("⏸ 暂停识别与翻译" if is_cap else "▶ 开启识别与翻译")
        if getattr(self.win, "_tray_rolling_two_lines_act", None):
            self.win._tray_rolling_two_lines_act.blockSignals(True)
            self.win._tray_rolling_two_lines_act.setChecked(getattr(self.win, "rolling_two_lines", False))
            self.win._tray_rolling_two_lines_act.blockSignals(False)
        if getattr(self.win, "_tray_bilingual_act", None):
            self.win._tray_bilingual_act.blockSignals(True)
            self.win._tray_bilingual_act.setChecked(getattr(self.win, "bilingual_mode", "trans_only") == "bilingual")
            self.win._tray_bilingual_act.blockSignals(False)
        if getattr(self.win, "_tray_model_dir_act", None):
            self.win._tray_model_dir_act.setText(f"📁 设置模型路径 (当前: {Path(config.MODELS_DIR).name})")
        show_tray_menu_native(menu, int(self.win.winId()), pos)

    def get_current_preset(self) -> str:
        backend = getattr(self.win, "asr_backend", "qwen3")
        ms = getattr(self.win, "partial_interval_ms", 1000)
        if backend == "qwen3" and ms == 1000:
            return "movie"
        elif backend == "qwen3" and ms == 500:
            return "speech"
        elif backend == "nemotron" or ms in (80, 160, 320, 560, 1120):
            return "stream"
        return "movie"

    def _launch_nemotron_download(self) -> None:
        """Launch background one-click provisioning script and toast."""
        script_py = config.ROOT / "scripts" / "setup_nemotron.py"
        try:
            cmd = [sys.executable, str(script_py), "--auto"]
            creation_flags = 0
            if sys.platform == "win32":
                creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            subprocess.Popen(
                cmd,
                cwd=str(config.ROOT),
                creationflags=creation_flags,
            )
            self.win.command_received.emit(
                "toast", "🚀 已启动 Nemotron 极速引擎一键下载，正在后台拉取官方资源...", time.time(), time.time(), False
            )
        except Exception as e:
            QMessageBox.critical(
                self.win,
                "启动下载失败",
                f"启动自动下载脚本失败：{e}\n\n您也可以在终端手动运行：\npython scripts/setup_nemotron.py",
            )

    def apply_engine_cadence(self, backend: str, ms: int) -> None:
        if backend == "nemotron":
            try:
                from server.asr.nemotron import check_nemotron_status
                is_ready, reason = check_nemotron_status()
            except Exception as e:
                is_ready, reason = False, str(e)

            if not is_ready:
                if not _is_testing_env():
                    reply = QMessageBox.question(
                        self.win,
                        "未检测到 Nemotron 引擎组件",
                        f"检测到尚未配置 NVIDIA Nemotron 3.5 极速端侧引擎组件：\n{reason}\n\n"
                        "是否立即启动一键自动拉取？（官方模型与推理引擎约 700MB）\n\n"
                        "【是】：后台启动一键脚本拉取并配置官方引擎\n"
                        "【否】：取消切换，安全保持使用 Qwen3-ASR 通用模型",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.Yes,
                    )
                    if reply == QMessageBox.StandardButton.Yes:
                        self._launch_nemotron_download()
                    else:
                        self.win.command_received.emit(
                            "toast", "已取消切换，保持使用 Qwen3-ASR 通用模型", time.time(), time.time(), False
                        )
                self._sync_engine_cadence_actions()
                return

        cfg = config.load_desktop_config()
        rolling = getattr(self.win, "rolling_two_lines", True)
        punct = True

        cfg["asr_backend"] = backend
        cfg["partial_interval_ms"] = ms
        cfg["block_by_punct"] = punct
        cfg["rolling_two_lines"] = rolling
        config.save_desktop_config(cfg)

        self.win.set_asr_backend_ui(backend)
        self.win.set_cadence_ui(ms)
        self.win.set_block_by_punct(punct)
        self._sync_engine_cadence_actions()

        QTimer.singleShot(10, lambda: self.win.config_changed.emit({
            "asr_backend": backend,
            "partial_interval_ms": ms,
            "block_by_punct": punct,
            "rolling_two_lines": rolling,
        }))

    def apply_preset(self, preset_key: str) -> None:
        if preset_key == "movie":
            self.apply_engine_cadence("qwen3", 1000)
        elif preset_key == "speech":
            self.apply_engine_cadence("qwen3", 500)
        elif preset_key == "stream":
            self.apply_engine_cadence("nemotron", 160)

    def _on_engine_cadence_triggered(self, action: QAction) -> None:
        if not action:
            return
        self.safe_close_menus()
        data = action.data()
        if isinstance(data, dict):
            backend = str(data.get("backend", "qwen3"))
            ms = int(data.get("ms", 1000))
            self.apply_engine_cadence(backend, ms)
        elif isinstance(data, str):
            self.apply_preset(data)

    def _on_scene_preset_triggered(self, action: QAction) -> None:
        self._on_engine_cadence_triggered(action)

    def _sync_engine_cadence_actions(self) -> None:
        backend = getattr(self.win, "asr_backend", "qwen3")
        ms = getattr(self.win, "partial_interval_ms", 1000)
        seen_grps = set()
        for grp in (
            getattr(self.win, "engine_cadence_group", None),
            getattr(self.win, "tray_engine_cadence_group", None),
            getattr(self.win, "scene_group", None),
            getattr(self.win, "tray_scene_group", None),
        ):
            if not grp or id(grp) in seen_grps:
                continue
            seen_grps.add(id(grp))
            for a in grp.actions():
                d = a.data()
                grp.blockSignals(True)
                if isinstance(d, dict):
                    a.setChecked(d.get("backend") == backend and d.get("ms") == ms)
                elif isinstance(d, str):
                    a.setChecked(d == self.get_current_preset())
                grp.blockSignals(False)

    def _sync_preset_actions(self, preset_key: str) -> None:
        self._sync_engine_cadence_actions()

    def _sync_lang_actions(self) -> None:
        src = getattr(self.win, "source_lang", "auto")
        tgt = getattr(self.win, "target_lang", "zh")
        for grp in (getattr(self.win, "src_group", None), getattr(self.win, "tray_src_group", None)):
            if grp:
                grp.blockSignals(True)
                for act in grp.actions():
                    act.setChecked(str(act.data()) == src)
                grp.blockSignals(False)
        for grp in (getattr(self.win, "tgt_group", None), getattr(self.win, "tray_tgt_group", None)):
            if grp:
                grp.blockSignals(True)
                for act in grp.actions():
                    act.setChecked(str(act.data()) == tgt)
                grp.blockSignals(False)

    def _toggle_bilingual(self, checked: bool) -> None:
        self.safe_close_menus()
        mode = "bilingual" if checked else "trans_only"
        self.win.bilingual_mode = mode
        cfg = config.load_desktop_config()
        cfg["bilingual_mode"] = mode
        config.save_desktop_config(cfg)
        for act in (getattr(self.win, "_bilingual_act", None), getattr(self.win, "_tray_bilingual_act", None)):
            if act:
                act.blockSignals(True)
                act.setChecked(checked)
                act.blockSignals(False)
        self.win._paint()
        QTimer.singleShot(10, lambda: self.win.config_changed.emit({"bilingual_mode": mode}))

    def _on_context_bilingual_toggled(self, checked: bool) -> None:
        self._toggle_bilingual(checked)

    def _on_tray_bilingual_toggled(self, checked: bool) -> None:
        self._toggle_bilingual(checked)

    def safe_close_menus(self) -> None:
        for m in (self.context_menu, self.tray_menu, getattr(self.win, "_context_menu", None), getattr(self.win, "_tray_menu", None)):
            if not m:
                continue
            try:
                for act in m.actions():
                    sub = act.menu()
                    if sub and sub.isVisible():
                        sub.close()
                if m.isVisible():
                    m.close()
            except Exception:
                pass

    def _refresh_audio_menu(self) -> None:
        if not hasattr(self.win, "audio_menu") or not self.win.audio_menu:
            return
        self.win.audio_menu.clear()

        act_global = self.win.audio_menu.addAction("● 全局系统声音 (默认)")
        act_global.setCheckable(True)
        act_global.setChecked(self.win.current_audio_pid == 0)
        act_global.setData(0)
        self.win.audio_group.addAction(act_global)

        act_refresh = self.win.audio_menu.addAction("🔄 刷新会话列表")
        act_refresh.setData("refresh")
        self.win.audio_group.addAction(act_refresh)

        try:
            from server.process_loopback import ProcessLoopbackManager
            mgr = ProcessLoopbackManager()
            sessions = mgr.enumerate_sessions() if mgr.is_available else []
        except Exception:
            sessions = []

        if sessions:
            sessions.sort(key=lambda s: (not s.get("is_active", False), s.get("name", "").lower()))
            self.win.audio_menu.addSeparator()
            for s in sessions:
                pid = s["pid"]
                name = s["name"]
                is_active = s["is_active"]
                label = f"{name} (PID: {pid})"
                if is_active:
                    label += " [正在发声]"
                act = self.win.audio_menu.addAction(label)
                act.setCheckable(True)
                act.setChecked(self.win.current_audio_pid == pid)
                act.setData(pid)
                self.win.audio_group.addAction(act)

        self.win.audio_menu.addSeparator()
        hint_act = self.win.audio_menu.addAction("💡 未出现目标应用？请先让其播放声音再打开此菜单")
        hint_act.setEnabled(False)

    def _on_audio_source_changed(self, action: QAction) -> None:
        if not action:
            return
        data = action.data()
        if data == "refresh":
            self._refresh_audio_menu()
            return
        self.safe_close_menus()
        try:
            pid = int(data)
        except (TypeError, ValueError):
            pid = 0
        name = action.text()
        self.win.current_audio_pid = pid
        QTimer.singleShot(10, lambda: self.win.config_changed.emit({
            "target_pid": pid,
            "target_process_name": name,
        }))

    def _on_source_lang_changed(self, action: QAction) -> None:
        if not action:
            return
        self.safe_close_menus()
        code = str(action.data())

        current_backend = getattr(self.win, "asr_backend", "qwen3")
        from server.asr.text_filters import is_nemo_supported
        if current_backend in ("nemotron", "nv") and not is_nemo_supported(code):
            self.apply_engine_cadence("qwen3", 1000)
            lang_name = action.text().split("(")[0].strip()
            self.win.command_received.emit(
                "toast",
                f"「{lang_name}」超出 NV 极速引擎范围，已自动为您启用 Qwen3 通用大模型引擎",
                time.time(),
                time.time(),
                False,
            )

        self.win.source_lang = code
        self.win.set_status(code)
        self._sync_lang_actions()
        cfg = config.load_desktop_config()
        cfg["language"] = code
        config.save_desktop_config(cfg)
        QTimer.singleShot(10, lambda: self.win.config_changed.emit({"language": code}))

    def _on_target_lang_changed(self, action: QAction) -> None:
        if not action:
            return
        self.safe_close_menus()
        code = str(action.data())
        self.win.target_lang = code
        self.win.set_status(target_lang=code)
        cfg = config.load_desktop_config()
        cfg["target_language"] = code
        config.save_desktop_config(cfg)
        QTimer.singleShot(10, lambda: self.win.config_changed.emit({"target_language": code}))

    def _on_cadence_changed(self, action: QAction) -> None:
        if not action:
            return
        self.safe_close_menus()
        ms = int(action.data())
        self.win.partial_interval_ms = ms
        cfg = config.load_desktop_config()
        cfg["partial_interval_ms"] = ms
        config.save_desktop_config(cfg)
        self.win.set_cadence_ui(ms)
        QTimer.singleShot(10, lambda: self.win.config_changed.emit({"partial_interval_ms": ms}))

    def _on_font_changed(self, action: QAction) -> None:
        if not action:
            return
        self.safe_close_menus()
        try:
            sz = int(action.data())
        except (TypeError, ValueError):
            sz = 20
        cfg = config.load_desktop_config()
        cfg["font_px"] = sz
        config.save_desktop_config(cfg)
        QTimer.singleShot(10, lambda: self.win.apply_font(sz))

    def _on_color_changed(self, action: QAction) -> None:
        if not action:
            return
        self.safe_close_menus()
        chex = str(action.data())
        cfg = config.load_desktop_config()
        cfg["font_color"] = chex
        config.save_desktop_config(cfg)
        QTimer.singleShot(10, lambda: self.win.apply_color(chex))

    def _on_asr_backend_changed(self, action: QAction) -> None:
        if not action:
            return
        self.safe_close_menus()
        backend = str(action.data())
        self.win.set_asr_backend_ui(backend)
        cfg = config.load_desktop_config()
        cfg["asr_backend"] = backend
        cfg["block_by_punct"] = True
        self.win.set_block_by_punct(True)
        if backend == "qwen3":
            cfg["partial_interval_ms"] = 1000
            self.win.set_cadence_ui(1000)
        else:
            cfg["partial_interval_ms"] = 160
            self.win.set_cadence_ui(160)
        config.save_desktop_config(cfg)
        QTimer.singleShot(10, lambda: self.win.config_changed.emit({"asr_backend": backend, "block_by_punct": True}))

    def _on_bilingual_mode_changed(self, action: QAction) -> None:
        if not action:
            return
        self.safe_close_menus()
        mode = str(action.data())
        self.win.bilingual_mode = mode
        for grp in (getattr(self.win, "display_mode_group", None), getattr(self.win, "tray_display_mode_group", None)):
            if grp:
                for a in grp.actions():
                    if a.data() == mode:
                        grp.blockSignals(True)
                        a.setChecked(True)
                        grp.blockSignals(False)
                        break
        cfg = config.load_desktop_config()
        cfg["bilingual_mode"] = mode
        config.save_desktop_config(cfg)
        self.win._paint()

    def _on_choose_model_directory(self) -> None:
        self.safe_close_menus()
        current = str(config.MODELS_DIR)
        path = QFileDialog.getExistingDirectory(
            self.win,
            "选择模型存放目录 (请选择包含 Qwen-ASR-0.6B / Index-Translate 的文件夹)",
            current,
        )
        if path:
            p = Path(path)
            config.set_models_dir(p)
            if hasattr(self.win, "_model_dir_act") and self.win._model_dir_act:
                self.win._model_dir_act.setText(f"📁 设置模型路径 (当前: {p.name})")
            QMessageBox.information(
                self.win,
                "模型路径已设置",
                f"模型目录已成功设置为：\n{p}\n\n该路径已持久化保存至 desktop_config.json。\n建议重启程序使所有引擎平滑重新加载。",
            )
            self.win.config_changed.emit({"models_dir": str(p)})

    def _populate_lang_group(
        self, menu: QMenu, group: QActionGroup, items: list[tuple[str, str]], current: str
    ):
        for label, code in items:
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(code == current)
            act.setData(code)
            group.addAction(act)

    def _add_lang_submenu(
        self,
        parent: QMenu,
        group: QActionGroup,
        title: str,
        items: list[tuple[str, str]],
        current: str,
    ):
        sub = parent.addMenu(title)
        self._populate_lang_group(sub, group, items, current)
