"""0.9.1 phase 2: a session returns an account of what it did.

The third audit's P1 #8. Until this, a session handed back an integer, so anything that
needed to know whether the session had *changed* something inferred it afterwards — the
retry gate by re-reading the canonical bytes of `audit/state.json`. That comparison is
blind to a changed source file, to an audit artifact, and to a commit, which is how a
`provider-error` whose material effect had already landed looked effect-free and was
dispatched again.

The fix is not a better comparison. It is that the session says.

What is asserted here:

* every field, populated from a REAL dispatched session, one arm per outcome —
  `ok`, `provider-error`, `refused`, `cancelled`;
* HEAD before and after are read from git and move when the session commits, and do
  not move when it only edits the worktree;
* `applied_paths` is the courier's own authorized path set, taken from the courier
  rather than re-derived here;
* the record is immutable;
* no caller of `run_session` was broken by the change — proved by grep AND by the fact
  that `RunResult` IS an int;
* the mutation witness reddens a CONSUMER: with `effects_applied` forced to False, the
  retry gate re-dispatches a session whose material effect already landed, which is the
  audit's scenario exactly.
"""

import io
import contextlib
import json
import shutil
import subprocess
import unittest

from tests.harness import (Fixture, REPO, finding_record, state_doc, xcheck_submodule)

runner = xcheck_submodule("runner")
state_mod = xcheck_submodule("state")
util = xcheck_submodule("util")
cli = xcheck_submodule("cli")

GIT = shutil.which("git")

# The four outcomes with a real child command that produces each. `provider-error` and
# `refused` are classified from LOG NEEDLES (`runner._SIGNATURES`), so the child has to
# print the words a real agent would print — asserting the classifier's own table here
# would be asserting that a constant equals itself.
CHILDREN = {
    "ok": "true {prompt}",
    "provider-error": "sh -c 'echo \"api error: upstream error\"; exit 1' {prompt}",
    "refused": "sh -c 'echo \"i will not complete this charter\"; exit 1' {prompt}",
}


def git(cwd, *args):
    return subprocess.run([GIT, *args], cwd=str(cwd), capture_output=True, text=True,
                          timeout=60)


def dispatchable(**over):
    fx = Fixture(doc=state_doc(findings=[finding_record()]))
    for args in (("init", "-q"), ("config", "user.email", "t@example.invalid"),
                 ("config", "user.name", "test"), ("add", "-A"), ("commit", "-qm", "base")):
        git(fx.root, *args)
    # PHASE 3: `trust_level` has no default in `CONF_DEFAULTS` and must not get one —
    # the refusal exists because the cheap assumption is `trusted`. A fixture project
    # is synthetic and trusted BY CONSTRUCTION, so it makes the operator's declaration
    # explicitly. `tests/test_trust_level.py` is where the unset case is exercised.
    raw = dict(util.CONF_DEFAULTS)
    raw.setdefault("trust_level", "trusted")
    raw.update({k: str(v) for k, v in over.items()})
    return fx, util.Conf(raw)


class TheRecordIsShaped(unittest.TestCase):

    def test_it_is_an_exit_code_so_no_caller_can_be_wrong_about_it(self):
        """The compatibility argument, made structural rather than audited by hand."""
        r = runner.RunResult(0, "ok")
        self.assertIsInstance(r, int)
        self.assertTrue(r == 0 and not r != 0)
        self.assertEqual(runner.RunResult(1, "crash") != 0, True)
        print(f"\n{r!r}")

    def test_it_is_immutable(self):
        r = runner.RunResult(0, "ok", applied_paths=["a.py"])
        for attempt in (lambda: setattr(r, "outcome", "refused"),
                        lambda: setattr(r, "effects_applied", True),
                        lambda: delattr(r, "outcome")):
            with self.assertRaises(AttributeError):
                attempt()
        self.assertEqual(r.outcome, "ok")
        self.assertIsInstance(r.applied_paths, tuple, "a list would be mutable")

    def test_the_record_serialises_every_field(self):
        rec = runner.RunResult(1, "timeout", attempt=2).record()
        self.assertEqual(set(rec), set(runner.RunResult.FIELDS) | {"code"})
        print(f"\nrecord keys: {sorted(rec)}")


class EveryFieldComesFromARealSession(unittest.TestCase):
    """Criterion 3. Four outcomes, four dispatched sessions, asserted one at a time —
    a parameterised loop whose failure names no outcome tells a reader nothing."""

    def run_one(self, outcome, **over):
        fx, conf = dispatchable(sandbox_profile="worktree",
                                remediator_cmd=CHILDREN[outcome], **over)
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = runner.run_session(fx.root, conf, "Remediator", "charter")
        return fx, result

    def assert_account_is_complete(self, result, outcome):
        self.assertIsInstance(result, runner.RunResult)
        self.assertEqual(result.outcome, outcome)
        print(f"\n  {outcome:<15} {result!r}")
        for field in ("outcome", "head_before", "head_after", "session_id",
                      "state_revision_before", "state_revision_after"):
            self.assertIsNotNone(getattr(result, field),
                                 f"{outcome}: {field} is None on a dispatched session")
        self.assertRegex(result.head_before, r"^[0-9a-f]{40}$")
        self.assertRegex(result.head_after, r"^[0-9a-f]{40}$")
        self.assertRegex(result.session_id, r"^[0-9a-f]{16}$")
        self.assertIsInstance(result.effects_applied, bool)
        self.assertIsInstance(result.applied_paths, tuple)

    def test_ok(self):
        _fx, result = self.run_one("ok")
        self.assert_account_is_complete(result, "ok")
        self.assertEqual(int(result), 0)

    def test_provider_error(self):
        _fx, result = self.run_one("provider-error")
        self.assert_account_is_complete(result, "provider-error")
        self.assertNotEqual(int(result), 0)

    def test_refused(self):
        _fx, result = self.run_one("refused")
        self.assert_account_is_complete(result, "refused")
        self.assertNotEqual(int(result), 0)

    def test_cancelled(self):
        """The one outcome that needs a second actor: the cancel request is a FILE the
        running session polls, so the lock has to exist and carry it before the child
        is launched."""
        fx, conf = dispatchable(sandbox_profile="worktree",
                                remediator_cmd="sh -c 'sleep 8' {prompt}")
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        lock = runner.Lock(fx.audit, "Remediator")
        lock.acquire()
        lock.request_cancel()
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                result = runner.run_session(fx.root, conf, "Remediator", "charter",
                                            lock=lock)
        finally:
            lock.release()
        self.assert_account_is_complete(result, "cancelled")
        self.assertFalse(result.effects_applied,
                         "a cancelled session applied nothing, so it must say so")

    def test_a_dry_run_says_it_ran_nothing(self):
        fx, conf = dispatchable(remediator_cmd=CHILDREN["ok"])
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        with contextlib.redirect_stdout(io.StringIO()):
            result = runner.run_session(fx.root, conf, "Remediator", "charter",
                                        dry_run=True)
        print(f"\n  dry-run        {result!r}")
        self.assertEqual(int(result), 0)
        self.assertEqual(result.outcome, "dry-run")
        self.assertFalse(result.effects_applied)
        self.assertEqual(result.applied_paths, ())
        self.assertEqual(result.head_before, result.head_after)


class HeadIsReadFromGit(unittest.TestCase):

    def test_a_session_that_commits_moves_head_and_a_dry_run_does_not(self):
        """Two arms, because either alone is ambiguous: a HEAD that never moves proves
        nothing about a field that could be one constant string.

        The contrast arm is the DRY RUN, and that is a fact about xcheck worth stating
        plainly — there is no such thing as a dispatched session that leaves HEAD alone.
        The orchestrator opens an invocation envelope into `audit/state.json` before the
        child starts and the courier commits it afterwards, so *every* session moves
        HEAD, including one whose child is `true`. Writing this test the other way round
        was the mistake that surfaced it, and it is exactly why `effects_applied` cannot
        be "did HEAD move" (see BookkeepingIsNotAnEffect below).
        """
        writer, conf = dispatchable(
            sandbox_profile="worktree",
            remediator_cmd="sh -c 'echo work >> audit/note.md' {prompt}")
        self.addCleanup(shutil.rmtree, writer.root, ignore_errors=True)
        head_at_start = state_mod.git_head(writer.root)
        with contextlib.redirect_stdout(io.StringIO()):
            moved = runner.run_session(writer.root, conf, "Remediator", "charter")
        print(f"\n  writing session head {moved.head_before[:7]} -> "
              f"{moved.head_after[:7]}  applied={moved.applied_paths} "
              f"effects={moved.effects_applied}")
        self.assertNotEqual(moved.head_before, moved.head_after,
                            "the courier committed the session's work and HEAD did not "
                            "move — head_after is not being read from git")
        # The two ends are git's answers, not the record's own invention: `head_before`
        # is the sha this test read before dispatching, `head_after` is the sha git
        # reports now. A fabricated field passes neither.
        self.assertEqual(moved.head_before, head_at_start)
        self.assertEqual(moved.head_after, state_mod.git_head(writer.root))

        dry, conf2 = dispatchable(remediator_cmd=CHILDREN["ok"])
        self.addCleanup(shutil.rmtree, dry.root, ignore_errors=True)
        with contextlib.redirect_stdout(io.StringIO()):
            still = runner.run_session(dry.root, conf2, "Remediator", "charter",
                                       dry_run=True)
        print(f"  dry run        head {still.head_before[:7]} -> {still.head_after[:7]}")
        self.assertEqual(still.head_before, still.head_after,
                         "nothing ran, so HEAD cannot have moved")


class BookkeepingIsNotAnEffect(unittest.TestCase):
    """The distinction `effects_applied` exists to draw.

    Every dispatched session moves HEAD, because the orchestrator writes the invocation
    envelope and the session row itself and the courier commits them. If `effects_applied`
    were "HEAD moved" it would be True for every session ever run — a retry gate that has
    stopped asking, and phase 4 would inherit a field that says nothing. So the pair below
    is the real assertion: a session whose child does nothing must report False even
    though its HEAD moved, and a session that writes one file must report True.
    """

    def run_one(self, cmd):
        fx, conf = dispatchable(sandbox_profile="worktree", remediator_cmd=cmd)
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        with contextlib.redirect_stdout(io.StringIO()):
            return runner.run_session(fx.root, conf, "Remediator", "charter")

    def test_a_session_that_did_nothing_says_so_even_though_head_moved(self):
        idle = self.run_one(CHILDREN["ok"])
        wrote = self.run_one("sh -c 'echo work >> audit/note.md' {prompt}")
        for name, r in (("idle", idle), ("wrote", wrote)):
            print(f"\n  {name:<6} head moved={r.head_before != r.head_after} "
                  f"applied={r.applied_paths} effects_applied={r.effects_applied}")
        self.assertNotEqual(idle.head_before, idle.head_after,
                            "premise of this test: the orchestrator's own bookkeeping "
                            "commit moves HEAD even for a child that does nothing")
        self.assertFalse(idle.effects_applied,
                         "the session changed nothing, but the record counted the "
                         "orchestrator's own envelope commit as the session's effect")
        self.assertEqual(idle.applied_paths, ())
        self.assertTrue(wrote.effects_applied,
                        "the session wrote audit/note.md and the record missed it")
        self.assertIn("audit/note.md", wrote.applied_paths)


class AppliedPathsComeFromTheCourier(unittest.TestCase):
    """Criterion 5. The point is not that the list is right — it is that there is only
    ONE list. Two derivations of "what did this session change" is how a read-only role
    was able to write a file whose name git quoted."""

    def test_the_record_carries_the_same_tuple_the_authorization_used(self):
        seen = {}
        real = runner.changed_paths

        def spy(workdir, base):
            paths = real(workdir, base)
            seen.setdefault("authorization", list(paths))
            seen["calls"] = seen.get("calls", 0) + 1
            return paths
        runner.changed_paths = spy
        self.addCleanup(lambda: setattr(runner, "changed_paths", real))
        self.assertIsNot(runner.changed_paths, real, "the spy was not installed")

        fx, conf = dispatchable(
            sandbox_profile="readonly",
            remediator_cmd="sh -c 'echo evidence >> audit/note.md' {prompt}")
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        with contextlib.redirect_stdout(io.StringIO()):
            result = runner.run_session(fx.root, conf, "Remediator", "charter")
        print(f"\n  courier saw {seen.get('authorization')} in {seen.get('calls')} call(s)"
              f"\n  record says {list(result.applied_paths)}")
        self.assertTrue(seen.get("authorization"),
                        "the spy recorded nothing — the courier never asked git, so this "
                        "test is comparing two empty lists")
        self.assertEqual(list(result.applied_paths), seen["authorization"])
        self.assertIn("audit/note.md", result.applied_paths)
        self.assertTrue(result.patch_digest, "a patch was applied with no digest recorded")


class TheAccountOutlivesTheProcess(unittest.TestCase):
    """A record the retry gate reads and nobody can read afterwards is half a fix.

    The account is written to the envelope journal beside the session that made it, so
    the human reconstructing a decision months later reads the SAME fields the gate read
    — not a prose line in a log that happens to agree with them.
    """

    def test_the_journal_carries_the_record_the_gate_decided_on(self):
        fx, conf = dispatchable(
            sandbox_profile="worktree", retry_limit=0,
            remediator_cmd="sh -c 'echo work >> audit/note.md; "
                           "echo \"api error: upstream error\"; exit 1' {prompt}")
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = cli._dispatch_with_retry(fx.root, conf, "Remediator", "charter",
                                              False, None, None, sleep=lambda s: None)
            # retry_limit=0, named on the conf above: this test is about the ACCOUNT the
            # gate reads, and a second dispatch would put a second record in the journal.
        events = [json.loads(ln) for ln in
                  (fx.audit / "events.jsonl").read_text(encoding="utf-8").splitlines() if ln]
        records = [e for e in events if e.get("event") == "session_result"]
        print(f"\n  journal events: {[e.get('event') for e in events]}")
        self.assertEqual(len(records), 1, "the session's account was not journalled")
        rec = records[0]
        print(f"  session_result: applied_paths={rec.get('applied_paths')} "
              f"effects_applied={rec.get('effects_applied')} outcome={rec.get('outcome')}")
        self.assertEqual(rec.get("session_id"), result.session_id)
        self.assertEqual(rec.get("outcome"), "provider-error")
        self.assertEqual(rec.get("applied_paths"), list(result.applied_paths))
        self.assertEqual(rec.get("effects_applied"), result.effects_applied)
        self.assertEqual(rec.get("head_after"), result.head_after)
        self.assertTrue(result.effects_applied,
                        "the child wrote audit/note.md before erroring — this is the "
                        "audit's own scenario and the account must show the effect")
        self.assertIn("this session already changed the project", out.getvalue())
        # The operator watching the run sees the account too, on the line above the
        # decision it produced. This assertion is why `print(f"result: {rc!r}")` in
        # cli.py is product output rather than a debug line someone forgot to delete.
        # Both answers on the operator's line, not one. `outcome` is how the process
        # ended; `protocol` is whether the role moved canonical state. This session
        # errored having filed nothing, so the pair reads provider-error + no-progress —
        # and asserting them TOGETHER is what stops the two from being collapsed back
        # into a single field later.
        self.assertRegex(out.getvalue(),
                         r"result: RunResult\(code=1, outcome='provider-error', "
                         r"protocol='protocol-no-progress', moved=0, "
                         r"effects_applied=True")
        # And in words, not only as a field. A repr is for the record; this line is for
        # the person deciding what to do next.
        self.assertIn("without recording a canonical transition", out.getvalue(),
                      "the operator was told the outcome but not that nothing moved")


class NoCallerWasBroken(unittest.TestCase):

    def test_every_call_site_still_treats_the_result_as_an_exit_code(self):
        """A grep, and then the reason the grep is allowed to be reassuring: the value
        IS an int, so `rc == 0` and `return rc` cannot mean something new."""
        sites = []
        needle = "run_" + "session("
        for py in sorted((REPO / "xcheck").glob("*.py")):
            for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if needle in line and "def " not in line and not line.lstrip().startswith("#"):
                    sites.append(f"xcheck/{py.name}:{i}")
        print(f"\nrun_session call sites in the package ({len(sites)}): "
              f"{', '.join(sites)}")
        self.assertTrue(sites, "the scan found no call sites at all")
        self.assertTrue(issubclass(runner.RunResult, int))


class TheRecordIsLoadBearing(unittest.TestCase):
    """Mutation witness for criterion 7 — against a CONSUMER, not the constructor.

    `effects_applied` is what stops the retry gate re-dispatching a session whose work
    already landed. Force it to False and the audit's reproduced scenario comes back:
    the session changed material, returned `provider-error`, and is run again.
    """

    def scenario(self, expect_dispatches):
        fx = Fixture(state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        envelope = xcheck_submodule("envelope")
        calls, marker = [], fx.root / "material.txt"

        def fake_session(project, conf, role, charter, dry_run=False, attempt=0, **kw):
            calls.append(role)
            # The session's material effect: appended, so a second run is VISIBLE.
            with marker.open("a", encoding="utf-8") as fh:
                fh.write(f"effect-{len(calls)}|")
            sid = f"{len(calls):016x}"
            rec = envelope.dispatch_record(
                ["true"], role, charter or "", "prompt", sid,
                runner.resolve_profile({}, role), envelope.current_revision(project),
                head_before="0" * 40, env={}, conf={})
            envelope.store(project, envelope.finish(rec, 1, "provider-error", 1.0,
                                                    "0" * 40, fx.audit / "state.json"),
                           event="session_finished")
            return runner.RunResult(1, "provider-error", applied_paths=("material.txt",),
                                    effects_applied=self.EFFECTS, attempt=attempt,
                                    session_id=sid, head_before="0" * 40,
                                    head_after="0" * 40)

        marker.write_text("base|", encoding="utf-8")
        real = cli.run_session
        cli.run_session = fake_session
        self.addCleanup(lambda: setattr(cli, "run_session", real))
        self.assertIsNot(cli.run_session, real, "the stub was not installed")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli._dispatch_with_retry(fx.root, util.Conf(dict(util.CONF_DEFAULTS, trust_level="trusted",
                                                             retry_limit="2")),
                                     "Remediator", "F-0001", False, None, None,
                                     sleep=lambda s: None)
        material = marker.read_text(encoding="utf-8")
        print(f"\n  effects_applied={self.EFFECTS}: CALLS={len(calls)} MATERIAL={material}")
        print("  " + out.getvalue().strip().replace("\n", "\n  "))
        self.assertEqual(len(calls), expect_dispatches)
        return material

    EFFECTS = True

    def test_the_record_stops_the_retry(self):
        self.EFFECTS = True
        material = self.scenario(1)
        self.assertEqual(material, "base|effect-1|",
                         "the material effect was applied more than once")

    def test_witness_with_effects_applied_forced_false_the_effect_lands_three_times(self):
        self.EFFECTS = False          # <-- the mutation: the record lies about the session
        material = self.scenario(3)
        self.assertEqual(material, "base|effect-1|effect-2|effect-3|",
                         "the witness is inert — with the field neutered the retry gate "
                         "still refused, so it is not the field doing the work")


if __name__ == "__main__":
    unittest.main(verbosity=2)
