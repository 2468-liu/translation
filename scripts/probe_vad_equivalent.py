"""Did #6 change anything on the shipped defaults? It must not have.

The merge feature is off unless TRANSLATION_VAD_MERGE_WINDOW_MS is set, and this
checks that claim at the level that matters: the set of utterances the splitter
emits, not just the count. A change in the gate's plumbing, in `_holding`, or in
`flush()` could move a boundary while still producing the same number of
segments, so the comparison is per-fragment (speech_ms, why, start offset).

Usage:
    conda activate pytorch
    python scripts/probe_vad_equivalent.py --wav samples/e_3min.wav
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.probe_vad_gate import Recorder, load_wav, run_vad  # noqa: E402
from server import vad as vad_mod  # noqa: E402


def fragment_key(rec: Recorder) -> list[tuple]:
    """What the downstream pipeline would receive, one entry per closing.

    Offset is reconstructed from the cumulative silence and speech the splitter
    reports, so an identical key means an identical boundary -- not merely an
    identical duration.
    """
    out = []
    for e in rec.ends:
        out.append((round(e["speech_ms"]), e["why"], e["early_partial"]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", default="samples/e_3min.wav")
    args = ap.parse_args()

    wav = ROOT / args.wav
    audio = load_wav(wav)

    print(f"wav {args.wav}")
    print(f"TRANSLATION_VAD_MERGE_WINDOW_MS in effect: {vad_mod.VAD_MERGE_WINDOW_MS}")
    print(f"TRANSLATION_VAD_MIN_SPEECH_MS in effect:   {vad_mod.VAD_MIN_SPEECH_MS}")
    print(f"whisper tuning: min_speech={vad_mod.whisper_tuning().min_speech_ms}"
          f" merge_window={vad_mod.whisper_tuning().merge_window_ms}")

    rec = run_vad(audio)
    frags = fragment_key(rec)
    held = len(rec.held)
    print(f"\nfragments emitted: {len(frags)}   held events: {held}")

    # The invariants a default run must satisfy. Each one is something the merge
    # work could plausibly have broken while still passing its own tests.
    bad = []
    if held != 0:
        bad.append(f"merge events on a default run ({held}) -- the window is supposed to be 0")
    if vad_mod.whisper_tuning().merge_window_ms != 0:
        bad.append("default merge window is not 0")
    reasons = sorted({f[1] for f in frags})
    if "held_timeout" in reasons:
        bad.append("a held_timeout reached the default path")

    print(f"reasons: {reasons}")
    print(f"durations: {sorted(f[0] for f in frags)}")

    if bad:
        print("\nFAIL")
        for b in bad:
            print(f"  - {b}")
        return 1
    print("\nOK: defaults emit no merge events and no held_timeout")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
