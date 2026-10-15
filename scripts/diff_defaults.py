"""Every constant whose value differs between two revisions, as a review table.

A merge of thirty-six commits carries its real risk in one place: not the commit
list, but the set of defaults that change underneath it. A reader who has seen the
commit messages still does not know that MAX_UTTERANCE_MS went from 20000 to 12000,
because that is a line in a diff, not a decision anyone announced.

So this reads the two versions of server/config.py directly and reports only what
differs. It also covers the other files that hold behaviour-changing settings,
because a threshold in vad.py changes the pipeline as surely as one in config.py.

The intent is a table a person can read in a minute and sign off on, not another
log. Nothing here runs a measurement.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

# Files that hold defaults which change what the pipeline does. A merge that moves
# any of these has changed behaviour whether or not a commit message says so.
WATCHED = ("server/config.py", "server/vad.py", "server/caption_window.py",
           "server/translate.py")

ASSIGN = re.compile(
    r"^\s*([A-Z][A-Z0-9_]*|\w*[A-Z]\w*)\s*=\s*(.+?)\s*(?:#.*)?$")


def read(rev: str, path: str) -> list[str] | None:
    done = subprocess.run(["git", "show", f"{rev}:{path}"],
                          capture_output=True)
    if done.returncode != 0:
        return None
    return done.stdout.decode("utf-8", "replace").splitlines()


def constants(lines: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in lines:
        line = raw.split("#")[0] if not raw.strip().startswith("#") else ""
        if not line.strip():
            continue
        m = ASSIGN.match(line)
        if not m:
            continue
        name, value = m.group(1), m.group(2).strip().rstrip(",")
        if name.startswith("_") or name in {"True", "False", "None"}:
            continue
        out[name] = value
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", default="main", help="revision to compare from")
    ap.add_argument("--new", default="gemma-audio", help="revision to compare to")
    args = ap.parse_args()

    grand = 0
    for path in WATCHED:
        old_lines = read(args.old, path)
        new_lines = read(args.new, path)
        if old_lines is None or new_lines is None:
            print(f"{path}: 读取失败，跳过")
            continue
        old, new = constants(old_lines), constants(new_lines)
        rows = [(k, old.get(k), new.get(k))
                for k in sorted(set(old) | set(new))
                if old.get(k) != new.get(k)]
        grand += len(rows)
        print(f"=== {path}   ({len(old)} -> {len(new)} 个常量, {len(rows)} 处不同)")
        if not rows:
            print("    无差异")
            print()
            continue
        for name, before, after in rows:
            tag = "新增" if before is None else ("删除" if after is None else "改值")
            print(f"  [{tag}] {name}")
            print(f"      {args.old:12} {before if before is not None else '-'}")
            print(f"      {args.new:12} {after if after is not None else '-'}")
        print()

    print(f"合计 {grand} 处默认值差异。逐条确认是否是你想要的。")
    return 0


if __name__ == "__main__":
    sys.exit(main())