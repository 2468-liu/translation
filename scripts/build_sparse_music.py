"""Build samples/sparse_music.wav: sparse short phrases over a music bed.

Why this material exists. The reported symptom is not "recognition is bad", it is
"there is sound and nothing is translated", and it comes with two preconditions
the existing samples do not reproduce:

  speech is short -- a few words, one short clause
  and it sits under background music

None of the six samples qualifies. Measured: blog_3min is 93.4% speech with a
10.93s median segment, er_3min is 71.8% with a 0.00s median gap, e_3min is 36.7%.
Measuring a short-phrase-under-music fault on those measures something else.

So the conditions are constructed, from real audio rather than from nothing:

  speech  short English clauses lifted out of meeting_6min, which is two people
          talking -- "A little bit.", "Yeah, please.", "Hi, how are you?" Real
          conversational speech at real boundaries, so the phrases are not the
          clipped remains of a longer sentence.
  bed     the complement of er_3min's own timeline: 58.0s of that clip with every
          speech span removed, which is its score and its camera sounds. The gaps
          come from different points in the film, so the bed cuts where the film
          cuts, which is what a commentary video's background actually does.
  density one clause every 6 seconds. The point is scarcity, not continuity.
  level   the speech is placed at a stated number of dB over the bed. A material
          whose difficulty is a feeling cannot be compared against another one,
          and the whole reason to build it rather than reuse a sample is that its
          difficulty becomes a printed figure.

⚠️ It is synthetic, and that limits what it can settle. The speech is real but
re-positioned, the bed is real but re-cut, and the combination never occurred. It
is a probe for "does the pipeline lose short phrases under music", not a
substitute for the commentary video the symptom came from. Anything it shows has
to be confirmed on the real file before it becomes a decision.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_RATE = 16000


def load_mono(path: Path) -> np.ndarray:
    data, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if sr != SAMPLE_RATE:
        raise SystemExit(f"{path} is {sr}Hz, want {SAMPLE_RATE}")
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data


def segments(name: str) -> list[dict]:
    doc = json.loads((ROOT / "samples" / f"{name}_timing.json").read_text(encoding="utf-8"))
    return doc["segments"] if isinstance(doc, dict) else doc


def merge(spans: list[tuple[float, float]], gap: float = 0.05) -> list[list[float]]:
    out: list[list[float]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def complement(total: float, spans: list[list[float]]) -> list[tuple[float, float]]:
    gaps, cursor = [], 0.0
    for a, b in spans:
        if a > cursor:
            gaps.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < total:
        gaps.append((cursor, total))
    return gaps


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0


def build(out_path: Path, speech_name: str, bed_name: str, target_s: float,
          spacing_s: float, snr_db: float, min_s: float, max_s: float,
          lang: str, loop: bool) -> None:
    speech_src = load_mono(ROOT / "samples" / f"{speech_name}.wav")
    bed_src = load_mono(ROOT / "samples" / f"{bed_name}.wav")

    doc = json.loads((ROOT / "samples" / f"{bed_name}_timing.json").read_text(encoding="utf-8"))
    bed_total = float(doc.get("audio_total") or len(bed_src) / SAMPLE_RATE)
    gaps = complement(bed_total, merge([(s["start"], s["end"]) for s in segments(bed_name)]))
    gaps = [(a, b) for a, b in gaps if b - a >= 1.0]
    gaps.sort(key=lambda g: g[1] - g[0], reverse=True)
    if not gaps:
        raise SystemExit(f"{bed_name} has no non-speech stretch long enough to use")

    bed = np.concatenate([bed_src[int(a * SAMPLE_RATE):int(b * SAMPLE_RATE)] for a, b in gaps])
    if loop and len(bed) < int(target_s * SAMPLE_RATE):
        reps = int(np.ceil(target_s * SAMPLE_RATE / max(1, len(bed))))
        bed = np.tile(bed, reps)
    bed = bed[:int(target_s * SAMPLE_RATE)]
    if len(bed) < int(target_s * SAMPLE_RATE):
        raise SystemExit(
            f"bed only {len(bed) / SAMPLE_RATE:.1f}s after {reps if loop else 0} pass(es); "
            f"pass --loop to tile it")

    picks = [s for s in segments(speech_name)
             if min_s <= s["end"] - s["start"] <= max_s
             and (lang is None or s.get("lang") == lang)]
    if not picks:
        raise SystemExit(f"{speech_name} has no {lang or 'any'} clause in "
                         f"{min_s}~{max_s}s")
    picks.sort(key=lambda s: s["start"])

    room = int(spacing_s * SAMPLE_RATE)
    needed = room * len(picks)
    if needed > len(bed):
        # Informational, not a fault: the spacing is what makes the material
        # sparse, so the answer is to place fewer clauses rather than to crowd
        # them in. It goes to stdout because stderr reads as a failure here.
        keep = max(1, len(bed) // room)
        print(f"  spacing {spacing_s}s fits {keep} of {len(picks)} available clauses")
        picks = picks[:keep]

    bed_level = rms(bed)
    if bed_level <= 0:
        raise SystemExit("the bed is silent")
    target = bed_level * (10.0 ** (snr_db / 20.0))

    placed = []
    for i, s in enumerate(picks):
        a = int(s["start"] * SAMPLE_RATE)
        b = int(s["end"] * SAMPLE_RATE)
        clip = speech_src[a:b].copy()
        level = rms(clip)
        if level <= 0:
            continue
        clip *= target / level
        at = i * room + int(0.4 * SAMPLE_RATE)
        if at + len(clip) > len(bed):
            break
        # Short fades, so a clause does not begin or end on a discontinuity. Real
        # speech under a score has neither; a hard cut would read as an edit and
        # could move the VAD for reasons that have nothing to do with the test.
        fade = min(int(0.02 * SAMPLE_RATE), len(clip) // 4)
        if fade > 1:
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
            clip[:fade] *= ramp
            clip[-fade:] *= ramp[::-1]
        bed[at:at + len(clip)] += clip
        placed.append({"i": i, "start": round(at / SAMPLE_RATE, 2),
                       "end": round((at + len(clip)) / SAMPLE_RATE, 2),
                       "dur": round(len(clip) / SAMPLE_RATE, 2),
                       "text": s.get("text", "").strip()})

    peak = float(np.max(np.abs(bed)))
    if peak > 0.99:
        bed *= 0.99 / peak

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_path), bed.astype(np.float32), SAMPLE_RATE, subtype="PCM_16")

    # The bed on its own, at the same placement. An earlier attempt to recover it
    # later by rebuilding from the source clips produced identical RMS at fifteen
    # different offsets, which cannot be true of real audio -- so the analysis was
    # measuring its own reconstruction bug. Writing it out removes the need to
    # reconstruct anything, and a denoising experiment needs the bed and the mix
    # separately anyway.
    bed_path = out_path.with_name(out_path.stem + "_bed.wav")
    sf.write(str(bed_path), bed.astype(np.float32), SAMPLE_RATE, subtype="PCM_16")

    speech_only = np.zeros_like(bed)
    for i, s in enumerate(picks):
        a = int(s["start"] * SAMPLE_RATE)
        b = int(s["end"] * SAMPLE_RATE)
        clip = speech_src[a:b]
        if rms(clip) <= 0:
            continue
        at = i * room + int(0.4 * SAMPLE_RATE)
        if at + len(clip) > len(bed):
            break
        speech_only[at:at + len(clip)] = clip * (target / rms(clip))
    speech_path = out_path.with_name(out_path.stem + "_speech.wav")
    sf.write(str(speech_path), speech_only.astype(np.float32), SAMPLE_RATE, subtype="PCM_16")

    try:
        shown = out_path.resolve().relative_to(ROOT)
    except ValueError:
        shown = out_path
    print(f"  wrote {shown}  {len(bed) / SAMPLE_RATE:.1f}s")
    print(f"    speech from {speech_name}  {len(picks)} clauses of "
          f"{min_s}~{max_s}s, lang={lang or 'any'}")
    print(f"    bed from {bed_name} non-speech complement "
          f"({len(gaps)} stretches, {len(gaps) * 0 + sum(b - a for a, b in gaps):.1f}s before tiling)")
    print(f"    spacing {spacing_s}s   speech {snr_db:+.1f} dB over the bed")
    print(f"    bed rms {rms(bed_src[int(gaps[0][0] * SAMPLE_RATE):int(gaps[0][1] * SAMPLE_RATE)]):.4f} "
          f"(first stretch, before mixing)")
    speech_total = sum(p["dur"] for p in placed)
    print(f"    speech occupies {speech_total:.1f}s of {len(bed) / SAMPLE_RATE:.1f}s "
          f"({speech_total / (len(bed) / SAMPLE_RATE) * 100:.1f}%)")
    for p in placed[:8]:
        print(f"      {p['start']:>6.1f}s  {p['dur']:.2f}s  {p['text'][:44]}")
    if len(placed) > 8:
        print(f"      ... {len(placed) - 8} more")
    (out_path.parent / f"{out_path.stem}_plan.json").write_text(
        json.dumps({
            "speech_source": speech_name, "bed_source": bed_name,
            "target_s": target_s, "spacing_s": spacing_s, "snr_db": snr_db,
            "clauses": placed,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  plan: {out_path.stem}_plan.json")
    print(f"  also wrote {bed_path.name} (bed alone) and {speech_path.name} (clauses alone)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="samples/sparse_music.wav")
    ap.add_argument("--speech", default="meeting_6min")
    ap.add_argument("--bed", default="er_3min")
    ap.add_argument("--target-s", type=float, default=90.0)
    ap.add_argument("--spacing-s", type=float, default=6.0)
    ap.add_argument("--snr-db", type=float, default=10.0,
                    help="speech level relative to the bed")
    ap.add_argument("--min-s", type=float, default=0.7)
    ap.add_argument("--max-s", type=float, default=1.4)
    ap.add_argument("--lang", default="en")
    ap.add_argument("--loop", action="store_true",
                    help="tile the bed when it is shorter than --target-s")
    args = ap.parse_args()
    build(Path(args.out), args.speech, args.bed, args.target_s, args.spacing_s,
          args.snr_db, args.min_s, args.max_s, args.lang or None, args.loop)


if __name__ == "__main__":
    main()