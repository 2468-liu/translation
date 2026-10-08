r"""Report translation-quality failures in a replay log.

Latency metrics answer whether the caption arrives quickly. They say nothing
about whether what arrives is a translation. On the audio-native path the model
reads the waveform itself, so three failure modes showed up that the whisper
pipeline could not produce, and none of them move a timing number:

  untranslated  the model fell back to transcribing the English
  looping        it repeated a phrase until the fragment ended
  hallucinated   it invented content that was not spoken

Run after a replay:

    python scripts/audit_quality.py audio1 audio2 audio3
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.config import AUDIO_CHINESE_SHARE_MIN  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _resolve(stem: str, ext: str, live_name: str, name: str) -> Path:
    """Find a run's log, archived or still pending.

    A replay's logs are only renamed once the next one starts, so the most recent
    run is still sitting under the un-suffixed name. Reading it here means the
    last run of a sweep does not have to be waited on -- and without this, every
    tool reports the newest run as missing.

    Lives here rather than in probe_silence because that module imports from this
    one, and a resolver both need would otherwise be an import cycle.
    """
    archived = ROOT / "logs" / f"{stem}.{name}{ext}"
    if archived.exists():
        return archived
    pending = ROOT / "logs" / "run.pending.json"
    live = ROOT / "logs" / live_name
    if live.exists() and pending.exists():
        try:
            recorded = json.loads(pending.read_text(encoding="utf-8-sig")).get("name")
        except (json.JSONDecodeError, OSError):
            recorded = None
        if recorded == name:
            return live
    return archived

# A phrase that shows up this many times, separated by a comma, is a loop and
# not a sentence. Two is the threshold: "湖畔，湖岸" is legitimate contrast, the
# same pair three times is the model stuck.
LOOP_REPEAT = 3
LOOP_SEP = re.compile(r"[，,、]")

_CJK = re.compile(r"[\u4e00-\u9fff]")


def fragments(events: list[dict]) -> list[dict]:
    """Rebuild each fragment's finished text from the streamed packets."""
    out: list[dict] = []
    for event in events:
        if event.get("type") != "translation":
            continue
        text = str(event.get("text") or "")
        wall = float(event["wall"])
        if out and text.startswith(out[-1]["last"] or "") and wall - out[-1]["end"] < 3:
            out[-1]["last"] = text
            out[-1]["end"] = wall
        else:
            out.append({"start": wall, "end": wall, "last": text, "at": event.get("audio_sec")})
    return out


def cjk_ratio(text: str) -> float:
    """Share of letters that are Chinese -- the guard's own measure.

    Was a separate definition here, and the two drifted the way they did before:
    this one counted non-whitespace characters, the guard counts letters. A
    correct translation quoting an English title then scored below 0.5 here and
    was reported as untranslated while the guard let it through -- the audit
    contradicting the thing it audits.
    """
    letters = [c for c in (text or "") if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if _CJK.match(c)) / len(letters)


def find_loop(text: str) -> str | None:
    """Return the phrase that repeats, or None.

    Compared clause by clause rather than by scanning windows: the failure is
    always a whole clause copied verbatim, and comparing clauses makes that
    visible without a substring search.
    """
    parts = [p.strip() for p in LOOP_SEP.split(text) if p.strip()]
    if len(parts) < LOOP_REPEAT:
        return None
    for size in (1, 2):
        for i in range(len(parts) - LOOP_REPEAT * size + 1):
            head = parts[i : i + size]
            tail = parts[i + size : i + LOOP_REPEAT * size]
            if head and tail == head * (LOOP_REPEAT - 1):
                return "".join(head)
    return None


def check(name: str) -> dict | None:
    # Through the shared resolver, so the most recent run is readable while its
    # logs are still un-suffixed. Otherwise the newest run always looks missing
    # and the audit appears to have nothing new to say.
    path = _resolve("blog_3min_test", ".json", "blog_3min_test.json", name)
    if not path.exists():
        return None
    frags = fragments(json.loads(path.read_text(encoding="utf-8"))["events"])
    rows = []
    for frag in frags:
        text = frag["last"]
        loop = find_loop(text)
        if loop:
            rows.append(("loop", frag["at"], text, loop))
        elif cjk_ratio(text) < AUDIO_CHINESE_SHARE_MIN:
            rows.append(("untranslated", frag["at"], text, f"cjk {cjk_ratio(text):.0%}"))
    return {"run": name, "frags": frags, "rows": rows}


def main(argv: list[str]) -> int:
    names = argv or ["audio1", "audio2"]
    print(f"{'run':10} {'frags':>6} {'bad':>5} {'loop':>5} {'untr':>5}  {'rate':>6}")
    for name in names:
        report = check(name)
        if report is None:
            print(f"{name:10} missing")
            continue
        rows = report["rows"]
        loops = sum(1 for r in rows if r[0] == "loop")
        untr = sum(1 for r in rows if r[0] == "untranslated")
        total = len(report["frags"])
        rate = (loops + untr) / total * 100 if total else 0.0
        print(
            f"{name:10} {total:6} {loops + untr:5} {loops:5} {untr:5}  {rate:5.1f}%"
        )
        for kind, at, text, why in rows:
            at_s = f"{float(at):6.1f}s" if isinstance(at, (int, float)) else "     -"
            print(f"    {kind:12} {at_s}  {text[:38]}")
            print(f"    {'':12} {'':7}  -> {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
