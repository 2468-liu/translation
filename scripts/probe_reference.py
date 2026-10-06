r"""One zero for every route and every threshold: the reference audio's own timeline.

Two earlier versions of this comparison were not comparable across
configurations. Both zeroed on the moment the server closed a fragment, and that
moment is the end of speech plus the closing-silence threshold -- so a 400ms arm
and a 1000ms arm were measured from points 600ms apart and the arm that waited
longest looked the slowest.

The source material fixes the speech boundaries exactly. samples/blog_3min_timing.json
carries a start and an end per sentence, taken from the recording rather than from
any threshold, so every route and every setting is measured from the same place.

Four numbers per sentence, so a slow caption can be attributed rather than just
observed:

  to_first    speech starts  -> first translation packet leaves the server
  display     first packet    -> first pixel
  settled     speech ends     -> last packet for that sentence

`display` is the caption window's share and it is the one that decides whether
roll-up and the settle experiment are worth doing. A route that streams early and
then waits three seconds for the window is not slow because it translates slowly.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.probe_silence import _resolve  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TIMING = ROOT / "samples" / "blog_3min_timing.json"

# A packet this long after the next sentence began belongs to that sentence.
# Generous, because a caption for the previous sentence is still on screen while
# the next one is being spoken and its packets keep arriving.
GRACE_SEC = 0.0


def offset_of(name: str) -> float:
    events = json.loads(
        _resolve("blog_3min_test", ".json", "blog_3min_test.json", name).read_text(encoding="utf-8")
    )["events"]
    for event in events:
        if event.get("audio_sec") is not None:
            return float(event["wall"]) - float(event["audio_sec"])
    raise SystemExit(f"{name}: no playback position in its log")


def paints(name: str) -> list[float]:
    path = _resolve("perf_caption", ".log", "perf_caption.log", name)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if " caption.paint " not in line:
            continue
        try:
            out.append(float(line.split(" ", 1)[0]))
        except ValueError:
            continue
    return sorted(out)


def packets(name: str) -> list[tuple[float, float]]:
    """(playback position, wall) for every translation packet."""
    events = json.loads(
        _resolve("blog_3min_test", ".json", "blog_3min_test.json", name).read_text(encoding="utf-8")
    )["events"]
    return [
        (float(e["audio_sec"]), float(e["wall"]))
        for e in events
        if e.get("type") == "translation" and e.get("audio_sec") is not None
    ]


def pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    return sorted(values)[min(len(values) - 1, int(q * len(values)))]


def measure(name: str, sentences: list[dict]) -> dict:
    off = offset_of(name)
    shots = paints(name)
    stream = packets(name)

    to_first: list[float] = []
    display: list[float] = []
    settled: list[float] = []
    missed = 0
    for index, row in enumerate(sentences):
        end = row["end"]
        nxt = sentences[index + 1]["start"] if index + 1 < len(sentences) else end + 5.0
        own = [(a, w) for a, w in stream if row["start"] - GRACE_SEC <= a <= nxt]
        if not own:
            missed += 1
            continue
        began = off + row["start"]
        ended = off + end
        first_wall = own[0][1]
        last_wall = own[-1][1]
        to_first.append((first_wall - began) * 1000.0)
        settled.append((last_wall - ended) * 1000.0)
        # The first paint after this sentence's first packet. If the window was
        # holding an earlier line, no paint happens until the hold expires, which
        # is exactly the delay this column exists to show.
        after = [p for p in shots if p >= first_wall]
        if after:
            display.append((after[0] - first_wall) * 1000.0)

    return {
        "sentences": len(sentences),
        "missed": missed,
        "to_first": to_first,
        "display": display,
        "settled": settled,
    }


def main(argv: list[str]) -> int:
    groups = []
    for spec in argv:
        label, _, names = spec.partition("=")
        groups.append((label.strip(), [n for n in names.split(",") if n]))
    sentences = json.loads(TIMING.read_text(encoding="utf-8"))

    header = (
        f"{'route':22} {'runs':>4} {'sent':>5} {'miss':>5} "
        f"{'to_first':>17} {'display':>17} {'settled':>17}"
    )
    print(header)
    print("-" * len(header))
    for label, runs in groups:
        pooled: dict[str, list[float]] = {"to_first": [], "display": [], "settled": []}
        missed = seen = 0
        for name in runs:
            try:
                got = measure(name, sentences)
            except SystemExit as exc:
                print(f"  {name}: {exc}")
                continue
            for key in pooled:
                pooled[key].extend(got[key])
            missed += got["missed"]
            seen += got["sentences"]
        if not seen:
            continue
        cells = []
        for key in ("to_first", "display", "settled"):
            v = pooled[key]
            body = f"{statistics.median(v):.0f} [p90 {pct(v, 0.9):.0f}]" if v else "-"
            cells.append(f"{body:>17}")
        print(f"{label:22} {len(runs):>4} {seen:>5} {missed:>5} " + " ".join(cells))
    print()
    print("to_first = speech starts -> first packet      (server)")
    print("display  = first packet   -> first pixel      (caption window)")
    print("settled  = speech ends    -> last packet      (both)")
    print("all zeroed on samples/blog_3min_timing.json, so thresholds do not move them")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
