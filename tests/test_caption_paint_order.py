"""The caption canvas must never be empty while it is being repainted.

`_paint` used to open with `self.canvas.delete("all")`, so the canvas held nothing
from that statement until `_draw` called `create_text` several lines later. Everything
in between -- `_fit_width`, `wrap_caption`, `font.metrics` -- ran against an empty
canvas, and the gap is not symmetric across scripts: `font.measure` falls back to a
font search for glyphs outside the primary face, which costs 1.33ms per character for
Korean against 0.066ms for Chinese, and `wrap_caption` measures at every candidate
break point. Measured worst case was 169ms of blank canvas, ten frames at 60Hz.

These tests drive `_paint` against a canvas that records the order of `create_text` and
`delete`, because the property is the *order* and not the end state: a canvas that ends
up with exactly one item looks identical either way, and only the sequence distinguishes
drawing-before-clearing from clearing-before-drawing.

The empty-body path is deliberately still clear-first. Segment changes set `self.line`
to `""` and exist to take the text off the screen, so there clearing is the point.

Two of these tests are order guards and two are end-state guards, and only the first
two distinguish the two implementations. A mutation check that reverts the ordering
fails `test_new_text_is_created_before_anything_is_deleted` and
`test_repaint_keeps_the_canvas_populated_throughout`; the other two pass either way and
exist to catch over-deletion, not mis-ordering.
"""

import unittest
from unittest import mock

from server.caption_window import _Caption


class _Canvas:
    """Records the sequence of drawing calls the way Tk would receive them."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []
        self.items: dict[int, str] = {}
        self._next = 1

    def create_text(self, *args, **kwargs) -> int:
        item = self._next
        self._next += 1
        self.items[item] = kwargs.get("text", "")
        self.calls.append(("create", item))
        return item

    def delete(self, *args) -> None:
        # Tk's canvas.delete(tag) removes the tagged item and leaves the rest, while
        # delete("all") removes everything. An earlier version of this double ignored
        # the argument and wiped the dict either way, which made the tests below fail
        # for a reason that had nothing to do with the paint order under test.
        targets = list(self.items) if not args or args[0] == "all" else [
            item for item in self.items if item == args[0]
        ]
        for item in targets:
            self.items.pop(item, None)
            self.calls.append(("delete", item))

    def find_all(self) -> list[int]:
        return sorted(self.items)

    def configure(self, **kwargs) -> None:
        pass

    def winfo_width(self) -> int:
        return self.width

    def winfo_height(self) -> int:
        return self.height


class _Font:
    """A stand-in whose measure cost is irrelevant; only the call order is asserted."""

    def measure(self, text: str) -> int:
        return len(text) * 20

    def metrics(self, kind: str) -> int:
        return 30 if kind == "linespace" else 10


class _Root:
    def __init__(self) -> None:
        self.geometry_calls: list[str] = []
        self.idletasks = 0

    def winfo_x(self) -> int:
        return 100

    def winfo_width(self) -> int:
        return self.width

    def winfo_y(self) -> int:
        return 100

    def geometry(self, value: str) -> None:
        self.geometry_calls.append(value)

    def update_idletasks(self) -> None:
        self.idletasks += 1


def _window(text: str = "你好", width: int = 760) -> object:
    """The smallest object _paint actually touches, built by hand."""
    stub = mock.Mock()
    stub.canvas = _Canvas()
    stub.canvas.width = width
    stub.canvas.height = 90
    stub.root = _Root()
    stub.root.width = width
    stub.root.height = 90
    stub.font_line = _Font()
    stub.line = text
    stub.visible = True
    stub.closed = False
    stub.width = width
    stub.height = 90
    stub.screen_width = 1920
    stub.width_locked = False
    stub._line_since = 0.0
    stub._trace_action = ""
    stub._trace_sent = 0.0
    stub._trace_recv = 0.0
    stub._report_dwell = mock.Mock()
    stub._fit_width = lambda body, wrap: _Caption._fit_width(stub, body, wrap)
    # Both of these have to be bound by hand. mock.Mock() invents any attribute it has
    # not been given, so leaving _draw unbound meant the real method never ran and the
    # canvas stayed empty -- three failures that looked like a paint-order bug and were
    # a missing binding.
    stub._draw = lambda text, font, wrap: _Caption._draw(stub, text, font, wrap)
    return stub


class PaintOrderTests(unittest.TestCase):
    def test_new_text_is_created_before_anything_is_deleted(self):
        stub = _window("你好")
        # One item already on screen from a previous paint, so the delete has a victim.
        stub.canvas.create_text(text="上一句")
        stub.canvas.calls.clear()

        with mock.patch("server.caption_window.perf.emit"):
            _Caption._paint(stub)

        kinds = [kind for kind, _ in stub.canvas.calls]
        self.assertEqual(
            kinds,
            ["create", "delete"],
            f"expected the new item before the clear, got {stub.canvas.calls}",
        )

    def test_repaint_keeps_the_canvas_populated_throughout(self):
        """The blank frame is the state *at the moment of the draw*, not before the clear.

        Watching delete and asserting the canvas was non-empty then cannot see the
        bug: under the old order the canvas still held the previous line when delete
        ran, so the check passed while the screen went blank for the whole of the
        font measurement that followed. The state that matters is what create_text
        finds in front of it.
        """
        stub = _window("你好世界")
        stub.canvas.create_text(text="上一句")
        stub.canvas.calls.clear()

        empty_when_drawing: list[int] = []
        real_create = stub.canvas.create_text

        def watched_create(*args, **kwargs) -> int:
            empty_when_drawing.append(len(stub.canvas.find_all()))
            return real_create(*args, **kwargs)

        stub.canvas.create_text = watched_create

        with mock.patch("server.caption_window.perf.emit"):
            _Caption._paint(stub)

        self.assertTrue(empty_when_drawing, "the paint never drew anything")
        self.assertNotIn(
            0,
            empty_when_drawing,
            "the canvas was empty when the new text was drawn, which is the blank frame",
        )

    def test_the_new_text_survives_the_clear(self):
        stub = _window("第二句")
        stub.canvas.create_text(text="第一句")

        with mock.patch("server.caption_window.perf.emit"):
            _Caption._paint(stub)

        self.assertEqual(len(stub.canvas.find_all()), 1)
        self.assertEqual(list(stub.canvas.items.values()), ["第二句"])

    def test_empty_body_still_clears_the_screen(self):
        # Segment changes blank the caption on purpose. If this path stopped clearing,
        # the previous line would hang on screen instead of disappearing.
        #
        # Empty body needs *both* an empty line and a hidden window: a visible window
        # with no text shows the "实时字幕" placeholder instead, so line="" alone
        # reaches _draw rather than this branch.
        stub = _window("")
        stub.visible = False
        stub.canvas.create_text(text="上一句")
        stub.canvas.calls.clear()

        with mock.patch("server.caption_window.perf.emit"):
            _Caption._paint(stub)

        self.assertEqual(stub.canvas.find_all(), [])
        self.assertEqual([kind for kind, _ in stub.canvas.calls], ["delete"])

    def test_first_paint_on_an_empty_canvas_still_draws(self):
        stub = _window("第一句")

        with mock.patch("server.caption_window.perf.emit"):
            _Caption._paint(stub)

        self.assertEqual(list(stub.canvas.items.values()), ["第一句"])


if __name__ == "__main__":
    unittest.main()