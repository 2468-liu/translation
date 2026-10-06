r"""Summarise a GPU power trace into the numbers a reader can compare.

Reports the mean, p95 and maximum rather than a single figure: a card that idles
between sentences and peaks while decoding has an average that describes neither
state, and the peak is what a laptop has to cool.

The first samples are dropped. They cover the model warming up and the card
climbing to its clocks, which belong to no configuration, and including them
makes a short run read as cooler than a long one.

Per-process memory is requested but frequently unavailable: under WDDM on Windows
nvidia-smi answers N/A for it, so the honest report is that the number could not
be had rather than the whole-card figure relabelled as a process figure.
"""

from __future__ import annotations

import csv
import statistics
import sys
from pathlib import Path


def read_samples(path: Path) -> list[dict]:
    """timestamp, power, util, memory.used, temperature, sm clock.

    Six columns, matching the query that produced the file. Reading a column the
    query never asked for yields an empty trace that looks like a machine that
    drew no power at all.
    """
    rows = []
    for row in csv.reader(path.read_text(encoding="utf-8").splitlines()):
        if len(row) < 4:
            continue
        values = []
        for cell in row[1:]:
            try:
                values.append(float(cell))
            except ValueError:
                values.append(float("nan"))
        while len(values) < 5:
            values.append(float("nan"))
        rows.append(
            {
                "power": values[0],
                "util": values[1],
                "mem": values[2],
                "temp": values[3],
                "clock": values[4],
            }
        )
    return rows


def pct(values: list[float], q: float) -> float:
    clean = [v for v in values if v == v]
    if not clean:
        return float("nan")
    return sorted(clean)[min(len(clean) - 1, int(q * len(clean)))]


def stat(label: str, values: list[float], unit: str = "", scale: float = 1.0) -> None:
    clean = [v for v in values if v == v]
    if not clean:
        print(f"   {label:16} not reported")
        return
    print(
        f"   {label:16} mean {statistics.mean(clean) * scale:7.1f}   "
        f"p95 {pct(clean, 0.95) * scale:7.1f}   max {max(clean) * scale:7.1f}  {unit}"
    )


def app_memory(apps: str) -> float | None:
    """Per-process MiB, or None when the driver does not report it."""
    total = 0.0
    seen = False
    for line in apps.splitlines():
        if "," not in line:
            continue
        cell = line.split(",")[1].strip().replace("MiB", "").strip()
        try:
            total += float(cell)
        except ValueError:
            # N/A, which is what WDDM reports. Not zero: unavailable is not empty.
            return None
        seen = True
    return total if seen else None


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        raise SystemExit("usage: summarize_power.py <csv> <name> [flags]")
    path = Path(argv[0])
    name = argv[1]
    apps = total = ""
    cpu_path = ""
    warmup = 0
    rest = argv[2:]
    for index, flag in enumerate(rest):
        nxt = rest[index + 1] if index + 1 < len(rest) else ""
        if flag == "--apps":
            apps = nxt
        elif flag == "--total":
            total = nxt
        elif flag == "--warmup":
            warmup = int(nxt or 0)
        elif flag == "--cpu":
            cpu_path = nxt

    rows = read_samples(path)
    if not rows:
        raise SystemExit(f"no usable samples in {path}")
    dropped = min(warmup, max(0, len(rows) - 1))
    rows = rows[dropped:]
    if not rows:
        raise SystemExit(f"{path}: warmup of {warmup}s left no samples")

    print(f"== {name}")
    print(
        f"   samples          {len(rows)} kept, {dropped} dropped as warmup"
        f"   (raw {len(read_samples(path))})"
    )
    stat("board power W", [r["power"] for r in rows])
    stat("gpu util %", [r["util"] for r in rows])
    stat("sm clock MHz", [r["clock"] for r in rows])
    stat("temperature C", [r["temp"] for r in rows])
    stat("card memory MiB", [r["mem"] for r in rows])

    busy = [r["util"] for r in rows if r["util"] == r["util"] and r["util"] > 0]
    if busy:
        print(
            f"   {'busy fraction':16} {len(busy) / len(rows) * 100:7.1f}% of samples,"
            f" {statistics.mean(busy):.0f}% mean utilisation while busy"
        )

    per_process = app_memory(apps)
    cpu = []
    if cpu_path and Path(cpu_path).exists():
        for line in Path(cpu_path).read_text(encoding="utf-8").splitlines():
            try:
                cpu.append(float(line.strip()))
            except ValueError:
                continue
    if cpu:
        stat("cpu busy %", cpu[dropped:], scale=1.0)
    else:
        print(f"   {'cpu busy %':16} not sampled (no counter or counter unavailable)")
    if per_process is None:
        print(
            f"   {'compute procs':16} not reported by this driver (WDDM answers N/A);"
            " only whole-card memory is available"
        )
    else:
        whole = None
        if "," in total:
            try:
                whole = float(total.split(",")[0].strip().replace("MiB", "").strip())
            except ValueError:
                whole = None
        rest_of_card = f", rest of card {whole - per_process:.0f} MiB" if whole else ""
        print(f"   {'compute procs':16} {per_process:7.0f} MiB{rest_of_card}")
    print(f"   trace            {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
