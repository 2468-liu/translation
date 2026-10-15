"""Generation-side defences on the audio-native path.

The caption window now catches a clause that repeats past the limit, which stops
the loop reaching the screen but leaves the model still producing it. These are
the three checks that stop it earlier, each aimed at a failure the logs contain
rather than at a failure that might occur:

  budget      the wire was fixed at 400 tokens, so a decoder that stopped making
              progress had 400 tokens of room to say so
  untranslated  the model transcribes the English instead of translating it,
              reproducibly, on the same fragments in every run
  repeat final  it can hand back the line it gave the previous fragment

The window's own clause rule is not enough on its own: it trims what is already
on screen, and it cannot un-send a fragment.
"""

import unittest

from server.audio_mt import (
    build_payload,
    looks_untranslated,
    token_budget,
    chinese_share,
)
from server.config import AUDIO_CHINESE_SHARE_MIN, AUDIO_MAX_TOKENS
from server.pipeline import Session


class TokenBudgetTests(unittest.TestCase):
    def test_budget_grows_with_the_audio(self):
        self.assertLess(token_budget(2.0), token_budget(8.0))

    def test_a_loop_runs_out_of_room_before_the_wire_does(self):
        # 3 seconds of speech. At the fixed 400 tokens a loop had room to repeat
        # a clause a dozen times; scaled, it has about a sentence's worth.
        self.assertLess(token_budget(3.0), AUDIO_MAX_TOKENS)

    def test_a_very_short_fragment_still_has_room_for_a_clause(self):
        self.assertGreaterEqual(token_budget(0.2), 64)

    def test_a_very_long_fragment_stops_at_the_configured_maximum(self):
        self.assertEqual(token_budget(600.0), AUDIO_MAX_TOKENS)

    def test_the_payload_carries_the_scaled_budget_and_a_penalty(self):
        payload = build_payload("AAAA", audio_seconds=3.0)
        self.assertEqual(payload["max_tokens"], token_budget(3.0))
        self.assertGreater(payload["repeat_penalty"], 1.0)


class UntranslatedTests(unittest.TestCase):
    def test_a_pure_transcription_is_caught(self):
        text = "Short for a blog and long enough to show every place."
        self.assertTrue(looks_untranslated(text))

    def test_a_finished_translation_is_not_caught(self):
        self.assertFalse(looks_untranslated("这句话足够长，可以读完它。"))

    def test_punctuation_and_spacing_do_not_count_against_a_line(self):
        # Half the characters are punctuation, none of it foreign. Counting
        # characters instead of letters would fail this line.
        self.assertFalse(looks_untranslated("他说，湖边，湖边，然后——走了。"))

    def test_a_quoted_english_phrase_inside_a_translation_is_tolerated(self):
        # Real output: a Chinese line quoting an English title.
        text = "这简写了“博客”，足够长，可以展示所有“Words Start Dancing”的地点。"
        self.assertGreater(chinese_share(text), AUDIO_CHINESE_SHARE_MIN)

    def test_empty_text_is_not_untranslated(self):
        self.assertEqual(chinese_share(""), 0.0)
        self.assertFalse(looks_untranslated(""))


class SettleTests(unittest.TestCase):
    def setUp(self):
        self.session = Session.__new__(Session)
        self.session._last_audio_final = ""
        self.session.shown_translation = ""

    def settle(self, accepted):
        return Session._settle_audio_text(self.session, accepted)

    def test_a_clean_line_passes_through(self):
        self.assertEqual(self.settle(["这句话足够长。"]), "这句话足够长。")

    def test_the_finished_line_is_used_not_the_first_packet(self):
        self.assertEqual(self.settle(["这", "这句", "这句话足够长。"]), "这句话足够长。")

    def test_a_transcription_is_dropped_rather_than_captioned(self):
        self.assertEqual(self.settle(["Short for a blog and long enough."]), "")

    def test_a_looped_line_is_cut_at_the_loop(self):
        looped = "在湖边，在湖边，在湖边，在湖边，在湖边"
        self.assertLess(len(self.settle([looped])), len(looped))

    def test_the_previous_fragments_line_is_not_repeated(self):
        first = self.settle(["这句话足够长。"])
        self.session._last_audio_final = first
        self.assertEqual(self.settle([first]), "")

    def test_a_different_line_after_the_previous_one_is_allowed(self):
        self.session._last_audio_final = "第一句话。"
        self.assertEqual(self.settle(["第二句话。"]), "第二句话。")

    def test_nothing_accepted_yields_nothing(self):
        self.assertEqual(self.settle([]), "")


class GrowthStepTests(unittest.TestCase):
    def test_a_short_line_arrives_in_one_step(self):
        self.assertEqual(Session._growth_steps("一句话。"), ["一句话。"])

    def test_a_long_line_arrives_in_several_readable_steps(self):
        steps = Session._growth_steps("在" * 100)
        self.assertGreater(len(steps), 1)
        self.assertEqual(steps[-1], "在" * 100)
        # Each step has to extend the last, or the caption window sees a rewrite
        # rather than growth and holds the line.
        for earlier, later in zip(steps, steps[1:]):
            self.assertTrue(later.startswith(earlier))

    def test_step_count_is_small(self):
        # 500 packets for one sentence is what made the window redraw hundreds of
        # times; a handful is enough to read as text arriving.
        self.assertLessEqual(len(Session._growth_steps("在" * 400)), 4)


if __name__ == "__main__":
    unittest.main()
