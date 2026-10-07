"""One ASR call's cost, measured properly, on a length that matters.

The pipeline calls the recogniser every 250ms on a fragment that is typically
1 to 3 seconds. Every earlier number for whisper.cpp was taken the wrong way and
has been discarded: one cold call on 20 seconds of audio, with the CLI defaults
rather than the settings the pipeline actually uses. Cold includes buffer
allocation and, on an idle card, the clock has not come up yet; 20 seconds is
longer than any fragment the pipeline produces; and the CLI defaults to beam 5
and best-of 5 with temperature fallback, where the pipeline uses greedy.

So: greedy explicitly, no temperature fallback, no timestamps, warm process,
repetitions with the first few discarded, and audio_ctx scaled to the clip the
way it would have to be scaled in use -- about 50 positions per second of audio
plus a tenth, because a fixed value truncates the input and measures nothing but
the truncation.

Whisper's own stage timings are read rather than inferred, so the split between
encoding and decoding is measured and not obtained by subtracting one total from
another.
"""

from __future__ import annotations

import argparse
import re
import statistics
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

# ~50 positions per second of audio, plus a tenth. whisper's encoder positions
# are 20ms apart.
POSITIONS_PER_SECOND = 50
CTX_HEADROOM = 1.1

STAGE = re.compile(r"(load|mel|encode|decode|sample|batchd|prompt|total) time\s*=\s*([\d.]+)")

# Two things about how whisper-cli reports, both of which had to be corrected here
# after being got wrong once.
#
# "total time" is not the cost of the call. It is load + mel + encode + decode +
# batchd + sample, and on this machine the load alone is 656ms of the 800ms
# total. Charging every call that load makes a 144ms call look like an 800ms one.
#
# And whisper-cli is a one-shot process: there is no resident process to warm up,
# so discarding early repetitions discards nothing -- every run pays the same
# load. What is wanted here is the marginal cost of a call in a server that
# already holds the model, which is total minus load. That subtraction is an
# estimate, not a measurement, and is labelled as such wherever it is printed.


def slice_wav(src: Path, seconds: float, dst: Path) -> float:
    with wave.open(str(src), "rb") as w:
        rate = w.getframerate()
        channels = w.getnchannels()
        n = int(rate * seconds)
        raw = np.frombuffer(w.readframes(n), dtype="<i2")
    if channels == 2:
        raw = raw.reshape(-1, channels)[:, 0]
    out = wave.open(str(dst), "wb")
    out.setnchannels(1)
    out.setsampwidth(2)
    out.setframerate(rate)
    out.writeframes(raw.tobytes())
    out.close()
    return n / rate


def one_call(cli: Path, model: Path, clip: Path, ctx: int, threads: int, device: str) -> dict:
    args = [
        str(cli), "-m", str(model), "-f", str(clip),
        "-ac", str(ctx), "-t", str(threads), "-nt",
        # Greedy, and no temperature fallback: one deterministic pass.
        "-bs", "1", "-bo", "1", "-et", "1.0", "-sns",
    ]
    if device == "cpu":
        args += ["-ng"]
    if device == "cuda":
        args += ["-dev", "0"]
    done = subprocess.run(args, capture_output=True, text=True, timeout=300)
    text = done.stderr + done.stdout
    stages = {k: float(v) for k, v in STAGE.findall(text)}
    return {"exit": done.returncode, "stages": stages, "raw": text}


def measure(label: str, cli: Path, model: Path, clip: Path, seconds: float,
            threads: int, device: str, repeats: int, discard: int) -> None:
    ctx = max(64, int(POSITIONS_PER_SECOND * seconds * CTX_HEADROOM))
    calls = []
    for i in range(repeats):
        got = one_call(cli, model, clip, ctx, threads, device)
        if got["exit"] != 0:
            print(f"  {label}: call failed (exit {got['exit']})")
            print("  " + got["raw"].strip().splitlines()[-1][:120])
            return
        calls.append(got["stages"])
    # whisper-cli has no resident mode, so discarding runs removes no variance:
    # every run is equally cold. Kept anyway so the number of runs is on record,
    # but the spread it produces is the spread of repeated model loads, not of
    # repeated inference, and must not be read as inference jitter.
    kept = calls[discard:]
    print(f"== {label}  {seconds:.1f}s audio  audio_ctx={ctx}  {device}  n={len(kept)} "
          f"(of {len(calls)} cold runs; discarding does not warm anything)")
    for stage in ("load", "mel", "encode", "decode", "batchd", "sample", "total"):
        values = [c[stage] for c in kept if stage in c]
        if not values:
            continue
        print(
            f"   {stage:8} p50 {statistics.median(values):7.1f}ms  "
            f"min {min(values):7.1f}ms  max {max(values):7.1f}ms"
        )
    enc = statistics.median([c["encode"] for c in kept if "encode" in c])
    load = statistics.median([c["load"] for c in kept if "load" in c])
    tot = statistics.median([c["total"] for c in kept if "total" in c])
    marg = tot - load
    print(f"   load share of total    {load / tot * 100:.0f}%   "
          f"({load:.0f}ms paid once per process, not per call)")
    print(f"   marginal (total-load)  {marg:.1f}ms   ESTIMATE, not measured")
    print(f"   encode share of margin {enc / marg * 100:.0f}%")
    print(f"   realtime factor        {seconds / (marg / 1000.0):.2f}x  (marginal)")
    verdict = "fits" if marg < 250 else f"{marg / 250:.1f}x over"
    print(f"   vs 250ms partial       {verdict} a 250ms cadence")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", default="samples/e_3min.wav")
    ap.add_argument("--model", default="models/whisper_small_fp16.cpp/ggml-small.bin")
    ap.add_argument("--cpu-cli", default="tools/whisper.cpp/build-cpu/bin/Release/whisper-cli.exe")
    ap.add_argument("--cuda-cli", default="tools/whisper.cpp/build-cuda/bin/whisper-cli.exe")
    ap.add_argument("--device", default="cpu", choices=("cpu", "cuda"))
    ap.add_argument("--seconds", default="1.4", help="comma separated")
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--repeats", type=int, default=8)
    ap.add_argument("--discard", type=int, default=3)
    args = ap.parse_args()

    clip_src = Path(args.clip)
    cli = Path(args.cpu_cli if args.device == "cpu" else args.cuda_cli)
    model = Path(args.model)
    if not cli.exists():
        print(f"missing {cli}")
        return 1
    tmp = Path("logs/asr_cost")
    tmp.mkdir(parents=True, exist_ok=True)

    for raw_seconds in args.seconds.split(","):
        seconds = float(raw_seconds)
        clip = tmp / f"frag_{seconds:g}s.wav"
        actual = slice_wav(clip_src, seconds, clip)
        measure(
            f"{args.device} {model.name} {actual:.1f}s",
            cli, model, clip, actual, args.threads, args.device,
            args.repeats, args.discard,
        )
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())