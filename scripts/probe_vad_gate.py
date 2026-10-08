"""Should a 250-1000ms speech burst be dropped, or merged into the next one?

This is the question #6 actually asks, and it is not the question "what is the
median segment length". The gate at server/vad.py:377 is a DROP, not a delay:
when the utterance closes and ``speech_ms < VAD_MIN_SPEECH_MS`` the audio is
discarded by the unconditional ``_drop()`` that follows. Raising the gate from
250ms to 1000ms therefore does not merge anything -- it permanently discards
every 250-1000ms burst that the pipeline currently translates.

In the existing perf log that is 20 of 106 segments, or 19% of what currently
reaches the translator. Whether that is a quality win or a content loss depends
on what those bursts contain, and that is what this probe measures: it runs the
real Silero VAD over the real movie audio ONCE, records every utterance's
speech_ms, and then reports what each gate would keep or drop. One decode, so
the two arms cannot differ by anything except the gate.

The dropped segments' audio is printed as duration and RMS so a burst can be
told apart from music -- but the judgement that matters ("is this speech?") is
made downstream by feeding the survivors to ASR, which is what #7 will do.

Usage:
    conda activate pytorch
    python scripts/probe_vad_gate.py --wav samples/e_3min.wav --gates 250 500 1000 1500
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import perf, vad as vad_mod  # noqa: E402
from server.config import SAMPLE_RATE  # noqa: E402


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as src:
        assert src.getframerate() == SAMPLE_RATE, f"{path} is {src.getframerate()}Hz, want {SAMPLE_RATE}"
        assert src.getnchannels() == 1, f"{path} is {src.getnchannels()}ch, want mono"
        pcm = np.frombuffer(src.readframes(src.getnframes()), dtype="<i2")
    return pcm.astype(np.float32) / 32768.0


class Recorder:
    """Collects the perf events the splitter emits, instead of writing them out.

    The splitter already announces every closing with its speech_ms and its
    reason (server/vad.py:368). Reconstructing those numbers afterwards is what
    once produced a segment length twice the true median, so this reads the
    emitted value rather than deriving one.
    """

    def __init__(self) -> None:
        self.ends: list[dict] = []
        self.silences: list[float] = []
        self.held: list[float] = []

    def __call__(self, source: str, event: str, **fields) -> None:
        if source != "vad":
            return
        if event == "end":
            self.ends.append({
                "why": fields.get("why"),
                "speech_ms": float(fields.get("speech_ms") or 0.0),
                "silence_ms": float(fields.get("silence_ms") or 0.0),
                "early_partial": int(fields.get("early_partial") or 0),
            })
        elif event == "silence":
            self.silences.append(float(fields.get("ms") or 0.0))
        elif event == "held":
            self.held.append(float(fields.get("speech_ms") or 0.0))


def run_vad(audio: np.ndarray, tuning=None) -> Recorder:
    """One pass of the real splitter, with the real Silero model.

    ``engine.reset()`` first is not housekeeping, it is what makes two passes
    comparable. Silero carries LSTM state and a 64-sample context across frames
    -- that is the point of a streaming detector -- and the splitter deliberately
    does not clear it between utterances. So a second pass in the same process
    starts from the first pass's final state, and this probe measured 32, 35, 38
    and 39 closings on one unchanged file depending on how many passes had run
    before it. After an explicit reset the pass is bit-for-bit reproducible, so
    the reset is the difference between an arm effect and a warm-up artefact.
    """
    rec = Recorder()
    original = perf.emit
    perf.emit = rec  # type: ignore[assignment]
    try:
        engine = vad_mod.load_vad()
        engine.reset()
        splitter = vad_mod.UtteranceSplitter(
            engine=engine,
            tuning=tuning if tuning is not None else vad_mod.whisper_tuning(),
        )
        # One VAD frame per push, then flush. An earlier version pushed ~1.28s
        # batches, and the two arms then produced different utterance counts for
        # the SAME audio (32 against 35) because the batch boundary decided how
        # much was still buffered when flush() ran. A probe whose two arms see
        # different segmentation before the variable under test is applied cannot
        # compare them.
        step = vad_mod.FRAME
        for start in range(0, audio.size, step):
            splitter.push(audio[start:start + step])
        splitter.flush()
    finally:
        perf.emit = original  # type: ignore[assignment]
    return rec


def merge_arm(gate: int, window: int):
    """The proposed tuning: hold short utterances open for a neighbour.

    ``window`` is how much silence may pass first. This is the arm #6 asks for,
    and it is deliberately not the same as "raise the gate": raising the gate
    alone discards the fragments, which is what the drop column below shows.
    """
    base = vad_mod.whisper_tuning()
    return vad_mod.Tuning(
        min_silence_ms=base.min_silence_ms,
        max_utterance_ms=base.max_utterance_ms,
        speech_pad_ms=base.speech_pad_ms,
        early_end_ms=base.early_end_ms,
        soft_cut_speech_ms=base.soft_cut_speech_ms,
        soft_cut_silence_ms=base.soft_cut_silence_ms,
        partial_interval_ms=base.partial_interval_ms,
        name="merge",
        min_speech_ms=gate,
        merge_window_ms=window,
    )


def summarize(ends: list[dict], gate: int) -> dict:
    speech = sorted(e["speech_ms"] for e in ends)
    kept = [e for e in ends if e["speech_ms"] >= gate]
    dropped = [e for e in ends if e["speech_ms"] < gate]
    return {
        "gate_ms": gate,
        "utterances": len(ends),
        "kept": len(kept),
        "dropped": len(dropped),
        "dropped_share": round(len(dropped) / len(ends), 3) if ends else None,
        "kept_speech_s": round(sum(e["speech_ms"] for e in kept) / 1000.0, 1),
        "dropped_speech_s": round(sum(e["speech_ms"] for e in dropped) / 1000.0, 1),
        "median_speech_ms_all": round(statistics.median(speech), 1) if speech else None,
        "median_speech_ms_kept": round(statistics.median([e["speech_ms"] for e in kept]), 1) if kept else None,
        "kept_durations": [round(e["speech_ms"]) for e in kept],
        "dropped_durations": [round(e["speech_ms"]) for e in dropped],
        "why": {w: sum(1 for e in ends if e["why"] == w) for w in sorted({e["why"] for e in ends})},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", default="samples/e_3min.wav")
    ap.add_argument("--gates", default="250,500,1000,1500")
    ap.add_argument("--merge-gate", type=int, default=1000,
                    help="gate for the merge arm; short utterances below it are held")
    ap.add_argument("--merge-window", type=int, default=2000,
                    help="silence allowed before a held fragment closes itself")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    wav = ROOT / args.wav
    if not wav.exists():
        raise SystemExit(f"audio not found: {wav}")
    gates = [int(x) for x in str(args.gates).split(",") if x.strip()]
    audio = load_wav(wav)
    print(f"audio      {args.wav}  {audio.size / SAMPLE_RATE:.1f}s")
    print(f"gate today {vad_mod.VAD_MIN_SPEECH_MS} ms (server/config.py)\n")

    rec = run_vad(audio)
    if not rec.ends:
        raise SystemExit("VAD produced no closings at all; the probe is not measuring anything")
    print(f"utterances {len(rec.ends)}   reasons {summarize(rec.ends, gates[0])['why']}\n")

    rows = [summarize(rec.ends, g) for g in gates]
    header = f"{'gate':>6} {'kept':>5} {'dropped':>8} {'drop%':>6} {'all p50':>8} {'kept p50':>9} {'dropped speech':>15}"
    print("A. raise the gate only (what the splitter does today, at a higher threshold)")
    print(header)
    print("-" * len(header))
    for r in rows:
        print(f"{r['gate_ms']:>6} {r['kept']:>5} {r['dropped']:>8} "
              f"{(r['dropped_share'] or 0) * 100:>5.1f}% "
              f"{str(r['median_speech_ms_all']):>8} {str(r['median_speech_ms_kept']):>9} "
              f"{r['dropped_speech_s']:>13.1f}s")

    base = rows[0]
    print("\nagainst today's gate:")
    for r in rows[1:]:
        extra = r["dropped"] - base["dropped"]
        extra_s = r["dropped_speech_s"] - base["dropped_speech_s"]
        print(f"  gate {r['gate_ms']:>4} ms would additionally discard {extra:>3} utterances"
              f" ({extra_s:>4.1f}s of speech) that are translated today")

    print("\nwhat the extra drops look like (duration ms, sorted):")
    for r in rows[1:]:
        extra = sorted(x for x in r["dropped_durations"] if x < r["gate_ms"] and x >= base["gate_ms"])
        print(f"  gate {r['gate_ms']:>4}: {extra}")

    # B. merge vs drop at the SAME gate. Arm A raises the gate with merging off,
    # which discards the fragments; arm B raises it with merging on, which keeps
    # them. Comparing B against A isolates the hold, which is the only part that
    # is a new idea.
    print(f"\nB. gate {args.merge_gate} ms, merging OFF (discard) then ON (hold)")
    dropped = summarize(run_vad(audio, tuning=merge_arm(args.merge_gate, 0)).ends, args.merge_gate)
    merged = run_vad(audio, tuning=merge_arm(args.merge_gate, args.merge_window))
    m = summarize(merged.ends, args.merge_gate)
    print(f"{'':>4}{'utterances':>12}{'kept':>7}{'dropped':>9}{'speech kept':>13}{'kept p50':>10}{'held':>6}")
    print(f"{'off':>4}{dropped['utterances']:>12}{dropped['kept']:>7}{dropped['dropped']:>9}"
          f"{dropped['kept_speech_s']:>12.1f}s{dropped['median_speech_ms_kept']:>10}{0:>6}")
    print(f"{'on':>4}{m['utterances']:>12}{m['kept']:>7}{m['dropped']:>9}"
          f"{m['kept_speech_s']:>12.1f}s{m['median_speech_ms_kept']:>10}{len(merged.held):>6}")
    print(f"\n   held fragments (ms): {[round(x) for x in merged.held]}")
    print(f"   reasons: {m['why']}")
    print(f"   kept durations: {sorted(round(e['speech_ms']) for e in merged.ends if e['speech_ms'] >= args.merge_gate)}")

    print("\nall utterance durations (ms, sorted, no-merge pass):")
    print("  " + " ".join(str(x) for x in sorted(round(e['speech_ms']) for e in rec.ends)))
    print("\nsilence runs inside utterances, by the splitter's own bins:")
    counts: dict[str, int] = {}
    for ms in rec.silences:
        label = vad_mod._silence_bin(ms)
        counts[label] = counts.get(label, 0) + 1
    print("  " + json.dumps(counts, ensure_ascii=False, sort_keys=True))

    if args.out:
        Path(args.out).write_text(json.dumps({
            "wav": args.wav,
            "raise_gate_only": rows,
            "merge": {**m, "held": merged.held},
            "ends": rec.ends,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
