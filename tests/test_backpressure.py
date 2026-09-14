"""Backpressure on finding debt: a finite triage cap, and no new auditing over an open
critical. The audit's evidence for both is the same corpus — 133 reported, 0 triaged,
1 critical open — produced by an orchestrator that had no way of saying "enough".

The two gates are different pressures and neither replaces the other. The cap bounds the
VOLUME of undecided debt; the critical gate bounds its SEVERITY. A project can sit under
the cap and still be sitting on a hole.
"""

import contextlib
import io
import shutil
import unittest
from pathlib import Path

from tests.harness import Fixture, finding_record, queue_pass, state_doc, xcheck_submodule

cli = xcheck_submodule("cli")
decision = xcheck_submodule("decision")
policy = xcheck_submodule("policy")
util = xcheck_submodule("util")

TERMINAL = util.TERMINAL


def project(findings=(), passes=("P-01",)):
    f = Fixture(state_doc(findings=list(findings),
                          queue=[queue_pass(p) for p in passes]))
    return f


class TheShippedCapIsFinite(unittest.TestCase):
    """`triage_batch_cap=0` is not a neutral default. It is the instruction "never ask
    for triage", and the project that shipped it reached 133 undecided findings."""

    def test_the_default_is_a_finite_number(self):
        self.assertNotEqual("0", util.CONF_DEFAULTS["triage_batch_cap"])
        self.assertGreater(int(util.CONF_DEFAULTS["triage_batch_cap"]), 0)
        print(f"\n  CAP  CONF_DEFAULTS['triage_batch_cap'] = "
              f"{util.CONF_DEFAULTS['triage_batch_cap']}")

    def test_the_conf_a_new_project_gets_carries_the_same_number(self):
        """`DEFAULT_CONF` is written into a project that has none. A template that
        still said 0 would hand every new project the defect by copy."""
        want = f"triage_batch_cap={util.CONF_DEFAULTS['triage_batch_cap']}"
        self.assertIn(want, util.DEFAULT_CONF)
        self.assertNotIn("triage_batch_cap=0", util.DEFAULT_CONF)

    def test_the_number_is_justified_where_it_is_set(self):
        """The value is a judgement, and a judgement with no stated basis is a number
        someone will 'tidy' back to 0. The comment names the backlog it answers."""
        block = util.DEFAULT_CONF.split("triage_batch_cap=")[0]
        comment = block[block.rindex("# triage_batch_cap"):]
        self.assertIn("133", comment, "the cap does not name the backlog that justifies it")
        print(f"  JUSTIFICATION  {' '.join(comment.split())[:150]}")

    def test_zero_is_still_a_legal_explicit_choice(self):
        """Shipping unbounded and PERMITTING unbounded are different. An operator who
        writes 0 has decided; a project that inherits 0 has not."""
        self.assertEqual(0, util.NUMERIC_CONF["triage_batch_cap"])
        self.assertEqual(0, util.conf_number({"triage_batch_cap": "0"}, "triage_batch_cap"))

    def test_the_cap_is_read_through_the_one_default_not_a_second_one(self):
        """`int(conf.get("triage_batch_cap", "0"))` at the call site was a second
        default. It agreed with the first until the first changed, and then a plain
        dict quietly got the unbounded behaviour the conf had stopped offering."""
        self.assertEqual(int(util.CONF_DEFAULTS["triage_batch_cap"]),
                         util.conf_number({}, "triage_batch_cap"))
        f = project([finding_record(f"F-{i:04d}", "reported") for i in range(1, 22)])
        self.addCleanup(shutil.rmtree, f.root, True)
        kind, _ = decision.state_and_decision(f.root, {})[3:]
        self.assertEqual("stop-triage", kind,
                         "21 undecided findings did not reach a cap of "
                         f"{util.CONF_DEFAULTS['triage_batch_cap']} — the call site is "
                         "still reading a default of its own")


class AnOpenCriticalStopsNewAuditing(unittest.TestCase):

    def decide(self, f, conf=None):
        return decision.state_and_decision(f.root, {} if conf is None else conf)[3:]

    def test_a_new_pass_is_refused_and_the_gate_names_the_finding(self):
        f = project([finding_record("F-0001", "reported", severity="critical")])
        self.addCleanup(shutil.rmtree, f.root, True)
        kind, detail = self.decide(f)
        print(f"\n  GATE     open critical F-0001 (reported) -> {kind} {detail}")
        self.assertEqual("stop-critical", kind)
        self.assertEqual(("F-0001", "reported"), detail)

    def test_open_means_non_terminal_not_merely_reported(self):
        """Triaging a critical does not close the hole. `deferred` is the sharpest case:
        it is a decision, it is not terminal, and it is exactly the status a project
        reaches for when it wants to keep auditing over a known hole."""
        f = project([finding_record("F-0001", "deferred", severity="critical")])
        self.addCleanup(shutil.rmtree, f.root, True)
        kind, detail = self.decide(f)
        print(f"  OPEN     deferred critical -> {kind} {detail}")
        self.assertEqual("stop-critical", kind)

    def test_the_gate_never_blocks_the_work_that_resolves_it(self):
        """A gate that also refused remediation would make the critical unfixable and
        the queue permanently stuck — the failure mode that decides WHERE the gate is
        read (after the decision, on the auditing branch only)."""
        f = project([finding_record("F-0001", "accepted", severity="critical")])
        self.addCleanup(shutil.rmtree, f.root, True)
        kind, _ = self.decide(f)
        self.assertEqual("run-remediator", kind,
                         "the gate blocked remediation — the one way out")
        print(f"  NOT GATED  accepted critical -> {kind} (remediation is the way out)")

    def test_a_closed_critical_does_not_gate(self):
        f = project([finding_record("F-0001", "closed", severity="critical")])
        self.addCleanup(shutil.rmtree, f.root, True)
        self.assertEqual(("run-auditor", "P-01"), self.decide(f))

    def test_control_no_critical_means_business_as_usual(self):
        """Without this the gate could be always-on and every assertion above would
        still pass."""
        f = project([finding_record("F-0001", "reported", severity="major")])
        self.addCleanup(shutil.rmtree, f.root, True)
        kind, detail = self.decide(f)
        print(f"  CONTROL  worst open severity major -> {kind} {detail}")
        self.assertEqual(("run-auditor", "P-01"), (kind, detail))

    def test_the_gate_says_what_the_operator_can_do(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.report_gate("stop-critical", ("F-0098", "reported"))
        out = buf.getvalue()
        self.assertIn("F-0098", out)
        for expected in ("triage", "audit_over_critical=on"):
            self.assertIn(expected, out, "the gate does not name the way out")
        self.assertNotIn("stop-critical\n", out, "the kind fell through to print(kind)")


class TheOverrideIsAProjectDecision(unittest.TestCase):
    """Whether to keep auditing over a known hole is a judgement about THIS audit, not a
    containment setting. It belongs in the project half of the conf, and it belongs off."""

    def test_it_is_a_project_key_not_a_policy_key(self):
        self.assertEqual("project", policy.classify("audit_over_critical"))
        self.assertIn("audit_over_critical", policy.PROJECT_KEYS)
        self.assertNotIn("audit_over_critical", policy.POLICY_KEYS)

    def test_it_defaults_to_off(self):
        self.assertEqual("off", util.CONF_DEFAULTS["audit_over_critical"])
        self.assertIs(False, util.conf_flag({}, "audit_over_critical"))

    def test_on_dispatches_the_pass_anyway(self):
        f = project([finding_record("F-0001", "reported", severity="critical")])
        self.addCleanup(shutil.rmtree, f.root, True)
        self.assertEqual(
            ("run-auditor", "P-01"),
            decision.state_and_decision(f.root, {"audit_over_critical": "on"})[3:])

    def test_it_goes_through_conf_flag_so_a_typo_cannot_disarm_it(self):
        """A hand-rolled `conf.get(...) == "on"` reads `audit_over_critcal=on` as off and
        the operator never learns the gate they asked to lift is still armed — or, worse,
        `if conf.get(key):` reads the STRING "off" as true and the gate is never armed at
        all. The boundary refuses the value instead (F-0113)."""
        self.assertIn("audit_over_critical", util.BOOLEAN_CONF)
        with self.assertRaises(SystemExit) as e:
            util.conf_flag({"audit_over_critical": "yes-please"}, "audit_over_critical")
        self.assertIn("audit_over_critical", str(e.exception))


class ThisProjectsOwnStateThroughTheGate(unittest.TestCase):
    """Not a fixture. The audit measured THIS corpus, so this replays it."""

    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent
        if not (self.root / "audit" / "state.json").is_file():
            self.skipTest("no audit corpus in this tree")

    def test_the_live_corpus_routes_to_the_decision_the_operator_made(self):
        """PHASE 10 of the third-audit response moved this test's subject.

        It used to assert `stop-triage` on a corpus of 133 undecided findings. The
        operator then triaged the one critical — F-0098, `reported` -> `accepted`, at
        state revision 480 — and §5's priority puts an ACCEPTED finding above the triage
        pile, because remediation is work that is already decided while triage is work
        that is not. So the live decision is now `run-remediator`, and asserting the old
        answer would be asserting that the human's decision had no effect.

        What the test still holds is the thing it was written for: this project's own
        corpus, not a fixture, reaches a backpressure gate rather than opening more audit
        passes on top of 132 undecided findings.
        """
        conf = dict(util.load_conf(self.root / "audit")._raw)
        state, _u, _c, kind, detail = decision.state_and_decision(self.root, conf)
        records = list(state.findings) + list(state.class_findings)
        crit = [r.id for r in records
                if r.severity == "critical" and r.status not in TERMINAL]
        reported = [r.id for r in records if r.status == "reported"]
        accepted = [r.id for r in records if r.status == "accepted"]
        print(f"\n  LIVE  {len(records)} findings, {len(reported)} reported, "
              f"{len(accepted)} accepted, open critical {crit}")
        print(f"  LIVE  shipped cap {conf['triage_batch_cap']} -> {kind} {detail}")
        self.assertEqual("run-remediator", kind,
                         "the operator's accepted finding no longer routes the run")
        self.assertEqual(accepted[:1], list(detail))

        # The triage debt is NOT discharged by that one decision, and the count says so
        # exactly: 132 findings are still `reported`, still over the shipped cap, and
        # still owed a human sitting. A run that read "no triage gate" off the line
        # above would be reading a routing decision as an all-clear.
        self.assertGreaterEqual(len(reported), int(conf["triage_batch_cap"]))
        print(f"  LIVE  triage still owed on {len(reported)} finding(s), cap "
              f"{conf['triage_batch_cap']}")

        # And the critical is ACCEPTED, which is not closed: `stop-critical` reads
        # non-terminal, so the hole is still open and still guards new auditing. The
        # gate's own behaviour is exercised against fixtures above; what is asserted
        # here is the live precondition it fires on.
        self.assertEqual(["F-0098"], crit,
                         "the live open critical changed identity")
        self.assertFalse(util.conf_flag(conf, "audit_over_critical"),
                         "the live conf waives the critical gate")


if __name__ == "__main__":
    unittest.main()
