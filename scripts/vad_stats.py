"""VAD statistics from a run's perf log, same shape as the offline probe.

Reads the splitter's own `vad.end` lines. Those are emitted before the min-speech
gate, so a short one may or may not have been discarded downstream -- which is why
this reports the distribution and the gate's cut, not a count of survivors.
"""
import re
import statistics
import sys
from pathlib import Path

path = Path(sys.argv[1])
gates = [int(x) for x in (sys.argv[2] if len(sys.argv) > 2 else "250,500,1000,1500").split(",")]
text = path.read_text(encoding="utf-8", errors="replace")

ends = []
held = 0
waits = []
for line in text.splitlines():
    if "vad.end" in line:
        m = re.search(r"why=(\w+)", line)
        s = re.search(r"speech_ms=([0-9.]+)", line)
        if m and s:
            ends.append((m.group(1), float(s.group(1))))
    elif "vad.held" in line:
        held += 1
    elif "pipeline.asr" in line:
        m = re.search(r"qwait=([0-9.]+)", line)
        if m:
            waits.append(float(m.group(1)))

print(f"{path}")
print(f"vad.end 行数 {len(ends)}   vad.held 行数 {held}")
if not ends:
    raise SystemExit("no vad.end lines; not a usable run")
speech = sorted(s for _w, s in ends)
print(f"speech_ms 中位数 {statistics.median(speech)}   min {speech[0]}   max {speech[-1]}")
print("why: " + str({w: sum(1 for x, _ in ends if x == w) for w in sorted({x for x, _ in ends})}))
for g in gates:
    below = [s for s in speech if s < g]
    kept = [s for s in speech if s >= g]
    print(f"  <{g}ms: {len(below):>3}/{len(speech)} ({100 * len(below) / len(speech):.1f}%)"
          f"   保留 {len(kept)}  保留 p50 {statistics.median(kept) if kept else '-'}")
if waits:
    print(f"pipeline.asr qwait_ms 中位数 {statistics.median(waits)}  n={len(waits)}")
