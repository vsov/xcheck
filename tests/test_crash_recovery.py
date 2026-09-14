"""Kill the writer at each step of the durable write, then ask what survived.

`write_state` is five operations, not one: validate, write a temp file, fsync it,
`os.replace` it over the real file, fsync the directory. A crash between any two of
them leaves a different world, and "atomic" is a claim about exactly this — that every
one of those worlds is either the whole old state or the whole new one, with no decoy
temp file left behind for a later glob to mistake for authority.

The write is followed by two more steps outside `write_state`: the courier's git
commit, and the regeneration of the Markdown views. Those are not atomic with it and
cannot be, so the claim there is weaker and different: the tool must REPORT the
incomplete step rather than proceed as though it had finished. A stale view is refused
by name; an uncommitted state is still durable and still visible in the worktree.

Each crash is injected by making the real syscall raise at the chosen moment, so the
code under test takes its real error path. No branch is added to production code to
make it testable — a crash test that needs the writer's cooperation is testing the
cooperation.
"""

import json
import os
import subprocess
import unittest
from unittest import mock

from tests.harness import (
    Fixture, finding_record, state_doc, xcheck_submodule,
)

state = xcheck_submodule("state")
views = xcheck_submodule("views")

TMP_DECOY = f".{state.STATE_FILENAME}.tmp"


class Boom(RuntimeError):
    """The crash. A distinct type so a test cannot pass on some other exception."""


class DurableWriteCrashPoints(unittest.TestCase):
    """Points 1-3: inside `write_state`, where the promise is all-or-nothing."""

    def setUp(self):
        self.fx = Fixture(doc=state_doc(findings=[finding_record()]))
        self.addCleanup(self.fx.cleanup)
        self.before = state.load_state(self.fx.audit)
        self.original = (self.fx.audit / state.STATE_FILENAME).read_bytes()

    def assert_old_world_intact(self, where):
        after = state.load_state(self.fx.audit)          # loads cleanly, or this raises
        self.assertEqual((self.fx.audit / state.STATE_FILENAME).read_bytes(),
                         self.original,
                         f"{where}: the state file changed despite the crash")
        self.assertEqual(after.state_revision, self.before.state_revision)
        self.assertFalse((self.fx.audit / TMP_DECOY).exists(),
                         f"{where}: a stale {TMP_DECOY} survived — a decoy that looks "
                         f"like durable state to anything scanning the directory")

    def test_1_crash_before_the_temp_write(self):
        with mock.patch("builtins.open", side_effect=Boom("crash: before temp write")):
            with self.assertRaises(Boom):
                state.write_state(self.fx.audit, self.before)
        self.assert_old_world_intact("crash 1 (before the temp write)")

    def test_2_crash_after_the_temp_write_before_fsync(self):
        with mock.patch("os.fsync", side_effect=Boom("crash: before fsync")):
            with self.assertRaises(Boom):
                state.write_state(self.fx.audit, self.before)
        self.assert_old_world_intact("crash 2 (after the temp write, before fsync)")

    def test_3_crash_after_fsync_before_replace(self):
        with mock.patch("os.replace", side_effect=Boom("crash: before replace")):
            with self.assertRaises(Boom):
                state.write_state(self.fx.audit, self.before)
        self.assert_old_world_intact("crash 3 (after fsync, before os.replace)")

    def test_3b_crash_after_replace_before_the_directory_fsync(self):
        # The rename already happened, so the NEW state is what a reader sees. The
        # directory fsync only makes the rename itself durable across a power loss —
        # which this process-level crash is not. The state must still load cleanly and
        # carry the new revision, and no decoy may survive.
        real_fsync, calls = os.fsync, []

        def fsync(fd):
            calls.append(fd)
            if len(calls) > 1:                       # the FILE fsync ran; fail the DIR one
                raise Boom("crash: before the directory fsync")
            return real_fsync(fd)

        with mock.patch("os.fsync", side_effect=fsync):
            with self.assertRaises(Boom):
                state.write_state(self.fx.audit, self.before)
        after = state.load_state(self.fx.audit)
        self.assertEqual(after.state_revision, self.before.state_revision + 1,
                         "the replace had already happened: the new state is the world")
        self.assertFalse((self.fx.audit / TMP_DECOY).exists())


class CrashBetweenTheWriteAndItsFollowUpSteps(unittest.TestCase):
    """Points 4-5: outside `write_state`, where the honest promise is a REPORT."""

    def project(self):
        fx = Fixture(doc=state_doc(findings=[finding_record()]))
        self.addCleanup(fx.cleanup)
        return fx

    def git(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                              text=True, timeout=60)

    def test_4_crash_after_the_state_write_before_the_git_commit(self):
        fx = self.project()
        self.git(fx.root, "init", "-q")
        self.git(fx.root, "config", "user.email", "t@example.invalid")
        self.git(fx.root, "config", "user.name", "test")
        self.git(fx.root, "add", "-A")
        self.git(fx.root, "commit", "-qm", "base")
        before = state.load_state(fx.audit)

        # A real write verb IS this crash point: `_apply` makes the state durable and
        # regenerates the views, and the git commit belongs to the courier, which runs
        # later — in `run_session`, after the agent exits. Killing the orchestrator
        # here leaves exactly what this asserts.
        code, out = fx.run("set-status", "F-0001", "accepted")
        self.assertEqual(code, 0, out)

        after = state.load_state(fx.audit)            # loads cleanly, or this raises
        self.assertEqual(after.findings[0].status, "accepted")
        self.assertEqual(after.state_revision, before.state_revision + 1)
        # Nothing is silently lost: the uncommitted transition is visible in the tree,
        # which is what lets the NEXT courier commit pick it up rather than skip it.
        dirty = self.git(fx.root, "status", "--porcelain").stdout
        self.assertIn("audit/state.json", dirty)
        code, out = fx.run("status")
        self.assertEqual(code, 0, out)

    def test_5_crash_between_the_state_write_and_the_view_regeneration(self):
        fx = self.project()
        doc = json.loads((fx.audit / "state.json").read_text())
        doc["findings"][0]["status"] = "accepted"
        doc["state_revision"] += 1
        (fx.audit / "state.json").write_text(
            json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        # …and the crash lands here, before write_views. The views now describe the
        # previous world.

        for command in ("status", "lint", "metrics"):
            code, out = fx.run(command)
            self.assertNotEqual(code, 0, f"`{command}` proceeded on a half-applied "
                                         f"transaction as if it had finished:\n{out}")
            self.assertIn("disagree with state.json", out)
            self.assertIn("render-views", out, "the report must name the repair")

    def test_5b_the_named_repair_actually_repairs(self):
        # A refusal that names a fix nobody can run is a dead end, so the recovery path
        # is part of the crash test rather than a separate story.
        fx = self.project()
        doc = json.loads((fx.audit / "state.json").read_text())
        doc["findings"][0]["status"] = "accepted"
        doc["state_revision"] += 1
        (fx.audit / "state.json").write_text(
            json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.assertNotEqual(fx.run("status")[0], 0)
        code, out = fx.run("render-views")
        self.assertEqual(code, 0, out)
        code, out = fx.run("status")
        self.assertEqual(code, 0, out)
        self.assertIn("accepted", (fx.audit / "LEDGER.md").read_text())


class ANoOpWriteLeavesNoDebris(unittest.TestCase):
    """The control arm for the four crash points: with nothing failing, the same code
    path completes and leaves the directory clean. Without this, "no temp file
    survived" could just mean the temp file was never created."""

    def test_a_successful_write_advances_the_revision_and_leaves_no_temp(self):
        fx = Fixture(doc=state_doc(findings=[finding_record()]))
        self.addCleanup(fx.cleanup)
        before = state.load_state(fx.audit)
        rev = state.write_state(fx.audit, before)
        self.assertEqual(rev, before.state_revision + 1)
        self.assertFalse((fx.audit / TMP_DECOY).exists())
        self.assertEqual(state.load_state(fx.audit).state_revision, rev)


if __name__ == "__main__":
    unittest.main()
