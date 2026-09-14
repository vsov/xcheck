"""Candidate classes over the real corpus, and the discrimination that makes them one.

133 findings and 0 class findings is a list. But the failure mode of a clusterer is not
"too few classes" — it is a clusterer nobody can argue with, so what is asserted here is
mostly the ways the grouping could be meaningless:

  1. the fingerprint is deterministic — a lexicon, a regex and a sort, with no model and
     no network, asserted over the module's AST rather than its prose;
  2. two findings that share a ROOT CAUSE in different units ARE grouped, and two in the
     same unit with different causes are NOT — a fingerprint keyed on where the file
     lives is a directory listing;
  3. the fingerprint survives a rename that does not change the defect, and does not
     survive a change that does;
  4. the UNGROUPED count is part of the result: a clusterer that groups everything has
     learnt nothing, and this one leaves 120 of 133 alone;
  5. the detector cannot write. It cannot create a class finding and cannot set
     `superseded-by-class` — asserted by attempting both and by the structural fact that
     it imports nothing that writes.
"""

import ast
import unittest
from pathlib import Path

from tests.harness import (needs_live_corpus, require_live_corpus, REPO, Fixture, finding_record, queue_pass, state_doc,
                           xcheck_submodule)

classes = xcheck_submodule("classes")
okf = xcheck_submodule("okf")
state_mod = xcheck_submodule("state")

# PHASE 15 arms read the live corpus repeatedly; `live()` re-parses 133 evidence files
# each call, so it is bound once here.
REAL = None

BODY_RUNNER = "Material anchors:\n\n- `xcheck/runner.py:120-140` shows it.\n"
BODY_STATE = "Material anchors:\n\n- `xcheck/state.py:12` shows it.\n"
BODY_DOC = "Material anchors:\n\n- `docs/SECURITY.md:10` shows it.\n"


def live():
    require_live_corpus()
    global REAL
    if REAL is None:
        REAL = state_mod.load_state(Path(REPO, "audit"))
    return REAL


_EXPORTED = None


def exported():
    """The projection `write_bundle` receives — the ONE table a proposal may cite.

    PHASE 9 (fifth audit): `proposals_for` used to take the findings and mint an
    OBSERVATION record per member, whose ids the bundle never exported. It now takes the
    records themselves, so a test that hands it anything else is testing a shape
    production does not build."""
    global _EXPORTED
    if _EXPORTED is None:
        envelope = xcheck_submodule("envelope")
        _EXPORTED = okf.project_bundle(live(), envelope.read_events(REPO))
    return _EXPORTED


class TheRealCorpus(unittest.TestCase):
    """The phase's own evidence, run on this repository's actual findings."""

    @classmethod
    def setUpClass(cls):
        cls.state = live()
        cls.classes, cls.ungrouped = classes.candidates(cls.state, REPO)

    def test_the_candidate_classes_are_printed_with_their_members(self):
        self.assertEqual(133, len(self.state.findings), "the corpus moved")
        print(f"\n=== CANDIDATE CLASSES over {len(self.state.findings)} findings ===")
        for c in self.classes:
            print(f"[{c['size']:2}] {c['root_cause']}")
            print(f"     norms={list(c['norm'])}  evidence={list(c['evidence'])}")
            print(f"     units={c['units']}  members={c['members']}")
        self.assertTrue(self.classes, "no candidate class at all")
        for c in self.classes:
            self.assertGreaterEqual(c["size"], 3)

    def test_the_ungrouped_count_is_part_of_the_result(self):
        grouped = sum(c["size"] for c in self.classes)
        self.assertEqual(len(self.state.findings), grouped + len(self.ungrouped))
        # The load-bearing assertion of this module: a clusterer that grouped everything
        # would pass every other test here and have learnt nothing.
        self.assertGreater(len(self.ungrouped), len(self.state.findings) // 2,
                           "most findings grouped — this is a clusterer that has learnt "
                           "nothing, not a corpus with a dozen classes in it")
        print(f"\nGROUPED {grouped}   UNGROUPED {len(self.ungrouped)}   "
              f"CLASSES {len(self.classes)}")

    def test_a_class_crosses_units_which_is_the_whole_point(self):
        crossing = [c for c in self.classes if len(c["units"]) > 1]
        self.assertTrue(crossing, "every candidate sits in one unit — that is a "
                                  "directory listing, not a class")
        print("\nCROSSES UNITS: " + "; ".join(
            f"{c['root_cause']} over {c['units']}" for c in crossing))

    def test_the_mechanism_census_shows_no_single_bucket_swallowing_the_corpus(self):
        census = classes.mechanism_census(self.state)
        biggest = max(v for k, v in census.items() if k is not None)
        print("\nMECHANISM CENSUS")
        for k, v in census.most_common():
            print(f"  {str(k):42} {v}")
        self.assertLess(biggest, len(self.state.findings) // 3,
                        "one mechanism claimed a third of the corpus — the lexicon is "
                        "matching a word, not a defect")


class TheGroupingDiscriminates(unittest.TestCase):
    """The two cases the phase names, and they pull in opposite directions."""

    def catalogs(self):
        return live().catalogs

    def fp(self, title, dimension, unit, body):
        # A plain stand-in, not a state record: `fingerprint` reads exactly three
        # attributes, and building a whole frozen state per case would hide which
        # three by making every field available.
        class R:
            pass
        r = R()
        r.title, r.dimension, r.unit = title, dimension, [unit]
        return classes.fingerprint(r, body, self.catalogs())

    def test_same_root_cause_in_different_units_is_grouped(self):
        a = self.fp("The loader silently discards a declared key", "state-authority",
                    "U12", BODY_RUNNER)
        b = self.fp("The parser silently discards a declared row", "state-authority",
                    "U19", BODY_RUNNER)
        self.assertNotEqual(a["unit"], b["unit"])
        self.assertEqual(classes.group_key(a), classes.group_key(b))
        print(f"\nGROUPED     units {a['unit']} vs {b['unit']}, same cause "
              f"`{a['root_cause']}` -> one key")

    def test_same_unit_with_different_root_causes_is_not_grouped(self):
        a = self.fp("The loader silently discards a declared key", "state-authority",
                    "U12", BODY_RUNNER)
        b = self.fp("The loader cannot record a declared key", "state-authority",
                    "U12", BODY_RUNNER)
        self.assertEqual(a["unit"], b["unit"])
        self.assertNotEqual(a["root_cause"], b["root_cause"])
        self.assertNotEqual(classes.group_key(a), classes.group_key(b))
        print(f"NOT GROUPED same unit {a['unit']}, causes `{a['root_cause']}` vs "
              f"`{b['root_cause']}` -> two keys")

    def test_unit_is_carried_but_never_keyed_on(self):
        self.assertEqual(("root_cause", "norm", "evidence"), classes.GROUP_KEY)
        self.assertNotIn("unit", classes.GROUP_KEY)
        self.assertIn("unit", self.fp("x cannot y", "state-authority", "U12", ""))


class TheFingerprintIsStableWhereItShouldBe(unittest.TestCase):

    def catalogs(self):
        return live().catalogs

    def fp(self, title, body):
        class R:
            pass
        r = R()
        r.title, r.dimension, r.unit = title, "state-authority", ["U12"]
        return classes.fingerprint(r, body, self.catalogs())

    def test_a_rename_that_does_not_change_the_defect_leaves_it_unchanged(self):
        before = self.fp("The loader cannot record a key",
                         "Material anchors:\n- `xcheck/runner.py:120` shows it.\n")
        after = self.fp("The loader cannot record a key",
                        "Material anchors:\n- `xcheck/orchestrator.py:400` shows it.\n")
        self.assertEqual(classes.group_key(before), classes.group_key(after))
        print(f"\nSTABLE      xcheck/runner.py -> xcheck/orchestrator.py, "
              f"shape {list(before['evidence'])} unchanged")

    def test_a_change_that_does_change_the_defect_changes_it(self):
        cause = self.fp("The loader cannot record a key", BODY_RUNNER)
        other = self.fp("The loader silently discards a key", BODY_RUNNER)
        moved = self.fp("The loader cannot record a key", BODY_DOC)
        self.assertNotEqual(classes.group_key(cause), classes.group_key(other))
        self.assertNotEqual(classes.group_key(cause), classes.group_key(moved))
        print(f"UNSTABLE    different mechanism -> different key; "
              f"{list(cause['evidence'])} -> {list(moved['evidence'])} -> different key")

    def test_an_unclassified_finding_groups_with_nothing_including_its_own_kind(self):
        a = self.fp("Directory fsync fails after state commit", BODY_RUNNER)
        b = self.fp("Cancel crashes on a declared legacy lock", BODY_RUNNER)
        self.assertIsNone(a["root_cause"])
        self.assertIsNone(classes.group_key(a))
        self.assertIsNone(classes.group_key(b))
        print("UNCLASSIFIED two findings with no mechanism -> no key at all "
              "(\"we could not tell\" is not a shared cause)")


class TheDetectorCannotDecide(unittest.TestCase):

    def test_it_cannot_create_a_class_finding(self):
        with self.assertRaises(PermissionError) as caught:
            okf.apply_effect("open-finding", "CF-0001")
        print(f"\nREFUSED     create a class finding -> {str(caught.exception)[:110]}")

    def test_it_cannot_set_superseded_by_class(self):
        with self.assertRaises(PermissionError) as caught:
            okf.apply_effect("move-status", "F-0115", "superseded-by-class")
        print(f"REFUSED     set superseded-by-class -> {str(caught.exception)[:110]}")

    def test_it_imports_nothing_that_writes(self):
        """Structural, and stronger than the two attempts above: the detector holds no
        capability to write, so there is no path to refuse in the first place."""
        tree = ast.parse(Path(xcheck_submodule("classes").__file__).read_text("utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.update(f"{node.module}.{a.name}" for a in node.names)
        for writer in ("xcheck.write", "xcheck.state.write_state", "write", "subprocess",
                       "urllib", "socket"):
            self.assertNotIn(writer, imported, f"the detector imports {writer}")
        print(f"NO CAPABILITY imports={sorted(imported)} — nothing that writes, "
              f"no model, no network")

    def test_its_output_is_proposals_that_name_a_verb_for_a_human(self):
        st = live()
        cands, _ung = classes.candidates(st, REPO)
        props = classes.proposals_for(cands, exported())
        self.assertEqual(len(cands), len(props))
        for p in props:
            self.assertEqual("proposed", p["status"])
            self.assertIn("changes nothing", p["note"])
            self.assertIn("CANDIDATE, not a class", p["reason"])
        print(f"\nPROPOSALS   {len(props)}, each status={props[0]['status']!r} "
              f"verb={props[0]['verb']!r}")
        print(f"            e.g. {props[0]['reason'][:150]}")


# ------------------------------------------------------------------------ PHASE 15
#
# The four classes above were found, printed by this test module, and read by nobody
# else: `classes.candidates` had no production caller and `classes.proposals_for` had no
# caller at all. A detector that finds real structure and is invoked by nothing is a
# library behind a public flag, which is a promise the tool was not keeping — the phase-1
# truthfulness gate is what caught it, and these arms are what empty that gate's
# allowlist.
#
# Two properties in tension. It has to REACH a consumer (`xcheck status` and the triage
# gate, on the CLI's own path, checked by the phase-1 call graph rather than by grep), and
# it has to stay PROPOSAL-ONLY there: reaching the human must not hand it the ability to
# create a class finding or set `superseded-by-class`.

class TheDetectorReachesAConsumer(unittest.TestCase):

    def test_the_phase_1_gate_reports_classes_as_wired(self):
        """Asserted by the CALL GRAPH, not by grep. Grepping for `classes` finds the conf
        key, the README row and this file — the reason the gate resolves over the AST."""
        from tests.test_feature_truthfulness import (PACKAGE, CallGraph, UNWIRED,
                                                     wiring_report)
        rows = {r.name: r for r in wiring_report(CallGraph(PACKAGE))}
        self.assertEqual("wired", rows["classes"].status, rows["classes"].detail)
        print(f"\n  WIRED      classes: {rows['classes'].detail}")
        self.assertEqual({}, UNWIRED,
                         "the allowlist is not empty — some feature is still a promise")
        print(f"  ALLOWLIST  UNWIRED is now EMPTY: every one of the "
              f"{len(rows)} declared feature flags resolves to a production call site")

    def test_status_on_THIS_repository_shows_the_real_candidates(self):
        lines = classes.report(live(), REPO)
        print("\n  REAL STATUS OUTPUT:")
        for line in lines:
            print(f"  | {line}")
        found, ungrouped = classes.candidates(live(), REPO)
        self.assertEqual(4, len(found), "the corpus's four classes")
        self.assertEqual(13, sum(c["size"] for c in found))
        self.assertEqual(120, len(ungrouped))
        self.assertIn("4 candidate(s)", lines[0])

    def test_the_ungrouped_count_is_on_the_surface(self):
        """`a-clusterer-that-groups-everything-learnt-nothing`, in the other direction: a
        surface showing four classes and nothing else would say this corpus has been
        understood, when 120 of its 133 findings share no pattern at all."""
        head = classes.report(live(), REPO)[0]
        self.assertIn("120 share no pattern", head)
        self.assertIn("13 of 133", head)
        print(f"  UNGROUPED  the headline carries it: {head}")

    @needs_live_corpus
    def test_the_real_verb_prints_it(self):
        """Through the CLI, not the producer. A block that only a function returns is a
        block nobody sees."""
        import io
        import contextlib
        from tests.harness import xcheck_module
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                xcheck_module().main(["xcheck", "--project", str(REPO), "status"])
            except SystemExit:
                pass
        out = buf.getvalue()
        self.assertIn("recurring classes:", out)
        self.assertIn("120 share no pattern", out)
        line = next(ln for ln in out.splitlines() if ln.startswith("recurring classes:"))
        print(f"  VIA CLI    `xcheck status` prints: {line}")

    def test_the_triage_gate_shows_them_too(self):
        """The one moment a human is being asked to sort findings is the moment these are
        worth seeing: triaging four instances of one defect as four unrelated findings is
        the work this detector exists to save."""
        src = (REPO / "xcheck" / "cli.py").read_text(encoding="utf-8")
        gate = src[src.index('if kind == "stop-triage":'):]
        self.assertIn("_class_lines()", gate[:600],
                      "the triage gate does not reach the detector")

    def test_the_proposal_form_reaches_the_bundle(self):
        """`proposals_for` had NO caller. The detector could describe a class and nothing
        ever asked it for the form a human acts on."""
        src = (REPO / "xcheck" / "cli.py").read_text(encoding="utf-8")
        self.assertIn("classes_mod.proposals_for(", src)
        found, _ = classes.candidates(live(), REPO)
        proposals = classes.proposals_for(found, exported())
        self.assertEqual(len(found), len(proposals))
        print(f"  PROPOSALS  {len(proposals)} carried in the OKF bundle, e.g. "
              f"{proposals[0]['kind']} -> {proposals[0]['target']}")


class TheOutputIsCompactEnoughForStatus(unittest.TestCase):
    """Criterion 6, decided on a measurement rather than a preference."""

    def test_the_measured_line_count_fits_the_declared_bound(self):
        lines = classes.report(live(), REPO)
        print(f"\n  MEASURED   {len(lines)} line(s) for {len(live().findings)} findings "
              f"and 4 classes; the declared inline bound is "
              f"{classes.MAX_INLINE_LINES}")
        print("  CHOSEN     status-inline. It fits, and `xcheck status` is where a human "
              "already looks before deciding what to triage — behind an explicit verb "
              "the detector would only ever run for someone who already suspected there "
              "was something to find.")
        self.assertLessEqual(len(lines), classes.MAX_INLINE_LINES)

    def test_it_degrades_rather_than_flooding_the_status_output(self):
        """The bound is enforced, not just declared. A corpus with more classes than fit
        gets a summary and a pointer, never forty lines in `status`."""
        many = state_doc(
            findings=[finding_record(f"F-{i:04d}", title="accepts what it should refuse")
                      for i in range(1, 60)],
            queue=[queue_pass("P-01")])
        fx = Fixture(many)
        self.addCleanup(fx.cleanup)
        st = state_mod.load_state(fx.audit)
        lines = classes.report(st, fx.root, threshold=2)
        self.assertLessEqual(len(lines), classes.MAX_INLINE_LINES)
        print(f"  BOUNDED    a {len(st.findings)}-finding corpus still renders in "
              f"{len(lines)} line(s)")


class ItStaysProposalOnlyAtTheConsumer(unittest.TestCase):
    """Reaching the human must not hand the detector the ability to act. Both halves:
    structurally, and by attempting it."""

    def test_STRUCTURAL_the_module_imports_nothing_that_writes(self):
        tree = ast.parse((REPO / "xcheck" / "classes.py").read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        forbidden = {n for n in names if any(
            w in n for w in ("xcheck.write", "xcheck.views", "xcheck.ledger"))}
        self.assertEqual(set(), forbidden, f"the detector imports a writer: {forbidden}")
        print(f"\n  STRUCTURAL classes.py imports {sorted(names)} — no write path")

    def test_STRUCTURAL_it_calls_no_write_verb(self):
        tree = ast.parse((REPO / "xcheck" / "classes.py").read_text(encoding="utf-8"))
        called = {n.func.attr for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        for forbidden in ("write_state", "file_finding", "set_status", "record_ruling"):
            self.assertNotIn(forbidden, called)

    def test_STRUCTURAL_the_new_call_sites_pass_it_nothing_writable(self):
        """The consumer half. `classes.report` and `_class_lines` are called with a state
        and a project path — reading material — and their return value is printed."""
        src = (REPO / "xcheck" / "cli.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "report"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "classes_mod"):
                self.assertLessEqual(len(node.args), 3)
                break
        else:                                                  # pragma: no cover
            self.fail("classes_mod.report is not called from cli.py")

    def test_BEHAVIOURAL_a_status_run_writes_nothing(self):
        fx = Fixture(state_doc(findings=[finding_record("F-0001")]))
        self.addCleanup(fx.cleanup)
        before = (fx.audit / "state.json").read_bytes()
        code, out = fx.run("status")
        self.assertEqual(0, code, out)
        self.assertEqual(before, (fx.audit / "state.json").read_bytes(),
                         "printing the class candidates changed canonical state")
        self.assertEqual([], list(state_mod.load_state(fx.audit).class_findings),
                         "a class finding was created by a read command")
        print("  BEHAVIOURAL `xcheck status` printed the block; state.json byte-identical "
              "and 0 class findings created")

    def test_no_finding_gained_superseded_by_class(self):
        """The other capability a class carries. Asserted separately because a detector
        that could not create a CF but could mark members superseded would have taken the
        same decision by a different door."""
        fx = Fixture(state_doc(findings=[finding_record("F-0001")]))
        self.addCleanup(fx.cleanup)
        fx.run("status")
        after = state_mod.load_state(fx.audit)
        self.assertIsNone(after.findings[0].cls,
                          "a finding was assigned to a class by a read command")

    def test_the_surface_says_it_is_a_proposal(self):
        """An operator reading four confident-looking groups needs the sentence saying
        nobody has decided they are one defect."""
        lines = classes.report(live(), REPO)
        self.assertTrue(any("PROPOSALS ONLY" in ln for ln in lines))
        self.assertTrue(any("a human's call" in ln for ln in lines))
        print(f"  PROPOSAL-ONLY {[ln for ln in lines if 'PROPOSALS ONLY' in ln][0]}")


class TheEmptyCaseIsSaidPlainly(unittest.TestCase):
    """CONTROL. A heading with nothing under it reads as a detector that failed rather
    than one that found nothing — and without this arm, a `report` that returned an empty
    list for every input would satisfy every other test here."""

    def test_a_corpus_with_no_recurring_class_says_so(self):
        fx = Fixture(state_doc(
            findings=[finding_record("F-0001", title="one lonely defect"),
                      finding_record("F-0002", title="a different lonely defect")],
            queue=[queue_pass("P-01")]))
        self.addCleanup(fx.cleanup)
        st = state_mod.load_state(fx.audit)
        lines = classes.report(st, fx.root)
        self.assertEqual(1, len(lines), lines)
        self.assertIn("none", lines[0])
        self.assertIn("2 finding(s) examined", lines[0])
        self.assertNotIn("PROPOSALS ONLY", "".join(lines),
                         "an empty result printed a heading with nothing under it")
        print(f"\n  CONTROL    {lines[0]}")

    def test_and_the_same_fixture_DOES_produce_a_class_when_one_exists(self):
        """The positive control on the control. If `report` were broken in a way that
        returned `none` always, the arm above would still be green."""
        fx = Fixture(state_doc(
            findings=[finding_record(f"F-{i:04d}", title="accepts what it should refuse")
                      for i in range(1, 5)],
            queue=[queue_pass("P-01")]))
        self.addCleanup(fx.cleanup)
        st = state_mod.load_state(fx.audit)
        lines = classes.report(st, fx.root, threshold=2)
        self.assertGreater(len(lines), 1, lines)
        self.assertIn("candidate(s)", lines[0])
        print(f"  POSITIVE   the same shape WITH a repeated mechanism: {lines[0]}")


if __name__ == "__main__":
    unittest.main()
