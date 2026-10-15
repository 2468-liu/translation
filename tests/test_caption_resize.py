"""The caption window can be resized from either side.

Only the right edge could be dragged. `_edges` tested `event.x >= width - EDGE` and
returned a second element that was hardcoded False, so there was no left edge to grab
at all -- not a handle that misbehaved, a handle that was never defined. The width
arithmetic was fine; there was simply nothing on the left.

These run against a stub rather than a real window because `_edges` needs a canvas width
and an event position, and `_resize_geometry` needs an origin tuple. Neither needs Tk,
and a test that needs a display is a test that does not run.

The property that matters on the left edge is not the width. It is that the *right*
edge stays where it was: a left-edge drag has to move the window as it resizes, or the
text slides sideways while the pointer is being dragged.
"""

import unittest

from server.caption_window import EDGE, _Caption


class Event:
    def __init__(self, x: float, x_root: float | None = None, y_root: float = 0.0) -> None:
        self.x = x
        self.x_root = x if x_root is None else x_root
        self.y_root = y_root


class Stub:
    """The attributes _edges and _resize_geometry read."""

    def __init__(self, width: int = 760, x: int = 100, y: int = 200) -> None:
        self.canvas = type("C", (), {"winfo_width": lambda self_: width})()
        self._resize_origin = None
        self.width = width
        self.height = 69
        self.root = type("R", (), {"winfo_x": lambda self_: x,
                                   "winfo_y": lambda self_: y,
                                   "winfo_width": lambda self_: width})()
        self.x = x
        self.y = y


class EdgeDetectionTests(unittest.TestCase):
    def edges(self, stub, x):
        return _Caption._edges(stub, Event(x))

    def test_the_right_edge_is_detected(self):
        stub = Stub(width=760)
        on_x, _on_y, side = self.edges(stub, 760 - 2)
        self.assertTrue(on_x)
        self.assertEqual(side, "right")

    def test_the_left_edge_is_detected(self):
        stub = Stub(width=760)
        on_x, _on_y, side = self.edges(stub, 2)
        self.assertTrue(on_x)
        self.assertEqual(side, "left")

    def test_the_middle_is_not_an_edge(self):
        stub = Stub(width=760)
        on_x, _on_y, side = self.edges(stub, 380)
        self.assertFalse(on_x)
        self.assertIsNone(side)

    def test_the_edges_are_exactly_edge_pixels_wide(self):
        stub = Stub(width=760)
        self.assertEqual(self.edges(stub, EDGE - 1)[2], "left")
        self.assertIsNone(self.edges(stub, EDGE + 1)[2])
        self.assertEqual(self.edges(stub, 760 - EDGE)[2], "right")
        self.assertIsNone(self.edges(stub, 760 - EDGE - 1)[2])

    def test_on_a_narrow_window_the_deeper_edge_wins(self):
        # Below twice EDGE the two bands overlap, and "on an edge" would be true of
        # both. Picking the one the pointer is further inside is what makes the drag
        # do what the cursor promised.
        stub = Stub(width=20)
        self.assertEqual(self.edges(stub, 1)[2], "left")
        self.assertEqual(self.edges(stub, 19)[2], "right")


class LeftEdgeGeometryTests(unittest.TestCase):
    def begin(self, stub, side, at):
        stub._resize_origin = (at, 0, stub.width, stub.height, side, stub.root.winfo_x())

    def test_dragging_the_right_edge_grows_the_width_and_moves_nothing(self):
        stub = Stub(width=760, x=100)
        self.begin(stub, "right", at=500)
        x, _y, width, _h = _Caption._resize_geometry(stub, Event(0, x_root=560))
        self.assertEqual(width, 820)
        self.assertEqual(x, 100, "the right edge is what the pointer grabbed")

    def test_dragging_the_left_edge_shrinks_and_moves_the_window(self):
        stub = Stub(width=760, x=100)
        self.begin(stub, "left", at=105)
        x, _y, width, _h = _Caption._resize_geometry(stub, Event(0, x_root=155))
        self.assertEqual(width, 710)
        # The window slides right by exactly what the width gave up, so the right edge
        # stays at 100 + 760.
        self.assertEqual(x + width, 100 + 760)

    def test_the_right_edge_holds_for_every_drag_distance(self):
        stub = Stub(width=760, x=100)
        for moved in (-200, -50, 0, 50, 200):
            self.begin(stub, "left", at=105)
            x, _y, width, _h = _Caption._resize_geometry(stub, Event(0, x_root=105 + moved))
            self.assertEqual(
                x + width, 860,
                f"the right edge moved when the pointer moved {moved}px",
            )

    def test_the_width_has_a_floor_and_the_window_still_lines_up(self):
        stub = Stub(width=200, x=100)
        self.begin(stub, "left", at=105)
        x, _y, width, _h = _Caption._resize_geometry(stub, Event(0, x_root=9999))
        self.assertEqual(width, 120)
        self.assertEqual(x + width, 300, "clamping must not let the right edge drift")

    def test_no_resize_in_progress_means_no_geometry(self):
        stub = Stub()
        stub._resize_origin = None
        self.assertIsNone(_Caption._resize_geometry(stub, Event(10)))

    def test_release_does_not_recompute_anything(self):
        # _motion already applied the geometry. Doing it again on release would resize a
        # second time from a pointer that has left the window.
        #
        # Comments come off first. The paragraph above says "geometry" and so does the
        # comment in the method, so the first version of this failed on its own
        # explanation -- the same trap as the `if fitted != self.width` assertion in
        # test_caption_layout.
        import inspect

        source = inspect.getsource(_Caption._release)
        code = []
        for raw in source.splitlines():
            hash_at = raw.find("#")
            code.append(raw[:hash_at] if hash_at >= 0 else raw)
        body = "\n".join(code)
        for forbidden in ("geometry", "_resize_geometry", "winfo_width"):
            self.assertNotIn(forbidden, body)
        self.assertIn("self._drag = None", body)
        self.assertIn("self._resize_origin = None", body)


if __name__ == "__main__":
    unittest.main()