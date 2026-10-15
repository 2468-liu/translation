# 🎬 Real-Time Bilingual Caption & Interpretation Desktop System

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Ubuntu%2024.04%20LTS-brightgreen.svg)]()
[![Backend](https://img.shields.io/badge/backend-llama.cpp%20%7C%20Vulkan-orange.svg)]()

**Ultra-low latency, 100% offline, privacy-first bilingual live subtitle and interpretation desktop app.**  
Supports Windows WASAPI process-tree loopback capture and Linux PipeWire/PulseAudio 0dB unattenuated physical loopback.  
*Zero cloud API keys required. Zero audio file extraction needed. Instant play, instant caption.*

[ 简体中文 ](README.md) | [ English ](README_EN.md)

</div>

---

## 🌟 Key Architectural Innovations

### 1. Dual-Mode Heterogeneous ASR Engine
* **Cinema-Grade Semantic Block Output (Qwen3-ASR 0.6B)**:
  * Recommended default for movies, TV series, and podcasts with a 1000ms chunk cadence.
  * Driven by punctuation boundary detection and Silero VAD breath-pause slicing. Utterances are emitted as complete grammatical sentences, eliminating flickering word-by-word streaming and speech truncation in dense dialogues.
* **Ultra-Fast Streaming Interpretation (NVIDIA Nemotron 3.5 0.6B)**:
  * Powered by native Vulkan C API runtime with a 160ms step cadence.
  * Delivers word-by-word instant on-screen output, ideal for live keynote speeches and international conference calls.

### 2. High-Throughput Concurrent Translation on Consumer GPUs
* Equipped with **Index-Translate 1.9B (Q4_K_M)**, fine-tuned specifically for colloquial dialogues, film idioms, and natural expressions.
* Backed by `llama-server` dual/triple-slot parallel execution.
* Total VRAM footprint for both ASR and Translation models is only **~3.2GB**. Smoothly runs at full speed on NVIDIA RTX 3050 / 3060 / 4060 laptops while watching 4K movies or playing games.

### 3. Dual-Platform True Lossless Loopback Capture
* **Windows (Native WASAPI Process-Tree Loopback)**:
  * Captures audio strictly from the target media player or browser process hierarchy.
  * Immune to background microphone noises, keyboard clicks, or notification chimes from other apps.
* **Linux (Ubuntu 24.04 / Zorin OS 18)**:
  * Natively interfaces with PipeWire / PulseAudio monitor streams using unattenuated 0dB physical loopback.
  * Strictly rejects dirty post-processing software amplification (AGC), preserving original acoustic SNR and physical dynamic range.

### 4. Cinema-Grade Visual UX & Self-Healing Guards
* **Chromium-Grade Dual-Line Smooth Rolling Buffer**:
  * Adopts Google Live Caption's anti-jitter algorithmic logic (`grow` / `hold` / `drop`). Once the preceding utterance finalizes, it smoothly shifts into the upper anchor line while the active streaming text emerges below.
* **Dynamic Audio Normalizer**:
  * Front-end adaptive soft limiter and noise-floor squelch. Film explosions and gunshots never distort, while whispers and quiet conversations are crystal-clear.
* **Interactive Mount Picker & Engine Rollback Guard**:
  * Prompts an intuitive dark-themed modal on the splash screen if models reside in custom mounts (e.g., uppercase `/mnt/DATA`).
  * Automatically rolls back to Qwen3-ASR if Nemotron fails or is chosen on Linux, preventing restart crash loops.

---

## 📥 Model & Engine Download Matrix

In accordance with open-source community standards, **large neural network weights and third-party compiled binaries are excluded from this repository**. Please download the required components using the links below:

### 1. Essential Models (Place inside `models/` directory)

| Component | Quantization / Format | Destination Path | Official Download Source (HuggingFace / ModelScope / GitHub) |
| :--- | :--- | :--- | :--- |
| **Silero VAD** | `silero_vad.onnx` (~4 MB) | `models/silero_vad.onnx` | [GitHub Direct Download](https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx) |
| **Index-Translate** | `index-translate-q4_k_m.gguf` | `models/Index-Translate/` | [HuggingFace](https://huggingface.co/IndexTeam/Index-1.9B-Chat) / [ModelScope](https://www.modelscope.cn/models/IndexTeam/Index-1.9B-Chat) |
| **Qwen3-ASR Model** | `qwen3-asr-0.6b.gguf` | `models/Qwen-ASR-0.6B/` | [HuggingFace](https://huggingface.co/Qwen/Qwen3-ASR-0.6B-GGUF) / [ModelScope](https://www.modelscope.cn/models/Qwen/Qwen3-ASR-0.6B-GGUF) |
| **Qwen3-ASR Projector**| `mmproj-qwen3-asr-0.6b.gguf` | `models/Qwen-ASR-0.6B/` | Located in the same repository as Qwen3-ASR |

> 💡 **One-Click Download**: Run `python scripts/download_models.py` in the root folder to automatically fetch Silero VAD and print download links for the LLMs.

### 2. Runtime Server Engine (`llama-server`)

The translation service (port 8080) and Qwen3-ASR service (port 8099) are powered by the official `llama-server`:
* **Windows**: Download [llama.cpp Official Releases](https://github.com/ggml-org/llama.cpp/releases) (build 11630+, e.g. `llama-bxxxx-bin-win-cuda-cu12.4-x64.zip`), extract `llama-server.exe`, and ensure it is in your system `PATH` or in `tools/llama-server/`.
* **Linux (Ubuntu 24.04)**: Verify with `llama-server --version`. If not present, download the corresponding Linux binary from `llama.cpp` releases or utilize LM Studio.

### 3. Optional On-Device Streaming Engine (NVIDIA Nemotron 3.5 - Windows Only)
* **Model Weights**: `nemotron-3.5-asr-streaming-0.6b.q8_0.gguf`, placed in `models/NV3.5-ASR-0.6B/`.
* **Vulkan C Library**: `nemo_speech_asr_c.dll`, placed in `tools/nemo-speech-vulkan/.../bin`.

#### Official Recommended Streaming Chunk Size Matrix

| Profile | Chunk Size | Recommended Scenarios |
| :--- | :--- | :--- |
| **Lowest latency** | **80 ms** | Ultra-low latency requirements with relaxed accuracy demands |
| **Fast** | **160 ms** | **Official Recommendation**, optimal balance between latency and accuracy |
| **Balanced** | **320 ms** | Model default configuration, suitable for general streaming transcription |
| **Accurate** | **560 ms** | Higher transcription accuracy where moderate latency is acceptable |
| **Most accurate** | **1.12 s (1120 ms)** | High-precision transcription where real-time latency is secondary |

---

## 🚀 Quick Start

### Windows Setup
1. **Clone repository & install dependencies**:
   ```powershell
   git clone https://github.com/2468-liu/translation.git
   cd translation
   pip install -r requirements.txt
   ```
2. **Download & arrange models** as described in the table above.
3. **Launch Desktop App**:
   ```powershell
   python desktop_app.py
   ```

### Linux (Ubuntu 24.04 LTS / Zorin OS 18) Setup
1. **One-click environment configuration**:
   ```bash
   chmod +x setup_ubuntu.sh start_ubuntu.sh
   ./setup_ubuntu.sh
   ```
2. **Run application**:
   ```bash
   ./start_ubuntu.sh
   ```
   > For detailed Linux audio routing, mounting Windows NTFS drives, and creating desktop shortcuts, refer to [README_LINUX.md](README_LINUX.md).

---

## 🗂️ Project Structure

```text
translation/
├── server/                    # Core backend and caption processing pipeline
│   ├── asr/                   # ASR Engine package (Qwen3-ASR, Nemotron, Filters)
│   ├── audio/                 # Audio capture & dynamic enhancer
│   ├── ui/                    # PyQt6 UI (Dual-line rolling window, context menu, splash)
│   ├── config.py              # Configuration, model discovery & mount normalization
│   ├── pipeline.py            # VAD slicing, streaming queue, concurrent MT dispatch
│   └── process_loopback.py    # Windows WASAPI process-tree loopback wrapper
├── desktop_app.py             # Desktop application entry point & watchdog
├── setup_ubuntu.sh            # Ubuntu 24.04 automated setup script
├── start_ubuntu.sh            # Linux one-click launch script
├── README.md                  # Project overview (Chinese)
├── README_EN.md               # Project overview (English)
├── README_LINUX.md            # Comprehensive Linux user manual
└── tests/                     # Test suite (20+ automated unit & integration tests)
```

---

## 🤝 Acknowledgments & Credits

This project is built upon the incredible work of the global open-source community:

* **[llama.cpp](https://github.com/ggml-org/llama.cpp)** (by Georgi Gerganov & ggml team): For the premier lightweight, cross-platform LLM inference engine and multi-slot HTTP server architecture.
* **[Qwen Team](https://github.com/QwenLM)** (Alibaba Cloud): For the groundbreaking multimodal audio-language model Qwen3-ASR.
* **[Index-Translate](https://github.com/bilibili/Index-1.9B)** (Bilibili Team): For the state-of-the-art lightweight bilingual dialogue translation model.
* **[NVIDIA NeMo](https://github.com/NVIDIA/NeMo)**: For the high-efficiency on-device FastConformer-RNNT architecture and Nemotron 3.5.
* **[Silero VAD](https://github.com/snakers4/silero-vad)**: For the ultra-fast, robust neural Voice Activity Detector.
* **[PyQt6](https://www.riverbankcomputing.com/software/pyqt/)**: For high-performance hardware-accelerated translucent GUI rendering.
* **[PipeWire](https://pipewire.org/) & [SoundCard](https://github.com/bastibe/SoundCard)**: For modern low-latency Linux and cross-platform audio loopback support.

### 🤖 AI Assistance & Tooling Credits

Special gratitude to the following foundation models and agentic developer platforms for their profound collaboration throughout architecture design, low-level debugging, and automated quality assurance:

* **[Google Gemini & Antigravity](https://deepmind.google/technologies/gemini/)** (Google DeepMind): For exceptional agentic pair-programming, driving dual-platform (Windows WASAPI / Linux PipeWire) audio alignment, self-healing fault-tolerance, and adversarial concurrency audits.
* **[DeepSeek](https://www.deepseek.com/)**: For deep-reasoning support in dense dialogue cadence synchronization, algorithmic deduction, and prompt optimization.
* **OpenCode & Modern Agent Tooling**: For providing an agile, highly automated local execution and verification environment.

---

## 📄 License

The source code of this project is licensed under the [MIT License](LICENSE).  
All referenced model weights, runtime binaries, and datasets remain subject to their respective original licenses and commercial terms.
