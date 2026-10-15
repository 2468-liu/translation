"""Did the planted clauses survive? This is what decides whether the material works.

Building a material that reproduces a fault is not the same as reproducing it. The
build wrote 15 clauses at known times; the point is to find out how many of those
15 the splitter actually delivered at the production gate, matched by time rather
than by count -- a count would be satisfied by the bed's own blips.

Matching is by overlap, not nearest: a clause that got split in two by a music
transient still counts as heard if any detected utterance covers it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import vad as vad_mod  # noqa: E402
from server.config import SAMPLE_RATE  # noqa: E402


def load(path: Path) -> np.ndarray:
    import wave

    with wave.open(str(path), "rb") as src:
        assert src.getframerate() == SAMPLE_RATE, f"{path} is {src.getframerate()}Hz"
        assert src.getnchannels() == 1, f"{path} is not mono"
        pcm = np.frombuffer(src.readframes(src.getnframes()), dtype="<i2")
    return pcm.astype(np.float32) / 32768.0


def spans(pcm: np.ndarray, gate_ms: int, merge_ms: int,
          preroll_ms: int = 0) -> list[tuple[float, float, bool, float]]:
    # One VAD frame per push, then flush. probe_vad_gate.py records why: an earlier
    # version here pushed ~1.28s batches and the arms then disagreed on utterance
    # counts for reasons that had nothing to do with the gate.
    base = vad_mod.whisper_tuning()
    tuning = vad_mod.Tuning(
        min_silence_ms=base.min_silence_ms,
        max_utterance_ms=base.max_utterance_ms,
        speech_pad_ms=base.speech_pad_ms,
        early_end_ms=base.early_end_ms,
        soft_cut_speech_ms=base.soft_cut_speech_ms,
        soft_cut_silence_ms=base.soft_cut_silence_ms,
        partial_interval_ms=base.partial_interval_ms,
        name="probe",
        min_speech_ms=gate_ms,
        merge_window_ms=merge_ms,
        preroll_max_ms=preroll_ms,
    )
    splitter = vad_mod.UtteranceSplitter(engine=vad_mod._VAD, tuning=tuning)

    frame = vad_mod.FRAME
    events: list[tuple[float, float, bool, float, float]] = []
    opened: float | None = None
    cursor = 0.0

    def record(kind: str, audio, at: float) -> None:
        nonlocal opened
        if audio is None or not audio.size:
            return
        if kind == "end":
            begin = opened if opened is not None else at
            # preroll_ms is read here rather than inferred from the audio length.
            # Inferring it as delivered-minus-span gives -0.26s with the pre-roll off,
            # because _trim cuts the trailing zeroed silence and the delivered audio is
            # shorter than the span for reasons that have nothing to do with the front.
            events.append((begin, at, True, len(audio) / SAMPLE_RATE,
                           splitter.preroll_ms / 1000.0))
            opened = None
        else:
            # A partial does not close the utterance, so it must not consume the
            # start. Popping it here -- which an earlier version did -- shifted
            # every later end by one utterance and reported 0 of 15 clauses
            # delivered, which was an artefact of the bookkeeping.
            events.append((opened if opened is not None else at, at, False,
                           len(audio) / SAMPLE_RATE, splitter.preroll_ms / 1000.0))

    for i in range(0, len(pcm) - frame + 1, frame):
        for kind, audio, _ in splitter.push(pcm[i:i + frame]):
            if kind == "start":
                opened = cursor
            else:
                record(kind, audio, cursor)
        cursor += frame / SAMPLE_RATE
    for kind, audio, _ in splitter.flush():
        record(kind, audio, cursor)
    return events


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--wav", default="samples/sparse_music.wav")
    ap.add_argument("--plan", default="samples/sparse_music_plan.json")
    ap.add_argument("--gate-ms", type=int, default=250)
    ap.add_argument("--merge-ms", type=int, default=0)
    ap.add_argument("--preroll-ms", type=int, default=0,
                    help="leading pre-roll cap, applied by the real splitter "
                         "rather than simulated")
    ap.add_argument("--threshold", type=float, default=None,
                    help="override the splitter's speech probability threshold "
                         "for this run only; patches the module global, which is "
                         "read at call time, so no production file changes")
    ap.add_argument("--sweep", default=None,
                    help="comma separated thresholds to compare in one run")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.sweep:
        for value in [float(v) for v in args.sweep.split(",")]:
            print(f"\n=== VAD_THRESHOLD = {value} ===")
            original = vad_mod.VAD_THRESHOLD
            vad_mod.VAD_THRESHOLD = value
            try:
                main_sweep(args, value, only_summary=True)
            finally:
                vad_mod.VAD_THRESHOLD = original
        return
    main_sweep(args, args.threshold, only_summary=args.quiet)


def main_sweep(args, threshold, only_summary: bool) -> None:
    if threshold is not None:
        original = vad_mod.VAD_THRESHOLD
        vad_mod.VAD_THRESHOLD = threshold
    try:
        report(args, only_summary)
    finally:
        if threshold is not None:
            vad_mod.VAD_THRESHOLD = original


def report(args, only_summary: bool) -> None:
    pcm = load(ROOT / args.wav)
    plan = json.loads((ROOT / args.plan).read_text(encoding="utf-8"))
    clauses = plan["clauses"]

    got = spans(pcm, args.gate_ms, args.merge_ms, args.preroll_ms)
    closed = [g for g in got if g[2]]
    tag = f"VAD_THRESHOLD={vad_mod.VAD_THRESHOLD}"
    if args.preroll_ms:
        tag += f"  preroll_cap={args.preroll_ms}ms"
    print(f"  gate {args.gate_ms}ms  merge {args.merge_ms}ms  {tag}")
    print(f"    埋入语句 {len(clauses)} 句（{sum(c['dur'] for c in clauses):.1f}s）"
          f"   检出并关闭 {len(closed)} 段")

    # The effective pre-roll is read off the splitter, not inferred: a real
    # pre-roll adds audio in front of the detected span and leaves the span's own
    # start where it was, so scoring on g[0] cannot see it. An earlier version here
    # reported "partially delivered" for every clause while the delivered audio had
    # grown from 0.60s to 1.30s, and then inferred the length as delivered-minus-span,
    # which reads -0.26s with the feature off because _trim cuts the trailing
    # silence. Read it, do not reconstruct it.
    effective = [(g[0], g[1], g[3], g[4], g[3]) for g in closed]
    heard, partial, missed = [], [], []
    onset_lag = []
    for c in clauses:
        a, b = c["start"], c["end"]
        # start of the audio actually delivered for a candidate segment
        candidates = [e for e in effective if e[1] > a and e[0] - e[3] < b]
        covering = [e for e in candidates if e[0] - e[3] <= a + 1e-6 and e[1] >= b - 1e-6]
        overlapping = [e for e in candidates if e[0] < b and e[1] > a]
        if covering:
            heard.append((c, "完整", covering[0]))
        elif overlapping:
            partial.append((c, "只覆盖一部分", overlapping[0]))
        else:
            missed.append(c)
        begins = [e[0] - e[3] for e in (covering or overlapping)]
        if begins:
            onset_lag.append(min(begins) - a)

    pr_values = sorted(e[3] for e in effective if e[3] > 0)
    if pr_values:
        print(f"    实测预卷 n={len(pr_values)}  p50 {pr_values[len(pr_values) // 2] * 1000:.0f}ms"
              f"  p90 {pr_values[min(len(pr_values) - 1, int(len(pr_values) * 0.9))] * 1000:.0f}ms"
              f"  被夹成0的段 {len(effective) - len(pr_values)}/{len(effective)}")
    else:
        print("    实测预卷：全部为 0（开关关闭，或 gap 全为 0）")

    lost = sum(c["dur"] for c in missed)
    part = sum(c["dur"] for _c, _w, _g in partial)
    total = sum(c["dur"] for c in clauses)
    delivered = sum(g[4] for c, _w, g in heard + partial)
    print(f"    完整 {len(heard)}/{len(clauses)}   部分 {len(partial)}   完全没到 {len(missed)}")
    print(f"    起始延迟 中位 {sorted(onset_lag)[len(onset_lag) // 2]:+.2f}s "
          f"最大 {max(onset_lag):+.2f}s" if onset_lag else "    起始延迟 无")
    print(f"    送出的音频 {delivered:.1f}s / 埋入 {total:.1f}s = {delivered / total * 100:.0f}%")
    print(f"    完全丢失 {lost:.1f}s   部分丢失 {part:.1f}s")

    if only_summary:
        return
    print()
    for title, rows in (("完整送达", heard), ("部分送达", partial)):
        for c, why, g in rows:
            print(f"      {c['start']:>6.1f}s  {c['dur']:.2f}s  {why}  "
                  f"[{g[0]:.1f}-{g[1]:.1f}s 送出 {g[3]:.2f}s]  {c['text'][:36]}")
    for c in missed:
        print(f"      {c['start']:>6.1f}s  {c['dur']:.2f}s  完全没到       {c['text'][:36]}")


if __name__ == "__main__":
    main()