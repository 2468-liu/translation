"""Stream a translation from the local llama.cpp server."""

from __future__ import annotations

import json
import re
from collections import deque
from collections.abc import AsyncIterator

import httpx

from .config import (
    GEMMA_TRANSLATOR_URL,
    LLAMA_MODEL,
    LLAMA_URL,
    MT_BACKEND,
    MT_CONTEXT_SENTENCES,
    TRANSLATE_MAX_TOKENS,
    TRANSLATE_TEMPERATURE,
)


def collapse_translation_repeats(text: str) -> str:
    """Remove pathological 5+ repetitions while preserving ordinary phrasing."""
    cleaned = text
    for _ in range(3):
        collapsed = re.sub(r"(.{1,12}?)(?:\1){4,}", r"\1", cleaned, flags=re.DOTALL)
        if collapsed == cleaned:
            break
        cleaned = collapsed
    return cleaned


# Both of the rules below allow three occurrences and cut the fourth.
#
# Repetition is ordinary in Chinese, and cutting at two turns ordinary lines into
# fragments: a quotation, a correction, and a list that returns to its own
# subject each repeat a phrase. What is not ordinary is a fourth and a fifth -- a
# decoder that has stopped making progress. Three is rare enough to be worth
# cutting and common enough that cutting earlier would cost real sentences.
CHAR_RUN_MAX = 3
CLAUSE_RUN_MAX = 3
# How many clauses back a repeat has to fall to count. Without a window, the same
# phrase reused six clauses later is cut as if it were a loop.
CLAUSE_WINDOW = 4
CLAUSE_SEP = "，,、；;：:"


def collapse_char_runs(text: str) -> str:
    """Cap a run of one identical character.

    Catches what clause comparison cannot see: no two adjacent characters are
    equal in a clause loop, but they are all equal here.
    """
    out: list[str] = []
    run = 0
    prev = ""
    for ch in text:
        if ch == prev and ch.strip():
            run += 1
            if run > CHAR_RUN_MAX:
                continue
        else:
            prev = ch
            run = 1
        out.append(ch)
    return "".join(out)


def collapse_clause_runs(text: str) -> str:
    """Cut at a clause the model has already said three times over."""
    parts = re.split(f"([{re.escape(CLAUSE_SEP)}])", text)
    recent: deque[str] = deque(maxlen=CLAUSE_WINDOW)
    kept: list[str] = []
    for part in parts:
        if part in CLAUSE_SEP or not part.strip():
            kept.append(part)
            continue
        if sum(1 for earlier in recent if earlier == part) >= CLAUSE_RUN_MAX:
            return collapse_char_runs("".join(kept).rstrip(CLAUSE_SEP))
        recent.append(part)
        kept.append(part)
    return collapse_char_runs("".join(kept))


def collapse_caption_repeats(text: str) -> str:
    """The rule the caption window and the generators both use.

    One implementation, because the two copies drifted: the window grew a clause
    rule the generators did not have, so a loop was cut on screen and still sent
    whole.
    """
    return collapse_clause_runs(text or "")

def _drop_think(piece: str, thinking: bool) -> tuple[str, bool]:
    out = []
    i = 0
    while i < len(piece):
        if thinking:
            end = piece.find("</think>", i)
            if end < 0:
                return "".join(out), True
            i = end + len("</think>")
            thinking = False
            continue
        start = piece.find("<think>", i)
        if start < 0:
            out.append(piece[i:])
            break
        out.append(piece[i:start])
        i = start + len("<think>")
        thinking = True
    return "".join(out), thinking


_LANG_NAME = {
    "en": "英语",
    "ja": "日语",
    "ko": "韩语",
    "fr": "法语",
    "de": "德语",
    "es": "西班牙语",
    "ru": "俄语",
    "it": "意大利语",
    "pt": "葡萄牙语",
    "vi": "越南语",
    "th": "泰语",
    "id": "印尼语",
    "ar": "阿拉伯语",
    "zh": "中文",
    "tr": "土耳其语",
    "hi": "印地语",
    "nl": "荷兰语",
    "pl": "波兰语",
    "sv": "瑞典语",
    "cs": "捷克语",
    "hu": "匈牙利语",
    "ro": "罗马尼亚语",
    "uk": "乌克兰语",
    "el": "希腊语",
    "he": "希伯来语",
    "ms": "马来语",
    "fa": "波斯语",
    "ur": "乌尔都语",
    "ta": "泰米尔语",
    "hr": "克罗地亚语",
}

# The Gemma translator is an instruct model, so it gets a role-separated prompt
# instead of the one-line instruction the Hy-MT2 fine-tune was trained on. The
# two prompts are NOT interchangeable and this is measurable: Hy-MT2 was tuned on
# the bare "将以下文本翻译为中文" form, and Gemma answers that form too, but it
# answers a system-prompted translation request more consistently and it is the
# only one of the two that can carry the preceding conversation usefully.
_GEMMA_SYSTEM = (
    "你是字幕翻译。把用户给出的内容译成简体中文，只输出译文。"
    "保留人名与专有名词。句子不完整就按现有内容翻译，不要补写剧情。"
)


def context_pairs(context: list[tuple[str, str]] | None, sentences: int) -> list[tuple[str, str]]:
    """The tail of the conversation, as (source, translation) pairs.

    ``sentences <= 0`` returns nothing, which is the control arm of the
    context-injection measurement: the rest of the request is byte-identical,
    so the difference between the two arms is the context and nothing else.
    """
    if sentences <= 0 or not context:
        return []
    return [(src, zh) for src, zh in context[-sentences:] if zh]


def _context_block(pairs: list[tuple[str, str]]) -> str:
    return "\n".join(f"- {src}  →  {zh}" for src, zh in pairs)


def build_messages(
    text: str,
    language: str | None,
    context: list[tuple[str, str]] | None = None,
    backend: str = "",
    context_sentences: int | None = None,
) -> list[dict]:
    """Chat messages for one line, for whichever backend is serving.

    The context used to be accepted and thrown away -- the signature had it and
    the body began with ``del language, context``. That made "does context help"
    unanswerable from the live pipeline and quietly turned every translation into
    a cold start, which is the shape of failure that reads as a bad model rather
    than a missing feature.
    """
    backend = (backend or MT_BACKEND).lower()
    budget = MT_CONTEXT_SENTENCES if context_sentences is None else context_sentences
    pairs = context_pairs(context, budget)
    if backend == "gemma4":
        user = f"源语言：{_LANG_NAME.get(language or '', language or '外语')}\n"
        if pairs:
            user += (
                "前文（已经翻译过，不要重复翻译，也不要续写）：\n"
                f"{_context_block(pairs)}\n\n"
            )
        user += f"当前原文：\n{text}"
        return [
            {"role": "system", "content": _GEMMA_SYSTEM},
            {"role": "user", "content": user},
        ]
    # Hy-MT2, and everything measured on it, was tuned on this exact one-liner.
    # Context is appended rather than restructured so the no-context arm stays
    # byte-identical to the prompt in REPORT.md.
    user = f"将以下文本翻译为中文，注意只需要输出翻译后的结果，不要额外解释：\n\n{text}"
    if pairs:
        user = (
            "前文（仅供参考，不要重复翻译）：\n"
            f"{_context_block(pairs)}\n\n" + user
        )
    return [{"role": "user", "content": user}]


def chat_completions_url(raw: str) -> str:
    url = (raw or "").strip().rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return url + "/chat/completions"


async def stream_translation(
    text: str,
    language: str | None,
    context: list[tuple[str, str]],
    client: httpx.AsyncClient,
    api: dict | None = None,
) -> AsyncIterator[str]:
    remote = api if api and api.get("enabled") and api.get("url") and api.get("model") else None
    gemma = MT_BACKEND == "gemma4" and not remote
    payload = {
        "model": remote["model"] if remote else LLAMA_MODEL,
        "stream": True,
        "temperature": TRANSLATE_TEMPERATURE,
        "max_tokens": TRANSLATE_MAX_TOKENS,
        "messages": build_messages(text, language, context),
    }
    headers = {}
    if remote:
        url = chat_completions_url(remote["url"])
        key = (remote.get("key") or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
    elif gemma:
        # Same sampling family the audio-native branch settled on for these exact
        # weights: near-greedy with a small repeat penalty, which is what keeps a
        # subtitle line from looping without making the wording adventurous.
        url = GEMMA_TRANSLATOR_URL
        payload["temperature"] = 0.7
        payload["top_p"] = 0.6
        payload["top_k"] = 20
        payload["repeat_penalty"] = 1.05
    else:
        url = LLAMA_URL
        payload["temperature"] = 0.7
        payload["top_p"] = 0.6
        payload["top_k"] = 20
        payload["repeat_penalty"] = 1.05
    thinking = False
    async with client.stream("POST", url, json=payload, headers=headers) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = obj.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or choices[0].get("message") or {}
            piece = delta.get("content") or ""
            if not piece:
                continue
            visible, thinking = _drop_think(piece, thinking)
            if visible:
                yield collapse_translation_repeats(visible)
