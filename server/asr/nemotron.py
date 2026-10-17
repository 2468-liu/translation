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
        config.MODELS_DIR / "nemotron-3.5-asr-streaming-0.6b.q8_0.gguf",
        config.ROOT / "models" / "NV3.5-ASR-0.6B" / "nemotron-3.5-asr-streaming-0.6b.q8_0.gguf",
        Path("/mnt/d/TranslationDesktop/models/NV3.5-ASR-0.6B/nemotron-3.5-asr-streaming-0.6b.q8_0.gguf"),
        Path("/mnt/d/models/NV3.5-ASR-0.6B/nemotron-3.5-asr-streaming-0.6b.q8_0.gguf"),
        Path("D:/TranslationDesktop/models/NV3.5-ASR-0.6B/nemotron-3.5-asr-streaming-0.6b.q8_0.gguf"),
        Path("D:/models/NV3.5-ASR-0.6B/nemotron-3.5-asr-streaming-0.6b.q8_0.gguf"),
        Path.home() / ".cache" / "translation" / "models" / "NV3.5-ASR-0.6B" / "nemotron-3.5-asr-streaming-0.6b.q8_0.gguf",
        Path.home() / "models" / "NV3.5-ASR-0.6B" / "nemotron-3.5-asr-streaming-0.6b.q8_0.gguf",
        Path("/opt/translation/models/NV3.5-ASR-0.6B/nemotron-3.5-asr-streaming-0.6b.q8_0.gguf"),
    ]
    if sys.platform != "win32":
        for m in (Path("/media"), Path("/run/media"), Path("/mnt")):
            if m.exists():
                for sub in ("d", "D", "DATA", "data"):
                    candidates.append(m / sub / "TranslationDesktop" / "models" / "NV3.5-ASR-0.6B" / "nemotron-3.5-asr-streaming-0.6b.q8_0.gguf")
                    candidates.append(m / sub / "models" / "NV3.5-ASR-0.6B" / "nemotron-3.5-asr-streaming-0.6b.q8_0.gguf")
    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).resolve().parent
        candidates.insert(0, app_dir / "models" / "NV3.5-ASR-0.6B" / "nemotron-3.5-asr-streaming-0.6b.q8_0.gguf")
        candidates.append(Path.cwd() / "models" / "NV3.5-ASR-0.6B" / "nemotron-3.5-asr-streaming-0.6b.q8_0.gguf")
    for p in candidates:
        if p.exists() and p.is_file() and p.stat().st_size > 500 * 1024 * 1024:
            return p

    # Dynamic search in user-configured models directory
    if config.MODELS_DIR.exists():
        for g in config.MODELS_DIR.glob("*.gguf"):
            if "nemotron" in g.name.lower() and g.is_file() and g.stat().st_size > 500 * 1024 * 1024:
                return g
        nv_sub = config.MODELS_DIR / "NV3.5-ASR-0.6B"
        if nv_sub.exists():
            for g in nv_sub.glob("*.gguf"):
                if "nemotron" in g.name.lower() and g.is_file() and g.stat().st_size > 500 * 1024 * 1024:
                    return g

    raise FileNotFoundError(
        f"Nemotron ASR model missing. Expected {candidates[0]}"
    )


def _resolve_nemo_speech_dll() -> tuple[ctypes.CDLL, str]:
    is_win = (sys.platform == "win32")
    lib_filename = "nemo_speech_asr_c.dll" if is_win else "libnemo_speech_asr_c.so"

    bin_candidates: list[Path] = []
    if is_win:
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            bin_candidates.extend([
                Path(local_app) / "Programs" / "NeMoSpeech" / "bin",
                Path(local_app) / "Programs" / "NeMoSpeech" / "lib",
                Path(local_app) / "Programs" / "NeMoSpeech",
                Path(local_app) / "nemo-speech" / "bin",
                Path(local_app) / "nemo-speech" / "lib",
                Path(local_app) / "nemo-speech",
            ])
        bin_candidates.extend([
            Path.home() / "AppData" / "Local" / "Programs" / "NeMoSpeech" / "bin",
            Path.home() / "AppData" / "Local" / "Programs" / "NeMoSpeech" / "lib",
            Path.home() / "AppData" / "Local" / "nemo-speech" / "bin",
            Path.home() / "AppData" / "Local" / "nemo-speech" / "lib",
            Path.home() / ".local" / "share" / "nemo-speech" / "bin",
            Path.home() / ".local" / "share" / "nemo-speech" / "lib",
            config.ROOT / "tools" / "nemo-speech-cuda" / "nemo-speech-0.2.0-windows-x86_64-cuda" / "bin",
            config.ROOT / "tools" / "nemo-speech-cuda" / "bin",
            config.ROOT / "tools" / "nemo-speech-cuda" / "lib",
            config.ROOT / "tools" / "nemo-speech" / "bin",
            config.ROOT / "tools" / "nemo-speech" / "lib",
            Path("D:/TranslationDesktop/tools/nemo-speech-cuda/nemo-speech-0.2.0-windows-x86_64-cuda/bin"),
            Path("D:/TranslationDesktop/tools/nemo-speech-cuda/bin"),
            config.ROOT / "tools" / "nemo-speech-cpu" / "nemo-speech-0.2.0-windows-x86_64-cpu" / "bin",
            config.ROOT / "tools" / "nemo-speech-cpu" / "bin",
            config.ROOT / "tools" / "nemo-speech-vulkan" / "nemo-speech-0.2.0-windows-x86_64-vulkan" / "bin",
        ])
    else:
        bin_candidates.extend([
            Path.home() / ".local" / "share" / "nemo-speech" / "lib",
            Path.home() / ".local" / "share" / "nemo-speech" / "bin",
            Path.home() / ".local" / "lib",
            config.ROOT / "tools" / "nemo-speech-cuda" / "nemo-speech-0.2.0-linux-x86_64-cuda" / "lib",
            config.ROOT / "tools" / "nemo-speech-cuda" / "nemo-speech-0.2.0-linux-x86_64-cuda" / "bin",
            config.ROOT / "tools" / "nemo-speech-cuda" / "lib",
            config.ROOT / "tools" / "nemo-speech-cuda" / "bin",
            config.ROOT / "tools" / "nemo-speech" / "lib",
            config.ROOT / "tools" / "nemo-speech" / "bin",
            Path("/usr/local/cuda/lib64"),
            Path("/opt/translation/tools/nemo-speech/lib"),
            Path("/usr/local/lib"),
            Path("/usr/lib"),
            config.ROOT / "tools" / "nemo-speech-cpu" / "nemo-speech-0.2.0-linux-x86_64-cpu" / "lib",
            config.ROOT / "tools" / "nemo-speech-cpu" / "nemo-speech-0.2.0-linux-x86_64-cpu" / "bin",
            config.ROOT / "tools" / "nemo-speech-vulkan" / "nemo-speech-0.2.0-linux-x86_64-vulkan" / "lib",
            config.ROOT / "tools" / "nemo-speech-vulkan" / "nemo-speech-0.2.0-linux-x86_64-vulkan" / "bin",
        ])

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        mp = Path(meipass)
        bin_candidates.insert(0, mp / "tools" / "nemo-speech-cuda" / "nemo-speech-0.2.0-windows-x86_64-cuda" / "bin")
        bin_candidates.insert(1, mp / "tools" / "nemo-speech-cuda" / "lib")
        bin_candidates.insert(2, mp / "tools" / "nemo-speech" / "lib")
        bin_candidates.insert(3, mp / "tools" / "nemo-speech" / "bin")
    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).resolve().parent
        bin_candidates.insert(0, app_dir / "tools" / "nemo-speech-cuda" / "nemo-speech-0.2.0-windows-x86_64-cuda" / "bin")
        bin_candidates.insert(1, app_dir / "tools" / "nemo-speech-cuda" / "lib")
        bin_candidates.insert(2, app_dir / "_internal" / "tools" / "nemo-speech-cuda" / "nemo-speech-0.2.0-windows-x86_64-cuda" / "bin")
        bin_candidates.insert(3, app_dir / "tools" / "nemo-speech" / "lib")

    for bdir in bin_candidates:
        dll = bdir / lib_filename
        if dll.exists():
            if is_win:
                if hasattr(os, "add_dll_directory"):
                    try:
                        os.add_dll_directory(str(bdir))
                    except OSError:
                        pass
                    cuda_path = os.environ.get("CUDA_PATH")
                    if cuda_path and (Path(cuda_path) / "bin").exists():
                        try:
                            os.add_dll_directory(str(Path(cuda_path) / "bin"))
                        except OSError:
                            pass
            else:
                orig_ld = os.environ.get("LD_LIBRARY_PATH", "")
                if str(bdir) not in orig_ld:
                    os.environ["LD_LIBRARY_PATH"] = f"{bdir}:{orig_ld}" if orig_ld else str(bdir)
            try:
                lib = ctypes.CDLL(str(dll))
                return lib, str(bdir)
            except OSError:
                if not is_win:
                    for comp in (
                        "libcublas.so.12",
                        "libcublasLt.so.12",
                        "libcudart.so.12",
                        "libggml-cuda.so",
                        "libggml-base.so",
                        "libggml-cpu.so",
                        "libggml-vulkan.so",
                        "libggml.so",
                        "libllama.so",
                        "libnemo_speech_asr.so",
                    ):
                        comp_p = bdir / comp
                        if comp_p.exists():
                            try:
                                ctypes.CDLL(str(comp_p), mode=ctypes.RTLD_GLOBAL)
                            except Exception:
                                pass
                    lib = ctypes.CDLL(str(dll))
                    return lib, str(bdir)
                raise

    # Fallback to system dynamic library search on POSIX/Linux
    if not is_win:
        try:
            lib = ctypes.CDLL(lib_filename)
            return lib, ""
        except Exception:
            pass

    raise FileNotFoundError(
        f"{lib_filename} not found in tools/nemo-speech-* or system library paths"
    )


def check_nemotron_status() -> tuple[bool, str]:
    """Check whether Nemotron model and dynamic library are available.

    Returns:
        tuple[bool, str]: (is_ready, message_or_reason)
    """
    missing = []
    try:
        _resolve_nemotron_model_path()
    except Exception:
        missing.append("模型权重 (nemotron-3.5-asr-streaming-0.6b.q8_0.gguf)")
    try:
        _resolve_nemo_speech_dll()
    except Exception:
        lib_name = "nemo_speech_asr_c.dll" if sys.platform == "win32" else "libnemo_speech_asr_c.so"
        missing.append(f"推理引擎动态库 ({lib_name})")
    if missing:
        return False, "缺少组件: " + " 及 ".join(missing)
    return True, "就绪"


def _detect_best_vulkan_gpu() -> int:
    """Detect discrete NVIDIA/AMD GPU device index for Vulkan.

    Prioritizes discrete GPUs (NVIDIA/AMD) over integrated GPUs (Intel/AMD APU).
    Respects TRANSLATION_NEMO_GPU env var or desktop_config.json if explicitly set.
    """
    env_override = os.environ.get("TRANSLATION_NEMO_GPU")
    if env_override is not None and env_override.strip() != "":
        try:
            return int(env_override.strip())
        except ValueError:
            pass

    try:
        cfg = config.load_desktop_config()
        if "nemo_gpu" in cfg:
            return int(cfg["nemo_gpu"])
    except Exception:
        pass

    try:
        vk_name = "vulkan-1.dll" if sys.platform == "win32" else "libvulkan.so.1"
        try:
            vk = ctypes.CDLL(vk_name)
        except OSError:
            if sys.platform != "win32":
                vk = ctypes.CDLL("libvulkan.so")
            else:
                raise

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

        vk.vkCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        vk.vkCreateInstance.restype = ctypes.c_int
        vk.vkDestroyInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        vk.vkDestroyInstance.restype = None
        vk.vkEnumeratePhysicalDevices.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        vk.vkEnumeratePhysicalDevices.restype = ctypes.c_int
        vk.vkGetPhysicalDeviceProperties.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        vk.vkGetPhysicalDeviceProperties.restype = None

        app = _VkAppInfo(1, None, b"probe", 1, b"probe", 1, (1 << 22) | (0 << 12))
        info = _VkInstInfo(1, None, 0, ctypes.pointer(app), 0, None, 0, None)
        inst = ctypes.c_void_p()
        if vk.vkCreateInstance(ctypes.byref(info), None, ctypes.byref(inst)) == 0:
            count = ctypes.c_uint32(0)
            vk.vkEnumeratePhysicalDevices(inst, ctypes.byref(count), None)
            devs = (ctypes.c_void_p * count.value)()
            vk.vkEnumeratePhysicalDevices(inst, ctypes.byref(count), devs)

            best_idx = 0
            best_score = -100
            for idx, dev in enumerate(devs):
                props = _VkProps()
                vk.vkGetPhysicalDeviceProperties(dev, ctypes.byref(props))
                dev_name = bytes(props.deviceName).split(b"\x00")[0].decode("utf-8", "ignore").lower()
                score = 0
                # Discrete GPU (type 2) gets highest priority
                if props.deviceType == 2 or any(k in dev_name for k in ("geforce", "rtx", "gtx", "discrete")):
                    score += 100
                elif props.deviceType == 1 or any(k in dev_name for k in ("iris", "uhd", "integrated")):
                    score -= 50  # Strongly penalize integrated GPU
                # Vendor scoring
                if props.vendorID == 0x10DE or "nvidia" in dev_name:
                    score += 50
                elif props.vendorID == 0x1002 or "amd" in dev_name:
                    score += 25
                if score > best_score:
                    best_score = score
                    best_idx = idx
            vk.vkDestroyInstance(inst, None)
            print(f"[ASR Nemotron] Auto-detected best Vulkan GPU device index {best_idx} (score={best_score})")
            return best_idx
    except Exception as e:
        print(f"[ASR Nemotron] Vulkan GPU device probe notice: {e}")
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

    def reset_state(self):
        """Stateless recognizer; explicit cache/sync boundary hook."""
        pass

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
