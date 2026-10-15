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
from ctypes import wintypes
import os
from pathlib import Path
import sys
import threading
from typing import Callable, List, Optional

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
        return self._dll is not None

    def enumerate_sessions(self, max_entries: int = 32) -> List[dict]:
        """Returns list of active audio sessions: [{'pid': int, 'name': str, 'is_active': bool}]."""
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

    def stop(self) -> None:
        if self._dll:
            try:
                self._dll.StopAudioLoopback()
            except Exception:
                pass
        self._current_pid = 0

    def is_running(self) -> bool:
        if not self._dll:
            return False
        return bool(self._dll.IsAudioLoopbackRunning())

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
