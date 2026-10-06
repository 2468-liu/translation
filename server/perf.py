"""Latency instrumentation. Observability only — never changes behaviour.

Every helper swallows its own failure, so a full disk or a closed stdout can
never break a caption.

Log format, one line per probe (append-only, safe to write from both the
server process and the caption window process):

    1770000000.123 pipeline.asr kind=partial qwait=8.4 asr=181.2 ...

`<wall>` is ``time.time()`` so lines from different processes line up, while
every ``*_ms`` field comes from ``perf_counter`` so durations stay exact.

    TRANSLATION_PERF=0        switch every probe off (default: on)
    TRANSLATION_PERF_LOG=...  write somewhere else (default: logs/perf.log)
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = Path(os.environ.get("TRANSLATION_PERF_LOG") or ROOT / "logs" / "perf.log")
ENABLED = os.environ.get("TRANSLATION_PERF", "1") != "0"

_LOCK = threading.Lock()
_FD: int | None = None
_OPENED = False


def mono() -> float:
    """perf_counter: for durations inside one process."""
    return time.perf_counter()


def ms(since: float) -> float:
    """Milliseconds elapsed since a ``mono()`` timestamp."""
    return (time.perf_counter() - since) * 1000.0


def wall() -> float:
    """time.time(): joinable across processes and machines."""
    return time.time()


def wall_ms(since: float) -> float:
    """Milliseconds elapsed since a ``wall()`` timestamp."""
    return (time.time() - since) * 1000.0


def _open() -> int | None:
    global _FD, _OPENED
    if _OPENED:
        return _FD
    _OPENED = True
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        # O_APPEND keeps concurrent writers on separate lines: one os.write of a
        # short line lands whole instead of interleaving mid-message.
        _FD = os.open(LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    except OSError:
        _FD = None
    return _FD


def _format(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        # Wall-clock epochs need their millisecond digits; durations do not.
        return f"{value:.3f}" if abs(value) >= 1e6 else f"{value:.1f}"
    return str(value)


def emit(src: str, tag: str, **fields) -> None:
    """Append ``<wall> <src>.<tag> key=value ...`` to the perf log."""
    if not ENABLED:
        return
    try:
        body = " ".join(f"{key}={_format(value)}" for key, value in fields.items())
        line = f"{time.time():.3f} {src}.{tag} {body}\n" if body else f"{time.time():.3f} {src}.{tag}\n"
    except Exception:
        return
    with _LOCK:
        fd = _open()
        if fd is not None:
            try:
                os.write(fd, line.encode("utf-8"))
                return
            except OSError:
                pass
    try:
        print(line, end="", flush=True)
    except Exception:
        pass
