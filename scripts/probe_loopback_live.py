"""Loopback inside the server: is the task alive, and are frames arriving?

Earlier runs disagreed with each other -- one showed raw_peak 0.5 with no VAD
utterances, one showed raw_peak 0.0 for the same clip that records back fine in a
standalone process (peak 0.5137, three times, reproducible). Two different
symptoms, so this run does not guess which: it keeps the socket open, plays for a
longer window, and reports the loopback's own probes as they appear, so "no task",
"task with silence" and "task with level but no utterances" can be told apart.
"""
import asyncio
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.config import SAMPLE_RATE, WS_HOST, WS_PORT  # noqa: E402

CLIPS = ("smoke_10s", "e_3min")


async def main() -> int:
    import websockets
    import soundcard as sc

    clip_name = sys.argv[1] if len(sys.argv) > 1 else "smoke_10s"
    repeats = int(sys.argv[2]) if len(sys.argv) > 2 else 3

    audio, rate = sf.read(str(ROOT / "samples" / f"{clip_name}.wav"), dtype="float32", always_2d=False)
    clip = np.concatenate([audio] * repeats)
    print(f"clip {clip_name} x{repeats} = {clip.size / SAMPLE_RATE:.1f}s, "
          f"played peak {float(np.abs(clip).max()):.4f}")

    received: list[dict] = []
    async with websockets.connect(f"ws://{WS_HOST}:{WS_PORT}/ws", max_size=2_000_000,
                                 ping_interval=None) as ws:
        # ping_interval=None: this client blocks inside player.play() and cannot
        # answer a keepalive ping mid-playback, which is enough to get the server
        # to close the connection. That was observed once and would end the
        # loopback task with it, so it is removed as a variable here.
        await ws.send(json.dumps({"type": "config", "language": "en"}))
        await ws.send(json.dumps({"type": "arm"}))

        async def reader() -> None:
            try:
                async for message in ws:
                    if isinstance(message, str):
                        received.append(json.loads(message))
            except Exception:
                pass

        task = asyncio.create_task(reader())
        await asyncio.sleep(3)
        print("playing...", flush=True)
        speaker = sc.default_speaker()
        with speaker.player(samplerate=SAMPLE_RATE, channels=1) as player:
            player.play(clip.astype(np.float32))
        print("played; draining 10s", flush=True)
        await asyncio.sleep(10)
        task.cancel()

    texts = [r for r in received if r.get("type") in ("asr", "translation")]
    print(f"\nsocket messages {len(received)}  asr/translation {len(texts)}")
    for r in received[:6]:
        kind = r.get("type")
        body = (r.get("text") or r.get("message") or "")
        print(f"   {kind:16} {str(body)[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
