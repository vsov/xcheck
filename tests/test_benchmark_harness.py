"""A benchmark harness, shipped unrun — and the refusals that keep it honest.

Nothing here runs a paid benchmark. The operator scoped that out, and a harness that
quietly ran one would be the defect. So what is asserted is the shape of the thing:

- a DRY RUN reaches every measurement point and records an ARTEFACT at each, so "reached"
  is a value on disk rather than a flag something set on its way past;
- every metric is `unmeasured` and asking for one RAISES. `0` would be a measurement —
  a run that happened and found nothing — and an unrun benchmark's failure mode is not an
  empty table, it is a table of zeroes someone quotes;
- foreign code is `untrusted` with no per-target override, and a non-container profile is
  refused before anything resolves;
- the metric ARITHMETIC is exercised against a fixture whose ground truth is known by
  construction: one planted defect, one clean file, four reported findings.
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.harness import xcheck_submodule

benchmark = xcheck_submodule("benchmark")
policy = xcheck_submodule("policy")
routing = xcheck_submodule("routing")

CONTAINED = {"sandbox_profile": "container"}


def full_conf(**over):
    conf = dict(CONTAINED, budgets="on")
    conf.update({k: "100000" for k in benchmark.budget.CEILING_KEYS})
    conf.update(over)
    return conf


class KnownFixture:
    """A target repository whose defects are known because we put them there.

    One file with a real defect, one file with none. That is the entire ground truth, and
    it is what makes the precision arithmetic checkable without paying for a run.
    """

    PLANTED = "planted.py"
    CLEAN = "clean.py"

    def __init__(self, case):
        self.root = Path(tempfile.mkdtemp(prefix="xcheck-bench-"))
        case.addCleanup(__import__("shutil").rmtree, self.root, ignore_errors=True)
        (self.root / self.PLANTED).write_text(
            "def withdraw(balance, amount):\n"
            "    # the planted defect: no check that amount <= balance\n"
            "    return balance - amount\n", encoding="utf-8")
        (self.root / self.CLEAN).write_text(
            "def add(a, b):\n"
            "    return a + b\n", encoding="utf-8")
        for argv in (["init", "-q"], ["config", "user.email", "b@example.invalid"],
                     ["config", "user.name", "b"], ["add", "-A"],
                     ["commit", "-qm", "fixture"]):
            subprocess.run(["git"] + argv, cwd=self.root, check=True, capture_output=True)

    # The bookkeeping a real pass WOULD have produced against this fixture. Four findings
    # reported: two about `planted.py` (the second a duplicate of the first) and two about
    # `clean.py`, which has nothing wrong with it. The duplicate is never triaged, because
    # a duplicate is not a second opinion.
    GROUND_TRUTH = dict(
        reported=("F-1 planted.py", "F-2 planted.py (dup of F-1)",
                  "F-3 clean.py", "F-4 clean.py"),
        triaged=("F-1 planted.py", "F-3 clean.py", "F-4 clean.py"),
        accepted=("F-1 planted.py",),
        duplicates=("F-2 planted.py (dup of F-1)",),
        closures=(), reopened=(),
    )


class TheDryRunReachesEveryMeasurementPoint(unittest.TestCase):

    def setUp(self):
        self.fx = KnownFixture(self)
        self.report = benchmark.dry_run([self.fx.root], full_conf())

    def test_every_point_is_reached_and_carries_an_artefact(self):
        print("\nMEASUREMENT POINTS")
        for point in benchmark.MEASUREMENT_POINTS:
            self.assertTrue(self.report.reached(point), f"never reached: {point}")
            got = self.report.points[point]
            self.assertTrue(got, f"{point} was recorded with nothing in it — that is a "
                                 f"flag, not evidence of reaching it")
            shown = json.dumps(got, default=str)
            print(f"   {point:<22} {shown[:110]}{'…' if len(shown) > 110 else ''}")

    def test_it_stops_at_dispatch_rather_than_passing_through_it(self):
        self.assertEqual(benchmark.MEASUREMENT_POINTS[-1], benchmark.DRY_RUN_STOPS_AT)
        self.assertIn("NOT PERFORMED", self.report.points[benchmark.DRY_RUN_STOPS_AT])
        self.assertTrue(self.report.dry_run)
        print(f"\nSTOP  {self.report.points[benchmark.DRY_RUN_STOPS_AT]}")

    def test_the_points_are_not_merely_started_they_hold_real_values(self):
        """A dry run that recorded `True` at each point would pass a `reached()` check
        and prove nothing. These are the values a real run would carry forward."""
        target = str(self.fx.root)
        commit = self.report.points["target-resolved"][target]
        self.assertRegex(commit, r"^[0-9a-f]{40}$",
                         "the subject was not resolved to a real commit")
        self.assertEqual(policy.UNTRUSTED,
                         self.report.points["trust-classified"][target])
        self.assertEqual("container", self.report.points["containment-resolved"])
        self.assertIn(self.report.points["route-chosen"][target], routing.ROUTES)
        self.assertEqual(64, len(self.report.points["charter-built"][target]))
        print(f"\nARTEFACTS  commit={commit[:12]}…  "
              f"trust={self.report.points['trust-classified'][target]}  "
              f"route={self.report.points['route-chosen'][target]}  "
              f"budget={self.report.points['budget-bound']}")

    def test_a_high_risk_benchmark_pass_is_never_routed_cheap(self):
        route = self.report.points["route-chosen"][str(self.fx.root)]
        self.assertNotIn(route, routing.NEVER_AT_HIGH_RISK,
                         "a benchmark of unseen code went to a cheap route")


class ItRefusesToReportWhatItDidNotMeasure(unittest.TestCase):

    def setUp(self):
        self.fx = KnownFixture(self)
        self.report = benchmark.dry_run([self.fx.root], full_conf())

    def test_every_metric_is_unmeasured_and_none_of_them_is_zero(self):
        table = self.report.table()
        self.assertEqual({benchmark.UNMEASURED}, set(table.values()))
        for name, value in table.items():
            self.assertNotEqual(0, value, f"{name} reported 0 — that is a MEASUREMENT")
            self.assertIsNot(value, 0.0)
        print("\nDRY-RUN TABLE")
        for name in benchmark.METRICS:
            print(f"   {name:<28} {table[name]}")

    def test_asking_for_a_metric_no_run_produced_raises(self):
        print()
        for name in benchmark.METRICS:
            with self.assertRaises(benchmark.BenchmarkError) as caught:
                self.report.value(name)
            msg = str(caught.exception)
            self.assertIn("is not 0", msg)
            self.assertIn(benchmark.NEEDS[name], msg,
                          "the refusal does not say what a real run would need")
            print(f"   REFUSED {name:<28} {msg.splitlines()[0][:88]}")

    def test_the_refusal_names_every_precondition(self):
        with self.assertRaises(benchmark.BenchmarkError) as caught:
            self.report.value("cost_per_durable_closure")
        msg = str(caught.exception)
        for what, _why in benchmark.PRECONDITIONS:
            self.assertIn(what, msg, f"the refusal does not mention {what}")
        print(f"\n{msg}")

    def test_an_unknown_metric_is_refused_by_name(self):
        with self.assertRaises(benchmark.BenchmarkError):
            self.report.value("precision")

    def test_a_zero_denominator_is_unmeasured_not_zero(self):
        """0 accepted out of 0 reported is not a 0% acceptance rate."""
        board = benchmark.scoreboard()
        self.assertEqual(benchmark.UNMEASURED, board["acceptance_rate"])
        self.assertEqual(benchmark.UNMEASURED, board["false_positive_rate"])
        print(f"\nEMPTY  scoreboard() -> "
              f"{ {k: v for k, v in board.items() if k != '_counts'} }")


class ForeignCodeRunsUntrusted(unittest.TestCase):

    def setUp(self):
        self.fx = KnownFixture(self)

    def test_a_non_container_profile_is_refused_before_anything_resolves(self):
        for profile in ("worktree", "readonly", "none", ""):
            with self.assertRaises(benchmark.BenchmarkError) as caught:
                benchmark.dry_run([self.fx.root], full_conf(sandbox_profile=profile))
            msg = str(caught.exception)
            self.assertIn("untrusted", msg)
            self.assertIn("sandbox_profile=container", msg)
            print(f"\nREFUSED  sandbox_profile={profile!r} -> {msg.splitlines()[0][:100]}")

    def test_the_container_arm_is_allowed_which_is_the_control(self):
        """A harness that refused every profile would pass the test above."""
        report = benchmark.dry_run([self.fx.root], full_conf())
        self.assertEqual("container", report.points["containment-resolved"])
        print(f"ALLOWED  sandbox_profile='container' -> reached "
              f"{len(report.points)} measurement point(s)")

    def test_trust_is_a_constant_with_no_per_target_override(self):
        import inspect
        self.assertEqual(policy.UNTRUSTED, benchmark.trust_for(self.fx.root))
        self.assertEqual(policy.UNTRUSTED, benchmark.trust_for("/anything/at/all"))
        # Structural: one parameter, so there is nothing to pass a trust level THROUGH.
        params = list(inspect.signature(benchmark.trust_for).parameters)
        self.assertEqual(["target"], params,
                         f"trust_for grew a parameter: {params} — a per-target trust knob "
                         f"is how a stranger's repository gets audited under a worktree")

    def test_a_trusted_declaration_in_the_conf_does_not_help(self):
        with self.assertRaises(benchmark.BenchmarkError):
            benchmark.dry_run([self.fx.root],
                              full_conf(sandbox_profile="worktree",
                                        trust_level=policy.TRUSTED))
        print(f"REFUSED  trust_level={policy.TRUSTED!r} in the conf does not unlock a "
              f"worktree run")


class ResultsCarryTheProvenanceThatMakesThemComparable(unittest.TestCase):

    def setUp(self):
        self.fx = KnownFixture(self)
        self.report = benchmark.dry_run([self.fx.root], full_conf(),
                                        policy_digest="a" * 64)

    def test_it_records_subject_model_policy_and_the_route_table(self):
        prov = self.report.points["provenance-sealed"][str(self.fx.root)]
        self.assertRegex(prov["subject_commit"], r"^[0-9a-f]{40}$")
        self.assertRegex(prov["subject_manifest"], r"^[0-9a-f]{40,64}$")
        self.assertEqual("a" * 64, prov["policy_digest"])
        self.assertRegex(prov["route_table_digest"], r"^[0-9a-f]{64}$")
        print("\nPROVENANCE")
        for k in ("subject_commit", "subject_manifest", "policy_digest",
                  "model_identity", "route_table_digest", "controller_commit"):
            print(f"   {k:<22} {str(prov[k])[:64]}")

    def test_a_missing_piece_is_named_rather_than_dropped(self):
        prov = self.report.points["provenance-sealed"][str(self.fx.root)]
        self.assertIn("model_identity", prov["incomparable_because"],
                      "no auditor_cmd was given, so the model is unknown — a comparison "
                      "against a run whose model nobody recorded is not a comparison")
        print(f"INCOMPARABLE BECAUSE {prov['incomparable_because']}")

    def test_the_route_table_digest_moves_when_the_table_does(self):
        before = benchmark.route_table_digest()
        original = dict(routing.TABLE)
        try:
            routing.TABLE[(routing.INVENTORY, routing.LOW)] = routing.STRONG
            self.assertNotEqual(before, benchmark.route_table_digest(),
                                "the digest did not move when the table did, so two runs "
                                "under different routing would look comparable")
        finally:
            routing.TABLE.clear()
            routing.TABLE.update(original)
        self.assertEqual(before, benchmark.route_table_digest())


class TheKnownFixturePrecisionArithmetic(unittest.TestCase):
    """The metric DEFINITIONS, exercised without a paid run.

    One planted defect, one clean file, four reported findings: the only correct answers
    are arithmetic, so they are written out here rather than recomputed from the code
    under test.
    """

    def setUp(self):
        self.fx = KnownFixture(self)
        self.report = benchmark.dry_run([self.fx.root], full_conf(),
                                        ground_truth=KnownFixture.GROUND_TRUTH)

    def test_the_arithmetic_is_what_the_counts_say_it_is(self):
        counts = self.report.points["bookkeeping"]
        self.assertEqual({"reported": 4, "triaged": 3, "accepted": 1, "duplicates": 1,
                          "closures": 0, "reopened": 0,
                          "tokens": benchmark.UNMEASURED}, counts)
        # 1 accepted of 4 reported; 1 duplicate of 4; 2 of 3 triaged were wrong.
        self.assertEqual(0.25, self.report.value("acceptance_rate"))
        self.assertEqual(0.25, self.report.value("duplicate_rate"))
        self.assertEqual(0.6667, self.report.value("false_positive_rate"))
        print("\nKNOWN FIXTURE  1 planted defect (planted.py), 1 clean file (clean.py)")
        print(f"   counts             {counts}")
        print(f"   acceptance_rate    1/4 = {self.report.value('acceptance_rate')}")
        print(f"   duplicate_rate     1/4 = {self.report.value('duplicate_rate')}")
        print(f"   false_positive     1 - 1/3 = {self.report.value('false_positive_rate')}")

    def test_the_cost_metrics_stay_refused_even_with_a_full_ground_truth(self):
        """The load-bearing one. Bookkeeping is not spending: a harness that started
        answering `cost_per_accepted_finding` from counts alone would be inventing the
        one number this whole phase exists not to invent."""
        for name in ("cost_per_accepted_finding", "cost_per_durable_closure"):
            with self.assertRaises(benchmark.BenchmarkError) as caught:
                self.report.value(name)
            print(f"STILL REFUSED  {name} — {str(caught.exception).splitlines()[0][:80]}")

    def test_a_closure_that_reopened_is_not_a_durable_closure(self):
        board = benchmark.scoreboard(reported=("a",), triaged=("a",), accepted=("a",),
                                     closures=("a", "b"), reopened=("b",), tokens=1000)
        self.assertEqual(1000.0, board["cost_per_durable_closure"],
                         "the reopened closure was counted as durable")
        print(f"\nDURABLE  2 closures, 1 reopened, 1000 tokens -> "
              f"{board['cost_per_durable_closure']} per DURABLE closure (not 500)")


class ItStatesWhatARealRunNeeds(unittest.TestCase):

    def setUp(self):
        self.fx = KnownFixture(self)

    def test_the_preconditions_are_listed_and_unmet_ones_reported(self):
        report = benchmark.dry_run([self.fx.root], CONTAINED)
        self.assertTrue(report.unmet)
        print("\nPRECONDITIONS UNMET (default conf)")
        for line in report.unmet:
            print(f"   - {line}")
        self.assertTrue(any("budget" in u for u in report.unmet))
        self.assertTrue(any("accepts the cost" in u for u in report.unmet))

    def test_everything_satisfied_leaves_nothing_unmet_which_is_the_control(self):
        report = benchmark.dry_run([self.fx.root], full_conf(), accepted_cost=True)
        self.assertEqual([], report.unmet,
                         f"a fully-configured run still reports preconditions unmet, so "
                         f"the list can never reach empty: {report.unmet}")
        print(f"\nCONTROL  fully configured -> preconditions unmet: {report.unmet}")

    def test_consent_is_an_argument_not_a_configuration_key(self):
        """A `benchmark_accepted_cost=on` line would put consent to spend money in a file
        the audited project could contain."""
        import inspect
        self.assertIn("accepted_cost",
                      inspect.signature(benchmark.preconditions_unmet).parameters)
        # And the conf route does NOT work: the key is not read.
        report = benchmark.dry_run([self.fx.root],
                                   full_conf(benchmark_accepted_cost="yes"))
        self.assertTrue(any("accepts the cost" in u for u in report.unmet),
                        "a config key bought consent to spend money")
        print("REFUSED  `benchmark_accepted_cost=yes` in the conf buys nothing")

    def test_this_phase_ships_it_unrun(self):
        """Structural, not a grep of this file's own text.

        The first version of this test searched its own source for a spending call —
        which is the self-scanning detector this project keeps rediscovering: the needle
        matches the line that looks for it. What is actually checkable is that the
        HARNESS holds no capability to spend. It imports no dispatcher, no subprocess,
        no network; there is no `real_run`; and `dry_run` is the only entry point that
        walks the measurement points.
        """
        import ast
        src = Path(benchmark.__file__).read_text(encoding="utf-8")
        imported = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(f"{node.module}.{a.name}" for a in node.names)
        for spender in ("subprocess", "socket", "urllib", "xcheck.runner",
                        "xcheck.runner.run_child", "xcheck.envelope"):
            self.assertNotIn(spender, imported,
                             f"the harness imports {spender} — it can dispatch, and this "
                             f"phase ships it UNRUN")
        self.assertFalse(hasattr(benchmark, "real_run"),
                         "a `real_run` entry point appeared — adding the spending path "
                         "is a decision with a bill attached")
        print(f"\nUNRUN  xcheck/benchmark.py imports {sorted(imported)} — nothing that "
              f"dispatches, spends or reaches a network; no `real_run`")


if __name__ == "__main__":
    unittest.main()


# ------------------------------------------------------------- PHASE 12 (fifth audit)

from tests.harness import (needs_live_corpus, needs_repo_file, REPO, Fixture, finding_record,  # noqa: E402
                           state_doc, xcheck_submodule as _sub)

state_mod = _sub("state")
envelope = _sub("envelope")


def outcome_fixture(confirmed=0, durable=0, reported=0, tokens=(), durations=()):
    """A state whose useful-outcome count is known BY CONSTRUCTION.

    Ground truth is the point: the metrics under test are ratios, and a ratio checked
    against a corpus nobody counted by hand is a ratio checked against itself. Here the
    denominator is whatever this function was asked for.
    """
    findings = []
    for i in range(confirmed):
        findings.append(finding_record(f"F-{i + 1:04d}", status="validated"))
    for i in range(durable):
        findings.append(finding_record(f"F-{confirmed + i + 1:04d}", status="closed",
                                       attempts=0))
    for i in range(reported):
        findings.append(finding_record(f"F-{confirmed + durable + i + 1:04d}",
                                       status="reported"))
    sessions = [{"session_id": f"{i:016d}", "role": "Auditor", "provider": "openai",
                 "agent_model": "unknown", "executable": "codex",
                 "executable_version": "unknown", "prompt_hash": "0" * 64,
                 "charter_hash": "0" * 64, "state_revision": 1, "head_before": "0" * 40,
                 "sandbox_profile": "worktree", "started": "2026-08-14T00:00:00Z",
                 "outcome": "ok", "exit_status": 0, "duration_s": d,
                 "head_after": "0" * 40, "log_digest": "0" * 64}
                for i, d in enumerate(durations)]
    fx = Fixture(state_doc(findings=findings, sessions=sessions))
    events = [{"event": "session_finished", "session_id": f"{i:016d}",
               **({"tokens": t} if t is not None else {})}
              for i, t in enumerate(tokens)]
    return fx, state_mod.load_state(fx.audit), events


RATES = {"input_per_million": 3.0, "output_per_million": 15.0, "currency": "USD",
         "models": {"gpt-5": {"input_per_million": 3.0,
                              "output_per_million": 15.0,
                              "cache_per_million": 0.3}}}


@needs_live_corpus
class FourMetricsPerUsefulOutcome(unittest.TestCase):
    """The audit's economics finding. `docs/ab-model-routing.md` asked for a 30% TOKEN
    reduction and put latency under "deliberately not measured" — so a cheap model that
    spends the same tokens for less money scores as no improvement, and a slower arm
    scores as a tie. Four figures, and a denominator that is not activity."""

    def test_the_four_are_named_and_each_says_what_it_needs(self):
        print("\n  FOUR METRICS")
        for name in benchmark.VALUE_METRICS:
            print(f"    {name:<36} needs: {benchmark.NEEDS[name][:70]}")
            self.assertIn(name, benchmark.NEEDS, name)
        self.assertEqual(4, len(benchmark.VALUE_METRICS))
        self.assertEqual(4, len(set(benchmark.VALUE_METRICS)))

    def test_the_denominator_is_stated_and_is_not_activity(self):
        fx, st, ev = outcome_fixture(confirmed=2, durable=1, reported=9)
        self.addCleanup(fx.cleanup)
        got = benchmark.value_report(st, ev)["denominator"]
        print(f"\n  DENOMINATOR {got['caption']}")
        self.assertEqual(3, got["useful_outcomes"], "9 reported findings are not outcomes")
        self.assertEqual({"confirmed_defect": 2, "durable_fix": 1}, got["by_kind"])
        self.assertTrue(any("canonical transitions" in n for n in got["not_an_outcome"]),
                        "the thing the old figure divided by is not named as activity")
        self.assertIn("NOT by canonical transitions", got["caption"])

    def test_the_two_kinds_of_outcome_do_not_double_count(self):
        """The defect this fixture found. `closed` is in `VALIDATION_SURVIVED`, so a
        durable fix was counted as a confirmed defect AND as a fix: 2 + 1 came back as 4.
        An inflated denominator makes every per-outcome cost look better, which is the
        direction an error is least likely to be noticed in."""
        fx, st, ev = outcome_fixture(confirmed=2, durable=1)
        self.addCleanup(fx.cleanup)
        got = benchmark.value_report(st, ev)["denominator"]
        print(f"\n  DISJOINT   2 validated + 1 closed -> {got['useful_outcomes']} "
              f"outcome(s) {got['by_kind']}")
        self.assertEqual(3, got["useful_outcomes"])
        self.assertEqual(sum(got["by_kind"].values()), got["useful_outcomes"],
                         "the parts do not add up to the whole they are captioned as")

    def test_all_four_are_measured_where_the_inputs_exist(self):
        """Ground truth by construction: 3 outcomes, 300,000 tokens, 4 timed sessions.

        PHASE 7 (sixth audit) rewrote two of these expectations, and the old ones are
        named here rather than deleted, because what changed is the CONTRACT:

        - money was `1.5` — 300,000 tokens multiplied by ONE output rate and divided by
          3. It now refuses on this fixture: these events carry no `telemetry_source`,
          which is the shape of this repository's real corpus, and a price built on an
          agent's own claim about its own spend is the subject of the audit pricing
          itself. The priced path is exercised in `ProvenanceIsAPrecondition`.
        - wall-clock per-outcome was `153.33` — the SUM of the four durations over 3
          outcomes. It is now the audit's own span, and these events carry no `ts`, so
          the span is unavailable and the figure is None rather than the sum.
        """
        fx, st, ev = outcome_fixture(confirmed=2, durable=1,
                                     tokens=(100_000, 100_000, 50_000, 50_000),
                                     durations=(10.0, 20.0, 30.0, 400.0))
        self.addCleanup(fx.cleanup)
        r = benchmark.value_report(st, ev, rates=RATES, review_seconds=900)
        print("\n  MEASURED   3 useful outcomes, 300,000 tokens, 4 timed sessions")
        for name in benchmark.VALUE_METRICS:
            block = r[name]
            print(f"    {name:<36} {block.get('value', block.get('p50'))} "
                  f"[{block['kind']}]")
        for name in ("tokens_per_outcome", "wall_clock_seconds",
                     "human_review_seconds_per_outcome"):
            self.assertIsNone(r[name]["unmeasured_reason"], name)
        self.assertEqual(100_000.0, r["tokens_per_outcome"]["value"])
        self.assertEqual(300.0, r["human_review_seconds_per_outcome"]["value"])
        self.assertEqual(20.0, r["wall_clock_seconds"]["p50"])
        self.assertEqual(400.0, r["wall_clock_seconds"]["p95"])

        # WAS 1.5, at one output rate over unattested figures.
        print(f"    money WAS 1.5 at one output rate; now "
              f"{r['money_per_outcome']['value']!r} — "
              f"{r['money_per_outcome']['unmeasured_reason'][:60]}…")
        self.assertIsNone(r["money_per_outcome"]["value"])
        self.assertIn("entitled to say so",
                      r["money_per_outcome"]["unmeasured_reason"])
        self.assertEqual("USD", r["money_per_outcome"]["currency"])

        # WAS 153.33 = sum(10, 20, 30, 400) / 3. The sum is still REPORTED, beside it.
        print(f"    wall-clock per-outcome WAS 153.33 (the sum over 3); now "
              f"{r['wall_clock_seconds']['per_outcome']!r}, with "
              f"sum_of_sessions_s={r['wall_clock_seconds']['sum_of_sessions_s']}")
        self.assertIsNone(r["wall_clock_seconds"]["per_outcome"])
        self.assertEqual(460.0, r["wall_clock_seconds"]["sum_of_sessions_s"])

    def test_each_one_REFUSES_rather_than_reporting_zero(self):
        """`unmeasured-is-not-zero`, over all four. Every input is removed one at a time
        so no single missing thing can silence more than it should."""
        fx, st, ev = outcome_fixture(confirmed=2, durable=1,
                                     tokens=(100_000,), durations=(10.0,))
        self.addCleanup(fx.cleanup)
        cases = {
            "money_per_outcome": benchmark.value_report(st, ev),                # no rates
            "human_review_seconds_per_outcome": benchmark.value_report(st, ev, rates=RATES),
        }
        print("\n  REFUSALS")
        for name, report in cases.items():
            block = report[name]
            print(f"    {name:<36} value={block['value']} "
                  f"reason={block['unmeasured_reason'][:56]}")
            self.assertIsNone(block["value"], f"{name} printed a number")
            self.assertIsNotNone(block["unmeasured_reason"], name)
            self.assertNotEqual(0, block["value"], "0 would read as a measurement")

    def test_no_outcomes_refuses_every_per_outcome_figure_but_keeps_the_distribution(self):
        """The real corpus's shape: work happened, nothing was independently confirmed.
        The latency DISTRIBUTION is still a measurement — it is not per-outcome."""
        fx, st, ev = outcome_fixture(reported=12, tokens=(100_000, 100_000),
                                     durations=(10.0, 30.0))
        self.addCleanup(fx.cleanup)
        r = benchmark.value_report(st, ev, rates=RATES, review_seconds=60)
        print("\n  NO OUTCOMES 12 reported findings, 200,000 tokens spent, "
              "0 useful outcomes")
        for name in benchmark.VALUE_METRICS:
            if name == "wall_clock_seconds":
                continue
            self.assertIsNone(r[name]["value"], f"{name} divided by zero outcomes")
            self.assertIsNotNone(r[name]["unmeasured_reason"], name)
        self.assertEqual(200_000, r["tokens_per_outcome"]["tokens_measured"],
                         "the spend is still reported — it happened")
        self.assertEqual(30.0, r["wall_clock_seconds"]["p95"])
        self.assertIsNone(r["wall_clock_seconds"]["per_outcome"])
        print(f"    tokens spent {r['tokens_per_outcome']['tokens_measured']:,}, "
              f"per outcome {r['tokens_per_outcome']['value']} "
              f"(reason: {r['tokens_per_outcome']['unmeasured_reason'][:48]})")

    def test_xcheck_ships_no_price_list_and_a_broken_rate_refuses(self):
        """Criterion 3. A rate is a fact about a contract; guessing one prints money
        nobody was charged. A malformed declaration RAISES rather than degrading to
        `unmeasured`, which would read as "you declared nothing"."""
        self.assertIsNone(benchmark.token_rates(None))
        self.assertIsNone(benchmark.token_rates({}))
        for bad, why in ((({"input_per_million": 3.0}), "missing"),
                         ({"input_per_million": "x", "output_per_million": 1,
                           "currency": "USD"}, "not a number"),
                         ({"input_per_million": -1, "output_per_million": 1,
                           "currency": "USD"}, "negative"),
                         ({"input_per_million": 1, "output_per_million": 1,
                           "currency": " "}, "empty currency")):
            with self.assertRaises(benchmark.BenchmarkError) as caught:
                benchmark.token_rates(bad)
            print(f"  RATE REFUSED {str(caught.exception)[:88]}")
            self.assertIn(why, str(caught.exception))
        src = (REPO / "xcheck" / "benchmark.py").read_text(encoding="utf-8")
        for model in ("gpt-5", "claude", "sonnet", "opus"):
            self.assertNotIn(f'"{model}":', src, "a price list by model name appeared")

    def test_human_review_time_is_recorded_never_derived(self):
        """Criterion 5. The gap between two timestamps is elapsed time, not attention."""
        fx, st, ev = outcome_fixture(durable=1, durations=(3600.0,))
        self.addCleanup(fx.cleanup)
        r = benchmark.value_report(st, ev)["human_review_seconds_per_outcome"]
        print(f"  REVIEW TIME a 3,600s session recorded, review value={r['value']} "
              f"({r['unmeasured_reason'][:50]})")
        self.assertIsNone(r["value"], "review time was derived from a duration")
        self.assertIn("recorded, never derived", r["caption"])

    def test_the_real_corpus_refuses_every_per_outcome_figure(self):
        """Not a fixture. This repository has 0 closures and 0 validations, so the four
        must refuse — and the table of zeroes this replaces would have read as a cheap,
        fast audit."""
        st = state_mod.load_state(REPO / "audit")
        r = benchmark.value_report(st, envelope.read_events(REPO))
        print(f"\n  REAL CORPUS useful outcomes={r['denominator']['useful_outcomes']}, "
              f"tokens measured={r['tokens_per_outcome']['tokens_measured']:,} over "
              f"{r['tokens_per_outcome']['measured_sessions']} of "
              f"{r['tokens_per_outcome']['finished_sessions']} sessions")
        print(f"    wall clock  P50={r['wall_clock_seconds']['p50']}s  "
              f"P95={r['wall_clock_seconds']['p95']}s over "
              f"{r['wall_clock_seconds']['sessions_timed']} timed session(s)")
        self.assertEqual(0, r["denominator"]["useful_outcomes"])
        for name in ("tokens_per_outcome", "money_per_outcome",
                     "human_review_seconds_per_outcome"):
            self.assertIsNone(r[name]["value"], name)
        self.assertIsNotNone(r["wall_clock_seconds"]["p50"])


# The A/B protocol, read ONCE and guarded.
#
# Both classes below used to read it in their class BODY. That runs at import, so in the
# exported tree — where `docs/` is internal and this file is not carried — the module
# raised `FileNotFoundError` during collection: one loader error standing in for the
# whole module, hiding the forty-odd tests here that have nothing to do with the
# document. Reading it here, guarded, turns that into two honest skips.
AB_PROTOCOL = REPO / "docs" / "ab-model-routing.md"
AB_PROTOCOL_TEXT = (AB_PROTOCOL.read_text(encoding="utf-8")
                    if AB_PROTOCOL.is_file() else "")
AB_PROTOCOL_WHY = ("the pre-registered A/B protocol these classes grade. `docs/` is "
                   "internal by default and the export names the files it ships one by "
                   "one; an exported tree has no document here to grade.")


@needs_repo_file("docs/ab-model-routing.md", AB_PROTOCOL_WHY)
class TheProtocolIsCorrectedAndStillUnrun(unittest.TestCase):
    """Criteria 6 and 7, over the document itself."""

    DOC = AB_PROTOCOL_TEXT

    def test_the_token_only_criterion_became_four_including_latency(self):
        for phrase in ("tokens per useful outcome", "money per useful outcome",
                       "wall-clock P50 and P95", "recorded human review time"):
            self.assertIn(phrase, self.DOC, phrase)
        print("\n  PROTOCOL   four criteria, latency included")

    def test_the_correction_is_marked_and_the_original_reasoning_kept(self):
        self.assertIn("Correction, 2026-09-05", self.DOC)
        self.assertIn("~~**Latency.**", self.DOC, "the original claim was deleted, "
                                                  "not struck through")
        self.assertIn("confounded by provider queueing", self.DOC,
                      "the reasoning being corrected is gone, so the correction is "
                      "unreadable")
        self.assertEqual(2, self.DOC.count("CORRECTED (phase 12)"))
        print("  CORRECTION marked, with the struck-through original kept beside it")

    def test_the_falsifier_moved_with_the_criterion(self):
        f2 = self.DOC[self.DOC.index("- **F2.**"):]
        f2 = f2[:f2.index("- **F3.**")]
        self.assertIn("per useful outcome", f2)
        self.assertIn("unmeasured", f2)
        self.assertIn("(Was:", f2, "the falsifier changed without saying what it was")
        print(f"  F2         {f2.strip()[:120]}")

    def test_it_is_still_written_not_run(self):
        self.assertIn("**Status: WRITTEN, NOT RUN.**", self.DOC)
        self.assertIn("Nothing here has been run", self.DOC)
        print("  UNRUN      the protocol is registered, and no paid run happened")


# -------------------------------------------------------------- PHASE 7 (sixth audit)
#
# Measurement, lower bound, estimate — never the same number.
#
#  was handed two finished sessions with a token figure on ONE of them,
# and that one figure `agent-reported`. It returned tokens_per_outcome=1000.0 and
# money_per_outcome=0.01 with unmeasured_reason=None: a confident number over 50%
# coverage and zero provider attestation, while the A/B document promises
# provider-attested data. Four more defects sat in the same function — one output rate
# over every token, NaN and Infinity accepted as rates, the sum of session durations
# reported as elapsed time, and `closed` + `attempts == 0` counted as a durable fix.

from datetime import date, timedelta  # noqa: E402

#: The audit's exact input: two finished sessions, tokens on one, agent-reported.
P7_AUDIT_EVENTS = [
    {"event": "session_finished", "session_id": "a", "tokens": 1000,
     "telemetry_source": "agent-reported", "ts": "2026-09-01T10:00:00+00:00"},
    {"event": "session_finished", "session_id": "b",
     "ts": "2026-09-01T10:10:00+00:00"},
]

#: Rates keyed per MODEL and per token CATEGORY. A single-rate calculation cannot be
#: expressed in this shape, which is the point — the impossibility is structural, not a
#: comment asking the next author not to.
MODEL_RATES = {
    "currency": "USD",
    "input_per_million": 2.0,
    "output_per_million": 10.0,
    "models": {
        "gpt-5": {"input_per_million": 1.25, "output_per_million": 10.0,
                  "cache_per_million": 0.125},
        "gpt-5-mini": {"input_per_million": 0.25, "output_per_million": 2.0,
                       "cache_per_million": 0.025},
    },
}


def p7_state(closed_days_ago=None, attempts=0):
    """A state whose one finding is closed `closed_days_ago` days ago, or still open."""
    if closed_days_ago is None:
        rec = finding_record("F-0001", status="reported")
    else:
        when = (date.today() - timedelta(days=closed_days_ago)).isoformat()
        rec = finding_record("F-0001", status="closed", attempts=attempts, updated=when)
    return state_mod._build(state_doc(findings=[rec]))


def p7_attested(n, model="gpt-5", **over):
    """`n` finished sessions the PROVIDER reported, with a category split."""
    out = []
    for i in range(n):
        out.append(dict({
            "event": "session_finished", "session_id": f"s{i}", "tokens": 1000,
            "tokens_input": 600, "tokens_output": 300, "tokens_cache": 100,
            "telemetry_source": "provider", "provider_model": model,
            "ts": f"2026-09-01T10:{i:02d}:00+00:00"}, **over))
    return out


class TheAuditsInputIsRefused(unittest.TestCase):
    """Criterion 1: the before and the after, on the same input."""

    def report(self, **over):
        return benchmark.value_report(p7_state(closed_days_ago=90), P7_AUDIT_EVENTS,
                                      **over)

    def test_tokens_per_outcome_no_longer_answers_over_half_a_corpus(self):
        rep = self.report()
        tp = rep["tokens_per_outcome"]
        print("\n  BEFORE     tokens_per_outcome: value=1000.0 unmeasured_reason=None")
        print(f"  AFTER      kind={tp['kind']!r} value={tp['value']!r} "
              f"coverage={tp['coverage']:.0%} floor={tp['coverage_floor']:.0%}")
        print(f"  AFTER      reason: {tp['unmeasured_reason'][:96]}…")
        self.assertIsNone(tp["value"], "a per-outcome figure over 50% coverage")
        self.assertEqual(benchmark.LOWER_BOUND, tp["kind"])
        self.assertIn("floor", tp["unmeasured_reason"])
        # The measured total survives: a floor may not suppress arithmetic already known.
        self.assertEqual(1000, tp["tokens_measured"])

    def test_money_per_outcome_refuses_an_agent_reported_figure(self):
        rep = self.report(rates=MODEL_RATES)
        mp = rep["money_per_outcome"]
        print("  BEFORE     money_per_outcome: value=0.01 unmeasured_reason=None")
        print(f"  AFTER      kind={mp['kind']!r} value={mp['value']!r} "
              f"attested_sessions={mp['attested_sessions']}")
        print(f"  AFTER      reason: {mp['unmeasured_reason'][:96]}…")
        self.assertIsNone(mp["value"])
        self.assertEqual(0, mp["attested_sessions"])
        self.assertIn("entitled to say so", mp["unmeasured_reason"])


class ProvenanceIsAPrecondition(unittest.TestCase):
    """And the CONTROL that stops it from being a refusal of everything."""

    def test_provider_attested_figures_at_the_same_coverage_DO_produce_a_number(self):
        events = p7_attested(2)                       # 2 of 2 finished: 100% coverage
        rep = benchmark.value_report(p7_state(closed_days_ago=90), events, rates=MODEL_RATES)
        mp, tp = rep["money_per_outcome"], rep["tokens_per_outcome"]
        print(f"\n  CONTROL    provider-attested, {tp['coverage']:.0%} coverage: "
              f"tokens/outcome={tp['value']} money/outcome={mp['value']} "
              f"{mp['currency']}")
        print(f"  CONTROL    by model: {mp['by_model']}")
        self.assertIsNotNone(tp["value"], "the check refuses even attested figures")
        self.assertIsNotNone(mp["value"])
        self.assertIsNone(mp["unmeasured_reason"])
        self.assertEqual(benchmark.MEASUREMENT, tp["kind"])

    def test_the_same_sessions_unattested_produce_nothing(self):
        """Same counts, same coverage, one field different."""
        events = p7_attested(2, telemetry_source="agent-reported")
        rep = benchmark.value_report(p7_state(closed_days_ago=90), events, rates=MODEL_RATES)
        print(f"  CONTRAST   identical counts, telemetry_source='agent-reported': "
              f"money={rep['money_per_outcome']['value']!r}")
        self.assertIsNone(rep["money_per_outcome"]["value"])


class TheCoverageFloor(unittest.TestCase):
    """Declared as a number, justified in one sentence, and exercised on both sides."""

    def test_the_floor_is_declared_and_justified(self):
        source = (xcheck_submodule("benchmark").__file__)
        with open(source, encoding="utf-8") as fh:
            text = fh.read()
        where = text.index("COVERAGE_FLOOR = ")
        justification = text[max(0, where - 700):where]
        print(f"\n  FLOOR      COVERAGE_FLOOR = {benchmark.COVERAGE_FLOOR}")
        self.assertEqual(0.80, benchmark.COVERAGE_FLOOR)
        self.assertIn("80%", justification, "the floor's value is not justified in prose")

    def test_just_under_the_floor_refuses_and_just_over_it_answers(self):
        # 5 finished sessions: 4 measured is 80% (the floor), 3 is 60%.
        over = p7_attested(4) + [{"event": "session_finished", "session_id": "x",
                               "ts": "2026-09-01T11:00:00+00:00"}]
        under = p7_attested(3) + [{"event": "session_finished", "session_id": "x",
                                "ts": "2026-09-01T11:00:00+00:00"},
                               {"event": "session_finished", "session_id": "y",
                                "ts": "2026-09-01T11:01:00+00:00"}]
        st = p7_state(closed_days_ago=90)
        at_floor = benchmark.value_report(st, over)["tokens_per_outcome"]
        below = benchmark.value_report(st, under)["tokens_per_outcome"]
        print(f"  AT FLOOR   {at_floor['coverage']:.0%} -> kind={at_floor['kind']!r} "
              f"value={at_floor['value']}")
        print(f"  BELOW      {below['coverage']:.0%} -> kind={below['kind']!r} "
              f"value={below['value']}")
        self.assertIsNotNone(at_floor["value"])
        self.assertEqual(benchmark.MEASUREMENT, at_floor["kind"])
        self.assertIsNone(below["value"])
        self.assertEqual(benchmark.LOWER_BOUND, below["kind"])


class RatesArePerModelAndPerCategory(unittest.TestCase):

    def test_a_cheap_model_and_a_strong_one_are_priced_differently(self):
        st = p7_state(closed_days_ago=90)
        strong = benchmark.value_report(st, p7_attested(2, model="gpt-5"), rates=MODEL_RATES)
        cheap = benchmark.value_report(st, p7_attested(2, model="gpt-5-mini"),
                                       rates=MODEL_RATES)
        s, c = strong["money_per_outcome"], cheap["money_per_outcome"]
        print(f"\n  PER MODEL  gpt-5 {s['total']} {s['currency']} vs gpt-5-mini "
              f"{c['total']} {c['currency']} for identical token counts")
        self.assertNotEqual(s["total"], c["total"],
                            "two models priced the same is the single-rate defect")
        self.assertGreater(s["total"], c["total"])

    def test_input_output_and_cache_are_priced_separately(self):
        st = p7_state(closed_days_ago=90)
        as_input = p7_attested(1, tokens_input=1000, tokens_output=0, tokens_cache=0)
        as_output = p7_attested(1, tokens_input=0, tokens_output=1000, tokens_cache=0)
        a = benchmark.value_report(st, as_input, rates=MODEL_RATES)["money_per_outcome"]
        b = benchmark.value_report(st, as_output, rates=MODEL_RATES)["money_per_outcome"]
        print(f"  PER CATEGORY 1000 input tokens: {a['total']} · "
              f"1000 output tokens: {b['total']}")
        self.assertNotEqual(a["total"], b["total"],
                            "one rate over every token is the defect this replaces")

    def test_the_total_equals_the_sum_of_its_models(self):
        """The two numbers printed side by side must agree. They did not: `total` added
        each model's RUNNING row total once per session, so two sessions on one model
        were charged three times their price."""
        st = p7_state(closed_days_ago=90)
        mp = benchmark.value_report(st, p7_attested(2), rates=MODEL_RATES)["money_per_outcome"]
        parts = round(sum(r["money"] for r in mp["by_model"].values()), 6)
        print(f"  AGREES     total={mp['total']} · sum(by_model)={parts}")
        self.assertEqual(mp["total"], parts,
                         "total and by_model disagree: the per-model row is being "
                         "re-added once per session")
        one = benchmark.value_report(st, p7_attested(1),
                                     rates=MODEL_RATES)["money_per_outcome"]
        print(f"  LINEAR     1 session {one['total']} · 2 sessions {mp['total']}")
        # delta, not equality: each total is rounded to 6 places once, so doubling a
        # rounded figure and rounding a doubled one differ in the last digit. The defect
        # this guards against tripled the bill, not moved it by 1e-6.
        self.assertAlmostEqual(one["total"] * 2, mp["total"], delta=2e-6,
                               msg="doubling the sessions must double the bill, not "
                                   "triple it")

    def test_a_model_with_no_declared_rate_refuses_rather_than_guessing(self):
        st = p7_state(closed_days_ago=90)
        rep = benchmark.value_report(st, p7_attested(2, model="some-new-model"),
                                     rates=MODEL_RATES)
        mp = rep["money_per_outcome"]
        print(f"  UNPRICED   {mp['unmeasured_reason'][:88]}…")
        self.assertIsNone(mp["value"])
        self.assertIn("some-new-model", mp["unmeasured_reason"])

    def test_the_input_SHAPE_makes_a_single_rate_impossible(self):
        """Asserted by the shape rather than by a comment: money comes from a mapping
        keyed by model, and each model's entry is keyed by category. There is nowhere to
        put one number that applies to everything."""
        for model, row in MODEL_RATES["models"].items():
            self.assertEqual({"input_per_million", "output_per_million",
                              "cache_per_million"}, set(row),
                             f"{model}'s rates are not per category")
        print(f"  SHAPE      rates.models = {sorted(MODEL_RATES['models'])}, each with "
              f"{sorted(MODEL_RATES['models']['gpt-5'])}")


class BrokenRatesRaiseByName(unittest.TestCase):

    def bad(self, value):
        rates = {"currency": "USD", "input_per_million": 2.0, "output_per_million": 10.0,
                 "models": {"gpt-5": {"input_per_million": value,
                                      "output_per_million": 10.0,
                                      "cache_per_million": 0.1}}}
        return benchmark.value_report(p7_state(closed_days_ago=90), p7_attested(2),
                                      rates=rates)

    def test_nan_infinity_and_negative_each_raise(self):
        seen = {}
        for name, value in (("NaN", float("nan")), ("Infinity", float("inf")),
                            ("negative", -1.0)):
            with self.assertRaises(benchmark.BenchmarkError) as e:
                self.bad(value)
            seen[name] = str(e.exception)
        print(f"\n  MODEL_RATES      NaN      -> {seen['NaN'][:70]}")
        print(f"  MODEL_RATES      Infinity -> {seen['Infinity'][:70]}")
        print(f"  MODEL_RATES      negative -> {seen['negative'][:70]}")
        self.assertIn("NaN", seen["NaN"])
        self.assertIn("inf", seen["Infinity"])
        self.assertIn("negative", seen["negative"])

    def test_CONTROL_a_sound_rate_does_not_raise(self):
        rep = benchmark.value_report(p7_state(closed_days_ago=90), p7_attested(2),
                                     rates=MODEL_RATES)
        print(f"  MODEL_RATES      sound    -> {rep['money_per_outcome']['total']} USD")
        self.assertIsNotNone(rep["money_per_outcome"]["total"])


class WallClockIsTheAuditsOwnSpan(unittest.TestCase):

    def test_a_parallel_fixture_where_the_two_differ(self):
        """Two sessions of 600s that overlap: the audit took 610s and the sum says 1200."""
        events = [
            {"event": "session_finished", "session_id": "a", "tokens": 1000,
             "telemetry_source": "provider", "provider_model": "gpt-5",
             "tokens_input": 600, "tokens_output": 300, "tokens_cache": 100,
             "ts": "2026-09-01T10:00:00+00:00"},
            {"event": "session_finished", "session_id": "b", "tokens": 1000,
             "telemetry_source": "provider", "provider_model": "gpt-5",
             "tokens_input": 600, "tokens_output": 300, "tokens_cache": 100,
             "ts": "2026-09-01T10:10:10+00:00"},
        ]
        doc = state_doc(findings=[finding_record(
            "F-0001", status="closed", attempts=0,
            updated=(date.today() - timedelta(days=90)).isoformat())])
        doc["sessions"] = [{"session_id": "a", "duration_s": 600.0},
                           {"session_id": "b", "duration_s": 600.0}]
        rep = benchmark.value_report(state_mod._build(doc), events, rates=MODEL_RATES)
        wc = rep["wall_clock_seconds"]
        print(f"\n  WALL CLOCK audit span {wc['audit_span_s']}s · sum of sessions "
              f"{wc['sum_of_sessions_s']}s")
        print(f"  WALL CLOCK per_outcome divides {wc['audit_span_s']}s "
              f"(kind={wc['kind']!r})")
        self.assertEqual(610.0, wc["audit_span_s"])
        self.assertEqual(1200.0, wc["sum_of_sessions_s"])
        self.assertNotEqual(wc["audit_span_s"], wc["sum_of_sessions_s"])
        self.assertEqual(610.0, wc["per_outcome"],
                         "per_outcome divides the sum, which is not elapsed time")
        self.assertIn("NOT elapsed time", wc["caption"])


class DurabilityNeedsAnObservationWindow(unittest.TestCase):

    def test_a_closure_from_yesterday_is_not_yet_a_durable_fix(self):
        fresh = benchmark.useful_outcomes(p7_state(closed_days_ago=1))
        old = benchmark.useful_outcomes(p7_state(closed_days_ago=90))
        print(f"\n  WINDOW     {benchmark.DURABILITY_WINDOW_DAYS} days · closed "
              f"yesterday -> {fresh[1]} · closed 90 days ago -> {old[1]}")
        self.assertEqual(0, fresh[1]["durable_fix"],
                         "a closure with no observation window counted as durable")
        self.assertEqual(1, old[1]["durable_fix"])
        # It does not VANISH: an un-aged closure is still a record that survived
        # validation, so the denominator keeps it under the kind it has earned.
        self.assertEqual(1, fresh[1]["confirmed_defect"])
        self.assertEqual(1, fresh[0])
        self.assertEqual(1, old[0])

    def test_a_reset_counter_does_not_buy_durability(self):
        """`attempts == 0` on a record closed today is what a rewrite produces. The old
        rule counted it; the window is what refuses it."""
        reset = p7_state(closed_days_ago=0, attempts=0)
        now = benchmark.useful_outcomes(reset)
        print(f"  RESET      closed today, attempts=0 -> {now[1]}")
        self.assertEqual(0, now[1]["durable_fix"])
        # And the old behaviour, reproduced with window=0, to show what changed.
        was = benchmark.useful_outcomes(reset, window=0)
        print(f"  OLD RULE   the same record with window=0 -> {was[1]}")
        self.assertEqual(1, was[1]["durable_fix"],
                         "window=0 must reproduce the rule this phase replaced")

    def test_the_window_is_declared(self):
        self.assertEqual(14, benchmark.DURABILITY_WINDOW_DAYS)


class EveryFigureSaysWhichKindItIs(unittest.TestCase):

    FIGURES = ("tokens_per_outcome", "money_per_outcome", "wall_clock_seconds",
               "human_review_seconds_per_outcome")

    def test_no_figure_is_unlabelled(self):
        rep = benchmark.value_report(p7_state(closed_days_ago=90), p7_attested(2),
                                     rates=MODEL_RATES, review_seconds=120)
        kinds = {f: rep[f].get("kind") for f in self.FIGURES}
        print(f"\n  KINDS      {kinds}")
        for figure, kind in kinds.items():
            self.assertIn(kind, benchmark.KINDS,
                          f"{figure} is labelled {kind!r}, which is not one of the three")

    def test_money_is_an_estimate_even_at_full_coverage(self):
        """It multiplies measured counts by prices the operator DECLARED, and a declared
        price is an assumption about a bill nobody here has seen."""
        rep = benchmark.value_report(p7_state(closed_days_ago=90), p7_attested(2),
                                     rates=MODEL_RATES)
        print(f"  KINDS      money at 100% coverage is still "
              f"{rep['money_per_outcome']['kind']!r}")
        self.assertEqual(benchmark.ESTIMATE, rep["money_per_outcome"]["kind"])


@needs_live_corpus
class TheRealCorpusStillReads(unittest.TestCase):
    """Criterion 10: this repository's own 1,043 events and 159 sessions, recomputed.

    Every figure the phase pins is a figure the rewrite must NOT have moved — the change
    was to what the numbers are called and what they refuse, not to the arithmetic over
    measured sessions. The one figure that is new is `audit_span_s`, which the old code
    did not compute at all.

    Note the two roots: `load_state` takes the AUDIT DIRECTORY and `read_events` takes the
    PROJECT. Passing the audit dir to `read_events` returns an empty stream and every
    figure below reads 0 — a wrong answer whose failure mode is silence.
    """

    @classmethod
    def setUpClass(cls):
        cls.state = state_mod.load_state(str(REPO / "audit"))
        cls.events = envelope.read_events(str(REPO))
        cls.report = benchmark.value_report(cls.state, cls.events)

    def test_the_pinned_figures_are_unchanged(self):
        t = self.report["tokens_per_outcome"]
        w = self.report["wall_clock_seconds"]
        d = self.report["denominator"]
        print(f"\n  CORPUS     {len(self.events)} event(s) · "
              f"{len(self.state.sessions)} session(s) in state")
        print(f"  CORPUS     finished {t['finished_sessions']} · measured "
              f"{t['measured_sessions']} · coverage {t['coverage']:.2%} · "
              f"{t['tokens_measured']} token(s)")
        print(f"  CORPUS     P50 {w['p50']}s · P95 {w['p95']}s · useful outcomes "
              f"{d['useful_outcomes']} {d['by_kind']}")
        self.assertEqual(159, len(self.state.sessions))
        self.assertEqual(158, t["finished_sessions"])
        self.assertEqual(7, t["measured_sessions"])
        self.assertEqual(0.0443, t["coverage"])
        self.assertEqual(1_666_799, t["tokens_measured"])
        self.assertEqual(429.552, w["p50"])
        self.assertEqual(910.022, w["p95"])
        self.assertEqual(0, d["useful_outcomes"])

    def test_what_the_corpus_now_refuses_and_why(self):
        """4.43% coverage is a twentieth of the floor, so the per-outcome figure is
        absent and the measured TOTAL is still reported — as the lower bound it is."""
        t = self.report["tokens_per_outcome"]
        print(f"  CORPUS     tokens kind={t['kind']!r} value={t['value']!r} "
              f"total={t['tokens_measured']}")
        self.assertEqual(benchmark.LOWER_BOUND, t["kind"])
        self.assertIsNone(t["value"])
        self.assertEqual(1_666_799, t["tokens_measured"], "the floor gated arithmetic")

    def test_the_two_wall_clocks_differ_on_the_real_corpus_by_twentyfold(self):
        w = self.report["wall_clock_seconds"]
        ratio = w["audit_span_s"] / w["sum_of_sessions_s"]
        print(f"  CORPUS     audit span {w['audit_span_s']}s · sum of sessions "
              f"{w['sum_of_sessions_s']}s · ratio {ratio:.1f}x")
        print("  CORPUS     reported per-outcome divides the SPAN")
        self.assertEqual(1_568_095.0, w["audit_span_s"])
        self.assertEqual(77_308.457, w["sum_of_sessions_s"])
        self.assertGreater(ratio, 15, "the two numbers are close enough to be confused")

    def test_no_figure_on_the_real_corpus_is_unlabelled(self):
        kinds = {k: self.report[k]["kind"] for k in benchmark.VALUE_METRICS}
        print(f"  CORPUS     {kinds}")
        for name, kind in kinds.items():
            self.assertIn(kind, benchmark.KINDS, name)


@needs_repo_file("docs/ab-model-routing.md", AB_PROTOCOL_WHY)
class TheF2CorrectionIsStruckThroughNotRewritten(unittest.TestCase):
    """Criterion 9. A run that quietly replaced its own admitted reasoning would be
    doing, to its own document, the thing the reconciler was faulted for doing to a
    finding: declaring the old text gone and calling that a fix."""

    DOC = AB_PROTOCOL_TEXT

    def f2(self):
        block = self.DOC[self.DOC.index("- **F2.**"):]
        return block[:block.index("- **F3.**")]

    def test_the_thirty_percent_token_bar_is_struck_through_and_still_readable(self):
        f2 = self.f2()
        struck = f2[f2.index("~~") + 2:f2.index("~~", f2.index("~~") + 2)]
        print(f"\n  STRUCK     {' '.join(struck.split())[:110]}…")
        self.assertIn("30%", struck, "the old bar is not inside the strikethrough")
        self.assertIn("(Was: token reduction below 30%, on the raw total.)", struck,
                      "the fifth audit's own note about what IT replaced was deleted")

    def test_the_replacement_is_a_money_bar_with_a_date(self):
        f2 = self.f2()
        replacement = f2[f2.rindex("~~") + 2:]
        print(f"  REPLACES   {' '.join(replacement.split())[:110]}…")
        self.assertIn("2026-09-06", f2, "the correction is undated")
        self.assertIn("sixth audit, phase 7", f2)
        self.assertIn("Money per useful outcome", replacement)
        # The needle is the BAR PHRASING, not the number: "30%" appears in the
        # replacement as the thing being retired ("the 30% bar was still a TOKEN bar"),
        # and a bare substring hunt cannot tell that from a new threshold.
        for bar in ("at least 30%", "30% below", "≥30%", "under 30%"):
            self.assertNotIn(bar, replacement,
                             f"the replacement still fixes a bar in advance: {bar!r}")
        self.assertIn("No percentage is fixed in advance", self.DOC)

    def test_tokens_are_reported_and_no_longer_gate(self):
        f2 = self.f2()
        self.assertIn("stops being a gate", f2)
        print("  TOKENS     reported, not gated")

    def test_the_durable_fix_definition_carries_its_window(self):
        self.assertIn("observation window", self.DOC)
        self.assertIn("14 days", self.DOC)
        # And the definition it replaces is still legible beside it.
        self.assertIn("~~closed on the first attempt, never reopened~~", self.DOC,
                      "the old definition was deleted rather than struck through")
        print("  DURABLE    14-day window, old definition struck through beside it")

    def test_the_three_kinds_are_named_in_the_document(self):
        for kind in benchmark.KINDS:
            self.assertIn(f"`{kind}`", self.DOC, kind)
        print(f"  KINDS      {list(benchmark.KINDS)} all named in the protocol")

    def test_it_is_STILL_unrun_after_the_correction(self):
        self.assertIn("**Status: WRITTEN, NOT RUN.**", self.DOC)
        self.assertIn("nothing here has been run", self.DOC.lower())
        self.assertIn("4.4% coverage", self.DOC)
        print("  UNRUN      still 4.4% coverage, still 0% provider-attested, still unrun")
