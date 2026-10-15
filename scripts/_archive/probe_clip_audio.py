"""The audio model is fine on one clip and broken on another. Compare the clips.

A 12.48s fragment of blog_3min came back as a complete, correct translation. The
same model, same server, same settings produced nonsense on every fragment of
e_3min -- including a 10.28s one. So the variable is the material, and e_3min is
the clip with a loud music bed (the case REPORT 15.6 left untested).

This compares the two files as audio, not as transcripts: level, dynamic range,
clipping, spectral balance. The point is to find something measurable that
separates them, so "the music bed confuses it" becomes a mechanism rather than a
guess -- or gets falsified.
"""
import json
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.config import SAMPLE_RATE  # noqa: E402


def load(name: str) -> np.ndarray:
    with wave.open(str(ROOT / "samples" / f"{name}.wav"), "rb") as src:
        pcm = np.frombuffer(src.readframes(src.getnframes()), dtype="<i2")
    return pcm.astype(np.float32) / 32768.0


def describe(name: str, audio: np.ndarray) -> None:
    rms = float(np.sqrt((audio ** 2).mean()))
    peak = float(np.abs(audio).max())
    # Crest factor: peak over RMS. Speech sits around 10-15 dB; heavily limited
    # material is much lower, and a low crest factor is what "the music is as loud
    # as the speech" looks like numerically.
    crest_db = 20 * np.log10(peak / max(rms, 1e-9))
    clipped = int((np.abs(audio) >= 0.999).sum())
    # Frame-level RMS spread: how much the level moves over time.
    frame = SAMPLE_RATE // 10
    frames = audio[: (audio.size // frame) * frame].reshape(-1, frame)
    frame_rms = np.sqrt((frames ** 2).mean(axis=1))
    p10, p50, p90 = (float(np.percentile(frame_rms, q)) for q in (10, 50, 90))
    print(f"{name}")
    print(f"   duration {audio.size / SAMPLE_RATE:6.2f}s   peak {peak:.3f}   rms {rms:.4f}   "
          f"crest {crest_db:5.1f} dB   clipped {clipped}")
    print(f"   frame rms  p10 {p10:.4f}  p50 {p50:.4f}  p90 {p90:.4f}   "
          f"p90/p10 {p90 / max(p10, 1e-9):6.1f}   (floor-to-speech ratio)")
    # Energy below 200Hz, which is where a music bed usually lives.
    spec = np.abs(np.fft.rfft(audio * np.hanning(audio.size)))
    freqs = np.fft.rfftfreq(audio.size, 1 / SAMPLE_RATE)
    total = spec.sum()
    low = spec[freqs < 200].sum() / max(total, 1e-9)
    speech_band = spec[(freqs >= 200) & (freqs <= 4000)].sum() / max(total, 1e-9)
    print(f"   spectrum   <200Hz {low * 100:5.1f}%   200-4000Hz {speech_band * 100:5.1f}%")


def per_segment(name: str, limit: int) -> None:
    rows = json.loads((ROOT / "samples" / f"{name}_timing.json").read_text(encoding="utf-8"))
    rows = rows["segments"] if isinstance(rows, dict) else rows
    rows = [r for r in rows if (r.get("text") or "").strip()]
    audio = load(name)
    print(f"   per-segment (first {limit}):")
    print(f"      {'i':>3} {'dur':>6} {'peak':>6} {'rms':>7} {'crest':>6}  text")
    for i, row in enumerate(rows[:limit]):
        lo, hi = int(row["start"] * SAMPLE_RATE), int(row["end"] * SAMPLE_RATE)
        seg = audio[lo:hi]
        if not seg.size:
            continue
        r = float(np.sqrt((seg ** 2).mean()))
        p = float(np.abs(seg).max())
        print(f"      {i:>3} {seg.size / SAMPLE_RATE:>6.2f} {p:>6.3f} {r:>7.4f} "
              f"{20 * np.log10(p / max(r, 1e-9)):>6.1f}  {(row.get('text') or '')[:40]!r}")


def main() -> int:
    for name in ("blog_3min", "e_3min", "er_3min"):
        path = ROOT / "samples" / f"{name}.wav"
        if not path.exists():
            continue
        describe(name, load(name))
        per_segment(name, 6)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
