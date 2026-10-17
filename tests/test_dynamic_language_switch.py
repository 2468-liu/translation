"""Verify that dynamic language switching injects into the next VAD slice without restarting."""

import unittest
import numpy as np

from server.pipeline import Session


class FakeAsr:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls: list[str | None] = []

    def transcribe(self, _audio: np.ndarray, language: str | None) -> tuple[str, str | None, float]:
        self.calls.append(language)
        if self.results:
            return self.results.pop(0)
        return "test text", language or "en", 0.99


class TestDynamicLanguageSwitch(unittest.IsolatedAsyncioTestCase):
    async def test_switch_language_injects_next_slice(self):
        fake_asr = FakeAsr([
            ("First sentence", "en", 0.99),
            ("二番目の文", "ja", 0.99),
            ("Third sentence", "en", 0.99),
        ])
        session = Session(fake_asr)
        scheduled = []
        session._schedule_translate = lambda text, final, language, generation, send: scheduled.append(
            (text, final, language, generation)
        )

        async def send(_payload):
            pass

        try:
            # 1. First slice with auto: should call ASR with None (auto)
            await session._run("end", np.ones(16000, dtype=np.float32), send)
            self.assertIsNone(fake_asr.calls[0])
            self.assertEqual(scheduled[0][2], "en")

            # 2. Dynamic switch to Japanese on the fly (no restart!)
            session.set_language("ja")
            self.assertEqual(session.language, "ja")
            self.assertTrue(session.language_locked)

            # Next slice immediately calls ASR with "ja"
            await session._run("end", np.ones(16000, dtype=np.float32), send)
            self.assertEqual(fake_asr.calls[1], "ja")
            self.assertEqual(scheduled[1][2], "ja")

            # 3. Dynamic switch back to auto on the fly
            session.set_language("auto")
            self.assertFalse(session.language_locked)
            self.assertIsNone(session._asr_language())

            # Next slice immediately calls ASR with None (auto)
            await session._run("end", np.ones(16000, dtype=np.float32), send)
            self.assertIsNone(fake_asr.calls[2])
            self.assertEqual(scheduled[2][2], "en")
        finally:
            await session.close()

    def test_switch_clears_stale_partial_cache(self):
        fake_asr = FakeAsr()
        session = Session(fake_asr)
        session._partial_result = ("old partial", "en", 0.95)
        session._last_audio_final = "old final"
        session.last_text = "old text"
        session.context.append(("hello", "你好"))

        # Switch to "ja"
        session.set_language("ja")
        self.assertIsNone(session._partial_result)
        self.assertEqual(session._last_audio_final, "")
        self.assertEqual(session.last_text, "")
        self.assertEqual(session.context, [])

    def test_switch_cadence_applies_to_running_vad_and_next_slice(self):
        from server import ws_server, config
        fake_asr = FakeAsr()
        session = Session(fake_asr)
        self.assertEqual(session.vad.tuning.partial_interval_ms, config.PARTIAL_INTERVAL_MS)

        ws_server._CURRENT_SESSION = session
        try:
            ws_server._switch_partial_interval(560)
            # The running session's VAD tuning is immediately updated for the next slice
            self.assertEqual(session.vad.tuning.partial_interval_ms, 560)
        finally:
            ws_server._CURRENT_SESSION = None


if __name__ == "__main__":
    unittest.main()
