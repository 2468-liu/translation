"""What the language confidences look like, before deciding what to do about them.

Per-segment language detection with language=None was being designed against an
assumption: that high-confidence segments are trustworthy enough to skip
re-checking, and that low-confidence ones are ambiguous enough that re-checking
would not help. Both are claims about a distribution nobody has looked at. So
this reads the distribution instead of assuming it.

Two things get measured per clip.

The confidence histogram, because the policy turns on whether it is bimodal. A
clean high mode means "do not re-check when confident" is safe. A spread with no
separation means a confidence threshold is picking an arbitrary point in a
continuum, and any threshold chosen now is tuning in the dark.

And, on the same clips, the ratio of partial calls to end calls. The pipeline
re-decodes the growing fragment every PARTIAL_INTERVAL_MS and only finalises at
end, so partials outnumber ends by however fast the caller polls. That ratio
decides how much wrong-language decoding a policy of "detect at end, hold for the
segment" would actually let through -- if it is five, the first few partials
after a boundary are all decoded in the previous segment's language.

Language detection cost is measured alongside, because re-detecting on every
partial would be the obvious way to avoid the boundary error and is almost
certainly too expensive to do.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import wave

# Buckets fixed here, before the numbers exist.
CONFIDENCE_BUCKETS = ((0.0, 0.3), (0.3, 0.6), (0.6, 0.9), (0.9, 1.01))


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        channels = w.getnchannels()
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    if channels == 2:
        raw = raw.reshape(-1, channels)[:, 0]
    samples = raw.astype(np.float32) / 32768.0
    if rate != 16000:
        idx = np.arange(0, len(samples), rate / 16000)
        samples = np.interp(idx, np.arange(len(samples)), samples)
    return samples


def bucket_of(p: float) -> str:
    for lo, hi in CONFIDENCE_BUCKETS:
        if lo <= p < hi:
            return f"{lo:.1f}-{hi:.2f}".replace("1.01", "1.0")
    return "?"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", default="samples/clip222_90s.wav,samples/er_3min.wav")
    ap.add_argument("--model", default="models/faster-whisper-small")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--partial-ms", type=int, default=250,
                    help="the pipeline's partial cadence, used to count partials per segment")
    ap.add_argument("--out", default="logs/lang_baseline.json")
    args = ap.parse_args()

    from faster_whisper import WhisperModel

    model = WhisperModel(args.model, device=args.device,
                         compute_type="float16", cpu_threads=4)

    report = {"partial_interval_ms": args.partial_ms, "clips": []}

    for raw in args.clips.split(","):
        path = Path(raw.strip())
        if not path.exists():
            print(f"  missing {path}")
            continue
        audio = read_wav(path)
        duration = len(audio) / 16000

        t0 = time.time()
        segments, info = model.transcribe(
            audio, language=None, beam_size=1, vad_filter=False,
            condition_on_previous_text=False, temperature=0.0,
            without_timestamps=False)
        elapsed = time.time() - t0
        detected = info.language
        detected_p = float(info.language_probability)

        rows = []
        for s in segments:
            text = s.text.strip()
            if not text:
                continue
            span = s.end - s.start
            rows.append({
                "start": round(s.start, 2), "end": round(s.end, 2),
                "seconds": round(span, 2), "text": text[:60],
                "avg_logprob": round(s.avg_logprob, 3),
                "no_speech_prob": round(s.no_speech_prob, 3),
            })

        # How many partials would this segment have produced at the pipeline's
        # cadence. This is arithmetic on the segment length, not a measurement of
        # the pipeline -- it says how much wrong-language decoding a
        # detect-at-end-only policy would permit, given how often the caller polls.
        per_segment = [max(0, int(r["seconds"] * 1000 / args.partial_ms) - 1) for r in rows]
        partials = sum(per_segment)

        confs = [info.language_probability]  # clip-level, below
        print(f"== {path.name}  {duration:.1f}s")
        print(f"   clip-level: language={detected!r} probability={detected_p:.3f}")
        # A list of (lang, prob) pairs in this version, not a dict. Printed as
        # whatever it actually is rather than assumed, since guessing here threw
        # on the first clip and cost the run.
        if info.all_language_probs:
            pairs = info.all_language_probs
            if isinstance(pairs, dict):
                pairs = list(pairs.items())
            top = sorted(pairs, key=lambda kv: -kv[1])[:5]
            print("   top: " + ", ".join(f"{k}={v:.2f}" for k, v in top))
        print(f"   segments {len(rows)}, total {sum(r['seconds'] for r in rows):.0f}s "
              f"({sum(r['seconds'] for r in rows) / duration * 100:.0f}% of clip)")
        if rows:
            lens = [r["seconds"] for r in rows]
            print(f"   segment length  median {statistics.median(lens):.1f}s  "
                  f"max {max(lens):.1f}s")
            print(f"   partials per segment at {args.partial_ms}ms: "
                  f"median {statistics.median(per_segment):.1f}  "
                  f"total {partials}  ratio to ends {partials / len(rows):.1f}x")
            logprobs = [r["avg_logprob"] for r in rows]
            # avg_logprob is a log, not a probability: it comes out around -0.2 to
            # -0.9, so every segment fell into the lowest bucket when the buckets
            # were applied to it directly, and all four read zero. Exponentiated
            # first, which is the per-token geometric mean probability.
            probs = [min(1.0, pow(2.718281828, r["avg_logprob"])) for r in rows]
            print(f"   avg_logprob     median {statistics.median(logprobs):.2f}  "
                  f"p10 {sorted(logprobs)[max(0, int(len(logprobs) * 0.1))]:.2f}")
            print(f"   exp(logprob)    median {statistics.median(probs):.3f}  "
                  f"p10 {sorted(probs)[max(0, int(len(probs) * 0.1))]:.3f}  "
                  f"min {min(probs):.3f}")
            hist = {}
            for p in probs:
                hist[bucket_of(p)] = hist.get(bucket_of(p), 0) + 1
            print("   confidence buckets (exp of avg_logprob):")
            for lo, hi in CONFIDENCE_BUCKETS:
                key = f"{lo:.1f}-{hi:.2f}".replace("1.01", "1.0")
                count = hist.get(key, 0)
                bar = "#" * count
                print(f"     {key:14} {count:3d}  {bar}")

        # Cost of detecting per call, since re-detecting on every partial is the
        # obvious way to avoid the boundary error.
        window = audio[: 16000 * 5]
        reps = 5
        d0 = time.time()
        for _ in range(reps):
            model.detect_language(window)
        per = (time.time() - d0) / reps * 1000
        print(f"   detect_language on 5s: {per:.1f}ms per call "
              f"({per / args.partial_ms * 100:.0f}% of the partial budget)")
        print()

        report["clips"].append({
            "clip": path.name, "seconds": round(duration, 1),
            "language": detected, "language_probability": detected_p,
            "segments": rows, "partials_total": partials,
            "transcribe_seconds": round(elapsed, 1),
            "detect_language_ms_5s": round(per, 1),
            "confidences": confs,
        })

    Path(args.out).write_text(__import__("json").dumps(report, indent=1,
                                                      ensure_ascii=False),
                              encoding="utf-8")
    print(f"written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())