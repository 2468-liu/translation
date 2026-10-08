"""Same speech, only the window length changes. Is fragment duration the variable?

The clip probe left two candidates for why e_3min fails where blog_3min succeeds:
the loudness structure (crest 12.9 dB against 18.5) or the fragment length (1-3s
against 11-12s). Both differ between the clips at once, which is exactly the
confound this repository keeps getting caught by, so this holds the speech fixed
and varies only the window.

Each fragment is padded symmetrically with its surrounding audio to a target
length. The padded content stays in order, so the translation of the speech should
not change -- if only duration matters, quality should climb as padding grows and
be flat thereafter. If padding to 12s still produces the same wrong sentence, then
duration is not what is wrong and the caller can stop looking at segmentation.
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
TARGETS = (1.0, 2.0, 4.0, 8.0, 12.0)


def load(name: str):
    with wave.open(str(ROOT / "samples" / f"{name}.wav"), "rb") as src:
        pcm = np.frombuffer(src.readframes(src.getnframes()), dtype="<i2")
    audio = pcm.astype(np.float32) / 32768.0
    rows = json.loads((ROOT / "samples" / f"{name}_timing.json").read_text(encoding="utf-8"))
    rows = rows["segments"] if isinstance(rows, dict) else rows
    return audio, [r for r in rows if (r.get("text") or "").strip()]


def window(audio: np.ndarray, row: dict, seconds: float) -> np.ndarray:
    """The speech, padded symmetrically with neighbours up to `seconds`."""
    lo, hi = int(row["start"] * SAMPLE_RATE), int(row["end"] * SAMPLE_RATE)
    want = int(seconds * SAMPLE_RATE)
    if hi - lo >= want:
        mid = (lo + hi) // 2
        return audio[mid - want // 2: mid + want // 2]
    pad = want - (hi - lo)
    left = min(pad // 2, lo)
    right = min(pad - left, audio.size - hi)
    left += min(pad - left - right, lo - left)
    return audio[lo - left: hi + right]


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
    names = sys.argv[1].split(",") if len(sys.argv) > 1 else ["e_3min", "blog_3min"]
    rows_to_use = int(sys.argv[2]) if len(sys.argv) > 2 else 3

    async with httpx.AsyncClient(timeout=300.0) as client:
        for name in names:
            audio, rows = load(name)
            print(f"\n===== {name} =====")
            for row in rows[:rows_to_use]:
                speech = row["end"] - row["start"]
                print(f"\n  src ({speech:.2f}s speech) {row['text'][:56]!r}")
                for target in TARGETS:
                    if target + 0.05 < speech:
                        continue
                    seg = window(audio, row, target)
                    out = await one(client, seg)
                    mark = "  "
                    print(f"    pad->{target:>5.1f}s  out {out[:66]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
