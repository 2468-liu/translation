"""Local WebSocket server. Binary frames are int16 PCM, text frames are JSON."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import threading
from pathlib import Path

from websockets.asyncio.server import serve

from . import perf
from . import config
from .asr import AsrEngine, build_asr
from .caption_window import (
    ack_switch,
    arm_caption,
    clear_caption,
    hide_caption,
    note_caption,
    raise_caption,
    set_font_size,
    set_font_color,
    set_text_only,
    start_caption,
    update_caption,
)
from .config import LOOPBACK_ENABLED, WS_HOST, WS_PORT
from .loopback import run_loopback
from .pipeline import Session


def style_font_px(data: dict):
    """Font size from a config or style frame. Missing means leave it; 0 is still a value."""
    if not isinstance(data, dict) or data.get("type") not in ("config", "style"):
        return None
    if "fontPx" not in data or data.get("fontPx") is None:
        return None
    return data.get("fontPx")


def style_font_color(data: dict):
    if not isinstance(data, dict) or data.get("type") not in ("config", "style"):
        return None
    color = data.get("color")
    if isinstance(color, str) and len(color) == 7 and color.startswith("#"):
        try:
            int(color[1:], 16)
        except ValueError:
            return None
        return color
    return None


STATE_PORT = 8767

# The session serving the current connection. Held so the caption window's switch
# can reach it: the window is a separate process and cannot hold a reference.
_CURRENT_SESSION = None


def _switch_partial_interval(ms: int) -> None:
    """Apply a cadence change to the live session and to any started later.

    Both halves are needed. Setting only the override would leave the session
    already running on the old value, because its splitter was built at
    construction; setting only the live splitter would be forgotten the moment
    the connection is re-established.
    """
    from . import vad

    vad.set_partial_interval(ms)
    session = _CURRENT_SESSION
    if session is not None:
        vad.apply_partial_interval(session.vad, ms)
        # Clear what is on screen and in flight. Half of one cadence and half of
        # another in the same caption would be read as neither.
        session.reset_for_media()
        clear_caption()
        ack_switch(ms)
    perf.emit("config", "switch", partial_interval_ms=ms)


def _apply_caption_controls(data: dict) -> None:
    if "textOnly" in data:
        set_text_only(bool(data.get("textOnly")))
    font_px = style_font_px(data)
    if font_px is not None:
        set_font_size(font_px)
    color = style_font_color(data)
    if color is not None:
        set_font_color(color)


def _listen_caption_state(loop: asyncio.AbstractEventLoop, send) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", STATE_PORT))
    while True:
        data, _ = sock.recvfrom(65535)
        try:
            message = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        kind = message.get("kind")

        # kind == "config" used to be handled here, over UDP, for the caption
        # window's own 250/750 buttons. Those buttons are gone -- the control is
        # in the extension, which reaches _switch_partial_interval through the
        # WebSocket config frame instead. The listener stays because kind ==
        # "state" still carries the window's position lock and transparency, and
        # removing it would take those with it.
        if kind != "state":
            continue
        value = message.get("value") or {}
        payload = {"type": "toggle_transparent", "value": bool(value.get("textOnly", False))}
        if "positionLocked" in value:
            payload["positionLocked"] = bool(value.get("positionLocked"))
        if "fontPx" in value:
            payload["fontPx"] = int(value.get("fontPx"))
        if "fontColor" in value:
            payload["fontColor"] = value.get("fontColor")
        try:
            asyncio.run_coroutine_threadsafe(send(payload), loop)
        except RuntimeError:
            break


async def handle(ws, asr: AsrEngine | None) -> None:
    global _CURRENT_SESSION
    session = Session(asr)
    _CURRENT_SESSION = session
    caption_enabled = True
    caption_text_only = False
    caption_position_locked = True
    loopback_stop = asyncio.Event()
    loopback_task = None

    async def send(payload: dict) -> None:
        if payload.get("type") in ("translation", "segment_start"):
            # Server-side reference point: pair it with the caption window's
            # `caption.paint since_send` line via the shared wall clock.
            perf.emit(
                "ws",
                "send",
                type=payload.get("type"),
                final=payload.get("final", False),
                chars=len(str(payload.get("text") or "")),
                sent_at=perf.wall(),
            )
        if caption_enabled:
            update_caption(payload)
        await ws.send(json.dumps(payload, ensure_ascii=False))

    state_listener = threading.Thread(
        target=_listen_caption_state,
        args=(asyncio.get_running_loop(), send),
        daemon=True,
    )
    state_listener.start()

    print("client connected")
    worker = asyncio.create_task(session.worker(send))
    try:
        async for message in ws:
            if isinstance(message, bytes):
                # The exclusion lives here because here the source is known. With
                # loopback capturing, a socket frame is a second producer talking
                # into the same splitter, and that interleaving is what fragmented
                # every replay (REPORT 23). Text frames are still served -- the
                # extension keeps this connection for config, arm, flush, styles.
                if LOOPBACK_ENABLED:
                    continue
                for payload in session.push_pcm(message):
                    await send(payload)
                continue
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue
            if data.get("type") == "config":
                caption_enabled = not bool(data.get("audioOnly"))
                if not caption_enabled:
                    hide_caption()
                # Loopback is the product's capture path and it is mutually
                # exclusive with the socket's PCM -- both feeding this one
                # Session is what fragmented every replay (REPORT 23). The
                # extension keeps this connection for text: config, arm, flush,
                # styles, hide.
                if LOOPBACK_ENABLED and loopback_task is None:
                    loopback_task = asyncio.create_task(
                        run_loopback(session, send, loopback_stop)
                    )
                # Tell the client who owns capture, so it can stop encoding and
                # sending audio that would be dropped anyway. Sent here rather
                # than at connect time because a client that has not been told
                # yet may still be sending; both sides treat silence as "keep
                # doing what you were doing".
                await send({
                    "type": "capture-mode",
                    "audioSource": config.AUDIO_SOURCE,
                    "captureEnabled": not LOOPBACK_ENABLED,
                })
                if "language" in data:
                    session.set_language(data.get("language") or "auto")
                if any(key in data for key in ("apiEnabled", "apiUrl", "apiKey", "apiModel")):
                    session.set_api(data)
                # The caption window's 250/750 switch reaches _switch_partial_interval
                # over UDP. The extension's switch has to arrive here too, or the field
                # is accepted by the config frame and then dropped -- the extension owns
                # the controls now, so the WebSocket is the only path a user takes.
                if data.get("partial_interval_ms"):
                    _switch_partial_interval(int(data["partial_interval_ms"]))
                _apply_caption_controls(data)
                if "textOnly" in data:
                    caption_text_only = bool(data.get("textOnly"))
                if "positionLocked" in data:
                    caption_position_locked = bool(data.get("positionLocked"))
                if session.api_error:
                    notice = {"type": "status", "level": "warn", "message": session.api_error}
                elif session.api:
                    notice = {"type": "status", "level": "info", "message": "已连接，翻译走外部接口"}
                else:
                    notice = {"type": "status", "level": "info", "message": "已连接，翻译走混元翻译"}
                await send(notice)
            elif data.get("type") == "media-start":
                session.reset_for_media()
            elif data.get("type") == "note":
                note_caption(data.get("text") or "")
            elif data.get("type") == "hide":
                hide_caption()
            elif data.get("type") == "style":
                _apply_caption_controls(data)
                if "textOnly" in data:
                    caption_text_only = bool(data.get("textOnly"))
                if "positionLocked" in data:
                    caption_position_locked = bool(data.get("positionLocked"))
            elif data.get("type") == "toggle_transparent":
                caption_text_only = bool(data.get("value"))
                _apply_caption_controls({"type": "style", "textOnly": caption_text_only})
                await send({"type": "toggle_transparent", "value": caption_text_only})
            elif data.get("type") == "get_state":
                await send({
                    "type": "toggle_transparent",
                    "value": caption_text_only,
                    "positionLocked": caption_position_locked,
                })
            elif data.get("type") == "arm":
                arm_caption()
            elif data.get("type") == "raise":
                raise_caption()
            elif data.get("type") == "flush":
                for payload in session.flush():
                    await send(payload)
    finally:
        print("client gone")
        loopback_stop.set()
        if loopback_task and not loopback_task.done():
            loopback_task.cancel()
        hide_caption()
        worker.cancel()
        await session.close()


def _build_revision() -> str:
    """Which tree this process is, in one token, so daily use can tell versions apart.

    Comparing two builds by behaviour alone is how a merge gets judged on effects
    nobody intended: the transcript looks different and the only question is
    whether it is the change or the checkout. So the revision goes on the same
    line as the configuration.

    Dirty is reported separately because that is the more common case during
    ordinary editing, and a bare sha would otherwise read as a clean identifier.
    """
    def run(*cmd: str) -> str:
        try:
            done = subprocess.run(["git", *cmd], cwd=str(_repo_root()),
                                  capture_output=True, text=True, timeout=10)
            return done.stdout.strip() if done.returncode == 0 else ""
        except Exception:
            return ""

    sha = run("rev-parse", "--short", "HEAD")[:8]
    if not sha:
        return "nogit"
    status = run("status", "--porcelain")
    return f"{sha}+dirty" if status else sha


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _report_effective_config() -> None:
    """One line, machine-readable: what this process is actually running with.

    Read by scripts/manifest.py. Every name here is a value the run's behaviour
    depends on, including the ones that live in the caption window rather than in
    config.py -- SETTLE_MS in particular, because it is a candidate for the swap
    deadline and so a latency knob rather than a rendering one.
    """
    from . import caption_window, config

    fields = {
        "partial_interval_ms": config.PARTIAL_INTERVAL_MS,
        "asr_device": config.ASR_DEVICE,
        "asr_cpu_threads": config.ASR_CPU_THREADS,
        # The engine, and the cap. Without these, config.effective recorded
        # partial_interval_ms and mt_backend but not which recogniser answered, so a
        # qwen3 arm and a whisper arm both logged asr_device=cuda and could only be
        # told apart by the arm name -- the same substitution that made twelve "MTP on"
        # runs read as MTP off. It also makes perf_compare's block splitting reliable,
        # since that is what delimits a run.
        "asr_backend": config.ASR_BACKEND,
        "asr_max_tokens": config.QWEN3_ASR_MAX_TOKENS if config.ASR_BACKEND == "qwen3" else 0,
        "audio_native": os.environ.get("TRANSLATION_AUDIO_NATIVE", "0"),
        # Which translator answers, and how much of the conversation it is shown.
        # Both are invisible from outside -- the protocol is identical whichever
        # backend serves it -- and the context count is the variable under test,
        # so a run that does not record it cannot be compared to another run.
        "mt_backend": config.MT_BACKEND,
        "mt_context_sentences": config.MT_CONTEXT_SENTENCES,
        "audio_context_sentences": config.AUDIO_CONTEXT_SENTENCES,
        "audio_repeat_penalty": config.AUDIO_REPEAT_PENALTY,
        "audio_chinese_share_min": config.AUDIO_CHINESE_SHARE_MIN,
        "gemma_min_silence_ms": config.GEMMA_MIN_SILENCE_MS,
        "gemma_max_utterance_ms": config.GEMMA_MAX_UTTERANCE_MS,
        # The gate is a drop, and the merge window decides whether a short burst
        # survives; both are now injectable, so a run that does not record them
        # cannot be told apart from one that ran the other arm (REPORT 22).
        # Which producer feeds the splitter. Exactly one does, and which one it is
        # cannot be read off the transcript, so it goes in the record -- a run
        # whose loopback was silently closed looks identical to a quiet clip.
        "audio_source": config.AUDIO_SOURCE,
        "loopback_enabled": config.LOOPBACK_ENABLED,
        "vad_min_speech_ms": config.VAD_MIN_SPEECH_MS,
        "vad_merge_window_ms": config.VAD_MERGE_WINDOW_MS,
        "settle_ms": caption_window.SETTLE_MS,
        "hold_abs_min_ms": caption_window.HOLD_ABS_MIN_MS,
        "hold_abs_max_ms": caption_window.HOLD_ABS_MAX_MS,
        "readable_ratio": caption_window.READABLE_RATIO,
        "git_rev": _build_revision(),
        "pid": os.getpid(),
    }
    line = "effective.config " + json.dumps(fields, sort_keys=True)
    print(line, flush=True)
    # Also into the perf log, because stdout is consumed by the harness and
    # discarded. A record that only ever goes to a console is not a record: the
    # manifest looked in the perf log for this line, found nothing, and reported
    # that no run had ever reported its configuration -- which was true of the
    # file and false of the process.
    perf.emit("config", "effective", json=json.dumps(fields, sort_keys=True))


async def main() -> None:
    # The thresholds this process is actually running with, printed here rather
    # than read back by whoever wants to know them.
    #
    # Reading them from another process with "from server import config" reports
    # what the file says, under that reader's environment, not what is running.
    # That difference is invisible and it is the whole point: an arm that inherited
    # the previous arm's value, or a threshold injected into one process and not
    # the other, both look correct from outside and run differently. The manifest
    # reads this line instead of re-deriving it.
    _report_effective_config()
    if os.environ.get("TRANSLATION_AUDIO_ONLY") != "1":
        start_caption()
    # Two pipelines, one protocol. With AUDIO_NATIVE=1 the audio goes straight
    # to a model that translates it, and no recogniser is loaded at all -- which
    # is what fits the audio model and a translation model onto a 4GB card.
    asr: AsrEngine | None = None
    if os.environ.get("TRANSLATION_AUDIO_NATIVE") == "1":
        print("pipeline: audio-native (no ASR)")
    else:
        asr = build_asr()
        # The backend's own name, not a literal. This branch covers whisper *and*
        # qwen3 -- both load a recogniser and a separate text translator -- so a
        # hardcoded "whisper" printed "pipeline: whisper + text model" on the qwen3-hy
        # chain, where the only thing that gave the arm away was effective.config.
        # Reading an arm off a log line that can be wrong is the thing this repository
        # has been bitten by repeatedly; the arm identity has to come from the field
        # that is set, not from the branch it took.
        print(f"pipeline: {type(asr).__name__} + text model (asr_backend={config.ASR_BACKEND})")
    print(f"WebSocket ws://{WS_HOST}:{WS_PORT}/ws")
    async with serve(
        lambda ws: handle(ws, asr),
        WS_HOST,
        WS_PORT,
        max_size=1_000_000,
    ):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
