"""Clause-level tracking for the caption line, at the application layer.

The comparison belongs here rather than in the renderer, and this module is the reason
that is a design statement and not a preference: `_punct_gate` computed
`introduces_boundary(...)` and then threw the boundary away, because `self.line` is one
flat string and a renderer holding one string cannot be told which part of it is new.

Everything in this file is a pure function over `str` and `list[str]`. None of it
imports tkinter, and none of it needs a window, which is the practical payoff -- the
paint-order tests in `test_caption_paint_order.py` needed a fake canvas because the
logic was welded to the rendering unit, and three of those failures were bugs in the
fake rather than in the code under test.

The renderer half is a separate piece of work and is not here: a single canvas text
item cannot hold separate clauses, so consuming this diff means one item per clause.
This module's job is to make that possible by producing the change record without
knowing anything about canvases.

Alignment is by longest common subsequence rather than by index. Measured on the
translation stream, only 26% and 19% of consecutive fragments are prefix
relationships, so clauses are inserted and removed rather than only appended, and an
index-based comparison would call everything after one insertion changed. The extra
cost is O(n*m) over clause counts of about six, not character counts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The marks that end a clause in the display language. Kept identical to the set the
# caption window uses for its boundary test; they are duplicated rather than imported
# because this module must not depend on the rendering unit.
TERMINAL_PUNCT = "。，、；：！？…—"


def split_clauses(text: str) -> list[str]:
    """Split a line into clauses, each carrying its own terminating mark.

    A trailing fragment with no mark is kept and is what the fallback draws. Dropping it
    would mean the last clause of a line could never be displayed, which is the same
    mistake the boundary test avoided by counting marks rather than comparing sets.
    """
    out: list[str] = []
    cur = ""
    for ch in text:
        cur += ch
        if ch in TERMINAL_PUNCT:
            out.append(cur)
            cur = ""
    if cur.strip():
        out.append(cur)
    return out


def punctuation_count(text: str) -> int:
    """How many terminal marks the text carries, repeats included."""
    return sum(1 for ch in text if ch in TERMINAL_PUNCT)


def introduces_boundary(shown: str, incoming: str) -> bool:
    """True when the incoming text carries more terminal marks than the line does.

    Counts rather than set difference. Set difference measured 13.5 firings a minute
    against a clause rate of 17.7: once the line holds the handful of marks Chinese
    uses, almost nothing new can be introduced as a character. Counting measured 14.2
    against the same rate, so neither reaches the registered 17 -- and the two are not
    interchangeable, which is why both numbers are written down rather than the better
    one.
    """
    return punctuation_count(incoming) > punctuation_count(shown)


def align_clauses(previous: list[str], current: list[str]) -> list[tuple[int, int]]:
    """Match clauses across two versions of a line, by longest common subsequence.

    Returns (previous_index, current_index) pairs in order. Unmatched clauses on either
    side are the insertions and deletions.
    """
    n, m = len(previous), len(current)
    if not n or not m:
        return []
    table = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            table[i][j] = (
                table[i + 1][j + 1] + 1
                if previous[i] == current[j]
                else max(table[i + 1][j], table[i][j + 1])
            )
    pairs: list[tuple[int, int]] = []
    i = j = 0
    while i < n and j < m:
        if previous[i] == current[j]:
            pairs.append((i, j))
            i += 1
            j += 1
        elif table[i + 1][j] >= table[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


@dataclass
class ClauseDiff:
    """What changed between two versions of a line, in terms of clauses."""

    kept: list[tuple[int, int]] = field(default_factory=list)
    added: list[tuple[int, str]] = field(default_factory=list)
    removed: list[tuple[int, str]] = field(default_factory=list)
    changed: list[tuple[int, int, str, str]] = field(default_factory=list)

    @property
    def unchanged_ratio(self) -> float:
        """Share of the current line that the alignment proves was not touched.

        Zero of this is what "only replace what differs" is worth, and it is also the
        number that says whether a per-clause renderer would have had anything to keep.
        """
        total = len(self.kept) + len(self.changed) + len(self.added)
        return len(self.kept) / total if total else 0.0

    @property
    def touches(self) -> list[int]:
        """Indices into the current clause list that a renderer would rebuild.

        Kept clauses are excluded on purpose -- they are what a per-clause renderer
        leaves alone. This is the list the canvas half consumes, and it is the reason
        the comparison can avoid a full redraw.
        """
        return sorted({j for _, j, _, _ in self.changed} | {j for j, _ in self.added})


def diff_clauses(previous: list[str], current: list[str]) -> ClauseDiff:
    """Compare two versions of a line at clause granularity.

    A clause the alignment matched is kept. The clauses left over on both sides are
    paired up in order -- the k-th unmatched previous with the k-th unmatched current --
    and each pair is reported as a rewrite if both sides have one. Anything left over
    on a single side is an insertion or a removal.

    Pairing by order rather than by similarity is deliberate: it needs no threshold to
    tune, and it cannot report the same clause as both changed and removed. The cost is
    that a line which rewrites a clause and inserts another reports both as rewrites
    when one of them was really an insertion.
    """
    diff = ClauseDiff()
    matched_prev: set[int] = set()
    matched_cur: set[int] = set()
    for i, j in align_clauses(previous, current):
        if previous[i] == current[j]:
            diff.kept.append((i, j))
            matched_prev.add(i)
            matched_cur.add(j)

    loose_prev = [i for i in range(len(previous)) if i not in matched_prev]
    loose_cur = [j for j in range(len(current)) if j not in matched_cur]
    paired = min(len(loose_prev), len(loose_cur))
    for k in range(paired):
        i, j = loose_prev[k], loose_cur[k]
        diff.changed.append((i, j, previous[i], current[j]))
    for i in loose_prev[paired:]:
        diff.removed.append((i, previous[i]))
    for j in loose_cur[paired:]:
        diff.added.append((j, current[j]))
    return diff