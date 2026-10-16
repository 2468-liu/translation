#!/usr/bin/env bash
# ==============================================================================
# Setup script for Realtime AI Subtitles on Ubuntu 24.04 LTS / Zorin OS 18 / WSL2
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================================="
echo "  配置 Ubuntu 24.04 / Zorin OS 18 / WSL2 运行环境        "
echo "=========================================================="

# 1. 检测系统包管理器并安装底层动态库与音视频依赖
if command -v apt &> /dev/null; then
    echo "[1/4] 安装系统级依赖库 (PulseAudio / XCB / GL / 工具集)..."
    sudo apt update
    # Ubuntu 24.04 uses libasound2t64, while older distros use libasound2
    ASOUND_PKG="libasound2t64"
    if ! apt-cache show libasound2t64 &> /dev/null; then
        ASOUND_PKG="libasound2"
    fi
    APT_DEPS=(
        python3
        python3-venv
        python3-pip
        libpulse0
        "$ASOUND_PKG"
        libgl1
        libegl1
        libxcb-cursor0
        libxcb-xinerama0
        libxcb-icccm4
        libxcb-image0
        libxcb-keysyms1
        libxcb-randr0
        libxcb-render-util0
        libxcb-shape0
        libxkbcommon-x11-0
        fonts-noto-cjk
        fonts-wqy-microhei
        psmisc
        lsof
        curl
    )
    sudo apt install -y "${APT_DEPS[@]}"
fi

# 2. 创建 Python 独立虚拟环境 (绕过 PEP 668 EXTERNALLY-MANAGED 限制)
echo "[2/4] 初始化 Python 虚拟环境 (venv)..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "  -> 虚拟环境 venv 创建成功"
fi

source venv/bin/activate
pip install --upgrade pip

# 3. 安装 Python 核心依赖
echo "[3/4] 安装 Python 依赖包 (PyQt6 / soundcard / onnxruntime / websockets)..."
pip install -r requirements_ubuntu.txt

# 4. 检测模型目录与双系统快速挂载
echo "[4/4] 检测模型目录 (models)..."
if [ ! -d "models" ]; then
    # 自动探测 Windows 双系统或 WSL 挂载点
    WINDOWS_C_MODELS=$(ls -d /mnt/c/Users/*/Desktop/translation/models 2>/dev/null | head -n 1)
    WINDOWS_D_MODELS="/mnt/d/TranslationDesktop/models"
    if [ -d "$WINDOWS_D_MODELS" ]; then
        echo "  -> 自动发现 Windows D盘已下载模型: $WINDOWS_D_MODELS"
        ln -s "$WINDOWS_D_MODELS" models
        echo "  -> 已建立软链接: ./models -> $WINDOWS_D_MODELS (免重复下载!)"
    elif [ -d "$WINDOWS_C_MODELS" ]; then
        echo "  -> 自动发现 Windows C盘已下载模型: $WINDOWS_C_MODELS"
        ln -s "$WINDOWS_C_MODELS" models
        echo "  -> 已建立软链接: ./models -> $WINDOWS_C_MODELS (免重复下载!)"
    else
        mkdir -p models
        echo "  -> 已创建 models 目录。若为双系统，可软链接 Windows 分区中的 models 目录："
        echo "     ln -s /media/<用户名>/<分区>/models ./models"
    fi
else
    echo "  -> 发现已有 models 目录"
fi

# 5. 检测 llama-server / llama 可执行引擎
if ! command -v llama-server &> /dev/null && ! command -v llama &> /dev/null && [ ! -f "tools/llama-server/llama-server" ] && [ ! -f "tools/llama" ]; then
    echo "----------------------------------------------------------"
    echo "【提示】未检测到 Linux 版本的 llama 或 llama-server 可执行引擎。"
    echo "您可以选择以下任一方式就绪："
    echo "  方式 A (推荐): 安装官方 llama CLI 工具或 LM Studio Linux 版；"
    echo "  方式 B: 下载 llama.cpp Linux 编译版 (例如 release 页面中的 b3xxx-bin-ubuntu-x64.zip)"
    echo "          将其中的 llama-server 或 llama 放入 ./tools/llama-server/ 或加入系统 PATH"
    echo "          并赋予执行权限: chmod +x tools/llama-server/llama-server"
    echo "----------------------------------------------------------"
fi

chmod +x start_ubuntu.sh 2>/dev/null || true
chmod +x create_desktop_shortcut.sh 2>/dev/null || true

# 6. 生成桌面一键启动快捷方式
if [ -f "./create_desktop_shortcut.sh" ]; then
    echo "[6/6] 正在创建桌面一键启动快捷方式..."
    ./create_desktop_shortcut.sh || true
fi

echo "=========================================================="
echo "  配置完成！"
echo "  您可双击桌面上的【实时双语字幕】图标直接启动，"
echo "  或在终端执行 ./start_ubuntu.sh 启动！"
echo "=========================================================="
