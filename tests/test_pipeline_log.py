"""The pipeline line must name the recogniser that was actually loaded.

`ws_server` printed "pipeline: whisper + text model" for every branch that loads a
recogniser, which is both whisper and qwen3. On the qwen3-hy chain that log line said
whisper, and the only thing that gave the arm away was `effective.config` several lines
earlier.

This is a deployment-correctness test rather than a preference. Reading an arm off a log
line that can be wrong is the same shape as the twelve "MTP on" arms that all ran with
draft decoding off, and the rule this repository settled on is that arm identity comes
from observed state. A startup line that cannot be wrong is part of that state.

The assertions are on the source and on the factory's mapping rather than on a running
server: starting one would need models on disk, and building a real engine loads
whisper-large-v3-turbo into memory, which is four seconds and two gigabytes too much for
a unit test that is only checking what a line of code says.

Comments are stripped before matching, because this file explains at length why the
audio-native branch starts neither recogniser, and an assertion satisfiable by an
explanatory comment is not an assertion.
"""

import re
import unittest
from pathlib import Path
from unittest import mock

SOURCE = (Path(__file__).resolve().parents[1] / "server" / "ws_server.py").read_text(
    encoding="utf-8"
)


def _executable_lines() -> str:
    out = []
    for raw in SOURCE.splitlines():
        if raw.strip().startswith("#"):
            continue
        out.append(raw.split("  #")[0] if "  #" in raw else raw)
    return "\n".join(out)


def _pipeline_prints() -> list[str]:
    return re.findall(r'print\(\s*f?"pipeline:[^"]*"', _executable_lines())


def _recogniser_branch_line() -> str:
    """The print that runs after a recogniser is built, not the audio-native one."""
    for line in _pipeline_prints():
        if "audio-native" not in line:
            return line
    raise AssertionError(f"no recogniser branch print among {_pipeline_prints()}")


class PipelineLogTests(unittest.TestCase):
    def test_no_print_names_whisper_as_a_literal(self):
        for line in _pipeline_prints():
            self.assertNotIn(
                'whisper + text model"',
                line,
                "a startup line that cannot be wrong has no hardcoded backend in it",
            )

    def test_the_recogniser_branch_reports_the_engine_it_built(self):
        line = _recogniser_branch_line()
        self.assertIn("type(asr).__name__", line)
        self.assertIn("ASR_BACKEND", line)

    def test_the_two_backends_build_differently_named_engines(self):
        # The factory's mapping is what makes type(asr).__name__ worth printing at all.
        # The engine classes are patched so nothing is loaded.
        from server import asr as asr_module

        sentinel = {"qwen3": "qwen-engine", "whisper": "whisper-engine"}

        # Real classes, not Mock: type(engine).__name__ is what the log prints, and a
        # Mock reports "Mock" however its __class__ is set.
        class WhisperEngine:
            pass

        class QwenEngine:
            pass

        built = {"qwen3": "QwenEngine", "whisper": "WhisperEngine"}
        for backend in sentinel:
            with mock.patch.object(asr_module.config, "ASR_BACKEND", backend), \
                 mock.patch.object(asr_module, "Qwen3AsrEngine", QwenEngine), \
                 mock.patch.object(asr_module, "AsrEngine", WhisperEngine):
                engine = asr_module.build_asr()
            self.assertEqual(type(engine).__name__, built[backend], backend)
            self.assertNotEqual(type(engine).__name__, built[
                "whisper" if backend == "qwen3" else "qwen3"
            ], f"{backend} built the other backend's engine")

    def test_an_unknown_backend_is_refused_rather_than_defaulted(self):
        # The other half of "the line cannot lie": a value the factory does not know
        # has to fail, because a silent fallback to whisper is exactly how a log line
        # ends up naming the wrong arm.
        from server import asr as asr_module

        with mock.patch.object(asr_module.config, "ASR_BACKEND", "gemma-e2e"):
            with self.assertRaises(ValueError):
                asr_module.build_asr()


if __name__ == "__main__":
    unittest.main()