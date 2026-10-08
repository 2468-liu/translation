"""Same audio, different prompt and duration: what makes the audio model behave?

Every configuration so far -- MTP on/off, 32/99 GPU layers, CPU or GPU projector --
produced the same wrong translations, so the configurations are not the variable.
The timeline is verified aligned (whisper hears exactly what the text says), so
the input audio is the right audio.

That leaves how the fragment is *presented*: how long it is, and what instruction
accompanies it. Both are cheap to vary and neither has been varied yet. A long
fragment on a clean clip is included as a control, because a model that fails on
every long fragment is a different problem from one that fails on short ones.
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
from server.config import SAMPLE_RATE  # noqa: E402

URL = "http://127.0.0.1:8082/v1/chat/completions"

PROMPTS = {
    "current": "Translate the following English speech into Simplified Chinese. "
               "Output only the Chinese translation.",
    "terse": "把下面的英语语音翻译成简体中文，只输出译文。",
    "transcribe+translate": "Listen to the audio. First write what you hear in English, "
                            "then on a new line write its Simplified Chinese translation.",
}


def load(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as src:
        pcm = np.frombuffer(src.readframes(src.getnframes()), dtype="<i2")
    return pcm.astype(np.float32) / 32768.0


def segments(name: str, limit: int) -> list[tuple[str, np.ndarray]]:
    audio = load(ROOT / "samples" / f"{name}.wav")
    rows = json.loads((ROOT / "samples" / f"{name}_timing.json").read_text(encoding="utf-8"))
    rows = rows["segments"] if isinstance(rows, dict) else rows
    rows = [r for r in rows if (r.get("text") or "").strip()]
    out = []
    for row in rows[:limit]:
        lo, hi = int(row["start"] * SAMPLE_RATE), int(row["end"] * SAMPLE_RATE)
        out.append((row["text"], audio[lo:hi]))
    return out


async def ask(client: httpx.AsyncClient, prompt: str, audio: np.ndarray, tokens: int = 200) -> dict:
    body = {
        "model": "local",
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": [
                {"type": "input_audio",
                 "input_audio": {"data": encode_wav(audio), "format": "wav"}},
            ]},
        ],
        "temperature": 0,
        "max_tokens": tokens,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    r = await client.post(URL, json=body)
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code} {r.text.strip()[:100]}"}
    data = r.json()
    choice = (data.get("choices") or [{}])[0]
    t = data.get("timings") or {}
    return {
        "out": ((choice.get("message") or {}).get("content") or "").strip(),
        "prompt_ms": t.get("prompt_ms"),
        "tok_s": t.get("predicted_per_second"),
        "finish": choice.get("finish_reason"),
    }


async def main() -> int:
    # A long segment on the clean clip, as the control.
    blog = segments("blog_3min", 1)
    e3 = segments("e_3min", 4)

    async with httpx.AsyncClient(timeout=300.0) as client:
        print("=" * 96)
        print("A. duration: 1.1s / 1.4s / 1.2s fragments, then a 10.3s and a long clean one")
        print("=" * 96)
        for i, (text, audio) in enumerate(e3):
            got = await ask(client, PROMPTS["current"], audio)
            dur = audio.size / SAMPLE_RATE
            print(f"\n[{i}] {dur:5.2f}s  src {text[:60]!r}")
            if "error" in got:
                print(f"     ERROR {got['error']}")
            else:
                print(f"     out {got['out'][:78]!r}   (finish={got['finish']})")

        text, audio = e3[2]
        print(f"\n[2-long] {audio.size / SAMPLE_RATE:5.2f}s  src {text[:60]!r}")
        got = await ask(client, PROMPTS["current"], audio)
        print(f"     out {got.get('out', got.get('error'))[:78]!r}")

        if blog:
            text, audio = blog[0]
            print(f"\n[blog0]  {audio.size / SAMPLE_RATE:5.2f}s  src {text[:60]!r}")
            got = await ask(client, PROMPTS["current"], audio)
            print(f"     out {got.get('out', got.get('error'))[:78]!r}")

        print()
        print("=" * 96)
        print("B. prompt: same fragment, three instructions (transcribe-first forces listening)")
        print("=" * 96)
        text, audio = e3[3]
        for name, prompt in PROMPTS.items():
            got = await ask(client, prompt, audio)
            print(f"\n[{name}]  src {text[:60]!r}")
            print(f"     out {got.get('out', got.get('error'))[:200]!r}")

        print()
        print("=" * 96)
        print("C. sample rate: the same fragment resampled to 8k and 24k before encoding")
        print("=" * 96)
        text, audio = e3[3]
        for rate in (8000, 16000, 24000):
            if rate == SAMPLE_RATE:
                resampled = audio
            else:
                n = int(audio.size * rate / SAMPLE_RATE)
                idx = np.clip((np.arange(n) * SAMPLE_RATE / rate).astype(int), 0, audio.size - 1)
                resampled = audio[idx]
            got = await ask(client, PROMPTS["current"], resampled)
            print(f"\n[{rate}Hz]  src {text[:56]!r}")
            print(f"     out {got.get('out', got.get('error'))[:120]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
