r"""How the audio was cut, and what forced the cut.

The point of running the real recording is that the TTS clip triggers the silence
threshold constantly and the real one never does. So the interesting number is not
how the two routes compare -- that is a different question -- but how each one
behaves when its normal closing condition never arrives.

Reports the segment lengths the server actually produced, and separates the ones
that ended on a pause from the ones that ended because a length cap fired. A
segment that ended on a cap is a segment whose closing condition never came, and
in the audio-native route that means the first character cannot land until the
segment does.

Reads the run log the pipeline writes. Segment boundaries are the segment_start
events, and the closing reason is not in them, so it is inferred from the
segment length against the two caps that exist: GEMMA_MAX_UTTERANCE_MS for the
hard limit, and the soft cut at "5s of speech plus a 200ms pause". A segment at
the cap is a forced cut; anything shorter is taken to have ended on a pause.
That inference is stated rather than hidden, because it is exactly the kind of
thing that gets quoted later as if it were measured.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_silence import _resolve  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

HARD_CAP_S = 26.0  # GEMMA_MAX_UTTERANCE_MS
SOFT_SPEECH_S = 5.0  # speech before a 200ms pause is allowed to cut
PAUSE_S = 0.2


def segments(name: str) -> list[float]:
    try:
        data = json.loads(
            _resolve("blog_3min_test", ".json", "blog_3min_test.json", name).read_text(
                encoding="utf-8"
            )
        )
    except (OSError, FileNotFoundError, json.JSONDecodeError):
        return []
    starts = [
        float(e["audio_sec"])
        for e in data.get("events", [])
        if e.get("type") == "segment_start" and e.get("audio_sec") is not None
    ]
    starts.sort()
    return [b - a for a, b in zip(starts, starts[1:])]


def _pct(values: list[float], q: float) -> float:
    clean = [v for v in values if v == v]
    if not clean:
        return float("nan")
    return sorted(clean)[min(len(clean) - 1, int(q * len(clean)))]


def main(argv: list[str]) -> int:
    allow = "--allow-unmanifested" in argv
    argv = [a for a in argv if a != "--allow-unmanifested"]
    if not argv:
        print(__doc__)
        return 2

    header = (
        f"{'route':22} {'n':>3} {'segs':>5} {'len p50':>8} {'p90':>6} {'max':>6} "
        f"{'forced':>7} {'>2s':>6} {'>5s':>6} {'>10s':>6}"
    )
    print(header)
    print("-" * len(header))
    for spec in argv:
        label, _, names = spec.partition("=")
        pooled: list[float] = []
        used = 0
        for name in names.split(","):
            if not allow and not _manifest_passes(name):
                print(f"  {name}: no passing manifest, skipped")
                continue
            pooled.extend(segments(name))
            used += 1
        if not pooled:
            print(f"{label:22} {0:>3}   (nothing)")
            continue
        forced = sum(1 for x in pooled if x >= HARD_CAP_S - 0.5)
        print(
            f"{label:22} {used:3d} {len(pooled):5d} "
            f"{statistics.median(pooled):8.2f} {_pct(pooled, 0.90):6.2f} {max(pooled):6.2f} "
            f"{forced:7d} {sum(1 for x in pooled if x > 2):6d} "
            f"{sum(1 for x in pooled if x > 5):6d} {sum(1 for x in pooled if x > 10):6d}"
        )
    print()
    print(f"forced = segments at the {HARD_CAP_S:.0f}s cap, i.e. no pause ever closed them.")
    print("         Inferred from length, not recorded: the log does not carry the")
    print("         closing reason, so a segment shorter than the cap is taken to have")
    print("         ended on a pause. Stated because it will get quoted as measured.")
    print(f"soft cut = {SOFT_SPEECH_S:.0f}s of speech followed by a {PAUSE_S * 1000:.0f}ms pause")
    return 0


def _manifest_passes(name: str) -> bool:
    path = ROOT / "logs" / f"run.{name}.manifest"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return "_failed" not in data


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))