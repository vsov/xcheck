"""0.9.0 phase 3: the four admin verbs, so no operational flow needs a hand-edit.

README §13 used to say, in the project's own words, that there was no write verb for
a limit and the operator should change it in `state.json` and re-render. Every other
sentence in the project says machine state changes through verbs; that one sentence
made "all changes go through verbs" aspirational, and it did so for the setting that
gates the mandatory human stop.

A hand-edit is not merely untidy. It walks around, in one move: the writing lock, the
atomic write, the `state_revision` bump, the appended event, and immediate schema
validation. The four verbs here have all five, because they go through the same
`_apply` as the other thirteen.

Deliberately typed, not a generic `edit-state`. A verb that can express any change
cannot refuse an illegal one by name — and every test below is an assertion about a
refusal naming its condition AND its remedy.
"""

import json
import unittest


from tests.harness import (    REPO, Fixture, finding_record, queue_pass, state_doc, xcheck_submodule,
)

state = xcheck_submodule("state")
write = xcheck_submodule("write")


def coverage_for(pid="P-01"):
    return {"report_path": f"passes/{pid}-report.md", "findings": [],
            "updated": "2026-08-14", "status": "done"}


class AdminVerbCase(unittest.TestCase):
    """One fixture per test; every verb runs through the REAL CLI, not `_VERB_FN`.
    Calling the function directly would prove the function works while leaving open
    the question the audit actually asks: is it reachable, and does it transact."""

    def setUp(self):
        self.fx = Fixture(doc=self.doc())
        self.addCleanup(self.fx.cleanup)

    def doc(self):
        return state_doc(findings=[finding_record("F-0001")],
                         queue=[queue_pass("P-01"), queue_pass("P-02")])

    def run_verb(self, *argv):
        """(exit code, printed output or the refusal message)."""
        from xcheck.cli import main
        import contextlib, io
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                return (main(["xcheck", "--project", str(self.fx.root), *argv]) or 0,
                        buf.getvalue().strip())
        except SystemExit as e:
            return (1 if not isinstance(e.code, int) else e.code,
                    buf.getvalue().strip() + str(e.code))

    def state(self):
        return state.load_state(self.fx.root / "audit")

    def events(self):
        p = self.fx.root / "audit" / "events.jsonl"
        if not p.is_file():
            return []
        return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln]

    def refuses(self, *argv, naming):
        code, out = self.run_verb(*argv)
        self.assertNotEqual(code, 0, f"{argv} was expected to refuse, but it applied: {out}")
        for needle in naming:
            self.assertIn(needle, out,
                          f"the refusal for {argv} does not name {needle!r} — an operator "
                          f"cannot act on it. Got: {out}")
        print(f"  REFUSED {' '.join(argv[:3]):<38} {out[:180]}")
        return out


class SetLimitTransacts(AdminVerbCase):

    def test_one_invocation_locks_bumps_renders_and_emits(self):
        """Criterion 1, all four properties of ONE invocation."""
        before = self.state()
        code, out = self.run_verb("set-limit", "reopen_limit", "3")
        after = self.state()
        ledger = (self.fx.root / "audit" / "LEDGER.md")
        lock = (self.fx.root / "audit" / ".lock")
        events = [e for e in self.events() if e.get("event") == "state_transition"]

        print(f"\n[admin] {out}")
        print(f"[admin] revision {before.state_revision} -> {after.state_revision}; "
              f"event verb={events[-1]['verb']} target={events[-1]['target']} "
              f"rev={events[-1]['state_revision']}; LEDGER.md rendered={ledger.is_file()}; "
              f"lock released={not lock.exists()}")

        self.assertEqual(code, 0, out)
        self.assertEqual(after.state_revision, before.state_revision + 1,
                         "state_revision did not move by exactly 1")
        self.assertEqual(after.limits["reopen_limit"], 3)
        self.assertTrue(ledger.is_file(), "views were not re-rendered")
        self.assertFalse(lock.exists(), "the lock was not released")
        self.assertEqual(events[-1]["verb"], "set-limit")
        self.assertEqual(events[-1]["target"], "reopen_limit")
        self.assertEqual(events[-1]["state_revision"], after.state_revision)

    def test_the_three_refusals(self):
        """Criterion 2. The out-of-range case is `reopen_limit 0` on purpose: that
        key's lower bound is the mandatory human stop, not a preference."""
        print()
        self.refuses("set-limit", "reopen_limitt", "3",
                     naming=["reopen_limitt", "class_threshold", "reopen_limit"])
        self.refuses("set-limit", "reopen_limit", "2.5",
                     naming=["whole number", "2.5"])
        self.refuses("set-limit", "reopen_limit", "0",
                     naming=["outside the legal range 1..99",
                             "mandatory human stop after repeated failures never fires"])
        self.assertEqual(self.state().state_revision, 1,
                         "a refused verb still moved the state")
        self.assertEqual([e for e in self.events() if e.get("event") == "state_transition"],
                         [], "a refused verb emitted an event — the stream would then "
                              "record a change that never happened")

    def test_int_lookalikes_are_refused(self):
        """`int()` accepts `' 2 '` and reads `'2_0'` as twenty. A limit that silently
        means something other than what was typed is the wrong surprise at a gate."""
        print()
        for bad in ("2_0", "0x2", "two", "", "+-1"):
            self.refuses("set-limit", "reopen_limit", bad, naming=["whole number"])

    def test_every_known_key_is_settable(self):
        for key, (lo, hi) in state.LIMIT_FIELDS.items():
            with self.subTest(key):
                code, out = self.run_verb("set-limit", key, str(hi))
                self.assertEqual(code, 0, out)
                self.assertEqual(self.state().limits[key], hi)


class AmendPassChangesOnlyAQueuedPass(AdminVerbCase):

    def test_all_four_fields_change(self):
        code, out = self.run_verb("amend-pass", "P-02", "--charter", "a narrower charter",
                                  "--stop", "8 findings", "--units", "U01",
                                  "--dimension", "invariants")
        q = next(q for q in self.state().queue if q.id == "P-02")
        print(f"\n[admin] {out}")
        self.assertEqual(code, 0, out)
        self.assertEqual(q.charter, "a narrower charter")
        self.assertEqual(q.stop, "8 findings")
        self.assertEqual(q.units, ("U01",))
        self.assertEqual(q.dimension, "invariants")

    def test_a_done_pass_is_refused_and_the_report_is_named(self):
        doc = self.doc()
        doc["queue"][1]["done"] = True
        doc["queue"][1]["coverage"] = coverage_for("P-02")
        self.fx.write_state(doc)
        print()
        self.refuses("amend-pass", "P-02", "--charter", "rewriting history",
                     naming=["passes/P-02-report.md", "done",
                             "describe work nobody did", "xcheck queue-pass"])

    def test_an_empty_amendment_is_refused(self):
        print()
        self.refuses("amend-pass", "P-01", naming=["nothing to change", "--charter"])

    def test_an_unknown_pass_names_the_queue(self):
        print()
        self.refuses("amend-pass", "P-99", "--stop", "x",
                     naming=["P-99", "P-01", "xcheck queue-pass"])

    def test_an_uncatalogued_dimension_and_unit_are_refused(self):
        print()
        self.refuses("amend-pass", "P-01", "--dimension", "not-a-dimension",
                     naming=["not-a-dimension", "xcheck update-catalog dimension"])
        self.refuses("amend-pass", "P-01", "--units", "U99",
                     naming=["U99", "xcheck update-catalog unit"])


class CancelPassRemovesOnlyAnUnreferencedQueuedPass(AdminVerbCase):

    def test_a_queued_pass_is_removed(self):
        code, out = self.run_verb("cancel-pass", "P-02")
        print(f"\n[admin] {out}")
        self.assertEqual(code, 0, out)
        self.assertEqual([q.id for q in self.state().queue], ["P-01"])

    def test_a_pass_with_findings_is_refused_and_they_are_named(self):
        print()
        self.refuses("cancel-pass", "P-01",
                     naming=["F-0001", "pointing at a pass that does not exist"])
        self.assertEqual([q.id for q in self.state().queue], ["P-01", "P-02"])

    def test_a_done_pass_is_refused(self):
        doc = self.doc()
        doc["queue"][1]["done"] = True
        doc["queue"][1]["coverage"] = coverage_for("P-02")
        self.fx.write_state(doc)
        print()
        self.refuses("cancel-pass", "P-02", naming=["passes/P-02-report.md", "done"])


class UpdateCatalogAddsAmendsAndRefusesADanglingRemoval(AdminVerbCase):

    def test_a_norm_a_dimension_and_a_unit_are_added_then_amended(self):
        print()
        adds = [
            ("norm", "N9", ["--source", "RFC 9110 §9.2.2", "--scope", "idempotence"]),
            ("dimension", "concurrency", ["--catches", "races and lost updates",
                                          "--norms", "N9"]),
            ("unit", "U09", ["--material", "src/new.c", "--size", "400 lines",
                             "--responsibility", "the new thing"]),
        ]
        for kind, key, opts in adds:
            code, out = self.run_verb("update-catalog", kind, key, *opts)
            self.assertEqual(code, 0, out)
            print(f"  ADDED   {out}")
        st = self.state()
        self.assertIn("N9", [n.id for n in st.catalogs.norms])
        self.assertIn("concurrency", [d.key for d in st.catalogs.dimensions])
        self.assertIn("U09", [u.id for u in st.catalogs.units])
        self.assertEqual(next(d for d in st.catalogs.dimensions
                              if d.key == "concurrency").norms, ("N9",))

        amends = [("norm", "N9", ["--scope", "idempotence and retries"]),
                  ("dimension", "concurrency", ["--catches", "races only"]),
                  ("unit", "U09", ["--size", "420 lines"])]
        for kind, key, opts in amends:
            code, out = self.run_verb("update-catalog", kind, key, *opts)
            self.assertEqual(code, 0, out)
            print(f"  AMENDED {out}")
        st = self.state()
        self.assertEqual(next(n for n in st.catalogs.norms if n.id == "N9").scope,
                         "idempotence and retries")
        self.assertEqual(next(u for u in st.catalogs.units if u.id == "U09").size,
                         "420 lines")

    def test_removing_a_referenced_entry_is_refused_and_the_holders_are_named(self):
        print()
        self.refuses("update-catalog", "unit", "U01", "--remove",
                     naming=["U01", "F-0001", "pass P-01", "make the audit unreadable"])
        self.refuses("update-catalog", "dimension", "invariants", "--remove",
                     naming=["invariants", "F-0001", "pass P-01"])

    def test_an_unreferenced_entry_is_removable(self):
        self.run_verb("update-catalog", "unit", "U09", "--material", "src/new.c",
                      "--size", "400 lines", "--responsibility", "the new thing")
        code, out = self.run_verb("update-catalog", "unit", "U09", "--remove")
        print(f"\n[admin] {out}")
        self.assertEqual(code, 0, out)
        self.assertNotIn("U09", [u.id for u in self.state().catalogs.units])

    def test_a_partial_add_is_refused_field_by_field(self):
        print()
        self.refuses("update-catalog", "unit", "U09", "--material", "src/new.c",
                     naming=["--size", "--responsibility", "does not exist yet"])
        self.refuses("update-catalog", "gizmo", "G1",
                     naming=["gizmo", "dimension, norm, unit"])


class TheStateRoundTripsAfterEveryVerb(AdminVerbCase):
    """Criterion 7. `_apply` re-validates before writing, but "the write succeeded"
    is not the same claim as "the document on disk loads". This asserts the second."""

    def test_all_four_verbs_leave_a_loadable_document(self):
        seq = [
            ("set-limit", "reopen_limit", "4"),
            ("amend-pass", "P-02", "--charter", "narrower"),
            ("update-catalog", "unit", "U09", "--material", "m", "--size", "s",
             "--responsibility", "r"),
            ("cancel-pass", "P-02"),
        ]
        revisions = []
        for argv in seq:
            code, out = self.run_verb(*argv)
            self.assertEqual(code, 0, out)
            st = state.load_state(self.fx.root / "audit")   # raises on an invalid doc
            revisions.append(st.state_revision)
        print(f"\n[admin] four verbs, revisions {revisions}, document loads after each")
        self.assertEqual(revisions, [2, 3, 4, 5])


class TheVerbTableIsTheOneDeclaration(unittest.TestCase):

    def test_seventeen_verbs_and_every_one_has_an_implementation(self):
        print(f"\n[admin] len(write.VERBS) = {len(write.VERBS)}")
        self.assertEqual(len(write.VERBS), 17)
        for name in write.VERBS:
            if name == "render-views":       # dispatched to cli.cmd_render_views
                continue
            self.assertIn(name, write._VERB_FN, f"{name} is declared but not implemented")
        for name in write._VERB_FN:
            self.assertIn(name, write.VERBS, f"{name} is implemented but not declared")

    def test_help_is_generated_from_the_table(self):
        from xcheck.cli import main
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                main(["xcheck", "--help"])
            except SystemExit:
                pass
        text = buf.getvalue()
        missing = [n for n in write.VERBS if f"xcheck {n}" not in text]
        print(f"[admin] --help lists {len(write.VERBS) - len(missing)}/{len(write.VERBS)} verbs")
        self.assertEqual(missing, [], "help is not generated from VERBS")
        for n in ("set-limit", "amend-pass", "cancel-pass", "update-catalog"):
            self.assertIn(n, text)


class NoShippedDocumentInstructsAHandEdit(unittest.TestCase):
    """Criterion 8. The phrasing check is a PROXY — phase 4 replaces it with a
    structural one that resolves every documented action to a name in `write.VERBS`.
    It is here now because the sentence it forbids is the one this phase deleted, and
    a deleted sentence that nothing guards comes back."""

    DOCS = ["README.md", "XCHECK.md"]
    GLOBS = ["skills/*/SKILL.md", "templates/*.md"]

    # Instruction shapes, not the mere co-occurrence of "edit" and "state.json":
    # every shipped doc SHOULD say "never hand-edit a view", and a blunt substring
    # search would flag exactly the sentences that are doing the right thing.
    FORBIDDEN = [
        "change it in `state.json`",
        "edit `state.json`",
        "edit state.json",
        "editing `state.json`",
        "editing state.json",
        "edit it in `state.json`",
        "by editing the state file",
    ]

    def shipped(self):
        out = [REPO / d for d in self.DOCS]
        for g in self.GLOBS:
            out += sorted(REPO.glob(g))
        return [p for p in out if p.is_file()]

    def test_no_shipped_document_tells_anyone_to_edit_machine_state(self):
        hits = []
        for p in self.shipped():
            low = p.read_text(encoding="utf-8").lower()
            for phrase in self.FORBIDDEN:
                if phrase in low:
                    hits.append(f"{p.relative_to(REPO)}: {phrase!r}")
        print(f"\n[admin] {len(self.shipped())} shipped documents scanned for "
              f"{len(self.FORBIDDEN)} hand-edit instruction shapes: {len(hits)} hit(s)")
        for h in hits:
            print(f"  {h}")
        self.assertEqual(hits, [])

    def test_the_readme_points_at_the_verb(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertIn("xcheck set-limit reopen_limit 3", readme,
                      "§13 does not show the verb that replaced the hand-edit")
        self.assertNotIn("There is no write verb for a limit", readme)

    def test_the_detector_would_catch_the_sentence_that_was_removed(self):
        """The scanner's own control arm: the exact sentence deleted from §13 must
        come back as a hit. A detector never shown detecting is not evidence."""
        removed = ("There is no write verb for a limit: change it in `state.json` — "
                   "which IS the authority — and run `xcheck render-views`:")
        low = removed.lower()
        caught = [p for p in self.FORBIDDEN if p in low]
        print(f"[admin] positive control: the removed §13 sentence matches "
              f"{len(caught)} forbidden shape(s): {caught}")
        self.assertTrue(caught, "the scanner would not have caught the sentence this "
                                "phase removed, so it guards nothing")


if __name__ == "__main__":
    unittest.main()
