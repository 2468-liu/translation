# 🎬 Real-Time Bilingual Caption & Interpretation Desktop System

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Ubuntu%2024.04%20LTS-brightgreen.svg)]()
[![Backend](https://img.shields.io/badge/backend-llama.cpp%20%7C%20CUDA%20Tensor%20Core-orange.svg)]()

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
  * Powered by official NVIDIA CUDA native runtime with Tensor Core acceleration, featuring native step cadences of 320ms (balanced) and 560ms (high-accuracy long sentences).
  * Single-call inference latency is reduced to **~78ms** (3.5x faster than Qwen3, 30x faster than Vulkan), delivering instant word-by-word on-screen output for keynote speeches, live streams, and documentaries.

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

### 3. On-Device Streaming Engine (NVIDIA Nemotron 3.5 - Official CUDA Native Deployment)
* **Full NVIDIA CUDA Native Acceleration**: Vulkan has been completely deprecated in favor of official NeMo-Speech CUDA runtime, unlocking Tensor Cores and native FP16 operators. Single-inference latency drops from 22 seconds to **~78ms** (a 30+ fold speedup), with an RTF of **0.02x (46x real-time throughput)**.
* **NVIDIA Official One-Click Deployment**:
  * **Windows (PowerShell)**:
    ```powershell
    irm https://github.com/NVIDIA/NeMo-Speech.cpp/raw/main/scripts/install.ps1 | iex
    ```
    Automatically deploys to `%LOCALAPPDATA%\Programs\NeMoSpeech`.
  * **Linux (Ubuntu 24.04+ / Debian)**:
    ```bash
    curl -fsSL https://github.com/NVIDIA/NeMo-Speech.cpp/raw/main/scripts/install.sh | sh
    ```
    Automatically deploys to `~/.local/share/nemo-speech`.
  * **Adaptive GUI**: Switch to **⚡ On-Device Fast Streaming NV (Nemotron)** directly via the context menu. If components are missing locally, an interactive download terminal will appear to provision components automatically; running `python scripts/setup_nemotron.py` also detects and reuses existing mounted models without redownloading the 700MB GGUF.
* **Component Path Layout**:
  * **Model Weights**: `nemotron-3.5-asr-streaming-0.6b.q8_0.gguf`, placed in `models/NV3.5-ASR-0.6B/` (automatically reuses mounted Windows/Linux drives).
  * **Dynamic Library**: Windows resolves `%LOCALAPPDATA%\Programs\NeMoSpeech\bin\nemo_speech_asr_c.dll`; Linux resolves `~/.local/share/nemo-speech/lib/libnemo_speech_asr_c.so`.

#### Official Native Streaming Cadence Matrix (FastConformer 80ms Multiples)

FastConformer-RNNT acoustic steps strictly adhere to $80\text{ms}$ ($4 \times 20\text{ms}$ downsampling) multiples. The narrow and fragile 160ms chunking has been deprecated in favor of official standard specifications:

| Profile | Native Step | Context Spec | Recommended Scenarios & Performance |
| :--- | :--- | :--- | :--- |
| **⚖️ Balanced** | **320 ms** | `[56, 4]` | **Official Recommendation & Default**. Covers complete syllables with 350~450ms responsive latency, zero scheduling storms, and natural real-time dialogue rhythm. |
| **🎯 Accurate** | **560 ms** | `[56, 7]` | **Official Long-Sentence Preferred**. Full acoustic context, precise liaison/weak form capture, and substantially lower WER across lectures, videos, and complex sentences. |
| **💎 Most accurate** | **1120 ms (1.12s)** | `[56, 14]` | Maximum acoustic receptive field for offline transcription and archival accuracy. |
| **⚡ Lowest latency** | **80 ms** | `[56, 1]` | Experimental lowest latency mode where higher acoustic error tolerance is acceptable. |
| ~~**⚠️ Fast**~~ | ~~**160 ms**~~ | `[56, 2]` | **Deprecated**. Fragmented acoustic context causes word omissions and hallucinations, and 60+ kernel dispatches per 10s audio trigger CPU/GPU scheduling overhead. |

---

### 📊 Real-World Stress Benchmark (*The Big Bang Theory* 5-Minute Stream + Laugh Track)

Evaluated under Windows 11 host (RTX 3050 Ti Laptop GPU, official NeMo-Speech CUDA runtime) on *The Big Bang Theory* (5 minutes of rapid, continuous dialogue + canned audience laugh bursts, audio `samples/tbbt_5min.wav`):

| Evaluation Metric | NV @ 320ms (CUDA Native)<br>*(Recommended Balanced)* | NV @ 560ms (CUDA Native)<br>*(Recommended Accurate)* | Qwen3-ASR (0.6B)<br>*(Historical Baseline)* | Architectural Takeaways |
| :--- | :--- | :--- | :--- | :--- |
| **Inference Backend** | **NeMo-Speech CUDA Native** | **NeMo-Speech CUDA Native** | HTTP Service (llama-server) | Direct Tensor Core execution, zero HTTP/JSON serialization overhead |
| **300s Audio Total Time** | **6.53 seconds** | **6.53 seconds** | ~300 seconds (server-limited) | **46x Real-Time Throughput** |
| **Real-Time Factor (RTF)** | **0.0218x** | **0.0218x** | 1.001x | Vastly surplus compute; zero backlog during dense speeches |
| **Single Inference Latency p50** | **78.2 ms** | **77.7 ms** | 271.5 ms | **NV is 3.5x faster** |
| **Single Inference Latency p90** | **124.8 ms** | **124.1 ms** | 468.2 ms | **NV is 3.75x faster on long segments** |
| **Single Inference Latency p99** | **147.7 ms** | **148.7 ms** | 558.2 ms | Worst-case latency solidly contained below 150ms |
| **1.0x Real-Time Queue Wait (`qwait`)** | **p50=0.1ms \| Max=0.3ms** | **p50=0.2ms \| Max=0.3ms** | p50=0.1ms \| Max=164.3ms | **Zero queue delay**; frames are immediately dispatched to GPU |
| **Maximum Queue Depth (`qlen Max`)**| **1** (No stacking) | **1** (No stacking) | 1 | Zero frame drops (`Drops = 0`) across entire 5-minute run |
| **Laugh Track & Noise Immunity** | **Zero hallucinations, zero stall**| **Zero hallucinations, clean phrases** | Occasional hallucinations during laugh bursts | Acoustic decoder effectively filters non-vocal applause and laughter |

#### 💡 ASR Engine Scenario Selection Guide (Scenario Recommendation)

| Content Category | Typical Genres & Scenarios | Recommended Engine | Recommended Cadence | Key Experience Advantage |
| :--- | :--- | :--- | :--- | :--- |
| **Colloquial & Fragmented Dialogue** | Sitcoms (*The Big Bang Theory*, *Friends*), drama series, casual vlogs, interactive live streaming | **🧠 General LLM ASR (Qwen3)** | **1000ms (Recommended)** / 500ms | Leveraging strong LLM semantic priors and context reasoning to **handle slurred speech, slang, interruptions, and fragmented words** seamlessly |
| **Formal & Technical Content** | Economics, military, warfare, science documentaries, keynote speeches, official conferences, international news | **⚡ On-Device Fast Streaming NV (Nemotron)** | **320ms (Recommended)** / 560ms | Powered by FastConformer streaming architecture; **significantly more fluid and responsive than LLMs** with real-time acoustic audio-visual synchronization |

> 📌 **Practical Tips**:
> * For fast-paced dialogue, sitcoms, or live casual talk, use **Qwen3-ASR (1000ms)** by default;
> * For documentaries, news broadcasts, technical conferences, or serious historical/military films, switch via right-click to **Nemotron (320ms / 560ms)** for ultra-smooth real-time playback.

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
