"""`xcheck reconcile` — the backlog against HEAD, proposed and never applied.

PHASE 14 (fourth audit). 133 findings sit non-terminal, filed against commits that have
moved, so the decision engine keeps routing remediation at defects that are already fixed.
The audit's worked example is F-0098, fixed in `d4ba983` while its finding sat unchanged.

Two things this module has to prove, and they pull in opposite directions.

The reconciler must actually DECIDE — a classifier that answers `requires-human-ruling`
for everything is a very safe way of doing nothing, so the census over all 133 real
findings is printed and the counterfactual arms assert that a live anchor and a dead one
get different verdicts. And it must NOT ACT: no status changes, no capability to change
one, asserted structurally over the imports and behaviourally by attempting a write.
"""

import ast
import subprocess
import unittest
from pathlib import Path

from tests.harness import (needs_live_corpus, HAVE_LIVE_CORPUS, REPO, Fixture,
                           finding_record, state_doc, xcheck_submodule)

reconcile = xcheck_submodule("reconcile")
state_mod = xcheck_submodule("state")
util = xcheck_submodule("util")

# The corpus. Loaded once: 133 findings, each reading an evidence file and some running a
# git pickaxe, is not work to repeat per test.
# Guarded: this runs at IMPORT, so a decorator is too late — a released tree with no
# audit/ would fail to collect the module at all rather than skip its tests.
REAL = state_mod.load_state(REPO / "audit") if HAVE_LIVE_CORPUS else None


@needs_live_corpus
class TheCensusOverTheRealCorpus(unittest.TestCase):
    """A reconciler demonstrated only on fixtures has not been demonstrated."""

    @classmethod
    def setUpClass(cls):
        cls.rows = reconcile.reconcile(REAL, REPO)

    def test_every_non_terminal_finding_gets_exactly_one_declared_verdict(self):
        live = [f for f in REAL.findings if f.status not in reconcile.TERMINAL]
        self.assertEqual(len(live), len(self.rows))
        self.assertEqual(len(self.rows), len({r.id for r in self.rows}),
                         "a finding was classified twice")
        for r in self.rows:
            self.assertIn(r.verdict, reconcile.VERDICTS, r.id)

        counts = reconcile.census(self.rows)
        print(f"\n  CENSUS over {len(self.rows)} non-terminal finding(s) at HEAD:")
        for verdict in reconcile.VERDICTS:
            print(f"    {verdict:<24} {counts[verdict]:>4}")
        self.assertEqual(len(self.rows), sum(counts.values()))

    def test_the_census_reports_every_verdict_including_the_empty_ones(self):
        """An omitted zero makes an unreached verdict indistinguishable from one that
        does not exist."""
        self.assertEqual(set(reconcile.VERDICTS), set(reconcile.census(self.rows)))

    def test_it_decides_rather_than_deferring_everything(self):
        """The failure mode this arm exists for: `anchor-missing` on all 133 would
        satisfy every other criterion in this file."""
        counts = reconcile.census(self.rows)
        decided = len(self.rows) - counts[reconcile.ANCHOR_MISSING]
        print(f"  DECIDED    {decided} of {len(self.rows)} classified against HEAD; "
              f"{counts[reconcile.ANCHOR_MISSING]} with no anchor to check")
        self.assertGreater(decided, len(self.rows) // 2,
                           "the reconciler deferred more than half the corpus")
        self.assertGreaterEqual(len({r.verdict for r in self.rows}), 3,
                                "a classifier with two outcomes is a sorting hat")

    def test_anchor_missing_is_a_verdict_with_a_reason(self):
        """Criterion 6. It is the honest answer for a body a machine cannot check, and it
        says WHY rather than appearing as a residue."""
        human = [r for r in self.rows if r.verdict == reconcile.ANCHOR_MISSING]
        self.assertTrue(human)
        for r in human:
            self.assertTrue(r.evidence["note"], r.id)
        print(f"  HUMAN      {len(human)} finding(s) cannot be decided by machine — "
              f"their bodies quote no source line, so there is nothing to compare "
              f"against HEAD. Example {human[0].id}: {human[0].evidence['note'][:90]}")

    def test_every_verdict_carries_its_evidence(self):
        """Criterion 5. A classification without evidence is an opinion."""
        for r in self.rows:
            for field in ("locator", "source_hash_then", "source_hash_now", "note"):
                self.assertIn(field, r.evidence, r.id)
            self.assertTrue(r.evidence["note"], f"{r.id} has a verdict and no reason")
            self.assertTrue(r.evidence["source_hash_then"], r.id)

    def test_the_absent_source_hash_is_recorded_as_unknown_not_as_blank(self):
        """0 of 133 findings carry a `source_hash`: the field became required at phase 9
        of the THIRD audit and the whole corpus predates it. Saying `unknown` is a
        different claim from saying nothing, and only one of them is checkable."""
        self.assertEqual(0, len([f for f in REAL.findings if f.source_hash]))
        for r in self.rows:
            self.assertIn("unknown", r.evidence["source_hash_then"])
        print(f"  ANCHORS    0 of {len(REAL.findings)} findings carry a source_hash or a "
              f"locator; the classifier keys on the evidence BODY instead — quoted "
              f"source in 93, a path reference in all of them")


@needs_live_corpus
class TheAuditsWorkedExample(unittest.TestCase):
    """F-0098, fixed in `d4ba983` while its finding sat unchanged. The reconciler has to
    reach that verdict from the tree, not from being told."""

    @classmethod
    def setUpClass(cls):
        cls.row = reconcile.verdict_for(
            next(f for f in REAL.findings if f.id == "F-0098"), REPO)

    def test_its_anchor_is_gone_which_is_all_the_module_may_say(self):
        """PHASE 8 (fifth audit): this used to assert `already-fixed`. F-0098 WAS fixed,
        in `d4ba983` — but a human established that, and the same evidence (a quoted line
        that no longer matches) is produced by one added space. The verdict now says what
        was observed."""
        self.assertEqual(reconcile.ANCHOR_CHANGED, self.row.verdict)
        self.assertNotIn("fixed", self.row.action,
                         "the proposal claims a fix from an anchor")

    def test_the_evidence_names_the_commit_that_fixed_it(self):
        """`git log -S` finds where the literal's occurrence count changed, so the verdict
        is a citation a reader can check rather than a conclusion to accept."""
        commit = self.row.evidence["commit"]
        self.assertTrue(commit, "anchor-changed with no commit is an assertion")
        self.assertTrue(commit.startswith("d4ba983"),
                        f"expected the audit's commit, got {commit!r}")
        print(f"\n  F-0098     verdict={self.row.verdict}")
        print(f"    locator          {self.row.evidence['locator']}")
        print(f"    source hash then {self.row.evidence['source_hash_then']}")
        print(f"    source hash now  {self.row.evidence['source_hash_now'][:16]}…")
        print(f"    changed in       {commit}")
        print(f"    why              {self.row.evidence['note']}")
        print(f"    $ {self.row.action}")

    def test_the_primary_locator_decides_and_not_the_surviving_context(self):
        """The classifier defect this arm was written against. F-0098's body quotes three
        different things: the defective code in `xcheck/envelope.py`, the CALL SITE in
        `xcheck/parallel.py` that reaches it, and the NORM it violates from `SECURITY.md`.
        Seven of its nine quotes are still alive — the call site works fine after the fix
        and the norm had better still be there — so `any quote survives` read a fixed
        defect as still-present."""
        body = (REPO / "audit" / next(f for f in REAL.findings
                                      if f.id == "F-0098").body_path).read_text(
            encoding="utf-8")
        quoted = [a for a in reconcile.anchors(body) if a.quote and a.path]
        alive = [a for a in quoted if a.quote in (REPO / a.path).read_text(
            encoding="utf-8", errors="replace")]
        by_path = {}
        for a in quoted:
            by_path.setdefault(a.path, []).append(a)
        print(f"  QUOTES     {len(alive)} of {len(quoted)} still alive, across "
              f"{len(by_path)} file(s):")
        for path, group in by_path.items():
            live = sum(1 for a in group if a in alive)
            print(f"    {path:<24} {live}/{len(group)} alive")
        self.assertGreater(len(alive), len(quoted) // 2,
                           "most quotes should survive — that is the point")
        self.assertEqual(0, sum(1 for a in alive if a.path == quoted[0].path),
                         "the primary locator's quotes should be gone")


class ItProposesAndCannotWrite(unittest.TestCase):

    def test_STRUCTURAL_the_module_imports_nothing_that_writes(self):
        """Promising not to write is not a control. This reads the imports."""
        tree = ast.parse((REPO / "xcheck" / "reconcile.py").read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
                names |= {f"{node.module}.{a.name}" for a in node.names}
        writers = {n for n in names if any(
            w in n for w in ("xcheck.write", "xcheck.ledger", "xcheck.views",
                             "xcheck.state", "xcheck.migrate"))}
        self.assertEqual(set(), writers,
                         f"the reconciler imports a writing module: {writers}")
        print(f"\n  STRUCTURAL imports {sorted(names)} — no write path, no state writer")

    def test_STRUCTURAL_it_calls_no_write_verb(self):
        src = (REPO / "xcheck" / "reconcile.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        called = {node.func.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        # Names nobody writes by accident. `append` was in this list for one run and it
        # matched `list.append` — a needle that is also an ordinary word reports a
        # violation on every honest module (`a-needle-set-must-exclude-ordinary-words`).
        for forbidden in ("write_state", "set_status", "record_fix", "store",
                          "write_text", "write_bytes", "unlink", "mkdir", "rename"):
            self.assertNotIn(forbidden, called, f"the reconciler calls {forbidden}")

        # The real capability question, since this module DOES shell out: which git.
        # `log` and `grep` read; a module that could reach `commit` or `checkout` would
        # hold the ability to change the tree whatever its imports said.
        subcommands = set()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "_git_read"):
                continue
            first = node.args[1] if len(node.args) > 1 else None
            if isinstance(first, ast.Constant):
                subcommands.add(first.value)
        self.assertTrue(subcommands, "the git wrapper is called with no literal verb")
        self.assertEqual({"log", "grep"}, subcommands,
                         "a git verb is hidden from this check inside a splatted list — "
                         "it once passed reporting only ['grep'] while `log` was "
                         "invisible to it")
        self.assertEqual(set(), subcommands - {"log", "grep"},
                         f"the reconciler runs git {sorted(subcommands)}")
        print(f"  GIT        runs only: {sorted(subcommands)} — both read-only")

    def test_BEHAVIOURAL_the_verb_leaves_state_byte_identical(self):
        """The attempt, not the intention. `reconcile` runs against a real project and
        the state file is compared before and after."""
        fx = Fixture(state_doc(findings=[finding_record("F-0001")]))
        self.addCleanup(fx.cleanup)
        target = fx.audit / "state.json"
        before = target.read_bytes()
        code, out = fx.run("reconcile")
        self.assertEqual(0, code, out)
        self.assertEqual(before, target.read_bytes(),
                         "`xcheck reconcile` changed canonical state")
        self.assertIn("PROPOSAL", out)
        print("  BEHAVIOURAL `xcheck reconcile` ran to completion; "
              "audit/state.json byte-identical before and after")

    @needs_live_corpus
    def test_the_proposal_hands_over_exact_commands(self):
        """Criterion 8. "Consider reviewing this" is not a proposal anybody can act on."""
        rows = reconcile.reconcile(REAL, REPO)
        text = reconcile.proposal(rows[:6], reconcile.pass_report(REAL, REPO)[:2])
        self.assertIn("Nothing below has been applied", text)
        self.assertIn("xcheck set-status", text)
        commands = sorted({r.action for r in rows})
        print("\n  COMMANDS HANDED OVER:")
        for c in commands:
            print(f"    $ {c}")
        self.assertTrue(any(c.startswith("xcheck ") for c in commands))

    def test_a_status_this_module_cannot_change_is_still_changeable_by_a_human(self):
        """The control on the refusal: `reconcile` not writing is a property of
        `reconcile`, not a project that has become read-only."""
        fx = Fixture(state_doc(findings=[finding_record("F-0001")]))
        self.addCleanup(fx.cleanup)
        code, out = fx.run("set-status", "F-0001", "accepted")
        self.assertEqual(0, code, out)
        after = state_mod.load_state(fx.audit)
        self.assertEqual("accepted", after.findings[0].status)


class TheCounterfactualArms(unittest.TestCase):
    """A reconciler that returns one verdict for everything is a sorting hat. Both
    directions are asserted."""

    def setUp(self):
        import tempfile
        self.root = Path(tempfile.mkdtemp(prefix="xcheck-recon-"))
        (self.root / "audit" / "findings").mkdir(parents=True)
        (self.root / "src").mkdir()
        (self.root / "src" / "live.py").write_text(
            "def widget():\n    return CONSTANT_THAT_IS_LONG_ENOUGH_TO_ANCHOR\n",
            encoding="utf-8")

    def _finding(self, name, path, quote):
        body = f"body-{name}.md"
        (self.root / "audit" / "findings" / body).write_text(
            f"## Evidence\n\nAt `{path}:2`, the defect is:\n\n> `{quote}`\n",
            encoding="utf-8")
        return type("F", (), {"id": name, "status": "reported", "title": name,
                              "unit": ("U01",), "body_path": f"findings/{body}",
                              "source_hash": None})()

    def test_an_anchor_that_still_matches_HEAD_is_NOT_anchor_changed(self):
        row = reconcile.verdict_for(
            self._finding("F-9001", "src/live.py",
                          "return CONSTANT_THAT_IS_LONG_ENOUGH_TO_ANCHOR"), self.root)
        self.assertEqual(reconcile.ANCHOR_UNCHANGED, row.verdict)
        self.assertNotEqual(reconcile.ANCHOR_CHANGED, row.verdict)
        print(f"\n  ARM A      a live anchor -> {row.verdict}")

    def test_an_anchor_that_is_gone_is_NOT_anchor_unchanged(self):
        row = reconcile.verdict_for(
            self._finding("F-9002", "src/live.py",
                          "return A_CONSTANT_THAT_WAS_DELETED_LONG_AGO"), self.root)
        self.assertEqual(reconcile.ANCHOR_CHANGED, row.verdict)
        self.assertNotEqual(reconcile.ANCHOR_UNCHANGED, row.verdict)
        print(f"  ARM B      a dead anchor -> {row.verdict}")

    def test_a_file_that_is_gone_with_no_quote_has_no_anchor(self):
        body = "body-F-9003.md"
        (self.root / "audit" / "findings" / body).write_text(
            "## Evidence\n\nSee `src/deleted.py:10` — nothing is quoted.\n",
            encoding="utf-8")
        row = reconcile.verdict_for(
            type("F", (), {"id": "F-9003", "status": "reported", "title": "t",
                           "unit": ("U01",), "body_path": f"findings/{body}",
                           "source_hash": None})(), self.root)
        self.assertEqual(reconcile.ANCHOR_MISSING, row.verdict)
        print(f"  ARM C      a named file that no longer exists -> {row.verdict}")

    def test_a_body_with_no_anchor_at_all_is_anchor_missing(self):
        body = "body-F-9004.md"
        (self.root / "audit" / "findings" / body).write_text(
            "## Evidence\n\nThe design feels wrong. No path, no quote.\n",
            encoding="utf-8")
        row = reconcile.verdict_for(
            type("F", (), {"id": "F-9004", "status": "reported", "title": "t",
                           "unit": ("U01",), "body_path": f"findings/{body}",
                           "source_hash": None})(), self.root)
        self.assertEqual(reconcile.ANCHOR_MISSING, row.verdict)
        print(f"  ARM D      no anchor at all -> {row.verdict}")

    def test_two_findings_with_one_unit_and_one_title_are_duplicates(self):
        a = self._finding("F-9005", "src/live.py", "return CONSTANT_THAT_IS_LONG_ENOUGH")
        b = self._finding("F-9006", "src/live.py", "return CONSTANT_THAT_IS_LONG_ENOUGH")
        b.title = a.title = "the same defect, filed twice"
        dupes = reconcile.duplicate_map([a, b])
        self.assertEqual({"F-9006": "F-9005"}, dupes)
        row = reconcile.verdict_for(b, self.root, dupes)
        self.assertEqual(reconcile.POSSIBLE_DUPLICATE, row.verdict)
        print(f"  ARM E      a second filing of one defect -> {row.verdict} "
              f"(of {dupes['F-9006']})")

    @needs_live_corpus
    def test_the_audit_tree_is_excluded_from_the_survival_search(self):
        """The trap this cost an hour to find. `git grep` for a finding's own quoted line
        finds the FINDING — the audit tree is where evidence quotes source — so every
        dead anchor came back "surviving elsewhere" — in the document that quoted it.
        22 changed anchors were hidden behind it."""
        src = (REPO / "xcheck" / "reconcile.py").read_text(encoding="utf-8")
        self.assertIn(":(exclude)audit/", src)
        # And the searcher must not quote what it searches for. This module's docstring
        # illustrated the anchor shape with F-0098's REAL `subprocess` line, so
        # `tree_contains` found it in xcheck/reconcile.py and the worked example flipped
        # from `already-fixed` to `source-moved` — the searcher matching itself
        # (`self-scanning-detector-needs-a-split-needle`). The illustration is now a
        # placeholder, and this asserts it stays one.
        self.assertNotIn("--version\"], capture_output", src,
                         "the reconciler's own prose quotes a line it searches for")
        quote = "p = subprocess.run([exe, \"--version\"]"
        self.assertEqual([], reconcile.tree_contains(REPO, quote),
                         "the excluded audit tree is still being searched")
        raw = subprocess.run(["git", "grep", "-lF", quote], cwd=str(REPO),
                             capture_output=True, text=True)
        self.assertTrue(raw.stdout.strip(),
                        "control: an unscoped search DOES find it, in audit/")
        print(f"\n  EXCLUSION  unscoped `git grep` finds the line in "
              f"{raw.stdout.strip().splitlines()[0]}; scoped search finds it nowhere")


@needs_live_corpus
class TheUnfinishedPassesAreReported(unittest.TestCase):
    """Criterion 9. A pass queued against material that is gone should be cancelled; one
    nobody got to should be run. Reporting both as `pending` is what lets 17 unfinished
    passes look like a single backlog."""

    def test_each_unfinished_pass_is_checked_against_HEAD(self):
        rows = reconcile.pass_report(REAL, REPO)
        unfinished = [q for q in REAL.queue
                      if not (q.coverage and q.coverage.status == "done")]
        self.assertEqual(len(unfinished), len(rows))
        print(f"\n  PASSES     {len(rows)} unfinished:")
        for pid, dim, units, note in rows:
            print(f"    {pid:<6} {str(dim):<22} {note}")
        gone = [r for r in rows if "MISSING" in r[3]]
        print(f"  RESULT     {len(rows) - len(gone)} still meaningful against HEAD, "
              f"{len(gone)} queued against material that no longer exists")
        for _pid, _dim, _units, note in rows:
            self.assertTrue(note)

    def test_unit_material_is_parsed_into_paths_that_exist(self):
        """The parse had two traps, both of which reported a live pass as dead: prose
        stuck to a path (`xcheck/runner.py` lines 1-1120`) and a half-expanded brace
        group (`templates/{finding,…,plan}.md` matching only `plan}.md`)."""
        by_id = {u.id: u for u in REAL.catalogs.units}
        self.assertEqual(("xcheck/runner.py",),
                         reconcile.unit_paths(by_id["U16"].material))
        expanded = reconcile.unit_paths(by_id["U07"].material)
        self.assertIn("templates/finding.md", expanded)
        self.assertIn("templates/plan.md", expanded)
        self.assertNotIn("plan}.md", expanded)
        print(f"  U07 PATHS  {list(expanded)}")


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------- PHASE 8 (fifth audit)

class OneAddedSpaceIsNotAFix(unittest.TestCase):
    """The audit's minimal example, run through the real classifier.

        def authorized(token): return True
        def authorized(token):  return True

    The guard admits every token in both. The quoted line stops matching, and the old
    vocabulary called that `already-fixed` — then proposed `xcheck set-status F-0001
    fixed`, which §5 does not allow from `reported` at all.
    """

    QUOTE = "def authorized(token): return True"

    def build(self, line):
        import subprocess
        import tempfile
        root = Path(tempfile.mkdtemp(prefix="xcheck-space-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        (root / "audit" / "findings").mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        (root / "auth.py").write_text(line + "\n", encoding="utf-8")
        (root / "audit" / "findings" / "F-0001.md").write_text(
            f"at `auth.py:1`, the guard admits every token\n\n> {self.QUOTE}\n",
            encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-qm", "x"], cwd=root, check=True)
        finding = type("F", (), {"id": "F-0001", "status": "reported", "unit": ("U01",),
                                 "title": "the guard admits every token",
                                 "body_path": "findings/F-0001.md",
                                 "source_hash": None})()
        return reconcile.verdict_for(finding, root)

    def test_the_whitespace_edit_is_anchor_changed_and_proposes_no_status(self):
        before = self.build(self.QUOTE)
        after = self.build("def authorized(token):  return True")   # one added space
        print(f"\n  BEFORE     `{self.QUOTE}` -> {before.verdict}")
        print(f"    $ {before.action}")
        print(f"  AFTER      one added space, behaviour identical -> {after.verdict}")
        print(f"      {after.action}")
        self.assertEqual(reconcile.ANCHOR_UNCHANGED, before.verdict)
        self.assertEqual(reconcile.ANCHOR_CHANGED, after.verdict)
        self.assertTrue(after.action.startswith("#"),
                        "a moved anchor proposed a status change")
        self.assertNotIn("fixed", after.action)

    def test_the_message_says_what_the_verdict_does_and_does_not_mean(self):
        """Criterion 3. The verdict is only useful if the reader knows its limit."""
        means = reconcile.MEANS[reconcile.ANCHOR_CHANGED]
        print(f"  MEANS      {means}")
        self.assertIn("edited", means)
        self.assertIn("space", means, "nothing warns that whitespace moves an anchor")
        self.assertIn("NOT a fix verdict", reconcile.NOT_A_FIX_VERDICT)

    def test_no_verdict_in_the_module_claims_a_defect_is_fixed(self):
        """Criterion 2, over the vocabulary itself rather than one example."""
        self.assertEqual(("anchor-unchanged", "anchor-changed", "anchor-missing",
                          "possible-duplicate"), reconcile.VERDICTS)
        for verdict in reconcile.VERDICTS:
            self.assertNotIn("fixed", verdict)
            self.assertIsNone(reconcile.WANTS.get(verdict) == "fixed" or None)
        print(f"  VOCABULARY {', '.join(reconcile.VERDICTS)} — no verdict names a fix")


@needs_live_corpus
class EveryProposalIsATransitionTheCLIAccepts(unittest.TestCase):
    """Criterion 4. The proposals are fed to the validator `set-status` itself runs, over
    the real corpus and over every (verdict, status) pair the table allows.

    The reconciler may not IMPORT the writer — it must hold no write capability, asserted
    above — so the §5 table moved to `util` in this phase, where both readers share one
    copy. This test imports the writer precisely because it is not the reconciler."""

    @classmethod
    def setUpClass(cls):
        cls.write = xcheck_submodule("write")
        cls.rows = reconcile.reconcile(REAL, REPO)

    def commands(self, rows):
        return [(r.id, r.status, r.action) for r in rows if not r.action.startswith("#")]

    def test_every_command_over_the_real_corpus_is_legal_from_its_own_status(self):
        proposed = self.commands(self.rows)
        self.assertTrue(proposed, "no command was proposed at all — nothing is tested")
        for fid, status, action in proposed:
            want = action.split()[-1]
            allowed = util.TRANSITIONS.get(status, set())
            self.assertIn(want, allowed,
                          f"{fid} is {status!r} and the proposal says {want!r}")
            self.assertNotIn((status, want), util.RESERVED,
                             f"{fid}: {status} -> {want} is reserved for a dedicated verb")
        seen = sorted({(s, a.split()[-1]) for _f, s, a in proposed})
        print(f"\n  PROPOSED   {len(proposed)} command(s) over {len(self.rows)} row(s); "
              f"transitions used: {seen}")

    def test_the_old_proposal_would_have_been_refused(self):
        """The defect, executed rather than described: the command the previous
        vocabulary printed for these same findings."""
        changed = [r for r in self.rows if r.verdict == reconcile.ANCHOR_CHANGED]
        self.assertTrue(changed)
        fid, status = changed[0].id, changed[0].status
        allowed = util.TRANSITIONS.get(status, set())
        print(f"  OLD        `xcheck set-status {fid} fixed` from {status!r} — §5 allows "
              f"{sorted(allowed)}")
        self.assertNotIn("fixed", allowed,
                         "the old proposal would have been accepted after all")
        self.assertTrue(changed[0].action.startswith("#"))

    def test_a_status_with_no_legal_move_says_so_instead_of_proposing_one(self):
        """`proposal_for` is driven directly across the whole table, because the corpus
        happens to be almost all `reported` and one status cannot exercise a rule."""
        rows = []
        for status in sorted(util.TRANSITIONS) + ["closed"]:
            got = reconcile.proposal_for(reconcile.ANCHOR_UNCHANGED, status, "F-0001")
            legal = "accepted" in util.TRANSITIONS.get(status, set())
            rows.append((status, legal, got))
            self.assertEqual(legal, not got.startswith("#"),
                             f"{status}: proposal disagrees with the table")
            if not got.startswith("#"):
                self.assertNotIn((status, "accepted"), util.RESERVED)
        print("  PER STATUS")
        for status, legal, got in rows:
            print(f"    {status:<20} {'command' if legal else 'comment'}  {got[:66]}")

    def test_the_transition_table_has_exactly_one_home(self):
        """The seam this phase moved. Two copies of a closed vocabulary drift, and the
        drift is invisible until an operator types the command that the other copy
        allows."""
        self.assertFalse(hasattr(self.write, "TRANSITIONS"),
                         "write.py kept its own copy of the table")
        self.assertIs(util.TRANSITIONS, self.write.util.TRANSITIONS)
        source = (REPO / "xcheck" / "reconcile.py").read_text(encoding="utf-8")
        self.assertNotIn("TRANSITIONS = {", source, "the reconciler declared a copy")
        print(f"  ONE TABLE  util.TRANSITIONS, {len(util.TRANSITIONS)} statuses, read by "
              f"write._set_status and reconcile.proposal_for")


@needs_live_corpus
class TheCensusIsPrintedInBothVocabularies(unittest.TestCase):
    """Criterion 5. 22 findings were called `already-fixed` by the previous run. The
    reader has to be able to see which ones stopped being called that."""

    def test_the_real_corpus_beside_the_old_counts(self):
        rows = reconcile.reconcile(REAL, REPO)
        counts = reconcile.census(rows)
        # The previous run's published census, from docs/audit-response-4.md.
        old = {"still-present": 65, "already-fixed": 22, "source-moved": 6,
               "requires-human-ruling": 40}
        print(f"\n  CENSUS at HEAD, {len(rows)} non-terminal finding(s)")
        print(f"    OLD  {', '.join(f'{k}={v}' for k, v in old.items())}")
        for v in reconcile.VERDICTS:
            print(f"    NEW  {v:<20} {counts[v]:>4}   {reconcile.MEANS[v][:60]}")
        print(f"    the {old['already-fixed']} findings the previous run called "
              f"`already-fixed` are now `anchor-changed`: an edit, dated, with no claim "
              f"about behaviour")
        self.assertEqual(len(rows), sum(counts.values()))
        self.assertNotIn("already-fixed", reconcile.VERDICTS)

    def test_the_proposal_document_says_an_anchor_is_not_a_fix(self):
        """Criterion 6, in the artefact a human reads rather than in a docstring."""
        text = reconcile.proposal(reconcile.reconcile(REAL, REPO)[:4])
        self.assertIn("NOT a fix verdict", text)
        self.assertIn("record-verdict", text)
        self.assertIn("reproducer", text)
        for verdict in reconcile.VERDICTS:
            self.assertIn(reconcile.MEANS[verdict][:40], text,
                          f"{verdict} is counted without saying what it means")
        print("  DOCUMENT   the proposal carries NOT_A_FIX_VERDICT and all four meanings")
