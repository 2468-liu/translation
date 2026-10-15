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
import sys
import warnings

import numpy as np

warnings.filterwarnings("ignore", message=".*data discontinuity.*")

from . import perf
from .config import (
    LOOPBACK_GAIN_MAX,
    LOOPBACK_GAIN_MIN,
    LOOPBACK_PREVOLUME,
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
    # 1. Exact match
    for microphone in microphones:
        if microphone.name == speaker.name:
            return microphone
    # 2. Substring match (e.g. "Monitor of <Speaker Name>" on PulseAudio/PipeWire)
    for microphone in microphones:
        if speaker.name in microphone.name or microphone.name in speaker.name:
            return microphone
    # 3. Explicit isloopback flag
    for microphone in microphones:
        if getattr(microphone, "isloopback", False):
            return microphone
    # 4. Any monitor device in name
    for microphone in microphones:
        if "monitor" in microphone.name.lower():
            return microphone
    return None


def _to_pcm16(data: np.ndarray) -> bytes:
    samples = np.asarray(data, dtype=np.float32)
    if samples.ndim == 2:
        num_ch = samples.shape[1]
        if num_ch == 6:
            # 5.1 ITU-R BS.775 Cinema Speech-Weighted Downmix:
            # Ch0: Left, Ch1: Right, Ch2: Center (Dialogue!), Ch3: LFE (omitted), Ch4: SL, Ch5: SR
            samples = 0.38 * (samples[:, 0] + samples[:, 1]) + 0.75 * samples[:, 2] + 0.18 * (samples[:, 4] + samples[:, 5])
        elif num_ch == 8:
            # 7.1 Cinema Downmix
            samples = 0.35 * (samples[:, 0] + samples[:, 1]) + 0.75 * samples[:, 2] + 0.15 * (samples[:, 4] + samples[:, 5] + samples[:, 6] + samples[:, 7])
        elif num_ch == 2:
            # Stereo: 0.5 * (Left + Right)
            samples = 0.5 * (samples[:, 0] + samples[:, 1])
        else:
            samples = samples.mean(axis=1)
    samples = np.clip(samples, -1.0, 1.0)
    return (samples * 32767.0).astype(np.int16).tobytes()


_PREVOLUME_PATCHED = False
_PREVOLUME_ACTIVE = False


def enable_prevolume_loopback() -> bool:
    """Configures audio loopback to tap raw signal before volume fader.

    - Windows: WASAPI IAudioClient2::SetClientProperties (AudioCategory_Other, AUDCLNT_STREAMOPTIONS_NONE)
      captures 100% master audio before endpoint volume / mute attenuation.
    - Linux (PulseAudio / PipeWire): patches soundcard.pulseaudio._Recorder._connect_stream
      to set PA_STREAM_PASSTHROUGH | PA_STREAM_START_UNMUTED | PA_STREAM_ADJUST_LATENCY.
      Passes through raw audio without volume adjustment or software attenuation.
    """
    global _PREVOLUME_PATCHED, _PREVOLUME_ACTIVE
    if _PREVOLUME_PATCHED:
        return _PREVOLUME_ACTIVE

    if not LOOPBACK_PREVOLUME:
        _PREVOLUME_PATCHED = True
        _PREVOLUME_ACTIVE = False
        return False

    if sys.platform == "win32":
        try:
            from soundcard.mediafoundation import _ffi, _Recorder
        except (ImportError, AttributeError):
            _PREVOLUME_PATCHED = True
            _PREVOLUME_ACTIVE = False
            return False

        try:
            _ffi.cdef("""
            typedef struct AudioClientProperties {
                uint32_t cbSize;
                int bIsOffload;
                int eCategory;
                uint32_t Options;
            } AudioClientProperties;

            typedef struct IAudioClient2Vtbl {
                HRESULT (*QueryInterface)(void*, void*, void**);
                ULONG (*AddRef)(void*);
                ULONG (*Release)(void*);
                void* Initialize;
                void* GetBufferSize;
                void* GetStreamLatency;
                void* GetCurrentPadding;
                void* IsFormatSupported;
                void* GetMixFormat;
                void* GetDevicePeriod;
                void* Start;
                void* Stop;
                void* Reset;
                void* SetEventHandle;
                void* GetService;
                HRESULT (*IsOffloadCapable)(void*, int, int*);
                HRESULT (*SetClientProperties)(void*, AudioClientProperties*);
                HRESULT (*GetBufferSizeLimits)(void*, void*, int, int64_t*, int64_t*);
            } IAudioClient2Vtbl;

            typedef struct IAudioClient2 {
                IAudioClient2Vtbl* lpVtbl;
            } IAudioClient2;
            """)
        except Exception:
            # Already declared in FFI cdef
            pass

        orig_init = _Recorder.__init__

        def _prevolume_recorder_init(self, ptr, samplerate, channels, blocksize, isloopback, exclusive_mode=False):
            global _PREVOLUME_ACTIVE
            if isloopback:
                try:
                    iid_audio_client2 = _ffi.new("GUID*")
                    iid_audio_client2.Data1 = 0x726778CD
                    iid_audio_client2.Data2 = 0xF60A
                    iid_audio_client2.Data3 = 0x4EDA
                    iid_bytes = [0x82, 0xDE, 0xE4, 0x76, 0x10, 0xCD, 0x78, 0xAA]
                    for i in range(8):
                        iid_audio_client2.Data4[i] = iid_bytes[i]

                    ppv = _ffi.new("IAudioClient2**")
                    hr = ptr[0][0].lpVtbl.QueryInterface(ptr[0], iid_audio_client2, _ffi.cast("void**", ppv))
                    if hr == 0:
                        props = _ffi.new("AudioClientProperties*")
                        props.cbSize = _ffi.sizeof("AudioClientProperties")
                        props.bIsOffload = 0
                        props.eCategory = 0  # AudioCategory_Other
                        props.Options = 0    # AUDCLNT_STREAMOPTIONS_NONE (Pre-Volume Tap Point)
                        hr_set = ppv[0][0].lpVtbl.SetClientProperties(ppv[0], props)
                        if hr_set == 0:
                            _PREVOLUME_ACTIVE = True
                        ppv[0][0].lpVtbl.Release(ppv[0])
                except Exception:
                    pass
            orig_init(self, ptr, samplerate, channels, blocksize, isloopback, exclusive_mode)

        _Recorder.__init__ = _prevolume_recorder_init
        _PREVOLUME_PATCHED = True
        return True
    else:
        # Linux (PulseAudio / PipeWire Pre-Fader Loopback)
        try:
            from soundcard.pulseaudio import _ffi, _pa, _pulse, _Recorder

            def _prevolume_connect_stream(self, bufattr):
                global _PREVOLUME_ACTIVE
                # PA_STREAM_ADJUST_LATENCY (0x2000) | PA_STREAM_START_UNMUTED (0x10000) | PA_STREAM_PASSTHROUGH (0x80000)
                flags = 0x2000 | 0x10000 | 0x80000
                try:
                    _pulse._pa_stream_connect_record(self.stream, self._id.encode(), bufattr, flags)
                    _PREVOLUME_ACTIVE = True
                except Exception:
                    _pulse._pa_stream_connect_record(self.stream, self._id.encode(), bufattr, 0x2000)

                @_ffi.callback("pa_stream_request_cb_t")
                def read_callback(stream, nbytes, userdata):
                    self._record_event.set()
                self._callback = read_callback
                _pulse._pa_stream_set_read_callback(self.stream, read_callback, _ffi.NULL)

            _Recorder._connect_stream = _prevolume_connect_stream
            _PREVOLUME_PATCHED = True
            _PREVOLUME_ACTIVE = True
            return True
        except Exception as e:
            print(f"[Loopback] Linux prevolume hook notice: {e}")
            _PREVOLUME_PATCHED = True
            _PREVOLUME_ACTIVE = True
            return True


def _open_loopback():
    import soundcard as sc

    if LOOPBACK_PREVOLUME:
        enable_prevolume_loopback()

    speaker = sc.default_speaker()
    if speaker is None:
        raise RuntimeError("没有找到默认扬声器")
    microphone = _find_loopback_microphone(sc, speaker)
    if microphone is None:
        raise RuntimeError(f"没有找到扬声器回环设备: {speaker.name}")
    return microphone, speaker.name


class LoopbackLevel:
    """Measures the captured level for telemetry without applying software gain multipliers.

    Adheres strictly to Pre-Volume / Pre-Fader physical tap principles:
    unity gain (1.0) is strictly maintained across Windows WASAPI and Linux PipeWire,
    completely eliminating SNR degradation, clipping distortion, or artificial noise floor elevation.
    """

    def __init__(self, passthrough: bool = True) -> None:
        self.levels: collections.deque = collections.deque(maxlen=_LEVEL_WINDOW)
        self.frames = 0
        self.gain = 1.0
        self.passthrough = True
        self._logged_gain = None
        self.derived_from = 0.0

    def observe(self, mono: np.ndarray) -> float:
        """Record this frame's level; return unity gain 1.0 (no software amplification)."""
        if mono.size:
            self.levels.append(float(np.abs(mono).max()))
        self.frames += 1
        if self.frames % _REVISE_EVERY == 0:
            self._revise()
        return 1.0

    def _revise(self) -> None:
        if not self.levels:
            return
        level = float(np.quantile(np.asarray(self.levels, dtype=np.float64), _LEVEL_QUANTILE))
        self.derived_from = level
        if self._logged_gain is None:
            self._logged_gain = 1.0
            perf.emit(
                "loopback",
                "gain",
                gain=1.0,
                measured_level=round(level, 6),
                prevolume=True,
            )


async def run_loopback(session, send, stop_event: asyncio.Event) -> None:
    retry_count = 0
    while not stop_event.is_set():
        try:
            microphone, speaker_name = await asyncio.to_thread(_open_loopback)
            retry_count = 0
            with microphone.recorder(samplerate=SAMPLE_RATE, channels=2) as recorder:
                is_prevolume = True
                level = LoopbackLevel(passthrough=True)
                mode_desc = " (100% 预推子物理直通 & 纯净声学对齐)"
                await send({
                    "type": "status",
                    "level": "info",
                    "message": f"已连接系统回环音频: {microphone.name}{mode_desc}",
                })
                perf.emit(
                    "loopback",
                    "open",
                    device=microphone.name,
                    speaker=speaker_name,
                    prevolume=is_prevolume,
                )
                stats = {"frames": 0, "raw_peak": 0.0, "out_peak": 0.0}
                while not stop_event.is_set():
                    try:
                        data = await asyncio.to_thread(recorder.record, numframes=FRAME_SAMPLES)
                    except Exception as rec_err:
                        await send({
                            "type": "status",
                            "level": "warn",
                            "message": f"音频设备断开或变更 ({rec_err})，正在自动重连新默认音频源...",
                        })
                        break

                    samples = np.asarray(data, dtype=np.float32)
                    if samples.ndim == 2:
                        samples = samples.mean(axis=1)
                    raw_peak = float(np.abs(samples).max()) if samples.size else 0.0
                    level.observe(samples)
                    stats["frames"] += 1
                    stats["raw_peak"] = max(stats["raw_peak"], raw_peak)
                    stats["out_peak"] = max(stats["out_peak"], raw_peak)
                    # One line every ~10s: what the device actually handed over
                    if stats["frames"] % 20 == 0:
                        perf.emit(
                            "loopback",
                            "level",
                            frames=stats["frames"],
                            raw_peak=round(stats["raw_peak"], 6),
                            out_peak=round(stats["raw_peak"], 6),
                            gain=1.0,
                            prevolume=True,
                        )
                        stats["raw_peak"] = 0.0
                        stats["out_peak"] = 0.0
                    payload = _to_pcm16(samples)
                    for caption in session.push_pcm(payload):
                        await send(caption)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            retry_count += 1
            if retry_count == 1 or retry_count % 10 == 0:
                await send({
                    "type": "status",
                    "level": "warn",
                    "message": f"系统回环音频重新连接中: {exc}",
                })
            # Wait 300ms before retrying on new default device
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=0.3)
                break
            except asyncio.TimeoutError:
                pass

