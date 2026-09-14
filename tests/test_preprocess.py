"""The fact sheet a script produces for free, and the four ways it could be worthless.

The audit's economics finding: the model pays to discover facts `ast` and `git` answer in
milliseconds, and every session rediscovers them. `xcheck/preprocess.py` answers them
once. The ways that could go wrong, each with a test below:

  1. it could be wrong — so one row is checked BY HAND against the tree, not against
     another run of the same code;
  2. it could be non-deterministic — so two runs are compared byte for byte, and the
     fixture plants a file whose discovery order is the thing that would vary;
  3. it could re-inflate the prompt it exists to shrink — so the capsule cost is
     measured against the capsule without it;
  4. it could quietly become a model call — so this module's own AST is walked.

And the fifth, which is not about being wrong but about being silent: a file the sheet
cannot parse must appear in `unparsed` with the reason. A fact sheet that drops the file
nobody could read is most wrong exactly where a reader most needs it.
"""

import ast
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.harness import needs_live_corpus, REPO, xcheck_submodule

pp = xcheck_submodule("preprocess")
capsule = xcheck_submodule("capsule")
state_mod = xcheck_submodule("state")

# The fixture tree, stated here so the expected numbers below are readable beside it.
#
#   pkg/__init__.py      a package marker            (0 public names)
#   pkg/core.py          imports pkg.util, calls subprocess.run and os.remove
#   pkg/util.py          two public names, one private
#   tests/test_core.py   imports pkg.core             -> the test map's one edge
#   broken.py           `def (` — UNPARSEABLE, on purpose
#   notes.md, pyproject.toml
FIXTURE = {
    "pkg/__init__.py": "",
    "pkg/core.py": (
        "import subprocess\n"
        "import os\n"
        "from pkg.util import helper\n"
        "\n"
        "def run_it(cmd):\n"
        "    subprocess.run(cmd)\n"
        "    os.remove('/tmp/x')\n"
        "\n"
        "def _private():\n"
        "    return 1\n"
    ),
    "pkg/util.py": "def helper():\n    return 1\n\n\nVALUE = 2\n\n\ndef _hidden():\n    pass\n",
    "tests/test_core.py": "from pkg.core import run_it\n\n\ndef test_it():\n    assert run_it\n",
    "broken.py": "def (\n",
    "notes.md": "# notes\n",
    "pyproject.toml": "[project]\nname = 'fixture'\n",
}


def tree(case, files=None):
    root = Path(tempfile.mkdtemp(prefix="xcheck-preprocess-"))
    case.addCleanup(shutil.rmtree, root, True)
    for rel, text in (files or FIXTURE).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


class TheFixtureTreeProducesExactlyTheExpectedSheet(unittest.TestCase):
    """CONTROL. A sheet checked only against the real repository can be self-consistent
    and wrong; this one has a shape small enough to hold in your head."""

    def setUp(self):
        self.root = tree(self)
        self.sheet = pp.build_sheet(self.root)

    def test_the_inventory_is_every_file_and_nothing_else(self):
        got = [f["path"] for f in self.sheet["inventory"]["files"]]
        self.assertEqual(sorted(FIXTURE), got)

    def test_the_module_map_names_the_one_package(self):
        self.assertEqual(["pkg"], self.sheet["modules"]["packages"])
        self.assertEqual({"python": 5, "markdown": 1, "toml": 1},
                         self.sheet["modules"]["by_language"])

    def test_the_dependency_edges_are_the_intra_repo_imports_only(self):
        # `import subprocess` and `import os` are third-party to this tree and are
        # dropped: "what does changing this break HERE" is the question, and `os` is
        # the same answer for every file.
        self.assertEqual([{"from": "pkg/core.py", "to": "pkg.util"},
                          {"from": "tests/test_core.py", "to": "pkg.core"}],
                         self.sheet["dependencies"]["edges"])

    def test_the_test_map_is_the_test_half_of_those_edges(self):
        self.assertEqual({"tests/test_core.py": ["pkg.core"]},
                         self.sheet["tests"]["tests"])

    def test_public_interfaces_skip_underscored_names(self):
        self.assertEqual({"pkg/core.py": ["run_it"],
                          "pkg/util.py": ["VALUE", "helper"],
                          # A test file's own public names count too: it is a module
                          # like any other, and pretending otherwise would make the
                          # sheet's rule "no underscore" plus an unstated exception.
                          "tests/test_core.py": ["test_it"]},
                         self.sheet["interfaces"]["public"])

    def test_the_sinks_are_located_and_not_judged(self):
        self.assertEqual(
            [{"path": "pkg/core.py", "line": 6, "call": "subprocess.run",
              "kind": "process"},
             {"path": "pkg/core.py", "line": 7, "call": "os.remove",
              "kind": "destructive"}],
            self.sheet["sinks"]["sinks"])
        for row in self.sheet["sinks"]["sinks"]:
            self.assertNotIn("severity", row)
            self.assertNotIn("verdict", row)

    def test_a_planted_change_moves_exactly_the_rows_it_should(self):
        """Without this the sheet could be a constant. A second sink in one file must
        move the sink rows and the line count — and NOTHING else."""
        before = pp.build_sheet(self.root)
        p = self.root / "pkg" / "core.py"
        p.write_text(p.read_text(encoding="utf-8") + "\n\ndef more():\n    eval('1')\n",
                     encoding="utf-8")
        after = pp.build_sheet(self.root)
        moved = sorted(k for k in before if before[k] != after[k])
        print(f"\n  PLANTED    a second sink in pkg/core.py moved: {moved}")
        self.assertEqual(["interfaces", "inventory", "sinks"], moved)
        self.assertEqual(len(before["sinks"]["sinks"]) + 1,
                         len(after["sinks"]["sinks"]))
        self.assertIn("more", after["interfaces"]["public"]["pkg/core.py"])
        # And the parts that had no business moving did not.
        for key in ("modules", "dependencies", "tests", "surfaces"):
            self.assertEqual(before[key], after[key], key)


class ItDegradesHonestly(unittest.TestCase):

    def test_an_unparseable_file_is_reported_with_its_reason(self):
        root = tree(self)
        sheet = pp.build_sheet(root)
        rows = [u for u in sheet["unparsed"] if u["path"] == "broken.py"]
        print(f"  UNPARSED   {rows[0]['path']}: {rows[0]['reason'][:70]}")
        self.assertEqual(1, len(rows), "the unparseable file was dropped silently")
        self.assertIn("SyntaxError", rows[0]["reason"])
        # It is still in the INVENTORY — the file exists, and only its AST is missing.
        self.assertIn("broken.py",
                      [f["path"] for f in sheet["inventory"]["files"]])
        # And it contributes nothing to the derived facts rather than a wrong something.
        self.assertNotIn("broken.py", sheet["interfaces"]["public"])

    def test_an_undecodable_file_is_reported_too(self):
        root = tree(self)
        (root / "binary.dat").write_bytes(b"\x80\x81\x82")
        sheet = pp.build_sheet(root)
        rows = [u for u in sheet["unparsed"] if u["path"] == "binary.dat"]
        self.assertEqual(1, len(rows))
        self.assertIn("UnicodeDecodeError", rows[0]["reason"])

    def test_a_nested_checkout_is_pruned_by_its_own_git_and_not_by_name(self):
        """A vendored clone, a submodule, or a worktree is some OTHER repository's
        files. Counting them reports a tree that does not exist — this project's harness
        keeps worktrees inside the tree, and walking them doubled every number.

        The rule is `has its own .git`, not a list of tool directory names: naming the
        operator's config directory in shipped code is a claim xcheck must not make, and
        `tests/test_style_claim_is_narrow` refuses it. That refusal is what produced
        this rule, and the general rule is the better answer anyway.
        """
        root = tree(self)
        before = pp.build_sheet(root)
        nested = root / "vendor" / "other-repo"
        nested.mkdir(parents=True)
        (nested / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
        (nested / "pkg").mkdir()
        # Deliberately the SAME relative shapes as the fixture's own files, so a walker
        # that failed to prune would double the counts rather than add odd new rows.
        for rel, text in FIXTURE.items():
            q = nested / rel
            q.parent.mkdir(parents=True, exist_ok=True)
            q.write_text(text, encoding="utf-8")
        after = pp.build_sheet(root)
        print(f"  NESTED     {len(before['inventory']['files'])} files before a nested "
              f"checkout of {len(FIXTURE)} files, "
              f"{len(after['inventory']['files'])} after")
        self.assertEqual(pp.sheet_digest(before), pp.sheet_digest(after),
                         "a nested checkout changed the sheet, so its files were "
                         "counted as this repository's")
        # CONTROL: the same files WITHOUT the `.git` marker are ordinary vendored source
        # and must be counted — otherwise this test passes for a walker that skips
        # anything under `vendor/`.
        (nested / ".git").unlink()
        counted = pp.build_sheet(root)
        self.assertGreater(len(counted["inventory"]["files"]),
                           len(before["inventory"]["files"]),
                           "without the .git marker those files are ordinary source "
                           "and were still skipped")

    def test_a_missing_base_says_so_rather_than_reporting_no_changes(self):
        """`[]` and "nobody asked" are opposite claims. The one that reads as an
        all-clear is the one this must not print."""
        sheet = pp.build_sheet(tree(self))
        ch = sheet["changed"]
        self.assertIsNone(ch["base"])
        self.assertEqual([], ch["files"])
        self.assertIn("not a claim that nothing changed", ch["note"])


class ItIsDeterministic(unittest.TestCase):

    def canon(self, sheet):
        return json.dumps(sheet, sort_keys=True, separators=(",", ":"))

    def test_two_runs_over_one_tree_are_byte_identical(self):
        root = tree(self)
        a, b = pp.build_sheet(root), pp.build_sheet(root)
        print(f"  DIGEST     {pp.sheet_digest(a)[:16]} == {pp.sheet_digest(b)[:16]}: "
              f"{pp.sheet_digest(a) == pp.sheet_digest(b)}")
        self.assertEqual(self.canon(a), self.canon(b))
        self.assertEqual(pp.sheet_digest(a), pp.sheet_digest(b))

    def test_it_is_stable_across_a_copy_of_the_tree(self):
        """The sharper arm: a copy has different inodes and a different creation order,
        so anything that leaked filesystem order into the output shows up here and not
        in a second run over the same directory."""
        root = tree(self)
        other = Path(tempfile.mkdtemp(prefix="xcheck-preprocess-copy-"))
        self.addCleanup(shutil.rmtree, other, True)
        shutil.copytree(root, other / "t")
        self.assertEqual(pp.sheet_digest(pp.build_sheet(root)), pp.sheet_digest(pp.build_sheet(other / "t")))

    def test_the_digest_moves_when_the_tree_moves(self):
        """CONTROL for the two above: a digest that never changes is also stable."""
        root = tree(self)
        before = pp.sheet_digest(pp.build_sheet(root))
        (root / "pkg" / "extra.py").write_text("def added():\n    pass\n", encoding="utf-8")
        self.assertNotEqual(before, pp.sheet_digest(pp.build_sheet(root)))


class NoModelAndNoNetwork(unittest.TestCase):
    """Asserted by walking this module's own AST, not by reading its docstring."""

    FORBIDDEN_IMPORTS = ("socket", "urllib", "http", "requests", "ssl", "asyncio",
                         "anthropic", "openai", "xcheck.runner", "xcheck.parallel")

    def module_ast(self):
        return ast.parse(Path(REPO, "xcheck", "preprocess.py").read_text(encoding="utf-8"))

    def test_it_imports_nothing_that_could_reach_a_provider(self):
        imported = set()
        for node in ast.walk(self.module_ast()):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        print(f"  IMPORTS    {sorted(imported)}")
        for bad in self.FORBIDDEN_IMPORTS:
            self.assertNotIn(bad, imported)
        self.assertEqual({"ast", "hashlib", "json", "subprocess", "pathlib",
                          "xcheck.util"}, imported,
                         "a new import here is a new capability — declare it on purpose")

    def test_the_only_thing_it_executes_is_git(self):
        """`subprocess` IS imported, and that is the one capability this module has.
        Every call site is asserted to be git, so the module cannot grow a provider CLI
        invocation while still passing the import test above."""
        argv0 = []
        for node in ast.walk(self.module_ast()):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id == "subprocess":
                first = node.args[0] if node.args else None
                self.assertIsInstance(first, ast.List,
                                      "a subprocess call with no literal argv list")
                self.assertIsInstance(first.elts[0], ast.Constant)
                argv0.append(first.elts[0].value)
        print(f"  EXECUTES   {argv0}")
        self.assertEqual(["git"], argv0)

    def test_it_reaches_no_other_xcheck_module_that_could_dispatch(self):
        """Over the AST's NAMES, not the file's text. The words themselves appear in
        this module's prose — a sheet built "at dispatch" is a sentence, not a call —
        and a substring hunt would force the documentation to be deleted to pass, which
        is the wrong thing to optimise. What must not appear is a reference."""
        names = {n.id for n in ast.walk(self.module_ast()) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(self.module_ast())
                  if isinstance(n, ast.Attribute)}
        for bad in ("run_session", "dispatch", "run_child", "build_cmd", "PROMPTS",
                    "Sandbox"):
            self.assertNotIn(bad, names,
                             f"{bad} is REFERENCED in a module that must not run "
                             f"anything but git")
        print(f"  NO DISPATCH names referenced: {len(names)}, none of them a launcher")


class ItRunsOnThisRepositoryAndTheNumbersAreCheckable(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.sheet = pp.build_sheet(REPO)

    def test_the_counts_are_printed(self):
        s = self.sheet
        print("\n  THIS REPOSITORY, by a script with no model and no network")
        for label, n in (
                ("files", len(s["inventory"]["files"])),
                ("python files", len(s["modules"]["python_files"])),
                ("packages", len(s["modules"]["packages"])),
                ("intra-repo import edges", len(s["dependencies"]["edges"])),
                ("test files mapped to sources", len(s["tests"]["tests"])),
                ("modules with a public interface", len(s["interfaces"]["public"])),
                ("security-sensitive call sites", len(s["sinks"]["sinks"])),
                ("build / CI / ownership surfaces", len(s["surfaces"]["surfaces"])),
                ("files the sheet could NOT parse", len(s["unparsed"]))):
            print(f"    {label:<34} {n:>5}")
        kinds = {}
        for row in s["sinks"]["sinks"]:
            kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
        print(f"    sink kinds                         {dict(sorted(kinds.items()))}")
        self.assertGreater(len(s["inventory"]["files"]), 50)

    def test_the_surfaces_row_is_checked_by_hand_against_the_tree(self):
        """The hand check. `surfaces` is chosen because a human can verify it with `ls`
        in ten seconds — three named files at the root, one workflow, and whatever is in
        `ci/` — where a count of import edges could only be checked by rewriting the
        code that produced it.
        """
        got = [s["path"] for s in self.sheet["surfaces"]["surfaces"]]
        root_named = sorted(n for n in (".gitignore", "MANIFEST.in", "pyproject.toml")
                            if Path(REPO, n).is_file())
        ci_files = sorted(f"ci/{p.name}" for p in Path(REPO, "ci").iterdir()
                          if p.is_file())
        workflows = sorted(f".github/workflows/{p.name}"
                           for p in Path(REPO, ".github", "workflows").iterdir()
                           if p.is_file())
        expected = sorted(root_named + ci_files + workflows)
        print(f"\n  HAND CHECK surfaces: {len(got)} reported, {len(expected)} counted "
              f"by hand from the tree")
        for path in expected:
            print(f"    {path}")
        self.assertEqual(expected, got)

    def test_the_sheet_excludes_the_audits_own_bookkeeping(self):
        """`audit/` is the record OF the audit, not the material under it. A sheet that
        counted 133 finding bodies as source files would report a repository that does
        not exist."""
        for path in [f["path"] for f in self.sheet["inventory"]["files"]]:
            self.assertFalse(path.startswith(("audit/", "audit-archive/", ".git/")),
                             path)

    def test_changed_symbols_against_head_is_reported_with_its_base(self):
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO),
                              capture_output=True, text=True).stdout.strip()
        ch = pp.changed_symbols(REPO, head)
        print(f"  CHANGED    vs {head[:12]}: {len(ch['files'])} file(s), "
              f"{len(ch['symbols'])} top-level symbol(s)")
        self.assertEqual(head, ch["base"])
        self.assertIn("file-granular", ch["note"])


@needs_live_corpus
class TheCapsuleCarriesASliceAndNotTheSheet(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.state = state_mod.load_state(Path(REPO, "audit"))
        cls.sheet = pp.build_sheet(REPO)

    def capsules(self):
        for q in self.state.queue:
            cap = capsule.build(self.state, REPO, "Auditor", f"{q.id}: {q.charter}",
                                sheet=self.sheet)
            without = {k: v for k, v in cap.items() if k != "preprocess"}
            yield cap, len(capsule.render_capsule(cap)), \
                len(capsule.render_capsule(without))

    def test_the_slice_costs_a_few_hundred_bytes_not_a_few_thousand(self):
        rows = list(self.capsules())
        with_slice = sum(r[1] for r in rows) / len(rows)
        without = sum(r[2] for r in rows) / len(rows)
        corpus = sum(capsule.corpus_bytes(REPO).values())
        print(f"\n  CAPSULE SIZE over {len(rows)} live charters")
        print(f"    without the slice   {without:8.0f} bytes avg")
        print(f"    with the slice      {with_slice:8.0f} bytes avg "
              f"(+{with_slice - without:.0f}, {(with_slice / without - 1) * 100:.1f}%)")
        print(f"    the corpus it replaced {corpus:6,} bytes -> "
              f"{corpus / with_slice:.0f}x smaller")
        self.assertLess(with_slice - without, 2000,
                        "the slice re-inflated the prompt the capsule exists to shrink")
        self.assertGreater(corpus / with_slice, 10,
                           "the capsule stopped being a reduction")

    def test_the_capsule_carries_the_digest_and_not_the_sheet(self):
        cap = next(self.capsules())[0]
        pre = cap["preprocess"]
        self.assertEqual(pp.sheet_digest(self.sheet), pre["preprocess_digest"])
        blob = json.dumps(cap, sort_keys=True)
        for whole in ("dependencies", "surfaces", "preprocess_schema"):
            self.assertNotIn(f'"{whole}"', blob,
                             "the capsule is carrying the whole sheet")

    def test_a_path_the_sheet_never_scanned_says_NOT_SCANNED(self):
        """`audit/AUDIT.md` is material some charters name, and the sheet deliberately
        skips `audit/`. A row of dashes would read as "scanned, and empty"."""
        sheet = pp.build_sheet(REPO)
        sl = pp.slice_for(sheet, ["audit/AUDIT.md", "xcheck/util.py"])
        by_path = {r["path"]: r for r in sl["rows"]}
        self.assertFalse(by_path["audit/AUDIT.md"]["present"])
        self.assertTrue(by_path["xcheck/util.py"]["present"])
        text = capsule.render_capsule(
            {"role": "Auditor", "charter": "c", "postcondition": {}, "allowed_verbs": [],
             "stop": [], "dimensions": [], "norms": [], "sources": [], "findings": [],
             "limits": {}, "preprocess": sl,
             "read_more": {"methodology": "a", "plan": "b", "note": "c"}})
        print("  NOT SCANNED row rendered for audit/AUDIT.md: "
              f"{'NOT SCANNED' in text}")
        self.assertIn("NOT SCANNED", text)


if __name__ == "__main__":
    unittest.main()
