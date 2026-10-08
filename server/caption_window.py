"""Parentless subtitle window. No title bar, no minimize, text only by default."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path

from . import perf
from .config import _env_int
from .translate import CHAR_RUN_MAX, CLAUSE_RUN_MAX, collapse_caption_repeats

try:
    import ctypes
except ImportError:  # pragma: no cover
    ctypes = None

CAPTION_PORT = 8766
STATE_PORT = 8767
# The 3-minute blog is a reference, not a schedule. There the gap between
# spoken pieces had a 3.25s median and a typical line was about 13 characters.
# Other videos sit on either side of that, so the curve below is the comfort
# center and live utterance gaps scale it between PACE_MIN and PACE_MAX.
# Length bands at pace 1 (a 13-character line is about 2.4s, under that 3.25s gap):
#   1–3 chars   glance, fixed 1.0s
#   4–8 chars   short clause, 150ms for each extra character
#   9–18 chars  reference band, 130ms each (blog-like lines)
#   19+ chars   long line, 80ms each; absolute cap HOLD_ABS_MAX_MS
REF_GAP_S = 3.25
PACE_MIN = 0.62
PACE_MAX = 1.48
PACE_MIN_GAPS = 3
READABLE_RATIO = 0.70
# The floor on how long a line stays up, whatever its length or the speaker's pace.
#
# This was tried at 600 and put back, and the reason is worth keeping: it is the wrong
# knob for the stall. Lowering it to 600 moved held-group gap p50 from 1482ms to 1502ms
# and waiting p50 from 808ms to 820ms -- both inside noise -- while costing 180ms on
# every one-to-three-character line, the only band the floor reaches. The floor only
# clamps when the reference table scaled below it, and the table bottoms out at 1000ms,
# so 620ms is the smallest value the table and the fastest pace factor can produce
# together. At 800 that floor still clamped those lines; at 600 it stopped clamping
# anything at all, which makes 600 an inert parameter rather than a tighter hold.
#
# What drives the median instead is the 9-to-18 character band: 13 characters is
# 2400ms in the reference table and 1488ms after the 0.62 factor, which is the measured
# held-group p50 almost exactly. Only that band and the pace factor can move it, and the
# factor is a readability trade rather than a free win.
#
# ⚠️ One inference here was wrong and is worth not repeating. The 600 run was chosen
# because waiting p50 measured 808ms against a floor of 800, which reads like the floor
# setting the wait. It does not: the 800 floor was never in that number, because hold_ms
# is one candidate among several and the wait it produces is the remainder, not the
# hold. The same numbers' smallest values were 54ms and 157ms. Two figures landing near
# each other is not one figure explaining the other.
#
# Injectable, like every other threshold, so an experiment moves one number instead of
# editing the file the last one measured.
HOLD_ABS_MIN_MS = _env_int("TRANSLATION_HOLD_ABS_MIN_MS", 800)
HOLD_ABS_MAX_MS = 4200
# Settle is the pause after a one-character revision before the change is drawn,
# so a stream of single-character corrections does not flicker. It is also a
# candidate for the swap deadline, which makes it a latency cost and not only a
# rendering one.
#
# It was made injectable because the display leg's median sat near 450 and settle
# was the obvious suspect. Measured, and it is not: over 6831 and 6906 decisions
# at 450 and 150 respectively, settle set the deadline 5 times and 2 times, or
# 0.07% and 0.03%. The hold did it 91% and 93% of the time.
#
# That is structural rather than a small sample. hold_ms() never returns less than
# HOLD_ABS_MIN_MS, so the hold candidate is at least 800 ms away from the moment a
# line is drawn, while settle_until is at most SETTLE_MS past the same moment. Any
# SETTLE_MS below HOLD_ABS_MIN_MS therefore cannot bind, except in the one case
# where growth_stopped_at is still 0 -- the state before any line has been drawn.
# Sweeping it below 800 is a no-op by construction; to make settle matter it would
# have to exceed the minimum hold, which is a different question and a worse idea.
#
# Injected through the environment like every other threshold, so an experiment
# can move it without editing the file the previous experiment measured.
SETTLE_MS = _env_int("TRANSLATION_SETTLE_MS", 450)
_MIN_LENGTH_RATIO = 0.75
_MIN_SIMILARITY = 0.72
_KEY = "#010203"
_COMMANDS: queue.Queue = queue.Queue()
_SOCK = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_STATE_SOCK = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

HWND_TOPMOST = -1
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TRANSPARENT = 0x00000020
EDGE = 12
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
SW_SHOWNOACTIVATE = 4

_LANG = {
    "en": "英语",
    "ja": "日语",
    "ko": "韩语",
    "fr": "法语",
    "de": "德语",
    "es": "西班牙语",
    "ru": "俄语",
    "zh": "中文",
}


def fit_caption_width(measured: int, current: int, screen_width: int, locked: bool) -> int:
    return current


def wrap_caption(text: str, width: int, measure, max_lines: int = 2) -> list[str]:
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
    return [line for line in lines[-max_lines:] if line]


def _port_busy() -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.bind(("127.0.0.1", CAPTION_PORT))
        return False
    except OSError:
        return True
    finally:
        probe.close()


def _listen(commands: queue.Queue) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", CAPTION_PORT))
    while True:
        data, _addr = sock.recvfrom(65535)
        recv_at = time.time()
        try:
            message = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        kind = message.get("kind")
        sent_at = message.get("sentAt")
        perf.emit(
            "caption",
            "udp",
            kind=kind,
            net=perf.wall_ms(sent_at) if isinstance(sent_at, (int, float)) else -1.0,
        )
        commands.put((kind, message.get("value"), sent_at, recv_at))


def _enable_dpi() -> float:
    if not ctypes:
        return 1.0
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = int(ctypes.windll.gdi32.GetDeviceCaps(hdc, 88))
        ctypes.windll.user32.ReleaseDC(0, hdc)
        return max(1.0, dpi / 96.0)
    except Exception:
        return 1.0


class _Caption:
    def __init__(self, scale: float) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.text_only = False
        self.position_locked = True
        self.visible = False
        self.closed = False
        self.have_translation = False
        self.status = ""
        self.line = ""
        self._growth_stopped_at = 0.0
        self._settle_until = 0.0
        self._pending = ""
        self._hold_job = None
        self._gaps: list[float] = []
        self._last_segment_at = 0.0
        self._next_utterance_at = 0.0
        self._trace_sent = 0.0
        self._trace_recv = 0.0
        self._trace_action = ""
        self._defer_t0 = 0.0
        self._defer_text = ""
        self._defer_chars = 0
        self._segment_seq = 0
        self._wait_segment = 0
        self._line_since = 0.0
        self._prev_chars = 0
        self._held_paint = False
        # When text first became drawable since the last grow/swap, or 0.0 if nothing
        # has arrived. Read-only, and it exists so a dwell can be attributed: a gap
        # with text sitting in _pending is the hold delaying something that was ready,
        # and a gap with nothing in _pending is the material having no speech in it.
        # Those are different problems and the fix is opposite for each -- the first
        # is the hold being too long, the second is nothing to fix.
        self._pending_since = 0.0
        self.font_status = tkfont.Font(family="Microsoft YaHei", size=-max(12, round(12 * scale)))
        self.font_px = 20
        self.font_color = "#ffffff"
        self.font_line = tkfont.Font(family="Microsoft YaHei", size=-self.font_px)
        self.screen_width = self.root.winfo_screenwidth()
        self.width = max(round(760 * scale), self.font_line.measure("实时字幕") + 120)
        self.width = min(self.width, max(420, self.screen_width - 80))
        self.width_locked = False
        self.height = self.font_line.metrics("linespace") + 38
        screen_h = self.root.winfo_screenheight()
        x = max(0, (self.screen_width - self.width) // 2)
        y = max(0, screen_h - self.height - 120)
        self.root.geometry(f"{self.width}x{self.height}+{x}+{y}")

        self.canvas = tk.Canvas(self.root, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self.close = tk.Label(
            self.root,
            text="×",
            fg="#ffffff",
            font=("Segoe UI", 11),
            cursor="hand2",
            padx=2,
            pady=0,
        )
        self.close.bind("<Button-1>", lambda _event: self.hide())
        self.text_only_var = tk.BooleanVar(value=self.text_only)
        self.text_only_control = tk.Checkbutton(
            self.root,
            text="只有字",
            variable=self.text_only_var,
            command=lambda: self.apply_text_only(bool(self.text_only_var.get())),
            fg="#ffffff",
            bg="#000000",
            activeforeground="#ffffff",
            activebackground="#000000",
            selectcolor="#000000",
            font=("Microsoft YaHei", 9),
            bd=0,
            highlightthickness=0,
            padx=2,
            pady=0,
        )
        self.position_lock_var = tk.BooleanVar(value=self.position_locked)
        self.position_lock_control = tk.Checkbutton(
            self.root,
            text="锁定",
            variable=self.position_lock_var,
            command=lambda: self.apply_position_lock(bool(self.position_lock_var.get())),
            fg="#ffffff",
            bg="#000000",
            activeforeground="#ffffff",
            activebackground="#000000",
            selectcolor="#000000",
            font=("Microsoft YaHei", 9),
            bd=0,
            highlightthickness=0,
            padx=2,
            pady=0,
        )

        # Read-only cadence readout. The control moved to the browser extension,
        # which owns the controls and this window owns the display. What stayed is
        # the feedback: comparing 250 against 750 is something you do while
        # watching, and a switch that lands silently cannot be judged while
        # watching. ack_switch already carries the value (server/caption_window.py
        # ack_switch sends it as switch_ok's payload), so the read path was
        # already there and the pump was the only thing dropping it.
        self._cadence_label = tk.Label(
            self.root,
            text="250ms",
            fg="#888888",
            bg="#000000",
            font=("Microsoft YaHei", 9),
            bd=0,
            padx=4,
        )

        self._drag = None
        self._resize_origin = None
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<ButtonPress-3>", self._press_resize)
        self.canvas.bind("<B3-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-3>", self._release)
        self.root.bind("<Enter>", lambda _event: self._show_close())
        self.root.bind("<Leave>", lambda _event: self.close.place_forget())
        self._apply_colors()
        self.root.after(50, self._pump)
        self.root.after(300, self._pin)

    def hwnd(self) -> int:
        self.root.update_idletasks()
        child = int(self.root.winfo_id())
        if not ctypes:
            return child
        parent = ctypes.windll.user32.GetParent(child)
        return int(parent or child)

    def _strip_frame(self) -> None:
        if not ctypes:
            return
        # This is a native Windows topmost window. It never enters the page or
        # changes the browser's fullscreen state.
        hwnd = self.hwnd()
        user32 = ctypes.windll.user32
        get_long = user32.GetWindowLongPtrW
        set_long = user32.SetWindowLongPtrW
        get_long.restype = ctypes.c_ssize_t
        get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
        set_long.restype = ctypes.c_ssize_t
        set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        style = get_long(hwnd, GWL_STYLE)
        style &= ~(WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
        set_long(hwnd, GWL_STYLE, style)
        exstyle = get_long(hwnd, GWL_EXSTYLE)
        extra = WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE
        if self.text_only:
            extra |= WS_EX_LAYERED
            if self.position_locked:
                extra |= WS_EX_TRANSPARENT
        set_long(hwnd, GWL_EXSTYLE, exstyle | extra)
        user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_SHOWWINDOW,
        )
        if self.text_only:
            self.root.attributes("-transparentcolor", _KEY)

    def _pin(self) -> None:
        if ctypes and self.visible:
            ctypes.windll.user32.SetWindowPos(
                self.hwnd(),
                HWND_TOPMOST,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )
        self.root.after(300, self._pin)

    def _apply_colors(self) -> None:
        bg = _KEY if self.text_only else "#000000"
        self.root.configure(bg=bg)
        self.canvas.configure(bg=bg)
        self.close.configure(bg=bg)
        self.text_only_control.configure(
            bg=bg,
            activebackground=bg,
            selectcolor=bg,
        )
        if self.text_only:
            self.text_only_control.place_forget()
            self.position_lock_control.place(x=80, y=3, anchor="nw")
        else:
            self.text_only_control.place(x=8, y=3, anchor="nw")
            self.position_lock_control.place_forget()
        # The readout sits to the right of whichever control is showing, and stays
        # visible in both modes: noticing that the cadence is not what you think it
        # is is something you do mid-playback, not at setup.
        base = 80 if self.text_only else 160
        self._cadence_label.configure(bg=bg)
        self._cadence_label.place(x=base, y=3, anchor="nw")
        if self.text_only:
            self.root.attributes("-transparentcolor", _KEY)
        else:
            self.root.attributes("-transparentcolor", "")
        self._set_input_mode()
        self._paint()

    def _set_input_mode(self) -> None:
        if not ctypes:
            return
        hwnd = self.hwnd()
        user32 = ctypes.windll.user32
        get_long = user32.GetWindowLongPtrW
        set_long = user32.SetWindowLongPtrW
        get_long.restype = ctypes.c_ssize_t
        get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
        set_long.restype = ctypes.c_ssize_t
        set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        exstyle = get_long(hwnd, GWL_EXSTYLE)
        if self.text_only:
            exstyle |= WS_EX_LAYERED | WS_EX_NOACTIVATE
            if self.position_locked:
                exstyle |= WS_EX_TRANSPARENT
            else:
                exstyle &= ~WS_EX_TRANSPARENT
        else:
            exstyle &= ~WS_EX_TRANSPARENT
            exstyle |= WS_EX_NOACTIVATE
        set_long(hwnd, GWL_EXSTYLE, exstyle)

    def apply_position_lock(self, enabled: bool) -> None:
        self.position_locked = bool(enabled)
        self.position_lock_var.set(self.position_locked)
        self._set_input_mode()
        self._broadcast_state()

    def _show_close(self) -> None:
        if self.visible:
            self.close.place(relx=1, x=-5, y=3, anchor="ne")

    def _fit_width(self, text: str, wrap: int) -> int:
        measured = self.font_line.measure(text.replace("\n", ""))
        return fit_caption_width(measured, self.width, self.screen_width, self.width_locked)

    def _paint(self) -> None:
        self.canvas.delete("all")
        body = self.line or ("实时字幕" if self.visible else "")
        if not body:
            return
        wrap = max(240, self.width - 48)
        fitted = self._fit_width(body, wrap)
        if fitted != self.width:
            self.width = fitted
            x = self.root.winfo_x()
            center = x + self.root.winfo_width() / 2
            left = max(0, min(round(center - self.width / 2), self.screen_width - self.width))
            self.root.geometry(f"{self.width}x{self.height}+{left}+{self.root.winfo_y()}")
            wrap = self.width - 48
        display_lines = wrap_caption(body, wrap, self.font_line.measure)
        display_body = "\n".join(display_lines)
        line_h = max(1, self.font_line.metrics("linespace"))
        lines = len(display_lines)
        height = 30 + lines * line_h + 8
        if height != self.height:
            delta = height - self.height
            self.height = height
            if self.visible:
                self.root.update_idletasks()
                x = self.root.winfo_x()
                y = max(0, self.root.winfo_y() - delta)
                self.root.geometry(f"{self.width}x{self.height}+{x}+{y}")
            else:
                self.root.geometry(f"{self.width}x{self.height}")
        self._draw(display_body, self.font_line, wrap)
        now = time.monotonic()
        self._report_dwell(now)
        if self._trace_action in ("grow", "swap"):
            self._line_since = now
        if self._trace_sent:
            # First draw after a packet: this is "actually on screen".
            perf.emit(
                "caption",
                "paint",
                action=self._trace_action,
                since_send=perf.wall_ms(self._trace_sent),
                since_recv=perf.wall_ms(self._trace_recv) if self._trace_recv else -1.0,
                chars=len(display_body),
                # Rendered line count, and therefore whether this paint moved the
                # window. The height is 30 + lines*line_h + 8, and a change makes
                # the window grow and shift up by the same amount so its bottom edge
                # stays put -- about one line height, which is plainly visible.
                #
                # The sig below cannot see this: it hashes the wrapped text, so a
                # reflow reads as new content rather than as the same sentence being
                # laid out again. That is what left "how often does it flicker"
                # unanswerable from the log -- the one thing that moves on screen was
                # the one thing the content hash was blind to.
                lines=lines,
                held=1 if self._held_paint else 0,
                # A hash of what was actually shown, so a recorder can tell
                # whether this fragment's translation reached the screen at all.
                # Length alone is not enough: two fragments share one.
                sig=_signature(display_body),
            )
            self._trace_sent = 0.0
            self._trace_recv = 0.0
            self._trace_action = ""

    def _draw(self, text: str, font: tkfont.Font, wrap: int) -> None:
        x = self.width / 2
        y = 25
        self.canvas.create_text(
            x,
            y,
            text=text,
            anchor="n",
            justify="center",
            fill=self.font_color,
            font=font,
            width=wrap,
        )

    def _edges(self, event) -> tuple[bool, bool]:
        width = max(self.canvas.winfo_width(), 1)
        return event.x >= width - EDGE, False

    def _hover(self, event) -> None:
        if self._resize_origin or self._drag:
            return
        on_x, on_y = self._edges(event)
        if on_x:
            cursor = "sb_h_double_arrow"
        else:
            cursor = "arrow"
        self.canvas.configure(cursor=cursor)

    def _press(self, event) -> None:
        if self.text_only and self.position_locked:
            return
        on_x, on_y = self._edges(event)
        if on_x or on_y:
            self._begin_resize(event, on_x, on_y)
            return
        self._resize_origin = None
        self._drag = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _press_resize(self, event) -> None:
        self._begin_resize(event, True, False)

    def _begin_resize(self, event, on_x: bool, on_y: bool) -> None:
        self._drag = None
        if on_x:
            self.width_locked = True
        self.width = self.root.winfo_width()
        self.height = self.root.winfo_height()
        self._resize_origin = (event.x_root, event.y_root, self.width, self.height, on_x, on_y)

    def _motion(self, event) -> None:
        if self._resize_origin:
            x0, y0, width, height, on_x, on_y = self._resize_origin
            if on_x:
                self.width = max(120, width + event.x_root - x0)
            if on_y:
                self.height = max(36, height + event.y_root - y0)
            self.root.geometry(f"{self.width}x{self.height}")
            self._paint()
            return
        if not self._drag:
            return
        x = event.x_root - self._drag[0]
        y = event.y_root - self._drag[1]
        self.root.geometry(f"+{x}+{y}")

    def _release(self, _event) -> None:
        self._drag = None
        self._resize_origin = None

    def apply_text_only(self, enabled: bool) -> None:
        self.text_only = bool(enabled)
        self.text_only_var.set(self.text_only)
        if self.text_only:
            self.position_locked = True
            self.position_lock_var.set(True)
        self._apply_colors()
        self._broadcast_state()

    def apply_font(self, px: int) -> None:
        size = clamped_font_px(px, self.font_px)
        if size is None:
            return
        self.font_px = size
        self.font_line = tkfont.Font(family="Microsoft YaHei", size=-size)
        self._broadcast_state()
        if self.line or self.visible:
            self._paint()

    def apply_color(self, color: str) -> None:
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            return
        normalized = color.lower()
        if normalized == self.font_color:
            return
        self.font_color = normalized
        self._broadcast_state()
        if self.line or self.visible:
            self._paint()

    def _broadcast_state(self) -> None:
        payload = {
            "kind": "state",
            "value": {
                "textOnly": bool(self.text_only),
                "positionLocked": bool(self.position_locked),
                "fontPx": int(self.font_px),
                "fontColor": self.font_color,
            },
        }
        try:
            _STATE_SOCK.sendto(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                ("127.0.0.1", STATE_PORT),
            )
        except OSError:
            return

    def arm(self) -> None:
        # A new capture may show the next non-empty line. Do not reveal an empty bar.
        self.closed = False

    def show(self) -> None:
        self.closed = False
        self.visible = True
        self._strip_frame()
        if ctypes:
            ctypes.windll.user32.ShowWindow(self.hwnd(), SW_SHOWNOACTIVATE)
        else:
            self.root.deiconify()
        self._paint()

    def _note_pending(self, pending: str) -> None:
        """Stamp when text first became drawable in the current gap.

        Read-only bookkeeping, factored out because the same assignment happens on two
        paths (a packet arriving and the hold expiring) and the test needs to reach it
        without a Tk loop. Only the first text of a gap is stamped: a later partial must
        not push the time forward, or the waiting share would shrink every time one
        lands.
        """
        if pending and not self._pending_since:
            self._pending_since = time.monotonic()

    def _report_dwell(self, now: float) -> None:
        """Emit how long the previous sentence stayed up, and what filled the gap.

        Streaming repaints do not count; the clock starts when a sentence appears.

        `why` is the point of this. A dwell is either the hold still sitting on text
        that was already ready ("held") or the stream having nothing to say ("empty"),
        and those call for opposite responses -- one is the hold being too long, the
        other is the material and must not be tuned against at all. On
        samples/lecture_mix.wav gap alone does not tell them apart: p50 1622ms against a
        3.25s reference gap, 68 of 68 gaps over a second, and 18 to 22 second stretches
        of film-only audio that cannot paint anything.

        waiting is the share of the gap during which text was ready and undrawn, which is
        the part a shorter hold would actually recover. idle is the rest.
        """
        if not (self._line_since and self._trace_action == "swap"):
            self._pending_since = 0.0
            return
        waited = (now - self._pending_since) if self._pending_since else 0.0
        gap = now - self._line_since
        waited = max(0.0, waited)
        perf.emit(
            "caption",
            "dwell",
            gap=round(gap * 1000.0, 1),
            lost=self._prev_chars,
            shown=len(self.line),
            why="held" if self._pending_since else "empty",
            waiting=round(waited * 1000.0, 1),
            idle=round(max(0.0, gap - waited) * 1000.0, 1),
        )
        # Cleared here so the next gap cannot inherit this one's arrival time, which
        # would report a wait that began before its own clock started.
        self._pending_since = 0.0

    def hide(self) -> None:
        self.visible = False
        self.closed = True
        self.line = ""
        self.status = ""
        self._pending = ""
        self._growth_stopped_at = 0.0
        self._settle_until = 0.0
        self._gaps.clear()
        self._last_segment_at = 0.0
        self._next_utterance_at = 0.0
        self._trace_sent = 0.0
        self._trace_recv = 0.0
        self._trace_action = ""
        self._close_wait("", "hide")
        self._line_since = 0.0
        self._prev_chars = 0
        self._pending_since = 0.0
        self._held_paint = False
        self._wait_segment = self._segment_seq
        self.have_translation = False
        if self._hold_job is not None:
            self.root.after_cancel(self._hold_job)
            self._hold_job = None
        self.close.place_forget()
        self.root.withdraw()

    def set_status(self, lang: str) -> None:
        name = _LANG.get(lang or "", "")
        self.status = f"{name} → 中文" if name and name != "中文" else ""

    def show_cadence(self, ms: int) -> None:
        """Display the cadence the server has actually applied.

        Read-only on purpose. The buttons that used to live here are gone: the
        control is in the extension, and a caption window that can also be
        controlled from two places is a window whose state nobody can predict.
        The value arrives from ack_switch, which is sent by _switch_partial_interval
        on the WebSocket path -- so this shows what the server applied rather than
        what the extension last asked for.
        """
        try:
            value = int(ms)
        except (TypeError, ValueError):
            return
        self._cadence_label.configure(text=f"{value}ms")

    def clear(self) -> None:
        """Blank the line but leave the window up and armed.

        Swapping the text out rather than closing, because the next fragment
        belongs to the new configuration and must be able to land immediately.
        The deadline state is reset with it: a hold left over from the previous
        cadence would delay the first line of the new one by a wait that was
        never meant for it.
        """
        self.line = ""
        self._pending = ""
        self._growth_stopped_at = 0.0
        self._settle_until = 0.0
        self._next_utterance_at = 0.0
        self._line_since = 0.0
        self._prev_chars = 0
        self._pending_since = 0.0
        if self._hold_job is not None:
            self.root.after_cancel(self._hold_job)
            self._hold_job = None
        self._close_wait("", "clear")
        if self.visible:
            self._paint()

    def begin_segment(self) -> None:
        now = time.monotonic()
        self._segment_seq += 1
        if self._last_segment_at:
            gap = now - self._last_segment_at
            if 0.45 <= gap <= 8.0:
                self._gaps.append(gap)
                del self._gaps[:-6]
        self._last_segment_at = now
        if self.line and self._growth_stopped_at and now > self._growth_stopped_at + 0.2:
            self._next_utterance_at = now
        self.have_translation = False
        if self._pending:
            self._arm_hold()

    def _pace(self) -> float:
        return pace_scale(self._gaps)

    def show_source(self, text: str) -> None:
        if self.line or self.have_translation or self.closed:
            return
        cleaned = _collapse_repeats((text or "").strip())
        if not cleaned:
            return
        self._growth_stopped_at = time.monotonic()
        self._settle_until = 0.0
        self._apply_line(cleaned, "note")

    def _open_wait(self, pending: str) -> None:
        """Start, or move, the window in which this sentence is kept off screen."""
        if not self._defer_t0:
            perf.emit("caption", "defer", chars=len(pending))
            self._wait_segment = self._segment_seq
        self._defer_t0 = time.monotonic() if not self._defer_t0 else self._defer_t0
        self._defer_text = pending
        self._defer_chars = len(pending)

    def _close_wait(self, pending: str, action: str) -> None:
        """Close the wait once nothing is pending any more.

        A wait ends however it ends -- drawn, swapped in, or dropped -- so that
        the next sentence starts its own clock. Leaving it open would report one
        sentence's wait as part of the next one.
        """
        if not self._defer_t0 or pending:
            return
        perf.emit(
            "caption",
            "hold",
            wait=round((time.monotonic() - self._defer_t0) * 1000.0, 1),
            action=action,
            chars=self._defer_chars,
            shown=len(self.line),
        )
        self._defer_t0 = 0.0
        self._defer_text = ""
        self._defer_chars = 0

    def set_text(self, text: str) -> str:
        cleaned = _collapse_repeats((text or "").strip())
        if not cleaned:
            return "empty"
        self.have_translation = True
        previous = self._pending

        # B1: diff pre-check — if new text just extends current line, append directly
        if self.line and cleaned.startswith(self.line):
            appended = cleaned[len(self.line):]
            if appended:
                # Grow by the new characters only, bypassing hold/settle logic
                self.line = cleaned
                self._apply_line(self.line, "grow")
                return "grow"

        action, line, pending, anchor, settle, _deadline = step_caption(
            self.line,
            self._pending,
            self._growth_stopped_at,
            self._settle_until,
            cleaned,
            time.monotonic(),
            self._pace(),
            self._next_utterance_at,
        )
        if action == "drop":
            return action
        if action == "defer":
            # A translation arrives one character at a time, so most of what
            # arrives while a sentence waits is that same sentence growing or
            # being revised. Text from the next utterance can only appear after a
            # segment boundary, and only then does the waiting sentence get lost.
            if (
                previous
                and self._segment_seq != self._wait_segment
                and classify_caption(previous, pending) == "sentence"
            ):
                perf.emit(
                    "caption",
                    "overwrite",
                    held=round((time.monotonic() - self._defer_t0) * 1000.0, 1)
                    if self._defer_t0
                    else -1.0,
                    chars=len(pending),
                    lost=len(previous),
                )
            self._open_wait(pending)
        self._pending = pending
        self._note_pending(pending)
        self._growth_stopped_at = anchor
        self._settle_until = settle
        self._close_wait(pending, action)
        # Only on the packet-arrival path. The flush path has just reassigned the
        # anchor to now, so it would report a fresh hold rather than the one that
        # expired, which is the opposite of what this column is for.
        self._emit_deadline(action)
        if action in ("grow", "swap"):
            self._next_utterance_at = 0.0
        if action in ("grow", "char", "swap"):
            self._apply_line(line, action)
        self._arm_hold()
        return action

    def _swap_deadline(self) -> float:
        return swap_deadline_why(
            self._growth_stopped_at,
            self._settle_until,
            self.line,
            self._pace(),
            self._next_utterance_at,
        )[0]

    def _emit_deadline(self, action: str) -> None:
        """Record every candidate deadline and the one that actually applied.

        Emitted on each decision rather than derived later, because the three
        candidates overlap in time and a draw blocked by one of them looks
        identical to a draw blocked by another unless the reason was written down
        at the moment it was true.
        """
        if not self._growth_stopped_at:
            return
        deadline, name, candidates = swap_deadline_why(
            self._growth_stopped_at,
            self._settle_until,
            self.line,
            self._pace(),
            self._next_utterance_at,
        )
        now = time.monotonic()
        perf.emit(
            "caption",
            "deadline",
            action=action,
            # Which of the three clocks set it. This is the column that answers
            # "is the display leg the hold, the settle, or the next utterance".
            why=name,
            wait=round((deadline - now) * 1000.0, 1),
            hold_in=round(
                (candidates.get("hold", deadline) - now) * 1000.0, 1
            ),
            settle_in=round(
                (candidates.get("settle", deadline) - now) * 1000.0, 1
            ),
            readable_in=round(
                (candidates.get("readable_at", deadline) - now) * 1000.0, 1
            ),
            next_utt_in=round(
                (candidates.get("next_utterance", deadline) - now) * 1000.0, 1
            ),
            hold_ms=hold_ms(self.line, self._pace()),
            chars=len(self.line),
        )

    def _arm_hold(self) -> None:
        if self._hold_job is not None:
            self.root.after_cancel(self._hold_job)
            self._hold_job = None
        if not self._pending:
            return
        wait = int(round((self._swap_deadline() - time.monotonic()) * 1000))
        self._hold_job = self.root.after(max(50, wait), self._flush_pending)

    def _flush_pending(self) -> None:
        self._hold_job = None
        action, line, pending, anchor, settle, _deadline = flush_caption(
            self.line,
            self._pending,
            self._growth_stopped_at,
            self._settle_until,
            time.monotonic(),
            self._pace(),
            self._next_utterance_at,
        )
        self._pending = pending
        self._note_pending(pending)
        self._growth_stopped_at = anchor
        self._settle_until = settle
        self._close_wait(pending, action)
        if action in ("grow", "swap"):
            self._next_utterance_at = 0.0
        if action in ("grow", "char", "swap"):
            # Name the action here too. Without it a paint that came out of the
            # hold is reported as whatever the last packet was, and every swap
            # that had to wait looks like a defer.
            self._held_paint = True
            self._apply_line(line, action)
            self._held_paint = False
        if action == "wait" or self._pending:
            self._arm_hold()

    def _apply_line(self, text: str, action: str = "") -> None:
        # The caller says why this line is changing, so the paint probe can
        # tell an immediate redraw apart from a held one.
        if action:
            self._trace_action = action
        self._prev_chars = len(self.line)
        self.line = (text or "").strip()
        action = caption_action(self.closed, self.line, self.visible)
        if action == "withdraw":
            self.visible = False
            self.root.withdraw()
            return
        if action == "stay":
            return
        if action == "paint":
            self._paint()
            return
        self.show()

    def _pump(self) -> None:
        while True:
            try:
                kind, value, sent_at, recv_at = _COMMANDS.get_nowait()
            except queue.Empty:
                break
            lag = perf.wall_ms(recv_at) if isinstance(recv_at, (int, float)) else -1.0
            sent_wall = sent_at if isinstance(sent_at, (int, float)) else 0.0
            if kind == "text":
                # Keep our own copies: _paint clears the shared trace when the
                # packet turns into an immediate draw.
                self._trace_sent = sent_wall
                self._trace_recv = recv_at if isinstance(recv_at, (int, float)) else 0.0
                self._trace_action = ""
                action = self.set_text(value or "")
                if action in ("empty", "drop"):
                    # Nothing will be drawn for this packet; stop attributing
                    # a later repaint to it.
                    self._trace_sent = 0.0
                    self._trace_recv = 0.0
                    self._trace_action = ""
                else:
                    # An immediate draw already recorded itself inside
                    # _apply_line; a held one keeps the decision for later.
                    self._trace_action = action
                perf.emit(
                    "caption",
                    "text",
                    action=action,
                    net=perf.wall_ms(sent_wall) if sent_wall else -1.0,
                    pump=lag,
                    depth=_COMMANDS.qsize(),
                    chars=len(value or ""),
                )
            elif kind == "asr":
                pass
            elif kind == "note":
                self._trace_sent = sent_wall
                self._trace_recv = recv_at if isinstance(recv_at, (int, float)) else 0.0
                self._trace_action = "note"
                self.show_source(value or "")
            elif kind == "segment":
                self.begin_segment()
            elif kind == "status":
                self.set_status(value or "")
            elif kind == "style":
                self.apply_text_only(bool(value))
            elif kind == "font":
                self.apply_font(value)
            elif kind == "color":
                self.apply_color(value)
            elif kind == "arm":
                self.arm()
            elif kind == "clear":
                self.clear()
            elif kind == "switch_ok":
                self.show_cadence(value)
            elif kind == "raise":
                if self.line:
                    self.show()
            elif kind == "hide":
                self.hide()
        self.root.after(50, self._pump)


def _main() -> None:
    app = _Caption(_enable_dpi())
    print("caption window ready", flush=True)
    app.root.mainloop()


def start_caption() -> None:
    if _port_busy():
        return
    root = Path(__file__).resolve().parents[1]
    kwargs = {}
    if sys.platform == "win32":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        kwargs["startupinfo"] = startup
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    env = dict(os.environ)
    # The caption window is a second process. Appending to one shared file from
    # two processes collides on Windows, so it gets its own.
    env.setdefault("TRANSLATION_PERF_LOG", str(root / "logs" / "perf_caption.log"))
    subprocess.Popen(
        [sys.executable, "-m", "server.caption_window"],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        **kwargs,
    )


# The repeat rules live in translate.py and are shared: the generators need the
# clause rule as much as the window does, and two copies of a threshold drift.
_CHAR_RUN_MAX = CHAR_RUN_MAX
_CLAUSE_RUN_MAX = CLAUSE_RUN_MAX


def _collapse_repeats(text: str) -> str:
    return collapse_caption_repeats(text)


def _read_units(text: str) -> int:
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
    """Subtitle comfort bands tuned for more live, low-latency display.

    The speech line is kept short enough to feel timely in a live captioning
    situation, but still leaves a little extra room when the pace falls below a
    comfortable reading speed.
    """
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


# Which candidate actually set the deadline. The display leg was reported as one
# number and then split two ways, and neither split held up: the hold explains
# the tail but not the median, and the median sits near SETTLE_MS. Three
# different clocks can push the deadline out, and which one did is the whole
# question -- so it is recorded rather than inferred afterwards from overlapping
# intervals, which also avoids having to assume a hold never re-enters.
#
# Returned alongside the deadline so the caller does not have to recompute any of
# it. The name is the constraint that produced the returned value.
def swap_deadline_why(
    growth_stopped_at: float,
    settle_until: float,
    line: str = "",
    pace: float = 1.0,
    next_utterance_at: float = 0.0,
) -> tuple[float, str, dict]:
    candidates: dict[str, float] = {}
    # Unconditional, including when growth_stopped_at is 0.0. _arm_hold reaches
    # here with pending text set and the anchor still at its initial value, and
    # dropping the hold candidate there would fire the flush immediately instead
    # of one hold later. The equivalence check over 1280 combinations is what
    # caught this; the guard looked harmless because it agrees with the truthy
    # case.
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
        # The next-utterance branch clamps the max, so it can pull the deadline
        # earlier rather than push it later.
        ceiling = max(
            candidates["readable_at"],
            candidates["next_utterance"],
            candidates.get("settle", 0.0),
        )
        floor = max(candidates["hold"], candidates.get("settle", 0.0))
        deadline = min(floor, ceiling)
    else:
        deadline = max(candidates["hold"], candidates.get("settle", 0.0))

    # Name the candidate that equals the answer. On a tie the later one wins,
    # because that is the one still running when the clock expired.
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


def _send(kind: str, value) -> None:
    try:
        # Wall clock inside the packet: the caption window compares its own
        # receipt and paint times against it, so both sides join on one clock.
        sent_at = time.time()
        payload = json.dumps(
            {"kind": kind, "value": value, "sentAt": sent_at}, ensure_ascii=False
        ).encode("utf-8")
        _SOCK.sendto(payload, ("127.0.0.1", CAPTION_PORT))
    except OSError:
        return
    perf.emit("caption", "send", kind=kind, bytes=len(payload), sent_at=sent_at)


def update_caption(payload: dict) -> None:
    kind = payload.get("type")
    if kind == "segment_start":
        _send("segment", "")
    elif kind == "translation":
        _send("text", payload.get("text") or "")
    elif kind == "capture-error":
        _send("text", payload.get("error") or payload.get("message") or "")
    elif kind == "status" and payload.get("level") == "warn":
        _send("text", payload.get("message") or "")


def note_caption(text: str) -> None:
    _send("note", text)


def hide_caption() -> None:
    _send("hide", None)


def set_text_only(enabled: bool) -> None:
    _send("style", enabled)


def set_font_size(px: int) -> None:
    try:
        size = int(px)
    except (TypeError, ValueError):
        return
    _send("font", size)


def set_font_color(color: str) -> None:
    if isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        _send("color", color.lower())


def arm_caption() -> None:
    _send("arm", None)


def clear_caption() -> None:
    """Drop whatever is on screen, without closing the window.

    Distinct from hide_caption: that also marks the window closed, so the next
    packet has to re-arm it. This is for a configuration change mid-playback,
    where the window should stay up and simply stop showing the old cadence's
    text.
    """
    _send("clear", None)


def ack_switch(ms: int) -> None:
    """Tell the window the cadence change landed, so it can stop saying 切换中."""
    _send("switch_ok", int(ms))


def raise_caption() -> None:
    _send("raise", None)


if __name__ == "__main__":
    threading.Thread(target=_listen, args=(_COMMANDS,), daemon=True).start()
    _main()
