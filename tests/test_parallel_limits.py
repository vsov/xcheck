"""0.9.1 phase 7: the parallel dispatcher has ceilings, and they are MEASURED.

The third audit's P1 #9. `ThreadPoolExecutor(max_workers=len(pass_ids))` made the
queue's length the concurrency: three hundred disjoint passes started three hundred
billed agent sessions at once, with no cap, no budget and nothing to confirm. The audit
put this beside the evidence loss deliberately — it is a financial and operational
hazard that survives the evidence fix, and it has to exist BEFORE the parallel refusal
is lifted, or lifting it re-enables unbounded fan-out in the same commit.

The same file carried the arithmetic half: one pass reserved a fixed block of 50 finding
ids while `max_findings_per_pass` is an operator limit whose schema range is 1..999. At
200 the 51st finding of one pass lands in the next pass's block.

**Parallel dispatch is still refused.** These tests call `parallel.run_passes` directly
with a fake `session`, which is the bypass — visible here, at the call sites, rather
than as an escape hatch in the product. `TheRefusalIsStillInPlace` asserts that the
operator-facing path has not quietly opened.

Criterion 1 is measured, never read back. A test asserting `pool._max_workers == 2`
proves the constructor received an argument; the peak below is recorded from INSIDE the
sessions, under a lock, as a genuine read-modify-write — two threads incrementing a
counter can lose each other, which is exactly the property that makes the measurement
worth something.
"""

import io
import threading
import time
import unittest

from tests.harness import Fixture, queue_pass, state_doc, xcheck_submodule

parallel = xcheck_submodule("parallel")
util = xcheck_submodule("util")
state = xcheck_submodule("state")
StateError = state.StateError


def conf(**over):
    """A WHOLE conf. Partial conf dicts drop keys the code then reads as absent, which
    is how a limit test comes to measure a different limit than it names."""
    raw = dict(util.CONF_DEFAULTS, **{k: str(v) for k, v in over.items()})
    raw.setdefault("trust_level", "trusted")   # see tests/test_trust_level.py
    return util.Conf(raw)


def project(n_passes, **limits):
    """A project with `n_passes` queued passes over DISJOINT units.

    Disjoint because `refuse_overlaps` runs first and would refuse the run before any
    ceiling was reached — a fixture that overlaps measures the overlap guard.
    """
    queue = [dict(queue_pass(f"P-{i:02d}", done=False), units=[f"U{i:02d}"])
             for i in range(1, n_passes + 1)]
    doc = state_doc(queue=queue)
    # Every unit a pass names must be in the catalog, or the state refuses to load and
    # the test measures the schema instead of the ceiling.
    doc["catalogs"] = dict(doc["catalogs"], units=[
        {"id": f"U{i:02d}", "material": f"src/u{i}.py", "size": "0.4 kloc",
         "responsibility": "a unit"} for i in range(1, n_passes + 1)])
    if limits:
        doc["limits"] = dict(doc["limits"], **limits)
    return Fixture(doc)


class PeakRecorder:
    """Concurrency as OBSERVED, not as configured.

    `enter`/`leave` are a read-modify-write under one lock. Without the lock two
    threads can read the same `live` and write back the same value, losing an
    increment — the peak would then be an undercount and the bound would look
    respected on a run that violated it.
    """

    def __init__(self, hold=0.25):
        self.lock = threading.Lock()
        self.live = 0
        self.peak = 0
        self.started = []
        self.hold = hold

    def session(self, pid):
        with self.lock:
            self.live += 1
            self.peak = max(self.peak, self.live)
            self.started.append(pid)
        # Held long enough that every worker the pool is willing to run is running.
        # Without the hold, a fast session finishes before the next starts and the peak
        # reads 1 no matter how wide the pool is — a green result measuring nothing.
        time.sleep(self.hold)
        with self.lock:
            self.live -= 1
        return None                       # nothing to merge; the peak is the subject


class Terminal(io.StringIO):
    """A stdin that IS a terminal, so the confirmation path can be exercised at all."""

    def isatty(self):
        return True


class TheWorkerPoolIsBounded(unittest.TestCase):
    """Criterion 1, on the observed peak."""

    def test_the_peak_never_exceeds_parallel_workers(self):
        fx = project(6)
        self.addCleanup(fx.cleanup)
        rec = PeakRecorder()
        c = conf(parallel_workers=2, max_sessions_per_run=10,
                 parallel_confirm_above=10)
        parallel.run_passes(fx.root, c, [f"P-{i:02d}" for i in range(1, 7)],
                            rec.session, announce=lambda *_: None)
        print(f"\nconfigured parallel_workers=2 · observed peak={rec.peak} "
              f"· sessions started={len(rec.started)}")
        self.assertEqual(len(rec.started), 6, "not every pass ran")
        self.assertLessEqual(rec.peak, 2, "more sessions were in flight than the pool "
                                          "is allowed to hold")
        # The positive control: a peak of 1 would satisfy the bound and prove nothing,
        # because it is also what a SERIAL run produces.
        self.assertGreaterEqual(rec.peak, 2, "the run never had two sessions in flight, "
                                             "so this measurement cannot tell a bounded "
                                             "pool from no concurrency at all")

    def test_a_wider_pool_actually_gets_wider(self):
        """The other direction. If the peak were pinned by something else — the sleep,
        the fixture, the GIL — it would not move when the bound moves."""
        fx = project(6)
        self.addCleanup(fx.cleanup)
        rec = PeakRecorder()
        parallel.run_passes(fx.root,
                            conf(parallel_workers=5, max_sessions_per_run=10,
                                 parallel_confirm_above=10),
                            [f"P-{i:02d}" for i in range(1, 7)], rec.session,
                            announce=lambda *_: None)
        print(f"configured parallel_workers=5 · observed peak={rec.peak}")
        self.assertGreaterEqual(rec.peak, 3, "the bound moved and the observed peak did "
                                             "not, so the peak is not measuring the pool")
        self.assertLessEqual(rec.peak, 5)

    def test_the_pool_is_never_wider_than_the_work(self):
        c = conf(parallel_workers=8)
        self.assertEqual(parallel.worker_count(c, 2), 2)
        self.assertEqual(parallel.worker_count(c, 20), 8)

    def test_a_zero_worker_pool_is_a_hang_and_is_refused(self):
        """0 workers is not less concurrency, it is none — the pool would accept every
        future and run nothing at all.

        The floor lives in `NUMERIC_CONF` and fires where the value is READ, so the
        refusal is asserted through `worker_count`, the one consumer. Asserting it at
        construction would pass today and say nothing about the read path, which is
        where a bad value actually reaches the pool.
        """
        with self.assertRaises(SystemExit) as e:
            parallel.worker_count(conf(parallel_workers=0), 4)
        print(f"\nparallel_workers=0 -> {str(e.exception)[:150]}")
        self.assertIn("parallel_workers", str(e.exception))


class TheSessionCapStopsDispatch(unittest.TestCase):
    """Criterion 2."""

    def test_over_the_cap_the_run_refuses_naming_count_and_limit(self):
        fx = project(6)
        self.addCleanup(fx.cleanup)
        rec = PeakRecorder(hold=0)
        with self.assertRaises(StateError) as e:
            parallel.run_passes(fx.root, conf(max_sessions_per_run=4),
                                [f"P-{i:02d}" for i in range(1, 7)], rec.session,
                                announce=lambda *_: None)
        msg = str(e.exception)
        print(f"\n{msg}")
        self.assertIn("6 sessions", msg)
        self.assertIn("max_sessions_per_run=4", msg)
        self.assertEqual(rec.started, [], "the cap refused AFTER dispatching — the "
                                          "sessions it was supposed to prevent already "
                                          "ran and were already billed")

    def test_at_the_cap_it_proceeds(self):
        """The boundary, both sides. A cap that refuses AT the limit is off by one and
        an operator's configured 8 silently means 7."""
        fx = project(4)
        self.addCleanup(fx.cleanup)
        rec = PeakRecorder(hold=0)
        parallel.run_passes(fx.root,
                            conf(max_sessions_per_run=4, parallel_confirm_above=10),
                            [f"P-{i:02d}" for i in range(1, 5)], rec.session,
                            announce=lambda *_: None)
        self.assertEqual(len(rec.started), 4)


class TheBudgetCeilingIsWallClockAndSaysSo(unittest.TestCase):
    """Criterion 2's other half — the cost ceiling."""

    def test_the_worst_case_over_the_ceiling_refuses_with_the_arithmetic(self):
        fx = project(6)
        self.addCleanup(fx.cleanup)
        with self.assertRaises(StateError) as e:
            parallel.run_passes(fx.root,
                                conf(max_sessions_per_run=10, session_timeout=3600,
                                     parallel_budget_minutes=120),
                                [f"P-{i:02d}" for i in range(1, 7)],
                                PeakRecorder(hold=0).session, announce=lambda *_: None)
        msg = str(e.exception)
        print(f"\n{msg}")
        self.assertIn("6 x session_timeout 3600s = 360 min", msg)
        self.assertIn("parallel_budget_minutes=120", msg)
        self.assertIn("not a monetary one", msg,
                      "the message implies a cost xcheck cannot measure")

    def test_the_two_shipped_ceilings_bind_at_the_same_point(self):
        """Neither default may shadow the other. A budget that refuses at three passes
        while `max_sessions_per_run` says eight means the session cap is scenery, and
        an operator who raises it gets a refusal quoting a limit they did not touch.
        """
        d = util.CONF_DEFAULTS
        cap = int(d["max_sessions_per_run"])
        worst = cap * int(d["session_timeout"]) / 60.0
        print(f"\ndefaults: {cap} sessions x {d['session_timeout']}s = {worst:.0f} min "
              f"vs parallel_budget_minutes={d['parallel_budget_minutes']}")
        self.assertLessEqual(worst, int(d["parallel_budget_minutes"]),
                             "at the shipped defaults the budget refuses a run the "
                             "session cap allows")

    def test_zero_disables_the_ceiling(self):
        fx = project(3)
        self.addCleanup(fx.cleanup)
        rec = PeakRecorder(hold=0)
        parallel.run_passes(fx.root,
                            conf(parallel_budget_minutes=0, session_timeout=3600,
                                 parallel_confirm_above=10),
                            ["P-01", "P-02", "P-03"], rec.session,
                            announce=lambda *_: None)
        self.assertEqual(len(rec.started), 3)


class TheLargeFanOutNeedsAWord(unittest.TestCase):
    """Criteria 3 and 4. Both arms, and the arm with nobody at the keyboard."""

    def dispatch(self, n, **kw):
        fx = project(n)
        self.addCleanup(fx.cleanup)
        rec = PeakRecorder(hold=0)
        said = []
        parallel.run_passes(fx.root,
                            conf(max_sessions_per_run=20, parallel_confirm_above=4,
                                 parallel_budget_minutes=0),
                            [f"P-{i:02d}" for i in range(1, n + 1)], rec.session,
                            announce=said.append, **kw)
        return rec, said

    def test_above_the_threshold_without_confirmation_it_refuses(self):
        with self.assertRaises(StateError) as e:
            self.dispatch(6, stdin=io.StringIO("yes\n"))     # a stream, not a terminal
        msg = str(e.exception)
        print(f"\n{msg}")
        self.assertIn("parallel_confirm_above=4", msg)
        self.assertIn("without confirmation", msg)

    def test_with_the_flag_it_proceeds(self):
        rec, said = self.dispatch(6, assume_yes=True)
        print(f"assume_yes -> {said[0]}; {len(rec.started)} sessions started")
        self.assertEqual(len(rec.started), 6)
        self.assertIn("confirmed by --yes", said[0])

    def test_with_a_terminal_saying_yes_it_proceeds(self):
        rec, said = self.dispatch(6, stdin=Terminal("yes\n"))
        print(f"terminal 'yes' -> prompt was: {said[0][:110]}")
        self.assertEqual(len(rec.started), 6)
        self.assertIn("about to dispatch 6 concurrent agent sessions", said[0])

    def test_with_a_terminal_saying_no_it_refuses(self):
        with self.assertRaises(StateError):
            self.dispatch(6, stdin=Terminal("n\n"))

    def test_at_or_below_the_threshold_nothing_is_asked(self):
        rec, said = self.dispatch(4, stdin=None)
        self.assertEqual(len(rec.started), 4)
        self.assertFalse(any("about to dispatch" in s for s in said),
                         "a fan-out inside the threshold prompted anyway")

    def test_with_stdin_closed_it_refuses_rather_than_blocking(self):
        """Criterion 4. The failure this prevents is not a wrong answer, it is a HANG —
        and a hang here holds `audit/.lock`, so the next run blocks too. The test would
        not fail on a regression, it would never finish; the timeout is the assertion.
        """
        done, err = threading.Event(), []

        def run():
            try:
                self.dispatch(6, stdin=None)
            except BaseException as e:                       # noqa: BLE001 - recorded
                err.append(e)
            finally:
                done.set()

        t = threading.Thread(target=run, daemon=True)
        t.start()
        finished = done.wait(30)
        print(f"\nstdin closed: finished={finished} raised={type(err[0]).__name__ if err else None}")
        self.assertTrue(finished, "the run BLOCKED with no terminal to prompt — that is "
                                  "a hang in cron, holding audit/.lock")
        self.assertTrue(err and isinstance(err[0], StateError))
        self.assertIn("REFUSED rather than", str(err[0]))


class TheReservedBlockFollowsTheLimit(unittest.TestCase):
    """Criterion 6. Derived AND refused by name — the second is why the first can be
    trusted, since the reservation is arithmetic and the refusal reads what was filed.
    """

    def blocks(self, limit, n=3):
        fx = project(n, max_findings_per_pass=limit)
        self.addCleanup(fx.cleanup)
        st = state.load_state(fx.audit)
        return parallel.id_block(st), parallel.reserve_ids(
            st, [f"P-{i:02d}" for i in range(1, n + 1)])

    def test_the_block_is_the_limit_not_a_constant(self):
        for limit in (15, 50, 200, 999):
            block, reserved = self.blocks(limit)
            spans = [(a, b) for a, b in reserved.values()]
            print(f"\nmax_findings_per_pass={limit:<4} block={block:<4} {spans}")
            self.assertEqual(block, limit)
            for first, last in spans:
                self.assertEqual(last - first + 1, limit)

    def test_the_blocks_never_overlap_at_any_limit(self):
        for limit in (1, 15, 200, 999):
            _block, reserved = self.blocks(limit, n=4)
            spans = sorted(reserved.values())
            for (a1, b1), (a2, _b2) in zip(spans, spans[1:]):
                self.assertLess(b1, a2, f"blocks overlap at limit {limit}: "
                                        f"{(a1, b1)} and {(a2, _b2)}")

    def test_a_finding_outside_its_block_is_refused_by_name(self):
        """Exhaustion, named. A pass that files past its block has exceeded a limit it
        was told in its own charter, and the merge says which ids and which block."""
        fx = project(2, max_findings_per_pass=2)
        self.addCleanup(fx.cleanup)
        base = state.load_state(fx.audit)
        reserved = parallel.reserve_ids(base, ["P-01", "P-02"])
        session_doc = state_doc(
            queue=[dict(queue_pass("P-01", done=False), units=["U01"])],
            findings=[{"id": f"F-{i:04d}", "title": "t", "severity": "major",
                       "status": "reported", "dimension": "invariants",
                       "unit": ["U01"], "pass": "P-01", "attempts": 0,
                       "updated": "2026-08-14", "body_path": f"findings/F-{i:04d}.md"}
                      for i in (1, 2, 3)])
        sfx = Fixture(session_doc)
        self.addCleanup(sfx.cleanup)
        with self.assertRaises(StateError) as e:
            parallel.harvest(base, state.load_state(sfx.audit), "P-01",
                             reserved["P-01"])
        print(f"\nblock for P-01 = {reserved['P-01']}")
        print(str(e.exception))
        self.assertIn("F-0003", str(e.exception))

    def test_the_charter_tells_the_session_its_block(self):
        _block, reserved = self.blocks(15)
        text = parallel.block_charter("audit U01", *reserved["P-01"])
        print(f"\n{text.splitlines()[-1]}")
        first, last = reserved["P-01"]
        self.assertIn(f"F-{first:04d}", text)
        self.assertIn(f"F-{last:04d}", text)


class TheCeilingsReachTheOperatorPath(unittest.TestCase):
    """Phase 8 opened this branch, so the ceilings above stopped being theory.

    Until phase 8 these limits were reachable only from a test, because the command
    that would reach them refused. This class is what that phase owed them: the
    operator's own path carries the confirmation answer, and it carries it as an
    explicit flag rather than a prompt nobody is there to answer."""

    def test_the_flag_is_honoured_now_that_evidence_survives(self):
        self.assertTrue(parallel.enabled(conf(parallel_passes="on")))
        self.assertFalse(parallel.enabled(conf(parallel_passes="off")))

    def test_the_ceilings_are_reachable_from_the_dispatcher(self):
        """The chain, asserted end to end: `--yes` is a declared flag, `_dispatch`
        forwards it, `cmd_next` takes it, and `run_passes` is called with it. A break
        anywhere in that chain makes `parallel_confirm_above`'s refusal name a route
        that does not exist."""
        import inspect
        cli = xcheck_submodule("cli")
        self.assertIn("--yes", cli.FLAG_COMMANDS)
        self.assertEqual(cli.FLAG_COMMANDS["--yes"], {"next", "loop"})
        self.assertIn("assume_yes", inspect.signature(parallel.run_passes).parameters)
        self.assertIn("assume_yes", inspect.signature(cli.cmd_next).parameters)
        self.assertIn("assume_yes", inspect.getsource(cli._dispatch))
        self.assertIn("assume_yes=assume_yes", inspect.getsource(cli.cmd_next))

    def test_the_refusal_names_a_flag_the_parser_accepts(self):
        """The phase-5 rule, applied to this phase's own message: a documented route to
        a verb that refuses is the same defect as no route at all."""
        try:
            parallel.refuse_oversized_run(conf(parallel_confirm_above=1),
                                          ["P-01", "P-02"], stdin=io.StringIO(""))
            self.fail("the confirmation ceiling did not refuse")
        except StateError as e:
            named = [w.strip("`") for w in str(e).split() if w.startswith("`--")]
        cli = xcheck_submodule("cli")
        print(f"\nthe refusal names {named}; the parser accepts "
              f"{sorted(cli.FLAG_COMMANDS)}")
        self.assertEqual(named, ["--yes"])
        for flag in named:
            self.assertIn(flag, cli.FLAG_COMMANDS,
                          f"the refusal tells the operator to re-run with {flag}, "
                          f"which the argument parser rejects")


if __name__ == "__main__":
    unittest.main(verbosity=2)
