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

from pathlib import Path
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
    still carries the position lock and transparency.
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
                      "the loop parameter is still used by the state branch")

    def test_the_websocket_path_is_the_one_that_remains(self):
        self.assertIn("_switch_partial_interval", self.ws_server)
        self.assertIn('if data.get("partial_interval_ms"):', self.ws_server)

    def test_caption_window_has_no_cadence_button_left(self):
        for gone in ("_switch_buttons", "_switch_var", "_switch_note",
                     "def switch_partial_interval", "_paint_switch_state"):
            self.assertNotIn(gone, self.caption, f"{gone} still in the caption window")

    def test_caption_window_still_sends_over_udp_for_the_position_lock(self):
        # Removing the switch's sendto must not have removed the listener's other
        # use of the same socket.
        self.assertIn("_STATE_SOCK.sendto(", self.caption)
        self.assertIn("STATE_PORT", self.caption)

    def test_cadence_stays_readable_on_the_window(self):
        # The readout is fed by ack_switch, which already carried the value; the
        # pump was the only thing dropping it. Losing this leaves a switch that
        # lands silently, which cannot be judged while watching.
        self.assertIn("def show_cadence", self.caption)
        self.assertIn('self._cadence_label.configure(text=f"{value}ms")', self.caption)
        self.assertIn("self.show_cadence(value)", self.caption)

    def test_the_extension_still_owns_the_control(self):
        self.assertIn('type: "cadence"', self.popup)
        self.assertIn('data-ms="250"', (ROOT / "extension" / "popup.html").read_text(encoding="utf-8"))
        self.assertIn('data-ms="750"', (ROOT / "extension" / "popup.html").read_text(encoding="utf-8"))


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

    def test_popup_offers_exactly_the_two_cadences_under_test(self):
        self.assertIn('data-ms="250"', self.popup_html)
        self.assertIn('data-ms="750"', self.popup_html)

    def test_popup_default_matches_the_server_default(self):
        # Otherwise pressing 开始 silently changes the cadence: the extension
        # sends its stored value on the opening config frame, and the server
        # applies whatever arrives.
        from server import config

        self.assertIn("partialIntervalMs: CADENCE_MS.fast", self.popup)
        self.assertEqual(config.PARTIAL_INTERVAL_MS, 250)
        self.assertIn("fast: 250", self.popup)


if __name__ == "__main__":
    unittest.main()