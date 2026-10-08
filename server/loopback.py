"""Windows speaker-loopback capture for the local translation session.

This is the product's audio source (TRANSLATION_AUDIO_SOURCE=loopback, the
default). It captures what the machine is playing, which has no dependency on
Chrome, survives fullscreen and DRM, and cannot disagree with itself about the
sample rate -- the class of failure that tab capture kept producing.

It is also the *only* producer. It used to run alongside the extension's socket
audio, both pushing 100ms frames into the same Session, and the splitter saw the
clip and the machine's output interleaved (REPORT 23). ws_server now drops socket
PCM while this is running; the exclusivity is the fix, not a preference.

The gain is measured rather than assumed, and that is deliberate. A 440Hz tone
played at amplitude 0.5 comes back from this machine's default speaker at a peak
of about 0.024 -- 26 dB down -- and the ratio barely moved when the system volume
was changed, so it looks like a property of the device path. "Looks like" is not
"is": another driver, or an enhancement layer like Nahimic, can attenuate by
something else, and the VAD threshold is absolute, so a quiet input silently
reads as non-speech. So this measures its own signal and derives the multiplier,
within configured bounds, and writes both numbers to the perf log -- because
"the gain is 7.9x" is a fact that has to be readable afterwards, not inferred
from a quiet transcript.
"""

from __future__ import annotations

import asyncio
import collections

import numpy as np

from . import perf
from .config import (
    LOOPBACK_GAIN_MAX,
    LOOPBACK_GAIN_MIN,
    LOOPBACK_TARGET_PEAK,
    SAMPLE_RATE,
)

FRAME_SAMPLES = 1600

# How long a level window the gain is derived from, and how often it is revised.
# Long enough to span a pause (so a silent stretch does not drive the gain to its
# ceiling), short enough to follow a real change within a couple of seconds.
_LEVEL_WINDOW = 50          # ~5s at 100ms frames
_REVISE_EVERY = 20          # ~2s
# Below this the material is silence and no gain can be derived from it.
_SILENCE_FLOOR = 1e-4
# Level target: the loudest frames, not the average, so a pause cannot pull the
# gain up until speech clips.
_LEVEL_QUANTILE = 0.90


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
    return microphone, speaker.name


class LoopbackLevel:
    """Measures the captured level and derives the gain to compensate for it."""

    def __init__(self) -> None:
        self.levels: collections.deque = collections.deque(maxlen=_LEVEL_WINDOW)
        self.frames = 0
        self.gain = 1.0
        self._logged_gain = None
        self.derived_from = 0.0

    def observe(self, mono: np.ndarray) -> float:
        """Record this frame's level; return the gain to apply to it."""
        if mono.size:
            self.levels.append(float(np.abs(mono).max()))
        self.frames += 1
        if self.frames % _REVISE_EVERY == 0:
            self._revise()
        return self.gain

    def _revise(self) -> None:
        if not self.levels:
            return
        level = float(np.quantile(np.asarray(self.levels, dtype=np.float64), _LEVEL_QUANTILE))
        self.derived_from = level
        if level < _SILENCE_FLOOR:
            # Nothing but silence so far: leave the gain alone rather than wind it
            # up against the ceiling on a quiet stretch.
            return
        want = LOOPBACK_TARGET_PEAK / level
        want = max(LOOPBACK_GAIN_MIN, min(LOOPBACK_GAIN_MAX, want))
        # Ease towards it, so a pause and a loud passage do not make the level
        # jump between chunks.
        self.gain = float(self.gain + (want - self.gain) * 0.5)
        if self._logged_gain is None or abs(self.gain - self._logged_gain) > 0.25 * self._logged_gain:
            self._logged_gain = self.gain
            perf.emit(
                "loopback",
                "gain",
                gain=round(self.gain, 3),
                measured_level=round(level, 6),
                target=LOOPBACK_TARGET_PEAK,
            )


async def run_loopback(session, send, stop_event: asyncio.Event) -> None:
    level = LoopbackLevel()
    try:
        microphone, speaker_name = await asyncio.to_thread(_open_loopback)
        with microphone.recorder(samplerate=SAMPLE_RATE, channels=2) as recorder:
            await send({
                "type": "status",
                "level": "info",
                "message": f"已连接系统回环音频: {microphone.name}",
            })
            perf.emit("loopback", "open", device=microphone.name, speaker=speaker_name)
            stats = {"frames": 0, "raw_peak": 0.0, "out_peak": 0.0}
            while not stop_event.is_set():
                data = await asyncio.to_thread(recorder.record, numframes=FRAME_SAMPLES)
                samples = np.asarray(data, dtype=np.float32)
                if samples.ndim == 2:
                    samples = samples.mean(axis=1)
                raw_peak = float(np.abs(samples).max()) if samples.size else 0.0
                gain = level.observe(samples)
                if gain != 1.0:
                    samples = samples * gain
                out_peak = float(np.abs(samples).max()) if samples.size else 0.0
                stats["frames"] += 1
                stats["raw_peak"] = max(stats["raw_peak"], raw_peak)
                stats["out_peak"] = max(stats["out_peak"], out_peak)
                # One line every ~10s: what the device actually handed over, what
                # gain was applied, and what went into the splitter. Without this
                # the only symptom of a dead capture is an empty transcript, which
                # reads the same as a quiet clip.
                if stats["frames"] % 20 == 0:
                    perf.emit(
                        "loopback",
                        "level",
                        frames=stats["frames"],
                        raw_peak=round(stats["raw_peak"], 6),
                        out_peak=round(stats["out_peak"], 6),
                        gain=round(gain, 2),
                    )
                    stats["raw_peak"] = 0.0
                    stats["out_peak"] = 0.0
                payload = _to_pcm16(samples)
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
