"""What counts as a repetition depends on the unit being repeated.

Three identical characters is emphasis; two identical clauses is a decoder that
has stopped making progress. One threshold cannot express both, and the one that
did was the clause-shaped failure's blind spot: no two adjacent characters in
"在湖边，在湖边" are equal, so the character rule never fired and the loop reached
the screen intact.
"""

import unittest

from server.caption_window import (
    _CHAR_RUN_MAX,
    _CLAUSE_RUN_MAX,
    _collapse_repeats,
)


class CharacterRunTests(unittest.TestCase):
    def test_keeps_three_identical_characters(self):
        self.assertEqual(_collapse_repeats("好好好"), "好好好")

    def test_drops_a_fourth_identical_character(self):
        self.assertEqual(_collapse_repeats("好好好好"), "好好好")

    def test_characters_that_differ_are_not_a_run(self):
        self.assertEqual(_collapse_repeats("湖边湖边湖边"), "湖边湖边湖边")

    def test_threshold_is_what_the_name_says(self):
        self.assertEqual(_CHAR_RUN_MAX, 3)


class ClauseRunTests(unittest.TestCase):
    def test_three_occurrences_are_within_limit(self):
        # Three is allowed, not a trigger. A quotation, a correction, and a list
        # that returns to its own subject all repeat a phrase three times in
        # ordinary Chinese, and cutting them mangles a line that was fine.
        self.assertEqual(_collapse_repeats("在湖边，在湖边，在湖边"), "在湖边，在湖边，在湖边")

    def test_two_occurrences_are_within_limit(self):
        self.assertEqual(_collapse_repeats("在湖边，在湖边"), "在湖边，在湖边")

    def test_drops_the_fourth_occurrence(self):
        self.assertEqual(_collapse_repeats("在湖边，在湖边，在湖边，在湖边"), "在湖边，在湖边，在湖边")

    def test_cuts_the_loop_the_model_actually_produced(self):
        # Real output: six clauses alternating two distinct phrases. The window
        # counts a clause against the last four, so the fifth occurrence trips it
        # and the line is cut at four -- truncated mid-sentence, which is the
        # intended trade against an unbounded loop.
        looped = "在湖边，湖边，在湖边，在湖边，在湖边，在湖边，"
        self.assertEqual(_collapse_repeats(looped), "在湖边，湖边，在湖边，在湖边")

    def test_distinct_clauses_survive(self):
        text = "冰雪覆盖着湖泊，湖泊在远方上空，在湖边。"
        self.assertEqual(_collapse_repeats(text), text)

    def test_a_clause_recurring_far_apart_is_coincidence(self):
        # Same clause three times in a long line is not a decoder stuck in a
        # loop, and truncating here would cut a sentence that was going fine.
        text = "他说湖边，然后我们走到了桥上，最后又回到了湖边。"
        self.assertEqual(_collapse_repeats(text), text)

    def test_clause_threshold_matches_the_character_one(self):
        # Both are three, so a line is not judged by a stricter rule at one
        # granularity than the other.
        self.assertEqual(_CLAUSE_RUN_MAX, _CHAR_RUN_MAX)

    def test_clauses_are_checked_before_characters(self):
        # Both rules apply to this line: a fourth identical character collapses,
        # and so does a clause repeated past the limit.
        self.assertEqual(_collapse_repeats("切，切，切，切"), "切，切，切")


if __name__ == "__main__":
    unittest.main()
