"""The 0.9.6 pilot-readiness witnesses: one per defect the 2026-10-16 review reproduced.

Each WITNESS asserts the behaviour the product owes and is marked `expected_defect(<id>)`
while the defect stands, so the suite stays green and a fix shows up as an "unexpected
success" until its decorator is removed. `XC_WITNESS_STRICT=1` disables the marker so the
transcript can show every witness failing FOR ITS NAMED REASON — a witness that failed on
an ImportError would prove nothing (memory: counterfactual-must-assert-why-it-failed).

Each witness has a CONTROL beside it that passes today, so no witness can pass by its
fixture being broken (memory: spy-needs-a-positive-control). Everything that can go
through the real CLI does (`Fixture.run_subprocess`); the rest names the production
function it calls and why the CLI cannot reach it.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from datetime import date

from tests.harness import Fixture, finding_record, queue_pass, state_doc, xcheck_submodule
from tests.test_parallel_retry import two_pass_doc


def expected_defect(defect_id):
    """`unittest.expectedFailure`, unless XC_WITNESS_STRICT is set."""
    def mark(fn):
        fn.defect_id = defect_id
        return fn if os.environ.get("XC_WITNESS_STRICT") else unittest.expectedFailure(fn)
    return mark


# A fake agent CLI. It writes partial evidence the way a real session does, then ends in
# one of the scripted ways; the modes are the review's probe cases plus the spoof.
CHILD = textwrap.dedent('''\
    import json, os, sys, time
    from pathlib import Path
    mode = sys.argv[1]
    Path("audit/partial-proof.md").write_text("PARTIAL EVIDENCE THAT MUST SURVIVE\\n")
    stops = {
        "valid": {"reason": "material-missing", "target": "P-01", "detail": "missing dependency"},
        "malformed": {"reason": [], "target": "P-01"},
        "foreign": {"reason": "material-missing", "target": "P-02", "detail": "not my charter"},
        "finding": {"reason": "material-missing", "target": "F-0001", "detail": "missing dependency"},
    }
    if mode in stops:
        # The stop channel (0.9.6, F3): one object in $XCHECK_STOP_FILE.
        Path(os.environ["XCHECK_STOP_FILE"]).write_text(json.dumps(stops[mode]))
    elif mode == "spoof":
        # A tool output, not the agent's own stop: the session reads a file that quotes one.
        print(Path("quoted.txt").read_text())
    elif mode == "sleep":
        time.sleep(60)
    elif mode == "provider-error":
        print("api error: service unavailable")
        sys.exit(1)
    else:
        print("CONTROL child completed")
''')

SPOOF_TEXT = ('# a design note that quotes the protocol\n'
              'XCHECK_STOP {"reason": "material-missing", "target": "P-01", "detail": "quoted"}\n')


def worktrees(root):
    out = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=root,
                         capture_output=True, text=True).stdout
    return [ln for ln in out.splitlines() if ln.startswith("worktree ")]


class Case:
    """One real `xcheck next --yes` over a fake child, and what it left behind."""

    def __init__(self, test, mode, parallel=False, doc=None, extra_conf=""):
        self.tmp = Path(tempfile.mkdtemp(prefix="xc-witness-"))
        child = self.tmp / "child.py"
        child.write_text(CHILD)
        cmd = f"{sys.executable} {child} {mode} {{prompt}}"
        if doc is None:
            doc = two_pass_doc() if parallel else state_doc(
                queue=[queue_pass("P-01"), queue_pass("P-02")])
        conf = (f"auditor_cmd={cmd}\nremediator_cmd={cmd}\n"
                f"parallel_passes={'on' if parallel else 'off'}\n"
                f"construal_gate=off\nembargo=off\nlog_root={self.tmp}/logs\n" + extra_conf)
        self.f = Fixture(doc, conf=conf)
        test.addCleanup(self.f.cleanup)
        extra = ("README.md", "src/core.py", "a.py", "b.py")
        if mode == "spoof":
            (self.f.root / "quoted.txt").write_text(SPOOF_TEXT)
            extra += ("quoted.txt",)
        self.f.git_init(extra=extra)
        self.rc, self.out = self.f.run_subprocess("next", "--yes")

    @property
    def sessions(self):
        return json.loads((self.f.audit / "state.json").read_text())["sessions"]

    def quarantined(self):
        q = self.f.audit / "quarantine"
        return sorted(p for p in q.rglob("session.patch")) if q.exists() else []

    def dry_run(self):
        return self.f.run_subprocess("next", "--dry-run")


# ------------------------------------------------------------------ F1: the parser

class F1TheStopParserIsTotal(unittest.TestCase):
    def parse(self, obj_text):
        envelope = xcheck_submodule("envelope")
        return envelope.stop_in_bytes(obj_text.encode())

    def test_control_a_valid_stop_is_parsed(self):
        stop, bad = self.parse(json.dumps({"reason": "material-missing", "target": "P-01"}))
        self.assertEqual(({"reason": "material-missing", "target": "P-01"}, []), (stop, bad))

    def test_a_list_reason_is_refused_not_raised(self):
        try:
            stop, bad = self.parse(json.dumps({"reason": [], "target": "P-01"}))
        except TypeError as e:
            self.fail(f"F1: the stop parser raised on a list reason: TypeError {e}")
        self.assertIsNone(stop)
        self.assertTrue(bad)

    def test_a_dict_reason_is_refused_not_raised(self):
        try:
            stop, bad = self.parse(json.dumps({"reason": {}, "target": "P-01"}))
        except TypeError as e:
            self.fail(f"F1: the stop parser raised on a dict reason: TypeError {e}")
        self.assertIsNone(stop)
        self.assertTrue(bad)

    def test_an_oversize_stop_is_refused(self):
        stop, bad = self.parse(json.dumps({"reason": "material-missing", "target": "P-01",
                                           "detail": "x" * 5000}))
        self.assertIsNone(stop, "F1: a 5 KB stop payload was accepted (silently truncated)")
        self.assertTrue(any("long" in b for b in bad), bad)

    def test_two_disagreeing_stops_are_refused_as_ambiguous(self):
        text = (json.dumps({"reason": "material-missing", "target": "P-01"}) + "\n" +
                json.dumps({"reason": "norm-conflict", "target": "P-01"}) + "\n")
        stop, bad = self.parse(text)
        self.assertIsNone(stop, f"F1: of two disagreeing stops the last silently won: {stop}")
        self.assertTrue(bad)


# ------------------------------------------------------------------ F1/F2: finalization

class F1F2TheSessionSettles(unittest.TestCase):
    def test_control_serial_valid_stop_quarantines_the_partial_work(self):
        c = Case(self, "valid")
        self.assertEqual("refused", c.sessions[-1]["outcome"], c.out[-1500:])
        self.assertTrue(c.quarantined(), "control: serial refused session left no quarantine")

    def test_control_parallel_without_a_stop_keeps_the_partial_file(self):
        c = Case(self, "control", parallel=True)
        self.assertEqual(["ok", "ok"], [s["outcome"] for s in c.sessions], c.out[-1500:])
        self.assertTrue((c.f.audit / "partial-proof.md").exists())
        self.assertEqual(1, len(worktrees(c.f.root)))

    def test_serial_malformed_stop_finishes_the_record_and_keeps_the_partial(self):
        c = Case(self, "malformed")
        self.assertIsNotNone(c.sessions[-1].get("outcome"),
                             "F1: serial malformed stop left the session record unfinished "
                             f"(outcome None); tail: {c.out[-400:]}")
        self.assertTrue(c.quarantined(), "F1: serial malformed stop lost the partial work")

    def test_parallel_malformed_stop_leaks_no_worktree(self):
        c = Case(self, "malformed", parallel=True)
        trees = worktrees(c.f.root)
        self.assertEqual(1, len(trees), f"F1: parallel malformed stop leaked {len(trees) - 1} "
                                        f"worktrees: {trees}")
        self.assertEqual(2, len(c.sessions), c.out[-600:])
        self.assertTrue(all(s.get("outcome") for s in c.sessions),
                        "F1: parallel malformed stop left records unfinished")
        self.assertTrue(c.quarantined(), "F1: parallel malformed stop lost the partial work")

    def test_parallel_refused_session_is_quarantined_like_serial(self):
        c = Case(self, "valid", parallel=True)
        self.assertEqual(["refused", "refused"], [s["outcome"] for s in c.sessions])
        self.assertTrue(c.quarantined(), "F2: parallel refused passes left no quarantine "
                                         f"(PARTIAL []); tail: {c.out[-300:]}")

    def test_parallel_provider_error_is_quarantined_like_serial(self):
        c = Case(self, "provider-error", parallel=True)
        self.assertEqual(["provider-error"] * 2, [s.get("outcome") for s in c.sessions])
        self.assertTrue(c.quarantined(), "F2: parallel provider-error left no quarantine")

    def test_control_serial_provider_error_is_quarantined(self):
        c = Case(self, "provider-error")
        self.assertEqual("provider-error", c.sessions[-1]["outcome"])
        self.assertTrue(c.quarantined())

    def test_parallel_timeout_is_quarantined_like_serial(self):
        # 30 is the configuration's floor; a smaller value is refused at load, and a
        # refused conf dispatches nothing — `all()` over zero sessions would then pass.
        c = Case(self, "sleep", parallel=True, extra_conf="session_timeout=30\n")
        self.assertEqual(["timeout", "timeout"], [s.get("outcome") for s in c.sessions],
                         c.out[-600:])
        self.assertTrue(c.quarantined(), "F2: parallel timeout left no quarantine")


# ------------------------------------------------------------------ F3: binding

class F3TheStopIsBoundToTheCharter(unittest.TestCase):
    def test_control_without_a_stop_the_finding_is_dispatched(self):
        doc = state_doc(findings=[finding_record(status="accepted")])
        c = Case(self, "control", doc=doc)
        rc, out = c.dry_run()
        self.assertIn("Remediator", out)

    def test_a_finding_stop_halts_the_same_finding(self):
        doc = state_doc(findings=[finding_record(status="accepted")])
        c = Case(self, "finding", doc=doc)
        self.assertEqual("refused", c.sessions[-1]["outcome"])
        rc, out = c.dry_run()
        self.assertNotIn("=== Remediator session ===", out,
                         "F3.1: a Remediator stop on F-0001 did not stop F-0001 being "
                         "dispatched again")

    def test_control_a_stop_on_its_own_pass_halts_it(self):
        c = Case(self, "valid")
        rc, out = c.dry_run()
        self.assertIn("refusal on P-01", out)

    def test_a_stop_naming_another_pass_is_refused(self):
        c = Case(self, "foreign")
        rc, out = c.dry_run()
        self.assertNotIn("refusal on P-02", out,
                         "F3.2: an Auditor on P-01 halted P-02 with a foreign-target stop")

    def test_the_command_the_gate_prints_clears_the_stop(self):
        c = Case(self, "valid")
        rc, out = c.dry_run()
        import re
        import shlex
        m = re.search(r"`xcheck ([^`]+)`", out[out.find("refusal on P-01"):])
        self.assertIsNotNone(m, out[-800:])
        # Executed VERBATIM: the placeholder text is a legal resolution, so nothing in the
        # printed command is edited before it runs.
        argv = shlex.split(m.group(1))
        rc2, out2 = c.f.run_subprocess(*argv)
        self.assertEqual(0, rc2, f"F3.3: the printed resolution `xcheck {' '.join(argv)}` "
                                 f"does not work: {out2.strip()}")
        rc3, out3 = c.dry_run()
        self.assertNotIn("refusal on P-01", out3, "the resolution did not lift the stop")

    def test_control_a_quoting_file_without_the_line_is_ok(self):
        c = Case(self, "control")
        self.assertEqual("ok", c.sessions[-1]["outcome"])

    def test_a_quoted_stop_line_in_tool_output_is_not_a_stop(self):
        c = Case(self, "spoof")
        s = c.sessions[-1]
        self.assertEqual("ok", s["outcome"],
                         f"F3: a STOP line quoted in a file the session printed became "
                         f"control (outcome {s['outcome']}, stop {s.get('stop')})")
        self.assertIsNone(s.get("stop"))


# ------------------------------------------------------------------ F4: identity

class F4TheDigestCoversThePresentedMaterial(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="xc-identity-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.root)]))
        for argv in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                     ["config", "user.name", "t"]):
            subprocess.run(["git"] + argv, cwd=self.root, check=True)
        (self.root / "payload.bin").write_bytes(b"\x00baseline\x00")
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.root, check=True)
        self.manifest = xcheck_submodule("state").subject_manifest

    def test_control_tracked_binary_edits_are_distinguished(self):
        base = self.manifest(self.root)
        (self.root / "payload.bin").write_bytes(b"\x00one\x00")
        a = self.manifest(self.root)
        (self.root / "payload.bin").write_bytes(b"\x00two\x00")
        b = self.manifest(self.root)
        self.assertEqual(3, len({base, a, b}))

    def test_an_untracked_file_and_its_edit_change_the_digest(self):
        base = self.manifest(self.root)
        (self.root / "untracked.py").write_text("result = 1\n")
        a = self.manifest(self.root)
        (self.root / "untracked.py").write_text("result = 999\n")
        b = self.manifest(self.root)
        self.assertEqual(3, len({base, a, b}),
                         f"F4: untracked material is invisible to the digest: {base} {a} {b}")


# ------------------------------------------------------------------ F5: the capsule

class F5TheCapsuleCarriesTheCharter(unittest.TestCase):
    def build(self):
        f = Fixture(state_doc(queue=[queue_pass("P-01")]))
        self.addCleanup(f.cleanup)
        st = xcheck_submodule("state").load_state(f.audit)
        cap = xcheck_submodule("capsule")
        # The serial dispatcher passes the decision detail — the bare pass id — as the
        # charter (decision.py, runner.py `capsule.build(..., "Auditor", detail)`).
        return st.queue[0], cap.render_capsule(cap.build(st, f.root, "Auditor", "P-01"))

    def test_control_the_capsule_names_the_pass(self):
        q, text = self.build()
        self.assertIn("P-01", text)

    def test_the_capsule_carries_the_canonical_charter_text(self):
        q, text = self.build()
        self.assertIn(q.charter, text, "F5: the rendered capsule lacks the canonical charter")


# ------------------------------------------------------------------ F6: durability

class F6DurabilityIsNotAge(unittest.TestCase):
    def outcomes(self, day):
        benchmark = xcheck_submodule("benchmark")
        rec = SimpleNamespace(id="F-0001", status="closed", attempts=0, updated="2026-10-01")
        return benchmark.useful_outcomes(SimpleNamespace(findings=[rec], class_findings=[]),
                                         now=day)

    def test_control_the_closed_record_is_one_outcome_on_both_days(self):
        self.assertEqual(1, self.outcomes(date(2026, 10, 14))[0])
        self.assertEqual(1, self.outcomes(date(2026, 10, 16))[0])

    def test_elapsed_time_alone_does_not_make_a_fix_durable(self):
        before, after = self.outcomes(date(2026, 10, 14)), self.outcomes(date(2026, 10, 16))
        self.assertEqual(before, after, "F6: the same unchanged record became a durable fix "
                                        f"by waiting two days: {before} -> {after}")


# ------------------------------------------------------------------ P1: provider path + payer

class P1TheProviderPathAndThePayer(unittest.TestCase):
    def container_cmd(self):
        runner, envelope, util = (xcheck_submodule(m) for m in ("runner", "envelope", "util"))
        tmp = Path(tempfile.mkdtemp(prefix="xc-argv-"))
        conf = dict(util.CONF_DEFAULTS, sandbox_profile="container",
                    auditor_cmd="provider-wrapper --sidecar {sidecar} {prompt}")
        side = envelope.sidecar_path(tmp / "logs" / "session.log", "a" * 16)
        cmd = runner.build_cmd(conf, "Auditor", "audit the code", sidecar=side)
        argv = runner.container_argv(cmd, conf, tmp / "work", tmp / "out", tmp / "subject")
        mounts = [argv[i + 1].split(":")[0] for i, a in enumerate(argv) if a == "-v"]
        return cmd, mounts

    def test_control_the_container_mounts_src_work_out(self):
        cmd, mounts = self.container_cmd()
        self.assertEqual(3, len(mounts), mounts)

    def test_the_contained_command_names_no_unreachable_host_path(self):
        """P1 (0.9.6): rewritten to the new contract. Under `container` a `{sidecar}`
        command is refused at the launch, and the provider source is reachable WITHOUT a
        sidecar mount: the parent parses the CLI's own stream (`codex-stream`)."""
        runner, envelope, util, provider = (xcheck_submodule(m) for m in
                                            ("runner", "envelope", "util", "provider"))
        container = runner.PROFILES["container"]
        conf = dict(util.CONF_DEFAULTS, sandbox_profile="container",
                    auditor_cmd="provider-wrapper --sidecar {sidecar} {prompt}")
        self.assertIsNotNone(runner.contained_sidecar_problem(conf, "Auditor", container),
                             "P1: a {sidecar} host path launched inside the container")
        conf = dict(conf, auditor_cmd="codex exec --json {prompt}",
                    telemetry_adapter="codex-stream")
        self.assertIsNone(runner.contained_sidecar_problem(conf, "Auditor", container))
        tmp = Path(tempfile.mkdtemp(prefix="xc-argv-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        cmd = runner.build_cmd(conf, "Auditor", "audit the code",
                               sidecar=envelope.sidecar_path(tmp / "s.log", "a" * 16))
        self.assertEqual([], [a for a in cmd if a.startswith("/")], cmd)
        log = tmp / "s.log"
        log.write_text('{"type": "turn.completed", "usage": {"input_tokens": 3, '
                       '"cached_input_tokens": 0, "output_tokens": 2}}\n')
        tele, total = envelope.telemetry(log, session_id="a" * 16,
                                         adapter=provider.ADAPTERS["codex-stream"])
        self.assertEqual(("provider", 5), (tele["telemetry_source"], total),
                         "P1: no provider source is reachable under container")

    def gates(self, subject_conf, profile_text):
        util, budget, state, policy = (xcheck_submodule(m) for m in
                                       ("util", "budget", "state", "policy"))
        tmp = Path(tempfile.mkdtemp(prefix="xc-payer-"))
        (tmp / "audit").mkdir()
        (tmp / "audit" / "orchestrator.conf").write_text(subject_conf)
        # Outside the "project": a profile inside it is refused for a different reason.
        prof = Path(tempfile.mkdtemp(prefix="xc-payer-profile-")) / "operator.conf"
        prof.write_text(profile_text)
        f = Fixture(state_doc())
        self.addCleanup(f.cleanup)
        st = state.load_state(f.audit)
        try:
            conf = util.load_conf(tmp / "audit", profile=prof)
        except (policy.PolicyError, SystemExit) as e:
            if "belong to the PROJECT" not in str(e):
                raise
            self.fail(f"P1: the operator profile cannot own a token ceiling: {e}")
        ev = [{"event": "session_finished", "session_id": "a" * 16, "tokens": 101}]
        return budget.gates(st, conf, ev)

    def test_control_a_subject_ceiling_stops_a_known_overrun(self):
        fired = self.gates("budgets=on\ntokens_per_audit=100\n", "session_timeout=60\n")
        self.assertEqual("stop-known-overrun", fired[0])

    def test_the_subject_cannot_disarm_the_operators_ceiling(self):
        # 0.9.6 (P1b): an operator `tokens_per_audit` needs its reservation beside it.
        fired = self.gates("budgets=off\ntokens_per_audit=100000\n",
                           "budgets=on\ntokens_per_audit=100\ntokens_per_session=50\n")
        self.assertIsNotNone(fired, "P1: subject budgets=off disarmed the operator's ceiling")


if __name__ == "__main__":
    unittest.main()
