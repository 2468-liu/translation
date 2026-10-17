"""Local realtime subtitle service configuration."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    """A threshold that a sweep can set without editing this file.

    Sweeps used to rewrite the constants here, which meant three things went
    wrong at once: the working tree went dirty so every run's stamp read
    "+dirty" and no two runs could be compared, eight vad tests that read these
    constants failed while a sweep was in flight, and a run could inherit the
    previous arm's value without anything recording it.
    """
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


if getattr(sys, "frozen", False):
    app_dir = Path(sys.executable).resolve().parent
    ROOT = app_dir
else:
    ROOT = Path(__file__).resolve().parents[1]


def load_desktop_config() -> dict:
    """Load user configuration from desktop_config.json if present."""
    paths = []
    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).resolve().parent
        paths.append(app_dir / "desktop_config.json")
    paths.append(Path.cwd() / "desktop_config.json")
    paths.append(Path("D:/TranslationDesktop/desktop_config.json"))
    for p in paths:
        if p.exists():
            try:
                import json
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return {}


def save_desktop_config(cfg: dict) -> None:
    """Save user configuration to desktop_config.json."""
    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).resolve().parent
    else:
        app_dir = Path.cwd()
    p = app_dir / "desktop_config.json"
    try:
        import json
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Config] Failed to save desktop_config.json: {e}")


def get_vad_backend() -> str:
    """Get currently configured VAD backend ('silero' or 'firered')."""
    cfg = load_desktop_config()
    choice = cfg.get("vad_backend")
    if not choice:
        choice = os.environ.get("TRANSLATION_VAD_BACKEND", "silero")
    return str(choice).strip().lower()


def set_vad_backend(choice: str) -> None:
    """Persist VAD backend selection to desktop_config.json."""
    c = str(choice).strip().lower()
    if c not in ("silero", "firered"):
        c = "silero"
    cfg = load_desktop_config()
    cfg["vad_backend"] = c
    save_desktop_config(cfg)


def _has_valid_model_suite(dir_path: Path) -> bool:
    if not dir_path.exists() or not dir_path.is_dir():
        return False
    # Check flat gguf files directly in dir_path
    try:
        for f in dir_path.glob("*.gguf"):
            if f.stat().st_size > 50 * 1024 * 1024:
                return True
    except Exception:
        pass
    # Check standard subdirectories
    for sub in ("NV3.5-ASR-0.6B", "Qwen-ASR-0.6B", "Index-Translate", "hy-mt2-1.8b"):
        sub_dir = dir_path / sub
        if sub_dir.exists() and sub_dir.is_dir():
            try:
                for f in sub_dir.glob("*.gguf"):
                    if f.stat().st_size > 50 * 1024 * 1024:
                        return True
            except Exception:
                pass
    return False


def normalize_models_dir(path: str | Path) -> Path:
    """Normalize a user-selected or configured models directory path.
    Handles cases where user selects:
    1. An inner subfolder (e.g. Index-Translate, Qwen-ASR-0.6B, NV3.5-ASR-0.6B) -> step up to parent
    2. A project directory containing a 'models' directory -> step into 'models'
    3. Flat model directory or standard models directory -> as-is
    """
    p = Path(path).resolve()
    if not p.exists():
        return p

    # If user selected an inner model folder, step up to parent
    if p.name in ("Index-Translate", "Qwen-ASR-0.6B", "NV3.5-ASR-0.6B", "hy-mt2-1.8b", "firered_vad"):
        parent = p.parent
        if parent.exists() and (
            any((parent / sub).exists() for sub in ("Index-Translate", "Qwen-ASR-0.6B", "NV3.5-ASR-0.6B", "hy-mt2-1.8b"))
            or any(f.stat().st_size > 50 * 1024 * 1024 for f in parent.glob("*.gguf"))
        ):
            return parent

    # If user selected repo/app root which contains a valid 'models' directory
    if (p / "models").is_dir() and _has_valid_model_suite(p / "models"):
        return p / "models"

    return p


def resolve_models_dir() -> Path:
    """Resolve models directory. Checks TRANSLATION_MODELS_DIR, desktop_config.json,
    case-insensitive Linux mount points (/mnt/DATA, /mnt/d, /DATA, /media/*), and local directories.
    """
    # 0. User environment variable (highest priority)
    env_dir = os.environ.get("TRANSLATION_MODELS_DIR") or os.environ.get("MODELS_DIR")
    if env_dir and Path(env_dir).exists():
        return Path(env_dir).resolve()

    # 1. User custom config in desktop_config.json
    cfg = load_desktop_config()
    custom = cfg.get("models_dir")
    if custom and Path(custom).exists():
        return Path(custom).resolve()

    candidates: list[Path] = [
        ROOT / "models",
        Path.cwd() / "models",
        Path("D:/TranslationDesktop/models"),
        Path("D:/models"),
    ]
    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).resolve().parent
        candidates.insert(0, app_dir / "models")

    # 2. Linux / WSL2 cross-platform mount point candidates (casing & mount variations)
    linux_mount_roots = [
        Path("/mnt/d"),
        Path("/mnt/D"),
        Path("/mnt/DATA"),
        Path("/mnt/data"),
        Path("/DATA"),
        Path("/data"),
    ]
    # Scan /media and /run/media for automounts (e.g. /media/$USER/DATA or /media/DATA)
    for media_root in [Path("/media"), Path("/run/media")]:
        if media_root.exists():
            try:
                for entry in media_root.iterdir():
                    if entry.is_dir():
                        linux_mount_roots.append(entry)
                        # Check sub-mounts like /media/username/DATA
                        try:
                            for sub in entry.iterdir():
                                if sub.is_dir():
                                    linux_mount_roots.append(sub)
                        except Exception:
                            pass
            except Exception:
                pass

    for mroot in linux_mount_roots:
        candidates.append(mroot / "TranslationDesktop" / "models")
        candidates.append(mroot / "models")
        candidates.append(mroot)

    candidates.extend([
        Path.home() / "models",
        Path.home() / ".cache" / "translation" / "models",
        Path("/opt/translation/models"),
        Path("/usr/local/share/translation/models"),
    ])
    if sys.platform != "win32":
        try:
            for wsl_user in Path("/mnt/c/Users").glob("*"):
                if wsl_user.is_dir() and not wsl_user.name.startswith((".", "Default", "Public", "All Users")):
                    candidates.append(wsl_user / "Desktop" / "translation" / "models")
                    candidates.append(wsl_user / "TranslationDesktop" / "models")
        except Exception:
            pass

    # Phase 1: prioritize directories containing full LLM/ASR model suites with valid weights
    for c in candidates:
        if _has_valid_model_suite(c):
            return c.resolve()

    # Phase 2: directory with VAD or standalone gguf
    for c in candidates:
        if c.exists() and ((c / "silero_vad.onnx").exists() or list(c.glob("*.gguf"))):
            return c.resolve()

    for c in candidates:
        if c.exists():
            return c.resolve()

    return candidates[0]


MODELS_DIR = resolve_models_dir()


def set_models_dir(path: str | Path) -> None:
    """Set custom models directory and update all model paths."""
    global MODELS_DIR, SILERO_ONNX, FIRERED_ONNX, QWEN3_ASR_MODEL, QWEN3_ASR_MMPROJ
    p = normalize_models_dir(path)
    MODELS_DIR = p

    # Dynamic resolution for VAD models
    if (MODELS_DIR / "silero_vad.onnx").exists():
        SILERO_ONNX = MODELS_DIR / "silero_vad.onnx"
    elif (MODELS_DIR / "silero" / "silero_vad.onnx").exists():
        SILERO_ONNX = MODELS_DIR / "silero" / "silero_vad.onnx"
    else:
        SILERO_ONNX = MODELS_DIR / "silero_vad.onnx"

    if (MODELS_DIR / "firered_vad" / "firered_vad.onnx").exists():
        FIRERED_ONNX = MODELS_DIR / "firered_vad" / "firered_vad.onnx"
    elif (MODELS_DIR / "firered_vad.onnx").exists():
        FIRERED_ONNX = MODELS_DIR / "firered_vad.onnx"
    else:
        FIRERED_ONNX = MODELS_DIR / "firered_vad" / "firered_vad.onnx"

    # Dynamic resolution for Qwen ASR model (subfolder or flat)
    qwen_candidate = MODELS_DIR / "Qwen-ASR-0.6B" / "Qwen3-ASR-0.6B-Q8_0.gguf"
    if not qwen_candidate.exists() and (MODELS_DIR / "Qwen3-ASR-0.6B-Q8_0.gguf").exists():
        qwen_candidate = MODELS_DIR / "Qwen3-ASR-0.6B-Q8_0.gguf"
    elif not qwen_candidate.exists() and MODELS_DIR.exists():
        for g in MODELS_DIR.glob("*.gguf"):
            if "qwen" in g.name.lower() and "mmproj" not in g.name.lower() and g.stat().st_size > 50 * 1024 * 1024:
                qwen_candidate = g
                break
    QWEN3_ASR_MODEL = qwen_candidate

    # Dynamic resolution for Qwen mmproj
    mmproj_candidate = MODELS_DIR / "Qwen-ASR-0.6B" / "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf"
    if not mmproj_candidate.exists() and (MODELS_DIR / "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf").exists():
        mmproj_candidate = MODELS_DIR / "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf"
    elif not mmproj_candidate.exists() and MODELS_DIR.exists():
        for g in MODELS_DIR.glob("*.gguf"):
            if "mmproj" in g.name.lower() and g.stat().st_size > 10 * 1024 * 1024:
                mmproj_candidate = g
                break
    QWEN3_ASR_MMPROJ = mmproj_candidate

    cfg = load_desktop_config()
    cfg["models_dir"] = str(p)
    save_desktop_config(cfg)


# WebSocket (extension <-> Python)
WS_HOST = "127.0.0.1"
WS_PORT = 8765

# llama.cpp OpenAI-compatible server
LLAMA_HOST = "127.0.0.1"
LLAMA_PORT = 8080
LLAMA_URL = f"http://{LLAMA_HOST}:{LLAMA_PORT}/v1/chat/completions"
LLAMA_MODEL = "Index-Translate-2B"

# Audio-native model (gemma-audio branch): one model both hears and translates, so
# whisper and hy-mt2 are no longer loaded.
#
# The projector stays on the CPU because that is where it is faster, not because
# it is the only place it fits -- it would fit on the GPU with room over, and
# measured there it is 3.7x slower (first packet 3442ms against 920ms). The
# encoder's prefill competes with the main model for one card; on the CPU the two
# overlap. See scripts/start.ps1 for the full numbers and scripts/sweep_mmproj.ps1
# to reproduce them.
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
#
# large-v3-turbo, not small. Chosen for the noisy-material case (film dialogue
# under music) where small misses speech. Measured on this machine, same
# backend, 205.8s of film audio:
#   small   +649 MiB loaded,  +731 MiB after a transcribe
#   turbo  +2107 MiB loaded, +2243 MiB after a transcribe
if hasattr(sys, "_MEIPASS") and (Path(sys._MEIPASS) / "models" / "silero_vad.onnx").exists():
    SILERO_ONNX = Path(sys._MEIPASS) / "models" / "silero_vad.onnx"
else:
    SILERO_ONNX = MODELS_DIR / "silero_vad.onnx"

if hasattr(sys, "_MEIPASS") and (Path(sys._MEIPASS) / "models" / "firered_vad" / "firered_vad.onnx").exists():
    FIRERED_ONNX = Path(sys._MEIPASS) / "models" / "firered_vad" / "firered_vad.onnx"
elif (MODELS_DIR / "firered_vad" / "firered_vad.onnx").exists():
    FIRERED_ONNX = MODELS_DIR / "firered_vad" / "firered_vad.onnx"
elif (ROOT / "models" / "firered_vad" / "firered_vad.onnx").exists():
    FIRERED_ONNX = ROOT / "models" / "firered_vad" / "firered_vad.onnx"
else:
    FIRERED_ONNX = MODELS_DIR / "firered_vad" / "firered_vad.onnx"

# Audio
SAMPLE_RATE = 16000

ASR_DEVICE = "cuda"
ASR_COMPUTE_TYPE = "float16"
ASR_CPU_THREADS = 4
# Which recogniser runs: "qwen3" or "nemotron".
ASR_BACKEND = os.environ.get("TRANSLATION_ASR_BACKEND", "nemotron").strip().lower()
QWEN3_ASR_MODEL = MODELS_DIR / "Qwen-ASR-0.6B" / "Qwen3-ASR-0.6B-Q8_0.gguf"
QWEN3_ASR_MMPROJ = MODELS_DIR / "Qwen-ASR-0.6B" / "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf"
QWEN3_ASR_URL = os.environ.get("TRANSLATION_QWEN3_ASR_URL", "http://127.0.0.1:8099")
QWEN3_ASR_SLOTS = _env_int("TRANSLATION_QWEN3_SLOTS", 2)
ASR_SLOTS = _env_int("TRANSLATION_ASR_SLOTS", QWEN3_ASR_SLOTS)

# Generation cap for Qwen3-ASR, in tokens.
#
# This is a bound on a failure, not a tuning knob. On samples/lecture_mix.wav 5 of 149
# calls never emitted an end token and generated until llama.cpp ran out of context:
# eval 3912 to 3951 tokens against a normal 7 to 46, 30 to 33 seconds per call, and
# 184 prompt tokens with 184 + 3912 = 4096 exactly, which is that server's -c. The
# text reached the screen and 74 seconds passed with nothing after "你真的，你真的".
#
# Measured normal output on that material peaks at 65 tokens for a 14s clip, and 33 to
# 39 on the ones the pipeline actually sends, so 128 leaves roughly three times the
# headroom the longest legitimate transcription needed. It is a round number above
# that margin rather than a fitted value, because the trigger is not understood and a
# tight cap would silently drop real speech if the trigger turns out to be common.
#
# Responses that hit the cap are dropped whole, not trimmed: see Qwen3AsrEngine.
QWEN3_ASR_MAX_TOKENS = _env_int("TRANSLATION_QWEN3_ASR_MAX_TOKENS", 128)
# How often recognition re-reads the utterance while it is still growing, which is how
# often the caption can be revised. 250 was the default for the whole project and it is
# what every earlier number was measured at.
#
# 750 now ships, because the two effects pull in opposite directions and on this
# material the revision count was the one that hurt. Measured on samples/blog_3min.wav
# at 250: 215 recognitions over 49 utterances, 4.39 per clause, and 112 caption
# rewrites. At 750 on samples/lecture_mix.wav: 136 recognitions, and the transcript
# lengths confirm the stream is no longer fragmented -- median 19 characters, 71% over
# 10, with only 4.4% under 5. The cost is the first line arriving later, which is why
# this is still adjustable in the extension rather than removed: 250 measured 382ms
# first-translation-to-paint against 750's 434ms on the same material, a 52ms
# difference for 3x fewer revisions.
#
# The engine matters here more than the number. turbo at 823ms per call cannot keep up
# Nemotron 3.5 ASR uses 80ms chunk multiples. The official recommended default
# for streaming subtitles is 160ms ([56, 1], 1 right-context frame).
PARTIAL_INTERVAL_MS = _env_int("TRANSLATION_PARTIAL_INTERVAL_MS", 160)

# --------------------------------------------------------------- llama.cpp 版本
#
# One variable, two states: unset means "the newest build installed on this machine",
# and set means exactly that version. The decision to keep it a value rather than a
# command-line flag is that it had already become three flags in two files that
# disagreed -- start.ps1 defaulted to newest behind -UsePinned, resolve_llama.ps1
# defaulted to a hardcoded 2.47.0 behind -AutoLatest, and replay.ps1 called the second
# one. So measurements landed on 2.47.0 not because anything pinned them but because
# the measuring path and the running path resolved the same question differently, and
# the next edit to either would have made every earlier number incomparable.
#
# Resolved by scripts/resolve_llama.ps1, which is the only reader: an explicit -Version,
# then this variable, then runtime.json's llama_version, then newest installed. A named
# version that is not installed is an error, never a fallback -- see MEASUREMENT_LLAMA_VERSION.
LLAMA_VERSION = os.environ.get("TRANSLATION_LLAMA_VERSION", "").strip()

# The build every number in REPORT.md was measured on. replay.ps1 pins this rather than
# reading LLAMA_VERSION, because a measurement has to be comparable to the last
# measurement, not to whatever the machine currently has installed. Pinning from the
# machine's own setting would reintroduce exactly the drift the pin exists to stop, one
# level up: a new build gets installed, the machine's setting follows it, and the
# difference between two arms becomes the difference between two llama versions with
# nothing in the logs to say so.
#
# Shipping still floats (LLAMA_VERSION unset), so this constant is used by the
# measuring path only. See AGENT.md section 5, rule nine.
MEASUREMENT_LLAMA_VERSION = "2.47.0"

# The chains, and the default, live here rather than in runtime.json.
#
# runtime.json is machine-local and gitignored -- it holds absolute paths to this
# machine's llama builds and that is exactly why it is not versioned. Putting the chain
# table there made a fresh clone have no default at all, and eleven tests that read it
# failed on a clean checkout: the defaults for a shipped configuration cannot live in a
# file that is not shipped.
#
# A chain is the whole pipeline, not a recogniser. gemma-e2e is the audio-native path
# where one model both hears and translates, which removes the translator process
# entirely -- calling that "an ASR engine" would misdescribe it and would make a fourth
# option look like a fourth engine.
#
# `status` is what stops an unimplemented chain from being offered as if it worked.
DEFAULT_CHAIN = "qwen3-in"
CHAINS = {
    "qwen3-in": {
        "label": "Qwen3-ASR 识别 + Index 影视翻译 (原子直出)",
        "status": "implemented, measured, adopted default",
        "asr_backend": "qwen3",
        "output": "block",
        "qwen3_port": 8099,
        "measured": "Index-Translate 2B Q4_K_M + Qwen3-ASR 0.6B Q8_0; BLEU 20.38, TTFT 20.5ms, Atomic Clause Output",
        "caveat": "atomic finished clause direct output, zero flicker, 1 paint per clause",
        "translator_weights": "Index-Translate-2B.Q4_K_M.gguf",
    },
    "nv-in": {
        "label": "Nemotron 3.5 ASR 识别 + Index 翻译 (整句原子直出)",
        "status": "implemented, measured, speed alternative",
        "asr_backend": "nemotron",
        "output": "block",
        "qwen3_port": 0,
        "measured": "Nemotron 3.5 ASR Q8_0 (0.6B) + Vulkan GPU; step latency 62.8ms, RTF 0.392, 0 queue backlog",
        "caveat": "atomic clause direct output, zero flicker, 1 paint per clause",
        "translator_weights": "Index-Translate-2B.Q4_K_M.gguf",
    },
    "gemma-e2e": {
        "label": "音频直出（一个模型同时听和译）",
        "status": "implemented, measured, rejected on music-backed material",
        "asr_backend": None,
        # 输出方式归并到路线：流式。交付本身就是整句（没有可分的 partial），
        # 标点分块在这条路上没有可切的东西。
        "output": "stream",
        "qwen3_port": 0,
        "measured": "works on clean narration: blog_3min, 12.48s clip, four sentences "
                    "all correct. Degrades on music: er_3min mostly right, e_3min broken "
                    "-- \"I reckon we should call it a day.\" heard by whisper and "
                    "misheard by the Gemma4 audio encoder as 大鸟",
        "caveat": "rejected on this material, not on the card. REPORT 25.3 ruled out MTP, "
                  "layer split, projector placement, three prompts, five sample rates, "
                  "five clip durations and five gains by single-variable comparison; the "
                  "boundary is the model's audio understanding on speech under music "
                  "(crest 18.5dB blog / 13.4dB er / 12.9dB e_3min), not configuration",
    },
}
# Whether start.ps1 can start it, which is not the same question as whether it is any
# good. gemma-e2e can be started and was measured; it lost on material with music under
# the dialogue. It is excluded here only because start.ps1 has no branch for it, and the
# reason it is excluded is a missing branch rather than a missing implementation --
# the pipeline path exists (server/pipeline.py _run_audio_native) and start.ps1 has a
# 132-line gemma4 branch that this switch does not reach.
STARTABLE_CHAINS = tuple(n for n, s in CHAINS.items() if s["status"] != "not implemented")
# Tested at least once, which is every entry here. Kept separate from STARTABLE_CHAINS
# because a chain that was measured and rejected must not read as one that was never
# tried: that is the claim I made about gemma-e2e in the first version of this table, and
# the user had it in front of them.
MEASURED_CHAINS = tuple(CHAINS)
QWEN3_ASR_PORT = CHAINS["qwen3-in"]["qwen3_port"]

# VAD
VAD_THRESHOLD = _env_float("TRANSLATION_VAD_THRESHOLD", 0.5)
VAD_SUSTAIN_THRESHOLD = _env_float("TRANSLATION_VAD_SUSTAIN_THRESHOLD", 0.18)
VAD_PREROLL_MAX_MS = _env_int("TRANSLATION_VAD_PREROLL_MAX_MS", 300)
VAD_MIN_SPEECH_MS = _env_int("TRANSLATION_VAD_MIN_SPEECH_MS", 250)
VAD_MERGE_WINDOW_MS = _env_int("TRANSLATION_VAD_MERGE_WINDOW_MS", 0)
VAD_MIN_SILENCE_MS = _env_int("TRANSLATION_VAD_MIN_SILENCE_MS", 550)
VAD_SPEECH_PAD_MS = _env_int("TRANSLATION_VAD_SPEECH_PAD_MS", 250)
SOFT_CUT_SPEECH_MS = _env_int("TRANSLATION_SOFT_CUT_SPEECH_MS", 5000)
SOFT_CUT_SILENCE_MS = _env_int("TRANSLATION_SOFT_CUT_SILENCE_MS", 350)

# Adaptive Audio Enhancer for Cinema & Dynamic Dialogue
AUDIO_ENHANCE_ENABLED = os.environ.get("TRANSLATION_AUDIO_ENHANCE", "1").strip().lower() not in ("0", "false", "no")

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
GEMMA_MIN_SILENCE_MS = _env_int("TRANSLATION_GEMMA_MIN_SILENCE_MS", 1000)
GEMMA_MAX_UTTERANCE_MS = 26_000
GEMMA_VAD_SPEECH_PAD_MS = 200
# Past this much speech, close even if the speaker has not paused. Keeps a
# rambling speaker from building a fragment whose own latency is unacceptable.
GEMMA_SOFT_CUT_SPEECH_MS = 12_000
GEMMA_SOFT_CUT_SILENCE_MS = 700

# Translation: Index-Translate / Hy-MT2 StreamRevise recommends greedy decoding (temperature=0.0)
# to prevent subtitles flickering across incremental partial updates.
TRANSLATE_TEMPERATURE = _env_float("TRANSLATION_TEMPERATURE", 0.0)
TRANSLATE_MAX_TOKENS = _env_int("TRANSLATION_MAX_TOKENS", 256)
TRANSLATION_SLOTS = _env_int("TRANSLATION_SLOTS", 3)
CONTEXT_SENTENCES = 2

# ---------------------------------------------------------------- audio capture
# Where the audio comes from.
#
# "loopback" captures what the machine is playing, through WASAPI, in this
# process. "socket" takes PCM from the extension's tab capture.
#
# Exactly one of them feeds the splitter, and that exclusivity is the fix rather
# than a preference: both used to run at once, each pushing 100ms frames into the
# same Session, so the VAD saw the clip and the machine's output interleaved. On
# samples/e_3min.wav that turned 32 clean utterances into 48 fragments with a
# 96ms median whose every closing read why=silence, and the recogniser returned
# no text for most of them (REPORT 23).
#
# Loopback is the default because it has no dependency on Chrome, survives
# fullscreen and DRM, and cannot disagree with itself about the sample rate.
# TRANSLATION_AUDIO_SOURCE=socket restores the old behaviour, and that is what
# the replay uses: injecting PCM directly keeps a measurement locked to one
# variable instead of adding a speaker round trip.
AUDIO_SOURCE = os.environ.get("TRANSLATION_AUDIO_SOURCE", "loopback").strip().lower()
LOOPBACK_ENABLED = AUDIO_SOURCE != "socket"

# Measured on this machine, a 440Hz tone played at amplitude 0.5 comes back from
# the default speaker's loopback at a peak of about 0.024 -- 26 dB down -- and the
# ratio barely moved when the system volume was changed, so it is a property of
# the device path rather than of the slider.
#
# It is still not written down as a constant. Another machine, another driver or
# an enhancement layer like Nahimic can attenuate by something else, and the VAD
# threshold is absolute: too quiet an input reads as non-speech. So the loopback
# measures its own signal and derives the gain at runtime, and these are the
# bounds on that -- target to aim at, and the hard limits on what it may apply.
LOOPBACK_TARGET_PEAK = float(os.environ.get("TRANSLATION_LOOPBACK_TARGET_PEAK", "0.20"))
LOOPBACK_GAIN_MIN = float(os.environ.get("TRANSLATION_LOOPBACK_GAIN_MIN", "0.5"))
LOOPBACK_GAIN_MAX = float(os.environ.get("TRANSLATION_LOOPBACK_GAIN_MAX", "40.0"))
LOOPBACK_PREVOLUME = os.environ.get("TRANSLATION_LOOPBACK_PREVOLUME", "1").strip().lower() not in ("0", "false", "no")

# Which text-to-text translator serves the whisper/nemotron/qwen path.
#
# "index-translate" is the adopted default (Index-Translate-2B.Q4_K_M.gguf).
# "hy-mt2" serves as legacy fallback. "gemma4" swaps in Gemma 4 E2B weights.
MT_BACKEND = os.environ.get("TRANSLATION_MT_BACKEND", "index-translate").strip().lower()


# 输出方式（流式 / 块状）是**路线的属性**：一条路线一处声明（上面 CHAINS 里的 output），
# 别处只读结论。它先是独立的 TRANSLATION_BLOCK_BY_PUNCT 开关，后来改成由识别后端派生
# （cf2dbc0），现在归并进路线表 —— 理由两次都一样：同一个行为有两个地方可以设置，
# 就一定会有一个在说谎，而 REPORT 58/59 那一周的日志就是两个开关打架的代价。
#
# 谁在跑：优先读 start.ps1 导出的 TRANSLATION_CHAIN_RUNNING —— 那是**观测到的**值，
# 回退分支改过它；没有这个变量才按管线实际用的识别器/翻译器推。分辨"请求 qwen3-hy
# 实际回退到 whisper-hy"正是这条存在的理由（AGENT §4：臂的身份只能来自观测状态）。
#
# 放在 MT_BACKEND 之后：它要读这个常量，而模块级赋值是自上而下执行的。
def running_chain() -> str:
    declared = os.environ.get("TRANSLATION_CHAIN_RUNNING", "").strip().lower()
    if declared in CHAINS:
        return declared
    if MT_BACKEND == "gemma4":
        return "gemma-e2e"
    if ASR_BACKEND in ("nemotron", "nv"):
        return "nv-in"
    if ASR_BACKEND == "qwen3":
        return "qwen3-in"
    return DEFAULT_CHAIN


def chain_output(chain: str | None = None) -> str:
    """'stream' or 'block' for a chain (default: the one running now)."""
    spec = CHAINS.get(chain or running_chain(), CHAINS[DEFAULT_CHAIN])
    return str(spec.get("output", "stream"))


OUTPUT_MODE = chain_output()
BLOCK_OUTPUT = OUTPUT_MODE == "block"

# The Gemma translator runs as its own llama-server on its own port, because the
# audio-native branch answers the same /v1/chat/completions shape and the two
# cannot both own 8080.
#
# Only the port lives here. The model file, the GPU layer count, the context
# length and whether the MTP draft is enabled are read by scripts/start.ps1
# straight from TRANSLATION_GEMMA_* when it builds the llama-server command line,
# and it carries its own defaults there. They used to be duplicated in this file
# as GEMMA_TRANSLATOR_* constants, which meant three places to change a number
# and no way to tell from a log which one the running server had used -- the same
# failure as the stale runtime.json pin, in a different file.
GEMMA_TRANSLATOR_PORT = _env_int("TRANSLATION_GEMMA_PORT", 8082)
GEMMA_TRANSLATOR_URL = f"http://{LLAMA_HOST}:{GEMMA_TRANSLATOR_PORT}/v1/chat/completions"

# How much of the preceding conversation the translator is shown.
#
# Not copied from CONTEXT_SENTENCES, and that is deliberate: the question the
# user asked is what injection costs and buys, and it cannot be answered with a
# value that also changes when the other path's context changes. 0 is the
# control arm.
#
# The whisper path passed no context at all until this was wired up --
# translate.build_messages used to drop its context argument -- so "2" is a new
# behaviour rather than the old default.
MT_CONTEXT_SENTENCES = _env_int("TRANSLATION_MT_CONTEXT_SENTENCES", 2)

# The audio-native path sends no context, and this is why.
#
# Measured both ways on the two fragments that fail in every replay and translate
# correctly when sent alone: with the preceding lines in the prompt, the same
# audio comes back as a transcription of the English, or as a clause repeated
# until the fragment ends. Without it, four of four runs each produced a correct
# translation of both, and three full replays produced no bad fragment at all.
#
# It was already known to be expensive here -- 854ms of first token against 86ms
# without, plus a hallucinated word -- but the branch kept passing context while
# the rest of the report treated it as switched off.
#
# Injectable because the interaction with MTP is worth measuring rather than
# assuming: speculative decoding and a longer prompt change the same prefill
# cost, and a 2x2 on one backend settles it. Both axes are swept together for
# that reason and never separately.
AUDIO_CONTEXT_SENTENCES = _env_int("TRANSLATION_AUDIO_CONTEXT_SENTENCES", 0)
PARTIAL_STABLE_REPEAT = 1  # translate the first partial; later growth follows after it finishes

# llama.cpp GPU layers — probe script writes actual value into runtime.json
LLAMA_NGL_DEFAULT = 99

RUNTIME_JSON = ROOT / "runtime.json"

# Which llama.cpp build to run is not decided here. It used to be, as a hardcoded
# two-entry list naming 2.47.0 and 2.33.0, which went stale the moment a third
# build was installed and could not see a machine other than this one.
# scripts/resolve_llama.ps1 owns the decision now: it globs the LM Studio backends
# directory for this platform, honours -Pin and TRANSLATION_LLAMA_VERSION, and
# prints what it picked and what else is installed. A list of paths written down
# in Python is the thing that was wrong, not the lookup.


def resolve_llama_runtime(vram_mb: int | None = None) -> tuple[int, int]:
    """Use aggressive GPU offload on normal machines and partial offload only on 4GB GPUs.

    This project's live footprint is small enough for a 4GB card to run the Q4
    model without blowing up, but it still benefits from cutting a bit of the
    layer count when memory is tight. Machines with more than 4GB keep almost all
    layers inside VRAM to minimize latency.

    ``vram_mb`` is the card's *total* memory, not what is currently used.
    Comparing "used after load" against a fixed 4096 conflated the two: a used
    figure is a property of whichever model happened to be loaded, so it was
    both the wrong quantity and, in runtime.json, a stale one carried over from
    a deleted model. On an 8GB card whose probe recorded 3000 MiB used, this
    would read 3000 <= 4096 and quietly demote a card that did not need it.
    """
    if vram_mb is None:
        if RUNTIME_JSON.exists():
            try:
                runtime = json.loads(RUNTIME_JSON.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                runtime = {}
            # Total first; the used figure is only a fallback for old files.
            vram_mb = runtime.get("memory_total_mib")
            if vram_mb is None:
                vram_mb = runtime.get("vram_used_mib_after_load") or runtime.get("fit_target_mib")
        if vram_mb is None:
            vram_mb = 8192
    vram_mb = max(1, int(vram_mb))
    if vram_mb <= 4096:
        return 60, 1024
    return 99, 1536


