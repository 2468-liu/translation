"""Translation by a model that hears the audio itself.

One request per fragment: the audio goes in, Chinese comes out. Nothing
transcribes first, so there is no intermediate text to wait for and no second
model to keep resident on the GPU -- which is what makes this fit a 4GB card
where whisper-small and a translation model together do not.

Two details are load-bearing and easy to get wrong:

* **The instruction has to be English.** The same instruction in Chinese was
  measured returning an empty answer after 585 tokens of reasoning, against 17
  tokens and a correct translation in English.
* **Thinking has to be switched off.** The model is a reasoning model; left on,
  it spends the entire token budget thinking and returns nothing.
"""

from __future__ import annotations

import base64
import io
import json
import wave

import numpy as np

from . import perf
from .config import (
    AUDIO_CHAT_TEMPLATE_KWARGS,
    AUDIO_MAX_TOKENS,
    AUDIO_MODEL_NAME,
    AUDIO_MODEL_URL,
    AUDIO_SYSTEM_PROMPT,
    SAMPLE_RATE,
)


def encode_wav(audio: np.ndarray, rate: int = SAMPLE_RATE) -> str:
    """Base64 WAV for the ``input_audio`` part of a chat message."""
    if audio.dtype != np.float32:
        audio = audio.astype(np.float32)
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm.tobytes())
    return base64.b64encode(buf.getvalue()).decode("ascii")


def build_messages(audio_b64: str, context: list[tuple[str, str]] | None = None) -> list[dict]:
    """Chat messages for one fragment.

    ``context`` is the preceding ``(source, translation)`` pairs. They go in as
    text rather than as more audio: the model is a context-aware translator, and
    handing it the preceding sentences in their own output language is cheaper
    and closer to what it was tuned on than replaying their audio.
    """
    system = AUDIO_SYSTEM_PROMPT
    if context:
        prior = "\n".join(f"- {zh}" for _src, zh in context if zh)
        if prior:
            system += (
                "\n\nThe speech continues from these already-translated lines "
                "about the same talk. Do not repeat or translate them again; "
                "continue seamlessly:\n" + prior
            )
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": [
                {
                    "type": "input_audio",
                    "input_audio": {"data": audio_b64, "format": "wav"},
                }
            ],
        },
    ]


def build_payload(
    audio_b64: str, context: list[tuple[str, str]] | None = None
) -> dict:
    return {
        "model": AUDIO_MODEL_NAME,
        "messages": build_messages(audio_b64, context),
        "temperature": 0,
        "max_tokens": AUDIO_MAX_TOKENS,
        "stream": True,
        "chat_template_kwargs": AUDIO_CHAT_TEMPLATE_KWARGS,
    }


async def stream_audio_translation(
    audio: np.ndarray,
    client,
    context: list[tuple[str, str]] | None = None,
    url: str = AUDIO_MODEL_URL,
):
    """Yield content deltas for one fragment, with probes for first and last.

    Yields accumulated text, not deltas, so a caller that drops a chunk cannot
    end up with a mangled line.
    """
    import httpx

    if audio.size == 0:
        return
    payload = build_payload(encode_wav(audio), context)
    # A long fragment is a long prompt: first token costs roughly 300ms plus
    # 250ms per second of audio, so the read timeout has to clear 26s easily.
    timeout = httpx.Timeout(120.0, connect=5.0)
    request = client.build_request("POST", url, json=payload, timeout=timeout)
    started = perf.mono()
    response = await client.send(request, stream=True)
    response.raise_for_status()
    pieces = 0
    acc = ""
    first = True
    async for line in response.aiter_lines():
        if not line or not line.startswith("data:"):
            continue
        chunk = line[5:].strip()
        if chunk == "[DONE]":
            break
        try:
            obj = json.loads(chunk)
        except ValueError:
            continue
        for choice in obj.get("choices") or []:
            piece = (choice.get("delta") or {}).get("content") or ""
            if not piece:
                continue
            # A CR means the server is replacing what it already emitted, not
            # adding to it. Appending it duplicated a clause: the worst observed
            # line reached 90 characters with the same sentence present twice.
            # Split on CR rather than only testing for a leading one, so a
            # correction arriving mid-chunk is handled too.
            segments = piece.split("\r")
            if len(segments) > 1:
                acc = ""
            acc += segments[-1]
            pieces += 1
            if first:
                first = False
                perf.emit(
                    "audio",
                    "ttft",
                    ttft=perf.ms(started),
                    audio_ms=audio.size * 1000.0 / SAMPLE_RATE,
                )
            yield acc
    perf.emit(
        "audio",
        "total",
        total=perf.ms(started),
        pieces=pieces,
        chars=len(acc),
        audio_ms=audio.size * 1000.0 / SAMPLE_RATE,
        empty=int(not acc.strip()),
    )