"""The receipt: what a session moved, asked of the evidence rather than of the session.

`classify_outcome` returns `ok` for any child that exits zero. That answers "did the
process end?" and was being read as "did the role do its job?" — so 136 of the 151
Ouroboros-4 sessions came back `ok` having moved nothing, and the loop re-dispatched the
same charter against the same state. The receipt is the second answer, derived from
`state_transition` events, which `write._apply` emits only AFTER a state write is durable:
a verb that refused emits nothing and cannot appear here.

The numbers this file asserts are imported from `tests.test_corpus_baseline`, which counted
them with its own reader before any of this existed. That is the point of having both: the
production derivation and an independent count agreeing is evidence, where production code
asserting its own output would not be.
"""

import json
import unittest

from tests import test_corpus_baseline as base
from tests.harness import xcheck_submodule

envelope = xcheck_submodule("envelope")
write = xcheck_submodule("write")

MINTED = {"repair-body-paths", "repair-p13-artifact-paths"}


def corpus():
    return [json.loads(line) for line in base.CORPUS.open(encoding="utf-8") if line.strip()]


def dispatched(events):
    return [e["session_id"] for e in events if e.get("event") == "session_dispatched"]


class TheReceiptReproducesTheBaselineThroughProductionCode(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.events = corpus()
        cls.sessions = dispatched(cls.events)
        cls.receipts = [envelope.receipt(cls.events, s) for s in cls.sessions]

    def test_the_split_matches_the_independent_count(self):
        recorded = [r for r in self.receipts
                    if r["protocol"] == envelope.PROTOCOL_RECORDED]
        barren = [r for r in self.receipts
                  if r["protocol"] == envelope.PROTOCOL_NO_PROGRESS]
        print("\n  RECEIPT OVER THE FROZEN CORPUS")
        print(f"    dispatched        {len(self.sessions):5}")
        print(f"    recorded          {len(recorded):5}")
        print(f"    protocol-no-progress {len(barren):5}")
        self.assertEqual(base.DISPATCHED, len(self.sessions))
        self.assertEqual(base.WITH_TRANSITION, len(recorded))
        self.assertEqual(base.DISPATCHED_WITHOUT, len(barren))

    def test_every_session_gets_exactly_one_of_the_two_answers(self):
        """A partition. A third answer, or a missing one, would mean the gate has a
        state the loop has no branch for."""
        answers = {r["protocol"] for r in self.receipts}
        self.assertEqual({envelope.PROTOCOL_RECORDED, envelope.PROTOCOL_NO_PROGRESS},
                         answers)
        self.assertEqual(len(self.sessions), len(self.receipts))

    def test_the_protocol_answer_is_not_in_the_process_outcome_vocabulary(self):
        """The defect was one field answering two questions; the repair must not put the
        second answer back into the first field."""
        util = xcheck_submodule("util")
        self.assertNotIn(envelope.PROTOCOL_NO_PROGRESS, util.OUTCOMES)
        self.assertNotIn(envelope.PROTOCOL_RECORDED, util.OUTCOMES)


class OnlyADeclaredVerbCountsAsProgress(unittest.TestCase):
    """W-07: `write._apply` takes `verb` as a free string and `emit` validates no payload,
    so a session that imports it mints its own progress. Two records in the corpus do
    exactly that."""

    @classmethod
    def setUpClass(cls):
        cls.events = corpus()

    def test_the_corpus_contains_verbs_the_orchestrator_never_declared(self):
        seen = {e.get("verb") for e in self.events
                if e.get("event") == "state_transition"}
        undeclared = seen - set(write.VERBS)
        self.assertEqual(MINTED, undeclared,
                         "the set of minted verbs changed — if a new one appeared, it "
                         "arrived the same way and the receipt's rule needs re-arguing")

    def test_a_minted_verb_is_reported_but_buys_no_progress(self):
        events = [{"event": "state_transition", "session_id": "s",
                   "verb": "repair-body-paths", "target": None, "state_revision": 1}]
        r = envelope.receipt(events, "s")
        self.assertEqual(envelope.PROTOCOL_NO_PROGRESS, r["protocol"],
                         "a verb nobody declared bought progress — a subject that can "
                         "mint a verb can mint its own success")
        self.assertEqual(1, len(r["undeclared"]),
                         "the bypass left no trace in the receipt, so nobody would see it")

    def test_a_declared_verb_beside_a_minted_one_still_counts(self):
        """CONTROL. The rule narrows what counts; it must not discard a real transition
        because a minted one sat beside it."""
        events = [{"event": "state_transition", "session_id": "s", "verb": "file-finding",
                   "target": "F-1", "state_revision": 1},
                  {"event": "state_transition", "session_id": "s",
                   "verb": "repair-body-paths", "target": None, "state_revision": 2}]
        r = envelope.receipt(events, "s")
        self.assertEqual(envelope.PROTOCOL_RECORDED, r["protocol"])
        self.assertEqual(1, len(r["transitions"]))
        self.assertEqual(1, len(r["undeclared"]))

    def test_both_minted_records_belong_to_sessions_that_also_did_real_work(self):
        """Why the corpus baseline is unchanged by the rule, stated as a check rather
        than as a claim in prose."""
        for e in self.events:
            if e.get("event") == "state_transition" and e.get("verb") in MINTED:
                r = envelope.receipt(self.events, e["session_id"])
                self.assertEqual(envelope.PROTOCOL_RECORDED, r["protocol"])


class TheJoinIsOnTheSession(unittest.TestCase):
    """CONTROL. Without these, a derivation that ignored the session id would report
    every session as productive and still reproduce a plausible-looking number."""

    def test_a_session_with_no_events_of_its_own_is_no_progress(self):
        r = envelope.receipt(corpus(), "0000000000000000")
        self.assertEqual(envelope.PROTOCOL_NO_PROGRESS, r["protocol"])
        self.assertEqual([], r["transitions"])

    def test_an_empty_stream_gives_no_progress_rather_than_an_error(self):
        r = envelope.receipt([], "abcdefabcdefabcd")
        self.assertEqual(envelope.PROTOCOL_NO_PROGRESS, r["protocol"])

    def test_one_session_does_not_inherit_anothers_transitions(self):
        events = [{"event": "state_transition", "session_id": "a", "verb": "file-finding",
                   "target": "F-1", "state_revision": 1}]
        self.assertEqual(envelope.PROTOCOL_RECORDED,
                         envelope.receipt(events, "a")["protocol"])
        self.assertEqual(envelope.PROTOCOL_NO_PROGRESS,
                         envelope.receipt(events, "b")["protocol"])


class TheReceiptIsBoundToTheCharter(unittest.TestCase):
    """The audit's sentence: until the receipt is action-bound, any permitted CLI command
    buys a session the status `recorded`.

    Binding needs the charter TEXT. The envelope carries `charter_hash`, and a digest
    cannot be reversed into a list of finding ids — so the orchestrator hands the receipt
    the same charter it handed the child, and a call without one says so instead of
    implying a check it did not make.
    """

    AUDITOR_CHARTER = "P-16 — invariants × U01, U02: prove the lock is a lease"
    REMEDIATOR_CHARTER = "findings F-0125, F-0132"

    def moved(self, verb, target, session="s"):
        return {"event": "state_transition", "session_id": session, "verb": verb,
                "target": target, "state_revision": 1}

    def test_the_three_answers_and_the_case_that_produces_each(self):
        cases = [
            ("recorded", "Auditor", self.AUDITOR_CHARTER,
             [self.moved("file-finding", "F-0140")],
             "filed a finding — its charter's work"),
            ("protocol-violation", "Auditor", self.AUDITOR_CHARTER,
             [self.moved("set-limit", "reopen_limit")],
             "changed a limit and nothing else"),
            ("protocol-no-progress", "Auditor", self.AUDITOR_CHARTER, [],
             "moved nothing at all"),
        ]
        print("\n  THE THREE PROTOCOL ANSWERS")
        for want, role, charter, events, why in cases:
            with self.subTest(protocol=want):
                r = envelope.receipt(events, "s", role=role, charter=charter)
                print(f"    {want:<21} {role} that {why}")
                self.assertEqual(want, r["protocol"])
        self.assertEqual({c[0] for c in cases}, set(envelope.PROTOCOLS),
                         "the vocabulary and the cases have come apart")

    def test_the_reproduced_scenario_an_auditor_that_changed_a_limit(self):
        """The audit's own example, named. `set-limit` is a declared verb and a real
        transition; before the binding it was indistinguishable from the pass report the
        session was dispatched to write."""
        r = envelope.receipt([self.moved("set-limit", "reopen_limit")], "s",
                             role="Auditor", charter=self.AUDITOR_CHARTER)
        print(f"\n  REPRODUCED  Auditor session, one transition ({r['transitions'][0]['verb']}) "
              f"-> {r['protocol']}  (postcondition_met={r['postcondition_met']})")
        self.assertEqual(envelope.PROTOCOL_VIOLATION, r["protocol"])
        self.assertIsNone(r["postcondition_met"])
        self.assertEqual(1, len(r["transitions"]),
                         "the transition was dropped rather than judged — the receipt "
                         "must still record what moved")

    def test_control_removing_the_binding_returns_that_case_to_recorded(self):
        """CONTROL, asserting the SPECIFIC regression rather than a generic failure: the
        same events, the same session, with the charter binding withheld, are `recorded`
        again — which is exactly the old behaviour the audit described."""
        events = [self.moved("set-limit", "reopen_limit")]
        unbound = envelope.receipt(events, "s")                    # no role, no charter
        bound = envelope.receipt(events, "s", role="Auditor",
                                 charter=self.AUDITOR_CHARTER)
        print(f"  CONTROL  same events: unbound -> {unbound['protocol']}, "
              f"bound -> {bound['protocol']}")
        self.assertEqual(envelope.PROTOCOL_RECORDED, unbound["protocol"])
        self.assertFalse(unbound["charter_bound"],
                         "an unbound receipt claimed it had checked the charter")
        self.assertEqual(envelope.PROTOCOL_VIOLATION, bound["protocol"])

    def test_a_session_that_does_its_work_names_the_postcondition_it_met(self):
        r = envelope.receipt([self.moved("record-coverage", "P-16")], "s",
                             role="Auditor", charter=self.AUDITOR_CHARTER)
        print(f"  RECORDED  postcondition_met = {r['postcondition_met']!r}")
        self.assertEqual(envelope.PROTOCOL_RECORDED, r["protocol"])
        self.assertIn("coverage report", r["postcondition_met"])

    def test_a_bound_verb_on_a_target_outside_the_charter_is_a_violation(self):
        """The half that catches a Remediator fixing a finding it was not given. The
        control beside it is the same verb on a finding the charter names."""
        outside = envelope.receipt([self.moved("record-fix", "F-0999")], "s",
                                   role="Remediator", charter=self.REMEDIATOR_CHARTER)
        inside = envelope.receipt([self.moved("record-fix", "F-0125")], "s",
                                  role="Remediator", charter=self.REMEDIATOR_CHARTER)
        print(f"  CHARTER SCOPE  record-fix F-0999 -> {outside['protocol']}; "
              f"record-fix F-0125 -> {inside['protocol']} "
              f"(charter targets {inside['charter_targets']})")
        self.assertEqual(envelope.PROTOCOL_VIOLATION, outside["protocol"])
        self.assertEqual(envelope.PROTOCOL_RECORDED, inside["protocol"])

    def test_a_created_record_is_bound_by_the_role_not_by_the_id(self):
        """A finding the Auditor is FILING has an id that cannot be in the charter — it
        did not exist when the charter was written. Binding those by id would make every
        Auditor session a violation, which is the failure mode opposite to the one being
        fixed."""
        r = envelope.receipt([self.moved("file-finding", "F-9999")], "s",
                             role="Auditor", charter=self.AUDITOR_CHARTER)
        self.assertEqual(envelope.PROTOCOL_RECORDED, r["protocol"])

    def test_charter_targets_are_read_from_the_text(self):
        self.assertEqual({"F-0125", "F-0132"},
                         set(envelope.charter_targets(self.REMEDIATOR_CHARTER)))
        self.assertEqual({"P-16"}, set(envelope.charter_targets(self.AUDITOR_CHARTER)))
        self.assertEqual(set(), set(envelope.charter_targets(None)))

    def test_an_unknown_protocol_value_is_refused(self):
        runner = xcheck_submodule("runner")
        runner.RunResult(0, "ok", protocol=envelope.PROTOCOL_VIOLATION)   # declared: fine
        with self.assertRaises(SystemExit) as cm:
            runner.RunResult(0, "ok", protocol="looks-fine-to-me")
        self.assertIn("not a declared protocol answer", str(cm.exception))


class TheFrozenCorpusThroughTheNewReceipt(unittest.TestCase):
    """The corpus replayed through the action-bound receipt, and its number RESTATED.

    It is unchanged at 15, and the reason is not that the binding is inert: the frozen
    stream carries `charter_hash` on its dispatch events and no charter TEXT anywhere, so
    every one of those 151 sessions replays through the UNBOUND path — the two-value
    answer, with `charter_bound` False saying so. A receipt cannot bind against a digest,
    and inventing charters for admitted evidence would be rewriting it.

    So this is a reconciliation, not a confirmation: 15 is what the old receipt said and
    what the new one says, for a reason the test names. The bound behaviour is measured
    against constructed streams above, where the charter is known because the test wrote
    it.
    """

    @classmethod
    def setUpClass(cls):
        cls.events = corpus()
        cls.sessions = dispatched(cls.events)

    def test_the_replay_is_restated_and_reconciled(self):
        receipts = [envelope.receipt(self.events, s) for s in self.sessions]
        by = {p: sum(1 for r in receipts if r["protocol"] == p)
              for p in sorted(envelope.PROTOCOLS)}
        print("\n  FROZEN CORPUS THROUGH THE ACTION-BOUND RECEIPT")
        for p, n in by.items():
            print(f"    {p:<21} {n:5}")
        print(f"    baseline `recorded`   {base.WITH_TRANSITION:5}  "
              f"(independent count, unchanged)")
        print(f"    charter-bound         {sum(1 for r in receipts if r['charter_bound']):5}"
              f"  — the stream carries charter_hash, never charter text")
        self.assertEqual(base.WITH_TRANSITION, by[envelope.PROTOCOL_RECORDED],
                         "the corpus's recorded count moved; that is a RESULT to explain "
                         "in this docstring, not a number to restore")
        self.assertEqual(0, by[envelope.PROTOCOL_VIOLATION],
                         "a violation appeared in a replay that cannot bind a charter — "
                         "the binding is firing on something it cannot know")
        self.assertEqual(0, sum(1 for r in receipts if r["charter_bound"]))

    def test_the_events_the_corpus_carries_are_not_rewritten_to_make_this_work(self):
        """audit-archive is frozen evidence: read and copy, never write. Asserted here
        because the temptation this phase creates is to add a charter to the fixture."""
        raw = base.CORPUS.read_text(encoding="utf-8")
        self.assertNotIn('"charter"', raw,
                         "a charter was written into the frozen corpus — that is "
                         "rewriting admitted evidence to make a new check pass")


if __name__ == "__main__":
    unittest.main()
