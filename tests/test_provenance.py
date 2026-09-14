"""What was audited, as durable facts. F: `state.json.head_before` names `130646f4…` —
a commit in no branch, no tag and no other clone, listed by `git fsck --unreachable`,
one `git gc` from gone. It got there honestly: the write verb that recorded it ran
INSIDE a disposable worktree, where `git rev-parse HEAD` answers the sandbox's own seed
commit.

One field was answering four questions — what was audited, what the sandbox ran, what
content it was, and what version of the tool did the auditing — so it answered all of
them badly. Four fields answer them separately now, and `policy_digest` (phase 4) is the
fifth fact about a dispatch.
"""

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.harness import Fixture, finding_record, state_doc, xcheck_submodule

envelope = xcheck_submodule("envelope")
runner = xcheck_submodule("runner")
state = xcheck_submodule("state")
util = xcheck_submodule("util")
write = xcheck_submodule("write")

GIT = shutil.which("git") or "git"
FIVE = ("subject_commit", "subject_manifest", "sandbox_seed", "controller_commit",
        "policy_digest")
# The commit this repository's own state document names. Kept as a literal: the point of
# the test is that THIS value fails the reachability question.
UNREACHABLE = "130646f43c48429cd7dadb2999ac25e9c6b87769"


def git(cwd, *args):
    return subprocess.run([GIT] + list(args), cwd=str(cwd), capture_output=True,
                          text=True, timeout=60)


def repo(doc=None):
    fx = Fixture(doc=doc or state_doc(findings=[finding_record()]))
    git(fx.root, "init", "-q")
    git(fx.root, "config", "user.email", "t@example.invalid")
    git(fx.root, "config", "user.name", "test")
    git(fx.root, "add", "-A")
    git(fx.root, "commit", "-qm", "base")
    return fx


class TheSchemaWasAskedFirst(unittest.TestCase):
    """The accept/refuse probe, kept as a test so the answer stays true. A field the
    closed schema refuses cannot be written by any wiring, and finding that out from a
    failing session is finding it out late."""

    def record(self, **extra):
        rec = {"session_id": "a" * 16, "role": "Auditor", "provider": "p",
               "agent_model": "m", "executable": "x", "executable_version": "v",
               "charter_hash": "0" * 64, "prompt_hash": "0" * 64, "state_revision": 1,
               "head_before": "unknown", "sandbox_profile": "readonly",
               "started": "2026-09-03"}
        rec.update(extra)
        return rec

    def load(self, rec):
        fx = Fixture(state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, fx.root, True)
        doc = json.loads((fx.audit / "state.json").read_text(encoding="utf-8"))
        doc["sessions"] = [rec]
        (fx.audit / "state.json").write_text(json.dumps(doc), encoding="utf-8")
        return state.load_state(fx.audit)

    def test_all_five_clear_the_closed_schema(self):
        loaded = self.load(self.record(
            subject_commit="b" * 40, subject_manifest="c" * 64, sandbox_seed="d" * 40,
            controller_commit="e" * 40, policy_digest="f" * 64))
        rec = loaded.sessions[0]
        print("\n  SCHEMA PROBE")
        for f in FIVE:
            print(f"    {f:<18} ACCEPTED")
        self.assertEqual("b" * 40, rec["subject_commit"])

    def test_each_is_validated_not_merely_tolerated(self):
        """A free-text field would accept `HEAD~3`, a branch name, or a sentence. The
        four new ones are git object names or sha-256 digests, and nothing else."""
        for field, bad in (("subject_commit", "HEAD~3"),
                           ("subject_manifest", "not-a-digest"),
                           ("sandbox_seed", "the sandbox one"),
                           ("controller_commit", "v0.9.1")):
            with self.subTest(field=field), self.assertRaises(state.StateError) as e:
                self.load(self.record(**{field: bad}))
            self.assertIn(field, str(e.exception))

    def test_a_record_written_before_this_phase_still_loads(self):
        """Backward compatibility is closed in CODE. The alternative — going back and
        adding the fields to records that were already admitted — is rewriting evidence,
        and that lesson has been paid for."""
        loaded = self.load(self.record())
        rec = loaded.sessions[0]
        for f in FIVE:
            self.assertIsNone(rec.get(f), f"{f} was invented for an old record")
        print("  OLD RECORD   loads with all five absent, none invented")


class TheSubjectCommitIsReachable(unittest.TestCase):

    def setUp(self):
        self.fx = repo()
        self.addCleanup(shutil.rmtree, self.fx.root, True)

    def test_a_recorded_subject_commit_is_reachable_from_a_ref(self):
        sha = state.subject_commit(self.fx.root, env={})
        print(f"\n  REACHABLE    {sha[:12]} -> "
              f"{state.commit_reachable(self.fx.root, sha)}")
        self.assertTrue(state.commit_reachable(self.fx.root, sha))

    def test_the_same_assertion_on_the_commit_this_audit_recorded(self):
        """The control that gives the assertion above its meaning. A disposable commit
        exists, `git cat-file` confirms it, and no ref reaches it — so a provenance
        field pointing at one is a dead pointer that still looks like evidence."""
        wt = Path(self.fx.root).parent / "wt-unreachable"
        git(self.fx.root, "worktree", "add", "-q", "--detach", str(wt))
        self.addCleanup(shutil.rmtree, wt, True)
        self.addCleanup(git, self.fx.root, "worktree", "prune")
        git(wt, "commit", "-q", "--allow-empty", "--no-verify", "-m", "xcheck sandbox seed")
        seed = git(wt, "rev-parse", "HEAD").stdout.strip()
        git(self.fx.root, "worktree", "remove", "--force", str(wt))
        self.assertEqual("commit", git(self.fx.root, "cat-file", "-t", seed).stdout.strip(),
                         "the seed commit does not exist, so the test proves nothing")
        print(f"  UNREACHABLE  {seed[:12]} (a real sandbox seed) -> "
              f"{state.commit_reachable(self.fx.root, seed)}")
        self.assertFalse(state.commit_reachable(self.fx.root, seed))
        self.assertFalse(state.commit_reachable(self.fx.root, UNREACHABLE),
                         f"{UNREACHABLE[:7]} — the value this audit recorded — passed")


class TheManifestOutlivesEveryCommit(unittest.TestCase):

    def setUp(self):
        self.fx = repo()
        self.addCleanup(shutil.rmtree, self.fx.root, True)

    def test_it_is_a_content_digest_not_an_object_name(self):
        m = state.subject_manifest(self.fx.root)
        self.assertRegex(m, r"^[0-9a-f]{64}$")
        self.assertNotEqual(state.git_head(self.fx.root), m)

    def test_the_same_content_gives_the_same_manifest_across_commits(self):
        """A commit is a pointer with a parent and a timestamp; the manifest is the
        content. Rewriting history — the ordinary thing that makes a recorded commit
        unreachable — does not change what was audited."""
        before = state.subject_manifest(self.fx.root)
        git(self.fx.root, "commit", "-q", "--allow-empty", "--amend", "-m", "rewritten")
        self.assertNotEqual("base", git(self.fx.root, "log", "-1", "--format=%s").stdout.strip())
        after = state.subject_manifest(self.fx.root)
        print(f"\n  MANIFEST     {before[:12]} -> history rewritten -> {after[:12]}")
        self.assertEqual(before, after)

    def test_an_uncommitted_edit_moves_it(self):
        """The index alone would call a dirty tree identical to a clean one, and a
        session audits the tree as it stands — pending edits included."""
        before = state.subject_manifest(self.fx.root)
        (self.fx.audit / "XCHECK.md").write_text("edited\n", encoding="utf-8")
        after = state.subject_manifest(self.fx.root)
        print(f"  MANIFEST     dirty tree -> {after[:12]} (moved: {before != after})")
        self.assertNotEqual(before, after)


class TheWriteVerbRecordsTheOuterCommit(unittest.TestCase):
    """The real worktree path, with both arms. Without the control this test would pass
    on a build that never entered a worktree at all."""

    def setUp(self):
        self.fx = repo()
        self.addCleanup(shutil.rmtree, self.fx.root, True)
        self.outer = state.git_head(self.fx.root)
        self.wt = Path(self.fx.root).parent / "wt-session"
        git(self.fx.root, "worktree", "add", "-q", "--detach", str(self.wt))
        self.addCleanup(git, self.fx.root, "worktree", "prune")
        self.addCleanup(shutil.rmtree, self.wt, True)
        # what Sandbox._seed does: commit the project's pending work inside the worktree
        git(self.wt, "commit", "-q", "--allow-empty", "--no-verify", "-m",
            "xcheck sandbox seed (Auditor)")
        self.seed = git(self.wt, "rev-parse", "HEAD").stdout.strip()
        self.assertNotEqual(self.outer, self.seed)

    def recorded_head(self):
        return json.loads((self.wt / "audit" / "state.json").read_text(
            encoding="utf-8"))["head_before"]

    def file_a_finding(self):
        (self.wt / "body.md").write_text("why\n", encoding="utf-8")
        # PHASE 9: the locator is resolved against the SUBJECT commit, which is the
        # whole point of this test class — inside the worktree the ambient HEAD is the
        # disposable seed, and `audit/XCHECK.md` is present in both, so a passing
        # locator here does not by itself say which commit answered.
        import hashlib
        import subprocess
        blob = subprocess.run(["git", "cat-file", "blob", "HEAD:audit/XCHECK.md"],
                              cwd=self.wt, capture_output=True).stdout
        write.run(self.wt, "file-finding", [
            "--id", "F-0002", "--title", "t", "--severity", "minor", "--dimension", "invariants",
            "--unit", "U01", "--pass", "P-01", "--body", str(self.wt / "body.md"),
            "--locator", "audit/XCHECK.md:1",
            "--source-hash", hashlib.sha256(blob).hexdigest()])

    def test_the_ambient_head_inside_a_worktree_is_the_sandbox_seed(self):
        """The CONTROL, and the defect exactly as it happened: with nothing exported,
        the ambient answer is the disposable commit — reachable from nothing."""
        env = dict(os.environ)
        env.pop(state.SUBJECT_COMMIT_ENV, None)
        self.assertEqual(self.seed, state.subject_commit(self.wt, env=env))
        self.assertFalse(state.commit_reachable(self.fx.root, self.seed))

    def test_the_exported_outer_commit_is_what_gets_written(self):
        old = os.environ.get(state.SUBJECT_COMMIT_ENV)
        os.environ[state.SUBJECT_COMMIT_ENV] = self.outer
        try:
            self.file_a_finding()
        finally:
            if old is None:
                os.environ.pop(state.SUBJECT_COMMIT_ENV, None)
            else:
                os.environ[state.SUBJECT_COMMIT_ENV] = old
        got = self.recorded_head()
        print(f"\n  WORKTREE     outer {self.outer[:12]} | seed {self.seed[:12]} "
              f"| recorded {got[:12]}")
        self.assertEqual(self.outer, got, "the write recorded the sandbox's own commit")
        self.assertTrue(state.commit_reachable(self.fx.root, got))

    def test_the_orchestrator_exports_it(self):
        """The wiring the test above assumes. Without this the child has nothing to
        read and falls back to the seed — silently, and correctly-looking."""
        env = runner.child_environment(util.Conf(dict(util.CONF_DEFAULTS)),
                                       {state.SUBJECT_COMMIT_ENV: self.outer})
        self.assertEqual(self.outer, env[state.SUBJECT_COMMIT_ENV])


class TheFiveFactsOnARealDispatch(unittest.TestCase):

    def setUp(self):
        self.fx = repo()
        self.addCleanup(shutil.rmtree, self.fx.root, True)

    def test_a_dispatch_record_carries_all_five_separately(self):
        profile = runner.resolve_profile(util.Conf(dict(util.CONF_DEFAULTS)), "Auditor")
        rec = envelope.dispatch_record(
            ["sh", "-c", "true"], "Auditor", "charter", "prompt", "a" * 16, profile, 1,
            head_before=state.git_head(self.fx.root),
            subject_commit=state.subject_commit(self.fx.root, env={}),
            subject_manifest=state.subject_manifest(self.fx.root),
            controller_commit=state.controller_commit())
        log = Path(self.fx.root) / "session.log"
        log.write_text("done\n", encoding="utf-8")
        rec = envelope.finish(rec, 0, "ok", 1.0, state.git_head(self.fx.root), log,
                              sandbox_seed="d" * 40)
        print("\n  FIVE FACTS ON ONE DISPATCH")
        for f in FIVE:
            print(f"    {f:<18} {rec.get(f)}")
        for f in FIVE:
            self.assertIsNotNone(rec.get(f), f"{f} missing from the dispatch record")
        self.assertNotEqual(rec["subject_commit"], rec["sandbox_seed"])
        self.assertNotEqual(rec["subject_commit"], rec["subject_manifest"])

    def test_none_of_them_is_guessed_from_another(self):
        """`unknown` is a recorded answer. A field that quietly copies its neighbour
        when it cannot answer is worse than one that says it does not know."""
        profile = runner.resolve_profile(util.Conf(dict(util.CONF_DEFAULTS)), "Auditor")
        rec = envelope.dispatch_record(["sh"], "Auditor", "c", "p", "a" * 16, profile, 1,
                                       head_before="b" * 40)
        for f in ("subject_commit", "subject_manifest", "controller_commit"):
            self.assertEqual(envelope.UNKNOWN, rec[f], f"{f} was derived, not recorded")
        self.assertNotIn("sandbox_seed", rec, "a seed was invented before the sandbox ran")
        print(f"  NOT GUESSED  head_before={rec['head_before'][:12]} but "
              f"subject_commit={rec['subject_commit']}")


if __name__ == "__main__":
    unittest.main()
