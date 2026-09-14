"""The eight pre-registered Ouroboros-4 metrics, against a fixture whose answers
are written down by hand.

Every expected value in this file is stated as a literal beside the arithmetic that
produces it, in a comment a reader can check without running anything. That is the
whole discipline: a metric verified against a number the metric code computed proves
only that the code is self-consistent, which is what `docs/ouroboros-4-preregistration.md`
forbids in advance. If a formula changes, these numbers must be re-derived by hand or
the change is not reviewable.

The fixture is one synthetic project — ten records, three passes, four sessions, six
transitions in `events.jsonl` — chosen so that no two metrics share a denominator by
accident. `reopen rate` and `fix durability` in particular must NOT be complements of
each other, or one of them is measuring nothing.
"""

import json
import re
import subprocess
import sys
import unittest

from tests.harness import (needs_repo_file, 
    CATALOGS, Fixture, REPO, XCHECK, finding_record, queue_pass, state_doc,
    xcheck_submodule,
)

decision = xcheck_submodule("decision")
envelope = xcheck_submodule("envelope")
util = xcheck_submodule("util")
state_mod = xcheck_submodule("state")

# A second dimension, so `durability_by_dimension` has something to break down.
CATALOGS_2D = {
    **CATALOGS,
    "dimensions": list(CATALOGS["dimensions"]) + [
        {"key": "contracts", "catches": "broken interface contracts", "norms": ["N1"]}],
}

# --------------------------------------------------------------------------
# the fixture, stated once
# --------------------------------------------------------------------------
#
# id       status               attempts  dimension   note
# F-0001   closed               0         invariants  first-pass close
# F-0002   closed               1         invariants  closed after one reopen
# F-0003   reopened             0         contracts   was CLOSED, undone by a human
# F-0004   fixed                0         contracts   awaiting a verdict
# F-0005   planned              0         invariants   remediation not started
# F-0006   disputed             0         invariants  validation killed it; carries a ruling
# F-0007   rejected             0         contracts   never reached validation
# F-0008   reported             0         invariants  untriaged; a recurrence of F-0001
# F-0009   superseded-by-class  0         contracts   member of CF-0001
# CF-0001  closed               0         contracts   first-pass close of a class

FINDINGS = [
    finding_record("F-0001", "closed"),
    finding_record("F-0002", "closed", attempts=1),
    finding_record("F-0003", "reopened", dimension="contracts"),
    finding_record("F-0004", "fixed", dimension="contracts"),
    finding_record("F-0005", "planned"),
    finding_record("F-0006", "disputed",
                   norm_ruling="human:operator 2026-08-15 — N1 does not apply here"),
    finding_record("F-0007", "rejected", dimension="contracts"),
    finding_record("F-0008", "reported", recurrence_of="F-0001"),
    finding_record("F-0009", "superseded-by-class", dimension="contracts"),
]
CLASS_FINDINGS = [
    dict(finding_record("CF-0001", "closed", dimension="contracts"),
         members=["F-0009"]),
]
QUEUE = [
    queue_pass("P-01", done=True),                       # covered, no residue
    queue_pass("P-02", done=False),                      # not run
    dict(queue_pass("P-03", done=True),                  # covered, WITH residue
         coverage={"report_path": "passes/P-03-report.md", "findings": ["F-0001"],
                   "updated": "2026-08-15", "status": "done"}),
]
def session(sid, role, outcome, duration_s, provider="anthropic", model="opus"):
    """One envelope, with every field the schema requires actually present.

    Not a trimmed dict: `state.json` refuses a session record missing a
    machine-meaningful field, and a fixture that skipped them would be testing a
    document the tool would never accept.
    """
    return {"session_id": sid, "role": role, "provider": provider,
            "agent_model": model, "executable": "/usr/bin/claude",
            "executable_version": "1.0.0", "charter_hash": "0" * 64,
            "prompt_hash": "1" * 64, "state_revision": 1, "head_before": "0" * 40,
            "head_after": "0" * 40, "sandbox_profile": "worktree",
            "sandbox_details": {}, "started": "2026-08-15T10:00:00+00:00",
            "duration_s": duration_s, "exit_status": 0, "outcome": outcome,
            "log_digest": "2" * 64}


SESSIONS = [
    session("a" * 16, "Auditor", "ok", 100),
    session("b" * 16, "Remediator", "timeout", 200),
    session("c" * 16, "Remediator", "ok", 300),
    session("d" * 16, "Verifier", "crash", 400, provider="openai", model="o3"),
]

# The event stream tells the history the state document cannot: F-0003 was CLOSED and
# then stopped being closed. That is the false closure, and by the time state.json is
# written it says only `reopened`.
EVENTS = [
    {"ts": "2026-08-15T10:00:00+00:00", "event": "gate_reached", "session_id": None,
     "gate": "stop-triage"},
    {"ts": "2026-08-15T10:01:00+00:00", "event": "state_transition",
     "session_id": "b" * 16, "verb": "record-verdict", "target": "F-0001",
     "from_status": "fixed", "to_status": "closed", "verdict": "closed",
     "summary": "F-0001: fixed -> closed"},
    {"ts": "2026-08-15T10:02:00+00:00", "event": "state_transition",
     "session_id": "b" * 16, "verb": "record-verdict", "target": "F-0002",
     "from_status": "fixed", "to_status": "reopened", "verdict": "reopened",
     "reason": "incomplete-fix", "summary": "F-0002: fixed -> reopened"},
    {"ts": "2026-08-15T10:03:00+00:00", "event": "state_transition",
     "session_id": "c" * 16, "verb": "record-verdict", "target": "F-0002",
     "from_status": "fixed", "to_status": "closed", "verdict": "closed",
     "summary": "F-0002: fixed -> closed"},
    {"ts": "2026-08-15T10:04:00+00:00", "event": "state_transition",
     "session_id": "c" * 16, "verb": "record-verdict", "target": "F-0003",
     "from_status": "fixed", "to_status": "closed", "verdict": "closed",
     "summary": "F-0003: fixed -> closed"},
    {"ts": "2026-08-15T10:05:00+00:00", "event": "state_transition",
     "session_id": None, "verb": "set-status", "target": "F-0003",
     "from_status": "closed", "to_status": "reopened",
     "summary": "F-0003: closed -> reopened (human:operator re-take)"},
    {"ts": "2026-08-15T10:06:00+00:00", "event": "state_transition",
     "session_id": "d" * 16, "verb": "record-verdict", "target": "CF-0001",
     "from_status": "fixed", "to_status": "closed", "verdict": "closed",
     "summary": "CF-0001: fixed -> closed"},
    {"ts": "2026-08-15T10:07:00+00:00", "event": "gate_reached", "session_id": None,
     "gate": "stop-needs-human"},
]

# --------------------------------------------------------------------------
# the answers, derived by hand
# --------------------------------------------------------------------------
EXPECTED = {
    # 1. auditor accuracy — reached validation: F-0001..F-0005 (survived), F-0006
    #    (killed), CF-0001 (survived) = 7. Survived = 6. 6/7 = 85.714…
    "auditor_accuracy_pct": 85.7,
    # 2. fix durability — reached a verdict: F-0001, F-0002, F-0003, CF-0001 = 4.
    #    Closed on the first attempt: F-0001, CF-0001 = 2. 2/4.
    "fix_durability_pct": 50.0,
    "fix_durability_baseline_pct": 70.3,
    # 3. reopen rate — reached remediation: F-0001..F-0005, CF-0001 = 6. Ever
    #    reopened: F-0002 (attempts 1), F-0003 (status reopened) = 2. 2/6 = 33.33…
    #    Note this is NOT 100 - durability (66.7): the denominators differ on purpose.
    "reopen_rate_pct": 33.3,
    # 5. false closures — to_status closed: F-0001, F-0002, F-0003, CF-0001 = 4.
    #    from_status closed: F-0003 = 1. 1/4.
    "false_closure_rate_pct": 25.0,
    "false_closures": 1,
    "closures_recorded": 4,
    # 7. coverage completeness — passes with a `done` coverage report: P-01, P-03 = 2
    #    of 3 queued. 2/3 = 66.66…
    "coverage_completeness_pct": 66.7,
    # 8. recovery after a kill — killed sessions: index 1 (timeout), index 3 (crash).
    #    The run continued past index 1 and stopped at index 3. 1/2.
    "recovery_after_kill_pct": 50.0,
    "killed_sessions": 2,
}
# 4. human interventions — two `gate_reached` events, one record carrying a
#    `norm_ruling`, one transition whose summary records a human re-take.
EXPECTED_INTERVENTIONS = {"gates": 2, "by_gate": {"stop-triage": 1, "stop-needs-human": 1},
                          "norm_rulings": 1, "human_retakes": 1, "total": 3}
# 6. cost — 100+200+300+400 = 1000 session seconds. Accepted findings = every record
#    except F-0007 (rejected) and F-0008 (reported) = 8. 1000/8 = 125.0 seconds each.
EXPECTED_COST = {"session_seconds": 1000, "accepted_findings": 8,
                 "seconds_per_accepted_finding": 125.0}
# Instrumentation added AFTER the pre-registration was fixed, each named in its own
# labelled section of the document. Not metrics, not among the nine, and no claim in the
# pre-registration is settled by one.
AFTER_THE_PREREGISTRATION = frozenset({"sessions_by_route"})

EXPECTED_BREAKDOWNS = {
    # reached by dimension: invariants F-0001,F-0002; contracts F-0003,CF-0001.
    # first-pass by dimension: invariants F-0001; contracts CF-0001. Both 1/2.
    "durability_by_dimension": {"contracts": 50.0, "invariants": 50.0},
    # individual reached F-0001,F-0002,F-0003 (1 first-pass) = 33.33…; class reached
    # CF-0001 (1 first-pass) = 100.
    "durability_by_fix_type": {"class": 100.0, "individual": 33.3},
    # one record (F-0008) of ten carries `recurrence_of`.
    "recurrence_rate_pct": 10.0,
    # transitions INTO reopened: F-0002 with a stated reason, F-0003 with none.
    "reopen_cause": {"incomplete-fix": 1, "unrecorded": 1},
    # (2 gates + 1 ruling) / 3 queued passes.
    "human_intervention_rate": 1.0,
    # 1000 seconds over 3 closed records (F-0001, F-0002, CF-0001) = 333.33…
    "seconds_per_closed_finding": 333.3,
    # of the 2 covered passes, 1 (P-03) reported a non-empty residue.
    "residue_pass_share_pct": 50.0,
    "false_closures_found_later": 1,
}


def synthetic_project():
    doc = state_doc(findings=FINDINGS, class_findings=CLASS_FINDINGS, queue=QUEUE,
                    catalogs=CATALOGS_2D, sessions=SESSIONS)
    fx = Fixture(doc)
    (fx.audit / "events.jsonl").write_text(
        "".join(json.dumps(e, sort_keys=True, separators=(",", ":")) + "\n"
                for e in EVENTS), encoding="utf-8")
    return fx


class TheFixtureIsAValidProject(unittest.TestCase):
    """If the fixture does not load, every number below is measuring a hand-built
    dict rather than a state document the tool would accept."""

    def test_it_loads_through_the_one_reader(self):
        fx = synthetic_project()
        self.addCleanup(fx.cleanup)
        state = state_mod.load_state(fx.audit)
        self.assertEqual(len(state.findings), 9)
        self.assertEqual(len(state.class_findings), 1)
        self.assertEqual(len(state.queue), 3)
        self.assertEqual(len(state.sessions), 4)
        self.assertEqual(len(envelope.read_events(fx.root)), 8)


class EachMetricMatchesTheHandDerivedAnswer(unittest.TestCase):

    def setUp(self):
        self.fx = synthetic_project()
        self.addCleanup(self.fx.cleanup)
        state = state_mod.load_state(self.fx.audit)
        self.report = decision.metrics_report(state, envelope.read_events(self.fx.root))

    def test_the_eight_scalar_metrics(self):
        for key, want in EXPECTED.items():
            with self.subTest(metric=key):
                self.assertEqual(self.report[key], want)

    def test_human_interventions(self):
        self.assertEqual(self.report["human_interventions"], EXPECTED_INTERVENTIONS)

    def test_cost_is_reported_as_a_proxy_and_never_as_money(self):
        cost = self.report["cost_per_accepted_finding"]
        for key, want in EXPECTED_COST.items():
            self.assertEqual(cost[key], want, key)
        self.assertIn("proxy", cost)
        self.assertIn("billing data", cost["proxy"],
                      "the cost metric must say what it cannot see; a seconds figure "
                      "presented as cost is an invented number")

    def test_the_nine_p2_breakdowns(self):
        got = self.report["breakdowns"]
        for key, want in EXPECTED_BREAKDOWNS.items():
            with self.subTest(breakdown=key):
                self.assertEqual(got[key], want)
        self.assertIn("verification_independence", got)
        # NINE were pre-registered, and that list does not move. Instrumentation added
        # after the pre-registration was fixed is named in its own after-the-fact section
        # of the document — the `tokens` / `telemetry` precedent — and is asserted here
        # as an explicit allow-list rather than by relaxing the count to `>= 9`, which
        # would let an undeclared key in silently.
        self.assertEqual(sorted(EXPECTED_BREAKDOWNS) + ["verification_independence"],
                         sorted(set(got) - AFTER_THE_PREREGISTRATION),
                         f"the nine pre-registered breakdowns changed: {sorted(got)}")
        self.assertEqual(len(got), 9 + len(AFTER_THE_PREREGISTRATION & set(got)),
                         f"an undeclared breakdown appeared, got {sorted(got)}")

    def test_reopen_rate_is_not_the_complement_of_durability(self):
        # The check that keeps metric 3 from being metric 2 with a minus sign. If a
        # future edit makes the denominators equal, this reddens and says why.
        self.assertNotAlmostEqual(
            self.report["reopen_rate_pct"], 100 - self.report["fix_durability_pct"],
            msg="reopen rate and fix durability now share a denominator; one of them "
                "is no longer measuring anything the other does not")

    def test_an_empty_project_reports_null_rather_than_zero(self):
        # "no finding has reached a verdict" and "every fix failed" are opposite
        # facts. A metric that prints 0.0 for both has destroyed the difference.
        empty = Fixture(state_doc(queue=[]))
        self.addCleanup(empty.cleanup)
        report = decision.metrics_report(state_mod.load_state(empty.audit), [])
        for key in ("auditor_accuracy_pct", "fix_durability_pct", "reopen_rate_pct",
                    "coverage_completeness_pct", "recovery_after_kill_pct",
                    "false_closure_rate_pct"):
            self.assertIsNone(report[key], key)

    def test_a_stream_with_no_status_fields_reports_unmeasurable(self):
        # An events.jsonl written before phase 10 carries no from_status/to_status.
        # The false-closure metric must say it cannot see, not report a clean 0%.
        state = state_mod.load_state(self.fx.audit)
        old = [{k: v for k, v in e.items() if k not in ("from_status", "to_status")}
               for e in EVENTS]
        report = decision.metrics_report(state, old)
        self.assertIsNone(report["false_closure_rate_pct"])
        self.assertEqual(report["closures_recorded"], 0)


class TheJsonSurfaceIsAProducerCheckedContract(unittest.TestCase):

    def test_every_metric_reaches_metrics_json_and_validates(self):
        fx = synthetic_project()
        self.addCleanup(fx.cleanup)
        p = subprocess.run([sys.executable, str(XCHECK), "--project", str(fx.root),
                            "metrics", "--json"],
                           capture_output=True, text=True, timeout=180)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        payload = json.loads(p.stdout)
        self.assertEqual(util.json_output_issues(payload, util.METRICS_SCHEMA, "metrics"),
                         [], "the command printed a payload its own schema refuses")
        print("\nmetrics --json on the synthetic fixture, beside the hand-derived answer:")
        for key, want in sorted(EXPECTED.items()):
            print(f"  {key:<32} got {payload[key]!r:<8} expected {want!r}")
            self.assertEqual(payload[key], want, key)
        self.assertEqual(payload["human_interventions"], EXPECTED_INTERVENTIONS)
        self.assertEqual(payload["breakdowns"]["reopen_cause"],
                         EXPECTED_BREAKDOWNS["reopen_cause"])
        # The independence summary is carried INTO the breakdown rather than computed
        # a second time, so the two surfaces cannot disagree.
        self.assertEqual(payload["breakdowns"]["verification_independence"],
                         payload["independence"])

    def test_an_unknown_key_and_a_missing_key_both_fail(self):
        good = {k: None for k in util.METRICS_SCHEMA}
        # A payload of the right SHAPE, so what is measured is the key check and not
        # a type error standing in for it.
        self.assertTrue(any("unknown key" in i for i in util.json_output_issues(
            dict(good, invented=1), util.METRICS_SCHEMA, "metrics")))
        missing = {k: v for k, v in good.items() if k != "reopen_rate_pct"}
        self.assertTrue(any("reopen_rate_pct" in i and "missing" in i
                            for i in util.json_output_issues(
                                missing, util.METRICS_SCHEMA, "metrics")))

    def test_the_human_printer_and_the_json_carry_the_same_numbers(self):
        fx = synthetic_project()
        self.addCleanup(fx.cleanup)
        p = subprocess.run([sys.executable, str(XCHECK), "--project", str(fx.root),
                            "metrics"], capture_output=True, text=True, timeout=180)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        print("\n" + p.stdout)
        for want in ("85.7%", "50.0%", "33.3%", "25.0%", "66.7%", "125.0s"):
            self.assertIn(want, p.stdout, f"the human printer never showed {want}")


class ComputingMetricsLaunchesNoSession(unittest.TestCase):
    """Criterion: the harness is proved end-to-end without an agent running.

    Stating it is not evidence. `run_session` is replaced with something that raises,
    so a metrics run that dispatched anything would fail here rather than pass quietly
    — and the stub is asserted to be in place, or the test proves nothing at all.
    """

    def test_metrics_never_reaches_the_runner(self):
        runner = xcheck_submodule("runner")
        cli = xcheck_submodule("cli")
        fx = synthetic_project()
        self.addCleanup(fx.cleanup)
        reached = []

        def refuse(*a, **k):
            reached.append(a)
            raise AssertionError("metrics dispatched an agent session")

        real = runner.run_session
        runner.run_session = refuse
        cli.run_session = refuse
        self.addCleanup(lambda: (setattr(runner, "run_session", real),
                                 setattr(cli, "run_session", real)))
        self.assertIs(cli.run_session, refuse, "the stub was not installed")

        self.assertEqual(cli.cmd_metrics(fx.root, as_json=False), 0)
        self.assertEqual(cli.cmd_metrics(fx.root, as_json=True), 0)
        self.assertEqual(reached, [])
        # And nothing was appended to the stream: reading metrics is not an event.
        self.assertEqual(len(envelope.read_events(fx.root)), len(EVENTS))
        self.assertFalse((fx.audit / "orchestrator-logs").exists())


@needs_repo_file(
    "docs/ouroboros-4-preregistration.md",
    "the pre-registration this class grades. `docs/` is internal by default and the "
    "export names the files it ships one by one, so an exported tree has no document "
    "here to grade. The pre-registration is about a run over THIS repository's own "
    "corpus, which is not exported either.")
class ThePreregistrationIsFixedInAdvance(unittest.TestCase):
    """The document is evidence, so a later edit that softens it must redden."""

    DOC = REPO / "docs" / "ouroboros-4-preregistration.md"

    def setUp(self):
        self.text = self.DOC.read_text(encoding="utf-8")

    def test_it_names_every_metric_the_tool_computes(self):
        report = decision.metrics_report(
            state_mod.State(schema_version=1, state_revision=1, generated_by="t",
                            head_before=None), [])
        missing = [k for k in report if k not in ("breakdowns",) and k not in self.text]
        self.assertEqual(missing, [],
                         "the tool computes a metric the pre-registration never fixed "
                         "in advance — a metric chosen after the data is not "
                         "pre-registered")
        for k in report["breakdowns"]:
            self.assertIn(k, self.text, f"breakdown {k} is not in the document")

    def test_every_metric_states_what_would_falsify_it(self):
        self.assertIn("Falsified if", self.text)
        self.assertEqual(self.text.count("**Falsified if**"), 8,
                         "each of the eight metrics states its own falsification "
                         "condition; a metric that cannot be wrong is not a hypothesis")

    def test_an_explanation_is_forbidden_in_advance(self):
        self.assertIn("Forbidden in advance", self.text)

    def test_the_stopping_rules_are_fixed(self):
        for token in ("Stopping rules", "inconclusive", "minimum"):
            self.assertIn(token, self.text, token)

    def test_ouroboros_3_is_carried_forward_unsoftened(self):
        # The frozen prior is admitted evidence. An edit that drops the qualifier —
        # "60.7%" without "4 of 39" and without "inconclusive" — would turn a stopped
        # run into a result.
        for token in ("60.7", "4 of 39", "inconclusive", "70.3"):
            self.assertIn(token, self.text, f"the document no longer states {token}")

    def test_it_says_plainly_that_the_run_has_not_happened(self):
        self.assertIn("has not run", self.text)
        self.assertIn("expired", self.text,
                      "the reason the run has not happened — the agent CLI's expired "
                      "credentials — is the operator's next action, not a footnote")


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------
# the token caption: one denominator, in the value AND in the sentence under it
# --------------------------------------------------------------------------
#
# The third audit divided 1,666,799 by 159 and got 10,483, beside a printed 69,450.
# Both numbers came out of the same payload: the VALUE divides by
# `canonical_transitions_measured` (24), the CAPTION named `canonical_transitions`
# (159). Nothing was wrong with the arithmetic — the sentence describing it named a
# population 6.6x too large, and a sentence is what gets quoted into a slide.
#
# The fixture below is the same synthetic project with four `session_finished` events
# added, chosen so the two denominators DIFFER. A fixture where measured == total
# cannot tell a fixed caption from the broken one.
#
#   session  finished  tokens        canonical transitions it made
#   a        yes       1000          none                     <- measured, moved nothing
#   b        yes       1000          F-0001, F-0002           <- measured
#   c        yes       2000          F-0002, F-0003           <- measured
#   d        yes       none          CF-0001                  <- UNMEASURED
#   (human)  n/a       n/a           F-0003 set-status        <- no session at all
#
# finished 4, measured 3 (a, b, c) = 75.0% coverage; total 4,000 tokens.
# canonical transitions in the stream: 6. Made by a measured session: 4 (b's two and
# c's two). 4,000 / 4 = 1,000 per transition — NOT 4,000 / 6 = 667.
# Of the measured tokens, a's 1,000 (25.0%) bought no canonical transition.

TOKEN_EVENTS = EVENTS + [
    {"ts": "2026-08-15T11:00:00+00:00", "event": "session_finished",
     "session_id": "a" * 16, "outcome": "ok", "tokens": 1000,
     "telemetry_source": "provider"},
    {"ts": "2026-08-15T11:01:00+00:00", "event": "session_finished",
     "session_id": "b" * 16, "outcome": "timeout", "tokens": 1000,
     "telemetry_source": "provider"},
    {"ts": "2026-08-15T11:02:00+00:00", "event": "session_finished",
     "session_id": "c" * 16, "outcome": "ok", "tokens": 2000,
     "telemetry_source": "provider"},
    # No `tokens` key at all — the shape a session whose log carried no figure leaves.
    # Not `"tokens": 0`, which would be a measurement of zero and would land in both
    # totals.
    {"ts": "2026-08-15T11:03:00+00:00", "event": "session_finished",
     "session_id": "d" * 16, "outcome": "crash"},
]

EXPECTED_TOKEN_LINES = [
    "6b tokens per canonical transition: 1,000",
    "   = 4,000 tokens / 4 canonical transition(s), both from the same 3 measured "
    "session(s)",
    "   measured: 3 of 4 finished session(s) (75.0%), 4 of 6 canonical transition(s)",
    "   of which 1,000 (25.0%) went to sessions that recorded no canonical transition "
    "at all",
    "   the other 1 finished session(s) reported no figure — unmeasured, not zero — so "
    "every figure above speaks for 75.0% of the run; the remaining 2 canonical "
    "transition(s) were not made by a measured session and are outside the ratio",
]

# One parser, deliberately loose about the JOINING WORD, so it reads the caption this
# code prints today AND the caption it used to print. A checker that only understands
# the fixed form would report "nothing to check" on the broken one, and a control that
# cannot redden is not a control.
_VALUE = re.compile(r"6b tokens per canonical transition: ([\d,]+)")
_PAIR = re.compile(r"([\d,]+) tokens (?:/|over) ([\d,]+)")


def caption_problems(text):
    """Recompute the printed ratio from the printed numerator and denominator.

    Reads the OUTPUT, never the payload: the defect this guards against is a caption
    that disagrees with a value both of which the payload got right.
    """
    v, pair = _VALUE.search(text), _PAIR.search(text)
    if not v:
        return ["no `6b tokens per canonical transition:` value was printed"]
    if not pair:
        return ["a value was printed with no numerator and denominator beside it"]
    value = int(v.group(1).replace(",", ""))
    num, den = (int(g.replace(",", "")) for g in pair.groups())
    if not den:
        return ["the caption printed a zero denominator"]
    if round(num / den) != value:
        return [f"the caption says {num:,}/{den} = {round(num / den):,}, "
                f"the value printed is {value:,}"]
    return []


def token_project():
    doc = state_doc(findings=FINDINGS, class_findings=CLASS_FINDINGS, queue=QUEUE,
                    catalogs=CATALOGS_2D, sessions=SESSIONS)
    fx = Fixture(doc)
    (fx.audit / "events.jsonl").write_text(
        "".join(json.dumps(e, sort_keys=True, separators=(",", ":")) + "\n"
                for e in TOKEN_EVENTS), encoding="utf-8")
    return fx


class TheCostCaptionNamesTheDenominatorItUsed(unittest.TestCase):

    def setUp(self):
        self.fx = token_project()
        self.addCleanup(self.fx.cleanup)
        p = subprocess.run([sys.executable, str(XCHECK), "--project", str(self.fx.root),
                            "metrics"], capture_output=True, text=True, timeout=180)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.out = p.stdout
        state = state_mod.load_state(self.fx.audit)
        self.tok = decision.metrics_report(
            state, envelope.read_events(self.fx.root))["tokens"]

    def test_the_hand_derived_population_is_what_the_payload_computed(self):
        # If these drift the expected STRINGS below are testing a different fixture.
        for key, want in (("sessions_finished", 4), ("sessions_measured", 3),
                          ("sessions_unmeasured", 1), ("total", 4000),
                          ("canonical_transitions", 6),
                          ("canonical_transitions_measured", 4),
                          ("coverage_pct", 75.0), ("per_canonical_transition", 1000),
                          ("on_sessions_that_moved_nothing", 1000),
                          ("share_that_moved_nothing_pct", 25.0)):
            with self.subTest(key=key):
                self.assertEqual(self.tok[key], want)
        self.assertNotEqual(self.tok["canonical_transitions"],
                            self.tok["canonical_transitions_measured"],
                            "a fixture whose two denominators are equal cannot tell "
                            "the fixed caption from the broken one")

    def test_the_printed_ratio_is_the_printed_numerator_over_the_printed_denominator(self):
        print("\n" + "\n".join(l for l in self.out.splitlines()
                               if "token" in l or l.startswith("   ")))
        self.assertEqual(caption_problems(self.out), [])

    def test_control_the_caption_the_audit_found_is_rejected_by_this_check(self):
        # The exact sentence `cli.py` used to print, rebuilt from the same payload the
        # fixed one is built from. It must fail, or the test above proves nothing.
        broken = (f"6b tokens per canonical transition: "
                  f"{self.tok['per_canonical_transition']:,} "
                  f"({self.tok['total']:,} tokens over "
                  f"{self.tok['canonical_transitions']} transition(s))")
        problems = caption_problems(broken)
        print("\nCONTROL   " + broken + "\n          -> " + "; ".join(problems))
        self.assertEqual(problems, ["the caption says 4,000/6 = 667, "
                                    "the value printed is 1,000"])

    def test_the_five_token_lines_are_exactly_these(self):
        # The control the phase asks for: a future edit that changes a denominator
        # changes a test. Whole lines, not substrings, so a dropped population is a
        # failure and not a silent narrowing.
        printed = [l for l in self.out.splitlines()
                   if l.startswith("6b ") or l.startswith("   ")]
        for want, got in zip(EXPECTED_TOKEN_LINES, printed):
            self.assertEqual(got, want)
        self.assertEqual(len(printed), len(EXPECTED_TOKEN_LINES), printed)

    def test_the_measured_share_is_stated_rather_than_left_to_the_reader(self):
        self.assertIn("measured: 3 of 4 finished session(s) (75.0%)", self.out,
                      "the reader must not have to divide 3 by 4 themselves")
        self.assertIn("4 of 6 canonical transition(s)", self.out,
                      "the transition population is the one the audit found missing")
        # And the unmeasured remainder stated as a LIMIT on the figures, not as a
        # footnote about session bookkeeping.
        self.assertRegex(self.out, r"unmeasured, not zero .* speaks for 75\.0% of the run")

    def test_no_figure_anywhere_in_the_output_is_money(self):
        # The existing refusal, asserted against the whole printed surface rather than
        # the one payload field that carries the disclaimer.
        self.assertNotIn("$", self.out)
        self.assertNotRegex(self.out, r"USD|EUR|\bcents?\b")
        # The word `dollar` DOES appear — inside the sentence refusing to print one.
        # That sentence is the control being asserted, so a check that banned the word
        # outright would delete the evidence that the refusal is still there.
        self.assertIn("no dollar figure is printed", self.out)
        self.assertIn("PROXY", self.out)

    def test_both_denominators_remain_separate_keys_and_no_key_changed_meaning(self):
        # Criterion: the JSON keeps both, and OUTPUT_SCHEMA_VERSION bumps only when a
        # key's MEANING moves. This phase changed a caption; `canonical_transitions`
        # and `canonical_transitions_measured` mean today exactly what they meant
        # before, so the version deliberately did NOT move.
        p = subprocess.run([sys.executable, str(XCHECK), "--project", str(self.fx.root),
                            "metrics", "--json"],
                           capture_output=True, text=True, timeout=180)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        tok = json.loads(p.stdout)["tokens"]
        self.assertEqual(tok["canonical_transitions"], 6)
        self.assertEqual(tok["canonical_transitions_measured"], 4)
        self.assertEqual(json.loads(p.stdout)["output_schema_version"],
                         util.OUTPUT_SCHEMA_VERSION)

    def test_measured_sessions_that_moved_nothing_print_a_reason_not_a_crash(self):
        # `per_canonical_transition` is None when the measured sessions made no
        # canonical transition — a real state, and the one the Ouroboros-4 corpus is
        # closest to. The old caption formatted it with `{:,}` and would have raised
        # TypeError on the way to saying so.
        fx = Fixture(state_doc(findings=FINDINGS, class_findings=CLASS_FINDINGS,
                               queue=QUEUE, catalogs=CATALOGS_2D, sessions=SESSIONS))
        self.addCleanup(fx.cleanup)
        barren = [e for e in TOKEN_EVENTS
                  if e.get("event") != "session_finished" or e["session_id"] == "a" * 16]
        (fx.audit / "events.jsonl").write_text(
            "".join(json.dumps(e, sort_keys=True, separators=(",", ":")) + "\n"
                    for e in barren), encoding="utf-8")
        p = subprocess.run([sys.executable, str(XCHECK), "--project", str(fx.root),
                            "metrics"], capture_output=True, text=True, timeout=180)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        line = next(l for l in p.stdout.splitlines() if l.startswith("6b "))
        print("\nBARREN    " + line)
        self.assertEqual(line, "6b tokens per canonical transition: n/a (the 1 measured "
                               "session(s) recorded no canonical transition of the 6 in "
                               "the stream; 1,000 tokens bought none of them)")
