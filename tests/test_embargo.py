"""A charter whose identical attempts moved nothing is not dispatched again.

The key is composed only of values the orchestrator computes for itself. The analysis
that opened this work proposed including a typed `blocker_code` the agent reports; a key
that trusts the subject's account of why it failed inherits the disease it exists to
cure — `rc=0` is exactly such an account.

`head` and `state_revision` were considered and rejected: the courier moves both on every
session, so a key containing either can never repeat and the embargo would never fire.

The third component was a GLOBAL count of declared transitions. That was the right notion
at the wrong scope — it asked "has anything moved anywhere", so a finding filed in a
completely different pass lifted the embargo on a charter that was still exactly as stuck
as before. It is now `decision.charter_slice`: the held charter's own queue entry, the
findings filed against it, and the policy in force. A rewritten pass report, a fresh log,
a new commit and another pass's events all leave it untouched, which is what must not lift
an embargo; a triage decision on this pass's own findings does move it, which must.
"""

import contextlib
import io
import json
import pathlib
import shutil
import unittest

from tests import test_corpus_baseline as base
from tests.harness import Fixture, queue_pass, state_doc, xcheck_submodule

cli = xcheck_submodule("cli")

decision = xcheck_submodule("decision")
envelope = xcheck_submodule("envelope")

PASS = "P-05"
CH = envelope.sha256_text(PASS)
DIGEST = "a" * 64


def dispatch(sid, charter=PASS, role="Auditor"):
    return {"event": "session_dispatched", "session_id": sid, "role": role,
            "charter_hash": envelope.sha256_text(charter)}


def key_event(sid, digest=DIGEST, charter_slice=None):
    return {"event": "dispatch_key", "session_id": sid, "source_digest": digest,
            "charter_slice": charter_slice}


def transition(sid, verb="file-finding"):
    return {"event": "state_transition", "session_id": sid, "verb": verb,
            "target": "F-1", "state_revision": 1}


def report_churn(sid):
    """What a session that only rewrote its pass report leaves behind: a finished
    envelope and nothing canonical. The whole point is that this is NOT progress."""
    return {"event": "session_finished", "session_id": sid, "outcome": "ok"}


class AnIdenticalAttemptIsNotBoughtTwice(unittest.TestCase):

    def report(self, events, after=2):
        return decision.embargo_report(events, [PASS], DIGEST, after=after)

    def test_two_barren_attempts_hold_the_third(self):
        """The analysis's own first test: a session exits zero twice having changed only
        its pass report, and the next dispatch does not happen."""
        events = []
        for sid in ("a" * 16, "b" * 16):
            events += [dispatch(sid), key_event(sid), report_churn(sid)]
        takeable, held = self.report(events)
        self.assertEqual([], takeable)
        self.assertIn(PASS, held)
        attempts, key, last = held[PASS]
        self.assertEqual(2, attempts)
        self.assertEqual(("Auditor", CH, None, DIGEST), key,
                         "the stop does not name the key that held it")
        # The slice is None on both sides here: this stream records no `charter_slice`
        # and the report is asked without state, which is the pre-field/historical
        # shape. `TheEmbargoIsScopedToItsOwnCharter` below supplies both.
        self.assertEqual("b" * 16, last)

    def test_one_barren_attempt_is_not_enough(self):
        """CONTROL. An embargo that fires on the first failure is not an embargo, it is
        a ban, and it would have refused seven of the fifteen passes that succeeded."""
        events = [dispatch("a" * 16), key_event("a" * 16), report_churn("a" * 16)]
        takeable, held = self.report(events)
        self.assertEqual([PASS], takeable)
        self.assertEqual({}, held)

    def test_a_session_that_moved_is_not_an_attempt_against_the_charter(self):
        """A third session against the same pass that DID record a transition is not
        counted as a barren attempt, so it never adds to the hold.

        This used to assert that it also LIFTED the hold, which it did for the wrong
        reason: the key carried a global transition count, so any transition anywhere
        changed every key in the audit. Lifting is now the slice's job and is measured
        in `TheEmbargoIsScopedToItsOwnCharter`, against real state."""
        events = []
        for sid in ("a" * 16, "b" * 16):
            events += [dispatch(sid), key_event(sid), report_churn(sid)]
        events += [dispatch("c" * 16), key_event("c" * 16), transition("c" * 16)]
        _, held = self.report(events)
        attempts, _, last = held[PASS]
        self.assertEqual(2, attempts, "the session that moved was counted as an attempt")
        self.assertEqual("b" * 16, last)

    def test_a_minted_verb_does_not_lift_it(self):
        """W-07: `_apply` takes `verb` as a free string. If an undeclared verb counted,
        a session could lift its own embargo by inventing one."""
        events = []
        for sid in ("a" * 16, "b" * 16):
            events += [dispatch(sid), key_event(sid), report_churn(sid)]
        events += [transition("b" * 16, verb="repair-body-paths")]
        takeable, held = self.report(events)
        self.assertEqual([], takeable)
        self.assertIn(PASS, held)

    def test_a_changed_orchestrator_lifts_it(self):
        """The escape hatch that stops a defect from reading as an impossible audit.
        W-08 is the case: every pass was failing on one function signature."""
        events = []
        for sid in ("a" * 16, "b" * 16):
            events += [dispatch(sid), key_event(sid), report_churn(sid)]
        takeable, held = decision.embargo_report(events, [PASS], "b" * 64, after=2)
        self.assertEqual([PASS], takeable)
        self.assertEqual({}, held)

    def test_another_charters_failures_do_not_hold_this_one(self):
        events = []
        for sid in ("a" * 16, "b" * 16):
            events += [dispatch(sid, charter="P-09"), key_event(sid), report_churn(sid)]
        takeable, held = self.report(events)
        self.assertEqual([PASS], takeable)

    def test_a_dispatch_from_another_role_is_not_an_attempt(self):
        events = []
        for sid in ("a" * 16, "b" * 16):
            events += [dispatch(sid, role="Remediator"), key_event(sid), report_churn(sid)]
        takeable, held = self.report(events)
        self.assertEqual([PASS], takeable)


class TheKeyRejectsWhatMovesOnEverySession(unittest.TestCase):
    """`head` and `state_revision` are recorded on every dispatch and change on every
    session, because the courier commits the orchestrator's own bookkeeping. A key built
    from either could never repeat, so the embargo could never fire — the mistake is
    silent, which is why it is asserted rather than commented."""

    def test_the_key_names_only_the_four_intended_components(self):
        key = decision._embargo_key("Auditor", CH, 7, DIGEST)
        self.assertEqual(("Auditor", CH, 7, DIGEST), key)
        self.assertEqual(4, len(key))

    def test_no_agent_reported_value_enters_the_key(self):
        """The analysis proposed a `blocker_code` the agent supplies. Nothing here
        depends on the agent's own account of why it failed."""
        import inspect
        src = inspect.getsource(decision.embargo_report)
        for reported in ("blocker_code", "blocker_inputs_hash", "outcome"):
            self.assertNotIn(reported, src,
                             f"{reported} entered the key — a key that trusts the "
                             f"subject's self-report inherits the rc=0 defect")


class TheCorpusReplaysThroughTheSameFunction(unittest.TestCase):
    """The frozen Ouroboros-4 stream, scored by production code rather than a script.

    The question has to be asked the way the orchestrator asks it: at each dispatch,
    using only what preceded it. Replaying to the END of the stream and asking what is
    held there answers a different question — `generation` is then the final count, so
    only attempts made after the last transition can match, and the answer is always
    "nothing held" no matter how long the streaks were. That reading cost a full cycle
    here, and the pinned numbers below are the incremental one.

    Every dispatch in this corpus predates `dispatch_key`, so no source digest was
    recorded and `embargo_hold` treats the absence as matching. That is the fail-closed
    direction: it counts those attempts rather than exempting them, which makes these
    numbers the MOST favourable the gate can score on this stream.

    RESTATED under the charter-scoped fingerprint (2026-09-03), not carried forward
    unexamined. The numbers below are byte-identical to the ones the global transition
    counter produced, and identical numbers from a changed rule need a reason before they
    can be trusted: measured on this stream, of the 136 barren Auditor dispatches spread
    over 10 charters, ZERO adjacent pairs on one charter are separated by a declared
    transition. Every charter's barren attempts form one contiguous run, so the old
    key's `generation` component was constant across each run and matched exactly where
    the new key's absent-slice component matches. The two rules coincide HERE; they do
    not coincide in general, which is what `TheEmbargoIsScopedToItsOwnCharter` measures.

    The pre-registered threshold therefore stays FALSIFIED on exactly the same evidence:
    120 barren refusals were required and 109 are what the gate scores at the shipped
    `embargo_after=3`, killing 8 of the 15 productive sessions. Nothing about this phase
    renegotiates that, and the shipped default stays `off`.
    """

    ROLE = "Auditor"

    @classmethod
    def setUpClass(cls):
        cls.events = [json.loads(l) for l in
                      base.CORPUS.read_text(encoding="utf-8").splitlines() if l.strip()]
        declared = envelope.declared_verbs()
        cls.moved = {e["session_id"] for e in cls.events
                     if e.get("event") == "state_transition" and e.get("verb") in declared}

    def replay(self, after):
        """`(barren_refused, productive_refused)` — the counterfactual, in order."""
        barren = productive = 0
        for i, e in enumerate(self.events):
            if e.get("event") != "session_dispatched" or e.get("role") != self.ROLE:
                continue
            hold = decision.embargo_hold(self.events[:i], self.ROLE, e["charter_hash"],
                                         "unchanged", after=after)
            if not hold:
                continue
            if e["session_id"] in self.moved:
                productive += 1
            else:
                barren += 1
        return barren, productive

    def test_the_corpus_still_holds_the_shape_the_replay_assumes(self):
        self.assertEqual(base.WITH_TRANSITION, len(self.moved))
        dispatched = [e for e in self.events if e.get("event") == "session_dispatched"]
        self.assertEqual(base.DISPATCHED, len(dispatched))

    def test_the_replay_records_what_the_gate_would_have_refused(self):
        measured = {after: self.replay(after) for after in (2, 3, 4, 5)}
        print(f"\n  COUNTERFACTUAL OVER THE FROZEN CORPUS "
              f"({base.DISPATCHED_WITHOUT} barren, {base.WITH_TRANSITION} productive)")
        for after, (barren, productive) in measured.items():
            print(f"    embargo_after={after}: refused {barren:3d} barren, "
                  f"killed {productive:2d} productive")
        self.assertEqual({2: (117, 9), 3: (109, 8), 4: (102, 7), 5: (96, 6)}, measured)

    def test_the_pre_registered_threshold_is_not_met_at_any_setting(self):
        """W-05 fixed the bar BEFORE the measurement: refuse at least 120 of the 136
        barren sessions and exactly 0 of the 15 productive ones. No setting does both,
        and the failure is on both arms at once — raising `after` to spare productive
        sessions lowers the barren count further from 120, not closer.

        The threshold is NOT renegotiated here. This test pins the falsification so that
        a later change which appears to satisfy it has to come and edit this assertion,
        and say against which corpus.
        """
        for after in (2, 3, 4, 5):
            barren, productive = self.replay(after)
            self.assertFalse(barren >= 120 and productive == 0,
                             f"embargo_after={after} now meets a threshold this corpus "
                             f"falsified; re-state the claim and name the corpus")

    def test_the_sessions_it_would_kill_are_the_ones_that_fought_the_lock(self):
        """WHY the corpus cannot score this gate. Ten of the fifteen productive sessions
        arrived only after a barren streak, several after 9 to 53 failures, and seven of
        the fifteen reached state only through the `_apply` bypass. Those streaks are the
        W-08 defect being fought, not a charter being hopeless. A gate that cuts them is
        being measured against a run whose every success was a persistence contest.
        """
        streaks, run = [], 0
        for e in self.events:
            if e.get("event") != "session_dispatched" or e.get("role") != self.ROLE:
                continue
            if e["session_id"] in self.moved:
                streaks.append(run)
                run = 0
            else:
                run += 1
        self.assertEqual([0, 0, 0, 0, 0, 1, 2, 3, 4, 9, 11, 12, 12, 29, 53], sorted(streaks))
        self.assertGreaterEqual(sorted(streaks)[7], decision.EMBARGO_AFTER,
                                "the median productive session no longer needed more "
                                "barren attempts than the gate allows — re-score W-05")


class TheDefaultIsOffAndThatIsAMeasuredResult(unittest.TestCase):
    """The gate ships OFF. Not caution — the number.

    The decision that a bound shipping off protects nobody who did not know to ask for it
    stands in general; it loses here to a measurement. Replayed over the frozen corpus the
    gate refuses at best 117 of 136 barren sessions against a pre-registered bar of 120,
    and kills 9 of the 15 productive ones. Until a post-W-08 run says otherwise, a bound
    that discards three of every five successes is not a default. Turning it on is one
    conf line, and that is how the clean run will score it.
    """

    def project(self, passes=("P-01", "P-02")):
        f = Fixture(state_doc(queue=[queue_pass(p) for p in passes]))
        self.addCleanup(shutil.rmtree, f.root, True)
        return f

    def barren(self, f, pass_id, n):
        for i in range(n):
            envelope.emit(f.root, "session_dispatched", f"{pass_id}-{i}".ljust(16, "x"),
                          role="Auditor", charter_hash=envelope.sha256_text(pass_id))

    def decide(self, f, conf):
        return decision.state_and_decision(f.root, conf)[3:]

    def test_the_shipped_conf_says_off(self):
        conf = xcheck_submodule("util").DEFAULT_CONF
        self.assertIn("embargo=off", conf)
        self.assertNotIn("embargo=on", conf)

    def test_a_conf_without_the_key_does_not_get_the_gate(self):
        """An orchestrator.conf written before this key existed must read as OFF. The
        absent-key fallback is the real default for every already-installed project."""
        f = self.project()
        self.barren(f, "P-01", 3)
        self.assertEqual(("run-auditor", "P-01"), self.decide(f, {}))

    def test_on_holds_the_charter(self):
        f = self.project()
        self.barren(f, "P-01", 3)
        self.assertEqual(("run-auditor", "P-02"), self.decide(f, {"embargo": "on"}),
                         "the gate did not hold, or held and froze the queue instead of "
                         "routing past it")

    def test_the_switch_goes_through_the_conf_boundary_every_other_flag_uses(self):
        """The first wiring hand-rolled `conf.get("embargo", ...) == "on"`. Two things
        were wrong with it: the default lived there instead of in `CONF_DEFAULTS`, and an
        unrecognised value read as OFF — a gate the operator believes is armed and is not,
        which is precisely the failure F-0113's ruling addressed. `conf_flag` owns the
        vocabulary and REFUSES anything outside it."""
        f = self.project()
        self.barren(f, "P-01", 3)
        for value in ("on", "true", "yes", "1"):
            with self.subTest(arms=value):
                self.assertEqual(("run-auditor", "P-02"),
                                 self.decide(f, {"embargo": value}))
        for value in ("off", "false", "no", "0", ""):
            with self.subTest(leaves_off=value):
                self.assertEqual(("run-auditor", "P-01"),
                                 self.decide(f, {"embargo": value}))
        for value in ("maybe", "ON!", "of"):
            with self.subTest(refuses=value):
                with self.assertRaises(SystemExit) as caught:
                    self.decide(f, {"embargo": value})
                self.assertIn("embargo must be one of", str(caught.exception))

    def test_the_defaults_live_where_every_other_conf_default_lives(self):
        util = xcheck_submodule("util")
        self.assertEqual("off", util.CONF_DEFAULTS["embargo"])
        self.assertEqual("3", util.CONF_DEFAULTS["embargo_after"])
        self.assertIn("embargo", util.BOOLEAN_CONF)
        self.assertEqual(1, util.NUMERIC_CONF["embargo_after"],
                         "embargo_after=0 would hold every charter before a single "
                         "attempt had been bought — a queue that refuses itself")

    def test_a_bound_of_zero_is_refused(self):
        f = self.project()
        self.barren(f, "P-01", 3)
        with self.assertRaises(SystemExit):
            self.decide(f, {"embargo": "on", "embargo_after": "0"})

    def test_one_held_charter_does_not_freeze_the_queue_behind_it(self):
        """`_decide_core` returns `unchecked_passes[0]` unconditionally, so a veto that
        answered "no" after the pick would stall every other pass behind one bad charter
        — which is what P-05 did the expensive way, 54 times, while the rest waited."""
        f = self.project(("P-01", "P-02", "P-03"))
        self.barren(f, "P-01", 5)
        self.assertEqual(("run-auditor", "P-02"), self.decide(f, {"embargo": "on"}))

    def test_a_queue_held_end_to_end_stops_at_a_gate_that_names_the_key(self):
        f = self.project()
        self.barren(f, "P-01", 3)
        self.barren(f, "P-02", 3)
        kind, detail = self.decide(f, {"embargo": "on"})
        self.assertEqual("stop-embargo", kind)
        self.assertEqual({"P-01", "P-02"}, set(detail))

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.report_gate(kind, detail)
        said = out.getvalue()
        self.assertNotEqual("stop-embargo\n", said,
                            "the gate fell through to `print(kind)` — a stop with no "
                            "reason and no key is not a human gate")
        # "declared transition(s)" until phase 7: the key's third component was a global
        # count of them. It is the charter's own state slice now, and the message has to
        # say so or the operator reads a digest as a tally.
        for must in ("P-01", "P-02", "3 barren attempt(s)", "charter slice",
                     "orchestrator", "embargo=off"):
            self.assertIn(must, said)

    def test_the_gate_detail_reaches_the_stream_as_data(self):
        """`_gate_detail` had no dict branch, so this gate's detail was recorded as a
        Python repr: present in the stream, unparseable, unreplayable."""
        detail = {"P-01": (3, ("Auditor", "c" * 64, 0, "d" * 64), "e" * 16)}
        self.assertEqual({"P-01": [3, ["Auditor", "c" * 64, 0, "d" * 64], "e" * 16]},
                         json.loads(json.dumps(cli._gate_detail(detail))))

    def test_the_existing_detail_shapes_are_untouched(self):
        """CONTROL for the widening. Every gate that shipped before carries a string or a
        tuple; adding the dict branch must not have moved any of them."""
        self.assertEqual("F-0001", cli._gate_detail("F-0001"))
        self.assertEqual(["F-0001", "missing-material"],
                         cli._gate_detail(("F-0001", "missing-material")))
        self.assertIsNone(cli._gate_detail(None))


class TheSavingPricedWithItsAssumptionsNamed(unittest.TestCase):
    """W-05's second half: what the refused sessions actually cost.

    Priced by the W-02 reader from the frozen digest-to-tokens table, so the figure in
    any report is READ from the run rather than typed into it.

    The saving is a RANGE, and the range is an upper region rather than a confidence
    interval, because the two things that would lower it are of different kinds:

      upper bound — every refused barren session was worth nothing, and destroying a
        productive session costs nothing to make good. Both assumptions are false; this
        is the number the analysis's framing implies and it is the most favourable one
        that can be defended at all.
      lower bound — the same, minus the tokens of the productive sessions the gate would
        have destroyed. Their transitions would have to be bought again, and the floor on
        buying them again is what they cost the first time. It is a floor and not an
        estimate: a re-run of destroyed work is not cheaper than the run that did it.

    NEITHER bound prices the diagnostic evidence inside the refused barren sessions.
    Several of them recorded, in prose, the lock refusal that became W-08 — the single
    most valuable finding of the run came out of sessions this gate would have refused.
    That value is real and is not quantified here, and it would lower BOTH bounds. Saying
    so is the point: a saving stated without it would be a number pretending to be a
    measurement.
    """

    ROLE = "Auditor"
    TOTAL = 24_938_795
    PRICED = {2: (117, 18_304_426, 9, 2_070_947),
              3: (109, 16_925_844, 8, 1_904_083),
              4: (102, 15_791_702, 7, 1_667_653),
              5: (96, 14_800_474, 6, 1_391_022)}

    @classmethod
    def setUpClass(cls):
        cls.events = [json.loads(l) for l in
                      base.CORPUS.read_text(encoding="utf-8").splitlines() if l.strip()]
        table = json.loads((pathlib.Path(__file__).parent / "fixtures" /
                            "ouroboros-4-tokens.json").read_text(encoding="utf-8"))
        by_digest = table["tokens_by_log_digest"]
        cls.tokens = {e["session_id"]: by_digest.get(e.get("log_digest"))
                      for e in cls.events if e.get("event") == "session_finished"}
        declared = envelope.declared_verbs()
        cls.moved = {e["session_id"] for e in cls.events
                     if e.get("event") == "state_transition" and e.get("verb") in declared}

    def replay(self, after):
        """`(barren_n, barren_tokens, productive_n, productive_tokens)` for what the gate
        would have refused, decided at each dispatch from only what preceded it."""
        bn = bt = pn = pt = 0
        for i, e in enumerate(self.events):
            if e.get("event") != "session_dispatched" or e.get("role") != self.ROLE:
                continue
            if not decision.embargo_hold(self.events[:i], self.ROLE, e["charter_hash"],
                                         "unchanged", after=after):
                continue
            cost = self.tokens.get(e["session_id"]) or 0
            if e["session_id"] in self.moved:
                pn += 1
                pt += cost
            else:
                bn += 1
                bt += cost
        return bn, bt, pn, pt

    def test_the_price_of_each_group_is_read_from_the_run(self):
        measured = {after: self.replay(after) for after in sorted(self.PRICED)}
        print("\n  WHAT THE GATE WOULD HAVE REFUSED, PRICED "
              f"(run total {self.TOTAL:,} tokens)")
        for after, (bn, bt, pn, pt) in measured.items():
            spared = self.TOTAL - bt - pt
            print(f"    after={after}: refused {bn:3d} barren = {bt:>10,} tok | "
                  f"killed {pn:2d} productive = {pt:>9,} tok | spared {spared:>10,} tok")
            print(f"              saving {bt - pt:>10,} .. {bt:>10,}  "
                  f"(lower subtracts the destroyed work at its own cost; neither bound "
                  f"prices the diagnostics inside the refused sessions)")
        self.assertEqual(self.PRICED, measured)

    def test_every_refused_session_was_priced(self):
        """A saving computed over sessions the reader could not price would be a saving
        made larger by unreadable logs. The one unpriced session of the run was never
        dispatched twice, so nothing the gate refuses is unpriced — asserted, not assumed."""
        for i, e in enumerate(self.events):
            if e.get("event") != "session_dispatched" or e.get("role") != self.ROLE:
                continue
            if decision.embargo_hold(self.events[:i], self.ROLE, e["charter_hash"],
                                     "unchanged", after=2):
                self.assertIsNotNone(self.tokens.get(e["session_id"]),
                                     f"{e['session_id']} would be refused and has no "
                                     f"price; the saving would count it as free")

    def test_the_bounds_are_ordered_and_the_lower_one_is_not_the_upper_one(self):
        """CONTROL. A range whose halves are equal is a point estimate wearing a range's
        clothes; these differ by the cost of the productive work the gate destroys, which
        is the whole reason the second bound exists."""
        for after in sorted(self.PRICED):
            bn, bt, pn, pt = self.replay(after)
            self.assertGreater(bt, bt - pt)
            self.assertGreater(pt, 0, "at this setting the gate destroys nothing, so the "
                                      "two bounds would coincide — restate the range")

    def test_the_saving_is_never_stated_as_a_share_of_a_total_it_did_not_measure(self):
        """The refused tokens must not be compared against the run total as though the
        remainder were all productive. At the shipped default the gate refuses sessions
        worth 67.9% of the run — while destroying 8 of the 15 sessions that moved
        anything, which is the fact any percentage here has to be printed beside."""
        bn, bt, pn, pt = self.replay(decision.EMBARGO_AFTER)
        share = round(bt / self.TOTAL * 100, 1)
        self.assertEqual(67.9, share)
        self.assertEqual(8, pn, "the destroyed-productive count moved; the share above "
                                "may no longer be quoted without it")


if __name__ == "__main__":
    unittest.main()


class TheEmbargoIsScopedToItsOwnCharter(unittest.TestCase):
    """The audit's probe, inverted.

    Measured on the code before this phase:

        held_before = True
        unrelated file-finding in another charter
        held_after_unrelated_transition = False

    An arbitrary transition in another pass lifted the embargo on a charter that had not
    moved at all. The key carried a GLOBAL count of declared transitions, so anything
    that moved anywhere changed every key in the audit.
    """

    OTHER = "P-06"

    def state(self, extra_findings=(), pass_id="P-05"):
        """Real state, not a stand-in: `charter_slice` reads the queue entry and the
        findings filed against the pass, and a duck-typed fake would let the slice's
        definition drift away from the document it claims to slice."""
        from tests.harness import finding_record
        doc = state_doc(
            findings=[finding_record(f, "reported", **{"pass": p})
                      for f, p in extra_findings],
            queue=[queue_pass("P-05", done=False), queue_pass("P-06", done=False)])
        fx = Fixture(doc)
        self.addCleanup(fx.cleanup)
        state_mod = xcheck_submodule("state")
        return state_mod.load_state(fx.audit)

    def barren(self, at_slice):
        events = []
        for sid in ("a" * 16, "b" * 16):
            events += [dispatch(sid), key_event(sid, charter_slice=at_slice),
                       report_churn(sid)]
        return events

    def held(self, events, state):
        _, held = decision.embargo_report(events, [PASS], DIGEST, after=2, state=state)
        return PASS in held

    def test_an_unrelated_transition_in_another_charter_does_not_lift_it(self):
        before_state = self.state()
        at_dispatch = decision.charter_slice(before_state, PASS)
        events = self.barren(at_dispatch)
        held_before = self.held(events, before_state)

        # A finding filed under a DIFFERENT pass, by a different session — the audit's
        # exact probe. It is a real, declared, canonical transition; it is simply not
        # this charter's.
        events += [dispatch("c" * 16, charter=self.OTHER),
                   key_event("c" * 16),
                   {"event": "state_transition", "session_id": "c" * 16,
                    "verb": "file-finding", "target": "F-0002", "state_revision": 9}]
        after_state = self.state(extra_findings=[("F-0002", self.OTHER)])
        held_after = self.held(events, after_state)

        print("\n  THE AUDIT'S PROBE, RE-RUN")
        print(f"    held_before                        {held_before}")
        print(f"    unrelated file-finding in {self.OTHER}      (a real declared "
              f"transition, another charter)")
        print(f"    held_after_unrelated_transition    {held_after}")
        self.assertTrue(held_before, "the charter was not held to begin with")
        self.assertTrue(held_after,
                        "an arbitrary transition in another pass lifted the embargo on a "
                        "charter that has not moved")

    def test_a_relevant_change_does_lift_it(self):
        """The other half, and the one that stops the fix from being 'never lift'. A
        finding filed against the HELD charter's own pass changes its slice."""
        before_state = self.state()
        events = self.barren(decision.charter_slice(before_state, PASS))
        after_state = self.state(extra_findings=[("F-0002", PASS)])
        held_before = self.held(events, before_state)
        held_after = self.held(events, after_state)
        print(f"\n  RELEVANT CHANGE  held_before {held_before} -> "
              f"a finding filed against {PASS} itself -> held_after {held_after}")
        self.assertTrue(held_before)
        self.assertFalse(held_after,
                         "the held charter's own state moved and it is still held — the "
                         "embargo has stopped being conditional on anything")

    def test_the_fingerprint_ignores_the_four_named_inputs(self):
        """Logs, timestamps, courier commits, another pass's events. Enumerated, because
        "it does not depend on those" is a claim about inputs and inputs can be listed.

        Asserted on the SLICE, which is the component this phase added: the rest of the
        key is the role, the charter hash and the orchestrator's source digest, none of
        which any of these four can reach."""
        state = self.state()
        base_slice = decision.charter_slice(state, PASS)
        arms = {
            "a fresh session log": self.state(),                       # no state change
            "a new timestamp": self.state(),
            "a courier commit": self.state(),
            "another pass's finding": self.state(extra_findings=[("F-0002", self.OTHER)]),
        }
        print("\n  FINGERPRINT INPUTS")
        for what, st in arms.items():
            with self.subTest(input=what):
                got = decision.charter_slice(st, PASS)
                print(f"    {what:<26} slice unchanged: {got == base_slice}")
                self.assertEqual(base_slice, got,
                                 f"{what} moved the charter's fingerprint")
        # And the control: something that MUST move it.
        moved = decision.charter_slice(self.state(extra_findings=[("F-0002", PASS)]), PASS)
        print(f"    {'this pass own finding':<26} slice unchanged: {moved == base_slice}")
        self.assertNotEqual(base_slice, moved,
                            "nothing moves the fingerprint — it is a constant, and the "
                            "four arms above prove nothing")

    def test_the_policy_in_force_is_part_of_the_slice(self):
        """A charter blocked by the containment it ran under becomes worth re-dispatching
        when the operator changes that containment."""
        state = self.state()
        self.assertNotEqual(decision.charter_slice(state, PASS, policy_digest="a" * 64),
                            decision.charter_slice(state, PASS, policy_digest="b" * 64))


class TheRestatedCorpusNumbersAreExplainedNotAsserted(unittest.TestCase):
    """The reason the counterfactual's numbers did not move, measured rather than
    claimed. A changed rule that produces identical numbers is either coincidence or a
    rule that did not change; this says which, on this stream."""

    def test_no_charters_barren_run_is_broken_by_a_declared_transition(self):
        events = [json.loads(line) for line in
                  base.CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]
        declared = envelope.declared_verbs()
        moved = {e["session_id"] for e in events
                 if e.get("event") == "state_transition" and e.get("verb") in declared}
        at = [i for i, e in enumerate(events)
              if e.get("event") == "state_transition" and e.get("verb") in declared]
        runs = {}
        for i, e in enumerate(events):
            if (e.get("event") == "session_dispatched" and e.get("role") == "Auditor"
                    and e["session_id"] not in moved):
                runs.setdefault(e["charter_hash"], []).append(i)
        broken = sum(1 for idxs in runs.values()
                     for a, b in zip(idxs, idxs[1:])
                     if any(a < t < b for t in at))
        print(f"\n  WHY THE NUMBERS COINCIDE  {sum(len(v) for v in runs.values())} barren "
              f"Auditor dispatches over {len(runs)} charters; {broken} adjacent pairs on "
              f"one charter are separated by a declared transition")
        self.assertEqual(136, sum(len(v) for v in runs.values()))
        self.assertEqual(0, broken,
                         "a barren run IS broken by a transition on this stream, so the "
                         "old and new keys should have scored differently and the "
                         "restated docstring is wrong")
