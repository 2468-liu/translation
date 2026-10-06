r"""Line each fragment's translation up against the script it came from.

Latency and coverage both answer whether the caption arrived; neither answers
whether it was right. On this material that can be checked, because the TTS
source is known sentence for sentence and each sentence has a start and an end in
samples/blog_3min_timing.json.

A fragment is assigned to the reference sentence its close falls inside. The
scores are mechanical, not a judgement of fluency:

  0  unusable   loops, falls back to transcribing the English, or empty
  1  lossy      reads as a translation but drops or adds content the sentence
                did not have -- detectable as a length far outside the band a
                faithful Chinese rendering of the same sentence occupies
  2  faithful   inside the band

The length band is the only automatic signal and it is a weak one: it flags
sentences worth reading, not sentences that are wrong. Printed pairs are meant to
be read. Nothing here scores the default route or the audio route against each
other -- one replay's fragments are longer than the other's, so the bands do not
transfer.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.audit_quality import cjk_ratio, find_loop  # noqa: E402
from scripts.probe_silence import _resolve, enqueues  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TIMING = ROOT / "samples" / "blog_3min_timing.json"

# A faithful rendering of one English sentence into Chinese lands well short of
# it in characters and nowhere near its length in words. These bounds are wide on
# purpose: the point is to catch a fragment that lost or invented a clause, not
# to grade style.
MIN_RATIO = 0.25
MAX_RATIO = 0.85


def audio_offset(name: str) -> float:
    """Wall clock minus playback position, so a wall time becomes an audio time.

    Taken from the first event in the run rather than assumed: the replay starts
    its clock when it starts playing, and assuming a start would slide every
    assignment by however long the connection took.
    """
    events = json.loads(
        _resolve("blog_3min_test", ".json", "blog_3min_test.json", name).read_text(encoding="utf-8")
    )["events"]
    for event in events:
        if event.get("audio_sec") is not None:
            return float(event["wall"]) - float(event["audio_sec"])
    raise SystemExit(f"{name}: no event carries a playback position")


def final_texts(name: str, closed: list[tuple[float, float]]) -> list[str]:
    """The finished text of each closed fragment, in order.

    Taken from the windows between closes rather than by watching the text grow.
    Grouping by growth assumes each packet extends the last, which is true of the
    audio path and false of whisper: its partials revise a line in place, so
    nothing is ever a prefix of what came before and every packet starts a new
    group. On whisper that yields zero texts and the run is skipped.
    """
    events = json.loads(
        _resolve("blog_3min_test", ".json", "blog_3min_test.json", name).read_text(encoding="utf-8")
    )["events"]
    packets = [
        (float(e["wall"]), str(e.get("text") or ""))
        for e in events
        if e.get("type") == "translation"
    ]
    bounds = [row[0] for row in closed]
    edges = [-float("inf")] + bounds[1:] + [float("inf")]
    out = []
    for before, until in zip(edges, edges[1:]):
        own = [text for wall, text in packets if before <= wall < until]
        out.append(own[-1] if own else "")
    return out


def assign(at: float, sentences: list[dict]) -> dict | None:
    for row in sentences:
        if row["start"] <= at <= row["end"]:
            return row
    # A fragment can close just past its sentence's end; take the nearest.
    if sentences:
        return min(sentences, key=lambda r: min(abs(at - r["start"]), abs(at - r["end"])))
    return None


def score(text: str, reference: str) -> tuple[int, str]:
    if not text.strip():
        return 0, "empty"
    loop = find_loop(text)
    if loop:
        return 0, f"loops on {loop!r}"
    if cjk_ratio(text) < 0.5:
        return 0, f"not translated (cjk {cjk_ratio(text):.0%})"
    ratio = len(text) / max(1, len(reference))
    if ratio < MIN_RATIO:
        return 1, f"short: {ratio:.2f} of source length"
    if ratio > MAX_RATIO:
        return 1, f"long: {ratio:.2f} of source length"
    return 2, "in band"


def main(argv: list[str]) -> int:
    names = argv or ["audio1"]
    sentences = json.loads(TIMING.read_text(encoding="utf-8"))
    for name in names:
        try:
            offset = audio_offset(name)
            closed = enqueues(name)
        except SystemExit as exc:
            print(f"{name}: {exc}")
            continue
        finals = final_texts(name, closed)

        buckets: dict[int, list[str]] = {}
        for row, text in zip(closed, finals):
            if not text.strip():
                continue
            at = row[0] - offset
            target = assign(at, sentences)
            if target is None:
                continue
            buckets.setdefault(int(target["i"]), []).append(text)

        tally = {0: 0, 1: 0, 2: 0}
        print(f"== {name}")
        for row in sentences:
            got = buckets.get(int(row["i"]), [])
            joined = " ".join(got)
            if not got:
                print(f"  [{row['i']:2}] -- nothing translated --")
                continue
            value, why = score(joined, row["text"])
            tally[value] += 1
            flag = {0: "FAIL", 1: "thin", 2: "ok  "}[value]
            print(f"  [{row['i']:2}] {flag} {why}")
            print(f"        src: {row['text'][:78]}")
            print(f"        out: {joined[:78]}")
        total = sum(tally.values()) or 1
        print(
            f"  tally  0={tally[0]}  1={tally[1]}  2={tally[2]}"
            f"   ({tally[0] / total * 100:.0f}% unusable of {total} sentences)"
        )
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
