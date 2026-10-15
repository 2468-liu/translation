"""Unit tests for CaptionDisplayBuffer (Zero-delay visual persistence buffer)."""

import time
import unittest

from server.ui.punct_gate import CaptionDisplayBuffer


class CaptionDisplayBufferTests(unittest.TestCase):
    def test_inline_concatenation_for_rapid_short_sentences(self):
        buf = CaptionDisplayBuffer(max_inline_chars=24, min_dwell_sec=0.85)

        # 1. First short sentence
        res1 = buf.push_new_sentence("原来那个人就是他！")
        self.assertEqual(res1, "原来那个人就是他！")
        self.assertEqual(buf.line1, "")
        self.assertEqual(buf.line2, "原来那个人就是他！")

        # 2. Rapid follow-up sentence arrives in 200ms
        time.sleep(0.05)
        res2 = buf.push_new_sentence("没错！")
        # Inline concatenated with two spaces!
        self.assertEqual(res2, "原来那个人就是他！  没错！")
        self.assertEqual(buf.line1, "")
        self.assertEqual(buf.line2, "原来那个人就是他！  没错！")

    def test_two_line_rolling_when_exceeding_max_inline_chars(self):
        buf = CaptionDisplayBuffer(max_inline_chars=24, min_dwell_sec=0.85)

        # 1. Sentence 1 (18 chars)
        res1 = buf.push_new_sentence("我们必须在天黑之前穿过这片森林。")
        self.assertEqual(res1, "我们必须在天黑之前穿过这片森林。")

        # 2. Sentence 2 (15 chars) arrives in 200ms -> 18 + 15 = 33 > 24
        res2 = buf.push_new_sentence("否则一旦迷失方向后果不堪设想。")
        self.assertEqual(
            res2,
            "我们必须在天黑之前穿过这片森林。\n否则一旦迷失方向后果不堪设想。"
        )
        self.assertEqual(buf.line1, "我们必须在天黑之前穿过这片森林。")
        self.assertEqual(buf.line2, "否则一旦迷失方向后果不堪设想。")

    def test_two_line_rolling_when_dwell_time_exceeded(self):
        buf = CaptionDisplayBuffer(max_inline_chars=24, min_dwell_sec=0.20)

        # 1. Sentence 1
        res1 = buf.push_new_sentence("第一句短句。")
        self.assertEqual(res1, "第一句短句。")

        # Wait longer than min_dwell_sec
        time.sleep(0.25)

        # 2. Sentence 2 arrives after dwell window
        res2 = buf.push_new_sentence("第二句短句。")
        self.assertEqual(res2, "第一句短句。\n第二句短句。")
        self.assertEqual(buf.line1, "第一句短句。")
        self.assertEqual(buf.line2, "第二句短句。")

    def test_reset_clears_state(self):
        buf = CaptionDisplayBuffer()
        buf.push_new_sentence("测试句子")
        self.assertEqual(buf.line2, "测试句子")

        buf.reset()
        self.assertEqual(buf.line1, "")
        self.assertEqual(buf.line2, "")
        self.assertEqual(buf.last_update_ts, 0.0)


if __name__ == "__main__":
    unittest.main()
