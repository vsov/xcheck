"""Which model does which work, and the one direction it must never fail in.

The audit's sentence has two halves and they pull opposite ways: paying a strong model
to list files is the largest avoidable cost, and paying a cheap one to judge a security
invariant is the largest avoidable risk. A router that optimises only the first half is
worse than no router at all, so most of this module is about the second.

  1. the table is DATA and every cell is written out — the test enumerates it and reads
     the shipped tuple, so a new work kind cannot arrive unrouted;
  2. an unrouted kind fails closed to STRONG, never to cheap;
  3. a HIGH-risk pass cannot be routed cheap — not by the shipped table and not by an
     operator override, and the refusal names the pass and the rule;
  4. routing states model STRENGTH and never grants or waives INDEPENDENCE;
  5. no model chooses the route: resolving one makes no subprocess call at all;
  6. off by default, and the off case computes no route to branch on.
"""

import subprocess
import unittest
from pathlib import Path
from unittest import mock

from tests.harness import needs_live_corpus, REPO, xcheck_submodule

routing = xcheck_submodule("routing")
envelope = xcheck_submodule("envelope")
state_mod = xcheck_submodule("state")
util = xcheck_submodule("util")
decision = xcheck_submodule("decision")


class TheTableIsData(unittest.TestCase):

    def test_every_work_kind_and_risk_has_a_written_out_cell(self):
        missing = [(k, r) for k in routing.WORK_KINDS for r in routing.RISKS
                   if (k, r) not in routing.TABLE]
        self.assertEqual([], missing, "a cell nobody wrote is a cell that gets defaulted")
        stray = sorted(k for k, _r in routing.TABLE if k not in routing.WORK_KINDS)
        self.assertEqual([], stray)
        print(f"TABLE {len(routing.TABLE)} cells over "
              f"{len(routing.WORK_KINDS)} kinds x {len(routing.RISKS)} risks")
        for kind in routing.WORK_KINDS:
            print("  {:<22} {}".format(kind, "  ".join(
                f"{r}={routing.TABLE[(kind, r)]}" for r in routing.RISKS)))

    def test_the_audits_five_rows_are_covered(self):
        """The audit's own table, row for row, at its normal risk."""
        rows = [
            # PHASE 13: the audit's row said `deterministic/cheap` and this asserted
            # `deterministic`, a route naming something the tool cannot do — there is no
            # mechanism to not dispatch, so an inventory pass ran the same expensive
            # model under a label promising the largest available saving. The row is
            # `cheap` now, which is what happens.
            ("inventory, formatting, dedup", routing.INVENTORY, routing.CHEAP),
            ("first-pass obvious defects", routing.OBVIOUS_DEFECTS, routing.CHEAP),
            ("architectural / semantic invariants", routing.INVARIANTS, routing.STRONG),
            # PHASE 13: `strong-independent` folded an independence REQUIREMENT into the
            # strength vocabulary, and this module never enforced it —
            # `stop-verifier-independence` does. Strength here, independence there; the
            # arm below asserts the independence machinery is untouched by the collapse.
            ("security / high-severity verification", routing.SECURITY_VERIFICATION,
             routing.STRONG),
            ("report re-synthesis", routing.REPORT_SYNTHESIS, routing.CHEAP),
        ]
        self.assertEqual(len(rows), len(routing.WORK_KINDS))
        for label, kind, expected in rows:
            got = routing.route_for(kind, routing.risk_of())
            self.assertEqual(expected, got, label)
            print(f"AUDIT ROW  {label:<38} -> {got}")

    def test_the_shipped_table_obeys_its_own_rule(self):
        self.assertEqual([], routing.table_problems())


class UnroutedFailsClosedToStrong(unittest.TestCase):

    def test_a_work_kind_nobody_classified_takes_the_strong_route(self):
        for unclassified in (None, "sentiment-analysis", "", "INVENTORY"):
            got = routing.route_for(unclassified, routing.NORMAL)
            self.assertEqual(routing.STRONG, got)
            self.assertNotEqual(routing.CHEAP, got)
        print("FAIL CLOSED unrouted work kind -> "
              f"{routing.route_for('sentiment-analysis', routing.NORMAL)} "
              f"(never {routing.CHEAP})")

    def test_a_dimension_nobody_listed_does_not_become_cheap(self):
        self.assertIsNone(routing.work_kind(role="Auditor", dimension="astrology"))
        self.assertEqual(routing.STRONG, routing.route_for(
            routing.work_kind(role="Auditor", dimension="astrology"), routing.NORMAL))
        print("FAIL CLOSED unknown dimension `astrology` -> work_kind None -> strong")

    def test_a_verifier_is_security_verification_whatever_the_dimension_says(self):
        self.assertEqual(routing.SECURITY_VERIFICATION,
                         routing.work_kind(role="Verifier", dimension="style"))
        self.assertEqual(routing.STRONG, routing.route_of_dispatch(
            {routing.FLAG: "on"}, role="Verifier", dimension="style"))


class HighRiskIsNeverCheap(unittest.TestCase):
    """The phase's control, and the reason the table is closed."""

    def test_no_shipped_cell_routes_a_high_risk_pass_cheap(self):
        for kind in routing.WORK_KINDS:
            self.assertNotIn(routing.TABLE[(kind, routing.HIGH)],
                             routing.NEVER_AT_HIGH_RISK, kind)

    def test_an_operator_override_that_buys_savings_out_of_a_verdict_is_refused(self):
        override = {(routing.SECURITY_VERIFICATION, routing.HIGH): routing.CHEAP}
        with self.assertRaises(ValueError) as caught:
            routing.route_for(routing.SECURITY_VERIFICATION, routing.HIGH,
                              overrides=override, where="pass P-31")
        msg = str(caught.exception)
        self.assertIn("pass P-31", msg)
        self.assertIn("HIGH-risk pass may never be routed", msg)
        print("CONTROL    ", msg[:150])

    def test_a_benign_override_is_allowed_so_the_refusal_is_not_a_blanket_no(self):
        """The negative arm has to be able to pass, or the refusal proves nothing."""
        # `a-refusal-test-passes-over-a-dead-feature`: the benign arm must be APPLIED,
        # not merely not-refused. The override moves a normally-cheap row to strong,
        # which is the direction an operator is always allowed to go.
        override = {(routing.OBVIOUS_DEFECTS, routing.LOW): routing.STRONG}
        self.assertEqual(routing.CHEAP,
                         routing.route_for(routing.OBVIOUS_DEFECTS, routing.LOW))
        self.assertEqual(routing.STRONG, routing.route_for(
            routing.OBVIOUS_DEFECTS, routing.LOW, overrides=override))
        print("CONTROL     a benign override IS applied -> cheap becomes "
              f"{routing.route_for(routing.OBVIOUS_DEFECTS, routing.LOW, overrides=override)}")

    def test_a_critical_finding_makes_the_pass_high_risk_by_itself(self):
        self.assertEqual(routing.NORMAL, routing.risk_of(dimension="style"))
        self.assertEqual(routing.HIGH,
                         routing.risk_of(dimension="style", severities=["critical"]))
        # And that promotion is what changes the route, not a separate rule.
        self.assertEqual(routing.CHEAP, routing.route_of_dispatch(
            {routing.FLAG: "on"}, role="Auditor", dimension="style"))
        self.assertEqual(routing.STRONG, routing.route_of_dispatch(
            {routing.FLAG: "on"}, role="Auditor", dimension="style",
            severities=["critical"]))
        print("RISK        style pass: normal -> cheap; with a critical finding -> strong")


class RoutingCannotOverrideIndependence(unittest.TestCase):

    def test_a_route_states_strength_and_independence_is_measured_elsewhere(self):
        """Independence is a REQUIREMENT this module states and never enforces. Whether
        it was met is `envelope.independence_level`'s answer, and a route cannot change
        it.

        PHASE 13: this asked `independence_still_decides(STRONG_INDEPENDENT)` — putting a
        question about independence to the strength vocabulary, which is the confusion
        that route name created. It keys on the WORK KIND now. The claim is unchanged and
        the arm below is the proof of it: the strongest route in the table, over a fixer
        and verifier that are the SAME session, and the independence machinery still says
        `same`."""
        self.assertTrue(routing.independence_still_decides(
            routing.SECURITY_VERIFICATION))
        self.assertFalse(routing.independence_still_decides(routing.INVENTORY))
        same = {"session_id": "a" * 16, "provider": "claude", "agent_model": "opus"}
        # The strongest route in the table, over a fixer and verifier that are the SAME
        # session. The independence machinery still says `same`.
        self.assertEqual("same", envelope.independence_level(same, same))
        print("INDEPENDENCE route=strong, same session -> "
              f"independence_level={envelope.independence_level(same, same)!r} "
              "(the route did not grant it)")

    def test_routing_cannot_waive_it_either(self):
        fixer = {"session_id": "a" * 16, "provider": "claude", "agent_model": "opus"}
        verifier = {"session_id": "b" * 16, "provider": "claude", "agent_model": "opus"}
        level = envelope.independence_level(fixer, verifier)
        self.assertEqual("same-provider-different-session", level)
        # Routing the verification cheap is refused before it could even be attempted,
        # and routing it strong does not upgrade the level above.
        with self.assertRaises(ValueError):
            routing.route_for(routing.SECURITY_VERIFICATION, routing.HIGH,
                              overrides={(routing.SECURITY_VERIFICATION,
                                          routing.HIGH): routing.CHEAP},
                              where="finding F-0001")
        self.assertEqual(level, envelope.independence_level(fixer, verifier))
        print(f"INDEPENDENCE routing a verification cheap is refused; level stays {level!r}")


class NoModelChoosesTheRoute(unittest.TestCase):

    def test_resolving_a_route_makes_no_subprocess_call(self):
        with mock.patch.object(subprocess, "run") as run, \
                mock.patch.object(subprocess, "Popen") as popen, \
                mock.patch.object(subprocess, "check_output") as check:
            for kind in routing.WORK_KINDS:
                for risk in routing.RISKS:
                    routing.route_for(kind, risk)
            routing.route_of_dispatch({routing.FLAG: "on"}, role="Auditor",
                                      dimension="security")
            calls = run.call_count + popen.call_count + check.call_count
        self.assertEqual(0, calls, "the router executed something")
        # A positive control: the spy CAN see a call, so 0 above means silence and not
        # a patch that missed its target.
        with mock.patch.object(subprocess, "run") as run:
            subprocess.run(["true"])
        self.assertEqual(1, run.call_count)
        print(f"NO MODEL    {len(routing.TABLE) + 1} resolutions -> 0 subprocess calls "
              f"(spy proven live: 1)")

    def test_the_module_imports_nothing_that_could_call_a_provider(self):
        text = Path(xcheck_submodule("routing").__file__).read_text(encoding="utf-8")
        for banned in ("subprocess", "urllib", "socket", "http", "requests"):
            self.assertNotIn(f"import {banned}", text,
                             f"routing imports {banned}: a table does not need one")


class OffByDefault(unittest.TestCase):

    def test_the_flag_is_off_and_the_off_case_computes_no_route(self):
        self.assertEqual("off", util.CONF_DEFAULTS[routing.FLAG])
        self.assertIn(routing.FLAG, util.BOOLEAN_CONF)
        self.assertFalse(routing.routing_enabled({}))
        self.assertIsNone(routing.route_of_dispatch({}, role="Auditor",
                                                    dimension="security"))
        self.assertIsNone(routing.route_of_dispatch({routing.FLAG: "off"},
                                                    role="Verifier"))
        self.assertEqual(routing.STRONG, routing.route_of_dispatch(
            {routing.FLAG: "on"}, role="Verifier"))
        print(f"FLAG        default={util.CONF_DEFAULTS[routing.FLAG]}; "
              f"off -> {routing.route_of_dispatch({}, role='Verifier')}; "
              f"on -> {routing.route_of_dispatch({routing.FLAG: 'on'}, role='Verifier')}")

    def test_an_envelope_omits_the_field_entirely_when_no_route_was_resolved(self):
        """Byte-identical when off: the record has no `route` key at all, rather than a
        `route: strong` nobody decided."""
        from types import SimpleNamespace
        profile = SimpleNamespace(name="worktree", details=lambda conf=None, verified=None: {})
        off = envelope.dispatch_record(
            ["true"], "Auditor", "pass P-01", "prompt", "a" * 16, profile, 1,
            head_before="0" * 40, route=None)
        on = envelope.dispatch_record(
            ["true"], "Auditor", "pass P-01", "prompt", "a" * 16, profile, 1,
            head_before="0" * 40, route=routing.STRONG)
        self.assertNotIn("route", off)
        self.assertEqual(routing.STRONG, on["route"])
        self.assertEqual({"route"}, set(on) - set(off),
                         "turning routing on changed more than the route field")
        print("ENVELOPE    off -> no `route` key; on -> route="
              f"{on['route']}; delta={sorted(set(on) - set(off))}")


class TheRouteIsRecordedAndPriceable(unittest.TestCase):

    def test_the_session_schema_declares_the_route_as_optional(self):
        required, optional = state_mod.SESSION_FIELDS
        self.assertIn("route", optional)
        self.assertNotIn("route", required, "a pre-phase-14 session recorded no route")

    def test_metrics_counts_sessions_per_route_and_names_the_unrouted(self):
        sessions = [{"route": routing.STRONG}, {"route": routing.STRONG},
                    {"route": routing.CHEAP}, {}]
        counts = decision._sessions_by_route(type("S", (), {"sessions": sessions})())
        self.assertEqual({routing.CHEAP: 1, routing.STRONG: 2, "unrouted": 1}, counts)
        self.assertIn("sessions_by_route", util.METRICS_SCHEMA["breakdowns"])
        self.assertEqual(len(sessions), sum(counts.values()),
                         "the denominator lost a session")
        print(f"PRICEABLE   sessions_by_route={counts}")


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------- PHASE 13

class ARouteChangesTheCommand(unittest.TestCase):
    """The audit's finding, reproduced and then closed. A route that is computed,
    recorded and discarded is worse than no routing: the label gets quoted in metrics as
    if it described what ran."""

    CMD = "codex exec --model {model} --sandbox workspace-write {prompt}"

    def conf(self, **over):
        return dict({"model_routing": "on", "cheap_model": "gpt-5-mini",
                     "strong_model": "gpt-5", "auditor_cmd": self.CMD}, **over)

    def test_argv_differs_by_route_and_the_off_case_is_refused_not_identical(self):
        runner = xcheck_submodule("runner")
        cheap = runner.build_cmd(self.conf(), "Auditor", "GO", route=routing.CHEAP)
        strong = runner.build_cmd(self.conf(), "Auditor", "GO", route=routing.STRONG)
        print(f"\n  ROUTE cheap   argv={cheap}")
        print(f"  ROUTE strong  argv={strong}")
        self.assertNotEqual(cheap, strong, "the two routes produce identical argv")
        self.assertIn("gpt-5-mini", cheap)
        self.assertIn("gpt-5", strong)
        self.assertNotIn("gpt-5-mini", strong)

        # With routing OFF the placeholder has nothing to fill it, and passing `{model}`
        # to a provider verbatim is a request for a model of that name: the CLI rejects
        # it, and the operator's real mistake is invisible in the log.
        with self.assertRaises(SystemExit) as e:
            runner.build_cmd(self.conf(model_routing="off"), "Auditor", "GO")
        print(f"  ROUTE off     REFUSED: {str(e.exception)[:120]}")

    def test_the_command_with_no_placeholder_is_refused_when_routing_is_on(self):
        """The reproduction, as a permanent gate. This is the exact state the audit
        found: routing on, a command that cannot express a model, argv unchanged."""
        bad = routing.routing_problem(self.conf(), routing.CHEAP,
                                      "codex exec --sandbox workspace-write {prompt}")
        self.assertIsNotNone(bad)
        self.assertIn("recorded and never passed to the provider", bad)
        print(f"  NO SLOT       {bad[:150]}")

    def test_a_route_with_no_model_assigned_is_refused(self):
        bad = routing.routing_problem(self.conf(cheap_model=""), routing.CHEAP, self.CMD)
        self.assertIn("cheap_model` is unset", bad)
        self.assertIn("a label that metrics will quote as a fact", bad)

    def test_CONTROL_a_properly_configured_route_is_not_refused(self):
        self.assertIsNone(routing.routing_problem(self.conf(), routing.CHEAP, self.CMD))
        self.assertIsNone(routing.routing_problem(self.conf(), routing.STRONG, self.CMD))

    def test_a_route_resolves_to_a_provider_and_a_model(self):
        """Two halves from two operator declarations: the model from the route, the
        provider from `telemetry_adapter`. Neither guesses at the other."""
        provider_mod = xcheck_submodule("provider")
        got = routing.resolution(self.conf(), routing.CHEAP,
                                 provider_mod.ADAPTERS["codex"])
        self.assertEqual(("openai", "gpt-5-mini"), got)
        print(f"  RESOLUTION    cheap -> provider={got[0]!r} model={got[1]!r}")

    def test_nothing_here_asks_a_model_which_model_to_use(self):
        """Over the IMPORTS, not the prose. The module's own docstring says "no
        subprocess" while explaining why, and a substring hunt reads the explanation as
        the violation — `assert-over-the-ast-not-the-prose`, met twice this run."""
        import ast
        tree = ast.parse((REPO / "xcheck" / "routing.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(set(), imported & {"subprocess", "requests", "urllib",
                                            "http", "socket"},
                         f"the router reaches outside itself: {sorted(imported)}")
        print(f"  DICT LOOKUP   routing.py imports {sorted(imported) or 'nothing'} — "
              f"no provider call, no subprocess, no scoring function")


class TheRouteIsAttestedOrItIsNotRecorded(unittest.TestCase):
    """What turns a route from an annotation into a fact: the PROVIDER says what ran, and
    a disagreement refuses the record naming both."""

    def setUp(self):
        import json
        import tempfile
        provider_mod = xcheck_submodule("provider")
        self.adapter = provider_mod.ADAPTERS["codex"]
        self.log = Path(tempfile.mkdtemp(prefix="xcheck-route-")) / "s.log"
        self.log.write_text("work\n", encoding="utf-8")
        self.sid = "0" * 16
        envelope.make_sidecar_dir(self.log)
        envelope.sidecar_path(self.log, self.sid).write_text(json.dumps({
            "session_id": self.sid, "model": "gpt-5", "input_tokens": 4,
            "output_tokens": 6, "total_tokens": 10}), encoding="utf-8")

    def finish(self, planned, route):
        return envelope.finish({"session_id": self.sid}, 0, "ok", 1.0, "a" * 40,
                               self.log, adapter=self.adapter, planned_model=planned,
                               route=route)

    def test_a_disagreeing_model_REFUSES_and_names_both(self):
        with self.assertRaises(routing.RouteError) as e:
            self.finish("gpt-5-mini", routing.CHEAP)
        msg = str(e.exception)
        self.assertIn("gpt-5-mini", msg)
        self.assertIn("gpt-5", msg)
        self.assertIn("cheap", msg)
        print(f"\n  REFUSED       {msg[:190]}")

    def test_CONTROL_an_agreeing_session_finishes_normally(self):
        """`a-refusal-test-passes-over-a-dead-feature`: without this the refusal could be
        universal and the arm above would still be green."""
        rec = self.finish("gpt-5", routing.STRONG)
        self.assertEqual("attested", rec["route_attestation"])
        self.assertEqual("gpt-5", rec["provider_model"])
        print(f"  CONTROL       agreeing session finishes: "
              f"route_attestation={rec['route_attestation']!r}")

    def test_no_provider_figure_is_UNATTESTED_and_not_a_mismatch(self):
        """Silence is neither agreement nor disagreement. Treating it as a mismatch would
        refuse every session on a machine with no adapter configured; treating it as
        agreement would let the label stand unchecked."""
        import tempfile
        bare = Path(tempfile.mkdtemp(prefix="xcheck-route-")) / "s.log"
        bare.write_text("nothing\n", encoding="utf-8")
        rec = envelope.finish({"session_id": self.sid}, 0, "ok", 1.0, "a" * 40, bare,
                              adapter=self.adapter, planned_model="gpt-5",
                              route=routing.STRONG)
        self.assertEqual("unattested", rec["route_attestation"])
        print(f"  UNATTESTED    no provider figure -> "
              f"{rec['route_attestation']!r} (not a refusal)")

    def test_the_attestation_reaches_the_closed_session_schema(self):
        _required, optional = state_mod.SESSION_FIELDS
        self.assertIn("route_attestation", optional)

    def test_the_runner_records_a_mismatch_rather_than_losing_the_session(self):
        """PHASE 2 (fifth audit): the same claim, EXECUTED.

        This used to grep `runner.py` for `outcome = "protocol-violation"`. The string
        was there and the branch had never run — the routed dispatch died before a child
        existed, so no provider could disagree with it — and the value it pinned was a
        PROTOCOL answer written into the session OUTCOME field, which `state.py` refuses.
        A record the schema will not take is not "recording a mismatch rather than losing
        the session"; it is losing the session, with a source grep saying otherwise.
        """
        src = (REPO / "xcheck" / "runner.py").read_text(encoding="utf-8")
        self.assertIn("except routing.RouteError", src)
        from tests.test_smoke_e2e import WrongModel
        case = WrongModel("test_a_provider_that_reports_another_model_refuses_the_route")
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.test_a_provider_that_reports_another_model_refuses_the_route()
        rec = case.session_record()
        print(f"  MISMATCH      kept: outcome={rec['outcome']!r}, "
              f"in the closed set {rec['outcome'] in util.OUTCOMES}")
        self.assertIn(rec["outcome"], util.OUTCOMES)


@needs_live_corpus
class HistoricalRouteFiguresAreLabels(unittest.TestCase):
    """Criterion 8, stated rather than quietly fixed: `sessions_by_route` existed before
    the route changed anything, so every historical figure it produced is a breakdown of
    a recorded-but-unapplied label."""

    def test_the_real_corpus_carries_no_attested_route(self):
        st = state_mod.load_state(REPO / "audit")
        routed = [s for s in st.sessions if s.get("route")]
        attested = [s for s in st.sessions if s.get("route_attestation") == "attested"]
        print(f"\n  HISTORY       {len(st.sessions)} sessions, {len(routed)} carry a "
              f"route, {len(attested)} attested")
        print("  READ AS       any `sessions_by_route` breakdown over sessions before "
              "this phase describes a LABEL, not a model: argv was byte-identical with "
              "routing on and off, so every route ran the same model. Those figures "
              "cannot be compared with post-phase-13 ones and are not retrofittable — "
              "the provider was never asked what ran.")
        self.assertEqual(0, len(attested),
                         "history gained an attestation nothing could have produced")


@needs_live_corpus
class ThePreRegisteredProtocolExistsAndIsUnrun(unittest.TestCase):

    PROTOCOL = REPO / "docs" / "ab-model-routing.md"

    def test_it_is_written_and_says_it_has_not_been_run(self):
        text = self.PROTOCOL.read_text(encoding="utf-8")
        self.assertIn("WRITTEN, NOT RUN", text)
        print(f"\n  PROTOCOL      {self.PROTOCOL.relative_to(REPO)}: "
              f"{len(text.splitlines())} lines, registered before any measurement")

    def test_it_names_the_four_success_criteria(self):
        text = self.PROTOCOL.read_text(encoding="utf-8")
        for needle in ("95%", "30%", "recall", "false-closure", "hard-budget"):
            self.assertIn(needle, text, needle)

    def test_it_names_its_falsifiers(self):
        text = self.PROTOCOL.read_text(encoding="utf-8")
        self.assertIn("Falsifiers", text)
        for f in ("F1.", "F2.", "F3.", "F4.", "F5."):
            self.assertIn(f, text, f)
        print("  FALSIFIERS    F1 missed finding · F2 <30% reduction · F3 unattested "
              "route · F4 high risk on cheap · F5 false closures up")

    def test_its_entry_condition_is_not_met_by_this_repository(self):
        """The protocol cannot start here, and the reason is phase 11's measurement."""
        st = state_mod.load_state(REPO / "audit")
        attested = [s for s in st.sessions if s.get("telemetry_source") == "provider"]
        pct = len(attested) / len(st.sessions) * 100
        self.assertLess(pct, 95.0)
        print(f"  ENTRY         {pct:.1f}% provider-attested telemetry against a 95% "
              f"entry condition — the run cannot begin, and no paid run is spent")


# ---------------------------------------------------------------------- PHASE 2 (fifth)

class OneBuildPerDispatch(unittest.TestCase):
    """The fourth audit's routing defect was a SECOND `build_cmd` call.

    `runner.py:2382` built the command with the route for the door checks and the
    `--dry-run` preview; `runner.py:2524` built it again with the sidecar and no route.
    Both builds were correct in isolation, which is why every test of `build_cmd` was
    green while the feature had never dispatched. The structural half of the fix is that
    there is now ONE call, and that it is handed both of the inputs that were split
    across the two.
    """

    SOURCE = REPO / "xcheck" / "runner.py"

    def calls(self):
        import ast
        tree = ast.parse(self.SOURCE.read_text(encoding="utf-8"))
        return [n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "build_cmd"]

    def test_run_session_builds_the_command_exactly_once(self):
        calls = self.calls()
        print(f"\n  BUILD SITES   {[c.lineno for c in calls]} in "
              f"{self.SOURCE.relative_to(REPO)}")
        self.assertEqual(1, len(calls),
                         f"build_cmd is called at lines {[c.lineno for c in calls]}; a "
                         f"second call site is how the route came to be dropped between "
                         f"the door check and the dispatch")

    def test_that_one_call_is_handed_both_the_route_and_the_sidecar(self):
        """Counting call sites is not enough on its own: one call that passes neither
        would satisfy the count and dispatch exactly the argv the audit found."""
        kwargs = {k.arg for k in self.calls()[0].keywords}
        print(f"  ITS KEYWORDS  {sorted(kwargs)}")
        self.assertIn("route", kwargs, "the one build is not handed the route")
        self.assertIn("sidecar", kwargs, "the one build is not handed the sidecar")


class TheChildReceivedTheModel(unittest.TestCase):
    """The same claim as `ARouteChangesTheCommand`, one seam further out.

    That class asks `build_cmd` what it returns. This one dispatches a real child process
    and reads the argv the child wrote down. The difference is the whole finding: the
    return value was right for four phases while nothing with a model in it ever ran.
    """

    def test_the_argv_a_real_child_recorded_carries_the_routed_model(self):
        from tests.test_smoke_e2e import RoutingApplied
        case = RoutingApplied("test_the_child_receives_the_routed_model")
        case.setUp()
        self.addCleanup(case.doCleanups)
        model, route = case.expected_model()
        sc, result, _out = case.routed()
        print(f"\n  CHILD ARGV    route={route!r} -> {sc.argv[3:5]}  (read from the file "
              f"the child wrote, not from build_cmd)")
        self.assertEqual(model, sc.argv[sc.argv.index("--model") + 1])
        self.assertEqual(0, int(result))
