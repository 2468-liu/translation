"""Retrieve fragments of the repository's own notes, so they do not have to be read.

Context is the scarce resource here, and the notes are the wrong shape for it.
REPORT.md has passed a thousand lines; STATE.md and PLAN.md are meant to stay
short, but a session that ends without updating them accumulates facts that only
exist in a transcript nobody can reach. The result has been the same question
decided three times, and a decision reopened because the answer had been written
down somewhere and not found.

So the notes are indexed by section and retrieved by keyword. Nothing here
summarises or judges; it hands back the section that contains the match, with its
heading, so the caller can decide whether it is still true.

Deliberately stdlib-only and no embeddings. The corpus is a few thousand lines of
Markdown in one language, and a substring match over section text answers
"where is this written down" as well as anything heavier would, while remaining
runnable on a machine with nothing installed.

Ranking counts how many distinct query terms a section contains, so a query with
more words prefers the section that matches more of them, and the snippet is
trimmed around the first match rather than dumped whole.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# AGENT.md is excluded: it is read in full every session by design, so indexing it
# would put two copies of the same contract into every retrieval.
INDEXED = ("REPORT.md", "STATE.md", "PLAN.md", "README.md", "samples/INDEX.md")

INDEX_PATH = Path("logs/rag_index.json")

# A heading line: '#', '##', '###' and any number of them.
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
# Chinese has no spaces, so a whitespace split would treat a whole sentence as one
# term and never match anything. Split on whitespace *and* on every CJK character,
# while keeping latin words whole because "audio_ctx" should match as one term.
CJK = re.compile(r"([\u4e00-\u9fff])")


def terms(query: str) -> list[str]:
    out: list[str] = []
    for word in query.split():
        if CJK.search(word):
            out.extend(c for c in word if c.strip())
        else:
            out.append(word.lower())
    return [t for t in out if t]


def sections(path: Path) -> list[tuple[str, str]]:
    """(heading, body) pairs, so a match points at a section rather than a file."""
    out: list[tuple[str, str]] = []
    heading, buf = "(preamble)", []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = HEADING.match(line)
        if m:
            if buf:
                out.append((heading, "\n".join(buf)))
            heading = m.group(2).strip()
            buf = [line]
        else:
            buf.append(line)
    if buf:
        out.append((heading, "\n".join(buf)))
    return out


def build() -> dict:
    index: dict[str, list] = {}
    for name in INDEXED:
        path = Path(name)
        if not path.exists():
            continue
        index[name] = [
            {"heading": h, "text": body, "lower": body.lower()}
            for h, body in sections(path)
        ]
    return index


def score(section_text_lower: str, query_terms: list[str]) -> int:
    """How many distinct query terms this section contains.

    Distinct rather than total, so a section that repeats one word twenty times
    does not outrank a section that matches every word once.
    """
    return sum(1 for t in query_terms if t in section_text_lower)


def snippet(text: str, query_terms: list[str], width: int = 700) -> str:
    low = text.lower()
    pos = -1
    for t in query_terms:
        found = low.find(t)
        if found >= 0 and (pos < 0 or found < pos):
            pos = found
    if pos < 0:
        return text[:width]
    start = max(0, pos - width // 3)
    return ("..." if start else "") + text[start:start + width].rstrip() + \
           ("..." if start + width < len(text) else "")


def load() -> dict:
    if INDEX_PATH.exists():
        try:
            return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return build()


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index", help="rebuild the index (run after editing any note)")
    q = sub.add_parser("query", help="find sections mentioning these words")
    q.add_argument("text", nargs="+")
    q.add_argument("--top", type=int, default=5)
    q.add_argument("--full", action="store_true", help="print the whole section")
    q.add_argument("--file", help="print one file in full instead of searching")
    lst = sub.add_parser("list", help="list indexed files and section counts")
    args = ap.parse_args()

    if args.cmd == "list":
        index = load()
        total = 0
        for name, secs in index.items():
            print(f"  {name:22} {len(secs):4d} sections")
            total += len(secs)
        print(f"  {'total':22} {total:4d} sections")
        return 0

    if args.cmd == "index":
        index = build()
        INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        INDEX_PATH.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        print(f"indexed {len(index)} files, "
              f"{sum(len(v) for v in index.values())} sections -> {INDEX_PATH}")
        return 0

    if args.cmd == "query":
        if args.file:
            p = Path(args.file)
            if not p.exists():
                print(f"no such file: {p}")
                return 1
            print(p.read_text(encoding="utf-8", errors="replace"))
            return 0
        query_terms = terms(" ".join(args.text))
        index = load()
        if not index:
            print("index empty; run: python scripts/rag.py index")
            return 1
        hits = []
        for name, secs in index.items():
            for sec in secs:
                s = score(sec["lower"], query_terms)
                if s:
                    hits.append((s, name, sec))
        if not hits:
            print(f"no section mentions {query_terms}")
            print("try fewer or broader words, or: python scripts/rag.py list")
            return 1
        hits.sort(key=lambda h: -h[0])
        # A section that matches one word weakly can still beat a section that
        # matches the whole question, so the score is shown alongside: a reader
        # deciding whether a note is relevant needs to see how much of the query
        # it actually accounted for.
        print(f"query: {' '.join(query_terms)}  ({len(query_terms)} terms)")
        print(f"{len(hits)} sections match\n")
        for s, name, sec in hits[:args.top]:
            coverage = f"{s}/{len(query_terms)}"
            print(f"--- {name} :: {sec['heading']}   [matched {coverage}]")
            if args.full:
                print(sec["text"])
            else:
                print(snippet(sec["text"], query_terms))
            print()
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())