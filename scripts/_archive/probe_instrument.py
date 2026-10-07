"""What this machine's power reading can actually resolve.

This is an instrument characterisation, not a result about any system under
test. Nothing here says whether the recogniser costs anything, or whether
anything is cheaper than anything else. It says how small a difference the
reading can distinguish from noise, which decides whether such questions are
answerable here at all.

The distinction matters because of how the last round went. Four baselines read
9.62, 9.84, 10.01 and 10.28 W, and forty consecutive samples produced only five
distinct values. A difference of 0.27 W was reported as a measured null. It was
not a null; it was below what the reading could resolve, and the two look
identical in a results table. Anything produced by this file must not be cited
as evidence about a component.

Four things are measured, and the second decides whether more sampling could ever
help.

Sensor update rate, by sampling at 10 Hz and timing how far apart the values
change. NVML's power.draw is a cached value on many drivers, refreshing every
few seconds; if the interval between changes clusters at five to ten seconds
then forty samples contain about five independent ones, and repeating a
three-minute arm a hundred times buys nothing that waiting a hundred minutes
would not. If the intervals are short and even, the noise is real and averaging
works.

Sigma, from the instantaneous power.draw only. power.draw.average is a rolling
mean whose successive samples are strongly correlated, so a sigma computed from
it is systematically too small and an effective sample size derived from that
sigma is too large -- which would turn "too noisy to tell" into "comfortably
enough samples". It is recorded here only to cross-check against the
instantaneous value.

Effective sample size, computed from the autocorrelation rather than assumed
equal to the count:
    ESS = n / (1 + 2 * sum of autocorrelations up to the first non-positive lag)
and the minimum detectable difference from it, at 95% on two arms:
    MDD(t) = 1.96 * sigma * sqrt(2 / ESS(t))

And the end-to-end repeatability, from two identical arms. Sigma covers sampling
noise alone. An A/A difference covers window selection, warmup, thermal drift and
sampling correlation together, which is the error that actually matters when a
result is read, and the two can differ by a lot.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import sys
import time
from pathlib import Path

# Pre-registered before the run. Not to be revised afterwards.
DECISION_RELEVANT_EFFECT_W = 1.0   # below this, nothing downstream changes
CONTINUE_MDD_W = 0.5              # half of it: if we cannot clear this, do not run
AA_LIMIT_W = 0.5                  # above this, every smaller difference is INDETERMINATE

FIELDS = ("power.draw", "power.draw.average", "clocks.sm", "clocks.mem",
          "utilization.gpu", "temperature.gpu", "memory.used")


def sample() -> dict | None:
    query = ",".join(FIELDS)
    try:
        done = subprocess.run(
            ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15)
    except Exception:
        return None
    lines = done.stdout.strip().splitlines()
    if not lines:
        return None
    out = {}
    for name, raw in zip(FIELDS, (p.strip() for p in lines[0].split(","))):
        try:
            out[name] = float(raw)
        except ValueError:
            return None
    return out


def burst(seconds: int, hz: int) -> list[dict]:
    """Fast sampling, to time how often the reading actually changes."""
    out = []
    step = 1.0 / hz
    end = time.time() + seconds
    while time.time() < end:
        s = sample()
        if s:
            out.append(s)
        target = len(out) * step
        drift = target - (time.time() - (end - seconds))
        if drift > 0:
            time.sleep(drift)
    return out


def acf(values: list[float], max_lag: int) -> list[float]:
    """Autocorrelation of the demeaned series, normalised to lag 0 = 1."""
    n = len(values)
    if n < 3:
        return []
    mean = statistics.mean(values)
    dev = [v - mean for v in values]
    denom = sum(d * d for d in dev)
    if denom == 0:
        return []
    return [sum(dev[i] * dev[i + k] for i in range(n - k)) / denom
            for k in range(max_lag + 1)]


def ess_and_mdd(values: list[float]) -> dict:
    """Effective sample size from the autocorrelation, and the detectable floor.

    The autocorrelation sum stops at the first non-positive lag: continuing past
    it would add oscillations that are not evidence of remaining dependence, and
    would understate the effective sample size.
    """
    rho = acf(values, max_lag=20)
    total = 0.0
    used = 0
    for k in range(1, len(rho)):
        if rho[k] <= 0:
            break
        total += rho[k]
        used = k
    n = len(values)
    inflation = max(1.0, 1.0 + 2.0 * total)
    ess = n / inflation
    sigma = statistics.stdev(values)
    mdd = 1.96 * sigma * math.sqrt(2.0 / ess) if ess > 0 else float("inf")
    return {"n": n, "sigma": round(sigma, 4), "acf_sum": round(total, 3),
            "lags_used": used, "inflation": round(inflation, 3),
            "ess": round(ess, 1), "mdd_95": round(mdd, 3),
            "acf": [round(r, 3) for r in rho[:11]]}


def change_intervals(values: list[float]) -> list[float]:
    """Seconds between successive changes, paired with when they happened."""
    stamps = []
    last = None
    for i, v in enumerate(values):
        if last is None or v != last:
            if last is not None:
                stamps.append(i)
            last = v
    return [b - a for a, b in zip(stamps, stamps[1:])]


def steady(rows: list[dict], warmup_s: int) -> list[dict]:
    if len(rows) <= warmup_s:
        return []
    return rows[warmup_s:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--burst-seconds", type=int, default=30)
    ap.add_argument("--hz", type=int, default=10)
    ap.add_argument("--long-seconds", type=int, default=1800)
    ap.add_argument("--warmup", type=int, default=60)
    ap.add_argument("--aa-seconds", type=int, default=180)
    ap.add_argument("--exe", default="tools/whisper.cpp/build-cuda/bin/whisper-server.exe")
    ap.add_argument("--model", default="models/whisper_small_q5/ggml-small-q5_1.bin")
    ap.add_argument("--ctx", type=int, default=220)
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--cadence", type=int, default=250)
    ap.add_argument("--clip", default="logs/asr_cost/frag_1.4s.wav")
    ap.add_argument("--out", default="logs/instrument.json")
    args = ap.parse_args()

    report = {"note": "instrument characterisation, not a result about any system",
              "thresholds": {"decision_relevant_w": DECISION_RELEVANT_EFFECT_W,
                             "continue_mdd_w": CONTINUE_MDD_W,
                             "aa_limit_w": AA_LIMIT_W}}

    print("=== 1. sensor update rate (10 Hz burst) ===")
    print("    answers whether more sampling could ever help")
    b = burst(args.burst_seconds, args.hz)
    burst_power = [s["power.draw"] for s in b]
    intervals = change_intervals(burst_power)
    distinct = len(set(burst_power))
    print(f"   {len(b)} samples in {args.burst_seconds}s, {distinct} distinct values")
    if intervals:
        print(f"   {len(intervals)} changes; interval seconds "
              f"min {min(intervals) / args.hz:.2f} median "
              f"{statistics.median(intervals) / args.hz:.2f} max {max(intervals) / args.hz:.2f}")
        clustered = [i for i in intervals if i / args.hz >= 4]
        print(f"   intervals >= 4s: {len(clustered)}/{len(intervals)}")
        if len(clustered) > len(intervals) * 0.5:
            print("   VERDICT: update rate is slow. Repetition does not add independent\n"
                  "           samples; only a longer window would, and that is not viable.")
            report["update_rate"] = "slow"
        else:
            print("   VERDICT: changes are frequent enough that averaging can help")
            report["update_rate"] = "fast"
    else:
        print("   the reading never changed during the burst; nothing to characterise")
        report["update_rate"] = "static"
    report["burst"] = {"samples": len(b), "distinct": distinct,
                        "change_intervals_s": [round(i / args.hz, 2) for i in intervals[:40]]}

    print("\n=== 2. long idle series: sigma, ACF, ESS, MDD ===")
    print(f"    sampling at 1 Hz for {args.long_seconds}s")
    rows = []
    end = time.time() + args.long_seconds
    while time.time() < end:
        s = sample()
        if s:
            rows.append(s)
        time.sleep(1.0)
    st = steady(rows, args.warmup)
    if not st:
        print(f"   VOID: {len(rows)} samples cannot supply the {args.warmup}s discard")
        sys.exit(1)
    power = [s["power.draw"] for s in st]
    avg = [s["power.draw.average"] for s in st]
    clock = [s["clocks.sm"] for s in st]
    mem = [s["clocks.mem"] for s in st]
    util = [s["utilization.gpu"] for s in st]
    temp = [s["temperature.gpu"] for s in st]
    used = [s["memory.used"] for s in st]

    stats = ess_and_mdd(power)
    print(f"   steady samples {stats['n']}")
    print(f"   sigma (instantaneous) {stats['sigma']:.4f} W")
    # power.draw is a cached value on this driver: sampling at 1 Hz re-reads the
    # same number and counts it again. A positive control showed the instrument
    # responds at all -- it moved 6.54W under a flat-out load -- but that establishes
    # range, not resolution, and range is not resolution any more than a scale that
    # weighs a person resolves a coin. Whether 0.11W sits above the noise floor is a
    # separate question, answered by the dedup analysis below and nothing else.
    #
    # So sigma and the effective sample size are computed on the sequence of values
    # that actually changed. Counting repeats deflates sigma and inflates n, which
    # is exactly the direction that makes a noisy reading look decisive.
    changed, last = [], None
    for i, v in enumerate(power):
        if last is None or v != last:
            changed.append(i)
            last = v
    n_eff = len(changed)
    print(f"   raw samples {stats['n']}, distinct values {n_eff}, "
          f"update rate {n_eff / max(1e-9, (args.long_seconds - args.warmup) / 60):.1f}/min")
    if n_eff < 5:
        print(f"   VOID: {n_eff} distinct readings is too few to characterise")
        sys.exit(1)
    dedup = [power[i] for i in changed]
    sigma_dedup = statistics.stdev(dedup)
    stats_d = ess_and_mdd(dedup)
    gaps = [b - a for a, b in zip(changed, changed[1:])]
    print(f"   gap between changes (s): median {statistics.median(gaps)} "
          f"min {min(gaps)} max {max(gaps)}")
    print(f"   sigma on deduplicated series {sigma_dedup:.4f} W "
          f"(raw-series sigma {stats['sigma']:.4f} W)")
    stats["n_raw"] = stats["n"]
    stats["n_eff"] = n_eff
    stats["sigma_dedup"] = round(sigma_dedup, 4)
    stats["ess_dedup"] = stats_d["ess"]
    stats["mdd_dedup"] = stats_d["mdd_95"]
    print(f"   ESS on deduplicated series {stats_d['ess']:.1f} of {n_eff}, "
          f"MDD {stats_d['mdd_95']:.3f} W")
    print(f"   ACF lag0-10 {stats['acf']}")
    print(f"   lags used {stats['lags_used']}, sum {stats['acf_sum']}, "
          f"inflation {stats['inflation']}x")
    print(f"   ESS {stats['ess']:.1f} of {stats['n']} nominal")
    print(f"   MDD(95%, two arms) at this window: {stats['mdd_95']:.3f} W")
    print(f"   cross-check: sigma of the rolling average is "
          f"{statistics.stdev(avg):.4f} W, and it is NOT used for any conclusion")
    print(f"   core clock {min(clock):.0f}-{max(clock):.0f} MHz, "
          f"mem clock {min(mem):.0f}-{max(mem):.0f} MHz")
    print(f"   util {min(util):.0f}-{max(util):.0f}%, "
          f"vram {min(used):.0f}-{max(used):.0f} MiB")
    print(f"   temperature {temp[0]:.0f} -> {temp[-1]:.0f} C")

    per_minute = {}
    for label, minutes in (("3", 3), ("10", 10), ("30", 30)):
        window = power[:minutes * 60]
        # The label comes from what was actually collected, never from the argument.
        # A 70-second collection once printed "MDD at 30 min", which is a number
        # about 70 seconds wearing a 30-minute label -- the same class of defect as
        # a short window reported as a full one.
        actual_min = len(window) / 60.0
        if len(window) < 30:
            per_minute[label] = None
            continue
        stx = ess_and_mdd(window)
        stx["n_used"] = len(window)
        stx["duration_actual_min"] = round(actual_min, 2)
        per_minute[label] = stx
        print(f"   MDD at {label} min requested: {stx['mdd_95']:.3f} W   "
              f"(n_used {stx['n_used']}, actual {stx['duration_actual_min']:.1f} min, "
              f"sigma {stx['sigma']:.3f}, ESS {stx['ess']:.0f}/{stx['n']})")
    report["idle"] = {"stats": stats, "mdd_by_minutes": per_minute,
                      "clock_min": min(clock), "clock_max": max(clock),
                      "mem_clock_min": min(mem), "mem_clock_max": max(mem),
                      "temp_start": temp[0], "temp_end": temp[-1],
                      "util_max": max(util), "vram_max": max(used)}

    print("\n=== 3. A/A: the same arm twice, end to end ===")
    print("    this covers window choice, warmup and thermal drift, which sigma does not")
    sys.path.insert(0, "scripts")
    import probe_power_diff as pp

    clip = Path(args.clip)
    aa = []
    for run in (1, 2, 3):
        row = pp.measure_one(f"A/A run {run}", Path(args.exe), Path(args.model), clip,
                             args.aa_seconds, args.ctx, args.threads, "cuda",
                             8097, args.cadence, Path("logs/asr_cost"))
        if row.get("void"):
            print(f"   run {run}: VOID ({row['n']} steady samples)")
            aa.append(row)
            continue
        # Thermal state is compared before the power difference is, because two
        # runs at different starting temperatures are not a repeatability test.
        if aa and row["temp_start"] is not None and aa[0]["temp_start"] is not None:
            gap = abs(row["temp_start"] - aa[0]["temp_start"])
            if gap > 2.0:
                print(f"   run {run}: starts at {row['temp_start']:.0f} C against "
                      f"{aa[0]['temp_start']:.0f} C on the first run, over the 2 C "
                      f"limit; treat the spread as thermal, not as repeatability")
        aa.append(row)
        print(f"   run {run}: {row['power_mean']:.2f} W mean, "
              f"{row['clock_min']:.0f}-{row['clock_max']:.0f} MHz, "
              f"{row['mem_clock_min']:.0f}-{row['mem_clock_max']:.0f} mem MHz, "
              f"temp {row.get('temp_start')} -> {row.get('temp_end')} C")
        print(f"          {row['requests']} requests, empty {row['empty_outputs']}, "
              f"call p50 {row['p50_call_ms']:.0f} ms, "
              f"achieved cadence {row['achieved_cadence_ms']:.0f} ms")

    report["aa"] = {}
    valid_aa = [r for r in aa if not r.get("void")]

    # Runs of identical configuration must also have done comparable work, or the
    # power difference between them measures something that varied underneath
    # rather than repeatability. The tolerance is 1% of the larger count, the rule
    # fixed in advance; exact equality is not the rule and rejected a run pair that
    # differed by 3 requests out of 519.
    for field, tol in (("requests", 0.01), ("empty_outputs", 0.0)):
        counts = [r.get(field) for r in valid_aa if r.get(field) is not None]
        if len(counts) < 2 or max(counts) == 0:
            continue
        worst = (max(counts) - min(counts)) / max(counts)
        if worst > tol:
            print(f"   A/A MISMATCH: {field} {counts} differ by {worst * 100:.1f}%, "
                  f"over the {tol * 100:.0f}% limit. The runs did not do the same work, "
                  f"so the power difference is not repeatability.")
            report["aa"]["work_mismatch"] = field
    if any(r.get("empty_outputs") for r in valid_aa):
        print("   A/A VOID: a run returned empty output, so no recognition happened")
        report["aa"]["void"] = "empty output in an A/A run"
    if len(valid_aa) < 2:
        print("   VOID: fewer than two usable runs, so no repeatability figure")
        aa_delta = None
        spread = None
    else:
        # The spread across three runs is the figure to read. A single pair gives
        # one draw from the distribution of run-to-run differences; with the noise
        # floor not yet known, one small difference proves nothing -- it is
        # consistent with a sigma much larger than the value observed.
        means = [r["power_mean"] for r in valid_aa]
        aa_delta = max(abs(a - b) for i, a in enumerate(means)
                       for b in means[i + 1:])
        spread = max(means) - min(means)
        print(f"   means {' '.join(f'{m:.2f}' for m in means)}   "
              f"largest pairwise |difference| {aa_delta:.3f} W   spread {spread:.3f} W")
    report["aa"] = {"runs": [{k: v for k, v in r.items()
                              if k in ("power_mean", "power_min", "power_max",
                                       "clock_min", "clock_max", "requests",
                                       "empty_outputs", "p50_call_ms", "n",
                                       "temp_start", "temp_end", "void")} for r in aa],
                    "max_pairwise_delta_w": round(aa_delta, 3) if aa_delta else None,
                    "spread_w": round(spread, 3) if spread else None}

    print("\n=== judgement, thresholds pre-registered before the run ===")
    slot30 = per_minute.get("30")
    mdd30 = slot30["mdd_95"] if slot30 else None
    if mdd30 is None:
        print("   INDETERMINATE: no 30-minute window was collected")
        verdict = "indeterminate: window too short"
    elif mdd30 > DECISION_RELEVANT_EFFECT_W:
        print(f"   MDD(30min) {mdd30:.2f} W exceeds the {DECISION_RELEVANT_EFFECT_W} W "
              f"decision-relevant effect, so no comparison at that size is answerable.")
        verdict = "closed: MDD above the decision-relevant effect"
    elif mdd30 <= CONTINUE_MDD_W:
        print(f"   MDD(30min) {mdd30:.2f} W at or under {CONTINUE_MDD_W} W; arms 2 and 3 "
              f"are worth running with thresholds at MDD x 1.5")
        verdict = "continue: MDD clears half the decision-relevant effect"
    else:
        print(f"   MDD(30min) {mdd30:.2f} W sits between {CONTINUE_MDD_W} and "
              f"{DECISION_RELEVANT_EFFECT_W} W: marginal. Default is not to run unless "
              f"power becomes a product constraint.")
        verdict = "marginal: between half and all of the decision-relevant effect"

    if aa_delta is None:
        print("   no repeatability figure; the A/A constraint is not established")
    elif aa_delta > AA_LIMIT_W:
        print(f"   A/A largest difference {aa_delta:.2f} W exceeds {AA_LIMIT_W} W: every "
              f"smaller difference is INDETERMINATE, permanently, for this pipeline.")
        report["permanent_aa_limit_w"] = aa_delta
    else:
        print(f"   A/A largest difference {aa_delta:.2f} W is within {AA_LIMIT_W} W. "
              f"This clears the pipeline of gross non-reproducibility; it does not "
              f"establish the noise floor, which comes from sigma and ESS alone.")

    report["verdict"] = verdict
    Path(args.out).write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"\n   verdict: {verdict}")
    print(f"   written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())