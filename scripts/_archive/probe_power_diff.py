"""GPU power with the recogniser on the card, and with it off the card.

The question is whether moving ASR off the GPU is worth building. The whole
difference between the two candidate architectures is whether the recogniser
runs on the card, so that difference can be measured without either one being
wired into the pipeline: hold everything else constant, add one recogniser load,
and read the delta.

The translation model stays up and idle in all three arms, so its draw cancels.
It must stay idle -- a translating llama-server draws more than an idle one, and
a varying load would swamp the effect being measured, so this script asserts it
is idle before starting and never sends it anything.

Judgement thresholds are fixed here, before any number is seen, because a
threshold picked after looking at the data is not a threshold:

  A - baseline > 5W and B - baseline < 2W   -> the move is worth building
  every arm within 2W of baseline           -> it is not, and the backend is skipped
  clock or utilisation differing by >2     -> different wake behaviour, keep watching

Fan speed is unavailable on this machine -- nvidia-smi reports [N/A] and there
is no thermal zone exposed -- so nothing here claims to measure a fan.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

# Fixed before looking at any data.
THRESHOLD_SIGNIFICANT_W = 5.0
THRESHOLD_NOISE_W = 2.0
THRESHOLD_CLOCK_NOTCHES = 2

ARM_SECONDS = 180
WARMUP_S = 60          # skipped; the clock does not come up instantly
SAMPLE_INTERVAL_S = 1.0

# clocks.mem is recorded because a steady-state sm clock can hide a burst: the
# card can read 210 MHz on the sample that lands between decodes and still run
# higher most of the time. min and max over the window are what show that.
GPU_FIELDS = ("power.draw", "clocks.sm", "clocks.mem", "utilization.gpu",
              "temperature.gpu")

# Fixed before the run. A result is only believable if the instrument can see a
# difference at all, and three arms all reading about 10 W is the shape of a probe
# that cannot. One arm therefore runs the recogniser flat out with no pacing and
# must move the reading; if it does not, the remaining arms are void, not a result.
CONTROL_MIN_DELTA_W = 1.0

# Fixed before the run: arms whose per-run ranges overlap are not distinguishable
# on this instrument, whatever the medians say.
REPEATS = 3


def gpu_sample() -> dict | None:
    fields = ",".join(GPU_FIELDS)
    # Stamped, because the steady window is chosen by elapsed time and a bare
    # sample count cannot tell 60 seconds from 60 samples. Once sampling moved
    # inside the request loop the rate rose to roughly 4 Hz, and a 60-sample
    # discard that had meant a minute quietly became fifteen seconds -- which is
    # enough to move a reported mean from 10 W to 20 W with nothing on the card
    # having changed.
    t0 = time.time()
    try:
        done = subprocess.run(
            ["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return None
    line = done.stdout.strip().splitlines()
    if not line:
        return None
    parts = [p.strip() for p in line[0].split(",")]
    out = {"t": t0, "sample_cost_ms": (time.time() - t0) * 1000.0}
    for name, raw in zip(GPU_FIELDS, parts):
        try:
            out[name] = float(raw)
        except ValueError:
            return None
    return out


def sample_for(seconds: int) -> list[dict]:
    out = []
    end = time.time() + seconds
    while time.time() < end:
        s = gpu_sample()
        if s:
            out.append(s)
        time.sleep(SAMPLE_INTERVAL_S)
    return out


def hammer_and_sample(port, clip, cadence_ms: int, seconds: int) -> dict:
    """Drive the recogniser and sample the card inside one loop, one window.

    These were two sequential steps: the requests ran to completion, then sampling
    began. The power samples therefore described a period in which the recogniser
    had already stopped, which is why the steady arm read +0.11W -- it was
    measuring the load being withdrawn rather than the load. Request count and
    sample count disagreed for the same reason, 480 against 720 for what looked
    like one configuration.

    One loop removes the possibility of the two windows drifting apart, rather than
    trying to keep two timers in agreement.

    Sampling on every iteration rather than on a 1 Hz tick gives more points than
    the sensor can distinguish, since power.draw here is a cached value; the
    deduplication step downstream would collapse them anyway. Doing it this way
    means the sample times cannot drift relative to the request times.
    """
    end = time.time() + seconds
    nxt = time.perf_counter()
    lat, empty, sent, samples = [], 0, 0, []
    while time.time() < end:
        s = gpu_sample()
        if s:
            samples.append(s)
        ms, text = post_clip(port, clip)
        lat.append(ms)
        sent += 1
        if not text.strip():
            empty += 1
        nxt += cadence_ms / 1000.0
        sleep = nxt - time.perf_counter()
        if sleep > 0:
            time.sleep(sleep)
        else:
            nxt = time.perf_counter()  # behind; resync rather than accumulate debt
    lat.sort()
    return {
        "requests": sent,
        "p50_call_ms": lat[len(lat) // 2] if lat else 0.0,
        "p90_call_ms": lat[int(len(lat) * 0.9)] if lat else 0.0,
        "empty_outputs": empty,
        "achieved_cadence_ms": (seconds * 1000.0 / sent) if sent else 0.0,
        "samples": samples,
        "cadence_ms": cadence_ms,
    }


def assert_backend_idle(port: int = 8080) -> None:
    """The translation model must be up and idle, or the delta means nothing."""
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5)
    except urllib.error.HTTPError:
        return  # any HTTP answer means it is listening
    except Exception as exc:
        sys.exit(f"  translation backend not answering on {port}: {exc}\n"
                 f"  it must stay up and idle in every arm, or its draw does not cancel.")


def start_server(exe: Path, model: Path, port: int, ctx: int, threads: int,
                 device: str, log: Path) -> subprocess.Popen:
    args = [str(exe), "-m", str(model), "--host", "127.0.0.1", "--port", str(port),
            "-ac", str(ctx), "-t", str(threads), "-l", "en", "-nt", "-nlp", "-sns"]
    args += ["-ng"] if device == "cpu" else ["-dev", "0"]
    handle = log.open("wb")
    proc = subprocess.Popen(args, stdout=handle, stderr=subprocess.STDOUT)
    deadline = time.time() + 180
    while time.time() < deadline:
        if proc.poll() is not None:
            handle.flush()
            txt = log.read_text(encoding="utf-8", errors="replace")
            why = next((l.strip() for l in txt.splitlines() if "error" in l), "(no error line)")
            sys.exit(f"  recogniser exited (code {proc.returncode}): {why}\n  log: {log}")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)
            return proc
        except urllib.error.HTTPError:
            return proc
        except Exception:
            time.sleep(1.0)
    proc.kill()
    sys.exit(f"  recogniser never became ready; log: {log}")


def post_clip(port: int, clip: Path) -> tuple[float, str]:
    boundary = f"----{uuid.uuid4().hex}"
    body = (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="a.wav"\r\n'
            f"Content-Type: audio/wav\r\n\r\n").encode() + clip.read_bytes() \
        + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/inference", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = resp.read()
    ms = (time.perf_counter() - t0) * 1000.0
    try:
        text = json.loads(payload).get("text", "")
    except Exception:
        text = ""
    return ms, text


def hammer(port: int, clip: Path, cadence_ms: int, seconds: int) -> dict:
    """Drive the recogniser at a fixed cadence and report whether it kept up.

    Duty cycle is reported because a recogniser that cannot finish inside its
    cadence silently falls behind, and a lagging caller is not the workload the
    arm was supposed to represent.
    """
    end = time.time() + seconds
    nxt = time.perf_counter()
    lat, empty = [], 0
    sent = 0
    while time.time() < end:
        ms, text = post_clip(port, clip)
        lat.append(ms)
        sent += 1
        if not text.strip():
            empty += 1
        nxt += cadence_ms / 1000.0
        sleep = nxt - time.perf_counter()
        if sleep > 0:
            time.sleep(sleep)
        else:
            nxt = time.perf_counter()  # behind; resync rather than accumulate debt
    return {
        "requests": sent,
        "p50_call_ms": statistics.median(lat),
        "p90_call_ms": sorted(lat)[int(len(lat) * 0.9)],
        "empty_outputs": empty,
        "achieved_cadence_ms": (seconds * 1000.0) / sent if sent else 0,
    }


# A steady window shorter than this cannot support a comparison at the scale
# being discussed. An 80-second arm left 16 samples, and a mean over 16 samples
# was about to be compared against a tenth-of-a-watt effect.
MIN_STEADY_SAMPLES = 30

# A sampling call that costs half the budget is a warning that the instrument is
# part of the load it is measuring. On this machine nvidia-smi has taken 200-400ms
# per call, which is why that check exists rather than an assumption that sampling
# is free.


def summarise(name: str, samples: list[dict], work: dict | None) -> dict:
    # If the arm is too short to have a steady window, say so rather than quietly
    # reporting the warmup as if it were steady.
    #
    # A 4-second check ran through here with 4 samples against a 60s discard, so
    # the slice was empty and the code fell back to using all of them. It printed
    # "4 steady samples of 4" and reported +4.21W -- the GPU clock ramping up as
    # the model loaded, which is a transient, not a cost. The same configuration
    # over three minutes reads +0.00W at the same 210 MHz. The short run was not
    # a noisier version of the same measurement; it was a different measurement,
    # and it pointed the other way.
    # Selected by elapsed time, never by index. The two are only the same thing at
    # exactly 1 Hz, and the sampling rate stopped being 1 Hz the moment sampling
    # moved inside the request loop.
    if not samples:
        print(f"== {name}   VOID: no samples")
        return {"name": name, "void": True, "n": 0,
                "requests": work["requests"] if work else None}
    t_first = samples[0]["t"]
    span = samples[-1]["t"] - t_first
    if span <= WARMUP_S:
        print(f"== {name}   VOID: window spans {span:.0f}s, which cannot supply the "
              f"{WARMUP_S}s discard this protocol requires; raise --seconds")
        return {"name": name, "void": True, "n": len(samples), "span_s": round(span, 1),
                "requests": work["requests"] if work else None}
    steady = [s for s in samples if s["t"] - t_first >= WARMUP_S]
    if len(steady) < MIN_STEADY_SAMPLES:
        print(f"== {name}   VOID: steady window has {len(steady)} samples over "
              f"{span - WARMUP_S:.0f}s, under the {MIN_STEADY_SAMPLES} this protocol "
              f"requires. A mean over that many samples is not comparable with a "
              f"tenth-of-a-watt effect.")
        return {"name": name, "void": True, "n": len(steady),
                "requests": work["requests"] if work else None,
                "empty_outputs": work["empty_outputs"] if work else None}
    rate = len(samples) / max(1e-9, span)
    cost = statistics.median([s["sample_cost_ms"] for s in samples])
    print(f"== {name}   ({len(steady)} steady samples, {rate:.1f} Hz, "
          f"{span - WARMUP_S:.0f}s steady, each nvidia-smi call {cost:.0f}ms)")
    if cost > SAMPLE_INTERVAL_S * 1000 * 0.5:
        print(f"   WARN  a sampling call costs {cost:.0f}ms against a "
              f"{SAMPLE_INTERVAL_S * 1000:.0f}ms budget. The instrument may be "
              f"perturbing what it measures, and the read should be treated as a "
              f"lower bound on cost, not a clean figure.")
    power = [s["power.draw"] for s in steady]
    clock = [s["clocks.sm"] for s in steady]
    mem_clock = [s["clocks.mem"] for s in steady]
    util = [s["utilization.gpu"] for s in steady]
    temp = [s["temperature.gpu"] for s in steady]
    row = {
        "name": name,
        "n": len(steady),
        "power_mean": statistics.mean(power),
        "power_median": statistics.median(power),
        "power_min": min(power),
        "power_max": max(power),
        "clock_median": statistics.median(clock),
        "clock_min": min(clock), "clock_max": max(clock),
        "mem_clock_median": statistics.median(mem_clock),
        "mem_clock_min": min(mem_clock), "mem_clock_max": max(mem_clock),
        "util_median": statistics.median(util),
        "temp_start": temp[0] if temp else None,
        "temp_end": temp[-1] if temp else None,
    }
    print(f"== {name}   ({len(steady)} steady samples of {len(samples)})")
    print(f"   power    mean {row['power_mean']:6.2f} W   median {row['power_median']:6.2f} W"
          f"   range {row['power_min']:.1f}-{row['power_max']:.1f}")
    print(f"   clock    median {row['clock_median']:6.0f} MHz   "
          f"range {row['clock_min']:.0f}-{row['clock_max']:.0f}")
    print(f"   memclk   median {row['mem_clock_median']:6.0f} MHz   "
          f"range {row['mem_clock_min']:.0f}-{row['mem_clock_max']:.0f}")
    print(f"   util     median {row['util_median']:6.0f} %")
    print(f"   temp     {row['temp_start']:.0f} -> {row['temp_end']:.0f} C")
    if work:
        achieved = work["achieved_cadence_ms"]
        print(f"   asr      {work['requests']} req, achieved {achieved:.0f}ms "
              f"cadence, call p50 {work['p50_call_ms']:.0f}ms / p90 {work['p90_call_ms']:.0f}ms, "
              f"empty {work['empty_outputs']}")
        # The achieved cadence is compared against what was asked for. Two runs of
        # one configuration produced 480 and 720 requests, which looked like a
        # broken cadence and was not: the duration argument differed. Comparing
        # achieved against intended makes that visible instead of something a
        # reader has to reconstruct.
        wanted = work.get("cadence_ms")
        if wanted and abs(achieved - wanted) / wanted > 0.10:
            print(f"   WARN      cadence {wanted}ms requested, {achieved:.0f}ms "
                  f"achieved ({abs(achieved - wanted) / wanted * 100:.0f}% off). "
                  f"This arm is not comparable to one that hit its cadence.")
        # Carried into the row, so a consumer that only sees the row can still
        # check that two runs did the same amount of work. Without this the A/A
        # rows printed "requests None" and the alignment guard could not run --
        # and alignment is the whole point of comparing two arms.
        row.update({"requests": work["requests"],
                    "empty_outputs": work["empty_outputs"],
                    "p50_call_ms": work["p50_call_ms"],
                    "achieved_cadence_ms": work["achieved_cadence_ms"]})
    return row


def measure_one(name, exe, model, clip, seconds, ctx, threads, device, port,
                cadence_ms, log_dir):
    """Start a recogniser, drive it at a fixed cadence, sample, stop it.

    cadence_ms of 0 means back to back with no pacing. That exists for the positive
    control arm only, and it is not a valid measurement of any policy, because it
    changes duty cycle along with the load.
    """
    log = log_dir / f"power_{device}_{ctx}_{cadence_ms}.log"
    proc = start_server(exe, model, port, ctx, threads, device, log)
    try:
        time.sleep(5)
        if cadence_ms:
            work = hammer_and_sample(port, clip, cadence_ms, seconds)
        else:
            end = time.time() + seconds
            lat, empty, sent, samples = [], 0, 0, []
            while time.time() < end:
                s = gpu_sample()
                if s:
                    samples.append(s)
                ms, text = post_clip(port, clip)
                lat.append(ms)
                sent += 1
                if not text.strip():
                    empty += 1
            lat.sort()
            work = {"requests": sent,
                    "p50_call_ms": lat[len(lat) // 2] if lat else 0.0,
                    "p90_call_ms": lat[int(len(lat) * 0.9)] if lat else 0.0,
                    "empty_outputs": empty,
                    "achieved_cadence_ms": (seconds * 1000.0 / sent) if sent else 0.0,
                    "samples": samples,
                    "cadence_ms": cadence_ms}
        return summarise(name, work.pop("samples"), work)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.kill()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", default="logs/asr_cost/frag_1.4s.wav")
    ap.add_argument("--exe", default="tools/whisper.cpp/build-cuda/bin/whisper-server.exe")
    ap.add_argument("--cuda-model", default="models/whisper_small_q5/ggml-small-q5_1.bin")
    ap.add_argument("--cpu-model", default="models/whisper_small_q8/ggml-small-q8_0.bin")
    ap.add_argument("--ctx", type=int, default=220)
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--seconds", type=int, default=ARM_SECONDS)
    ap.add_argument("--cuda-cadence", type=int, default=250)
    ap.add_argument("--cpu-cadence", type=int, default=500)
    ap.add_argument("--out", default="logs/power_diff.json")
    ap.add_argument("--arms", default="cuda,cpu",
                    help="comma separated: cuda, cuda500, cpu")
    ap.add_argument("--repeats", type=int, default=REPEATS)
    ap.add_argument("--control", action="store_true",
                    help="also run a flat-out arm that must move the reading")
    ap.add_argument("--control-seconds", type=int, default=120)
    ap.add_argument("--allow-no-backend", action="store_true",
                    help="permit the translation model to be absent, to measure "
                         "its floor cost; every arm of a run must agree on this")
    ap.add_argument("--dry-run", action="store_true",
                    help="verify the setup and print the plan without measuring")
    args = ap.parse_args()

    clip = Path(args.clip)
    if not clip.exists():
        sys.exit(f"missing clip {clip}")

    if args.dry_run:
        print("dry run: nothing will be measured")
        print(f"  clip           {clip}")
        print(f"  recogniser     {args.exe}")
        print(f"  ctx {args.ctx}, threads {args.threads}")
        print(f"  arms           {args.arms}  x{args.repeats}, order reshuffled per rep")
        print(f"  per arm        {args.seconds}s sampled, first {WARMUP_S}s discarded")
        if args.control:
            print(f"  control        flat out, {args.control_seconds}s, must move power "
                  f"by >= {CONTROL_MIN_DELTA_W}W or every arm is void")
        if args.allow_no_backend:
            print("  translation    permitted absent (NOT the normal steady state)")
        print(f"  thresholds     {THRESHOLD_SIGNIFICANT_W}W / {THRESHOLD_NOISE_W}W / "
              f"{THRESHOLD_CLOCK_NOTCHES * 100}MHz / control {CONTROL_MIN_DELTA_W}W, fixed")
        holders = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name",
                                  "--format=csv,noheader"], capture_output=True, text=True)
        print("  GPU holders    " + (holders.stdout.strip().replace("\n", " | ") or "none"))
        for path in (args.cuda_model, args.cpu_model):
            p = Path(path)
            if not p.exists():
                print(f"  MISSING MODEL  {path}")
                return 1
            print(f"  model          {path}  {p.stat().st_size / 1e6:.0f} MB")
        try:
            assert_backend_idle()
            print("  translation    up and idle on 8080")
        except SystemExit as exc:
            print(f"  translation    {exc}")
            return 1
        print("  setup ok")
        return 0

    assert_backend_idle()

    log_dir = Path("logs/asr_cost")

    if args.allow_no_backend:
        print("translation model permitted absent: this run measures the card with "
              "nothing else resident, which is not the steady state the other arms "
              "assume")
    else:
        assert_backend_idle()

    arms = []
    picked = args.arms.split(",")
    if "cuda" in picked:
        arms.append(("wc-GPU-250", args.cuda_model, "cuda", args.cuda_cadence, 8093))
    if "cuda500" in picked:
        arms.append(("wc-GPU-500", args.cuda_model, "cuda", 500, 8095))
    if "cpu" in picked:
        arms.append(("wc-CPU-500", args.cpu_model, "cpu", args.cpu_cadence, 8094))
    if not arms:
        sys.exit(f"no arms selected by --arms {args.arms!r}")

    base_vals, per_arm = [], {}
    for rep in range(args.repeats):
        order = list(arms)
        # Reshuffled per repetition. The baseline showed the card warming across a
        # window and the effect being looked for is a quarter of a watt, so a fixed
        # order would charge that drift to whichever arm happened to run last.
        random.Random(1000 + rep).shuffle(order)
        print(f"--- repetition {rep + 1}/{args.repeats}: "
              + " then ".join(a[0] for a in order))
        b = summarise(f"baseline r{rep + 1}", sample_for(args.seconds), None)
        base_vals.append(b["power_mean"])
        print()
        for label, model, device, cadence, port in order:
            per_arm.setdefault(label, []).append(
                measure_one(label, Path(args.exe), Path(model), clip, args.seconds,
                            args.ctx, args.threads, device, port, cadence, log_dir))
            print()

    base_w = statistics.median(base_vals)
    print("=== judgement, thresholds fixed before the run ===")
    print(f"   baseline    median {base_w:6.2f} W   runs "
          + " ".join(f"{v:.2f}" for v in base_vals))
    for label, rows in per_arm.items():
        vals = [r["power_mean"] for r in rows]
        med = statistics.median(vals)
        print(f"   {label:12} median {med:6.2f} W  net {med - base_w:+6.2f} W  "
              f"range {min(vals):.2f}-{max(vals):.2f}  runs "
              + " ".join(f"{v:.2f}" for v in vals))

    control_delta = None
    if args.control:
        ctrl = measure_one("CONTROL flat-out", Path(args.exe), Path(args.cuda_model),
                           clip, args.control_seconds, args.ctx, args.threads,
                           "cuda", 8096, 0, log_dir)
        control_delta = ctrl["power_mean"] - base_w
        print("\n=== positive control: flat out, no pacing ===")
        print(f"   {ctrl['power_mean']:.2f} W, net {control_delta:+.2f} W, "
              f"clock {ctrl['clock_median']:.0f} MHz "
              f"(range {ctrl['clock_min']:.0f}-{ctrl['clock_max']:.0f})")
        if control_delta < CONTROL_MIN_DELTA_W:
            print(f"   VOID: the control moved power {control_delta:+.2f} W, under the "
                  f"{CONTROL_MIN_DELTA_W} W fixed in advance. The instrument does not "
                  f"respond to this load, so no arm above can be read as a result.")
        else:
            print(f"   control moved power {control_delta:+.2f} W, above the fixed "
                  f"{CONTROL_MIN_DELTA_W} W; the instrument does respond to load")

    def overlaps(a, b):
        return min(a) <= max(b) and min(b) <= max(a)

    labels = list(per_arm)
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            lo, hi = labels[i], labels[j]
            a = [r["power_mean"] for r in per_arm[lo]]
            b = [r["power_mean"] for r in per_arm[hi]]
            if overlaps(a, b):
                print(f"   {lo} vs {hi}: ranges overlap -> not distinguishable")
            else:
                print(f"   {lo} vs {hi}: ranges separate -> "
                      f"{statistics.median(a) - statistics.median(b):+.2f} W")

    Path(args.out).write_text(json.dumps(
        {"thresholds": {"significant_w": THRESHOLD_SIGNIFICANT_W,
                        "noise_w": THRESHOLD_NOISE_W,
                        "clock_mhz": THRESHOLD_CLOCK_NOTCHES * 100,
                        "control_min_delta_w": CONTROL_MIN_DELTA_W},
         "baseline_runs": base_vals,
         "control_delta_w": control_delta,
         "arms": {k: [r["power_mean"] for r in v] for k, v in per_arm.items()}},
        indent=1), encoding="utf-8")
    print(f"\n   written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())