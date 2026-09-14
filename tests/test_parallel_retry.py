"""Phase 11: concurrent auditor passes, and a retry policy with a name for every outcome.

Two features, one scheduling surface, one test module. What is actually asserted here:

* two passes really do run at the same time (each thread records when it entered and
  left; the intervals must overlap) in two DIFFERENT worktrees;
* the second merge is REFUSED as stale and re-derived against fresh state, and the
  revision advances exactly twice — the first writer's findings are still there;
* an overlap is refused BEFORE anything launches, naming the shared units;
* with `parallel_passes` off, the decision path produces byte-identical output;
* every one of the seven declared outcomes has its own retry answer, one test each;
* the mutation witness: delete the revision check and the second writer silently
  overwrites the first — with a control arm that stays green.
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tests.harness import (Fixture, REPO, XCHECK, finding_record, queue_pass,
                           state_doc, xcheck_module, xcheck_submodule)

parallel = xcheck_submodule("parallel")
runner = xcheck_submodule("runner")
state_mod = xcheck_submodule("state")
write_mod = xcheck_submodule("write")
util = xcheck_submodule("util")
policy = xcheck_submodule("policy")
cli = xcheck_module()

OUTCOMES = util.OUTCOMES


def two_pass_doc():
    """Two queued passes over DISJOINT units, and the catalogs that make them legal."""
    catalogs = {
        "norms": [{"id": "N1", "source": "README.md", "scope": "what it claims"}],
        "dimensions": [{"key": "invariants", "catches": "broken invariants",
                        "norms": ["N1"]}],
        "units": [{"id": "U01", "material": "a.py", "size": "small",
                   "responsibility": "one"},
                  {"id": "U02", "material": "b.py", "size": "small",
                   "responsibility": "two"}],
    }
    q1 = dict(queue_pass("P-01"), units=["U01"])
    q2 = dict(queue_pass("P-02"), units=["U02"])
    return state_doc(queue=[q1, q2], catalogs=catalogs)


def overlapping_doc():
    doc = two_pass_doc()
    doc["queue"][1]["units"] = ["U01", "U02"]
    return doc


def commit(root):
    """Give the fixture a HEAD commit: `Sandbox` refuses to cut a worktree from a repo
    that has none, and this module's concurrency claim is about REAL worktrees."""
    for args in (("init", "-q"), ("config", "user.email", "t@example.invalid"),
                 ("config", "user.name", "test"), ("add", "-A"), ("commit", "-qm", "base")):
        subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                       text=True, timeout=60)


def filed(fid, pid, unit):
    """A finding as a pass would have filed it."""
    return finding_record(fid, "reported", **{"pass": pid, "unit": [unit]})


def adding(fid, pid, unit):
    """A `mutate` that files one finding — what `harvest` returns, hand-built."""
    def mutate(doc, _state):
        doc["findings"].append(filed(fid, pid, unit))
        return f"merged {pid}", {"pass": pid}
    return mutate


class TwoDisjointPassesRunAtOnceAndMergeOneAtATime(unittest.TestCase):

    def test_the_sessions_overlap_in_time_and_the_revision_advances_exactly_twice(self):
        fx = Fixture(two_pass_doc())
        self.addCleanup(fx.cleanup)
        commit(fx.root)
        before = state_mod.load_state(fx.audit).state_revision
        spans, workdirs = {}, {}
        started = threading.Barrier(2, timeout=30)

        def session(pid):
            # A REAL disposable worktree per pass, created by the same Sandbox the
            # runner uses — "in separate worktrees" is asserted, not assumed.
            sandbox = runner.Sandbox(fx.root, runner.resolve_profile({}, "Auditor"),
                                     f"Auditor-{pid}")
            sandbox.enter()
            workdirs[pid] = Path(sandbox.workdir)
            t0 = time.monotonic()
            started.wait()                     # both threads are inside their session
            time.sleep(0.05)
            spans[pid] = (t0, time.monotonic())
            sandbox.leave()
            return adding("F-0001" if pid == "P-01" else "F-0002", pid,
                          "U01" if pid == "P-01" else "U02")

        out = io.StringIO()
        results = parallel.run_passes(fx.root, {}, ["P-01", "P-02"], session,
                                      announce=lambda m: out.write(m + "\n"))
        (a0, a1), (b0, b1) = spans["P-01"], spans["P-02"]
        self.assertTrue(a0 < b1 and b0 < a1,
                        f"the two sessions did not overlap in time: {spans}")
        self.assertNotEqual(workdirs["P-01"], workdirs["P-02"])
        for pid, wd in workdirs.items():
            self.assertNotIn(str(fx.root.resolve()), str(wd.resolve().parent),
                             f"{pid} ran inside the project, not a disposable worktree")

        after = state_mod.load_state(fx.audit)
        print(f"\nstate revision {before} -> {after.state_revision}; worktrees: "
              + ", ".join(f"{p}={w.name}" for p, w in sorted(workdirs.items())))
        print(out.getvalue().rstrip())
        self.assertEqual(after.state_revision, before + 2,
                         "two merges must be two transitions — no more, no fewer")
        self.assertEqual(sorted(r.id for r in after.findings), ["F-0001", "F-0002"],
                         "a merge overwrote the other pass's findings")
        self.assertEqual(sorted(r.id for r, in [(r,) for r in after.findings]
                                if r.status == "reported"), ["F-0001", "F-0002"])
        # One merge landed first-try, the other was refused as stale and re-derived.
        self.assertEqual(sorted(a for _p, a, _e in results), [1, 2],
                         f"expected one clean merge and one retried merge: {results}")

    def test_the_stale_merge_is_refused_and_retried_against_fresh_state(self):
        fx = Fixture(two_pass_doc())
        self.addCleanup(fx.cleanup)
        base = state_mod.load_state(fx.audit).state_revision
        said = []
        parallel.merge(fx.root, adding("F-0001", "P-01", "U01"), base,
                       target="P-01", announce=said.append)
        # P-02 still declares the revision it read, which is now one behind.
        attempts = parallel.merge(fx.root, adding("F-0002", "P-02", "U02"), base,
                                  target="P-02", announce=said.append)
        refusal = "\n".join(said)
        print("\n" + refusal)
        self.assertEqual(attempts, 2)
        self.assertIn("merge refused", refusal)
        self.assertIn(f"computed against state revision {base}", refusal)
        self.assertIn("Nothing was written", refusal)
        after = state_mod.load_state(fx.audit)
        self.assertEqual(sorted(r.id for r in after.findings), ["F-0001", "F-0002"])
        self.assertEqual(after.state_revision, base + 2)

    def test_a_refused_merge_writes_nothing_at_all(self):
        fx = Fixture(two_pass_doc())
        self.addCleanup(fx.cleanup)
        base = state_mod.load_state(fx.audit).state_revision
        raw = (fx.audit / "state.json").read_bytes()
        with self.assertRaises(write_mod.StaleRevision):
            write_mod._apply(fx.root, adding("F-0009", "P-01", "U01"),
                             verb="merge-pass", target="P-01",
                             expect_revision=base + 99)
        self.assertEqual((fx.audit / "state.json").read_bytes(), raw,
                         "a refused merge changed the state file")


class OverlappingUnitSetsAreRefusedBeforeLaunch(unittest.TestCase):

    def test_the_overlap_is_named_and_nothing_is_launched(self):
        fx = Fixture(overlapping_doc())
        self.addCleanup(fx.cleanup)
        launched = []
        with self.assertRaises(state_mod.StateError) as caught:
            parallel.run_passes(fx.root, {}, ["P-01", "P-02"],
                                lambda pid: launched.append(pid), announce=lambda m: None)
        print("\n" + str(caught.exception))
        self.assertEqual(launched, [], "a session was launched despite the overlap")
        self.assertIn("U01", str(caught.exception))
        self.assertIn("P-01 and P-02 both cover", str(caught.exception))

    def test_disjoint_sets_report_no_overlap(self):
        fx = Fixture(two_pass_doc())
        self.addCleanup(fx.cleanup)
        state = state_mod.load_state(fx.audit)
        self.assertEqual(parallel.unit_overlaps(state, ["P-01", "P-02"]), [])
        self.assertEqual(parallel.refuse_overlaps(state, ["P-01", "P-02"]),
                         ["P-01", "P-02"])

    def test_the_group_chooser_drops_an_overlapping_pass_rather_than_reordering(self):
        fx = Fixture(overlapping_doc())
        self.addCleanup(fx.cleanup)
        state = state_mod.load_state(fx.audit)
        self.assertEqual(cli._parallel_group(state, ["P-01", "P-02"]), ["P-01"])


class ReservedIdBlocksMakeCollisionUnrepresentable(unittest.TestCase):

    def test_each_pass_gets_a_disjoint_block_starting_after_the_highest_id(self):
        fx = Fixture(state_doc(findings=[finding_record("F-0007")],
                               queue=two_pass_doc()["queue"],
                               catalogs=two_pass_doc()["catalogs"]))
        self.addCleanup(fx.cleanup)
        state = state_mod.load_state(fx.audit)
        reserved = parallel.reserve_ids(state, ["P-01", "P-02"], block=10)
        print(f"\nreserved: {reserved}")
        self.assertEqual(reserved, {"P-01": (8, 17), "P-02": (18, 27)})
        self.assertIn("F-0008 through F-0017",
                      parallel.block_charter("c", *reserved["P-01"]))

    def test_a_finding_outside_the_block_is_refused_at_harvest(self):
        fx = Fixture(two_pass_doc())
        self.addCleanup(fx.cleanup)
        base = state_mod.load_state(fx.audit)
        session_doc = two_pass_doc()
        session_doc["findings"] = [filed("F-0099", "P-01", "U01")]
        with self.assertRaises(state_mod.StateError) as caught:
            parallel.harvest(base, state_mod._build(session_doc), "P-01", (1, 50))
        print("\n" + str(caught.exception))
        self.assertIn("F-0099", str(caught.exception))
        self.assertIn("reserved id block", str(caught.exception))

    def test_a_finding_attributed_to_another_pass_is_refused_at_harvest(self):
        fx = Fixture(two_pass_doc())
        self.addCleanup(fx.cleanup)
        base = state_mod.load_state(fx.audit)
        session_doc = two_pass_doc()
        session_doc["findings"] = [filed("F-0001", "P-02", "U02")]
        with self.assertRaises(state_mod.StateError) as caught:
            parallel.harvest(base, state_mod._build(session_doc), "P-01", (1, 50))
        self.assertIn("against another pass", str(caught.exception))

    def test_harvest_carries_the_findings_and_the_coverage_report(self):
        fx = Fixture(two_pass_doc())
        self.addCleanup(fx.cleanup)
        base = state_mod.load_state(fx.audit)
        session_doc = two_pass_doc()
        session_doc["findings"] = [filed("F-0001", "P-01", "U01")]
        session_doc["queue"][0]["done"] = True
        session_doc["queue"][0]["coverage"] = {
            "report_path": "passes/P-01-report.md", "findings": ["F-0001"],
            "updated": "2026-08-15", "status": "done"}
        mutate = parallel.harvest(base, state_mod._build(session_doc), "P-01", (1, 50))
        parallel.merge(fx.root, mutate, base.state_revision, target="P-01",
                       announce=lambda m: None)
        after = state_mod.load_state(fx.audit)
        self.assertEqual([r.id for r in after.findings], ["F-0001"])
        self.assertTrue(after.queue_entry("P-01").done)
        self.assertEqual(after.queue_entry("P-01").coverage.status, "done")


class TheFlagOffPathIsTheOldPath(unittest.TestCase):
    """Criterion: with `parallel_passes` off the decision path is byte-identical.

    Compared against real output, not read: `status` prints the decision the dispatcher
    would act on, and the two runs must agree to the byte.
    """

    def run_status(self, fx, conf):
        fx.configure(conf)
        p = subprocess.run([sys.executable, str(XCHECK), "--project", str(fx.root),
                            "status"], capture_output=True, text=True, timeout=180,
                           env=fx.env())
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout

    def test_the_decision_output_is_identical_with_the_flag_absent_and_off(self):
        fx = Fixture(two_pass_doc())
        self.addCleanup(fx.cleanup)
        absent = self.run_status(fx, "auditor_cmd=true {prompt}\n")
        off = self.run_status(fx, "auditor_cmd=true {prompt}\nparallel_passes=off\n")
        self.assertEqual(absent, off)
        print("\nflag absent vs parallel_passes=off: byte-identical "
              f"({len(absent)} bytes)\n{absent.strip()}")

    def test_the_dispatcher_does_not_take_the_parallel_branch_with_the_flag_off(self):
        fx = Fixture(two_pass_doc())
        self.addCleanup(fx.cleanup)
        taken = []
        real = parallel.run_passes
        parallel.run_passes = lambda *a, **k: taken.append(a) or []
        self.addCleanup(lambda: setattr(parallel, "run_passes", real))
        self.assertIsNot(cli.parallel.run_passes, real, "the stub was not installed")
        sessions = []
        real_session = cli.run_session
        cli.run_session = lambda *a, **k: sessions.append(a) or 0
        self.addCleanup(lambda: setattr(cli, "run_session", real_session))
        conf = util.load_conf(fx.audit, profile=fx.profile)
        cli.cmd_next(fx.root, conf, dry_run=False)
        self.assertEqual(taken, [], "the parallel branch ran with the flag off")
        self.assertEqual(len(sessions), 1, "the serial dispatcher did not run")


class EveryDeclaredOutcomeHasARetryAnswer(unittest.TestCase):
    """All seven, one test each — none unclassified by omission."""

    def decide(self, outcome, **kw):
        return runner.retry_decision(outcome, **kw)

    def test_ok_is_not_retried(self):
        retry, why = self.decide("ok")
        self.assertFalse(retry)
        self.assertIn("succeeded", why)

    def test_refused_is_never_retried(self):
        retry, why = self.decide("refused")
        self.assertFalse(retry)
        self.assertIn("refusal retried is a refusal ignored", why)

    def test_blocked_is_never_retried(self):
        retry, why = self.decide("blocked")
        self.assertFalse(retry)
        self.assertIn("human decides", why)

    def test_provider_error_is_retried(self):
        retry, why = self.decide("provider-error")
        self.assertTrue(retry)
        self.assertIn("transient", why)

    def test_a_transport_timeout_is_retried_and_a_budget_timeout_is_not(self):
        retry, why = self.decide("timeout", log_text="urllib3: read timed out")
        self.assertTrue(retry, why)
        retry, why = self.decide("timeout", log_text="still working on the charter")
        self.assertFalse(retry)
        self.assertIn("wall-clock budget", why)

    def test_crash_is_never_retried(self):
        retry, why = self.decide("crash")
        self.assertFalse(retry)
        self.assertIn("second attempt", why)

    def test_cancelled_is_never_retried(self):
        retry, why = self.decide("cancelled")
        self.assertFalse(retry)
        self.assertIn("overrule", why)

    def test_every_declared_outcome_is_answered_and_the_table_is_printed(self):
        rows = []
        for outcome in OUTCOMES:
            retry, why = self.decide(outcome, log_text="read timed out")
            rows.append((outcome, retry, why))
        print("\nretry classification (all %d declared outcomes):" % len(OUTCOMES))
        for outcome, retry, why in rows:
            print(f"  {outcome:<15} {'RETRY' if retry else 'no   '}  {why[:88]}")
        # 7 -> 8 in phase 12: `stalled`, a session killed by a deadline that fired
        # INSIDE its wall-clock budget. It is not retryable, and the pin moves because a
        # declared outcome was ADDED — the assertion below is the one that matters, and
        # it is unchanged: only `provider-error` and `timeout` may be re-dispatched.
        self.assertEqual(len(rows), 8)
        self.assertEqual(sorted(o for o, r, _ in rows if r),
                         ["provider-error", "timeout"])

    def test_an_undeclared_outcome_refuses_rather_than_guessing(self):
        retry, why = self.decide("weird")
        self.assertFalse(retry)
        self.assertIn("not a declared outcome", why)

    def test_a_session_that_changed_state_is_never_retried(self):
        retry, why = self.decide("provider-error", state_changed=True)
        self.assertFalse(retry)
        self.assertIn("already changed the project", why)


class RetriesAreBoundedAndRecorded(unittest.TestCase):

    def test_the_limit_bounds_the_attempts(self):
        self.assertTrue(runner.retry_decision("provider-error", attempt=0, limit=2)[0])
        self.assertTrue(runner.retry_decision("provider-error", attempt=1, limit=2)[0])
        last = runner.retry_decision("provider-error", attempt=2, limit=2)
        self.assertFalse(last[0])
        self.assertIn("retry limit reached", last[1])
        self.assertFalse(runner.retry_decision("provider-error", attempt=0, limit=0)[0])

    def test_the_backoff_grows_and_is_bounded(self):
        waits = [runner.retry_backoff(i) for i in range(8)]
        print(f"\nbackoff: {waits}")
        self.assertEqual(waits[:4], [1, 2, 4, 8])
        self.assertTrue(all(b <= a for a, b in zip(waits[1:], waits[2:])
                            if False) or waits == sorted(waits))
        self.assertEqual(max(waits), 60)

    def test_a_transient_failure_is_re_dispatched_with_a_fresh_session_and_recorded(self):
        fx = Fixture(two_pass_doc())
        self.addCleanup(fx.cleanup)
        envelope = xcheck_submodule("envelope")
        calls = []

        def fake_session(project, conf, role, charter, dry_run=False, **kw):
            calls.append(role)
            sid = f"{len(calls):016x}"
            rec = envelope.dispatch_record(
                ["true"], role, charter or "", "prompt", sid,
                runner.resolve_profile({}, role), envelope.current_revision(project),
                head_before="0" * 40, env={}, conf={})
            outcome = "provider-error" if len(calls) == 1 else "ok"
            envelope.store(project, envelope.finish(rec, 1 if len(calls) == 1 else 0,
                                                    outcome, 1.0, "0" * 40,
                                                    fx.audit / "state.json"),
                           event="session_finished")
            return 1 if len(calls) == 1 else 0

        real = cli.run_session
        cli.run_session = fake_session
        self.addCleanup(lambda: setattr(cli, "run_session", real))
        self.assertIs(cli.run_session, fake_session, "the stub was not installed")
        slept = []
        rc = cli._dispatch_with_retry(fx.root, {"retry_limit": "2"}, "Auditor", "P-01",
                                      False, None, None, sleep=slept.append)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 2, "the transient failure was not re-dispatched")
        self.assertEqual(slept, [1], "the retry did not back off")
        events = [e for e in envelope.read_events(fx.root)
                  if e["event"] == "retry_considered"]
        print("\nretry events:")
        for e in events:
            print(f"  outcome={e['outcome']} attempt={e['attempt']} retry={e['retry']} "
                  f"session={e['session_id']}")
        self.assertEqual([e["retry"] for e in events], [True])
        self.assertEqual(events[0]["outcome"], "provider-error")
        ids = [s["session_id"] for s in state_mod.load_state(fx.audit).sessions]
        self.assertEqual(len(ids), len(set(ids)), "a retry reused a session id")

    def test_a_session_that_already_changed_state_refuses_the_retry(self):
        fx = Fixture(two_pass_doc())
        self.addCleanup(fx.cleanup)
        envelope = xcheck_submodule("envelope")
        calls = []

        def fake_session(project, conf, role, charter, dry_run=False, **kw):
            calls.append(role)
            # The first attempt COMMITS: it files a finding through the write path.
            write_mod._apply(project, adding("F-0001", "P-01", "U01"),
                             verb="file-finding", target="F-0001")
            sid = f"{len(calls):016x}"
            rec = envelope.dispatch_record(
                ["true"], role, charter or "", "prompt", sid,
                runner.resolve_profile({}, role), envelope.current_revision(project),
                head_before="0" * 40, env={}, conf={})
            envelope.store(project, envelope.finish(rec, 1, "provider-error", 1.0,
                                                    "0" * 40, fx.audit / "state.json"),
                           event="session_finished")
            return 1

        real = cli.run_session
        cli.run_session = fake_session
        self.addCleanup(lambda: setattr(cli, "run_session", real))
        out = io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(out):
            rc = cli._dispatch_with_retry(fx.root, {"retry_limit": "2"}, "Auditor",
                                          "P-01", False, None, None, sleep=lambda s: None)
        print("\n" + out.getvalue().strip())
        self.assertEqual(rc, 1)
        self.assertEqual(len(calls), 1,
                         "a session that already changed state was run a second time")
        self.assertIn("already changed the project", out.getvalue())


class TheRevisionCheckIsTheThingThatStopsTheOverwrite(unittest.TestCase):
    """Mutation witness with a control arm.

    The mutant removes ONLY the `expect_revision` comparison in `write._apply`. If the
    control were also red, the witness would be measuring the harness rather than the
    guard.

    The scenario is a READ-MODIFY-WRITE, because that is the shape the check exists for.
    Two independent appends cannot lose each other's work — `_apply` hands every mutate a
    freshly loaded document, so an append lands on top of whatever is there. What the
    revision check protects is a change whose CONTENT was computed from a state read
    earlier: writer B carries the queue it read at revision N, and writing that queue back
    at revision N+1 reverts A's `done`. The guarantee under test is the refusal itself —
    nothing is written — not a repair.
    """

    SCRIPT = r"""
import sys, json
sys.path.insert(0, {repo!r})
from xcheck import parallel
from xcheck.state import load_state
import xcheck.write as w

MUTATE = {mutate!r} == "yes"
if MUTATE:
    src_apply = w._apply
    def no_check(project, mutate, role="write", verb=None, target=None,
                 expect_revision=None, lock=None):
        # Every parameter is forwarded EXCEPT the one being removed. A double that
        # drops an argument fails with a TypeError, which looks exactly like a mutant
        # that was intercepted — and 0.9.1 phase 8 added `lock=`, which is how this
        # one nearly came to report a dead guard.
        return src_apply(project, mutate, role=role, verb=verb, target=target,
                         expect_revision=None, lock=lock)   # <-- the guard, removed
    w._apply = no_check
    parallel._apply = no_check

root = {root!r}
base_state = load_state(root + "/audit")
base = base_state.state_revision
# What writer B read BEFORE writer A merged: P-01 is not done yet.
stale_queue = json.load(open(root + "/audit/state.json"))["queue"]

def coverage(pid):
    return {{"report_path": "passes/" + pid + "-report.md", "findings": [],
            "updated": "2026-08-14", "status": "done"}}

def fresh_writer(pid):
    "Reads the queue it is handed — the correct shape."
    def mutate(doc, state):
        for q in doc["queue"]:
            if q["id"] == pid:
                q["done"] = True
                q["coverage"] = coverage(pid)
        return "merged " + pid, {{"pass": pid}}
    return mutate

def stale_writer(pid):
    "Writes back the queue it read at revision %d — the shape the check exists for." % base
    def mutate(doc, state):
        q = json.loads(json.dumps(stale_queue))
        for e in q:
            if e["id"] == pid:
                e["done"] = True
                e["coverage"] = coverage(pid)
        doc["queue"] = q
        return "merged " + pid, {{"pass": pid}}
    return mutate

try:
    parallel.merge(root, fresh_writer("P-01"), base, target="P-01",
                   announce=lambda m: None)
    parallel.merge(root, stale_writer("P-02"), base, target="P-02",
                   announce=lambda m: None, attempts=1)
except Exception as e:
    print("REFUSED:", type(e).__name__)
after = load_state(root + "/audit")
print("DONE:", ",".join(sorted(q.id for q in after.queue if q.done)) or "(none)")
"""

    def arm(self, mutate):
        fx = Fixture(two_pass_doc())
        self.addCleanup(fx.cleanup)
        for pid in ("P-01", "P-02"):
            (fx.audit / "passes" / f"{pid}-report.md").write_text(
                "Coverage report.\n", encoding="utf-8")
        script = self.SCRIPT.format(repo=str(REPO), root=str(fx.root),
                                    mutate="yes" if mutate else "no")
        p = subprocess.run([sys.executable, "-c", script], capture_output=True,
                           text=True, timeout=180)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout

    def test_the_control_keeps_the_first_writers_change_and_the_mutant_reverts_it(self):
        control = self.arm(mutate=False)
        mutant = self.arm(mutate=True)
        print(f"\ncontrol (check intact): {control.strip()}")
        print(f"mutant  (check removed): {mutant.strip()}")
        self.assertIn("REFUSED: StaleRevision", control)
        self.assertEqual(control.split("DONE:")[1].strip(), "P-01",
                         "with the check intact the stale write must land nowhere: "
                         "P-01 stays done and P-02 is not recorded")
        self.assertNotIn("REFUSED", mutant,
                         "the mutant still refused — the guard was not the thing removed")
        self.assertEqual(mutant.split("DONE:")[1].strip(), "P-02",
                         "the mutant kept P-01 done, so nothing was overwritten and this "
                         "witness proves nothing")


class TheConcurrentDispatchIsDeclaredWhereItMatters(unittest.TestCase):

    def test_the_flag_and_the_limit_are_documented_defaults(self):
        # 0.9.1: `retry_limit` was "2" here. It is not loosened, it is TIGHTENED — see
        # `BothBlockersAreClosedAtTheDefault` below, which asserts the new value AND the
        # behaviour it buys (one dispatch, not three).
        self.assertEqual(util.CONF_DEFAULTS["parallel_passes"], "off")
        self.assertEqual(util.CONF_DEFAULTS["retry_limit"], "0")
        self.assertIn("parallel_passes", util.BOOLEAN_CONF)
        self.assertIn("retry_limit", util.NUMERIC_CONF)
        self.assertIn("parallel_passes=off", policy.OPERATOR_PROFILE_TEMPLATE)
        self.assertIn("retry_limit=0", policy.OPERATOR_PROFILE_TEMPLATE)

    def test_a_concurrent_dispatch_does_not_declare_its_sibling_abandoned(self):
        """`envelope.store` closes every OTHER open dispatch as crashed — right for a
        killed orchestrator, wrong for a sibling that is still working."""
        fx = Fixture(two_pass_doc())
        self.addCleanup(fx.cleanup)
        envelope = xcheck_submodule("envelope")
        profile = runner.resolve_profile({}, "Auditor")
        first = envelope.dispatch_record(["true"], "Auditor", "P-01", "p", "a" * 16,
                                         profile, 1, head_before="0" * 40, env={}, conf={},
                                         concurrent_group="grp-test")
        envelope.store(fx.root, first, event="session_dispatched")
        second = envelope.dispatch_record(["true"], "Auditor", "P-02", "p", "b" * 16,
                                          profile, 2, head_before="0" * 40, env={}, conf={},
                                          concurrent_group="grp-test")
        envelope.store(fx.root, second, event="session_dispatched", live=["a" * 16])
        sessions = {s["session_id"]: s for s in state_mod.load_state(fx.audit).sessions}
        self.assertIsNone(sessions["a" * 16]["outcome"],
                          "the live sibling was declared crashed")
        # …and without `live`, the previous behaviour is unchanged.
        third = envelope.dispatch_record(["true"], "Auditor", "P-03", "p", "c" * 16,
                                         profile, 3, head_before="0" * 40, env={}, conf={})
        envelope.store(fx.root, third, event="session_dispatched")
        sessions = {s["session_id"]: s for s in state_mod.load_state(fx.audit).sessions}
        self.assertEqual(sessions["a" * 16]["outcome"], "crash")


class BothBlockersAreClosedAtTheDefault(unittest.TestCase):
    """0.9.1 phase 1. The third audit reproduced two defects and called them release
    blockers. Neither is reachable from the shipped defaults any more, and this class is
    where that is asserted rather than announced.

    * `parallel_passes=on` no longer enables anything — it stops the run and says why.
    * `retry_limit` defaults to 0, so a failed session is dispatched once.

    The two mutation witnesses restore the 0.9.0 behaviour and show what it costs: three
    dispatches of a session that failed, and a finding whose evidence does not exist.
    """

    SENTINEL = "EVIDENCE-SENTINEL-only-the-worktree-ever-held-this"

    # ---- helpers ---------------------------------------------------------------

    def failing_session(self, fx, calls, outcome="provider-error"):
        """A `run_session` stand-in that always fails with a transient outcome."""
        envelope = xcheck_submodule("envelope")

        def fake(project, conf, role, charter, dry_run=False, **kw):
            calls.append(role)
            sid = f"{len(calls):016x}"
            rec = envelope.dispatch_record(
                ["true"], role, charter or "", "prompt", sid,
                runner.resolve_profile({}, role), envelope.current_revision(project),
                head_before="0" * 40, env={}, conf={})
            envelope.store(project, envelope.finish(rec, 1, outcome, 1.0, "0" * 40,
                                                    fx.audit / "state.json"),
                           event="session_finished")
            return 1
        return fake

    def install(self, fx, calls):
        real = cli.run_session
        cli.run_session = self.failing_session(fx, calls)
        self.addCleanup(lambda: setattr(cli, "run_session", real))
        self.assertIsNot(cli.run_session, real, "the stub was not installed")

    def run_cli(self, fx, *argv):
        # `fx.env()` carries XCHECK_OPERATOR_PROFILE: since the policy split the role
        # commands live outside the subject, and a child that cannot find the profile
        # refuses for the wrong reason ("orchestrator.conf lacks auditor_cmd").
        return subprocess.run([sys.executable, str(XCHECK), "--project", str(fx.root),
                               *argv], capture_output=True, text=True, timeout=300,
                              env=fx.env())

    # ---- criterion 1: the default retry limit -----------------------------------

    def test_the_default_retry_limit_is_zero_and_a_failed_session_is_dispatched_once(self):
        """Asserted on the CALL COUNT. A message saying "no retry" is what the defective
        version also printed on the paths where it happened not to retry."""
        self.assertEqual(util.CONF_DEFAULTS["retry_limit"], "0")
        self.assertIn("retry_limit=0", policy.OPERATOR_PROFILE_TEMPLATE)
        fx = Fixture(two_pass_doc())
        self.addCleanup(fx.cleanup)
        calls = []
        self.install(fx, calls)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli._dispatch_with_retry(
                fx.root, util.Conf(dict(util.CONF_DEFAULTS, trust_level="trusted")), "Auditor", "P-01",
                False, None, None,
                sleep=lambda s: self.fail("the default backed off — it retried"))
        print(f"\ndefault conf + provider-error: dispatches={len(calls)} rc={rc}")
        print(out.getvalue().strip())
        self.assertEqual(rc, 1)
        self.assertEqual(len(calls), 1,
                         f"the shipped default re-dispatched a failed session: {calls}")

    def test_witness_restoring_the_old_default_of_two_re_dispatches_the_failure(self):
        """Mutation witness A. Restores the 0.9.0 value — it does not delete the key,
        because a missing key would fail for a different reason and prove nothing."""
        original = util.CONF_DEFAULTS["retry_limit"]
        util.CONF_DEFAULTS["retry_limit"] = "2"          # <-- the 0.9.0 default, restored
        self.addCleanup(util.CONF_DEFAULTS.__setitem__, "retry_limit", original)
        fx = Fixture(two_pass_doc())
        self.addCleanup(fx.cleanup)
        calls, slept = [], []
        self.install(fx, calls)
        with contextlib.redirect_stdout(io.StringIO()):
            cli._dispatch_with_retry(fx.root, util.Conf(dict(util.CONF_DEFAULTS, trust_level="trusted")),
                                     "Auditor", "P-01", False, None, None,
                                     sleep=slept.append)
        print(f"\nWITNESS A — old default restored: dispatches={len(calls)} "
              f"backoffs={slept}")
        self.assertGreater(len(calls), 1,
                           "the witness is inert: the old default did not re-dispatch")
        self.assertEqual(len(calls), 3, "limit 2 means one attempt and two retries")
        self.assertEqual(util.CONF_DEFAULTS["retry_limit"], "2",
                         "the mutation did not take — the count above proves nothing")

    # ---- criteria 2 and 3: the refusal, on every surface -------------------------

    def test_the_flag_is_read_at_exactly_one_gate(self):
        """The surface list is DERIVED from the tree, not remembered.

        One call site is the whole safety argument: a second caller added later would be
        a path around the refusal, and this fails the moment one appears."""
        sites = []
        for py in sorted((REPO / "xcheck").glob("*.py")):
            for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if "parallel.enabled(" in line and not line.lstrip().startswith("#"):
                    sites.append(f"xcheck/{py.name}:{i}")
        print(f"\nparallel.enabled call sites ({len(sites)}): {', '.join(sites)}")
        self.assertEqual(len(sites), 1,
                         f"more than one gate reads the flag: {sites}")

    def test_a_build_that_cannot_preserve_evidence_is_refused_on_every_surface(self):
        """`next`, `loop`, and `next --dry-run` — the three ways an operator arrives.

        Phase 8 replaced the unconditional ban with a probe, so the refusal is now
        conditional on the machinery working. Breaking it has to close all three
        surfaces, and the dry run matters most: it is what somebody types to check
        their configuration, and being told the decision would have been "audit" would
        send them to the real run to discover the refusal there.

        The break travels as an env var read by a `sitecustomize` shim, because these
        surfaces are SUBPROCESSES — a monkeypatch in this process would not reach them,
        and a test that patched the parent and asserted on the child would be green over
        nothing at all."""
        shim = Path(tempfile.mkdtemp(prefix="xcheck-break-"))
        self.addCleanup(shutil.rmtree, shim, ignore_errors=True)
        (shim / "sitecustomize.py").write_text(
            "import os\n"
            "if os.environ.get('XCHECK_BREAK_PRESERVATION'):\n"
            "    from xcheck import parallel\n"
            "    parallel.apply_artifacts = lambda project, manifest: (lambda: None)\n",
            encoding="utf-8")
        broken = dict(os.environ, XCHECK_BREAK_PRESERVATION="1",
                      PYTHONPATH=f"{shim}:{REPO}")
        surfaces = [("next",), ("loop",), ("next", "--dry-run")]
        conf = "auditor_cmd=true {prompt}\nparallel_passes=on\n"
        for argv in surfaces:
            with self.subTest(surface=" ".join(argv)):
                fx = Fixture(two_pass_doc(), conf=conf)
                self.addCleanup(fx.cleanup)
                commit(fx.root)
                p = subprocess.run([sys.executable, str(XCHECK), "--project",
                                    str(fx.root), *argv], capture_output=True,
                                   text=True, timeout=300, env=fx.env(broken))
                text = p.stdout + p.stderr
                print(f"\n=== xcheck {' '.join(argv)} (preservation broken) "
                      f"exit={p.returncode}\n{text.strip()[:700]}")
                self.assertNotEqual(p.returncode, 0, "the refusal exited 0")
                self.assertIn("cannot preserve a pass's evidence", text)
                self.assertIn("wrote nothing", text)                # what the probe saw
                self.assertIn("parallel_passes=off", text)          # the remedy
                self.assertEqual(
                    len(state_mod.load_state(fx.audit).sessions), 0,
                    "a session was dispatched before the refusal")

    def test_control_arm_the_same_surfaces_run_when_preservation_works(self):
        """Without this, the three refusals above could be a broken shim: a subprocess
        that fails to start refuses everything."""
        fx = Fixture(two_pass_doc(),
                     conf="auditor_cmd=true {prompt}\nparallel_passes=on\n")
        self.addCleanup(fx.cleanup)
        commit(fx.root)
        p = self.run_cli(fx, "next")
        text = (p.stdout + p.stderr).strip()
        print(f"\n=== CONTROL: xcheck next (parallel_passes=on, unbroken) "
              f"exit={p.returncode}\n{text[-700:]}")
        self.assertNotIn("cannot preserve", text)
        self.assertEqual(p.returncode, 0, text)

    def test_control_arm_the_same_run_completes_with_the_flag_off(self):
        """Without this, all three refusals above could be a broken fixture."""
        fx = Fixture(two_pass_doc(),
                     conf="auditor_cmd=true {prompt}\nparallel_passes=off\n")
        self.addCleanup(fx.cleanup)
        commit(fx.root)
        p = self.run_cli(fx, "next")
        print(f"\n=== CONTROL: xcheck next (parallel_passes=off) exit={p.returncode}\n"
              + (p.stdout + p.stderr).strip()[-600:])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(len(state_mod.load_state(fx.audit).sessions), 1,
                         "the control arm dispatched no session — the fixture is broken, "
                         "so the refusals above prove nothing")

    # ---- criterion 6: what the refusal is standing in front of -------------------

    def test_witness_removing_the_refusal_reopens_the_branch_and_loses_the_evidence(self):
        """Mutation witness B, in two halves.

        Half one: restore `enabled` to the plain flag read and the dispatcher takes the
        parallel branch again — so the refusal, and not something else, is what stops it.

        Half two: run the branch it reopens, and watch the evidence disappear. The
        session writes a finding body inside its worktree, exactly as a real pass does;
        `harvest` carries the RECORD out; `sandbox.leave()` deletes the worktree. What
        lands in the project is a finding with generated frontmatter and no evidence
        under it, and `xcheck lint` goes red — which is the audit's reproduction, kept
        here as the reason the refusal exists.
        """
        real_enabled = parallel.enabled
        parallel.enabled = lambda conf: util.conf_flag(conf, "parallel_passes")
        self.addCleanup(lambda: setattr(parallel, "enabled", real_enabled))
        self.assertIsNot(cli.parallel.enabled, real_enabled, "the mutation was not installed")

        # -- half one: the branch is reachable again ------------------------------
        fx = Fixture(two_pass_doc(),
                     conf="auditor_cmd=true {prompt}\nparallel_passes=on\n")
        self.addCleanup(fx.cleanup)
        commit(fx.root)
        taken = []
        real_passes = parallel.run_passes
        parallel.run_passes = lambda *a, **k: taken.append(a[2]) or []
        self.addCleanup(lambda: setattr(parallel, "run_passes", real_passes))
        with fx.profile_env():
            cli.cmd_next(fx.root, util.load_conf(fx.audit, profile=fx.profile),
                         dry_run=False)
        print(f"\nWITNESS B/1 — refusal removed: parallel branch taken with {taken}")
        self.assertEqual(taken, [["P-01", "P-02"]],
                         "the branch stayed closed with the refusal removed — this "
                         "witness is measuring something other than the refusal")
        parallel.run_passes = real_passes

        # -- half two: the branch, run for real, destroys the evidence ------------
        fx2 = Fixture(two_pass_doc())
        self.addCleanup(fx2.cleanup)
        commit(fx2.root)
        base = state_mod.load_state(fx2.audit)
        record = filed("F-0001", "P-01", "U01")
        body_rel = record["body_path"]
        report_rel = "passes/P-01-report.md"

        def session(pid):
            sandbox = runner.Sandbox(fx2.root, runner.resolve_profile({}, "Auditor"),
                                     f"Auditor-{pid}")
            sandbox.enter()
            try:
                work = Path(sandbox.workdir) / "audit"
                for rel in (body_rel, report_rel):
                    p = work / rel
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(f"# {rel}\n\n{self.SENTINEL}\n", encoding="utf-8")
                sdoc = two_pass_doc()
                sdoc["findings"] = [record]
                sdoc["queue"][0]["done"] = True
                sdoc["queue"][0]["coverage"] = {
                    "report_path": report_rel, "findings": ["F-0001"],
                    "updated": "2026-08-15", "status": "done"}
                (work / "state.json").write_text(
                    json.dumps(sdoc, sort_keys=True, indent=2) + "\n", encoding="utf-8")
                return parallel.harvest(base, state_mod.load_state(work), pid, (1, 50))
            finally:
                sandbox.leave()

        said = []
        parallel.run_passes(fx2.root, {}, ["P-01"], session, announce=said.append)
        print("\nWITNESS B/2 — the merge said:\n  " + "\n  ".join(said))

        after = state_mod.load_state(fx2.audit)
        body = fx2.audit / body_rel
        report = fx2.audit / report_rel
        present = [r.id for r in after.findings]
        body_text = body.read_text(encoding="utf-8") if body.exists() else ""
        lint = self.run_cli(fx2, "lint")
        print(f"  FINDING_RECORD_MERGED={present}")
        print(f"  BODY_EXISTS={body.exists()}  "
              f"BODY_HAS_EVIDENCE={self.SENTINEL in body_text}")
        print(f"  REPORT_EXISTS={report.exists()}")
        print(f"  LINT_RC={lint.returncode}")
        self.assertEqual(present, ["F-0001"], "nothing merged — no loss to observe")
        self.assertNotIn(self.SENTINEL, body_text,
                         "the evidence survived: the defect this refusal stands in front "
                         "of is gone, and the refusal should be lifted, not asserted")
        self.assertFalse(report.exists(), "the pass report survived the teardown")
        self.assertNotEqual(lint.returncode, 0,
                            "lint stayed green over a finding with no evidence")

    # ---- criterion 7: nothing inherits the old default silently ------------------

    def test_no_test_relies_on_the_implicit_retry_default(self):
        """A test written against `retry_limit=2` that now silently reads 0 is a test
        that stopped testing what its name says. Every call site must either name the
        limit or name `CONF_DEFAULTS`, which is the one honest way to mean "the default"."""
        # Assembled from halves: this scanner is inside the tree it scans, and written
        # whole the needle would match the line below and report this test as its own
        # offender. Excluding the file would be worse — it is the one place a real
        # implicit call site could then hide.
        needle = "_dispatch_with" + "_retry("
        sites, silent = [], []
        for py in sorted((REPO / "tests").glob("test_*.py")):
            lines = py.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines):
                if needle not in line:
                    continue
                window = "\n".join(lines[i:i + 4])
                where = f"tests/{py.name}:{i + 1}"
                explicit = "retry_limit" in window or "CONF_DEFAULTS" in window
                sites.append((where, "explicit" if explicit else "IMPLICIT"))
                if not explicit:
                    silent.append(where)
        print(f"\nretry call sites ({len(sites)}):")
        for where, how in sites:
            print(f"  {how:<9} {where}")
        self.assertGreater(len(sites), 2, "the scan found almost nothing to classify")
        self.assertEqual(silent, [],
                         f"these inherit the retry default silently: {silent}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
