"""Does the clamp do its job on dense material? Criteria 2, 3 and 7.

On the sparse material the clamp never binds -- the bed is minutes of music-only
between clauses -- so it was never exercised. That is exactly what criterion 7 is
for: on dense material most gaps are shorter than the cap, the pre-roll should be
clamped to the gap, and nothing should merge that did not already merge.

The number to watch is not the median length, which criterion 3 covers. It is how
many segment starts land inside the preceding segment's own span -- that is what
"pre-roll too long" looks like, and a median can stay flat while a tail grows.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from check_sparse_music import load, spans  # noqa: E402


def closed_segments(pcm, gate_ms: int, preroll_ms: int):
    return sorted((g[0], g[1], g[3], g[4]) for g in spans(pcm, gate_ms, 0, preroll_ms) if g[2])


def close_reasons(pcm, gate_ms: int, preroll_ms: int):
    """Why segments closed, counted from the splitter's own perf events.

    Criterion 5, and the one worth being careful about: the close condition is
    silence_ms >= 400, or speech_ms >= 5000 and silence_ms >= 200. A pre-roll that
    reached silence_ms would satisfy the second term the moment a segment opened, so
    long utterances would be cut at exactly 5.000s with no pause at all. The count
    of why=soft_cut is what would show it, and it has to be read off the splitter
    rather than reconstructed from durations -- reconstructing it is what produced
    a segment length twice the true median elsewhere.
    """
    import collections

    import server.perf as perf_mod
    from server import vad as vad_mod

    reasons: collections.Counter = collections.Counter()
    original = perf_mod.emit

    def capture(scope, name, **fields):
        if scope == "vad" and name == "end" and fields.get("why"):
            reasons[fields["why"]] += 1
        return original(scope, name, **fields)

    perf_mod.emit = capture
    vad_mod.perf.emit = capture
    try:
        spans(pcm, gate_ms, 0, preroll_ms)
    finally:
        perf_mod.emit = original
        vad_mod.perf.emit = original
    return reasons


def report(name: str, pcm, gate_ms: int, caps) -> None:
    base = closed_segments(pcm, gate_ms, 0)
    base_len = [e[1] - e[0] for e in base]
    print(f"=== {name} ===")
    print(f"  段数 {len(base)}   跨度 p50 {np.median(base_len) * 1000:.0f}ms"
          f"   送出音频合计 {sum(e[2] for e in base):.1f}s")

    print("  上限    段数   跨度p50   预卷p50   预卷p90   被夹0   预卷吃掉上一句的字   关段原因")
    for cap in caps:
        got = closed_segments(pcm, gate_ms, cap)
        lengths = [e[1] - e[0] for e in got]
        pr = sorted(e[3] * 1000 for e in got)
        clamped = sum(1 for e in got if e[3] <= 0)
        # Criterion 7, with the definition that can actually decide it.
        #
        # Two corrections were needed before this number meant anything.
        #
        # First version: did the pre-roll start fall inside the previous segment's
        # span? No -- a span ends min_silence_ms after its last speech frame and the
        # gap is measured from that last speech frame, so a correctly clamped
        # pre-roll overlaps the span's trailing silence by construction. Overlapping
        # silence costs nothing.
        #
        # Second version: did it start before the previous segment's speech ended,
        # taken as span_start + delivered - pad? Also wrong. A segment's delivered
        # audio is its pre-roll followed by its trimmed speech, so it begins
        # preroll *before* the detected start; using the detected start put the
        # previous segment's speech boundary up to 800ms too late and reported 55 to
        # 95 percent harm that the clamp was preventing.
        #
        # The boundary is the end of the delivered audio, minus the trailing pad,
        # measured from where that audio actually starts.
        from server.config import VAD_SPEECH_PAD_MS

        pad = VAD_SPEECH_PAD_MS / 1000.0
        eaten = 0
        for i in range(1, len(got)):
            delivered_start = got[i][0] - got[i][3]
            prev_audio_start = got[i - 1][0] - got[i - 1][3]
            prev_speech_end = prev_audio_start + got[i - 1][2] - pad
            if delivered_start < prev_speech_end - 1e-6:
                eaten += 1
        why = close_reasons(pcm, gate_ms, cap)
        p50 = np.median(lengths) * 1000
        print(f"  {cap:>4}ms  {len(got):>5}  {p50:>7.0f}ms"
              f"  {pr[len(pr) // 2]:>8.0f}ms  {pr[min(len(pr) - 1, int(len(pr) * 0.9))]:>8.0f}ms"
              f"  {clamped:>5}   {eaten:>5} / {max(1, len(got) - 1)}"
              f" ({eaten / max(1, len(got) - 1) * 100:>2.0f}%)   {dict(why)}")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gate-ms", type=int, default=250)
    ap.add_argument("--caps", default="0,400,800,1200")
    args = ap.parse_args()
    caps = [int(c) for c in args.caps.split(",")]
    for name in ("e_3min", "er_3min", "meeting_6min", "clip222_90s"):
        path = ROOT / "samples" / f"{name}.wav"
        if not path.exists():
            continue
        report(name, load(path), args.gate_ms, caps)


if __name__ == "__main__":
    main()