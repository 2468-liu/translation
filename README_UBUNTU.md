# 实时 AI 字幕桌面端 (Ubuntu 24.04 LTS / Zorin OS 18 / WSL2 指南)

本项目为**免编译源码绿色版**，在 Linux 环境下直接使用 Python 虚拟环境与原生 Linux 引擎即可运行，无需 PyInstaller 打包。

---

## 一、 双系统极速复用现有模型（无需重新下载 5GB 模型）

如果您是 **Windows + Ubuntu / Zorin 双系统**，您在 Windows 下已下载好的模型文件可以直接跨系统软链接复用，节省数十 GB 磁盘空间与重复下载时间：

```bash
# 1. 在 Ubuntu 文件管理器中挂载 Windows 的 C 盘或 D 盘
# 2. 找到 Windows 分区中模型的路径，例如：
#    /media/<你的Ubuntu用户名>/<Windows分区UUID或卷标>/TranslationDesktop/models
#    或者 /mnt/d/TranslationDesktop/models (WSL 环境)

# 3. 在项目根目录下建立软链接：
ln -s "/media/$USER/DATA/TranslationDesktop/models" ./models

# 4. 验证软链接：
ls -l ./models
# 应该能看到 Qwen-ASR-0.6B、Index-Translate、silero_vad.onnx 等
```

---

## 二、 快速安装与配置（两步搞定）

在解压后的工程根目录下打开终端：

### 1. 运行一键配置脚本
```bash
chmod +x setup_ubuntu.sh start_ubuntu.sh
./setup_ubuntu.sh
```
该脚本会自动：
- 安装 Ubuntu 24.04 / Zorin OS 18 所需的系统级音频库（`libpulse0`, `libasound2t64`）、X11/XCB 动态库；
- 创建隔离的 Python 虚拟环境 `venv/`（完美避开 Ubuntu 24.04 PEP 668 的 externally-managed 限制）；
- 安装 `PyQt6`, `soundcard`, `onnxruntime`, `numpy`, `websockets` 等核心依赖。

---

## 三、 配置 Linux 版 llama-server 引擎

因为 Windows 版的 `llama-server.exe` 无法直接在 Linux 下以 ELF 格式执行，您只需准备一个 Linux 版本的 `llama-server`：

### 方式 A（最简单·推荐）：安装 Linux 版 LM Studio
如果您在 Linux 下安装了 LM Studio，本程序会自动检测 `~/.cache/lm-studio/extensions/backends/` 下的 GPU 引擎，完全零配置。

### 方式 B：手动放置 Linux 版 llama-server
1. 前往 llama.cpp 官方 Releases 页面下载 Ubuntu 版本的编译包（如 `llama-bXXXX-bin-ubuntu-x64.zip` 或 CUDA12 加速版本）；
2. 将解压出来的可执行文件 `llama-server` 放入本项目的 `tools/llama-server/` 目录下：
   ```bash
   mkdir -p tools/llama-server
   cp /path/to/downloaded/llama-server tools/llama-server/
   chmod +x tools/llama-server/llama-server
   ```
3. 验证是否可执行：
   ```bash
   ./tools/llama-server/llama-server --version
   ```

---

## 四、 启动字幕程序

直接执行一键启动脚本：
```bash
./start_ubuntu.sh
```

### 参数与特性：
- **音频回环监听**：自动挂钩 Ubuntu 24.04 / Zorin 18 默认的 PipeWire / PulseAudio 扬声器回环源（Monitor），任何播放器（如 VLC、Chrome、B站、网易云等）播放的声音均可直接被捕获并翻译；
- **窗口置顶保护**：脚本已预设 `export QT_QPA_PLATFORM=xcb`，保证在 Wayland 桌面下悬浮双行字幕窗口依然具备绝对置顶与零穿透能力；
- **引擎自由切换**：右键点击字幕或托盘图标，可直接切换 Qwen3-ASR 或 Nemotron 步长，切换时自动杀死空闲引擎彻底还回 Linux 显存。

---

## 五、 在 WSL2 (Ubuntu) 下进行测试

如果您在 Windows 下想先通过 WSL2 进行验证：
1. 确保已开启 WSLg（Windows 11 默认自带支持 GUI）；
2. 在 WSL 终端中进入项目目录：
   ```bash
   cd /mnt/c/Users/20693/Desktop/translation
   ./setup_ubuntu.sh
   ./start_ubuntu.sh --dry-run
   ```
3. 测试通过后即可无缝迁移到实体机 Ubuntu 24.04 / Zorin 18 双系统中日常使用！
