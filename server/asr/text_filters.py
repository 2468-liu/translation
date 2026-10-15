"""Text filters, language mapping, hallucination detection, and prompt parsing for ASR engines."""

from __future__ import annotations

import re

_HALLUCINATIONS = {
    "thanks for watching",
    "thank you for watching",
    "thanks for listening",
    "subtitles by the amara.org community",
    "subscribe to my channel",
    "please subscribe",
}


def is_hallucination(text: str) -> bool:
    normalized = re.sub(r"[\s\.\,\!\?。！？…'\"“”]+", " ", text).strip().lower()
    if not normalized:
        return True
    if normalized in _HALLUCINATIONS:
        return True
    return False


_LANG_NAMES = {
    # 2-letter ISO codes and BCP-47
    "zh": "zh", "en": "en", "ja": "ja", "ko": "ko",
    "es": "es", "fr": "fr", "de": "de", "ru": "ru",
    "pt": "pt", "it": "it", "ar": "ar", "tr": "tr",
    "vi": "vi", "th": "th", "id": "id", "hi": "hi",
    "nl": "nl", "pl": "pl", "sv": "sv", "cs": "cs",
    "hu": "hu", "ro": "ro", "uk": "uk", "el": "el",
    "he": "he", "ms": "ms", "fa": "fa", "ur": "ur",
    "ta": "ta", "hr": "hr", "yue": "yue", "tl": "tl",
    "fil": "tl", "km": "km", "my": "my", "gu": "gu",
    "te": "te", "mr": "mr", "bn": "bn", "bo": "bo",
    "kk": "kk", "mn": "mn", "ug": "ug", "nb": "nb",
    "no": "no", "da": "da", "fi": "fi", "sk": "sk",
    "bg": "bg", "et": "et", "sl": "sl", "lt": "lt",
    "lv": "lv", "ka": "ka", "az": "az", "uz": "uz",
    # English names
    "chinese": "zh", "mandarin": "zh", "cantonese": "yue",
    "english": "en", "japanese": "ja", "korean": "ko",
    "spanish": "es", "french": "fr", "german": "de",
    "russian": "ru", "portuguese": "pt", "italian": "it",
    "arabic": "ar", "turkish": "tr", "vietnamese": "vi",
    "thai": "th", "indonesian": "id", "hindi": "hi",
    "dutch": "nl", "polish": "pl", "swedish": "sv",
    "czech": "cs", "hungarian": "hu", "romanian": "ro",
    "ukrainian": "uk", "greek": "el", "hebrew": "he",
    "malay": "ms", "persian": "fa", "urdu": "ur",
    "tamil": "ta", "croatian": "hr", "tagalog": "tl",
    "filipino": "tl", "khmer": "km", "burmese": "my",
    "gujarati": "gu", "telugu": "te", "marathi": "mr",
    "bengali": "bn", "tibetan": "bo", "kazakh": "kk",
    "mongolian": "mn", "uyghur": "ug", "norwegian": "no",
    "danish": "da", "finnish": "fi", "slovak": "sk",
    "bulgarian": "bg", "estonian": "et", "slovenian": "sl",
    "lithuanian": "lt", "latvian": "lv", "georgian": "ka",
    "azerbaijani": "az", "uzbek": "uz",
}

_CODE_TO_FULL_LANG = {
    "zh": "Chinese",
    "zh-cn": "Chinese",
    "zh-tw": "Chinese",
    "zh-hk": "Chinese",
    "yue": "Cantonese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "ru": "Russian",
    "pt": "Portuguese",
    "it": "Italian",
    "ar": "Arabic",
    "tr": "Turkish",
    "vi": "Vietnamese",
    "th": "Thai",
    "id": "Indonesian",
    "hi": "Hindi",
    "nl": "Dutch",
    "pl": "Polish",
    "sv": "Swedish",
    "cs": "Czech",
    "hu": "Hungarian",
    "ro": "Romanian",
    "uk": "Ukrainian",
    "el": "Greek",
    "he": "Hebrew",
    "ms": "Malay",
    "fa": "Persian",
    "ur": "Urdu",
    "ta": "Tamil",
    "hr": "Croatian",
    "tl": "Filipino",
    "fil": "Filipino",
    "km": "Khmer",
    "my": "Burmese",
    "gu": "Gujarati",
    "te": "Telugu",
    "mr": "Marathi",
    "bn": "Bengali",
    "bo": "Tibetan",
    "kk": "Kazakh",
    "mn": "Mongolian",
    "ug": "Uyghur",
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
    "ka": "Georgian",
    "az": "Azerbaijani",
    "uz": "Uzbek",
    # Full name fallbacks
    "chinese": "Chinese",
    "english": "English",
    "japanese": "Japanese",
    "korean": "Korean",
}


def format_qwen3_prompt(language: str | None) -> str:
    """Format natural language instruction prompt for Qwen3-ASR."""
    if not language or language.lower() in ("auto", "none", ""):
        return "Transcribe the audio."
    code = language.lower().strip()
    base_code = code.split("-")[0]
    full_name = _CODE_TO_FULL_LANG.get(code) or _CODE_TO_FULL_LANG.get(base_code) or code.capitalize()
    return f"Transcribe the {full_name} audio."


def parse_qwen3_asr(raw: str) -> tuple[str, str | None, float]:
    """Split the model's own framing off the transcript.

    Qwen3-ASR answers with a structured prefix before the text:
        language English<asr_text>I think you should go with your instincts.
    or
        language zh<asr_text>你好
    or
        <|zh|>你好
    or without marker:
        language: Japanese\nこんにちは
    or bare text:
        こんにちは

    This parser strips all metadata framing, extracts detected language,
    and returns pure subtitle transcript text.
    """
    if not raw:
        return "", None, 0.0

    detected: str | None = None
    text = raw.strip()

    # 1. Check for special tokens at start (e.g. <|zh|>, <zh>, <|en|>)
    m_tag = re.match(r"^<\|?([a-zA-Z_\-]+)\|?>\s*(.*)$", text, re.DOTALL)
    if m_tag:
        tag_val = m_tag.group(1).lower().split("-")[0]
        for word, code in _LANG_NAMES.items():
            if tag_val == word or tag_val == code:
                detected = code
                break
        text = m_tag.group(2).strip()

    # 2. Standard Qwen3 <asr_text> marker
    marker = "<asr_text>"
    if marker in text:
        head, body = text.split(marker, 1)
        text = body.strip()
        lowered = head.strip().lower()
        if lowered.startswith("language"):
            name = lowered[len("language"):].strip().strip(".:=")
            # Check exact code match first
            if name in _LANG_NAMES:
                detected = _LANG_NAMES[name]
            else:
                for word, code in sorted(_LANG_NAMES.items(), key=lambda x: len(x[0]), reverse=True):
                    if name.startswith(word) or name == code:
                        detected = code
                        break
        # Strip any stray tags in body
        text = re.sub(r"<\|?[a-zA-Z0-9_\-]+\|?>", "", text).strip()
        return text, detected, 1.0 if text else 0.0

    # 3. Fallback prefix check (e.g. "language Japanese: ..." or "language: zh ...")
    m = re.match(r"^language\s*[:=\s]?\s*([a-zA-Z_\-]+)[\s:：\n]+(.*)$", text, re.DOTALL | re.IGNORECASE)
    if m:
        name = m.group(1).lower().split("-")[0]
        if name in _LANG_NAMES:
            detected = _LANG_NAMES[name]
        else:
            for word, code in sorted(_LANG_NAMES.items(), key=lambda x: len(x[0]), reverse=True):
                if name.startswith(word) or name == code:
                    detected = code
                    break
        text = m.group(2).strip()

    # Strip any remaining XML-like tags <...> or <|...|>
    text = re.sub(r"<\|?[a-zA-Z0-9_\-]+\|?>", "", text).strip()
    return text, detected, 1.0 if text else 0.0


_NEMO_LANG_MAP = {
    # 32 language locales supported by Nemotron 3.5 ASR
    "zh": "zh-CN", "chinese": "zh-CN", "mandarin": "zh-CN", "zh-cn": "zh-CN", "zh-hans": "zh-CN",
    "en": "en-US", "english": "en-US", "en-us": "en-US", "en-gb": "en-GB",
    "ja": "ja-JP", "japanese": "ja-JP", "ja-jp": "ja-JP",
    "ko": "ko-KR", "korean": "ko-KR", "ko-kr": "ko-KR",
    "es": "es-ES", "spanish": "es-ES", "es-es": "es-ES", "es-us": "es-US",
    "fr": "fr-FR", "french": "fr-FR", "fr-fr": "fr-FR", "fr-ca": "fr-CA",
    "de": "de-DE", "german": "de-DE", "de-de": "de-DE",
    "ru": "ru-RU", "russian": "ru-RU", "ru-ru": "ru-RU",
    "it": "it-IT", "italian": "it-IT", "it-it": "it-IT",
    "pt": "pt-PT", "portuguese": "pt-PT", "pt-pt": "pt-PT", "pt-br": "pt-BR",
    "nl": "nl-NL", "dutch": "nl-NL", "nl-nl": "nl-NL",
    "tr": "tr-TR", "turkish": "tr-TR", "tr-tr": "tr-TR",
    "ar": "ar-AR", "arabic": "ar-AR", "ar-ar": "ar-AR",
    "hi": "hi-IN", "hindi": "hi-IN", "hi-in": "hi-IN",
    "vi": "vi-VN", "vietnamese": "vi-VN", "vi-vn": "vi-VN",
    "uk": "uk-UA", "ukrainian": "uk-UA", "uk-ua": "uk-UA",
    "pl": "pl-PL", "polish": "pl-PL", "pl-pl": "pl-PL",
    "sv": "sv-SE", "swedish": "sv-SE", "sv-se": "sv-SE",
    "cs": "cs-CZ", "czech": "cs-CZ", "cs-cz": "cs-CZ",
    "nb": "nb-NO", "norwegian": "nb-NO", "nb-no": "nb-NO",
    "da": "da-DK", "danish": "da-DK", "da-dk": "da-DK",
    "bg": "bg-BG", "bulgarian": "bg-BG", "bg-bg": "bg-BG",
    "fi": "fi-FI", "finnish": "fi-FI", "fi-fi": "fi-FI",
    "hr": "hr-HR", "croatian": "hr-HR", "hr-hr": "hr-HR",
    "sk": "sk-SK", "slovak": "sk-SK", "sk-sk": "sk-SK",
    "hu": "hu-HU", "hungarian": "hu-HU", "hu-hu": "hu-HU",
    "ro": "ro-RO", "romanian": "ro-RO", "ro-ro": "ro-RO",
    "et": "et-EE", "estonian": "et-EE", "et-ee": "et-EE",
}

_NEMO_BCP47_TO_ISO = {
    "en-us": "en", "en-gb": "en", "es-us": "es", "es-es": "es",
    "fr-fr": "fr", "fr-ca": "fr", "it-it": "it", "pt-br": "pt",
    "pt-pt": "pt", "nl-nl": "nl", "de-de": "de", "tr-tr": "tr",
    "ru-ru": "ru", "ar-ar": "ar", "hi-in": "hi", "ja-jp": "ja",
    "ko-kr": "ko", "vi-vn": "vi", "uk-ua": "uk", "pl-pl": "pl",
    "sv-se": "sv", "cs-cz": "cs", "nb-no": "nb", "da-dk": "da",
    "bg-bg": "bg", "fi-fi": "fi", "hr-hr": "hr", "sk-sk": "sk",
    "zh-cn": "zh", "hu-hu": "hu", "ro-ro": "ro", "et-ee": "et",
}


def to_nemo_bcp47(lang: str | None) -> str | None:
    if not lang or lang.strip().lower() in ("auto", "none", ""):
        return None
    c = lang.strip().lower()
    if c in _NEMO_LANG_MAP:
        return _NEMO_LANG_MAP[c]
    for bcp in _NEMO_LANG_MAP.values():
        if bcp.lower() == c:
            return bcp
    # Fallback by primary language prefix (e.g. "zh-tw" -> "zh", "es-mx" -> "es")
    base = c.split("-")[0].split("_")[0]
    if base in _NEMO_LANG_MAP:
        return _NEMO_LANG_MAP[base]
    return None


def parse_nemotron_transcript(raw: str, default_lang: str | None = None) -> tuple[str, str | None]:
    """Parse Nemotron ASR output, detecting and stripping <lang-XX> or <zh-CN> tags.

    Nemotron in auto mode appends or embeds tags like <en-US>, <zh-CN>, <lang-en>.
    This extracts the language code (mapped to 2-letter ISO or normalized)
    and strips the tags so internal tokens never leak into subtitles or translation.
    """
    if not raw:
        return "", default_lang

    text = raw.strip()
    detected = default_lang

    # Look for tags like <en-US>, <zh-CN>, <ja-JP>, <lang-en>, etc.
    match = re.search(r"<([a-zA-Z0-9_\-]+)>", text)
    if match:
        tag = match.group(1).strip()
        tag_lower = tag.lower()
        if tag_lower.startswith("lang-"):
            tag_lower = tag_lower[5:]

        found_code = None
        for bcp, iso in _NEMO_BCP47_TO_ISO.items():
            if bcp.lower() == tag_lower:
                found_code = iso
                break
        if not found_code:
            prefix = tag_lower.split("-")[0]
            if prefix in _LANG_NAMES or prefix in ("zh", "en", "ja", "ko", "es", "fr", "de", "ru"):
                found_code = prefix
            elif tag_lower in _LANG_NAMES:
                found_code = _LANG_NAMES[tag_lower]

        if found_code:
            detected = found_code
        elif not detected or detected.lower() in ("auto", "none", ""):
            detected = tag_lower.split("-")[0]

        # Strip all XML-like tags <...>
        text = re.sub(r"<[a-zA-Z0-9_\-]+>", "", text).strip()

    return text, detected
