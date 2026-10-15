"""Lock the gap definition, and cross-check it before anything is implemented.

Why the gap's definition is the experiment's real precondition
-------------------------------------------------------------
gap_clamped means "extend the start backwards by at most the silence that actually
preceded this utterance". If gap is measured by Silero, the frames in [T-d, T) --
real speech the detector called non-speech -- count as gap, so the pre-roll is
filled with the lost opening words. If gap were measured by an energy threshold,
the music bed under every clause would leave gap near zero, the clamp would eat the
pre-roll before it did anything, and the run would end up measuring "does feeding
ASR a little silence help" -- a different question from the diagnosis.

So: gap is measured in non-speech frames, by the same detector that produced T.
That is not a free choice; it is forced by the diagnosis, which says the lost thing
is speech the detector was late on, not silence.

The assertion, and why it is not the obvious one
------------------------------------------------
"It must contain a frame whose probability is above the speech threshold" fails on
every segment, including the ones where the pre-roll worked perfectly: T is defined
as the frame where the detector first crossed the threshold, so every frame in
[T-d, T) is below it by construction. Asserting that would say the pre-roll never
took effect, which is the opposite of what a working pre-roll looks like.

What the assertion has to rule out is "the pre-roll filled with silence". That needs
a quantity independent of the detector, and this repo has one for the synthetic
material: sparse_music_speech.wav is the clause layer with the bed removed, written
out by build_sparse_music.py. So:

  the pre-roll window must contain speech-layer energy

and, separately, the count of below-threshold frames inside the window is reported
rather than asserted, because it is the diagnosis itself: if it is zero then T was
not caused by detector latency and the whole plan is measuring the wrong thing.

The cross-check, which is cheap and decides whether the definition is right
--------------------------------------------------------------------------
  sparse material, first utterance   gap should be ~5.9s: file start to the first
                                     clause, all bed
  dense material, a chosen utterance gap should equal the real silence between
                                     clauses, and never exceed it
If either disagrees, the definition is wrong and gets fixed here rather than after
a run whose attribution would be meaningless.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import vad as vad_mod  # noqa: E402
from server.config import SAMPLE_RATE  # noqa: E402

from check_sparse_music import load, spans  # noqa: E402


def probabilities(pcm: np.ndarray, frame: int) -> np.ndarray:
    """Silero's per-frame speech probability over the whole file.

    Its own state, stepped one frame at a time, because that is what the splitter
    sees -- asking each frame independently would give a different number and the
    assertion would then be checking the wrong thing.
    """
    engine = vad_mod._Silero(vad_mod.SILERO_ONNX)
    out = np.zeros(len(pcm) // frame, dtype=np.float32)
    for i in range(len(out)):
        out[i] = engine.prob(pcm[i * frame:(i + 1) * frame])
    return out


def gap_before(start_s: float, probs: np.ndarray, frame_s: float, threshold: float) -> float:
    """Non-speech run immediately before an utterance, in seconds.

    Non-speech *by the detector that produced the start*, not by energy. That is the
    whole point: the frames it calls non-speech are exactly the ones a pre-roll
    should recover, so counting them is what makes the clamp safe rather than what
    makes it vacuous.
    """
    idx = int(round(start_s / frame_s))
    i = idx - 1
    run = 0
    while i >= 0 and probs[i] < threshold:
        run += 1
        i -= 1
    return run * frame_s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cap-ms", type=int, default=800)
    ap.add_argument("--gate-ms", type=int, default=250)
    ap.add_argument("--threshold", type=float, default=None)
    args = ap.parse_args()

    threshold = args.threshold if args.threshold is not None else vad_mod.VAD_THRESHOLD
    frame = vad_mod.FRAME
    frame_s = frame / SAMPLE_RATE
    print(f"  gap 定义：Silero 非语音帧（阈值 {threshold}），不按能量")
    print(f"  预卷 = min(上限 {args.cap_ms}ms, gap)；帧长 {frame_s * 1000:.0f}ms")
    print()

    speech_layer_path = ROOT / "samples" / "sparse_music_speech.wav"
    speech_layer = load(speech_layer_path) if speech_layer_path.exists() else None

    for name, plan_name in (("sparse_music", "sparse_music"), ("e_3min", None), ("er_3min", None)):
        pcm = load(ROOT / "samples" / f"{name}.wav")
        probs = probabilities(pcm, frame)
        got = spans(pcm, args.gate_ms, 0)
        closed = sorted((g[0], g[1]) for g in got if g[2])

        print(f"=== {name}: {len(closed)} 段 ===")
        show = [closed[0], closed[1], closed[len(closed) // 2]] if len(closed) > 2 else closed

        applied, clamped, speech_in_window = [], 0, []
        for start, _end in show:
            gap = gap_before(start, probs, frame_s, threshold)
            actual = min(args.cap_ms / 1000.0, gap)
            if gap < args.cap_ms / 1000.0:
                clamped += 1
            applied.append(actual)
            lo = int(round((start - actual) / frame_s))
            hi = int(round(start / frame_s))
            if speech_layer is not None and name == "sparse_music":
                w = speech_layer[lo * frame:hi * frame]
                speech_in_window.append((float(np.sqrt(np.mean(w ** 2))) if w.size else 0.0,
                                        float(np.mean(probs[lo:hi] < threshold)) if hi > lo else 0.0))
            print(f"    起点 {start:6.1f}s  gap {gap:5.2f}s  生效 {actual * 1000:5.0f}ms"
                  f"{'  ← 被夹住' if gap < args.cap_ms / 1000.0 else ''}")
        print(f"    生效 p50 {sorted(applied)[len(applied) // 2] * 1000:.0f}ms   "
              f"被夹 {clamped}/{len(show)}")
        if speech_in_window:
            for rmsv, below in speech_in_window:
                print(f"      窗口内语音层 RMS {rmsv:.4f}   低于阈值的帧占比 {below:.0%}")
        print()

    print("  断言（对合成素材有效，因为只有它有独立的语音层）:")
    if speech_layer is not None:
        print("    窗口内必须有语音层能量 —— 上面 speech层 RMS 全为 0 即为失败")
    print("    窗口内低于阈值的帧占比按构造接近 100%；若接近 0，说明 T 不是检测器延迟")
    print("    造成的，本方案的归因就是错的")


if __name__ == "__main__":
    main()