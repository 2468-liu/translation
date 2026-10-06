"""Summarize the perf log into per-stage percentiles.

    python scripts/perf_report.py            # logs/perf.log + logs/perf_caption.log
    python scripts/perf_report.py some.log   # explicit files

Each line in a perf log is ``<wall> <src>.<tag> key=value ...`` (see
server/perf.py). The report answers one question per row: where does the
caption's time go?
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (src, tag, fields) — one row per field, in reading order.
STAGES = [
    ("pipeline", "enqueue", ("qlen", "audio_ms")),
    ("pipeline", "asr", ("qwait", "asr")),
    ("pipeline", "drop", ("qwait",)),
    ("pipeline", "ttft", ("ttft",)),
    ("pipeline", "ttotal", ("total", "pieces")),
    ("caption", "udp", ("net",)),
    ("caption", "text", ("net", "pump", "depth")),
    ("caption", "paint", ("since_send", "since_recv")),
]

COUNTS = ["pipeline.drop", "pipeline.defer", "pipeline.stale", "caption.text", "caption.paint"]

# Non-numeric fields, reported as a tally: {"caption.text": {"action=grow": 3, ...}}
TEXT_FIELDS = ("action",)


def parse(paths: list[Path]) -> tuple[dict[str, dict[str, list[float]]], dict[str, dict[str, int]]]:
    """Return (``{tag: {field: [numbers]}}``, ``{tag: {"key=value": n}}``)."""
    data: dict[str, dict[str, list[float]]] = {}
    tally: dict[str, dict[str, int]] = {}
    for path in paths:
        if not path.exists():
            print(f"missing: {path}", file=sys.stderr)
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) < 2 or "." not in parts[1]:
                continue
            tag = parts[1]
            bucket = data.setdefault(tag, {"_count": []})
            bucket["_count"].append(1.0)
            for item in parts[2:]:
                if "=" not in item:
                    continue
                key, _, value = item.partition("=")
                if key in TEXT_FIELDS:
                    counter = tally.setdefault(tag, {})
                    slot = f"{key}={value}"
                    counter[slot] = counter.get(slot, 0) + 1
                    continue
                try:
                    bucket.setdefault(key, []).append(float(value))
                except ValueError:
                    bucket.setdefault(key, [])
    return data, tally


def percentile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = round((p / 100.0) * (len(ordered) - 1))
    return ordered[min(len(ordered) - 1, max(0, int(index)))]


def fmt(value: float) -> str:
    if value != value:  # NaN
        return "-"
    return f"{value:.0f}" if abs(value) >= 100 else f"{value:.1f}"


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv] or [
        ROOT / "logs" / "perf.log",
        ROOT / "logs" / "perf_caption.log",
    ]
    data, tally = parse(paths)
    if not data:
        print("no perf lines found", file=sys.stderr)
        return 1

    print("stage.field                      n      p50      p90      p99      max")
    print("-" * 64)
    for src, tag, fields in STAGES:
        bucket = data.get(f"{src}.{tag}")
        if not bucket:
            continue
        for field in fields:
            values = bucket.get(field) or []
            if not values:
                continue
            name = f"{src}.{tag}/{field}"
            print(
                f"{name:<30} {len(values):>5} {fmt(percentile(values, 50)):>8} "
                f"{fmt(percentile(values, 90)):>8} {fmt(percentile(values, 99)):>8} "
                f"{fmt(max(values)):>8}"
            )

    print()
    for name in COUNTS:
        bucket = data.get(name)
        if bucket:
            print(f"{name}: {len(bucket.get('_count', []))} lines")
    for tag, counter in sorted(tally.items()):
        joined = " ".join(f"{slot}={n}" for slot, n in sorted(counter.items()))
        print(f"{tag}: {joined}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
