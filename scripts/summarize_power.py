r"""Summarise a GPU power trace into the numbers a reader can compare.

Reports the mean, p95 and maximum rather than a single figure: a card that idles
between sentences and peaks while decoding has an average that describes neither
state, and the peak is what a laptop has to cool.

Memory comes in two columns on purpose. Per-process is what the models cost; the
whole-card figure includes the desktop compositor and the browser, which on a 4GB
card is the difference between fitting and not.
"""

from __future__ import annotations

import csv
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_samples(path: Path) -> list[tuple[float, float, float]]:
    """timestamp, power.draw, utilization.gpu, memory.used -- in that order.

    Three numbers, not four: the query does not ask for total memory, and reading
    a column that was never requested yields an empty trace that looks like a
    machine that drew no power at all.
    """
    rows = []
    for row in csv.reader(path.read_text(encoding="utf-8").splitlines()):
        if len(row) < 4:
            continue
        try:
            power, util, used = (float(cell) for cell in row[1:4])
        except ValueError:
            continue
        rows.append((power, util, used))
    return rows


def pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    return sorted(values)[min(len(values) - 1, int(q * len(values)))]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        raise SystemExit("usage: summarize_power.py <csv> <name> [--apps ..] [--total ..]")
    path = Path(argv[0])
    name = argv[1]
    apps = ""
    total = ""
    for flag in argv[2:]:
        if flag == "--apps" and argv.index(flag) + 1 < len(argv):
            apps = argv[argv.index(flag) + 1]
        if flag == "--total" and argv.index(flag) + 1 < len(argv):
            total = argv[argv.index(flag) + 1]

    rows = read_samples(path)
    if not rows:
        raise SystemExit(f"no usable samples in {path}")
    power = [r[0] for r in rows]
    util = [r[1] for r in rows]
    used = [r[2] for r in rows]
    busy = [u for u in util if u > 0]

    app_mb = sum(float(line.split(",")[1]) for line in apps.splitlines() if "," in line)
    total_mb = float(total.split(",")[0]) if "," in total else float("nan")
    cap_mb = float(total.split(",")[1]) if "," in total else float("nan")

    print(f"== {name}")
    print(f"   samples          {len(rows)}")
    print(
        f"   board power W    mean {statistics.mean(power):6.1f}   "
        f"p95 {pct(power, 0.95):6.1f}   max {max(power):6.1f}"
    )
    print(
        f"   gpu util %       mean {statistics.mean(util):6.1f}   "
        f"p95 {pct(util, 0.95):6.1f}   max {max(util):6.1f}"
    )
    # The duty cycle, over the seconds the card was doing nothing as well as the
    # ones it was busy. Averaging over busy seconds alone would flatter a
    # pipeline that idles politely between sentences.
    if busy:
        print(
            f"   busy fraction    {len(busy) / len(util) * 100:6.1f}% of samples,"
            f" util {statistics.mean(busy):.0f}% while busy"
        )
    print(
        f"   card memory MiB  mean {statistics.mean(used):6.0f}   max {max(used):6.0f}"
        f"   of {cap_mb:.0f}"
    )
    print(
        f"   compute procs    {app_mb:6.0f} MiB"
        f"   (rest of card: {total_mb - app_mb:.0f} MiB)"
    )
    print(f"   trace            {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
