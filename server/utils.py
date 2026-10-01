"""Shared helpers for the local subtitle pipeline."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import numpy as np

from . import config


def load_runtime() -> dict[str, Any]:
    if not config.RUNTIME_JSON.exists():
        return {}
    return json.loads(config.RUNTIME_JSON.read_text(encoding="utf-8"))


def save_runtime(data: dict[str, Any]) -> None:
    existing = load_runtime()
    existing.update(data)
    config.RUNTIME_JSON.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def pcm_s16le_to_float32(data: bytes) -> np.ndarray:
    if not data:
        return np.zeros(0, dtype=np.float32)
    count = len(data) // 2
    samples = struct.unpack(f"<{count}h", data[: count * 2])
    return (np.asarray(samples, dtype=np.float32) / 32768.0).clip(-1.0, 1.0)


def find_gguf() -> Path | None:
    if not config.GGUF_DIR.exists():
        return None
    files = sorted(config.GGUF_DIR.rglob("*.gguf"))
    # Prefer q4_k_m
    for f in files:
        name = f.name.lower()
        if "q4_k_m" in name or "q4km" in name:
            return f
    return files[0] if files else None


def resolve_asr_model() -> Path:
    if config.ASR_MODEL_DIR.exists() and (config.ASR_MODEL_DIR / "model.bin").exists():
        return config.ASR_MODEL_DIR
    if config.ASR_FALLBACK_DIR.exists() and (config.ASR_FALLBACK_DIR / "model.bin").exists():
        return config.ASR_FALLBACK_DIR
    raise FileNotFoundError(
        f"ASR model not found. Expected {config.ASR_MODEL_DIR} or {config.ASR_FALLBACK_DIR}"
    )


def translation_messages(
    source_text: str,
    source_lang: str,
    recent_zh: list[str],
) -> list[dict[str, str]]:
    context = "\n".join(s for s in recent_zh[-config.CONTEXT_SENTENCES :] if s) or "（无）"
    lang_name = {
        "en": "英语",
        "ja": "日语",
        "ko": "韩语",
        "fr": "法语",
        "de": "德语",
        "es": "西班牙语",
        "ru": "俄语",
    }.get(source_lang, source_lang or "外语")
    system = (
        "你是字幕翻译。把用户给出的内容译成简体中文。"
        "只输出译文。保留人名。句子不完整就按现有内容翻译，不要补写剧情。"
    )
    user = f"源语言：{lang_name}\n前文译文：\n{context}\n\n当前原文：\n{source_text}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
