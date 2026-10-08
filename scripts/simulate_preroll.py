"""Would a leading pre-roll deliver the clauses? Simulated, before any code.

The diagnosis says the utterance begins exactly at the frame the detector first
called speech, so every millisecond of detector latency is lost words. The obvious
fix is to extend the start backwards by a fixed amount. Whether that works, and how
much is enough, can be answered without touching production code: a pre-roll of N ms
is exactly equivalent to reporting each detected utterance as starting N ms earlier,
and the audio for those milliseconds is in the file.

So this sweeps N and reports how many of the planted clauses would be covered. It
cannot tell us whether a real implementation would cost anything elsewhere -- the
soft_cut and speech_ms coupling is a property of the splitter's counters, not of the
audio -- which is why the criteria pin those separately and are checked against the
real splitter afterwards.

Two failure shapes are worth separating before the sweep:
  N too small   the clause is still clipped, and it reads as "pre-roll does not help"
  N too large   clauses reach full but segment starts land inside the previous
                clause's tail, which merges things that should stay apart
The second is the one the sparse material cannot show, because it has no adjacent
clauses -- that is what the e_3min criteria are for.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from check_sparse_music import load, spans  # noqa: E402


def score(clauses: list[dict], got, preroll_s: float) -> dict:
    """How many planted clauses a pre-roll of preroll_s would cover.

    A pre-roll moves each utterance's start *earlier*, so a clause at [a, b] is
    covered once the utterance begins no later than a + preroll. The sign matters:
    testing g[0] <= a - preroll makes the requirement stricter as the pre-roll
    grows, which produced a confident-looking monotone decline that was this
    function's bug and not a property of pre-roll.
    """
    closed = [g for g in got if g[2]]
    full = partial = lost = 0
    for c in clauses:
        a, b = c["start"], c["end"]
        latest_start = a + preroll_s
        covered = [g for g in closed if g[0] <= latest_start + 1e-6 and g[1] >= b - 1e-6]
        some = [g for g in closed if g[0] < b and g[1] > latest_start]
        if covered:
            full += 1
        elif some:
            partial += 1
        else:
            lost += 1
    return {"full": full, "partial": partial, "lost": lost,
            "rate": full / len(clauses) * 100}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--wav", default="samples/sparse_music.wav")
    ap.add_argument("--plan", default="samples/sparse_music_plan.json")
    ap.add_argument("--preroll-ms", default="0,100,200,300,400,600")
    ap.add_argument("--gate-ms", type=int, default=250)
    args = ap.parse_args()

    pcm = load(ROOT / args.wav)
    plan = json.loads((ROOT / args.plan).read_text(encoding="utf-8"))
    clauses = plan["clauses"]
    got = spans(pcm, args.gate_ms, 0)

    print(f"  gate {args.gate_ms}ms   埋入 {len(clauses)} 句   "
          f"检出并关闭 {len([g for g in got if g[2]])} 段")
    print()
    print("  预卷    完整   部分   没到   完整送达率")
    print("  " + "-" * 46)
    for value in [int(v) for v in args.preroll_ms.split(",")]:
        s = score(clauses, got, value / 1000.0)
        bar = "#" * s["full"]
        print(f"  {value:>4}ms  {s['full']:>4}  {s['partial']:>5}  {s['lost']:>4}"
              f"   {s['rate']:>5.0f}%  {bar}")

    print()
    print("  起点会提前到哪（预卷 > 间隔时会落到上一句里）:")
    spacing = min(c["start"] - clauses[i - 1]["start"]
                  for i, c in enumerate(clauses) if i) if len(clauses) > 1 else 0
    print(f"    最小起点间隔 {spacing:.1f}s —— 预卷超过这个值，起点就落进上一句了")


if __name__ == "__main__":
    main()