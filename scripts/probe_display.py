r"""Break the display leg down, because it is the only axis audio wins.

Audio's package-side latency loses to whisper by more than a second on both
to_first and settled, and wins only on the display leg. That leg was reported as
a single number, which is the least useful way to report a thing that is made of
several: 836 ms could be one wait or five small ones, and the fix is different
either way.

So this attributes the display leg to the parts that are already logged:

    first packet  ->  the caption window received it      (network + poll)
                 ->  the window decided to draw it        (hold / defer)
                 ->  the pixel appeared                   (pump + draw)

The middle term is the one that matters. The window deliberately holds a
sentence on screen so a viewer can read it -- hold_ms() is 800 to 4200 ms by
length and pace -- and every packet that arrives during that hold is a defer.
So a route that produces more packets pays the hold more often, which is a cost
of the route and not of the display, and pooling the whole leg hides which of the
two it is.

Reads the same caption log perf_compare reads, trimmed with its first_block so a
run's margins cannot inflate the window. Only arms whose caption window covered
the clip are reported; the rest are named and skipped, because a window that
died partway reports a display leg that never happened.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from perf_compare import first_block, load, numbers, tally  # noqa: E402
from probe_silence import _resolve  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TIMING = ROOT / "samples" / "blog_3min_timing.json"
MIN_WINDOW_FRACTION = 0.90


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else float("nan")


def _pct(values: list[float], q: float) -> float:
    clean = [v for v in values if v == v]
    if not clean:
        return float("nan")
    return sorted(clean)[min(len(clean) - 1, int(q * len(clean)))]


def caption_rows(name: str) -> list[tuple[float, str, dict]]:
    path = _resolve("perf_caption", ".log", "perf_caption.log", name)
    if not path.exists():
        return []
    return first_block(load(path))


def replay_window(name: str) -> float:
    path = _resolve("blog_3min_test", ".json", "blog_3min_test.json", name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0.0
    positions = [e["audio_sec"] for e in data.get("events", []) if e.get("audio_sec") is not None]
    if not positions:
        return 0.0
    # The clip, not the first sample: the earliest event carrying a position is
    # where playback began, which is a fraction of a second in.
    return max(positions)


def report(name: str) -> dict | None:
    cap = caption_rows(name)
    if not cap:
        return {"name": name, "skipped": "no caption log"}
    clip = replay_window(name)
    lived = cap[-1][0] - cap[0][0]
    if clip > 0 and lived / clip < MIN_WINDOW_FRACTION:
        return {
            "name": name,
            "skipped": f"caption window lived {lived:.0f}s of {clip:.0f}s",
        }

    paints = numbers(cap, "caption.paint", "since_recv")
    holds = numbers(cap, "caption.hold", "wait")
    pumps = numbers(cap, "caption.text", "pump")
    held = [r for r in cap if r[1] == "caption.paint" and r[2].get("held") == "1"]

    # The display leg as probe_reference measures it starts at the first packet
    # leaving the server, not at its arrival here. So it splits into the transit
    # and the draw, and only reporting the draw would make the transit invisible.
    #
    # caption.udp carries net= in milliseconds for the packets that carried text,
    # measured by the receiver against the sender's stamp. Those are the packets
    # the display leg is made of, so their net is the transit term.
    nets = [
        float(r[2]["net"])
        for r in cap
        if r[1] == "caption.udp" and r[2].get("kind") == "text" and r[2].get("net", "") not in ("", "-1")
    ]

    # The other half of the question. The leg is measured as "the first draw at or
    # after the first packet", but the window does not draw on arrival while it is
    # holding the previous sentence -- the hold is deliberate, so a new sentence
    # waits for the old one to expire. When that happens the draw that finally
    # shows the new sentence is minutes of hold later, and it lands inside the
    # measured leg even though nothing about the display was slow.
    #
    # So for every draw, ask whether the window was inside a hold when its packet
    # arrived. caption.hold is emitted when the wait closes, with the wait length
    # in ms, so the holds that were open at a given moment can be reconstructed
    # from their end times.
    hold_spans = [
        (float(r[0]), float(r[0]) - float(r[2]["wait"]) / 1000.0)
        for r in cap
        if r[1] == "caption.hold" and r[2].get("wait")
    ]
    paint_walls = [(r[0], r[2]) for r in cap if r[1] == "caption.paint"]
    during_hold: list[float] = []
    outside_hold: list[float] = []
    for wall, fields in paint_walls:
        raw = fields.get("since_recv")
        if not raw:
            continue
        value = float(raw)
        recv_at = wall - value / 1000.0
        blocked = any(start <= recv_at <= end for end, start in hold_spans)
        (during_hold if blocked else outside_hold).append(value)

    return {
        "name": name,
        "paint_since_recv": paints,
        "hold_wait": holds,
        "text_pump": pumps,
        "udp_net": nets,
        "paint_after_hold": during_hold,
        "paint_not_after_hold": outside_hold,
        "held_paints": len(held),
        "text_actions": tally(cap, "caption.text", "action"),
        "paint_actions": tally(cap, "caption.paint", "action"),
        "window": lived,
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    labels: list[tuple[str, list[str]]] = []
    for spec in argv:
        label, _, names = spec.partition("=")
        labels.append((label.strip(), [n for n in names.split(",") if n]))

    header = (
        f"{'arm':20} {'n':>2} {'draw p50':>9} {'draw p90':>9} {'hold':>7} "
        f"{'waited':>7} {'drew':>6} {'blocked p50':>12} {'pump':>5}"
    )
    print(header)
    print("-" * len(header))
    for label, names in labels:
        pooled: dict[str, list[float]] = {
            "paint_since_recv": [],
            "hold_wait": [],
            "text_pump": [],
            "udp_net": [],
            "paint_after_hold": [],
            "paint_not_after_hold": [],
        }
        held = 0
        tally_sum: dict[str, int] = {}
        used = 0
        for name in names:
            got = report(name)
            if got is None or "skipped" in got:
                if got:
                    print(f"  {name}: {got['skipped']}")
                continue
            used += 1
            for key in pooled:
                pooled[key].extend(got[key])
            held += got["held_paints"]
            for key, count in got["text_actions"].items():
                tally_sum[key] = tally_sum.get(key, 0) + count
        if not used:
            print(f"{label:22}   0   (no usable arms)")
            continue
        print(
            f"{label:20} {used:2d} "
            f"{_median(pooled['paint_since_recv']):9.0f} "
            f"{_pct(pooled['paint_since_recv'], 0.90):9.0f} "
            f"{_median(pooled['hold_wait']):7.0f} "
            f"{len(pooled['paint_after_hold']):7d} "
            f"{len(pooled['paint_not_after_hold']):6d} "
            f"{_median(pooled['paint_after_hold']):12.0f} "
            f"{_median(pooled['text_pump']):5.0f}"
        )
    print(f"{'':20} {'':2} {tally_sum}")
    print()
    print("draw      = received -> pixels changed. This is the display leg proper.")
    print("hold      = how long a finished sentence stayed up before being replaced.")
    print("            A readability choice by the window, not a delay: it is doing")
    print("            this so a viewer can finish reading.")
    print("waited    = draws whose packet arrived while a hold was open, so the draw")
    print("            could not happen until that hold expired")
    print("drew      = draws that were free to happen on arrival")
    print("blocked   = median draw among the waited ones. If this is far above the")
    print("            overall median, the display leg is mostly hold expiry.")
    print("pump      = the window's 50ms poll, the floor under every draw")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))