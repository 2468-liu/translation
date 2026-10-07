r"""Decide what a run was, from what can be observed, and refuse the runs that lie.

Five times in this project a number was read off something that was not what its
name said. A "705 ms median" was measured from a zero that was not the zero. An
"acceptance rate of 0.30 on both sides" was read off a field the disabled side
does not emit. A "3774 MiB" was off by half. A context-injection result named a
change that was never made. And twelve cells labelled "MTP on" ran with MTP off,
because the arm name was the only record of what had been configured.

The shape is always the same: the fact a conclusion rests on had no source
independent of the label. So this file makes the label irrelevant. An arm's
identity is assembled from things that can be read back -- the live process's
command line, the configuration the server imported, a hash of the weights, the
git state, and whether the run actually completed -- and compared against what
the caller expected. A mismatch is a failed run, not a footnote.

It also catches the failure that was found by eye: a caption window that died
partway through leaves a run whose latency is meaningless and whose quality is
still fine, because a window that stops painting does not change the text the
model produced. Nothing in the logs says so, so this says so.

Usage:
    manifest.py write <name>            assemble and store logs/run.<name>.manifest
    manifest.py check <name> [<name>...] report, and exit non-zero on any fail
    manifest.py sweep <prefix>          every arm whose name starts with the prefix
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from perf_compare import first_block  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs"

# A run is expected to cover the whole clip. A caption window that stops early
# takes the latency with it and leaves the text alone, so the two are judged
# separately rather than as one verdict.
MIN_WINDOW_FRACTION = 0.90

_DRAFT = re.compile(r"--model-draft\s+(\S+)")
_MMPROJ = re.compile(r"--mmproj-device\s+(\S+)")
_MODEL = re.compile(r"-m\s+(\S+)")
_SPEC = re.compile(r"--spec-type\s+(\S+)")
_NGL = re.compile(r"-ngl\s+(\S+)")


def _live_backend() -> str | None:
    """The command line of the llama-server that is actually running, if any."""
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" "
                "| Select-Object -First 1).CommandLine",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = out.stdout.strip()
    return line or None


def _group(cmd: str | None, pattern: re.Pattern[str]) -> str:
    if not cmd:
        return "absent"
    match = pattern.search(cmd)
    return match.group(1) if match else "absent"


_DIGEST_CACHE: dict[tuple[str, int, int], str] = {}


def _file_digest(path: Path) -> str:
    """Full SHA-256 of a weight file, cached by (path, size, mtime).

    The head-and-length shortcut was wrong for exactly the case that matters
    here. A GGUF's first megabyte is metadata and vocabulary, so two fine-tunes
    of the same architecture at the same quantisation have the same length and
    very nearly the same head -- and telling the base model apart from the
    Translate Gemma fine-tune is precisely the distinction a manifest exists to
    make. So the whole file is hashed, and the cache key includes the mtime so an
    edited file rehashes instead of reporting a stale answer.
    """
    if not path.exists():
        return "absent"
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    cached = _DIGEST_CACHE.get(key)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 22), b""):
            digest.update(chunk)
    value = f"sha256:{digest.hexdigest()}"
    _DIGEST_CACHE[key] = value
    return value


_EFFECTIVE = re.compile(r"effective\.config\s+(\{.*\})")


def _effective_config(name: str) -> dict | None:
    """What the server said it was running with, from its own log.

    Not re-derived by importing the config here. Another process importing
    server.config reports what the file says under that reader's environment,
    which is a different question from what is running -- and the whole class of
    bug this file exists for is a run that was not what it was named. The caption
    window is a separate process again, so its thresholds are only knowable from
    what it printed.
    """
    for candidate in (
        LOGS / f"perf.{name}.log",
        LOGS / f"perf_caption.{name}.log",
    ):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
            found = _EFFECTIVE.search(line)
            if found:
                try:
                    return json.loads(found.group(1))
                except json.JSONDecodeError:
                    return None
    return None


def _nvidia() -> dict:
    """Card state that can invalidate a power or latency comparison.

    Throttle reasons matter more than they look: a card that was power or
    temperature limited during one arm and not another produces a difference that
    is the card's behaviour, not the arm's. Under WDDM the compute-apps query
    answers N/A, so foreign-process detection is done by name instead of by
    memory.
    """
    out: dict = {"throttle_reasons": "unavailable", "compute_apps": "unavailable"}
    try:
        query = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=clocks_throttle_reasons.active,temperature.gpu",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if query.returncode == 0 and query.stdout.strip():
            out["throttle_reasons"] = query.stdout.strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        apps = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,process_name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if apps.returncode == 0 and apps.stdout.strip():
            names = [l.strip() for l in apps.stdout.splitlines() if l.strip()]
            out["compute_apps"] = names
            out["foreign_gpu_processes"] = [
                n for n in names if "llama-server" not in n.lower()
            ]
    except (OSError, subprocess.SubprocessError):
        pass
    return out


def _git_state() -> dict:
    def run(args: list[str]) -> str:
        try:
            out = subprocess.run(
                args, cwd=ROOT, capture_output=True, text=True, timeout=30
            )
            return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    sha = run(["git", "rev-parse", "HEAD"])
    # git diff HEAD covers staged and unstaged together, which is what "the code
    # on disk" means, so the index does not need a second pass.
    diff = run(["git", "diff", "HEAD"])
    # Untracked files are part of the code too. A new module that a sweep imports
    # is invisible to git diff, so two arms could differ by exactly the file that
    # decides the result.
    untracked = run(["git", "ls-files", "--others", "--exclude-standard"])
    untracked_list = sorted(l for l in untracked.splitlines() if l.strip())
    blob = hashlib.sha256()
    blob.update(diff.encode())
    for item in untracked_list:
        blob.update(b"\0" + item.encode())
    return {
        "sha": sha,
        "dirty": bool(diff or untracked_list),
        "diff": blob.hexdigest()[:12],
        "untracked": untracked_list,
    }


def _meta(name: str) -> dict | None:
    """The run's own record of itself, or None if it cannot be read at all.

    utf-8-sig, not utf-8: PowerShell's Set-Content -Encoding utf8 writes a BOM,
    and reading it as plain utf-8 raises rather than returning the fields. A
    reader that catches that and carries on with an empty dict would report every
    run as having no meta -- which is the same failure as having no meta, wearing
    a different name.
    """
    path = LOGS / f"run.{name}.meta"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return None


def _caption_window(name: str) -> tuple[float, int]:
    """(seconds the caption window lived, paints) from its archived log.

    Trimmed with perf_compare's own first_block, and for the same reason: a raw
    span over the whole file includes whatever was logged before the replay
    started and after it ended, which inflates the window and would let a window
    that died at 100s pass a completeness check by borrowing time from the
    margins. Taking the first contiguous burst is what makes the number mean
    "the window was alive for this long during the run".
    """
    path = LOGS / f"perf_caption.{name}.log"
    if not path.exists():
        return (0.0, 0)
    rows: list[tuple[float, str, dict]] = []
    paints = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            stamp = float(parts[0])
        except ValueError:
            continue
        if stamp < 1e9:  # a torn line, not an epoch
            continue
        if parts[1] == "caption.paint":
            paints += 1
        rows.append((stamp, parts[1], {}))
    block = first_block(rows)
    if len(block) < 2:
        return (0.0, paints)
    return (block[-1][0] - block[0][0], paints)


def _replay_seconds(name: str) -> float:
    """Wall-clock seconds the replay ran, from the server log's own window.

    Not from the event log's audio position: the first event carrying a position
    is where playback started, which is 0.2s into the clip, not its length.
    """
    server = _server_window(name)
    return server


def _server_window(name: str) -> float:
    path = LOGS / f"perf.{name}.log"
    if not path.exists():
        return 0.0
    rows: list[tuple[float, str, dict]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            stamp = float(parts[0])
        except ValueError:
            continue
        if stamp < 1e9:
            continue
        rows.append((stamp, parts[1], {}))
    block = first_block(rows)
    if len(block) < 2:
        return 0.0
    return block[-1][0] - block[0][0]


def _translations(name: str) -> int:
    path = LOGS / f"blog_3min_test.{name}.json"
    if not path.exists():
        return 0
    text = path.read_text(encoding="utf-8", errors="replace")
    return text.count('"type": "translation"') + text.count('"type":"translation"')


def _newest_run() -> str | None:
    """The arm whose log was written most recently, i.e. the one now running."""
    newest: tuple[float, str] | None = None
    for path in LOGS.glob("run.*.meta"):
        try:
            stamp = path.stat().st_mtime
        except OSError:
            continue
        if newest is None or stamp > newest[0]:
            newest = (stamp, path.name[len("run.") : -len(".meta")])
    return newest[1] if newest else None


def build(name: str) -> dict:
    """Everything observable about one arm, and nothing taken on trust.

    The live process is only evidence about the arm that is running now. Reading
    it for an older arm would attribute today's command line to yesterday's run,
    which is the same error as reading a setting off the name of the thing it
    describes -- so for anything but the newest arm the live fields are recorded
    as not applicable rather than filled in with the wrong answer.
    """
    newest = _newest_run()
    is_current = newest is None or newest == name
    cmd = _live_backend() if is_current else None
    model_path = Path(_group(cmd, _MODEL)) if cmd and _group(cmd, _MODEL) != "absent" else None
    live_seconds, paints = _caption_window(name)
    replay_seconds = _replay_seconds(name)
    meta = _meta(name) or {}
    return {
        "name": name,
        "backend": {
            # False for any arm other than the one that is running now: the
            # process has since been restarted, so its command line says nothing
            # about that arm.
            "observed_live": cmd is not None,
            "applicable": is_current,
            "model": str(model_path) if model_path else "not applicable",
            "model_digest": _file_digest(model_path) if model_path else "not applicable",
            "draft": _group(cmd, _DRAFT),
            "spec_type": _group(cmd, _SPEC),
            "mmproj_device": _group(cmd, _MMPROJ),
            "ngl": _group(cmd, _NGL),
        },
        "meta": {
            "code": meta.get("code"),
            "llama_version": meta.get("llama_version"),
            "mtp_draft_recorded": meta.get("mtp_draft"),
            "mmproj_device_recorded": meta.get("mmproj_device_live"),
            "thresholds": meta.get("thresholds"),
        },
        "git": _git_state(),
        "effective": _effective_config(name),
        "gpu": _nvidia(),
        "completeness": {
            "replay_seconds": round(replay_seconds, 1),
            "caption_window_seconds": round(live_seconds, 1),
            "caption_paints": paints,
            "translations": _translations(name),
        },
    }


def judge(manifest: dict, expect: dict | None = None) -> list[tuple[str, bool, str]]:
    """Failing checks, each with the reason it matters."""
    checks: list[tuple[str, bool, str]] = []
    back = manifest["backend"]
    done = manifest["completeness"]

    if back["applicable"] and not back["observed_live"]:
        checks.append(
            ("backend live", False, "this is the newest arm but no llama-server is running")
        )
    if expect and back["applicable"]:
        for key, want in expect.items():
            got = back.get(key)
            if want is None:
                continue
            if str(got) != str(want):
                checks.append(
                    (key, False, f"wanted {want}, the running backend says {got}")
                )
    if done["translations"] == 0:
        checks.append(("translations", False, "the run produced no translation packets"))
    if done["replay_seconds"] <= 0:
        checks.append(("replay seconds", False, "no playback position in the run log"))

    # The window is judged against the replay, because a window that covered the
    # whole clip is fine even if the clip was shorter than usual.
    if done["replay_seconds"] > 0:
        fraction = done["caption_window_seconds"] / done["replay_seconds"]
        if done["caption_window_seconds"] == 0:
            checks.append(("caption window", False, "no caption window log; latency cannot be measured"))
        elif fraction < MIN_WINDOW_FRACTION:
            checks.append(
                (
                    "caption window",
                    False,
                    f"lived {done['caption_window_seconds']}s of {done['replay_seconds']}s "
                    f"({fraction:.0%}); latency is unusable, quality is not",
                )
            )

    # Cross-check the two independent records of the same fact. The meta was
    # written during the run and the process read at the end; if they disagree,
    # at least one of them is describing a different arm than the name says.
    recorded = manifest["meta"].get("mtp_draft_recorded")
    if recorded is not None and back["applicable"]:
        live_draft = back["draft"] != "absent"
        if bool(recorded) != live_draft:
            checks.append(
                (
                    "mtp_draft agreement",
                    False,
                    f"meta recorded {recorded}, the live process has {live_draft}",
                )
            )
    recorded_mm = manifest["meta"].get("mmproj_device_recorded")
    if recorded_mm and back["applicable"] and back["mmproj_device"] not in ("unset", "not applicable"):
        if recorded_mm != back["mmproj_device"]:
            checks.append(
                (
                    "mmproj agreement",
                    False,
                    f"meta recorded {recorded_mm}, the live process has {back['mmproj_device']}",
                )
            )
    effective = manifest.get("effective")
    if effective is None:
        checks.append(
            (
                "effective config",
                False,
                "the server did not report the configuration it was running with, so "
                "no threshold can be confirmed from the run itself",
            )
        )

    # Power or thermal limiting makes one arm's numbers the card's behaviour
    # rather than the arm's. Reported rather than assumed: the flag is a bitmask
    # and only its meaning is ambiguous, so a non-zero value that decodes to
    # nothing is left alone and a value containing a reason is called out.
    reasons = str(manifest.get("gpu", {}).get("throttle_reasons", "")).lower()
    for word in ("sw power", "hw slowdown", "thermal", "sw thermal"):
        if word in reasons:
            checks.append(
                (
                    "throttle",
                    False,
                    f"the card reported '{word}' limiting during or after this run, "
                    "which contaminates both its power and its latency",
                )
            )
            break

    foreign = manifest.get("gpu", {}).get("foreign_gpu_processes")
    if foreign:
        checks.append(
            (
                "foreign gpu process",
                False,
                f"another process was on the card: {foreign}",
            )
        )

    code = manifest["meta"].get("code")
    if code is None:
        checks.append(("meta", False, "no meta file, so nothing records what code this arm ran"))
    elif manifest["git"]["diff"] not in ("unknown",):
        ran = str(code)
        now = str(manifest["git"]["sha"])
        # Compare short forms. The stamp is written short and the full hash is
        # read back, so a literal comparison would call every arm a mismatch and
        # train the reader to ignore this line.
        short = now[: len(ran.split("+")[0])] if "+" not in ran else ran.split("+")[0]
        if not ran.startswith(short) or "+" in ran != bool(manifest["git"]["dirty"]):
            checks.append(
                (
                    "code identity",
                    False,
                    f"ran {code}, the working tree is now at {now[:7]}"
                    + ("+dirty" if manifest["git"]["dirty"] else ""),
                )
            )
    if manifest["meta"].get("mtp_draft_recorded") is None:
        checks.append(
            (
                "mtp_draft recorded",
                False,
                "the meta carries no mtp_draft, so nothing independent of the arm name "
                "records whether a draft was loaded",
            )
        )
    return checks


def _load_expect() -> dict:
    """What the caller said it was running, so the two records can be compared.

    Draft is stored as the model path when present and "absent" when not, because
    that is the only form of the fact that can be read off a command line. The
    caller says on/off; the manifest answers present/absent.
    """
    expect: dict = {}
    draft = os.environ.get("TRANSLATION_EXPECT_DRAFT")
    if draft in ("0", "1"):
        expect["draft"] = "absent" if draft == "0" else "present"
    mmproj = os.environ.get("TRANSLATION_EXPECT_MMPROJ")
    if mmproj:
        expect["mmproj_device"] = mmproj
    return expect


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    action, names = argv[1], argv[2:]
    if action == "sweep" and names:
        prefix = names[0]
        names = sorted(
            p.name[len("run.") : -len(".meta")]
            for p in LOGS.glob(f"run.{prefix}*.meta")
        )
    if not names:
        print("no runs named")
        return 2

    expect = _load_expect()
    failed = False
    for name in names:
        manifest = build(name)
        path = LOGS / f"run.{name}.manifest"
        path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        checks = judge(manifest, expect or None)
        bad = [c for c in checks if not c[1]]
        flag = "FAIL" if bad else "ok  "
        if bad:
            failed = True
            # Recorded in the file as well as printed, so a reader of the
            # manifest later sees the verdict without re-running the judgement.
            # probe_reference reads this flag and skips the arm.
            manifest["_failed"] = [{"check": label, "reason": reason} for label, _, reason in bad]
        print(f"{flag} {name}")
        for label, _, reason in checks:
            print(f"       {label}: {reason}")
        if bad:
            path.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
            )
    print(f"\nmanifests written to {LOGS}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))