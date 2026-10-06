r"""Did MTP ever engage on the audio path, and would it help if it had?

The earlier measurement said no: 15.09 ms/token with the draft model against
12.85 without, at the same 0.30 acceptance rate in both. That does not say
speculative decoding does not help here. It says something narrower, and the
difference matters:

  if the draft had actually run, the acceptance rate would differ between the
  two configurations. Identical acceptance in both is what a draft that never
  engaged looks like. So the measurement established "enabling MTP changed
  nothing and cost time", not "MTP works and is slower".

Three reasons the draft may never have engaged, all checkable:

  it never loaded    llama-server logs which spec implementation it selected
  it had nowhere to run  the draft is Q8_0 and the main model already holds
                    3774 of 4096 MiB, leaving about 320 for a draft head
  it is structurally misplaced  the projector runs on CPU, so prefill is the
                    bottleneck and speculative decoding only shortens decode,
                    which is already quick

So this probe reports four things per run and does not average them into a
verdict: tokens per second, the acceptance rate as the server reports it, the
draft counters, and the finish reason. A run whose draft counters are all zero
is a run in which the draft did nothing, whatever the token rate says.

Requests are interleaved between the fragments and each repetition is sliced at a
different offset. Repeating one request verbatim measures llama-server's prompt
cache -- 58ms against 314ms cold on this model -- which is a larger effect than
the thing under test.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import statistics
import sys
import wave
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.config import (  # noqa: E402
    AUDIO_CHAT_TEMPLATE_KWARGS,
    AUDIO_MODEL_NAME,
    AUDIO_SYSTEM_PROMPT,
    SAMPLE_RATE,
)

WAV = ROOT / "samples" / "blog_3min.wav"
TIMING = ROOT / "samples" / "blog_3min_timing.json"
URL = "http://127.0.0.1:8080/v1/chat/completions"

# The two fragments that fail the same way in every run, plus one that has
# always translated cleanly. The failures are the point: they are where extra
# generated tokens would show up if the draft were working.
TARGETS = {
    "clean-early": 0,
    "untranslated-en": 11,
    "clause-loop": 13,
}


def load_wav() -> "np.ndarray":
    import numpy as np

    with wave.open(str(WAV), "rb") as src:
        assert src.getframerate() == SAMPLE_RATE, src.getframerate()
        pcm = np.frombuffer(src.readframes(src.getnframes()), dtype="<i2")
    return pcm.astype("float32") / 32768.0


def slice_wav(samples, start: float, end: float, shift: float) -> bytes:
    """Encode a window, optionally shifted so no two requests share a prompt."""
    lo = max(0.0, start + shift) * SAMPLE_RATE
    hi = min(len(samples) / SAMPLE_RATE, end + shift) * SAMPLE_RATE
    pcm = samples[int(lo) : int(hi)]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes((pcm * 32767.0).astype("<i2").tobytes())
    return base64.b64encode(buf.getvalue()).decode("ascii")


async def one(client: httpx.AsyncClient, audio_b64: str) -> dict:
    body = {
        "model": AUDIO_MODEL_NAME,
        "messages": [
            {"role": "system", "content": AUDIO_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {"data": audio_b64, "format": "wav"},
                    }
                ],
            },
        ],
        "temperature": 0,
        "chat_template_kwargs": AUDIO_CHAT_TEMPLATE_KWARGS,
    }
    r = await client.post(URL, json=body, timeout=180.0)
    r.raise_for_status()
    data = r.json()
    choice = (data.get("choices") or [{}])[0]
    t = data.get("timings") or {}
    return {
        "text": (choice.get("message") or {}).get("content") or "",
        "finish": choice.get("finish_reason"),
        # predicted_per_second is tokens/second; ms/token is its reciprocal.
        "ms_per_token": (1000.0 / t["predicted_per_second"]) if t.get("predicted_per_second") else None,
        "predicted_n": t.get("predicted_n"),
        "draft_n": t.get("draft_n"),
        "draft_n_accepted": t.get("draft_n_accepted"),
        "accept_rate": t.get("draft_n_accepted", 0) / t["draft_n"] if t.get("draft_n") else None,
        "prompt_ms": t.get("prompt_ms"),
    }


async def run(reps: int, shifts: list[float]) -> dict:
    import numpy as np  # noqa: F401

    samples = load_wav()
    sentences = json.loads(TIMING.read_text(encoding="utf-8"))
    rows: list[dict] = []
    async with httpx.AsyncClient() as client:
        for rep in range(reps):
            for name, index in TARGETS.items():
                row = sentences[index]
                shift = shifts[(rep + index) % len(shifts)]
                audio_b64 = slice_wav(samples, row["start"], row["end"], shift)
                got = await one(client, audio_b64)
                got["fragment"] = name
                got["rep"] = rep
                rows.append(got)
    return {"rows": rows}


def report(data: dict) -> None:
    rows = data["rows"]
    print(f"{'fragment':18} {'n':>3} {'ms/token':>18} {'draft':>12} {'accepted':>12} {'finish':>8}")
    print("-" * 78)
    for name in TARGETS:
        own = [r for r in rows if r["fragment"] == name]
        rates = [r["ms_per_token"] for r in own if r["ms_per_token"]]
        drafted = [r["draft_n"] for r in own if r["draft_n"] is not None]
        accepted = [r["draft_n_accepted"] for r in own if r["draft_n_accepted"] is not None]
        finish = sorted({str(r["finish"]) for r in own})
        rate_s = (
            f"{statistics.median(rates):7.2f} [{min(rates):.2f}-{max(rates):.2f}]"
            if rates
            else f"{'-':>18}"
        )
        draft_s = f"{sum(drafted)}/{len(drafted)}" if drafted else "absent"
        acc_s = (
            f"{sum(accepted)}/{sum(drafted)}"
            + (
                f" {statistics.median([r['accept_rate'] for r in own if r['accept_rate']]):.2f}"
                if any(r["accept_rate"] for r in own)
                else ""
            )
            if drafted
            else ""
        )
        print(
            f"{name:18} {len(own):>3} {rate_s:>18} {draft_s:>12} {acc_s:>12} "
            f"{'/'.join(finish):>8}"
        )
    print()
    total_drafted = sum(r["draft_n"] or 0 for r in rows)
    print(
        f"tokens drafted in total: {total_drafted}"
        + (
            "  -> the draft never ran; this configuration cannot say anything"
            " about speculative decoding"
            if total_drafted == 0
            else ""
        )
    )
    print()
    for name in TARGETS:
        own = [r for r in rows if r["fragment"] == name]
        print(f"-- {name}")
        for r in own:
            print(f"   rep{r['rep']} finish={r['finish']} draft={r['draft_n']} "
                  f"accepted={r['draft_n_accepted']} {r['text'][:56]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=4)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    shifts = [0.0, 0.37, -0.29, 0.61]
    data = asyncio.run(run(args.reps, shifts))
    if args.out:
        Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    report(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
