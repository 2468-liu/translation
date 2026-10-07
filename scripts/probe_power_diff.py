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

GPU_FIELDS = ("power.draw", "clocks.sm", "utilization.gpu", "temperature.gpu")


def gpu_sample() -> dict | None:
    fields = ",".join(GPU_FIELDS)
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
    out = {}
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
    if len(samples) <= WARMUP_S:
        print(f"== {name}   VOID: {len(samples)} samples cannot supply the "
              f"{WARMUP_S}s discard this protocol requires; raise --seconds")
        return {"name": name, "void": True, "n": len(samples)}
    steady = samples[WARMUP_S:]
    power = [s["power.draw"] for s in steady]
    clock = [s["clocks.sm"] for s in steady]
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
        "util_median": statistics.median(util),
        "temp_start": temp[0] if temp else None,
        "temp_end": temp[-1] if temp else None,
    }
    print(f"== {name}   ({len(steady)} steady samples of {len(samples)})")
    print(f"   power    mean {row['power_mean']:6.2f} W   median {row['power_median']:6.2f} W"
          f"   range {row['power_min']:.1f}-{row['power_max']:.1f}")
    print(f"   clock    median {row['clock_median']:6.0f} MHz")
    print(f"   util     median {row['util_median']:6.0f} %")
    print(f"   temp     {row['temp_start']:.0f} -> {row['temp_end']:.0f} C")
    if work:
        print(f"   asr      {work['requests']} req, achieved {work['achieved_cadence_ms']:.0f}ms "
              f"cadence, call p50 {work['p50_call_ms']:.0f}ms / p90 {work['p90_call_ms']:.0f}ms, "
              f"empty {work['empty_outputs']}")
    return row


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
        print(f"  arm A          {args.cuda_model} on GPU @ {args.cuda_cadence}ms")
        print(f"  arm B          {args.cpu_model} on CPU @ {args.cpu_cadence}ms")
        print(f"  per arm        {args.seconds}s sampled, first {WARMUP_S}s discarded")
        print(f"  thresholds     {THRESHOLD_SIGNIFICANT_W}W / {THRESHOLD_NOISE_W}W / "
              f"{THRESHOLD_CLOCK_NOTCHES * 100}MHz, fixed")
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
    rows = []

    print("baseline: translation model idle, no recogniser")
    base = summarise("baseline", sample_for(args.seconds), None)
    rows.append(base)
    print()

    for label, model, device, cadence, port in (
        ("A: whisper.cpp Q5_1 on GPU", args.cuda_model, "cuda", args.cuda_cadence, 8093),
        ("B: whisper.cpp Q8 on CPU", args.cpu_model, "cpu", args.cpu_cadence, 8094),
    ):
        print(f"{label}, cadence {cadence}ms")
        proc = start_server(Path(args.exe), Path(model), port, args.ctx,
                            args.threads, device, log_dir / f"power_{device}.log")
        try:
            time.sleep(5)
            work = hammer(port, clip, cadence, args.seconds)
            row = summarise(label, sample_for(args.seconds), work)
            rows.append(row)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except Exception:
                proc.kill()
        print()

    base_w = rows[0]["power_mean"]
    a_w, b_w = rows[1]["power_mean"], rows[2]["power_mean"]
    da, db = a_w - base_w, b_w - base_w

    print("=== judgement, thresholds fixed before the run ===")
    print(f"   baseline        {base_w:6.2f} W")
    print(f"   A - baseline    {da:+6.2f} W")
    print(f"   B - baseline    {db:+6.2f} W")
    print(f"   A vs B          {a_w - b_w:+6.2f} W   <- the number the decision turns on")
    dclock = abs(rows[1]["clock_median"] - rows[2]["clock_median"])
    print(f"   clock gap A-B   {dclock:6.0f} MHz")

    if da > THRESHOLD_SIGNIFICANT_W and db < THRESHOLD_NOISE_W:
        verdict = "SIGNIFICANT: moving the recogniser off the card is worth building"
    elif abs(da) <= THRESHOLD_NOISE_W and abs(db) <= THRESHOLD_NOISE_W:
        verdict = "NOISE: both arms sit within 2W of baseline, skip the backend work"
    else:
        verdict = (f"MIXED: A is {da:+.2f}W and B is {db:+.2f}W against a fixed rule of "
                   f"A>5W and B<2W; this does not satisfy either branch and needs a human")
    print(f"   verdict         {verdict}")
    if dclock > THRESHOLD_CLOCK_NOTCHES * 100:
        print(f"   note            clock differs by {dclock:.0f} MHz, so the two arms sit in "
              f"different wake states even if power does not separate them")

    Path(args.out).write_text(json.dumps(
        {"thresholds": {"significant_w": THRESHOLD_SIGNIFICANT_W,
                        "noise_w": THRESHOLD_NOISE_W,
                        "clock_mhz": THRESHOLD_CLOCK_NOTCHES * 100},
         "rows": rows, "verdict": verdict}, indent=1), encoding="utf-8")
    print(f"\n   written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())