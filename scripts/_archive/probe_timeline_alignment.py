"""Does the timing file's text actually match the audio at those timestamps?

Every audio-native configuration produced the same wrong translation for the same
fragment -- across MTP on/off and 32/99 GPU layers -- and different wrong
translations for fragments that differ only in wording. Identical output from
different configurations means the configurations are not what is wrong.

So the question moves to the input. samples/e_3min_timing.json carries a text
column, and this transcribes the audio at those exact offsets with whisper to see
whether the two agree. If the timeline's text describes different audio than the
file at those offsets, then every "the audio-native model mistranslates this"
verdict so far has been comparing the model against the wrong reference.
"""
import json
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.config import SAMPLE_RATE  # noqa: E402

WAV = ROOT / "samples" / "e_3min.wav"
TIMING = ROOT / "samples" / "e_3min_timing.json"


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 6

    with wave.open(str(WAV), "rb") as src:
        frames = src.getnframes()
        pcm = np.frombuffer(src.readframes(frames), dtype="<i2")
    audio = pcm.astype(np.float32) / 32768.0
    print(f"{WAV.name}: {frames / SAMPLE_RATE:.2f}s, {frames} frames @ {SAMPLE_RATE}Hz")

    data = json.loads(TIMING.read_text(encoding="utf-8"))
    rows = data["segments"] if isinstance(data, dict) else data
    if isinstance(data, dict):
        print(f"timeline header: audio_total={data.get('audio_total')} covered_span={data.get('covered_span')}")
    print(f"timeline rows: {len(rows)}  first={rows[0].get('start')}..{rows[0].get('end')}  "
          f"last={rows[-1].get('end')}")
    print()

    # The evidence that does not need whisper: what is actually in the samples at
    # each offset, and whether the timeline's own numbers are self-consistent.
    print(f"{'i':>3} {'start':>7} {'end':>7} {'dur':>6} {'peak':>6} {'rms':>7}  text")
    for i, row in enumerate(rows[:limit]):
        lo, hi = int(row["start"] * SAMPLE_RATE), int(row["end"] * SAMPLE_RATE)
        seg = audio[lo:hi]
        peak = float(np.abs(seg).max()) if seg.size else 0.0
        rms = float(np.sqrt((seg ** 2).mean())) if seg.size else 0.0
        print(f"{i:>3} {row['start']:>7.2f} {row['end']:>7.2f} "
              f"{row['end'] - row['start']:>6.2f} {peak:>6.3f} {rms:>7.4f}  {(row.get('text') or '')[:52]!r}")

    print("\ntranscribing the same offsets with whisper-small...")
    from faster_whisper import WhisperModel

    model = WhisperModel(str(ROOT / "models" / "faster-whisper-small"), device="cuda", compute_type="float16")
    print(f"{'i':>3}  {'timeline text':<52} | whisper hears")
    for i, row in enumerate(rows[:limit]):
        lo, hi = int(row["start"] * SAMPLE_RATE), int(row["end"] * SAMPLE_RATE)
        segments, _info = model.transcribe(audio[lo:hi], language="en", beam_size=1)
        heard = " ".join(s.text.strip() for s in segments).strip()
        print(f"{i:>3}  {(row.get('text') or '')[:52]:<52} | {heard[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
