"""Ceilings that stop a run, and the proof that each stops for its own reason.

The audit's number is the reason this module exists: 85.4% of Ouroboros-4's tokens were
spent by sessions that recorded no canonical transition, and nothing in the system could
stop that while it was happening.

Six gates, and the hard part is not making them fire — it is proving that six fixtures
produced six DIFFERENT refusals rather than one gate catching all of them. So every
fixture is run against every gate in turn, and the assertion is that exactly one fires
and it is the one the fixture was built for.

Two rules carried in from earlier runs and asserted here:

  - `a-budget-that-only-gates-the-start` — a ceiling that is a precondition is walked
    through exactly once. Each gate is evaluated at the decision point, and the
    per-audit one refuses when the REMAINING budget cannot cover one more session, not
    after the overspend has landed.
  - every figure is priced on MEASURED sessions only, with the coverage in the message.
    A ceiling enforced against an unmeasured majority is a guess with an exit code.
"""

import collections
import unittest

from tests.harness import needs_live_corpus, REPO, Fixture, state_doc, queue_pass, xcheck_submodule

budget = xcheck_submodule("budget")
state_mod = xcheck_submodule("state")
util = xcheck_submodule("util")

CHARTER = "check the documented invariants hold in the unit"


def finished(sid, tokens):
    return {"event": "session_finished", "session_id": sid, "tokens": tokens}


def moved(sid, verb="file-finding"):
    return {"event": "state_transition", "session_id": sid, "verb": verb}


def accepted(sid):
    return {"event": "state_transition", "session_id": sid, "verb": "set-status",
            "to_status": "accepted"}


def dispatched(sid, charter=CHARTER):
    """The event `envelope` actually writes: a `charter_hash`, never a `charter`.

    PHASE 5 (fourth audit). This helper used to emit `{"charter": charter}`, a field no
    production emitter has ever written, and the repeat gate keyed on it. The gate was
    unreachable on every real run and its test was green, because the test built the
    event and invented the field. The fixture is now the production shape, hashed through
    the SAME helper the gate uses — see `AGateMustReadTheFieldProductionWrites` below,
    which checks this fixture against the real stream rather than trusting it."""
    return {"event": "session_dispatched", "session_id": sid,
            "charter_hash": budget.charter_key(charter)}


def on(**over):
    """A conf with budgets armed and every ceiling off but the ones named."""
    conf = {budget.FLAG: "on"}
    conf.update({k: str(v) for k, v in over.items()})
    return conf


# Deliberately generous values for the five ceilings a case is NOT about. Arming all six
# on every fixture is what turns "it fired" into "it fired for its own reason": with the
# other five armed and satisfied, the one that fires is the one whose CONDITION the
# fixture actually violates, and not the only gate that happened to be switched on.
LOOSE = {"tokens_per_session": 1_000_000, "tokens_per_pass": 10_000_000,
         "tokens_per_audit": 100_000_000, "no_progress_share_pct": 99,
         "charter_repeat_limit": 100, "marginal_value_window": 1000}


def all_armed(**tight):
    return on(**{**LOOSE, **tight})


# Six fixtures, one per gate. Each is a (conf, events) pair built to trip exactly one.
def events_expensive():
    """Sessions that DO move state, so nothing but the token ceilings can fire."""
    ev = []
    for i in range(4):
        sid = f"{i:016d}"
        ev += [dispatched(sid, f"pass P-{i:02d}"), moved(sid), accepted(sid),
               finished(sid, 50_000)]
    return ev


def events_barren():
    """Sessions that finish having moved nothing: the audit's 85.4% shape."""
    ev = []
    for i in range(4):
        sid = f"{i:016d}"
        ev += [dispatched(sid, f"pass P-{i:02d}"), finished(sid, 50_000)]
    # One that DID move, so the share is high but not 100% — a gate that only fires at
    # 100% would never have caught Ouroboros-4 either.
    ev += [dispatched("9" * 16, "pass P-09"), moved("9" * 16), accepted("9" * 16),
           finished("9" * 16, 10_000)]
    return ev


def events_repeated_charter():
    """One charter, bought again and again — and each attempt DID move something, which
    is what makes this different from the embargo."""
    ev = []
    for i in range(4):
        sid = f"{i:016d}"
        ev += [dispatched(sid, CHARTER), moved(sid), accepted(sid), finished(sid, 1_000)]
    return ev


def events_stalled():
    """Progress, then a long run of sessions with no new ACCEPTED evidence."""
    ev = [dispatched("0" * 16, "pass P-00"), moved("0" * 16), accepted("0" * 16),
          finished("0" * 16, 1_000)]
    for i in range(1, 10):
        sid = f"{i:016d}"
        # They move state — they file findings — but nothing is accepted. That is the
        # distinction the rule is about: sessions are not evidence.
        ev += [dispatched(sid, f"pass P-{i:02d}"), moved(sid), finished(sid, 1_000)]
    return ev


def events_one_expensive_pass():
    """Four sessions dispatched under ONE pass's charter, so the per-pass gate has a pass
    to measure. PHASE 12: `tokens_per_pass` used to divide the audit's whole spend by its
    done passes, so any events at all fed it; it now measures the pass being dispatched,
    attributed session by session, and a fixture whose sessions belong to no queue entry
    measures nothing."""
    ev = []
    for i in range(4):
        sid = f"{i:016d}"
        ev += [dispatched(sid, "P-01"), moved(sid), accepted(sid), finished(sid, 50_000)]
    return ev


def events_already_over():
    """Four measured sessions, all finished, whose TOTAL has already passed the ceiling
    below. Nothing here is an estimate: every figure is spend a finished session
    reported."""
    ev = []
    for i in range(4):
        sid = f"{i:016d}"
        ev += [dispatched(sid, "P-01"), moved(sid), accepted(sid), finished(sid, 50_000)]
    return ev


CASES = {
    # PHASE 7 (fifth audit): measured 200,000 against a 100,000 ceiling. The audit's
    # reproduction was the same shape ten times smaller, and it returned `no-verdict`.
    "stop-known-overrun": (all_armed(tokens_per_audit=100_000), events_already_over),
    "stop-budget-audit": (all_armed(tokens_per_audit=210_000), events_expensive),
    "stop-budget-session": (all_armed(tokens_per_session=1_000), events_expensive),
    "stop-budget-pass": (all_armed(tokens_per_pass=1_000), events_one_expensive_pass),
    "stop-no-progress-share": (all_armed(no_progress_share_pct=50), events_barren),
    "stop-charter-repeats": (all_armed(charter_repeat_limit=3),
                             events_repeated_charter),
    "stop-marginal-value": (all_armed(marginal_value_window=8), events_stalled),
}


def fixture_state():
    fx = Fixture(state_doc(queue=[queue_pass("P-01", done=True)]))
    return fx, state_mod.load_state(fx.audit)


def measured_pass(sessions=4, tokens=50_000, charter=CHARTER):
    """A pass whose spend is ATTRIBUTED: dispatch events under the queue entry's own
    charter AND the session records `attribution_report` reads the figure off.

    `events_one_expensive_pass` deliberately has neither — its sessions dispatch under the
    pass ID and leave no records, so the per-pass gate measures nothing there and reaches
    its ceiling through the estimate instead. The two fixtures are what separate `this
    pass has already spent it` from `one more session would`."""
    ev, recs = [], []
    for i in range(sessions):
        sid = f"{i:016d}"
        ev += [dispatched(sid, charter), moved(sid), accepted(sid), finished(sid, tokens)]
        recs.append({"session_id": sid, "role": "Auditor", "provider": "openai",
                     "agent_model": "unknown", "executable": "codex",
                     "executable_version": "unknown", "prompt_hash": "0" * 64,
                     "charter_hash": budget.charter_key(charter), "state_revision": 1,
                     "head_before": "0" * 40, "sandbox_profile": "worktree",
                     "started": "2026-08-14T00:00:00Z", "tokens": tokens,
                     # FINALISED, or the state schema refuses the document: more than one
                     # open dispatch means a session ended without being closed.
                     "outcome": "ok", "exit_status": 0, "duration_s": 1.0,
                     "head_after": "0" * 40, "log_digest": "0" * 64})
    fx = Fixture(state_doc(queue=[queue_pass("P-01", done=True)], sessions=recs))
    return fx, state_mod.load_state(fx.audit), ev


class EveryCeilingFires(unittest.TestCase):

    def test_each_ceiling_fires_with_its_own_message(self):
        for gate, (conf, make_events) in CASES.items():
            with self.subTest(gate=gate):
                fx, st = fixture_state()
                self.addCleanup(fx.cleanup)
                fired = budget.gates(st, conf, make_events(), charter=CHARTER)
                self.assertIsNotNone(fired, f"{gate} did not fire")
                self.assertEqual(gate, fired[0])
                msg = budget.gate_message(*fired)
                self.assertIn(budget.GATE_KEY[gate], msg,
                              "a refusal must name the key to raise")
                print(f"{gate}\n    {msg}\n")

    def test_every_ceiling_key_is_wired_to_a_gate_and_a_default(self):
        # SETS since phase 7 (fifth audit). `tokens_per_audit` now has two gates — the
        # statistical one that compares an estimate of the next session against what is
        # left, and the hard one that fires when measured spend has already passed the
        # ceiling. The claim was and remains "no ceiling key is unwired"; what changed is
        # that the mapping is no longer one-to-one, and a list comparison was asserting
        # that as well without meaning to.
        self.assertEqual(set(budget.CEILING_KEYS), set(budget.GATE_KEY.values()))
        for key in budget.CEILING_KEYS:
            self.assertIn(key, util.CONF_DEFAULTS, key)
            self.assertIn(key, util.NUMERIC_CONF, key)
        self.assertEqual(set(CASES), set(budget.GATE_KEY),
                         "a gate with no fixture is a gate nobody drove")


class EachFiresForItsOwnReason(unittest.TestCase):
    """The control the phase asks for. Six fixtures could be one gate catching all six;
    this runs every fixture against every ceiling ALONE and asserts the cross terms are
    silent."""

    def test_each_case_violates_only_the_ceiling_it_is_about(self):
        """All six armed on every fixture. Exactly one fires, and it is the one whose
        condition that fixture violates — the other five are armed AND satisfied."""
        for name, (conf, make_events) in CASES.items():
            with self.subTest(case=name):
                fx, st = fixture_state()
                self.addCleanup(fx.cleanup)
                self.assertEqual(len(budget.CEILING_KEYS),
                                 sum(1 for k in budget.CEILING_KEYS if conf.get(k)),
                                 "the case did not arm every ceiling")
                fired = budget.gates(st, conf, make_events(), charter=CHARTER)
                self.assertIsNotNone(fired, f"{name} did not fire with all six armed")
                self.assertEqual(name, fired[0],
                                 f"{name}'s fixture tripped {fired[0]} instead")
                print(f"OWN REASON  {name:<26} all 6 armed -> {fired[0]}")

    def test_loosening_only_that_one_ceiling_silences_the_case(self):
        """The other half of the same claim. Take the tight ceiling back off and NOTHING
        fires — so the refusal above came from that ceiling and not from a neighbour."""
        for name, (conf, make_events) in CASES.items():
            with self.subTest(case=name):
                fx, st = fixture_state()
                self.addCleanup(fx.cleanup)
                relaxed = dict(conf)
                relaxed[budget.GATE_KEY[name]] = str(LOOSE[budget.GATE_KEY[name]])
                self.assertIsNone(
                    budget.gates(st, relaxed, make_events(), charter=CHARTER),
                    f"{name} still fires with {budget.GATE_KEY[name]} loosened, so it "
                    f"was never the reason")
                print(f"SILENCED    {name:<26} {budget.GATE_KEY[name]} loosened -> None")


class PricedOnMeasuredFiguresOnly(unittest.TestCase):

    def test_the_coverage_of_the_figure_is_printed_beside_it(self):
        fx, st = fixture_state()
        self.addCleanup(fx.cleanup)
        ev = events_expensive() + [{"event": "session_finished", "session_id": "z" * 16}]
        fired = budget.gates(st, on(tokens_per_audit=210_000), ev, charter=CHARTER)
        self.assertIsNotNone(fired)
        self.assertIn("measured on 4 of 5 finished session(s)", fired[1]["why"])
        print("MEASURED   ", fired[1]["why"][:180])

    def test_an_unmeasured_run_is_not_gated_on_a_guess(self):
        """Every session finished, none carried a token figure. There is no measured mean,
        so the token ceilings say nothing rather than inventing one."""
        fx, st = fixture_state()
        self.addCleanup(fx.cleanup)
        ev = [{"event": "session_finished", "session_id": f"{i:016d}"} for i in range(9)]
        self.assertIsNone(budget.mean_session_cost(budget.spend(st, ev)))
        # PHASE 12: the claim above is unchanged and the ANSWER got stronger. This
        # asserted `None` — no gate fires — which was right about the ceiling and wrong
        # about what the caller learns, because `None` is also what a satisfied gate
        # returns. At 0% coverage the honest outcome is a third one: NO VERDICT, naming
        # the coverage and the floor. What must still hold is that it is not a STOP.
        for key in ("tokens_per_audit", "tokens_per_session", "tokens_per_pass"):
            got = budget.gates(st, on(**{key: 1}), ev, charter=CHARTER)
            self.assertEqual(budget.NO_VERDICT, got[0], key)
            self.assertNotIn(got[0], budget.GATE_KEY, f"{key} fired on a guess")
            self.assertIn("0.0%", got[1]["why"])
        print("UNMEASURED  0 measured of 9 finished -> no token ceiling fires, and the "
              "outcome is `no-verdict`, NOT a pass "
              "(a ceiling over an unmeasured majority is a guess with an exit code)")

    def test_the_no_progress_share_reports_the_audits_reference_figure(self):
        fx, st = fixture_state()
        self.addCleanup(fx.cleanup)
        fired = budget.gates(st, on(no_progress_share_pct=50), events_barren(),
                             charter=CHARTER)
        self.assertEqual("stop-no-progress-share", fired[0])
        self.assertEqual(budget.OUROBOROS4_BARREN_SHARE_PCT, fired[1]["reference"])
        print(f"NO-PROGRESS share={fired[1]['share']}% over bound "
              f"{fired[1]['cap']}%; Ouroboros-4 reference "
              f"{fired[1]['reference']}%")


class TheAuditCeilingRefusesBeforeTheOverspend(unittest.TestCase):
    """`a-budget-that-only-gates-the-start`: a bound that fires only after the spend has
    landed has described a fact rather than prevented one."""

    def test_it_refuses_while_the_budget_still_has_room_but_not_enough(self):
        fx, st = fixture_state()
        self.addCleanup(fx.cleanup)
        ev = events_expensive()          # 4 x 50,000 = 200,000 spent, mean 50,000
        tok = budget.spend(st, ev)
        self.assertEqual(200_000, tok["total"])
        self.assertEqual(50_000, budget.mean_session_cost(tok))

        # 210,000: 10,000 left, which is REAL headroom and still cannot cover a session.
        fired = budget.gates(st, on(tokens_per_audit=210_000), ev, charter=CHARTER)
        self.assertEqual("stop-budget-audit", fired[0])
        self.assertLess(fired[1]["spent"], fired[1]["cap"],
                        "it fired only after the cap was already exceeded")
        print(f"BEFORE      spent {fired[1]['spent']:,} of {fired[1]['cap']:,} "
              f"(under the cap), next session estimated {fired[1]['estimate']:,} "
              f"-> refused before dispatch")

        # 260,000 covers one more session, so it does not fire. The negative arm is what
        # makes the positive one mean anything.
        self.assertIsNone(budget.gates(st, on(tokens_per_audit=260_000), ev,
                                       charter=CHARTER))


class TheMarginalValueRuleIsAStop(unittest.TestCase):

    def test_it_counts_accepted_evidence_and_not_sessions(self):
        ev = events_stalled()
        # 10, not 9: the first session's `accepted` lands BEFORE its own
        # `session_finished`, so all ten finished sessions are counted.
        self.assertEqual(10, budget.sessions_since_accepted_evidence(ev))
        # The sessions DID move state — they filed findings. Counting movement would have
        # reset the window and the rule would never fire on the shape it is about.
        self.assertTrue(any(e.get("verb") == "file-finding" for e in ev))

    def test_the_derived_default_is_printed_with_its_derivation(self):
        fx, st = fixture_state()
        self.addCleanup(fx.cleanup)
        fired = budget.gates(st, on(marginal_value_window=8), events_stalled(),
                             charter=CHARTER)
        self.assertEqual("stop-marginal-value", fired[0])
        self.assertEqual(8, fired[1]["derived_default"])
        self.assertIn("6.8", fired[1]["derivation"])
        self.assertIn("STOPS", fired[1]["why"], "the rule is a stop, not a warning")
        print(f"MARGINAL    {fired[1]['why']}")

    def test_an_accepted_transition_resets_the_window(self):
        ev = events_stalled() + [accepted("z" * 16)]
        self.assertEqual(0, budget.sessions_since_accepted_evidence(ev))


class OffByDefaultAndNoMoney(unittest.TestCase):

    def test_every_ceiling_ships_off_and_changes_nothing(self):
        fx, st = fixture_state()
        self.addCleanup(fx.cleanup)
        self.assertEqual("off", util.CONF_DEFAULTS[budget.FLAG])
        for key in budget.CEILING_KEYS:
            self.assertEqual("0", util.CONF_DEFAULTS[key], key)
        # Armed but every ceiling 0: still nothing fires, on the events that trip all six.
        for make in (events_expensive, events_barren, events_repeated_charter,
                     events_stalled):
            self.assertIsNone(budget.gates(st, {budget.FLAG: "on"}, make(),
                                           charter=CHARTER))
            self.assertIsNone(budget.gates(st, {}, make(), charter=CHARTER))
        print("DEFAULTS    flag off, all six ceilings 0 -> no gate fires on any of the "
              "four fixtures that trip them when armed")

    def test_zero_means_off_and_never_refuse_everything(self):
        self.assertEqual(0, budget._ceiling({"tokens_per_audit": "0"}, "tokens_per_audit"))
        self.assertEqual(0, budget._ceiling({}, "tokens_per_audit"))

    def test_no_dollar_amount_is_ever_printed(self):
        fx, st = fixture_state()
        self.addCleanup(fx.cleanup)
        texts = [budget.gate_message(*budget.gates(st, conf, make(), charter=CHARTER))
                 for conf, make in CASES.values()]
        texts += budget.derived_report()
        for text in texts:
            for money in ("$", "USD", "EUR", "cents", "dollar"):
                self.assertNotIn(money, text, f"a money figure appeared in: {text[:80]}")
        print(f"NO MONEY    {len(texts)} refusal/derivation strings, 0 currency tokens")


# The FIELDS each gate in `budget.gates` reads out of an event, and the event kind it
# reads them from. Declared here rather than derived, because the whole defect was a gate
# reading a field nobody wrote — a check that derived the list FROM the gate would derive
# the mistake with it, and a check that derived it from the events could never notice a
# field that is missing everywhere.
GATE_FIELDS = {
    "stop-budget-audit": [("session_finished", "tokens")],
    "stop-budget-session": [("session_finished", "tokens")],
    "stop-budget-pass": [("session_finished", "tokens")],
    "stop-no-progress-share": [("session_finished", "tokens"),
                               ("state_transition", "verb")],
    "stop-charter-repeats": [("session_dispatched", "charter_hash")],
    "stop-marginal-value": [("state_transition", "to_status")],
    # PHASE 7 (fifth audit): reads the same measured figure the audit gate reads, and
    # reads it as a LOWER BOUND rather than as the input to a mean.
    "stop-known-overrun": [("session_finished", "tokens")],
}


@needs_live_corpus
class AGateMustReadTheFieldProductionWrites(unittest.TestCase):
    """PHASE 5 (fourth audit). `charter_attempts` keyed on `charter` — a field the
    envelope has never emitted — so the repeat gate could not fire on any real run, and
    the test that covered it was green because the test invented the event.

    Everything here reads THIS REPOSITORY'S OWN event stream. A fixture is the thing that
    hid the defect, so a fixture cannot be the thing that proves it fixed."""

    @classmethod
    def setUpClass(cls):
        envelope = xcheck_submodule("envelope")
        cls.events = envelope.read_events(REPO)
        cls.by_kind = collections.defaultdict(list)
        for e in cls.events:
            cls.by_kind[e.get("event")].append(e)

    def test_the_production_stream_has_charter_hash_and_no_charter(self):
        """The audit's figures, reproduced as an assertion rather than quoted."""
        rows = self.by_kind["session_dispatched"]
        with_hash = [e for e in rows if e.get("charter_hash")]
        with_text = [e for e in rows if e.get("charter")]
        print(f"\n  REAL STREAM {len(self.events)} events, "
              f"{len(rows)} session_dispatched: "
              f"{len(with_text)} carry `charter`, {len(with_hash)} carry `charter_hash`")
        self.assertTrue(rows, "this checkout carries no dispatch events to check")
        self.assertEqual([], with_text,
                         "an event carrying `charter` would mean the emitter changed")
        self.assertEqual(len(rows), len(with_hash))

    def test_the_repeat_counter_runs_on_the_real_stream(self):
        """The census the default has to be argued from, computed on real dispatches.

        What this arm proves and what it does not: the COUNTER now finds real charters
        in real events, where before it found none. It cannot drive `gates()` end to end
        from here, because the gate takes charter TEXT and the stream carries a DIGEST,
        which does not invert — and none of the queue's charter strings hashes to one of
        these, since the runner composes the dispatched charter. The text -> hash -> count
        closure is proved in the next test, on events in the production shape. Saying
        which half each arm covers is the point; a single arm claiming both would be the
        same overclaim this phase is fixing."""
        counts = budget.charter_attempts(self.events)
        self.assertTrue(counts, "the counter found nothing in the real stream — it is "
                                "reading a field production does not write")
        worst_hash, worst = max(counts.items(), key=lambda kv: kv[1])
        hist = dict(sorted(collections.Counter(counts.values()).items()))
        print(f"  CENSUS      {len(counts)} distinct charter(s), "
              f"max {worst} attempt(s); attempts -> charters {hist}")
        self.assertEqual(worst, counts[worst_hash])
        self.assertGreater(worst, 1, "a stream where no charter repeats cannot exercise "
                                     "a repeat limit")
        once = sum(1 for n in counts.values() if n == 1)
        print(f"  ARGUABLE    {once} of {len(counts)} charters were dispatched once and "
              f"a limit would never have touched them; the ceiling is a decision about "
              f"the {len(counts) - once} that repeated, up to {worst}")

    def test_the_other_gates_run_on_the_real_stream_too(self):
        """`gates()` itself, over 1,043 real events, with each ceiling set from the real
        figure it reads. A gate that cannot fire on this repository's own history is a
        gate nobody has run."""
        fx, st = fixture_state()
        tok = budget.spend(st, self.events)
        print(f"  REAL SPEND  {tok['total']:,} tokens, "
              f"{budget.coverage_note(tok)}, "
              f"share that moved nothing = {tok['share_that_moved_nothing_pct']}%")
        fired = []
        for ceiling, value in (("no_progress_share_pct", 1),
                               ("tokens_per_session", 1),
                               ("tokens_per_audit", 1),
                               ("marginal_value_window", 1)):
            got = budget.gates(st, all_armed(**{ceiling: value}), self.events,
                               charter=CHARTER)
            if got:
                fired.append((ceiling, got[0]))
                print(f"    {ceiling:<24} -> {got[0]}")
        self.assertTrue(fired, "no gate could fire on the real stream at any ceiling")
        # CONTROL: with every ceiling generous, no CEILING fires on the same real stream
        # — so the firings above are the CONDITIONS being met, not the gates being
        # switched on.
        #
        # PHASE 12: this asserted `None` outright. The real stream measures 7 of 158
        # sessions (4.4%), which is under the statistical coverage floor, so the honest
        # answer here stopped being `None` and became `no-verdict`. The control's point is
        # untouched — no ceiling fired — and is now asserted directly rather than through
        # a None that conflated "nothing fired" with "nothing could be judged".
        got = budget.gates(st, all_armed(), self.events, charter=CHARTER)
        self.assertEqual(budget.NO_VERDICT, got[0],
                         "a gate fired on the real stream with every ceiling generous")
        self.assertNotIn(got[0], budget.GATE_KEY)
        self.assertEqual(4.4, got[1]["coverage_pct"])

    def test_the_gate_reaches_the_counter_through_one_shared_helper(self):
        """The caller has TEXT, the events carry a HASH. Two hashing sites that must
        agree by hand is the next version of this bug, so there is one."""
        envelope = xcheck_submodule("envelope")
        charter = "check the documented invariants hold in the unit"
        self.assertEqual(envelope.sha256_text(charter), budget.charter_key(charter))
        self.assertEqual(budget.charter_key(f"  {charter}  "),
                         budget.charter_key(charter), "the key must be stripped once")

        fx, st = fixture_state()
        events = [dispatched("0" * 16, charter) for _ in range(3)]
        fired = budget.gates(st, all_armed(charter_repeat_limit=3), events,
                             charter=charter)
        self.assertIsNotNone(fired, "text -> hash -> count did not close")
        self.assertEqual("stop-charter-repeats", fired[0])
        print(f"  ONE HELPER  {budget.gate_message(*fired)[:120]}")

    def test_counterfactual_the_old_synthetic_shape_now_counts_zero(self):
        """A fixture carrying only `charter`, which is what the replaced test built.
        It must count NOTHING — a counter that read both fields would still be green
        over an emitter that stopped writing either one."""
        old_shape = [{"event": "session_dispatched", "session_id": f"{i:016d}",
                      "charter": CHARTER} for i in range(4)]
        self.assertEqual({}, budget.charter_attempts(old_shape),
                         "the counter still reads the field production never wrote")
        fx, st = fixture_state()
        self.assertIsNone(
            budget.gates(st, all_armed(charter_repeat_limit=1), old_shape,
                         charter=CHARTER),
            "the gate fired on events shaped the way the broken fixture was")
        # CONTROL: the same four dispatches in the production shape DO fire, so the
        # zero above is the shape and not the fixture being empty.
        new_shape = [dispatched(f"{i:016d}", CHARTER) for i in range(4)]
        self.assertEqual(
            "stop-charter-repeats",
            budget.gates(st, all_armed(charter_repeat_limit=1), new_shape,
                         charter=CHARTER)[0])
        print("  COUNTERFACT old shape -> 0 attempts, gate silent; "
              "same four in the production shape -> stop-charter-repeats")

    def test_every_other_gate_reads_fields_the_real_stream_carries(self):
        """The same audit, run over the other five gates. A field that is declared for a
        gate and appears on ZERO production events of that kind is this defect again."""
        print("  FIELD AUDIT gate -> (event kind, field): events carrying it")
        missing = []
        for kind, wants in sorted(GATE_FIELDS.items()):
            for event_kind, field in wants:
                rows = self.by_kind[event_kind]
                have = [e for e in rows if e.get(field) is not None]
                mark = "OK " if have else "NONE"
                print(f"    {mark} {kind:<24} {event_kind}.{field:<14} "
                      f"{len(have)} of {len(rows)}")
                if rows and not have:
                    missing.append(f"{kind}: {event_kind}.{field} is on 0 of "
                                   f"{len(rows)} real events")
        self.assertEqual([], missing, "\n".join(missing))

    def test_the_declared_gate_list_is_the_whole_gate_list(self):
        """A gate added without a row here would go unaudited, which is how the first
        one got in."""
        self.assertEqual(sorted(budget.GATE_KEY), sorted(GATE_FIELDS),
                         "a gate exists that this audit does not cover")


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------- PHASE 12

class TheHardCapReachesTheProvider(unittest.TestCase):
    """The audit's point in one sentence: a historical mean is a forecast, not a cap.

    A ceiling compared against measured spend can only report an overspend that already
    happened. The only form that STOPS a session is a number the provider is told before
    the model runs, which means it has to be in the argv.
    """

    def test_the_cap_is_in_the_recorded_argv(self):
        runner = xcheck_submodule("runner")
        argv = runner.build_cmd(
            on(tokens_per_session=40_000,
               auditor_cmd="codex exec --max-tokens {token_cap} {prompt}"),
            "Auditor", "GO")
        self.assertEqual(["codex", "exec", "--max-tokens", "40000", "GO"], argv)
        self.assertIn("40000", argv)
        print(f"\n  HARD CAP   argv={argv}")

    def test_the_flag_is_the_operators_and_the_number_is_xchecks(self):
        """xcheck does not know what a provider calls its output limit and does not
        guess. Inventing `--max-tokens` would be choosing a provider nobody named — the
        same error `telemetry_adapter` exists to avoid."""
        runner = xcheck_submodule("runner")
        argv = runner.build_cmd(
            on(tokens_per_session=1234,
               auditor_cmd="claude -p --limit={token_cap} {prompt}"), "Auditor", "GO")
        self.assertIn("--limit=1234", argv)
        src = (REPO / "xcheck" / "runner.py").read_text(encoding="utf-8")
        self.assertNotIn('"--max-tokens"', src, "the tool invented a provider's flag")

    def test_a_ceiling_with_nowhere_to_go_is_REFUSED_not_warned(self):
        """An operator who set `tokens_per_session` believes they have a cap. If the
        command carries no placeholder they have a forecast, and being told after the
        run is exactly the failure the audit named."""
        fx, st = fixture_state()
        self.addCleanup(fx.cleanup)
        fired = budget.gates(st, on(tokens_per_session=1000), events_expensive(),
                             charter=CHARTER, cmd="codex exec {prompt}")
        self.assertEqual("stop-dispatch-cap", fired[0])
        self.assertIn("{token_cap}", fired[1]["why"])
        print(f"  REFUSED    {fired[1]['why'][:150]}")

    def test_CONTROL_the_same_conf_with_the_placeholder_passes(self):
        fx, st = fixture_state()
        self.addCleanup(fx.cleanup)
        ok = budget.gates(st, on(tokens_per_session=1000), events_expensive(),
                          charter=CHARTER,
                          cmd="codex exec --max-tokens {token_cap} {prompt}")
        self.assertNotEqual("stop-dispatch-cap", (ok or ("", {}))[0])

    def test_no_command_offered_is_not_a_missing_cap(self):
        """`cmd=None` means nothing was handed over to inspect. Refusing that would stop
        every caller that asks about ceilings without being about to run anything."""
        self.assertIsNone(budget.cap_problem(on(tokens_per_session=1000), None))
        self.assertIsNotNone(budget.cap_problem(on(tokens_per_session=1000), "codex {prompt}"))

    def test_the_cap_is_not_substituted_when_budgets_are_off(self):
        """PHASE 13 corrected this. It asserted the placeholder SURVIVES with the flag
        off, which was true of the code and wrong as a behaviour: `--max-tokens
        {token_cap}` reaching a provider verbatim is a request for a limit literally
        named `{token_cap}`. The CLI rejects it, the session fails for a reason nothing
        in the log explains, and the operator's real mistake — budgets off — is invisible.

        The claim this arm was FOR is unchanged and still checked: with the flag off, no
        cap is applied. What changed is that the unfilled placeholder is now REFUSED at
        build time rather than shipped."""
        runner = xcheck_submodule("runner")
        with self.assertRaises(SystemExit) as e:
            runner.build_cmd({"tokens_per_session": "999",
                              "auditor_cmd": "codex --max-tokens {token_cap} {prompt}"},
                             "Auditor", "GO")
        self.assertIn("{token_cap}", str(e.exception))
        self.assertIn("nothing filled it", str(e.exception))
        # And the flag being off is genuinely why: no cap number reaches an argv.
        clean = runner.build_cmd({"tokens_per_session": "999",
                                  "auditor_cmd": "codex {prompt}"}, "Auditor", "GO")
        self.assertNotIn("999", clean, "a cap was applied with the flag off")


class GatesAreStatisticalOrHard(unittest.TestCase):
    """The declared list. A gate whose kind nobody wrote down is a gate whose exemption
    from the coverage floor is an accident of where it sits in a function."""

    def test_every_gate_is_declared_exactly_once(self):
        both = list(budget.STATISTICAL_GATES) + list(budget.HARD_GATES)
        self.assertEqual(len(both), len(set(both)), "a gate is in both lists")
        self.assertEqual(set(budget.GATE_KEY) | {"stop-dispatch-cap"}, set(both))
        print(f"\n  STATISTICAL {', '.join(budget.STATISTICAL_GATES)}")
        print(f"  HARD        {', '.join(budget.HARD_GATES)}")

    def test_the_hard_gates_fire_below_the_floor(self):
        """A per-dispatch limit needs no history, so the floor does not silence it."""
        fx, st = fixture_state()
        self.addCleanup(fx.cleanup)
        ev = [{"event": "session_finished", "session_id": f"{i:016d}"} for i in range(9)]
        ev += [dispatched(f"{i:016d}", CHARTER) for i in range(4)]
        self.assertEqual(0.0, budget.spend(st, ev)["coverage_pct"])
        fired = budget.gates(st, on(charter_repeat_limit=3), ev, charter=CHARTER)
        self.assertEqual("stop-charter-repeats", fired[0])
        print("  BELOW FLOOR at 0.0% coverage stop-charter-repeats still fires "
              "(counting one charter's own dispatches extrapolates from nothing)")


class ThinCoverageProducesNoVerdictNotAPass(unittest.TestCase):
    """`n-gates-need-n-plus-n-arms`, and the distinction the phase turns on. A gate that
    cannot see enough to decide and returns the same value as a satisfied gate has told
    its caller everything is fine."""

    def _events(self, measured, total, tokens=500_000):
        ev = []
        for i in range(total):
            sid = f"{i:016d}"
            # Each session MOVES state, so the barren-share gate has nothing to catch
            # and the only variable between the arms is telemetry coverage.
            ev += [dispatched(sid, "P-01"), moved(sid), accepted(sid)]
            ev.append({"event": "session_finished", "session_id": sid,
                       **({"tokens": tokens} if i < measured else {})})
        return ev

    # PHASE 7 (fifth audit): the ceiling here is ABOVE what these fixtures have already
    # spent, on purpose. The arms are about the floor's own question — extrapolating the
    # next session's cost from a minority — and a ceiling measured spend has already
    # passed is not that question: it is answered from measurement alone, above the
    # floor, by `stop-known-overrun`. With the old 1,000-token ceiling both arms would
    # now be measuring the hard gate and the floor would go untested.
    # 8 x 500k = 4,000,000 measured is UNDER it, so the hard gate stays silent and the
    # statistical one fires on the estimate — which is what these arms are about.
    CEILING = 4_200_000

    def test_COUNTERFACTUAL_below_the_floor_the_gate_refuses_to_judge(self):
        fx, st = fixture_state()
        self.addCleanup(fx.cleanup)
        ev = self._events(measured=2, total=10)          # 20%
        got = budget.gates(st, on(tokens_per_audit=self.CEILING), ev, charter="P-01")
        self.assertEqual(budget.NO_VERDICT, got[0])
        self.assertNotIn(got[0], budget.GATE_KEY)
        self.assertIn("20.0%", got[1]["why"])
        self.assertIn("50.0% floor", got[1]["why"])
        self.assertIn("NOT a pass", got[1]["why"])
        print(f"\n  NO VERDICT {got[1]['why'][:190]}")

    def test_CONTROL_above_the_floor_the_same_fixture_produces_the_verdict(self):
        """A floor that silenced every gate would pass the arm above and mean nothing."""
        fx, st = fixture_state()
        self.addCleanup(fx.cleanup)
        ev = self._events(measured=8, total=10)          # 80%
        got = budget.gates(st, on(tokens_per_audit=self.CEILING), ev, charter="P-01")
        self.assertEqual("stop-budget-audit", got[0])
        print(f"  VERDICT    at 80% coverage the SAME fixture -> {got[0]}")

    def test_no_verdict_and_pass_are_different_values(self):
        """Asserted directly: the two outcomes a caller must not confuse."""
        fx, st = fixture_state()
        self.addCleanup(fx.cleanup)
        clean = budget.gates(st, all_armed(), self._events(measured=8, total=10),
                             charter="P-01")
        thin = budget.gates(st, all_armed(), self._events(measured=2, total=10),
                            charter="P-01")
        self.assertIsNone(clean, "a generous conf above the floor should be a PASS")
        self.assertEqual(budget.NO_VERDICT, thin[0])
        self.assertNotEqual(clean, thin)
        print(f"  DISTINCT   pass={clean!r}  vs  no-verdict={thin[0]!r}")

    def test_a_no_verdict_does_not_stop_a_dispatch(self):
        """Refusing to judge is not judging against. A machine whose only fault is an
        unconfigured telemetry adapter must not be unable to work."""
        src = (REPO / "xcheck" / "decision.py").read_text(encoding="utf-8")
        self.assertIn("fired[0] != budget.NO_VERDICT", src)


@needs_live_corpus
class SessionsAreAttributedToPassesAndCharters(unittest.TestCase):
    """On the REAL stream, not a fixture. `tokens_per_pass` divided the audit's whole
    spend by its done passes until this existed."""

    @classmethod
    def setUpClass(cls):
        envelope = xcheck_submodule("envelope")
        cls.st = state_mod.load_state(REPO / "audit")
        cls.events = envelope.read_events(REPO)

    def test_every_dispatched_session_resolves_to_one_pass_and_one_charter(self):
        placed = budget.attribution(self.st, self.events)
        rows, unplaced = budget.attribution_report(self.st, self.events)
        print(f"\n  ATTRIBUTION {len(placed)} dispatched session(s) -> {len(rows)} pass(es),"
              f" {len(unplaced)} unattributable")
        for row in sorted(rows, key=lambda r: -r[3])[:5]:
            print(f"    {row[0]:<6} dispatched={row[1]:<3} measured={row[2]:<2} "
                  f"tokens={row[3]:,}")
        for sid, why in unplaced:
            print(f"    UNATTRIBUTABLE {sid}: {why}")
        self.assertEqual([], unplaced)
        for sid, (pass_id, h) in placed.items():
            self.assertNotEqual(budget.UNATTRIBUTED, pass_id, sid)
            self.assertTrue(h, f"{sid} carries no charter hash")

    def test_the_attributed_tokens_account_for_every_measured_one(self):
        """A per-pass figure that quietly drops what it could not place improves as
        attribution gets worse."""
        rows, _ = budget.attribution_report(self.st, self.events)
        self.assertEqual(budget.spend(self.st, self.events)["total"],
                         sum(r[3] for r in rows))

    def test_the_current_pass_is_measured_not_averaged(self):
        """The defect, side by side. The old figure divided total spend by DONE passes;
        the measured passes cost three times it."""
        tok = budget.spend(self.st, self.events)
        averaged = budget._spend_per_pass(tok, self.st)
        rows, _ = budget.attribution_report(self.st, self.events)
        real = sorted(r[3] for r in rows if r[2])
        print(f"  AVERAGED   {averaged:,} tokens 'per pass' = {tok['total']:,} / "
              f"{len([q for q in self.st.queue if q.coverage and q.coverage.status == 'done'])}"
              f" done passes")
        print(f"  MEASURED   the {len(real)} passes with a measured session cost "
              f"{real[0]:,}..{real[-1]:,}, median {real[len(real) // 2]:,}")
        self.assertLess(averaged, min(real),
                        "the averaged figure was not below every measured pass")
        for pass_id in (r[0] for r in rows if r[2]):
            spent, (measured, dispatched) = budget.current_pass_spend(
                self.st, self.events, pass_id)
            self.assertGreater(spent, 0, pass_id)
            self.assertLessEqual(measured, dispatched)

    def test_an_unknown_charter_is_reported_not_dropped(self):
        ev = list(self.events) + [{"event": "session_dispatched",
                                   "session_id": "f" * 16, "charter_hash": "0" * 64}]
        rows, unplaced = budget.attribution_report(self.st, ev)
        self.assertEqual(1, len(unplaced))
        self.assertIn("matches no queue entry", unplaced[0][1])
        print(f"  REPORTED   {unplaced[0][1][:120]}")


@needs_live_corpus
class TheDerivedDefaultsAreReDerived(unittest.TestCase):
    """Criterion 9: a derived number that no longer follows from the data is corrected,
    not kept. `xcheck status` prints these, so they are claims."""

    @classmethod
    def setUpClass(cls):
        envelope = xcheck_submodule("envelope")
        cls.st = state_mod.load_state(REPO / "audit")
        cls.events = envelope.read_events(REPO)

    def test_charter_repeat_limit_is_re_derived_against_the_real_charters(self):
        attempts = sorted(budget.charter_attempts(self.events).values(), reverse=True)
        under = sum(1 for v in attempts if v <= 3)
        print(f"\n  RE-DERIVED charter_repeat_limit=3: {len(attempts)} charters, "
              f"{under} at or under 3, {len(attempts) - under} over, max {attempts[0]}")
        self.assertEqual(3, budget.DERIVED_DEFAULTS["charter_repeat_limit"][0])
        self.assertIn("54 attempts", budget.DERIVED_DEFAULTS["charter_repeat_limit"][1],
                      "the derivation does not cite the data it was re-checked against")
        self.assertEqual(54, attempts[0], "the cited maximum drifted from the corpus")

    def test_tokens_per_pass_is_newly_derivable_and_states_its_coverage(self):
        rows, _ = budget.attribution_report(self.st, self.events)
        real = sorted(r[3] for r in rows if r[2])
        value, why = budget.DERIVED_DEFAULTS["tokens_per_pass"]
        print(f"  RE-DERIVED tokens_per_pass={value:,}: observed max {real[-1]:,}, "
              f"median {real[len(real) // 2]:,} over {len(real)} measured passes")
        self.assertGreater(value, real[-1], "the default is under an observed pass")
        self.assertIn(f"{real[-1]:,}", why)
        self.assertIn("4.4% coverage", why, "a derived figure hides its coverage")

    def test_the_barren_share_cannot_be_re_derived_below_the_floor(self):
        """The honest non-answer. Measured barren share is 0.0% here — over 7 of 158
        sessions, which is precisely what the floor exists to refuse to reason from."""
        tok = budget.spend(self.st, self.events)
        self.assertEqual(0.0, tok["share_that_moved_nothing_pct"])
        self.assertIsNotNone(budget.coverage_floor_problem(tok))
        self.assertEqual(85.4, budget.OUROBOROS4_BARREN_SHARE_PCT)
        print(f"  NOT RE-DERIVED no_progress_share_pct / marginal_value_window: this "
              f"corpus measures {tok['share_that_moved_nothing_pct']}% barren over "
              f"{tok['sessions_measured']}/{tok['sessions_finished']} sessions "
              f"({tok['coverage_pct']}%), under the floor — it can neither confirm nor "
              f"refute the frozen Ouroboros-4 figure of "
              f"{budget.OUROBOROS4_BARREN_SHARE_PCT}%, and the defaults stand on that run")

    def test_every_derived_default_names_a_real_ceiling(self):
        for key in budget.DERIVED_DEFAULTS:
            self.assertIn(key, budget.CEILING_KEYS, key)

    def test_a_derivation_may_not_promise_a_floor_the_ceiling_no_longer_waits_for(self):
        """PHASE 7 (fifth audit), and the correction criterion 9 asks for. A derivation
        is a claim about WHEN the gate fires, and this phase moved two ceilings across
        that line: `tokens_per_pass`'s derivation still said the gate would not fire
        until half the sessions were measured, which stopped being true when the hard
        half landed. Re-checking the number is not enough — the sentence around it is
        the part an operator reads."""
        floor_words = ("until half the sessions are measured",
                       "will not fire below the coverage floor")
        for key, (_value, why) in budget.DERIVED_DEFAULTS.items():
            hard = key in budget.HARD_CEILING
            said_floor = [w for w in floor_words if w in why]
            print(f"    {key:<22} {'hard' if hard else 'statistical':<12} "
                  f"{'promises the floor' if said_floor else ''}")
            if hard:
                self.assertEqual([], said_floor,
                                 f"{key} is hard and its derivation promises the floor")


# ------------------------------------------------------ PHASE 7 (fifth audit)

class AKnownOverrunIsNotAStatisticalQuestion(unittest.TestCase):
    """The audit's reproduction: 1,000 measured tokens against a 100-token ceiling at
    33.3% coverage returned `no-verdict`, and the decision engine continues on
    `no-verdict`. The uncertainty was never the point — measured spend is a LOWER BOUND
    and the unmeasured sessions can only add to it.

    The control below is what keeps the floor intact: a ceiling that has NOT been reached
    is still a question about the next session's cost, and that is still refused."""

    def _events(self, measured, total, tokens):
        ev = []
        for i in range(total):
            sid = f"{i:016d}"
            ev += [dispatched(sid, "P-01"), moved(sid), accepted(sid)]
            ev.append(finished(sid, tokens) if i < measured
                      else {"event": "session_finished", "session_id": sid})
        return ev

    def test_the_audits_case_is_a_hard_stop_above_the_floor(self):
        fx, st = fixture_state()
        self.addCleanup(fx.cleanup)
        got = budget.gates(st, on(tokens_per_audit=100), self._events(1, 3, 1_000),
                           charter="P-01")
        print("\n  THE AUDIT'S CASE  measured 1,000 / ceiling 100 / coverage 33.3%")
        print(f"    -> {got[0]}")
        print(f"    {budget.gate_message(*got)[:200]}")
        self.assertEqual("stop-known-overrun", got[0])
        self.assertEqual((1_000, 100), (got[1]["measured"], got[1]["cap"]))
        self.assertIn("LOWER bound", got[1]["why"])
        self.assertIn("tokens_per_audit", budget.gate_message(*got))

    def test_CONTROL_under_the_ceiling_the_floor_still_refuses_to_judge(self):
        """The arm that stops the fix from deleting the floor. Same thin coverage, same
        fixture, a ceiling that has NOT been reached."""
        fx, st = fixture_state()
        self.addCleanup(fx.cleanup)
        got = budget.gates(st, on(tokens_per_audit=1_000_000), self._events(1, 3, 1_000),
                           charter="P-01")
        print(f"  CONTROL           measured 1,000 / ceiling 1,000,000 / same 33.3% "
              f"-> {got[0]}")
        self.assertEqual(budget.NO_VERDICT, got[0])

    def test_the_exemption_is_declared_not_a_branch_order(self):
        """Asserted from the declaration rather than from which branch runs first, and
        the floor's own message names the gates it does not cover."""
        self.assertIn("stop-known-overrun", budget.HARD_GATES)
        self.assertNotIn("stop-known-overrun", budget.STATISTICAL_GATES)
        thin = budget.coverage_floor_problem(
            {"coverage_pct": 20.0, "sessions_measured": 2, "sessions_finished": 10})
        print(f"  DECLARED HARD     {', '.join(budget.HARD_GATES)}")
        self.assertIn("stop-known-overrun", thin,
                      "the floor exempts a gate its own message never mentions")

    def test_the_pass_ceiling_is_hard_too_and_names_the_key_that_fired(self):
        """`tokens_per_pass` is hard for the same reason: spend attributed to a pass,
        session by session, is a lower bound on that pass.

        Which needs the attribution to have JOINED. This fixture dispatches under the
        queue entry's own charter — `events_one_expensive_pass` dispatches under the pass
        ID, which hashes to nothing the queue holds, so its 200,000 tokens are
        unattributed and the SAME ceiling is reached through the statistical door
        instead. Both arms below, because the difference is the whole point: `already
        over` and `one more would go over` are different claims."""
        fx, st, attributed = measured_pass()
        self.addCleanup(fx.cleanup)
        hard = budget.gates(st, on(tokens_per_pass=1_000), attributed, charter=CHARTER)
        # A SECOND state: `st` now carries session records for these ids, and the soft
        # arm's point is a pass whose spend nothing could attribute.
        plain_fx, plain_st = fixture_state()
        self.addCleanup(plain_fx.cleanup)
        soft = budget.gates(plain_st, on(tokens_per_pass=1_000),
                            events_one_expensive_pass(), charter=CHARTER)
        print(f"  PER PASS  attributed 200,000 / ceiling 1,000 -> {hard[0]} "
              f"(key={hard[1].get('key')})")
        print(f"            unattributed, next session estimated -> {soft[0]}")
        self.assertEqual("stop-known-overrun", hard[0])
        self.assertEqual("tokens_per_pass", hard[1]["key"])
        self.assertIn("tokens_per_pass", budget.gate_message(*hard),
                      "the message names the default key, not the one that fired")
        self.assertEqual("stop-budget-pass", soft[0])


class EveryCeilingHasOneOfTwoVerdicts(unittest.TestCase):
    """Criterion 5. Two tables, no ceiling in both and none in neither, each with the
    reason it cannot be known from measured spend alone."""

    def test_no_ceiling_is_left_unlisted(self):
        print("\n  EVERY CEILING")
        for key in budget.CEILING_KEYS:
            hard = key in budget.HARD_CEILING
            table = budget.HARD_CEILING if hard else budget.STATISTICAL_CEILING
            verdict = "known-overrun hard" if hard else "statistical, floor applies"
            print(f"    {key:<22} {verdict:<26} {table[key][:62]}")
            self.assertNotEqual(hard, key in budget.STATISTICAL_CEILING,
                                f"{key} is in both tables or in neither")
        self.assertEqual(set(budget.CEILING_KEYS),
                         set(budget.HARD_CEILING) | set(budget.STATISTICAL_CEILING),
                         "a ceiling with no declared verdict")
        self.assertEqual(set(), set(budget.HARD_CEILING) & set(budget.STATISTICAL_CEILING))

    def test_each_hard_ceiling_has_a_gate_that_can_actually_fire_on_it(self):
        """A declaration nothing drives is a comment. `charter_repeat_limit` is hard
        through its own gate; the two token keys through `known_overrun`, each on a
        fixture whose spend is attributed to what the key measures."""
        fx, st, ev = measured_pass()
        self.addCleanup(fx.cleanup)
        driven = {"charter_repeat_limit": budget.gates(
            st, all_armed(charter_repeat_limit=3), events_repeated_charter(),
            charter=CHARTER)}
        for key in ("tokens_per_audit", "tokens_per_pass"):
            driven[key] = budget.gates(st, on(**{key: 1_000}), ev, charter=CHARTER)
        for key, (kind, detail) in driven.items():
            print(f"    {key:<22} drives {kind}")
            self.assertIn(kind, budget.HARD_GATES,
                          f"{key} is declared hard and nothing hard fires on it")
            self.assertIn(key, budget.gate_message(kind, detail))


class TheCapIsUnverifiedNotEnforced(unittest.TestCase):
    """`cap_problem` checked that `{token_cap}` was PRESENT and nothing else, so
    `true {token_cap} {prompt}` passed — a command that enforces nothing and swallows the
    number as an argument. xcheck cannot verify an operator's wrapper without running it,
    and running it is the finding this project already closed (F-0098), so it stops
    implying enforcement rather than pretending to check."""

    CONF = on(tokens_per_session=1_500)
    ENFORCING = "agent --max-tokens {token_cap} {prompt}"

    def test_three_verdicts_and_no_fourth(self):
        cases = [("no-ceiling", {}, self.ENFORCING),
                 ("no-ceiling", on(), self.ENFORCING),
                 ("refused", self.CONF, "agent {prompt}"),
                 ("unverified", self.CONF, self.ENFORCING)]
        print("\n  CAP VERDICTS")
        for want, conf, cmd in cases:
            got, _msg = budget.cap_verdict(conf, cmd)
            print(f"    budgets={conf.get(budget.FLAG, 'off'):<4} {cmd:<40} -> {got}")
            self.assertEqual(want, got)
        self.assertEqual({"no-ceiling", "refused", "unverified"},
                         {c[0] for c in cases},
                         "a fourth verdict exists and nothing here drives it")

    def test_COUNTERFACTUAL_a_command_that_enforces_nothing_is_unverified(self):
        """Criterion 8, the audit's example verbatim. Not refused — the number DOES reach
        the command — and not a silent pass either."""
        got, msg = budget.cap_verdict(self.CONF, "true {token_cap} {prompt}")
        print(f"  COUNTERFACTUAL    `true {{token_cap}} {{prompt}}` -> {got}")
        print(f"    {msg.splitlines()[0][:160]}")
        self.assertEqual("unverified", got)
        self.assertIn("NOT verified", msg)
        self.assertIn("true {token_cap}", msg,
                      "the message does not name the case it cannot tell apart")

    def test_the_message_says_a_request_cap_is_not_a_session_total(self):
        """Criterion 7."""
        _got, msg = budget.cap_verdict(self.CONF, self.ENFORCING)
        line = [ln for ln in msg.splitlines() if "ONE request" in ln][0]
        print(f"  ONE REQUEST       {line[:150]}")
        self.assertIn("tokens_per_audit", msg, "it never says which key IS a spend ceiling")

    def test_nothing_here_claims_the_provider_was_limited(self):
        for cmd in (self.ENFORCING, "true {token_cap} {prompt}"):
            _got, msg = budget.cap_verdict(self.CONF, cmd)
            for word in ("enforced", "guaranteed", "the provider limited"):
                self.assertNotIn(word, msg, f"{word!r} claims what nothing observed")
