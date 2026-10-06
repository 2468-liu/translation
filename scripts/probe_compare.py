r"""Put the two pipelines on the same axes, from logs that already exist.

The audio-native path can only speak once a fragment closes, so "speech start to
first character" measures the length of the sentence rather than the speed of
anything. Comparing that number against whisper's is comparing a sentence to a
pipeline: whisper's partials put text up mid-sentence, so it always looks faster.

Two numbers are comparable, and both are measured from the server's own log so
no zero has to be inferred:

  first on screen   speech start to any character, which is what a viewer feels
  settled           the speaker's last word to the screen going quiet, which is
                    what decides whether the caption is behind or on top

The second is the one that separates the routes. Whisper has to transcribe and
then translate a finished utterance; the audio path translates the waveform
directly, with no separate recognition step to wait for.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.probe_silence import _resolve  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

_ENQUEUE = re.compile(r"^(\S+) pipeline\.enqueue kind=(\w+).*audio_ms=([\d.]+)")


def read_perf(name: str) -> list[dict]:
    path = _resolve("perf", ".log", "perf.log", name)
    if not path.exists():
        raise SystemExit(f"missing {path}")
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        head, _, tail = line.partition(" ")
        for event in ("pipeline.enqueue", "pipeline.asr", "pipeline.translate"):
            if tail.startswith(event):
                row = {"wall": float(head), "event": event.split(".", 1)[1]}
                for field in tail.split()[1:]:
                    if "=" in field:
                        key, _, value = field.partition("=")
                        try:
                            row[key] = float(value)
                        except ValueError:
                            row[key] = value
                rows.append(row)
                break
    return rows


def measure(name: str) -> dict:
    events = json.loads(
        _resolve("blog_3min_test", ".json", "blog_3min_test.json", name).read_text(encoding="utf-8")
    )["events"]
    rows = read_perf(name)
    packets = [float(e["wall"]) for e in events if e.get("type") == "translation"]

    closed = [r for r in rows if r["event"] == "enqueue" and r.get("kind") == "end"]
    if not closed:
        raise SystemExit(f"{name}: no closed fragments in its server log")
    if not packets:
        raise SystemExit(f"{name}: no translations in its event log")

    # Each closed fragment owns the translations between it and the next close.
    #
    # Grouping the streamed packets by watching the text grow does not survive the
    # whisper route: its partials revise a line rather than extend it, so nothing
    # is ever a prefix of what came before and one utterance fragments into a
    # dozen. The close is the boundary both routes agree on.
    #
    # Boundaries come from the close times alone. Deriving a speech start as
    # "closed minus audio_ms" does not work: audio_ms counts the whole buffer
    # handed over, overlaps the previous fragment, and sums to 283s over a 179s
    # replay. The derived starts are not monotonic, the windows nest, and packets
    # land in the wrong fragment.
    bounds = [row["wall"] for row in closed]
    edges = [-float("inf")] + bounds[1:] + [float("inf")]

    lead: list[float] = []
    settled: list[float] = []
    speech: list[float] = []
    empty = 0
    for row, before, until in zip(closed, edges, edges[1:]):
        own = [w for w in packets if before <= w < until]
        if not own:
            empty += 1
            continue
        speech.append(row["audio_ms"])
        # How far ahead of the close the first text appeared. Negative means the
        # caption was already up while the speaker was still going, which is the
        # whole structural difference between the routes.
        lead.append((min(own) - row["wall"]) * 1000.0)
        settled.append((max(own) - row["wall"]) * 1000.0)

    # Whisper's own cost, for the record: the transcription call, not the caption.
    asr = [r["asr"] for r in rows if r["event"] == "asr" and r.get("kind") == "end"]
    return {
        "frags": len(closed),
        "lead": lead,
        "settled": settled,
        "speech": speech,
        "asr_end": asr,
        "empty": empty,
    }


def pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    return sorted(values)[min(len(values) - 1, int(q * len(values)))]


def main(argv: list[str]) -> int:
    groups = []
    for spec in argv:
        label, _, names = spec.partition("=")
        runs = [n for n in names.split(",") if n]
        if not runs:
            raise SystemExit(f"no runs for {label!r}")
        groups.append((label.strip(), runs))

    header = (
        f"{'route':22} {'runs':>4} {'frags':>6} {'empty':>6} "
        f"{'lead med':>10} {'lead p90':>10} {'settled med':>12} {'settled p90':>12}"
    )
    print(header)
    print("-" * len(header))
    for label, runs in groups:
        stats = []
        for name in runs:
            try:
                stats.append(measure(name))
            except SystemExit as exc:
                print(f"  {name}: {exc}")
        if not stats:
            continue

        def pooled(key: str) -> list[float]:
            out: list[float] = []
            for s in stats:
                out.extend(s[key])
            return out

        lead, settled = pooled("lead"), pooled("settled")
        frags = [s["frags"] for s in stats]
        asr = pooled("asr_end")
        frags_s = f"{statistics.median(frags):.0f}"
        if len(frags) > 1:
            frags_s += f"[{min(frags)}-{max(frags)}]"
        print(
            f"{label:22} {len(runs):>4} {frags_s:>6} {sum(s['empty'] for s in stats):>6} "
            f"{statistics.median(lead):10.0f} {pct(lead, 0.9):10.0f} "
            f"{statistics.median(settled):12.0f} {pct(settled, 0.9):12.0f}"
        )
        if asr:
            print(
                f"{'':22} {'':>4} {'asr per closed utterance:':>34} "
                f"n={len(asr):<4} median {statistics.median(asr):.0f}ms"
            )
    print()
    print("lead    = fragment close -> first character on screen; negative means")
    print("          the caption was already up while the speaker was still going")
    print("settled = fragment close -> caption stops changing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
