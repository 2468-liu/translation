"""What the repeat-collapsing functions actually do, decided by running them.

The report claimed that CHAR_RUN_MAX = 3 means three identical characters get
collapsed, and inferred from that that a laugh like "哈哈哈" would become "哈". That
was reasoned, not measured, and the constant name argues the other way -- "max 3"
reads as a cap on how many to keep, not as a trigger. Whether laughter survives,
and whether an emphasised repetition is treated as noise, are properties of these
three functions and nothing else.

So the cases below are written as questions and answered by the code. Cases that
represent genuine repetition in the content -- a refrain, laughter, someone
insisting on a phrase -- are marked, because those are the ones a cleanup pass
would damage if it is wrong, and the difference between them and the accidental
runs that motivated the function is not something to guess at.

This file is expected to fail until the branch carrying these functions is merged.
That is deliberate: a test written against a function that does not exist is a
statement of what should be there, and the failure names the gap.
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

try:
    from server.translate import (
        CHAR_RUN_MAX,
        CLAUSE_RUN_MAX,
        collapse_caption_repeats,
        collapse_translation_repeats,
    )
    HAVE = True
except ImportError as exc:  # pragma: no cover - reported, not silently skipped
    print(f"collapse functions unavailable: {exc}", file=sys.stderr)
    HAVE = False

# (input, note). "content" marks a case where the repetition is the meaning --
# a caption must not clean these up, because cleaning them changes what is said.
CASES = [
    # accidental runs: what the function exists for
    ("哈哈哈哈", "content - laughter, must survive intact"),
    ("哈哈哈哈哈", "content - laughter, longer run"),
    ("我说了三遍，我说了三遍，我说了三遍，我说了三遍", "content - insistence"),
    ("这是歌词，这是歌词，这是歌词", "content - refrain"),
    ("好好好", "content - agreement, 3 chars"),
    # accidental: a generation loop repeating itself
    ("今天天气", "plain, unchanged"),
    ("好的好的", "run of 2, below any threshold"),
    ("。。。。。", "punctuation run"),
    ("啊啊啊啊啊啊啊啊啊啊", "content but 10 long -- tests the cap, not a guess"),
]

# Applied to already-produced translations rather than by running the pipeline
# twice. The question is how many stored captions the pass would alter, and
# feeding it stored text answers that in seconds instead of two runs.
TRANSLATION_CASES = [
    "今天我们来聊聊延迟。",
    "这个方案的成本很高。",
    "他说：「我不同意。」",
    "哈哈哈哈太好笑了",
    "好好好，就按你说的办。",
    "第一点，第二点，第三点。",
    "这个这个这个这个功能",
    "。。。。。",
    "我要说三遍：不要不要不要。",
]


def main() -> int:
    if not HAVE:
        print("functions not importable; run this on the branch that has them")
        return 2

    print(f"constants: CHAR_RUN_MAX={CHAR_RUN_MAX} "
          f"CLAUSE_RUN_MAX={CLAUSE_RUN_MAX}")
    print()
    print("=== collapse_caption_repeats ===")
    for text, note in CASES:
        out = collapse_caption_repeats(text)
        flag = "CHANGED" if out != text else "same   "
        print(f"  {flag}  {text!r}")
        if out != text:
            print(f"          -> {out!r}")
        print(f"          ({note})")

    print()
    print("=== collapse_translation_repeats ===")
    for text in TRANSLATION_CASES:
        out = collapse_translation_repeats(text)
        flag = "CHANGED" if out != text else "same   "
        print(f"  {flag}  {text!r}")
        if out != text:
            print(f"          -> {out!r}")

    changed = [t for t in TRANSLATION_CASES if collapse_translation_repeats(t) != t]
    print()
    print(f"stored translations altered: {len(changed)}/{len(TRANSLATION_CASES)}")
    for t in changed:
        print(f"    {t!r} -> {collapse_translation_repeats(t)!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())