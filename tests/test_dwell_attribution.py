"""A dwell gap must say what filled it, because the two causes need opposite fixes.

caption.dwell reports how long the previous sentence stayed up. On
samples/lecture_mix.wav that number was p50 1622ms with 68 of 68 gaps over a second,
which reads as the hold being too long. It may not be. That material has 18 to 22 second
stretches of film-only audio where the narrator is not speaking, so a long gap there is
the material and there is nothing to fix -- while a gap with text already sitting in
_pending is the hold delaying something that was ready. Shortening the hold helps the
second and cannot help the first, and it costs readability on both.

So the event now carries why=held|empty and splits the gap into waiting and idle. These
tests pin the split, because a field that is present but never set to the interesting
value is the same as no field: the classification has to be reachable from the state the
window actually keeps, not only from a state a test can arrange.

Also pinned: the attribution must not change behaviour. It is instrumentation on a
path that decides when to draw, and a fix that made captions draw differently would be
judged on numbers this change was supposed to leave alone.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from server import caption_window as cw


def make_window():
    """A window with no Tk, so the attribution can be exercised headless.

    _report_dwell takes the paint time as an argument precisely so it does not need a
    clock: the tests set the gap by setting _line_since and passing a now.
    """
    win = cw._Caption.__new__(cw._Caption)
    win._pending_since = 0.0
    win._line_since = 0.0
    win._prev_chars = 12
    win.line = "you call it a day"
    win._trace_action = "swap"
    return win


def report(win, now: float):
    with patch.object(cw.perf, "emit") as emit:
        win._report_dwell(now)
    return emit


def dwell_of(emit):
    found = [c for c in emit.call_args_list if c.args[1] == "dwell"]
    assert len(found) == 1, f"expected exactly one dwell event, got {len(found)}"
    return found[0].kwargs


class AttributionTests(unittest.TestCase):
    def test_held_when_text_was_ready(self):
        win = make_window()
        win._line_since = 98.0          # gap 2.0s
        win._pending_since = 99.0       # text became drawable 1.0s into it
        kw = dwell_of(report(win, 100.0))
        self.assertEqual(kw["why"], "held")
        self.assertAlmostEqual(kw["gap"], 2000.0, places=1)
        self.assertAlmostEqual(kw["waiting"], 1000.0, places=1)
        self.assertAlmostEqual(kw["idle"], 1000.0, places=1)

    def test_empty_when_nothing_arrived(self):
        """The film-only case: a long gap with no text anywhere. why=empty, so nobody
        goes and shortens the hold for it."""
        win = make_window()
        win._line_since = 90.0          # gap 10.0s
        win._pending_since = 0.0
        kw = dwell_of(report(win, 100.0))
        self.assertEqual(kw["why"], "empty")
        self.assertAlmostEqual(kw["gap"], 10000.0, places=1)
        self.assertAlmostEqual(kw["waiting"], 0.0, places=1)
        self.assertAlmostEqual(kw["idle"], 10000.0, places=1)

    def test_text_arriving_at_the_very_start_is_all_waiting(self):
        win = make_window()
        win._line_since = 98.0
        win._pending_since = 98.0
        kw = dwell_of(report(win, 100.0))
        self.assertEqual(kw["why"], "held")
        self.assertAlmostEqual(kw["waiting"], 2000.0, places=1)
        self.assertAlmostEqual(kw["idle"], 0.0, places=1)

    def test_negative_waiting_cannot_appear(self):
        """A clock that went backwards, or a stamp from after the paint, would make
        waiting exceed the gap. idle is the difference, so it must not go negative
        either -- a negative figure in this event would be read as a real quantity."""
        win = make_window()
        win._line_since = 98.0
        win._pending_since = 100.5      # after the paint: clock skew or a stale stamp
        kw = dwell_of(report(win, 100.0))
        self.assertGreaterEqual(kw["waiting"], 0.0)
        self.assertGreaterEqual(kw["idle"], 0.0)
        self.assertAlmostEqual(kw["waiting"] + kw["idle"], kw["gap"], places=1)


class DoesNotChangeBehaviourTests(unittest.TestCase):
    def test_pending_since_is_cleared_after_the_gap_is_reported(self):
        """Otherwise the next gap would inherit this one's arrival time and report a
        wait that began before its own clock did."""
        win = make_window()
        win._line_since = 98.0
        win._pending_since = 99.0
        report(win, 100.0)
        self.assertEqual(win._pending_since, 0.0)

    def test_arrival_records_only_the_first_text_of_a_gap(self):
        """Later packets during the same gap must not push the stamp forward, or the
        waiting share would shrink every time another partial lands."""
        win = make_window()
        win._pending_since = 0.0
        with patch.object(cw.time, "monotonic", return_value=50.0):
            win._note_pending("first")
        first = win._pending_since
        with patch.object(cw.time, "monotonic", return_value=52.0):
            win._note_pending("second")
        self.assertEqual(win._pending_since, first)

    def test_empty_text_does_not_open_a_wait(self):
        win = make_window()
        win._pending_since = 0.0
        with patch.object(cw.time, "monotonic", return_value=50.0):
            win._note_pending("")
        self.assertEqual(win._pending_since, 0.0)

    def test_hide_clears_the_stamp(self):
        """A stamp surviving a hide would be charged to the next session's first gap."""
        win = make_window()
        win._pending_since = 42.0
        win.line = ""
        win.status = ""
        win._gaps = []
        win._close_wait = MagicMock()
        win._hold_job = None
        win.root = MagicMock()
        win.have_translation = False
        win.close = MagicMock()
        win._segment_seq = 0
        win._wait_segment = 0
        win.hide()
        self.assertEqual(win._pending_since, 0.0)


if __name__ == "__main__":
    unittest.main()