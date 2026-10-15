"""One audio-native matrix: does MTP break the audio path, or is it -ngl 32?

REPORT 16.2's 503 is gone -- on 2.53 an audio request with the draft loaded is
served (5/5), so MTP reaches the audio path now. But the same five fragments came
back as garbage while the text-only path translates them correctly:

    I reckon we should call it a day.      -> 我以为我们应该叫它"大鸟"
    It's the final pitch, Tommy.           -> 这是最终的推举
    It'll still be there in the morning.   -> 我早就在早上，我保证
    Give me one more try.                  -> 他已经很长时间了

Two explanations, and they call for opposite fixes:

    the draft interferes   speculative decoding corrupts the audio path, so MTP
                           must stay off for audio even though it now loads
    the layers are short   36 layers with 32 on the GPU (and the mmproj on the
                           GPU too) leaves part of the model on the CPU, and the
                           audio encoder's features do not survive that

So this runs the same fragments against both configurations and prints the
translations side by side. It also reports the draft counters, because a draft
that never engages cannot be the cause of anything.
"""
import argparse
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

WAV = ROOT / "samples" / "e_3min.wav"
TIMING = ROOT / "samples" / "e_3min_timing.json"
SYSTEM = AUDIO_SYSTEM_PROMPT


def fragments(limit: int) -> list[dict]:
    with wave.open(str(WAV), "rb") as src:
        pcm = np.frombuffer(src.readframes(src.getnframes()), dtype="<i2")
    audio = pcm.astype(np.float32) / 32768.0
    rows = json.loads(TIMING.read_text(encoding="utf-8"))
    rows = rows["segments"] if isinstance(rows, dict) else rows
    rows = [r for r in rows if (r.get("text") or "").strip()]
    out = []
    for row in rows[:limit]:
        lo, hi = int(row["start"] * SAMPLE_RATE), int(row["end"] * SAMPLE_RATE)
        out.append({"text": row["text"], "audio": audio[lo:hi]})
    return out


async def one(client: httpx.AsyncClient, url: str, audio: np.ndarray, prompt: str) -> dict:
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
        "max_tokens": 160,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        r = await client.post(url, json=body)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code} {r.text.strip()[:120]}"}
    data = r.json()
    choice = (data.get("choices") or [{}])[0]
    t = data.get("timings") or {}
    return {
        "out": ((choice.get("message") or {}).get("content") or "").strip(),
        "prompt_ms": t.get("prompt_ms"),
        "tok_s": t.get("predicted_per_second"),
        "draft_n": t.get("draft_n"),
        "draft_ok": t.get("draft_n_accepted"),
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8082/v1/chat/completions")
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    rows = fragments(args.limit)
    print(f"audio-native  {args.url}   {len(rows)} fragments\n")
    results = []
    async with httpx.AsyncClient(timeout=240.0) as client:
        for i, row in enumerate(rows):
            got = await one(client, args.url, row["audio"], SYSTEM)
            got["src"] = row["text"]
            results.append(got)
            if "error" in got:
                print(f"[{i}] ERROR {got['error']}")
                continue
            draft = f"draft={got['draft_ok']}/{got['draft_n']}" if got["draft_n"] else "draft=absent"
            print(f"[{i}] prompt={round(got['prompt_ms'] or 0)}ms tok/s={round(got['tok_s'] or 0,1)} {draft}")
            print(f"    src {row['text'][:64]!r}")
            print(f"    out {got['out'][:64]!r}")

    served = [r for r in results if "error" not in r]
    if served:
        prompt = [r["prompt_ms"] for r in served if r.get("prompt_ms")]
        rate = [r["tok_s"] for r in served if r.get("tok_s")]
        drafted = sum(r.get("draft_n") or 0 for r in served)
        accepted = sum(r.get("draft_ok") or 0 for r in served)
        print(f"\nserved {len(served)}/{len(results)}")
        if prompt:
            print(f"prompt_ms median {round(sorted(prompt)[len(prompt)//2])}  "
                  f"tok/s median {round(sorted(rate)[len(rate)//2], 1)}")
        print(f"draft tokens {accepted}/{drafted}"
              + ("   -> the draft never ran" if drafted == 0 else ""))
    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
