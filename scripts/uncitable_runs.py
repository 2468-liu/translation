"""List the runs whose conclusions cannot be checked for staleness.

This is a NON-CITABLE list, not a list of problems. The difference is the whole
point of the tool, because the two read the same way on a page:

    20 runs in this list        ->  "20 runs have a problem"

is exactly the shape of the number that came before it. An earlier probe asked
"is each run's code an ancestor of HEAD" and answered yes for 119 of 119 -- which
looks alarming and means nothing, because in a repo that keeps moving, every
run ever recorded is older than HEAD. A big number here is not a finding.

So the question is deliberately narrow: **which runs did not record the
preconditions needed to check them at all?** A run missing `thresholds` or
`llama_version` cannot be compared against the current effective config, because
there is nothing on its side of the comparison. That makes it impossible to
cite safely *in the presence of a check* -- not wrong, not broken, just
unverifiable. Most of these predate replay.ps1 writing those fields, and they are
probably fine.

    python scripts/uncitable_runs.py

Exit code is always 0. This tool reports a property of the corpus; it does not
judge it, and a non-zero exit here would be the same mistake as the one above --
a verdict where there was only a measurement.

What a hit is NOT, stated because the number invites the misreading:
  * not "this run was broken"
  * not "this run's numbers are wrong"
  * not "delete it"
What a hit IS:
  * "citing this run, you cannot mechanically confirm it still applies"
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"

# The fields that make staleness checkable. `code` is deliberately absent: knowing
# a run's commit is not a precondition, and including it would put all 119 runs
# back on this list for the reason the earlier probe got wrong.
REQUIRED = ("thresholds", "llama_version")


def load_runs() -> list[dict]:
    """Every run meta directly under logs/.

    Not recursive, and that is load-bearing rather than incidental. An archive of
    the same files under logs/ doubled the count and put half of them in the
    "unreadable" bucket, because they still carried the BOM they were archived
    with -- which reads as "cannot judge" rather than "this is a copy". See the
    gitignore note on scripts/_archive/metas-*/ for the same incident.
    """
    runs = []
    for path in sorted(LOGS.glob("run.*.meta")):
        name = path.stem.removeprefix("run.")
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
            runs.append({"name": name, "when": meta.get("when", ""), "meta": meta})
        except (OSError, json.JSONDecodeError) as exc:
            runs.append({"name": name, "when": "", "meta": {},
                         "unreadable": f"{type(exc).__name__}: {exc}"})
    return runs


def main() -> int:
    runs = load_runs()
    unreadable = [r for r in runs if "unreadable" in r]
    listed = []
    for run in runs:
        missing = [f for f in REQUIRED if not run["meta"].get(f)]
        if missing:
            listed.append((run, missing))

    print(f"  runs in logs/                 {len(runs)}")
    print(f"  unreadable (not a staleness issue)  {len(unreadable)}")
    print(f"  non-citable (missing preconditions) {len(listed)}")
    print()

    if unreadable:
        print("  unreadable -- a parser problem, reported separately so the two")
        print("  populations never get summed together:")
        for run in unreadable:
            print(f"    {run['name']:<20} {run['unreadable']}")
        print()

    if not listed:
        print("  none: every run recorded both fields, so every one is checkable.")
        return 0

    print("  NON-CITABLE (cannot be checked for staleness; not known to be wrong)")
    print(f"  {'run':<22}{'when':<21}missing")
    for run, missing in sorted(listed, key=lambda r: str(r[0]["when"]), reverse=True):
        print(f"  {run['name']:<22}{str(run['when'])[:19]:<21}{', '.join(missing)}")

    listed_names = {run["name"] for run, _ in listed}
    unreadable_names = {r["name"] for r in unreadable}
    citable = [r for r in runs
               if r["name"] not in listed_names and r["name"] not in unreadable_names]
    print()
    print(f"  citable {len(citable)} / {len(runs) - len(unreadable)} parseable")
    print()
    print("  Reading this list: it says a run cannot be verified, nothing more.")
    print("  Whether any of them is actually still cited is a separate question,")
    print("  and answering it needs to know what is being cited -- which does not")
    print("  exist yet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())