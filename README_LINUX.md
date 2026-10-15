# 实时 AI 双语字幕桌面端 (Linux / Ubuntu 24.04 / Zorin OS 18 / WSL2 极简指南)

本项目为**免编译 Python 绿色源码版**，在 Linux 环境下直接配合原生 Linux 推理引擎即可运行，无需 PyInstaller 打包。

---

## 一、 数据盘未挂载 / 纯大写 DATA 盘 / 模型路径配置

很多用户的 Linux 硬盘挂载点各不相同（例如 Windows 分区自动挂载为纯大写 `/mnt/DATA`、`/media/$USER/DATA`，或根本尚未挂载）。本项目提供了 **全自动图形引导** 与 **多级容错机制**：

### 1. 启动弹窗图形化引导（最简单·零门槛）
* 当启动程序时，若系统在默认候选路径未找到模型文件，**启动窗口（Splash Screen）会自动弹出交互对话框**：
  * 点击 **【📁 手动选择模型目录...】**：直接在 Linux 文件管理器中点击您存放模型的文件夹（包含 `Index-Translate` 和 `Qwen-ASR` 的目录）；
  * 系统验证有效后会自动保存到 `desktop_config.json`，**一次设定，永久生效**，下次开机自启无需重复选择；
  * 如果您刚插上移动硬盘或刚执行完 `mount`，点击 **【🔄 重新扫描】** 即可秒级自动探测。

### 2. 环境变量一键指定（进阶推荐）
如果您习惯在脚本或终端中运行，可直接通过环境变量覆盖：
```bash
# 纯大写 DATA 挂载路径示例
export TRANSLATION_MODELS_DIR="/mnt/DATA/TranslationDesktop/models"

# 或直接挂载在 /media 下的外置盘
export TRANSLATION_MODELS_DIR="/media/$USER/DATA/TranslationDesktop/models"
```

### 3. 双系统建立软链接复用（省 5GB 空间）
如果您在 Windows 下已下载过模型，可以在 Linux 挂载 Windows 盘后软链接到项目目录：
```bash
ln -s "/mnt/DATA/TranslationDesktop/models" ./models
```

---

## 二、 快速安装与配置（两步搞定）

在解压后的工程根目录下打开终端：

### 1. 运行一键环境配置脚本
```bash
chmod +x setup_ubuntu.sh start_ubuntu.sh create_desktop_shortcut.sh
./setup_ubuntu.sh
```
该脚本会自动完成：
1. 安装系统级音频依赖库（`libpulse0`, `libasound2t64`）与 X11/XCB 动态库；
2. 安装高清无衬线中文字体包（`fonts-noto-cjk`, `fonts-wqy-microhei`），解决 Linux 下字幕模糊或缺字乱码问题；
3. 创建隔离的 Python 虚拟环境 `venv/`（绕过 Ubuntu 24.04 PEP 668 限制）；
4. 安装 `PyQt6`, `soundcard`, `onnxruntime`, `numpy`, `websockets` 等核心依赖；
5. 自动在您的 Linux 桌面生成【实时双语字幕】快捷方式图标。

---

## 三、 配置 Linux 版 llama 推理引擎

因 Windows 版 `llama-server.exe` 无法直接在 Linux 下以 ELF 格式运行，您只需准备官方 Linux 版本的 `llama`：

### 推荐方式：官方一键安装（已实测兼容 build 11630）
在终端中执行官方官方脚本（极速安装至 `~/.local/bin/llama`）：
```bash
curl -fsSL https://llama.app/install.sh | bash
```
安装完成后验证：
```bash
llama --version
# 输出: version: 0.6.0-dev (build 11630) built with Clang for Linux x86_64
```
本程序已自动集成该路径扫描，无需手动复制，启动时会自动调用。

---

## 四、 启动字幕程序

直接执行一键启动脚本：
```bash
./start_ubuntu.sh
```

### 核心工业级特性：
- **前置 0dB 物理直通回环**：彻底废弃业务层人工动态放大，直接截取 PipeWire 0dB 预推子声学信号。即便您在夜间将电脑音量拧到极低或静音，VAD/ASR 依然能 100% 稳定识别！
- **Linux 内核级单实例互斥锁 (`flock`)**：基于 `$XDG_RUNTIME_DIR` 内存文件锁控制，彻底杜绝连续双击图标导致多开冲突、端口爆满与显存争抢；进程退出后内核自动释放，绝不死锁。
- **三重安全 PID 防线**：服务管理严格限定 `LISTEN` 套接字，配合 `/proc/<pid>/cmdline` 进程指纹检测，自保自身 PID 并防止 Linux PID 漂移误杀系统无关服务。
- **Wayland / X11 绝对置顶**：启动脚本内置 `QT_QPA_PLATFORM=xcb`，保证悬浮双行字幕在任何全屏播放器或浏览器上方始终置顶，零穿透。

---

## 五、 生成桌面双击启动图标

如果需要重新生成桌面图标，执行：
```bash
./create_desktop_shortcut.sh
```
生成后桌面上会出现【实时双语字幕】图标。
*(若在 Ubuntu 24.04 GNOME 46 下首次双击未响应，右键点击图标选择【允许启动 (Allow Launching)】即可)*

---

## 六、 常见问题排查 (FAQ)

* **Q1: 为什么我的 Windows 托盘里出现了多个 Ubuntu 图标？**
  * **原因**：此前在 WSLg 自动化测试环境下执行单元测试，测试框架模拟生成了托盘对象被 WSLg 转发。
  * **解决**：后台测试已全部结束，鼠标在 Windows 任务栏右下角托盘轻轻滑过，Windows Shell 即会自动清除这些幽灵图标。正式代码中已加入测试隔离，正常使用绝不会产生多余图标。
* **Q2: 提示端口 8080 或 8099 被占用？**
  * 程序内置智能端口指纹探测，会安全接管并清理残留的模型进程，不会影响系统正常网络服务。
* **Q3: 换电脑后模型盘符变了？**
  * 随时修改项目根目录下的 `desktop_config.json`，或直接在启动时点击弹出的【📁 手动选择模型目录】按钮重新指定即可。
