"""The eight Markdown control-plane bypasses, re-asserted after the rewrite.

Each test builds a temp audit tree carrying one poison and then asks a REAL
command what it decided. The green condition is the command's observable
outcome — the decision line, the addressed refusal, the exit code — never a
predicate's return value in isolation (see tests/README.md).

Every bypass is paired with a CONTROL: the same situation with the poison
replaced by a legitimate declaration, asserting the opposite outcome. Without
the control, a test that "passes" proves only that the fixture reached some
state, not that the guard is what put it there.

PHASE 5 — what changed. All eight were closed during Ouroboros-3 by teaching a
line recognizer about one more Markdown spelling. They are now closed a second
time, and differently: machine state lives in `audit/state.json`, so each poison
is not a rejected input but an UNWRITABLE one. Every test below therefore has
three parts:

  1. the one-line note on why the poison is structurally inexpressible now;
  2. the legacy tree carrying it, refused outright — proof there is no
     "fall back to Markdown" path left for the poison to arrive on;
  3. the nearest thing the poison CAN still be said as, in JSON, and what the
     boundary does with it.

The control then shows the legitimate declaration producing the opposite
outcome, so the pair still measures the guard and not the fixture.
"""

import copy
import unittest

from tests.harness import (Fixture, LegacyFixture, LIMITS, audit_md, finding,
                           finding_record, ledger, queue_pass, state_doc)

NEEDS_HUMAN = "needs-human on F-0001"
NO_STATE = "no state file"

# A finding reopened once against the shipped `reopen_limit: 2`: attempts + 1 >=
# reopen_limit, so the mandatory human gate must fire.
REOPENED = finding_record("F-0001", "reopened", attempts=1, next="Remediator")


class LegacyTreeHasNoWayIn(unittest.TestCase):
    """Before the eight: the tree every one of them lived in is refused whole.

    This is the assertion that makes the other eight's "inexpressible" claim
    mean something. If a Markdown audit still loaded, "the poison cannot be
    written in JSON" would only mean the attacker writes it in Markdown.
    """

    def test_a_complete_markdown_audit_is_not_read_at_all(self):
        fx = LegacyFixture(
            ledger_text=ledger([("F-0001", "a finding", "major", "reported")]),
            findings={"F-0001-a.md": finding()})
        self.addCleanup(fx.cleanup)
        for cmd in ("status", "lint", "metrics"):
            code, out = fx.run(cmd)
            self.assertNotEqual(code, 0, f"{cmd} read a legacy tree")
            self.assertIn(NO_STATE, out)
            self.assertIn("xcheck migrate", out)
        code, out = fx.decision()
        self.assertNotEqual(code, 0)
        self.assertIn(NO_STATE, out)


class FrontmatterStructure(unittest.TestCase):
    """Bypasses 1-2: the finding record's own structure."""

    def test_nested_yaml_key_does_not_flatten_into_a_top_level_field(self):
        """Bypass 1 (F-0149/F-0103). INEXPRESSIBLE: a record is a JSON object, so
        `meta.status` is a nested value under `meta`, not a second `status` — and
        `meta` is not a field of the record, which the boundary says by name."""
        fx = LegacyFixture(
            ledger_text=ledger([("F-0001", "a finding", "major", "reported")]),
            findings={"F-0001-a.md": finding(extra="meta:\n  status: closed\n")})
        self.addCleanup(fx.cleanup)
        code, out = fx.decision()
        self.assertIn(NO_STATE, out)                      # no Markdown path left

        rec = finding_record("F-0001", "reported")
        rec["meta"] = {"status": "closed"}
        js = Fixture(state_doc(findings=[rec]))
        self.addCleanup(js.cleanup)
        code, out = js.decision()
        self.assertNotEqual(code, 0)
        self.assertIn("unknown field(s) 'meta'", out)
        self.assertNotIn("audit complete", out)

    def test_frontmatter_closer_with_a_trailing_tail_is_not_a_closer(self):
        """Bypass 2 (F-0103). INEXPRESSIBLE: there is no delimiter to get wrong —
        a record ends where its JSON object ends, and a document that is not
        well-formed JSON has no records at all rather than some of them."""
        poisoned = finding().replace("attempts: 0\n---\n", "attempts: 0\n--- trailing junk\n")
        fx = LegacyFixture(
            ledger_text=ledger([("F-0001", "a finding", "major", "reported")]),
            findings={"F-0001-a.md": poisoned})
        self.addCleanup(fx.cleanup)
        code, out = fx.decision()
        self.assertIn(NO_STATE, out)

        js = Fixture(raw='{"schema_version": 1, "findings": [ trailing junk\n')
        self.addCleanup(js.cleanup)
        code, out = js.decision()
        self.assertNotEqual(code, 0)
        self.assertIn("not valid JSON", out)
        self.assertNotIn("audit complete", out)

    def test_control_a_well_formed_finding_reaches_the_human_triage_gate(self):
        js = Fixture(state_doc(findings=[finding_record("F-0001", "reported")],
                               queue=[queue_pass("P-01", done=True)]))
        self.addCleanup(js.cleanup)
        code, out = js.decision()
        self.assertEqual(code, 0)
        self.assertIn("triage needed", out)


class QueueEntries(unittest.TestCase):
    """Bypasses 3-5: what counts as a pass in the queue."""

    def test_commented_out_queue_item_is_not_a_pass(self):
        """Bypass 3 (F-0156). INEXPRESSIBLE: the queue is a JSON array. A JSON
        document has no comment syntax, so there is no way to write an entry that
        is present in the file and absent from the array."""
        fx = LegacyFixture(audit_text=audit_md(
            queue_body="<!-- - [ ] P-09 — invariants × U01: charter; stop: budget -->"))
        self.addCleanup(fx.cleanup)
        code, out = fx.decision()
        self.assertIn(NO_STATE, out)

        js = Fixture(state_doc(queue=[queue_pass("P-01", done=True)]))
        self.addCleanup(js.cleanup)
        code, out = js.decision()
        self.assertEqual(code, 0)
        self.assertNotIn("P-09", out)                     # no ninth pass exists
        self.assertIn("audit complete", out)

    def test_queue_item_inside_a_code_fence_is_not_a_pass(self):
        """Bypass 4 (F-0156). INEXPRESSIBLE: same reason — a fence is Markdown
        rendering, and there is no rendering layer between the bytes and the array."""
        fx = LegacyFixture(audit_text=audit_md(
            queue_body="```\n- [ ] P-09 — invariants × U01: charter; stop: budget\n```"))
        self.addCleanup(fx.cleanup)
        code, out = fx.decision()
        self.assertIn(NO_STATE, out)

        js = Fixture(state_doc(queue=[queue_pass("P-01", done=True)]))
        self.addCleanup(js.cleanup)
        code, out = js.decision()
        self.assertEqual(code, 0)
        self.assertNotIn("P-09", out)

    def test_contradictory_checkboxes_for_one_pass_id_never_dispatch(self):
        """Bypass 5 (F-0155). INEXPRESSIBLE: a pass is ONE object with ONE `done`
        boolean. Two lines claiming different things about P-01 become two entries
        with the same id, which the boundary refuses as a duplicate."""
        fx = LegacyFixture(audit_text=audit_md(
            queue_body="- [ ] P-01 — invariants × U01: c; stop: s\n"
                       "- [x] P-01 — invariants × U01: c; stop: s"))
        self.addCleanup(fx.cleanup)
        code, out = fx.decision()
        self.assertIn(NO_STATE, out)

        js = Fixture(state_doc(queue=[queue_pass("P-01", done=False),
                                      queue_pass("P-01", done=True)]))
        self.addCleanup(js.cleanup)
        code, out = js.decision()
        self.assertNotEqual(code, 0)
        self.assertIn("is declared twice", out)
        self.assertNotIn("audit complete", out)

    def test_control_a_real_queue_entry_is_dispatched(self):
        js = Fixture(state_doc(queue=[queue_pass("P-01", done=False)]))
        self.addCleanup(js.cleanup)
        code, out = js.decision()
        self.assertEqual(code, 0)
        self.assertIn("P-01", out)
        self.assertIn("Auditor", out)


class LimitOverrides(unittest.TestCase):
    """Bypasses 6-7: which `reopen_limit` is in force."""

    def test_reopen_limit_inside_an_html_comment_does_not_override(self):
        """Bypass 6 (F-0158). INEXPRESSIBLE: `limits` is a JSON object of numbers.
        A commented-out limit needs a comment; JSON has none, so the only
        `reopen_limit` in the document is the one in force."""
        fx = LegacyFixture(audit_text=audit_md(limits=LIMITS + "\n<!-- reopen_limit: 999 -->\n"),
                           ledger_text=ledger([("F-0001", "a finding", "major", "reopened")]))
        self.addCleanup(fx.cleanup)
        code, out = fx.decision()
        self.assertIn(NO_STATE, out)

        js = Fixture(state_doc(findings=[copy.deepcopy(REOPENED)]))
        self.addCleanup(js.cleanup)
        code, out = js.decision()
        self.assertEqual(code, 0)
        self.assertIn(NEEDS_HUMAN, out)                   # the real limit still governs

    def test_reopen_limit_inside_a_code_fence_does_not_override(self):
        """Bypass 7 (F-0158). INEXPRESSIBLE: same — and a limit that IS written is
        range-checked at the boundary, so `999` is refused on its way in rather
        than silently suppressing the human gate."""
        fx = LegacyFixture(audit_text=audit_md(limits=LIMITS + "\n```\nreopen_limit: 999\n```\n"),
                           ledger_text=ledger([("F-0001", "a finding", "major", "reopened")]))
        self.addCleanup(fx.cleanup)
        code, out = fx.decision()
        self.assertIn(NO_STATE, out)

        js = Fixture(state_doc(findings=[copy.deepcopy(REOPENED)],
                               limits={"reopen_limit": 999}))
        self.addCleanup(js.cleanup)
        code, out = js.decision()
        self.assertNotEqual(code, 0)
        self.assertIn("reopen_limit", out)
        self.assertNotIn(NEEDS_HUMAN.replace("needs-human", "audit complete"), out)

    def test_control_a_real_reopen_limit_override_is_honoured(self):
        """A legitimate in-range override raises the gate — so the two tests above
        measure the poison, not a fixture that could never reach the gate."""
        js = Fixture(state_doc(findings=[copy.deepcopy(REOPENED)],
                               limits={"reopen_limit": 9}))
        self.addCleanup(js.cleanup)
        code, out = js.decision()
        self.assertEqual(code, 0)
        self.assertNotIn(NEEDS_HUMAN, out)
        self.assertIn("Remediator", out)


class CoverageReports(unittest.TestCase):
    """Bypass 8: what counts as a pass's coverage report."""

    def test_coverage_markers_inside_a_comment_are_not_a_report(self):
        """Bypass 8 (F-0153/F-0154). INEXPRESSIBLE: coverage is a RECORD bound to
        its queue entry, not a marker mined out of a Markdown body. A commented
        report is not a weaker report — a done pass with no coverage record is a
        document the boundary refuses."""
        fx = LegacyFixture(audit_text=audit_md(queue_body="- [x] P-01 — invariants × U01: c; stop: s"),
                           passes={"P-01-report.md": "---\nid: P-01\n---\n"
                                                     "<!--\n**COVERED:** all\n**NOT COVERED:** none\n-->\n"})
        self.addCleanup(fx.cleanup)
        code, out = fx.decision()
        self.assertIn(NO_STATE, out)

        js = Fixture(state_doc(queue=[queue_pass("P-01", done=True, coverage=False)]))
        self.addCleanup(js.cleanup)
        code, out = js.decision()
        self.assertNotEqual(code, 0)
        self.assertIn("no coverage record", out)
        self.assertNotIn("audit complete", out)

    def test_a_report_belonging_to_another_pass_does_not_certify_this_one(self):
        """F-0154 corollary. INEXPRESSIBLE: a coverage record is reachable only
        THROUGH its queue entry, so it cannot be addressed to a different pass —
        there is no `id:` field left to disagree with where the record sits."""
        js = Fixture(state_doc(queue=[queue_pass("P-01", done=True, coverage=False),
                                      queue_pass("P-02", done=True)]))
        self.addCleanup(js.cleanup)
        code, out = js.decision()
        self.assertNotEqual(code, 0)
        self.assertIn("queue[0]", out)                    # P-01 named, not P-02's report
        self.assertIn("no coverage record", out)

    def test_a_checked_pass_with_no_report_at_all_blocks_completion(self):
        js = Fixture(state_doc(queue=[queue_pass("P-01", done=True, coverage=False)]))
        self.addCleanup(js.cleanup)
        code, out = js.decision()
        self.assertNotEqual(code, 0)
        self.assertNotIn("audit complete", out)

    def test_control_a_real_coverage_report_completes_the_audit(self):
        js = Fixture(state_doc(queue=[queue_pass("P-01", done=True)]))
        self.addCleanup(js.cleanup)
        code, out = js.decision()
        self.assertEqual(code, 0)
        self.assertIn("audit complete", out)


if __name__ == "__main__":
    unittest.main()
