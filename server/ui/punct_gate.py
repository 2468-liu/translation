"""Punctuation block gating, text similarity calculation, and Chromium-aligned LCP monotonic rolling window."""

from __future__ import annotations

import time
from typing import Optional

from ..caption_window import (
    CLAUSE_MAX_MS,
    _collapse_repeats,
    _lcs_len,
    classify_caption,
    introduces_boundary,
    punctuation_marks,
    step_caption,
)

DIFF_SIMILARITY_THRESHOLD = 0.75


def get_lcp_length(s1: str, s2: str) -> int:
    """Return length of longest common prefix between two strings."""
    n = min(len(s1), len(s2))
    i = 0
    while i < n and s1[i] == s2[i]:
        i += 1
    return i


def calculate_text_similarity(left: str, right: str) -> float:
    """Calculate skeletal similarity between two strings, ignoring trailing punctuation."""
    c_clean = (left or "").rstrip(".,?!:;，。？！：；… ")
    n_clean = (right or "").rstrip(".,?!:;，。？！：；… ")
    if not c_clean and not n_clean:
        return 1.0
    if not c_clean or not n_clean:
        return 0.0
    if c_clean == n_clean:
        return 1.0
    # Common prefix fast-path
    if n_clean.startswith(c_clean):
        return len(c_clean) / len(n_clean)
    if c_clean.startswith(n_clean):
        return len(n_clean) / len(c_clean)

    lcs = _lcs_len(c_clean, n_clean)
    total = len(c_clean) + len(n_clean)
    return (2.0 * lcs) / total if total > 0 else 0.0


def decide_final_action(current_line: str, final_cleaned: str) -> str:
    """Decide whether a final commit should diff-patch or overwrite.

    Returns:
        "same": Text is identical, no redraw needed.
        "diff": High similarity (>=0.75), apply smooth diff patch.
        "overwrite": Low similarity (<0.75), apply full atomic overwrite.
    """
    if final_cleaned == current_line:
        return "same"
    if not current_line:
        return "overwrite"
    sim = calculate_text_similarity(current_line, final_cleaned)
    if sim >= DIFF_SIMILARITY_THRESHOLD:
        return "diff"
    return "overwrite"


def resolve_streaming_monotonic(current: str, incoming: str) -> tuple[str, str]:
    """Resolve streaming partial updates with 3-tier LCP diff classification (Anti-Flicker).

    Rules:
    1. If incoming == current: return ("drop", current) - zero redraw
    2. Tier 1: Pure Forward Append (LCP = 100% of current)
       `incoming.startswith(current)`: keep existing text 100% frozen, append only to tail -> ("grow", incoming)
    3. Transient contraction protection:
       `current.startswith(incoming)`: beam-search pruned tail, hold current on screen -> ("drop", current)
    4. Tier 2: Tail Diff Patch (LCP covers >= 60% of current or incoming)
       Keep the verified prefix locked, replace only tail words -> ("tail_update", incoming)
    5. Tier 3: Structural Rerank (LCP < 60%)
       Sentence restructuring, execute clean atomic swap -> ("swap", incoming)
    """
    if incoming == current:
        return "drop", current
    if not current:
        return "grow", incoming
    if incoming.startswith(current):
        return "grow", incoming
    if current.startswith(incoming):
        # Anti-flicker: never shrink rendered text during streaming
        return "drop", current

    lcp = get_lcp_length(current, incoming)
    if lcp >= 3 and (lcp >= len(current) * 0.60 or lcp >= len(incoming) * 0.60):
        return "tail_update", incoming

    return "swap", incoming


class ChromiumRollingBuffer:
    """Two-line rolling buffer aligned with Chromium Live Caption architecture.

    Line 1 (Anchor): Committed static text from the previous sentence/clause.
    Line 2 (Active): Streaming text being actively transcribed/translated.
    """

    def __init__(self):
        self.anchor_line: str = ""
        self.active_line: str = ""

    def clear(self) -> None:
        self.anchor_line = ""
        self.active_line = ""

    def update_streaming(self, incoming: str) -> tuple[str, str]:
        """Update active streaming line using LCP monotonic resolution.

        Returns (action, full_display_text).
        """
        action, resolved = resolve_streaming_monotonic(self.active_line, incoming)
        if action != "drop":
            self.active_line = resolved
        return action, self.get_composite_text()

    def commit(self, final_text: str = "") -> str:
        """Commit the active sentence/clause and roll.

        Active text rolls to anchor_line; active_line is reset to receive the next sentence.
        """
        cleaned = (final_text or self.active_line).strip()
        if cleaned:
            self.anchor_line = cleaned
            self.active_line = ""
        return self.get_composite_text()

    def get_composite_text(self) -> str:
        """Return combined two-line representation."""
        if self.anchor_line and self.active_line:
            return f"{self.anchor_line}\n{self.active_line}"
        return self.active_line or self.anchor_line

    def get_lines_for_render(self) -> list[tuple[str, bool]]:
        """Return list of (line_text, is_anchor) for two-line drawing."""
        lines = []
        if self.anchor_line:
            lines.append((self.anchor_line, True))
        if self.active_line:
            lines.append((self.active_line, False))
        return lines


class CaptionDisplayBuffer:
    """Zero-delay visual persistence buffer using space over time.

    Rule 1: Inline concatenation if incoming sentence arrives within min_dwell_sec (<0.85s)
            and total chars <= max_inline_chars (24).
    Rule 2: Two-line rolling buffer if inline cannot fit: active rolls to anchor, new becomes active.
    Rule 3: Auto-clear anchor line if no new sentence for 4.0s.
    """

    def __init__(self, max_inline_chars: int = 24, min_dwell_sec: float = 0.85):
        self.line1 = ""               # 上一行（历史留存行 / 锚定行）
        self.line2 = ""               # 当前行（最新字幕行 / 动作行）
        self.last_update_ts = 0.0     # line2 最近一次独立刷新的时间
        self.max_inline_chars = max_inline_chars
        self.min_dwell_sec = min_dwell_sec

    def reset(self) -> None:
        self.line1 = ""
        self.line2 = ""
        self.last_update_ts = 0.0

    def push_new_sentence(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return self.render()

        now = time.monotonic()
        dt = (now - self.last_update_ts) if self.last_update_ts > 0 else 999.0

        # 规则 1：如果上一句刚出不到 0.85 秒，且两句合起来不超过 24 字 -> 同行直接拼接！
        if self.line2 and dt < self.min_dwell_sec and (len(self.line2) + len(text) <= self.max_inline_chars):
            self.line2 = f"{self.line2}  {text}"
            # 注意：这里不重置 last_update_ts，避免连续第 3 句无限往后接
        else:
            # 规则 2：拼不下或上一句已看够 0.85 秒 -> 原先的 line2 顶到 line1 继续停留，新句进 line2
            if self.line2:
                self.line1 = self.line2
            self.line2 = text
            self.last_update_ts = now

        return self.render()

    def render(self) -> str:
        # 如果超过 4 秒没有新话，自动把旧的 line1 清掉，保持界面干净
        if self.last_update_ts > 0 and (time.monotonic() - self.last_update_ts > 4.0):
            self.line1 = ""
        if self.line1 and self.line2:
            return f"{self.line1}\n{self.line2}"
        return self.line2 or self.line1

