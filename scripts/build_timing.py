"""Rebuild a sample's timeline so its language provenance is recorded, not assumed.

Every existing timeline in samples/ was produced by an inline command that forced
language='en'. For the two English clips that happens to be right. For the others
it is fabrication: the meeting recording's 165 sentences contain zero Japanese,
Korean, Chinese, Cyrillic or Arabic characters, and E-R's closing Russian line was
rendered as the English transliteration "Shul shul".

That matters beyond tidiness, because these files are the denominator. A replay
scores coverage against the timeline, so a timeline that disagrees with the audio
produces a coverage figure that measures the timeline rather than the pipeline.

The fix is per-segment rather than per-clip. transcribe(language=None) detects
once, at the opening, then forces that language over everything after -- running
it on the Japanese-heavy clip produced fluent-looking English hallucinations such
as "How do you speak Japanese?" which is not what the audio says. Passing each
VAD segment separately puts one language in front of the detector each time.

Every row records where its language came from, because a coverage figure computed
per language is only meaningful if you can tell a detection from an annotation.
src is "auto" here; a human label would be "manual", and mixing the two in one
field is how a later reader ends up treating a 0.34-confidence guess as ground
truth.

This writes nothing until the result has been validated: numbering, ordering,
monotonicity, containment in the audio, and the languages the clip is supposed to
contain. A run that would produce an all-English timeline for a multilingual clip
fails instead of overwriting the file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import wave

DEFAULT_MODEL = "models/faster-whisper-large-v3-turbo"


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        channels = w.getnchannels()
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    if channels == 2:
        raw = raw.reshape(-1, channels)[:, 0]
    return raw.astype(np.float32) / 32768.0, rate


def build(audio: np.ndarray, rate: int, model) -> list[dict]:
    # Pass one uses a forced language on purpose, and only to find where speech
    # is. Reading text off it would be exactly the mistake being fixed.
    segments, _ = model.transcribe(
        audio, language="en", beam_size=1, vad_filter=True,
        condition_on_previous_text=False, temperature=0.0,
        without_timestamps=False)
    segments = [s for s in segments if s.text.strip()]

    rows: list[dict] = []
    for s in segments:
        window = audio[int(s.start * rate):int(s.end * rate)]
        if len(window) < rate:      # under a second: nothing to detect on
            continue
        pieces, info = model.transcribe(
            window, language=None, beam_size=1, vad_filter=False,
            condition_on_previous_text=False, temperature=0.0,
            without_timestamps=False)
        text = "".join(p.text for p in pieces).strip()
        if not text:
            continue
        rows.append({
            "start": round(s.start, 2),
            "end": round(s.end, 2),
            "lang": info.language,
            "src": "auto",
            "p": round(float(info.language_probability), 3),
            "text": text,
        })
    for i, row in enumerate(rows):
        row["i"] = i
    return rows


def covered_span(rows: list[dict]) -> float:
    """Union of the segment intervals, not their sum.

    Segments come from a VAD pass and do not overlap in practice, but summing
    would silently over-count if a future detector emitted overlaps, and the
    number's whole purpose is to be a trustworthy denominator.
    """
    if not rows:
        return 0.0
    ordered = sorted((r["start"], r["end"]) for r in rows)
    total = 0.0
    cur_start, cur_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= cur_end:
            cur_end = max(cur_end, end)
        else:
            total += cur_end - cur_start
            cur_start, cur_end = start, end
    return total + (cur_end - cur_start)


def validate(rows: list[dict], duration: float, expect: list[str]) -> list[str]:
    """Everything wrong with this output, as text. Empty means it may be written."""
    problems: list[str] = []
    if not rows:
        return ["no segments produced"]

    for i, r in enumerate(rows):
        if r["i"] != i:
            problems.append(f"row {i} numbered {r['i']}")
        if r["end"] <= r["start"]:
            problems.append(f"row {i} end {r['end']} <= start {r['start']}")
        if r["end"] > duration + 0.5:
            problems.append(f"row {i} ends at {r['end']}, past the audio at {duration:.1f}s")
        if i and r["start"] < rows[i - 1]["start"]:
            problems.append(f"row {i} starts at {r['start']}, before the previous "
                            f"{rows[i - 1]['start']}")
        if not r["text"].strip():
            problems.append(f"row {i} has empty text")
        if r["src"] not in ("auto", "manual"):
            problems.append(f"row {i} has src={r['src']!r}")

    # The failure this whole exercise exists to catch: a timeline that claims one
    # language for audio that is not in one language. Forced decoding produces a
    # clean, plausible, entirely wrong file, so it has to fail loudly here rather
    # than be discovered when a coverage number comes out odd.
    #
    # The rule is symmetric, and both halves are needed. A blanket "at least two
    # languages" check refuses to write a genuinely monolingual clip -- it rejected
    # e_3min, which is English throughout -- and a one-way check cannot tell that
    # refusal from a real failure later. So a monolingual expectation demands an
    # exact match, and a multilingual one demands at least two languages with every
    # expected language present.
    langs = {r["lang"] for r in rows}
    if len(expect) <= 1:
        if langs != set(expect):
            problems.append(f"expected exactly {sorted(expect)}, got {sorted(langs)}")
    else:
        if len(langs) < 2:
            problems.append(f"only one language in the result ({sorted(langs)}) but "
                            f"{len(expect)} were expected -- per-segment detection "
                            f"appears not to have run")
        for want in expect:
            if want not in langs:
                problems.append(f"expected language {want!r} absent; got {sorted(langs)}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--expect", default="",
                    help="comma separated languages that must appear")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    wav, out = Path(args.wav), Path(args.out)
    if not wav.exists():
        print(f"missing {wav}")
        return 1
    expect = [x for x in args.expect.split(",") if x]

    from faster_whisper import WhisperModel
    model = WhisperModel(args.model, device=args.device,
                         compute_type="float16", cpu_threads=4)
    audio, rate = read_wav(wav)
    duration = len(audio) / rate
    rows = build(audio, rate, model)

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["lang"]] = counts.get(r["lang"], 0) + 1
    print(f"{wav.name}  {duration:.1f}s  {len(rows)} segments")
    print("  languages: " + ", ".join(f"{k}={v}" for k, v in
                                       sorted(counts.items(), key=lambda kv: -kv[1])))

    span = covered_span(rows)

    problems = validate(rows, duration, expect)
    if problems:
        print("\n  REFUSING TO WRITE:")
        for p in problems:
            print(f"    - {p}")
        return 1

    print("  validation ok")
    print(f"  covered_span {span:.1f}s of {duration:.1f}s ({span / duration * 100:.0f}%)")
    if args.dry_run:
        print("  dry run, nothing written")
        return 0

    # Written only after validation, then read back and compared, because a write
    # that reports success and leaves the old content is the failure mode this
    # project keeps hitting.
    #
    # The header is deliberately additive and lives outside the list. Coverage is
    # not redefined -- it stays a count over segments -- but the denominator
    # becomes visible: e_3min opens with 31.5s of humming over a music bed that
    # VAD judged non-speech, and without this those seconds read as "not
    # translated" when there was nothing there to translate. Both numbers sit side
    # by side and the reader decides, because deciding is a judgement about the
    # clip, not something a tool can do.
    payload = {
        "audio_total": round(duration, 2),
        "covered_span": round(span, 2),
        "segments": rows,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    back = json.loads(out.read_text(encoding="utf-8"))
    if back != payload:
        print(f"  WRITE MISMATCH: {out} does not match what was built")
        return 1
    print(f"  written and verified: {out} ({out.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())