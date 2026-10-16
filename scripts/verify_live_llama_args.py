"""Verification script to test whether llama memory and slot parameters take effect in live OS processes and logs."""

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.serve_config import (
    CACHE_RAM_MB,
    CTX_CHECKPOINTS,
    NO_CACHE_IDLE_SLOTS,
    get_mt_server_args,
)


def verify():
    print("=" * 60)
    print("1. SSOT Parameter Definitions:")
    print(f"   CACHE_RAM_MB        = {CACHE_RAM_MB}")
    print(f"   CTX_CHECKPOINTS     = {CTX_CHECKPOINTS}")
    print(f"   NO_CACHE_IDLE_SLOTS = {NO_CACHE_IDLE_SLOTS}")
    print("=" * 60)

    exe = Path(r"C:\Users\20693\AppData\Local\Microsoft\WindowsApps\llama.exe")
    model = Path(r"D:\TranslationDesktop\models\Index-Translate\Index-Translate-2B.Q4_K_M.gguf")

    cmd = get_mt_server_args(
        exe=exe,
        model_path=model,
        host="127.0.0.1",
        port=8097,
        is_index=True,
    )
    cmd.extend(["-lv", "4"])
    print("\n2. Generated Launch Command:")
    print("   " + " ".join(cmd))

    # Assert parameters in cmd
    assert "--cache-ram" in cmd
    assert cmd[cmd.index("--cache-ram") + 1] == "512"
    assert "--ctx-checkpoints" in cmd
    assert cmd[cmd.index("--ctx-checkpoints") + 1] == "1"
    assert "--no-cache-idle-slots" in cmd
    print("   -> PASS: Generated arguments contain all 3 memory controls.")

    log_path = ROOT / "logs" / "verify_test.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        pass

    out_file = open(log_path, "w", encoding="utf-8")

    print("\n3. Launching live llama.exe on port 8097 to inspect OS and engine logs...")
    proc = subprocess.Popen(
        cmd,
        stdout=out_file,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    try:
        time.sleep(3.5)

        # 4. Check OS kernel CommandLine via PowerShell
        ps_code = f"Get-CimInstance Win32_Process -Filter 'ProcessId = {proc.pid}' | Select-Object -ExpandProperty CommandLine"
        kernel_res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_code],
            capture_output=True,
            text=True,
        )
        kernel_cmdline = kernel_res.stdout.strip()
        print("\n4. Live Windows OS Kernel CommandLine (from Win32_Process):")
        print("   " + kernel_cmdline)

        has_ram = "--cache-ram 512" in kernel_cmdline
        has_ctx = "--ctx-checkpoints 1" in kernel_cmdline
        has_idle = "--no-cache-idle-slots" in kernel_cmdline
        print(f"   -> '--cache-ram 512':        {'PRESENT' if has_ram else 'MISSING'}")
        print(f"   -> '--ctx-checkpoints 1':    {'PRESENT' if has_ctx else 'MISSING'}")
        print(f"   -> '--no-cache-idle-slots':  {'PRESENT' if has_idle else 'MISSING'}")

        # 5. Check engine internal logs for physical activation
        out_file.flush()
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        print("\n5. Engine Internal Startup Telemetry (Physical Proof from Engine C++ runtime):")
        telemetry_found = []
        for l in content.splitlines():
            if "prompt cache is enabled" in l or "context checkpoints enabled" in l or "use `--cache-ram 0`" in l:
                print("   [Engine Telemetry] " + l.strip())
                telemetry_found.append(l)

        print("\n" + "=" * 60)
        if has_ram and has_ctx and has_idle and len(telemetry_found) >= 2:
            print("VERIFICATION RESULT: 100% CONFIRMED & PHYSICALLY ACTIVE IN ENGINE!")
        else:
            print(f"VERIFICATION RESULT: FAILED (telemetry count = {len(telemetry_found)})")
        print("=" * 60)

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
        out_file.close()


if __name__ == "__main__":
    verify()
