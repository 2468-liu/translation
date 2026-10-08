"""The four clips side by side, taken from the replay summaries the project prints.

perf_compare.py already computes the numbers that matter and prints them, and its
audio_end_to_paint pairs speech endings with caption paints on the wall clock --
reimplementing that here would create a second definition of the same metric, which
is how two files in this repository drifted apart before (collapse_caption_repeats).
So this reads the summaries the replays wrote and lays them out together. It also
reads the per-sentence pairs out of the archived replay JSON, so quality can be
read next to latency without opening four files.

Usage:
    python scripts/replay_summary_table.py v1-blog_3min v1-e_3min v1-er_3min v1-meeting_6min
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Each entry: label shown in the table, regex over the summary text.
SCALARS = {
    "asr p50": r"asr_ms\s+n=\d+\s+p50=\s*([0-9.]+)",
    "asr p90": r"asr_ms\s+n=\d+\s+p50=\s*[0-9.]+\s+p90=\s*([0-9.]+)",
    "asr>250": r"asr >250ms\s+(\d+/\d+ \(\d+%\))",
    "qwait p50": r"asr_qwait_ms\s+n=\d+\s+p50=\s*([0-9.]+)",
    "qwait p90": r"asr_qwait_ms\s+n=\d+\s+p50=\s*[0-9.]+\s+p90=\s*([0-9.]+)",
    "audio p50": r"asr_audio_ms\s+n=\d+\s+p50=\s*([0-9.]+)",
    "ttft p50": r"ttft_ms\s+n=\d+\s+p50=\s*([0-9.]+)",
    "ttft p90": r"ttft_ms\s+n=\d+\s+p50=\s*[0-9.]+\s+p90=\s*([0-9.]+)",
    "translate p50": r"translate_total_ms\s+n=\d+\s+p50=\s*([0-9.]+)",
    "end->paint p50": r"audio_end_to_paint\s+n=\d+\s+p50=\s*([0-9.]+)",
    "end>1s": r"audio_end >1000ms\s+(\d+/\d+ \(\d+%\))",
    "e2e p50": r"e2e_first_tr_to_paint\s+n=\d+\s+p50=\s*([0-9.]+)",
    "e2e p90": r"e2e_first_tr_to_paint\s+n=\d+\s+p50=\s*[0-9.]+\s+p90=\s*([0-9.]+)",
    "e2e>1s": r"e2e >1000ms\s+(\d+/\d+ \(\d+%\))",
    "coverage": r"coverage \(prefix match\)\s+(\d+/\d+ \(\d+%\))",
    "final sentences": r"sentences with a final translation\s+(\d+/\d+)",
    "asr_ratio p50": r"asr_ratio p50\s+([0-9.]+)",
    "hold p50": r"hold_wait_ms\s+n=\d+\s+p50=\s*([0-9.]+)",
    "hold>1500": r"hold >1500ms\s+(\d+/\d+)",
    "overwritten": r"sentences overwritten\s+(\d+)",
    "timeline cover": r"timeline:\s+(\d+)s covered of \d+s audio",
    "timeline total": r"timeline:\s+\d+s covered of (\d+)s audio",
}


def read_any(path: Path) -> str:
    """Read a replay capture whatever encoding PowerShell gave it.

    Tee-Object on PowerShell 5.1 writes UTF-16LE, so a capture read as UTF-8 comes
    back as NUL-interleaved text and every metric in it silently fails to match --
    which is exactly what happened here: the table printed all "-" for four runs
    whose numbers were sitting in the file.
    """
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    # No BOM but NULs in the first bytes: UTF-16LE without a marker.
    if b"\x00" in raw[:64]:
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8", errors="replace")


def parse_summary(name: str) -> dict | None:
    path = ROOT / "logs" / f"{name}.txt"
    if not path.exists():
        return None
    text = read_any(path)
    out: dict[str, str] = {}
    for label, pattern in SCALARS.items():
        match = re.search(pattern, text)
        out[label] = match.group(1).strip() if match else "-"
    return out


def sentence_pairs(name: str, limit: int) -> list[tuple[str, str]]:
    """Timeline text vs what the replay recorded for it, from the archived JSON."""
    stem = name.replace("v1-", "")
    candidates = sorted((ROOT / "logs").glob(f"blog_3min_test.{name}.json"))
    if not candidates:
        candidates = sorted((ROOT / "logs").glob(f"blog_3min_test.{stem}*.json"))
    if not candidates:
        return []
    data = json.loads(candidates[-1].read_text(encoding="utf-8"))
    pairs = []
    for row in (data.get("sentences") or [])[:limit]:
        pairs.append((str(row.get("source") or "")[:52],
                      str(row.get("translation") or "").replace("\n", " ")[:44]))
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--pairs", type=int, default=0, help="also print this many sentence pairs per clip")
    args = ap.parse_args()

    rows = [(name, parse_summary(name)) for name in args.runs]
    rows = [(n, r) for n, r in rows if r]
    if not rows:
        raise SystemExit("no replay summaries found in logs/")

    labels = list(SCALARS)
    width = max(len(l) for l in labels)
    header = f"{'metric':<{width}} " + " ".join(f"{n.replace('v1-', ''):>14}" for n, _ in rows)
    print(header)
    print("-" * len(header))
    for label in labels:
        print(f"{label:<{width}} " + " ".join(f"{r[label]:>14}" for _, r in rows))

    if args.pairs:
        for name, _ in rows:
            pairs = sentence_pairs(name, args.pairs)
            if not pairs:
                continue
            print(f"\n===== {name.replace('v1-', '')} =====")
            for src, dst in pairs:
                print(f"   {src!r:56} -> {dst!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
