"""Same clip, only the level changes. Is loudness what breaks the audio encoder?

The duration sweep falsified duration: padding e_3min's speech out to 12s still
produced the wrong sentence, and at one padding the correct translation appeared
mixed in with invented text ("什么？ ... 最后的推销，汤米。"). A model that had not
heard the speech could not produce that fragment, so the audio is being decoded --
partially, and unstably.

The clips differ in level by about 6 dB (e_3min rms 0.20 crest 12.9; blog_3min rms
0.095 crest 18.5) and blog_3min is the one that works. So this holds the speech and
the duration fixed and varies only the gain. If quiet turns it correct, the audio
encoder is being driven too hard and the fix is a level on the way in, not a
different model.

Both directions are tested, including making blog_3min as loud as e_3min, so the
result cannot be read as "quieter is always better".
"""
import asyncio
import json
import sys
import wave
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.audio_mt import encode_wav  # noqa: E402
from server.config import AUDIO_SYSTEM_PROMPT, SAMPLE_RATE  # noqa: E402

URL = "http://127.0.0.1:8082/v1/chat/completions"
GAINS = (1.0, 0.5, 0.25, 0.125, 0.0625)


def load(name: str):
    with wave.open(str(ROOT / "samples" / f"{name}.wav"), "rb") as src:
        pcm = np.frombuffer(src.readframes(src.getnframes()), dtype="<i2")
    audio = pcm.astype(np.float32) / 32768.0
    rows = json.loads((ROOT / "samples" / f"{name}_timing.json").read_text(encoding="utf-8"))
    rows = rows["segments"] if isinstance(rows, dict) else rows
    return audio, [r for r in rows if (r.get("text") or "").strip()]


async def one(client: httpx.AsyncClient, audio: np.ndarray) -> str:
    body = {
        "model": "local",
        "messages": [
            {"role": "system", "content": AUDIO_SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "input_audio",
                 "input_audio": {"data": encode_wav(audio), "format": "wav"}},
            ]},
        ],
        "temperature": 0,
        "max_tokens": 220,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    r = await client.post(URL, json=body)
    if r.status_code != 200:
        return f"<HTTP {r.status_code}>"
    return ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()


async def main() -> int:
    async with httpx.AsyncClient(timeout=300.0) as client:
        for name, take in (("e_3min", 3), ("blog_3min", 1)):
            audio, rows = load(name)
            print(f"\n===== {name} =====")
            for row in rows[:take]:
                lo, hi = int(row["start"] * SAMPLE_RATE), int(row["end"] * SAMPLE_RATE)
                seg = audio[lo:hi]
                rms = float(np.sqrt((seg ** 2).mean()))
                print(f"\n  src ({seg.size / SAMPLE_RATE:.2f}s, rms {rms:.4f}) {row['text'][:54]!r}")
                for gain in GAINS:
                    out = await one(client, seg * gain)
                    print(f"    gain {gain:>7.4f} (rms {rms * gain:.4f})  out {out[:64]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
