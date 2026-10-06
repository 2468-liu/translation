"""Windows speaker-loopback capture for the local translation session."""

from __future__ import annotations

import asyncio

import numpy as np

from .config import SAMPLE_RATE

FRAME_SAMPLES = 1600


def _find_loopback_microphone(soundcard, speaker):
    microphones = soundcard.all_microphones(include_loopback=True)
    for microphone in microphones:
        if microphone.name == speaker.name:
            return microphone
    for microphone in microphones:
        if speaker.name in microphone.name or microphone.name in speaker.name:
            return microphone
    return None


def _to_pcm16(data: np.ndarray) -> bytes:
    samples = np.asarray(data, dtype=np.float32)
    if samples.ndim == 2:
        samples = samples.mean(axis=1)
    samples = np.clip(samples, -1.0, 1.0)
    return (samples * 32767.0).astype(np.int16).tobytes()


def _open_loopback():
    import soundcard as sc

    speaker = sc.default_speaker()
    if speaker is None:
        raise RuntimeError("没有找到默认扬声器")
    microphone = _find_loopback_microphone(sc, speaker)
    if microphone is None:
        raise RuntimeError(f"没有找到扬声器回环设备: {speaker.name}")
    return microphone


async def run_loopback(session, send, stop_event: asyncio.Event) -> None:
    try:
        microphone = await asyncio.to_thread(_open_loopback)
        with microphone.recorder(samplerate=SAMPLE_RATE, channels=2) as recorder:
            await send({
                "type": "status",
                "level": "info",
                "message": f"已连接系统回环音频: {microphone.name}",
            })
            while not stop_event.is_set():
                data = await asyncio.to_thread(recorder.record, numframes=FRAME_SAMPLES)
                payload = _to_pcm16(data)
                for caption in session.push_pcm(payload):
                    await send(caption)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await send({
            "type": "status",
            "level": "warn",
            "message": f"系统回环音频失败: {exc}",
        })
