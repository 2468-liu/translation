"""Standalone Windows Desktop Realtime Subtitle Application.

Runs completely standalone without Chrome browser extension:
1. Verifies/launches llama-server (Index-Translate-2B) on 127.0.0.1:8080.
2. Initializes discrete NVIDIA GPU Nemotron 3.5 ASR engine.
3. Captures Windows system speaker audio via WASAPI Loopback.
4. Renders subtitles on the desktop with QtCaptionWindow & System Tray Icon.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

# Early log redirection for frozen mode
_log_file = None
class _AutoFlushStream:
    def __init__(self, file):
        self._f = file
    def write(self, s):
        self._f.write(s)
        self._f.flush()
    def flush(self):
        self._f.flush()
    def isatty(self):
        return False

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
    try:
        _log_path = ROOT / "desktop_app.log"
        _log_file = open(_log_path, "a", encoding="utf-8")
        sys.stdout = _AutoFlushStream(_log_file)
        sys.stderr = _AutoFlushStream(_log_file)
        print(f"\n--- [DesktopApp] Session Start: {time.strftime('%Y-%m-%d %H:%M:%S')} (Root: {ROOT}) ---")
    except Exception:
        pass
else:
    ROOT = Path(__file__).resolve().parent

def _global_exception_handler(exctype, value, tb):
    import traceback
    err = "".join(traceback.format_exception(exctype, value, tb))
    sys.stderr.write(f"\n[CRITICAL UNCAUGHT EXCEPTION]:\n{err}\n")
    if _log_file:
        try:
            _log_file.flush()
        except Exception:
            pass

sys.excepthook = _global_exception_handler

sys.path.insert(0, str(ROOT))

try:
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtNetwork import QLocalServer, QLocalSocket

    from server import config
    from server.asr import build_asr
    from server.audio_enhancer import AudioEnhancer
    from server.loopback import run_loopback
    from server.pipeline import Session
    if sys.platform == "win32":
        try:
            from server.process_loopback import ProcessLoopbackManager
        except Exception as e:
            print(f"[DesktopApp] Windows process loopback error: {e}")
            ProcessLoopbackManager = None
    else:
        ProcessLoopbackManager = None
    from server.qt_caption_window import QtCaptionWindow
    from server.ui import SplashScreen
    from server.vad import apply_partial_interval, cinema_tuning

    SINGLE_INSTANCE_MUTEX_NAME = "Local\\TranslationDesktop_SingleInstance_Mutex"
    SINGLE_INSTANCE_SERVER_NAME = "TranslationDesktop_SingleInstance_Server"


    def check_single_instance(mutex_name: str = SINGLE_INSTANCE_MUTEX_NAME) -> tuple[bool, any]:
        """Check if another instance is already running via Windows Named Mutex.

        Handles both ERROR_ALREADY_EXISTS (183) and ERROR_ACCESS_DENIED (5)
        for cross-privilege / cross-session security boundary protection.
        Returns (is_primary, mutex_handle).
        """
        if sys.platform != "win32":
            return True, None
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            mutex = kernel32.CreateMutexW(None, False, mutex_name)
            last_error = kernel32.GetLastError()
            if last_error == 183:  # ERROR_ALREADY_EXISTS
                return False, mutex
            if last_error == 5:    # ERROR_ACCESS_DENIED (already open in another integrity/session)
                return False, None
            return True, mutex
        except Exception:
            return True, None


    def wake_existing_instance() -> bool:
        """Send an ACTIVATE signal to the existing running instance via QLocalSocket."""
        try:
            socket = QLocalSocket()
            socket.connectToServer(SINGLE_INSTANCE_SERVER_NAME)
            if socket.waitForConnected(400):
                socket.write(b"ACTIVATE\n")
                socket.flush()
                socket.disconnectFromServer()
                return True
        except Exception as e:
            print(f"[DesktopApp] Error waking existing instance: {e}")
        return False

except Exception:
    import traceback
    traceback.print_exc()
    if getattr(sys, "frozen", False):
        try:
            _log_file.flush()
        except Exception:
            pass
    raise


def _is_llama_ready(port: int = 8080) -> bool:
    try:
        url = f"http://127.0.0.1:{port}/health"
        req = urllib.request.Request(url, headers={"User-Agent": "desktop_app"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        pass
    try:
        url = f"http://127.0.0.1:{port}/v1/models"
        req = urllib.request.Request(url, headers={"User-Agent": "desktop_app"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _pids_on_port(port: int) -> list[int]:
    """Find all PIDs listening on a specific TCP port."""
    pids: list[int] = []
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) < 5 or parts[-2] != "LISTENING":
                    continue
                local = parts[1]
                if local.endswith(f":{port}"):
                    try:
                        pid = int(parts[-1])
                        if pid and pid not in pids:
                            pids.append(pid)
                    except ValueError:
                        continue
        except Exception:
            pass
    else:
        # Linux / Ubuntu 24.04 / Zorin: try lsof or fuser
        try:
            result = subprocess.run(["lsof", "-t", f"-i:{port}"], capture_output=True, text=True, check=False)
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.isdigit():
                    pid = int(line)
                    if pid not in pids:
                        pids.append(pid)
        except Exception:
            pass
        if not pids:
            try:
                result = subprocess.run(["fuser", f"{port}/tcp"], capture_output=True, text=True, check=False)
                for part in result.stdout.split():
                    part = part.strip()
                    if part.isdigit():
                        pid = int(part)
                        if pid not in pids:
                            pids.append(pid)
            except Exception:
                pass
    return pids


def _kill_port(port: int) -> list[int]:
    """Kill all processes listening on a port and wait until the port is closed."""
    pids = _pids_on_port(port)
    for pid in pids:
        try:
            print(f"[DesktopApp] Terminating process PID {pid} listening on port {port}...")
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    check=False,
                )
            else:
                import os, signal
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        except Exception as e:
            print(f"[DesktopApp] Failed to kill PID {pid}: {e}")
    t0 = time.monotonic()
    while time.monotonic() - t0 < 2.0:
        if not _listening(port):
            break
        time.sleep(0.1)
    return pids


def _find_llama_server_exe() -> Path | None:
    # 0. Check custom environment variable
    custom = os.environ.get("TRANSLATION_LLAMA_EXE") or os.environ.get("TRANSLATION_LLAMA_SERVER")
    if custom and Path(custom).exists():
        return Path(custom)

    exe_names = ["llama-server.exe", "llama-server"] if sys.platform == "win32" else ["llama-server"]

    # 1. Check system PATH (standard on Linux/Ubuntu)
    import shutil
    for name in exe_names:
        which_path = shutil.which(name)
        if which_path and Path(which_path).exists():
            return Path(which_path)

    # 2. Check LM Studio extensions (PRIORITY: 2.53.0 stable engine for mmproj, then latest CUDA12)
    userprofile = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    lm_backends = userprofile / ".lmstudio" / "extensions" / "backends"
    if not lm_backends.exists():
        lm_backends = Path(os.path.expanduser("~/.cache/lm-studio/extensions/backends"))

    if lm_backends.exists():
        p53 = lm_backends / "llama.cpp-win-x86_64-nvidia-cuda12-avx2-2.53.0" / "llama-server.exe"
        if p53.exists():
            return p53
        for name in exe_names:
            candidates = sorted(
                lm_backends.glob(f"*{name}*"),
                key=lambda p: p.name,
                reverse=True,
            )
            for cand in candidates:
                if cand.is_file():
                    return cand
                sub_exe = cand / name
                if sub_exe.exists():
                    return sub_exe

    # 3. Check local tools/ (bundled standalone engine or manual copy)
    search_dirs = [ROOT / "tools", Path.cwd() / "tools", Path("/usr/local/bin"), Path("/opt/llama-server")]
    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).resolve().parent
        search_dirs.insert(0, app_dir / "tools")
        search_dirs.insert(1, app_dir / "_internal" / "tools")

    for sdir in search_dirs:
        if sdir.exists():
            for name in exe_names:
                for exe in sdir.glob(f"**/{name}"):
                    if exe.exists() and exe.is_file():
                        return exe

    return None


def _ensure_llama_server(splash: Optional[SplashScreen] = None) -> subprocess.Popen | None:
    exe = _find_llama_server_exe()
    if not exe:
        print("[DesktopApp] WARNING: llama-server.exe not found. Translation may be unavailable.")
        return None

    model_candidates = [
        config.MODELS_DIR / "Index-Translate" / "Index-Translate-2B.Q4_K_M.gguf",
        config.MODELS_DIR / "Index-Translate" / "Index-Translate-2B.Q5_K_M.gguf",
        config.MODELS_DIR / "Index-Translate-2B.Q4_K_M.gguf",
        config.MODELS_DIR / "hy-mt2-1.8b" / "Hy-MT2-1.8B-StreamRevise-v4-Q4_K_M.gguf",
        config.MODELS_DIR / "Hy-MT2-1.8B-StreamRevise-v4-Q4_K_M.gguf",
        ROOT / "models" / "Index-Translate" / "Index-Translate-2B.Q4_K_M.gguf",
        ROOT / "models" / "Index-Translate" / "Index-Translate-2B.Q5_K_M.gguf",
        ROOT / "models" / "hy-mt2-1.8b" / "Hy-MT2-1.8B-StreamRevise-v4-Q4_K_M.gguf",
        Path.cwd() / "models" / "Index-Translate" / "Index-Translate-2B.Q4_K_M.gguf",
        Path.cwd() / "models" / "hy-mt2-1.8b" / "Hy-MT2-1.8B-StreamRevise-v4-Q4_K_M.gguf",
    ]
    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).resolve().parent
        model_candidates.insert(0, app_dir / "models" / "Index-Translate" / "Index-Translate-2B.Q4_K_M.gguf")
        model_candidates.insert(1, app_dir / "models" / "Index-Translate" / "Index-Translate-2B.Q5_K_M.gguf")
        model_candidates.insert(2, app_dir / "models" / "hy-mt2-1.8b" / "Hy-MT2-1.8B-StreamRevise-v4-Q4_K_M.gguf")

    model_path = None
    for cand in model_candidates:
        if cand.exists():
            model_path = cand
            break

    if not model_path:
        print(f"[DesktopApp] WARNING: Translation model missing. Checked candidates: {model_candidates}")
        return None

    is_index = "index" in model_path.name.lower()
    model_name = "Index-Translate 2B Q4" if is_index else "Hy-MT2 1.8B"

    if splash:
        splash.set_progress(0.20, f"正在检测本地翻译模型 ({model_name})...")

    if _is_llama_ready(config.LLAMA_PORT):
        running_is_target = False
        try:
            url = f"http://127.0.0.1:{config.LLAMA_PORT}/v1/models"
            req = urllib.request.Request(url, headers={"User-Agent": "desktop_app"})
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for m in data.get("data", []):
                    mid = m.get("id", "").lower()
                    if is_index and "index" in mid:
                        running_is_target = True
                        break
                    elif not is_index and ("hy" in mid or "mt2" in mid):
                        running_is_target = True
                        break
        except Exception:
            pass

        if running_is_target:
            print(f"[DesktopApp] llama-server ({model_name}) already running on port {config.LLAMA_PORT}")
            if splash:
                splash.set_progress(0.40, f"{model_name} 翻译服务已就绪")
            return None
        else:
            print(f"[DesktopApp] Port {config.LLAMA_PORT} running older model; terminating to reload {model_name}...")
            _kill_port(config.LLAMA_PORT)

    if splash:
        splash.set_progress(0.25, f"正在启动 Index 影视翻译引擎 ({exe.name})...")

    cmd = [
        str(exe),
        "-m", str(model_path),
        "--host", config.LLAMA_HOST,
        "--port", str(config.LLAMA_PORT),
        "-ngl", "60",
        "-c", "2048",
        "-t", "4",
        "-np", "3",
        "--no-webui",
    ]
    if is_index:
        cmd.extend(["--reasoning", "off"])

    print(f"[DesktopApp] Starting llama-server ({model_name}): {exe.name}...")
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW

    log_dir = ROOT / "_internal" / "logs" if (ROOT / "_internal").exists() else ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    out_f = open(log_dir / "llama8080.log", "a", encoding="utf-8")

    proc = subprocess.Popen(
        cmd,
        cwd=str(exe.parent),
        stdout=out_f,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )

    # Wait up to 20s for ready with heartbeat logging
    t0 = time.monotonic()
    last_print = 0.0
    while time.monotonic() - t0 < 20.0:
        if _is_llama_ready(config.LLAMA_PORT):
            print(f"[DesktopApp] llama-server ready on port {config.LLAMA_PORT} in {time.monotonic()-t0:.1f}s")
            if splash:
                splash.set_progress(0.45, f"{model_name} 翻译引擎已就绪")
            return proc
        elapsed = time.monotonic() - t0
        if splash:
            prog = 0.25 + min(0.18, elapsed * 0.02)
            splash.set_progress(prog, f"正在装载 {model_name} 翻译模型至显存... ({elapsed:.0f}s)")
        if time.monotonic() - last_print >= 2.0:
            print(f"[DesktopApp] Loading {model_name} into GPU VRAM... ({elapsed:.1f}s)")
            last_print = time.monotonic()
        time.sleep(0.4)

    print("[DesktopApp] WARNING: llama-server launch timed out waiting for ready.")
    return proc


def _ensure_qwen_server(splash: Optional[SplashScreen] = None) -> subprocess.Popen | None:
    chain_cfg = config.CHAINS.get("qwen3-in") or config.CHAINS.get("qwen3-hy") or {}
    port = chain_cfg.get("qwen3_port", 8099) or 8099
    if splash:
        splash.set_progress(0.50, "正在检测 Qwen3-ASR 语音大模型引擎...")

    if _is_llama_ready(port):
        print(f"[DesktopApp] Qwen3-ASR llama-server already running on port {port}")
        if splash:
            splash.set_progress(0.75, "Qwen3-ASR 语音模型已就绪")
        return None

    if _listening(port):
        print(f"[DesktopApp] Port {port} is occupied by an unresponsive process; killing it to restart...")
        _kill_port(port)

    exe = _find_llama_server_exe()
    if not exe:
        print("[DesktopApp] WARNING: llama-server.exe not found. Qwen3-ASR may be unavailable.")
        return None

    model_candidates = [
        config.MODELS_DIR / "Qwen-ASR-0.6B" / "Qwen3-ASR-0.6B-Q8_0.gguf",
        Path("D:/TranslationDesktop/models/Qwen-ASR-0.6B/Qwen3-ASR-0.6B-Q8_0.gguf"),
        ROOT / "models" / "Qwen-ASR-0.6B" / "Qwen3-ASR-0.6B-Q8_0.gguf",
        Path.cwd() / "models" / "Qwen-ASR-0.6B" / "Qwen3-ASR-0.6B-Q8_0.gguf",
    ]
    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).resolve().parent
        model_candidates.insert(0, app_dir / "models" / "Qwen-ASR-0.6B" / "Qwen3-ASR-0.6B-Q8_0.gguf")

    model_path = None
    for cand in model_candidates:
        if cand.exists():
            model_path = cand
            break

    if not model_path:
        print(f"[DesktopApp] WARNING: Qwen3-ASR model missing. Checked candidates: {model_candidates}")
        return None

    mmproj_candidates = [
        model_path.parent / "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf",
        config.MODELS_DIR / "Qwen-ASR-0.6B" / "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf",
        Path("D:/TranslationDesktop/models/Qwen-ASR-0.6B/mmproj-Qwen3-ASR-0.6B-Q8_0.gguf"),
    ]
    mmproj_path = None
    for cand in mmproj_candidates:
        if cand.exists():
            mmproj_path = cand
            break

    if not mmproj_path:
        print(f"[DesktopApp] WARNING: mmproj file missing for Qwen3-ASR. Checked candidates: {mmproj_candidates}")
        return None

    if splash:
        splash.set_progress(0.55, "正在启动 Qwen3 语音大模型引擎...")

    cmd = [
        str(exe),
        "-m", str(model_path),
        "--mmproj", str(mmproj_path),
        "--host", config.LLAMA_HOST,
        "--port", str(port),
        "-ngl", "99",
        "-c", "4096",
        "-t", "4",
        "-np", "2",
        "--no-webui",
    ]
    print(f"[DesktopApp] Starting Qwen3-ASR llama-server on port {port} (-c 4096, -np 2, -ngl 99)...")
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW

    log_dir = ROOT / "_internal" / "logs" if (ROOT / "_internal").exists() else ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    out_f = open(log_dir / "llama8099.log", "a", encoding="utf-8")

    proc = subprocess.Popen(
        cmd,
        cwd=str(exe.parent),
        stdout=out_f,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )

    t0 = time.monotonic()
    last_print = 0.0
    while time.monotonic() - t0 < 25.0:
        if _is_llama_ready(port):
            print(f"[DesktopApp] Qwen3-ASR ready on port {port} in {time.monotonic()-t0:.1f}s")
            if splash:
                splash.set_progress(0.78, "Qwen3-ASR 语音模型已就绪")
            return proc
        elapsed = time.monotonic() - t0
        if splash:
            prog = 0.55 + min(0.22, elapsed * 0.02)
            splash.set_progress(prog, f"正在装载 Qwen3-ASR 语音模型至显存... ({elapsed:.0f}s)")
        if time.monotonic() - last_print >= 2.0:
            print(f"[DesktopApp] Loading Qwen3-ASR & mmproj into GPU VRAM... ({elapsed:.1f}s)")
            last_print = time.monotonic()
        time.sleep(0.4)

    print("[DesktopApp] WARNING: Qwen3-ASR launch timed out waiting for ready.")
    return proc


class DesktopApp:
    def __init__(self, mutex_handle=None, app: Optional[QApplication] = None, splash: Optional[SplashScreen] = None):
        self._instance_mutex = mutex_handle
        self.splash = splash

        # Initialize Qt Application early if not passed
        self.app = app or QApplication.instance() or QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        # 1. Start or check llama-server for Index-Translate
        self.llama_proc = _ensure_llama_server(splash=self.splash)
        self.qwen_proc = None

        # 2. Dynamic Audio Normalizer (AGC + Tanh soft limiter for movies)
        self.audio_enhancer = AudioEnhancer()

        # 3. Initialize Qt Caption Window
        if self.splash:
            self.splash.set_progress(0.48, "正在初始化字幕界面与图形渲染...")
        self.window = QtCaptionWindow()
        try:
            icon = self.window.windowIcon()
            if icon and hasattr(icon, "isNull") and not icon.isNull():
                self.app.setWindowIcon(icon)
        except Exception:
            pass
        self._is_capturing = True

        # Setup Local Server for Single-Instance Wakeup IPC
        self.local_server = QLocalServer(self.app)
        QLocalServer.removeServer(SINGLE_INSTANCE_SERVER_NAME)
        if self.local_server.listen(SINGLE_INSTANCE_SERVER_NAME):
            self.local_server.newConnection.connect(self._on_single_instance_activate)
        else:
            print(f"[DesktopApp] Warning: QLocalServer listen failed: {self.local_server.errorString()}")

        # Initialize ASR and Pipeline based on configuration (default: qwen3 for movies)
        cfg = config.load_desktop_config()
        if "text_only" in cfg:
            self.window.apply_text_only(bool(cfg.get("text_only", False)), from_source="init")
        initial_backend = cfg.get("asr_backend", "qwen3")
        self._current_asr_backend = initial_backend

        if initial_backend == "qwen3":
            print("[DesktopApp] Loading Qwen3-ASR (Cinema Enhanced Model)...")
            self.qwen_proc = _ensure_qwen_server(splash=self.splash)
            if self.splash:
                self.splash.set_progress(0.85, "正在初始化 ASR 语音识别管道...")
            config.ASR_BACKEND = "qwen3"
            os.environ["TRANSLATION_ASR_BACKEND"] = "qwen3"
            os.environ["TRANSLATION_CHAIN_RUNNING"] = "qwen3-in"
            self.asr = build_asr()
            self.session = Session(self.asr, tuning=cinema_tuning())
            self.session.set_language("auto")
            self.session.set_target_language("zh")
            cadence = int(cfg.get("partial_interval_ms", 1000))
            apply_partial_interval(self.session.vad, cadence)
            self.window.set_cadence_ui(cadence)
            self.window.set_asr_backend_ui("qwen3")
        else:
            print("[DesktopApp] Loading Nemotron 3.5 ASR on NVIDIA GPU...")
            chain_cfg = config.CHAINS.get("qwen3-in") or config.CHAINS.get("qwen3-hy") or {}
            port = chain_cfg.get("qwen3_port", 8099) or 8099
            _kill_port(port)
            self.qwen_proc = None
            if self.splash:
                self.splash.set_progress(0.65, "正在初始化 Nemotron 3.5 ASR 引擎...")
            config.ASR_BACKEND = "nemotron"
            os.environ["TRANSLATION_ASR_BACKEND"] = "nemotron"
            os.environ["TRANSLATION_CHAIN_RUNNING"] = "nv-in"
            self.asr = build_asr()
            self.session = Session(self.asr, tuning=cinema_tuning())
            self.session.set_language("auto")
            self.session.set_target_language("zh")
            cadence = int(cfg.get("partial_interval_ms", 160))
            apply_partial_interval(self.session.vad, cadence)
            self.window.set_cadence_ui(cadence)
            self.window.set_asr_backend_ui("nemotron")

        # Initialize Native Process Loopback Manager (Windows only)
        if sys.platform == "win32" and ProcessLoopbackManager is not None:
            if self.splash:
                self.splash.set_progress(0.95, "正在初始化 WASAPI 音频捕获引擎...")
            self.loopback_mgr = ProcessLoopbackManager()
            self._on_pcm_received = None
            if self.loopback_mgr and self.loopback_mgr.is_available:
                print("[DesktopApp] Native Process-Tree Loopback DLL loaded successfully.")
        else:
            if self.splash:
                self.splash.set_progress(0.95, "正在初始化 PipeWire/PulseAudio 回环捕获引擎...")
            self.loopback_mgr = None
            self._on_pcm_received = None

        # Hook Window Config Changes directly to Session
        self.window.config_changed.connect(self._on_config_changed)

        # Background Audio Loopback & Worker Thread
        self.stop_event = asyncio.Event()
        self.loop = asyncio.new_event_loop()
        self.worker_thread = threading.Thread(target=self._run_async_pipeline, daemon=True)
        self.worker_thread.start()

        # Background Watchdog: Periodically monitor backend health and auto-restart if exited
        from PyQt6.QtCore import QTimer
        self.watchdog_timer = QTimer(self.app)
        self.watchdog_timer.setInterval(2500)
        self.watchdog_timer.timeout.connect(self._check_backends_health)
        self.watchdog_timer.start()

        # Register Clean Exit
        self.app.aboutToQuit.connect(self._cleanup)

        # Transition from splash to main window
        if self.splash:
            self.splash.set_progress(1.0, "启动完成，即将呈现字幕...")
            self.splash.finish_and_close(self.window, duration_ms=320)
        else:
            self.window.show()

    def _check_backends_health(self) -> None:
        try:
            # 1. Translation llama-server health
            if self.llama_proc is not None and self.llama_proc.poll() is not None:
                code = self.llama_proc.poll()
                print(f"[DesktopApp] Watchdog: Translation server (8080) exited with code {code}. Auto-restarting...")
                self.llama_proc = _ensure_llama_server()

            # 2. Qwen3-ASR llama-server health
            if getattr(self, "_current_asr_backend", None) == "qwen3":
                if self.qwen_proc is not None and self.qwen_proc.poll() is not None:
                    code = self.qwen_proc.poll()
                    print(f"[DesktopApp] Watchdog: Qwen3-ASR server (8099) exited with code {code}. Auto-restarting...")
                    self.qwen_proc = _ensure_qwen_server()
        except Exception as e:
            print(f"[DesktopApp] Watchdog check error: {e}")

    def _on_config_changed(self, cfg: dict):
        try:
            target_backend = str(cfg["asr_backend"]) if "asr_backend" in cfg else None
            target_cadence = int(cfg["partial_interval_ms"]) if "partial_interval_ms" in cfg else None

            if target_backend:
                self._switch_asr_backend(target_backend, cadence=target_cadence)
            elif target_cadence:
                print(f"[DesktopApp] Cadence slice updated -> {target_cadence}ms")
                apply_partial_interval(self.session.vad, target_cadence)
                self.window.set_cadence_ui(target_cadence)
            if "is_capturing" in cfg:
                self._is_capturing = bool(cfg["is_capturing"])
                print(f"[DesktopApp] Capture state updated -> {'Active' if self._is_capturing else 'Paused'}")
                if not self._is_capturing:
                    self.audio_enhancer.reset()
                    if hasattr(self, "loop") and self.loop and self.loop.is_running():
                        self.loop.call_soon_threadsafe(self.session.reset)
                    else:
                        self.session.reset()
            if "target_pid" in cfg:
                pid = int(cfg["target_pid"])
                name = str(cfg.get("target_process_name", ""))
                self._switch_audio_source(pid, name)
            if "block_by_punct" in cfg:
                enabled = bool(cfg["block_by_punct"])
                print(f"[DesktopApp] Block by punctuation updated -> {enabled}")
                self.window._block_by_punct = enabled
        except Exception as e:
            print(f"[DesktopApp] Error processing config change: {e}")
            import traceback
            traceback.print_exc()

    def _kill_qwen_server(self) -> None:
        chain_cfg = config.CHAINS.get("qwen3-in") or config.CHAINS.get("qwen3-hy") or {}
        port = chain_cfg.get("qwen3_port", 8099) or 8099
        print(f"[DesktopApp] Stopping Qwen3-ASR server and freeing port {port}...")
        proc = getattr(self, "qwen_proc", None)
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=1.5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self.qwen_proc = None
        _kill_port(port)

    def _switch_asr_backend(self, backend: str, cadence: int | None = None):
        target_backend = "nemotron" if backend in ("nemotron", "nv", "nemotron-3.5") else "qwen3"
        current_backend = getattr(self, "_current_asr_backend", None)
        current_cadence = getattr(self.session.vad.tuning, "partial_interval_ms", None) if hasattr(self, "session") else None

        if current_backend == target_backend and (cadence is None or current_cadence == cadence):
            return

        print(f"[DesktopApp] Seamlessly switching ASR engine on unified pipeline -> {target_backend} (cadence: {cadence}ms)...")
        was_capturing = getattr(self, "_is_capturing", True)
        self._is_capturing = False
        try:
            if target_backend == "qwen3":
                chain_cfg = config.CHAINS.get("qwen3-in") or config.CHAINS.get("qwen3-hy") or {}
                port = chain_cfg.get("qwen3_port", 8099) or 8099
                if not _is_llama_ready(port):
                    _kill_port(port)
                    self.window.command_received.emit(
                        "toast", "正在启动 Qwen3-ASR 服务...", time.time(), time.time(), False
                    )
                    self.qwen_proc = _ensure_qwen_server()
                    if not _is_llama_ready(port):
                        raise RuntimeError(f"Qwen3-ASR 服务在端口 {port} 启动失败或超时")

                config.ASR_BACKEND = "qwen3"
                os.environ["TRANSLATION_ASR_BACKEND"] = "qwen3"
                os.environ["TRANSLATION_CHAIN_RUNNING"] = "qwen3-in"
                new_asr = build_asr()
                effective_cadence = cadence if cadence is not None else 1000

                if hasattr(self, "session") and getattr(self.session, "asr", None) is not None:
                    try:
                        self.session.asr.close()
                    except Exception:
                        pass
                self.session.switch_asr(new_asr, effective_cadence)
                self.session.asr = new_asr
                self._current_asr_backend = "qwen3"

                self.window.set_cadence_ui(effective_cadence)
                self.window.set_asr_backend_ui("qwen3")
                self.window.set_block_by_punct(True)
                cfg = config.load_desktop_config()
                cfg["asr_backend"] = "qwen3"
                cfg["partial_interval_ms"] = effective_cadence
                cfg["block_by_punct"] = True
                config.save_desktop_config(cfg)
                self.window.command_received.emit(
                    "toast", f"已平滑切换至 Qwen3-ASR ({effective_cadence}ms)", time.time(), time.time(), False
                )
            else:
                self._kill_qwen_server()
                config.ASR_BACKEND = "nemotron"
                os.environ["TRANSLATION_ASR_BACKEND"] = "nemotron"
                os.environ["TRANSLATION_CHAIN_RUNNING"] = "nv-in"
                new_asr = build_asr()
                effective_cadence = cadence if cadence is not None else 160

                if hasattr(self, "session") and getattr(self.session, "asr", None) is not None:
                    try:
                        self.session.asr.close()
                    except Exception:
                        pass
                self.session.switch_asr(new_asr, effective_cadence)
                self.session.asr = new_asr
                self._current_asr_backend = "nemotron"

                self.window.set_cadence_ui(effective_cadence)
                self.window.set_asr_backend_ui("nemotron")
                self.window.set_block_by_punct(True)
                cfg = config.load_desktop_config()
                cfg["asr_backend"] = "nemotron"
                cfg["partial_interval_ms"] = effective_cadence
                cfg["block_by_punct"] = True
                config.save_desktop_config(cfg)
                self.window.command_received.emit(
                    "toast", f"已平滑切换至 Nemotron 3.5 ASR ({effective_cadence}ms，已释放 Qwen 显存)", time.time(), time.time(), False
                )
        except Exception as e:
            print(f"[DesktopApp] Failed to switch ASR backend: {e}")
            import traceback
            traceback.print_exc()
            self.window.command_received.emit(
                "toast", f"切换引擎失败: {e}", time.time(), time.time(), False
            )
        finally:
            self._is_capturing = was_capturing

    def _switch_audio_source(self, target_pid: int, name: str):
        if not self.loopback_mgr or not self.loopback_mgr.is_available:
            print("[DesktopApp] Process loopback manager is not available.")
            return

        print(f"[DesktopApp] Switching audio capture target -> PID: {target_pid} ({name})")
        if self._on_pcm_received:
            # Clean slate: Reset adaptive audio enhancer gains and noise floor
            self.audio_enhancer.reset()
            # Reset pipeline session to clear in-flight VAD buffer, RNN states, and stale jobs
            if hasattr(self, "loop") and self.loop and self.loop.is_running():
                self.loop.call_soon_threadsafe(self.session.reset)
            else:
                self.session.reset()
            # Clear old caption text to avoid lingering fragments
            self.window.command_received.emit("clear", "", time.time(), time.time(), False)

            success = self.loopback_mgr.start(
                target_pid,
                self._on_pcm_received,
                self._on_target_exit,
                getattr(self, "_on_device_changed", None),
            )
            if success:
                display_msg = f"已定向捕获: {name}" if target_pid > 0 else "已恢复全局音频捕获"
                self.window.command_received.emit("toast", display_msg, time.time(), time.time(), False)
            else:
                print(f"[DesktopApp] Failed to switch audio capture to PID {target_pid}")

    def _on_target_exit(self, exited_pid: int):
        print(f"[DesktopApp] Target PID {exited_pid} terminated. Automatically falling back to global loopback...")
        self.window.current_audio_pid = 0
        if self.loopback_mgr and self.loopback_mgr.is_available and self._on_pcm_received:
            self.audio_enhancer.reset()
            if hasattr(self, "loop") and self.loop and self.loop.is_running():
                self.loop.call_soon_threadsafe(self.session.reset)
            else:
                self.session.reset()
            self.window.command_received.emit("clear", "", time.time(), time.time(), False)
            self.loopback_mgr.start(
                0,
                self._on_pcm_received,
                self._on_target_exit,
                getattr(self, "_on_device_changed", None),
            )
        self.window.command_received.emit("toast", "目标应用已退出，已恢复全局捕获", time.time(), time.time(), False)

    def _run_async_pipeline(self):
        asyncio.set_event_loop(self.loop)

        async def send_callback(payload: dict):
            mtype = payload.get("type")
            sent_at = payload.get("sentAt", time.time())
            if mtype == "translation":
                text = str(payload.get("text") or "")
                final = bool(payload.get("final"))
                source = str(payload.get("source") or "")
                if source:
                    self.window.source_line = source
                self.window.command_received.emit("text", text, sent_at, time.time(), final)
            elif mtype == "asr":
                source = str(payload.get("text") or "")
                if source:
                    self.window.source_line = source
                if getattr(self.window, "bilingual_mode", "trans_only") == "source_only":
                    final = bool(payload.get("final"))
                    self.window.command_received.emit("text", source, sent_at, time.time(), final)
            elif mtype == "segment_start":
                self.window.command_received.emit("segment", "", sent_at, time.time(), False)
            elif mtype == "status":
                msg = str(payload.get("message") or "")
                level = payload.get("level", "info")
                print(f"[DesktopApp] [{level.upper()}] {msg}")
                if "lang" in payload:
                    self.window.command_received.emit("status", payload["lang"], sent_at, time.time(), False)
                elif level == "warn":
                    self.window.command_received.emit("status", msg, sent_at, time.time(), False)

        def on_pcm_received(pcm_bytes: bytes):
            if not getattr(self, "_is_capturing", True):
                return
            for caption in self.session.push_pcm(pcm_bytes):
                self.loop.call_soon_threadsafe(
                    lambda item=caption: asyncio.create_task(send_callback(item))
                )

        def on_device_changed(device_info: str):
            print(f"[DesktopApp] WASAPI Loopback Endpoint Migrated: {device_info}")
            self.loop.call_soon_threadsafe(
                lambda: asyncio.create_task(
                    send_callback({
                        "type": "status",
                        "level": "info",
                        "message": f"音频源已热切换: {device_info}",
                    })
                )
            )

        self._on_pcm_received = on_pcm_received
        self._on_device_changed = on_device_changed

        async def pipeline_main():
            worker_task = asyncio.create_task(self.session.worker(send_callback))
            try:
                if self.loopback_mgr and self.loopback_mgr.is_available:
                    print("[DesktopApp] Starting Native WASAPI Process-Tree Loopback (Pre-Volume)...")
                    self.loopback_mgr.start(
                        0,
                        on_pcm_received,
                        self._on_target_exit,
                        self._on_device_changed,
                    )
                    await send_callback({
                        "type": "status",
                        "level": "info",
                        "message": "已连接系统原生回环音频 (Process-Tree & Pre-Volume 分层就绪)",
                    })
                    while not self.stop_event.is_set():
                        await asyncio.sleep(0.1)
                    self.loopback_mgr.stop()
                else:
                    await run_loopback(self.session, send_callback, self.stop_event)
            finally:
                worker_task.cancel()

        try:
            self.loop.run_until_complete(pipeline_main())
        except Exception as exc:
            print(f"[DesktopApp] Pipeline thread ended: {exc}")

    def _on_single_instance_activate(self):
        client = self.local_server.nextPendingConnection()
        if client:
            client.waitForReadyRead(200)
            msg = bytes(client.readAll()).decode("utf-8", errors="ignore").strip()
            client.close()
            print(f"[DesktopApp] Received single-instance IPC activation message: {msg}")
            self.window.activate_existing()

    def _cleanup(self):
        print("[DesktopApp] Shutting down...")
        self.loop.call_soon_threadsafe(self.stop_event.set)
        if hasattr(self, "local_server") and self.local_server:
            try:
                self.local_server.close()
                QLocalServer.removeServer(SINGLE_INSTANCE_SERVER_NAME)
            except Exception:
                pass
        if getattr(self, "_instance_mutex", None) and sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(self._instance_mutex)
                self._instance_mutex = None
            except Exception:
                pass
        if hasattr(self, "watchdog_timer") and self.watchdog_timer:
            try:
                self.watchdog_timer.stop()
            except Exception:
                pass
        if self.loopback_mgr:
            self.loopback_mgr.stop()
        if self.llama_proc and self.llama_proc.poll() is None:
            print("[DesktopApp] Terminating spawned Index-Translate llama-server...")
            self.llama_proc.terminate()
            try:
                self.llama_proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self.llama_proc.kill()
        self._kill_qwen_server()
        if getattr(self, "session", None) and getattr(self.session, "asr", None):
            if hasattr(self.session.asr, "close"):
                try:
                    self.session.asr.close()
                except Exception:
                    pass
        print("[DesktopApp] Shutdown complete.")


    def run(self):
        return self.app.exec()


def main():
    if "--help" in sys.argv:
        print("Translation Desktop - Standalone Realtime Subtitle Application")
        print("Usage: TranslationDesktop.exe [--dry-run]")
        if _log_file:
            _log_file.flush()
        sys.exit(0)

    try:
        if "--dry-run" in sys.argv:
            print("[DesktopApp] Running dry-run validation...")
            if _log_file:
                _log_file.flush()
            qapp = QApplication.instance() or QApplication(sys.argv)
            window = QtCaptionWindow()
            print("[DesktopApp] Qt window initialized successfully.")
            config.ASR_BACKEND = "nemotron"
            asr_nemo = build_asr()
            print(f"[DesktopApp] Nemotron ASR built successfully: {type(asr_nemo).__name__}")
            config.ASR_BACKEND = "qwen3"
            asr_qwen = build_asr()
            print(f"[DesktopApp] Qwen3 ASR built successfully: {type(asr_qwen).__name__}")
            enhancer = AudioEnhancer()
            print(f"[DesktopApp] AudioEnhancer initialized successfully: {type(enhancer).__name__}")
            if _log_file:
                _log_file.flush()
            sys.exit(0)

        # Single instance check: prevent duplicate windows, redundant tray icons, and model memory collision
        is_primary, mutex_handle = check_single_instance()
        if not is_primary:
            print("[DesktopApp] Another instance is already running. Waking it up to foreground...")
            app = QApplication.instance() or QApplication(sys.argv)
            wake_existing_instance()
            print("[DesktopApp] Secondary instance exiting cleanly.")
            if _log_file:
                _log_file.flush()
            sys.exit(0)

        # Early Splash Screen: Instant visual response upon user double-click (< 100ms)
        qapp = QApplication.instance() or QApplication(sys.argv)
        qapp.setQuitOnLastWindowClosed(False)

        # Linux / Ubuntu High-DPI & Anti-aliased Font Family Fallback
        if sys.platform != "win32":
            from PyQt6.QtGui import QFont
            app_font = QFont()
            app_font.setFamilies([
                "Noto Sans CJK SC",
                "Source Han Sans CN",
                "WenQuanYi Micro Hei",
                "Ubuntu Sans",
                "Ubuntu",
                "DejaVu Sans",
                "sans-serif",
            ])
            app_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality)
            qapp.setFont(app_font)

        splash = SplashScreen()
        splash.show()
        splash.set_progress(0.10, "正在初始化实时同传系统与硬件加速...")
        qapp.processEvents()

        app = DesktopApp(mutex_handle=mutex_handle, app=qapp, splash=splash)
        sys.exit(app.run())
    except Exception:
        import traceback
        traceback.print_exc()
        if _log_file:
            _log_file.flush()
        raise


if __name__ == "__main__":
    main()
