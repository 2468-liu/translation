"""Monotonic Prefix Freezing (前缀单调锁定) State Machine.

Aligned with Google Live Caption architecture:
1. Stable Prefix (稳定区):
   - Monotonically non-decreasing: Once frozen, words/characters NEVER retract or roll back.
   - Freezing conditions:
     a. Multi-slice confirmation: Longest common prefix across recent 2-3 audio slices.
     b. Stability duration: Survives unchanged for >= 600ms.
2. Hypothesis Suffix (游离区):
   - Absorbs acoustic frame updates for the latest 2-3 characters (tail buffer).
   - Rendered with visual differentiation (subtle italic / floating accent).
"""

from __future__ import annotations

import time
from typing import List, Optional, Tuple


def get_lcp_length(s1: str, s2: str) -> int:
    """Computes the length of the longest common prefix between two strings."""
    min_len = min(len(s1), len(s2))
    for i in range(min_len):
        if s1[i] != s2[i]:
            return i
    return min_len


def is_latin_word_char(ch: str) -> bool:
    """Returns True if character is an ASCII alphanumeric character."""
    return bool(len(ch) == 1 and (("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("0" <= ch <= "9")))


def find_overlap_suffix_prefix(s1: str, s2: str) -> int:
    """Finds the maximum k such that s1[-k:] == s2[:k]."""
    max_k = min(len(s1), len(s2))
    for k in range(max_k, 0, -1):
        if s1.endswith(s2[:k]):
            return k
    return 0


# Punctuation Diversity and Hierarchical Classification
TIER_1_CJK = {"。", "！", "？", "\n"}
TIER_2_CJK = {"，", "、", "；", "："}
CLOSING_PAIRS = {'"', "'", "”", "’", "）", ")", "》", "】", "]", "}"}

LATIN_ABBREVIATIONS = {
    "mr.", "mrs.", "ms.", "dr.", "prof.", "st.", "vs.", "etc.",
    "e.g.", "i.e.", "jr.", "sr.", "co.", "inc.", "ltd.", "corp.",
    "dept.", "u.s.", "u.k.", "no.", "vol.", "gen.", "rep.", "sen.",
    "d.c.", "approx.", "est.", "al.", "min.", "max.",
}


def find_punctuation_boundaries(text: str) -> list[tuple[int, int]]:
    """Scans text and returns a list of (freeze_index, tier) tuples.

    Tier 1: Sentence boundary (full stop, question, exclamation, ellipsis)
    Tier 2: Clause boundary (comma, semicolon, colon, dash, dunhao)
    freeze_index: index in text (1-based slice end) including punctuation, closing quotes, and trailing space.
    """
    if not text:
        return []

    boundaries: list[tuple[int, int]] = []
    n = len(text)
    i = 0

    while i < n:
        ch = text[i]

        # 1. CJK Ellipsis (……)
        if text.startswith("……", i):
            end_idx = i + 2
            while end_idx < n and text[end_idx] == "…":
                end_idx += 1
            while end_idx < n and text[end_idx] in CLOSING_PAIRS:
                end_idx += 1
            while end_idx < n and text[end_idx] == " ":
                end_idx += 1
            boundaries.append((end_idx, 1))
            i = end_idx
            continue

        # 2. CJK Em-dash (——)
        if text.startswith("——", i):
            end_idx = i + 2
            while end_idx < n and text[end_idx] == "—":
                end_idx += 1
            while end_idx < n and text[end_idx] in CLOSING_PAIRS:
                end_idx += 1
            while end_idx < n and text[end_idx] == " ":
                end_idx += 1
            boundaries.append((end_idx, 2))
            i = end_idx
            continue

        # 3. Latin Ellipsis (... or …)
        if text.startswith("...", i) or ch == "…":
            end_idx = i + 1
            while end_idx < n and text[end_idx] in (".", "…"):
                end_idx += 1
            while end_idx < n and text[end_idx] in CLOSING_PAIRS:
                end_idx += 1
            while end_idx < n and text[end_idx] == " ":
                end_idx += 1
            boundaries.append((end_idx, 1))
            i = end_idx
            continue

        # 4. CJK Tier 1
        if ch in TIER_1_CJK:
            end_idx = i + 1
            while end_idx < n and text[end_idx] in TIER_1_CJK:
                end_idx += 1
            while end_idx < n and text[end_idx] in CLOSING_PAIRS:
                end_idx += 1
            while end_idx < n and text[end_idx] == " ":
                end_idx += 1
            boundaries.append((end_idx, 1))
            i = end_idx
            continue

        # 5. CJK Tier 2
        if ch in TIER_2_CJK:
            end_idx = i + 1
            while end_idx < n and text[end_idx] in TIER_2_CJK:
                end_idx += 1
            while end_idx < n and text[end_idx] in CLOSING_PAIRS:
                end_idx += 1
            while end_idx < n and text[end_idx] == " ":
                end_idx += 1
            boundaries.append((end_idx, 2))
            i = end_idx
            continue

        # 6. Latin Exclamation and Question (!, ?)
        if ch in ("!", "?"):
            end_idx = i + 1
            while end_idx < n and text[end_idx] in ("!", "?"):
                end_idx += 1
            while end_idx < n and text[end_idx] in CLOSING_PAIRS:
                end_idx += 1
            while end_idx < n and text[end_idx] == " ":
                end_idx += 1
            boundaries.append((end_idx, 1))
            i = end_idx
            continue

        # 7. Latin Semicolon (;), Comma (,), Em-dash (—)
        if ch in (",", ";", "—"):
            end_idx = i + 1
            while end_idx < n and text[end_idx] in CLOSING_PAIRS:
                end_idx += 1
            while end_idx < n and text[end_idx] == " ":
                end_idx += 1
            boundaries.append((end_idx, 2))
            i = end_idx
            continue

        # 8. Latin Colon (:)
        if ch == ":":
            prev_digit = (i > 0 and text[i - 1].isdigit())
            next_digit = (i + 1 < n and text[i + 1].isdigit())
            is_url = text.startswith("://", i)
            if not (prev_digit and next_digit) and not is_url:
                end_idx = i + 1
                while end_idx < n and text[end_idx] in CLOSING_PAIRS:
                    end_idx += 1
                while end_idx < n and text[end_idx] == " ":
                    end_idx += 1
                boundaries.append((end_idx, 2))
                i = end_idx
                continue
            i += 1
            continue

        # 9. Latin Period (.)
        if ch == ".":
            # Decimal protection: e.g. 3.14, $19.99, v2.5
            prev_digit = (i > 0 and text[i - 1].isdigit())
            next_digit = (i + 1 < n and text[i + 1].isdigit())
            if prev_digit and next_digit:
                i += 1
                continue

            # Abbreviation protection: scan word ending at this period
            w_start = i - 1
            while w_start >= 0 and (text[w_start].isalpha() or text[w_start] == "."):
                w_start -= 1
            word = text[w_start + 1 : i + 1].lower()
            if word in LATIN_ABBREVIATIONS:
                i += 1
                continue

            # Single letter abbreviation like "U. S. A."
            if len(word) == 2 and word[0].isalpha() and word[1] == ".":
                if i + 2 < n and text[i + 1] == " " and text[i + 2].isupper():
                    i += 1
                    continue

            end_idx = i + 1
            while end_idx < n and text[end_idx] in CLOSING_PAIRS:
                end_idx += 1
            while end_idx < n and text[end_idx] == " ":
                end_idx += 1
            boundaries.append((end_idx, 1))
            i = end_idx
            continue

        # 10. Spaced dash: " - "
        if text.startswith(" - ", i):
            end_idx = i + 3
            boundaries.append((end_idx, 2))
            i = end_idx
            continue

        i += 1

    return boundaries


class MonotonicPrefixFreezer:
    """Implements Google Live Caption-style Monotonic Prefix Freezing."""

    def __init__(
        self,
        min_confirm_slices: int = 2,
        stability_timeout_s: float = 0.6,
        tail_margin: int = 2,
    ):
        self.min_confirm_slices = max(1, min_confirm_slices)
        self.stability_timeout_s = max(0.1, float(stability_timeout_s))
        self.tail_margin = max(1, int(tail_margin))

        self.frozen_prefix: str = ""
        self.hypothesis_suffix: str = ""

        # History of recent hypothesis texts and arrival times: [(text, timestamp), ...]
        self._history: list[tuple[str, float]] = []
        # Per-character first-seen timestamps: list of floats matching len(display_text)
        self._char_timestamps: list[float] = []

    def clear(self) -> None:
        """Resets the state machine for a clean start or silence purge."""
        self.frozen_prefix = ""
        self.hypothesis_suffix = ""
        self._history.clear()
        self._char_timestamps.clear()

    def get_display_text(self) -> str:
        """Returns the full combined text (stable prefix + hypothesis suffix)."""
        return self.frozen_prefix + self.hypothesis_suffix

    def update(self, incoming: str, now: Optional[float] = None) -> tuple[str, str]:
        """Ingests an incoming streaming hypothesis and returns (frozen_prefix, hypothesis_suffix).

        Guarantees:
        - `frozen_prefix` is monotonically non-decreasing (never rolls back or shrinks).
        - Hypothesis suffix absorbs streaming modifications on the tail.
        """
        now = time.monotonic() if now is None else float(now)
        cleaned = (incoming or "").strip()
        if not cleaned:
            return self.frozen_prefix, self.hypothesis_suffix




        # 1. Monotonic Prefix Invariant
        # Even if backend shrinks or rewrites tokens, frozen_prefix is IMMUTABLE!
        if self.frozen_prefix:
            if cleaned.startswith(self.frozen_prefix):
                raw_suffix = cleaned[len(self.frozen_prefix):]
                full_text = self.frozen_prefix + raw_suffix
            else:
                overlap_k = find_overlap_suffix_prefix(self.frozen_prefix, cleaned)
                lcp = get_lcp_length(self.frozen_prefix, cleaned)
                if overlap_k > 0:
                    raw_suffix = cleaned[overlap_k:]
                    full_text = self.frozen_prefix + raw_suffix
                elif lcp > 2 and (lcp >= len(self.frozen_prefix) * 0.35 or lcp >= len(cleaned) * 0.35):
                    # Substantial common prefix: same utterance with acoustic tail revision
                    raw_suffix = cleaned[lcp:]
                    full_text = self.frozen_prefix + raw_suffix
                else:
                    # New utterance boundary: completely disjoint tokens with no overlap.
                    # Reset frozen prefix for the new utterance to avoid blocking subsequent speech!
                    self.clear()
                    full_text = cleaned
        else:
            full_text = cleaned

        prev_text = self.get_display_text()
        prev_timestamps = list(self._char_timestamps)

        # 2. Update character stability timestamps
        new_timestamps: list[float] = []
        for i, ch in enumerate(full_text):
            if i < len(prev_text) and ch == prev_text[i] and i < len(prev_timestamps):
                new_timestamps.append(prev_timestamps[i])
            else:
                new_timestamps.append(now)
        self._char_timestamps = new_timestamps

        # Record history
        self._history.append((full_text, now))
        if len(self._history) > 6:
            self._history.pop(0)

        # 3. Determine Candidate Freezing Boundary
        cur_frozen_len = len(self.frozen_prefix)
        # Margin: reserve latest `tail_margin` characters as hypothesis buffer
        max_freezable_by_tail = max(cur_frozen_len, len(full_text) - self.tail_margin)

        # Branch A: Slice confirmation across recent slices
        slice_freeze_len = cur_frozen_len
        if len(self._history) >= self.min_confirm_slices:
            recent_texts = [h[0] for h in self._history[-self.min_confirm_slices:]]
            common_lcp = len(recent_texts[0])
            for t in recent_texts[1:]:
                common_lcp = min(common_lcp, get_lcp_length(recent_texts[0], t))
            slice_freeze_len = min(common_lcp, max_freezable_by_tail)

        # Branch B: Time-based duration stability (>= 600ms)
        time_freeze_len = cur_frozen_len
        for i in range(cur_frozen_len, len(full_text)):
            if (now - self._char_timestamps[i]) >= self.stability_timeout_s:
                time_freeze_len = i + 1
            else:
                break

        # Branch C: Punctuation-based hierarchical freezing (Tier 1 sentence & Tier 2 clause)
        punct_freeze_len = cur_frozen_len
        punct_boundaries = find_punctuation_boundaries(full_text)
        for b_idx, tier in punct_boundaries:
            # Punctuation boundary is valid if text has progressed past it or tail is safe
            if b_idx <= len(full_text) - 1 or b_idx <= max_freezable_by_tail:
                if b_idx > punct_freeze_len:
                    punct_freeze_len = b_idx

        # Candidate new frozen length: strictly monotonically non-decreasing
        new_frozen_len = max(cur_frozen_len, slice_freeze_len, time_freeze_len, punct_freeze_len)

        # 4. Word-boundary protection for Western alphabets
        # Prevent freezing half of an English word prematurely unless it timed out (> 600ms) or hit punctuation boundary
        if (
            new_frozen_len > cur_frozen_len
            and new_frozen_len < len(full_text)
            and is_latin_word_char(full_text[new_frozen_len - 1])
            and is_latin_word_char(full_text[new_frozen_len])
        ):
            # If this cut point was purely from slice confirmation, snap back to last word boundary
            if new_frozen_len > time_freeze_len and new_frozen_len > punct_freeze_len:
                last_space = full_text[:new_frozen_len].rfind(" ")
                if last_space >= cur_frozen_len:
                    new_frozen_len = last_space + 1
                else:
                    new_frozen_len = cur_frozen_len

        # Apply update
        self.frozen_prefix = full_text[:new_frozen_len]
        self.hypothesis_suffix = full_text[new_frozen_len:]
        return self.frozen_prefix, self.hypothesis_suffix

    def commit_final(self, final_text: str = "") -> str:
        """Commits final sentence upon endpoint or pause, returning full finalized text."""
        cleaned = (final_text or "").strip()
        result = cleaned if cleaned else self.get_display_text()
        self.clear()
        return result

    def split_line_segments(self, line: str, line_start_offset: int) -> tuple[str, str]:
        """Splits a wrapped display line into (stable_part, hypothesis_part).

        Args:
            line: The wrapped line text.
            line_start_offset: The character index in the full text where this line begins.

        Returns:
            (stable_text, hypothesis_text)
        """
        if not line:
            return "", ""

        frozen_end = len(self.frozen_prefix)
        line_len = len(line)

        if line_start_offset >= frozen_end:
            # Entire line is in hypothesis zone
            return "", line
        elif line_start_offset + line_len <= frozen_end:
            # Entire line is in frozen stable zone
            return line, ""
        else:
            # Line crosses the boundary
            split_idx = frozen_end - line_start_offset
            return line[:split_idx], line[split_idx:]
