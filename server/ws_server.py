"""Local WebSocket server. Binary frames are int16 PCM, text frames are JSON."""

from __future__ import annotations

import asyncio
import json

from websockets.asyncio.server import serve

from .asr import AsrEngine
from .caption_window import (
    arm_caption,
    hide_caption,
    note_caption,
    raise_caption,
    set_font_size,
    set_text_only,
    start_caption,
)
from .config import WS_HOST, WS_PORT
from .pipeline import Session, emit_json


def style_font_px(data: dict):
    """Font size from a config or style frame. Missing means leave it; 0 is still a value."""
    if not isinstance(data, dict) or data.get("type") not in ("config", "style"):
        return None
    if "fontPx" not in data or data.get("fontPx") is None:
        return None
    return data.get("fontPx")


def _apply_caption_controls(data: dict) -> None:
    if "textOnly" in data:
        set_text_only(bool(data.get("textOnly")))
    font_px = style_font_px(data)
    if font_px is not None:
        set_font_size(font_px)


async def handle(ws, asr: AsrEngine) -> None:
    session = Session(asr)
    print("client connected")
    worker = asyncio.create_task(session.worker(lambda payload: emit_json(ws, payload)))
    try:
        async for message in ws:
            if isinstance(message, bytes):
                for payload in session.push_pcm(message):
                    await emit_json(ws, payload)
                continue
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue
            if data.get("type") == "config":
                if "language" in data:
                    session.set_language(data.get("language") or "auto")
                if any(key in data for key in ("apiEnabled", "apiUrl", "apiKey", "apiModel")):
                    session.set_api(data)
                _apply_caption_controls(data)
                if session.api_error:
                    notice = {"type": "status", "level": "warn", "message": session.api_error}
                elif session.api:
                    notice = {"type": "status", "level": "info", "message": "已连接，翻译走外部接口"}
                else:
                    notice = {"type": "status", "level": "info", "message": "已连接，翻译走混元翻译"}
                await emit_json(ws, notice)
            elif data.get("type") == "note":
                note_caption(data.get("text") or "")
            elif data.get("type") == "hide":
                hide_caption()
            elif data.get("type") == "style":
                _apply_caption_controls(data)
            elif data.get("type") == "arm":
                arm_caption()
            elif data.get("type") == "raise":
                raise_caption()
            elif data.get("type") == "flush":
                for payload in session.flush():
                    await emit_json(ws, payload)
    finally:
        print("client gone")
        worker.cancel()
        await session.close()


async def main() -> None:
    start_caption()
    asr = AsrEngine()
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
