#!/usr/bin/env python3
"""The check scripts' own dependency contract: derive it, then verify it.

0.9.1 phase 6 (audit P0 #7). `ci/run-checks.sh` always calls the release gate; the gate
hard-fails when `build` is not importable — correctly, since faking a build another way
would produce a different artifact and report it as the one under test. But the workflow
installed only `ruff`, so a green cell proved that the RUNNER happened to have `build`
lying around. Reproduced by the auditor: under Python 3.11 the gate exits 1, and under
3.10 it passes only because that interpreter's ambient environment carries `build`.

Two things follow, and this file is both:

1. **Derived, not hand-listed.** The dependency set is read out of what the check
   scripts actually import and invoke. A list typed into `ci.yml` is correct the day it
   is typed and drifts the first time somebody adds a check — and drifts SILENTLY,
   because the runner's ambient state covers for it until the day it does not.
2. **Refused early, with the command.** A missing dependency should stop the run in a
   second with `python3 -m pip install "build==…"`, not a hundred lines into a build.

Run it directly to see the derivation:

    python3 ci/preflight.py            # verify, exit 1 on a missing REQUIRED dep
    python3 ci/preflight.py --print    # just print what was derived, always exit 0

`tests/test_ci_contract.py` compares this derivation against `.github/workflows/ci.yml`
and against `pyproject.toml`'s pins, so the three cannot disagree.
"""

import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The files whose needs ARE the CI contract. Not `xcheck/` — that is the product, and
# it is stdlib-only by a constraint of its own (pyproject: "NO runtime dependencies").
# `tests/` is here because `ci/run-checks.sh` runs the battery: a test that grew an
# import of `pytest` would be a CI dependency whether or not anyone declared it.
SOURCES = ("ci/*.py", "ci/*.sh", "tests/*.py", "tests/**/*.py")

# First-party names an install could never provide.
FIRST_PARTY = {"xcheck", "tests", "ci", "bin"}

# Filled by `derive()`: {name: [where it was seen]} for names that LOOK third-party and
# are a FILE in `tests/`. A test module may import a sibling by bare name — `import
# test_launch_surfaces`, which is what resolves when a file is run as
# `python tests/test_x.py` — and that top-level name is in none of the sets above, so
# the preflight used to refuse with `pip install "test_launch_surfaces"`: the right
# outcome for the wrong reason, and the next person to write one loses the same hour.
#
# Read off the disk, never typed here, so it cannot go stale; narrow to `tests/<name>.py`
# and nothing else, so a genuinely missing package still fails closed; and every hit is
# PRINTED, because a name that quietly stops being a dependency is how a real one
# disappears. Module-level rather than a third return value: `derive()`'s two-value
# result is a contract `tests/test_ci_contract.py` unpacks in three places, and a silent
# drop is the defect being fixed, so the record is public.
RECLASSIFIED = {}

# Bundled with CPython but absent from `sys.stdlib_module_names`. Named individually
# with the reason, because "it works on my machine" is the failure this file exists to
# stop and a silent allow-list is how that comes back.
BUNDLED = {
    "pip": "ships inside every venv via ensurepip; never installed separately",
}

# Derived names that are NOT fatal when missing, each with what happens instead. A name
# absent from this table is required — so a new dependency fails closed rather than
# quietly joining the optional set.
OPTIONAL = {
    "ruff": "ci/run-checks.sh falls back to `python -m py_compile`",
}

# `$PY -m mod`, `python3 -m mod`, `"$venv/bin/python" -m mod`
SH_DASH_M = re.compile(r"(?:python[0-9.]*|\$PY|\"\$PY\"|[\"']?\$\{?[A-Za-z_]*\}?[/\w.\"']*"
                       r"python[\w.\"']*)\s+-m\s+([A-Za-z_][\w.]*)")
# `$PY -c "import mod"` / `-c 'from mod import …'`
SH_DASH_C = re.compile(r"-c\s+[\"'][^\"']*?\b(?:import|from)\s+([A-Za-z_][\w.]*)")
# `command -v tool` — the shell idiom for "use it if it is here". A CLI, not a module.
SH_COMMAND_V = re.compile(r"command\s+-v\s+([A-Za-z_][\w.-]*)")


def _sources():
    out = set()
    for pat in SOURCES:
        out |= {p for p in ROOT.glob(pat) if p.is_file()}
    return sorted(out)


def derive():
    """({name: [where it was seen]}, {cli names}) — third-party only, first-party,
    stdlib, bundled and sibling-test-module names removed. The last of those is also
    recorded in `RECLASSIFIED`, which this call resets."""
    seen = {}
    clis = set()
    RECLASSIFIED.clear()

    def note(name, where):
        top = name.split(".")[0]
        if (top in sys.stdlib_module_names or top in FIRST_PARTY or top in BUNDLED
                or not top):
            return
        # After the checks above, never before: this may only rescue a name that would
        # otherwise have been reported as a package to install.
        #
        # A SAME-DIRECTORY sibling, checked on disk at the moment of the import. This
        # started as `tests/<stem>.py` only, and phase 19 hit it again one directory
        # over: `ci/tiers.py` holds the tier data and `ci/*.py` imports it by bare name,
        # which read as a PyPI distribution called `tiers`.
        #
        # Scoped to the importer's OWN directory rather than to a union of both, which
        # is the tempting one-liner: a union would let `tests/x.py` rescue a real missing
        # package that happens to share a name with something in `ci/`, and a
        # reclassifier that rescues too much is how a genuinely absent dependency stops
        # being reported. Still read off the disk, so deleting the file puts the name
        # straight back to being a missing package, and still PRINTED.
        if (ROOT / Path(where).parent / f"{top}.py").is_file():
            RECLASSIFIED.setdefault(top, []).append(where)
            return
        seen.setdefault(top, []).append(where)

    for f in _sources():
        rel = f.relative_to(ROOT).as_posix()
        text = f.read_text(encoding="utf-8")
        if f.suffix == ".py":
            # AST, not a regex: an import inside a function or a `try:` is still an
            # import, and this repository has several.
            for node in ast.walk(ast.parse(text, filename=str(f))):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        note(a.name, rel)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    note(node.module, rel)
            continue
        for m in SH_DASH_M.finditer(text):
            note(m.group(1), rel)
        for m in SH_DASH_C.finditer(text):
            note(m.group(1), rel)
        for m in SH_COMMAND_V.finditer(text):
            clis.add(m.group(1))
            note(m.group(1), rel)
    return seen, clis


def pins():
    """{package: pinned spec} from pyproject's `ci` extra — the ONE place versions live.

    Read with a regex rather than `tomllib`, which does not exist on 3.10 and this
    project supports 3.10. `tests/test_version_parity.py` reads the same file the same
    way.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"^ci\s*=\s*\[(.*?)\]", text, re.S | re.M)
    if not block:
        return {}
        # (no `ci` extra: the caller reports it — an empty pin table is a finding,
        # not a default)
    out = {}
    for spec in re.findall(r"[\"']([^\"']+)[\"']", block.group(1)):
        out[re.split(r"[=<>!~ ]", spec, 1)[0]] = spec
    return out


def install_command(names, py=None):
    p = py or sys.executable
    table = pins()
    return f"{p} -m pip install " + " ".join(
        f'"{table.get(n, n)}"' for n in sorted(names))


def available(name, is_cli=False):
    """Whether the dependency is usable — asked the way its CONSUMER asks.

    A module is checked with a SUBPROCESS import, because this process may already hold
    it and because that is exactly how the release gate asks (`"$PY" -c "import
    build"`). A tool reached through `command -v` is checked on PATH: `ruff` is a
    compiled binary whose package is not importable, so an import test would report the
    installed tool as absent and degrade a check that was working.
    """
    if is_cli:
        return shutil.which(name) is not None
    return subprocess.run([sys.executable, "-c", f"import {name}"],
                          capture_output=True).returncode == 0


def main(argv):
    seen, clis = derive()
    table = pins()
    print(f"== ci preflight: {len(seen)} third-party dependency(s) derived from "
          f"{len(_sources())} check-script source(s) ==")
    if RECLASSIFIED:
        print("  reclassified as first-party (a file in tests/, not a package on PyPI):")
        for name in sorted(RECLASSIFIED):
            print(f"    {name:<28} <- {', '.join(sorted(set(RECLASSIFIED[name])))}")
    missing = []
    for name in sorted(seen):
        kind = "cli" if name in clis else "module"
        need = "optional" if name in OPTIONAL else "REQUIRED"
        ok = available(name, name in clis)
        mark = "ok" if ok else ("absent" if name in OPTIONAL else "MISSING")
        print(f"  {name:<10} {kind:<10} {need:<8} {table.get(name, '(unpinned)'):<16} "
              f"{mark}   <- {', '.join(sorted(set(seen[name])))}")
        if not ok and name not in OPTIONAL:
            missing.append(name)
        elif not ok:
            print(f"             degraded: {OPTIONAL[name]}")
    unpinned = sorted(n for n in seen if n not in table)
    if unpinned:
        print(f"  note: {unpinned} have no pin in pyproject [project.optional-"
              f"dependencies].ci — CI would install whatever is newest that day")
    if "--print" in argv:
        return 0
    if missing:
        print()
        print(f"FAIL: ci preflight — {', '.join(missing)} not importable by "
              f"{sys.executable}.")
        print(f"      Install the CI toolchain:  {install_command(missing)}")
        print( "      Pinned in pyproject.toml [project.optional-dependencies].ci, and")
        print( "      installed by .github/workflows/ci.yml in every matrix cell.")
        print( "      The release gate does NOT fall back to another build path: a")
        print( "      different path produces a different artifact and reports it as")
        print( "      the one under test.")
        return 1
    print("preflight: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
