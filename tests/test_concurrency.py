"""The write boundary, and the four things that make it a boundary rather than a patch.

Phase 1 measured the race. This module pins the SHAPE of the fix, because the fix is
easy to get wrong in four specific ways and every one of them looks green:

  * put the lock in `run_passes` — then the next caller of `envelope.store` forgets it;
  * lock only `write_state` — then two readers of one revision still overwrite;
  * rename the temp file uniquely — then the crash goes away and the data loss stays;
  * serialize the CHILDREN — then parallelism is a thread pool running a sequential loop.

`COUNTERFACTUALS` below disarms the boundary and asserts each failure comes back, so a
later edit that quietly removes it cannot leave this suite green.
"""

import ast
import contextlib
import json
import threading
import time
import unittest
from pathlib import Path

from tests.harness import REPO, xcheck_submodule
from tests.test_parallel_state_race import RaceCase, StaleRead, WriteSpy, verdict_of

envelope = xcheck_submodule("envelope")
state_mod = xcheck_submodule("state")
util = xcheck_submodule("util")

# Every binding of `serialized` a disarm has to reach. `write._apply` imports it inside
# the function (so `xcheck.util` is its binding); `envelope` imports it at module level
# (so it has its own). Patching one and not the other disarms half the boundary and
# measures the wrong thing — the same trap phase 1 hit with `write_state`.
SERIALIZED_BINDINGS = (("util", "serialized"), ("envelope", "serialized"))

BOUNDARY = {"write": "_apply", "envelope": "store", "envelope_emit": "emit"}


@contextlib.contextmanager
def unserialized():
    """Remove the in-process window from the SUBJECT only."""
    import xcheck
    saved = [(m, n, getattr(getattr(xcheck, m), n)) for m, n in SERIALIZED_BINDINGS]
    for m, n in SERIALIZED_BINDINGS:
        setattr(getattr(xcheck, m), n, contextlib.nullcontext)
    try:
        yield
    finally:
        for m, n, fn in saved:
            setattr(getattr(xcheck, m), n, fn)


@contextlib.contextmanager
def unique_temp_name():
    """`write_state` with a per-call temp name — the fix-the-errno move, on its own."""
    import xcheck
    real = state_mod.write_state

    def patched(audit_dir, state, *a, **kw):
        # Emulate the narrow fix without editing the shipped module: give each writer
        # its own temp file by serialising the rename alone, which is exactly what a
        # unique name buys and no more.
        with _RENAME_ONLY:
            return real(audit_dir, state, *a, **kw)

    saved = {}
    for name in ("state", "envelope", "write"):
        mod = getattr(xcheck, name)
        saved[name] = getattr(mod, "write_state")
        setattr(mod, "write_state", patched)
    try:
        yield
    finally:
        for name, fn in saved.items():
            setattr(getattr(xcheck, name), "write_state", fn)


_RENAME_ONLY = threading.Lock()


# =========================================================== 1. the boundary is a boundary

class TheLockIsAtTheBoundary(unittest.TestCase):
    """A caller written later cannot forget a boundary it does not have to call."""

    def source(self, module):
        return Path(REPO, "xcheck", f"{module}.py").read_text(encoding="utf-8")

    def function(self, module, name):
        for node in ast.walk(ast.parse(self.source(module))):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        self.fail(f"{module}.{name} is gone — this gate's subject moved")

    def takes_the_window(self, node):
        """Does this function body enter `serialized()`?"""
        for n in ast.walk(node):
            if isinstance(n, ast.With):
                for item in n.items:
                    call = item.context_expr
                    if isinstance(call, ast.Call) and getattr(
                            call.func, "id", getattr(call.func, "attr", None)
                    ) == "serialized":
                        return True
        return False

    def test_every_writer_of_canonical_state_takes_the_window(self):
        got = {f"{m}.{f}": self.takes_the_window(self.function(m, f))
               for m, f in (("write", "_apply"), ("envelope", "store"),
                            ("envelope", "emit"))}
        print(f"\n  BOUNDARY   {got}")
        self.assertEqual({k: True for k in got}, got,
                         "a writer of canonical state does not take the merge window")

    def test_the_dispatcher_does_not_take_it_instead(self):
        """`run_passes` must need nothing. A lock in the caller is a convention."""
        node = self.function("parallel", "run_passes")
        print(f"  BOUNDARY   parallel.run_passes takes it: "
              f"{self.takes_the_window(node)} (must be False)")
        self.assertFalse(
            self.takes_the_window(node),
            "the fix is in the dispatcher, so the next caller of envelope.store — "
            "written by someone who never read this — races again")

    def test_the_window_covers_the_read_not_only_the_write(self):
        """`store` must enter the window BEFORE `load_state`, or two readers of one
        revision still overwrite each other however atomic each write is."""
        src = self.source("envelope")
        node = self.function("envelope", "store")
        body = ast.get_source_segment(src, node)
        self.assertIn("with serialized():", body)
        after = body.split("with serialized():", 1)[1]
        self.assertNotIn("load_state(", body.split("with serialized():", 1)[0],
                         "state is loaded before the window opens")
        print("  BOUNDARY   store enters the window before it loads state: True")
        self.assertTrue(after.strip(), "the window is empty")


# ======================================================= 2. the counterfactuals

class COUNTERFACTUALS(RaceCase):
    """Disarm the boundary; every symptom must come back."""

    def measure(self):
        """Run the race and report what it produced.

        `SystemExit` is caught deliberately: `_apply` refuses a transition over a broken
        stream by raising it, and a counterfactual that let that escape would report a
        harness crash where the subject actually refused correctly.
        """
        refused, results, spy = None, [], None
        try:
            results, _said, spy = self.race()
        except SystemExit as e:
            refused = "broken-stream" if "chain breaks" in str(e) else str(e)[:60]
        events = [json.loads(l) for l in
                  (self.fx.audit / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        return {
            "overlaps": len(spy.overlaps()) if spy else None,
            "threads": len(spy.threads) if spy else 0,
            "errors": [type(e).__name__ for _, _, e in results if e is not None],
            "refused": refused,
            "chain": verdict_of(xcheck_submodule("ledger").status(self.fx.root)),
            "dispatched": sum(1 for e in events if e.get("event") == "session_dispatched"),
            "finished": sum(1 for e in events if e.get("event") == "session_finished"),
        }

    def repeat(self, ctx, attempts=3):
        """Run the disarmed race until the symptom shows. The DEFECT is intermittent —
        that is what made the audit call it a flake — so a one-shot counterfactual is a
        coin toss. Measured 10 of 10 over a longer sample; three attempts here."""
        seen = []
        for i in range(attempts):
            if i:
                self.new_project()
            with ctx():
                seen.append(self.measure())
        return seen

    def test_removing_the_window_brings_every_symptom_back(self):
        seen = self.repeat(unserialized)
        print(f"\n  DISARMED   {seen}")
        self.assertTrue(all(g["threads"] > 1 for g in seen),
                        "positive control: more than one writer")
        self.assertTrue(any(g["overlaps"] > 0 for g in seen),
                        "without the window the writes must overlap again, or this "
                        "suite is green for a reason that is not the fix")
        self.assertTrue(any(g["dispatched"] + g["finished"] < 4 for g in seen),
                        "without the window evidence must go missing again")

    def test_a_unique_temp_name_removes_the_symptoms_and_not_the_race(self):
        """The repair that would have shipped if the crash had been read as the defect.

        MEASURED, and not what I assumed. Serialising the rename alone removes the
        `FileNotFoundError` AND, because the lost events were collateral of that crash,
        it removes the missing evidence too. Both visible symptoms go away.

        The writers still overlap. So the race is intact and only its current symptoms
        are gone — which is strictly worse than the crash, because the next symptom is
        now a matter of timing rather than of anything anyone chose. This arm exists to
        stop that repair from ever looking sufficient.
        """
        seen = []
        for i in range(3):
            if i:
                self.new_project()
            with unserialized(), unique_temp_name():
                seen.append(self.measure())
        print(f"  ERRNO-ONLY {seen}")
        # Narrowed deliberately: `StateError` here is an ordinary merge refusal — a
        # second pass computing against a revision the first one moved — and asserting
        # "no errors at all" made this arm fail on correct behaviour. The crash the
        # narrow fix removes is the `FileNotFoundError` from `os.replace`, and that is
        # what this asserts.
        crashes = [e for g in seen for e in g["errors"] if e == "FileNotFoundError"]
        self.assertEqual([], crashes,
                         "the narrow fix is supposed to remove the rename crash")
        worse = [g for g in seen if g["refused"] or g["chain"] != "intact"]
        self.assertTrue(worse or any((g["overlaps"] or 0) > 0 for g in seen),
                        "the narrow fix is supposed to leave the RACE in place — if the "
                        "writes no longer overlap and nothing downstream breaks, this "
                        "arm is not distinguishing the two repairs")
        if worse:
            print(f"  ERRNO-ONLY it got WORSE: {len(worse)} of {len(seen)} runs broke "
                  f"the event chain — removing the crash let both writers live long "
                  f"enough to append concurrently, and `_apply` then refused the "
                  f"transition over a stream it cannot stand behind")

    def test_the_window_restores_all_of_it(self):
        """CONTROL for the two arms above: armed, on the same fixture, all clean."""
        got = self.measure()
        print(f"  ARMED      {got}")
        self.assertEqual(0, got["overlaps"])
        self.assertEqual([], got["errors"])
        self.assertIsNone(got["refused"])
        self.assertEqual("intact", got["chain"])
        self.assertEqual((2, 2), (got["dispatched"], got["finished"]))


# ============================================================ 3. what must NOT be serialized

class TheChildrenStillOverlap(RaceCase):
    """A parallel run that serialized the SESSIONS has traded a race for a sequential
    loop wearing a thread pool. The transaction is the thing that serializes."""

    def test_sessions_overlap_while_their_writes_do_not(self):
        spans, lock = [], threading.Lock()

        def timed(pid):
            start = time.monotonic()
            time.sleep(0.30)                       # stand in for the agent's real work
            out = self.session_for(pid)()
            with lock:
                spans.append((pid, start, time.monotonic()))
            return out

        spy = WriteSpy(hold=0.02)
        with spy:
            parallel = xcheck_submodule("parallel")
            parallel.run_passes(str(self.fx.root), self.conf, ["P-01", "P-02"],
                                timed, announce=lambda *_: None, assume_yes=True)
        (_, a0, a1), (_, b0, b1) = spans[0], spans[1]
        session_overlap = min(a1, b1) - max(a0, b0)
        print(f"\n  THROUGHPUT sessions overlapped by {session_overlap:.2f}s · "
              f"state writes overlapped {len(spy.overlaps())} time(s)")
        self.assertGreater(session_overlap, 0.1,
                           "the agent sessions ran one after the other — the window is "
                           "around the children instead of the transaction")
        self.assertEqual([], spy.overlaps())


# ================================================================== 4. reentrancy

class TheWindowIsReentrant(RaceCase):
    """`run_session` holds this across a call that takes it again. A non-reentrant lock
    would deadlock one thread against itself the first time those nested."""

    def test_a_write_inside_a_held_window_completes(self):
        write = xcheck_submodule("write")
        done = []

        def nested():
            with util.serialized():                       # the caller's transaction
                with util.serialized():                   # a step inside it
                    envelope.store(self.fx.root, self.a_record(), event="session_dispatched")
                    write._apply(str(self.fx.root), lambda doc, state: "nested write",
                                 role="merge", verb="merge-pass")
                done.append(True)

        t = threading.Thread(target=nested, daemon=True)
        t.start()
        t.join(timeout=20)
        print(f"\n  REENTRANT  nested store + _apply inside a held window: "
              f"completed={bool(done)} (alive={t.is_alive()})")
        self.assertFalse(t.is_alive(), "the nested write deadlocked against itself")
        self.assertTrue(done, "the nested write did not complete")

    def a_record(self):
        runner = xcheck_submodule("runner")
        return envelope.dispatch_record(
            ["agent", "--prompt", "x"], "Auditor", "charter", "prompt",
            session_id="1" * 16, profile=runner.PROFILES["worktree"],
            state_revision=envelope.current_revision(self.fx.root), head_before=None)


# ============================================== 5. two readers of one revision

class TwoReadersOfOneRevision(RaceCase):
    """Locking only the write still loses an update. The barrier proves the window
    covers the read: two threads can no longer both be between load and write."""

    def test_no_two_threads_are_between_the_read_and_the_write(self):
        stale = StaleRead(lambda n: n.startswith("ThreadPool"), timeout=3)
        parallel = xcheck_submodule("parallel")
        sessions = {"P-01": self.session_for("P-01"), "P-02": self.session_for("P-02")}
        with stale:
            parallel.run_passes(str(self.fx.root), self.conf, ["P-01", "P-02"],
                                lambda pid: sessions[pid](),
                                announce=lambda *_: None, assume_yes=True)
        doc = json.loads((self.fx.audit / "state.json").read_text(encoding="utf-8"))
        revisions = [rev for _n, _i, rev in stale.parked]
        print(f"\n  READERS    parked reads saw revisions {revisions}")
        print(f"  READERS    sessions kept: {len(doc.get('sessions', []))} of 2")
        self.assertNotEqual([1, 1], revisions,
                            "both workers read revision 1 — they were inside the "
                            "read-modify-write together and the window does not cover "
                            "the read")
        self.assertEqual(2, len(doc.get("sessions", [])))


# ====================================================== 6. the sequential path is untouched

class TheSequentialPathIsUnchanged(RaceCase):
    """Adding a lock is a behaviour change until you show it is not.

    The window is an in-process mutex, so on one thread it must be invisible: the same
    transitions, the same revisions, the same events, in the same order. Comparing ARMED
    against DISARMED on a single thread is the before/after for this phase without
    needing a checkout of the previous commit.
    """

    def transitions(self):
        parallel = xcheck_submodule("parallel")
        for pid in ("P-01", "P-02"):
            self.session_for(pid)()
        parallel.merge(str(self.fx.root), lambda doc, state: "sequential write",
                       expect_revision=None, verb="merge-pass", target="P-01",
                       announce=lambda *_: None)
        doc = json.loads((self.fx.audit / "state.json").read_text(encoding="utf-8"))
        events = [json.loads(l) for l in
                  (self.fx.audit / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        return {
            "revision": doc["state_revision"],
            "sessions": len(doc.get("sessions", [])),
            "events": [e.get("event") for e in events],
            "chain": verdict_of(xcheck_submodule("ledger").status(self.fx.root)),
        }

    def test_one_thread_sees_exactly_what_it_saw_before(self):
        armed = self.transitions()
        self.new_project()
        with unserialized():
            disarmed = self.transitions()
        print(f"\n  SEQUENTIAL armed   {armed}")
        print(f"  SEQUENTIAL disarmed{disarmed}")
        self.assertEqual(disarmed, armed,
                         "the window changed single-threaded behaviour — it is supposed "
                         "to be invisible to one thread")
        self.assertGreater(len(armed["events"]), 2,
                           "the control produced almost nothing, so equality is cheap")


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()
