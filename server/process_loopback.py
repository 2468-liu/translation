"""Windows Process-Tree Loopback and Audio Session Manager.

Wraps audio_process_loopback.dll to provide:
1. Active audio session enumeration (detecting applications currently outputting audio).
2. Process-tree loopback capture (capturing exclusively a target process and its child processes,
   e.g. Chrome browser + Audio Service / Renderer tree).
3. Pre-Volume tap point integration with zero-latency 16kHz mono int16 PCM delivery.
4. Auto-failover: if the target application terminates, automatically falls back to global loopback.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from typing import Any, Callable, List, Optional

if sys.platform == "win32":
    from ctypes import wintypes
else:
    class wintypes:
        DWORD = ctypes.c_uint32
        WCHAR = ctypes.c_wchar

# Path to audio_process_loopback.dll
def _find_dll_path() -> Optional[Path]:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
        candidates = [
            base / "audio_process_loopback.dll",
            base / "_internal" / "audio_process_loopback.dll",
            base / "tools" / "process-loopback" / "audio_process_loopback.dll",
            base / "_internal" / "tools" / "process-loopback" / "audio_process_loopback.dll",
        ]
    else:
        root = Path(__file__).resolve().parents[1]
        candidates = [
            root / "tools" / "process-loopback" / "audio_process_loopback.dll",
            root / "audio_process_loopback.dll",
        ]

    for cand in candidates:
        if cand.exists():
            return cand
    return None


class _AudioSessionEntry(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("pid", wintypes.DWORD),
        ("name", wintypes.WCHAR * 260),
        ("isActive", ctypes.c_int),
    ]


if sys.platform == "win32":
    _PCM_CB_TYPE = ctypes.WINFUNCTYPE(None, ctypes.POINTER(ctypes.c_short), ctypes.c_int)
    _STATE_CB_TYPE = ctypes.WINFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p)
else:
    _PCM_CB_TYPE = ctypes.CFUNCTYPE(None, ctypes.POINTER(ctypes.c_short), ctypes.c_int)
    _STATE_CB_TYPE = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p)


class ProcessLoopbackManager:
    """Manages process-tree and global loopback audio capture via native DLL."""

    def __init__(self) -> None:
        self._dll: Optional[Any] = None
        self._current_pid: int = 0
        self._pcm_callback: Optional[Callable[[bytes], None]] = None
        self._on_exit_callback: Optional[Callable[[int], None]] = None
        self._on_device_change_callback: Optional[Callable[[str], None]] = None
        self._c_pcm_cb = None
        self._c_state_cb = None

        self._linux_proc: Optional[subprocess.Popen] = None
        self._linux_thread: Optional[threading.Thread] = None
        self._linux_stop: bool = False

        if sys.platform != "win32":
            return

        dll_path = _find_dll_path()
        if dll_path and dll_path.exists():
            try:
                self._dll = getattr(ctypes, "WinDLL", ctypes.CDLL)(str(dll_path))
                self._dll.EnumerateAudioSessions.argtypes = [ctypes.POINTER(_AudioSessionEntry), ctypes.c_int]
                self._dll.EnumerateAudioSessions.restype = ctypes.c_int

                self._dll.StartAudioLoopback.argtypes = [wintypes.DWORD, _PCM_CB_TYPE, _STATE_CB_TYPE]
                self._dll.StartAudioLoopback.restype = ctypes.c_int

                self._dll.StopAudioLoopback.restype = None
                self._dll.IsAudioLoopbackRunning.restype = ctypes.c_int
                self._dll.GetCurrentCapturePid.restype = wintypes.DWORD

                # Keep references to native callbacks to prevent garbage collection
                self._c_pcm_cb = _PCM_CB_TYPE(self._native_pcm_callback)
                self._c_state_cb = _STATE_CB_TYPE(self._native_state_callback)
            except Exception as e:
                print(f"[ProcessLoopback] Failed to initialize native DLL: {e}")
                self._dll = None

    @property
    def is_available(self) -> bool:
        if sys.platform == "win32":
            return self._dll is not None
        import shutil
        return bool(shutil.which("pactl") or shutil.which("pw-record") or shutil.which("parec"))

    def enumerate_sessions(self, max_entries: int = 32) -> List[dict]:
        """Returns list of active audio sessions: [{'pid': int, 'name': str, 'is_active': bool}]."""
        if sys.platform == "win32":
            if not self._dll:
                return []

            entries_arr = (_AudioSessionEntry * max_entries)()
            try:
                count = self._dll.EnumerateAudioSessions(entries_arr, max_entries)
                result = []
                for i in range(count):
                    e = entries_arr[i]
                    result.append({
                        "pid": int(e.pid),
                        "name": str(e.name),
                        "is_active": bool(e.isActive),
                    })
                return result
            except Exception as e:
                print(f"[ProcessLoopback] EnumerateAudioSessions error: {e}")
                return []
        else:
            return self._enumerate_linux_sessions(max_entries)

    def _get_linux_env(self) -> dict:
        env = dict(os.environ)
        env["LC_ALL"] = "C"
        if "XDG_RUNTIME_DIR" not in env and hasattr(os, "getuid"):
            try:
                run_user = f"/run/user/{os.getuid()}"
                if os.path.exists(run_user):
                    env["XDG_RUNTIME_DIR"] = run_user
            except Exception:
                pass
        return env

    def _get_linux_default_sink_monitor(self) -> str:
        env = self._get_linux_env()
        try:
            res = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True, timeout=1.0, env=env)
            if res.returncode == 0 and res.stdout.strip():
                sink = res.stdout.strip()
                return f"{sink}.monitor"
        except Exception:
            pass
        try:
            res = subprocess.run(["pactl", "list", "sinks", "short"], capture_output=True, text=True, timeout=1.0, env=env)
            if res.returncode == 0 and res.stdout.strip():
                first_line = res.stdout.strip().splitlines()[0]
                parts = first_line.split()
                if len(parts) >= 2:
                    return f"{parts[1]}.monitor"
        except Exception:
            pass
        return "@DEFAULT_SINK@.monitor"

    def _resolve_linux_process_name(self, pid: int, raw_name: str) -> str:
        name = raw_name
        name_lower = name.lower()
        if any(b in name_lower for b in ("chrome", "chromium", "google-chrome")):
            return "Google Chrome"

        # Check /proc/<pid>/cmdline
        try:
            cmdline_p = Path(f"/proc/{pid}/cmdline")
            if cmdline_p.exists():
                cmdline = cmdline_p.read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="ignore").lower()
                if "chrome" in cmdline:
                    return "Google Chrome"
                if "firefox" in cmdline:
                    return "Firefox"
                if "edge" in cmdline:
                    return "Microsoft Edge"
        except Exception:
            pass

        # Check parent process in /proc/<pid>/stat
        try:
            stat_p = Path(f"/proc/{pid}/stat")
            if stat_p.exists():
                fields = stat_p.read_text(errors="ignore").split()
                if len(fields) >= 4:
                    ppid = int(fields[3])
                    if ppid > 1:
                        parent_cmd = Path(f"/proc/{ppid}/cmdline")
                        if parent_cmd.exists():
                            p_cmd = parent_cmd.read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="ignore").lower()
                            if "chrome" in p_cmd:
                                return "Google Chrome"
        except Exception:
            pass

        return name

    def _enumerate_linux_sessions(self, max_entries: int = 32) -> List[dict]:
        import re
        result = []
        env = self._get_linux_env()
        try:
            res = subprocess.run(["pactl", "list", "sink-inputs"], capture_output=True, text=True, timeout=1.5, env=env)
            if res.returncode == 0:
                # Support both English ("Sink Input #") and Chinese ("信宿输入 #") output
                blocks = re.split(r"(?:Sink Input|信宿输入)\s*#\d+", res.stdout)
                for blk in blocks[1:]:
                    pid_match = re.search(r'application\.process\.id\s*[=:]\s*"?(\d+)"?', blk)
                    name_match = re.search(r'(?:application\.name|media\.name|node\.name|application\.process\.binary)\s*[=:]\s*"?([^"\r\n]+)"?', blk)
                    cork_match = re.search(r'(?:Corked|已暂停):\s*(yes|no|是|否)', blk, re.IGNORECASE)
                    if pid_match:
                        pid = int(pid_match.group(1))
                        raw_name = name_match.group(1).strip() if name_match else f"PID {pid}"
                        name = self._resolve_linux_process_name(pid, raw_name)
                        is_active = True
                        if cork_match:
                            val = cork_match.group(1).lower()
                            is_active = (val in ("no", "否"))
                        result.append({"pid": pid, "name": name, "is_active": is_active})
                        if len(result) >= max_entries:
                            break
        except Exception:
            pass
        return result

    def _find_linux_sink_input_index(self, target_pid: int) -> Optional[int]:
        import re
        env = self._get_linux_env()
        try:
            res = subprocess.run(["pactl", "list", "sink-inputs"], capture_output=True, text=True, timeout=1.5, env=env)
            if res.returncode == 0:
                matches = re.finditer(r"(?:Sink Input|信宿输入)\s*#(\d+)([\s\S]*?)(?=(?:Sink Input|信宿输入)\s*#|\Z)", res.stdout)
                for m in matches:
                    idx = int(m.group(1))
                    body = m.group(2)
                    pid_match = re.search(r'application\.process\.id\s*[=:]\s*"?(\d+)"?', body)
                    if pid_match:
                        p = int(pid_match.group(1))
                        if p == target_pid:
                            return idx
                        try:
                            stat_p = Path(f"/proc/{p}/stat")
                            if stat_p.exists():
                                fields = stat_p.read_text(errors="ignore").split()
                                if len(fields) >= 4 and int(fields[3]) == target_pid:
                                    return idx
                        except Exception:
                            pass
        except Exception:
            pass
        return None

    def start(
        self,
        target_pid: int,
        pcm_callback: Callable[[bytes], None],
        on_exit_callback: Optional[Callable[[int], None]] = None,
        on_device_change_callback: Optional[Callable[[str], None]] = None,
    ) -> bool:
        """Starts capturing audio.

        target_pid = 0 captures global system loopback.
        target_pid > 0 captures target process and its child process tree.
        """
        if sys.platform != "win32":
            return self._start_linux_loopback(target_pid, pcm_callback, on_exit_callback, on_device_change_callback)

        if not self._dll:
            return False

        self._current_pid = target_pid
        self._pcm_callback = pcm_callback
        self._on_exit_callback = on_exit_callback
        self._on_device_change_callback = on_device_change_callback

        try:
            res = self._dll.StartAudioLoopback(target_pid, self._c_pcm_cb, self._c_state_cb)
            return res == 0
        except Exception as e:
            print(f"[ProcessLoopback] StartAudioLoopback error: {e}")
            return False

    def _start_linux_loopback(
        self,
        target_pid: int,
        pcm_callback: Callable[[bytes], None],
        on_exit_callback: Optional[Callable[[int], None]] = None,
        on_device_change_callback: Optional[Callable[[str], None]] = None,
    ) -> bool:
        import shutil
        self.stop()
        self._current_pid = target_pid
        self._pcm_callback = pcm_callback
        self._on_exit_callback = on_exit_callback
        self._on_device_change_callback = on_device_change_callback
        self._linux_stop = False

        env = self._get_linux_env()
        cmd = None
        if target_pid > 0 and shutil.which("parec"):
            sink_idx = self._find_linux_sink_input_index(target_pid)
            if sink_idx is not None:
                cmd = ["parec", f"--monitor-stream={sink_idx}", "--raw", "--format=s16le", "--rate=16000", "--channels=1"]
        if not cmd and shutil.which("parec"):
            monitor_source = self._get_linux_default_sink_monitor()
            cmd = ["parec", "--raw", "--format=s16le", "--rate=16000", "--channels=1", "-d", monitor_source]
        elif not cmd and shutil.which("pw-record"):
            cmd = ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", "--target", "@DEFAULT_MONITOR@", "-"]

        if not cmd:
            print("[ProcessLoopback] Neither parec nor pw-record found on Linux.")
            return False

        try:
            self._linux_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
            def _reader():
                while not self._linux_stop and self._linux_proc and self._linux_proc.poll() is None:
                    raw = self._linux_proc.stdout.read(640)
                    if not raw:
                        break
                    if self._pcm_callback:
                        try:
                            self._pcm_callback(raw)
                        except Exception:
                            pass
                if not self._linux_stop and self._on_exit_callback and target_pid > 0:
                    self._on_exit_callback(target_pid)

            self._linux_thread = threading.Thread(target=_reader, daemon=True)
            self._linux_thread.start()
            return True
        except Exception as e:
            print(f"[ProcessLoopback] Linux loopback start error: {e}")
            return False

    def stop(self) -> None:
        if self._dll:
            try:
                self._dll.StopAudioLoopback()
            except Exception:
                pass
        self._linux_stop = True
        if getattr(self, "_linux_proc", None) is not None:
            try:
                self._linux_proc.terminate()
                self._linux_proc.wait(timeout=0.5)
            except Exception:
                try:
                    self._linux_proc.kill()
                except Exception:
                    pass
            self._linux_proc = None
        self._current_pid = 0

    def is_running(self) -> bool:
        if self._dll:
            return bool(self._dll.IsAudioLoopbackRunning())
        return bool(self._linux_proc is not None and self._linux_proc.poll() is None)

    def get_current_pid(self) -> int:
        return self._current_pid

    def _native_pcm_callback(self, data_ptr, num_samples):
        if not self._pcm_callback or num_samples <= 0:
            return
        # Convert short array to raw bytes (16000Hz mono int16 PCM)
        raw_bytes = ctypes.string_at(data_ptr, num_samples * 2)
        try:
            self._pcm_callback(raw_bytes)
        except Exception:
            pass

    def _native_state_callback(self, state_code: int, message_ptr):
        msg = message_ptr.decode("utf-8", errors="ignore") if message_ptr else ""
        # state_code: 0=Stopped, 1=Capturing, 2=TargetProcessExited, 3=DeviceMigrated, -1=Error
        if state_code == 2:
            print(f"[ProcessLoopback] Monitored target PID {self._current_pid} exited! Triggering auto-failover.")
            exited_pid = self._current_pid
            if self._on_exit_callback:
                self._on_exit_callback(exited_pid)
        elif state_code == 3:
            print(f"[ProcessLoopback] Audio device migrated/reconnected: {msg}")
            if self._on_device_change_callback:
                try:
                    self._on_device_change_callback(msg)
                except Exception:
                    pass
        elif state_code == -1:
            print(f"[ProcessLoopback] Warning/Error from native loopback: {msg}")
