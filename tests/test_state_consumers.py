"""Consumer-path coverage for the concerns whose selftest home was deleted.

Phase 5 removed 126 embedded checks. Most died with their subject — a Markdown
parser that no longer exists. A handful had a subject that survives and only
their FIXTURE was Markdown: the §4 rule 9 construal gate, the §5 refusal route,
§7 scope typing, recurrence-of routing, the reopen limit, and the state digest
the loop uses to notice that a session did something. Those are re-asserted
here, at the consumer, against a `state.json` fixture — which is where the
audit's P0 item 3 wanted them anyway ("вынести проверки из production-файла").

Each test asks a REAL command and reads its observable outcome, and each guard
is paired with the control showing the opposite outcome (tests/README.md).

The charter strings are not decorative. `decide()` builds a Remediator charter
as `finding F-0001`, and the construal key is derived from (role, charter), so a
record keyed on any other wording is a record about a different charter and the
gate correctly ignores it. `_CHARTER` below is that exact string.
"""

import unittest

from tests.harness import (Fixture, finding_record, queue_pass, state_doc,
                           xcheck_submodule)

_DEFAULT_CONF = xcheck_submodule("util").DEFAULT_CONF
# Feature flags are toggled by REPLACING the default line, never by writing a
# two-key conf: `load_conf` falls back to DEFAULT_CONF only when the file is
# absent, so a partial file drops `remediator_cmd` and every dispatch refuses
# for the wrong reason.
CONF_GATE = _DEFAULT_CONF.replace("construal_gate=off", "construal_gate=on")
CONF_SCOPE = _DEFAULT_CONF.replace("scope_typing=off", "scope_typing=on")

_CHARTER = "finding F-0001"

REFUSAL_BODY = ("# F-0001\n\n## Refusal\n\nThe material named by the charter is not "
                "in this repository, so the work cannot start.\n")
SCOPE_BODY = ("# F-0001\n\n## Admitted scope\n\n### Covers\n\nthe parser entry point\n\n"
              "### Does not cover\n\nany caller of it\n")


def construal(role, charter, status="admitted", session="fedcba9876543210",
              admitted_by="human:operator"):
    key = xcheck_submodule("util").construal_key(role, charter)
    rec = {"key": key, "role": role, "charter": charter, "session": session,
           "status": status, "created": "2026-08-14",
           "body_path": f"construals/{key}.md"}
    if status == "admitted":
        rec["admitted_by"] = admitted_by
        rec["admitted_at"] = "2026-08-14"
    return rec


class ConstrualGate(unittest.TestCase):
    """§4 rule 9: three routing states, and no self-admission."""

    def _fx(self, construals=(), conf=CONF_GATE):
        fx = Fixture(state_doc(findings=[finding_record("F-0001", "accepted",
                                                        next="Remediator")],
                               queue=[queue_pass("P-01", done=True)],
                               construals=list(construals)),
                     conf=conf)
        self.addCleanup(fx.cleanup)
        return fx

    def test_no_construal_dispatches_the_write_it_and_stop_charter(self):
        code, out = self._fx().decision()
        self.assertEqual(code, 0)
        self.assertIn("write your OPERATIONAL CONSTRUAL", out)
        self.assertIn("STOP without touching anything else", out)
        self.assertIn(f"<<<CHARTER>>>{_CHARTER}<<<END CHARTER>>>", out)

    def test_a_proposed_construal_stops_for_a_human(self):
        code, out = self._fx([construal("Remediator", _CHARTER, status="proposed")]).decision()
        self.assertEqual(code, 0)
        self.assertIn("gate: construal on the Remediator charter", out)
        self.assertIn("EVIDENCE, never authority", out)
        self.assertNotIn("=== Remediator session ===", out)

    def test_control_an_admitted_construal_dispatches_the_real_charter(self):
        code, out = self._fx([construal("Remediator", _CHARTER)]).decision()
        self.assertEqual(code, 0)
        self.assertIn("=== Remediator session ===", out)
        self.assertIn(f"charter: {_CHARTER}", out)
        self.assertNotIn("OPERATIONAL CONSTRUAL", out)

    def test_control_the_flag_off_dispatches_with_no_construal_at_all(self):
        code, out = self._fx(conf=_DEFAULT_CONF).decision()
        self.assertEqual(code, 0)
        self.assertIn("=== Remediator session ===", out)
        self.assertNotIn("OPERATIONAL CONSTRUAL", out)

    def test_a_construal_on_a_different_charter_does_not_license_this_one(self):
        """The key binds a record to (role, charter). A record about some other
        wording is a record about other work — the gate must not accept it."""
        code, out = self._fx([construal("Remediator", "finding F-0002")]).decision()
        self.assertEqual(code, 0)
        self.assertIn("write your OPERATIONAL CONSTRUAL", out)

    def test_a_self_admitted_construal_is_not_a_document(self):
        """F-0138: the record that would license its own dispatch cannot exist.
        The producer admitting itself is refused at the reading boundary, so the
        gate never has to decide whether to believe it."""
        code, out = self._fx(
            [construal("Remediator", _CHARTER, admitted_by="fedcba9876543210")]).decision()
        self.assertNotEqual(code, 0)
        self.assertIn("may not admit its own construal", out)

    def test_an_admitter_outside_the_vocabulary_is_not_a_document(self):
        code, out = self._fx(
            [construal("Remediator", _CHARTER,
                       admitted_by="session:fedcba9876543210")]).decision()
        self.assertNotEqual(code, 0)
        self.assertIn("is not an admitter", out)

    def test_writing_and_admitting_a_construal_each_move_the_state_digest(self):
        """The loop's progress signal (F-0118). A session that only wrote its
        construal must still count as having moved the audit, or the no-progress
        detector reads two legitimate gate steps as a non-converging cycle."""
        digest = xcheck_submodule("runner").audit_state_digest
        fx = self._fx()
        before = digest(fx.root)
        fx.write_state(state_doc(findings=[finding_record("F-0001", "accepted",
                                                          next="Remediator")],
                                 queue=[queue_pass("P-01", done=True)],
                                 construals=[construal("Remediator", _CHARTER,
                                                       status="proposed")]))
        proposed = digest(fx.root)
        fx.write_state(state_doc(findings=[finding_record("F-0001", "accepted",
                                                          next="Remediator")],
                                 queue=[queue_pass("P-01", done=True)],
                                 construals=[construal("Remediator", _CHARTER)]))
        admitted = digest(fx.root)
        self.assertNotEqual(before, proposed, "writing a construal left no trace")
        self.assertNotEqual(proposed, admitted, "admitting a construal left no trace")


class RefusalRoute(unittest.TestCase):
    """§5: a typed refusal is durable state, and the charter stays in force."""

    def _fx(self, refusal=None, body=REFUSAL_BODY, status="accepted", nxt="Remediator"):
        rec = finding_record("F-0001", status, next=nxt)
        if refusal:
            rec["refusal"] = refusal
        fx = Fixture(state_doc(findings=[rec], queue=[queue_pass("P-01", done=True)]))
        self.addCleanup(fx.cleanup)
        fx.body("findings/F-0001.md", body)
        return fx

    def test_a_live_refusal_stops_and_carries_its_reason_code(self):
        code, out = self._fx("material-missing").decision()
        self.assertEqual(code, 0)
        self.assertIn("refusal on F-0001", out)
        self.assertIn("material-missing", out)
        self.assertIn("STAYS IN FORCE", out)
        self.assertNotIn("=== Remediator session ===", out)

    def test_a_reason_code_outside_the_vocabulary_is_not_a_document(self):
        code, out = self._fx("because-i-said-so").decision()
        self.assertNotEqual(code, 0)
        self.assertIn("is not a canonical §5 refusal reason", out)

    def test_a_refusal_with_no_prose_is_an_addressed_lint_error(self):
        code, out = self._fx("material-missing",
                             body="# F-0001\n\nNo section here.\n").run("lint")
        self.assertEqual(code, 1)
        self.assertIn("`## Refusal` section is missing or empty", out)

    def test_control_a_refusal_with_its_prose_is_lint_clean(self):
        self.assertEqual(self._fx("material-missing").run("lint")[0], 0)

    def test_control_no_refusal_dispatches_the_remediator(self):
        code, out = self._fx().decision()
        self.assertEqual(code, 0)
        self.assertIn("=== Remediator session ===", out)

    def test_control_a_refusal_on_a_terminal_finding_is_history_not_an_obstacle(self):
        code, out = self._fx("material-missing", status="closed", nxt="—").decision()
        self.assertEqual(code, 0)
        self.assertIn("audit complete", out)
        self.assertNotIn("refusal on F-0001", out)


class ScopeTyping(unittest.TestCase):
    """§7/§10 `scope_typing`: the same record, lint-clean off and named on."""

    def _fx(self, conf, body, admitted_scope=None, status="fixed", nxt="Verifier"):
        rec = finding_record("F-0001", status, next=nxt, fixed_by="1a2b3c4d5e6f7a8b")
        if admitted_scope:
            rec["admitted_scope"] = admitted_scope
        fx = Fixture(state_doc(findings=[rec], queue=[queue_pass("P-01", done=True)]),
                     conf=conf)
        self.addCleanup(fx.cleanup)
        fx.body("findings/F-0001.md", body)
        return fx

    BARE = "# F-0001\n\nNo scope section.\n"

    def test_the_flag_is_what_makes_the_difference(self):
        self.assertEqual(self._fx(_DEFAULT_CONF, self.BARE).run("lint")[0], 0,
                         "the same record must be clean with the flag off")
        code, out = self._fx(CONF_SCOPE, self.BARE).run("lint")
        self.assertEqual(code, 1)
        self.assertIn("`admitted_scope` is empty", out)

    def test_the_residue_half_is_mandatory(self):
        """A coverage claim with no stated limit reads as complete while admitting
        nothing — the `### Does not cover` half is the load-bearing one."""
        half = ("# F-0001\n\n## Admitted scope\n\n### Covers\n\nthe parser entry point\n")
        code, out = self._fx(CONF_SCOPE, half, admitted_scope="the parser").run("lint")
        self.assertEqual(code, 1)
        self.assertIn("### Does not cover", out)

    def test_the_section_is_structural_not_a_substring(self):
        """A whole scope section inside a fence is text about a section, not one."""
        fenced = "# F-0001\n\n```\n" + SCOPE_BODY + "```\n"
        code, out = self._fx(CONF_SCOPE, fenced, admitted_scope="the parser").run("lint")
        self.assertEqual(code, 1)
        self.assertIn("no `## Admitted scope` section", out)

    def test_a_closed_finding_is_exempt_with_the_flag_on(self):
        """No retroactive invalidation of work admitted under the older norm."""
        self.assertEqual(
            self._fx(CONF_SCOPE, self.BARE, status="closed", nxt="—").run("lint")[0], 0)

    def test_control_a_real_scope_section_is_clean_with_the_flag_on(self):
        self.assertEqual(
            self._fx(CONF_SCOPE, SCOPE_BODY, admitted_scope="the parser").run("lint")[0], 0)


class RecurrenceRouting(unittest.TestCase):
    """F-0043: a recurrence of a terminal finding is new work, not completion."""

    def _fx(self, findings):
        fx = Fixture(state_doc(findings=findings, queue=[queue_pass("P-01", done=True)]))
        self.addCleanup(fx.cleanup)
        return fx

    def test_a_recurrence_of_a_closed_finding_blocks_done(self):
        code, out = self._fx([finding_record("F-0001", "closed", next="—"),
                              finding_record("F-0002", "reported",
                                             recurrence_of="F-0001")]).decision()
        self.assertEqual(code, 0)
        self.assertIn("triage needed", out)
        self.assertNotIn("audit complete", out)

    def test_a_recurrence_pointing_at_nothing_is_not_a_document(self):
        code, out = self._fx([finding_record("F-0002", "reported",
                                             recurrence_of="F-0099")]).decision()
        self.assertNotEqual(code, 0)
        self.assertIn("recurrence_of: F-0099 does not exist", out)

    def test_control_a_ledger_of_terminal_records_completes(self):
        code, out = self._fx([finding_record("F-0001", "closed", next="—")]).decision()
        self.assertEqual(code, 0)
        self.assertIn("audit complete", out)


class ReopenLimit(unittest.TestCase):
    """F-0005: the mandatory human gate fires from the record's own attempts,
    against the limit held in state — not from a number recognised in prose."""

    def _fx(self, attempts, limit=2):
        limits = {"max_findings_per_pass": 15, "remediation_batch_size": 8,
                  "class_threshold": 3, "reopen_limit": limit}
        fx = Fixture(state_doc(
            findings=[finding_record("F-0001", "reopened", attempts=attempts,
                                     next="Remediator")],
            queue=[queue_pass("P-01", done=True)], limits=limits))
        self.addCleanup(fx.cleanup)
        return fx

    def test_at_the_limit_the_human_gate_fires(self):
        code, out = self._fx(1).decision()
        self.assertEqual(code, 0)
        self.assertIn("needs-human on F-0001", out)

    def test_a_raised_limit_moves_the_gate_and_nothing_else_does(self):
        code, out = self._fx(1, limit=5).decision()
        self.assertEqual(code, 0)
        self.assertIn("=== Remediator session ===", out)

    def test_control_below_the_limit_remediation_continues(self):
        code, out = self._fx(0).decision()
        self.assertEqual(code, 0)
        self.assertIn("=== Remediator session ===", out)


if __name__ == "__main__":
    unittest.main()
