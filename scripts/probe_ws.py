"""Send samples/hello.wav to the running WebSocket server and print every reply."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.config import SAMPLE_RATE, WS_HOST, WS_PORT  # noqa: E402


async def main() -> None:
    wav = ROOT / "samples" / "hello.wav"
    audio, rate = sf.read(wav, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if rate != SAMPLE_RATE:
        src = np.linspace(0, 1, num=audio.shape[0], endpoint=False)
        dst = np.linspace(0, 1, num=int(audio.shape[0] / rate * SAMPLE_RATE), endpoint=False)
        audio = np.interp(dst, src, audio).astype(np.float32)
    pcm = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
    hop = int(SAMPLE_RATE * 0.1)

    async with connect(f"ws://{WS_HOST}:{WS_PORT}/ws") as ws:
        await ws.send(json.dumps({"type": "config", "language": "en"}))

        async def reader() -> None:
            async for message in ws:
                print(message, flush=True)

        task = asyncio.create_task(reader())
        for start in range(0, pcm.shape[0], hop):
            await ws.send(pcm[start : start + hop].tobytes())
            await asyncio.sleep(0.02)
        await ws.send(json.dumps({"type": "flush"}))
        await asyncio.sleep(12)
        task.cancel()


asyncio.run(main())
