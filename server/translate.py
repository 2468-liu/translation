"""Stream a translation from the local llama.cpp server."""

from __future__ import annotations

import json
import re
from collections import deque
from collections.abc import AsyncIterator

import httpx

from .config import LLAMA_MODEL, LLAMA_URL, TRANSLATE_MAX_TOKENS, TRANSLATE_TEMPERATURE, USE_NLLB
from .mt import get_engine, stream_mt


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


def build_messages(text: str, language: str | None, context: list[tuple[str, str]]) -> list[dict]:
    del language, context
    user = f"将以下文本翻译为中文，注意只需要输出翻译后的结果，不要额外解释：\n\n{text}"
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
    elif USE_NLLB and get_engine().ready:
        async for piece in stream_mt(text, language):
            yield piece
        return
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
