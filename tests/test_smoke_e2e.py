"""Ten scenarios driven through the public entry point, with a real child process.

The fourth audit shipped model routing with a green unit test and a broken run: the
route reached `build_cmd` and `build_cmd` was called a second time without it. Nothing
in 1,463 tests could catch that, because nothing dispatched a child whose argv could be
read back. This module is that instrument. Every scenario asserts an ARTEFACT — the
argv the child received, the sidecar path it was handed, the file it wrote, the record
that landed — never only an exit code.

The subject is `runner.run_session` and the `xcheck` CLI, not a helper. A test that
calls `build_cmd` directly is the check that already existed and did not catch this.

EXPECTED-RED (`unittest.expectedFailure`, so this suite is honest AND `ci/run-checks.sh`
stays green — the defect stays visible instead of being asserted away):

  * `MissingTelemetry.test_an_agent_printed_line_does_not_become_a_provider_fact` —
    phase 3 turns it green. A line the agent printed into its own log is recorded as
    `provider_model`, so the subject of the audit attests its own model.

CLOSED here, kept as the regression gate:

  * `RoutingApplied.test_the_child_receives_the_routed_model` — phase 2. `run_session`
    built the command twice and the second build dropped the route, so the dispatch died
    on its own unfilled `{model}` and no child was ever created. Making it reachable also
    found the refusal path under it recording `protocol-violation` — a PROTOCOL answer —
    in the session OUTCOME field, which the state schema refuses.

A phase that removes a decorator is what proves the fix; an assertion that the defect is
gone, written by the same hand that wrote the fix, is not.

Nothing here needs docker, a network or a paid call: the profile is `worktree`, and the
provider is `tests/fixtures/fake-agent/agent`.
"""

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.harness import (REPO, Fixture, finding_record, state_doc,
                           xcheck_submodule)
from tests.test_runner_sandbox import conf

runner = xcheck_submodule("runner")
ledger = xcheck_submodule("ledger")
write = xcheck_submodule("write")
provider = xcheck_submodule("provider")
util = xcheck_submodule("util")

AGENT = REPO / "tests" / "fixtures" / "fake-agent" / "agent"


class Scenario:
    """One dispatch's two files: what the agent was told to do, and what it did.

    Deep on purpose — every scenario below configures a child with one call and reads
    it back with one attribute, so the test bodies are about the claim and not about
    plumbing. Both files live in a system temp dir; the project tree is never written.
    """

    def __init__(self, case, **behaviour):
        self.tmp = Path(tempfile.mkdtemp(prefix="xcheck-smoke-"))
        case.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.record = self.tmp / "record.json"
        self.spec = self.tmp / "spec.json"
        self.spec.write_text(json.dumps(dict({"record": str(self.record)}, **behaviour)),
                             encoding="utf-8")

    def cmd(self, *extra):
        """The `<role>_cmd` template. `{prompt}` last, as every real one is."""
        return " ".join([str(AGENT), "--spec", str(self.spec), *extra,
                         "--usage", "{sidecar}", "{prompt}"])

    @property
    def runs(self):
        """Every dispatch the child recorded, oldest first. `[]` means it never ran."""
        if not self.record.exists():
            return []
        return json.loads(self.record.read_text(encoding="utf-8"))

    @property
    def argv(self):
        """The argv of the one dispatch — the fact the audit found nothing checking."""
        runs = self.runs
        assert len(runs) == 1, f"expected exactly one dispatch, got {len(runs)}"
        return runs[0]["argv"]


class SmokeCase(unittest.TestCase):
    """A fixture project, a git repo, and a dispatch helper that returns the output."""

    def setUp(self):
        self.fx = Fixture(doc=state_doc(findings=[finding_record("F-0001"),
                                                 finding_record("F-0002")]))
        self.addCleanup(self.fx.cleanup)
        self.head = self.fx.git_init()

    def conf(self, cmd, **over):
        return conf(auditor_cmd=cmd, sandbox_profile="worktree", session_timeout=60,
                    **over)

    def dispatch(self, cmd, charter="smoke charter", role="Auditor", dry_run=False,
                 **over):
        """One real session through the public entry point. Returns (result, output).

        A refusal is an outcome too, so `SystemExit` and `RouteError` come back as
        `(None, text)` rather than failing the test from inside the helper — the
        scenario decides whether the refusal was the right one.
        """
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                result = runner.run_session(self.fx.root, self.conf(cmd, **over), role,
                                            charter, dry_run=dry_run)
            return result, buf.getvalue()
        except BaseException as e:                            # noqa: BLE001 — reported
            return None, buf.getvalue() + f"\nREFUSED: {type(e).__name__}: {e}"

    ROUTED = dict(model_routing="on", cheap_model="gpt-5-mini", strong_model="gpt-5")

    def routed(self, sc=None, **over):
        sc = sc or Scenario(self, stdout="routed")
        result, out = self.dispatch(sc.cmd("--model", "{model}"),
                                    **dict(self.ROUTED, **over))
        return sc, result, out

    def expected_model(self):
        """The model this project's state routes to — DERIVED, not written down. A
        hard-coded name would pass on a fixture whose route silently changed."""
        routing = xcheck_submodule("routing")
        route = runner._route_for_dispatch(self.fx.root, self.conf("x {prompt}",
                                                                   **self.ROUTED),
                                           "Auditor", "smoke charter")
        return routing.model_for(self.conf("x {prompt}", **self.ROUTED), route), route

    def session_record(self):
        """The finished session as canonical state holds it."""
        doc = json.loads((self.fx.audit / "state.json").read_text(encoding="utf-8"))
        self.assertTrue(doc.get("sessions"), "no session was recorded at all")
        return doc["sessions"][-1]


# ------------------------------------------------------------------ 1. normal session

class NormalSession(SmokeCase):

    def test_the_child_ran_and_every_artefact_it_touched_is_accounted_for(self):
        sc = Scenario(self, stdout="did the work",
                      sidecar={"model": "gpt-5-mini", "total_tokens": 4321},
                      patch={"path": "audit/notes.md", "text": "from the session\n"})
        result, out = self.dispatch(sc.cmd())
        print(f"\n  SCENARIO normal session — outcome={result and result.outcome}")
        print(f"  argv[0]     {sc.argv[0]}")
        print(f"  sidecar     {sc.runs[0]['sidecar_arg']}")
        print(f"  cwd         {sc.runs[0]['cwd']}")

        self.assertEqual(1, len(sc.runs), "the child did not run exactly once")
        self.assertEqual(str(AGENT), sc.argv[0], "argv[0] is not the configured binary")
        self.assertEqual(0, int(result), out[-2000:])   # RunResult IS its exit code
        # The prompt is the LAST argument, and it is the role prompt — not the template.
        self.assertIn("Role: Auditor", sc.argv[-1])
        # It was handed a sidecar path, and that path is outside the project the child
        # can see: the whole point of the `{sidecar}` placeholder.
        handed = Path(sc.runs[0]["sidecar_arg"])
        self.assertTrue(str(handed).startswith(tempfile.gettempdir()))
        self.assertNotIn(str(self.fx.root), str(handed))
        # It ran in a disposable worktree, not in the project.
        self.assertNotEqual(str(self.fx.root), sc.runs[0]["cwd"])
        # And the sidecar it wrote was read: provider figures, not a log parse.
        rec = self.session_record()
        print(f"  recorded    telemetry_source={rec.get('telemetry_source')!r} "
              f"tokens={rec.get('tokens')} provider_model={rec.get('provider_model')!r}")
        self.assertEqual(4321, rec.get("tokens"))
        self.assertEqual("provider", rec.get("telemetry_source"))


# ----------------------------------------------------------------- 2. routing applied

class RoutingApplied(SmokeCase):
    """EXPECTED-RED until phase 2. `run_session` resolves a route, builds the command
    with it for the door check, then builds it AGAIN for the dispatch with no route —
    so the second build finds `{model}` unfilled and refuses. The audit measured it as
    `dry-run: успешно / обычный запуск: отказ / число запусков child process: 0`."""

    def test_the_child_receives_the_routed_model(self):
        model, route = self.expected_model()
        sc, result, out = self.routed()
        print(f"\n  SCENARIO routing applied — dispatches={len(sc.runs)} "
              f"route={route!r} model={model!r}")
        print(f"  argv       {sc.argv[:6]}")
        self.assertEqual(1, len(sc.runs),
                         "the route never reached a child process: run_session built "
                         "the command a second time without it")
        self.assertEqual(model, sc.argv[sc.argv.index("--model") + 1],
                         "the child was dispatched with the wrong model in its argv")
        self.assertNotIn("{model}", " ".join(sc.argv))

    def test_the_dry_run_previews_the_command_the_real_run_dispatches(self):
        """Criterion 4, compared rather than read. The two builds disagreeing IS the
        defect this phase closed, so the check is that one printed line and one recorded
        argv describe the same command — not that the code looks like it does."""
        sc = Scenario(self, stdout="routed")
        _s, _r, preview = self.routed(sc, dry_run=True)
        self.assertEqual([], sc.runs, "the dry run dispatched a child")
        shown = [l for l in preview.splitlines() if l.startswith("command: ")][0]
        _s2, _r2, _o = self.routed(sc)                      # the SAME configuration
        real = "command: " + " ".join("[+prompt]" if "Role: Auditor" in a else a
                                      for a in sc.argv)
        # One thing legitimately differs: the sidecar carries the session id, and two
        # sessions are two sessions. Normalise THAT and nothing else.
        norm = lambda s: re.sub(r"\S+/\.telemetry/\S+\.json", "<sidecar>", s)  # noqa: E731
        print(f"\n  DRY-RUN  {norm(shown)}")
        print(f"  REAL     {norm(real)}")
        self.assertEqual(norm(shown), norm(real))

    def test_CONTROL_with_routing_off_the_argv_carries_no_model(self):
        """The fix must not have made routing mandatory. Same command minus the
        placeholder, routing off: the child runs and no model reaches it."""
        sc = Scenario(self, stdout="unrouted")
        result, out = self.dispatch(sc.cmd(), model_routing="off")
        print(f"  CONTROL  routing off: argv={sc.argv[1:4]} dispatches={len(sc.runs)}")
        self.assertEqual(1, len(sc.runs))
        self.assertNotIn("gpt-5", " ".join(sc.argv))
        self.assertNotIn("gpt-5-mini", " ".join(sc.argv))

    def test_the_sidecar_still_reaches_the_command_alongside_the_route(self):
        """One build now fills BOTH placeholders. A merge that dropped the other one
        would leave telemetry silently unmeasured — indistinguishable from a wrapper
        that reports nothing."""
        model, _route = self.expected_model()
        sc, _r, _o = self.routed(Scenario(self, stdout="routed",
                                          sidecar={"model": model, "total_tokens": 77}))
        rec = self.session_record()
        print(f"  BOTH     model={model in sc.argv} "
              f"sidecar_written={Path(sc.runs[0]['sidecar_arg']).is_file()} "
              f"source={rec.get('telemetry_source')!r} tokens={rec.get('tokens')}")
        self.assertIn(model, sc.argv)
        self.assertEqual("provider", rec.get("telemetry_source"))
        self.assertEqual(77, rec.get("tokens"))


# --------------------------------------------------------------------- 3. wrong model

class WrongModel(SmokeCase):
    """A session whose provider reports a model other than the one xcheck routed to must
    not be recorded as a priced fact. Phase 1 could not reach this check at all — the
    routed dispatch died before a child existed — and reaching it found a second defect
    under it: the refusal recorded `protocol-violation`, a PROTOCOL answer, in the
    session OUTCOME field, so the whole record was lost to a schema refusal."""

    def test_a_provider_that_reports_another_model_refuses_the_route(self):
        model, route = self.expected_model()
        sc = Scenario(self, stdout="ran the wrong one",
                      sidecar={"model": "gpt-4o-mini-WRONG", "total_tokens": 10})
        result, out = self.dispatch(sc.cmd("--model", "{model}"),
                                    **self.ROUTED)
        said = [ln for ln in out.splitlines() if ln.startswith("route: REFUSED")]
        rec = self.session_record()
        print(f"\n  SCENARIO wrong model — routed to {model!r} on {route!r}, "
              f"provider said 'gpt-4o-mini-WRONG'")
        print(f"  {said[0][:180] if said else '(no refusal printed)'}")
        print(f"  recorded outcome={rec.get('outcome')!r} "
              f"route_attestation={rec.get('route_attestation')!r}")
        self.assertEqual(model, sc.argv[sc.argv.index("--model") + 1])
        self.assertTrue(said, "the mismatch was not refused")
        self.assertIn("gpt-4o-mini-WRONG", said[0])
        self.assertIn(model, said[0])
        # The record survives, and it does NOT claim the route.
        self.assertEqual("refused", rec.get("outcome"))
        self.assertIn(rec.get("outcome"), util.OUTCOMES,
                      "the refusal path writes a value the state schema does not accept")
        self.assertIsNone(rec.get("route_attestation"),
                          "a refused route was still recorded as attested")

    def test_the_patch_never_reaches_the_project_when_the_route_is_refused(self):
        """Criterion 6, by ordering rather than by reading the code. The attestation
        check used to live inside `envelope.finish`, which runs after `sandbox.collect()`
        — so the patch of a session about to be refused was already in the tree."""
        model, _route = self.expected_model()
        landed = self.fx.audit / "findings" / "F-9101.md"
        sc = Scenario(self, stdout="wrote a finding, on the wrong model",
                      sidecar={"model": "gpt-4o-mini-WRONG", "total_tokens": 10},
                      patch={"path": "audit/findings/F-9101.md", "text": "evidence\n"})
        result, out = self.dispatch(sc.cmd("--model", "{model}"), **self.ROUTED)
        rec = self.session_record()
        quarantine = [ln for ln in out.splitlines() if ln.startswith("quarantine:")]
        print(f"\n  ORDER    the child wrote audit/findings/F-9101.md in its worktree; "
              f"in the project: {landed.exists()}")
        print(f"  {quarantine[0][:150] if quarantine else '(no quarantine line)'}")
        bundle = [ln for ln in out.splitlines() if ln.strip().startswith("bundle:")]
        print(f"  {bundle[0].strip()[:120] if bundle else '(no bundle line)'}")
        print(f"  recorded outcome={rec.get('outcome')!r} "
              f"quarantine_path={result.quarantine_path!r}")
        self.assertTrue(out.index("route: REFUSED") < out.index("quarantine:"),
                        "the refusal came after the patch decision")
        self.assertFalse(landed.exists(),
                         "a session whose route was refused applied its patch")
        self.assertTrue(quarantine, "the work was neither applied nor quarantined")
        self.assertTrue(bundle, "the quarantine bundle is not named in the transcript")
        self.assertIsNotNone(result.quarantine_path,
                             "the record does not say where the work went")
        self.assertTrue((self.fx.root / result.quarantine_path / "manifest.json").is_file())
        self.assertEqual("refused", rec.get("outcome"))

        # POSITIVE CONTROL: the same patch, an attested model. It DOES land — without
        # this arm the assertion above passes on a detector that sees nothing.
        ok = Scenario(self, stdout="wrote a finding on the right model",
                      sidecar={"model": model, "total_tokens": 10},
                      patch={"path": "audit/findings/F-9102.md", "text": "evidence\n"})
        self.dispatch(ok.cmd("--model", "{model}"), **self.ROUTED)
        control = self.fx.audit / "findings" / "F-9102.md"
        print(f"  CONTROL  attested session's patch in the project: {control.exists()}")
        self.assertTrue(control.exists(),
                        "the control did not land either, so the assertion above is "
                        "measuring a collector that never applies anything")

    def test_CONTROL_the_matching_model_is_attested_and_not_refused(self):
        """Without this arm the refusal above could be firing on every routed session."""
        model, _route = self.expected_model()
        sc = Scenario(self, stdout="ran the right one",
                      sidecar={"model": model, "total_tokens": 10})
        result, out = self.dispatch(sc.cmd("--model", "{model}"),
                                    **self.ROUTED)
        rec = self.session_record()
        print(f"  CONTROL  provider said {model!r}: outcome={rec.get('outcome')!r} "
              f"attestation={rec.get('route_attestation')!r}")
        self.assertNotIn("route: REFUSED", out)
        self.assertEqual("ok", rec.get("outcome"))
        self.assertEqual("attested", rec.get("route_attestation"))


# ---------------------------------------------------------------- 4. missing telemetry

class MissingTelemetry(SmokeCase):

    def test_a_session_that_reports_nothing_is_unmeasured_and_says_so(self):
        sc = Scenario(self, stdout="quiet work")            # no sidecar, no telemetry
        result, out = self.dispatch(sc.cmd())
        rec = self.session_record()
        print(f"\n  SCENARIO missing telemetry — source={rec.get('telemetry_source')!r} "
              f"tokens={rec.get('tokens')!r}")
        self.assertEqual(1, len(sc.runs))
        self.assertIsNotNone(sc.runs[0]["sidecar_arg"], "no sidecar path was handed")
        self.assertFalse(Path(sc.runs[0]["sidecar_arg"]).exists(),
                         "the fixture wrote a sidecar it was told not to write")
        self.assertNotEqual("provider", rec.get("telemetry_source"))
        self.assertIsNone(rec.get("tokens"), "an absent measurement became a number")

    def test_an_agent_printed_line_cannot_attest_its_own_route(self):
        """The audit's fourth finding, on the real path. The agent prints
        `XCHECK_TELEMETRY {"model": "<the model xcheck routed to>"}` into its own log — a
        channel it fully controls — and before phase 3 that produced
        `route_attestation=attested`. The strings matched; the channel was the subject.

        `provider_model` is still recorded: what the agent said is worth keeping. What it
        may not do is CONFIRM anything, and `telemetry_source` is what decides that.
        """
        model, route = self.expected_model()
        sc = Scenario(self, telemetry={"model": model, "total_tokens": 1})
        result, out = self.dispatch(sc.cmd("--model", "{model}"), **self.ROUTED)
        rec = self.session_record()
        print(f"\n  SCENARIO agent self-attestation — routed to {model!r} on {route!r}, "
              f"the AGENT printed the same name")
        print(f"  source={rec.get('telemetry_source')!r} "
              f"provider_model={rec.get('provider_model')!r} "
              f"route_attestation={rec.get('route_attestation')!r}")
        self.assertEqual("agent-reported", rec.get("telemetry_source"))
        self.assertEqual(model, rec.get("provider_model"))
        self.assertEqual("unattested", rec.get("route_attestation"),
                         "the subject of the audit attested its own route")
        # And it is not refused either: a disagreement reported by the subject is not
        # grounds to refuse, because the accuser is the channel the rule distrusts.
        self.assertNotIn("route: REFUSED", out)
        # The label names the SOURCE as the reason, not merely "unattested".
        said = [ln for ln in out.splitlines() if ln.startswith("route: UNATTESTED")]
        print(f"  {said[0] if said else '(the label did not say why)'}")
        self.assertTrue(said)
        self.assertIn("agent-reported", said[0])

    def test_a_sidecar_the_validator_refuses_is_refused_in_production_too(self):
        """The bypassed validator, closed. `provider.read()` rejects a usage record that
        names no actual model; production called `adapter.parse` and recorded that exact
        record as `telemetry_source=provider`. One object, both paths, one answer."""
        obj = {"total_tokens": 500}                # complete but for the model
        judged, refused = provider.read(provider.ADAPTERS["codex"], dict(obj))
        sc = Scenario(self, sidecar=dict(obj))
        self.dispatch(sc.cmd())
        rec = self.session_record()
        print(f"\n  VALIDATOR  provider.read({obj}) -> refused: {refused[0][:70]}…")
        print(f"  PRODUCTION source={rec.get('telemetry_source')!r} "
              f"tokens={rec.get('tokens')!r}")
        print(f"  REFUSAL ON RECORD  {str(rec.get('telemetry_refused'))[:90]}…")
        self.assertIsNone(judged)
        self.assertTrue(refused)
        self.assertNotEqual("provider", rec.get("telemetry_source"),
                            "production accepted a record the validator refuses")
        self.assertIn("names no actual model", str(rec.get("telemetry_refused")),
                      "the refusal was silent — the operator never learns it happened")


# -------------------------------------------------------------------- 5. cap exceeded

class CapExceeded(SmokeCase):
    """`{token_cap}` reaches the child's argv. What the child then DOES with it is the
    operator's wrapper's business — and `cap_problem` accepting `true {token_cap}` is
    phase 7's subject. This scenario pins the half xcheck actually controls."""

    def test_the_ceiling_reaches_the_child_as_a_number(self):
        sc = Scenario(self, stdout="spent it all",
                      sidecar={"model": "gpt-5-mini", "total_tokens": 999999})
        result, out = self.dispatch(sc.cmd("--max-tokens", "{token_cap}"),
                                    budgets="on", tokens_per_session=1500)
        print(f"\n  SCENARIO cap exceeded — argv cap={sc.argv[sc.argv.index('--max-tokens') + 1]!r}"
              f"  reported={self.session_record().get('tokens')}")
        self.assertEqual(1, len(sc.runs))
        self.assertIn("1500", sc.argv, "the ceiling never reached the child")
        self.assertNotIn("{token_cap}", " ".join(sc.argv))
        # The overrun IS recorded — which is what makes phase 7's stop possible.
        self.assertEqual(999999, self.session_record().get("tokens"))


# --------------------------------------------------------------- 6. state-write failure

class StateWriteFailure(SmokeCase):
    """The audit's blocking finding, driven through the ordinary CLI. The event is
    reserved in the outbox, the state write fails, and the transition did not happen —
    but nothing distinguishes that from a transition that did."""

    def fail_the_state_write(self):
        saved = write.write_state

        def boom(*a, **kw):
            raise OSError("injected: the state write failed")

        write.write_state = boom
        self.addCleanup(setattr, write, "write_state", saved)

    def reserve_then_fail(self):
        """Drive the ordinary verb with the state write broken. The OSError escapes the
        CLI — that is the product's behaviour, and it is what leaves the outbox entry
        behind with no state write to match it."""
        before = (self.fx.audit / "state.json").read_bytes()
        self.fail_the_state_write()
        with self.assertRaises(OSError) as e:
            self.fx.run("set-status", "F-0001", "accepted")
        self.assertIn("injected", str(e.exception))
        return before

    def test_the_outbox_holds_an_event_the_state_never_took(self):
        before = self.reserve_then_fail()
        waiting = ledger.pending(self.fx.root)
        print(f"\n  SCENARIO state-write failure — "
              f"outbox={len(waiting)} state unchanged="
              f"{before == (self.fx.audit / 'state.json').read_bytes()}")
        self.assertEqual(before, (self.fx.audit / "state.json").read_bytes())
        self.assertEqual(1, len(waiting), "the reserved event is not in the outbox")
        self.assertEqual("accepted", waiting[0]["line"]["to_status"])


# ------------------------------------------------------------------ 7. repeated recovery

class RepeatedRecovery(StateWriteFailure):

    def test_recover_apply_twice_appends_the_same_event_once(self):
        self.test_the_outbox_holds_an_event_the_state_never_took()
        rows = lambda: len(ledger._read_lines(self.fx.root))         # noqa: E731
        first_rows = rows()
        c1, o1 = self.fx.run("recover", "--apply")
        after_one = rows()
        c2, o2 = self.fx.run("recover", "--apply")
        print(f"\n  SCENARIO repeated recovery — events {first_rows} -> {after_one} "
              f"-> {rows()}  exits={c1},{c2}")
        self.assertEqual(after_one, rows(), "the second --apply appended a duplicate")
        # PHASE 4 closed this. Before it, `recover --apply` appended a `state_transition`
        # for a write that raised: the ledger said `F-0001 reported→accepted, revision 2`
        # over a record that said `reported` at revision 1.
        doc = json.loads((self.fx.audit / "state.json").read_text(encoding="utf-8"))
        status = [f["status"] for f in doc["findings"] if f["id"] == "F-0001"][0]
        claims = [ln for ln in ledger._read_lines(self.fx.root)
                  if ln.get("target") == "F-0001" and ln.get("to_status") == "accepted"]
        print(f"  record says {status!r}; the ledger appended {after_one - first_rows} "
              f"event(s) and claims 'accepted' {len(claims)} time(s)")
        print(f"  {[ln for ln in o1.splitlines() if 'discard' in ln][0].strip()[:150]}")
        self.assertEqual("reported", status, "the state moved on a failed write")
        moved = [ln for ln in ledger._read_lines(self.fx.root)
                 if ln.get("event") == "state_transition"]
        leases = after_one - first_rows
        print(f"  the stream grew by {leases} event(s), all of them the lease pair "
              f"recovery takes the writer's lock with (phase 6); "
              f"state_transitions: {len(moved)}")
        self.assertEqual([], [m for m in moved if m.get("target") == "F-0001"],
                         "recovery appended an event for a transition that never landed")
        self.assertEqual([], claims, "the ledger claims a transition the state refuses")
        self.assertIn("discarded", o1)
        self.assertEqual([], ledger.pending(self.fx.root),
                         "the row was left in doubt for the next run to replay")


# ---------------------------------------------------------------------- 8. foreign lock

class ForeignLock(SmokeCase):
    """`recover --apply` writes the ledger. An ordinary transition takes `audit/.lock`
    first; this path does not, so a second process appends while another holds it.

    Phase 6's subject. The row it replays here is a real one — a transition that DID
    commit and whose append failed — so what the arm measures is the missing lock and not
    the phase-4 discard.
    """

    def strand_a_committed_event(self):
        """One real transition whose state write lands and whose append does not."""
        saved = ledger.append_chained
        ledger.append_chained = lambda *a, **kw: (_ for _ in ()).throw(
            OSError("injected: the ledger could not be appended"))
        self.addCleanup(setattr, ledger, "append_chained", saved)
        code, out = self.fx.run("set-status", "F-0001", "accepted")
        ledger.append_chained = saved
        self.assertEqual(0, code, out)
        self.assertTrue(ledger.pending(self.fx.root), "nothing was stranded")

    def hold_the_lock(self):
        """A lock owned by a LIVE process that is not this one, so the guard cannot
        dismiss it as stale. `sleep` is that process; it is killed on cleanup."""
        holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        d = self.fx.audit / ".lock"
        d.mkdir()
        (d / "owner").write_text(json.dumps(
            {"pid": holder.pid, "role": "Auditor", "started": "2026-09-05T00:00:00+00:00",
             "host": os.uname().nodename, "nonce": "0" * 16}), encoding="utf-8")
        return holder

    def test_recovery_takes_the_same_lock_a_write_verb_takes(self):
        """PHASE 6 inverted this. It shipped asserting that `recover --apply` appended
        while another process held the lock — the audit's reproduction — with the
        sentence "phase 6 landed; make this the refusal assertion" beside it."""
        self.strand_a_committed_event()
        before = len(ledger._read_lines(self.fx.root))
        holder = self.hold_the_lock()
        # CONTROL: the ordinary write path DOES see the foreign lock.
        code, out = self.fx.run("set-status", "F-0002", "accepted")
        print(f"\n  SCENARIO foreign lock — pid {holder.pid} holds audit/.lock")
        print(f"  CONTROL  set-status exit={code}: {out.strip().splitlines()[-1][:120]}")
        self.assertNotEqual(0, code, "a write verb ignored a live foreign lock")
        rc, rout = self.fx.run("recover", "--apply")
        after = len(ledger._read_lines(self.fx.root))
        print(f"  recover --apply exit={rc}: events {before} -> {after} "
              f"while the lock was held")
        print(f"    {rout.strip().splitlines()[-1][:120]}")
        self.assertNotEqual(0, rc, "recovery appended under a live foreign lock")
        self.assertEqual(before, after, "recovery wrote while another writer held")
        self.assertIn(str(holder.pid), rout, "the refusal does not name the holder")
        self.assertTrue(ledger.pending(self.fx.root),
                        "the committed event was dropped instead of being held")

    def test_CONTROL_with_no_lock_held_recovery_still_replays(self):
        """Without this arm, a `recover` that refused everything would pass above."""
        self.strand_a_committed_event()
        before = len(ledger._read_lines(self.fx.root))
        rc, rout = self.fx.run("recover", "--apply")
        after = len(ledger._read_lines(self.fx.root))
        print(f"  CONTROL  no lock held: exit={rc}, events {before} -> {after}")
        self.assertEqual(0, rc, rout)
        self.assertGreater(after, before, "the lock deleted the verb")
        self.assertEqual([], ledger.pending(self.fx.root))

    def test_a_read_only_recover_needs_no_lock_and_says_so(self):
        self.strand_a_committed_event()
        holder = self.hold_the_lock()
        rc, rout = self.fx.run("recover")
        said = [ln for ln in rout.splitlines() if "read-only" in ln]
        print(f"  READ-ONLY  under pid {holder.pid}'s lock: exit={rc}")
        print(f"    {said[0].strip() if said else '(it does not say)'}")
        self.assertTrue(said, "a read-only recover does not say it took no lock")
        self.assertNotIn("audit/.lock is held", rout)


# --------------------------------------------------------------------- 9. corrupt ledger

class CorruptLedger(SmokeCase):

    def test_a_broken_ledger_blocks_the_ordinary_write_path(self):
        self.fx.run("set-status", "F-0001", "accepted")
        events = self.fx.audit / "events.jsonl"
        rows = events.read_text(encoding="utf-8").splitlines()
        # NOT the last row. The chain proves each line against its SUCCESSOR's `prev`,
        # so editing the tail breaks nothing that is written down yet — the recorded
        # `ledger_anchor` is what covers that case, and it is phase 4's subject. Edit a
        # line that has a successor, which is the tamper the chain does answer.
        target = next(i for i, r in enumerate(rows[:-1]) if "\"v\"" in r)
        rows[target] = json.dumps(dict(json.loads(rows[target]), summary="tampered"))
        events.write_text("\n".join(rows) + "\n", encoding="utf-8")
        state, detail = ledger.status(self.fx.root, None)
        code, out = self.fx.run("set-status", "F-0002", "accepted")
        doc = json.loads((self.fx.audit / "state.json").read_text(encoding="utf-8"))
        print(f"\n  SCENARIO corrupt ledger — ledger.status={state!r}")
        print(f"  set-status on a broken ledger: exit={code}")
        print(f"  {out.strip().splitlines()[0][:110]}")
        self.assertNotEqual(ledger.INTACT, state, "the tamper was not even detected")
        # PHASE 5 inverted this. It shipped asserting `exit=0` — the reproduction — with
        # the sentence "the write path now refuses on a broken ledger; phase 5 landed,
        # invert this assertion" beside it. It landed.
        self.assertEqual(1, code, "a write ran over a stream the ledger calls broken")
        self.assertIn("refusing the transition", out)
        self.assertEqual("reported",
                         [f["status"] for f in doc["findings"] if f["id"] == "F-0002"][0],
                         "the refused transition changed state anyway")

    def test_a_directory_where_the_events_file_belongs_is_an_access_error(self):
        """PHASE 6 inverted this. It shipped asserting `intact` — the reproduction —
        because `_numbered_rows` swallowed OSError and an unreadable ledger reported as
        an empty one, with "an access error is now an access error; phase 6 landed"
        beside it."""
        events = self.fx.audit / "events.jsonl"
        if events.exists():
            events.unlink()
        events.mkdir()
        state, detail = ledger.status(self.fx.root, None)
        print(f"  a DIRECTORY at audit/events.jsonl -> {state!r} — "
              f"{detail[len(str(events)):][:70].strip()}")
        self.assertEqual(ledger.UNREADABLE, state)
        self.assertIn("DIRECTORY", detail)


# ------------------------------------------------- 10. failure before the patch is applied

class FailureBeforeThePatchIsApplied(SmokeCase):

    def test_a_crashed_session_leaves_its_edit_in_the_worktree_and_not_the_project(self):
        sc = Scenario(self, stdout="wrote it, then died", exit=1,
                      patch={"path": "audit/findings/F-9999.md", "text": "half a fix\n"})
        result, out = self.dispatch(sc.cmd())
        landed = self.fx.audit / "findings" / "F-9999.md"
        print(f"\n  SCENARIO failure before the patch is applied — "
              f"outcome={result and result.outcome} child_cwd_write=yes "
              f"landed_in_project={landed.exists()}")
        self.assertEqual(1, len(sc.runs))
        self.assertNotEqual(str(self.fx.root), sc.runs[0]["cwd"])
        self.assertFalse(landed.exists(),
                         "a failed session's edit reached the project tree")
        self.assertEqual("crash", result.outcome)


# ------------------------------------------------------------------- the tree is untouched

class TheRepositoryIsNeverWritten(SmokeCase):
    """Every artefact above lives in a system temp dir. Asserted with a positive
    control, because a snapshot comparison that cannot see a change is green forever
    (`spy-needs-a-positive-control`)."""

    def snapshot(self):
        return subprocess.run(["git", "status", "--porcelain"], cwd=str(REPO),
                              capture_output=True, text=True, timeout=60).stdout

    def test_a_full_dispatch_changes_nothing_in_this_repository(self):
        before = self.snapshot()
        sc = Scenario(self, stdout="work", patch={"path": "audit/x.md", "text": "x\n"})
        self.dispatch(sc.cmd())
        after = self.snapshot()
        print(f"\n  TREE  {len(before.splitlines())} dirty paths before, "
              f"{len(after.splitlines())} after")
        self.assertEqual(before, after, "the smoke suite wrote into the repository")

        # POSITIVE CONTROL: the same snapshot DOES see a write.
        planted = REPO / "tests" / "fixtures" / ".smoke-control"
        planted.write_text("planted\n", encoding="utf-8")
        try:
            self.assertNotEqual(before, self.snapshot(),
                                "the snapshot cannot see a change, so its silence "
                                "above proves nothing")
        finally:
            planted.unlink()
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
