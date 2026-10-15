r"""Why the audio was cut the way it was, and where the pauses actually are.

Two questions this answers that the logs previously could not.

The first is why a segment ended. There are four ways -- a pause long enough to
close it, the soft cut at "enough speech plus a short pause", the hard length cap,
and the stream ending -- and only the last of them shows up in the event stream by
name. Everything else arrived as an indistinguishable "end", which is why a real
recording was described as having 8.2 s segments when the median was 1.37 s: that
8.2 came from dividing wall-clock by a count that excluded every segment the reuse
path had handled. The two numbers spanned different populations and the division
pretended they were one.

The second is the shape of the pauses. Counting only the pauses that closed a
segment answers nothing about what a different threshold would have done, so every
run is recorded -- including the ones that never closed anything -- and binned on
the boundaries the project has actually used: 150, 200, 400, 700, 1000 ms.

Reads vad.end and vad.silence, emitted by the splitter at the moment each happened.
"""

from __future__ import annotations

import collections
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from perf_compare import first_block, load  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

BIN_ORDER = ("lt100", "lt200", "lt300", "lt400", "lt700", "lt1000", "ge1000")


def _rows(name: str) -> list[tuple[float, str, dict]]:
    path = ROOT / "logs" / f"perf.{name}.log"
    if not path.exists():
        return []
    return first_block(load(path))


def report(names: list[str]) -> None:
    why = collections.Counter()
    speech_by_why: dict[str, list[float]] = collections.defaultdict(list)
    silence_by_why: dict[str, list[float]] = collections.defaultdict(list)
    bins_in: collections.Counter[str] = collections.Counter()
    bins_out: collections.Counter[str] = collections.Counter()
    earliest = 0
    usable = 0

    for name in names:
        rows = _rows(name)
        if not rows:
            print(f"  {name}: no perf log")
            continue
        usable += 1
        earliest += sum(1 for r in rows if r[1] == "vad.end" and r[2].get("early_partial") == "1")
        for _wall, tag, fields in rows:
            if tag == "vad.end":
                key = fields.get("why", "?")
                why[key] += 1
                for source, bucket in ((fields.get("speech_ms"), speech_by_why),
                                       (fields.get("silence_ms"), silence_by_why)):
                    if source:
                        bucket[key].append(float(source))
            elif tag == "vad.silence":
                b = fields.get("bin", "?")
                if fields.get("inside_utterance") == "1":
                    bins_in[b] += 1
                else:
                    bins_out[b] += 1

    if not usable:
        print("no usable arms")
        return

    total = sum(why.values())
    print(f"=== end 原因（{usable} 格，共 {total} 次）===")
    print(f"{'why':14} {'n':>6} {'占比':>7} {'语音时长中位':>12} {'关闭时静音中位':>14}")
    for key in ("silence", "soft_cut", "max_length", "flush"):
        if not why[key]:
            continue
        sp = statistics.median(speech_by_why[key]) if speech_by_why[key] else float("nan")
        si = statistics.median(silence_by_why[key]) if silence_by_why[key] else float("nan")
        print(f"{key:14} {why[key]:6d} {why[key] / total * 100:6.1f}% {sp:11.0f}ms {si:13.0f}ms")
    if earliest:
        print(f"{'（其中带提前 partial）':14} {earliest:6d} {earliest / total * 100:6.1f}%")

    print()
    print("=== 静音段直方图（所有连续非语音段，不论是否关了段）===")
    print(f"{'bin(ms)':12} {'段内':>6} {'段间':>6} {'合计':>6}")
    for b in BIN_ORDER:
        a, c = bins_in[b], bins_out[b]
        if a or c:
            print(f"{b:12} {a:6d} {c:6d} {a + c:6d}")
    everything = sum(bins_in.values()) + sum(bins_out.values())
    if everything:
        print(f"{'total':12} {sum(bins_in.values()):6d} {sum(bins_out.values()):6d} {everything:6d}")

        def share(bins: collections.Counter, edge: float) -> float:
            below = sum(v for k, v in bins.items() if float(k[2:]) <= edge)
            return below / max(1, sum(bins.values())) * 100

        print()
        print("  若把关闭阈值设为 X，会切在多少比例的停顿上（段内 + 段间合计）：")
        for edge in (150, 200, 400, 700, 1000):
            combined = collections.Counter()
            combined.update(bins_in)
            combined.update(bins_out)
            below = sum(v for k, v in combined.items() if float(k[2:]) <= edge)
            print(f"    {edge:5d} ms -> {below / max(1, everything) * 100:5.1f}%")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    names: list[str] = []
    for spec in argv:
        label, _, listed = spec.partition("=")
        names.extend(n for n in listed.split(",") if n)
    report(names)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))