"""One durable transaction identity, and the two opposite errors a counter made.

PHASE 3 (sixth audit). Recovery decided whether to append a reserved event by comparing
the row's `state_revision` against a number read from canonical state. A number cannot
identify a transaction, and the audit found both errors that follow from pretending it
can:

  SCENARIO A  a session really finished, at revision 4. Its `session_finished` append
              failed and went to the outbox. `envelope.store` had bumped the revision
              WITHOUT updating `ledger_anchor`, so the rule compared 4 against a stale
              anchor and DELETED a real event.

  SCENARIO B  a `reported -> accepted` write failed before the state changed. A session
              whose command was `true` then bumped the revision through dispatch and
              finish. The number was now big enough, so the reservation was REPLAYED and
              the journal claimed a transition that never happened.

Both arms below reproduce the scenario first and then show it refused, and the
counterfactual reinstates the old rule to prove the new one is load-bearing rather than
decorative.
"""

import ast
import contextlib
import json
import os
import subprocess
import sys
import unittest

from tests.harness import needs_live_corpus, REPO, Fixture, finding_record, state_doc, xcheck_submodule

envelope = xcheck_submodule("envelope")
ledger = xcheck_submodule("ledger")
state_mod = xcheck_submodule("state")


def session_record(project, sid, command="true", outcome=None, log=None):
    """An envelope record built by the PRODUCT'S own constructors.

    `dispatch_record` and `finish` are what the dispatcher calls, and the §7 session
    schema is closed with every machine-meaningful field required — a hand-rolled dict
    is refused, and one tuned until it is accepted would be a fixture inventing the
    shape it wants. The command is `true` because that is the session the audit used to
    bump the revision past a reservation that never committed.
    """
    runner = xcheck_submodule("runner")
    rec = envelope.dispatch_record([command], "Auditor", "charter", "prompt",
                                   session_id=sid, profile=runner.PROFILES["worktree"],
                                   state_revision=envelope.current_revision(project),
                                   head_before=None)
    if outcome is None:
        return rec
    return envelope.finish(rec, 0, outcome, 1.0, None, str(log))


@contextlib.contextmanager
def append_fails():
    """The ledger append raises, exactly as a full disk does. The state write is not
    touched: this is the window between a durable transition and its event."""
    real = ledger.append_chained

    def boom(project, line):
        raise OSError(28, "No space left on device")

    ledger.append_chained = boom
    envelope.ledger.append_chained = boom
    try:
        yield
    finally:
        ledger.append_chained = real
        envelope.ledger.append_chained = real


@contextlib.contextmanager
def the_old_rule():
    """Recovery as it was before this phase: the row is honoured when the revision it
    names is not ahead of the one canonical state reports. Reinstated verbatim, so the
    counterfactual measures the RULE and not a hand-written approximation of it."""
    real = ledger.uncommitted

    def by_the_counter(entry, revision, txns=None):
        want = (entry.get("line") or {}).get("state_revision")
        if not isinstance(want, int):
            return None
        if revision is None:
            return ledger.Unproved(True, "canonical state is unreadable")
        if want <= revision:
            return None
        return ledger.Unproved(False, f"it names state revision {want} and canonical "
                                      f"state committed {revision}")

    ledger.uncommitted = by_the_counter
    try:
        yield
    finally:
        ledger.uncommitted = real


class ScenarioCase(unittest.TestCase):

    def project(self):
        fx = Fixture(doc=state_doc(findings=[finding_record("F-0001"),
                                             finding_record("F-0002")]))
        self.addCleanup(fx.cleanup)
        fx.git_init()
        return fx

    def anchor_of(self, fx):
        doc = json.loads((fx.audit / "state.json").read_text(encoding="utf-8"))
        anchor = doc.get("ledger_anchor") or {}
        return doc["state_revision"], anchor.get("state_revision"), \
            doc.get("ledger_commits", [])

    def events_of(self, fx, kind):
        return [ln for ln in ledger._read_lines(fx.root) if ln.get("event") == kind]

    def status_of(self, fx, fid="F-0001"):
        doc = json.loads((fx.audit / "state.json").read_text(encoding="utf-8"))
        return [f["status"] for f in doc["findings"] if f["id"] == fid][0]


class ScenarioA(ScenarioCase):
    """A real event, and a rule that erased it."""

    def finish_with_a_failed_append(self, fx):
        # One real transition FIRST, so `ledger_anchor` exists and names the revision
        # that write produced. Without it the anchor is absent, the old rule falls back
        # to the plain revision counter and this scenario does not arise at all — the
        # erasure needs an anchor that is STALE, which is precisely what `envelope.store`
        # bumping the revision without touching it produces.
        code, out = fx.run("set-status", "F-0002", "accepted")
        self.assertEqual(0, code, out)
        sid = "1" * 16
        log = fx.root / "session.log"
        log.write_text("XCHECK_TELEMETRY {}\n", encoding="utf-8")
        envelope.store(fx.root, session_record(fx.root, sid),
                       event="session_dispatched")
        with append_fails():
            rev = envelope.store(fx.root, session_record(fx.root, sid, outcome="ok",
                                                         log=log),
                                 event="session_finished")
        return sid, rev

    def test_a_finished_session_keeps_its_event(self):
        fx = self.project()
        sid, rev = self.finish_with_a_failed_append(fx)
        revision, anchor, commits = self.anchor_of(fx)
        pending = ledger.pending(fx.root)
        print(f"\n  A BEFORE   state revision {revision} · anchor "
              f"{anchor} · {len(pending)} row(s) pending · "
              f"session outcome={[s.get('outcome') for s in json.loads((fx.audit / 'state.json').read_text())['sessions']]}")
        self.assertEqual(1, len(pending), "the failed append did not reach the outbox")
        self.assertEqual([], self.events_of(fx, "session_finished"))
        self.assertIn(pending[0].get("txn"), commits,
                      "the row names a transaction canonical state does not confirm")

        appended, present, discarded = ledger.replay(fx.root)
        revision_after, anchor_after, _ = self.anchor_of(fx)
        print(f"  A AFTER    appended={appended} discarded={len(discarded)} · state "
              f"revision {revision_after} · anchor {anchor_after} · "
              f"{len(self.events_of(fx, 'session_finished'))} session_finished event(s)")
        self.assertEqual(1, appended, "a committed transition was not recovered")
        self.assertEqual([], discarded)
        self.assertEqual(1, len(self.events_of(fx, "session_finished")))
        self.assertEqual(rev, revision, "the store's revision is not the committed one")

    def test_COUNTERFACTUAL_the_old_rule_erases_it(self):
        """The same fixture, decided by the counter. This is the audit's finding."""
        fx = self.project()
        self.finish_with_a_failed_append(fx)
        with the_old_rule():
            appended, _present, discarded = ledger.replay(fx.root)
        print(f"  A OLD RULE appended={appended} discarded={len(discarded)} — "
              f"{discarded[0][:96] if discarded else ''}")
        self.assertEqual(0, appended)
        self.assertEqual(1, len(discarded),
                         "the old rule was supposed to delete this real event")
        self.assertEqual([], self.events_of(fx, "session_finished"))


class ScenarioB(ScenarioCase):
    """A transition that never happened, and a rule that replayed it."""

    def reserve_a_write_that_never_lands(self, fx):
        """Everything `write._apply` does up to the state write, and then nothing.

        The txn is minted and the row is reserved; the write that would have committed
        that identity raises. This is the process dying between reserve and replace.
        """
        revision = json.loads((fx.audit / "state.json").read_text())["state_revision"]
        line = envelope.event_line("state_transition", None, verb="set-status",
                                   target="F-0001", state_revision=revision + 1,
                                   from_status="reported", to_status="accepted",
                                   summary="a write that never landed")
        return ledger.reserve(fx.root, line, ledger.new_txn())

    def a_true_session_bumps_the_revision(self, fx):
        """The audit's own bumping writer: a session whose command is `true`."""
        sid = "2" * 16
        log = fx.root / "true.log"
        log.write_text("XCHECK_TELEMETRY {}\n", encoding="utf-8")
        envelope.store(fx.root, session_record(fx.root, sid, command="true"),
                       event="session_dispatched")
        return envelope.store(fx.root, session_record(fx.root, sid, command="true",
                                                      outcome="ok", log=log),
                              event="session_finished")

    def test_a_reservation_that_never_committed_is_discarded(self):
        fx = self.project()
        entry = self.reserve_a_write_that_never_lands(fx)
        before = self.anchor_of(fx)[0]
        rev = self.a_true_session_bumps_the_revision(fx)
        commits = self.anchor_of(fx)[2]
        print(f"\n  B BEFORE   reserved txn {entry['txn'][:8]} naming revision "
              f"{entry['line']['state_revision']} · `true` session moved the revision "
              f"{before} -> {rev} · status is still {self.status_of(fx)!r}")
        self.assertGreater(rev, entry["line"]["state_revision"],
                           "the bumping writer did not get the revision past the row")
        self.assertNotIn(entry["txn"], commits)

        appended, _present, discarded = ledger.replay(fx.root)
        claims = [ln for ln in self.events_of(fx, "state_transition")
                  if ln.get("to_status") == "accepted"]
        print(f"  B AFTER    appended={appended} discarded={len(discarded)} — "
              f"{discarded[0][:96] if discarded else ''}")
        print(f"  B AFTER    the journal claims {len(claims)} accepted transition(s); "
              f"the finding is {self.status_of(fx)!r}")
        self.assertEqual(0, appended, "a transition that never happened was replayed")
        self.assertEqual(1, len(discarded))
        self.assertEqual([], claims)

    def test_COUNTERFACTUAL_the_old_rule_replays_it(self):
        fx = self.project()
        self.reserve_a_write_that_never_lands(fx)
        self.a_true_session_bumps_the_revision(fx)
        with the_old_rule():
            appended, _present, discarded = ledger.replay(fx.root)
        claims = [ln for ln in self.events_of(fx, "state_transition")
                  if ln.get("to_status") == "accepted"]
        print(f"  B OLD RULE appended={appended} discarded={len(discarded)} · the journal "
              f"claims {len(claims)} accepted transition(s), the finding is "
              f"{self.status_of(fx)!r}")
        self.assertEqual(1, appended,
                         "the old rule was supposed to replay this phantom transition")
        self.assertEqual(1, len(claims))
        self.assertEqual("reported", self.status_of(fx))


class TheIdentityIsNotANumber(ScenarioCase):

    def test_two_transactions_with_one_revision_number_are_told_apart(self):
        """Two projects, each at revision 2, each with a row naming revision 2. Only one
        of the two transactions committed, and the rule has to separate them."""
        fx = self.project()
        landed = ledger.new_txn()
        never = ledger.new_txn()
        line = envelope.event_line("state_transition", None, verb="set-status",
                                   target="F-0001", state_revision=2,
                                   from_status="reported", to_status="accepted",
                                   summary="one of two")
        committed = ledger.reserve(fx.root, line, landed)
        # The same revision, a different transaction, and only the first one is confirmed.
        state_mod.write_state(fx.audit, state_mod.load_state(fx.audit), txn=landed)
        txns = ledger.committed_txns(fx.root)
        revision = json.loads((fx.audit / "state.json").read_text())["state_revision"]
        phantom = dict(committed, txn=never)
        print(f"\n  IDENTITY   both rows name state revision 2 at committed revision "
              f"{revision}; txns are {landed[:8]} and {never[:8]}")
        print(f"  IDENTITY   committed -> {ledger.uncommitted(committed, revision, txns)}")
        print(f"  IDENTITY   phantom   -> "
              f"{(ledger.uncommitted(phantom, revision, txns) or ledger.Unproved(None, 'admitted')).why[:80]}")
        self.assertIsNone(ledger.uncommitted(committed, revision, txns))
        verdict = ledger.uncommitted(phantom, revision, txns)
        self.assertIsNotNone(verdict, "two transactions at one revision were not told apart")
        self.assertFalse(verdict.hold)
        self.assertNotEqual(landed, never)

    def test_no_confirmation_at_all_disproves_a_row_that_names_one(self):
        """A row carries a txn only if this version wrote it, and this version confirms
        every commit — so an empty list is a disproof and not an absence of information.
        This is the state a project is in when a writer is killed between reserve and
        replace on its very first transition."""
        entry = {"txn": ledger.new_txn(), "line": {"state_revision": 2, "event": "x"}}
        verdict = ledger.uncommitted(entry, 1, ())
        print(f"  EMPTY      hold={verdict.hold} — {verdict.why[:88]}")
        self.assertFalse(verdict.hold)

    def test_a_row_whose_transaction_fell_out_of_the_window_is_held(self):
        """The third answer. `hold` is not `discard`: forgetting is not disproving, and
        deleting evidence because we no longer remember is the same class of error as
        inventing it."""
        entry = {"txn": ledger.new_txn(), "line": {"state_revision": 1, "event": "x"}}
        verdict = ledger.uncommitted(entry, 500, tuple(ledger.new_txn() for _ in range(64)))
        print(f"  WINDOW     {verdict.hold=} — {verdict.why[:88]}")
        self.assertTrue(verdict.hold)

    def test_a_historical_row_still_drains(self):
        """A row written before this phase carries no txn. It keeps the revision rule, so
        an outbox from an earlier version drains without anything being migrated."""
        old = {"event_id": "deadbeef", "line": {"state_revision": 2, "event": "x"}}
        print(f"  HISTORICAL proved={ledger.uncommitted(old, 3, ()) is None} at revision 3, "
              f"refused={ledger.uncommitted(old, 1, ()) is not None} at revision 1")
        self.assertIsNone(ledger.uncommitted(old, 3, ()))
        self.assertIsNotNone(ledger.uncommitted(old, 1, ()))


class EveryWriterParticipates(unittest.TestCase):
    """Structural. The confirmation is recorded by ONE function, and the writers reach it
    the only way there is — by calling it."""

    def calls_with_txn(self, module, function):
        src = (REPO / "xcheck" / f"{module}.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == function)
        return [c for c in ast.walk(fn) if isinstance(c, ast.Call)
                and getattr(c.func, "id", getattr(c.func, "attr", "")) == "write_state"
                and any(k.arg == "txn" for k in c.keywords)]

    def test_both_writers_commit_through_write_state(self):
        seen = {f"{m}.{f}": len(self.calls_with_txn(m, f))
                for m, f in (("write", "_apply"), ("envelope", "store"))}
        print(f"\n  PARTICIPATE {seen}")
        for where, n in seen.items():
            self.assertEqual(1, n, f"{where} does not commit a transaction identity")

    def test_only_write_state_records_a_confirmation(self):
        """A writer cannot record one any other way: `ledger_commits` is assigned in
        exactly one place in the package."""
        sites = []
        for path in sorted((REPO / "xcheck").glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
                    if getattr(node.slice, "value", None) == "ledger_commits":
                        sites.append(f"{path.name}:{node.lineno}")
        print(f"  ONE WRITER  ledger_commits is assigned at {sites}")
        self.assertEqual(1, len(sites), "more than one place records a confirmation")
        self.assertTrue(sites[0].startswith("state.py"))

    def test_COUNTERFACTUAL_a_writer_that_skips_it_is_caught(self):
        """The gate above is only worth having if it reddens. A writer that calls
        `write_state` without a txn is exactly the `envelope.store` this phase fixed."""
        src = (REPO / "xcheck" / "envelope.py").read_text(encoding="utf-8")
        skipped = src.replace("write_state(audit, replace(state, sessions=freeze(sessions)), "
                              "txn=txn)",
                              "write_state(audit, replace(state, sessions=freeze(sessions)))")
        self.assertNotEqual(src, skipped, "the mutation did not apply")
        fn = next(n for n in ast.walk(ast.parse(skipped))
                  if isinstance(n, ast.FunctionDef) and n.name == "store")
        calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)
                 and getattr(c.func, "id", "") == "write_state"
                 and any(k.arg == "txn" for k in c.keywords)]
        print(f"  MUTANT      envelope.store commits {len(calls)} identity/identities "
              f"(the gate above requires 1)")
        self.assertEqual([], calls, "the mutation left the call the gate looks for")


class ThePreviewAgreesWithTheApply(ScenarioCase):
    """`recover` without `--apply` is what an operator reads BEFORE deciding. It computed
    its verdicts from `uncommitted(entry, revision)` with no identities at all, so once
    the rule took them it would have previewed a different answer from the one `--apply`
    then performed. Two readers of one rule is the class of defect this whole run is
    about, so the preview is pinned to the apply rather than assumed to agree."""

    def test_the_dry_run_predicts_what_apply_does(self):
        fx = self.project()
        code, out = fx.run("set-status", "F-0002", "accepted")
        self.assertEqual(0, code, out)
        ledger.reserve(fx.root, envelope.event_line(
            "state_transition", None, verb="set-status", target="F-0001",
            state_revision=99, from_status="reported", to_status="accepted",
            summary="never landed"), ledger.new_txn())

        preview_code, preview = fx.run("recover")
        apply_code, applied = fx.run("recover", "--apply")
        said = [ln.strip() for ln in preview.splitlines() if "would " in ln]
        did = [ln.strip() for ln in applied.splitlines()
               if "replayed" in ln or "discarded" in ln]
        print(f"\n  PREVIEW    {said}")
        print(f"  APPLY      {did}")
        self.assertEqual(0, preview_code, preview)
        self.assertEqual(0, apply_code, applied)
        self.assertIn("would replay 0", preview)
        self.assertIn("discard 1", preview)
        self.assertIn("replayed 0", applied)
        self.assertIn("1 discarded", applied)
        self.assertEqual("reported", self.status_of(fx),
                         "the phantom transition reached canonical state")


class TheOldRuleReddensExactlyTheseArms(unittest.TestCase):
    """A new rule is decorative until you can say which tests would fail without it.

    The old rule is reinstated and the arms are re-run: the two that PROVE the protocol
    must go red, and an arm that does not depend on it must stay green. "Exactly" is the
    claim, so the control is half of the measurement.
    """

    PROVING = (("ScenarioA", "test_a_finished_session_keeps_its_event"),
               ("ScenarioB", "test_a_reservation_that_never_committed_is_discarded"))
    CONTROL = (("TheIdentityIsNotANumber", "test_a_historical_row_still_drains"),)

    def run_one(self, cls, name):
        suite = unittest.defaultTestLoader.loadTestsFromName(
            f"{name}", globals()[cls])
        result = unittest.TextTestRunner(stream=open(os.devnull, "w"),
                                         verbosity=0).run(suite)
        return result.wasSuccessful()

    def test_reinstating_the_counter_reddens_the_proving_arms_and_nothing_else(self):
        with contextlib.redirect_stdout(open(os.devnull, "w")):
            with the_old_rule():
                red = [f"{c}.{n}" for c, n in self.PROVING if not self.run_one(c, n)]
                control = [f"{c}.{n}" for c, n in self.CONTROL if not self.run_one(c, n)]
        print(f"\n  LOAD-BEARING under the old rule {len(red)} of {len(self.PROVING)} "
              f"proving arm(s) fail: {red}")
        print(f"  LOAD-BEARING and {len(control)} of {len(self.CONTROL)} control arm(s) "
              f"fail, which must be 0")
        self.assertEqual(len(self.PROVING), len(red),
                         "an arm that is supposed to prove the new rule passes without it")
        self.assertEqual([], control, "the old rule reddened an arm it has no bearing on")


@needs_live_corpus
class TheRealCorpusStillReads(unittest.TestCase):
    """Against this repository's own audit, not a fixture. The live state carries NO
    `ledger_anchor` and no `ledger_commits` at all, which is the backward-compatible case
    the code has to close rather than the evidence being migrated to fit it."""

    def test_the_live_state_loads_and_lints(self):
        audit = REPO / "audit"
        doc = json.loads((audit / "state.json").read_text(encoding="utf-8"))
        st = state_mod.load_state(audit)
        events = ledger._read_lines(REPO)
        print(f"\n  REAL        revision {st.state_revision} · {len(st.sessions)} "
              f"session(s) · {len(events)} event(s) · anchor="
              f"{doc.get('ledger_anchor')} · ledger_commits="
              f"{doc.get('ledger_commits', 'absent')}")
        self.assertEqual(doc["state_revision"], st.state_revision)
        self.assertEqual((), st.ledger_commits, "the live corpus grew a field it never had")
        p = subprocess.run([sys.executable, "-m", "xcheck", "lint"], cwd=str(REPO),
                           capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, PYTHONPATH=str(REPO)))
        print(f"  REAL        xcheck lint: exit={p.returncode}")
        self.assertEqual(0, p.returncode, p.stdout[-2000:] + p.stderr[-2000:])

    def test_a_document_without_the_field_round_trips_byte_identically(self):
        """No admitted evidence is rewritten. `to_document` omits the field when empty,
        so every state document written before this phase serialises to the same bytes."""
        before = (REPO / "audit" / "state.json").read_text(encoding="utf-8")
        after = state_mod.serialise(state_mod.to_document(
            state_mod.load_state(REPO / "audit")))
        print(f"  ROUND TRIP  {len(before)} bytes in, {len(after)} bytes out, "
              f"identical={before.strip() == after.strip()}")
        self.assertEqual(before.strip(), after.strip())


class BothClosedSchemas(unittest.TestCase):
    """A new state field has to clear the schema at the write boundary. It does not touch
    the OUTPUT schema, and that is a claim this phase has to make out loud rather than
    leave to be noticed."""

    def test_the_state_schema_admits_it_and_refuses_a_bad_one(self):
        good = state_mod.to_document(state_mod.empty_state("test"))
        good["ledger_commits"] = [ledger.new_txn()]
        state_mod._validate(good, "a document with one confirmation")
        for bad, why in ((["nope"], "not a transaction id"),
                         ([ledger.new_txn()] * 2, "the same id twice"),
                         ([ledger.new_txn() for _ in range(state_mod.COMMIT_WINDOW + 1)],
                          "past the window")):
            doc = dict(good, ledger_commits=bad)
            with self.assertRaises(state_mod.StateError, msg=why):
                state_mod._validate(doc, "a document that should be refused")
        print(f"\n  SCHEMA      state: admitted 1, refused 3 (bad id, duplicate, "
              f"> {state_mod.COMMIT_WINDOW})")

    #: Where `ledger_commits` may be named, and why. Declared rather than counted: a bare
    #: count goes stale on the next edit and says nothing about whether the field has
    #: leaked into a surface somebody reads.
    NAMES_THE_FIELD = {
        "ledger.py": "decides what a confirmation proves",
        "runner.py": "SUBTRACTS it as bookkeeping, so an idle session's effects_applied "
                     "is not True because the orchestrator wrote down that it wrote",
        "state.py": "records it, inside the same os.replace as the change",
    }
    #: The derived surfaces. If the field reached one of these it would be part of what
    #: xcheck publishes, and `OUTPUT_SCHEMA_VERSION` would have to move with it.
    RENDERED = ("views.py", "metrics.py", "bundle.py", "okf.py", "report.py",
                "benchmark.py", "status.py")

    def test_the_output_schema_did_not_move_and_here_is_why(self):
        """`ledger_commits` is recovery bookkeeping. Nothing DERIVED reads it, so
        `OUTPUT_SCHEMA_VERSION` stays where it is — and the claim is checked in both
        directions: the declared writers, and the rendered surfaces that must stay
        silent."""
        readers = {p.name for p in sorted((REPO / "xcheck").glob("*.py"))
                   if "ledger_commits" in p.read_text(encoding="utf-8")}
        rendered = [name for name in self.RENDERED
                    if (REPO / "xcheck" / name).exists()
                    and "ledger_commits" in (REPO / "xcheck" / name).read_text(encoding="utf-8")]
        print(f"\n  SCHEMA      output: unchanged; ledger_commits is named in "
              f"{sorted(readers)}")
        print(f"  SCHEMA      of {len(self.RENDERED)} rendered surface(s), {len(rendered)} "
              f"name it: {rendered}")
        self.assertEqual(set(self.NAMES_THE_FIELD), readers,
                         "a module names the confirmation field without being declared")
        self.assertEqual([], rendered,
                         "the field reached a rendered surface — the output schema moves")


if __name__ == "__main__":
    unittest.main()
