#!/usr/bin/env bash
# ==============================================================================
# Script to create Desktop Launcher for Ubuntu 24.04 / Zorin OS 18 / Debian
# ==============================================================================
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="${HOME}/Desktop"
if [ -d "${HOME}/桌面" ]; then
    DESKTOP_DIR="${HOME}/桌面"
fi

mkdir -p "${DESKTOP_DIR}"

SHORTCUT_PATH="${DESKTOP_DIR}/TranslationDesktop.desktop"
ICON_PATH="${PROJECT_DIR}/assets/icon.png"

# Ensure execution permissions on scripts
chmod +x "${PROJECT_DIR}/start_ubuntu.sh" 2>/dev/null || true
chmod +x "${PROJECT_DIR}/create_desktop_shortcut.sh" 2>/dev/null || true

echo "正在生成桌面快捷方式: ${SHORTCUT_PATH}..."

cat <<EOF > "${SHORTCUT_PATH}"
[Desktop Entry]
Version=1.0
Type=Application
Name=实时双语字幕 (Translation Desktop)
Comment=基于 Qwen3-ASR 与 Index-Translate 的本地离线流式字幕
Exec=bash -c "cd '${PROJECT_DIR}' && bash start_ubuntu.sh"
Icon=${ICON_PATH}
Path=${PROJECT_DIR}
Terminal=false
StartupNotify=true
Categories=AudioVideo;Audio;Utility;
EOF

chmod +x "${SHORTCUT_PATH}"

# Ubuntu 24.04 (GNOME 46) 桌面图标安全标记，允许双击直接启动
if command -v gio &> /dev/null; then
    gio set "${SHORTCUT_PATH}" metadata::trusted true || true
fi

echo "=================================================="
echo " 快捷方式创建成功！已放置在: ${DESKTOP_DIR}"
echo " 若首次双击未响应，请右键选择【允许启动 (Allow Launching)】即可。"
echo "=================================================="
