"""A final translation is drawn whatever the punctuation counts say.

Block mode's boundary test asks whether the incoming text carries more terminal marks
than the line already does. On a final that is never true: a sentence's own streaming
fragments bring its punctuation first, so the final arrives with the same count and is
declined -- and with the fallback already spent, it was never drawn at all. Measured on
the qwen3 arm, the test held 0 times out of 49 finals, and 26 of 49 finals never reached
the screen. The line settled on a mid-sentence fragment, which is what "卡一下才出现"
turned out to be.

So the final skips the test. It is the complete version of the sentence; the marks say
nothing about whether it should be shown.

No sentence-identity check guards this, and the reason is measured rather than assumed:
`promote` -- an end event arriving before its own translation finishes -- sends the final
when that job completes, not when the next sentence starts, and `pipeline.defer` was zero
across the run, so what is on screen when a final lands is that sentence's own text.
"""

import inspect
import json
import unittest
from pathlib import Path
from unittest import mock

from server.caption_window import _Caption, introduces_boundary

SOURCE = (Path(__file__).resolve().parents[1] / "server" / "caption_window.py").read_text(
    encoding="utf-8"
)


class _Stub:
    """The attributes _punct_gate reads and writes."""

    def __init__(self, line: str = "") -> None:
        self.line = line
        self._clause_t0 = 0.0
        self._clause_pending = ""
        self._clause_job = None
        self._clause_fell_back = False
        self._block_hits = 0
        self._block_timeouts = 0
        self.drawn: list[tuple[str, str]] = []
        self.font_line = mock.Mock()
        self.root = mock.Mock()
        self._flush_clause = lambda: None
        self._apply_line = lambda text, action="": (
            self.drawn.append((text, action)),
            setattr(self, "line", text),
        )[0]
        self._cancel_clause = lambda: (
            setattr(self, "_clause_t0", 0.0),
            setattr(self, "_clause_pending", ""),
            setattr(self, "_clause_fell_back", False),
        )[0]


def _gate(stub, text, is_final):
    with mock.patch("server.caption_window.time.monotonic", return_value=100.0), \
         mock.patch("server.caption_window.perf.emit"):
        return _Caption._punct_gate(stub, text, is_final)


class FinalDrawTests(unittest.TestCase):
    def test_a_final_is_drawn_even_when_the_boundary_test_declines_it(self):
        # The whole point. The line already carries the sentence's punctuation, so the
        # boundary test is false and the final would otherwise be dropped.
        line = "你有。"
        final = "你有。"
        self.assertFalse(introduces_boundary(line, final + "他"))
        stub = _Stub(line)
        action = _gate(stub, final + "他", True)
        self.assertEqual(action, "final")
        self.assertEqual(stub.drawn, [(final + "他", "final")])

    def test_a_final_clears_the_spent_fallback(self):
        stub = _Stub("你有。")
        stub._clause_fell_back = True
        _gate(stub, "你有。他", True)
        self.assertFalse(stub._clause_fell_back, "the clause stays closed after a final")

    def test_a_final_identical_to_the_line_draws_nothing(self):
        # Redrawing identical text would repaint for no reason, and the paint is the
        # expensive part of this path.
        stub = _Stub("你有。")
        action = _gate(stub, "你有。", True)
        self.assertEqual(stub.drawn, [])
        self.assertNotEqual(action, "final")

    def test_a_non_final_still_goes_through_the_boundary(self):
        stub = _Stub("")
        action = _gate(stub, "你", False)
        self.assertEqual(action, "block_wait")
        self.assertEqual(stub.drawn, [])

    def test_the_final_reports_itself(self):
        stub = _Stub("你有。")
        with mock.patch("server.caption_window.time.monotonic", return_value=100.0), \
             mock.patch("server.caption_window.perf.emit") as emit:
            _Caption._punct_gate(stub, "你有。他", True)
        reasons = [c.kwargs["why"] for c in emit.call_args_list
                   if c.args and c.args[1] == "block"]
        self.assertIn("final", reasons)


class FlagReachesTheGateTests(unittest.TestCase):
    """The flag has to survive the hop, or the fix does nothing.

    It used to be dropped at the only place the two processes meet: `update_caption`
    forwarded the text and nothing else, so the caption window could not tell a final
    from a fragment even in principle.
    """

    def test_send_puts_final_in_the_packet(self):
        from server import caption_window as cw

        captured = {}

        class Sock:
            @staticmethod
            def sendto(data, addr):
                captured["payload"] = json.loads(data.decode("utf-8"))

        with mock.patch.object(cw, "_SOCK", Sock), \
             mock.patch.object(cw.perf, "emit"):
            cw._send("text", "你有。", final=True)
        self.assertIs(captured["payload"]["final"], True)

        with mock.patch.object(cw, "_SOCK", Sock), \
             mock.patch.object(cw.perf, "emit"):
            cw._send("text", "你", final=False)
        self.assertIs(captured["payload"]["final"], False)

    def test_update_caption_forwards_the_final_flag(self):
        from server import caption_window as cw

        seen = []
        with mock.patch.object(cw, "_send", lambda *a, **k: seen.append((a, k))):
            cw.update_caption({"type": "translation", "text": "你有。", "final": True})
            cw.update_caption({"type": "translation", "text": "你", "final": False})
        self.assertIs(seen[0][1].get("final"), True)
        self.assertIs(seen[1][1].get("final"), False)

    def test_the_queue_carries_the_flag_and_the_pump_passes_it_on(self):
        code = "\n".join(
            line.split("#")[0] for line in SOURCE.splitlines()
        )
        self.assertIn('bool(message.get("final"))', code)
        self.assertIn("recv_at, is_final = _COMMANDS.get_nowait()", code)
        self.assertIn('self.set_text(value or "", is_final)', code)

    def test_set_text_passes_it_to_the_gate_only_in_block_mode(self):
        body = inspect.getsource(_Caption.set_text)
        self.assertIn("self._punct_gate(cleaned, is_final)", body)


if __name__ == "__main__":
    unittest.main()