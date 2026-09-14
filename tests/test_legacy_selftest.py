"""Characterization net for the embedded selftest.

This is the one test that treats `bin/xcheck selftest` as a black box. It exists
so the phase-3 package split — which moves 5 500 lines of embedded tests into
`xcheck/selftest.py` — is a move and not a rewrite: the same count must still
report PASS afterwards.

It runs the real executable as a subprocess rather than calling `selftest()`
in-process, because the exit code is half of what is being characterized.
"""

import re
import subprocess
import sys
import unittest

from tests.harness import XCHECK

# Bump deliberately when a check is added to the embedded suite. A silent drift
# in this number means checks were lost or duplicated in a refactor.
#
# 284 -> 158 in phase 5. The drop of 126 is NOT a refactor loss: every deleted
# check had a Markdown parser for its whole subject, and each one is accounted
# for by name in docs/phase-5-deleted-checks.md, with the replacement that now
# covers its concern. An unexplained drop is indistinguishable from deleting an
# inconvenient test, which retro-6.md already recorded once — so this number and
# that table are checked against each other rather than either alone.
# 158 -> 162 in phase 8: four containment checks (contained skip-permissions +
# profile resolution, the env allowlist + redactor, the outcome classification, the
# lease). They are the portable half of the phase; the claims that need real child
# processes and a real git repo live in tests/test_runner_sandbox.py.
# 162 -> 167 in phase 9: five envelope checks (the 13 declared fields with `unknown`
# recorded rather than guessed, the append-only stream, the versioned --json contract,
# the four measured independence levels, and `fixed-by` bound to the dispatch record).
# Again the portable half: three real sessions, both CLI surfaces and the real write
# verbs are exercised in tests/test_envelope.py.
# 167 -> 168 in the W-06 correction: one budget check. `--budget` used to gate only the
# START of a session, so a session dispatched a second under the ceiling could run a full
# `session_timeout` past it — W-06 spent 8055.8s under `--budget 7200` and called the
# budget unexhausted. The new arm carries its own control: with the worst case removed
# the old start-gating rule runs every session, so the arm cannot pass by accident.
# 168 -> 169 in phase 8 (backpressure): one more `report_gate` row. `stop-critical` is a
# new human gate, and the F-0116 table exists so a new gate cannot ship with an empty or
# duplicated message — a stop that reads like another stop teaches the operator nothing.
EXPECTED_CHECKS = 169


class LegacySelftest(unittest.TestCase):
    def test_embedded_selftest_passes_with_the_expected_check_count(self):
        p = subprocess.run([sys.executable, str(XCHECK), "selftest"],
                           capture_output=True, text=True, timeout=600)
        self.assertEqual(p.returncode, 0, msg=p.stdout[-2000:])
        m = re.search(r"^PASS \((\d+)/(\d+)\)$", p.stdout, re.M)
        self.assertIsNotNone(m, msg="no PASS (n/n) summary line:\n" + p.stdout[-2000:])
        passed, total = int(m.group(1)), int(m.group(2))
        self.assertEqual(passed, total)
        self.assertEqual(total, EXPECTED_CHECKS)

    def test_selftest_rejects_a_trailing_argument(self):
        """A glued typo must not ride along and still report PASS with exit 0 (F-0019)."""
        p = subprocess.run([sys.executable, str(XCHECK), "selftest", "--oops"],
                           capture_output=True, text=True, timeout=600)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("selftest takes no arguments", p.stderr + p.stdout)


if __name__ == "__main__":
    unittest.main()
