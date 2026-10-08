"""Build samples/lecture_mix.wav: a movie commentary track. Film bed, narrator over it.

Why this material exists. The burst question has been open since the engine swap and
every sample in the repo is the wrong shape for it. blog_3min is TTS: 93.4% speech, a
10.93s median segment, gaps that are almost uniform. The felt symptom -- a line stalls,
appears, stalls again -- comes from a commentary video, and the mechanism proposed for
it is specific: a stretch with no narration, then a narrator who runs off three or four
clauses, so the VAD emits three or four ends at once and translation receives them
together.

That needs narration and non-narration in one file, and nothing here has both. So this
is mixed, not spliced:

  bed        e_3min.wav, 184.3s, RMS -13.9dB, 21 speech segments. Its own dialogue,
             music and camera sound, running the whole length with no cuts. This is
             what a commentary video's audio track actually is.
  narrator   clip222_90s.wav, 107.8s, five fragments totalling 86s placed over the
             bed. Measured: 33 segments, ja 53% / en 19% / ko 18% / zh 8% / it 2%, a
             2.0s median segment against blog_3min's 10.93s, and a 3.0s median
             silence. Short clauses after a pause is the shape that produces a burst.
  gap        18-22s of bed between fragments. This is the quiet stretch the mechanism
             needs, and it is the reason the two tracks are mixed rather than cut:
             cutting would put digital silence there, which is not what a commentary
             video has between narrations.
  level      the narrator sits at a stated number of dB over the bed and the mixture
             is then limited once. A material whose difficulty is a feeling cannot be
             compared against another one, so the level is a printed figure and not a
             taste.

⚠️ Two properties of this file limit what it settles, and both are stated here so the
run cannot be over-read.

  The film has its own dialogue. e_3min is 36.7% speech with a 2.5s median segment, so
  a film-only stretch is not a silent stretch: it still produces translation requests.
  That is realistic and it is also a confound for the burst question, because it
  means the arrival gaps during a "quiet" stretch are set by the film rather than by
  the narrator's absence. The per-fragment table below is printed for exactly this
  reason -- a gap has to be attributed before it is read as a narration burst.
  The language changes under the narration. clip222 is ja/en/ko/zh/it and the film is
  English, so per-call language detection flips across the file. If the arrival
  intervals cluster, check the detected language inside the cluster before calling it
  a burst.

What it can settle: whether translation requests arrive as a clump or as a metronome,
and how many times one clause is recognised before it settles. Both feed the choice of
partial_interval_ms, and neither can be read off blog_3min.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_RATE = 16000

# (narration start inside clip222, length, placement inside the mix). The placement is
# what makes this a commentary video rather than an interleave: long film stretches
# first, so the narrator arrives after a silence and leaves again.
FRAGMENTS = [
    (0.0, 16.0, 8.0),
    (16.0, 16.0, 42.0),
    (32.0, 16.0, 80.0),
    (48.0, 18.0, 116.0),
    (66.0, 20.0, 156.0),
]
NARRATOR_DB_OVER_BED = 6.0
TOTAL_SECONDS = 180.0
PEAK_DBFS = -1.0


def load_mono(path: Path) -> np.ndarray:
    data, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if sr != SAMPLE_RATE:
        raise SystemExit(f"{path} is {sr}Hz, want {SAMPLE_RATE}")
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data


def segments(name: str) -> list[dict]:
    doc = json.loads((ROOT / "samples" / f"{Path(name).stem}_timing.json").read_text(encoding="utf-8"))
    return doc["segments"] if isinstance(doc, dict) else doc


def spans_in(name: str, lo: float, hi: float) -> list[list[float]]:
    return [[s["start"], s["end"]] for s in segments(name) if s["end"] > lo and s["start"] < hi]


def merged(spans: list[list[float]], offset: float = 0.0, gap: float = 0.05) -> list[list[float]]:
    out: list[list[float]] = []
    for a, b in sorted(spans):
        a, b = a + offset, b + offset
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def db(x: float) -> float:
    return 20.0 * np.log10(max(float(x), 1e-9))


def describe(spans: list[list[float]]) -> dict:
    durs = sorted(b - a for a, b in spans)
    gaps = sorted(spans[i + 1][0] - spans[i][1] for i in range(len(spans) - 1))
    return {
        "spans": len(spans),
        "speech_seconds": round(sum(durs), 2),
        "len_s": {"p50": round(durs[len(durs) // 2], 2), "max": round(durs[-1], 2)} if durs else {},
        "gap_s": {"p50": round(gaps[len(gaps) // 2], 2), "max": round(gaps[-1], 2)} if gaps else {},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(ROOT / "samples" / "lecture_mix.wav"))
    ap.add_argument("--plan", default=str(ROOT / "samples" / "lecture_mix_plan.json"))
    args = ap.parse_args()

    bed = load_mono(ROOT / "samples" / "e_3min.wav")
    narr = load_mono(ROOT / "samples" / "clip222_90s.wav")
    total = int(TOTAL_SECONDS * SAMPLE_RATE)
    if bed.size < total:
        raise SystemExit(f"e_3min 只有 {bed.size / SAMPLE_RATE:.1f}s，要 {TOTAL_SECONDS}s")

    mix = np.zeros(total, dtype=np.float32)
    mix[: bed.size] = bed[:total]

    bed_rms = float(np.sqrt(np.mean(bed[:total] ** 2)))
    # Headroom comes off the bed, not off the mix. Putting the stated offset on the
    # narrator alone drove the sum to +7.5dB and the limiter then crushed the whole
    # file by 8.5dB. The ratio the reader is shown is the same either way, so the
    # quieter file bought nothing except a smaller signal; attenuating the bed and
    # adding the narrator at unity keeps the offset and needs no limiter at all.
    bed_scale = 10.0 ** (-NARRATOR_DB_OVER_BED / 20.0)
    mix = mix * bed_scale

    plan_fragments = []
    narr_spans: list[list[float]] = []
    for i, (src, length, at) in enumerate(FRAGMENTS):
        a = int(src * SAMPLE_RATE)
        b = a + int(length * SAMPLE_RATE)
        if b > narr.size:
            raise SystemExit(f"clip222 只有 {narr.size / SAMPLE_RATE:.1f}s，片段 {i} 要到 {b / SAMPLE_RATE:.1f}s")
        m = int(at * SAMPLE_RATE)
        n = min(int(length * SAMPLE_RATE), total - m)
        piece = narr[a:b]
        piece_rms = db(float(np.sqrt(np.mean(piece ** 2))))
        mix[m: m + n] += piece[:n]
        inside = spans_in("clip222_90s.wav", src, src + length)
        # clip222's own timeline to the mix's: subtract where the fragment was cut
        # from before adding where it was laid down. Adding `at` to the raw span put
        # the last one at 242.7s inside a 180.0s file, which is how the timing
        # sidecar ended up claiming speech past the end of the audio.
        narr_spans.extend([[s - src + at, e - src + at] for s, e in inside])
        # What the narrator alone measures at, in the finished file, so the level is
        # a figure and not an intention.
        plan_fragments.append({
            "index": i,
            "narrator_span": [src, round(src + length, 2)],
            "mix_span": [at, round(at + length, 2)],
            "rms_dbfs": round(piece_rms, 1),
            "segments": len(inside),
            "speech_seconds": round(sum(e - s for s, e in inside), 2),
        })

    peak = float(np.abs(mix).max())
    ceiling = 10.0 ** (PEAK_DBFS / 20.0)
    limiter = ceiling / peak if peak > ceiling else 1.0
    mix = (mix * limiter).astype(np.float32)
    sf.write(args.out, mix, SAMPLE_RATE, subtype="PCM_16")

    # Measured off the built file's source timeline, not estimated from file size.
    film_spans = merged(spans_in("e_3min.wav", 0.0, TOTAL_SECONDS))
    narr_spans = merged(narr_spans)
    only_narr = [[a, b] for a, b in narr_spans if not any(x <= a < y for x, y in film_spans)]
    overlap = sum(1 for a, b in narr_spans if any(x <= a < y for x, y in film_spans))

    # Narration gaps: where nothing but the film is playing. These are the stretches
    # the burst mechanism needs, and they are listed so a later gap can be attributed
    # to the narrator rather than to the film's own dialogue.
    frag_spans = sorted((f["mix_span"] for f in plan_fragments), key=lambda s: s[0])
    film_only = [[0.0, round(frag_spans[0][0], 2)]]
    for prev, nxt in zip(frag_spans, frag_spans[1:]):
        film_only.append([round(prev[1], 2), round(nxt[0], 2)])
    film_only.append([round(frag_spans[-1][1], 2), round(TOTAL_SECONDS, 2)])

    plan = {
        "file": Path(args.out).name,
        "seconds": round(len(mix) / SAMPLE_RATE, 2),
        "bed": "e_3min.wav",
        "narrator": "clip222_90s.wav",
        "narrator_db_over_bed": NARRATOR_DB_OVER_BED,
        "measured": {
            "bed_rms_dbfs": round(db(bed_rms), 1),
            "peak_before_limit_dbfs": round(db(peak), 1),
            "limiter_gain": round(limiter, 4),
            "output_rms_dbfs": round(db(float(np.sqrt(np.mean(mix ** 2)))), 1),
            "film": describe(film_spans),
            "narrator_spans_over_film": len(narr_spans),
            "narrator_spans_no_film_underneath": len(only_narr),
            "narrator_spans_touching_film_speech": overlap,
            "film_only_stretches_s": [round(b - a, 1) for a, b in film_only],
        },
        "fragments": plan_fragments,
        "film_only_stretches": film_only,
    }
    Path(args.plan).write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")

    # The timing sidecar is not optional. replay.ps1 loads <sample>_timing.json to place
    # the zero points and refuses a clip whose timeline outruns the file -- "the zero
    # points would be measured against speech that does not exist". So the union of
    # film and narrator speech goes out as segments in the mix timeline, which is what
    # makes delivery scorable against this material at all.
    union = merged(film_spans + narr_spans)
    timing = {
        "audio_total": round(len(mix) / SAMPLE_RATE, 2),
        "covered_span": round(sum(b - a for a, b in union), 2),
        "segments": [
            {"start": round(a, 2), "end": round(b, 2), "lang": "auto", "src": "mixed",
             "p": 0.0, "text": "", "i": i}
            for i, (a, b) in enumerate(union)
        ],
    }
    timing_path = Path(args.plan).with_name(
        Path(args.plan).name.replace("_plan.json", "_timing.json"))
    timing_path.write_text(json.dumps(timing, indent=2, ensure_ascii=False), encoding="utf-8")

    m = plan["measured"]
    print(f"  写出 {args.out}  {plan['seconds']}s")
    for f in plan_fragments:
        print(f"    片段{f['index']}  讲解 {f['mix_span'][0]:6.1f}-{f['mix_span'][1]:6.1f}s"
              f"  取自 clip222 {f['narrator_span'][0]:5.1f}s 起 {f['length'] if 'length' in f else f['mix_span'][1] - f['mix_span'][0]:.0f}s"
              f"  {f['rms_dbfs']:6.1f}dB  段 {f['segments']:>3}  语音 {f['speech_seconds']:5.1f}s")
    print(f"    底噪轨 RMS {m['bed_rms_dbfs']}dB  混合后峰值 {m['peak_before_limit_dbfs']}dB"
          f"  限幅增益 {m['limiter_gain']}  输出 RMS {m['output_rms_dbfs']}dB")
    print(f"    影片自己的语音 {m['film']['spans']} 段 / {m['film']['speech_seconds']}s"
          f"  段长中位 {m['film']['len_s']['p50']}s")
    print(f"    讲解语音跨 {m['narrator_spans_over_film']} 段，其中 {m['narrator_spans_touching_film_speech']} 段下面有影片对白")
    print(f"    只有影片的段落（秒）: {m['film_only_stretches_s']}")


if __name__ == "__main__":
    main()