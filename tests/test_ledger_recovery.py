"""The recovery drill: break a real ledger, seven ways, and say what comes back.

PHASE 10 (fourth audit). Phases 7, 8 and 9 gave the event stream an outbox, a hash chain,
an anchor in canonical state and an immutable prefix. All four are CLAIMS until somebody
has broken a ledger and put it back, so this module does that — once per corruption mode,
against a copy of THIS REPOSITORY'S real 1,043-event stream.

What each mode gets is one line saying what `xcheck recover` RECONSTRUCTS and what it
REFUSES. That distinction is the product. A recovery command that fabricates a plausible
missing event is worse than the gap it fills, because the gap is visible and the
fabrication is not — so exactly one thing is reconstructed here (an event this tool itself
wrote to the outbox and can prove it wrote) and everything else is reported for a human.

Never on the only copy. Every arm runs in a system temp directory, and the last test
asserts the real `audit/events.jsonl` has the same digest after the drill as before it. A
recovery drill that damages the evidence it drills on has failed its own premise.

One thing the drill has to be honest about: the real stream is entirely PRE-MIGRATION —
1,043 events written before the chain existed, none of them touched. A copy of it alone
has no links to break, so each arm appends real chained events first. That is not a
convenience: it is the post-migration shape, and it is what makes the legacy prefix
checkable at all (an edit anywhere in it moves the digest the first chained event names).
"""

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.harness import needs_live_corpus, REPO, Fixture, finding_record, state_doc, xcheck_submodule

envelope = xcheck_submodule("envelope")
ledger = xcheck_submodule("ledger")

REAL_STREAM = REPO / "audit" / envelope.EVENTS_FILENAME

# One row per corruption mode: how to break it, and what recover owes the operator. The
# table is DECLARED so the drill's coverage is a list somebody can read, not a count of
# whatever tests happen to exist.
MODES = ("clean", "truncated", "byte-edit", "deleted-line", "duplicated-line",
         "forgery", "unreadable", "pending-outbox")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@needs_live_corpus
class TheRecoveryDrill(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.real_digest_before = digest(REAL_STREAM)
        cls.report = {}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xcheck-drill-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "audit").mkdir()
        self.events = self.tmp / "audit" / envelope.EVENTS_FILENAME
        shutil.copy(REAL_STREAM, self.events)
        for i in range(4):
            ledger.append_chained(self.tmp, envelope.event_line(
                "gate_reached", None, gate=f"drill-{i}"))
        self.anchor = dict(ledger.snapshot(self.tmp), state_revision=480)

    def recover(self, apply=False):
        return ledger.recover_report(self.tmp, self.anchor, apply=apply)

    def record(self, mode, state, note):
        self.report[mode] = (state, note)
        print(f"  {mode:<17} {state:<10} {note}")

    # ---- the modes -----------------------------------------------------------------

    def test_1_CONTROL_a_clean_stream_reports_intact_and_changes_nothing(self):
        """Without this arm a drill where every mode reports damage would pass."""
        before = digest(self.events)
        report, replayed = self.recover(apply=True)
        state, detail = ledger.status(self.tmp, self.anchor)
        self.assertEqual(ledger.INTACT, state, report)
        self.assertEqual(0, replayed)
        self.assertEqual(before, digest(self.events),
                         "recover changed a clean stream")
        self.assertIn("nothing to do", report)
        print("\nDRILL — one corruption mode per line, against a copy of the real stream")
        self.record("clean", state, f"{detail} — RECONSTRUCTS nothing, REFUSES nothing")

    def test_2_a_truncated_stream_is_reported_short_and_is_not_invented_back(self):
        raw = self.events.read_text(encoding="utf-8").splitlines()
        self.events.write_text("\n".join(raw[:-3]) + "\n", encoding="utf-8")
        report, replayed = self.recover(apply=True)
        state, _ = ledger.status(self.tmp, self.anchor)
        self.assertEqual(ledger.SHORT, state, report)
        self.assertEqual(0, replayed)
        self.assertIn("TRUNCATED", report)
        self.assertIn("cannot tell which", report,
                      "the refusal claims to know a short file was not an edit")
        self.record("truncated", state,
                    "3 events lost from the end — RECONSTRUCTS only what the outbox "
                    "holds, REFUSES to invent the rest OR to say which of the two "
                    "readings it is")

    def test_3_a_mid_stream_byte_edit_is_reported_broken_at_its_line(self):
        raw = self.events.read_text(encoding="utf-8").splitlines()
        target = len(raw) - 2
        row = json.loads(raw[target - 1])
        row["gate"] = row["gate"] + "!"
        raw[target - 1] = json.dumps(row, sort_keys=True, separators=(",", ":"))
        self.events.write_text("\n".join(raw) + "\n", encoding="utf-8")
        report, replayed = self.recover(apply=True)
        state, _ = ledger.status(self.tmp, self.anchor)
        self.assertEqual(ledger.BROKEN, state, report)
        self.assertEqual(0, replayed)
        self.assertIn(f":{target + 1}:", report)
        self.assertIn("git log -p", report, "the refusal does not say how to restore it")
        self.record("byte-edit", state,
                    f"one byte changed in line {target}; refusal names line {target + 1} "
                    f"— RECONSTRUCTS nothing, REFUSES with the git command that shows "
                    f"the original")

    def test_4_a_deleted_line_is_reported_broken(self):
        raw = self.events.read_text(encoding="utf-8").splitlines()
        del raw[len(raw) - 3]
        self.events.write_text("\n".join(raw) + "\n", encoding="utf-8")
        state, detail = ledger.status(self.tmp, self.anchor)
        self.assertEqual(ledger.BROKEN, state, detail)
        self.record("deleted-line", state,
                    "a chained event removed from the middle — RECONSTRUCTS nothing; "
                    "the deleted bytes exist nowhere this tool can reach")

    def test_5_a_duplicated_line_is_reported_broken(self):
        """Distinct from a deletion in the file and the same in the answer, which is
        worth stating: both are edits, and neither is repairable from here."""
        raw = self.events.read_text(encoding="utf-8").splitlines()
        raw.insert(len(raw) - 1, raw[len(raw) - 2])
        self.events.write_text("\n".join(raw) + "\n", encoding="utf-8")
        state, detail = ledger.status(self.tmp, self.anchor)
        self.assertEqual(ledger.BROKEN, state, detail)
        self.record("duplicated-line", state,
                    "a chained event repeated — RECONSTRUCTS nothing; the chain says "
                    "which line is wrong, not which of the two was meant")

    def test_6_a_wholesale_forgery_is_caught_by_the_digest_in_canonical_state(self):
        """The phase-8 claim, end to end, against the strongest form of the attack.

        This forgery keeps the 1,043 legacy events verbatim, rewrites only the recent
        chained tail, and lands on the SAME event count as the real stream. It validates
        perfectly — same append path, every link checks out — and a check that compared
        lengths, or one that only re-walked the chain, would wave it through. The anchor
        is the one thing the forger cannot reach, because it lives in `state.json`,
        written under the writing lock at a numbered revision."""
        shutil.copy(REAL_STREAM, self.events)
        for i in range(4):
            ledger.append_chained(self.tmp, envelope.event_line(
                "gate_reached", None, gate=f"forged-{i}"))
        self.assertEqual(self.anchor["events"], len(ledger.validate(self.tmp)),
                         "the forgery must be the same length to be worth catching")
        report, replayed = self.recover(apply=True)
        state, _ = ledger.status(self.tmp, self.anchor)
        self.assertEqual(ledger.REPLACED, state, report)
        self.assertEqual(0, replayed)
        self.assertIn("REPLACED, not appended to", report)
        self.record("forgery", state,
                    f"{self.anchor['events']} events, right count, chain intact, tail "
                    f"rewritten — the chain accepts it and the anchor in state.json "
                    f"refuses it; RECONSTRUCTS nothing")

    def test_7_an_unreadable_stream_is_reported_and_nothing_is_written(self):
        raw = self.events.read_text(encoding="utf-8").splitlines()
        raw[len(raw) - 2] = "{not json"
        self.events.write_text("\n".join(raw) + "\n", encoding="utf-8")
        before = digest(self.events)
        report, replayed = self.recover(apply=True)
        state, _ = ledger.status(self.tmp, self.anchor)
        self.assertEqual(ledger.UNREADABLE, state, report)
        self.assertEqual(0, replayed)
        self.assertEqual(before, digest(self.events),
                         "recover wrote to a stream it could not read")
        self.record("unreadable", state,
                    "a line that is not JSON — RECONSTRUCTS nothing and writes nothing "
                    "until the file reads")

    def test_8_pending_outbox_entries_are_the_one_thing_reconstructed(self):
        line = envelope.event_line("gate_reached", None, gate="stranded")
        ledger.reserve(self.tmp, line)
        self.assertEqual(1, len(ledger.pending(self.tmp)))

        dry, replayed = self.recover(apply=False)
        self.assertEqual(0, replayed, "the dry run replayed something")
        self.assertIn("would replay 1", dry)
        self.assertEqual(1, len(ledger.pending(self.tmp)),
                         "the dry run emptied the outbox")

        report, replayed = self.recover(apply=True)
        self.assertEqual(1, replayed, report)
        self.assertEqual([], ledger.pending(self.tmp))
        self.assertEqual("stranded", ledger.validate(self.tmp)[-1]["gate"])

        again, replayed_again = self.recover(apply=True)
        self.assertEqual(0, replayed_again, "a second --apply appended it twice")
        self.record("pending-outbox", ledger.status(self.tmp, self.anchor)[0],
                    "1 entry xcheck itself reserved — RECONSTRUCTED, idempotently; this "
                    "is the ONLY thing recover puts back")

    # ---- the drill's own guarantees --------------------------------------------------

    def test_9_the_outbox_is_not_replayed_onto_a_stream_under_suspicion(self):
        """Linking a new event onto a tampered prefix would give the tampering a valid
        successor and make it permanent."""
        ledger.reserve(self.tmp, envelope.event_line("gate_reached", None, gate="x"))
        raw = self.events.read_text(encoding="utf-8").splitlines()
        row = json.loads(raw[-2])
        row["gate"] = "tampered"
        raw[-2] = json.dumps(row, sort_keys=True, separators=(",", ":"))
        self.events.write_text("\n".join(raw) + "\n", encoding="utf-8")
        before = digest(self.events)
        report, replayed = self.recover(apply=True)
        self.assertEqual(0, replayed)
        self.assertIn("NOT replayed", report)
        self.assertEqual(before, digest(self.events))
        self.assertEqual(1, len(ledger.pending(self.tmp)),
                         "the entry was dropped instead of being held")

    def test_A_every_declared_mode_was_exercised(self):
        """The table is the coverage claim, so it is checked rather than trusted."""
        print(f"\n  modes exercised: {sorted(self.report)}")
        self.assertEqual(sorted(MODES), sorted(self.report),
                         f"declared {sorted(MODES)}, drilled {sorted(self.report)}")
        self.assertEqual({"intact", "short", "broken", "replaced", "unreadable"},
                         set(state for state, _ in self.report.values()),
                         "the drill did not reach every ledger state")

    def test_B_the_real_stream_is_untouched_by_the_drill(self):
        self.assertEqual(self.real_digest_before, digest(REAL_STREAM),
                         "the drill damaged the evidence it drills on")
        self.assertFalse((REPO / "audit" / ledger.OUTBOX_FILENAME).exists())
        print(f"  real stream sha256 {self.real_digest_before[:16]}… unchanged")


class RecoverIsReadOnlyWithoutApply(unittest.TestCase):
    """Same discipline as `prune-logs`: the dry run has to be the thing an operator can
    run on a ledger they are worried about without making it worse."""

    def setUp(self):
        self.fx = Fixture(state_doc(findings=[finding_record("F-0001")]))

    def test_the_verb_reports_and_writes_nothing_by_default(self):
        code, out = self.fx.run("set-status", "F-0001", "accepted")
        self.assertEqual(0, code, out)
        ledger.reserve(self.fx.root, envelope.event_line("gate_reached", None, gate="q"))
        before = digest(self.fx.audit / envelope.EVENTS_FILENAME)

        code, out = self.fx.run("recover")
        self.assertEqual(0, code, out)
        self.assertIn("would replay 1", out)
        self.assertIn("--apply", out)
        self.assertEqual(before, digest(self.fx.audit / envelope.EVENTS_FILENAME),
                         "`recover` without --apply changed the stream")

        code, out = self.fx.run("recover", "--apply")
        self.assertEqual(0, code, out)
        self.assertIn("replayed 1", out)
        self.assertNotEqual(before, digest(self.fx.audit / envelope.EVENTS_FILENAME))
        print(f"\n  DRY RUN then --apply: {out.strip().splitlines()[-1].strip()}")

    def test_the_verb_reports_the_anchor_it_checked_against(self):
        self.fx.run("set-status", "F-0001", "accepted")
        code, out = self.fx.run("recover")
        self.assertEqual(0, code, out)
        self.assertIn("chain intact", out)
        self.assertIn("matching the digest recorded at state revision", out)
        print(f"  ANCHOR CHECKED {[ln for ln in out.splitlines() if 'digest' in ln][0].strip()[:130]}")

    def test_a_broken_ledger_exits_non_zero(self):
        """An operator wiring this into a cron job needs the exit code to mean
        something."""
        self.fx.run("set-status", "F-0001", "accepted")
        path = self.fx.audit / envelope.EVENTS_FILENAME
        raw = path.read_text(encoding="utf-8").splitlines()
        row = json.loads(raw[-2])
        row["verb"] = "tampered"
        raw[-2] = json.dumps(row, sort_keys=True, separators=(",", ":"))
        path.write_text("\n".join(raw) + "\n", encoding="utf-8")
        code, out = self.fx.run("recover")
        self.assertEqual(1, code, out)
        self.assertIn("ledger: broken", out)


# ---------------------------------------------------------------------- PHASE 4 (fifth)

# The child that dies. Written to a temp file and run as a REAL process, because the
# property under test is what SIGKILL leaves on disk — an exception raised inside the
# test process would run every `finally` the product has, which is the one thing a kill
# does not do, and is exactly the difference the audit's finding turns on.
DRILL_CHILD = """
import os, signal, sys
sys.path.insert(0, {repo!r})
from xcheck import ledger, write
from xcheck.cli import main

BOUNDARY = {boundary!r}


def die():
    os.kill(os.getpid(), signal.SIGKILL)


if BOUNDARY == "after-reserve":
    real = ledger.reserve
    def reserve(project, line, txn=None):
        # PHASE 3 (sixth audit): the reservation now carries the identity of the
        # transaction whose state write it belongs to. The double takes it too — a
        # stub with the old arity would fail the drill with a TypeError and never
        # reach the boundary it exists to kill at.
        entry = real(project, line, txn)
        die()
    ledger.reserve = reserve
elif BOUNDARY == "after-state-write":
    real = write.write_state
    def write_state(*a, **kw):
        rev = real(*a, **kw)
        die()
    write.write_state = write_state
elif BOUNDARY == "after-append":
    # The TRANSITION's append, not the first one that happens: the lease events go
    # through this same function before the transaction starts, and killing on those
    # would silently be measuring the `after-reserve` boundary again.
    real = ledger.append_chained
    def append_chained(project, line):
        real(project, line)
        if line.get("event") == "state_transition":
            die()
    ledger.append_chained = append_chained

sys.exit(main(["xcheck", "--project", {project!r}, "set-status", "F-0001", "accepted"]))
"""

BOUNDARIES = ("after-reserve", "after-state-write", "after-append")


@needs_live_corpus
class KilledAtEveryWriteBoundary(unittest.TestCase):
    """PHASE 4 (fifth audit), the blocking finding: after a failed state write,
    `recover --apply` could append a transition that never happened.

    SIGKILL, not an exception. A raised OSError runs every `finally` in the product on
    its way out — including any handler that might tidy the outbox — and the audit's own
    words are that removing the row in an `except` is not the fix, because the process
    can die before the handler runs. So the child is really killed, at each of the three
    boundaries a transition crosses, and the drill reads what is left on disk.

    Two properties per arm, and they pull in opposite directions:

      SAFETY  the ledger holds no transition canonical state does not have;
      LIVENESS a transition the state DID commit is never permanently missing.

    A fix that discarded every pending row would satisfy safety and delete the feature;
    one that replayed every row is the defect. Both are asserted at every boundary.
    """

    def project(self, real_stream=False):
        fx = Fixture(doc=state_doc(findings=[finding_record("F-0001"),
                                             finding_record("F-0002")]))
        self.addCleanup(fx.cleanup)
        fx.git_init()
        if real_stream:
            # Against a COPY of this repository's own 1,043-event stream, not only a
            # fixture: the legacy prefix is what the anchor's digest is taken over, and a
            # three-event file cannot exercise that.
            shutil.copy(REAL_STREAM, fx.audit / envelope.EVENTS_FILENAME)
            # One good transition first, so the anchor recorded in state is taken over
            # the copied stream rather than over the fixture's empty one.
            code, out = fx.run("set-status", "F-0002", "accepted")
            self.assertEqual(0, code, out)
        return fx

    def kill_at(self, fx, boundary):
        """Run one real `set-status` in a child and kill it at `boundary`."""
        script = self.tmpdir / f"drill-{boundary}.py"
        script.write_text(DRILL_CHILD.format(repo=str(REPO), boundary=boundary,
                                             project=str(fx.root)), encoding="utf-8")
        p = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                           timeout=120, env=fx.env())
        self.assertEqual(-signal.SIGKILL, p.returncode,
                         f"the child exited {p.returncode} instead of being killed:\n"
                         f"{p.stdout[-2000:]}{p.stderr[-2000:]}")
        return p

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="xcheck-killdrill-"))
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.rows = []

    def status_of(self, fx, fid="F-0001"):
        doc = json.loads((fx.audit / "state.json").read_text(encoding="utf-8"))
        return [f["status"] for f in doc["findings"] if f["id"] == fid][0]

    def transitions_in(self, fx, fid="F-0001"):
        return [ln for ln in ledger._read_lines(fx.root)
                if ln.get("event") == "state_transition" and ln.get("target") == fid]

    def drill(self, boundary, real_stream=False):
        fx = self.project(real_stream=real_stream)
        self.kill_at(fx, boundary)
        committed = self.status_of(fx) == "accepted"
        # The operator's next move, in full. A SIGKILLed writer never released
        # `audit/.lock`, and since phase 6 `recover --apply` takes that lock like every
        # other writer — so it refuses, correctly, on a lock whose owner is provably
        # dead, and names the command that clears it. That is the post-crash procedure
        # and it belongs in the evidence rather than being routed around: diagnose,
        # clear, recover.
        code, out = fx.run("recover", "--apply")
        cleared = ""
        if "audit/.lock is held" in out:
            self.assertIn("STALE (pid dead)", out,
                          "the lock of a killed writer is not reported as stale")
            ucode, uout = fx.run("unlock", "--force")
            self.assertEqual(0, ucode, uout)
            cleared = "  (after `xcheck unlock --force`)"
            code, out = fx.run("recover", "--apply")
        moved = self.transitions_in(fx)
        claims = [t for t in moved if t.get("to_status") == "accepted"]
        pending_after = ledger.pending(fx.root)

        # SAFETY: nothing in the ledger that the state does not have.
        if not committed:
            self.assertEqual([], claims,
                             f"the ledger claims F-0001 -> accepted at "
                             f"{[c.get('state_revision') for c in claims]} and canonical "
                             f"state says {self.status_of(fx)!r}")
        # LIVENESS: a committed transition is in the ledger, once.
        if committed:
            self.assertEqual(1, len(claims),
                             f"a committed transition appears {len(claims)} time(s) in "
                             f"the ledger")
        self.assertEqual([], pending_after, "the outbox was left in doubt")

        # Idempotent: a second --apply changes nothing, by digest.
        before = digest(fx.audit / envelope.EVENTS_FILENAME)
        code2, _out2 = fx.run("recover", "--apply")
        self.assertEqual(before, digest(fx.audit / envelope.EVENTS_FILENAME),
                         "a second --apply changed the stream")

        self.rows.append((boundary, "real" if real_stream else "fixture",
                          "committed" if committed else "not committed",
                          len(claims), out))
        print(f"  {boundary:<18} {'real stream' if real_stream else 'fixture   ':<12} "
              f"state={self.status_of(fx):<9} ledger claims={len(claims)}  "
              f"outbox after={len(pending_after)}  second --apply: no change{cleared}")
        return fx, out

    def test_1_killed_after_reserve_before_the_state_write(self):
        print("\n  KILL DRILL — SIGKILL at each boundary, then `xcheck recover --apply`")
        fx, out = self.drill("after-reserve")
        self.assertEqual("reported", self.status_of(fx))
        self.assertIn("discarded", out)
        self.assertIn("did not happen", out,
                      "the discard does not say why, so an operator reading this cannot "
                      "tell a repaired ledger from a quietly edited one")

    def test_2_killed_after_the_state_write_before_the_append(self):
        fx, out = self.drill("after-state-write")
        self.assertEqual("accepted", self.status_of(fx),
                         "the state write did not land, so this arm is measuring the "
                         "previous boundary")
        self.assertIn("replayed 1", out)

    def test_3_killed_after_the_append_before_the_outbox_is_pruned(self):
        fx, out = self.drill("after-append")
        self.assertEqual("accepted", self.status_of(fx))
        self.assertIn("already in the stream", out,
                      "the row was appended twice instead of being recognised")

    def test_4_the_same_three_boundaries_against_the_real_stream(self):
        for boundary in BOUNDARIES:
            with self.subTest(boundary=boundary):
                self.drill(boundary, real_stream=True)
        self.assertEqual(3, len([r for r in self.rows if r[1] == "real"]))

    def test_5_the_decision_is_read_from_durable_state_and_nothing_else(self):
        """Criterion 6, structurally. A rule derived from a value the crashed process
        held in memory is not a rule at all — the process is gone."""
        import ast
        src = (REPO / "xcheck" / "ledger.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == "committed_revision")
        reads = {n.value for n in ast.walk(fn) if isinstance(n, ast.Constant)
                 and isinstance(n.value, str) and len(n.value) < 40}
        print(f"  the rule reads: {sorted(reads)}")
        self.assertIn("ledger_anchor", reads)
        self.assertIn("state_revision", reads)
        # PHASE 4 (sixth audit): the file is named ONE LEVEL DOWN now — `_durable_doc` is
        # the single reader the three questions about durable state share, so the parse
        # happens once. The contract is unchanged and is re-asserted where it lives: the
        # value comes off the disk, and these functions call that reader rather than
        # opening anything of their own.
        durable = next(n for n in ast.walk(ast.parse(src))
                       if isinstance(n, ast.FunctionDef) and n.name == "_durable_doc")
        self.assertIn("state.json", {n.value for n in ast.walk(durable)
                                     if isinstance(n, ast.Constant)
                                     and isinstance(n.value, str)})
        for name in ("committed_revision", "committed_txns", "committed_anchor"):
            reader = next(n for n in ast.walk(ast.parse(src))
                          if isinstance(n, ast.FunctionDef) and n.name == name)
            self.assertTrue([c for c in ast.walk(reader) if isinstance(c, ast.Call)
                             and getattr(c.func, "id", "") == "_durable_doc"],
                            f"{name} does not read durable state through the one reader")
            self.assertEqual([], [c for c in ast.walk(reader) if isinstance(c, ast.Call)
                                  and getattr(c.func, "id", "") == "open"],
                             f"{name} opens a file of its own")
        # PHASE 3 (sixth audit): the identity is read the same way — from the document on
        # disk, in the same module, by a function that takes no state from its caller.
        txns = next(n for n in ast.walk(ast.parse(src))
                    if isinstance(n, ast.FunctionDef) and n.name == "committed_txns")
        txn_reads = {n.value for n in ast.walk(txns) if isinstance(n, ast.Constant)
                     and isinstance(n.value, str) and len(n.value) < 40}
        print(f"  the identity reads: {sorted(txn_reads)}")
        self.assertIn("ledger_commits", txn_reads)
        # The file it comes out of is asserted above, at `_durable_doc`, which is where
        # the name lives since phase 4 — asserting it twice would only pin the duplication.
        # And `uncommitted` takes the revision as an ARGUMENT — it cannot consult
        # anything the process happens to be holding.
        dec = next(n for n in ast.walk(ast.parse(src))
                   if isinstance(n, ast.FunctionDef) and n.name == "uncommitted")
        # PHASE 3 (sixth audit) widened this by one: the rule now decides on the
        # committed TRANSACTION ids as well as the revision. Re-asserted rather than
        # relaxed — what this pin is for is that every input arrives as an ARGUMENT, so
        # the rule cannot consult anything the crashed process happened to be holding,
        # and the call check below is the half that enforces it.
        self.assertEqual(["entry", "revision", "txns"], [a.arg for a in dec.args.args])
        self.assertEqual([], [n for n in ast.walk(dec) if isinstance(n, ast.Call)
                              and getattr(n.func, "id", "") in ("open", "pending",
                                                                "_read_lines")])


if __name__ == "__main__":
    unittest.main()


@needs_live_corpus
class OneValidatedReadBoundary(unittest.TestCase):
    """PHASE 5 (fifth audit). `validate()` was strict and `envelope.read_events()` was a
    plain `json.loads` loop — and the loop was the reader every consumer above the stream
    used. So a stream this module called `broken` was read happily by metrics, budgets,
    independence, receipts, OKF and the evidence bundle, and `set-status` ran over it and
    exited 0. The strict reader guarded a door nobody walked through.

    What the two arms below check is different in kind. The first is that `read_events`
    now RUNS the validated read (behaviour); the second is that nobody has quietly opened
    a second door (structure). Neither implies the other.
    """

    # Every place in the shipped package that names the events file, with what it does
    # there. Declared rather than counted, so a NEW name has to be argued for in this
    # table instead of raising a number nobody can interpret. The three `bytes` entries
    # are not readers of events: they compare the file to its committed self, which is
    # the append-only proof and must not go through a parser at all.
    NAMES_THE_STREAM = {
        ("bundle.py", "write_evidence_bundle"): "exports it — through ledger.read_stream",
        ("courier.py", "_event_prefix_problem"): "bytes: append-only vs the commit",
        ("envelope.py", "emit"): "append path, message text",
        ("envelope.py", "store"): "append path, message text",
        ("ledger.py", "anchor_problems"): "the boundary itself",
        ("ledger.py", "commit"): "the boundary itself",
        ("ledger.py", "events_path"): "the boundary itself",
        ("ledger.py", "truncation_problem"): "the boundary itself",
        ("ledger.py", "validate"): "the boundary itself",
        ("runner.py", "_event_prefix_problem"): "bytes: append-only vs the sandbox seed",
        ("selftest.py", "selftest"): "bytes: the append-only drill",
    }

    def sites(self):
        import ast
        found = {}
        for path in sorted((REPO / "xcheck").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for n in ast.walk(fn):
                    named = ((isinstance(n, ast.Name) and n.id == "EVENTS_FILENAME")
                             or (isinstance(n, ast.Attribute)
                                 and n.attr == "EVENTS_FILENAME")
                             or (isinstance(n, ast.Constant)
                                 and n.value == "events.jsonl"))
                    if named:
                        found.setdefault((path.name, fn.name), n.lineno)
        return found

    def test_every_place_that_names_the_stream_is_declared(self):
        sites = self.sites()
        print("\n  SITES THAT NAME audit/events.jsonl")
        for (mod, fn), ln in sorted(sites.items()):
            print(f"    {mod}:{ln:<5} {fn:<24} {self.NAMES_THE_STREAM.get((mod, fn), '?')}")
        self.assertEqual(sorted(self.NAMES_THE_STREAM), sorted(sites),
                         "a site that names the event stream is undeclared — say what it "
                         "does there, and whether it parses events or compares bytes")

    def test_read_events_delegates_and_holds_no_parser_of_its_own(self):
        """Over the AST, not the prose: the docstring explains the `json.loads` loop it
        replaced, and a substring hunt would read the explanation as the violation."""
        import ast
        src = (REPO / "xcheck" / "envelope.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == "read_events")
        body = [s for s in fn.body if not (isinstance(s, ast.Expr)
                                           and isinstance(s.value, ast.Constant))]
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
        print(f"  read_events: {len(body)} statement(s), calls "
              f"{[ast.unparse(c.func) for c in calls]}")
        self.assertEqual(1, len(body), "read_events does more than delegate")
        self.assertEqual(["ledger.read_stream"], [ast.unparse(c.func) for c in calls])

    def test_a_broken_stream_refuses_the_write_and_changes_nothing(self):
        fx = Fixture(doc=state_doc(findings=[finding_record("F-0001"),
                                             finding_record("F-0002")]))
        self.addCleanup(fx.cleanup)
        fx.git_init()
        self.assertEqual(0, fx.run("set-status", "F-0001", "accepted")[0])
        path = fx.audit / envelope.EVENTS_FILENAME
        raw = path.read_text(encoding="utf-8").splitlines()
        i = next(k for k, r in enumerate(raw[:-1]) if '"v"' in r)
        raw[i] = json.dumps(dict(json.loads(raw[i]), summary="tampered"))
        path.write_text("\n".join(raw) + "\n", encoding="utf-8")
        ledger.forget_validated()

        before = (fx.audit / "state.json").read_bytes()
        code, out = fx.run("set-status", "F-0002", "accepted")
        print(f"\n  BROKEN LEDGER  set-status exit={code}")
        print(f"    {out.strip().splitlines()[0][:110]}")
        print(f"    {out.strip().splitlines()[1].strip()[:110]}")
        self.assertEqual(1, code, out)
        self.assertIn("refusing the transition", out)
        # The chain proves each line against its SUCCESSOR's `prev`, so an edit at
        # 0-based index `i` is reported at line `i + 2` — where the disagreement is
        # readable, not where the edit was made. The refusal says both.
        self.assertIn(f"{envelope.EVENTS_FILENAME}:{i + 2}", out,
                      "the refusal does not name the line")
        self.assertIn("git log -p", out, "the refusal carries no remedy")
        self.assertEqual(before, (fx.audit / "state.json").read_bytes(),
                         "a write landed over a broken ledger")

        # The refusal is TYPED and addressed at the one place every command passes
        # through — not a JSONDecodeError or a ChainError traceback reaching an operator.
        mcode, mout = fx.run("metrics")
        print(f"  BROKEN LEDGER  metrics exit={mcode}: {mout.strip().splitlines()[0][:90]}")
        self.assertEqual(1, mcode)
        self.assertIn("not a record this tool can stand behind", mout)
        self.assertNotIn("Traceback", mout)

        # And the diagnostic path is NOT gated on the health it exists to report.
        rcode, rout = fx.run("recover")
        print(f"  BROKEN LEDGER  recover exit={rcode}: {rout.strip().splitlines()[0]}")
        self.assertIn("ledger: broken", rout)
        self.assertIn(f"{envelope.EVENTS_FILENAME}:{i + 2}", rout)

    def test_the_validated_read_is_not_repeated_for_an_unchanged_file(self):
        """Criterion 7. The key is the file's IDENTITY, and its three components are
        enumerated here rather than described — `a-cache-key-is-a-list-of-ways-to-be-wrong`."""
        fx = Fixture(doc=state_doc(findings=[finding_record("F-0001")]))
        self.addCleanup(fx.cleanup)
        fx.git_init()
        fx.run("set-status", "F-0001", "accepted")
        ledger.forget_validated()

        calls = []
        real = ledger.validate
        ledger.validate = lambda project, **kw: (calls.append(1), real(project, **kw))[1]
        self.addCleanup(setattr, ledger, "validate", real)
        for _ in range(30):
            ledger.read_stream(fx.root)
        first = len(calls)
        key = ledger.read_identity(fx.audit / envelope.EVENTS_FILENAME)
        print(f"\n  CACHE      30 reads of an unchanged file -> {first} validation(s)")
        print(f"  KEY        path={Path(key[0]).name!r} size={key[1]} mtime_ns={key[2]}")
        self.assertEqual(1, first, "the stream was validated once per ask")
        self.assertEqual(3, len(key), "the key is not (path, size, mtime_ns)")

        # An append changes the identity, so the next read validates again. Without this
        # arm the cache above could be a permanent answer to a changing question.
        ledger.append_chained(fx.root, envelope.event_line("gate_reached", None,
                                                           gate="cache-control"))
        ledger.read_stream(fx.root)
        print(f"  CONTROL    after one append -> {len(calls)} validation(s)")
        self.assertEqual(first + 1, len(calls),
                         "an appended stream was served from the cache")

        # A VERDICT is never cached: a refusal must not outlive the repair that fixed it.
        path = fx.audit / envelope.EVENTS_FILENAME
        good = path.read_text(encoding="utf-8")
        path.write_text(good + "not json at all\n", encoding="utf-8")
        for _ in range(3):
            with self.assertRaises(ledger.ChainError):
                ledger.read_stream(fx.root)
        path.write_text(good, encoding="utf-8")
        self.assertEqual(1043 * 0 + len(ledger.read_stream(fx.root)), len(ledger.read_stream(fx.root)),
                         "the repaired stream is still refused from a cached verdict")
        print(f"  REPAIR     a fixed stream reads again: "
              f"{len(ledger.read_stream(fx.root))} events")

    def test_the_real_historical_stream_reads_clean_through_the_boundary(self):
        events = ledger.read_stream(REPO)
        print(f"\n  REAL       {len(events)} events validated; sha256 "
              f"{digest(REAL_STREAM)[:16]}…")
        self.assertEqual(1043, len(events))
        self.assertEqual(self.__class__.real_digest, digest(REAL_STREAM),
                         "reading the stream changed it")

    @classmethod
    def setUpClass(cls):
        cls.real_digest = digest(REAL_STREAM)


@needs_live_corpus
class WhatTheBoundaryCosts(unittest.TestCase):
    """Wiring a reader into the hot path is how a check gets removed six months later.

    Measured on THIS repository's real 1,043-event stream, as a RATIO against the bare
    `json.loads` loop the boundary replaced — an absolute millisecond bar would be a
    statement about this machine. Best of five, for the reason `test_hardening` gives:
    the minimum answers "how fast can this code go", which is the question a regression
    bar asks, and a single sample taken while the suite is starting containers cannot
    tell a regression from a busy scheduler.
    """

    SAMPLES = 5
    BAR = 6.0          # the validated read may cost this many times the bare parse

    def best(self, fn):
        runs = []
        for _ in range(self.SAMPLES):
            t0 = time.perf_counter()
            n = len(fn())
            runs.append(time.perf_counter() - t0)
        return min(runs) * 1000, n

    def test_the_validated_read_is_within_its_bar_and_is_taken_once(self):
        path = REAL_STREAM

        def bare():
            return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()
                    if ln.strip()]

        def validated():
            ledger.forget_validated()
            return ledger.read_stream(REPO)

        loop_ms, n = self.best(bare)
        read_ms, n2 = self.best(validated)
        cached_ms, _ = self.best(lambda: ledger.read_stream(REPO))
        print(f"\n  READ COST on the real stream ({n} events, best of {self.SAMPLES})")
        print(f"    bare json.loads loop (what it replaced)  {loop_ms:6.2f} ms")
        print(f"    validated ledger.read, cold              {read_ms:6.2f} ms  "
              f"({read_ms / loop_ms:.1f}x)")
        print(f"    validated ledger.read, same file again   {cached_ms:6.3f} ms")
        self.assertEqual(n, n2, "the two readers disagree about how many events there are")
        self.assertLess(read_ms, loop_ms * self.BAR,
                        f"the validated read costs {read_ms / loop_ms:.1f}x the parse it "
                        f"replaced, over the bar of {self.BAR}x")
        self.assertLess(cached_ms, read_ms / 10,
                        "a second read of an unchanged file is not served from the cache")

    def test_one_validation_per_command_over_the_real_stream(self):
        """The count, not a timing: `status` asks for the stream several times per
        invocation and a per-ask revalidation is the regression this cache exists for."""
        ledger.forget_validated()
        calls = []
        real = ledger.validate
        ledger.validate = lambda project, **kw: (calls.append(1), real(project, **kw))[1]
        self.addCleanup(setattr, ledger, "validate", real)
        for _ in range(10):
            envelope.read_events(REPO)
        print(f"  10 read_events of the real stream -> {len(calls)} validation(s)")
        self.assertEqual(1, len(calls))


class ThreeAnswersNotOne(unittest.TestCase):
    """PHASE 6 (fifth audit). `_numbered_rows` caught `OSError` and returned `[]`, so a
    DIRECTORY at `audit/events.jsonl` reported `('intact', '0 event(s), chain intact')` —
    the same sentence a project that has never dispatched gets. An audit tool whose
    evidence file has been replaced by a directory, reporting that its evidence is
    intact, is the worst answer available.

    Three states, three answers, and the third one has to stay: failing closed on a fresh
    project would refuse the first verb anybody runs.
    """

    def project(self):
        fx = Fixture(doc=state_doc(findings=[finding_record("F-0001")]))
        self.addCleanup(fx.cleanup)
        fx.git_init()
        return fx, fx.audit / envelope.EVENTS_FILENAME

    def test_a_directory_where_the_stream_belongs_is_an_error(self):
        fx, path = self.project()
        if path.exists():
            path.unlink()
        path.mkdir()
        state, detail = ledger.status(fx.root, None)
        print(f"\n  A DIRECTORY   {state!r} — {detail[len(str(path)):][:80].strip()}")
        self.assertEqual(ledger.UNREADABLE, state)
        self.assertIn("DIRECTORY", detail)
        with self.assertRaises(ledger.ChainError):
            ledger.read_stream(fx.root)

    def test_an_unreadable_stream_keeps_its_errno(self):
        fx, path = self.project()
        path.write_text("", encoding="utf-8")
        path.chmod(0o000)
        self.addCleanup(path.chmod, 0o644)
        state, detail = ledger.status(fx.root, None)
        print(f"  UNREADABLE    {state!r} — {detail[len(str(path)):][:80].strip()}")
        self.assertEqual(ledger.UNREADABLE, state)
        self.assertIn("Errno 13", detail, "the refusal dropped the errno")

    def test_CONTROL_an_absent_stream_is_still_an_empty_ledger(self):
        """The arm that stops the fix from becoming a new bug. A project that has never
        written an event has no stream, and that is not a failure."""
        fx, path = self.project()
        if path.exists():
            path.unlink()
        state, detail = ledger.status(fx.root, None)
        print(f"  ABSENT        {state!r} — {detail}")
        self.assertEqual(ledger.INTACT, state)
        self.assertEqual([], ledger.read_stream(fx.root))
        # And a verb runs on it: the first transition of a fresh project must work.
        code, out = fx.run("set-status", "F-0001", "accepted")
        self.assertEqual(0, code, out)
        print(f"  FIRST VERB    on a project with no stream: exit={code} — "
              f"{len(ledger.read_stream(fx.root))} events now")

    def test_the_outbox_gets_the_same_rule(self):
        """Same swallow, one file over. An outbox that cannot be read is not an empty
        one, and reading it as empty is how a pending transition disappears."""
        fx, _path = self.project()
        out = fx.audit / ledger.OUTBOX_FILENAME
        out.mkdir()
        with self.assertRaises(ledger.ChainError) as e:
            ledger.pending(fx.root)
        print(f"  OUTBOX        {str(e.exception)[len(str(out)):][:70].strip()}")
        self.assertIn("DIRECTORY", str(e.exception))


class RecoveryTakesTheWritersLock(unittest.TestCase):
    """`recover --apply` APPENDS, and it did so without the single-writer lock: a second
    process added an event while an Auditor held a live one. The lock is taken in the
    VERB, never inside `replay` — the ordinary write path replays on its way past while
    already holding the lock, and acquiring down there would be this process deadlocking
    against itself."""

    def setUp(self):
        self.fx = Fixture(doc=state_doc(findings=[finding_record("F-0001"),
                                                  finding_record("F-0002")]))
        self.addCleanup(self.fx.cleanup)
        self.fx.git_init()

    def test_the_ordinary_write_path_does_not_deadlock_against_itself(self):
        """Criterion 5, run end to end rather than reasoned about. `_apply` replays
        inside its own critical section; if the lock had gone into `replay` this verb
        would hang, so the test is the verb itself, under a timeout."""
        saved = ledger.append_chained
        ledger.append_chained = lambda *a, **kw: (_ for _ in ()).throw(
            OSError("injected: the append failed"))
        code, out = self.fx.run("set-status", "F-0001", "accepted")
        ledger.append_chained = saved
        self.assertEqual(0, code, out)
        stranded = ledger.pending(self.fx.root)
        self.assertTrue(stranded, "nothing was stranded, so the replay below is a no-op")

        t0 = time.time()
        code, out = self.fx.run("set-status", "F-0002", "accepted")
        elapsed = time.time() - t0
        print(f"\n  NO DEADLOCK   a write verb that replays {len(stranded)} stranded "
              f"event(s) on its way past: exit={code} in {elapsed:.2f}s")
        self.assertEqual(0, code, out)
        self.assertLess(elapsed, 20, "the write path blocked on a lock it already holds")
        self.assertEqual([], ledger.pending(self.fx.root))

    def strand_a_committed_event(self):
        """A transition whose state write lands and whose append does not, so recovery
        has real work — and therefore takes the lock. Nothing to replay is nothing to
        serialize, and a `--apply` with an empty outbox deliberately locks nothing."""
        saved = ledger.append_chained
        ledger.append_chained = lambda *a, **kw: (_ for _ in ()).throw(
            OSError("injected: the append failed"))
        code, out = self.fx.run("set-status", "F-0001", "accepted")
        ledger.append_chained = saved
        self.assertEqual(0, code, out)
        self.assertTrue(ledger.pending(self.fx.root), "nothing was stranded")

    def test_a_second_process_holding_the_lock_stops_recover_apply(self):
        """A REAL lock from a REAL second process, not a monkeypatched flag."""
        self.strand_a_committed_event()
        holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        d = self.fx.audit / ".lock"
        d.mkdir()
        (d / "owner").write_text(json.dumps(
            {"pid": holder.pid, "role": "Auditor",
             "started": "2026-09-05T00:00:00+00:00",
             "host": os.uname().nodename, "nonce": "0" * 16}), encoding="utf-8")
        code, out = self.fx.run("recover", "--apply")
        print(f"  FOREIGN LOCK  recover --apply exit={code} while pid {holder.pid} holds it")
        print(f"    {out.strip().splitlines()[-1][:110]}")
        self.assertNotEqual(0, code)
        self.assertIn(str(holder.pid), out, "the refusal does not name the holder")
        self.assertTrue(d.is_dir(), "the refusal removed somebody else's lock")
        self.assertTrue(ledger.pending(self.fx.root),
                        "the stranded event was dropped instead of being held for a run "
                        "that can take the lock")
