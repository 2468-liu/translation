<div align="center">

# 🎬 实时双语字幕与同传系统
### Real-Time Bilingual Caption & Interpretation Desktop System

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Ubuntu%2024.04%20LTS-brightgreen.svg)]()
[![Backend](https://img.shields.io/badge/backend-llama.cpp%20%7C%20Vulkan-orange.svg)]()

[ 简体中文 ](README.md) | [ English ](README_EN.md)

</div>

> **低延迟、纯本地离线、高精度的影视追剧与实时会议双语字幕桌面系统。**  
> 支持 Windows 原生 WASAPI 进程级回环录音与 Linux 原生 PipeWire/PulseAudio 0dB 物理直捕。无需云端 API，无需提取音频文件，即开即翻。

---

## 🌟 核心特性与架构亮点

* **双模 ASR 极速架构**：
  * **电影级语义整句直出 (Qwen3-ASR 0.6B)**：默认推荐，步长 1000ms。配合标点智能成块与气口断句，字幕输出即为完整自然句，彻底告别逐字跳动与高密度对白吞字。
  * **极速流式同传 (NVIDIA Nemotron 3.5 0.6B)**：端侧 Vulkan C 原生直驱，步长 160ms，逐字极速流式上屏，适合会议同传、演讲直播。
* **极低显存双并发影视翻译**：
  * 搭载 **Index-Translate 1.9B (Q4_K_M)**，针对影视口语对白、专业俚语与地道中文做了深度微调。
  * 配合 `llama-server` 多槽位（Slot）并发，ASR 与翻译总显存驻留仅约 **3.2GB**，RTX 3060/4060 即可全程满帧流畅运行。
* **双平台原生低损回环采集**：
  * **Windows**：原生 WASAPI 进程树级定向内录（可单独监听特定播放器或浏览器，不受麦克风底噪或其它系统提示音干扰）。
  * **Linux (Ubuntu 24.04 / Zorin 18)**：原生接入 PipeWire / PulseAudio 监听链，采用物理未衰减的 0dB 纯净回环，彻底杜绝软件自适应放大的底噪与爆音失真。
* **电影级交互体验**：
  * **双行平滑滚动字幕**：参考 Chromium / Google Live Caption 工业级抗拥堵算法，上一句定稿后平滑滚动锚定，新句流式生成，观影视线极其舒适。
  * **动态自适应音频规整器**：前置软阈值限幅与防爆音压缩，电影枪战爆炸不爆麦，窃窃私语不漏听。
  * **启动防呆自愈与目录选择器**：双系统挂载或自定义路径未识别时，启动页自动弹出手选引导；误切不支持的引擎时自动安全回退至 Qwen3-ASR，杜绝崩溃死循环。

---

## 📥 模型与引擎下载指引 (Model & Engine Matrix)

本项目遵循开源软件最佳实践，**不内置几百兆的大模型权重与第三方编译二进制**，请按需从官方源下载对应组件：

### 1. 必选模型（放置于 `models/` 目录）

| 组件 | 推荐量化版本 | 建议存放子路径 | 官方下载链接 (HuggingFace / ModelScope / GitHub) |
| :--- | :--- | :--- | :--- |
| **Silero VAD** | `silero_vad.onnx` (4MB) | `models/silero_vad.onnx` | [GitHub Direct Download](https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx) |
| **Index-Translate** | `index-translate-q4_k_m.gguf` | `models/Index-Translate/` | [HuggingFace 链接](https://huggingface.co/IndexTeam/Index-1.9B-Chat) / [ModelScope 镜像](https://www.modelscope.cn/models/IndexTeam/Index-1.9B-Chat) |
| **Qwen3-ASR 模型** | `qwen3-asr-0.6b.gguf` | `models/Qwen-ASR-0.6B/` | [HuggingFace 链接](https://huggingface.co/Qwen/Qwen3-ASR-0.6B-GGUF) / [ModelScope 镜像](https://www.modelscope.cn/models/Qwen/Qwen3-ASR-0.6B-GGUF) |
| **Qwen3-ASR 投影器** | `mmproj-qwen3-asr-0.6b.gguf` | `models/Qwen-ASR-0.6B/` | 包含在上述 Qwen3-ASR 仓库同级目录中 |

> 💡 **快速下载提示**：在项目根目录运行 `python scripts/download_models.py`，脚本将自动拉取轻量级 VAD 并输出其他模型的直链指引。

### 2. 运行时推理引擎 (llama-server)

本系统的翻译服务（8080端口）与 Qwen3-ASR 服务（8099端口）由官方 `llama-server` 驱动：
* **Windows**：推荐安装 [llama.cpp Official Releases](https://github.com/ggml-org/llama.cpp/releases) (build 11630+，如 `llama-bxxxx-bin-win-cuda-cu12.4-x64.zip`)，将其解压后将 `llama-server.exe` 放入系统 PATH 环境变量或 `tools/llama-server/` 目录。
* **Linux (Ubuntu 24.04)**：终端运行 `llama-server --version`；若未安装，可直接通过 `llama.cpp` Releases 下载对应 Linux 预编译包或安装 LM Studio。

### 3. 端侧极速流式引擎 (NVIDIA Nemotron 3.5 - Windows & Linux)
* **跨平台原生支持**：支持 Windows (x64) 与 Linux (Ubuntu 24.04+ x86_64)。
* **一键自动拉取向导**：
  * **图形界面自适应**：在字幕右键菜单中直接切换为【端侧极速流式 NV (Nemotron)】。若本地尚未配置，系统将弹出交互式对话框提示；点击【是】即可后台自动拉取官方资源；若取消则安全保持 Qwen3-ASR 正常运行。
  * **一键脚本命令行部署**：
    * **首选（官方原生引擎直连）**：`python scripts/setup_nemotron.py`（Linux: `./scripts/setup_nemotron.sh`，Windows: `.\scripts\setup_nemotron.ps1`），自动通过 HuggingFace/hf-mirror 下载模型 GGUF 并从 NVIDIA GitHub Releases 拉取预编译库（`nemo_speech_asr_c.dll` / `libnemo_speech_asr_c.so`）。
    * **次选（官方 Docker 容器）**：带 `--docker` 参数执行，自动拉取并配置 `nvcr.io/nvidia/nemo:latest` 容器化后端。
* **组件路径规划**：
  * **模型权重**：`nemotron-3.5-asr-streaming-0.6b.q8_0.gguf`，存放于 `models/NV3.5-ASR-0.6B/`。
  * **动态链接库**：Windows 存放于 `tools/nemo-speech-vulkan/.../bin/nemo_speech_asr_c.dll`；Linux 存放于 `tools/nemo-speech-vulkan/.../lib/libnemo_speech_asr_c.so` 或系统动态库路径。

#### 官方推荐分块档位 (Streaming Chunk Size Matrix)

| 配置档位 | 分块大小 (Chunk Size) | 适用场景 |
| :--- | :--- | :--- |
| **最低延迟 (Lowest latency)** | **80 ms** | 追求极致响应速度，对准确率要求相对宽松的场景 |
| **快速 (Fast)** | **160 ms** | 官方推荐 (Recommended)，在延迟与准确率之间取得良好平衡 |
| **均衡 (Balanced)** | **320 ms** | 模型默认配置，适合大多数通用流式转录任务 |
| **高准确率 (Accurate)** | **560 ms** | 对转录准确率要求更高，可以接受稍高延迟的场景 |
| **最高准确率 (Most accurate)** | **1.12 s (1120 ms)** | 用于离线或对实时性要求不高的高精度转录任务 |

#### 💡 ASR 引擎场景选型指南 (Scenario Recommendation)

根据大量真实影视片源、纪录片与网络直播的实测验证，两大引擎在不同对白风格下各具优势，推荐按内容类型匹配：

| 内容分类 | 典型场景 / 题材样例 | 推荐引擎 | 推荐步长 (Cadence) | 核心体验优势 |
| :--- | :--- | :--- | :--- | :--- |
| **高频口语与碎片对白** | 口语喜剧、情景剧（如《生活大爆炸》/《老友记》）、生活剧、日常 Vlog、网络直播互动 | **🧠 通用大模型 ASR (Qwen3)** | **1000ms (推荐)** / 500ms | 依托端侧 LLM 强语义先验与常识联想能力，**抗吞字、抗俚语倒装、抗抢话打断**，自动脑补连贯语句，整句原子直出体验佳 |
| **官方正式与专业用语** | 经济财经、军事战争、科学探索纪录片、高管演讲、政要发布会、国际新闻资讯 | **⚡ 端侧极速流式 NV (Nemotron)** | **160ms (官方推荐)** / 320ms / 80ms | 依托 FastConformer 端到端流式架构，声学落地极快，**字音与音画完全同频**；在正式严肃语境下，流畅度与丝滑感**远远超越大模型** |

> 📌 **日常使用技巧**：
> * 追快节奏对白的美剧、日剧、日常番或生肉直播时，默认推荐保持 **Qwen3-ASR (1000ms)**；
> * 观看纪录片、新闻专访、严肃大片或演讲发布会时，右键切换至 **Nemotron (160ms / 320ms)**，享受原生极速引擎带来的极致丝滑与零延迟跟手感。

---

## 🚀 快速上手 (Quick Start)

### Windows 快速启动
1. **安装依赖**：
   ```powershell
   git clone https://github.com/2468-liu/translation.git
   cd translation
   pip install -r requirements.txt
   ```
2. **下载并摆放模型**（参照上述模型矩阵放置在 `models/` 目录）。
3. **启动双语字幕桌面应用**：
   ```powershell
   python desktop_app.py
   ```

### Linux (Ubuntu 24.04 LTS / Zorin OS 18) 快速启动
1. **一键环境安装与检测**：
   ```bash
   chmod +x setup_ubuntu.sh start_ubuntu.sh
   ./setup_ubuntu.sh
   ```
2. **运行程序**：
   ```bash
   ./start_ubuntu.sh
   ```
   > 详细 Linux 音频路由配置、双系统 Windows 磁盘挂载以及桌面启动图标制作，请查阅 [README_LINUX.md](README_LINUX.md)。

---

## 🗂️ 项目文件结构

```text
translation/
├── server/                    # 核心服务端与字幕业务管道
│   ├── asr/                   # ASR 引擎包 (Qwen3-ASR, Nemotron, Text-Filter)
│   ├── audio/                 # 音频捕获与规整
│   ├── ui/                    # PyQt6 界面层 (双行滚动字幕窗, 菜单, 启动加载屏)
│   ├── config.py              # 统一路径解析与大小写挂载探针
│   ├── pipeline.py            # VAD 切片、ASR、流式队列与并发翻译调度
│   └── process_loopback.py    # Windows WASAPI 进程树内录接口
├── desktop_app.py             # 桌面程序入口 (单实例互斥, 服务守护, 看门狗)
├── setup_ubuntu.sh            # Ubuntu 24.04 一键安装环境依赖
├── start_ubuntu.sh            # Linux 一键启动脚本
├── README.md                  # 主项目说明文档
├── README_LINUX.md            # Linux 专属详细指导手册
└── tests/                     # 完整自动化单元测试套件 (20+ 项高覆盖率)
```

---

## 🤝 致谢与开源协议声明 (Acknowledgments & Credits)

本项目建立在开源社区众多杰出工作的基石之上，衷心感谢以下核心项目的开源贡献：

* **[llama.cpp](https://github.com/ggml-org/llama.cpp)** (by Georgi Gerganov & ggml team)：提供了极致卓越、轻量高效的跨平台 LLM 推理引擎与多槽位 HTTP 服务架构。
* **[Qwen Team](https://github.com/QwenLM)** (Alibaba Cloud)：打造了业界领先的端到端音频与语音理解大模型 Qwen3-ASR。
* **[Index-Translate](https://github.com/bilibili/Index-1.9B)** (Bilibili Team)：开源了优秀的轻量级高质量影视口语中英翻译大模型。
* **[NVIDIA NeMo](https://github.com/NVIDIA/NeMo)**：为端侧实时流式语音识别提供了出色的 FastConformer-RNNT 架构与 Nemotron 3.5 方案。
* **[Silero VAD](https://github.com/snakers4/silero-vad)**：提供了极致轻巧、抗噪性能卓越的离线端点检测模型。
* **[PyQt6](https://www.riverbankcomputing.com/software/pyqt/)**：为桌面提供了优雅、平滑的硬件加速半透明渲染。
* **[PipeWire](https://pipewire.org/) & [SoundCard](https://github.com/bastibe/SoundCard)**：为 Linux 与跨平台音频捕获提供了底层支持。

### 🤖 AI 协同与开发工具致谢 (AI Assistance & Tooling)

特别鸣谢以下前沿大模型与智能 Agent 开发平台在全流程系统工程中的深度协同：

* **[Google Gemini & Antigravity](https://deepmind.google/technologies/gemini/)** (Google DeepMind)：在端到端系统架构设计、跨平台（Windows WASAPI / Linux PipeWire）底层音频对齐、异常熔断自愈与复杂并发逻辑审查中提供了强悍的 Agentic 结对编程与红队质检支持。
* **[DeepSeek](https://www.deepseek.com/)**：在密集对白时序对齐分析、提示词调优与复杂算法逻辑深度推理中提供了卓越的智能支持。
* **OpenCode & 现代 Agent 生态**：提供了高敏捷度的本地协同与多工具自动化执行工作流。

---

## 📄 License

本项目代码采用 [MIT License](LICENSE) 开源协议。
项目中引用的各预训练模型权重、推理引擎二进制文件均须遵循各自原厂开源许可与商业条款。
