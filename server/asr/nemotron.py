"""NVIDIA Nemotron 3.5 ASR (0.6B FastConformer-RNNT) via C API."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys
import threading
import numpy as np

from .. import config
from ..config import SAMPLE_RATE
from .text_filters import (
    is_hallucination,
    parse_nemotron_transcript,
    to_nemo_bcp47,
)


class _NemoBackendConfig(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_size_t),
        ("gpu", ctypes.c_int32),
    ]


class _NemoModelConfig(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_size_t),
        ("path", ctypes.c_char_p),
        ("name", ctypes.c_char_p),
    ]


class _NemoRecognizerConfig(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_size_t),
        ("backend", ctypes.POINTER(_NemoBackendConfig)),
        ("model", ctypes.POINTER(_NemoModelConfig)),
        ("streaming", ctypes.c_void_p),
        ("decoder", ctypes.c_void_p),
        ("vad", ctypes.c_void_p),
        ("endpointing", ctypes.c_void_p),
        ("postproc", ctypes.c_void_p),
        ("diar", ctypes.c_void_p),
        ("batching", ctypes.c_void_p),
    ]


class _NemoRecognitionOptions(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_size_t),
        ("request_id", ctypes.c_char_p),
        ("language_code", ctypes.c_char_p),
        ("interim_results", ctypes.c_bool),
        ("enable_word_time_offsets", ctypes.c_bool),
        ("enable_automatic_punctuation", ctypes.c_bool),
        ("verbatim_transcripts", ctypes.c_bool),
        ("profanity_filter", ctypes.c_bool),
        ("stop_history_eou_ms", ctypes.c_int32),
        ("speech_contexts", ctypes.c_void_p),
        ("speech_context_count", ctypes.c_size_t),
        ("max_alternatives", ctypes.c_int32),
        ("enable_speaker_diarization", ctypes.c_bool),
        ("max_speaker_count", ctypes.c_int32),
    ]


def _resolve_nemotron_model_path() -> Path:
    candidates = [
        config.MODELS_DIR / "NV3.5-ASR-0.6B" / "nemotron-3.5-asr-streaming-0.6b.q8_0.gguf",
        Path("D:/TranslationDesktop/models/NV3.5-ASR-0.6B/nemotron-3.5-asr-streaming-0.6b.q8_0.gguf"),
        Path("D:/models/NV3.5-ASR-0.6B/nemotron-3.5-asr-streaming-0.6b.q8_0.gguf"),
    ]
    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).resolve().parent
        candidates.insert(0, app_dir / "models" / "NV3.5-ASR-0.6B" / "nemotron-3.5-asr-streaming-0.6b.q8_0.gguf")
        candidates.append(Path.cwd() / "models" / "NV3.5-ASR-0.6B" / "nemotron-3.5-asr-streaming-0.6b.q8_0.gguf")
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError(
        f"Nemotron ASR model missing. Expected {candidates[0]}"
    )


def _resolve_nemo_speech_dll() -> tuple[ctypes.CDLL, str]:
    bin_candidates = [
        config.ROOT / "tools" / "nemo-speech-vulkan" / "nemo-speech-0.2.0-windows-x86_64-vulkan" / "bin",
        config.ROOT / "tools" / "nemo-speech-cpu" / "nemo-speech-0.2.0-windows-x86_64-cpu" / "bin",
        Path("D:/TranslationDesktop/tools/nemo-speech-vulkan/nemo-speech-0.2.0-windows-x86_64-vulkan/bin"),
        Path("D:/TranslationDesktop/tools/nemo-speech-cpu/nemo-speech-0.2.0-windows-x86_64-cpu/bin"),
    ]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bin_candidates.insert(0, Path(meipass) / "tools" / "nemo-speech-vulkan" / "nemo-speech-0.2.0-windows-x86_64-vulkan" / "bin")
        bin_candidates.insert(1, Path(meipass) / "tools" / "nemo-speech-cpu" / "nemo-speech-0.2.0-windows-x86_64-cpu" / "bin")
    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).resolve().parent
        bin_candidates.insert(0, app_dir / "tools" / "nemo-speech-vulkan" / "nemo-speech-0.2.0-windows-x86_64-vulkan" / "bin")
        bin_candidates.insert(1, app_dir / "_internal" / "tools" / "nemo-speech-vulkan" / "nemo-speech-0.2.0-windows-x86_64-vulkan" / "bin")
    for bdir in bin_candidates:
        dll = bdir / "nemo_speech_asr_c.dll"
        if dll.exists():
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(str(bdir))
                except OSError:
                    pass
            lib = ctypes.CDLL(str(dll))
            return lib, str(bdir)
    raise FileNotFoundError(
        "nemo_speech_asr_c.dll not found in tools/nemo-speech-vulkan or tools/nemo-speech-cpu"
    )


def _detect_best_vulkan_gpu() -> int:
    """Detect discrete NVIDIA/AMD GPU device index for Vulkan.

    Falls back to 0 if detection fails or only integrated GPU is found.
    Respects TRANSLATION_NEMO_GPU env var if explicitly set.
    """
    env_override = os.environ.get("TRANSLATION_NEMO_GPU")
    if env_override is not None and env_override.strip() != "":
        try:
            return int(env_override.strip())
        except ValueError:
            pass

    try:
        vk = ctypes.CDLL("vulkan-1.dll")

        class _VkAppInfo(ctypes.Structure):
            _fields_ = [
                ("sType", ctypes.c_uint32),
                ("pNext", ctypes.c_void_p),
                ("pApplicationName", ctypes.c_char_p),
                ("applicationVersion", ctypes.c_uint32),
                ("pEngineName", ctypes.c_char_p),
                ("engineVersion", ctypes.c_uint32),
                ("apiVersion", ctypes.c_uint32),
            ]

        class _VkInstInfo(ctypes.Structure):
            _fields_ = [
                ("sType", ctypes.c_uint32),
                ("pNext", ctypes.c_void_p),
                ("flags", ctypes.c_uint32),
                ("pApplicationInfo", ctypes.POINTER(_VkAppInfo)),
                ("enabledLayerCount", ctypes.c_uint32),
                ("ppEnabledLayerNames", ctypes.c_void_p),
                ("enabledExtensionCount", ctypes.c_uint32),
                ("ppEnabledExtensionNames", ctypes.c_void_p),
            ]

        class _VkProps(ctypes.Structure):
            _fields_ = [
                ("apiVersion", ctypes.c_uint32),
                ("driverVersion", ctypes.c_uint32),
                ("vendorID", ctypes.c_uint32),
                ("deviceID", ctypes.c_uint32),
                ("deviceType", ctypes.c_uint32),
                ("deviceName", ctypes.c_char * 256),
                ("pipelineCacheUUID", ctypes.c_uint8 * 16),
                ("limits", ctypes.c_uint8 * 1024),
            ]

        vk.vkEnumeratePhysicalDevices.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        vk.vkGetPhysicalDeviceProperties.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        app = _VkAppInfo(1, None, b"probe", 1, b"probe", 1, (1 << 22) | (0 << 12))
        info = _VkInstInfo(1, None, 0, ctypes.pointer(app), 0, None, 0, None)
        inst = ctypes.c_void_p()
        if vk.vkCreateInstance(ctypes.byref(info), None, ctypes.byref(inst)) == 0:
            count = ctypes.c_uint32(0)
            vk.vkEnumeratePhysicalDevices(inst, ctypes.byref(count), None)
            devs = (ctypes.c_void_p * count.value)()
            vk.vkEnumeratePhysicalDevices(inst, ctypes.byref(count), devs)

            best_idx = 0
            best_score = -1
            for idx, dev in enumerate(devs):
                props = _VkProps()
                vk.vkGetPhysicalDeviceProperties(dev, ctypes.byref(props))
                score = 0
                if props.vendorID == 0x10DE:
                    score += 10
                elif props.vendorID == 0x1002:
                    score += 5
                if props.deviceType == 2:
                    score += 10
                if score > best_score:
                    best_score = score
                    best_idx = idx
            vk.vkDestroyInstance(inst, None)
            return best_idx
    except Exception:
        pass
    return 0


class NemotronAsrEngine:
    """NVIDIA Nemotron 3.5 ASR (0.6B FastConformer-RNNT).

    Low-latency multilingual streaming ASR via C API (nemo-speech with Vulkan / CPU).
    Same interface as AsrEngine and Qwen3AsrEngine so the pipeline cannot tell them apart.
    """

    def __init__(self):
        self._lock = threading.Lock()
        model_path = _resolve_nemotron_model_path()
        self._lib, self._bin_dir = _resolve_nemo_speech_dll()
        self._setup_c_api()

        target_gpu = _detect_best_vulkan_gpu()
        backend = _NemoBackendConfig(size=ctypes.sizeof(_NemoBackendConfig), gpu=target_gpu)
        model_path_bytes = str(model_path).encode("utf-8")
        model = _NemoModelConfig(size=ctypes.sizeof(_NemoModelConfig), path=model_path_bytes, name=None)
        cfg = _NemoRecognizerConfig(
            size=ctypes.sizeof(_NemoRecognizerConfig),
            backend=ctypes.pointer(backend),
            model=ctypes.pointer(model),
        )

        self._recognizer = ctypes.c_void_p(0)
        res = self._lib.nemo_speech_asr_create(ctypes.byref(cfg), ctypes.byref(self._recognizer))
        if res != 0:
            err = self._lib.nemo_speech_asr_last_error()
            err_msg = err.decode("utf-8", errors="replace") if err else str(res)
            print(f"ASR Nemotron Vulkan(gpu={target_gpu}) create failed ({err_msg}); trying default Vulkan(gpu=0)")
            backend.gpu = 0
            res = self._lib.nemo_speech_asr_create(ctypes.byref(cfg), ctypes.byref(self._recognizer))
            if res != 0:
                print(f"ASR Nemotron Vulkan(gpu=0) failed; falling back to CPU (gpu=-1)")
                backend.gpu = -1
                res = self._lib.nemo_speech_asr_create(ctypes.byref(cfg), ctypes.byref(self._recognizer))
                if res != 0:
                    err = self._lib.nemo_speech_asr_last_error()
                    err_msg = err.decode("utf-8", errors="replace") if err else str(res)
                    raise RuntimeError(f"Nemotron ASR initialization failed on Vulkan and CPU: {err_msg}")
                print(f"ASR loading Nemotron 3.5 ASR from {model_path.name} (backend=nemotron-cpu)")
            else:
                print(f"ASR loading Nemotron 3.5 ASR from {model_path.name} (backend=nemotron-vulkan gpu=0)")
        else:
            print(f"ASR loading Nemotron 3.5 ASR from {model_path.name} (backend=nemotron-vulkan gpu={target_gpu})")

    def _setup_c_api(self):
        lib = self._lib
        lib.nemo_speech_asr_version.restype = ctypes.c_char_p
        lib.nemo_speech_asr_last_error.restype = ctypes.c_char_p
        lib.nemo_speech_asr_recognition_options_default.restype = _NemoRecognitionOptions
        lib.nemo_speech_asr_create.argtypes = [
            ctypes.POINTER(_NemoRecognizerConfig),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        lib.nemo_speech_asr_create.restype = ctypes.c_int
        lib.nemo_speech_asr_destroy.argtypes = [ctypes.c_void_p]
        lib.nemo_speech_asr_destroy.restype = None
        lib.nemo_speech_asr_recognize_f32.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_NemoRecognitionOptions),
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_size_t,
            ctypes.c_int32,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        lib.nemo_speech_asr_recognize_f32.restype = ctypes.c_int
        lib.nemo_speech_asr_result_alternative_count.argtypes = [ctypes.c_void_p]
        lib.nemo_speech_asr_result_alternative_count.restype = ctypes.c_size_t
        lib.nemo_speech_asr_result_transcript.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        lib.nemo_speech_asr_result_transcript.restype = ctypes.c_char_p
        lib.nemo_speech_asr_result_confidence.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        lib.nemo_speech_asr_result_confidence.restype = ctypes.c_float
        lib.nemo_speech_asr_result_destroy.argtypes = [ctypes.c_void_p]
        lib.nemo_speech_asr_result_destroy.restype = None

    def transcribe(self, audio: np.ndarray, language: str | None) -> tuple[str, str | None, float]:
        if audio.size < 800:
            return "", language, 0.0

        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)

        opts = self._lib.nemo_speech_asr_recognition_options_default()
        detected_lang = language
        bcp47 = to_nemo_bcp47(language)
        if bcp47:
            opts.language_code = bcp47.encode("utf-8")

        audio_ptr = audio.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        result = ctypes.c_void_p(0)

        with self._lock:
            ret = self._lib.nemo_speech_asr_recognize_f32(
                self._recognizer,
                ctypes.byref(opts),
                audio_ptr,
                len(audio),
                SAMPLE_RATE,
                ctypes.byref(result),
            )
            if ret != 0 or not result.value:
                err = self._lib.nemo_speech_asr_last_error()
                err_msg = err.decode("utf-8", errors="replace") if err else str(ret)
                print(f"ASR Nemotron recognize error: {err_msg}")
                return "", detected_lang, 0.0

            alts = self._lib.nemo_speech_asr_result_alternative_count(result)
            if alts > 0:
                raw_bytes = self._lib.nemo_speech_asr_result_transcript(result, 0)
                raw_text = raw_bytes.decode("utf-8", errors="replace").strip() if raw_bytes else ""
                conf = float(self._lib.nemo_speech_asr_result_confidence(result, 0))
            else:
                raw_text = ""
                conf = 0.0
            self._lib.nemo_speech_asr_result_destroy(result)

        text, detected_lang = parse_nemotron_transcript(raw_text, detected_lang)
        if is_hallucination(text):
            return "", detected_lang, conf
        return text, detected_lang, conf

    def close(self):
        with self._lock:
            if getattr(self, "_recognizer", None) and self._recognizer.value:
                self._lib.nemo_speech_asr_destroy(self._recognizer)
                self._recognizer = ctypes.c_void_p(0)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
