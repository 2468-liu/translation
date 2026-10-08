"""Minimal server: a websocket that feeds every binary frame straight to the splitter.

#9 asks why the same PCM, over the same socket, with the same chunking, segments
into 53 closings inside the product and 32 inside the probe. Every difference
that could be tested in isolation has been eliminated:

    the audio bytes      client sha256 == probe sha256 (announced by the client)
    the socket           probe over a real websocket: 32 closings
    100ms chunking       direct and socket both: 32 closings
    real-time pacing     probe at 1x: 32 closings
    sample rate          the file is 16k mono; nothing resamples
    language             auto in both
    engine state         reset() before each probe pass

What is left is everything else server/ws_server.py does: the caption window, the
recogniser, the loopback task, the API config, the state/udp listener. This server
does none of it -- it reads binary frames, pushes them into one splitter, and
prints the closings. If it reproduces 53, the difference is in the splitter's
input and this is the smallest thing that shows it. If it reproduces 32, something
ws_server.py does is changing the input, and the next bisection is its.

Usage:
    conda activate pytorch
    python scripts/minimal_vad_server.py --port 8790 --seconds 200
Then in another shell:
    python scripts/probe_socket_vs_push.py --wav samples/e_3min.wav --socket --url ws://127.0.0.1:8790/ws
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import statistics
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import perf, vad as vad_mod  # noqa: E402


async def serve(port: int, seconds: float) -> None:
    from websockets.asyncio.server import serve as ws_serve

    engine = vad_mod.load_vad()
    engine.reset()
    splitter = vad_mod.UtteranceSplitter(engine=engine, tuning=vad_mod.whisper_tuning())

    state = {"ends": [], "bytes": 0, "payloads": 0, "digest": hashlib.sha256(), "shapes": []}

    async def handle(ws) -> None:
        print("client connected", flush=True)
        try:
            async for message in ws:
                if isinstance(message, str):
                    print(f"  text: {message[:80]}", flush=True)
                    continue
                state["payloads"] += 1
                state["bytes"] += len(message)
                state["shapes"].append(len(message))
                state["digest"].update(message)
                samples = np.frombuffer(message, dtype=np.int16).astype(np.float32) / 32768.0
                for kind, audio, flag in splitter.push(samples):
                    if kind == "end" and audio is not None:
                        state["ends"].append((len(audio),))
                if state["payloads"] % 200 == 0:
                    print(f"  {state['payloads']} payloads, {state['bytes']} bytes, "
                          f"{len(state['ends'])} ends", flush=True)
        finally:
            splitter.flush()
            print("client gone", flush=True)

    async with ws_serve(handle, "127.0.0.1", port, max_size=2_000_000):
        print(f"minimal vad server on ws://127.0.0.1:{port}/ws", flush=True)
        try:
            await asyncio.sleep(seconds)
        finally:
            shapes = {}
            for n in state["shapes"]:
                shapes[n] = shapes.get(n, 0) + 1
            print(f"\npayloads {state['payloads']}  bytes {state['bytes']}  "
                  f"unique payload sizes {sorted(shapes)[:5]}")
            print(f"input sha256 {state['digest'].hexdigest()[:16]}")
            if state["ends"]:
                sizes = sorted(e[0] for e in state["ends"])
                print(f"ends n={len(sizes)}  median samples={statistics.median(sizes)} "
                      f"({statistics.median(sizes) / 16:.0f}ms)  min={sizes[0]} max={sizes[-1]}")
            else:
                print("ends n=0")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--seconds", type=float, default=240.0)
    args = ap.parse_args()
    perf.emit  # imported so the module is initialised the same way the product does
    asyncio.run(serve(args.port, args.seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
