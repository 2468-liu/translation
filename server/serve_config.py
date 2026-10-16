"""Single Source of Truth (SSOT) configuration for model servers, contexts, and density regulation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

# ==============================================================================
# 1. Model Serving Context Sizing & Slot Allocation (Token Budget SSOT)
# ==============================================================================
# ASR: -c 2048 -np 4 -> 512 tokens / slot
# With max audio 15-16s (~230 audio tokens) + 128 max generated tokens = 358 < 512 (Zero OOM headroom)
ASR_CONTEXT = 2048
ASR_NP = 4
ASR_SLOT_TOKENS = ASR_CONTEXT // ASR_NP  # 512
ASR_MAX_TOKENS = 128
ASR_NGL = 99

# MT: -c 2048 -np 3 -> ~682 tokens / slot
MT_CONTEXT = 2048
MT_NP = 3
MT_NGL = 60

# Memory / Cache Leaks & Slot Eviction Control SSOT
# --cache-ram 512: Cap prompt cache RAM to 512 MiB (llama default is 8192 MiB = 8GB, causing ballooning on Win/Linux)
CACHE_RAM_MB = 512
# --ctx-checkpoints 1: Prevent checkpoint cloning across parallel slots from bloating memory
CTX_CHECKPOINTS = 1
# --no-cache-idle-slots: Release disconnected/idle slots immediately, avoiding zombie slots
NO_CACHE_IDLE_SLOTS = True


def get_common_memory_args() -> List[str]:
    """Generates canonical memory-cap and slot-eviction arguments for llama-server / llama.exe."""
    args = [
        "--cache-ram", str(CACHE_RAM_MB),
        "--ctx-checkpoints", str(CTX_CHECKPOINTS),
    ]
    if NO_CACHE_IDLE_SLOTS:
        args.append("--no-cache-idle-slots")
    return args

# ==============================================================================
# 2. Continuous Silence Density Regulation Hyperparameters
# ==============================================================================
# limit(ratio) = clamp(BASE + K * (ratio - R0), FLOOR, CEIL)
# Evaluated at every frame based on binarized speech/silence frames (0/1).
DENSITY_FLOOR = 4000      # Shortest breath + ASR context lower limit
DENSITY_CEIL = 16000      # 16s max cap, worst 358 tokens < 512 tokens/slot
DENSITY_BASE = 6000       # 2x+ p50 baseline
DENSITY_K = 17000.0       # Fitted slope
DENSITY_R0 = 0.118        # Anchor where limit(R0) = BASE (at ratio 0.222, returns ~7768ms ≈ old 8s)

# Phonation lower bound (syllable floor)
MIN_PHONATION_MS = 150


def compute_density_limit_ms(ratio: float) -> float:
    """Computes dynamic segment duration limit from inside-segment silence density ratio.

    ratio = silence_ms / (speech_ms + silence_ms)
    """
    raw = DENSITY_BASE + DENSITY_K * (ratio - DENSITY_R0)
    return max(float(DENSITY_FLOOR), min(float(DENSITY_CEIL), raw))


# ==============================================================================
# 3. Caption UI Dismissal Duration Regulation (Reading Ergonomics)
# ==============================================================================
# clamp(1500 + 150 * N, 1500, 6000)
DISMISS_FLOOR = 1500
DISMISS_CEIL = 6000
DISMISS_BASE = 1500
DISMISS_K = 150.0


def compute_dismiss_delay_ms(char_count: int) -> int:
    """Computes dynamic subtitle dismiss timeout from character count."""
    raw = DISMISS_BASE + DISMISS_K * max(0, char_count)
    return int(max(float(DISMISS_FLOOR), min(float(DISMISS_CEIL), raw)))


# ==============================================================================
# 4. Turn-Taking Merge Window Regulation (Syllable Length Adaptivity)
# ==============================================================================
# clamp(250 + 1.5 * (speech_ms - 100), 250, 600)
MERGE_WINDOW_FLOOR = 250.0
MERGE_WINDOW_CEIL = 600.0
MERGE_WINDOW_BASE = 250.0
MERGE_WINDOW_K = 1.5


def compute_dynamic_merge_window_ms(speech_ms: float) -> float:
    """Computes adaptive merge window based on phonation length of held fragment."""
    raw = MERGE_WINDOW_BASE + MERGE_WINDOW_K * max(0.0, speech_ms - 100.0)
    return max(MERGE_WINDOW_FLOOR, min(MERGE_WINDOW_CEIL, raw))


# ==============================================================================
# 5. Downstream MT Debounce & Backpressure Elastic Airbag
# ==============================================================================
# clamp(150 + 50 * Q + 0.2 * (gpu_latency - 150), 60, 350)
DEBOUNCE_FLOOR = 60.0
DEBOUNCE_CEIL = 350.0
DEBOUNCE_BASE = 150.0


def compute_dynamic_debounce_ms(queue_depth: int = 0, gpu_latency_ms: float = 150.0) -> float:
    """Computes adaptive translation debounce window based on backpressure and queue."""
    raw = DEBOUNCE_BASE + 50.0 * max(0, queue_depth) + 0.2 * (gpu_latency_ms - 150.0)
    return max(DEBOUNCE_FLOOR, min(DEBOUNCE_CEIL, raw))


def get_qwen_server_args(
    exe: Path,
    model_path: Path,
    mmproj_path: Path,
    host: str,
    port: int,
    threads: int = 4,
) -> List[str]:
    """Generates canonical command line arguments for Qwen3-ASR llama-server."""
    cmd = [str(exe)]
    if exe.stem.lower() in ("llama", "llama-cli"):
        cmd.append("serve")
    cmd.extend([
        "-m", str(model_path),
        "--mmproj", str(mmproj_path),
        "--host", str(host),
        "--port", str(port),
        "-ngl", str(ASR_NGL),
        "-c", str(ASR_CONTEXT),
        "-t", str(threads),
        "-np", str(ASR_NP),
        "--no-webui",
    ])
    cmd.extend(get_common_memory_args())
    return cmd


def get_mt_server_args(
    exe: Path,
    model_path: Path,
    host: str,
    port: int,
    is_index: bool = True,
    threads: int = 4,
) -> List[str]:
    """Generates canonical command line arguments for MT translation llama-server."""
    cmd = [str(exe)]
    if exe.stem.lower() in ("llama", "llama-cli"):
        cmd.append("serve")
    cmd.extend([
        "-m", str(model_path),
        "--host", str(host),
        "--port", str(port),
        "-ngl", str(MT_NGL),
        "-c", str(MT_CONTEXT),
        "-t", str(threads),
        "-np", str(MT_NP),
        "--no-webui",
    ])
    if is_index:
        cmd.extend(["--reasoning", "off"])
    cmd.extend(get_common_memory_args())
    return cmd
