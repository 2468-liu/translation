"""「只有字」是视图模式，不是锁死模式。锁定已整体删除（2026-10-09 晚）。

Four things, all from the same cleanup, pinned here so none of them comes back:

1. 锁定 is gone end to end. It was a second checkbox in the caption window's header
   that sat at x=80 next to 只有字, and while it existed the window could not be
   dragged: position_locked defaulted to True and _press returned early. Worse, it was
   never re-coloured when 只有字 was switched on -- _apply_colors re-keyed only the
   只有字 control -- so what stayed on a transparent window was a solid black box with
   the word 锁定 in it. That box is what the screenshot showed over the page.

2. apply_text_only has to strip the frame on the window that is already on screen, not
   only on one that is about to be shown. The transparency that removes the black bar
   comes from WS_EX_LAYERED, which only _strip_frame sets, and a visible window never
   runs show() again -- so toggling 只有字 from the popup left the bar at #010203,
   which is a near-black that reads as "the click did nothing".

3. _broadcast_state must not put an identical frame on the wire twice. The extension
   answers a state frame with a style frame carrying the same value, that returns here
   as apply_text_only, and with no comparison on either side the two echoed on a
   WebSocket+UDP round trip for as long as the process lived.

4. No method may call itself. The deleted apply_position_lock left its two tail lines
   (`self._set_input_mode()` / `self._broadcast_state()`) inside _set_input_mode, so
   every call to it -- including the one in __init__ -- raised RecursionError and the
   caption process exited before it drew anything. That is why the subtitles vanished.
"""

import ast
import json
import unittest
from pathlib import Path
from unittest import mock

from server import caption_window
from server.caption_window import _Caption


SOURCE = (Path(__file__).resolve().parents[1] / "server" / "caption_window.py").read_text(
    encoding="utf-8"
)


def _class_methods(source: str) -> dict:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "_Caption":
            return {
                item.name: item
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    raise AssertionError("_Caption not found")


def _body_source(method: ast.FunctionDef) -> str:
    return ast.get_source_segment(SOURCE, method) or ""


class _FakeSock:
    def __init__(self, fail_times: int = 0) -> None:
        self.sent: list[tuple[dict, tuple]] = []
        self.attempts = 0
        self._fail_times = fail_times

    def sendto(self, data: bytes, address: tuple) -> None:
        self.attempts += 1
        if self.attempts <= self._fail_times:
            raise OSError("socket is not ready")
        self.sent.append((json.loads(data.decode("utf-8")), address))


class _StyleStub:
    """The four attributes _broadcast_state touches. No Tk, no window."""

    def __init__(self) -> None:
        self.text_only = True
        self.font_px = 20
        self.font_color = "#ffffff"
        self._broadcast_last = ""


class NoSelfRecursionTests(unittest.TestCase):
    def test_no_method_calls_itself(self):
        """The shape of the bug that killed the subtitles, not just its instance."""
        offenders = []
        methods = _class_methods(SOURCE)
        for name, node in methods.items():
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                func = call.func
                if (isinstance(func, ast.Attribute) and func.attr == name
                        and isinstance(func.value, ast.Name) and func.value.id == "self"):
                    offenders.append(f"{name}() calls self.{name}()")
        self.assertEqual(offenders, [], f"self-recursive method(s): {offenders}")

    def test_set_input_mode_only_touches_the_window_style(self):
        body = _body_source(_class_methods(SOURCE)["_set_input_mode"])
        self.assertIn("set_long(hwnd, GWL_EXSTYLE, exstyle)", body)
        self.assertNotIn("self._broadcast_state", body)


class TextOnlyIsAViewModeTests(unittest.TestCase):
    def test_text_only_restrips_the_frame_of_a_visible_window(self):
        body = _body_source(_class_methods(SOURCE)["apply_text_only"])
        self.assertIn("if self.visible:", body)
        self.assertIn("self._strip_frame()", body)

    def test_press_has_no_lock_guard(self):
        body = _body_source(_class_methods(SOURCE)["_press"])
        self.assertNotIn("position_locked", body)
        self.assertIn("self._drag = (", body)

    def test_strip_frame_never_makes_the_window_click_through(self):
        body = _body_source(_class_methods(SOURCE)["_strip_frame"])
        self.assertIn("WS_EX_LAYERED", body)
        self.assertNotIn("|= WS_EX_TRANSPARENT", body)

    def test_the_only_text_control_survives_text_only_mode(self):
        """It used to be placed only when 只有字 was off, which is the one mode where it
        matters: the control that turns 只有字 off was hidden by 只有字, so the only way
        back was the popup -- and then two surfaces showed the same state with only one
        of them able to change it."""
        body = _body_source(_class_methods(SOURCE)["_apply_colors"])
        self.assertIn("self.text_only_control.place(x=8, y=3", body)
        self.assertNotIn("self.text_only_control.place_forget()", body)
        # The indicator is where the state is drawn, so it cannot be keyed out with the
        # background: on a transparent window that is an invisible checkbox.
        self.assertIn('selectcolor="#000000"', body)

    def test_one_control_state_is_mirrored_not_copied(self):
        body = _body_source(_class_methods(SOURCE)["apply_text_only"])
        self.assertIn("self.text_only_var.set(self.text_only)", body)


class StateBroadcastTests(unittest.TestCase):
    def _broadcast(self, stub: _StyleStub, sock: _FakeSock) -> None:
        with mock.patch.object(caption_window, "_STATE_SOCK", sock):
            _Caption._broadcast_state(stub)

    def test_identical_state_is_not_sent_twice(self):
        sock = _FakeSock()
        stub = _StyleStub()

        self._broadcast(stub, sock)
        self._broadcast(stub, sock)

        self.assertEqual(sock.attempts, 1, "the identical second frame must be suppressed")
        payload, address = sock.sent[0]
        self.assertEqual(payload["kind"], "state")
        self.assertEqual(payload["value"]["textOnly"], True)
        self.assertEqual(address, ("127.0.0.1", caption_window.STATE_PORT))
        self.assertNotIn("positionLocked", payload["value"])

    def test_a_changed_state_is_sent(self):
        sock = _FakeSock()
        stub = _StyleStub()
        self._broadcast(stub, sock)
        stub.text_only = False
        self._broadcast(stub, sock)
        self.assertEqual([item[0]["value"]["textOnly"] for item in sock.sent], [True, False])

    def test_a_failed_send_is_not_remembered_as_delivered(self):
        """Otherwise one dropped packet would silence this window's control forever."""
        sock = _FakeSock(fail_times=1)
        stub = _StyleStub()

        self._broadcast(stub, sock)
        self.assertEqual(stub._broadcast_last, "")
        self._broadcast(stub, sock)

        self.assertEqual(sock.attempts, 2)
        self.assertEqual(len(sock.sent), 1)


if __name__ == "__main__":
    unittest.main()
