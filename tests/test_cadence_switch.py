"""The cadence switch has to reach the session from both control surfaces.

Two paths exist and only one used to work. The caption window sends
``{"kind": "config"}`` over UDP and ``_listen_caption_state`` applied it. The
extension sends ``{"type": "config", ...}`` over the WebSocket, and that branch
read ``audioOnly``, ``language`` and the api fields -- never
``partial_interval_ms``. The field was accepted by the frame and then dropped,
which is the shape of bug the extension boundary tests exist to catch: nothing
errors, the control simply does nothing.

These tests pin both halves: that the switch reaches the live session, and that
the extension's frame is what carries it.
"""

import asyncio
import contextlib
import io
import json
from pathlib import Path
import socket
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from server import ws_server


ROOT = Path(__file__).resolve().parents[1]


class SwitchReachesTheSessionTests(unittest.TestCase):
    def test_switch_applies_to_override_and_to_the_running_splitter(self):
        session = MagicMock()
        with (
            patch.object(ws_server, "_CURRENT_SESSION", session),
            patch.object(ws_server, "clear_caption"),
            patch.object(ws_server, "ack_switch") as ack,
            patch("server.vad.set_partial_interval") as set_override,
            patch("server.vad.apply_partial_interval") as apply_live,
        ):
            ws_server._switch_partial_interval(750)

        set_override.assert_called_once_with(750)
        apply_live.assert_called_once_with(session.vad, 750)
        # Half a caption from one cadence and half from another reads as neither.
        session.reset_for_media.assert_called_once_with()
        ack.assert_called_once_with(750)

    def test_switch_is_still_remembered_with_no_session_running(self):
        with (
            patch.object(ws_server, "_CURRENT_SESSION", None),
            patch.object(ws_server, "clear_caption") as clear,
            patch.object(ws_server, "ack_switch") as ack,
            patch("server.vad.set_partial_interval") as set_override,
            patch("server.vad.apply_partial_interval") as apply_live,
        ):
            ws_server._switch_partial_interval(250)

        set_override.assert_called_once_with(250)
        # No session means nothing live to clear and nobody watching to ack.
        apply_live.assert_not_called()
        clear.assert_not_called()
        ack.assert_not_called()


class CadenceHasExactlyOneControlPathTests(unittest.TestCase):
    """One control, one path. The caption window's buttons are gone.

    Two live paths to the same switch was the reason the change was made: the
    extension's frame reached _switch_partial_interval while the caption window's
    UDP frame also did, and a control reachable from two places is a control whose
    state nobody can predict. The listener itself stays, because kind == "state"
    still carries 只有字 and the font from the window's own controls.
    """

    def setUp(self):
        self.ws_server = (ROOT / "server" / "ws_server.py").read_text(encoding="utf-8")
        self.caption = (ROOT / "server" / "caption_window.py").read_text(encoding="utf-8")
        self.popup = (ROOT / "extension" / "popup.js").read_text(encoding="utf-8")

    @staticmethod
    def _listener_body(source: str) -> str:
        """The UDP listener's executable lines, comments removed.

        The comment explaining the removal names the branch it removed, so the
        string being banned is present in the prose explaining the ban -- the
        same trap REPORT 37 records, hit here one commit later. Assertions run
        over the executable lines.
        """
        start = source.index("def _listen_caption_state")
        body = source[start:source.index("async def handle", start)]
        kept = []
        in_doc = False
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.count('"""') % 2 == 1:
                in_doc = not in_doc
                continue
            if in_doc or stripped.startswith("#"):
                continue
            kept.append(line)
        return "\n".join(kept)

    def test_udp_listener_no_longer_handles_config(self):
        listener = self._listener_body(self.ws_server)
        self.assertNotIn('kind == "config"', listener)
        self.assertNotIn("partial_interval_ms", listener)
        # The state branch must survive: it is not part of this change.
        self.assertIn('if kind != "state":', listener)
        self.assertIn("STATE_PORT", listener)
        self.assertIn("run_coroutine_threadsafe", listener,
                      "the state branch still hands the frame to the client's loop")

    def test_one_state_listener_serves_the_whole_process(self):
        """It used to be started per connection, and the second bind always failed.

        UDP has no SO_REUSEADDR here: the second thread's bind raised WSAEADDRINUSE and
        the thread died with the traceback on stderr, while the frames kept going to the
        closed connection's loop. So after the first reconnect a click on the caption
        window's control stopped reaching the popup and nothing reported it. The listener
        takes no loop/send now -- it reads whichever connection registered last.
        """
        self.assertNotIn("def _listen_caption_state(loop", self.ws_server)
        self.assertIn("def _set_state_target(loop, send)", self.ws_server)
        self.assertIn("_start_state_listener()", self.ws_server)
        self.assertIn("if _STATE_LISTENER is not None:", self.ws_server)
        listener = self._listener_body(self.ws_server)
        self.assertIn("_STATE_TARGET", listener)
        self.assertNotIn("async def handle", listener)

    def test_the_websocket_path_is_the_one_that_remains(self):
        self.assertIn("_switch_partial_interval", self.ws_server)
        self.assertIn('if data.get("partial_interval_ms"):', self.ws_server)

    def test_caption_window_has_no_cadence_button_left(self):
        for gone in ("_switch_buttons", "_switch_var", "_switch_note",
                     "def switch_partial_interval", "_paint_switch_state"):
            self.assertNotIn(gone, self.caption, f"{gone} still in the caption window")

    def test_caption_window_still_sends_over_udp_for_its_own_state(self):
        # Removing the switch's sendto must not have removed the listener's other
        # use of the same socket.
        self.assertIn("_STATE_SOCK.sendto(", self.caption)
        self.assertIn("STATE_PORT", self.caption)

    def test_the_ack_still_lands_even_though_the_readout_is_gone(self):
        """The window no longer draws the value, and it still has to receive it.

        ack_switch is the server's confirmation that it applied the cadence. Dropping
        it on the way to the window would leave a control that lands silently, which
        is the failure this whole test file exists to prevent -- it just moves from
        "drawn in the wrong place" to "not observable at all". So the pump keeps the
        frame and records it as state.
        """
        self.assertIn('kind == "switch_ok"', self.caption)
        self.assertIn("self._cadence_applied", self.caption)
        self.assertIn("def ack_switch", self.caption)
        self.assertIn('_send("switch_ok"', self.caption)
        # And the drawing is really gone, rather than merely unused.
        self.assertNotIn("def show_cadence", self.caption)
        self.assertNotIn("_cadence_label", self.caption)

    def test_the_extension_still_owns_the_control(self):
        self.assertIn('type: "cadence"', self.popup)
        html = (ROOT / "extension" / "popup.html").read_text(encoding="utf-8")
        self.assertIn('id="cadenceSlider"', html)
        # The caption window used to carry two buttons with these values and a
        # data-ms attribute each; asserting on those pinned a menu of two. The control
        # is a range now, so what has to hold is that the extension still owns it and
        # that no control is left behind on the window.
        self.assertNotIn('id="cadenceFast"', html)
        self.assertNotIn('id="cadenceSlow"', html)


class WebSocketConfigCarriesTheSwitchTests(unittest.TestCase):
    """Text-level, because the config branch sits inside the socket handler.

    Same style as test_extension_boundaries.py: these pin the wiring, not
    behaviour. The behaviour above pins what the wiring reaches.
    """

    def setUp(self):
        self.ws_server = (ROOT / "server" / "ws_server.py").read_text(encoding="utf-8")
        self.offscreen = (ROOT / "extension" / "offscreen.js").read_text(encoding="utf-8")
        self.popup = (ROOT / "extension" / "popup.js").read_text(encoding="utf-8")
        self.popup_html = (ROOT / "extension" / "popup.html").read_text(encoding="utf-8")

    def test_ws_config_branch_applies_partial_interval(self):
        self.assertIn('if data.get("partial_interval_ms"):', self.ws_server)
        self.assertIn('_switch_partial_interval(int(data["partial_interval_ms"]))', self.ws_server)

    def test_extension_frame_carries_the_field(self):
        self.assertIn("payload.partial_interval_ms", self.offscreen)
        self.assertIn("if (message.partialIntervalMs > 0)", self.offscreen.replace(
            "Number(message.partialIntervalMs) > 0", "message.partialIntervalMs > 0"))

    def test_popup_sends_the_switch_and_keeps_it_for_the_next_start(self):
        self.assertIn('type: "cadence"', self.popup)
        self.assertIn("partialIntervalMs", self.popup)
        # Stored, or a fresh start would silently revert to the default.
        self.assertIn("chrome.storage.local.set({ partialIntervalMs", self.popup)

    def test_popup_offers_the_measured_range(self):
        """250 to 1000 in steps of 50, and the slider's own bounds are the claim.

        Both ends are measured rather than chosen: below 620ms the caption hold floor
        stops clamping anything, so a lower bound would be offering a setting that
        cannot change the output; above 1000ms the reference table tops out at 4200ms
        and only the first-line delay grows. A wider range would advertise values no
        measurement covers.
        """
        self.assertIn('min="250"', self.popup_html)
        self.assertIn('max="1000"', self.popup_html)
        self.assertIn('step="50"', self.popup_html)
        for name, value in (("CADENCE_MIN_MS", 250), ("CADENCE_MAX_MS", 1000),
                            ("CADENCE_STEP_MS", 50)):
            self.assertIn(f"{name} = {value}", self.popup)

    def test_every_sent_value_is_inside_the_slider_bounds(self):
        """A stored value from an older build can be anything, and the server applies
        whatever arrives without clamping it. If the popup forwards it unchecked then
        a stale 150 reaches the splitter, so the clamp has to be on the send path and
        not only on the widget."""
        for snippet in ("clampCadence(cadenceSlider.value)",
                        "chrome.storage.local.set({ partialIntervalMs: v })"):
            self.assertIn(snippet, self.popup)

    def test_popup_default_matches_the_server_default(self):
        # Otherwise pressing 开始 silently changes the cadence: the extension
        # sends its stored value on the opening config frame, and the server
        # applies whatever arrives. This is the invariant that used to be violated by
        # the default moving to 750 on the server while the popup still sent 250.
        from server import config

        self.assertIn("partialIntervalMs: DEFAULT_CADENCE_MS", self.popup)
        self.assertEqual(config.PARTIAL_INTERVAL_MS, 750)
        self.assertIn("const DEFAULT_CADENCE_MS = 750", self.popup)


class StateListenerStartupTests(unittest.TestCase):
    """What happens when 8767 is already held -- the case that used to be silent."""

    def setUp(self):
        self.previous = ws_server._STATE_LISTENER
        # Hold the port exactly as a stale process would.
        self.holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.holder.bind(("127.0.0.1", ws_server.STATE_PORT))
        ws_server._STATE_LISTENER = None

    def tearDown(self):
        ws_server._STATE_LISTENER = self.previous
        self.holder.close()

    def test_a_held_port_is_announced_and_retryable(self):
        with contextlib.redirect_stdout(io.StringIO()) as said:
            ws_server._start_state_listener()
            for _ in range(100):
                if ws_server._STATE_LISTENER is None:
                    break
                time.sleep(0.01)
        self.assertIn("could not bind", said.getvalue(),
                      "a bind failure has to be reported, not left on stderr")
        self.assertIsNone(ws_server._STATE_LISTENER,
                          "a later connection must be able to try again")


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class StateListenerForwardingTests(unittest.TestCase):
    """The frame really arrives: UDP -> whichever connection is live.

    Source-string tests cannot tell a listener that forwards from one that binds the
    port and dies, which is exactly what the two-listener version did -- the difference
    was a traceback on stderr and a control that silently stopped working.
    """

    def setUp(self):
        self.received = []
        self.port = _free_udp_port()
        self.previous_target = ws_server._STATE_TARGET
        self.previous_listener = ws_server._STATE_LISTENER
        self.previous_port = ws_server.STATE_PORT
        self.previous_text_only = ws_server._CAPTION_TEXT_ONLY
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        ws_server.STATE_PORT = self.port
        ws_server._STATE_TARGET = (self.loop, self._send)
        ws_server._STATE_LISTENER = None
        ws_server._CAPTION_TEXT_ONLY = False
        ws_server._start_state_listener()
        for _ in range(100):
            if ws_server._STATE_LISTENER is not None:
                break
            time.sleep(0.01)
        time.sleep(0.05)  # let the thread reach recvfrom

    def tearDown(self):
        ws_server.STATE_PORT = self.previous_port
        ws_server._STATE_LISTENER = self.previous_listener
        ws_server._STATE_TARGET = self.previous_target
        ws_server._CAPTION_TEXT_ONLY = self.previous_text_only
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=2)
        self.loop.close()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    async def _send(self, payload) -> None:
        self.received.append(payload)

    def _send_frame(self, frame: dict) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(json.dumps(frame).encode("utf-8"), ("127.0.0.1", self.port))

    def test_a_state_frame_reaches_the_live_sender(self):
        self._send_frame({"kind": "state", "value": {"textOnly": True, "fontPx": 24}})
        for _ in range(100):
            if self.received:
                break
            time.sleep(0.01)
        self.assertEqual(
            self.received,
            [{"type": "toggle_transparent", "value": True, "fontPx": 24}],
            "the caption window's own control has to reach the client, and carry no lock",
        )
        # And it is remembered, so `get_state` answers what the window is in rather than
        # what the extension last asked for. Without this the popup shows a stale value
        # after a caption-side press, and the first press there does nothing.
        self.assertIs(ws_server._CAPTION_TEXT_ONLY, True)

    def test_other_kinds_are_still_dropped(self):
        self._send_frame({"kind": "config", "value": {"partial_interval_ms": 500}})
        time.sleep(0.2)
        self.assertEqual(self.received, [],
                         "the cadence has one path and it is the WebSocket")


if __name__ == "__main__":
    unittest.main()