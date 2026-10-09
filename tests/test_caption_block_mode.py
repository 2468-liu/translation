"""Block mode: a clause is drawn when its punctuation arrives, not before.

Two properties carry the design and both are easy to get backwards.

The boundary is a set difference, not a membership test. Punctuation stays on the
line once drawn, so 62% and 78% of arriving fragments contain a mark while only
16.5 a minute introduce one. Keying on presence fires on every fragment and reduces
the design to painting per batch -- measured at 94 draws a minute instead of 17.

Mid-clause revisions are impossible rather than uncommon: nothing is drawn while a
clause is open, so there is no draw for a later fragment to revise. That is a
structural consequence, not a rate, and these tests assert the structure.

The pure helpers are exercised directly because the drawing path needs Tk. The gate's
own accounting -- that a clause is not drawn twice and that the fallback reports
itself -- is checked against a stub window.
"""

import unittest

from server.caption_window import (
    CLAUSE_MAX_MS,
    TERMINAL_PUNCT,
    introduces_boundary,
    punctuation_marks,
)


class BoundaryTests(unittest.TestCase):
    def test_a_mark_already_on_the_line_does_not_fire(self):
        # The line ends a clause; the next fragment repeats that mark while growing the
        # following clause. Membership would say boundary, set difference says no.
        self.assertFalse(introduces_boundary("你有友好的饮料。", "你有友好的饮料。他"))

    def test_each_kind_of_terminal_mark_counts(self):
        for ch in TERMINAL_PUNCT:
            self.assertTrue(
                introduces_boundary("你有", f"你有{ch}"),
                f"{ch} should end a clause",
            )

    def test_a_clause_that_never_repeats_its_mark_still_fires(self):
        self.assertFalse(introduces_boundary("你有", "你有那"))
        self.assertTrue(introduces_boundary("你有那", "你有那。"))

    def test_a_swapped_mark_does_end_the_clause(self):
        # The line carried a comma and now carries a full stop. The comma is gone and
        # the full stop was not on screen, so this is a boundary. Written the other way
        # round first, and the measurement settled it: set difference fires here, and
        # set difference is also the only variant that matches the clause count.
        self.assertTrue(introduces_boundary("你有，", "你有。"))

    def test_a_repeated_mark_is_not_a_boundary_and_the_fallback_covers_it(self):
        # Known limitation of comparing sets rather than counts: a second full stop on
        # the same line adds nothing new, so that clause does not paint on arrival and
        # waits for CLAUSE_MAX_MS instead. Comparing counts instead was measured and is
        # wrong -- 63.5 and 89.2 firings a minute against 53 and 56 clauses, because a
        # revised clause makes the count oscillate and every swing counts.
        self.assertFalse(introduces_boundary("你有。", "你有。他。"))

    def test_the_fallback_is_between_the_measured_percentiles(self):
        # Measured p25 of the clause's first-character-to-punctuation interval was 14ms
        # and p75 was 966ms. The fallback sits inside that band rather than at either
        # end, because at 14ms it would fire on almost every clause and at 966ms it
        # would not be a fallback at all.
        self.assertGreater(CLAUSE_MAX_MS, 14)
        self.assertLess(CLAUSE_MAX_MS, 966)


class GateAccountingTests(unittest.TestCase):
    """The gate's own bookkeeping, against a stub that records what was drawn."""

    def _window(self, **attrs):
        import unittest.mock as mock

        stub = mock.Mock()
        stub.line = ""
        stub._clause_t0 = 0.0
        stub._clause_pending = ""
        stub._clause_job = None
        stub._block_hits = 0
        stub._block_timeouts = 0
        stub.drawn = []
        stub.jobs = []
        stub.root.after = lambda ms, fn: (stub.jobs.append((ms, fn)), len(stub.jobs))[1]
        stub.root.after_cancel = lambda job: None
        stub._apply_line = lambda text, action="": (
            stub.drawn.append((text, action)),
            setattr(stub, "line", text),
        )[0]
        for k, v in attrs.items():
            setattr(stub, k, v)
        return stub

    def _gate(self, stub, text, now):
        import unittest.mock as mock

        with mock.patch("server.caption_window.time.monotonic", return_value=now):
            from server.caption_window import _Caption

            return _Caption._punct_gate(stub, text)

    def test_an_open_clause_is_not_drawn(self):
        stub = self._window()
        action = self._gate(stub, "你", 100.0)
        self.assertEqual(action, "block_wait")
        self.assertEqual(stub.drawn, [])

    def test_the_clause_is_drawn_when_its_mark_arrives(self):
        stub = self._window()
        self._gate(stub, "你", 100.0)
        action = self._gate(stub, "你。", 100.1)
        self.assertEqual(action, "block")
        self.assertEqual(stub.drawn, [("你。", "block")])

    def test_a_repeated_mark_does_not_draw_a_second_time(self):
        stub = self._window()
        self._gate(stub, "你。", 100.0)          # boundary: drawn
        self._gate(stub, "你。他", 100.1)        # same mark, next clause opens
        self._gate(stub, "你。他", 100.2)        # identical text again
        self.assertEqual(len(stub.drawn), 1)
        self.assertEqual(stub._block_hits, 1)

    def test_the_fallback_draws_what_arrived_and_says_so(self):
        import unittest.mock as mock

        stub = self._window()
        self._gate(stub, "你有那", 100.0)          # opens, schedules the fallback
        self.assertEqual(stub.drawn, [])
        with mock.patch("server.caption_window.time.monotonic", return_value=100.5), \
             mock.patch("server.caption_window.perf.emit") as emit:
            from server.caption_window import _Caption

            _Caption._flush_clause(stub)
        self.assertEqual(stub.drawn, [("你有那", "block")])
        self.assertEqual(stub._block_timeouts, 1)
        reasons = [c.kwargs["why"] for c in emit.call_args_list if c.args[1] == "block"]
        self.assertEqual(reasons, ["timeout"])

    def test_the_fallback_reports_a_boundary_when_the_mark_landed_meanwhile(self):
        import unittest.mock as mock

        stub = self._window()
        self._gate(stub, "你有。", 100.0)
        # A boundary would already have drawn and reset, so drive the fallback on a
        # clause whose mark is present but which has not been drawn yet.
        stub._clause_t0 = 100.0
        stub._clause_pending = "你。"
        # The line must not already carry the mark, or the gate would have drawn it on
        # arrival and this branch would be unreachable. Driving it directly is the only
        # way to cover it, which is also the honest description: in normal operation the
        # gate paints the moment a boundary appears and the fallback sees a timeout.
        stub.line = ""
        with mock.patch("server.caption_window.time.monotonic", return_value=100.6), \
             mock.patch("server.caption_window.perf.emit") as emit:
            from server.caption_window import _Caption

            _Caption._flush_clause(stub)
        reasons = [c.kwargs["why"] for c in emit.call_args_list if c.args[1] == "block"]
        self.assertEqual(reasons, ["boundary"])
        self.assertEqual(stub._block_timeouts, 0)

    def test_a_segment_boundary_drops_the_open_clause(self):
        import unittest.mock as mock

        stub = self._window()
        self._gate(stub, "你有", 100.0)
        self.assertNotEqual(stub._clause_t0, 0.0)
        with mock.patch("server.caption_window.time.monotonic", return_value=100.2), \
             mock.patch("server.caption_window.perf.emit"):
            from server.caption_window import _Caption

            _Caption._cancel_clause(stub)
        self.assertEqual(stub._clause_t0, 0.0)
        self.assertEqual(stub._clause_pending, "")
        # And the next segment's clause starts from nothing rather than resuming.
        self._gate(stub, "他", 100.4)
        self.assertEqual(stub.drawn, [])


if __name__ == "__main__":
    unittest.main()