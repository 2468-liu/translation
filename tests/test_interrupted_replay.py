"""A replay that stopped early must not report numbers, and a perfect score is the tell.

scripts/replay.ps1 already refused a non-zero exit and a run with no translations. Neither
sees an interrupted playback: scripts/blog_video_test.py catches the closed WebSocket,
prints "play stopped at Ns", and exits 0 with a manifest full of translations. So the run
went on to print a complete set of numbers, among them

    coverage (prefix match)   5/5 (100%)
    audio_end >1000ms         0/5 (0%)

both of which read as excellent and both of which are artefacts of covering 34 seconds of
a 180-second clip.

Three of six replays died that way -- 11.0s, 48.6s, 34.4s, same message each time -- and
every one produced numbers. A truncated run that scores perfectly is worse than a run
that fails, because nothing about it looks wrong.

This pins the third guard: playback distance against clip length.
"""

from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPLAY = (ROOT / "scripts" / "replay.ps1").read_text(encoding="utf-8")


class InterruptedPlaybackTests(unittest.TestCase):
    def test_the_guard_exists_and_is_after_the_translation_check(self):
        """After, so a run with no translations still fails on the clearer message.

        Anchored on "0.8 * $clipSec" rather than on a phrase from the guard's message: an
        earlier guard in the file also says a run is void, so a message-shaped anchor
        matches the wrong place. That is the same trap as asserting on a comment.
        """
        self.assertIn("play stopped", REPLAY)  # quoted in the guard's own explanation
        self.assertIn("no translations in this run", REPLAY)
        self.assertGreater(
            REPLAY.index("0.8 * $clipSec"),
            REPLAY.index("no translations in this run"),
            "the interrupted-playback guard runs before the translation check, so a run "
            "with no backend is reported as a truncated replay instead")

    def test_it_compares_played_against_clip_and_refuses(self):
        self.assertIn("duration_sec", REPLAY)
        self.assertIn("audio_sec", REPLAY)
        self.assertIn("0.8 * $clipSec", REPLAY)
        self.assertIn("throw", REPLAY)

    def test_the_thresholds_are_documented_as_measured(self):
        """The three observed interruptions belong in the file that refuses them, so the
        next person can tell a known failure from a new one."""
        for seconds in ("11.0s", "48.6s", "34.4s"):
            self.assertIn(seconds, REPLAY)


class GuardDecisionTests(unittest.TestCase):
    """The rule, run against the thresholds.

    Executed rather than asserted from the source, because the failure this guards is a
    number that looks perfect -- so "the guard is present" is not the same as "the guard
    fires on the run it was written for".
    """

    def _verdict(self, clip_sec: float, played_sec: float) -> bool:
        return not (clip_sec > 0 and played_sec < 0.8 * clip_sec)

    def test_a_truncated_run_is_refused(self):
        # The run that motivated the guard: 34.2s of a 180s clip.
        self.assertFalse(self._verdict(180.0, 34.2))
        for observed in (11.0, 48.6, 34.4):
            self.assertFalse(self._verdict(180.0, observed))

    def test_a_complete_run_passes(self):
        # Both complete arms reached 179.8s of 180s.
        self.assertTrue(self._verdict(180.0, 179.8))

    def test_a_run_that_lost_its_last_few_seconds_still_passes(self):
        """80% and not 100%, so a trailing silence or a slow start is not a failure."""
        self.assertTrue(self._verdict(180.0, 145.0))

    def test_a_perfect_score_does_not_rescue_a_short_run(self):
        """The case the guard exists for: a truncated run that scored 100% coverage.

        Coverage is not an input to the decision, and it must not become one. Adding it
        would be the fix that makes the guard pass on exactly the run it was written for.
        """
        manifest = {"duration_sec": 180.0, "events": [{"audio_sec": 34.2}]}
        played = max(float(e["audio_sec"]) for e in manifest["events"])
        self.assertFalse(self._verdict(float(manifest["duration_sec"]), played))


if __name__ == "__main__":
    unittest.main()