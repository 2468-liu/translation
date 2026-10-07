r"""Which clock actually held the caption back.

The display leg was reported as one number and then split two ways, and neither
split held up. The hold explained the tail. The median sat near SETTLE_MS, which
is 450. So two candidates were left with no way to tell them apart, and the way
they had been told apart before -- reconstructing hold intervals and checking
whether a packet fell inside one -- cannot distinguish a hold from a settle,
because both delay a draw the same way and both overlap in time.

The window now writes down which candidate set the deadline and what each
candidate was, at the moment the decision was made. This reads that.

The distinction matters for what to do next. A draw blocked by the hold is a
readability choice and should not be optimised away. A draw blocked by the settle
is a flicker guard that may be set longer than it needs to be. And a draw blocked
by next_utterance is neither -- it is the window refusing to replace a line the
speaker has already moved past, which is neither of the other two.
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from perf_compare import first_block, load, numbers  # noqa: E402
from probe_silence import _resolve  # noqa: E402


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else float("nan")


def _pct(values: list[float], q: float) -> float:
    clean = [v for v in values if v == v]
    if not clean:
        return float("nan")
    return sorted(clean)[min(len(clean) - 1, int(q * len(clean)))]


def report(name: str) -> dict | None:
    path = _resolve("perf_caption", ".log", "perf_caption.log", name)
    if not path.exists():
        return {"name": name, "skipped": "no caption log"}
    block = first_block(load(path))
    rows = [r for r in block if r[1] == "caption.deadline"]
    if not rows:
        return {"name": name, "skipped": "no caption.deadline rows (run predates the probe)"}
    by_why: dict[str, list[float]] = {}
    actions: dict[str, dict[str, int]] = {}
    for _wall, _name, fields in rows:
        why = fields.get("why", "?")
        raw = fields.get("wait")
        if raw is not None:
            try:
                by_why.setdefault(why, []).append(float(raw))
            except ValueError:
                pass
        key = f"{fields.get('action', '?')}"
        actions.setdefault(why, {})
        actions[why][key] = actions[why].get(key, 0) + 1
    return {
        "name": name,
        "by_why": by_why,
        "actions": actions,
        "total": len(rows),
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    groups = []
    for spec in argv:
        label, _, names = spec.partition("=")
        groups.append((label.strip(), [n for n in names.split(",") if n]))

    print(f"{'arm':16} {'n':>4}  {'why':<14} {'count':>6} {'wait p50':>9} {'wait p90':>9}")
    print("-" * 66)
    for label, names in groups:
        pooled: dict[str, list[float]] = {}
        count = 0
        for name in names:
            got = report(name)
            if got is None:
                continue
            if "skipped" in got:
                print(f"  {name}: {got['skipped']}")
                continue
            count += got["total"]
            for why, values in got["by_why"].items():
                pooled.setdefault(why, []).extend(values)
        if not pooled:
            print(f"{label:16} {0:>4}  (nothing)")
            continue
        for why in sorted(pooled, key=lambda k: -len(pooled[k])):
            values = pooled[why]
            print(
                f"{label if why == max(pooled, key=lambda k: len(pooled[k])) else '':16} "
                f"{count:>4}  {why:<14} {len(values):>6} "
                f"{_median(values):>9.0f} {_pct(values, 0.90):>9.0f}"
            )
    print()
    print("why          which candidate set the swap deadline for this decision")
    print("hold         readability: the finished line stays up long enough to read")
    print("settle       flicker guard after a one-character revision")
    print("readable_at  the shortened hold used when the next utterance has started")
    print("next_utterance  the window will not replace a line the speaker has passed")
    print()
    print("wait is how far in the future that deadline was, when the packet arrived.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))