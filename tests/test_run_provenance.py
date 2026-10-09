"""A run's manifest has to say which clip it played, or the latency numbers are fiction.

scripts/blog_video_test.py recorded `result["wav"] = str(WAV_PATH)` -- a module constant
naming blog_3min -- while the run itself played `_sample_wav()`, which reads
TRANSLATION_SAMPLE_WAV. Line 466 printed the real one to stdout and nothing reconciled
the two, so every run on any other clip produced a manifest naming blog_3min.wav.

The cost landed on e2e_first_tr_to_paint. perf_compare scored it against
samples/blog_3min_timing.json, so the Qwen3 A/B ran on lecture_mix and had its
translations matched against another clip's segment starts: p50 434ms against a real
47ms. That number became "B's e2e is 8x worse" and was cited a dozen times before it was
checked.

The shape of the error is the part worth keeping. It was not a missing value and not a
crash. blog_3min's 16 segment starts fall inside lecture_mix's 180 seconds, so it
matched, produced a normal n, and returned a normal number of milliseconds -- while
n=16 was blog_3min's row count and lecture_mix has 17 segments. Only that metadata
disagreement gave it away.

These tests pin both halves. Fixing the consumer alone would have been worse than
leaving it: deriving the timeline from a manifest that lies resolves to a real file and
reproduces the original error.
"""

from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


class ManifestProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.src = (ROOT / "scripts" / "blog_video_test.py").read_text(encoding="utf-8")

    def test_the_manifest_records_the_clip_that_ran(self):
        """The producer, not the consumer.

        str(WAV_PATH) here is the defect in one line: it is the default, and every
        override of the sample then recorded the default instead of itself.
        """
        self.assertIn('result["wav"] = str(_sample_wav())', self.src)
        self.assertNotIn('result["wav"] = str(WAV_PATH)', self.src)

    def test_the_timeline_is_recorded_too(self):
        """Why one field was not enough.

        Deriving the timeline from `wav` cannot catch a manifest that lies about `wav`:
        a lie naming blog_3min resolves to a real blog_3min_timing.json and the original
        error reproduces silently. Two fields that have to agree catch what one cannot.
        """
        self.assertIn('result["timing"] = str(_sample_timing())', self.src)

    def test_the_video_is_not_claimed_for_a_clip_it_was_not_cut_from(self):
        """The second wrong field next to the one just fixed.

        mp4 belongs to blog_3min. A lecture_mix run that records it is asserting a video
        that does not correspond to the audio anyone measured.
        """
        self.assertIn("result[\"video\"] = str(MP4_PATH) if same_clip else None", self.src)


class ReferenceTimelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.src = (ROOT / "scripts" / "perf_compare.py").read_text(encoding="utf-8")

    def test_the_timeline_is_derived_not_hardcoded(self):
        self.assertNotIn('"blog_3min_timing.json"', self.src)
        self.assertIn("_segments(", self.src)

    def test_both_timeline_shapes_are_readable(self):
        """Only the bare list was handled, so this path could never have consumed any
        built timeline -- which is why the hardcoding stayed invisible: the one file it
        ever read was the one shape it understood."""
        self.assertIn("def _segments(", self.src)
        self.assertIn('for key in ("segments", "rows", "utterances"):', self.src)


class _SegmentsShapeTests(unittest.TestCase):
    def _segments(self, module_name: str, payload: object) -> list[dict]:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            module_name, ROOT / "scripts" / "perf_compare.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module._segments(payload)

    def test_a_bare_list_passes_through(self):
        rows = [{"i": 0, "start": 0.4, "end": 3.0, "text": "hi"}]
        self.assertEqual(self._segments("pc_a", rows), rows)

    def test_a_built_timeline_is_unwrapped(self):
        """What build_timing.py writes, and what every clip in this repository except
        blog_3min actually has."""
        rows = [{"i": 0, "start": 0.0, "end": 5.0, "text": "hi"}]
        payload = {"audio_total": 180.0, "covered_span": 117.14, "segments": rows}
        self.assertEqual(self._segments("pc_b", payload), rows)

    def test_anything_else_is_empty_rather_than_iterated(self):
        """Iterating a dict yields its keys, and row["start"] on a string is the crash
        that proved the shape was never handled. Empty is the safe answer: the metric
        reports n=0 instead of raising."""
        self.assertEqual(self._segments("pc_c", {"audio_total": 180.0}), [])
        self.assertEqual(self._segments("pc_d", None), [])


class LegacyManifestTests(unittest.TestCase):
    """The consumer's behaviour on a manifest written before the producer was fixed.

    The enumeration of historical files is deliberately not tested. There are 101 of them
    under logs/, which is gitignored, and a test that walks them would be a test about the
    past -- it could only fail, never tell anyone anything they did not already know.

    What is pinned is the behaviour, because that is what applies to the next run that
    somehow ends up with an inconsistent manifest.
    """

    def setUp(self) -> None:
        self.src = (ROOT / "scripts" / "perf_compare.py").read_text(encoding="utf-8")

    def test_inconsistent_provenance_refuses_instead_of_scoring(self):
        self.assertIn("this manifest's wav", self.src)
        self.assertIn("name different clips", self.src)

    def test_an_uncrosscheckable_manifest_says_so_in_the_output(self):
        """A manifest with no timing field cannot be verified, and quietly proceeding is
        what let the original defect run for a dozen citations."""
        self.assertIn("predates the provenance fix", self.src)


if __name__ == "__main__":
    unittest.main()