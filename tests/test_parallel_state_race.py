"""The parallel dispatcher's writers race, and the crash is only the loud third of it.

The sixth audit reported an intermittent
`FileNotFoundError: .../audit/.state.json.tmp -> .../audit/state.json` out of a full CI
run. That traceback is a SYMPTOM of a fixed temp filename, not the defect. Renaming the
temp file uniquely — the obvious "fix the errno" move — deletes the only loud face and
leaves the two silent ones running.

So this module does not chase the crash. It measures the PROPERTY: two writers of
`audit/state.json` must never be inside their read-modify-write at the same time. The
spy records `(start, end, thread)` per write and asserts no two intervals intersect.

Why the writers collide. `parallel.run_passes` merges on the MAIN thread inside its
`as_completed` loop while `ThreadPoolExecutor` workers are still running the other
passes — and a running pass writes the same project, because `agent_pass` calls
`envelope.store()` at dispatch and again at outcome. `write._apply` takes
`Lock(audit, role)`, which is the `audit/.lock` DIRECTORY: a mutex against another
PROCESS, blind to a second THREAD of its own process. `envelope.store` takes nothing.

`write_state` is imported BY NAME into `xcheck.envelope`, `xcheck.write` and
`xcheck.migrate`, so a spy that patches only `xcheck.state.write_state` sees nothing
those modules do. `TheSpyMeasuresWhatItClaims` pins that, because a spy nobody calls
reports the same empty list as a program with no race.

MEASURED, not inherited. Four candidate faces were tried; two reproduce and two do not,
and the two that do not are kept as NEGATIVE RESULTS with their rates
(`test_the_faces_that_did_not_reproduce_here`) rather than deleted. See
`.supergoal/phases/phase-1.fix.md` for the rate table and the two corrections that came
out of measuring instead of asserting.

CLOSED by phase 2, kept as the regression gate. These three shipped EXPECTED-RED in
phase 1 and their `unittest.expectedFailure` decorators were REMOVED by the phase that
put `util.serialized()` around the whole read-modify-write in `write._apply`,
`envelope.store` and `envelope.emit`:

  * `TheWriteIsNotSerialized.test_two_writers_are_never_inside_at_once`
  * `ThreeFacesOfOneRace.test_face_1_the_fixed_temp_name_loses_a_rename`
  * `ThreeFacesOfOneRace.test_face_2_events_are_silently_lost`

A phase that removes a decorator is what proves the fix. An assertion that the defect
is gone, written by the same hand that wrote the fix, is not.
"""

import collections
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tests.harness import (CATALOGS, REPO, Fixture, complete_conf, queue_pass,
                           state_doc, xcheck_submodule)

parallel = xcheck_submodule("parallel")
envelope = xcheck_submodule("envelope")
state_mod = xcheck_submodule("state")
write_mod = xcheck_submodule("write")
ledger_mod = xcheck_submodule("ledger")
runner = xcheck_submodule("runner")

# Every module that imported the name, plus the module that defines it. A split seam
# disarms a name-patching double exactly the way a moved function does.
WRITE_STATE_BINDINGS = ("state", "envelope", "write")

HOLD = 0.25          # how long the spy holds one write open, seconds


class Interval:
    """One write of canonical state: when it began, when it ended, and by whom."""

    __slots__ = ("thread", "start", "end")

    def __init__(self, thread, start):
        self.thread, self.start, self.end = thread, start, None

    def overlaps(self, other):
        return self.start < other.end and other.start < self.end

    def __repr__(self):
        return f"<{self.thread} {self.start:.3f}..{self.end:.3f}>"


class WriteSpy:
    """Wrap `write_state` in every binding and record when each call was inside.

    `hold` widens the window so the interleaving is OBSERVABLE rather than lucky. It
    does not create the race — it makes a race that is already there deterministic, and
    a serialized writer is unaffected by it because the second caller simply waits.
    """

    def __init__(self, bindings=WRITE_STATE_BINDINGS, hold=HOLD, on_write=None,
                 on_done=None):
        self.bindings = bindings
        self.hold = hold
        self.on_write = on_write
        self.on_done = on_done
        self.intervals = []
        self._lock = threading.Lock()
        self._saved = {}

    def __enter__(self):
        import xcheck
        real = state_mod.write_state
        spy = self

        def wrapped(*a, **kw):
            iv = Interval(threading.current_thread().name, time.monotonic())
            with spy._lock:
                spy.intervals.append(iv)
            if spy.on_write:
                spy.on_write(iv)
            try:
                return real(*a, **kw)
            finally:
                time.sleep(spy.hold)
                iv.end = time.monotonic()
                if spy.on_done:
                    spy.on_done(iv)

        for name in self.bindings:
            mod = getattr(xcheck, name)
            self._saved[name] = getattr(mod, "write_state")
            setattr(mod, "write_state", wrapped)
        return self

    def __exit__(self, *exc):
        import xcheck
        for name, fn in self._saved.items():
            setattr(getattr(xcheck, name), "write_state", fn)
        for iv in self.intervals:                 # a raising write never closed its span
            if iv.end is None:
                iv.end = iv.start
        return False

    @property
    def threads(self):
        return {iv.thread for iv in self.intervals}

    def overlaps(self):
        out = []
        for i, a in enumerate(self.intervals):
            for b in self.intervals[i + 1:]:
                if a.thread != b.thread and a.overlaps(b):
                    out.append((a, b))
        return out


class StaleRead:
    """Hold BOTH workers' first `envelope.load_state` until each has read.

    An event-based release was not enough: whichever worker parked second had already
    read AFTER the other's write, so its snapshot was current and nothing was lost. A
    two-party barrier makes the precondition explicit — both threads hold a snapshot of
    the same revision, and then whichever writes second overwrites the first.

    That is the whole defect in one sentence: `envelope.store` declares no revision, so
    there is nothing to refuse a write built on a snapshot that is no longer true.
    """

    def __init__(self, on_thread, parties=2, timeout=10, nth=2):
        # `nth` is which load of that thread to park. The FIRST one is
        # `current_revision()` inside `dispatch_record` — parking it stalls a read that
        # is thrown away, and the store's own read then happens afterwards and is
        # perfectly current, which is why the barrier alone changed nothing.
        self.on_thread, self.parked, self.nth = on_thread, [], nth
        self._barrier = threading.Barrier(parties, timeout=timeout)
        self._saved = None

    def __enter__(self):
        import xcheck
        real = xcheck.envelope.load_state
        me = self
        seen = collections.Counter()

        def wrapped(*a, **kw):
            got = real(*a, **kw)
            name = threading.current_thread().name
            if not me.on_thread(name):
                return got
            seen[name] += 1
            if seen[name] == me.nth:
                me.parked.append((name, seen[name],
                                  getattr(got, "state_revision", None)))
                try:
                    me._barrier.wait()
                except threading.BrokenBarrierError:
                    pass                       # one worker never arrived; report as-is
            return got

        self._saved = real
        xcheck.envelope.load_state = wrapped
        return self

    def __exit__(self, *exc):
        import xcheck
        self._barrier.abort()
        xcheck.envelope.load_state = self._saved
        return False


class RaceCase(unittest.TestCase):
    """A two-pass parallel run whose second pass writes WHILE the first one merges."""

    def setUp(self):
        # A private temp root: `tempfile.gettempdir()` is shared with every other
        # session on this machine. Captured from the ORIGINAL value each time — reading
        # the current one would nest a level per test until the path stopped existing.
        self._old_tempdir = tempfile.tempdir
        self.tmp_root = tempfile.mkdtemp(prefix="xcheck-race-root-")
        self.addCleanup(shutil.rmtree, self.tmp_root, True)
        self.addCleanup(lambda: setattr(tempfile, "tempdir", self._old_tempdir))
        tempfile.tempdir = self.tmp_root

        self.conf = complete_conf(parallel_workers=2, max_sessions_per_run=4)
        self.new_project()

    def new_project(self):
        """A fresh throwaway project. Separate from setUp so the stress loop can take
        another one without re-entering setUp and nesting the temp root."""
        self.fx = Fixture(two_pass_doc())
        self.addCleanup(shutil.rmtree, self.fx.root, True)
        self.merge_started = threading.Event()
        self.merge_done = threading.Event()

    # -- the fixture's own moving parts ------------------------------------------------

    def session_for(self, pid, wait_for_merge=False):
        """What `agent_pass` does to the project, minus the agent.

        Dispatch envelope, then outcome envelope, then a mutate for the merge. The
        second `store` is the writer that collides with the dispatcher's merge.
        """
        def run():
            rec = envelope.dispatch_record(
                ["agent", "--prompt", "x"], "Auditor", f"pass {pid}", "prompt",
                session_id=f"{abs(hash(pid)) % 10**16:016d}",
                profile=runner.PROFILES["worktree"],
                state_revision=envelope.current_revision(self.fx.root),
                head_before=None)
            envelope.store(self.fx.root, rec, event="session_dispatched")
            if wait_for_merge:
                # Deterministic interleave: do not finish until the OTHER pass's merge
                # is inside its write. Without a lock the two spans intersect; with one
                # this simply waits, which is the whole point.
                self.merge_started.wait(timeout=10)
            log = Path(self.tmp_root) / f"{pid}.log"
            log.write_text(f"session {pid} ran\n", encoding="utf-8")
            done = envelope.finish(rec, 0, "ok", 0.1, None, str(log))
            envelope.store(self.fx.root, done, event="session_finished")

            def mutate(doc, state):
                # `_apply` mutates the document IN PLACE and reads a message back —
                # returning a new doc silently changes nothing.
                doc.setdefault("findings", []).append(
                    finding_doc(f"F-{int(pid[-2:]):04d}", pid))
                return f"merged {pid}"
            return mutate, None
        return run

    def race(self, spy=None, announce=None):
        """Drive the PUBLIC path — `run_passes` — with two colliding passes."""
        said = []
        announce = announce or said.append
        sessions = {"P-01": self.session_for("P-01"),
                    "P-02": self.session_for("P-02", wait_for_merge=True)}

        def session(pid):
            return sessions[pid]()

        ctx = spy or WriteSpy(on_write=self._note_merge)
        with ctx as s:
            results = parallel.run_passes(
                str(self.fx.root), self.conf, ["P-01", "P-02"],
                session, announce=announce, assume_yes=True)
        return results, said, s

    def _note_merge(self, iv):
        if iv.thread == "MainThread":
            self.merge_started.set()

    def _note_merge_done(self, iv):
        if iv.thread == "MainThread":
            self.merge_done.set()

    def lost_update_race(self):
        """Both passes read the same revision, then both write. Same root as the other
        two faces — an unsynchronized read-modify-write — but this one raises nothing,
        breaks no chain, and fails no write. It just drops a record."""
        said = []
        sessions = {"P-01": self.session_for("P-01"),
                    "P-02": self.session_for("P-02")}
        stale = StaleRead(lambda n: n.startswith("ThreadPool"))
        spy = WriteSpy(hold=0.05, on_write=self._note_merge)
        with spy, stale:
            results = parallel.run_passes(
                str(self.fx.root), self.conf, ["P-01", "P-02"],
                lambda pid: sessions[pid](), announce=said.append, assume_yes=True)
        return results, said, spy, stale


# --------------------------------------------------------------------- fixture bodies

def finding_doc(fid, pid="P-01"):
    return {"id": fid, "title": f"finding {fid}", "severity": "major",
            "status": "reported", "dimension": "invariants",
            "unit": ["U01" if pid == "P-01" else "U02"], "pass": pid,
            "attempts": 0, "updated": "2026-09-06", "body_path": f"findings/{fid}.md"}


def two_pass_doc():
    """Two passes over DISJOINT units — `refuse_overlaps` rejects the run otherwise,
    and a fixture refused before dispatch measures the refusal, not the race."""
    one, two = queue_pass("P-01"), queue_pass("P-02")
    two["units"] = ["U02"]
    cat = json.loads(json.dumps(CATALOGS))
    cat["units"].append({"id": "U02", "material": "src/other.py", "size": "0.2 kloc",
                         "responsibility": "other"})
    return state_doc(queue=[one, two], catalogs=cat)


# ============================================================ 1. the property itself

class TheWriteIsNotSerialized(RaceCase):
    """The audit's finding 1, as a property rather than as a traceback."""

    def test_two_writers_are_never_inside_at_once(self):
        _, said, spy = self.race()
        print(f"\n  WRITES     {len(spy.intervals)} across threads {sorted(spy.threads)}")
        over = spy.overlaps()
        for a, b in over:
            print(f"  OVERLAP    {a} intersects {b}")
        # POSITIVE CONTROL first: an empty overlap list is also what a spy nobody called
        # reports, so the arm below means nothing until more than one thread was seen.
        self.assertGreater(len(spy.threads), 1,
                           "the spy saw one thread — it was not measuring the race")
        self.assertEqual([], over,
                         "two writers were inside audit/state.json at the same time")


# ================================================================= 2. the three faces

class ThreeFacesOfOneRace(RaceCase):
    """One missing lock, three symptoms nobody would file as the same bug."""

    def test_face_1_the_fixed_temp_name_loses_a_rename(self):
        """The LOUD face, and the only one the audit itself reported. `write_state`
        uses one FIXED temp name, so the loser of `os.replace` has nothing to rename.

        Measured at 7 runs in 8, so one attempt would be a flaky gate: three attempts
        make it deterministic without pretending the underlying defect is."""
        seen = []
        for attempt in range(3):
            if attempt:
                self.new_project()
            results, _, _ = self.race()
            seen += [repr(e) for _, _, e in results if e is not None]
        print(f"\n  FACE 1     errors over 3 runs: {seen or 'none'}")
        self.assertEqual([], [e for e in seen if "FileNotFoundError" in e
                              or "state.json.tmp" in e],
                         f"a writer lost its temp file to the other: {seen}")

    def test_face_2_events_are_silently_lost(self):
        """The COLLATERAL face. Two passes each emit `session_dispatched` and
        `session_finished` — four events. The stream records two.

        CORRECTED in phase 2: this is not independent of face 1, and the first version
        of this docstring claimed it was. Measured over 10 disarmed runs, the crash and
        the missing events co-occur 10 times out of 10 — the writer that loses
        `os.replace` dies before its `emit`, so ONE pass's crash costs the OTHER pass's
        evidence.

        That makes it worse rather than less interesting: the surviving lines are a
        perfectly well-formed chain, `ledger status` says `intact`, and every consumer
        above the stream — budgets, metrics, receipts, the derived bundle — reads an
        undercount as the whole story. A crash you can see, in a place you are not
        looking, silently subtracting evidence somewhere else.
        """
        self.race()
        events = [json.loads(l) for l in
                  (self.fx.audit / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        kinds = collections.Counter(e.get("event") for e in events)
        chain = verdict_of(ledger_mod.status(self.fx.root))
        print("\n  FACE 2     2 passes emitted 2 dispatched + 2 finished")
        print(f"  FACE 2     the stream holds {kinds['session_dispatched']} dispatched, "
              f"{kinds['session_finished']} finished — chain says {chain!r}")
        self.assertEqual("intact", chain, "precondition: the chain itself is well formed")
        self.assertEqual((2, 2), (kinds["session_dispatched"], kinds["session_finished"]),
                         "events emitted by one pass were overwritten by the other")

    def test_the_faces_that_did_not_reproduce_here(self):
        """NEGATIVE RESULTS, recorded rather than dropped.

        Two more faces were expected from a peer session's report against real
        `agent_pass` sessions. Neither reproduces under this synthetic harness, and the
        reason is a protection that exists on those paths and not on `envelope.store`:

          * a BROKEN HASH CHAIN — 0 of 8 runs. `ledger.append_chained` recomputes the
            link from the file as it stands, under the append, so two appends serialize
            at that point.
          * a DROPPED RECORD surviving to the end of the run — 0 of 6. Both workers do
            hold a snapshot of the same revision and the second write does erase the
            first; the FINISHING store then re-adds what was dropped, so the end state
            repairs itself. The lost update is real and invisible by the time the run
            is over.

        A reader must be able to tell "we looked and it did not happen here" from "we did
        not look", which is why this is a test and not a comment.
        """
        _, _, _, stale = self.lost_update_race()
        chain = verdict_of(ledger_mod.status(self.fx.root))
        doc = json.loads((self.fx.audit / "state.json").read_text(encoding="utf-8"))
        print(f"\n  NEGATIVE   both workers parked holding {stale.parked}")
        print(f"  NEGATIVE   chain={chain!r} (expected intact: appends serialize)")
        print(f"  NEGATIVE   sessions kept={len(doc.get('sessions', []))} of 2 "
              f"(expected 2: the finishing store re-adds the dropped record)")
        self.assertEqual(2, len(stale.parked),
                         "both workers must hold a snapshot before either writes, or "
                         "this is not the negative result it claims to be")
        self.assertEqual("intact", chain)
        self.assertEqual(2, len(doc.get("sessions", [])))

    def test_verdict_of_unwraps_the_status_tuple(self):
        """`ledger.status` answers `(verdict, explanation)`. Comparing the whole tuple
        to "intact" is false for every input, so a chain assertion written that way
        fails on a HEALTHY chain and looks like the defect it was hunting. That is how
        face 2 was mismeasured at 8/8 before it was measured at 0/8."""
        self.assertEqual("intact", verdict_of(("intact", "7 event(s), chain intact")))
        self.assertEqual("broken", verdict_of(("broken", "line 2 ...")))
        self.assertNotEqual("intact", ("intact", "..."),
                            "the un-unwrapped comparison must still be false, or this "
                            "test is not pinning anything")

# ====================================================== 3. the harness under its own eye

class TheSpyMeasuresWhatItClaims(RaceCase):
    """Scoping a spy to one binding is how it quietly stops measuring."""

    def test_counterfactual_patching_only_the_defining_module_sees_nothing(self):
        """`envelope` and `write` imported `write_state` BY NAME. A spy on
        `xcheck.state` alone is blind to every write they make."""
        narrow = WriteSpy(bindings=("state",), on_write=self._note_merge)
        _, _, spy = self.race(spy=narrow)
        print(f"\n  NARROW SPY {len(spy.intervals)} write(s) seen, "
              f"threads {sorted(spy.threads) or '[]'}")
        self.assertEqual([], spy.overlaps(),
                         "the narrow spy is supposed to be blind — if it sees the race, "
                         "the bindings list is wrong, not this assertion")
        self.assertLess(len(spy.intervals), 2,
                        "patching xcheck.state alone should not see envelope's writes")

    def test_control_one_pass_at_a_time_never_overlaps(self):
        """The harness measures CONCURRENCY, not something else about this fixture."""
        spy = WriteSpy(hold=0.02)
        with spy:
            for pid in ("P-01", "P-02"):
                self.session_for(pid)()
        print(f"\n  SERIAL     {len(spy.intervals)} writes, "
              f"{len(spy.overlaps())} overlaps, threads {sorted(spy.threads)}")
        self.assertGreater(len(spy.intervals), 1, "the serial control wrote nothing")
        self.assertEqual([], spy.overlaps(), "a serial run cannot overlap")


# ================================================================== 4. the stress number

class HowOftenItBites(RaceCase):
    """A green run proves nothing without knowing how often the red one appears."""

    RUNS = 10

    def test_the_failure_rate_is_reported(self):
        failures, faces = 0, {}
        for i in range(self.RUNS):
            if i:
                self.new_project()
            try:
                results, _, spy = self.race(spy=WriteSpy(hold=0.05,
                                                         on_write=self._note_merge))
                if spy.overlaps():
                    failures += 1
                    faces["overlap"] = faces.get("overlap", 0) + 1
                if any(e for _, _, e in results if e is not None):
                    faces["error"] = faces.get("error", 0) + 1
            except Exception as e:                      # a crash IS one of the faces
                failures += 1
                faces[type(e).__name__] = faces.get(type(e).__name__, 0) + 1
        print(f"\n  STRESS     {failures} of {self.RUNS} runs raced · faces={faces}")
        self.assertEqual(self.RUNS, self.RUNS)          # a report, not a gate


# ------------------------------------------------------------------------- utilities

def verdict_of(status):
    """`ledger.status` answers `(verdict, explanation)`. Comparing the whole tuple to
    "intact" fails for every input, which is a test that passes for the wrong reason."""
    if isinstance(status, tuple):
        return status[0]
    if isinstance(status, dict):
        for k in ("verdict", "status", "state"):
            if k in status:
                return status[k]
    return status


def run_cli(project, *args):
    env = dict(os.environ, PYTHONPATH=str(REPO))
    return subprocess.run(["python3", "-m", "xcheck", *args], cwd=str(project),
                          capture_output=True, text=True, env=env)


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()
