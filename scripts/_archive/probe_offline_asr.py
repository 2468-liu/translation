"""Offline recognition on a clip, by language block, with two models.

Answers the three questions the pipeline diagnostic cannot, because the
pipeline forces a language and only sees what its own segmenter produced:

  - what the stronger model thinks the language of each stretch is, and how
    confident it is, when nothing forces it
  - how much audio the same stretch costs each model, and how the cost scales
    with duration -- the number that decides whether a faster segmenter helps
    or just produces more, smaller calls
  - how many characters come out per second of speech, which is what the
    caption window's hold curve is calibrated against and which is meaningless
    if the recogniser is wrong about the language

Not a latency measurement. It is a single warm process doing sequential work, so
the numbers are cost-per-call, not the cost-per-call the pipeline sees under a
250ms cadence with a queue behind it.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
import wave
from pathlib import Path

import numpy as np


def load(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        channels = w.getnchannels()
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    if channels == 2:
        raw = raw.reshape(-1, channels).astype(np.float32).mean(axis=1)
    else:
        raw = raw.astype(np.float32)
    if rate != 16000:
        idx = np.arange(0, len(raw), rate / 16000.0)
        raw = np.interp(idx, np.arange(len(raw)), raw)
    return raw / 32768.0, 16000


def blocks(audio: np.ndarray, rate: int, seconds: int) -> list[np.ndarray]:
    step = rate * seconds
    return [audio[i : i + step] for i in range(0, len(audio), step) if len(audio[i : i + step]) > rate]


def run_ct2(audio: np.ndarray, rate: int, seconds: int, label: str) -> None:
    from faster_whisper import WhisperModel

    model = WhisperModel("models/faster-whisper-small", device="cuda", compute_type="float16", cpu_threads=4)
    print(f"== {label}: CT2 small fp16 ==")
    rows = []
    for i, chunk in enumerate(blocks(audio, rate, seconds)):
        t0 = time.time()
        segs, info = model.transcribe(
            chunk,
            beam_size=1,
            vad_filter=False,
            language=None,
            condition_on_previous_text=False,
            temperature=0.0,
            without_timestamps=True,
        )
        segs = list(segs)
        dt = time.time() - t0
        chars = sum(len(s.text.strip()) for s in segs)
        text = " ".join(s.text.strip() for s in segs)
        rows.append((i * seconds, info.language, info.language_probability, dt, chars, len(chunk) / rate, text))
        head = text[:96] if text.strip() else "(empty)"
        print(
            f"  {i * seconds:4d}s  {info.language}  p={info.language_probability:.2f}  "
            f"{dt * 1000:6.0f}ms  {chars:3d}ch  {head}"
        )
    _summarise(rows, label)


def _summarise(rows, label) -> None:
    if not rows:
        print(f"  {label}: no blocks")
        return
    langs: dict[str, int] = {}
    for _t, lang, _p, _dt, _c, _d, _x in rows:
        langs[lang] = langs.get(lang, 0) + 1
    probs = [p for _t, _l, p, *_ in rows]
    costs = [dt for _t, _l, _p, dt, *_ in rows]
    empty = sum(1 for r in rows if not r[6].strip())
    print()
    print(f"  {label}: languages {langs}")
    print(
        f"    confidence  p50 {statistics.median(probs):.2f}  "
        f"below 0.6: {sum(1 for p in probs if p < 0.6)}/{len(probs)}"
    )
    print(
        f"    per call    p50 {statistics.median(costs) * 1000:.0f}ms  max {max(costs) * 1000:.0f}ms"
    )
    print(f"    empty blocks {empty}/{len(rows)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("--seconds", type=int, default=10)
    ap.add_argument("--skip-ct2", action="store_true")
    args = ap.parse_args()
    audio, rate = load(Path(args.clip))
    print(f"clip {args.clip}  {len(audio) / rate:.1f}s")
    if not args.skip_ct2:
        run_ct2(audio, rate, args.seconds, args.clip)
    return 0


if __name__ == "__main__":
    sys.exit(main())