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

import hashlib
import json
import re
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


_PAINT = re.compile(r"^(\S+) caption\.paint .*(?: sig=(\S+))?$")


def paints(name: str) -> list[tuple[float, str]]:
    """(wall, signature of the text shown) for every draw.

    The signature is what makes the last two columns meaningful: without it a
    paint can only be placed in time, so "the sentence's final text reached the
    screen" has to be guessed by taking the next draw of any kind, which is
    usually a different sentence's line.
    """
    path = _resolve("perf_caption", ".log", "perf_caption.log", name)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if " caption.paint " not in line:
            continue
        head, _, tail = line.partition(" ")
        try:
            wall = float(head)
        except ValueError:
            continue
        sig = ""
        for field in tail.split():
            if field.startswith("sig="):
                sig = field.split("=", 1)[1]
        out.append((wall, sig))
    return sorted(out)


def signature(text: str) -> str:
    """The caption window's own digest of a line, so the two sides agree."""
    return hashlib.sha1((text or "").strip().encode("utf-8")).hexdigest()[:12]


def packets(name: str) -> list[tuple[float, float, str]]:
    """(playback position, wall, text) for every translation packet."""
    events = json.loads(
        _resolve("blog_3min_test", ".json", "blog_3min_test.json", name).read_text(encoding="utf-8")
    )["events"]
    return [
        (float(e["audio_sec"]), float(e["wall"]), str(e.get("text") or ""))
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
    first_shown: list[float] = []
    final_shown: list[float] = []
    missed = 0
    # Sentences whose finished text never reached the screen at all. Counted
    # rather than skipped: a line that arrives late and a line that never
    # arrives are both coverage gaps, and averaging only the late ones would
    # flatter the number.
    never_shown = 0
    for index, row in enumerate(sentences):
        end = row["end"]
        nxt = sentences[index + 1]["start"] if index + 1 < len(sentences) else end + 5.0
        own = [(a, w, t) for a, w, t in stream if row["start"] - GRACE_SEC <= a <= nxt]
        if not own:
            missed += 1
            continue
        began = off + row["start"]
        ended = off + end
        first_wall = own[0][1]
        last_wall = own[-1][1]
        to_first.append((first_wall - began) * 1000.0)
        settled.append((last_wall - ended) * 1000.0)
        after = [p for p in shots if p[0] >= first_wall]
        if after:
            display.append((after[0][0] - first_wall) * 1000.0)
            first_shown.append((after[0][0] - began) * 1000.0)

        # The draw that first showed this sentence's finished text. Matched on
        # the digest the caption window itself computes, so a line the window
        # trimmed on the way in is correctly reported as not shown.
        want = signature(own[-1][2])
        shown = [p for p in shots if p[1] == want]
        if shown:
            final_shown.append((shown[0][0] - ended) * 1000.0)
        else:
            never_shown += 1

    return {
        "sentences": len(sentences),
        "missed": missed,
        "to_first": to_first,
        "display": display,
        "settled": settled,
        "first_shown": first_shown,
        "final_shown": final_shown,
        "never_shown": never_shown,
    }


def main(argv: list[str]) -> int:
    groups = []
    for spec in argv:
        label, _, names = spec.partition("=")
        groups.append((label.strip(), [n for n in names.split(",") if n]))
    sentences = json.loads(TIMING.read_text(encoding="utf-8"))

    header = (
        f"{'route':20} {'n':>3} {'sent':>4} {'nosh':>5} "
        f"{'first shown':>16} {'final shown':>16} {'to_first':>9} {'settled':>9}"
    )
    print(header)
    print("-" * len(header))
    for label, runs in groups:
        pooled: dict[str, list[float]] = {
            k: [] for k in ("to_first", "display", "settled", "first_shown", "final_shown")
        }
        missed = seen = never = 0
        for name in runs:
            try:
                got = measure(name, sentences)
            except SystemExit as exc:
                print(f"  {name}: {exc}")
                continue
            for key in pooled:
                pooled[key].extend(got[key])
            missed += got["missed"]
            never += got["never_shown"]
            seen += got["sentences"]
        if not seen:
            continue
        cells = []
        for key in ("first_shown", "final_shown"):
            v = pooled[key]
            body = f"{statistics.median(v):.0f} [{pct(v, 0.9):.0f}]" if v else "-"
            cells.append(f"{body:>16}")
        for key in ("to_first", "settled"):
            v = pooled[key]
            cells.append(f"{statistics.median(v):>9.0f}" if v else f"{'-':>9}")
        print(
            f"{label:20} {len(runs):>3} {seen:>4} {never:>5} "
            + " ".join(cells)
        )
    print()
    print("first shown = speech starts -> first character drawn  (what a viewer sees)")
    print("final shown = speech ends   -> the finished text drawn")
    print("to_first    = speech starts -> first packet              (server only)")
    print("settled     = speech ends   -> last packet               (server only)")
    print("nosh        = sentences whose finished text never reached the screen")
    print("all zeroed on samples/blog_3min_timing.json, so thresholds do not move them")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
