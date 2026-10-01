"""Native Messaging host that owns the local backend process tree."""

from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time

WS_PORT = 8765
LLAMA_PORT = 8080
HOST_NAME = "translation_backend"


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parents[2]
    return Path(__file__).resolve().parents[1]


ROOT = project_root()
PID_FILE = Path(os.environ.get("LOCALAPPDATA", ROOT)) / "translation-backend" / "server.pid"


def send(payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(data)))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def receive() -> dict | None:
    header = sys.stdin.buffer.read(4)
    if len(header) != 4:
        return None
    size = struct.unpack("<I", header)[0]
    if size > 1024 * 1024:
        raise ValueError("native message too large")
    data = sys.stdin.buffer.read(size)
    if len(data) != size:
        return None
    return json.loads(data.decode("utf-8"))


def listening(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.2)
        try:
            probe.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False


def process_exists(pid: int) -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    output = result.stdout.lower()
    return str(pid) in output and ("powershell.exe" in output or "pwsh.exe" in output)


def read_pid() -> int | None:
    try:
        pid = int(PID_FILE.read_text(encoding="ascii").strip())
    except (FileNotFoundError, OSError, ValueError):
        return None
    return pid if process_exists(pid) else None


def write_pid(pid: int) -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid), encoding="ascii")


def remove_pid() -> None:
    try:
        PID_FILE.unlink()
    except FileNotFoundError:
        pass


def backend_ready() -> bool:
    return listening(WS_PORT) and listening(LLAMA_PORT)


def start_backend() -> dict:
    owned_pid = read_pid()
    if backend_ready():
        return {"ok": True, "ready": True, "owned": owned_pid is not None}

    script = ROOT / "scripts" / "start.ps1"
    if not script.exists():
        return {"ok": False, "error": f"找不到启动脚本: {script}"}

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(script),
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    write_pid(process.pid)

    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if backend_ready():
            return {"ok": True, "ready": True, "owned": True}
        if process.poll() is not None:
            remove_pid()
            return {"ok": False, "error": "本地后端启动失败"}
        time.sleep(0.25)

    return {"ok": False, "error": "本地后端启动超时"}


def stop_backend() -> dict:
    pid = read_pid()
    remove_pid()
    if pid is None:
        return {"ok": True, "stopped": False, "message": "没有由扩展启动的后端"}

    subprocess.run(
        ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not listening(WS_PORT) and not listening(LLAMA_PORT):
            return {"ok": True, "stopped": True}
        time.sleep(0.2)
    return {"ok": False, "error": "后端进程未能及时退出"}


def handle(message: dict) -> dict:
    kind = message.get("type")
    if kind == "ensure":
        return start_backend()
    if kind == "shutdown":
        return stop_backend()
    if kind == "status":
        return {"ok": True, "ready": backend_ready(), "owned": read_pid() is not None}
    return {"ok": False, "error": f"未知命令: {kind}"}


def main() -> None:
    while True:
        message = receive()
        if message is None:
            return
        try:
            response = handle(message)
            if "requestId" in message:
                response["requestId"] = message["requestId"]
            send(response)
        except Exception as error:
            response = {"ok": False, "error": str(error)}
            if "requestId" in message:
                response["requestId"] = message["requestId"]
            send(response)


if __name__ == "__main__":
    main()
