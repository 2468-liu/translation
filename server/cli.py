"""Feed a wav file through the same session used by the WebSocket server."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import numpy as np
import soundfile as sf

from .asr import build_asr
from .config import SAMPLE_RATE
from .pipeline import Session


def load_audio(path: str) -> np.ndarray:
    audio, rate = sf.read(path, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if rate == SAMPLE_RATE:
        return audio.astype(np.float32)
    duration = audio.shape[0] / rate
    target = int(duration * SAMPLE_RATE)
    if target <= 1:
        return np.zeros(0, dtype=np.float32)
    src = np.linspace(0, 1, num=audio.shape[0], endpoint=False)
    dst = np.linspace(0, 1, num=target, endpoint=False)
    return np.interp(dst, src, audio).astype(np.float32)


async def run(path: str, language: str) -> None:
    asr = build_asr()
    session = Session(asr)
    session.set_language(language)

    async def send(payload: dict) -> None:
        print(json.dumps(payload, ensure_ascii=False), flush=True)

    worker = asyncio.create_task(session.worker(send))
    audio = load_audio(path)
    pcm = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
    hop = int(SAMPLE_RATE * 0.1)
    try:
        for start in range(0, pcm.shape[0], hop):
            for payload in session.push_pcm(pcm[start : start + hop].tobytes()):
                await send(payload)
        for payload in session.flush():
            await send(payload)
        await session.jobs.join()
    finally:
        worker.cancel()
        await session.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wav")
    parser.add_argument("--language", default="auto")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.wav, args.language))
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
