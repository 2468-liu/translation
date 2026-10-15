"""Typography, word-wrapping, and bottom-pinned baseline rendering engine.

Handles:
1. Western word-boundary wrapping (no intra-word tearing).
2. Chinese and East-Asian typography kinsoku shori (避头尾法则, no orphaned line-start punctuation).
3. Bottom-pinned reading baseline coordinate projection.
4. Soft multi-layer ambient shadow and crisp text outline rendering.
"""

from __future__ import annotations

import re
import sys
from typing import Callable, List, Sequence, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
)


def get_caption_font(px: int, weight: QFont.Weight = QFont.Weight.Medium) -> QFont:
    """Returns a high-quality anti-aliased font with cross-platform fallback (Linux/Windows)."""
    font = QFont()
    if sys.platform == "win32":
        font.setFamilies(["Microsoft YaHei", "PingFang SC", "Segoe UI", "sans-serif"])
    else:
        font.setFamilies([
            "Noto Sans CJK SC",
            "Source Han Sans CN",
            "WenQuanYi Micro Hei",
            "Ubuntu Sans",
            "Ubuntu",
            "DejaVu Sans",
            "sans-serif",
        ])
    font.setPixelSize(px)
    font.setWeight(weight)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality)
    return font

# Typography Guard: Punctuation forbidden at start of line (行首禁排)
NO_LINE_START: frozenset[str] = frozenset(
    "，。！？、；：）》”’】｝…—～·.,!?;:)]}>\"'”’»›"
)

# Typography Guard: Punctuation forbidden at end of line (行尾禁排)
NO_LINE_END: frozenset[str] = frozenset(
    "（《“‘【｛([{<\"'“‘«‹"
)


def wrap_caption_typography(
    text: str,
    max_width: int,
    measure_fn: Callable[[str], int | float],
    max_lines: int = 2,
) -> list[str]:
    """Wraps text respecting Western word boundaries and Chinese 避头尾 typography rules.

    Args:
        text: Input string.
        max_width: Maximum allowed width in pixels.
        measure_fn: Callable that returns pixel width of string (e.g. fontMetrics.horizontalAdvance).
        max_lines: Cap on display lines returned (returns the latest `max_lines` rows).
    """
    all_rows = wrap_all_typography(text, max_width, measure_fn)
    if max_lines and max_lines > 0:
        return all_rows[-max_lines:]
    return all_rows


def wrap_all_typography(
    text: str,
    max_width: int,
    measure_fn: Callable[[str], int | float],
) -> list[str]:
    """Breaks full text into display lines with strict word-wrap and Kinsoku Shori."""
    if not text:
        return []

    lines: list[str] = []
    current = ""

    # Split into Western words, whitespaces, or individual CJK/other characters
    units = re.findall(r"[A-Za-z0-9][A-Za-z0-9'’./:-]*|\s+|.", text, re.DOTALL)

    for unit in units:
        if unit == "\n":
            if current:
                lines.append(current.rstrip())
                current = ""
            continue

        if unit.isspace() and not current:
            continue

        candidate = current + unit
        cand_w = measure_fn(candidate)

        if current and cand_w > max_width:
            # Check 避头尾 (Kinsoku Shori):
            # If `unit` is forbidden at line start, do NOT let it become the first char of the next line!
            # Instead, steal the trailing unit from `current` and push both to the new line.
            first_char = unit.strip()[:1] if unit.strip() else ""
            if first_char and first_char in NO_LINE_START and len(current) > 1:
                # Find trailing token or character in current
                sub_units = re.findall(r"[A-Za-z0-9][A-Za-z0-9'’./:-]*|\s+|.", current, re.DOTALL)
                if len(sub_units) >= 2:
                    stolen = sub_units[-1]
                    prev_chunk = "".join(sub_units[:-1]).rstrip()
                    lines.append(prev_chunk)
                    current = (stolen + unit).lstrip()
                    continue

            # Standard line break
            lines.append(current.rstrip())
            current = unit.lstrip()
        else:
            current = candidate

    if current:
        lines.append(current.rstrip())

    # Final post-pass: verify no line starts with a forbidden character
    rebalanced: list[str] = []
    for line in lines:
        cleaned = line.strip()
        if not cleaned:
            continue
        if rebalanced and cleaned[0] in NO_LINE_START and len(rebalanced[-1]) > 1:
            # Pull the last character from previous line down to join this line
            prev = rebalanced[-1]
            rebalanced[-1] = prev[:-1].rstrip()
            rebalanced.append(prev[-1] + cleaned)
        else:
            rebalanced.append(cleaned)

    return rebalanced


def compute_bottom_pinned_baselines(
    line_count: int,
    container_height: int | float,
    line_spacing: int | float,
    descent: int | float,
    bottom_pad: int | float = 16.0,
) -> list[float]:
    """Calculates bottom-pinned baseline Y coordinates.

    Regardless of whether line_count is 1 or 2, the bottommost line (index line_count - 1)
    is pinned at `container_height - bottom_pad - descent`.
    Previous lines are placed at `bottom_baseline - (line_count - 1 - i) * line_spacing`.
    This guarantees that the viewer's reading gaze on the latest line never jumps vertically.
    """
    if line_count <= 0:
        return []

    baseline_last = float(container_height) - float(bottom_pad) - float(descent)
    baselines = []
    for i in range(line_count):
        y = baseline_last - (line_count - 1 - i) * float(line_spacing)
        baselines.append(y)
    return baselines


def render_caption_text(
    painter: QPainter,
    lines: Sequence[str],
    baselines: Sequence[float],
    rect_width: int | float,
    font: QFont,
    metrics: QFontMetrics,
    primary_color: QColor | str = "#FFFFFF",
    anchor_line: str = "",
    active_line: str = "",
    stable_prefix: str = "",
    hypothesis_suffix: str = "",
    opacity: float = 1.0,
) -> None:
    """Renders subtitle lines with dual-layer soft ambient glow and Google-style dual-zone typography."""
    if not lines or not baselines or opacity <= 0.001:
        return

    painter.save()
    painter.setFont(font)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    effective_opacity = max(0.0, min(1.0, float(opacity)))
    w = float(rect_width)
    shadow_alpha = int(220 * effective_opacity)
    glow_pen = QPen(
        QColor(0, 0, 0, shadow_alpha),
        3.6,
        Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.RoundCap,
        Qt.PenJoinStyle.RoundJoin,
    )

    fill_color = QColor(primary_color)
    fill_color.setAlpha(int(255 * effective_opacity))

    for i, line_text in enumerate(lines):
        if i >= len(baselines):
            break
        line_w = metrics.horizontalAdvance(line_text)
        x = (w - line_w) / 2.0
        y = baselines[i]

        path = QPainterPath()
        path.addText(x, y, font, line_text)

        painter.setPen(glow_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill_color)
        painter.drawPath(path)

    painter.restore()


def update_window_layout_and_geometry(window: Any, text: str) -> list[str]:
    """Calculates typography wrapping, bottom-anchored height, and adjusts window geometry."""
    body = text or ""
    mode = getattr(window, "bilingual_mode", "trans_only")
    if mode == "source_only" and getattr(window, "source_line", ""):
        body = window.source_line

    font = get_caption_font(window.font_px)
    metrics = QFontMetrics(font)
    wrap_w = max(240, window.width() - 48)

    display_lines = wrap_caption_typography(body, wrap_w, metrics.horizontalAdvance, max_lines=2)
    line_h = metrics.lineSpacing()
    lines = len(display_lines)

    if mode == "bilingual" and getattr(window, "source_line", "") and lines == 1:
        lines = 2

    top_pad = 28 if (not window.text_only and getattr(window, "_is_hovered", False)) else 18
    bottom_pad = 16
    target_h = max(70, top_pad + lines * line_h + bottom_pad)

    if target_h != window.height():
        cur_geo = window.geometry()
        delta = target_h - cur_geo.height()
        new_y = max(0, cur_geo.y() - delta)
        window.setGeometry(cur_geo.x(), new_y, cur_geo.width(), target_h)

    window._drawn_text = "\n".join(display_lines)
    window.update()
    return display_lines


def render_caption_overlay(window: Any, painter: QPainter) -> None:
    """Renders background card, hover resize grips, secondary bilingual line, and primary text."""
    rect = window.rect()

    # 1. Background & resize grips
    if window.visible:
        if not window.text_only:
            box_alpha = 225
            border_alpha = 28
            painter.setPen(QPen(QColor(255, 255, 255, border_alpha), 1.0))
            painter.setBrush(QColor(16, 16, 20, box_alpha))
            painter.drawRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), 10.0, 10.0)

        # Draw subtle vertical resize grips on left & right borders when hovered
        if getattr(window, "_is_hovered", False):
            grip_color = QColor(255, 255, 255, 90) if not window.text_only else QColor(255, 255, 255, 60)
            grip_pen = QPen(grip_color, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(grip_pen)
            cy = rect.height() / 2.0
            painter.drawLine(QPointF(6.0, cy - 8.0), QPointF(6.0, cy + 8.0))
            painter.drawLine(QPointF(10.0, cy - 8.0), QPointF(10.0, cy + 8.0))
            painter.drawLine(QPointF(rect.width() - 10.0, cy - 8.0), QPointF(rect.width() - 10.0, cy + 8.0))
            painter.drawLine(QPointF(rect.width() - 6.0, cy - 8.0), QPointF(rect.width() - 6.0, cy + 8.0))

    # Header positioning
    if hasattr(window, "header_widget") and window.header_widget:
        window.header_widget.setGeometry(0, 0, rect.width(), 26)
        if not window.text_only and window.visible and getattr(window, "_is_hovered", False):
            window.header_widget.show()
        else:
            window.header_widget.hide()

    # 2. Subtitle Text - only draw when text is present
    body = window.line
    if not body:
        return

    mode = getattr(window, "bilingual_mode", "trans_only")
    if mode == "source_only" and getattr(window, "source_line", ""):
        body = window.source_line

    font = get_caption_font(window.font_px, QFont.Weight.Medium)
    metrics = QFontMetrics(font)
    wrap_w = max(240, rect.width() - 48)
    lines = wrap_caption_typography(body, wrap_w, metrics.horizontalAdvance, max_lines=2)

    line_h = metrics.lineSpacing()
    baselines = compute_bottom_pinned_baselines(
        line_count=len(lines),
        container_height=rect.height(),
        line_spacing=line_h,
        descent=metrics.descent(),
        bottom_pad=16.0,
    )

    anchor_text = window.rolling_buffer.anchor_line if getattr(window, "rolling_two_lines", False) else ""
    active_text = window.rolling_buffer.active_line if getattr(window, "rolling_two_lines", False) else ""

    # Secondary bilingual line (above the primary translation)
    if mode == "bilingual" and getattr(window, "source_line", "") and len(lines) == 1 and baselines:
        src_font = get_caption_font(max(11, window.font_px - 4), QFont.Weight.Normal)
        src_metrics = QFontMetrics(src_font)
        src_lines = wrap_caption_typography(window.source_line, wrap_w, src_metrics.horizontalAdvance, max_lines=1)
        if src_lines:
            render_caption_text(
                painter=painter,
                lines=src_lines,
                baselines=[baselines[0] - line_h],
                rect_width=rect.width(),
                font=src_font,
                metrics=src_metrics,
                primary_color="#94a3b8",
                opacity=window.fade_animator.text_opacity,
            )

    render_caption_text(
        painter=painter,
        lines=lines,
        baselines=baselines,
        rect_width=rect.width(),
        font=font,
        metrics=metrics,
        primary_color=window.font_color,
        anchor_line=anchor_text,
        active_line=active_text,
        stable_prefix=getattr(window, "stable_prefix", ""),
        hypothesis_suffix=getattr(window, "hypothesis_suffix", ""),
        opacity=window.fade_animator.text_opacity,
    )
