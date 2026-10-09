"""The application layer of clause tracking, tested without a window.

`server/caption_clauses.py` exists because the comparison belongs where the data is,
not in the renderer. A canvas text item holds one string, so a renderer holding one
string cannot be told which part of it changed -- which is why `_punct_gate` computed
a boundary and then discarded it, and why the paint-order tests needed a fake canvas
whose own bugs looked like failures in the code under test.

These are pure functions over strings. Nothing here opens a window, so a regression
shows up as a failing assertion rather than as a blank caption that nobody can explain.

The cases that matter are the ones the data said would happen: only 26% and 19% of
consecutive fragments are prefix relationships, so clauses get inserted and removed,
and an index-by-index comparison would report everything after the first insertion as
changed.
"""

import unittest

from server.caption_clauses import (
    align_clauses,
    diff_clauses,
    introduces_boundary,
    punctuation_count,
    split_clauses,
)


class SplitTests(unittest.TestCase):
    def test_each_mark_ends_a_clause_and_stays_with_it(self):
        for mark in "。！？；，、：":
            self.assertEqual(
                split_clauses(f"你{mark}"),
                [f"你{mark}"],
                f"{mark!r} should end a clause and stay attached to it",
            )

    def test_a_trailing_fragment_without_a_mark_is_kept(self):
        # Dropping it would mean the last clause of a line could never be shown, which
        # is the mistake the boundary test avoided by counting marks instead of
        # comparing sets.
        self.assertEqual(split_clauses("你。还在"), ["你。", "还在"])

    def test_spaces_alone_are_not_a_clause(self):
        self.assertEqual(split_clauses("  "), [])
        self.assertEqual(split_clauses("\n"), [])

    def test_clause_text_concatenates_back_to_the_line(self):
        line = "你有。还好？真的。"
        self.assertEqual("".join(split_clauses(line)), line)


class BoundaryTests(unittest.TestCase):
    def test_a_second_mark_of_the_same_kind_counts(self):
        self.assertTrue(introduces_boundary("你。", "你。还在。"))

    def test_a_repeated_mark_does_not_count(self):
        self.assertFalse(introduces_boundary("你。", "你。还在"))

    def test_repeats_are_counted_not_deduplicated(self):
        self.assertEqual(punctuation_count("你。他。"), 2)


class AlignmentTests(unittest.TestCase):
    def test_an_appended_clause_keeps_the_earlier_ones(self):
        prev = ["你。", "还好。"]
        cur = ["你。", "还好。", "真的。"]
        self.assertEqual(align_clauses(prev, cur), [(0, 0), (1, 1)])

    def test_an_insertion_in_the_middle_does_not_shift_the_rest(self):
        # The case an index comparison gets wrong: inserting one clause in the middle
        # would make every later clause look changed.
        prev = ["甲。", "乙。", "丙。"]
        cur = ["甲。", "新。", "乙。", "丙。"]
        self.assertEqual(align_clauses(prev, cur), [(0, 0), (1, 2), (2, 3)])

    def test_a_removal_is_not_reported_as_three_changes(self):
        prev = ["甲。", "删掉。", "乙。"]
        cur = ["甲。", "乙。"]
        diff = diff_clauses(prev, cur)
        self.assertEqual([i for i, _ in diff.kept], [0, 2])
        self.assertEqual(diff.removed, [(1, "删掉。")])
        self.assertEqual(diff.changed, [])

    def test_alignment_of_empty_input(self):
        self.assertEqual(align_clauses([], ["你。"]), [])
        self.assertEqual(align_clauses(["你。"], []), [])


class DiffTests(unittest.TestCase):
    def test_an_unchanged_line_touches_nothing(self):
        clauses = ["你。", "还好。"]
        diff = diff_clauses(clauses, list(clauses))
        self.assertEqual(diff.touches, [])
        self.assertEqual(diff.unchanged_ratio, 1.0)

    def test_one_rewritten_clause_leaves_the_others_alone(self):
        prev = ["你。", "还好。", "真的。"]
        cur = ["你。", "现在很糟。", "真的。"]
        diff = diff_clauses(prev, cur)
        self.assertEqual([i for i, _ in diff.kept], [0, 2])
        self.assertEqual([j for _, j, _, _ in diff.changed], [1])
        self.assertEqual(diff.touches, [1])
        self.assertEqual(diff.unchanged_ratio, 2 / 3)

    def test_a_grown_tail_is_an_addition_not_a_rewrite(self):
        prev = ["你。"]
        cur = ["你。", "还在。"]
        diff = diff_clauses(prev, cur)
        self.assertEqual(diff.kept, [(0, 0)])
        self.assertEqual(diff.added, [(1, "还在。")])
        self.assertEqual(diff.removed, [])

    def test_a_clause_is_never_both_changed_and_removed(self):
        prev = ["甲。", "乙。", "丙。"]
        cur = ["甲。", "完全不同。"]
        diff = diff_clauses(prev, cur)
        changed_prev = {i for i, _, _, _ in diff.changed}
        removed_prev = {i for i, _ in diff.removed}
        self.assertEqual(changed_prev & removed_prev, set())
        self.assertEqual(changed_prev | removed_prev, {1, 2})

    def test_the_change_record_accounts_for_every_clause(self):
        prev = ["甲。", "乙。", "丙。"]
        cur = ["甲。", "乙改。", "新。"]
        diff = diff_clauses(prev, cur)
        accounted = (
            {j for _, j in diff.kept}
            | {j for _, j, _, _ in diff.changed}
            | {j for j, _ in diff.added}
        )
        self.assertEqual(accounted, set(range(len(cur))))
        self.assertEqual(
            {i for i, _ in diff.kept} | {i for i, _, _, _ in diff.changed}
            | {i for i, _ in diff.removed},
            set(range(len(prev))),
        )


if __name__ == "__main__":
    unittest.main()