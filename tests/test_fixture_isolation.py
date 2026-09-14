"""A fixture is a different project, and it must not inherit this audit's lock.

F-0125 (`U24 write tests inherit an unrelated audit lock`) and F-0132 (`Parallel
preservation witness fails under inherited lock context`), both filed by the W-06 live run
against the W-08 fix that caused them.

The finding prescribes the direction: **isolate fixture execution rather than weaken
`held_lock`**, and preserve a test that a fixture-owned inherited lock is still honoured.
Both halves are asserted here, so a later "fix" that reached the same green by loosening
the production guard would turn this file red.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import tests
from tests.harness import REPO, xcheck_submodule

write = xcheck_submodule("write")

# The five modules AUDIT.md binds to U24, the unit the finding was filed against.
U24 = ("tests.test_write_verbs", "tests.test_admin_verbs", "tests.test_migrate",
       "tests.test_cli_grammar", "tests.test_version_parity")
# U26 — the modules F-0132 was filed against, where the child is a REAL subprocess the
# fixture launches through `runner.child_environment`, not an in-process call.
U26 = ("tests.test_parallel_preservation", "tests.test_parallel_limits")
FOREIGN_NONCE = "c5b4cf938a22541d"          # the nonce from the session that found this


def run_battery(modules, reinstate):
    """A battery run in a child that inherits a foreign nonce.

    `reinstate=True` puts the variable back AFTER importing `tests`, which reproduces the
    world before the strip — the control arm, without a second copy of the suite to
    maintain and without an off-switch in the harness that production could ever read.
    """
    prog = (
        "import os, sys, unittest\n"
        "sys.path.insert(0, '.')\n"
        "import tests\n"
        f"reinstate = {reinstate!r}\n"
        "if reinstate:\n"
        f"    os.environ['XCHECK_LOCK_INHERITED'] = {FOREIGN_NONCE!r}\n"
        f"suite = unittest.defaultTestLoader.loadTestsFromNames({list(modules)!r})\n"
        "r = unittest.TextTestRunner(verbosity=0).run(suite)\n"
        "print(f'RAN={r.testsRun} FAILED={len(r.failures) + len(r.errors)}')\n"
        "sys.exit(0 if r.wasSuccessful() else 1)\n")
    env = dict(os.environ, XCHECK_LOCK_INHERITED=FOREIGN_NONCE)
    p = subprocess.run([sys.executable, "-c", prog], capture_output=True, text=True,
                       cwd=str(REPO), env=env, timeout=600)
    return p.returncode, p.stdout + p.stderr


class TheOrchestrationEnvironmentDoesNotReachAFixture(unittest.TestCase):

    def test_the_variables_are_gone_from_this_process(self):
        for name in tests.ORCHESTRATION_ENV:
            self.assertNotIn(name, os.environ,
                             f"{name} survived into the test process; every fixture "
                             f"command, subprocess and parallel child inherits it")

    def test_what_was_stripped_is_recorded_rather_than_discarded(self):
        """So a session debugging a green suite can see what it was actually running
        under. The dict is empty outside a session, which is the normal case."""
        self.assertIsInstance(tests.INHERITED_ORCHESTRATION, dict)
        self.assertFalse(set(tests.INHERITED_ORCHESTRATION) - set(tests.ORCHESTRATION_ENV))

    def test_the_u24_battery_passes_under_an_inherited_nonce(self):
        """The finding's own acceptance criterion, run as it words it: the five U24
        modules, with an arbitrary inherited nonce whose owner exists only in a parent
        audit, must all pass."""
        rc, out = run_battery(U24, reinstate=False)
        print(f"\n  U24 with a foreign nonce inherited: {out.strip().splitlines()[-1]}")
        self.assertEqual(0, rc, out[-3000:])

    def test_and_fails_without_the_strip(self):
        """CONTROL. Without it the test above proves only that the battery is green, not
        that the strip is what makes it green under a foreign nonce."""
        rc, out = run_battery(U24, reinstate=True)
        tail = [l for l in out.splitlines() if l.startswith("RAN=")]
        print(f"  U24 with the strip undone:          {tail[-1] if tail else out[-80:]}")
        self.assertEqual(1, rc, "the battery passed with a foreign nonce reinstated — "
                                "the strip is not what is protecting it, so this file is "
                                "measuring nothing")
        self.assertIn("has no readable owner record", out,
                      "it failed for some other reason than the inherited lock")
    def test_the_parallel_battery_passes_too_where_the_child_is_a_real_process(self):
        """F-0132. The other reported route: `runner.child_environment` copies every
        `XCHECK_*` variable into the child, so a real fixture child inherited the nonce
        even though nothing in the fixture ever read the environment itself."""
        rc, out = run_battery(U26, reinstate=False)
        print(f"  U26 with a foreign nonce inherited: {out.strip().splitlines()[-1]}")
        self.assertEqual(0, rc, out[-3000:])

    def test_and_the_parallel_battery_fails_without_the_strip(self):
        """CONTROL for F-0132, and it reproduces the finding's own count: the audit
        measured 6 failures plus 1 error over the 48 tests the battery held then.

        RAN moved 48 -> 49 in phase 16: `test_parallel_preservation` gained a two-arm
        control for its own leak check. The FAILED count is the load-bearing half and it
        did not move — the added test does not dispatch a session, so an inherited nonce
        has nothing in it to break. A change in either number needs this sentence
        rewritten, which is why the assertion pins both.
        """
        rc, out = run_battery(U26, reinstate=True)
        tail = [l for l in out.splitlines() if l.startswith("RAN=")]
        print(f"  U26 with the strip undone:          {tail[-1] if tail else out[-80:]}")
        self.assertEqual(1, rc, "the parallel battery passed with the nonce reinstated")
        self.assertIn("RAN=49 FAILED=7", out,
                      "the reproduced count moved from the 7 the finding recorded; "
                      "restate what this control is measuring")


class HeldLockWasNotWeakened(unittest.TestCase):
    """The other half of the prescription. The fix is isolation, so the production guard
    must still refuse exactly what it refused before."""

    def project(self, lock=None):
        import tempfile
        root = Path(tempfile.mkdtemp(prefix="xcheck-iso-"))
        self.addCleanup(__import__("shutil").rmtree, root, True)
        (root / "audit").mkdir()
        if lock is not None:
            (root / "audit" / ".lock").mkdir()
            (root / "audit" / ".lock" / "owner").write_text(json.dumps(lock),
                                                            encoding="utf-8")
        return root

    def with_nonce(self, nonce, root):
        os.environ["XCHECK_LOCK_INHERITED"] = nonce
        self.addCleanup(os.environ.pop, "XCHECK_LOCK_INHERITED", None)
        return write.held_lock(root)

    def test_a_signal_with_no_owner_record_to_match_is_still_refused(self):
        root = self.project(lock=None)
        with self.assertRaises(SystemExit) as caught:
            self.with_nonce(FOREIGN_NONCE, root)
        self.assertIn("has no readable owner record", str(caught.exception))

    def test_a_signal_that_disagrees_with_the_owner_is_still_refused(self):
        root = self.project(lock={"nonce": "0000000000000000", "role": "Auditor"})
        with self.assertRaises(SystemExit) as caught:
            self.with_nonce(FOREIGN_NONCE, root)
        self.assertIn("does not match the owner", str(caught.exception))

    def test_a_fixture_that_owns_its_lock_still_has_its_nonce_honoured(self):
        """The preservation the finding asks for by name: isolation must not cost a
        fixture the ability to hold its own lock and write under it."""
        root = self.project(lock={"nonce": FOREIGN_NONCE, "role": "Auditor"})
        lock = self.with_nonce(FOREIGN_NONCE, root)
        self.assertIsNotNone(lock, "a fixture's own nonce stopped being honoured")
        self.assertEqual(FOREIGN_NONCE, lock.token)

    def test_no_signal_means_no_inherited_lock(self):
        self.assertIsNone(write.held_lock(self.project(lock=None)))


if __name__ == "__main__":
    unittest.main()
