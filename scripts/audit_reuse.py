"""Does the early-closing partial recognise the sentence as well as the full one?

The whole point of change B in ``vad.py`` is that when the speaker pauses, the
partial emitted at 150ms of silence already holds the whole utterance, so the
closing ``end`` event reuses that text instead of running ASR again. That saves
~250ms per sentence -- but only if the two texts actually agree. This script
checks that assumption offline, so the live server keeps its normal load:

    python scripts/audit_reuse.py                       # samples/blog_3min.wav
    python scripts/audit_reuse.py other.wav --limit 10

For every utterance it runs the recogniser twice, once on the audio the early
partial would have carried and once on the audio the ``end`` event would have
carried, then compares the two strings.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.asr import AsrEngine  # noqa: E402
from server.config import SAMPLE_RATE, VAD_MIN_SILENCE_MS  # noqa: E402
from server.vad import EARLY_END_SILENCE_MS, UtteranceSplitter  # noqa: E402

HOP_MS = 100  # what scripts/blog_video_test.py streams


def pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(round(p / 100 * (len(ordered) - 1)))))]


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("wav", nargs="?", default=str(ROOT / "samples" / "blog_3min.wav"))
    ap.add_argument("--limit", type=int, default=0, help="stop after N utterances")
    ap.add_argument("--show", type=int, default=12, help="how many mismatches to print")
    args = ap.parse_args(argv)

    import soundfile as sf

    pcm, rate = sf.read(args.wav, dtype="float32", always_2d=False)
    if pcm.ndim > 1:
        pcm = pcm.mean(axis=1)
    if rate != SAMPLE_RATE:
        raise SystemExit(f"expected {SAMPLE_RATE}Hz, got {rate}Hz")
    print(f"audio {args.wav} {pcm.size / SAMPLE_RATE:.1f}s")

    asr = AsrEngine()
    splitter = UtteranceSplitter()
    hop = int(SAMPLE_RATE * HOP_MS / 1000)

    early_audio: np.ndarray | None = None
    rows: list[dict] = []
    utterances = 0

    def events():
        nonlocal early_audio
        for start in range(0, pcm.size, hop):
            for event in splitter.push(pcm[start : start + hop]):
                kind, audio, flag = event
                if kind == "partial" and flag:
                    early_audio = audio
                elif kind == "end":
                    yield audio, flag, early_audio
                    early_audio = None
        for event in splitter.flush():
            kind, audio, flag = event
            if kind == "end":
                yield audio, flag, early_audio
                early_audio = None

    for end_audio, reuse, early in events():
        utterances += 1
        if reuse and early is not None:
            early_text = asr.transcribe(early, None)[0].strip()
            end_text = asr.transcribe(end_audio, None)[0].strip()
            rows.append(
                {
                    "early": early_text,
                    "end": end_text,
                    "ratio": similarity(early_text, end_text),
                    "prefix": bool(end_text) and end_text.startswith(early_text),
                    # Both buffers are trimmed to the same short release, so the
                    # win is not bytes: it is that the early partial is emitted at
                    # 150ms of silence instead of 400ms. What the trim does remove
                    # is the extra silence the end event used to carry.
                    "trim_ms": max(0.0, (end_audio.size - early.size) / SAMPLE_RATE * 1000.0),
                }
            )
        if args.limit and len(rows) >= args.limit:
            break

    reused = len(rows)
    print(f"utterances closed      {utterances}")
    print(f"reused (early existed) {reused}")
    if not rows:
        print("nothing to compare")
        return 0

    ratios = [r["ratio"] for r in rows]
    identical = len([r for r in rows if r["early"] == r["end"]])
    print(f"identical             {identical}/{reused}")
    print(f"early is prefix of end{len([r for r in rows if r['prefix']]):>3}/{reused}")
    print(
        "similarity            p50=%.3f p90=%.3f min=%.3f"
        % (pct(ratios, 50), pct(ratios, 90), min(ratios))
    )
    print(
        "extra silence trimmed p50=%.0fms max=%.0fms  (emission is %dms earlier anyway)"
        % (
            pct([r["trim_ms"] for r in rows], 50),
            max(r["trim_ms"] for r in rows),
            VAD_MIN_SILENCE_MS - EARLY_END_SILENCE_MS,
        )
    )

    bad = sorted(rows, key=lambda r: r["ratio"])
    if bad and bad[0]["ratio"] < 1.0:
        print(f"\nworst {min(args.show, len(bad))} of {len(bad)} differing:")
        for row in bad[: args.show]:
            print(f"  ratio={row['ratio']:.3f}")
            print(f"    early: {row['early']}")
            print(f"    end  : {row['end']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())