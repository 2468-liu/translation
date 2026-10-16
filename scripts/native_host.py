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
    """The repository root, found by looking for the marker rather than by counting.

    Counting parents is what broke. The exe sat directly in scripts/native-host/
    for the onefile build and moved to scripts/native-host/translation-native-host/
    for onedir, so parents[2] was the repository root in one layout and scripts/
    in the other. A frozen build that resolved one level too high then looked for
    scripts/scripts/start.ps1 and answered "找不到启动脚本" -- no backend, no
    trace in the popup, and nothing in the source to show why: the arithmetic is
    correct for the layout it was written against and wrong for the other one.

    Searching for the file the host actually needs makes the answer independent
    of how the build is packaged.
    """
    marker = Path("scripts") / "start.ps1"
    origin = Path(sys.executable).resolve() if getattr(sys, "frozen", False) else Path(__file__).resolve()
    for candidate in (origin, *origin.parents):
        if (candidate / marker).exists():
            return candidate
    # No marker anywhere above us. The old arithmetic still gets the common
    # layouts right, so use it rather than returning the executable's own folder.
    return origin.parents[2] if getattr(sys, "frozen", False) else origin.parents[1]


ROOT = project_root()
# Two places for the pid file, tried in order.
#
# The pid file is the only handle the extension has on the backend process tree:
# 终止后端 reads it and nothing else. So it cannot depend on one writable
# directory. Writing it under LOCALAPPDATA failed with PermissionError from
# inside the frozen exe while the same path written from a plain python.exe in
# the same shell succeeded -- so the file that decides whether the backend can be
# stopped at all was landing in a place this process cannot reach. With no pid
# file, 终止后端 answered "没有由扩展启动的后端" every time while a live backend
# kept both ports, and the button became a one-way door.
#
# ROOT/logs sits next to the exe, which is writable by definition -- that is how
# the exe got installed. It is gitignored.
def _pid_candidates() -> list[Path]:
    candidates = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "translation-backend" / "server.pid")
    candidates.append(ROOT / "logs" / "server.pid")
    return candidates


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


# The ports this project owns. Written down rather than discovered, because "kill
# everything on a port I happen to know about" is the only shape that cannot leave an
# orphan behind: a port nobody remembered becomes an invisible process holding VRAM,
# which is the same failure the pid file was supposed to prevent.
OWNED_PORTS = (WS_PORT, LLAMA_PORT, 8099)


def pids_on_port(port: int) -> list[int]:
    """Processes listening on a port, read from netstat.

    netstat rather than Get-NetTCPConnection because that is CIM underneath and CIM is
    refused in a confined session (probes.ps1 records the same refusal), and a probe
    that cannot run reads as a probe that passed. netstat gives pid and state as text.
    """
    result = subprocess.run(
        ["netstat", "-ano", "-p", "TCP"],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    pids: list[int] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[-2] != "LISTENING":
            continue
        local = parts[1]
        if not local.endswith(f":{port}"):
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid and pid not in pids:
            pids.append(pid)
    return pids


def kill_pid_tree(pid: int) -> str | None:
    """taskkill /T /F, returning an error string instead of raising.

    The returncode is reported rather than discarded. Without it "access denied",
    "no such process" and "did not exit in time" were the same message, which is three
    different problems and the reason a stop could not be retried.
    """
    result = subprocess.run(
        ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    if result.returncode == 0:
        return None
    detail = (result.stderr or result.stdout or "").strip()
    return f"taskkill {pid} 返回 {result.returncode}（{detail or '无输出'}）"


def read_pid() -> int | None:
    """The first candidate naming a process that still exists.

    A file naming a dead pid is skipped rather than returned, and the next
    candidate is tried -- so a stale entry in one place cannot shadow a live one
    in another.
    """
    for candidate in _pid_candidates():
        try:
            pid = int(candidate.read_text(encoding="ascii").strip())
        except (FileNotFoundError, OSError, ValueError):
            continue
        if process_exists(pid):
            return pid
    return None


def write_pid(pid: int) -> str | None:
    """Record the pid we started. Returns an error string instead of raising.

    This runs after the backend has already been spawned. Raising here aborted
    start_backend, so ensure answered ok:false while a working backend was coming
    up behind it: the popup showed a failure, the button stayed on 开始, and the
    user pressed it again against a backend that was already there. A bookkeeping
    failure must not undo the operation it was recording.

    Every candidate is tried before giving up, because losing this file means
    终止后端 can no longer stop what was just started.
    """
    problems = []
    for candidate in _pid_candidates():
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_text(str(pid), encoding="ascii")
            return None
        except OSError as error:
            problems.append(f"{candidate}: {type(error).__name__}: {error}")
    detail = " | ".join(problems)
    print(f"warning: backend started as pid {pid} but no pid file could be "
          f"written ({detail}); 终止后端 will not be able to stop it",
          file=sys.stderr, flush=True)
    return detail


def remove_pid() -> None:
    for candidate in _pid_candidates():
        try:
            candidate.unlink()
        except (FileNotFoundError, OSError):
            pass


def backend_ready() -> bool:
    return listening(WS_PORT) and listening(LLAMA_PORT)


def wait_for_existing_backend(owned_pid: int | None = None) -> dict | None:
    """Wait for a backend already opening either port instead of duplicating it.

    ``owned_pid`` is what separates the two cases that both look like "a port is
    open". A pid file means a backend this host started is still coming up, so
    waiting is right and duplicating it would fight it for the port. No pid file
    plus an open port means an orphan -- a llama-server left behind by an earlier
    session, which is exactly the state a killed stop leaves behind -- and waiting
    for it to turn into a whole backend holds the button down until the clock runs
    out and then fails. Return None there, so the caller starts the missing half.

    Without that distinction the wait could not tell a peer from a corpse.
    """
    if not (listening(WS_PORT) or listening(LLAMA_PORT)):
        return None
    if owned_pid is None:
        return None
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if backend_ready():
            return {"ok": True, "ready": True, "owned": False}
        if not listening(WS_PORT) and not listening(LLAMA_PORT):
            return None
        time.sleep(0.25)
    return {"ok": False, "error": "检测到已有后端正在启动，等待超时；未重复启动新后端"}


def start_backend(audio_only: bool = False) -> dict:
    owned_pid = read_pid()
    if backend_ready():
        return {"ok": True, "ready": True, "owned": owned_pid is not None}

    existing = wait_for_existing_backend(owned_pid)
    if existing is not None:
        return existing

    script = ROOT / "scripts" / "start.ps1"
    if not script.exists():
        return {"ok": False, "error": f"找不到启动脚本: {script}"}

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    environment = os.environ.copy()
    if audio_only:
        environment["TRANSLATION_AUDIO_ONLY"] = "1"
    else:
        environment.pop("TRANSLATION_AUDIO_ONLY", None)
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
        env=environment,
        creationflags=flags,
    )
    pid_note = write_pid(process.pid)

    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if backend_ready():
            reply = {"ok": True, "ready": True, "owned": True}
            if pid_note:
                reply["pid_file_error"] = pid_note
            return reply
        if process.poll() is not None:
            remove_pid()
            return {"ok": False, "error": "本地后端启动失败"}
        time.sleep(0.25)

    return {"ok": False, "error": "本地后端启动超时", "pid": process.pid}


def stop_backend() -> dict:
    """Stop the backend, and say so honestly when it could not.

    The pid file was the only handle here for a long time, and it is not enough on
    its own. A chain switch calls kill_everything() first, which clears the pid file
    and then starts a fresh backend -- but if that fresh start did not write one, or
    the powershell it launched has since exited, stop_backend read None and returned
    {"ok": True, "stopped": False}. The popup checks only `ok`, so it reported success
    while llama-server went on holding the card. That is the failure this was reported
    as: "clicking stop does not kill the model".

    So the pid file is now the fast path and the ports are the authority. If any port
    is still listening after the kill, the answer is a failure with retryable set,
    which is a different reply from "nothing was running" and has to look different.
    """
    pid = read_pid()
    if pid is None:
        # read_pid skips a file naming a pid that is gone, so a stale entry can
        # still be sitting there. Clearing it here is what stops one outliving
        # the process it names and confusing every later read.
        remove_pid()
        busy = [port for port in OWNED_PORTS if listening(port)]
        if not busy:
            return {"ok": True, "stopped": False, "message": "没有在运行的后端"}
        # Something is still up and nothing owns it. That is not "nothing to stop".
        problems = kill_everything()
        still = [port for port in OWNED_PORTS if listening(port)]
        if still:
            return {"ok": False, "retryable": True,
                    "error": f"端口仍在监听: {', '.join(str(p) for p in still)}；"
                              + "; ".join(problems or ["没有可归因的进程"]),
                    "stopped": False}
        return {"ok": True, "stopped": True,
                "message": f"没有 pid 文件，已按端口清掉 {', '.join(str(p) for p in busy)}"}

    killed = subprocess.run(
        ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if not listening(WS_PORT) and not listening(LLAMA_PORT):
            remove_pid()
            return {"ok": True, "stopped": True}
        time.sleep(0.2)

    # The pid file stays. It used to be removed before the kill was checked, so a
    # stop that failed left nothing behind to retry with: every later stop read
    # "no backend was started by the extension" while the backend was still
    # running and holding the ports. One bad stop was a one-way door.
    #
    # taskkill's status is reported too. check=False with a discarded returncode
    # turned "access denied" and "no such process" into the same message as "it
    # did not exit in time", which are three different problems.
    detail = (killed.stderr or killed.stdout or "").strip()
    reason = "后端进程未能及时退出"
    if killed.returncode != 0:
        reason = f"taskkill 返回 {killed.returncode}（{detail or '无输出'}）；{reason}"
    return {"ok": False, "error": reason, "retryable": True, "pid": pid}


def _runtime_path() -> Path:
    return ROOT / "runtime.json"


def read_chain() -> str:
    """The chain the backend is actually running, falling back to the requested one.

    Two fields, and the difference matters. `chain` is what was asked for;
    `chain_running` is what start.ps1 ended up starting after its own fallbacks -- it
    drops to whisper when the Qwen3 weights or the 2.53.0 build are missing, and it does
    that silently apart from one line in start.log. Reporting `chain` to the dropdown
    made it name a chain the backend was not on, which is the same substitution as
    reading an arm's identity off its name.

    Preferring the observed field is what makes the dropdown honest, and it also means a
    file written by an older start.ps1, with no chain_running, still answers with the
    requested chain rather than nothing.
    """
    try:
        # utf-8-sig rather than utf-8, because the writer may have been PowerShell.
        # PS 5.1's Set-Content -Encoding UTF8 emits a BOM, and a strict utf-8 read
        # raises on it -- which turns a readable file into the default chain, silently.
        # install_native_host.ps1 records the same trap for the host manifest; start.ps1
        # now writes without a BOM, and this side tolerates one anyway because a file
        # that cannot be read reports the wrong chain rather than no chain.
        data = json.loads(_runtime_path().read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, ValueError, OSError):
        return "nv-hy"
    for field in ("chain_running", "chain"):
        value = data.get(field)
        if isinstance(value, str) and value:
            return value
    return "nv-hy"


def write_chain(chain: str) -> str | None:
    """Record the chain. Returns an error string instead of raising.

    Writing before starting is what makes a failed start recoverable: the file holds
    the intended chain, so the fallback has something to put back. It also means a
    crash between write and start leaves the file naming a chain that is not running,
    which restart_chain reconciles by starting whatever is recorded.
    """
    try:
        data = json.loads(_runtime_path().read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, ValueError, OSError) as exc:
        return f"runtime.json 读不出来: {exc}"
    data["chain"] = chain
    try:
        _runtime_path().write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        return f"runtime.json 写不进去: {exc}"
    return None


def kill_everything() -> list[str]:
    """Stop the whole backend and return the problems, if any.

    The scope is fixed and written down: every process listening on the three ports
    this project owns, plus any live entry in the pid file. Not "all llama-server on
    this machine" -- that would take down an LM Studio chat model the user has open --
    and not "the pid file only", which is what left the orphan holding VRAM after a
    stop that failed halfway.

    Logs are flushed by waiting for the ports to close rather than by asking the
    process to flush: llama-server writes asynchronously, so the last few lines of
    llama*.err can be lost. That is accepted and documented rather than solved, because
    a truncated log tail on a deliberate kill is not a fault worth a shutdown hook.
    """
    problems: list[str] = []
    targets: list[int] = []
    for port in OWNED_PORTS:
        for pid in pids_on_port(port):
            if pid not in targets:
                targets.append(pid)
    pid_file_pid = read_pid()
    if pid_file_pid is not None and pid_file_pid not in targets:
        targets.append(pid_file_pid)

    for pid in targets:
        problem = kill_pid_tree(pid)
        if problem:
            problems.append(problem)

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if not any(listening(port) for port in OWNED_PORTS):
            break
        time.sleep(0.2)
    remove_pid()

    still = [port for port in OWNED_PORTS if listening(port)]
    if still:
        problems.append(f"端口仍在监听: {', '.join(str(p) for p in still)}")
    return problems


def restart_chain(chain: str) -> dict:
    """Switch to a chain by stopping everything and starting it again.

    Full teardown rather than a live switch, on purpose. A hot swap would need to know
    which processes to keep, and "which processes should survive a chain change" is a
    question that has to be re-answered every time a chain gains a model. Two actions --
    all down, all up -- cannot be wrong that way.

    The fallback is the point of the whole thing: a chain that will not start has to
    leave a working backend behind, because the alternative is editing a JSON file by
    hand to recover from a UI click. The last known-good chain is kept here rather than
    read from the file, because the file now names the chain being tried.
    """
    if chain not in ("nv-in", "qwen3-in", "gemma-e2e"):
        return {"ok": False, "error": f"未实现的链路: {chain}", "rejected": True}

    previous = read_chain()
    note = write_chain(chain)
    if note:
        return {"ok": False, "error": note}

    problems = kill_everything()
    started = start_backend(audio_only=False)
    if started.get("ok"):
        return {"ok": True, "chain": chain, "previous": previous,
                "warnings": problems or None}

    # The new chain did not come up. Put the old one back rather than leaving nothing.
    detail = started.get("error", "未知原因")
    write_chain(previous)
    kill_everything()
    restored = start_backend(audio_only=False)
    if restored.get("ok"):
        return {"ok": False, "chain": previous, "rolled_back": True,
                "error": f"切到 {chain} 失败（{detail}），已回退到 {previous}"}
    return {"ok": False, "chain": previous, "rolled_back": False,
            "error": f"切到 {chain} 失败（{detail}），回退到 {previous} 也失败了"}


def handle(message: dict) -> dict:
    kind = message.get("type")
    if kind == "ensure":
        return start_backend(bool(message.get("audioOnly")))
    if kind == "shutdown":
        return stop_backend()
    if kind == "status":
        # `owned` used to be the pid file's answer, which is not the question anyone
        # asks. What matters is whether a backend is up, and that is the ports. Both are
        # reported because a disagreement between them is the orphan case.
        return {"ok": True, "ready": backend_ready(),
                "owned": read_pid() is not None,
                "up": any(listening(port) for port in OWNED_PORTS),
                "chain": read_chain()}
    if kind == "switch-chain":
        return restart_chain(str(message.get("chain") or ""))
    return {"ok": False, "error": f"未知命令: {kind}"}


def main() -> None:
    try:
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
    finally:
        stop_backend()


if __name__ == "__main__":
    main()
