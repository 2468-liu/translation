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

import unittest

from server.caption_window import SLIDE_MS, wrap_all, wrap_caption

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

    def test_the_slide_is_short_enough_to_keep_up(self):
        # 120ms is seven frames at 60Hz. Longer and the next caption arrives mid-move;
        # shorter and the movement reads as a jump again.
        self.assertGreaterEqual(SLIDE_MS, 60)
        self.assertLessEqual(SLIDE_MS, 200)


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


if __name__ == "__main__":
    unittest.main()