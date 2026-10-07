"""The marginal cost of one ASR call, measured rather than inferred.

probe_call_cost.py had to subtract load time from whisper-cli's reported total,
because the CLI is a one-shot process and every run paid the same ~800ms model
load. That subtraction is an estimate: it assumes load and inference do not
overlap, that nothing else is paid per call, and that the model stays resident
between calls. All three are assumptions about a program that never runs that
way.

whisper-server does run that way. The model is loaded once and stays loaded, so
each request after the first is an actual marginal call. That makes the
discarded warmups meaningful for the first time -- in the CLI they were
discarding nothing, since every run was equally cold.

The same binary is used for both arms, separated only by -ng, so the device is
the only variable. Wall-clock around the request is the primary number because
it is what the pipeline would experience; the server's own reported timings are
printed alongside to check the two agree.

--audio-ctx is also swept here, on one fixed clip, because the CLI measurements
showed encode barely moving as ctx went from 77 to 660 and that needs a second
look in a resident process before it is believed.
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
import wave
from pathlib import Path

# ~50 encoder positions per second of audio plus a tenth. whisper's positions
# are 20ms apart.
POSITIONS_PER_SECOND = 50
CTX_HEADROOM = 1.1

READY_TIMEOUT_S = 180


def slice_wav(src: Path, seconds: float, dst: Path) -> tuple[float, float]:
    """Cut the loudest window of the requested length, not the first one.

    The first window of these samples is silence. Transcribing it returns an
    empty string, and an empty string decodes in no time at all, so the cost
    measured is the cost of doing nothing: 34ms at one window size and 2030ms at
    another, neither of which is a recognition cost. Both numbers were reported
    as real before this was noticed.

    Taking the highest-energy window guarantees speech is present. The return
    value carries the window's RMS so the caller can see it is not silence.
    """
    import numpy as np

    with wave.open(str(src), "rb") as w:
        rate = w.getframerate()
        channels = w.getnchannels()
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        total = len(raw) // channels
    if channels == 2:
        raw = raw.reshape(-1, channels)[:, 0]
    samples = raw.astype(np.float32) / 32768.0

    n = int(rate * seconds)
    if n >= len(samples):
        raise SystemExit(f"clip is shorter than {seconds}s: {len(samples) / rate:.1f}s")
    # Energy per candidate window, in 50ms hops so the cut can start anywhere.
    hop = rate // 20
    starts = np.arange(0, len(samples) - n, hop)
    energies = np.array([
        float(np.sqrt((samples[s:s + n] ** 2).mean())) for s in starts
    ])
    best = starts[int(np.argmax(energies))]
    window = samples[best:best + n]
    rms = float(np.sqrt((window ** 2).mean()))

    out = wave.open(str(dst), "wb")
    out.setnchannels(1)
    out.setsampwidth(2)
    out.setframerate(rate)
    out.writeframes(np.clip(window * 32768.0, -32768, 32767).astype("<i2").tobytes())
    out.close()
    return n / rate, rms


def np_frombuffer(buf: bytes, dtype: str):
    import numpy as np

    return np.frombuffer(buf, dtype=dtype)


# Kept only so the old helper name does not linger; slicing no longer uses it.
del np_frombuffer


def vram_used_mib() -> int | None:
    try:
        done = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20,
        )
        return int(done.stdout.strip().splitlines()[0])
    except Exception:
        return None


def start_server(exe: Path, model: Path, port: int, ctx: int, threads: int,
                 device: str, log: Path) -> subprocess.Popen:
    args = [
        str(exe), "-m", str(model),
        "--host", "127.0.0.1", "--port", str(port),
        "-ac", str(ctx), "-t", str(threads),
        "-l", "en", "-nt", "-nlp", "-sns",
    ]
    # whisper-server has no -ngl: layers are not selectable here, only -ng to leave
    # the card entirely and -dev to pick one. Passing -ngl, which whisper-cli
    # does accept, makes the server print its help and exit 0.
    args += ["-ng"] if device == "cpu" else ["-dev", "0"]
    handle = log.open("wb")
    proc = subprocess.Popen(args, stdout=handle, stderr=subprocess.STDOUT)
    deadline = time.time() + READY_TIMEOUT_S
    while time.time() < deadline:
        if proc.poll() is not None:
            # whisper-server answers an unrecognised flag by printing its help
            # and exiting 0. Reporting only the exit code made that look like a
            # clean start, and the actual reason -- the offending flag -- was in
            # the log. So the tail of the log comes with the failure.
            handle.flush()
            tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            # The diagnostic is at the top of the help dump, not the bottom, so
            # take the first line that names a problem rather than the last line.
            why = next((ln.strip() for ln in tail
                        if "error" in ln or "unknown" in ln or "invalid" in ln), None)
            hint = (why or (tail[-1] if tail else "(log empty)"))[:160]
            raise RuntimeError(
                f"server exited early (code {proc.returncode}); last log line: {hint}\n"
                f"  full log: {log}"
            )
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)
            return proc
        except urllib.error.HTTPError:
            # Any HTTP answer means the listener is up.
            return proc
        except Exception:
            time.sleep(1.0)
    proc.kill()
    raise RuntimeError("server did not become ready")


def post_clip(port: int, clip: Path) -> tuple[float, dict]:
    """One request, timed from just before send to just after the body is read."""
    boundary = f"----{uuid.uuid4().hex}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="a.wav"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode() + clip.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/inference",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = resp.read()
    elapsed = (time.perf_counter() - start) * 1000.0
    try:
        parsed = json.loads(payload)
    except Exception:
        parsed = {}
    return elapsed, parsed


def measure(label: str, exe: Path, model: Path, clip: Path, seconds: float,
            ctx: int, threads: int, device: str, port: int, repeats: int,
            discard: int, log_dir: Path) -> None:
    log = log_dir / f"server_{device}_{ctx}.log"
    before = vram_used_mib()
    proc = start_server(exe, model, port, ctx, threads, device, log)
    try:
        time.sleep(2.0)  # let the first-touch allocations land before sampling VRAM
        during = vram_used_mib()
        wall, enc, dec = [], [], []
        texts = []
        for _ in range(repeats):
            ms, parsed = post_clip(port, clip)
            wall.append(ms)
            timings = parsed.get("timings", {}) if parsed else {}
            if timings:
                enc.append(float(timings.get("encode_ms", 0) or 0))
                dec.append(float(timings.get("decode_ms", 0) or 0))
            segs = parsed.get("text", "") if parsed else ""
            texts.append(segs if isinstance(segs, str)
                         else "".join(s.get("text", "") for s in segs))
        kept = wall[discard:]
        # The text matters as much as the time. A window padded past the audio
        # gives the decoder silence to hallucinate over, and hallucination is
        # many tokens, so a wide window can cost more by producing words than by
        # encoding more positions. Reporting the time without the text would make
        # that indistinguishable from a real encoding cost.
        uniq = sorted(set(t.strip() for t in texts))
        print(f"   distinct outputs {len(uniq)} across {len(texts)} calls; "
              f"median chars {statistics.median([len(t.strip()) for t in texts]):.0f}")
        for t in uniq[:3]:
            print(f"     | {t[:90]!r}")
        # Void the measurement rather than report it. A recogniser that returns
        # nothing has not been exercised, so its speed says nothing about what it
        # would cost on speech, and a number that looks like a result is worse
        # than no number at all.
        if not any(t.strip() for t in texts):
            print("   VOID: every call returned empty text, so no recognition "
                  "happened; timings above measure nothing and are not a cost.")
            return
        print(f"== {label}  {seconds:.1f}s audio  audio_ctx={ctx}  {device}  "
              f"threads={threads}  n={len(kept)} (discarded {discard} warmup)")
        print(f"   wall per call   p50 {statistics.median(kept):7.1f}ms  "
              f"min {min(kept):7.1f}ms  max {max(kept):7.1f}ms")
        if enc and statistics.median(enc) > 0:
            print(f"   server encode   p50 {statistics.median(enc):7.1f}ms")
            print(f"   server decode   p50 {statistics.median(dec):7.1f}ms")
        vram = f"{before} -> {during} MiB" if before is not None and during is not None else "unknown"
        print(f"   vram            {vram}")
        marg = statistics.median(kept)
        verdict = "fits" if marg < 250 else f"{marg / 250:.1f}x over"
        print(f"   vs 250ms partial {verdict} a 250ms cadence")
        print(f"   realtime factor {seconds / (marg / 1000.0):.2f}x")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.kill()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", default="samples/e_3min.wav")
    ap.add_argument("--model", default="models/whisper_small_fp16.cpp/ggml-small.bin")
    ap.add_argument("--exe", default="tools/whisper.cpp/build-cuda/bin/whisper-server.exe")
    ap.add_argument("--device", default="cuda", choices=("cpu", "cuda"))
    ap.add_argument("--seconds", type=float, default=1.4)
    ap.add_argument("--ctx", default="", help="comma separated; empty scales from length")
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--repeats", type=int, default=14)
    ap.add_argument("--discard", type=int, default=4)
    ap.add_argument("--port", type=int, default=8091)
    args = ap.parse_args()

    exe, model = Path(args.exe), Path(args.model)
    if not exe.exists():
        print(f"missing {exe}")
        return 1
    log_dir = Path("logs/asr_cost")
    log_dir.mkdir(parents=True, exist_ok=True)
    clip = log_dir / f"frag_{args.seconds:g}s.wav"
    actual, rms = slice_wav(Path(args.clip), args.seconds, clip)
    print(f"clip: loudest {actual:.2f}s window of {args.clip}, rms {rms:.4f}")

    if args.ctx:
        ctx_list = [int(c) for c in args.ctx.split(",")]
    else:
        ctx_list = [max(64, int(POSITIONS_PER_SECOND * actual * CTX_HEADROOM))]

    for ctx in ctx_list:
        measure(
            f"{args.device} {model.name}", exe, model, clip, actual,
            ctx, args.threads, args.device, args.port,
            args.repeats, args.discard, log_dir,
        )
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())