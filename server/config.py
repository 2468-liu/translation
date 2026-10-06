"""Local realtime subtitle service configuration."""

from __future__ import annotations

import json
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

# Audio-native model (gemma-audio branch): one model both hears and translates, so
# whisper and hy-mt2 are no longer loaded. The projector stays on CPU, which is
# what keeps this inside a 4GB card.
AUDIO_MODEL_URL = f"http://{LLAMA_HOST}:{LLAMA_PORT}/v1/chat/completions"
AUDIO_MODEL_NAME = "local"
# The model will answer in English or start reasoning unless told not to. A
# Chinese instruction was measured producing an empty answer after 585 tokens of
# thinking; this wording returned 17 tokens and the translation. Pinned, with a
# test, because getting it wrong is silent.
AUDIO_SYSTEM_PROMPT = (
    "Translate the following English speech into Simplified Chinese. "
    "Output only the Chinese translation."
)
# Gemma is a reasoning model. Left on, it spends the whole token budget thinking
# and returns nothing.
AUDIO_CHAT_TEMPLATE_KWARGS = {"enable_thinking": False}
AUDIO_MAX_TOKENS = 400
# A fixed token budget is not a length limit, it is a wire: a decoder that has
# stopped making progress keeps going until the budget runs out, which is how a
# 3-second fragment came to emit a 40-character clause six times over. Scaling
# the budget to the audio bounds the damage instead of the symptom.
#
# 6 characters per second of speech is roughly what a faithful Chinese rendering
# of English narration runs to; the margin covers a slow passage without letting
# a loop fill the fragment.
AUDIO_CHARS_PER_SECOND = 6.0
AUDIO_TOKEN_MARGIN = 48
# A little above 1. Decoding penalty punishes a token the model has just used,
# which is what a loop is made of. Far enough above 1 to discourage repetition
# and low enough not to distort wording or break proper nouns, which is what a
# higher value does.
AUDIO_REPEAT_PENALTY = 1.05
# Below this share of Chinese letters, the model transcribed the English instead
# of translating it.
#
# Measured on the two real failures and one real translation that quotes an
# English title: 0% Chinese for a pure transcription, 51% for a line that
# translates correctly around a quoted phrase, 100% for clean lines. Judging by
# the share of foreign letters instead inverts this -- the quoted title is 49%
# Latin and would have been thrown away as untranslated.
AUDIO_CHINESE_SHARE_MIN = 0.30

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

# All local inference prefers GPU to reduce round-trip latency. CPU is a fallback
# only when CUDA is unavailable, not the normal path.
ASR_DEVICE = "cuda"
ASR_COMPUTE_TYPE = "float16"
ASR_CPU_THREADS = 4
ASR_BEAM_SIZE = 1
PARTIAL_INTERVAL_MS = 0
MT_DEVICE = "cuda"
MT_COMPUTE_TYPE = "int8"

# VAD
VAD_THRESHOLD = 0.5
VAD_MIN_SPEECH_MS = 250
VAD_MIN_SILENCE_MS = 400
VAD_SPEECH_PAD_MS = 120

# VAD, retuned for the audio-native model.
#
# Two reasons, both measured on samples/blog_3min.mp4 against
# translate_gemma4_sub-E2B-Q4_K_XL:
#
#   * Latency is paid on the whole fragment. First token arrives at roughly
#     300ms + 250ms per second of audio, so a 12s fragment costs ~1.25s before
#     any text appears. A long hold buys nothing.
#   * The model translates what it hears, so cutting mid-sentence costs quality
#     rather than just costing a re-run.
#
# So the closing silence is longer (700ms, was 400ms) to cut at sentence
# boundaries, but the ceiling is much lower (26s) than whisper's 30s window:
# there is no context stitching to fall back on.
GEMMA_MIN_SILENCE_MS = 1000
GEMMA_MAX_UTTERANCE_MS = 26_000
GEMMA_VAD_SPEECH_PAD_MS = 200
# Past this much speech, close even if the speaker has not paused. Keeps a
# rambling speaker from building a fragment whose own latency is unacceptable.
GEMMA_SOFT_CUT_SPEECH_MS = 12_000
GEMMA_SOFT_CUT_SILENCE_MS = 700

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


def resolve_llama_runtime(vram_mb: int | None = None) -> tuple[int, int]:
    """Use aggressive GPU offload on normal machines and partial offload only on 4GB GPUs.

    This project's live footprint is small enough for a 4GB card to run the Q4
    model without blowing up, but it still benefits from cutting a bit of the
    layer count when memory is tight. Machines with more than 4GB keep almost all
    layers inside VRAM to minimize latency.
    """
    if vram_mb is None:
        if RUNTIME_JSON.exists():
            try:
                runtime = json.loads(RUNTIME_JSON.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                runtime = {}
            vram_mb = runtime.get("vram_used_mib_after_load") or runtime.get("fit_target_mib")
        if vram_mb is None:
            vram_mb = 8192
    vram_mb = max(1, int(vram_mb))
    if vram_mb <= 4096:
        return 60, 1024
    return 99, 1536


