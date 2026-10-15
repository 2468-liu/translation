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
import unittest.mock
from pathlib import Path

from server.caption_window import (
    CLAUSE_MAX_MS,
    TERMINAL_PUNCT,
    introduces_boundary,
    punctuation_marks,
)


ROOT = Path(__file__).resolve().parents[1]


class BoundaryTests(unittest.TestCase):
    def test_a_growing_clause_keeps_its_mark_count(self):
        # The line ended a clause; the next fragment repeats that mark while growing the
        # following clause. The count is unchanged, so this is not a boundary.
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

    def test_a_second_mark_of_the_same_kind_does_fire(self):
        # This is the case the switch from sets to counts exists for. Set difference
        # could not see it: the line already carried a full stop, so a second one added
        # nothing new as a character, the boundary fired 13.5 times a minute against a
        # clause rate of 17.7, and two thirds of the draws fell through to the 400ms
        # fallback, putting first-character latency at 354ms against a 37ms baseline.
        self.assertTrue(introduces_boundary("你有。", "你有。他。"))

    def test_a_swapped_mark_does_not_change_the_count(self):
        # Known gap in counting, left as-is on purpose rather than patched now: the
        # line carried a comma and now carries a full stop. The count is the same, so
        # this reads as no boundary although the clause plainly ended. If the next run
        # still leans on the fallback, this is the case to subdivide -- a replacement by
        # a stronger mark counts as an end.
        self.assertFalse(introduces_boundary("你有，", "你有。"))

    def test_repeated_marks_are_kept_rather_than_deduplicated(self):
        self.assertEqual(punctuation_marks("你有。他。"), ("。", "。"))

    def test_output_mode_is_a_property_of_the_route(self):
        """流式/块状归并到三条路线：一条路线一处声明，别处只读结论。

        它先是独立的 `TRANSLATION_BLOCK_BY_PUNCT` 开关，后来改成由识别后端派生，现在
        声明在 `config.CHAINS[...]["output"]`。同一个行为有两个地方可以设置，就一定会有
        一个在说谎 —— REPORT 58/59 那一周的日志就是两个开关打架的代价。
        """
        from server import config

        for chain, spec in config.CHAINS.items():
            self.assertIn("output", spec, f"{chain} 没有声明输出方式")
            self.assertIn(spec["output"], ("stream", "block"), chain)
        self.assertEqual(config.CHAINS["qwen3-in"]["output"], "block")
        self.assertEqual(config.CHAINS["nv-in"]["output"], "block")
        self.assertEqual(config.CHAINS["gemma-e2e"]["output"], "stream")
        # 显示层只读结论，不再自己比较识别器的名字。
        source = (ROOT / "server" / "caption_window.py").read_text(encoding="utf-8")
        self.assertIn("BLOCK_BY_PUNCT = BLOCK_OUTPUT", source)
        self.assertNotIn('BLOCK_BY_PUNCT = ASR_BACKEND == "qwen3"', source)

    def test_the_declared_chain_wins_over_the_derived_one(self):
        """`TRANSLATION_CHAIN_RUNNING` 是 start.ps1 观测到的值（回退之后就更新过）。"""
        import importlib
        import os

        import server.caption_window as cw

        cases = (
            # (declared running chain, asr backend, expected BLOCK_BY_PUNCT)
            ("qwen3-in", "qwen3", True),
            ("nv-in", "nemotron", True),
            # 声明优先：环境声明的 chain_running 直接决定输出模式
            ("qwen3-in", "nemotron", True),
            ("gemma-e2e", "", False),
        )
        try:
            for chain, backend, expected in cases:
                with unittest.mock.patch.dict(os.environ, {
                    "TRANSLATION_CHAIN_RUNNING": chain,
                    "TRANSLATION_ASR_BACKEND": backend,
                    "TRANSLATION_MT_BACKEND": "Index-Translate-2B",
                }):
                    importlib.reload(importlib.import_module("server.config"))
                    reloaded = importlib.reload(cw)
                    self.assertIs(reloaded.BLOCK_BY_PUNCT, expected, f"{chain}/{backend}")
        finally:
            os.environ.pop("TRANSLATION_CHAIN_RUNNING", None)
            importlib.reload(importlib.import_module("server.config"))
            importlib.reload(cw)

    def test_block_mode_follows_the_recogniser(self):
        import importlib
        import os

        import server.caption_window as cw

        for backend, expected in (("nemotron", True), ("qwen3", True)):
            with unittest.mock.patch.dict(os.environ, {"TRANSLATION_ASR_BACKEND": backend}):
                # config has to be reloaded too: caption_window reads ASR_BACKEND at
                # import time, so reloading only the window reuses the cached value and
                # the test passes or fails for the wrong reason.
                importlib.reload(importlib.import_module("server.config"))
                reloaded = importlib.reload(cw)
                self.assertIs(reloaded.BLOCK_BY_PUNCT, expected, backend)
        importlib.reload(importlib.import_module("server.config"))
        importlib.reload(cw)

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
        stub._clause_fell_back = False
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

    def test_a_clause_gets_one_fallback_not_one_per_400ms(self):
        # The first run drew a punctuation-less clause seven times, once every 400ms,
        # with the text growing each time. The fallback is spent once; after that the
        # clause waits for its punctuation no matter how long that takes.
        import unittest.mock as mock

        stub = self._window()
        self._gate(stub, "你", 100.0)
        with mock.patch("server.caption_window.time.monotonic", return_value=100.5), \
             mock.patch("server.caption_window.perf.emit"):
            from server.caption_window import _Caption

            _Caption._flush_clause(stub)
        self.assertTrue(stub._clause_fell_back)
        self.assertEqual(len(stub.drawn), 1)

        # Three more fragments over the next 1.2s, each of which used to re-arm.
        for i, t in enumerate((100.6, 100.9, 101.3, 101.9)):
            stub.jobs.clear()
            self._gate(stub, "你有那招" * (i + 1), t)
            self.assertEqual(stub.jobs, [], f"a fallback was re-armed at {t}")
        self.assertEqual(len(stub.drawn), 1, "the clause was drawn more than once")

    def test_the_fallback_does_not_arm_at_fifty_milliseconds(self):
        # Not clearing _clause_t0 alone would compute wait = max(50, negative) = 50ms,
        # which redraws every twentieth of a second -- faster than the fallback it
        # replaced. The flag is what prevents the arming at all.
        import unittest.mock as mock

        stub = self._window()
        self._gate(stub, "你", 100.0)
        with mock.patch("server.caption_window.time.monotonic", return_value=101.0), \
             mock.patch("server.caption_window.perf.emit"):
            from server.caption_window import _Caption

            _Caption._flush_clause(stub)
        stub.jobs.clear()
        self._gate(stub, "你还在等", 102.0)
        self.assertEqual(stub.jobs, [])

    def test_a_new_clause_after_a_boundary_gets_its_own_fallback(self):
        stub = self._window()
        self._gate(stub, "你。", 100.0)              # boundary: drawn, clause closed
        stub.jobs.clear()
        self._gate(stub, "你。他", 100.2)            # next clause opens
        self.assertTrue(stub.jobs, "the next clause got no fallback at all")
        self.assertFalse(stub._clause_fell_back)

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