"""Whether a wrong-language decoding translates to roughly the right Chinese.

Character similarity said the wrong-language text shares nothing with the right
one -- median similarity 0.00 at a boundary, against 0.62 elsewhere. That looks
like the answer, but it is a comparison of two different scripts, where no
overlap is expected even when both are describing the same audio. So it cannot
decide the question that matters.

The question is what the viewer sees. They see Chinese, not Japanese, so the
comparison has to happen after translation:

    right   audio -> right text   -> Hy-MT2 -> A
    wrong   audio -> wrong text   -> Hy-MT2 -> B

and then ask how far A and B are apart. If A and B agree, showing the
wrong-language decoding immediately and overwriting it at the segment end costs
nothing the viewer can notice, and there is no leading-partial count to tune.
If they disagree, the viewer is shown something false before it is corrected,
which is worse than a gap, and some partials have to be held back instead.

Scoring is on the Chinese, by character overlap and by content-word overlap.
Character overlap alone would call two translations of the same sentence
dissimilar over phrasing, so content words are counted separately: those carry
the entities, numbers and verbs, which is where a wrong reading would show up.
Substring containment is also reported, because a translation that dropped the
verb but kept the object is not the same failure as one that invented a new
subject.

The right-language pass here uses the language the detector assigned to the
fragment, and the wrong-language pass uses its predecessor's -- the same two
texts probe_boundary_error.py compared, carried through translation so the
comparison lands on the output the viewer actually reads.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
import urllib.request
from pathlib import Path

# Buckets fixed before the numbers exist.
AGREE_BUCKETS = ((0.7, 1.01, "agree >=0.7"), (0.3, 0.7, "related 0.3-0.7"),
                 (-1.0, 0.3, "unrelated <0.3"))

# Words that carry the content of a caption: entities, numbers, and verbs.
# Function words are excluded because every language has them and two unrelated
# sentences still share most of theirs, which would flatten the score.
STOP = set("""的 了 是 在 有 和 就 不 人 都 一 一个 上 也 很 到 说 要 去 你 会 着 没有 看 好 自己 这 那 他 她 它 我们
你们 他们 什么 怎么 这个 那个 可以 因为 但是 所以 如果 就是 还是 一下 一样 现在 已经 觉得 时候 东西 应该 可能
""".split())

HAN = re.compile(r"[一-鿿]")


def content_words(text: str) -> set[str]:
    chars = {c for c in text if c.strip() and c not in STOP and not c.isascii()}
    latin = set(w.lower() for w in re.findall(r"[A-Za-z]{2,}", text))
    digits = set(re.findall(r"\d+", text))
    return chars | latin | digits


def char_sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # Longest common subsequence ratio, via DP on lengths only.
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0]
        for j, cb in enumerate(b, 1):
            cur.append(prev[j - 1] + 1 if ca == cb else max(prev[j], cur[j - 1]))
        prev = cur
    lcs = prev[-1]
    return lcs / max(len(a), len(b))


def score(a: str, b: str) -> dict:
    ca, cb = content_words(a), content_words(b)
    union = ca | cb
    jaccard = len(ca & cb) / len(union) if union else 0.0
    containment = (len(ca & cb) / min(len(ca), len(cb))
                   if ca and cb else 0.0)
    combined = 0.5 * char_sim(a, b) + 0.5 * jaccard
    return {
        "char_sim": round(char_sim(a, b), 3),
        "word_jaccard": round(jaccard, 3),
        "containment": round(containment, 3),
        "combined": round(combined, 3),
        "a_substring_of_b": bool(a) and a in b,
        "b_substring_of_a": bool(b) and b in a,
        "a_has_han": bool(HAN.search(a)),
        "b_has_han": bool(HAN.search(b)),
    }


def translate_batch(texts: list[str], url: str, timeout: float) -> list[str]:
    """One non-streaming request per text. Sequential: the server is one slot."""
    out = []
    for text in texts:
        if not text.strip():
            out.append("")
            continue
        body = json.dumps({
            "model": "hy-mt2",
            "stream": False,
            "temperature": 0.0,
            "max_tokens": 200,
            "messages": [{"role": "user", "content":
                          f"将以下文本翻译为中文，注意只需要输出翻译后的结果，不要额外解释：\n\n{text}"}],
        }).encode()
        req = urllib.request.Request(f"{url}/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = json.loads(r.read())
            out.append((payload["choices"][0]["message"]["content"] or "").strip())
        except Exception as exc:
            print(f"  translate failed: {exc}")
            out.append("")
        del t0
    return out


def bucket_label(v: float) -> str:
    for lo, hi, label in AGREE_BUCKETS:
        if lo <= v < hi:
            return label
    return "?"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--boundary", default="logs/boundary_error.json")
    ap.add_argument("--url", default="http://127.0.0.1:8080")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--all", action="store_true", help="include non-boundary fragments as control")
    ap.add_argument("--out", default="logs/semantic_agreement.json")
    args = ap.parse_args()

    src = Path(args.boundary)
    if not src.exists():
        sys.exit(f"missing {src}; run probe_boundary_error.py first")
    rows = json.loads(src.read_text(encoding="utf-8"))
    rows = [r for r in rows if r["class"] != "empty"]
    if args.all:
        pass
    else:
        rows = [r for r in rows if r["boundary"]]
    if not rows:
        sys.exit("no rows to compare")

    print(f"translating {len(rows)} x 2 = {len(rows) * 2} texts via {args.url}")
    print("  (right-language pass)")
    right_zh = translate_batch([r["correct"] for r in rows], args.url, args.timeout)
    print("  (wrong-language pass)")
    wrong_zh = translate_batch([r["wrong"] for r in rows], args.url, args.timeout)

    for r, a, b in zip(rows, right_zh, wrong_zh):
        r["right_zh"] = a
        r["wrong_zh"] = b
        r["scores"] = score(a, b)
        r["bucket"] = bucket_label(r["scores"]["combined"])
        # A translation that is empty or came back in the source language did not
        # make it to Chinese at all, so it cannot be compared on meaning.
        r["b_valid_zh"] = bool(b.strip()) and bool(HAN.search(b)) and b.strip() != r["wrong"].strip()
        r["a_valid_zh"] = bool(a.strip()) and bool(HAN.search(a)) and a.strip() != r["correct"].strip()

    print(f"\n{'':3} {'true':4} {'used':4} {'combined':>8} {'chars':>6} {'words':>6} "
          f"{'contain':>7}  {'bucket':16} right -> wrong")
    for r in rows:
        s = r["scores"]
        print(f"{r['i']:3} {r['true_lang']:4} {r['used_lang']:4} {s['combined']:8.3f} "
              f"{s['char_sim']:6.2f} {s['word_jaccard']:6.2f} {s['containment']:7.2f}  "
              f"{r['bucket']:16} {r['right_zh'][:22]!r} -> {r['wrong_zh'][:22]!r}")

    print("\n=== distribution over the Chinese ===")
    valid = [r for r in rows if r["a_valid_zh"] and r["b_valid_zh"]]
    invalid_b = [r for r in rows if not r["b_valid_zh"]]
    print(f"comparable pairs {len(valid)}; wrong-language text never reached Chinese {len(invalid_b)}")
    if invalid_b:
        print("   those are excluded from the distribution, not scored as disagreement:")
        for r in invalid_b:
            print(f"     i={r['i']} wrong={r['wrong'][:34]!r} -> {r['wrong_zh'][:34]!r}")

    def report(label: str, subset: list) -> None:
        if not subset:
            print(f"\n{label}: none")
            return
        counts = {}
        for r in subset:
            counts[r["bucket"]] = counts.get(r["bucket"], 0) + 1
        vals = [r["scores"]["combined"] for r in subset]
        print(f"\n{label}  n={len(subset)}")
        print(f"   combined   median {statistics.median(vals):.3f}  "
              f"p25 {sorted(vals)[max(0, len(vals) // 4)]:.3f}  "
              f"min {min(vals):.3f}  max {max(vals):.3f}")
        for _, _, name in AGREE_BUCKETS:
            c = counts.get(name, 0)
            if c:
                print(f"   {name:16} {c:3d}  ({c / len(subset) * 100:.0f}%)  {'#' * c}")

    report("at a language boundary", valid)
    if args.all:
        control = [r for r in rows if not r["boundary"] and r["a_valid_zh"] and r["b_valid_zh"]]
        report("not at a boundary (same language, control)", control)

    if valid:
        ok = sum(1 for r in valid if r["scores"]["combined"] >= 0.3)
        pct = ok / len(valid) * 100
        print(f"\n=== judgement ===")
        print(f"   agree + related: {ok}/{len(valid)} = {pct:.0f}%")
        if pct > 70:
            print("   verdict: show-immediately-then-overwrite holds; no leading-partial count to tune")
        elif pct < 50:
            print("   verdict: it does not hold; wrong-language Chinese is mostly unrelated, "
                  "so partials must be held back")
        else:
            print("   verdict: between; a hybrid is indicated (hold the first partials, "
                  "then show and overwrite)")
    else:
        print("\nno comparable pairs: the wrong-language text never translated, "
              "so the policy question cannot be answered from this run")

    Path(args.out).write_text(json.dumps(rows, indent=1, ensure_ascii=False),
                              encoding="utf-8")
    print(f"\nwritten to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())