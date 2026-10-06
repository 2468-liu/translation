"""One fixed set of latency metrics per recorded run, so two runs can be compared.

    python scripts/perf_compare.py <name> <server.perf.log> <caption.perf.log> [blog_3min_test.json]

A "run" is one 3-minute replay. Each log may hold several runs; only the first
contiguous block (no gap longer than 30s) is used, which is how a second replay
appended to the same file gets excluded.

The report answers the three questions behind the step-3 triggers:

1. does "first new translation -> on screen" exceed 1s often?  (`e2e`, `paint`)
2. is `asr` over 250ms often, and how much wall clock does it eat?  (`asr`)
3. how often does a translation job actually run?  (`ttft`, `defer`)

Every block is stamped with the code that produced it, because a metric with no
provenance is how ``run2`` ended up meaning "the run where the caption never
painted". Pass ``--code <label>`` to override the stamp for runs measured before
git was clean.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Inside one replay the client sends audio every 100ms, so there is never a
# long silence; two replays are always separated by a 17s restart pause.
GAP_S = 10.0
# A replay is ~185s and emits thousands of lines. Anything shorter is a restart
# artefact or a leftover, never a run worth measuring.
MIN_EVENTS = 200


def code_identity(override: str | None = None) -> str:
    """``<sha>`` or ``<sha>+dirty``, so a metrics block names its own source."""
    if override:
        return override
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if not sha:
        return "unknown"
    return f"{sha}+dirty" if dirty else sha


def load(path: Path) -> list[tuple[float, str, dict[str, str]]]:
    """Return ``[(wall, "src.tag", {field: value})]`` for every parseable line."""
    rows: list[tuple[float, str, dict[str, str]]] = []
    if not path or not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 2 or "." not in parts[1]:
            continue
        try:
            wall = float(parts[0])
        except ValueError:
            continue
        if wall < 1e9:
            # A torn line from the two-process single-file era: the first token
            # is a fragment such as "5.000", not an epoch.
            continue
        fields: dict[str, str] = {}
        for item in parts[2:]:
            if "=" in item:
                key, _, value = item.partition("=")
                fields[key] = value
        rows.append((wall, parts[1], fields))
    return rows


def first_block(rows, gap: float = GAP_S, min_events: int = MIN_EVENTS):
    """Trim to the first contiguous burst that is big enough to be a replay.

    Short blocks are not replays. A restart leaves an ASR warm-up, and a stray
    pytest run leaves a handful of lines, either of which would otherwise be
    picked as "the run" and produce confident nonsense.
    """
    if not rows:
        return rows
    start = 0
    while start < len(rows):
        end = start
        last = rows[start][0]
        while end < len(rows) and rows[end][0] - last <= gap:
            last = rows[end][0]
            end += 1
        if end - start >= min_events:
            return rows[start:end]
        start = end
    return rows[:0]


def numbers(rows, tag: str, field: str) -> list[float]:
    out = []
    for _wall, name, fields in rows:
        if name != tag:
            continue
        raw = fields.get(field)
        if raw is None:
            continue
        try:
            out.append(float(raw))
        except ValueError:
            pass
    return out


def pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = int(round((p / 100.0) * (len(ordered) - 1)))
    return ordered[min(len(ordered) - 1, max(0, index))]


def tally(rows, tag: str, field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for _wall, name, fields in rows:
        if name != tag:
            continue
        value = fields.get(field)
        if value is None:
            continue
        out[value] = out.get(value, 0) + 1
    return out


def fmt(value: float) -> str:
    if value != value:
        return "-"
    return f"{value:.0f}" if abs(value) >= 100 else f"{value:.1f}"


def series(name: str, values: list[float]) -> str:
    if not values:
        return f"{name:<26} n=0"
    return (
        f"{name:<26} n={len(values):<5} p50={fmt(pct(values, 50)):>6} "
        f"p90={fmt(pct(values, 90)):>6} p99={fmt(pct(values, 99)):>6} "
        f"max={fmt(max(values)):>6}"
    )


def signature(text: str) -> str:
    """Same digest the caption window puts in ``caption.paint``."""
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:12]


def first_translation_wall(events: list[dict], audio_sec: float) -> float | None:
    for event in events:
        if event.get("type") != "translation":
            continue
        if float(event.get("audio_sec") or 0.0) >= audio_sec:
            return float(event["wall"])
    return None


def main(argv: list[str]) -> int:
    override = None
    if "--code" in argv:
        at = argv.index("--code")
        if at + 1 >= len(argv):
            print("--code needs a label")
            return 2
        override = argv[at + 1]
        del argv[at : at + 2]
    if len(argv) < 3:
        print(__doc__)
        return 2
    name, server_path, caption_path = argv[0], Path(argv[1]), Path(argv[2])
    test_path = Path(argv[3]) if len(argv) > 3 else None

    server = first_block(load(server_path))
    caption = first_block(load(caption_path))
    print(f"== {name} ==  code {code_identity(override)}")
    if server:
        window = server[-1][0] - server[0][0]
        print(f"window {window:.1f}s  ({server[0][0]:.3f} .. {server[-1][0]:.3f})")
        if caption:
            print(f"caption window {caption[-1][0] - caption[0][0]:.1f}s")

    # --- pipeline -------------------------------------------------------
    asr = numbers(server, "pipeline.asr", "asr")
    asr_share = sum(asr) / 1000.0 / (server[-1][0] - server[0][0]) if server else 0.0
    print(series("asr_ms", asr))
    print(
        f"asr share of wall clock    {asr_share * 100:.1f}%  "
        f"(sum {sum(asr) / 1000.0:.1f}s / {server[-1][0] - server[0][0]:.1f}s)"
    )
    print(series("asr_qwait_ms", numbers(server, "pipeline.asr", "qwait")))
    print(series("asr_audio_ms", numbers(server, "pipeline.asr", "audio_ms")))
    print("asr by kind", tally(server, "pipeline.asr", "kind") or "-")
    over = len([v for v in asr if v > 250])
    print(f"asr >250ms                 {over}/{len(asr)} ({over / max(1, len(asr)) * 100:.0f}%)")
    print(series("ttft_ms", numbers(server, "pipeline.ttft", "ttft")))
    print(series("translate_total_ms", numbers(server, "pipeline.ttotal", "total")))
    print(series("enqueue_qlen", numbers(server, "pipeline.enqueue", "qlen")))
    for tag in ("pipeline.defer", "pipeline.drop", "pipeline.stale"):
        print(f"{tag:<26} {len([r for r in server if r[1] == tag])}")

    # --- caption window --------------------------------------------------
    print("caption.text actions", tally(caption, "caption.text", "action") or "-")
    print("caption.paint actions", tally(caption, "caption.paint", "action") or "-")
    paints = [r for r in caption if r[1] == "caption.paint"]
    print(f"caption.paint              {len(paints)}")
    print(series("paint_since_send_ms", numbers(caption, "caption.paint", "since_send")))
    print(series("paint_since_recv_ms", numbers(caption, "caption.paint", "since_recv")))
    print(series("caption_text_pump_ms", numbers(caption, "caption.text", "pump")))

    # --- how long a new sentence waits, and what that wait costs ---------
    # `caption.hold` closes when nothing is pending any more; `overwrite` marks a
    # sentence that was still waiting when another one arrived; `dwell` is how
    # long the line on screen survived before the next one replaced it.
    waits = numbers(caption, "caption.hold", "wait")
    print(series("hold_wait_ms", waits))
    print(f"hold >1500ms                 {len([v for v in waits if v > 1500])}/{len(waits)}")
    print("hold outcomes", tally(caption, "caption.hold", "action") or "-")
    over = numbers(caption, "caption.overwrite", "held")
    print(f"sentences overwritten        {len(over)}")
    print(series("overwrite_held_ms", over))
    dwell = numbers(caption, "caption.dwell", "gap")
    print(series("dwell_gap_ms", dwell))
    print(f"dwell <1000ms                {len([v for v in dwell if v < 1000])}/{len(dwell)}")

    # --- audio finished -> something new on screen -----------------------
    # `pipeline.enqueue kind=end` is the wall clock at which the VAD closed the
    # utterance, so this needs no probe of its own: both files are stamped with
    # time.time(). Unlike the metric below it counts every utterance, not only
    # the ones that line up with a reference sentence.
    if server and caption:
        paint_walls = sorted(r[0] for r in caption if r[1] == "caption.paint")
        ends = sorted(
            r[0]
            for r in server
            if r[1] == "pipeline.enqueue" and r[2].get("kind") == "end"
        )
        lags: list[float] = []

        for at in ends:
            slot = bisect.bisect_left(paint_walls, at)
            if slot >= len(paint_walls):
                continue
            lags.append((paint_walls[slot] - at) * 1000.0)
        print(series("audio_end_to_paint", lags))
        slow = len([v for v in lags if v > 1000.0])
        print(
            f"audio_end >1000ms          {slow}/{len(lags)} "
            f"({slow / max(1, len(lags)) * 100:.0f}%)"
        )

    # --- coverage: did each utterance's text ever reach the screen? --------
    # A fragment overwritten while waiting never appears, so the caption window
    # is the only place that can answer this. Match each painted line back to the
    # translation that produced it.
    if test_path and test_path.exists() and caption:
        payload = json.loads(test_path.read_text(encoding="utf-8"))
        events = payload.get("events") or []
        finals = [
            e
            for e in events
            if e.get("type") == "translation" and e.get("final") and str(e.get("text") or "")
        ]
        painted = {r[2].get("sig") for r in caption if r[1] == "caption.paint"}
        painted.discard("")
        painted.discard(None)
        if finals and painted:
            # A translation streams out character by character, so the line on
            # screen is usually a prefix of the final text rather than all of
            # it. Accept any prefix the window actually drew.
            hits = 0
            for event in finals:
                text = str(event.get("text")).strip()
                while text:
                    if signature(text) in painted:
                        hits += 1
                        break
                    text = text[:-1]
            print(
                f"coverage (prefix match) {hits}/{len(finals)} "
                f"({hits / len(finals) * 100:.0f}%)"
            )

    # --- end to end: first new translation of a sentence -> on screen ----
    if test_path and test_path.exists() and caption:
        payload = json.loads(test_path.read_text(encoding="utf-8"))
        events = payload.get("events") or []
        timing = json.loads(
            (ROOT / "samples" / "blog_3min_timing.json").read_text(encoding="utf-8")
        )
        paint_walls = sorted(r[0] for r in caption if r[1] == "caption.paint")
        lags: list[float] = []
        for row in timing:
            first = first_translation_wall(events, float(row["start"]))
            if first is None:
                continue
            later = [wall for wall in paint_walls if wall >= first]
            if not later:
                continue
            lags.append((later[0] - first) * 1000.0)
        print(series("e2e_first_tr_to_paint", lags))
        slow = len([v for v in lags if v > 1000.0])
        print(
            f"e2e >1000ms                {slow}/{len(lags)} "
            f"({slow / max(1, len(lags)) * 100:.0f}%)"
        )
        picked = payload.get("sentences") or []
        missing = len([s for s in picked if not s.get("translation")])
        print(f"sentences with a final translation {len(picked) - missing}/{len(picked)}")
        ratios = [s["asr_ratio"] for s in picked if s.get("asr_ratio") is not None]
        print(
            "asr_ratio p50 "
            f"{fmt(pct(ratios, 50))} (n={len(ratios)})" if ratios else "asr_ratio -"
        )
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
