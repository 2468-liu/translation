"""The prompt actually carries the context, and the control arm is unchanged.

Both halves are load-bearing and both have already failed once in this project:
the function accepted `context` and began with `del language, context`, so every
translation was a cold start while the signature said otherwise. A test is the
only thing that keeps that from coming back, because the symptom is a quality
difference rather than an exception.
"""

import unittest

from server.translate import build_messages, context_pairs

HY_MT2_BARE = "将以下文本翻译为中文，注意只需要输出翻译后的结果，不要额外解释：\n\nHello."


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
        self.assertIn(HY_MT2_BARE, content)  # the instruction survives verbatim
        self.assertIn("One  →  一", content)

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


if __name__ == "__main__":
    unittest.main()
