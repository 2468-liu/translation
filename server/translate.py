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
    # 常用推荐
    "zh": "中文",
    "zh-cn": "简体中文",
    "zh-tw": "繁体中文",
    "zh-hk": "繁体中文",
    "yue": "粤语",
    "en": "英语",
    "ja": "日语",
    "ko": "韩语",
    "fr": "法语",
    "de": "德语",
    "es": "西班牙语",
    "ru": "俄语",
    "it": "意大利语",
    "pt": "葡萄牙语",
    # 亚洲与东南亚
    "vi": "越南语",
    "th": "泰语",
    "id": "印尼语",
    "ms": "马来语",
    "tl": "菲律宾语",
    "fil": "菲律宾语",
    "km": "高棉语",
    "my": "缅甸语",
    "hi": "印地语",
    "bn": "孟加拉语",
    "ur": "乌尔都语",
    "ta": "泰米尔语",
    "te": "泰卢固语",
    "mr": "马拉地语",
    "gu": "古吉拉特语",
    # 欧洲语系
    "tr": "土耳其语",
    "nl": "荷兰语",
    "pl": "波兰语",
    "sv": "瑞典语",
    "cs": "捷克语",
    "hu": "匈牙利语",
    "ro": "罗马尼亚语",
    "uk": "乌克兰语",
    "el": "希腊语",
    "hr": "克罗地亚语",
    "nb": "挪威语",
    "no": "挪威语",
    "da": "丹麦语",
    "fi": "芬兰语",
    "sk": "斯洛伐克语",
    "bg": "保加利亚语",
    "et": "爱沙尼亚语",
    "sl": "斯洛文尼亚语",
    "lt": "立陶宛语",
    "lv": "拉脱维亚语",
    # 中东与南亚
    "ar": "阿拉伯语",
    "fa": "波斯语",
    "he": "希伯来语",
    # 民族与地区
    "bo": "藏语",
    "kk": "哈萨克语",
    "mn": "蒙古语",
    "ug": "维吾尔语",
    "ka": "格鲁吉亚语",
    "az": "阿塞拜疆语",
    "uz": "乌兹别克语",
}

_LANG_EN_NAME = {
    # 常用推荐
    "zh": "Chinese",
    "zh-cn": "Chinese",
    "zh-tw": "Traditional Chinese",
    "zh-hk": "Traditional Chinese",
    "yue": "Cantonese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "ru": "Russian",
    "it": "Italian",
    "pt": "Portuguese",
    # 亚洲与东南亚
    "vi": "Vietnamese",
    "th": "Thai",
    "id": "Indonesian",
    "ms": "Malay",
    "tl": "Filipino",
    "fil": "Filipino",
    "km": "Khmer",
    "my": "Burmese",
    "hi": "Hindi",
    "bn": "Bengali",
    "ur": "Urdu",
    "ta": "Tamil",
    "te": "Telugu",
    "mr": "Marathi",
    "gu": "Gujarati",
    # 欧洲语系
    "tr": "Turkish",
    "nl": "Dutch",
    "pl": "Polish",
    "sv": "Swedish",
    "cs": "Czech",
    "hu": "Hungarian",
    "ro": "Romanian",
    "uk": "Ukrainian",
    "el": "Greek",
    "hr": "Croatian",
    "nb": "Norwegian",
    "no": "Norwegian",
    "da": "Danish",
    "fi": "Finnish",
    "sk": "Slovak",
    "bg": "Bulgarian",
    "et": "Estonian",
    "sl": "Slovenian",
    "lt": "Lithuanian",
    "lv": "Latvian",
    # 中东与南亚
    "ar": "Arabic",
    "fa": "Persian",
    "he": "Hebrew",
    # 民族与地区
    "bo": "Tibetan",
    "kk": "Kazakh",
    "mn": "Mongolian",
    "ug": "Uyghur",
    "ka": "Georgian",
    "az": "Azerbaijani",
    "uz": "Uzbek",
}


def resolve_lang_name(lang: str | None, default: str = "中文") -> str:
    """Resolve ISO codes, English names, or Chinese names to canonical Chinese language names for Index-Translate."""
    if not lang or lang.strip().lower() in ("auto", "none", ""):
        return default
    c = lang.strip().lower()
    if c in _LANG_NAME:
        return _LANG_NAME[c]
    prefix = c.split("-")[0]
    if prefix in _LANG_NAME:
        return _LANG_NAME[prefix]
    for code, en_name in _LANG_EN_NAME.items():
        if en_name.lower() == c:
            return _LANG_NAME.get(code, default)
    for code, zh_name in _LANG_NAME.items():
        if zh_name == lang.strip():
            return zh_name
    for code, zh_name in _LANG_NAME.items():
        if zh_name.rstrip("语") == lang.strip().rstrip("语"):
            return zh_name
    return default


def resolve_source_lang_name(lang: str | None) -> str:
    """Resolve source language to Chinese name, or empty string if auto/unspecified."""
    if not lang or lang.strip().lower() in ("auto", "none", ""):
        return ""
    return resolve_lang_name(lang, default="")


def resolve_lang_en_name(lang: str | None, default: str = "English") -> str:
    """Resolve ISO codes, BCP-47 tags, or localized names to full English names for Hy-MT2."""
    if not lang or lang.strip().lower() in ("auto", "none", ""):
        return default
    c = lang.strip().lower()
    if c in _LANG_EN_NAME:
        return _LANG_EN_NAME[c]
    prefix = c.split("-")[0]
    if prefix in _LANG_EN_NAME:
        return _LANG_EN_NAME[prefix]
    for code, en_name in _LANG_EN_NAME.items():
        if en_name.lower() == c:
            return en_name
    for code, zh_name in _LANG_NAME.items():
        if zh_name == lang.strip():
            return _LANG_EN_NAME.get(code, default)
    for code, zh_name in _LANG_NAME.items():
        if zh_name.rstrip("语") == lang.strip().rstrip("语"):
            return _LANG_EN_NAME.get(code, default)
    return default

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
    target_language: str = "zh",
    prev_source: str = "",
    prev_translation: str = "",
) -> list[dict]:
    """Chat messages for one line, for whichever backend is serving.

    Supports:
    1. Index-Translate (default): Uses Chinese language names, official prompt template,
       sliding context and instTrans constraint format for stream revision.
    2. Hy-MT2: StreamRevise prompt template with full English language names.
    3. Gemma: Role-separated chat template.
    """
    backend = (backend or MT_BACKEND).lower()
    budget = MT_CONTEXT_SENTENCES if context_sentences is None else context_sentences
    pairs = context_pairs(context, budget)
    tgt_code = (target_language or "zh").strip().lower()

    if backend == "gemma4":
        tgt_zh = _LANG_NAME.get(tgt_code, "中文")
        gemma_system = (
            f"你是字幕翻译。把用户给出的内容译成{tgt_zh}，只输出译文。"
            "保留人名与专有名词。句子不完整就按现有内容翻译，不要补写剧情。"
        )
        src_zh = _LANG_NAME.get(language or "", language or "外语")
        user = f"源语言：{src_zh}\n目标语言：{tgt_zh}\n"
        if pairs:
            user += (
                "前文（已经翻译过，不要重复翻译，也不要续写）：\n"
                f"{_context_block(pairs)}\n\n"
            )
        user += f"当前原文：\n{text}"
        return [
            {"role": "system", "content": gemma_system},
            {"role": "user", "content": user},
        ]

    if backend == "hy-mt2":
        # Hy-MT2-1.8B-StreamRevise-v4 format (from models/hy-mt2-1.8b/README.md)
        # Uses full English language names (e.g. English, Chinese, Japanese).
        src_en = resolve_lang_en_name(language, default="English")
        tgt_en = resolve_lang_en_name(target_language, default="Chinese")

        has_revision = bool(prev_source and prev_translation)
        has_history = bool(pairs)

        if not has_revision and not has_history:
            # First chunk of a new utterance without history
            user = (
                f"Translate the following text from {src_en} into {tgt_en}. "
                "Note that you should only output the translated result without any additional explanation:\n\n"
                f"{text}"
            )
            return [{"role": "user", "content": user}]

        # StreamRevise layout with background information
        bg_blocks = []
        if has_history:
            recent_lines = ["Recent utterances and their translations:"]
            for s, t in pairs:
                recent_lines.append(f"{s} → {t}")
            bg_blocks.append("\n".join(recent_lines))

        if has_revision:
            bg_blocks.append(f"Previous version of the current source:\n{prev_source}")
            bg_blocks.append(f"Previous translation of the current source:\n{prev_translation}")

        bg_info = "\n\n".join(bg_blocks)
        user = (
            "[Background Information]\n"
            f"{bg_info}\n\n"
            "When the source meaning has not changed, preserve the still-correct prefix of the previous translation whenever possible. "
            "When content is added or corrected, accuracy and completeness take priority.\n\n"
            f"Please translate the following text from {src_en} into {tgt_en}, taking the provided background information into consideration.\n\n"
            "[Source Text]\n"
            f"{text}"
        )
        return [{"role": "user", "content": user}]

    # Index-Translate (bilibili official prompt format with Chinese language names)
    tgt_zh = resolve_lang_name(target_language, default="中文")
    src_zh = resolve_source_lang_name(language)
    header_src = f"{src_zh}文本" if src_zh else "文本"

    has_revision = bool(prev_source and prev_translation)
    has_history = bool(pairs)

    if not has_revision and not has_history:
        user = (
            f"请将以下{header_src}翻译为{tgt_zh}，直接输出翻译结果，不要进行任何解释。\n\n"
            f"{text}"
        )
        return [{"role": "user", "content": user}]

    constraints = []
    if has_revision:
        constraints.append(
            f"【硬性要求】前序草稿译文为“{prev_translation}”，若原文含义未变，请尽量保持译文前缀稳定，仅修正或增补新内容。"
        )

    bg_blocks = []
    if has_history:
        bg_blocks.append("【前文参考】\n" + "\n".join(f"- {s} → {t}" for s, t in pairs))

    bg_prefix = ("\n\n".join(bg_blocks) + "\n\n") if bg_blocks else ""

    if constraints:
        req_lines = [f"{i + 1}. {c}" for i, c in enumerate(constraints)]
        req_block = "\n".join(req_lines)
        user = (
            f"{bg_prefix}"
            f"请将以下{header_src}翻译成{tgt_zh}，并且严格遵循所有约束要求。\n\n"
            f"{req_block}\n\n"
            f"{text}"
        )
    else:
        user = (
            f"{bg_prefix}"
            f"请将以下{header_src}翻译为{tgt_zh}，直接输出翻译结果，不要进行任何解释。\n\n"
            f"{text}"
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
    target_language: str = "zh",
    prev_source: str = "",
    prev_translation: str = "",
) -> AsyncIterator[str]:
    remote = api if api and api.get("enabled") and api.get("url") and api.get("model") else None
    gemma = MT_BACKEND == "gemma4" and not remote
    dynamic_max_tokens = min(TRANSLATE_MAX_TOKENS, max(128, len(text) * 4))
    payload = {
        "model": remote["model"] if remote else LLAMA_MODEL,
        "stream": True,
        "temperature": TRANSLATE_TEMPERATURE,
        "max_tokens": dynamic_max_tokens,
        "messages": build_messages(
            text,
            language,
            context,
            target_language=target_language,
            prev_source=prev_source,
            prev_translation=prev_translation,
        ),
    }
    if not remote:
        payload["cache_prompt"] = True
        payload["chat_template_kwargs"] = {"enable_thinking": False}
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
        # Index-Translate / Hy-MT2 uses greedy decoding (temperature: 0) to prevent flicker across updates
        payload["temperature"] = TRANSLATE_TEMPERATURE
        payload["cache_prompt"] = True
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
