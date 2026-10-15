"""How much speech does #6 actually deliver to ASR, and is it speech?

Two questions, in this order, because the first one has a trap in it.

1. Delivered speech, per arm
---------------------------
probe_vad_gate.py's "speech kept" column sums only the closings at or above the
gate. That is the right question for "what would raising the gate throw away" and
the wrong question for "what does the merge arm deliver". Two corrections:

  * perf.emit("vad", "end") fires *before* the min_speech_ms test, so rec.ends
    lists fragments the splitter then discarded. Today's gate has to be applied or
    the default arm looks lossless when it is quietly throwing fragments away --
    the trap already written down as "vad.end log lines = events the VAD produced".
  * the merge arm has no discard path while merge_window_ms > 0. A held fragment
    that runs out of window is emitted as its own utterance. Filtering it at the
    gate makes the merge look lossy when it is not.

2. Is the extra speech speech
-----------------------------
Delivered seconds going up is not on its own a win. While a fragment is held,
silence_ms is reset to 0 and the closing block is suppressed, so audio the
splitter had classified as the gap between two utterances is now inside an
utterance and counted as speech. That extra time is either the neighbour's real
words -- which is the entire point of #6 -- or a bar of score that rode in because
the gate was open.

An RMS threshold cannot tell those apart, and that has already gone wrong here: a
loud recording reported zero pauses while the pipeline's own probes counted 35. So
the recogniser decides.

Fresh engine per pass. Silero carries LSTM state across utterances by design, so
without the reset the second arm starts from the first arm's final state and the
two arms differ by warm-up as well as by the gate. That is not hypothetical: the
probe once reported 32, 35, 38 and 39 closings on one unchanged file.

Usage:
    python scripts/probe_merge_delivery.py samples/e_3min.wav --gate 1000 --window 2000
    python scripts/probe_merge_delivery.py samples/e_3min.wav --asr-held
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from probe_vad_gate import Recorder, load_wav, merge_arm  # noqa: E402
from server import perf, vad as vad_mod  # noqa: E402


def run_capture(audio: np.ndarray, tuning=None):
    """One pass of the real splitter, keeping the audio of each emitted end.

    Same shape as probe_vad_gate.run_vad -- one VAD frame per push, engine.reset()
    first -- plus the buffers, so a closing can be handed to ASR and judged on its
    content instead of its duration.

    The perf log is NOT the event list, and for the merge arm the difference is not
    a discarded fragment but a missing one:

      * perf.emit("vad", "end") fires before the min_speech_ms test, so a fragment
        the splitter then drops is still logged. Today's gate discards five on
        e_3min: 32 lines, 27 events.
      * worse, in the merge arm the same line is written and then the branch takes
        `elif merge_window_ms > 0` and HOLDS the fragment instead of closing it. No
        end event follows. Every hold is a logged "vad.end" that is not an end --
        13 of them on e_3min.

    So the log cannot be the index into the events. Pairing walks both in order,
    which is sound because each perf line is written immediately before its event
    with nothing in between, and partials write no line at all. Whatever is left
    over at the end is a hold or a discard, and both counts are reported.
    """
    rec = Recorder()
    paired: list[tuple[dict, np.ndarray]] = []
    consumed = 0
    original = perf.emit
    perf.emit = rec  # type: ignore[assignment]
    try:
        engine = vad_mod.load_vad()
        engine.reset()
        splitter = vad_mod.UtteranceSplitter(
            engine=engine,
            tuning=tuning if tuning is not None else vad_mod.whisper_tuning(),
        )

        def drain(events):
            nonlocal consumed
            for kind, buf, _flag in events:
                if kind != "end":
                    continue
                if consumed >= len(rec.ends):
                    raise SystemExit(
                        f"an end event arrived with no perf line to pair it to ({len(rec.ends)} lines)"
                    )
                paired.append((rec.ends[consumed], np.asarray(buf, dtype=np.float32)))
                consumed += 1

        step = vad_mod.FRAME
        for start in range(0, audio.size, step):
            drain(splitter.push(audio[start : start + step]))
        drain(splitter.flush())
    finally:
        perf.emit = original  # type: ignore[assignment]
    return rec, paired, len(rec.ends) - consumed


def delivered(paired, logged: int, held_count: int, held_speech_s: float) -> dict:
    """Speech that reached ASR, from the emitted events rather than the log.

    ``logged`` is how many vad.end lines the pass wrote and ``len(paired)`` is how
    many ends it actually produced; the gap is fragments that were logged as ends
    and then either dropped at the gate or held for a neighbour. Counting the log
    would count those as delivered sentences, which is the whole reason this
    function takes paired events.
    """
    all_ms = [e["speech_ms"] for e, _buf in paired]
    by_why: dict[str, float] = {}
    for e, _buf in paired:
        by_why[e["why"] or "?"] = by_why.get(e["why"] or "?", 0.0) + e["speech_ms"]
    return {
        "delivered": len(paired),
        "logged": logged,
        "not_delivered": logged - len(paired),
        "delivered_s": round(sum(all_ms) / 1000.0, 1),
        "median_ms": round(statistics.median(all_ms), 0) if all_ms else 0.0,
        "p90_ms": round(sorted(all_ms)[int(len(all_ms) * 0.9)], 0) if all_ms else 0.0,
        "held": held_count,
        "held_speech_s": round(held_speech_s, 1),
        "delivered_by_reason_s": {k: round(v / 1000.0, 1) for k, v in sorted(by_why.items())},
    }


def report(a: dict, b: dict, wav: str, seconds: float, gate: int, window: int) -> None:
    print(f"audio            {wav}  {seconds:.1f}s")
    print(f"today            gate {vad_mod.VAD_MIN_SPEECH_MS}ms, merge window {vad_mod.VAD_MERGE_WINDOW_MS}ms")
    print(f"#6               gate {gate}ms, merge window {window}ms")
    print("\nCounted from the emitted events, not from vad.end log lines. The two differ:")
    print("  today's gate discards fragments after logging them; the merge arm logs an 'end'")
    print("  and then holds the fragment instead of closing it, so a hold is a logged end")
    print("  that never happened. Either way the log over-counts.\n")

    head = f"{'':10}{'日志行':>7}{'未送达':>8}{'送达':>6}{'送达语音':>11}{'中位':>8}{'p90':>8}{'挂起':>6}"
    print(head)
    print("-" * len(head))
    print(f"{'A today':10}{a['logged']:>7}{a['not_delivered']:>8}{a['delivered']:>6}{a['delivered_s']:>10.1f}s"
          f"{a['median_ms']:>8.0f}{a['p90_ms']:>8.0f}{a['held']:>6}")
    print(f"{'B #6':10}{b['logged']:>7}{b['not_delivered']:>8}{b['delivered']:>6}{b['delivered_s']:>10.1f}s"
          f"{b['median_ms']:>8.0f}{b['p90_ms']:>8.0f}{b['held']:>6}")
    print(f"{'B - A':10}{b['logged']-a['logged']:>+7}{b['not_delivered']-a['not_delivered']:>+8}"
          f"{b['delivered']-a['delivered']:>+6}{b['delivered_s']-a['delivered_s']:>+10.1f}s"
          f"{b['median_ms']-a['median_ms']:>+8.0f}{b['p90_ms']-a['p90_ms']:>+8.0f}{b['held']-a['held']:>+6}")

    print("\ndelivered speech by closing reason (seconds):")
    for why in sorted(set(a["delivered_by_reason_s"]) | set(b["delivered_by_reason_s"])):
        x = a["delivered_by_reason_s"].get(why)
        y = b["delivered_by_reason_s"].get(why)
        print(f"  {why:<16} A {('%.1f' % x) if x is not None else '-':>6}"
              f"   B {('%.1f' % y) if y is not None else '-':>6}")
    print(f"\nheld for a neighbour: A {a['held']} ({a['held_speech_s']}s)   B {b['held']} ({b['held_speech_s']}s)")
    if b["not_delivered"]:
        print(f"  B's {b['not_delivered']} undelivered closings are the holds: each wrote a vad.end"
              " line and then waited instead of closing")


def asr_report(paired, gate: int) -> None:
    from server.asr import AsrEngine

    engine = AsrEngine()
    print(f"\nwhat the recogniser makes of each delivered closing (#6 arm, gate {gate}ms)\n")
    rows = []
    for end, buf in paired:
        # None, not "auto": faster-whisper rejects the string outright, and the
        # pipeline's "auto" means exactly this -- no language argument.
        text, _detected, _p = engine.transcribe(buf, None)
        rows.append((end["why"] or "?", end["speech_ms"], text.strip()))
    width = max((len(t) for _w, _ms, t in rows if t), default=10)
    for why, ms, text in sorted(rows, key=lambda r: r[1]):
        flag = "" if text else "   <-- nothing"
        print(f"  {why:<14}{ms:>7.0f}ms  {text:<{width}}{flag}")
    empty = sum(1 for _w, _ms, t in rows if not t)
    print(f"\n  {len(rows)} delivered closings, {empty} produced no text at all")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--gate", type=int, default=1000)
    ap.add_argument("--window", type=int, default=2000)
    ap.add_argument("--asr-held", action="store_true",
                    help="also run the recogniser on every closing of the #6 arm")
    args = ap.parse_args()

    wav = ROOT / args.wav
    if not wav.exists():
        raise SystemExit(f"audio not found: {wav}")
    audio = load_wav(wav)

    default_rec, default_paired, default_gap = run_capture(audio)
    merged_rec, merged_paired, merged_gap = run_capture(
        audio, tuning=merge_arm(args.gate, args.window)
    )

    if not default_paired or not merged_paired:
        raise SystemExit("an arm delivered no closings; refusing to report a comparison")

    def summarise(rec, paired, gap):
        return delivered(paired, len(rec.ends), len(rec.held), sum(rec.held) / 1000.0)

    report(
        summarise(default_rec, default_paired, default_gap),
        summarise(merged_rec, merged_paired, merged_gap),
        args.wav, audio.size / 16000.0, args.gate, args.window,
    )
    if args.asr_held:
        asr_report(merged_paired, args.gate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
