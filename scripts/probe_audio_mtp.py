"""Does an audio request survive with MTP enabled on 2.53?

REPORT 16.2 says every request carrying input_audio came back 503 with the draft
model loaded, and that was measured on 2.47. The claim under test now is that
2.53 fixed the audio path's MTP routing -- a claim that arrived from outside this
repository and that nothing here has checked.

This asks one question and does not average: send the same short audio fragments
with the draft loaded, and report the status code of each. A 503 is the old
behaviour; a 200 with generated tokens is the fix. Both are printed with the
server's own error text so the answer cannot be read as "it seemed to work".
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

WAV = ROOT / "samples" / "e_3min.wav"
TIMING = ROOT / "samples" / "e_3min_timing.json"
URL = "http://127.0.0.1:8082/v1/chat/completions"


def fragments(n: int) -> list[tuple[str, np.ndarray]]:
    with wave.open(str(WAV), "rb") as src:
        pcm = np.frombuffer(src.readframes(src.getnframes()), dtype="<i2")
    audio = pcm.astype(np.float32) / 32768.0
    rows = json.loads(TIMING.read_text(encoding="utf-8"))
    rows = rows["segments"] if isinstance(rows, dict) else rows
    rows = [r for r in rows if (r.get("text") or "").strip()]
    out = []
    for row in rows[:n]:
        lo, hi = int(row["start"] * SAMPLE_RATE), int(row["end"] * SAMPLE_RATE)
        out.append((row["text"][:60], audio[lo:hi]))
    return out


async def main(n: int) -> int:
    rows = fragments(n)
    if not rows:
        raise SystemExit("no fragments")
    print(f"{len(rows)} audio fragments -> {URL}\n")
    ok = fail = 0
    async with httpx.AsyncClient(timeout=180.0) as client:
        for i, (text, audio) in enumerate(rows):
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
                "max_tokens": 120,
                "chat_template_kwargs": {"enable_thinking": False},
            }
            try:
                r = await client.post(URL, json=body)
            except Exception as exc:
                print(f"  [{i}] transport error {type(exc).__name__}: {exc}")
                fail += 1
                continue
            if r.status_code != 200:
                fail += 1
                detail = r.text.strip().replace("\n", " ")[:160]
                print(f"  [{i}] HTTP {r.status_code}  {detail}")
                continue
            ok += 1
            data = r.json()
            choice = (data.get("choices") or [{}])[0]
            out = (choice.get("message") or {}).get("content") or ""
            t = data.get("timings") or {}
            print(f"  [{i}] 200  draft={t.get('draft_n_accepted')}/{t.get('draft_n')} "
                  f"prompt_ms={round(t.get('prompt_ms') or 0)} "
                  f"tok/s={round(t.get('predicted_per_second') or 0, 1)}")
            print(f"        src {text!r}")
            print(f"        out {out.strip()[:70]!r}")
    print(f"\n200: {ok}   non-200: {fail}")
    if fail and not ok:
        print("VERDICT: the audio path is refused with this server's configuration")
    elif fail:
        print("VERDICT: mixed -- some audio requests are refused, see the codes above")
    else:
        print("VERDICT: audio requests are served")
    return 0 if ok else 1


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    raise SystemExit(asyncio.run(main(n)))
