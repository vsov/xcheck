"""The honest stop: a channel the orchestrator reads even when every verb refuses.

What the frozen Ouroboros-4 corpus shows once its logs are read (the derivation is
`tests/fixtures/derive_ouroboros4_stops.py`, the result `ouroboros-4-stops.jsonl`): 132 of
the 136 barren sessions saw the write path refuse them, recorded nothing, and SAID SO — in
a pass report the courier committed and in a final message the orchestrator then classified
`ok`, because `classify_outcome` answers `ok` for any rc==0 before it reads a word. One
charter was re-dispatched 54 times. F-0009, the finding about the refusal, was filed on the
second pass and stayed `reported` for the whole run. The sessions stopped honestly; the
orchestrator had no channel on which to hear it.

The channel is one JSON object the child writes to `$XCHECK_STOP_FILE` (0.9.6, F3: it
was a printed line, and a source file the session cat'ed could forge it), read by the
parent before teardown and bound to the targets the dispatch was given: it does not go
through `audit/.lock`, so it works precisely when the verbs do not. Three consumers read
it: the outcome (`refused`, whatever rc), the receipt (`stop-claimed`, a third protocol
answer beside `recorded` and `protocol-no-progress`), and the decision (`stop-refusal` on a
PASS, as it already is on a finding). The counterfactual at the bottom replays the corpus
with the overlay and states what the gate would have held — barren AND productive, because
a halt for the human holds both until the human acts.
"""

import hashlib
import json
import pathlib
import shutil
import tempfile
import unittest

from tests import test_corpus_baseline as base
from tests.harness import Fixture, state_doc, xcheck_submodule

envelope = xcheck_submodule("envelope")
runner = xcheck_submodule("runner")
decision = xcheck_submodule("decision")
util = xcheck_submodule("util")

OVERLAY = base.CORPUS.parent / "ouroboros-4-stops.jsonl"
# `shasum -a 256 tests/fixtures/ouroboros-4-stops.jsonl` at the freeze, 2026-09-09. Pinned:
# a digest the test derives from the file it is checking asserts nothing about that file.
OVERLAY_DIGEST = "ad5fdbb69c1cc90a8a95f424fddef97d1bd60817c5f71834ef626844b03e84ee"
# Counted by the derivation script, independently of the receipt (printed by its run).
STOPS = 132
BARREN_WITHOUT_STOP = 4      # 2 cut off by the provider after the refusal, 1 cut off
                             # before it, 1 whose log did not survive — none SAID it stopped
STOP = '{"reason": "blocked-dependency", "target": "P-05", "detail": "file-finding: audit/.lock is held"}'


def _rows(path):
    with path.open(encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def corpus():
    return _rows(base.CORPUS)


def overlay():
    return _rows(OVERLAY)


def with_overlay(events, rows):
    """The corpus AS IF the channel had existed: each session's stop on its own
    `session_finished` event, where `finish` would have put it."""
    stops = {r["session_id"]: r["stop"] for r in rows if r["stop"]}
    out = []
    for e in events:
        if e.get("event") == "session_finished" and e.get("session_id") in stops:
            e = dict(e, stop=stops[e["session_id"]])
        out.append(e)
    return out


class TheFileIsReadStrictly(unittest.TestCase):
    """0.9.6 (F3): the channel is a FILE (`envelope.STOP_FILE`); its bytes are one object."""

    def test_a_valid_stop(self):
        stop, bad = envelope.stop_in_bytes(STOP.encode())
        self.assertEqual(("blocked-dependency", "P-05"), (stop["reason"], stop["target"]))
        self.assertEqual([], bad)

    def test_an_unknown_reason_is_refused_not_coerced(self):
        stop, bad = envelope.stop_in_bytes(b'{"reason": "tired"}')
        self.assertIsNone(stop)
        self.assertTrue(bad and "tired" in bad[0])

    def test_a_missing_reason_is_refused(self):
        stop, bad = envelope.stop_in_bytes(b'{"target": "P-05"}')
        self.assertIsNone(stop)
        self.assertTrue(bad)

    def test_two_objects_are_refused(self):
        stop, bad = envelope.stop_in_bytes((STOP + "\n" + STOP).encode())
        self.assertIsNone(stop)
        self.assertTrue(bad and "one valid JSON object" in bad[0], bad)

    def test_the_old_line_form_is_not_an_object(self):
        stop, bad = envelope.stop_in_bytes(("XCHECK_STOP " + STOP).encode())
        self.assertIsNone(stop)
        self.assertTrue(bad)


class TheOutcomeDoesNotReadTheLog(unittest.TestCase):

    def test_a_stop_line_in_the_log_is_not_a_stop(self):
        """The spoof F3 names: a session that cat'ed a file containing the old line
        used to stop. Text in the log is no longer a channel at all."""
        self.assertEqual("ok", runner.classify_outcome(
            0, log_text="work\nXCHECK_STOP " + STOP + "\n"))

    def test_rc_zero_without_it_is_still_ok(self):
        self.assertEqual("ok", runner.classify_outcome(0, log_text="Blocked — not complete."))

    def test_the_operator_outcomes_still_win(self):
        self.assertEqual("cancelled", runner.classify_outcome(0, cancelled=True))
        self.assertEqual("timeout", runner.classify_outcome(0, timed_out=True))


class TheStopTravelsOnTheSessionsOwnEvent(unittest.TestCase):

    def session(self, sid, stop, refusals=()):
        prof = runner.resolve_profile({}, "Auditor")
        rec = envelope.dispatch_record(["codex", "exec"], "Auditor", "P-05", "prompt",
                                       sid, prof, 0, "unknown", charter_targets=["P-05"])
        envelope.store(self.f.root, rec, event="session_dispatched")
        rec = envelope.finish(rec, 0, "refused", 1.0, "b" * 40, self.log,
                              stop=stop, stop_refusals=list(refusals))
        envelope.store(self.f.root, rec, event="session_finished")
        return rec

    def setUp(self):
        self.f = Fixture(state_doc())
        self.addCleanup(shutil.rmtree, self.f.root, True)
        d = pathlib.Path(tempfile.mkdtemp(prefix="xcheck-stop-"))
        self.addCleanup(shutil.rmtree, d, True)
        self.log = d / "s.log"
        self.log.write_text("audited U05\ntokens used\n1 000\n", encoding="utf-8")

    def test_a_stopped_session_records_the_stop_in_state_and_in_the_stream(self):
        rec = self.session("a" * 16, envelope.stop_in_bytes(STOP.encode())[0])
        self.assertEqual("refused", rec["outcome"])
        self.assertEqual("P-05", rec["stop"]["target"])
        doc = json.loads((self.f.audit / "state.json").read_text(encoding="utf-8"))
        self.assertEqual("blocked-dependency", doc["sessions"][0]["stop"]["reason"])
        self.assertEqual(["P-05"], doc["sessions"][0]["charter_targets"])
        fin = [e for e in envelope.read_events(self.f.root) if e.get("event") == "session_finished"]
        self.assertEqual("blocked-dependency", fin[0]["stop"]["reason"])

    def test_a_malformed_stop_is_recorded_as_refused_not_dropped(self):
        rec = self.session("b" * 16, *envelope.stop_in_bytes(b'{"reason": "tired"}'))
        self.assertNotIn("stop", rec)
        self.assertIn("tired", rec.get("note", ""))

    def test_a_stop_on_another_pass_is_refused_and_named(self):
        rec = self.session("c" * 16, {"reason": "blocked-dependency", "target": "P-06"})
        self.assertNotIn("stop", rec)
        self.assertIn("P-06", rec["note"])
        self.assertIn("P-05", rec["note"])


class TheReceiptGivesAThirdAnswer(unittest.TestCase):

    def finished(self, sid, stop):
        return {"event": "session_finished", "session_id": sid, "outcome": "refused",
                "stop": stop}

    def test_nothing_moved_and_a_stop_is_honest_stop(self):
        ev = [self.finished("s", {"reason": "blocked-dependency", "target": "P-05"})]
        r = envelope.receipt(ev, "s", role="Auditor", charter="P-05")
        self.assertEqual(envelope.PROTOCOL_STOP_CLAIMED, r["protocol"])
        self.assertEqual("blocked-dependency", r["stop"]["reason"])

    def test_nothing_moved_and_no_stop_is_still_no_progress(self):
        ev = [{"event": "session_finished", "session_id": "s", "outcome": "ok"}]
        r = envelope.receipt(ev, "s", role="Auditor", charter="P-05")
        self.assertEqual(envelope.PROTOCOL_NO_PROGRESS, r["protocol"])
        self.assertIsNone(r["stop"])

    def test_work_recorded_and_then_a_stop_stays_recorded_with_the_stop_carried(self):
        """Partial work then a wall is a legitimate stop (§4 rule 4). The receipt answers
        'did the charter's work move' — yes — and carries the stop for the decision."""
        ev = [{"event": "state_transition", "session_id": "s", "verb": "file-finding",
               "target": None, "state_revision": 1},
              self.finished("s", {"reason": "blocked-dependency", "target": "P-05"})]
        r = envelope.receipt(ev, "s", role="Auditor", charter="P-05")
        self.assertEqual(envelope.PROTOCOL_RECORDED, r["protocol"])
        self.assertEqual("P-05", r["stop"]["target"])

    def test_a_minted_verb_beside_a_stop_is_still_reported(self):
        ev = [{"event": "state_transition", "session_id": "s", "verb": "repair-body-paths",
               "target": None, "state_revision": 1},
              self.finished("s", {"reason": "blocked-dependency", "target": "P-05"})]
        r = envelope.receipt(ev, "s", role="Auditor", charter="P-05")
        self.assertEqual(envelope.PROTOCOL_STOP_CLAIMED, r["protocol"])
        self.assertEqual(1, len(r["undeclared"]))

    def test_the_third_answer_is_declared_and_is_not_an_outcome(self):
        self.assertIn(envelope.PROTOCOL_STOP_CLAIMED, envelope.PROTOCOLS)
        self.assertNotIn(envelope.PROTOCOL_STOP_CLAIMED, util.OUTCOMES)


class TheDecisionHaltsOnAStoppedPass(unittest.TestCase):

    def fin(self, sid, target, ts):
        return {"event": "session_finished", "session_id": sid, "ts": ts, "role": "Auditor",
                "stop": {"reason": "blocked-dependency", "target": target}}

    def test_a_live_stop_names_who_claimed_it(self):
        live = decision.live_stops([self.fin("a", "P-05", "t1")])
        self.assertEqual({"P-05": {"reason": "blocked-dependency", "role": "Auditor",
                                   "session_id": "a", "detail": None}}, live)

    def test_a_transition_on_the_pass_clears_it(self):
        ev = [self.fin("a", "P-05", "t1"),
              {"event": "state_transition", "session_id": "b", "verb": "record-coverage",
               "target": "P-05", "state_revision": 9}]
        self.assertEqual({}, decision.live_stops(ev))

    def test_the_operators_resolution_clears_it(self):
        ev = [self.fin("a", "P-05", "t1"),
              {"event": decision.STOP_RESOLVED, "target": "P-05", "resolution": "x"}]
        self.assertEqual({}, decision.live_stops(ev))

    def test_a_transition_on_another_target_does_not(self):
        ev = [self.fin("a", "P-05", "t1"),
              {"event": "state_transition", "verb": "record-coverage", "target": "P-06"}]
        self.assertIn("P-05", decision.live_stops(ev))

    def test_the_core_stops_for_the_human_instead_of_dispatching(self):
        kind, detail = decision.decide([], ["P-05"], refusals={"P-05": "blocked-dependency"})
        self.assertEqual("stop-refusal", kind)
        self.assertEqual(("P-05", "blocked-dependency"), detail)

    def test_a_planner_stop_halts_the_planner(self):
        kind, detail = decision.decide([], [], plan_complete=False,
                                       refusals={"PLAN": "material-missing"})
        self.assertEqual(("stop-refusal", ("PLAN", "material-missing")), (kind, detail))

    def test_without_the_refusal_the_same_pass_is_dispatched(self):
        self.assertEqual(("run-auditor", "P-05"), decision.decide([], ["P-05"]))


class TheCounterfactualOverTheFrozenCorpus(unittest.TestCase):
    """What the channel would have changed, replayed through production code."""

    @classmethod
    def setUpClass(cls):
        cls.rows = overlay()
        cls.events = corpus()
        cls.replayed = with_overlay(cls.events, cls.rows)
        cls.sessions = [e["session_id"] for e in cls.events
                        if e.get("event") == "session_dispatched"]

    def test_the_overlay_is_the_frozen_one(self):
        self.assertEqual(OVERLAY_DIGEST, hashlib.sha256(OVERLAY.read_bytes()).hexdigest())
        self.assertEqual(base.DISPATCHED, len(self.rows))
        self.assertEqual(STOPS, sum(1 for r in self.rows if r["stop"]))

    def test_without_the_channel_the_corpus_has_no_honest_stop(self):
        """CONTROL. The frozen stream alone yields none — the channel did not exist, and
        a rule that found stops in it would be reading something else."""
        answers = [envelope.receipt(self.events, s)["protocol"] for s in self.sessions]
        self.assertEqual(0, answers.count(envelope.PROTOCOL_STOP_CLAIMED))

    def test_with_the_channel_the_split_is_recorded_honest_stop_and_a_residue(self):
        answers = [envelope.receipt(self.replayed, s)["protocol"] for s in self.sessions]
        c = {a: answers.count(a) for a in set(answers)}
        print("\n  RECEIPT OVER THE CORPUS WITH THE STOP CHANNEL")
        for k in (envelope.PROTOCOL_RECORDED, envelope.PROTOCOL_STOP_CLAIMED,
                  envelope.PROTOCOL_NO_PROGRESS):
            print(f"    {k:22} {c.get(k, 0):5}")
        self.assertEqual(base.WITH_TRANSITION, c.get(envelope.PROTOCOL_RECORDED))
        self.assertEqual(STOPS, c.get(envelope.PROTOCOL_STOP_CLAIMED))
        self.assertEqual(BARREN_WITHOUT_STOP, c.get(envelope.PROTOCOL_NO_PROGRESS))
        self.assertEqual(base.DISPATCHED, sum(c.values()))

    def test_what_the_gate_would_have_held(self):
        """Per pass, in dispatch order: once a stop is live on a pass, every later dispatch
        of that pass until a transition on it is one the decision would have refused.
        Both columns are stated — the productive sessions it would have held are the
        price of a human gate, and 7 of them only succeeded by importing `write._apply`."""
        by_pass = {}
        for d in [e for e in self.events if e.get("event") == "session_dispatched"]:
            row = next(r for r in self.rows if r["session_id"] == d["session_id"])
            by_pass.setdefault(row["pass"], []).append(row)
        held_barren, held_moved, tokens = 0, 0, 0
        for pid, rows in by_pass.items():
            live = False
            for r in rows:                       # dispatch order is corpus order
                if live:
                    if r["moved"]:
                        held_moved += 1
                    else:
                        held_barren += 1
                    tokens += r["tokens"] or 0
                if r["moved"]:
                    live = False                 # a transition on the pass clears it
                elif r["stop"]:
                    live = True
        print(f"    held barren        {held_barren:5}   (of {base.DISPATCHED_WITHOUT})")
        print(f"    held productive    {held_moved:5}   (of {base.WITH_TRANSITION})")
        print(f"    tokens held        {tokens:>11,}")
        # Pre-registered bar (hoku session, 2026-09-09): >= 125 of the 136 barren sessions.
        # MEASURED: 124. The bar is missed by one and the miss is recorded here rather than
        # the bar moved: the first stop on each pass is the one that fires and is not
        # held, and a productive session on a pass clears the hold so the next stop after
        # it fires again. The number asserted is the measurement, so a change in the
        # replay shows as a red rather than as a quieter miss.
        self.assertEqual(124, held_barren)
        # Stated, not asserted: the human gate holds productive sessions too. The number is
        # printed so the next reader sees the price rather than being told there was none.
        self.assertLessEqual(held_moved, base.WITH_TRANSITION)


if __name__ == "__main__":
    unittest.main()
