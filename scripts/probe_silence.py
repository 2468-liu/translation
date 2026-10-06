r"""Locate the best closing-silence threshold for the audio-native pipeline.

Latency here is paid per fragment, so the closing silence is a fixed cost on
every sentence. The measured relationship is first token at roughly 300ms plus
250ms per second of audio, which makes waiting longer doubly expensive: the
caption waits for the pause, then waits for the model.

Tightening it buys that fixed cost back and risks cutting sentences in half, so
a level is only worth anything if the quality cost is shown next to the gain.
Three things move when the threshold moves:

  blank med/p90  when the caption goes from nothing to visible
  frags         how many sentences the material is cut into
  short         fragments under 8 characters, which is what a cut sentence
                looks like once it reaches the screen

Levels are declared on the command line rather than read from the run name, so
the provenance of every number stays in the invocation:

    python scripts/probe_silence.py 400=sil400a,sil400b 550=sil550a,sil550b
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.audit_quality import cjk_ratio, find_loop, fragments  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# Under this length a fragment is more likely a cut sentence than a short one.
# The reference material's own shortest complete lines sit at 5-6 characters.
SHORT_UNITS = 8


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

    sizes = [len(f["last"]) for f in frags]
    bad = sum(
        1 for f in frags if find_loop(f["last"]) or cjk_ratio(f["last"]) < 0.5
    )
    return {
        "frags": len(frags),
        "blank_median": statistics.median(lag) if lag else float("nan"),
        "blank_p90": pct(lag, 0.9),
        "chars_median": statistics.median(sizes) if sizes else float("nan"),
        "short": sum(1 for s in sizes if s < SHORT_UNITS),
        "bad": bad,
    }


def pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    return sorted(values)[min(len(values) - 1, int(q * len(values)))]


def spread(values: list[float]) -> str:
    """Median and range, never a single point.

    One replay of this material has been seen to differ from itself by a factor
    of two, so a lone number here would be noise wearing a decimal.
    """
    if not values:
        return "       -"
    if len(values) == 1:
        return f"{values[0]:7.0f}"
    low, high = min(values), max(values)
    return f"{statistics.median(values):7.0f} [{low:.0f}-{high:.0f}]"


def parse_levels(argv: list[str]) -> list[tuple[str, list[str]]]:
    levels = []
    for spec in argv:
        level, _, names = spec.partition("=")
        runs = [n for n in names.split(",") if n]
        if not runs:
            raise SystemExit(f"no runs given for level {level!r}")
        levels.append((level.strip(), runs))
    return levels


def main(argv: list[str]) -> int:
    levels = parse_levels(argv)
    rows = []
    for level, runs in levels:
        stats = []
        for name in runs:
            try:
                stats.append(report(name))
            except SystemExit as exc:
                print(f"{name}: {exc}")
        if not stats:
            continue
        rows.append(
            (
                level,
                runs,
                [s["frags"] for s in stats],
                [s["blank_median"] for s in stats],
                [s["blank_p90"] for s in stats],
                [s["chars_median"] for s in stats],
                [s["short"] for s in stats],
                [s["bad"] for s in stats],
            )
        )

    header = (
        f"{'sil ms':>7} {'runs':>11} {'frags':>7} {'blank med':>16} "
        f"{'blank p90':>16} {'chars':>7} {'short':>6} {'bad':>5}"
    )
    print(header)
    print("-" * len(header))
    for level, runs, frags, med, p90, chars, short, bad in rows:
        frags_s = f"{statistics.median(frags):.0f}" + (
            f" [{min(frags)}-{max(frags)}]" if len(frags) > 1 else ""
        )
        chars_s = f"{statistics.median(chars):.0f}" + (
            f" [{min(chars):.0f}-{max(chars):.0f}]" if len(chars) > 1 else ""
        )
        print(
            f"{level:>7} {len(runs):>11} {frags_s:>7} {spread(med):>16} "
            f"{spread(p90):>16} {chars_s:>7} {sum(short):>6} {sum(bad):>5}"
        )
    if rows:
        print()
        print(f"runs: {', '.join(f'{lv}={len(r)}' for lv, r, *_ in rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
