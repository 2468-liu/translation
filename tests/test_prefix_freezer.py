import unittest
from server.ui.prefix_freezer import MonotonicPrefixFreezer, get_lcp_length


class PrefixFreezerTests(unittest.TestCase):
    def test_lcp_computation(self):
        self.assertEqual(get_lcp_length("hello world", "hello there"), 6)
        self.assertEqual(get_lcp_length("今天天气", "今天天气真好"), 4)
        self.assertEqual(get_lcp_length("abc", "xyz"), 0)
        self.assertEqual(get_lcp_length("", "abc"), 0)

    def test_multi_slice_confirmation_freezes_common_prefix(self):
        freezer = MonotonicPrefixFreezer(min_confirm_slices=2, stability_timeout_s=0.6, tail_margin=2)

        # Slice 1 at t=0.0: first hypothesis arrives, tail margin = 2
        p1, s1 = freezer.update("今天", now=0.0)
        self.assertEqual(p1, "")
        self.assertEqual(s1, "今天")

        # Slice 2 at t=0.16: confirms "今天", extends to "今天天气"
        p2, s2 = freezer.update("今天天气", now=0.16)
        # "今天" confirmed across 2 slices and outside tail margin (len 4 - 2 = 2)
        self.assertEqual(p2, "今天")
        self.assertEqual(s2, "天气")

        # Slice 3 at t=0.32: confirms "天气", extends to "今天天气真好"
        p3, s3 = freezer.update("今天天气真好", now=0.32)
        self.assertEqual(p3, "今天天气")
        self.assertEqual(s3, "真好")

    def test_monotonic_invariant_rejects_backend_regression(self):
        freezer = MonotonicPrefixFreezer(min_confirm_slices=2, stability_timeout_s=0.6, tail_margin=2)
        freezer.update("今天", now=0.0)
        freezer.update("今天天气", now=0.16)
        self.assertEqual(freezer.frozen_prefix, "今天")

        # Backend suddenly regresses and sends only "天" or "明天"
        p, s = freezer.update("天气真好", now=0.32)
        # Frozen prefix "今天" MUST NOT shrink or disappear!
        self.assertTrue(freezer.frozen_prefix.startswith("今天"))
        self.assertEqual(freezer.frozen_prefix, "今天")

    def test_stability_timeout_freezes_paused_text(self):
        freezer = MonotonicPrefixFreezer(min_confirm_slices=2, stability_timeout_s=0.6, tail_margin=2)
        freezer.update("Hello world", now=0.0)
        self.assertEqual(freezer.frozen_prefix, "")

        # 0.7s pass without new words (speaker paused)
        p, s = freezer.update("Hello world", now=0.7)
        # After 0.7s > 0.6s, all characters are frozen by duration!
        self.assertEqual(p, "Hello world")
        self.assertEqual(s, "")

    def test_word_boundary_protection_for_english(self):
        freezer = MonotonicPrefixFreezer(min_confirm_slices=2, stability_timeout_s=0.6, tail_margin=2)
        freezer.update("Hel", now=0.0)
        # Slice agreement: "Hello"
        p, s = freezer.update("Hello everyone", now=0.16)
        # Must not freeze half of "everyone"
        self.assertTrue(p.endswith(" ") or p == "")

    def test_commit_final_resets_state(self):
        freezer = MonotonicPrefixFreezer(min_confirm_slices=2, stability_timeout_s=0.6, tail_margin=2)
        freezer.update("今天天气", now=0.0)
        freezer.update("今天天气真好", now=0.16)

        final_out = freezer.commit_final("今天天气真好。")
        self.assertEqual(final_out, "今天天气真好。")
        self.assertEqual(freezer.frozen_prefix, "")
        self.assertEqual(freezer.hypothesis_suffix, "")

    def test_split_line_segments(self):
        freezer = MonotonicPrefixFreezer()
        freezer.frozen_prefix = "今天天气"  # len 4
        freezer.hypothesis_suffix = "真好"  # len 2

        # 1. Single line: starts at 0, len 6
        stable, hypo = freezer.split_line_segments("今天天气真好", 0)
        self.assertEqual(stable, "今天天气")
        self.assertEqual(hypo, "真好")

        # 2. Line 1: "今天天气" (start 0, len 4) -> all stable
        stable1, hypo1 = freezer.split_line_segments("今天天气", 0)
        self.assertEqual(stable1, "今天天气")
        self.assertEqual(hypo1, "")

        # 3. Line 2: "真好" (start 4, len 2) -> all hypo
        stable2, hypo2 = freezer.split_line_segments("真好", 4)
        self.assertEqual(stable2, "")
        self.assertEqual(hypo2, "真好")

    def test_find_punctuation_boundaries_cjk_and_western(self):
        from server.ui.prefix_freezer import find_punctuation_boundaries

        # CJK diverse punctuation
        cjk_text = "苹果、香蕉；橙子：都很甜。好的！真的吗？等等……没关系——"
        cjk_bounds = find_punctuation_boundaries(cjk_text)
        tiers = [t for _, t in cjk_bounds]
        self.assertEqual(tiers, [2, 2, 2, 1, 1, 1, 1, 2])

        # Western with decimal and abbreviation protection
        eng_text = "The price of Dr. Smith's v2.5 is $3.14, not 19.99. He said: Hello, world! Wait... really?"
        eng_bounds = find_punctuation_boundaries(eng_text)

        slices = [eng_text[:idx] for idx, _ in eng_bounds]
        self.assertTrue(any(s.endswith("$3.14, ") for s in slices))
        self.assertTrue(any(s.endswith("not 19.99. ") for s in slices))
        self.assertTrue(any(s.endswith("He said: ") for s in slices))
        self.assertTrue(any(s.endswith("Hello, ") for s in slices))
        self.assertTrue(any(s.endswith("world! ") for s in slices))
        self.assertTrue(any(s.endswith("Wait... ") for s in slices))
        self.assertTrue(any(s.endswith("really?") for s in slices))
        self.assertFalse(any(s.endswith("Dr. ") for s in slices))

    def test_punctuation_hierarchical_freeze_clause_cjk(self):
        freezer = MonotonicPrefixFreezer(min_confirm_slices=2, stability_timeout_s=0.6, tail_margin=2)
        freezer.update("今天天气", now=0.0)
        self.assertEqual(freezer.frozen_prefix, "")

        p, s = freezer.update("今天天气真好，我", now=0.16)
        self.assertEqual(p, "今天天气真好，")
        self.assertEqual(s, "我")

    def test_punctuation_hierarchical_freeze_western(self):
        freezer = MonotonicPrefixFreezer(min_confirm_slices=2, stability_timeout_s=0.6, tail_margin=2)
        freezer.update("Hello", now=0.0)
        self.assertEqual(freezer.frozen_prefix, "")

        p, s = freezer.update("Hello world, how", now=0.16)
        self.assertEqual(p, "Hello world, ")
        self.assertEqual(s, "how")

    def test_decimal_protection_does_not_prematurely_freeze(self):
        freezer = MonotonicPrefixFreezer(min_confirm_slices=2, stability_timeout_s=0.6, tail_margin=2)
        p, s = freezer.update("Price is 3.14 dollars", now=0.0)
        self.assertEqual(p, "")
        self.assertEqual(s, "Price is 3.14 dollars")


if __name__ == "__main__":
    unittest.main()
