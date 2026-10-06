r"""Locate the best closing-silence threshold for the audio-native pipeline.

Latency here is paid per fragment, so the closing silence is a fixed cost on
every sentence. The measured relationship is first token at roughly 300ms plus
250ms per second of audio, which makes waiting longer doubly expensive: the
caption waits for the pause, then waits for the model.

Three settings span the range -- a tight one that risks cutting sentences, a
loose one that costs the most time, and the middle -- so the curve can be read
rather than guessed at. ``run`` drives it:

    .\scripts\replay.ps1 sil400 -AudioNative
    .\scripts\replay.ps1 sil700 -AudioNative
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fragments(events: list[dict]) -> list[dict]:
    """Group streamed translations back into the fragment each one belongs to.

    A fragment's text only ever grows, so a new fragment starts where the text
    is not an extension of the previous one, or where the stream paused.
    """
    out: list[dict] = []
    for event in events:
        if event.get("type") != "translation":
            continue
        text = str(event.get("text") or "")
        wall = float(event["wall"])
        if out and text.startswith(out[-1]["last"] or "") and wall - out[-1]["end"] < 3:
            out[-1]["last"] = text
            out[-1]["end"] = wall
        else:
            out.append({"start": wall, "end": wall, "last": text})
    return out


def report(name: str) -> dict:
    path = ROOT / "logs" / f"blog_3min_test.{name}.json"
    if not path.exists():
        raise SystemExit(f"missing {path}")
    events = json.loads(path.read_text(encoding="utf-8"))["events"]
    frags = fragments(events)
    starts = [float(e["wall"]) for e in events if e.get("type") == "segment_start"]

    # Time from the speaker starting to anything appearing. This is the number
    # that matters: the caption is blank for all of it.
    lag: list[float] = []
    for at in starts:
        later = [f for f in frags if f["start"] >= at - 0.05]
        if later:
            lag.append((later[0]["start"] - at) * 1000.0)

    burst = [(f["end"] - f["start"]) * 1000.0 for f in frags]
    sizes = [len(f["last"]) for f in frags]
    gaps = [(frags[i + 1]["start"] - frags[i]["end"]) * 1000.0 for i in range(len(frags) - 1)]

    def p(values: list[float], q: float) -> float:
        if not values:
            return float("nan")
        return sorted(values)[min(len(values) - 1, int(q * len(values)))]

    return {
        "fragments": len(frags),
        "silence_ms": p(gaps, 0.5),
        "blank_median": statistics.median(lag) if lag else float("nan"),
        "blank_p90": p(lag, 0.9),
        "blank_max": max(lag) if lag else float("nan"),
        "burst_median": statistics.median(burst) if burst else float("nan"),
        "chars_median": statistics.median(sizes) if sizes else float("nan"),
    }


def main(argv: list[str]) -> int:
    names = argv or ["audio1", "audio2"]
    print(f"{'run':10} {'frags':>5} {'chars':>6} {'burst':>7} {'blank med':>10} {'blank p90':>10} {'blank max':>10}")
    for name in names:
        try:
            r = report(name)
        except SystemExit as exc:
            print(f"{name}: {exc}")
            continue
        print(
            f"{name:10} {r['fragments']:5} {r['chars_median']:6.0f} {r['burst_median']:7.0f} "
            f"{r['blank_median']:10.0f} {r['blank_p90']:10.0f} {r['blank_max']:10.0f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))