"""What decoding a fragment in the previous fragment's language actually produces.

The policy under test holds one language for a whole fragment and updates it
only when the fragment ends. That is cheap -- detecting on every partial costs
94ms, 38% of the partial budget -- and it has a known price: after a language
boundary, the first partials are decoded in the language of the fragment before
them. How bad that is has never been measured, and it is the only thing that
decides how many leading partials to hold back. A number of leading partials
chosen without it is a guess between showing garbage and throwing away a quarter
of the display opportunities.

So each fragment is decoded twice over the same audio: once in the language the
detector assigned to it, and once in the language of the fragment before it. The
first is what a correct pipeline would show. The second is what a
detect-at-end-only pipeline shows after a boundary, which is what is being
measured.

Similarity is character-level edit distance, because the outputs are different
languages and no word-level alignment means anything across them. Two degenerate
cases are counted separately rather than folded into a score: output that is
empty, and output that shares no characters at all with the correct decoding.
Those need different handling -- one is silence, the other is a wrong-language
hallucination -- and averaging them together would hide which is happening.

The boundaries are real in the clip rather than synthetic: the segments come
from one pass with language=None, so a boundary is any segment whose assigned
language differs from its predecessor's.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import wave


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


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance, iterative with one row. Fragments are short."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def similarity(correct: str, wrong: str) -> float:
    """1.0 identical, 0.0 nothing in common. Empty output scores 0.0, not 1.0."""
    if not correct:
        return 0.0
    if not wrong:
        return 0.0
    return 1.0 - edit_distance(correct, wrong) / max(len(correct), len(wrong))


def classify(sim: float, correct: str, wrong: str) -> str:
    """Buckets chosen so each needs a different response, not a different score."""
    if not wrong.strip():
        return "empty"
    if sim >= 0.8:
        return "essentially_correct"
    if sim >= 0.3:
        return "partly_readable"
    if sim > 0.0:
        return "garbled"
    return "no_characters_in_common"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", default="samples/clip222_90s.wav")
    ap.add_argument("--model", default="models/faster-whisper-small")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--out", default="logs/boundary_error.json")
    args = ap.parse_args()

    from faster_whisper import WhisperModel

    path = Path(args.clip)
    if not path.exists():
        sys.exit(f"missing {path}")
    audio = read_wav(path)
    print(f"clip {path.name}  {len(audio) / 16000:.1f}s")

    model = WhisperModel(args.model, device=args.device,
                         compute_type="float16", cpu_threads=4)

    # One pass with no forced language. This assigns each fragment a language and
    # defines the fragment boundaries; it is also the correct decoding for each.
    t0 = time.time()
    segments, info = model.transcribe(
        audio, language=None, beam_size=1, vad_filter=False,
        condition_on_previous_text=False, temperature=0.0, without_timestamps=False)
    frags = []
    for s in segments:
        text = s.text.strip()
        if not text:
            continue
        frags.append({"start": s.start, "end": s.end, "lang": info.language,
                      "correct": text, "logprob": round(s.avg_logprob, 3)})
    print(f"pass 1: {len(frags)} fragments in {time.time() - t0:.1f}s, "
          f"clip language {info.language!r} at {info.language_probability:.3f}")
    if len(frags) < 2:
        sys.exit("need at least two fragments to have a boundary")

    # Detect per fragment rather than reusing the clip-level answer, because a
    # clip that switches language cannot have one correct answer for all of it --
    # and the whole policy is about reacting to switches.
    print("\nper-fragment language detection:")
    for f in frags:
        window = audio[int(f["start"] * 16000):int(f["end"] * 16000)]
        if len(window) < 16000:
            f["lang"] = frags[max(0, frags.index(f) - 1)]["lang"] if frags.index(f) else info.language
            f["detect_ms"] = 0.0
            continue
        t = time.time()
        # detect_language returns (language, probability, all_probs) in
        # faster-whisper 1.2.1 -- three values, not two. Unpacking two raised on
        # the first fragment and lost the run.
        detected = model.detect_language(window)
        lang, prob = detected[0], detected[1]
        f["lang"] = lang
        f["lang_prob"] = round(float(prob), 3)
        f["detect_ms"] = round((time.time() - t) * 1000, 1)
        f["correct"] = f["correct"]  # pass 1 text stands as the reference
    langs = [f["lang"] for f in frags]
    print(f"  languages: {dict((l, langs.count(l)) for l in set(langs))}")

    # Pass 2: the same audio under the previous fragment's language, which is what
    # a pipeline holding the language across the boundary would decode.
    print("\ndecoding each fragment in its predecessor's language:")
    rows = []
    for i, f in enumerate(frags):
        prev = frags[i - 1]["lang"] if i else None
        boundary = prev is not None and prev != f["lang"]
        window = audio[int(f["start"] * 16000):int(f["end"] * 16000)]
        if prev is None or not len(window):
            continue
        t = time.time()
        segs, _ = model.transcribe(
            window, language=prev, beam_size=1, vad_filter=False,
            condition_on_previous_text=False, temperature=0.0, without_timestamps=False)
        wrong = "".join(s.text for s in segs).strip()
        ms = (time.time() - t) * 1000
        sim = similarity(f["correct"], wrong)
        kind = classify(sim, f["correct"], wrong)
        rows.append({
            "i": i, "boundary": boundary, "true_lang": f["lang"], "used_lang": prev,
            "seconds": round(f["end"] - f["start"], 2),
            "correct": f["correct"][:70], "wrong": wrong[:70],
            "similarity": round(sim, 3), "class": kind,
            "wrong_decode_ms": round(ms, 1),
            "true_logprob": f["logprob"],
        })

    print(f"\n{'':4} {'bnd':4} {'used':5} {'true':5} {'sim':>5} {'ms':>7}  class")
    for r in rows:
        print(f"{r['i']:4} {'BND' if r['boundary'] else '   ':4} {r['used_lang']:5} "
              f"{r['true_lang']:5} {r['similarity']:5.2f} {r['wrong_decode_ms']:7.0f}  "
              f"{r['class']:20} | {r['wrong'][:44]}")

    def report(label: str, subset: list) -> None:
        if not subset:
            print(f"\n{label}: none")
            return
        kinds = {}
        for r in subset:
            kinds[r["class"]] = kinds.get(r["class"], 0) + 1
        sims = [r["similarity"] for r in subset]
        print(f"\n{label}  n={len(subset)}")
        print(f"   similarity  median {statistics.median(sims):.2f}  "
              f"min {min(sims):.2f}  max {max(sims):.2f}")
        for k in ("essentially_correct", "partly_readable", "garbled",
                  "no_characters_in_common", "empty"):
            if kinds.get(k):
                print(f"   {k:22} {kinds[k]:3d}  ({kinds[k] / len(subset) * 100:.0f}%)  "
                      f"{'#' * kinds[k]}")

    print("\n=== how bad is decoding in the previous language ===")
    report("ALL fragments", rows)
    report("at a language boundary", [r for r in rows if r["boundary"]])
    report("not at a boundary (same language, control)", [r for r in rows if not r["boundary"]])

    Path(args.out).write_text(json.dumps(rows, indent=1, ensure_ascii=False),
                              encoding="utf-8")
    print(f"\nwritten to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())