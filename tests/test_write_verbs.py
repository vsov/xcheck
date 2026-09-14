"""Phase 7: the write verbs are the only way machine state changes.

Two tests per verb at minimum — one legal transition **observed in `state.json`
AND in the regenerated view**, one illegal transition refused with its addressed
message. Observing both surfaces is the point: a verb that updated the document
and left the mirror stale would pass a state-only assertion and then make every
subsequent command refuse for drift.

The class-wide property is at the bottom: with `write_state` stubbed to raise,
**every** verb fails. That is the phase-5 counterfactual applied to the write
side — a verb that still reports success is writing somewhere else.
"""

import json
import os
import unittest
from unittest import mock

from tests.harness import (Fixture, construal_record, finding_record, queue_pass,
                           state_doc, xcheck_submodule)

write = xcheck_submodule("write")
state_mod = xcheck_submodule("state")
views = xcheck_submodule("views")

SESSION_A = "1111111111111111"
SESSION_B = "2222222222222222"
FRESH_CHARTER = "verify the fixes"
FRESH_KEY = xcheck_submodule("util").construal_key("Verifier", FRESH_CHARTER)


class VerbCase(unittest.TestCase):
    """Shared fixture plumbing. `run` goes through the real CLI grammar, so a verb
    that works only when called directly is not passing anything here."""

    def fx(self, doc=None):
        f = Fixture(doc if doc is not None else state_doc(
            findings=[finding_record("F-0001", "reported")],
            queue=[queue_pass("P-01", done=True)]))
        self.addCleanup(f.cleanup)
        return f

    def record(self, fx, fid="F-0001"):
        st = state_mod.load_state(fx.audit)
        for r in list(st.findings) + list(st.class_findings):
            if r.id == fid:
                return r
        self.fail(f"no record {fid}")

    def assertMirrored(self, fx, fid, needle):
        """The regenerated frontmatter carries it too — state and view move together."""
        text = (fx.audit / "findings" / f"{fid}.md").read_text(encoding="utf-8")
        block = text.split("---")[1]
        self.assertIn(needle, block)
        self.assertEqual(views.verify_views(state_mod.load_state(fx.audit), fx.audit), [])


class SetStatus(VerbCase):

    def test_a_legal_transition_lands_in_state_and_in_the_view(self):
        fx = self.fx()
        code, out = fx.run("set-status", "F-0001", "accepted")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.record(fx).status, "accepted")
        self.assertMirrored(fx, "F-0001", "status: accepted")

    def test_a_transition_the_section_5_table_forbids_is_refused(self):
        fx = self.fx()
        code, out = fx.run("set-status", "F-0001", "closed")
        self.assertNotEqual(code, 0)
        self.assertIn("§5 allows only", out)
        self.assertEqual(self.record(fx).status, "reported", "the refusal still wrote")

    def test_a_terminal_status_is_immutable_and_the_message_says_what_to_do(self):
        fx = self.fx(state_doc(findings=[finding_record("F-0001", "closed", next="—")],
                               queue=[queue_pass("P-01", done=True)]))
        code, out = fx.run("set-status", "F-0001", "accepted")
        self.assertNotEqual(code, 0)
        self.assertIn("terminal and immutable", out)
        self.assertIn("recurrence-of", out)

    def test_re_running_an_applied_transition_is_refused_not_repeated(self):
        fx = self.fx()
        self.assertEqual(fx.run("set-status", "F-0001", "accepted")[0], 0)
        code, out = fx.run("set-status", "F-0001", "accepted")
        self.assertNotEqual(code, 0)
        self.assertIn("already 'accepted'", out)

    def test_a_transition_with_provenance_is_sent_to_its_own_verb(self):
        """`set-status F-1 fixed` would mint a fix nobody signed."""
        fx = self.fx(state_doc(findings=[finding_record("F-0001", "planned")],
                               queue=[queue_pass("P-01", done=True)]))
        code, out = fx.run("set-status", "F-0001", "fixed")
        self.assertNotEqual(code, 0)
        self.assertIn("xcheck record-fix", out)


class RecordFix(VerbCase):

    def planned(self):
        return self.fx(state_doc(findings=[finding_record("F-0001", "planned")],
                                 queue=[queue_pass("P-01", done=True)]))

    def test_it_sets_fixed_and_records_which_session_did_it(self):
        fx = self.planned()
        code, out = fx.run("record-fix", "F-0001", "--session", SESSION_A)
        self.assertEqual(code, 0, out)
        rec = self.record(fx)
        self.assertEqual((rec.status, rec.fixed_by), ("fixed", SESSION_A))
        self.assertMirrored(fx, "F-0001", f"fixed-by: {SESSION_A}")

    def test_a_fix_on_a_finding_that_is_not_planned_is_refused(self):
        fx = self.fx()
        code, out = fx.run("record-fix", "F-0001", "--session", SESSION_A)
        self.assertNotEqual(code, 0)
        self.assertIn("only on a 'planned' finding", out)

    def test_provenance_that_identifies_nothing_is_refused(self):
        fx = self.planned()
        code, out = fx.run("record-fix", "F-0001", "--session", "me")
        self.assertNotEqual(code, 0)
        self.assertIn("not a canonical 16-hex session id", out)


class RecordVerdict(VerbCase):

    def fixed(self, by=SESSION_A, attempts=0):
        return self.fx(state_doc(
            findings=[finding_record("F-0001", "fixed", fixed_by=by, attempts=attempts)],
            queue=[queue_pass("P-01", done=True)]))

    def test_a_verdict_from_another_session_closes_the_finding(self):
        fx = self.fixed()
        code, out = fx.run("record-verdict", "F-0001", "--verdict", "closed",
                           "--session", SESSION_B)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.record(fx).status, "closed")
        self.assertMirrored(fx, "F-0001", "status: closed")

    def test_the_fixer_may_not_return_the_verdict_on_its_own_fix(self):
        """The invariant that was prompt-only until this phase."""
        fx = self.fixed(by=SESSION_A)
        code, out = fx.run("record-verdict", "F-0001", "--verdict", "closed",
                           "--session", SESSION_A)
        self.assertNotEqual(code, 0)
        self.assertIn("cannot also return the verdict", out)
        self.assertEqual(self.record(fx).status, "fixed")

    def test_reaching_the_reopen_limit_stops_the_cycle_for_a_human(self):
        fx = self.fixed(attempts=1)                      # default reopen_limit is 2
        code, out = fx.run("record-verdict", "F-0001", "--verdict", "reopened",
                           "--session", SESSION_B)
        self.assertEqual(code, 0, out)
        rec = self.record(fx)
        self.assertEqual((rec.status, rec.attempts, rec.next), ("reopened", 2, "⚠ needs-human"))
        self.assertIn("a human decides", out)

    def test_control_a_reopen_below_the_limit_does_not_flag_a_human(self):
        fx = self.fixed(attempts=0)
        self.assertEqual(fx.run("record-verdict", "F-0001", "--verdict", "reopened",
                                "--session", SESSION_B)[0], 0)
        rec = self.record(fx)
        self.assertEqual((rec.attempts, rec.next), (1, None))



class TheHumanReTakeClearsTheStop(VerbCase):
    """`⚠ needs-human` is a stop, not a decoration. The transition that would
    resume the automatic cycle is exactly where somebody walks past it, so the
    verb takes the human's sanction as an INPUT and resets the counter in the
    same write — a re-take that left `attempts` at the limit would flag again on
    the very next reopen and turn the stop into noise."""

    def stopped(self):
        return self.fx(state_doc(
            findings=[finding_record("F-0001", "reopened", attempts=2,
                                     next="⚠ needs-human")],
            queue=[queue_pass("P-01", done=True)]))

    def test_resuming_without_a_recorded_sanction_is_refused(self):
        fx = self.stopped()
        code, out = fx.run("set-status", "F-0001", "planned")
        self.assertNotEqual(code, 0)
        self.assertIn("--retake human:", out)
        self.assertEqual(self.record(fx).status, "reopened")

    def test_a_bare_retake_value_is_not_a_human(self):
        """`--retake automatic` would be the gate signing its own permission."""
        fx = self.stopped()
        code, out = fx.run("set-status", "F-0001", "planned", "--retake", "automatic")
        self.assertNotEqual(code, 0)
        self.assertIn("--retake human:", out)

    def test_the_sanctioned_retake_resets_attempts_in_the_same_transition(self):
        fx = self.stopped()
        code, out = fx.run("set-status", "F-0001", "planned",
                           "--retake", "human:operator")
        self.assertEqual(code, 0, out)
        rec = self.record(fx)
        self.assertEqual((rec.status, rec.attempts, rec.next), ("planned", 0, None))
        self.assertIn("human:operator", out)

    def test_control_an_unflagged_record_needs_no_sanction(self):
        fx = self.fx(state_doc(
            findings=[finding_record("F-0001", "reopened", attempts=1)],
            queue=[queue_pass("P-01", done=True)]))
        self.assertEqual(fx.run("set-status", "F-0001", "planned")[0], 0)


class AdmitConstrual(VerbCase):

    KEY = None

    def with_construal(self, status="proposed", session=SESSION_A):
        rec = construal_record(status=status, session=session)
        AdmitConstrual.KEY = rec["key"]
        return self.fx(state_doc(findings=[finding_record("F-0001", "reported")],
                                 queue=[queue_pass("P-01", done=True)],
                                 construals=[rec]))

    def test_a_third_party_admits_it(self):
        fx = self.with_construal()
        code, out = fx.run("admit-construal", self.KEY, "--admitter", "human:operator")
        self.assertEqual(code, 0, out)
        c = state_mod.load_state(fx.audit).construals[0]
        self.assertEqual((c.status, c.admitted_by), ("admitted", "human:operator"))

    def test_the_producing_session_may_not_admit_its_own(self):
        fx = self.with_construal(session=SESSION_A)
        code, out = fx.run("admit-construal", self.KEY, "--admitter", SESSION_A)
        self.assertNotEqual(code, 0)
        self.assertIn("may not admit it", out)
        self.assertEqual(state_mod.load_state(fx.audit).construals[0].status, "proposed")


class FileFinding(VerbCase):

    BASE = ("--id", "F-0002", "--title", "a second finding", "--severity", "major",
            "--dimension", "invariants", "--unit", "U01", "--pass", "P-01",
            "--body", "findings/F-0002.md")

    def located(self, doc=None):
        """A fixture that is a repository, plus the args that locate the evidence in it.

        PHASE 9: a finding names a place a reader can go, so the fixture has to BE a
        place — `--locator` is resolved in the recorded subject commit at the write
        boundary, not accepted now and reviewed later.
        """
        fx = self.fx() if doc is None else self.fx(doc)
        fx.git_init()
        loc, sha = fx.locatable()
        return fx, list(self.BASE) + ["--locator", loc, "--source-hash", sha]

    def test_it_files_a_new_finding_at_reported(self):
        fx, args = self.located()
        (fx.audit / "findings" / "F-0002.md").write_text("Evidence.\n", encoding="utf-8")
        code, out = fx.run("file-finding", *args)
        self.assertEqual(code, 0, out)
        rec = self.record(fx, "F-0002")
        self.assertEqual(rec.status, "reported")
        print(f"\n  FILED      {rec.id} locator={rec.locator!r} "
              f"source_hash={rec.source_hash[:12]}…")
        self.assertEqual(args[args.index("--locator") + 1], rec.locator)
        self.assertEqual(args[args.index("--source-hash") + 1], rec.source_hash)
        self.assertMirrored(fx, "F-0002", "id: F-0002")

    def test_filing_an_id_that_already_exists_is_refused(self):
        fx, args = self.located()
        args[1] = "F-0001"
        code, out = fx.run("file-finding", *args)
        self.assertNotEqual(code, 0)
        self.assertIn("already exists", out)

    def test_a_severity_outside_the_section_2_vocabulary_is_refused(self):
        fx, args = self.located()
        args[args.index("--severity") + 1] = "catastrophic"
        code, out = fx.run("file-finding", *args)
        self.assertNotEqual(code, 0)
        self.assertIn("not a canonical §2 severity", out)

    # ---------------------------------------------------------------- phase 9

    def test_a_locator_that_does_not_resolve_is_refused_at_the_write_boundary(self):
        """Refused HERE, not filed and flagged later. 133 findings nobody has
        independently checked is what "flagged later" produces."""
        fx, args = self.located()
        head = fx.locatable()[1]
        for bad, expect in (
                ("no/such/file.py", "does not exist in the subject commit"),
                ("subject.py:9999", "names line 9999"),
                ("/etc/passwd", "not a path inside the subject"),
                # A path may legitimately contain a colon, so this is read as a PATH
                # and refused for not existing — the right answer, and not the one a
                # grammar-shaped refusal would give.
                ("subject.py:not-a-line", "does not exist in the subject commit")):
            with self.subTest(locator=bad):
                a = list(args); a[a.index("--locator") + 1] = bad
                code, out = fx.run("file-finding", *a)
                print(f"  REFUSED    {bad!r} -> {out.strip().splitlines()[0][:96]}")
                self.assertNotEqual(code, 0, out)
                self.assertIn(expect, out)
                # The message names the locator AND the commit it was checked against.
                self.assertIn(bad.split(":")[0], out)
        st = state_mod.load_state(fx.audit)
        self.assertFalse(any(r.id == "F-0002" for r in st.findings),
                         "a refused finding was filed anyway")
        self.assertTrue(head, "the fixture has no committed blob to hash")

    def test_a_source_hash_that_is_not_the_files_hash_is_refused(self):
        """A hash nobody recomputes is decoration. This one is the sha256 of the blob
        at the locator in the recorded subject commit, so a reader can reproduce it."""
        fx, args = self.located()
        args[args.index("--source-hash") + 1] = "0" * 64
        code, out = fx.run("file-finding", *args)
        print(f"  REFUSED    wrong hash -> {out.strip().splitlines()[0][:110]}")
        self.assertNotEqual(code, 0)
        self.assertIn("is not the sha-256 of", out)

    def test_control_the_legitimate_finding_is_accepted_and_carries_both(self):
        """CONTROL for the two refusals above: without it they are satisfied by a verb
        that refuses everything."""
        fx, args = self.located()
        code, out = fx.run("file-finding", *args)
        self.assertEqual(code, 0, out)
        rec = self.record(fx, "F-0002")
        self.assertTrue(rec.locator and rec.source_hash)
        print(f"  CONTROL    accepted: {rec.locator} {rec.source_hash[:12]}…")

    def test_a_commit_the_repository_cannot_see_is_a_refusal_not_a_pass(self):
        """Fails CLOSED. A locator checked against a commit nobody has is a locator
        checked against nothing, and the quiet version of this accepts the finding."""
        fx, args = self.located()
        with mock.patch.dict(os.environ, {"XCHECK_SUBJECT_COMMIT": "a" * 40}):
            code, out = fx.run("file-finding", *args)
        print(f"  UNSEEN     {out.strip().splitlines()[0][:110]}")
        self.assertNotEqual(code, 0)
        self.assertIn("is not present in this repository", out)


class RecordCoverage(VerbCase):

    def undone(self):
        return self.fx(state_doc(findings=[finding_record("F-0001", "reported")],
                                 queue=[queue_pass("P-01", done=False, coverage=False)]))

    def test_it_binds_the_one_canonical_report(self):
        fx = self.undone()
        (fx.audit / "passes" / "P-01-x.md").write_text("**COVERED:** all\n\n"
                                                       "**NOT COVERED:** none\n",
                                                       encoding="utf-8")
        code, out = fx.run("record-coverage", "P-01", "--report", "passes/P-01-x.md",
                           "--findings", "F-0001")
        self.assertEqual(code, 0, out)
        q = state_mod.load_state(fx.audit).queue[0]
        self.assertEqual((q.done, q.coverage.report_path), (True, "passes/P-01-x.md"))

    def test_a_second_report_for_one_pass_is_refused(self):
        """F-0154: coverage synthesised from several partial files is how a pass
        claims to be finished on evidence that was never written."""
        fx = self.fx()                                    # its P-01 already has coverage
        code, out = fx.run("record-coverage", "P-01", "--report", "passes/other.md")
        self.assertNotEqual(code, 0)
        self.assertIn("exactly ONE canonical record", out)

    def test_certifying_a_finding_nobody_filed_is_refused(self):
        fx = self.undone()
        code, out = fx.run("record-coverage", "P-01", "--report", "passes/P-01-x.md",
                           "--findings", "F-9999")
        self.assertNotEqual(code, 0)
        self.assertIn("F-9999", out)


class TypedRefusalAndTheNormGate(VerbCase):
    """The two §5/§8 records that move NO status. They are the easiest thing to
    leave as a hand-edit — nothing in the lifecycle appears to change — which is
    exactly why they need a verb: after phase 6 that edit writes nothing."""

    BODY = ("# F-0001\n\n## Refusal\n\nThe unit named in the charter is not in "
            "this checkout; supply it or narrow the charter.\n")

    def accepted(self, **over):
        fx = self.fx(state_doc(findings=[finding_record("F-0001", "accepted", **over)],
                               queue=[queue_pass("P-01", done=True)]))
        return fx

    def test_a_refusal_records_the_reason_and_leaves_the_status_alone(self):
        fx = self.accepted()
        fx.body("findings/F-0001.md", self.BODY)
        code, out = fx.run("record-refusal", "F-0001", "--reason", "material-missing")
        self.assertEqual(code, 0, out)
        rec = self.record(fx)
        self.assertEqual((rec.status, rec.refusal), ("accepted", "material-missing"))
        self.assertMirrored(fx, "F-0001", "refusal: material-missing")

    def test_a_reason_code_with_no_prose_half_is_refused(self):
        """A category name is not an obstacle anyone can act on (§5, both halves)."""
        fx = self.accepted()
        fx.body("findings/F-0001.md", "# F-0001\n\n## Evidence\n\nquote.\n")
        code, out = fx.run("record-refusal", "F-0001", "--reason", "material-missing")
        self.assertNotEqual(code, 0)
        self.assertIn("write the prose half FIRST", out)
        self.assertIsNone(self.record(fx).refusal)

    def test_free_text_is_not_a_reason(self):
        fx = self.accepted()
        code, out = fx.run("record-refusal", "F-0001", "--reason", "it-was-hard")
        self.assertNotEqual(code, 0)
        self.assertIn("closed §5 vocabulary", out)

    def test_the_norm_gate_is_fail_closed_until_a_human_rules(self):
        cf = {"id": "CF-0001", "title": "a class", "severity": "major",
              "status": "planned", "dimension": "invariants", "unit": ["U01"],
              "members": ["F-0001"], "attempts": 0, "updated": "2026-08-14",
              "body_path": "findings/CF-0001.md", "pass": "P-01"}
        fx = self.fx(state_doc(
            findings=[finding_record("F-0001", "superseded-by-class", **{"class": "CF-0001"})],
            class_findings=[cf], queue=[queue_pass("P-01", done=True)]))
        self.assertEqual(fx.run("block-on-norm", "CF-0001")[0], 0)
        code, out = fx.run("record-fix", "CF-0001", "--session", SESSION_A)
        self.assertNotEqual(code, 0, out)
        self.assertIn("norm-ratification", out)
        code, out = fx.run("record-ruling", "CF-0001", "--norm", "N1",
                           "--ruled-by", "the-agent-decided")
        self.assertNotEqual(code, 0)
        self.assertIn("ruled by the norm's OWNER, who is a human", out)
        code, out = fx.run("record-ruling", "CF-0001", "--norm", "N1-over-N4",
                           "--ruled-by", "human:norm-owner")
        self.assertEqual(code, 0, out)
        self.assertEqual(fx.run("record-fix", "CF-0001", "--session", SESSION_A)[0], 0)


class TheConstrualSchemaHasAWriter(VerbCase):
    """Every field of `CONSTRUAL_SCHEMA` is written by one of the two construal
    verbs. Until 0.8 this was checked against `templates/construal.md`'s
    frontmatter — a schema field that never reached the template was drift. That
    template has no block any more, so the claim is made against the WRITERS: a
    field the schema declares and no verb sets is a field that can only ever be
    filled by a hand-edit, which now writes nothing."""

    def test_propose_then_admit_fills_the_whole_schema(self):
        util = xcheck_submodule("util")
        charter = "finding F-0001"
        key = util.construal_key("Remediator", charter)
        fx = self.fx(state_doc(findings=[finding_record("F-0001", "accepted")],
                               queue=[queue_pass("P-01", done=True)]))
        fx.write("construals/%s.md" % key, "## Task frame\n\nMy reading.\n")
        code, out = fx.run("propose-construal", key, "--role", "Remediator",
                           "--charter", charter, "--session", SESSION_A,
                           "--body", "construals/%s.md" % key)
        self.assertEqual(code, 0, out)
        code, out = fx.run("admit-construal", key, "--admitter", "human:operator")
        self.assertEqual(code, 0, out)
        rec = json.loads((fx.audit / "state.json").read_text())["construals"][0]
        written = {k for k, v in rec.items() if v is not None}
        declared = {f.replace("-", "_") for f in util.CONSTRUAL_SCHEMA}
        self.assertEqual(declared - written, set(),
                         "a schema field no verb writes can only be hand-edited, "
                         "and a hand-edit now records nothing")
        print("\nconstrual schema fields written by the verbs: "
              + ", ".join(sorted(declared)))


class NoVerbWritesAnywhereElse(unittest.TestCase):
    """Phase 5's counterfactual, applied to the write side. With `write_state`
    stubbed to raise, a verb that still reports success is reaching the document
    through a second path — which is the whole defect this phase closes."""

    def setUp(self):
        self.saved = write.write_state

        def poison(*_a, **_kw):
            raise RuntimeError("write_state was called")

        write.write_state = poison
        self.addCleanup(lambda: setattr(write, "write_state", self.saved))

    def test_every_verb_fails(self):
        cases = {
            "set-status": ("F-0001", "accepted"),
            "record-fix": ("F-0002", "--session", SESSION_A),
            "record-verdict": ("F-0003", "--verdict", "closed", "--session", SESSION_B),
            "admit-construal": (None, "--admitter", "human:operator"),
            # PHASE 9: this verb now validates its locator against the subject commit
            # BEFORE it writes, so the fixture below is `git_init`-ed and these two
            # args are filled in from it — a poisoned `write_state` must be what this
            # verb dies of, not a missing locator.
            "file-finding": ("--id", "F-0009", "--title", "t", "--severity", "major",
                             "--dimension", "invariants", "--unit", "U01",
                             "--pass", "P-01", "--body", "findings/F-0009.md",
                             "--locator", "@LOCATOR@", "--source-hash", "@SHA@"),
            "record-coverage": ("P-02", "--report", "passes/P-02-x.md"),
            "propose-construal": (FRESH_KEY, "--role", "Verifier",
                                  "--charter", FRESH_CHARTER, "--session", SESSION_B,
                                  "--body", "construals/%s.md" % FRESH_KEY),
            "record-plan": ("RP-0001", "--findings", "F-0001",
                            "--body", "plans/RP-0001.md"),
            "queue-pass": ("P-09", "--dimension", "invariants", "--units", "U01",
                           "--charter", "c", "--stop", "s"),
            "record-refusal": ("F-0001", "--reason", "material-missing"),
            "block-on-norm": ("CF-0001",),
            "record-ruling": ("CF-0002", "--norm", "N1", "--ruled-by", "human:owner"),
            # 0.9.0 phase 3 — the admin verbs take the same one path. The pass they
            # touch is P-02 (queued, no coverage): P-01 is done here, and a done pass
            # is refused BEFORE the writer, which would measure nothing.
            "set-limit": ("reopen_limit", "3"),
            "amend-pass": ("P-02", "--charter", "a narrower charter"),
            "cancel-pass": ("P-02",),
            "update-catalog": ("unit", "U09", "--material", "src/new.c",
                               "--size", "400 lines", "--responsibility", "the new one"),
        }
        con = construal_record(status="proposed", session=SESSION_A)
        cf = dict(id="CF-0001", title="a class", severity="major", status="planned",
                  dimension="invariants", unit=["U01"], members=["F-0004"], attempts=0,
                  updated="2026-08-14", body_path="findings/CF-0001.md", **{"pass": "P-01"})
        cf2 = dict(cf, id="CF-0002", body_path="findings/CF-0002.md",
                   members=["F-0005"], blocked="norm-ratification", norm_ruling="pending")
        fx = Fixture(state_doc(
            findings=[finding_record("F-0001", "reported"),
                      finding_record("F-0002", "planned"),
                      finding_record("F-0003", "fixed", fixed_by=SESSION_A),
                      finding_record("F-0004", "superseded-by-class", **{"class": "CF-0001"}),
                      finding_record("F-0005", "superseded-by-class", **{"class": "CF-0002"})],
            class_findings=[cf, cf2],
            queue=[queue_pass("P-01", done=True),
                   queue_pass("P-02", done=False, coverage=False)],
            construals=[con]))
        self.addCleanup(fx.cleanup)
        (fx.audit / "findings" / "F-0009.md").write_text("Evidence.\n", encoding="utf-8")
        fx.git_init()
        loc, sha = fx.locatable()
        cases["file-finding"] = tuple(
            {"@LOCATOR@": loc, "@SHA@": sha}.get(a, a) for a in cases["file-finding"])
        fx.body("findings/F-0001.md", "# F-0001\n\n## Refusal\n\nThe unit is absent.\n")
        outcomes = {}
        for verb, argv in cases.items():
            argv = tuple(con["key"] if a is None else a for a in argv)
            try:
                code, out = fx.run(verb, *argv)
            except RuntimeError as e:
                # Reaching the poison IS the pass: the verb went through the one
                # writer. A verb that never got there would return 0 below.
                outcomes[verb] = f"reached the stub ({e})"
                continue
            self.assertNotEqual(code, 0, f"{verb} succeeded with write_state stubbed")
            outcomes[verb] = out.strip().splitlines()[-1][:70]
        print("\nno verb writes anywhere else — write_state stubbed to raise:")
        for verb, why in outcomes.items():
            print(f"  {verb:<16} {why}")
        self.assertEqual(len(outcomes), len(cases))
        # Reaching the stub is the ONLY pass. Any other non-zero exit — a drift
        # refusal, a bad fixture — would satisfy "the verb failed" while proving
        # nothing about which path it takes to the document.
        not_reached = sorted(v for v, why in outcomes.items()
                             if not why.startswith("reached the stub"))
        self.assertEqual(not_reached, [],
                         f"these verbs failed BEFORE the one write path, so this "
                         f"counterfactual measured nothing for them: {not_reached}")
        self.assertEqual(set(cases) | {"render-views"}, set(write.VERBS),
                         "a verb exists that this counterfactual never exercises — the one path is only proven for the verbs actually run here")


if __name__ == "__main__":
    unittest.main()
