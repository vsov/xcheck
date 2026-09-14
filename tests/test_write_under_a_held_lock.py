"""A session that obeys the lock protocol must still be able to write.

§4 rule 8 tells a writing session to take `audit/.lock` and hold it for the whole
session; F-0093 tells an orchestrated child that its parent already holds it and hands
over the nonce. Either way the lock is held while the session's verbs run — and
`write.run()` called `_apply()` with no lock, so every verb acquired again and was
refused by the lock its own session was obeying. The documented protocol and the CLI
were mutually exclusive.

Measured on the frozen Ouroboros-4 corpus before the fix: 130 of the 136 sessions that
produced nothing were refused this way while trying to record what they had found, and
7 of the 15 that succeeded did so by importing `_apply` and handing it the lock object
the CLI had no way to build.

Every test here runs the real CLI as a subprocess against a real fixture project. The
five branches are one table because the interesting property is the SHAPE of the whole
table: exactly one row writes, and it is the row that names the right nonce.
"""

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from tests.harness import REPO, Fixture

NONCE = "abcdef0123456789"
OWNER = {"pid": 0, "role": "Auditor", "started": "2026-08-17T12:00:00+00:00",
         "host": "probe", "nonce": NONCE}


class TheVerbWritesUnderTheLockItsSessionHolds(unittest.TestCase):

    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)
        self.project = Path(self.fx.root)
        self.audit = self.project / "audit"

    def hold_lock(self, owner=None):
        (self.audit / ".lock").mkdir(exist_ok=True)
        (self.audit / ".lock" / "owner").write_text(
            json.dumps(owner or OWNER), encoding="utf-8")

    def file_finding(self, fid, nonce=None):
        # PHASE 9: the locator is resolved against the subject commit at the write
        # boundary, so the fixture is a repository and these two args are taken from it.
        # This module is about the LOCK, and a finding refused for a missing locator
        # would look exactly like a finding refused by the lock.
        if not (self.project / ".git").exists():
            self.fx.git_init()
        loc, sha = self.fx.locatable()
        env = dict(os.environ)
        env.pop("XCHECK_LOCK_INHERITED", None)
        if nonce is not None:
            env["XCHECK_LOCK_INHERITED"] = nonce
        r = subprocess.run(
            [sys.executable, "bin/xcheck", "--project", str(self.project),
             "file-finding", "--id", fid, "--title", fid, "--severity", "minor",
             "--dimension", "invariants", "--unit", "U01", "--pass", "P-01",
             "--body", "audit/XCHECK.md",
             "--locator", loc, "--source-hash", sha],
            capture_output=True, text=True, cwd=str(REPO), env=env)
        return r.returncode, (r.stdout + r.stderr)

    def filed(self, fid):
        state = json.loads((self.audit / "state.json").read_text(encoding="utf-8"))
        return any(f["id"] == fid for f in state.get("findings", []))

    def test_the_right_nonce_writes_and_leaves_the_lock_alone(self):
        """The defect, and the whole point: the holder's own verb records."""
        self.hold_lock()
        rc, out = self.file_finding("F-9103", nonce=NONCE)
        self.assertEqual(0, rc, f"a verb naming the held lock's own nonce was refused:\n{out}")
        self.assertTrue(self.filed("F-9103"), "the verb exited 0 without recording")
        self.assertTrue((self.audit / ".lock" / "owner").is_file(),
                        "the verb RELEASED a lock it did not acquire — the holder's "
                        "transaction would end from inside one of its steps")

    def test_a_wrong_nonce_is_refused_and_says_so(self):
        """Without this, 'it writes now' would also describe a lock that stopped working."""
        self.hold_lock()
        rc, out = self.file_finding("F-9102", nonce="0" * 16)
        self.assertEqual(1, rc, "a verb naming somebody else's lock was allowed to write")
        self.assertIn("does not match the owner", out)
        self.assertFalse(self.filed("F-9102"))

    def test_naming_no_nonce_under_a_held_lock_is_still_refused(self):
        """Unchanged behaviour. A caller that cannot name the lock has no claim on it,
        and the fix must not turn that into a write."""
        self.hold_lock()
        rc, out = self.file_finding("F-9101")
        self.assertEqual(1, rc)
        self.assertIn("audit/.lock is held", out)
        self.assertFalse(self.filed("F-9101"))

    def test_a_nonce_with_no_lock_behind_it_is_refused(self):
        """The signal says a lock is held and the disk says it is not. Acquiring anyway
        would mean writing under a lock nobody can point at."""
        rc, out = self.file_finding("F-9104", nonce=NONCE)
        self.assertEqual(1, rc)
        self.assertIn("no readable owner record", out)
        self.assertFalse(self.filed("F-9104"))

    def test_a_standalone_verb_with_no_lock_and_no_nonce_still_writes(self):
        """CONTROL. The fix must not have made writing conditional on the new signal."""
        rc, out = self.file_finding("F-9105")
        self.assertEqual(0, rc, f"an ordinary standalone write stopped working:\n{out}")
        self.assertTrue(self.filed("F-9105"))
        self.assertFalse((self.audit / ".lock").exists(),
                         "the verb left its own lock behind")

    def test_exactly_two_of_the_five_branches_write(self):
        """The table's shape, asserted as a table. Four separate green tests would not
        notice a change that made three branches writable."""
        rows = []
        self.hold_lock()
        rows.append(("held, right nonce", self.file_finding("F-9201", NONCE)[0]))
        rows.append(("held, wrong nonce", self.file_finding("F-9202", "0" * 16)[0]))
        rows.append(("held, no nonce", self.file_finding("F-9203")[0]))
        shutil.rmtree(self.audit / ".lock")
        rows.append(("free, nonce named", self.file_finding("F-9204", NONCE)[0]))
        rows.append(("free, no nonce", self.file_finding("F-9205")[0]))
        print("\n  WRITE UNDER A HELD LOCK")
        for label, rc in rows:
            print(f"    {label:20} {'writes' if rc == 0 else 'refused'}")
        self.assertEqual([0, 1, 1, 1, 0], [rc for _, rc in rows],
                         "the branch table changed shape")


if __name__ == "__main__":
    unittest.main()
