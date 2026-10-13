"""Tests for NV (Nemotron 3.5 ASR) and HY (Hy-MT2 StreamRevise-v4) language mapping and prompt specifications."""

import unittest

from server.asr import (
    _NEMO_LANG_MAP,
    _NEMO_BCP47_TO_ISO,
    to_nemo_bcp47,
    parse_nemotron_transcript,
)
from server.translate import (
    _LANG_EN_NAME,
    resolve_lang_en_name,
    build_messages,
)


class TestNemotronLanguageMapping(unittest.TestCase):
    def test_all_32_locales_present_in_nemo_map(self):
        expected_locales = [
            "en-US", "en-GB", "es-US", "es-ES", "fr-FR", "fr-CA",
            "it-IT", "pt-BR", "pt-PT", "nl-NL", "de-DE", "tr-TR",
            "ru-RU", "ar-AR", "hi-IN", "ja-JP", "ko-KR", "vi-VN",
            "uk-UA", "pl-PL", "sv-SE", "cs-CZ", "nb-NO", "da-DK",
            "bg-BG", "fi-FI", "hr-HR", "sk-SK", "zh-CN", "hu-HU",
            "ro-RO", "et-EE",
        ]
        values = set(_NEMO_LANG_MAP.values())
        for loc in expected_locales:
            self.assertIn(loc, values, f"Missing locale: {loc}")

    def test_to_nemo_bcp47(self):
        self.assertEqual(to_nemo_bcp47("zh"), "zh-CN")
        self.assertEqual(to_nemo_bcp47("en"), "en-US")
        self.assertEqual(to_nemo_bcp47("ja"), "ja-JP")
        self.assertEqual(to_nemo_bcp47("ko"), "ko-KR")
        self.assertEqual(to_nemo_bcp47("en-US"), "en-US")
        self.assertEqual(to_nemo_bcp47("zh-CN"), "zh-CN")
        self.assertEqual(to_nemo_bcp47("auto"), None)
        self.assertEqual(to_nemo_bcp47(""), None)
        self.assertEqual(to_nemo_bcp47(None), None)

    def test_parse_nemotron_transcript_with_tags(self):
        # Auto mode tag appended
        raw = "Hello, how are you? <en-US>"
        text, detected = parse_nemotron_transcript(raw)
        self.assertEqual(text, "Hello, how are you?")
        self.assertEqual(detected, "en")

        # Tag prepended
        raw = "<zh-CN> 早上好，今天天气真好"
        text, detected = parse_nemotron_transcript(raw)
        self.assertEqual(text, "早上好，今天天气真好")
        self.assertEqual(detected, "zh")

        # Japanese tag
        raw = "こんにちは、元気ですか？ <ja-JP>"
        text, detected = parse_nemotron_transcript(raw)
        self.assertEqual(text, "こんにちは、元気ですか？")
        self.assertEqual(detected, "ja")

        # <lang-xx> format
        raw = "Bonjour le monde <lang-fr>"
        text, detected = parse_nemotron_transcript(raw)
        self.assertEqual(text, "Bonjour le monde")
        self.assertEqual(detected, "fr")

        # No tag with default language
        raw = "Just normal transcript"
        text, detected = parse_nemotron_transcript(raw, default_lang="en")
        self.assertEqual(text, "Just normal transcript")
        self.assertEqual(detected, "en")


class TestHyMt2LanguageMapping(unittest.TestCase):
    # Every prompt assertion in this class pins backend="hy-mt2" explicitly.
    #
    # It used to be left implicit, and build_messages resolves an absent backend to
    # the module-global MT_BACKEND. So these tests asserted the hy-mt2 StreamRevise
    # template while selecting it through whatever the machine happened to be
    # running: on a box configured for hy-mt2 they passed, and on this one
    # (MT_BACKEND=index-translate) four failed with
    #
    #     'Translate the following text from English into Japanese.'
    #       not found in '请将以下英语文本翻译为日语...'
    #
    # which reads as "the prompts were rewritten in Chinese". They were not. The
    # Chinese Index-Translate template is a different backend with a different
    # layout and a different contract. Rewriting the assertions to the Chinese
    # strings would have turned four contract tests into snapshots of an ambient
    # value -- and it would have quietly accepted a real difference: the Chinese
    # revision prompt carries prev_translation but drops prev_source, which the
    # hy-mt2 one includes. A test that follows the environment cannot tell you
    # which backend it was really checking.

    def test_resolve_lang_en_name(self):
        self.assertEqual(resolve_lang_en_name("en"), "English")
        self.assertEqual(resolve_lang_en_name("zh"), "Chinese")
        self.assertEqual(resolve_lang_en_name("ja"), "Japanese")
        self.assertEqual(resolve_lang_en_name("ko"), "Korean")
        self.assertEqual(resolve_lang_en_name("zh-CN"), "Chinese")
        self.assertEqual(resolve_lang_en_name("en-US"), "English")
        self.assertEqual(resolve_lang_en_name("英语"), "English")
        self.assertEqual(resolve_lang_en_name("中文"), "Chinese")
        self.assertEqual(resolve_lang_en_name("auto"), "English")
        self.assertEqual(resolve_lang_en_name(None), "English")

    def test_build_messages_streamrevise_first_chunk(self):
        messages = build_messages(
            text="Good morning.",
            language="en",
            target_language="zh",
            backend="hy-mt2",
        )
        self.assertEqual(len(messages), 1)
        content = messages[0]["content"]
        self.assertIn("Translate the following text from English into Chinese.", content)
        self.assertIn("Good morning.", content)
        self.assertNotIn("[Background Information]", content)

    def test_build_messages_streamrevise_with_bilingual_history(self):
        history = [("Hello", "你好"), ("How are you?", "你好吗？")]
        messages = build_messages(
            text="I am doing great.",
            language="en",
            context=history,
            target_language="zh",
            backend="hy-mt2",
        )
        content = messages[0]["content"]
        self.assertIn("[Background Information]", content)
        self.assertIn("Recent utterances and their translations:\nHello → 你好\nHow are you? → 你好吗？", content)
        self.assertIn("preserve the still-correct prefix", content)
        self.assertIn("Please translate the following text from English into Chinese", content)
        self.assertIn("[Source Text]\nI am doing great.", content)

    def test_build_messages_streamrevise_with_revision(self):
        messages = build_messages(
            text="I think you should go with your instincts.",
            language="en",
            target_language="zh",
            prev_source="I think you should go",
            prev_translation="我觉得你应该去",
            backend="hy-mt2",
        )
        content = messages[0]["content"]
        self.assertIn("[Background Information]", content)
        self.assertIn("Previous version of the current source:\nI think you should go", content)
        self.assertIn("Previous translation of the current source:\n我觉得你应该去", content)
        self.assertIn("preserve the still-correct prefix", content)
        self.assertIn("[Source Text]\nI think you should go with your instincts.", content)

    def test_comprehensive_33_plus_hy_languages(self):
        """Verify that all 33+ Tencent HY-MT languages resolve accurately to English names."""
        test_pairs = [
            ("zh", "Chinese"),
            ("yue", "Cantonese"),
            ("zh-tw", "Traditional Chinese"),
            ("en", "English"),
            ("ja", "Japanese"),
            ("ko", "Korean"),
            ("fr", "French"),
            ("de", "German"),
            ("es", "Spanish"),
            ("ru", "Russian"),
            ("it", "Italian"),
            ("pt", "Portuguese"),
            ("vi", "Vietnamese"),
            ("th", "Thai"),
            ("id", "Indonesian"),
            ("ms", "Malay"),
            ("tl", "Filipino"),
            ("fil", "Filipino"),
            ("hi", "Hindi"),
            ("km", "Khmer"),
            ("my", "Burmese"),
            ("bn", "Bengali"),
            ("ar", "Arabic"),
            ("tr", "Turkish"),
            ("nl", "Dutch"),
            ("pl", "Polish"),
            ("sv", "Swedish"),
            ("cs", "Czech"),
            ("hu", "Hungarian"),
            ("ro", "Romanian"),
            ("uk", "Ukrainian"),
            ("el", "Greek"),
            ("he", "Hebrew"),
            ("fa", "Persian"),
            ("ur", "Urdu"),
            ("ta", "Tamil"),
            ("te", "Telugu"),
            ("mr", "Marathi"),
            ("gu", "Gujarati"),
            ("bo", "Tibetan"),
            ("ug", "Uyghur"),
            ("kk", "Kazakh"),
            ("mn", "Mongolian"),
            ("nb", "Norwegian"),
            ("da", "Danish"),
            ("fi", "Finnish"),
        ]
        for code, expected_en in test_pairs:
            with self.subTest(code=code):
                self.assertEqual(resolve_lang_en_name(code), expected_en)

    def test_bidirectional_mutual_translation(self):
        """Verify arbitrary source -> target language prompt generation (not just -> zh)."""
        # English -> Japanese
        msg_en_ja = build_messages(
            text="Hello world",
            language="en",
            target_language="ja",
            backend="hy-mt2",
        )
        self.assertIn("Translate the following text from English into Japanese.", msg_en_ja[0]["content"])

        # Chinese -> Arabic
        msg_zh_ar = build_messages(
            text="欢迎来到这里",
            language="zh",
            target_language="ar",
            backend="hy-mt2",
        )
        self.assertIn("Translate the following text from Chinese into Arabic.", msg_zh_ar[0]["content"])

        # Japanese -> French
        msg_ja_fr = build_messages(
            text="こんにちは",
            language="ja",
            target_language="fr",
            backend="hy-mt2",
        )
        self.assertIn("Translate the following text from Japanese into French.", msg_ja_fr[0]["content"])

        # Korean -> Spanish
        msg_ko_es = build_messages(
            text="감사합니다",
            language="ko",
            target_language="es",
            backend="hy-mt2",
        )
        self.assertIn("Translate the following text from Korean into Spanish.", msg_ko_es[0]["content"])

        # Cantonese -> English
        msg_yue_en = build_messages(
            text="唔該你",
            language="yue",
            target_language="en",
            backend="hy-mt2",
        )
        self.assertIn("Translate the following text from Cantonese into English.", msg_yue_en[0]["content"])


if __name__ == "__main__":
    unittest.main()
