"""#9: the same PCM through the socket does not segment like the same PCM pushed directly.

Observed: samples/e_3min.wav gives 32 closings with a 1632ms median when the probe
pushes it frame by frame, and 52 closings with a 64ms median when the replay sends
the same file over the websocket at 100ms per message. Same file, same splitter,
same conversion on both sides (server/pipeline.py push_pcm is
int16 -> float32/32768 -> vad.push, exactly what the probe does).

This does not guess which of the differences is responsible. It records three
things and compares them:

  the push shapes   how many samples each push handed the splitter
  the samples       the concatenated input, hashed, so "same audio" is a fact
  the closings      the vad.end sequence, as (why, speech_ms)

Usage:
    conda activate pytorch
    python scripts/probe_socket_vs_push.py --wav samples/e_3min.wav
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import perf, vad as vad_mod  # noqa: E402
from server.config import SAMPLE_RATE, WS_HOST, WS_PORT  # noqa: E402


class Frame:
    """Feeds the splitter and records exactly what it was fed."""

    def __init__(self, reset_engine: bool = True) -> None:
        self.engine = vad_mod.load_vad()
        if reset_engine:
            self.engine.reset()
        self.splitter = vad_mod.UtteranceSplitter(engine=self.engine, tuning=vad_mod.whisper_tuning())
        self.push_shapes: list[int] = []
        self.digest = hashlib.sha256()
        self.audio = 0
        self.ends: list[tuple] = []
        self._events: list[tuple] = []

    def __enter__(self) -> "Frame":
        self._original = perf.emit
        splitter = self.splitter

        def logged(source: str, event: str, **fields) -> None:
            if source == "vad" and event == "end":
                self.ends.append((fields.get("why"), round(float(fields.get("speech_ms") or 0), 1)))
            self._original(source, event, **fields)

        perf.emit = logged  # type: ignore[assignment]
        original_push = splitter.push

        def recording_push(samples: np.ndarray):
            self.push_shapes.append(int(samples.size))
            self.audio += int(samples.size)
            self.digest.update(np.ascontiguousarray(samples, dtype=np.float32).tobytes())
            return original_push(samples)

        splitter.push = recording_push  # type: ignore[method-assign]
        return self

    def __exit__(self, *exc) -> None:
        perf.emit = self._original  # type: ignore[assignment]
        return None

    def flush(self) -> None:
        self.splitter.flush()


def load_pcm(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as src:
        assert src.getframerate() == SAMPLE_RATE, f"{path} is {src.getframerate()}Hz"
        assert src.getnchannels() == 1, f"{path} is {src.getnchannels()}ch"
        return np.frombuffer(src.readframes(src.getnframes()), dtype="<i2")


def shape_summary(shapes: list[int]) -> str:
    counts = {}
    for n in shapes:
        counts[n] = counts.get(n, 0) + 1
    return ", ".join(f"{n} samples x{c}" for n, c in sorted(counts.items(), key=lambda kv: -kv[1])[:6])


def ends_summary(ends: list[tuple]) -> str:
    if not ends:
        return "none"
    speech = sorted(e[1] for e in ends)
    whys = {w: sum(1 for x, _ in ends if x == w) for w in sorted({x for x, _ in ends})}
    return (f"n={len(ends)} median={statistics.median(speech)}ms min={speech[0]} max={speech[-1]} "
            f"why={whys}")


def direct_push_variants(pcm: np.ndarray, hop: int) -> dict[str, dict]:
    """What the probe does, and what the client's chunking would do if it arrived locally."""
    out = {}
    # Variant 1: one frame per push. This is what probe_vad_gate.py does.
    with Frame() as f:
        for start in range(0, pcm.size, vad_mod.FRAME):
            f.splitter.push(pcm[start:start + vad_mod.FRAME].astype(np.float32) / 32768.0)
        f.flush()
    out["direct, frame by frame"] = {
        "shapes": shape_summary(f.push_shapes), "pushes": len(f.push_shapes),
        "audio_s": f.audio / SAMPLE_RATE, "digest": f.digest.hexdigest()[:16],
        "ends": ends_summary(f.ends),
    }
    # Variant 2: the client's 100ms hop, pushed straight in.
    with Frame() as f:
        for start in range(0, pcm.size, hop):
            f.splitter.push(pcm[start:start + hop].astype(np.float32) / 32768.0)
        f.flush()
    out["direct, 100ms hops"] = {
        "shapes": shape_summary(f.push_shapes), "pushes": len(f.push_shapes),
        "audio_s": f.audio / SAMPLE_RATE, "digest": f.digest.hexdigest()[:16],
        "ends": ends_summary(f.ends),
    }
    # Variant 3: one big push, to see whether batching alone changes anything.
    with Frame() as f:
        f.splitter.push(pcm.astype(np.float32) / 32768.0)
        f.flush()
    out["direct, one push"] = {
        "shapes": shape_summary(f.push_shapes), "pushes": len(f.push_shapes),
        "audio_s": f.audio / SAMPLE_RATE, "digest": f.digest.hexdigest()[:16],
        "ends": ends_summary(f.ends),
    }
    return out


async def socket_variant(pcm: np.ndarray, hop: int, realtime: bool = False, drain_s: float = 3.0,
                         url: str = "", read_log: bool = True) -> dict:
    """The real path: the client's messages, over a websocket, to a live server.

    The splitter lives inside the server process, so this reads the server's own
    perf log rather than instrumenting it -- the log is what the replay measures,
    so comparing it against itself is the point.

    ``realtime`` paces the sends at 1x like blog_video_test.py does. If that flag
    changes the answer, the difference between this probe and the replay is
    timing and not audio, which is the whole question #9 asks.
    """
    import websockets
    from server import perf as perf_mod

    target = url or f"ws://{WS_HOST}:{WS_PORT}/ws"
    log_path = Path(perf_mod.LOG_PATH)
    before = log_path.read_text(encoding="utf-8", errors="replace") if (read_log and log_path.exists()) else ""

    async with websockets.connect(target, max_size=2_000_000) as ws:
        await ws.send(json.dumps({"type": "config", "language": "auto"}))
        await ws.send(json.dumps({"type": "media-start"}))
        started = asyncio.get_running_loop().time()
        for start in range(0, pcm.size, hop):
            await ws.send(pcm[start:start + hop].tobytes())
            if realtime:
                target = started + (start + hop) / SAMPLE_RATE
                delay = target - asyncio.get_running_loop().time()
                if delay > 0:
                    await asyncio.sleep(delay)
        await ws.send(json.dumps({"type": "flush"}))
        await asyncio.sleep(drain_s)

    after = log_path.read_text(encoding="utf-8", errors="replace") if read_log else ""
    fresh = after[len(before):] if after.startswith(before) else after
    ends = []
    for line in fresh.splitlines():
        if "vad.end" in line:
            why = line.split("why=")[1].split()[0] if "why=" in line else "?"
            ms = float(line.split("speech_ms=")[1].split()[0]) if "speech_ms=" in line else 0.0
            ends.append((why, round(ms, 1)))
    return {
        "pushes": len(range(0, pcm.size, hop)),
        "audio_s": round(pcm.size / SAMPLE_RATE, 1),
        "ends": ends_summary(ends) if read_log else "(see the server's own output; it does not write a perf log)",
        "log": str(log_path) if read_log else target,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", default="samples/e_3min.wav")
    ap.add_argument("--socket", action="store_true", help="also run the live-websocket variant (needs a server on 8765)")
    ap.add_argument("--realtime", action="store_true", help="pace the socket variant at 1x like the replay does")
    ap.add_argument("--drain", type=float, default=3.0, help="seconds to wait after flush")
    ap.add_argument("--url", default="", help="websocket url; empty = the product's server")
    ap.add_argument("--minimal", action="store_true",
                    help="talk to scripts/minimal_vad_server.py instead; it prints its own endings")
    args = ap.parse_args()

    wav = ROOT / args.wav
    pcm = load_pcm(wav)
    hop = int(SAMPLE_RATE * 0.1)
    print(f"wav        {args.wav}  {pcm.size / SAMPLE_RATE:.1f}s  {pcm.size} samples")
    print(f"client hop {hop} samples ({hop / SAMPLE_RATE * 1000:.0f}ms)")
    print(f"source     sha256 {hashlib.sha256(pcm.tobytes()).hexdigest()[:16]}\n")

    results = direct_push_variants(pcm, hop)

    if args.socket:
        label = "socket, 100ms hops, realtime" if args.realtime else "socket, 100ms hops, as fast as possible"
        try:
            results[label] = asyncio.run(socket_variant(
                pcm, hop, args.realtime, args.drain, args.url, not args.minimal))
        except Exception as exc:
            results[label] = {"error": f"{exc.__class__.__name__}: {exc}"}

    for name, r in results.items():
        print(f"-- {name}")
        if "error" in r:
            print(f"   error: {r['error']}\n")
            continue
        print(f"   pushes {r['pushes']}   audio {r['audio_s']}s   input {r.get('digest', '(server side)')}")
        print(f"   shapes {r.get('shapes', '(server side)')}")
        print(f"   vad.end {r['ends']}\n")

    # The comparison that answers #9.
    base = results["direct, frame by frame"]
    for name, r in results.items():
        if name == "direct, frame by frame" or "error" in r:
            continue
        same_input = r.get("digest") == base["digest"] if "digest" in r else "unknown"
        print(f"vs frame-by-frame: {name}")
        print(f"   same input bytes : {same_input}")
        print(f"   its vad.end      : {r['ends']}")
    print(f"   reference        : {base['ends']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
