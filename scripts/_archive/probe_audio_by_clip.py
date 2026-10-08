"""Which clips does the audio-native model actually translate? Three, side by side.

The clip comparison found the separating measurement: blog_3min has real dynamic
range (crest 16-18 dB, frame p90/p10 in the hundreds of thousands, true silence
between sentences) while e_3min and er_3min are flat and loud (crest 9-13 dB,
p90/p10 around 3). blog_3min is the one that translates correctly.

Two clips is not enough to call that a mechanism, so this runs all three through
the same server with the same prompt and prints the pairs. If er_3min behaves like
e_3min, the material's loudness structure is the separating variable; if er_3min
translates fine, then something about e_3min specifically is at fault and the
crest factor is a coincidence.
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
CLIPS = ("blog_3min", "e_3min", "er_3min")
PER_CLIP = 4


def load(name: str):
    with wave.open(str(ROOT / "samples" / f"{name}.wav"), "rb") as src:
        pcm = np.frombuffer(src.readframes(src.getnframes()), dtype="<i2")
    audio = pcm.astype(np.float32) / 32768.0
    rows = json.loads((ROOT / "samples" / f"{name}_timing.json").read_text(encoding="utf-8"))
    rows = rows["segments"] if isinstance(rows, dict) else rows
    rows = [r for r in rows if (r.get("text") or "").strip()]
    return audio, rows[:PER_CLIP]


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
        "max_tokens": 200,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    r = await client.post(URL, json=body)
    if r.status_code != 200:
        return f"<HTTP {r.status_code}>"
    return ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()


async def main() -> int:
    async with httpx.AsyncClient(timeout=300.0) as client:
        for name in CLIPS:
            path = ROOT / "samples" / f"{name}.wav"
            if not path.exists():
                continue
            audio, rows = load(name)
            frame = SAMPLE_RATE // 10
            usable = audio[: (audio.size // frame) * frame].reshape(-1, frame)
            fr = np.sqrt((usable ** 2).mean(axis=1))
            crest = 20 * np.log10(float(np.abs(audio).max()) / max(float(np.sqrt((audio ** 2).mean())), 1e-9))
            print(f"\n===== {name}   crest {crest:.1f} dB   "
                  f"p90/p10 {float(np.percentile(fr, 90)) / max(float(np.percentile(fr, 10)), 1e-9):.1f} =====")
            for i, row in enumerate(rows):
                lo, hi = int(row["start"] * SAMPLE_RATE), int(row["end"] * SAMPLE_RATE)
                seg = audio[lo:hi]
                out = await one(client, seg)
                print(f"  [{i}] {seg.size / SAMPLE_RATE:5.2f}s  src {row['text'][:52]!r}")
                print(f"        out {out[:72]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
