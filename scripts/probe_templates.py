"""Compare the AST template against a plain instruction on audio input.

Answers one question: when does Chinese first reach the screen? Not first token
-- for the AST template the first tokens are the English transcription, which a
subtitle cannot show.

Interleaved and offset so no prompt is served from cache: a warm second call
measured 58ms against 314ms for the same prompt, which is the prompt cache, not
the template.
"""

from __future__ import annotations

import base64
import io
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8080/v1/chat/completions"

SYS = (
    "Translate the following English speech into Simplified Chinese. "
    "Output only the Chinese translation."
)
AST = (
    "Transcribe the following speech segment in English, then translate it into "
    "Chinese. When formatting the answer, first output the transcription in "
    "English, then one newline, then output the string 'Chinese: ', then the "
    "translation in Chinese."
)


def wav_b64(secs: float, offset: float, rate: int) -> str:
    data, r = sf.read(ROOT / "samples" / "blog_3min.wav", dtype="float32", always_2d=False)
    seg = data[int(r * offset) : int(r * (offset + secs))]
    buf = io.BytesIO()
    sf.write(buf, seg, r, format="WAV", subtype="PCM_16")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def ask(messages: list[dict], marker: str) -> tuple[float | None, float, str]:
    body = {
        "model": "local",
        "messages": messages,
        "temperature": 0,
        "max_tokens": 400,
        "stream": True,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    t0 = time.perf_counter()
    first: float | None = None
    text = ""
    with urllib.request.urlopen(req, timeout=300) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            if line == "data: [DONE]":
                break
            try:
                piece = json.loads(line[6:])["choices"][0]["delta"].get("content") or ""
            except Exception:
                continue
            text = piece[1:] if piece.startswith("\r") else text + piece
            if first is None and marker in text:
                first = (time.perf_counter() - t0) * 1000.0
    return first, (time.perf_counter() - t0) * 1000.0, text


def main() -> int:
    import numpy as np

    _, rate = sf.read(ROOT / "samples" / "blog_3min.wav", frames=1, dtype="float32")
    results: dict[str, list[tuple[float | None, float]]] = {"AST": [], "sys": []}
    samples: dict[str, str] = {}

    for i, offset in enumerate((0, 20, 40, 60, 80)):
        audio = {
            "type": "input_audio",
            "input_audio": {"data": wav_b64(6, offset, int(rate)), "format": "wav"},
        }
        ast = ([{"role": "user", "content": [{"type": "text", "text": AST}, audio]}], "Chinese:")
        sysp = ([{"role": "system", "content": SYS}, {"role": "user", "content": [audio]}], "本")
        # Alternate the order so neither template always benefits from a warm cache.
        order = [("AST", ast), ("sys", sysp)]
        if i % 2:
            order.reverse()
        for name, (messages, marker) in order:
            first, total, text = ask(messages, marker)
            results[name].append((first, total))
            samples[f"{name}@{offset}s"] = text

    print(f"{'template':10} {'chinese visible':>18} {'stream done':>12}   n")
    for name, rows in results.items():
        hits = [r[0] for r in rows if r[0] is not None]
        med = statistics.median(hits) if hits else float("nan")
        print(
            f"{name:10} {med:8.0f}ms "
            f"[{min(hits) if hits else 0:.0f}-{max(hits) if hits else 0:.0f}]  "
            f"{statistics.median([r[1] for r in rows]):10.0f}ms   {len(hits)}/{len(rows)}"
        )

    print("\nsample output")
    for key in ("AST@0s", "sys@0s"):
        print(f"  {key}: {samples[key][:110]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())