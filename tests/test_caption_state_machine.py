"""The caption window's display state machine.

These assertions used to live in caption_window._self_check(), which nothing ever
called -- not the module's __main__, not a test. It was therefore both dead and
wrong: two of its expectations predated speech_hold_factor, so it would have
failed the moment anyone ran it. A check nobody runs is not a check, and its
failure would have been a false alarm about the display layer rather than the
stale expectation it actually was.

They are here instead so that changing hold/settle/swap -- which is what the
display-layer work does -- has to move these numbers on purpose.
"""

import unittest

from server.caption_window import (
    HOLD_ABS_MAX_MS,
    HOLD_ABS_MIN_MS,
    PACE_MAX,
    PACE_MIN,
    SETTLE_MS,
    _read_units,
    _reference_hold_ms,
    classify_caption,
    hold_ms,
    pace_scale,
    speech_hold_factor,
    step_caption,
    swap_deadline,
)

BASE = "这是一次实时字幕测试"
FIXED = "这是一次实时字幕测误"
OTHER = "完全不同的一句字幕内容"


class ClassifyTests(unittest.TestCase):
    def test_four_shapes_are_told_apart(self):
        self.assertEqual(classify_caption("这是一次", "这是一次实时"), "grow")
        self.assertEqual(classify_caption(BASE, "这是一次实时"), "rewind")
        self.assertEqual(classify_caption(BASE, FIXED), "char")
        self.assertEqual(classify_caption(BASE, OTHER), "sentence")

    def test_a_growing_line_does_not_restart_its_anchor(self):
        first = step_caption("", "", 0.0, 0.0, "这是一次", 10.0)
        self.assertEqual(first[0], "grow")
        self.assertEqual(first[1], "这是一次")
        self.assertEqual(first[2], "")

        second = step_caption(first[1], first[2], first[3], first[4], BASE, 10.5)
        self.assertEqual(second[0], "grow")
        self.assertEqual(second[1], BASE)
        self.assertEqual(second[3], 10.5)
        self.assertEqual(second[4], 0.0)


class HoldTests(unittest.TestCase):
    def test_hold_is_the_scaled_reference_clamped_to_the_absolute_bands(self):
        expected = round(_reference_hold_ms(_read_units(BASE)) * speech_hold_factor(1.0))
        self.assertEqual(hold_ms(BASE), expected)
        self.assertGreaterEqual(expected, HOLD_ABS_MIN_MS)
        self.assertLessEqual(expected, HOLD_ABS_MAX_MS)

    def test_a_very_short_line_falls_to_the_floor(self):
        # The reference table gives two units 1000ms. What ships is the scaled value
        # clamped into [HOLD_ABS_MIN_MS, HOLD_ABS_MAX_MS], and which of those two
        # numbers wins depends on the floor -- at 800 the clamp caught it exactly, at
        # 600 the scaled value of 800 passes through untouched. Asserting the floor
        # here rather than the expression made this test a floor test wearing a hold
        # test's name, and it failed on a floor change that was the whole point.
        scaled = round(_reference_hold_ms(_read_units("你好")) * speech_hold_factor(1.0))
        self.assertEqual(_reference_hold_ms(_read_units("你好")), 1000)
        self.assertEqual(hold_ms("你好"), max(HOLD_ABS_MIN_MS, min(HOLD_ABS_MAX_MS, scaled)))

    def test_the_floor_reaches_at_least_one_band(self):
        """A floor below 620ms is an inert parameter, and that has to fail loudly.

        The reference table bottoms out at 1000ms for one to three units and the fastest
        pace factor is 0.62, so 620ms is the smallest value the table and the factor can
        produce between them. A floor at or below that clamps nothing, and the minimum
        hold becomes 620 whatever the setting says.

        That was measured, not assumed: at 600 the clamp stopped reaching anything and
        held-group gap p50 moved 1482ms to 1502ms while every short line lost 180ms. So
        the change looked harmless on the metric and cost real readability, which is why
        the floor went back to 800. This test keeps the arithmetic visible so a future
        attempt to lower it has to read this before running the experiment.
        """
        fastest_band = _reference_hold_ms(_read_units("你")) * speech_hold_factor(0.0)
        self.assertAlmostEqual(fastest_band, 620.0, places=1)
        self.assertGreater(
            HOLD_ABS_MIN_MS, fastest_band,
            "the floor is at or below the 620ms the table can produce, so it clamps "
            "nothing and the minimum hold is 620 regardless of this setting")

    def test_longer_lines_hold_longer(self):
        self.assertGreater(hold_ms("这是十三字的一行参考字幕"), hold_ms(BASE))


class PaceTests(unittest.TestCase):
    def test_too_few_usable_gaps_leave_pace_alone(self):
        self.assertEqual(pace_scale([]), 1.0)
        self.assertEqual(pace_scale([1.6, 1.5]), 1.0)

    def test_fast_speech_shortens_the_hold_and_slow_lengthens_it(self):
        fast = pace_scale([1.6, 1.5, 1.7])
        slow = pace_scale([5.0, 5.5, 6.0])
        self.assertTrue(PACE_MIN <= fast < 1.0 < slow <= PACE_MAX)
        self.assertLess(hold_ms(BASE, fast), hold_ms(BASE))
        self.assertGreater(hold_ms(BASE, slow), hold_ms(BASE))

    def test_the_floor_holds_even_at_the_fastest_pace(self):
        fast = pace_scale([1.6, 1.5, 1.7])
        self.assertGreaterEqual(hold_ms("你", fast), HOLD_ABS_MIN_MS)
        self.assertLessEqual(hold_ms("你", fast), HOLD_ABS_MAX_MS)


class SwapDeadlineTests(unittest.TestCase):
    def test_an_upcoming_utterance_pulls_the_swap_forward(self):
        full = swap_deadline(10.0, 0.0, BASE)
        pulled = swap_deadline(10.0, 0.0, BASE, 1.0, 10.4)
        self.assertLess(pulled, full)
        self.assertGreaterEqual(pulled, 10.0 + HOLD_ABS_MIN_MS / 1000.0)

    def test_a_pull_past_the_deadline_does_not_push_it_back(self):
        full = swap_deadline(10.0, 0.0, BASE)
        self.assertEqual(swap_deadline(10.0, 0.0, BASE, 1.0, full + 1.0), full)


class StepCaptionTests(unittest.TestCase):
    def test_a_rewind_is_dropped_and_the_held_line_keeps_its_deadline(self):
        grown = step_caption("", "", 0.0, 0.0, "这是一次", 10.0)
        held = step_caption(grown[1], grown[2], grown[3], grown[4], BASE, 10.5)
        hold_deadline = held[5]

        dropped = step_caption(held[1], held[2], held[3], held[4], "这是一次实时", 11.0)
        self.assertEqual(dropped[0], "drop")
        self.assertEqual(dropped[1], BASE)
        self.assertEqual(dropped[3], 10.5)
        self.assertEqual(dropped[5], hold_deadline)

    def test_a_single_character_fix_keeps_the_anchor_and_extends_within_settle(self):
        grown = step_caption("", "", 0.0, 0.0, "这是一次", 10.0)
        held = step_caption(grown[1], grown[2], grown[3], grown[4], BASE, 10.5)
        hold_deadline = held[5]

        char_at = 11.2
        fixed = step_caption(BASE, "", 10.5, 0.0, FIXED, char_at)
        self.assertEqual(fixed[0], "char")
        self.assertEqual(fixed[1], FIXED)
        self.assertEqual(fixed[2], "")
        # The anchor does not move: a typo corrected mid-line is the same
        # utterance, not a new one.
        self.assertEqual(fixed[3], 10.5)
        # Settling may extend the hold, but only by SETTLE_MS and no further.
        self.assertLessEqual(fixed[5] - hold_deadline, SETTLE_MS / 1000.0 + 0.02)
        # And it is not the same as starting the hold clock again from scratch.
        self.assertNotEqual(fixed[5], char_at + hold_ms(BASE) / 1000.0)


if __name__ == "__main__":
    unittest.main()
