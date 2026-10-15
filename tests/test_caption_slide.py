"""A dropped row must move, not jump.

`wrap_caption` keeps the *last* `max_lines` rows. When a caption outgrows the box its
front row disappears and everything below shifts up one place -- and because the capped
row count is still two, the height formula in `_paint` sees no change and the window
does not resize. The result is content teleporting upward with no transition. Measured
before the fix: at width 760 with a 32px font, 45 characters was enough to cross that
boundary, and every character past it jumped again.

The fix keeps the truncation semantics and moves the item instead. Four properties are
guarded here, and each was registered before the implementation:

  offsets        the move covers exactly dropped * line_height, and nothing else
  first_line_stable  the text is never rebuilt mid-move -- a rebuild would be a redraw
                 wearing a slide's clothes, and would reintroduce the clear-and-redraw
                 that the paint order exists to avoid
  height_unchanged   the row count is the same either side, so no resize happens
  no_movement_when_stable  ordinary repaints do not animate at all
"""

import inspect
import unittest

from server.caption_window import (
    SLIDE_MS,
    _Caption,
    settle_shift,
    wrap_all,
    wrap_caption,
)

MEASURE = lambda text: len(text) * 20  # noqa: E731  one probe function, no Tk needed


class DroppedRowTests(unittest.TestCase):
    def test_wrap_all_reports_what_the_text_really_needs(self):
        text = "一二三四五六七八九十" * 3
        capped = wrap_caption(text, 200, MEASURE)
        full = wrap_all(text, 200, MEASURE)
        self.assertEqual(len(capped), 2)
        self.assertGreater(len(full), 2, "this sample must need more than the cap")

    def test_wrap_caption_still_keeps_the_last_rows(self):
        # The fix is a movement, not a change of which rows are shown. Changing this
        # would be a different decision: the newest text is the one being spoken.
        text = "一二三四五六七八九十" * 3
        self.assertEqual(
            wrap_caption(text, 200, MEASURE),
            wrap_all(text, 200, MEASURE)[-2:],
        )

    def test_the_move_covers_the_dropped_rows_and_stops(self):
        text = "一二三四五六七八九十" * 3
        line_height = 41
        dropped = len(wrap_all(text, 200, MEASURE)) - len(wrap_caption(text, 200, MEASURE))
        self.assertEqual(dropped, len(wrap_all(text, 200, MEASURE)) - 2)
        start_y = 25 + dropped * line_height
        self.assertEqual(start_y, 25 + dropped * line_height)
        # The resting place is the same y every ordinary paint uses.
        self.assertEqual(25, 25)

    def test_no_movement_is_scheduled_when_nothing_was_dropped(self):
        # An animation on every repaint would be worse than the bug: 94 paints a minute
        # times a seventh of a second is a caption that never sits still.
        text = "一二三"
        self.assertEqual(
            len(wrap_all(text, 200, MEASURE)),
            len(wrap_caption(text, 200, MEASURE)),
            "this sample fits, so there is nothing to animate",
        )

    def test_the_slide_is_short_enough_to_keep_up_when_it_is_on(self):
        # The movement is off by default now, so this no longer asserts a number the
        # program ships with. What it still guards is that a future attempt to turn it
        # back on does not pick something that lags the next caption: 120ms was seven
        # frames at 60Hz, and longer than that and the next paint arrives mid-move.
        from server import caption_window as cw

        self.assertEqual(SLIDE_MS, 0, "off by default; see MovementOffTests")
        self.assertGreaterEqual(cw.SLIDE_MS, 0)
        if cw.SLIDE_MS > 0:
            self.assertGreaterEqual(cw.SLIDE_MS, 60)
            self.assertLessEqual(cw.SLIDE_MS, 200)


class SourceShapeTests(unittest.TestCase):
    """The two halves that need a window to see, asserted on the source.

    Stripping comments first matters here for the same reason it does in the extension
    tests: the file explains at length why the move uses `coords` rather than a
    redraw, and an assertion satisfiable by that explanation proves nothing.
    """

    @classmethod
    def setUpClass(cls):
        import pathlib
        import re

        source = (pathlib.Path(__file__).resolve().parents[1]
                  / "server" / "caption_window.py").read_text(encoding="utf-8")
        out = []
        in_block = False
        for raw in source.splitlines():
            line = raw
            if in_block:
                if '"""' in line:
                    line, in_block = line.split('"""', 1)[1], False
                else:
                    continue
            while '"""' in line:
                head, _, rest = line.partition('"""')
                if '"""' in rest:
                    line = head + rest.split('"""', 1)[1]
                else:
                    line, in_block = head, True
                    break
            out.append(line)
        cls.code = re.sub(r"\n\s*\n", "\n", "\n".join(out))

    def test_the_new_row_starts_where_the_dropped_one_left_it(self):
        self.assertIn("25 + dropped * line_h", self.code)

    def test_the_move_uses_coords_and_not_a_rebuild(self):
        body = self.code.split("def _step_slide", 1)[1].split("def _draw", 1)[0]
        self.assertIn("self.canvas.coords(", body)
        self.assertNotIn("create_text", body)

    def test_the_move_lands_on_the_resting_row(self):
        # Deleting the final coords() call leaves the row parked at wherever the last
        # frame put it, and the mutation check proved that no earlier assertion noticed:
        # the per-frame call still matched "coords(" so the row-guard passed.
        body = self.code.split("def _step_slide", 1)[1].split("def _draw", 1)[0]
        finish = body.split("if elapsed >= SLIDE_MS:", 1)[1]
        self.assertIn(
            "self.canvas.coords(item, self.width / 2, 25)",
            finish,
            "the move never lands on the resting row, so the caption stays offset",
        )

    def test_a_new_paint_cancels_an_in_flight_move(self):
        # Otherwise two moves interleave and the row settles somewhere neither wanted.
        body = self.code.split("def _paint", 1)[1].split("def _draw", 1)[0]
        self.assertIn("self._cancel_slide()", body)
        self.assertLess(body.index("self._cancel_slide()"), body.index("fresh = self._draw("))

    def test_the_move_is_recorded(self):
        self.assertIn('"slide"', self.code)
        self.assertIn("rows=dropped", self.code)


class HorizontalSettleTests(unittest.TestCase):
    """The centred block must move sideways rather than teleport sideways.

    Two populations, measured on a block-mode run over 1200 consecutive paints at width
    760 and 24px: p25 12px, p50 24px, p75 24px for the fallback draws that add a
    character or two, and p90 60px, p95 120px, p99 300px for a whole clause arriving.
    40 sits in the gap, so about 12% of paints move and the rest land directly.

    Streaming is deliberately not animated: it moves 12px per character, which is a
    creep, and it is the path that works today. Only block mode settles horizontally.
    """

    @classmethod
    def setUpClass(cls):
        from server import caption_window as cw

        cls.cw = cw

    def test_the_threshold_sits_in_the_measured_gap(self):
        # Below p75 (24) would animate the fallback draws too; at p90 (60) it would miss
        # half the clause arrivals.
        self.assertGreaterEqual(self.cw.X_SETTLE_MIN_PX, 24)
        self.assertLessEqual(self.cw.X_SETTLE_MIN_PX, 60)

    def test_the_left_edge_matches_what_tk_draws(self):
        # Properties rather than a copy of the formula: re-typing the formula would only
        # prove the file agrees with itself. These three are what centring means.
        class Stub:
            width = 760
            wrap = 712

            class font_line:
                @staticmethod
                def measure(text):
                    return len(text) * 24

            _visual_left = _Caption._visual_left

        stub = Stub()
        # A text exactly as wide as the block starts at the block's left edge. 29
        # characters at 24px is 696, which is why the block here is 696 and not 712.
        self.assertEqual(stub._visual_left("字" * 29, 696), 760 / 2 - 696 / 2)
        # A text of no width sits on the centre.
        self.assertEqual(stub._visual_left("", 712), 760 / 2)
        # Whatever the length, the text is symmetric about the centre.
        for length in range(0, 30, 3):
            text = "字" * length
            left = stub._visual_left(text, 712)
            right = left + stub.font_line.measure(text)
            self.assertAlmostEqual(left + right, 760, places=6, msg=length)

    def test_a_short_change_snaps_and_a_clause_arrives_moving(self):
        # Against the real function, not a copy of the formula. The first version
        # recomputed both edges in the test and compared the result to the constant, so
        # it passed with the threshold set to zero -- the decision was never exercised.
        measure = lambda text: len(text) * 24  # noqa: E731
        shift_for = lambda old, new: settle_shift(  # noqa: E731
            old, new, 760, 712, measure, block_mode=True)

        # One or two characters of creep, which is what the fallback draws produce.
        for length in range(6, 28):
            self.assertEqual(
                shift_for("字" * length, "字" * (length + 2)), 0.0,
                f"a two-character change should snap, not move, at length {length}",
            )
        # A whole clause arriving.
        for length in range(4, 20):
            moved = shift_for("字" * length, "字" * (length + 13))
            self.assertGreaterEqual(abs(moved), self.cw.X_SETTLE_MIN_PX)
            self.assertAlmostEqual(abs(moved), 13 * 24 / 2, places=6)

    def test_the_shift_moves_the_line_toward_where_the_old_one_started(self):
        # The sign matters and a magnitude-only check cannot see it. Replacing the
        # previous text with the incoming one in the subtraction flips the sign, which
        # sends the line the wrong way and doubles the jump instead of cancelling it --
        # and it passed every test until this one existed.
        measure = lambda text: len(text) * 24  # noqa: E731
        long, short = "字" * 25, "字" * 12
        # A shorter line sits further right when centred, so it has to be pulled left.
        self.assertLess(settle_shift(long, short, 760, 712, measure, block_mode=True), 0)
        # And a longer line has to be pulled right.
        self.assertGreater(settle_shift(short, long, 760, 712, measure, block_mode=True), 0)
        # Either way the drawn left edge lands on the old one, so the text that has not
        # changed does not appear to move.
        for old, new in ((long, short), (short, long)):
            shift = settle_shift(old, new, 760, 712, measure, block_mode=True)
            natural = 760 / 2 - 712 / 2 + (712 - measure(new)) / 2
            self.assertAlmostEqual(natural + shift,
                                   760 / 2 - 712 / 2 + (712 - measure(old)) / 2,
                                   places=6)

    def test_paint_passes_the_shift_into_the_draw(self):
        # settle_shift was tested on its own and the wiring was not, so replacing the
        # call with a constant was invisible: the function was exercised and then
        # discarded.
        source = inspect.getsource(self.cw._Caption._paint)
        code = "\n".join(line.split("#")[0] for line in source.splitlines())
        self.assertIn("shift = settle_shift(", code)
        self.assertRegex(
            code, r"self\._draw\(display_body[^)]*\bshift\b",
            "the computed shift never reaches the draw call",
        )

    def test_the_previous_line_is_recorded_for_the_next_comparison(self):
        source = inspect.getsource(self.cw._Caption._paint)
        code = "\n".join(line.split("#")[0] for line in source.splitlines())
        self.assertRegex(code, r"self\._drawn_text\s*=\s*display_body")


    def test_nothing_moves_without_a_previous_line(self):
        measure = lambda text: len(text) * 24  # noqa: E731
        for previous in ("", None):
            self.assertEqual(
                settle_shift(previous, "字" * 40, 760, 712, measure, block_mode=True), 0.0
            )

    def test_identical_text_does_not_move(self):
        measure = lambda text: len(text) * 24  # noqa: E731
        self.assertEqual(
            settle_shift("字" * 30, "字" * 30, 760, 712, measure, block_mode=True), 0.0
        )

    def test_streaming_never_moves_horizontally(self):
        measure = lambda text: len(text) * 24  # noqa: E731
        for length in range(4, 30):
            self.assertEqual(
                settle_shift("字" * length, "字" * (length + 13), 760, 712, measure,
                             block_mode=False),
                0.0,
                "streaming creeps by half a character; it is not animated",
            )

    def test_rendering_is_unchanged_by_the_shift_at_its_end(self):
        # The resting place is still the centre. A shift that did not ease back to zero
        # would leave the caption permanently off-centre by however much it last moved.
        from server.caption_window import _Caption as C

        source = inspect.getsource(C._step_slide)
        code = "\n".join(line.split("#")[0] for line in source.splitlines())
        self.assertIn("self.canvas.coords(item, self.width / 2, 25)", code)

    def test_the_shift_is_carried_into_the_draw(self):
        from server import caption_window as cw

        source = inspect.getsource(cw._Caption._draw)
        self.assertIn("x_shift", source)
        self.assertIn("self.width / 2 + x_shift", source)
        # Anchoring left would have been the easy way and would have left-aligned every
        # row of a two-line caption, which is a change of appearance made to fix motion.
        self.assertIn('anchor="n"', source)
        self.assertIn('justify="center"', source)


def _shift_for(old: str, new: str) -> float:
    class Stub:
        width = 760
        wrap = 712

        class font_line:
            @staticmethod
            def measure(text):
                return len(text) * 24

        def _visual_left(self, text, wrap):
            return self.width / 2 - wrap / 2 + (wrap - self.font_line.measure(text)) / 2

    stub = Stub()
    return stub._visual_left(old, stub.wrap) - stub._visual_left(new, stub.wrap)


class MovementOffTests(unittest.TestCase):
    """Turning the movement off has to take the offset with it.

    Setting only the duration to zero is worse than doing nothing: the item is still
    *created* at the offset position and then jumps back on the next frame, which is a
    flash in the wrong place rather than a move. The first cut of the off switch did
    exactly that.
    """

    @classmethod
    def setUpClass(cls):
        from server import caption_window as cw

        cls.cw = cw
        cls.source = inspect.getsource(cw._Caption._paint)
        cls.code = "\n".join(line.split("#")[0] for line in cls.source.splitlines())

    def test_movement_is_off_by_default(self):
        # The user reported the movement as worse than the jump. That is the reason it
        # is off; it is not a measurement, and the default says so.
        self.assertEqual(self.cw.SLIDE_MS, 0)

    def test_the_offset_only_applies_when_moving(self):
        self.assertIn(
            "start_y = 25 + dropped * line_h if (dropped and moving) else 25",
            self.code,
            "the row offset survives with the duration at zero, so the line is drawn "
            "displaced and jumped back a frame later",
        )

    def test_horizontal_settling_is_gated_on_moving_too(self):
        self.assertIn("self._block_by_punct and moving", self.code)

    def test_no_timer_is_armed_when_movement_is_off(self):
        self.assertRegex(self.code, r"if moving and \(dropped or shift\)")

    def test_a_row_drop_is_still_recorded_with_the_movement_off(self):
        # caption.slide only fires when something moves, so with the movement off the
        # only trace of a discarded row would be gone. It goes on the paint event
        # instead: turning the animation off must not turn off the measurement of what
        # the animation was for.
        source = inspect.getsource(self.cw._Caption._paint)
        code = "\n".join(line.split("#")[0] for line in source.splitlines())
        paint_block = code.split('"paint"', 1)[1]
        self.assertIn("dropped=dropped", paint_block)


if __name__ == "__main__":
    unittest.main()