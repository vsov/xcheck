"""Two dispatchers, one guarantee — compared as data, from what the CHILD received.

PHASE 5 (sixth audit). On one configuration — `model_routing=on` with `{model}` in the
role command — the sequential run launched a child with `--model strong-1` and the
parallel run refused on an unfilled placeholder and started nothing. The divergence was
wider than one argument: `parallel.py` built its command with neither `route` nor
`sidecar`, finished its envelopes without adapter, planned model or route, and wrote to a
hardcoded `audit/orchestrator-logs` instead of the operator's log root.

Two implementations of one guarantee is the audit's central finding, and growing
parallelism on top of them raises speed and the probability of an expensive failure at the
same time. Both dispatchers now call `runner.prepare_logs`, `runner.dispatch_route`,
`runner.prepare_launch`, `runner.finish_session` and `runner.result_problem`; only the
merge is parallel-specific, and the set below says so in a form the next audit can check.
"""

import ast
import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.harness import (REPO, Fixture, complete_conf, finding_record, queue_pass,
                           state_doc, xcheck_submodule)
from tests.test_smoke_e2e import Scenario

envelope = xcheck_submodule("envelope")
parallel = xcheck_submodule("parallel")
routing = xcheck_submodule("routing")
runner = xcheck_submodule("runner")

#: THE shared preparation. Both dispatchers reach every one of these, and a dispatcher
#: that reaches none of them is building its own — which is the defect.
SHARED = ("prepare_logs", "dispatch_route", "prepare_launch", "finish_session",
          "result_problem")

#: What legitimately stays parallel-specific, and why. Enumerated so the next audit can
#: check the claim without rereading this one: anything `parallel.py` defines that is not
#: here has to justify itself, and the arm below fails if the set grows.
PARALLEL_ONLY = {
    # the fan-out itself
    "run_passes": "the fan-out: a thread pool, a merge window, and the order results "
                  "are applied in",
    "agent_pass": "the per-pass closure the fan-out submits to that pool",
    "worker_count": "how many sessions may be in flight at once — a thread pool size",
    "enabled": "the flag read, which asks both probes before any fan-out starts",
    # deciding WHICH passes may run together
    "queued_passes": "the queue entries a fan-out would launch, in queue order",
    "unit_overlaps": "which queued passes would report on the same unit",
    "refuse_overlaps": "refusing a fan-out whose passes overlap, before launch — two "
                       "concurrent passes on one unit is not a sequential problem",
    "refuse_oversized_run": "the three ceilings on a fan-out, checked before anything "
                            "is dispatched",
    # giving each pass a disjoint place to file
    "reserve_ids": "disjoint id blocks per pass, which only concurrent filing needs",
    "id_block": "how many ids one pass reserves, derived from its filing limit",
    "block_charter": "the charter text that tells a pass which block it owns",
    # the merge
    "merge": "applying one pass's result through the single write path, optimistically",
    "harvest": "lifting one pass's findings out of its worktree state — the merge",
    "stage_artifacts": "carrying a pass's files across the worktree teardown (merge)",
    "apply_artifacts": "landing those staged files in the project (merge)",
    "Manifest": "the record of what was staged and what it hashed to (merge)",
    # the two probes that gate a fan-out
    "preservation_probe": "proving THIS build can carry a pass's evidence across the "
                          "worktree teardown before enabling a fan-out",
    "dispatch_divergence": "reporting a configuration the two dispatchers would handle differently, before any child of a fan-out starts",
}


class BothDispatchersProduceOneArgv(unittest.TestCase):
    """Read back from the child, not from a builder's return value."""

    def setUp(self):
        self.fx = Fixture(doc=state_doc(findings=[finding_record("F-0001")],
                                        queue=[queue_pass("P-01"), queue_pass("P-02")]))
        self.addCleanup(self.fx.cleanup)
        self.fx.git_init()

    def conf(self, sc, routed=True):
        over = dict(model_routing="on", cheap_model="gpt-5-mini", strong_model="gpt-5") \
            if routed else {}
        return complete_conf(auditor_cmd=sc.cmd("--model", "{model}") if routed
                             else sc.cmd(),
                             sandbox_profile="worktree", session_timeout=60,
                             parallel_passes="on", **over)

    def sequential_argv(self, sc, routed=True):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            runner.run_session(self.fx.root, self.conf(sc, routed), "Auditor", "P-01")
        return sc.argv

    def parallel_argv(self, sc, routed=True):
        conf = self.conf(sc, routed)
        session = parallel.agent_pass(self.fx.root, conf, ["P-01"],
                                      announce=lambda *_a, **_k: None)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            session("P-01")
        return sc.argv

    def normalise(self, argv):
        """Everything but the two things that MUST differ: the prompt (a pass's charter
        is not the sequential charter) and the sidecar/log path (one file per session)."""
        return [a if not a.startswith("/") and "\n" not in a else
                ("<path>" if a.startswith("/") else "<prompt>") for a in argv]

    def test_the_audits_configuration_launches_a_child_on_both_paths(self):
        """The exact reproduction: `model_routing=on` and `{model}`."""
        seq_sc = Scenario(self, stdout="sequential")
        par_sc = Scenario(self, stdout="parallel")
        seq = self.sequential_argv(seq_sc)
        par = self.parallel_argv(par_sc)
        print(f"\n  SEQUENTIAL {self.normalise(seq)}")
        print(f"  PARALLEL   {self.normalise(par)}")
        self.assertEqual(self.normalise(seq), self.normalise(par),
                         "the two dispatchers handed the child different commands")
        model = [a for a in seq if a.startswith("gpt-")]
        self.assertTrue(model, "the sequential child got no routed model")
        self.assertIn(model[0], par, "the parallel child got no routed model")
        print(f"  BOTH       ok, child started with --model {model[0]}")

    def test_CONTROL_routing_off_is_unchanged_on_both_paths(self):
        """Unification must not have made routing mandatory."""
        seq_sc = Scenario(self, stdout="sequential")
        par_sc = Scenario(self, stdout="parallel")
        seq = self.sequential_argv(seq_sc, routed=False)
        par = self.parallel_argv(par_sc, routed=False)
        print(f"  OFF/SEQ    {self.normalise(seq)}")
        print(f"  OFF/PAR    {self.normalise(par)}")
        self.assertEqual(self.normalise(seq), self.normalise(par))
        self.assertEqual([], [a for a in seq if a.startswith("gpt-")],
                         "routing off put a model in the argv")


class BothDispatchersWriteOneEnvelope(unittest.TestCase):
    """Field by field, for the same configuration."""

    #: The fields that describe THE DISPATCH — the ones both paths must answer the same
    #: way for one configuration. `planned_model` and `adapter` are inputs to `finish`,
    #: not envelope fields, and `route` is recorded by `dispatch_record`: that is where
    #: the parallel path was silently dropping it.
    FIELDS = ("route", "telemetry_source", "provider_model", "route_attestation",
              "sandbox_profile", "role", "tokens")

    def setUp(self):
        self.fx = Fixture(doc=state_doc(findings=[finding_record("F-0001")],
                                        queue=[queue_pass("P-01")]))
        self.addCleanup(self.fx.cleanup)
        self.fx.git_init()

    def conf(self, sc):
        return complete_conf(auditor_cmd=sc.cmd("--model", "{model}"),
                             sandbox_profile="worktree", session_timeout=60,
                             parallel_passes="on", model_routing="on",
                             cheap_model="gpt-5-mini", strong_model="gpt-5")

    def sessions(self):
        doc = json.loads((self.fx.audit / "state.json").read_text(encoding="utf-8"))
        return doc.get("sessions", [])

    def test_the_parallel_envelope_carries_the_routing_fields(self):
        sc = Scenario(self, stdout="parallel", telemetry={"model": "gpt-5",
                                                          "total_tokens": 10})
        session = parallel.agent_pass(self.fx.root, self.conf(sc), ["P-01"],
                                      announce=lambda *_a, **_k: None)
        with contextlib.redirect_stdout(io.StringIO()):
            session("P-01")
        rec = self.sessions()[-1]
        got = {f: rec.get(f) for f in ("route", "telemetry_source", "provider_model",
                                       "route_attestation", "tokens")}
        print(f"\n  PARALLEL ENVELOPE {got}")
        self.assertIsNotNone(rec.get("route"),
                             "the parallel envelope records no route, so every metric "
                             "aggregating by route excludes every parallel session")
        self.assertIsNotNone(rec.get("provider_model"),
                             "the parallel envelope records no actual model")
        # `telemetry_source` is deliberately NOT asserted to a value here: this fixture
        # declares no provider adapter, so BOTH paths answer `agent-reported`, and the
        # claim worth making is that they answer the SAME thing — which the next test
        # measures rather than this one assuming.
        self.assertIsNotNone(rec.get("telemetry_source"))

    def test_both_envelopes_carry_the_same_field_NAMES(self):
        """Not the same values — the charters differ — but the same shape. A field the
        parallel path never sets is a metric that silently excludes half the corpus."""
        seq_sc = Scenario(self, stdout="sequential",
                          telemetry={"model": "gpt-5", "total_tokens": 10})
        with contextlib.redirect_stdout(io.StringIO()):
            runner.run_session(self.fx.root, self.conf(seq_sc), "Auditor", "P-01")
        seq = self.sessions()[-1]

        par_sc = Scenario(self, stdout="parallel",
                          telemetry={"model": "gpt-5", "total_tokens": 10})
        session = parallel.agent_pass(self.fx.root, self.conf(par_sc), ["P-01"],
                                      announce=lambda *_a, **_k: None)
        with contextlib.redirect_stdout(io.StringIO()):
            session("P-01")
        par = self.sessions()[-1]

        missing = [f for f in self.FIELDS if f in seq and f not in par]
        differing = {f: (seq.get(f), par.get(f)) for f in self.FIELDS
                     if f in seq and f in par and seq.get(f) != par.get(f)}
        print(f"  SEQ FIELDS {sorted(f for f in self.FIELDS if f in seq)}")
        print(f"  PAR FIELDS {sorted(f for f in self.FIELDS if f in par)}")
        print(f"  VALUES     {[(f, seq.get(f)) for f in self.FIELDS if f in seq]}")
        self.assertEqual([], missing, f"the parallel envelope is missing {missing}")
        # And field by field on VALUE, for the fields that describe the dispatch rather
        # than the session: same configuration, same answers, or the two paths are still
        # two paths.
        self.assertEqual({}, differing,
                         f"the two dispatchers disagree field by field: {differing}")


class BothDispatchersResolveOneLogRoot(unittest.TestCase):
    """Compared as resolved paths, not by reading the code."""

    def test_the_same_directory_for_the_same_configuration(self):
        fx = Fixture(doc=state_doc(findings=[finding_record("F-0001")]))
        self.addCleanup(fx.cleanup)
        fx.git_init()
        conf = complete_conf(auditor_cmd="agent {prompt}")
        seq_logs, seq_path = runner.prepare_logs(fx.root, conf, "auditor")
        par_logs, par_path = runner.prepare_logs(fx.root, conf, "auditor-p-01")
        retention = xcheck_submodule("retention")
        operator = retention.log_root(fx.root, conf, create=False)
        print(f"\n  LOG ROOT   sequential {seq_logs}")
        print(f"  LOG ROOT   parallel   {par_logs}")
        print(f"  LOG ROOT   operator's {operator}")
        self.assertEqual(seq_logs, par_logs)
        self.assertEqual(operator, seq_logs)
        self.assertNotEqual("orchestrator-logs", seq_logs.name,
                            "the hardcoded parallel directory is back")
        self.assertNotEqual(seq_path.name, par_path.name,
                            "two sessions must not share one log file")


class OnePreparationTwoCallSites(unittest.TestCase):
    """Structural: the gate that survives the next dispatcher."""

    def calls_in(self, module, function):
        src = (REPO / "xcheck" / f"{module}.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == function)
        return {getattr(c.func, "id", getattr(c.func, "attr", ""))
                for c in ast.walk(fn) if isinstance(c, ast.Call)}

    def test_both_dispatchers_reach_the_shared_preparation(self):
        seq = self.calls_in("runner", "run_session")
        # The parallel dispatcher's per-session work is the closure inside `agent_pass`.
        par = self.calls_in("parallel", "agent_pass")
        print(f"\n  SHARED/SEQ {sorted(seq & set(SHARED))}")
        print(f"  SHARED/PAR {sorted(par & set(SHARED))}")
        for name in SHARED:
            self.assertIn(name, seq | par, f"nothing calls {name}")
            self.assertIn(name, par, f"the parallel dispatcher does not call {name}")

    def test_COUNTERFACTUAL_a_dispatcher_that_builds_its_own_command_is_caught(self):
        """`parallel.py` as it was: `build_cmd` directly, with no route and no sidecar."""
        src = (REPO / "xcheck" / "parallel.py").read_text(encoding="utf-8")
        mutant = src.replace("plan = prepare_launch(conf, \"Auditor\", prompt, session_id, profile, log_path,\n"
                             "                              route)\n        cmd = plan.cmd",
                             "cmd = build_cmd(conf, \"Auditor\", prompt)")
        self.assertNotEqual(src, mutant, "the mutation did not apply")
        fn = next(n for n in ast.walk(ast.parse(mutant))
                  if isinstance(n, ast.FunctionDef) and n.name == "agent_pass")
        reached = {getattr(c.func, "id", getattr(c.func, "attr", ""))
                   for c in ast.walk(fn) if isinstance(c, ast.Call)}
        print(f"  MUTANT     reaches prepare_launch: {'prepare_launch' in reached}; "
              f"builds its own: {'build_cmd' in reached}")
        self.assertNotIn("prepare_launch", reached)
        self.assertIn("build_cmd", reached)

    def test_the_builder_is_not_importable_by_the_parallel_dispatcher(self):
        """Belt and braces: a dispatcher that cannot reach the raw builder cannot build
        without a route by accident. The import list is the declaration."""
        src = (REPO / "xcheck" / "parallel.py").read_text(encoding="utf-8")
        imported = {alias.name for node in ast.walk(ast.parse(src))
                    if isinstance(node, ast.ImportFrom) and node.module == "xcheck.runner"
                    for alias in node.names}
        print(f"  IMPORTS    parallel takes {sorted(imported)} from runner")
        self.assertNotIn("build_cmd", imported,
                         "the parallel dispatcher can still reach the raw builder")
        for name in SHARED:
            self.assertIn(name, imported)


class WhatStaysParallelSpecific(unittest.TestCase):
    """The set, enumerated in code — checkable next audit without rereading this one."""

    def defined_in_parallel(self):
        src = (REPO / "xcheck" / "parallel.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        return {n.name for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.ClassDef))
                and not n.name.startswith("_")}

    def test_the_set_is_the_merge_and_nothing_else(self):
        defined = self.defined_in_parallel()
        undeclared = sorted(defined - set(PARALLEL_ONLY))
        stale = sorted(set(PARALLEL_ONLY) - defined)
        print(f"\n  PARALLEL-ONLY {len(defined)} public name(s)")
        for name in sorted(defined):
            print(f"    {name:22s} {PARALLEL_ONLY.get(name, '*** UNDECLARED ***')}")
        self.assertEqual([], undeclared,
                         f"parallel.py defines {undeclared} with no declared reason — if "
                         f"this is dispatch work it belongs in the shared preparation, "
                         f"and if it is merge work it belongs in PARALLEL_ONLY")
        self.assertEqual([], stale,
                         f"PARALLEL_ONLY names {stale}, which no longer exists")

    def test_every_reason_names_the_merge_or_the_fan_out(self):
        """A declaration that says 'because it is' would pass the arm above and mean
        nothing. Each reason has to name the thing that legitimately differs."""
        vague = [n for n, why in PARALLEL_ONLY.items()
                 if not any(w in why.lower() for w in
                            ("merge", "fan-out", "pass", "flag", "operator", "worktree",
                             "thread", "concurrent", "dispatcher"))]
        print(f"  REASONS    {len(PARALLEL_ONLY)} declared, {len(vague)} vague")
        self.assertEqual([], vague, f"these reasons explain nothing: {vague}")


class TheRefusalLandsBeforeAnyChild(unittest.TestCase):
    """Criterion 1 and criterion 10 in one mechanism: a refusal that is COMPUTED.

    It must fire on a configuration the shared preparation cannot dispatch, and must not
    stand over one it can — a refusal left in place over a fixed feature is a lie in the
    other direction.
    """

    def counting_conf(self, **over):
        tmp = Path(tempfile.mkdtemp(prefix="xcheck-refusal-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        record = tmp / "runs.json"
        agent = tmp / "agent.sh"
        agent.write_text(f'#!/bin/sh\necho ran >> {record}\n', encoding="utf-8")
        agent.chmod(0o755)
        return record, complete_conf(auditor_cmd=f"{agent} --model {{model}} {{prompt}}",
                                     parallel_passes="on", **over)

    def test_an_unfillable_configuration_starts_zero_children_on_BOTH_paths(self):
        """Criterion 1, measured through the real dispatchers rather than through a
        helper. `model_routing=off` with `{model}` in the role command is the shape the
        audit reported — it launched sequentially and refused in parallel. Both refuse it
        now, and the count of children is read off disk rather than inferred.
        """
        record, conf = self.counting_conf(model_routing="off")
        fx = Fixture(doc=state_doc(findings=[finding_record("F-0001")],
                                   queue=[queue_pass("P-01")]))
        self.addCleanup(fx.cleanup)
        fx.git_init()
        refusals = {}
        for name in ("sequential", "parallel"):
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    if name == "sequential":
                        runner.run_session(fx.root, conf, "Auditor", "P-01")
                    else:
                        parallel.agent_pass(fx.root, conf, ["P-01"],
                                            announce=lambda *_a, **_k: None)("P-01")
                refusals[name] = None
            except SystemExit as e:
                refusals[name] = str(e).splitlines()[0]
        started = record.read_text().count("ran") if record.exists() else 0
        print(f"\n  BOTH REFUSE sequential: {refusals['sequential']}")
        print(f"  BOTH REFUSE parallel:   {refusals['parallel']}")
        print(f"  BOTH REFUSE children started: {started}")
        self.assertEqual(0, started, "a child was launched despite the refusal")
        self.assertIsNotNone(refusals["sequential"], "the sequential path accepted it")
        self.assertIsNotNone(refusals["parallel"], "the parallel path accepted it")
        self.assertEqual(refusals["sequential"], refusals["parallel"],
                         "the two dispatchers refused it for different reasons")

    def test_a_divergence_would_be_refused_before_any_child(self):
        """The mechanism, exercised on a divergence the shared preparation cannot
        produce — so it is injected. Without this the refusal is a branch nothing has
        ever taken, which is a refusal nobody can claim works.

        The double is installed on `xcheck.parallel`'s view of the name, because
        `dispatch_divergence` imports it inside the function and would otherwise bind to
        the real one at call time.
        """
        record, conf = self.counting_conf(model_routing="on", cheap_model="a",
                                          strong_model="b")
        real = runner.prepare_launch

        def leaves_a_placeholder(conf_, role, prompt, sid, profile, log_path, route):
            plan = real(conf_, role, prompt, sid, profile, log_path, route)
            return plan._replace(cmd=[*plan.cmd, "--reasoning", "{effort}"])

        runner.prepare_launch = leaves_a_placeholder
        self.addCleanup(setattr, runner, "prepare_launch", real)
        with self.assertRaises(SystemExit) as e:
            parallel.enabled(conf)
        text = str(e.exception)
        started = record.read_text().count("ran") if record.exists() else 0
        print(f"  INJECTED   {text.splitlines()[0]}")
        print(f"  INJECTED   names the placeholder: {'{effort}' in text} · children "
              f"started: {started}")
        self.assertEqual(0, started, "a child was launched before the refusal")
        self.assertIn("would not do the same thing", text)
        self.assertIn("{effort}", text)

    def test_the_refusal_is_narrowed_to_divergence_and_says_so(self):
        """Criterion 10. The refusal does not stand over configurations both dispatchers
        handle the same way — including ones they both REFUSE. A conf with no role
        command is refused identically by each; reporting that as a divergence would be
        the lie in the other direction."""
        answers = {
            "no role command at all": parallel.dispatch_divergence({}),
            "routing off, {model} in the command":
                parallel.dispatch_divergence(self.counting_conf(model_routing="off")[1]),
            "the audit's configuration":
                parallel.dispatch_divergence(self.counting_conf(
                    model_routing="on", cheap_model="a", strong_model="b")[1]),
        }
        for what, answer in answers.items():
            print(f"  NARROWED   {what}: {answer!r}")
        self.assertEqual([None, None, None], list(answers.values()),
                         "the divergence check reports a difference that is not one")

    def test_the_refusal_does_not_stand_over_the_audits_configuration(self):
        """The one the audit reported. It diverged; it does not any more; the refusal
        lifts by itself because it is computed by running the preparation."""
        _record, conf = self.counting_conf(model_routing="on", cheap_model="gpt-5-mini",
                                           strong_model="gpt-5")
        answer = parallel.dispatch_divergence(conf)
        print(f"  LIFTED     model_routing=on with {{model}} -> {answer!r}")
        self.assertIsNone(answer, "the refusal outlived the divergence it was for")
        self.assertTrue(parallel.enabled(conf))

    def test_the_refusal_is_derived_not_remembered(self):
        """Structurally: it calls the preparation. A refusal keyed on a version or a
        flag would answer from a memory of a fix."""
        src = (REPO / "xcheck" / "parallel.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == "dispatch_divergence")
        calls = {getattr(c.func, "id", getattr(c.func, "attr", ""))
                 for c in ast.walk(fn) if isinstance(c, ast.Call)}
        print(f"  DERIVED    dispatch_divergence runs {sorted(calls & set(SHARED))}")
        self.assertIn("prepare_launch", calls)
        self.assertIn("dispatch_route", calls)


class TheResultCheckRunsOnBothPaths(unittest.TestCase):
    """The fifth audit gave the sequential path a pre-merge model check. A guarantee that
    held on one of two dispatchers was never a guarantee about xcheck."""

    def test_a_pass_whose_model_disagrees_is_not_merged(self):
        fx = Fixture(doc=state_doc(findings=[finding_record("F-0001")],
                                   queue=[queue_pass("P-01")]))
        self.addCleanup(fx.cleanup)
        fx.git_init()
        # `sidecar=`, not `telemetry=`. The distinction is the fifth audit's finding:
        # a model an AGENT prints into its own log is `agent-reported` and can neither
        # attest a route nor refute one, so a fixture built on it would assert a refusal
        # that no dispatcher makes. The provider's usage object is the only channel that
        # can disagree with the plan.
        sc = Scenario(self, stdout="parallel",
                      sidecar={"model": "gpt-4o-mini-WRONG", "total_tokens": 10})
        conf = complete_conf(auditor_cmd=sc.cmd("--model", "{model}"),
                             sandbox_profile="worktree", session_timeout=60,
                             parallel_passes="on", model_routing="on",
                             cheap_model="gpt-5-mini", strong_model="gpt-5")
        said = []
        session = parallel.agent_pass(fx.root, conf, ["P-01"], announce=said.append)
        with contextlib.redirect_stdout(io.StringIO()):
            merged = session("P-01")
        refusals = [s for s in said if "REFUSED before the merge" in s]
        print("\n  MODEL      the provider reported 'gpt-4o-mini-WRONG'; the "
              "route planned a gpt-5 model")
        print(f"  RESULT     merged={merged is not None} · refusals={len(refusals)}")
        self.assertIsNone(merged, "a session with the wrong model was merged")
        self.assertEqual(1, len(refusals), said)


if __name__ == "__main__":
    unittest.main()
