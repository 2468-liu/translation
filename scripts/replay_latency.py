"""Latency and coverage from a replay, aligned to the clip's own speech timeline.

The comparison the user asked for is "against the log's audio track". Two clocks
are in the replay output and they answer different questions:

  audio_sec   where in the CLIP the event happened -- the speech's own timeline
  wall        when it happened -- for aligning events across processes

"First text on screen" alone is not comparable between the two paths, because
whisper puts a partial up mid-sentence while the audio-native model cannot answer
until the fragment closes. The number that is comparable is measured from the
speech, so this reports:

  lag_ms      first translation on screen, minus the start of the sentence the
              timeline says that speech belongs to (the replay already computes
              it; it is reported here with the distribution rather than alone)
  asr_ms      recogniser time per call, and how much of the wall clock it takes
  coverage    how many timeline sentences produced any translation at all

Reads the JSON the replay writes, so it works on an archived run.
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"no such replay output: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def dist(values: list[float]) -> str:
    if not values:
        return "n=0"
    ordered = sorted(values)
    p50 = statistics.median(ordered)
    p90 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))]
    return (f"n={len(ordered)}  p50={p50:.0f}  p90={p90:.0f}  "
            f"min={ordered[0]:.0f}  max={ordered[-1]:.0f}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="+", help="clip names, e.g. blog_3min e_3min")
    ap.add_argument("--dir", default="logs")
    ap.add_argument("--tag", default="", help="replay name suffix; empty = search")
    args = ap.parse_args()

    base = ROOT / args.dir
    for name in args.names:
        candidates = sorted(base.glob(f"blog_3min_test.*{name}*.json")) or \
                     sorted(base.glob(f"*{name}*.json"))
        candidates = [c for c in candidates if c.name != "blog_3min_timing.json"]
        if not candidates:
            print(f"\n===== {name}: no replay output found in {base} =====")
            continue
        path = candidates[-1]
        data = load(path)
        sentences = data.get("sentences") or []
        events = data.get("events") or []

        # Timing per sentence, straight from the replay's own bookkeeping.
        lags = [r["lag_ms"] for r in sentences if isinstance(r.get("lag_ms"), (int, float))]
        translated = [r for r in sentences if (r.get("translation") or "").strip()]
        asr_texts = [r for r in sentences if (r.get("asr") or "").strip()]

        # ASR cost from the event stream, which carries both clocks.
        asr_events = [e for e in events if e.get("type") == "asr"]
        translations = [e for e in events if e.get("type") == "translation"]
        coverage = data.get("timeline_header") or {}

        print(f"\n===== {name}   ({path.name}) =====")
        print(f"clip {data.get('duration_sec')}s   timeline sentences {len(sentences)}"
              f"   covered_span {coverage.get('covered_span')}")
        print(f"translation events {data.get('translation_events')}   "
              f"asr finals {data.get('asr_finals')}   "
              f"growths {data.get('growths')}   shrinks {data.get('shrinks')}   "
              f"low-overlap swaps {data.get('low_overlap_swaps')}")
        print(f"first-text lag   {dist(lags)}")
        print(f"coverage         {len(translated)}/{len(sentences)} sentences produced a translation"
              f"   ({100 * len(translated) / max(1, len(sentences)):.0f}%)")
        print(f"recogniser text  {len(asr_texts)}/{len(sentences)} sentences had recogniser output")

        if asr_events:
            spans = sorted(
                (e["audio_sec"], e["wall"]) for e in asr_events if "audio_sec" in e and "wall" in e
            )
            if spans:
                first, last = spans[0], spans[-1]
                print(f"asr events       {len(asr_events)}  audio {first[0]:.1f}s..{last[0]:.1f}s"
                      f"  wall span {last[1] - first[1]:.1f}s")
        if translations:
            ts = sorted(e["audio_sec"] for e in translations if "audio_sec" in e)
            if ts:
                print(f"translations at  audio {ts[0]:.1f}s .. {ts[-1]:.1f}s")

        # Sentence level pairs, for reading quality against the timeline.
        print("  per sentence:")
        for r in sentences:
            lag = r.get("lag_ms")
            lag_s = f"{lag:>7.0f}ms" if isinstance(lag, (int, float)) else "      -"
            src = (r.get("source") or "")[:44]
            got = (r.get("translation") or "")[:30]
            print(f"    {r.get('i', '?'):>3} {lag_s}  {src!r:48} -> {got!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
