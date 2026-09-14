"""The orchestrator's lock owner record must be READABLE from inside the sandbox.

The gate this file defends is F-0093's: the orchestrator holds `audit/.lock` around
the child and hands it the nonce, and the child must confirm that nonce against
`audit/.lock/owner` before skipping acquisition. A missing owner is a fail-closed
stop — correct, because a child that cannot prove its parent owns the lock must not
assume it does.

The seed could not deliver that file. `_seed` collects untracked work with
`git ls-files --others --exclude-standard`, and `--exclude-standard` honours
`.gitignore`, whose first line is `audit/.lock`. So the one record the gate reads was
the one record excluded from the tree the gate runs in. Measured on Ouroboros-4: four
of five sessions refused to start, and the fifth reported a nonce match with no file
to match against — a gate that is both unsatisfiable and non-deterministic.

Every test here builds a real repo, runs the real seed, and reads the resulting tree.
The control test is the load-bearing one: it proves the record arrives because it is
copied BY NAME, not because the general untracked-file loop happens to carry it. Take
the named copy out of `_seed` and the control still passes while the first test turns
red — which is the whole point of having both.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.harness import xcheck_submodule

runner = xcheck_submodule("runner")

GIT = shutil.which("git")
NONCE = "0813d1dbfe13e4fd"
OWNER = f"pid=1\nrole=Auditor\nnonce={NONCE}\n"


def git(cwd, *args):
    return subprocess.run([GIT] + list(args), cwd=str(cwd), capture_output=True,
                          text=True, timeout=60)


def make_repo(with_owner=True):
    """A throwaway git repo in a SYSTEM temp dir (never in the project tree, F-0120).

    `.gitignore` carries the shipped rule verbatim — the exclusion is the subject
    here, so a test repo that omitted it would prove nothing about the shipped one.
    """
    tmp = Path(tempfile.mkdtemp(prefix="xcheck-lockseed-test-"))
    (tmp / "audit").mkdir()
    (tmp / "audit" / "XCHECK.md").write_text("# XCHECK\n", encoding="utf-8")
    (tmp / ".gitignore").write_text("audit/.lock\n", encoding="utf-8")
    git(tmp, "init", "-q")
    git(tmp, "config", "user.email", "t@example.invalid")
    git(tmp, "config", "user.name", "test")
    git(tmp, "add", "-A")
    git(tmp, "commit", "-qm", "base")
    if with_owner:
        (tmp / "audit" / ".lock").mkdir()
        (tmp / "audit" / ".lock" / "owner").write_text(OWNER, encoding="utf-8")
    return tmp


class TheInheritedNonceIsCheckableFromInside(unittest.TestCase):

    def setUp(self):
        self.project = make_repo()
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        self.sb = runner.Sandbox(self.project, runner.PROFILES["readonly"], "Auditor")
        self.wt = self.sb.enter()
        self.addCleanup(self.sb.leave)

    def test_the_owner_record_reaches_the_session(self):
        owner = self.wt / "audit" / ".lock" / "owner"
        self.assertTrue(owner.is_file(),
                        f"{owner} is absent — the child cannot confirm its inherited "
                        f"nonce, so F-0093 makes it refuse to start")
        self.assertIn(NONCE, owner.read_text(encoding="utf-8"),
                      "the record arrived but carries a different nonce")

    def test_the_general_untracked_path_does_not_carry_it(self):
        """CONTROL. Without this the first test could pass for the wrong reason."""
        seeded = git(self.project, "ls-files", "--others", "--exclude-standard").stdout
        unignored = git(self.project, "ls-files", "--others").stdout
        self.assertNotIn("audit/.lock", seeded,
                         "CONTROL BROKEN: the seed's own untracked walk already lists "
                         "the owner record, so this repo does not reproduce the "
                         "exclusion the fix is about")
        self.assertIn("audit/.lock/owner", unignored,
                      "CONTROL BROKEN: the record is missing from the project itself, "
                      "so its absence downstream would prove nothing")

    def test_the_record_does_not_travel_back_out(self):
        """Readable IN is not the same as claimable OUT.

        Carrying a lock record back to the project would be carrying a claim on a
        lock (see `parallel.NOT_AN_ARTIFACT`). It stays ignored inside the worktree,
        so neither the seed commit nor `capture()` can see it as the session's work.
        """
        tracked = git(self.wt, "ls-files").stdout
        self.assertNotIn("audit/.lock", tracked,
                         "the owner record was COMMITTED into the seed — it would come "
                         "back through the courier as a claim on a lock")
        captured = self.sb.capture()
        paths = [] if captured is None else list(captured[1])
        self.assertEqual([p for p in paths if "audit/.lock" in p], [],
                         f"capture() reports the lock record as the session's own "
                         f"change: {paths}")


class NoLockMeansNoLock(unittest.TestCase):
    """The seed copies a record that exists. It never invents one.

    A sandbox that manufactured an owner file would turn the fail-closed gate into a
    rubber stamp: an unorchestrated session would find a lock record for a lock nobody
    holds.
    """

    def test_an_absent_owner_stays_absent(self):
        project = make_repo(with_owner=False)
        self.addCleanup(shutil.rmtree, project, ignore_errors=True)
        sb = runner.Sandbox(project, runner.PROFILES["readonly"], "Auditor")
        wt = sb.enter()
        self.addCleanup(sb.leave)
        self.assertFalse((wt / "audit" / ".lock").exists(),
                         "the sandbox created a lock record the project never had")


if __name__ == "__main__":
    unittest.main()
