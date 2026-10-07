"""How many stored translations would the caption-side collapse actually change.

The report reasoned that the caption-side collapsing is new in this branch and
that it ought to be measured before merging. It is new -- the caption pass did not
exist before, only the translation-side one did -- so the question is how much it
touches real output. Nine hand-picked sentences cannot answer that; the stored
translations from every past replay can.

Both functions are run over the same corpus. The old side is what main had, which
is collapse_translation_repeats as it stands there; the new side adds the caption
pass on top. Every string that differs is printed, because a summary count alone
cannot distinguish "collapsed a generation loop" from "ate a line of a song".

Corpus: every blog_3min_test*.json under logs/, both the Chinese sent to the
window and any source text recorded beside it. Historical logs live in the repo's
working copy, not in version control, so this reads what is on disk.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

sys.path.insert(0, ".")

from server.translate import collapse_caption_repeats, collapse_translation_repeats


def corpus() -> list[tuple[str, str]]:
    """Every stored string, tagged with where it came from."""
    out: list[tuple[str, str]] = []
    for path in sorted(pathlib.Path("logs").rglob("blog_3min_test*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for sentence in data.get("sentences", []):
            for key in ("text", "zh", "translation", "final", "src"):
                value = sentence.get(key)
                if isinstance(value, str) and value.strip():
                    out.append((value, f"{path.name}:{key}"))
        for event in data.get("events", []):
            if isinstance(event, dict):
                for key in ("text", "zh", "translation"):
                    value = event.get(key)
                    if isinstance(value, str) and value.strip():
                        out.append((value, f"{path.name}:events.{key}"))
    return out


def main() -> int:
    strings = corpus()
    print(f"corpus: {len(strings)} stored strings from the replay logs")
    if not strings:
        print("no stored translations found; run a replay first")
        return 2

    # What main already did, before this branch.
    old_changed = [(t, src) for t, src in strings
                   if collapse_translation_repeats(t) != t]
    # What this branch does: the translation pass and then the caption pass.
    def new_fn(t: str) -> str:
        return collapse_caption_repeats(collapse_translation_repeats(t))
    new_changed = [(t, src) for t, src in strings if new_fn(t) != t]

    print()
    print(f"old (translation pass only): {len(old_changed)} altered")
    print(f"new (translation + caption):  {len(new_changed)} altered")
    print(f"net additional:               {len(new_changed) - len(old_changed)}")
    print()

    extra = [t for t, _ in new_changed if t not in {x for x, _ in old_changed}]
    print(f"=== altered by the caption pass that the translation pass left alone: "
          f"{len(extra)} ===")
    for text in extra:
        print(f"  {text!r}")
        print(f"      -> {new_fn(text)!r}")

    same_both = [t for t, _ in old_changed if t not in {x for x, _ in new_changed}]
    if same_both:
        print(f"\naltered by the translation pass but not the caption pass: "
              f"{len(same_both)}")
        for text in same_both[:10]:
            print(f"  {text!r} -> {collapse_translation_repeats(text)!r}")

    # A refusal check, not a pass rate: collapsing a line of a song or a shouted
    # phrase is a different kind of damage from folding a generation loop, and the
    # counts above cannot tell them apart.
    print("\n=== cases that are content, not noise ===")
    for probe in ("歌词：啦啦啦啦啦啦啦",
                  "不要不要不要不要",
                  "我说了四遍四遍四遍四遍",
                  "。。。。。",
                  "哈哈哈哈哈哈",
                  "让我再想想，让我想想让我想"):
        after = new_fn(probe)
        mark = "ALTERED" if after != probe else "same"
        print(f"  {mark:8} {probe!r} -> {after!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())