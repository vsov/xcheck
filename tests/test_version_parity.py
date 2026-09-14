"""One version, every surface — and a test that fails the build when they disagree.

Before this phase `XCHECK.md` said 0.3.0, both plugin manifests said 0.7.1, and the
newest git tag was v0.3.0. Nothing noticed, because nothing was looking: each surface
was bumped by hand, at a different time, by whoever remembered.

So the deliverable here is not the bump. It is this file, and specifically the fact
that it READS all five values from their own files. A test that hard-coded "0.8.0"
would itself become a sixth surface that can drift — the same defect wearing a test's
clothing, which is exactly the mistake retro-6 recorded once already (a predicate
tested directly while nothing checked it was wired to the consumer).
"""

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

from tests.harness import REPO, xcheck_module          # noqa: F401  (path bootstrap)

import xcheck

XCHECK_MD_VERSION = re.compile(r"^version:\s*(\S+)\s*$", re.M)
PYPROJECT_VERSION = re.compile(r'^version\s*=\s*"([^"]+)"', re.M)
CHANGELOG_VERSION = re.compile(r"^##\s+(\d+\.\d+\.\d+)\b", re.M)
CLI_VERSION = re.compile(r"^xcheck\s+(\S+)\s*$")


def surfaces():
    """Every place a version number is written, read from the file itself.

    Six of these are files. The seventh, `bin/xcheck --version`, is a SUBPROCESS: it
    is the exact question the release gate asks an installed artifact, and running it
    proves the door opens and answers, not merely that a constant exists. It cannot
    drift from `xcheck.__version__` (it prints it) — its job here is that the entry
    point works at all, which a constant comparison can never show.
    """
    md = (REPO / "XCHECK.md").read_text(encoding="utf-8")
    py = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    ch = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    md_m, py_m, ch_m = (XCHECK_MD_VERSION.search(md), PYPROJECT_VERSION.search(py),
                        CHANGELOG_VERSION.search(ch))
    p = subprocess.run([sys.executable, str(REPO / "bin" / "xcheck"), "--version"],
                       capture_output=True, text=True, timeout=120)
    cli_m = CLI_VERSION.match(p.stdout.strip()) if p.returncode == 0 else None
    return {
        "xcheck.__version__": xcheck.__version__,
        "XCHECK.md version:": md_m.group(1) if md_m else None,
        ".claude-plugin/plugin.json": json.loads(
            (REPO / ".claude-plugin" / "plugin.json").read_text())["version"],
        ".codex-plugin/plugin.json": json.loads(
            (REPO / ".codex-plugin" / "plugin.json").read_text())["version"],
        "pyproject.toml version": py_m.group(1) if py_m else None,
        # The newest `## X.Y.Z` heading. A release whose changelog's top entry is some
        # older version is a release nobody wrote down.
        "CHANGELOG.md newest entry": ch_m.group(1) if ch_m else None,
        "bin/xcheck --version": cli_m.group(1) if cli_m else None,
    }


class TheSurfacesCarryOneVersion(unittest.TestCase):

    def test_every_surface_agrees_with_the_package(self):
        found = surfaces()
        width = max(len(k) for k in found)
        print("\nversion surfaces:")
        for k, v in found.items():
            print(f"  {k:<{width}}  {v}")
        self.assertNotIn(None, found.values(), "a surface's version could not be READ — "
                                               "an unreadable surface is a drifted one")
        self.assertEqual(len(set(found.values())), 1,
                         f"the surfaces disagree: {found}")

    def test_the_version_is_a_release_number_not_a_placeholder(self):
        self.assertRegex(xcheck.__version__, r"^\d+\.\d+\.\d+$")

    def test_the_test_reads_the_files_rather_than_restating_the_number(self):
        # The guard on the guard: if this file ever hard-codes the version, it becomes
        # a sixth surface that can drift while reporting parity.
        src = Path(__file__).read_text(encoding="utf-8")
        body = src.split('"""', 2)[-1]                      # skip the module docstring
        self.assertNotIn(xcheck.__version__, body,
                         "this test must READ the version from each surface, never "
                         "restate it — a restated number drifts like any other copy")


class TheSchemaVersionIsDeclaredSeparately(unittest.TestCase):
    """The release version and the state schema version move independently: a patch
    release must not imply a migration, and a schema bump must not wait for one."""

    def test_the_schema_version_is_its_own_number(self):
        from xcheck.state import SCHEMA_VERSION
        self.assertIsInstance(SCHEMA_VERSION, int)
        self.assertNotEqual(str(SCHEMA_VERSION), xcheck.__version__)


class TheEntryPointsAllReachTheSameCli(unittest.TestCase):
    """`bin/xcheck`, `python3 -m xcheck` and the console script are three doors into
    one grammar. Packaging ADDS doors; it never moves the one the skills use."""

    def test_bin_xcheck_and_module_invocation_print_the_same_help(self):
        by_path = subprocess.run([sys.executable, str(REPO / "bin" / "xcheck"), "--help"],
                                 capture_output=True, text=True, timeout=120)
        by_module = subprocess.run([sys.executable, "-m", "xcheck", "--help"],
                                   cwd=str(REPO), capture_output=True, text=True,
                                   timeout=120)
        self.assertEqual(by_path.returncode, 0, by_path.stderr)
        self.assertEqual(by_module.returncode, 0, by_module.stderr)
        self.assertEqual(by_path.stdout, by_module.stdout)

    def test_the_console_script_target_exists_and_is_callable(self):
        # Read with a regex rather than `tomllib`: the package supports Python 3.10,
        # where `tomllib` does not exist. A test that needs a newer interpreter than
        # the thing it tests is testing a different thing.
        py = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        m = re.search(r'^xcheck\s*=\s*"([^"]+)"', py, re.M)
        self.assertIsNotNone(m, "no console-script entry in pyproject.toml")
        target = m.group(1)
        module, _, attr = target.partition(":")
        mod = __import__(module, fromlist=[attr])
        self.assertTrue(callable(getattr(mod, attr)),
                        f"{target} is declared as the console script but is not callable")

    def test_the_package_declares_no_runtime_dependencies(self):
        py = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        m = re.search(r"^dependencies\s*=\s*\[([^\]]*)\]", py, re.M)
        self.assertIsNotNone(m, "pyproject.toml declares no `dependencies` key at all")
        self.assertEqual(m.group(1).strip(), "",
                         "N2: xcheck runs inside other people's projects and must never "
                         "be the reason their dependency resolution changed")


if __name__ == "__main__":
    unittest.main()
