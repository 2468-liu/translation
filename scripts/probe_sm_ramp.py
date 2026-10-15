"""Does the SM clock ramp explain part of the gap?

The audio arm's load is intermittent: the card idles between fragments and the
clock drops to its floor, so every fragment starts with the GPU still at 210 MHz
and has to climb. The whisper arm keeps the card saturated, so it may simply be
living at a higher clock the whole time.

That is a different claim from the one retracted earlier. "GPU contention" was
invented to explain a number and then measured against nothing. This asks a
question the trace can answer on its own: at the first sample of each burst, what
is the clock, and how long until it is up.

Reads the same six-column trace summarize_power.py reads -- timestamp, power,
util, memory, temperature, sm clock -- with no header row, so columns are
positional. Bursts are cut on util, which is what "the card was doing something"
means for this purpose.
"""

from __future__ import annotations

import csv
import statistics
import sys
from pathlib import Path

BUSY_UTIL = 5  # percent; below this the card is not working on our request
HIGH_CLOCK = 1900  # MHz; the boost range tops out near 2070


def load(path: Path) -> list[list[float]]:
    rows = []
    for row in csv.reader(path.read_text(encoding="utf-8").splitlines()):
        if len(row) < 6:
            continue
        try:
            rows.append([float(cell) for cell in row[1:6]])
        except ValueError:
            continue
    return rows  # power, util, memory, temperature, sm_clock


def bursts(rows: list[list[float]]) -> list[list[list[float]]]:
    out: list[list[list[float]]] = []
    current: list[list[float]] = []
    for row in rows:
        if row[1] > BUSY_UTIL:
            current.append(row)
        elif current:
            out.append(current)
            current = []
    if current:
        out.append(current)
    return [b for b in out if len(b) >= 2]


def report(name: str, path: Path) -> None:
    rows = load(path)
    groups = bursts(rows)
    first: list[float] = []
    peak: list[float] = []
    samples_to_high: list[int] = []
    lengths: list[int] = []
    idle_clock: list[float] = []
    for burst in groups:
        lengths.append(len(burst))
        first.append(burst[0][4])
        peak.append(max(row[4] for row in burst))
        for index, row in enumerate(burst):
            if row[4] >= HIGH_CLOCK:
                samples_to_high.append(index)
                break
    for row in rows:
        if row[1] <= BUSY_UTIL:
            idle_clock.append(row[4])

    def med(values: list[float]) -> str:
        return f"{statistics.median(values):7.0f}" if values else "     na"

    print(f"== {name}")
    print(f"   bursts          {len(groups)}   samples each, median {med(lengths)}")
    print(f"   clock at first sample of burst   {med(first)} MHz")
    print(f"   clock peak inside burst          {med(peak)} MHz")
    print(f"   clock while idle                 {med(idle_clock)} MHz")
    print(
        f"   samples until >= {HIGH_CLOCK}MHz   {med(samples_to_high)}"
        f"   of {med(lengths)} in the burst"
    )
    if groups:
        print(f"   power at first sample  {statistics.median(b[0][0] for b in groups):7.1f} W")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: probe_sm_ramp.py <name>=<csv> [...]")
        return 2
    for spec in argv:
        label, _, path = spec.partition("=")
        report(label.strip(), Path(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))