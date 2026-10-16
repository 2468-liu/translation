"""ASR Engine Package: Faster-Whisper, Qwen3-ASR HTTP, and NVIDIA Nemotron 3.5 streaming engines."""

from __future__ import annotations

from .. import config, perf
from .base import (
    AsrEngine,
    HEAD_CHARS,
    TAIL_CHARS,
    WAV_HEADER_BYTES,
    _pcm_bytes,
    _wav_bytes,
    usage_tokens,
)
from .nemotron import (
    NemotronAsrEngine,
    _detect_best_vulkan_gpu,
    _resolve_nemo_speech_dll,
    _resolve_nemotron_model_path,
    check_nemotron_status,
)
from .qwen3 import Qwen3AsrEngine
from .text_filters import (
    _CODE_TO_FULL_LANG,
    _HALLUCINATIONS,
    _LANG_NAMES,
    _NEMO_BCP47_TO_ISO,
    _NEMO_LANG_MAP,
    format_qwen3_prompt,
    is_hallucination,
    is_nemo_supported,
    parse_nemotron_transcript,
    parse_qwen3_asr,
    to_nemo_bcp47,
)
def build_asr(fallback_on_error: bool = True):
    """The recogniser the configuration asks for.

    Read from config at call time rather than from the name imported above, so the
    effective configuration and the engine that actually got built cannot disagree.
    Kept behind one factory so ws_server does not grow a branch.
    """
    backend = config.ASR_BACKEND
    if backend in ("nemotron", "nv", "nemotron-3.5"):
        try:
            return NemotronAsrEngine()
        except Exception as e:
            if not fallback_on_error:
                raise
            import logging
            logging.error(f"[ASR] Nemotron engine initialization failed ({e}); auto-falling back to Qwen3-ASR...")
            try:
                cfg = config.load_desktop_config()
                cfg["asr_backend"] = "qwen3"
                config.save_desktop_config(cfg)
            except Exception:
                pass
            config.ASR_BACKEND = "qwen3"
            import os
            os.environ["TRANSLATION_ASR_BACKEND"] = "qwen3"
            os.environ["TRANSLATION_CHAIN_RUNNING"] = "qwen3-in"
            return Qwen3AsrEngine()
    if backend == "qwen3":
        return Qwen3AsrEngine()
    raise ValueError(
        f"unknown ASR_BACKEND {backend!r}; expected 'nemotron' or 'qwen3'")


__all__ = [
    "AsrEngine",
    "Qwen3AsrEngine",
    "NemotronAsrEngine",
    "build_asr",
    "config",
    "perf",
    "is_hallucination",
    "_HALLUCINATIONS",
    "_LANG_NAMES",
    "_CODE_TO_FULL_LANG",
    "format_qwen3_prompt",
    "parse_qwen3_asr",
    "_NEMO_LANG_MAP",
    "_NEMO_BCP47_TO_ISO",
    "to_nemo_bcp47",
    "parse_nemotron_transcript",
    "_pcm_bytes",
    "_wav_bytes",
    "WAV_HEADER_BYTES",
    "HEAD_CHARS",
    "TAIL_CHARS",
    "usage_tokens",
    "_resolve_nemotron_model_path",
    "_resolve_nemo_speech_dll",
    "_detect_best_vulkan_gpu",
]
