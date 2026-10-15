"""The prompt actually carries the context, and the control arm is unchanged.

Both halves are load-bearing and both have already failed once in this project:
the function accepted `context` and began with `del language, context`, so every
translation was a cold start while the signature said otherwise. A test is the
only thing that keeps that from coming back, because the symptom is a quality
difference rather than an exception.
"""

import unittest

from server.translate import build_messages, context_pairs

HY_MT2_BARE = (
    "Translate the following text from English into Chinese. "
    "Note that you should only output the translated result without any additional explanation:\n\n"
    "Hello."
)


class ContextPairsTests(unittest.TestCase):
    def test_zero_sentences_is_the_control_arm(self):
        history = [("One", "一"), ("Two", "二")]
        self.assertEqual(context_pairs(history, 0), [])

    def test_only_the_tail_is_taken(self):
        history = [("One", "一"), ("Two", "二"), ("Three", "三")]
        self.assertEqual(context_pairs(history, 2), [("Two", "二"), ("Three", "三")])

    def test_untranstated_lines_are_dropped(self):
        # A remembered pair with no translation is not context; injecting an
        # empty arrow would teach the model that the arrow means nothing.
        self.assertEqual(context_pairs([("One", ""), ("Two", "二")], 2), [("Two", "二")])


class PromptShapeTests(unittest.TestCase):
    def test_hy_mt2_without_context_is_byte_identical_to_before(self):
        messages = build_messages("Hello.", "en", [("One", "一")], "hy-mt2", 0)
        self.assertEqual(messages, [{"role": "user", "content": HY_MT2_BARE}])

    def test_hy_mt2_keeps_the_tuned_instruction_when_context_is_added(self):
        messages = build_messages("Hello.", "en", [("One", "一")], "hy-mt2", 2)
        self.assertEqual(len(messages), 1)
        content = messages[0]["content"]
        self.assertIn("[Background Information]", content)
        self.assertIn("Recent utterances and their translations:\nOne → 一", content)
        self.assertIn("translate the following text from English into Chinese", content)
        self.assertIn("[Source Text]\nHello.", content)

    def test_hy_mt2_streamrevise_includes_previous_hypothesis_and_translation(self):
        messages = build_messages(
            "Hello world.",
            "en",
            [("One", "一")],
            "hy-mt2",
            2,
            prev_source="Hello",
            prev_translation="你好",
        )
        self.assertEqual(len(messages), 1)
        content = messages[0]["content"]
        self.assertIn("Previous version of the current source:\nHello", content)
        self.assertIn("Previous translation of the current source:\n你好", content)
        self.assertIn("[Source Text]\nHello world.", content)

    def test_gemma_uses_roles_and_injects_into_the_user_turn(self):
        messages = build_messages("Hello.", "en", [("One", "一"), ("Two", "二")], "gemma4", 2)
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        self.assertIn("字幕翻译", messages[0]["content"])
        self.assertIn("当前原文：\nHello.", messages[1]["content"])
        self.assertIn("One  →  一", messages[1]["content"])

    def test_gemma_without_context_has_no_context_header(self):
        messages = build_messages("Hello.", "en", [("One", "一")], "gemma4", 0)
        self.assertNotIn("前文", messages[1]["content"])

    def test_source_language_is_named_for_gemma(self):
        messages = build_messages("こんにちは", "ja", [], "gemma4", 2)
        self.assertIn("日语", messages[1]["content"])

    def test_index_translate_bare_prompt(self):
        messages = build_messages("Hello.", "en", [], "index-translate", 0)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "user")
        self.assertIn("请将以下英语文本翻译为中文，直接输出翻译结果，不要进行任何解释。", messages[0]["content"])
        self.assertIn("Hello.", messages[0]["content"])

    def test_index_translate_auto_source_omits_source_name(self):
        messages = build_messages("Hello.", "auto", [], "index-translate", 0)
        self.assertIn("请将以下文本翻译为中文，直接输出翻译结果，不要进行任何解释。", messages[0]["content"])

    def test_index_translate_with_history(self):
        messages = build_messages("Hello.", "en", [("One", "一")], "index-translate", 2)
        content = messages[0]["content"]
        self.assertIn("【前文参考】", content)
        self.assertIn("- One → 一", content)
        self.assertIn("请将以下英语文本翻译为中文", content)

    def test_index_translate_stream_revise_uses_inst_trans_constraint(self):
        messages = build_messages(
            "Hello world.",
            "en",
            [("One", "一")],
            "index-translate",
            2,
            prev_source="Hello",
            prev_translation="你好",
        )
        content = messages[0]["content"]
        self.assertIn("【前文参考】", content)
        self.assertIn("严格遵循所有约束要求", content)
        self.assertIn("【硬性要求】前序草稿译文为“你好”", content)
        self.assertIn("保持译文前缀稳定", content)
        self.assertIn("Hello world.", content)


class IndexTranslateLanguageMappingTests(unittest.TestCase):
    def test_resolve_lang_name_iso_and_names(self):
        from server.translate import resolve_lang_name, resolve_source_lang_name

        self.assertEqual(resolve_lang_name("en"), "英语")
        self.assertEqual(resolve_lang_name("English"), "英语")
        self.assertEqual(resolve_lang_name("ja"), "日语")
        self.assertEqual(resolve_lang_name("Japanese"), "日语")
        self.assertEqual(resolve_lang_name("ko"), "韩语")
        self.assertEqual(resolve_lang_name("Korean"), "韩语")
        self.assertEqual(resolve_lang_name("zh"), "中文")
        self.assertEqual(resolve_lang_name("fr"), "法语")
        self.assertEqual(resolve_lang_name("de"), "德语")
        self.assertEqual(resolve_lang_name("es"), "西班牙语")
        self.assertEqual(resolve_lang_name("ru"), "俄语")

    def test_resolve_source_lang_name_auto(self):
        from server.translate import resolve_source_lang_name

        self.assertEqual(resolve_source_lang_name("auto"), "")
        self.assertEqual(resolve_source_lang_name(""), "")
        self.assertEqual(resolve_source_lang_name(None), "")
        self.assertEqual(resolve_source_lang_name("en"), "英语")


if __name__ == "__main__":
    unittest.main()
