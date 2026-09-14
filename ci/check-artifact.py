#!/usr/bin/env python3
"""Is what we would ship actually shippable?

Two releases of this project committed scratch artefacts, once a 6 754-line copy of
the orchestrator (`retro-5.md`). A `.pyc` was tracked in git until phase 1. None of
that was caught by a test, because tests look at behaviour and this is a property of
the TREE.

So this checks the tree itself, and it is wired into `ci/run-checks.sh` rather than
living as a release-day ritual — a cleanliness rule nobody runs until release day is a
rule that fails on release day.

Three claims:
  1. nothing tracked in git is build output (`__pycache__`, `*.pyc`, egg-info, dist);
  2. nothing tracked looks like scratch (`*.orig`, `*.rej`, `*.bak`, `*copy*`, a temp
     name) — the shape the two committed accidents had;
  3. every file the installer ships exists and appears in the manifest list, and the
     installer's list and `xcheck.upgrade.SHIPPED` are the same set.

Exit 0 and one summary line when clean; exit 1 naming every offender otherwise.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xcheck.upgrade import SHIPPED                      # noqa: E402

BUILD_OUTPUT = re.compile(r"(^|/)(__pycache__/|.*\.pyc$|.*\.egg-info/|dist/|build/)")
SCRATCH = re.compile(
    r"(^|/)("
    r".*\.(orig|rej|bak|swp|tmp)$"
    r"|.*[-_. ]copy( ?\d+)?(\.[a-z0-9]+)?$"     # "orchestrator copy 2.py"
    r"|tmp[-_.].*"
    r"|scratch.*"
    r"|.*\.py\.[0-9]+$"
    r")", re.IGNORECASE)

# Files whose names would otherwise trip the scratch pattern and are deliberate.
ALLOWED = set()


def tracked():
    p = subprocess.run(["git", "ls-files"], cwd=str(ROOT), capture_output=True,
                       text=True, timeout=60)
    if p.returncode != 0:
        print("check-artifact: not a git repository — nothing to check")
        return None
    return [f for f in p.stdout.splitlines() if f]


def main():
    files = tracked()
    if files is None:
        return 0
    bad = []
    for f in files:
        if f in ALLOWED:
            continue
        if BUILD_OUTPUT.search(f):
            bad.append((f, "build output — regenerated on every install, and stale in "
                           "git the moment the source changes"))
        elif SCRATCH.search(f):
            bad.append((f, "looks like a scratch artefact; this project has committed "
                           "two of those, once a whole copy of the orchestrator"))

    # The shipped set: present on disk, and the same set on both sides. A file the
    # installer copies but `upgrade` does not know about is a file nothing ever
    # refreshes and nothing ever protects.
    sh = (ROOT / "install.sh").read_text(encoding="utf-8")
    m = re.search(r'^SHIPPED="([^"]+)"', sh, re.M)
    if not m:
        bad.append(("install.sh", "no SHIPPED list — the manifest would describe "
                                  "nothing"))
    else:
        installer = set(m.group(1).split())
        if installer != set(SHIPPED):
            only_i = sorted(installer - set(SHIPPED))
            only_u = sorted(set(SHIPPED) - installer)
            bad.append(("install.sh / xcheck/upgrade.py",
                        f"the shipped sets disagree — installer only: {only_i}, "
                        f"upgrade only: {only_u}"))
        for rel in sorted(installer):
            if not (ROOT / rel).is_file():
                bad.append((rel, "shipped by install.sh but missing from the tree"))

    if bad:
        print(f"FAIL: {len(bad)} artefact problem(s):")
        for f, why in bad:
            print(f"  - {f}: {why}")
        return 1
    print(f"clean: {len(files)} tracked files, no build output, no scratch artefacts, "
          f"{len(SHIPPED)} shipped files present and listed on both sides")
    return 0


if __name__ == "__main__":
    sys.exit(main())
