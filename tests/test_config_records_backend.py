"""A run must record which recogniser answered, not just which knobs were set.

config.effective is what scripts/manifest.py reads to say what a process is running
with, and it is also what perf_compare splits blocks on. It listed partial_interval_ms,
mt_backend and four asr fields, but not the engine: a qwen3 arm and a whisper arm both
logged asr_device=cuda, so an archive named after the arm was the only record of which
recogniser produced the captions.

That is the substitution that made twelve "MTP on" runs read as MTP off -- the arm name
standing in for an observed fact. Here it is caught by a test rather than by a sweep
report, because the cost of missing it is a comparison between two engines that was
never made.

The second field is the cap, and it is only meaningful for qwen3. It is recorded as 0
for whisper rather than omitted, so the field is always present and a reader never has
to distinguish "not applicable" from "not recorded".
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from server import ws_server


def reported() -> dict:
    """The dict _report_effective_config writes, without touching the log file."""
    captured: list[dict] = []
    with patch.object(ws_server.perf, "emit",
                      side_effect=lambda src, tag, **kw: captured.append(kw)):
        ws_server._report_effective_config()
    body = captured[0]["json"]
    return json.loads(body) if isinstance(body, str) else body


class BackendIsRecordedTests(unittest.TestCase):
    def test_qwen3_arm_records_the_engine(self):
        with patch.object(ws_server.config, "ASR_BACKEND", "qwen3"):
            fields = reported()
        self.assertEqual(fields.get("asr_backend"), "qwen3")

    def test_whisper_arm_records_the_engine(self):
        with patch.object(ws_server.config, "ASR_BACKEND", "whisper"):
            fields = reported()
        self.assertEqual(fields.get("asr_backend"), "whisper")

    def test_the_two_arms_are_distinguishable_from_the_record_alone(self):
        """The property the other two tests exist to protect: nothing outside this
        dict may be needed to tell the engines apart."""
        with patch.object(ws_server.config, "ASR_BACKEND", "qwen3"):
            qwen = reported()
        with patch.object(ws_server.config, "ASR_BACKEND", "whisper"):
            whisper = reported()
        self.assertNotEqual(qwen["asr_backend"], whisper["asr_backend"])
        self.assertNotEqual(qwen["asr_max_tokens"], whisper["asr_max_tokens"])

    def test_cap_present_for_both_engines(self):
        with patch.object(ws_server.config, "ASR_BACKEND", "whisper"):
            self.assertIn("asr_max_tokens", reported())
        with patch.object(ws_server.config, "ASR_BACKEND", "qwen3"):
            self.assertGreater(reported()["asr_max_tokens"], 0)


class DefaultsTests(unittest.TestCase):
    def test_nemotron_is_the_default_backend(self):
        """Nemotron 3.5 ASR is the measured and adopted default streaming backend."""
        from server import config
        self.assertEqual(config.ASR_BACKEND, "nemotron")

    def test_cap_default_is_128(self):
        from server import config
        self.assertEqual(config.QWEN3_ASR_MAX_TOKENS, 128)


if __name__ == "__main__":
    unittest.main()