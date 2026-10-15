"""Run the checks this repository already has, before a commit.

This adds no new rules. That is the whole point of it, and the reason it is worth
anything: the suite has 595 cases across 54 files and nothing ran it but whoever
remembered to. A rule that lives only in AGENT.md is a rule the agent has to
remember, and "the agent has to remember" is exactly the failure this repository
has already paid for -- 37 `probe_*.py` files reached the index before
`.gitignore` said they must not, which is a rule that was written down and then
not enforced for as long as enforcement was optional.

So the first move is not to write checks. It is to make the existing one run
without anyone deciding to.

Three things this deliberately does not do:

  * It does not check anything AGENT.md asks for and the suite does not already
    cover. Each added rule needs its own evidence first -- see the BOM rule,
    which was written down and cited for days on a stated consequence
    ("BOM in a .py stops pytest collecting it") that is simply false, because
    Python 3's tokenizer accepts a UTF-8 BOM. A script encoding that would be
    red for no reason, or green against the wrong thing.
  * It does not lint. `pyflakes` is a habit of one agent, not a convention of
    this repository -- it appears nowhere in AGENT.md. Adding it here would put
    two changes in the one commit that is supposed to change one thing.
  * It does not parse git history, check documents, or judge a diff. Those need
    judgement about intent, and a script that guesses is worse than no script.

Usage:

    python scripts/check.py            # run, exit 1 on failure
    SKIP_CHECK=1 git commit ...        # bypass, same as git commit --no-verify

The suite takes about 55 seconds. That is long for a commit hook, and it may
stay that way: the question this script exists to answer is whether the suite
catching things changes what gets committed, and that cannot be answered by a
faster subset. If a week of use says the wait is the problem, the fix is to
split the suite, not to guess which half matters.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


EXIT_OK = 0
EXIT_TESTS_FAILED = 1
EXIT_CANNOT_RUN = 2


def _tail(text: str, lines: int) -> str:
    kept = text.rstrip().splitlines()[-lines:]
    return "\n".join(kept)


def _can_import(module: str) -> bool:
    done = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return done.returncode == 0


def interpreter_problem() -> str:
    """Why this interpreter cannot run the suite, or "" when it can.

    This exists because the first version of the hook got it wrong, and the way
    it got it wrong is the whole reason it is here. Windows had a `python` on
    PATH without pytest installed, the hook picked it, and the suite could not
    start -- and the script reported that as

        check: FAILED -- commit refused.
        ... No module named pytest

    which reads exactly like a red suite. It blocked commits that had nothing
    wrong with them, on the authority of a verdict that was not a verdict. That
    is this repository's own failure mode wearing a different hat: a number
    that looks like a legitimate result and is not one. A person who trusts it
    learns to distrust all of it, and a gate nobody trusts is a gate nobody runs.

    So "could not run" gets its own exit code and its own wording, and never
    borrows the language of a test result.
    """
    if _can_import("pytest"):
        return ""
    return (
        f"this interpreter has no pytest: {sys.executable}\n"
        "  the suite never ran, so nothing below is a test result\n"
        "  set TRANSLATION_PYTHON to the environment that has it, e.g.\n"
        '    TRANSLATION_PYTHON="D:\\minanconda\\envs\\pytorch\\python.exe"'
    )


def run_tests() -> tuple[bool, str, float]:
    """The suite, with its own exit code carrying the verdict.

    Only the tail is relayed. pytest's full output on a red suite runs to
    hundreds of lines, and a commit hook that scrolls them off the top of the
    terminal has failed at the only job it has: telling the person what to fix.
    The command is printed above the tail so the full log can be reproduced by
    hand when the summary is not enough.
    """
    print("running: python -m pytest tests -q", flush=True)
    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed = time.monotonic() - started
    output = (done.stdout or "") + (done.stderr or "")
    return done.returncode == 0, _tail(output, 25), elapsed


def main() -> int:
    if os.environ.get("SKIP_CHECK") == "1":
        print("check: skipped by SKIP_CHECK=1")
        return EXIT_OK

    problem = interpreter_problem()
    if problem:
        print("check: COULD NOT RUN -- commit refused, and this is not a test failure.")
        print()
        print(problem)
        return EXIT_CANNOT_RUN

    ok, tail, elapsed = run_tests()
    if ok:
        print(f"check: tests passed in {elapsed:.0f}s")
        return EXIT_OK

    print()
    print(f"check: FAILED -- commit refused. ({elapsed:.0f}s)")
    print()
    print(tail)
    print()
    print("If these failures are already in the tree and not yours:")
    print("  SKIP_CHECK=1 git commit -m ...   (or git commit --no-verify)")
    print("Otherwise fix them first: a gate that is red for unrelated reasons")
    print("gets bypassed, and a bypassed gate is the same as no gate.")
    return EXIT_TESTS_FAILED


if __name__ == "__main__":
    raise SystemExit(main())