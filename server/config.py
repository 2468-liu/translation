"""Local realtime subtitle service configuration."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "models"
SAMPLES_DIR = ROOT / "samples"

# WebSocket (extension <-> Python)
WS_HOST = "127.0.0.1"
WS_PORT = 8765

# llama.cpp OpenAI-compatible server
LLAMA_HOST = "127.0.0.1"
LLAMA_PORT = 8080
LLAMA_URL = f"http://{LLAMA_HOST}:{LLAMA_PORT}/v1/chat/completions"
LLAMA_MODEL = "hy-mt2-1.8b"
USE_NLLB = False

# Paths — filled after ModelScope download / conversion
ASR_MODEL_DIR = MODELS_DIR / "faster-whisper-small"
ASR_FALLBACK_DIR = MODELS_DIR / "faster-whisper-base"
GGUF_DIR = MODELS_DIR / "qwen3.5-4b-mtp"
GGUF_FALLBACK_DIR = MODELS_DIR / "qwen2.5-3b-gguf"
NLLB_DIR = MODELS_DIR / "nllb-200-distilled-600M"
MT_DIR = MODELS_DIR / "nllb-600m-ct2"
SILERO_ONNX = MODELS_DIR / "silero_vad.onnx"

# Audio
SAMPLE_RATE = 16000
CHUNK_MS = 100
BYTES_PER_SAMPLE = 2  # int16

# ASR shares the GPU with the small translation model. The 4B LLM stays off.
ASR_DEVICE = "cuda"
ASR_COMPUTE_TYPE = "float16"
ASR_CPU_THREADS = 4
ASR_BEAM_SIZE = 1
PARTIAL_INTERVAL_MS = 250

# VAD
VAD_THRESHOLD = 0.5
VAD_MIN_SPEECH_MS = 250
VAD_MIN_SILENCE_MS = 400
VAD_SPEECH_PAD_MS = 120

# Translation
TRANSLATE_TEMPERATURE = 0.2
TRANSLATE_MAX_TOKENS = 256
CONTEXT_SENTENCES = 2
PARTIAL_STABLE_REPEAT = 1  # translate the first partial; later growth follows after it finishes

# llama.cpp GPU layers — probe script writes actual value into runtime.json
LLAMA_NGL_DEFAULT = 99
LLAMA_CTX = 1024
LLAMA_THREADS = 4
VRAM_SAFE_MB = 3600

RUNTIME_JSON = ROOT / "runtime.json"

LLAMA_SERVER_CANDIDATES = [
    Path(r"C:\Users\20693\.lmstudio\extensions\backends\llama.cpp-win-x86_64-nvidia-cuda12-avx2-2.47.0\llama-server.exe"),
    Path(r"C:\Users\20693\.lmstudio\extensions\backends\llama.cpp-win-x86_64-nvidia-cuda12-avx2-2.33.0\llama-server.exe"),
]


def find_llama_server() -> Path | None:
    for path in LLAMA_SERVER_CANDIDATES:
        if path.exists():
            return path
    return None


def find_gguf() -> Path | None:
    for folder, pattern in (
        (GGUF_DIR, "*UD-Q4_K_XL*.gguf"),
        (GGUF_DIR, "*UD-Q3_K_XL*.gguf"),
        (GGUF_FALLBACK_DIR, "*q4_k_m*.gguf"),
    ):
        if not folder.exists():
            continue
        found = sorted(folder.glob(pattern))
        if found:
            return found[0]
    return None
