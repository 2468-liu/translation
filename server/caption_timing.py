"""Pure mathematical algorithms, timing constants, and state machine transitions for captions.

Decoupled completely from Tkinter/PyQt windowing and graphics subsystems.
All functions here are pure and operate on primitive strings, floats, and lists.
"""

from __future__ import annotations

import hashlib
import re
from typing import Callable, List, Optional, Tuple

from .config import _env_int
from .translate import collapse_caption_repeats

# Ports for UDP state loopback
CAPTION_PORT = 8766
STATE_PORT = 8767

# Subtitle hold and settle timing constants
HOLD_ABS_MIN_MS = _env_int("TRANSLATION_HOLD_ABS_MIN_MS", 800)
HOLD_ABS_MAX_MS = 4200
SETTLE_MS = _env_int("TRANSLATION_SETTLE_MS", 450)
CLAUSE_MAX_MS = _env_int("TRANSLATION_CLAUSE_MAX_MS", 400)
READABLE_RATIO = 0.65
X_SETTLE_MIN_PX = _env_int("TRANSLATION_CAPTION_X_SETTLE_MIN_PX", 40)

# Pace tracking constants
PACE_MIN = 0.5
PACE_MAX = 2.0
PACE_MIN_GAPS = 3
REF_GAP_S = 1.6

# Classification thresholds
_MIN_LENGTH_RATIO = 0.35
_MIN_SIMILARITY = 0.50

# Display boundary marks
TERMINAL_PUNCT = "。，、；：！？…—"


def punctuation_marks(text: str) -> tuple[str, ...]:
    """The terminal marks the text carries, in order and with repeats kept."""
    return tuple(ch for ch in text if ch in TERMINAL_PUNCT)


def introduces_boundary(shown: str, incoming: str) -> bool:
    """True when the incoming text carries more terminal marks than the line does."""
    return len(punctuation_marks(incoming)) > len(punctuation_marks(shown))


def wrap_all(text: str, width: int, measure: Callable[[str], int | float]) -> list[str]:
    """Every display line the text needs, with no cap."""
    if not text:
        return []
    lines: list[str] = []
    current = ""
    units = re.findall(r"[A-Za-z0-9][A-Za-z0-9'’./:-]*|\s+|.", text, re.DOTALL)
    for unit in units:
        if unit == "\n":
            lines.append(current.rstrip())
            current = ""
            continue
        if unit.isspace() and not current:
            continue
        candidate = current + unit
        if current and measure(candidate) > width:
            lines.append(current.rstrip())
            current = unit.lstrip()
        else:
            current = candidate
    if current or not lines:
        lines.append(current.rstrip())
    return [line for line in lines if line]


def wrap_caption(
    text: str,
    width: int,
    measure: Callable[[str], int | float],
    max_lines: int = 2,
) -> list[str]:
    """Wraps text up to max_lines display rows."""
    all_lines = wrap_all(text, width, measure)
    return all_lines[-max_lines:] if max_lines else all_lines


def _collapse_repeats(text: str) -> str:
    """Collapse consecutive translation repeat artifacts."""
    return collapse_caption_repeats(text)


def _read_units(text: str) -> int:
    """Count syllables or character reading units in text."""
    units = 0
    in_word = False
    for ch in (text or "").strip():
        if ch.isspace() or ch in "，。！？、；：,.!?;:…—-「」\"'（）()【】":
            in_word = False
            continue
        if ord(ch) < 128 and (ch.isalnum() or ch == "'"):
            if not in_word:
                units += 1
                in_word = True
            continue
        in_word = False
        units += 1
    return units


def _reference_hold_ms(count: int) -> int:
    if count <= 3:
        return 1000
    if count <= 8:
        return 1000 + (count - 3) * 150
    if count <= 18:
        return 1750 + (count - 8) * 130
    return 3050 + (count - 18) * 80


def pace_scale(gaps: list[float]) -> float:
    usable = [gap for gap in gaps if 0.45 <= gap <= 8.0]
    if len(usable) < PACE_MIN_GAPS:
        return 1.0
    ordered = sorted(usable[-6:])
    mid = ordered[len(ordered) // 2]
    return max(PACE_MIN, min(PACE_MAX, mid / REF_GAP_S))


def speech_hold_factor(pace: float = 1.0) -> float:
    pace_val = float(pace if pace is not None else 1.0)
    if pace_val <= 0.75:
        return 0.62
    if pace_val <= 0.90:
        return 0.70
    if pace_val <= 1.10:
        return 0.80
    if pace_val <= 1.30:
        return 0.90
    return 0.98


def hold_ms(text: str, pace: float = 1.0) -> int:
    count = _read_units(text)
    if count <= 0:
        return HOLD_ABS_MIN_MS
    scale = speech_hold_factor(pace)
    scaled = int(round(_reference_hold_ms(count) * scale))
    return max(HOLD_ABS_MIN_MS, min(HOLD_ABS_MAX_MS, scaled))


def swap_deadline(
    growth_stopped_at: float,
    settle_until: float,
    line: str = "",
    pace: float = 1.0,
    next_utterance_at: float = 0.0,
) -> float:
    return swap_deadline_why(
        growth_stopped_at, settle_until, line, pace, next_utterance_at
    )[0]


def swap_deadline_why(
    growth_stopped_at: float,
    settle_until: float,
    line: str = "",
    pace: float = 1.0,
    next_utterance_at: float = 0.0,
) -> tuple[float, str, dict]:
    candidates: dict[str, float] = {}
    candidates["hold"] = growth_stopped_at + hold_ms(line, pace) / 1000.0
    if settle_until:
        candidates["settle"] = settle_until
    if next_utterance_at > growth_stopped_at and growth_stopped_at:
        readable_ms = max(
            HOLD_ABS_MIN_MS,
            int(round(READABLE_RATIO * _reference_hold_ms(max(1, _read_units(line))))),
        )
        candidates["readable_at"] = growth_stopped_at + readable_ms / 1000.0
        candidates["next_utterance"] = next_utterance_at

    if next_utterance_at > growth_stopped_at and growth_stopped_at:
        ceiling = max(
            candidates["readable_at"],
            candidates["next_utterance"],
            candidates.get("settle", 0.0),
        )
        floor = max(candidates["hold"], candidates.get("settle", 0.0))
        deadline = min(floor, ceiling)
    else:
        deadline = max(candidates["hold"], candidates.get("settle", 0.0))

    name = "none"
    for key in ("hold", "settle", "readable_at", "next_utterance"):
        if key in candidates and abs(candidates[key] - deadline) < 1e-6:
            name = key
    return (deadline, name, candidates)


def _signature(text: str) -> str:
    """Short stable digest of a line, for matching probes against a recording."""
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:12]


def classify_caption(current: str, new: str) -> str:
    if not new:
        return "empty"
    if not current:
        return "grow"
    if new == current:
        return "same"
    if len(new) > len(current) and new.startswith(current):
        return "grow"
    if len(new) < len(current) and current.startswith(new):
        return "rewind"
    if _is_char_edit(current, new):
        return "char"
    return "sentence"


def _is_char_edit(current: str, new: str) -> bool:
    current_len = len(current)
    new_len = len(new)
    if current_len == 0 or new_len == 0:
        return False
    if new_len < _MIN_LENGTH_RATIO * current_len or current_len < _MIN_LENGTH_RATIO * new_len:
        return False
    longest = max(current_len, new_len)
    distance = _edit_distance(current, new)
    if distance / longest > 1.0 - _MIN_SIMILARITY:
        return False
    lcs_ratio = _lcs_len(current, new) / longest
    edit_ratio = 1.0 - distance / longest
    return max(lcs_ratio, edit_ratio) >= _MIN_SIMILARITY


def _edit_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for i, ca in enumerate(left, 1):
        current = [i]
        for j, cb in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def _lcs_len(left: str, right: str) -> int:
    if not left or not right:
        return 0
    previous = [0] * (len(right) + 1)
    for ca in left:
        current = [0]
        for j, cb in enumerate(right, 1):
            if ca == cb:
                current.append(previous[j - 1] + 1)
            else:
                current.append(max(previous[j], current[-1]))
        previous = current
    return previous[-1]


def _shadow_pending(line: str, pending: str) -> str:
    if not pending or not line:
        return ""
    if classify_caption(line, pending) in ("same", "rewind"):
        return ""
    if len(pending) < _MIN_LENGTH_RATIO * len(line):
        return ""
    return pending


def step_caption(
    line: str,
    pending: str,
    growth_stopped_at: float,
    settle_until: float,
    text: str,
    now: float,
    pace: float = 1.0,
    next_utterance_at: float = 0.0,
) -> tuple[str, str, str, float, float, float]:
    deadline = swap_deadline(growth_stopped_at, settle_until, line, pace, next_utterance_at)
    if not text:
        return ("drop", line, pending, growth_stopped_at, settle_until, deadline)
    if not line:
        return ("grow", text, "", now, 0.0, swap_deadline(now, 0.0, text, pace))
    kind = classify_caption(line, text)
    if kind in ("same", "rewind", "empty"):
        return ("drop", line, pending, growth_stopped_at, settle_until, deadline)
    if kind == "grow":
        return ("grow", text, _shadow_pending(text, pending), now, 0.0, swap_deadline(now, 0.0, text, pace))
    if kind == "char":
        settle = max(settle_until, now + SETTLE_MS / 1000.0)
        return (
            "char",
            text,
            _shadow_pending(text, pending),
            growth_stopped_at,
            settle,
            swap_deadline(growth_stopped_at, settle, text, pace, next_utterance_at),
        )
    chosen = text
    if pending and classify_caption(pending, text) in ("same", "rewind"):
        chosen = pending
    if now >= deadline:
        return ("swap", chosen, "", now, 0.0, swap_deadline(now, 0.0, chosen, pace))
    return ("defer", line, chosen, growth_stopped_at, settle_until, deadline)


def flush_caption(
    line: str,
    pending: str,
    growth_stopped_at: float,
    settle_until: float,
    now: float,
    pace: float = 1.0,
    next_utterance_at: float = 0.0,
) -> tuple[str, str, str, float, float, float]:
    deadline = swap_deadline(growth_stopped_at, settle_until, line, pace, next_utterance_at)
    if not pending:
        return ("idle", line, "", growth_stopped_at, settle_until, deadline)
    if now < deadline:
        return ("wait", line, pending, growth_stopped_at, settle_until, deadline)
    kind = classify_caption(line, pending)
    if kind in ("same", "rewind", "empty"):
        return ("drop", line, "", growth_stopped_at, settle_until, deadline)
    if kind == "char":
        settle = max(settle_until, now + SETTLE_MS / 1000.0)
        return (
            "char",
            pending,
            "",
            growth_stopped_at,
            settle,
            swap_deadline(growth_stopped_at, settle, pending, pace, next_utterance_at),
        )
    if kind == "grow":
        return ("grow", pending, "", now, 0.0, swap_deadline(now, 0.0, pending, pace))
    return ("swap", pending, "", now, 0.0, swap_deadline(now, 0.0, pending, pace))


def caption_action(closed: bool, text: str, visible: bool) -> str:
    line = (text or "").strip()
    if not line:
        return "stay" if closed else "withdraw"
    if closed:
        return "stay"
    return "paint" if visible else "show"


def clamped_font_px(px, current=None):
    try:
        size = int(px)
    except (TypeError, ValueError):
        return None
    size = max(16, min(40, size))
    if current is not None and size == int(current):
        return None
    return size
