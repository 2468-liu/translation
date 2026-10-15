#!/usr/bin/env bash
# ==============================================================================
# Launch script for Realtime AI Subtitles on Ubuntu 24.04 LTS / Zorin OS 18 / WSL2
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

# 针对 Wayland / X11 兼容性优化
# 在 GNOME/Wayland 环境下，强制使用 xcb 确保悬浮字幕层绝对置顶与零穿透拦截
if [ -z "$QT_QPA_PLATFORM" ]; then
    export QT_QPA_PLATFORM=xcb
fi

# 屏蔽 Qt 无害警告
export QT_LOGGING_RULES="*.debug=false;qt.qpa.*=false"

# 优先查找本地 tools 目录下的 Linux 引擎
if [ -d "tools/llama-server" ]; then
    export PATH="$SCRIPT_DIR/tools/llama-server:$PATH"
fi

echo "[Linux Launcher] 正在启动实时字幕桌面程序..."
python3 desktop_app.py "$@"
