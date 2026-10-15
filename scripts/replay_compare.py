"""The four clips side by side, computed from the archived perf logs.

perf_compare.py reads logs/perf.log, which a long replay overwrites with whatever
was written most recently -- on the er_3min run that produced a 20.9s window for a
206s clip, and its coverage read 3/31. The archived perf.<run>.log is the whole
run, so this reads those instead and does the arithmetic here, which also means
the same numbers can be recomputed later without re-running anything.

Reported per clip, each from the splitter's and the pipeline's own probes:

  asr        p50/p90, and how many calls exceeded the 250ms partial cadence
  qwait      how long a fragment waited before the recogniser picked it up
  ttft       first token back from the translator
  scores     audio_end_to_paint and e2e_first_tr_to_paint as the caption window
             measured them (these come from the caption log, not the server one)
  sentences  how many timeline sentences produced any translation
"""
from __future__ import annotations

import argparse
import re
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def percentiles(values: list[float]) -> str:
    if not values:
        return "n=0"
    ordered = sorted(values)
    def at(q: float) -> float:
        return ordered[min(len(ordered) - 1, int(len(ordered) * q))]
    return (f"n={len(ordered):>4}  p50={statistics.median(ordered):>7.1f}  "
            f"p90={at(0.9):>7.1f}  max={ordered[-1]:>7.1f}")


def fields(pattern: str, text: str) -> list[dict]:
    out = []
    for match in re.finditer(pattern, text):
        out.append({k: float(v) for k, v in match.groupdict().items() if v is not None})
    return out


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def summarize(run: str) -> dict | None:
    server = ROOT / "logs" / f"perf.{run}.log"
    caption = ROOT / "logs" / f"perf_caption.{run}.log"
    if not server.exists():
        return None
    text = read(server)
    ctext = read(caption)

    # The server log lines look like:
    #   1791388961.2 pipeline.asr kind=partial qwait=12.3 ... asr=290.1 chars=8
    #   1791388962.5 pipeline.ttft ttft=83.3 chars=10 final=0 job=1
    #   1791388962.6 pipeline.ttotal total=131.1 ttft=83.3 ...
    # The field names repeat the event name, so the pattern must not look for the
    # name twice -- an earlier version did, matched nothing, and reported a ttft of
    # 0.0 for every run, which reads as "instant" rather than "unparsed".
    asr = [float(m) for m in re.findall(r"pipeline\.asr .*? asr=([0-9.]+)", text)]
    qwait = [float(m) for m in re.findall(r"pipeline\.asr .*? qwait=([0-9.]+)", text)]
    audio_ms = [float(m) for m in re.findall(r"pipeline\.asr .*? audio_ms=([0-9.]+)", text)]
    ttft = [float(m) for m in re.findall(r"pipeline\.ttft ttft=([0-9.]+)", text)]
    total = [float(m) for m in re.findall(r"pipeline\.ttotal total=([0-9.]+)", text)]
    drops = len(re.findall(r"pipeline\.drop", text))
    enqueues = len(re.findall(r"pipeline\.enqueue", text))

    # Caption-side latency, which is the number the user actually sees.
    end_to_paint = [float(m) for m in re.findall(r"caption\.paint .*? audio_end_to_paint=([0-9.]+)", ctext)]
    if not end_to_paint:
        end_to_paint = [float(m) for m in re.findall(r"audio_end_to_paint=([0-9.]+)", ctext)]
    e2e = [float(m) for m in re.findall(r"e2e_first_tr_to_paint=([0-9.]+)", ctext)]

    return {
        "run": run,
        "asr": asr, "qwait": qwait, "audio_ms": audio_ms, "ttft": ttft,
        "total": total, "drops": drops, "enqueues": enqueues,
        "end_to_paint": end_to_paint, "e2e": e2e,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+", help="replay names, e.g. v1-blog_3min v1-e_3min")
    args = ap.parse_args()

    rows = [r for r in (summarize(name) for name in args.runs) if r]
    if not rows:
        raise SystemExit("no archived perf logs matched")

    print(f"{'clip':<22} {'asr calls':>9} {'asr p50':>8} {'asr p90':>8} {'>250ms':>7} "
          f"{'qwait p50':>10} {'audio p50':>10} {'ttft p50':>9}")
    print("-" * 92)
    for r in rows:
        asr = r["asr"]
        over = sum(1 for a in asr if a > 250)
        share = f"{100 * over / len(asr):.0f}%" if asr else "-"
        p50 = statistics.median(asr) if asr else 0
        p90 = sorted(asr)[int(len(asr) * 0.9)] if asr else 0
        q = statistics.median(r["qwait"]) if r["qwait"] else 0
        au = statistics.median(r["audio_ms"]) if r["audio_ms"] else 0
        tf = statistics.median(r["ttft"]) if r["ttft"] else 0
        print(f"{r['run'].replace('v1-', ''):<22} {len(asr):>9} {p50:>8.0f} {p90:>8.0f} {share:>7} "
              f"{q:>10.1f} {au:>10.0f} {tf:>9.1f}")

    print()
    print(f"{'clip':<22} {'enqueue':>8} {'drop':>6} {'total p50':>10} "
          f"{'end->paint p50':>15} {'e2e p50':>9} {'e2e>1s':>7}")
    print("-" * 92)
    for r in rows:
        total = statistics.median(r["total"]) if r["total"] else 0
        e2p = statistics.median(r["end_to_paint"]) if r["end_to_paint"] else 0
        e2e = statistics.median(r["e2e"]) if r["e2e"] else 0
        over = sum(1 for v in r["e2e"] if v > 1000)
        share = f"{100 * over / len(r['e2e']):.0f}%" if r["e2e"] else "-"
        print(f"{r['run'].replace('v1-', ''):<22} {r['enqueues']:>8} {r['drops']:>6} {total:>10.0f} "
              f"{e2p:>15.0f} {e2e:>9.0f} {share:>7}")

    print("\nfull distributions")
    for r in rows:
        print(f"\n{r['run']}")
        if r["asr"]:
            print(f"   asr_ms      {percentiles(r['asr'])}")
        if r["qwait"]:
            print(f"   qwait_ms    {percentiles(r['qwait'])}")
        if r["audio_ms"]:
            print(f"   audio_ms    {percentiles(r['audio_ms'])}")
        if r["ttft"]:
            print(f"   ttft_ms     {percentiles(r['ttft'])}")
        if r["end_to_paint"]:
            print(f"   end->paint  {percentiles(r['end_to_paint'])}")
        if r["e2e"]:
            print(f"   e2e         {percentiles(r['e2e'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
