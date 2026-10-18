"""The live-corpus gate, and the counter-assertion that keeps it from hiding anything.

A large family of tests reads THIS repository's own audit corpus — 133 findings, 159
session records, 1,043 events. A released tree carries the package and its tests but no
`audit/`, so in any fresh clone their subject is simply absent, and erroring there made
the published battery look like a broken release: 105 of 112 remaining failures were
this one missing file.

A gate that skips is also a gate that can HIDE, which is the thing to be afraid of here
— `a-fast-tier-is-a-way-to-skip-the-proofs`. So the gate is one predicate with one name,
and this module asserts two things about it: that nothing skips where the corpus is
present, and that the gate is keyed on the corpus rather than on anything a caller could
set.
"""

import ast
import pathlib
import re
import unittest

from tests.harness import (HAVE_INTERNAL_DOCS, HAVE_LIVE_CORPUS, LIVE_AUDIT,
                           NO_CORPUS_REASON, REPO, needs_live_corpus,
                           require_live_corpus)

TESTS = REPO / "tests"


def gated_modules():
    """Every test module that names the gate, and how many classes it gates."""
    out = {}
    for f in sorted(TESTS.glob("test_*.py")):
        src = f.read_text(encoding="utf-8")
        n = src.count("@needs_live_corpus") + src.count("require_live_corpus()")
        if n:
            out[f.name] = n
    return out


class TheGateIsKeyedOnTheCorpusAndNothingElse(unittest.TestCase):

    def test_it_reads_one_path_and_that_path_is_the_corpus(self):
        self.assertEqual(REPO / "audit", LIVE_AUDIT)
        self.assertEqual((LIVE_AUDIT / "state.json").is_file(), HAVE_LIVE_CORPUS)
        print(f"\n  GATE       audit/state.json present: {HAVE_LIVE_CORPUS}")

    def test_the_reason_says_what_is_missing_and_what_to_do(self):
        for phrase in ("audit/state.json", "install.sh", "development repository"):
            self.assertIn(phrase, NO_CORPUS_REASON, phrase)
        print(f"  REASON     {NO_CORPUS_REASON[:96]}…")

    def test_there_is_exactly_one_spelling_of_the_question(self):
        """A sixteenth caller must not invent a seventeenth way to ask. Any test module
        that decides for itself whether the corpus exists is a gate this file cannot
        count, and a gate nobody counts is how a skip becomes permanent."""
        # A LINE scan, not an AST walk. `ast.get_source_segment` re-slices the source
        # for every node, so asking it per node across 86 modules turned a line-level
        # question into a quadratic one that ran for minutes. The question is "does any
        # line decide this for itself", and a line is what can answer it.
        rogue = []
        here = pathlib.Path(__file__).name
        for f in sorted(TESTS.glob("test_*.py")):
            if f.name == here:
                continue
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                # ROOTED AT REPO is the distinguishing property. Without it this
                # caught five innocent lines that check a FIXTURE's state.json
                # (`fx.audit`, `self.root`, a temp `proj`) — a temp project is not
                # this repository's corpus, and asking about one is not inventing a
                # second spelling of the gate.
                if ("state.json" in line and "REPO" in line
                        and (".exists()" in line or ".is_file()" in line)
                        and "HAVE_LIVE_CORPUS" not in line):
                    rogue.append(f"{f.name}:{n}: {line.strip()[:70]}")
        print(f"  ONE NAME   {len(gated_modules())} module(s) use the shared gate; "
              f"{len(set(rogue))} invent their own")
        self.assertEqual([], sorted(set(rogue)))


@unittest.skipUnless(HAVE_LIVE_CORPUS, "no corpus here — nothing to counter-assert")
class TheCorpusGateDoesNotDisarmTheDevelopmentTree(unittest.TestCase):
    """The counter-assertion. Where the corpus IS present, the gate must let everything
    through — a skip here would mean the development battery quietly stopped running the
    very tests that read the real audit history."""

    def test_nothing_is_skipped_for_want_of_a_corpus(self):
        self.assertTrue(HAVE_LIVE_CORPUS)
        # The decorator is `skipUnless(HAVE_LIVE_CORPUS, ...)`, so with the corpus
        # present it is the identity. Assert that directly rather than trusting it.
        sentinel = type("S", (unittest.TestCase,), {})
        self.assertIs(sentinel, needs_live_corpus(sentinel),
                      "the decorator altered a class even though the corpus is here")
        require_live_corpus()          # must NOT raise
        print("\n  NO DISARM  corpus present -> decorator is the identity and "
              "require_live_corpus() does not raise")

    def test_the_gated_modules_are_declared_and_counted(self):
        mods = gated_modules()
        total = sum(mods.values())
        print(f"  GATED      {total} site(s) across {len(mods)} module(s)")
        for name, n in sorted(mods.items()):
            print(f"    {name:<34} {n}")
        self.assertGreater(total, 20, "the gate vanished from the suite")
        self.assertIn("test_okf.py", mods)
        self.assertIn("test_context_capsule.py", mods)


# 0.9.5: two more gates, for two more things an exported tree does not carry.
#
# The corpus gate above answered 105 of the 112 failures. What was left was the same
# shape asked about different subjects: a test grading an INTERNAL DOCUMENT (`docs/` is
# not exported) and a test diffing against a COMMIT ID (the exported tree is a separate
# repository with its own history). Each got a named predicate in `tests/harness.py`
# rather than an inline `exists()`, for the reason this module exists at all — a gate
# nobody counts is how a skip becomes permanent.
GATES = ("needs_live_corpus", "require_live_corpus", "needs_repo_file", "needs_commit")


class TheOtherTwoGatesAreNamedToo(unittest.TestCase):

    def test_every_gate_is_defined_in_the_harness_and_nowhere_else(self):
        src = (TESTS / "harness.py").read_text(encoding="utf-8")
        for name in GATES:
            self.assertIn(f"def {name}(", src, f"{name} is not defined in the harness")
        others = [f.name for f in sorted(TESTS.glob("test_*.py"))
                  if any(f"def {g}(" in f.read_text(encoding="utf-8") for g in GATES)]
        self.assertEqual([], others,
                         f"a test module defines a gate of its own: {others}")

    def test_no_module_asks_these_questions_for_itself(self):
        """The same rogue-spelling scan as above, for the two subjects the new gates
        cover: a hand-rolled `(REPO / "docs" / …).exists()` and a hand-rolled
        `git cat-file` probe are each a second answer to a question that has one."""
        rogue = []
        here = pathlib.Path(__file__).name
        for f in sorted(TESTS.glob("test_*.py")):
            if f.name == here:
                continue
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue          # a comment describes a gate, it does not add one
                docs_probe = ('"docs"' in line or '"docs/' in line) and "REPO" in line \
                    and (".exists()" in line or ".is_file()" in line) \
                    and "HAVE_INTERNAL_DOCS" not in line
                commit_probe = "cat-file" in line and "REPO" in line
                if docs_probe or commit_probe:
                    rogue.append(f"{f.name}:{n}: {stripped[:70]}")
        print(f"\n  TWO MORE   gates named in the harness: "
              f"{', '.join(GATES)}; {len(set(rogue))} module(s) invent their own")
        self.assertEqual([], sorted(set(rogue)))

    def test_the_internal_docs_flag_is_one_name_too(self):
        """`docs/` is shipped file by file, so "is this the development repository?" has
        the same shape as "is the corpus here?" and gets the same treatment: one name in
        the harness, and `test_doc_drift` asking it rather than answering it itself."""
        src = (TESTS / "harness.py").read_text(encoding="utf-8")
        self.assertIn("HAVE_INTERNAL_DOCS =", src)
        users = [f.name for f in sorted(TESTS.glob("test_*.py"))
                 if "HAVE_INTERNAL_DOCS" in f.read_text(encoding="utf-8")
                 and f.name != pathlib.Path(__file__).name]
        self.assertEqual(["test_doc_drift.py"], users)
        print(f"  INTERNAL   docs/ tree present: {HAVE_INTERNAL_DOCS}; "
              f"asked by {', '.join(users)}")

    def test_each_new_gate_actually_gates_something(self):
        """A predicate with no caller is a comment. Both are used, and the modules that
        use them are named so a later deletion has to say which check it dropped."""
        users = {g: [] for g in ("needs_repo_file", "needs_commit")}
        for f in sorted(TESTS.glob("test_*.py")):
            if f.name == pathlib.Path(__file__).name:
                continue
            src = f.read_text(encoding="utf-8")
            for g in users:
                if f"@{g}(" in src:
                    users[g].append(f.name)
        # 0.9.6: test_capsule_contract grades XCHECK.md against the corpus's installed
        # copy, audit/XCHECK.md, which the export does not carry.
        self.assertEqual(["test_benchmark_harness.py", "test_capsule_contract.py",
                          "test_metrics.py", "test_model_routing.py"],
                         sorted(users["needs_repo_file"]))
        self.assertEqual(["test_hardening.py"], sorted(users["needs_commit"]))
        for g, mods in sorted(users.items()):
            print(f"  {g:<16} {', '.join(mods)}")


# 2026-10-16: the gate can also hide by being too WIDE. PR #2 reached GitHub with a
# boundary check disarmed because `OneValidatedReadBoundary` was gated as a CLASS while
# four of its five tests never read the corpus; an audit of every class gate then found
# 41 more such tests in 13 classes. A class gate is a claim about every method under it,
# so this asserts the claim, statically, in both trees.

#: What a corpus read looks like in source. `REPO` passed bare is a project root
#: (`read_events(REPO)`), so it counts; `REPO / "xcheck"` and `relative_to(REPO)` do not.
CORPUS_READ = re.compile(
    r'REPO\s*/\s*"audit"|Path\(\s*REPO\s*,\s*"audit"|\bLIVE_AUDIT\b|require_live_corpus\('
    r'|(?<!relative_to)\(\s*(?:str\()?REPO\)?\s*[,)](?!!r)|cwd=(?:str\()?REPO\b')
ATTR = re.compile(r"\b(?:self|cls|self\.__class__)\.(\w+)")


def corpus_blind_methods(src):
    """The tests under a class-level corpus gate that reach no corpus read at all.

    A read is reached directly, through a module-level name or helper that reads (to a
    fixed point), through an attribute a setup method or the class body derived from a
    read, or through a class helper that does. A setup STATEMENT that reads without
    assigning (`shutil.copy(REAL_STREAM, ...)` in `setUp`) reaches every method.

    Lenient by construction: every unresolved case counts as a read — a branch taken
    only for one argument, `build_sheet(REPO)` — so a miss is possible and an accusation
    of a test that does read the corpus is not. Measured on ed0d0b7: 39 of the 43
    tests a manual audit cleared are flagged, none it kept."""
    lines = src.splitlines()

    def text(node):
        return "\n".join(lines[node.lineno - 1:node.end_lineno])

    tree = ast.parse(src)
    names = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            names[node.name] = node
        elif isinstance(node, ast.Assign):
            names.update((t.id, node) for t in node.targets if isinstance(t, ast.Name))
    corpus = set()

    def reads(node):
        return bool(CORPUS_READ.search(text(node))) or any(
            isinstance(n, ast.Name) and n.id in corpus for n in ast.walk(node))

    while more := {k for k, n in names.items() if k not in corpus and reads(n)}:
        corpus |= more

    blind = []
    for cls in tree.body:
        if not (isinstance(cls, ast.ClassDef) and any(
                getattr(d, "id", None) == "needs_live_corpus" for d in cls.decorator_list)):
            continue
        tainted, every = set(), False
        for st in cls.body:
            if isinstance(st, ast.Assign) and reads(st):
                tainted |= {t.id for t in st.targets if isinstance(t, ast.Name)}
        fns = {f.name: f for f in cls.body if isinstance(f, ast.FunctionDef)}
        for setup in ("setUpClass", "setUp"):
            for st in getattr(fns.get(setup), "body", []):
                used = set(ATTR.findall(text(st)))
                if not (reads(st) or used & tainted):
                    continue
                if isinstance(st, ast.Assign):
                    tainted |= set(ATTR.findall("\n".join(text(t) for t in st.targets)))
                elif reads(st):
                    every = True
                else:
                    tainted |= used
        if every:
            continue
        helpers = {k for k in fns if not k.startswith("test")} - {"setUp", "setUpClass"}
        hot = set()

        def reaches(f):
            return reads(f) or bool(set(ATTR.findall(text(f))) & (tainted | hot))

        while more := {k for k in helpers - hot if reaches(fns[k])}:
            hot |= more
        blind += [f"{cls.name}.{k}" for k, f in fns.items()
                  if k.startswith("test") and not reaches(f)]
    return blind


class NoClassGateCoversATestThatReadsNoCorpus(unittest.TestCase):
    """Runs in BOTH trees: it reads source, never the corpus — and the exported tree is
    where an over-wide gate does its damage."""

    # The needle is assembled, not written, so these fixtures do not count as gate sites
    # in `gated_modules()` above.
    GATE = "@" + "needs_live_corpus"

    def test_every_class_gated_test_reaches_a_corpus_read(self):
        blind = []
        for f in sorted(TESTS.glob("test_*.py")):
            blind += [f"{f.name}::{m}"
                      for m in corpus_blind_methods(f.read_text(encoding="utf-8"))]
        print(f"\n  CLASS GATES {len(blind)} gated test(s) that read no corpus")
        self.assertEqual([], blind, "gate these per METHOD, and guard the class setup on "
                                    "HAVE_LIVE_CORPUS, so the rest run in the exported tree")

    def test_POSITIVE_CONTROL_the_shape_PR_2_hid_behind_is_flagged(self):
        """b15972c's case: the class setup reads the real digest, one test uses it, the
        other runs on a fixture. Only the fixture test is named."""
        src = (f"REAL_STREAM = REPO / \"audit\" / \"events.jsonl\"\n\n{self.GATE}\n"
               "class C(unittest.TestCase):\n"
               "    @classmethod\n"
               "    def setUpClass(cls):\n"
               "        cls.real_digest = digest(REAL_STREAM)\n"
               "    def test_on_a_fixture(self):\n"
               "        fx = Fixture(state_doc())\n"
               "    def test_the_real_stream(self):\n"
               "        self.assertEqual(self.real_digest, digest(REAL_STREAM))\n")
        self.assertEqual(["C.test_on_a_fixture"], corpus_blind_methods(src))

    def test_CONTROL_a_setup_that_copies_the_corpus_reaches_every_test(self):
        """`TheRecoveryDrill`'s shape: `setUp` copies the real stream into each test's
        temp tree, so a test that only names `self.tmp` still reads the corpus."""
        src = (f"{self.GATE}\n"
               "class C(unittest.TestCase):\n"
               "    def setUp(self):\n"
               "        self.tmp = Path(tempfile.mkdtemp())\n"
               "        shutil.copy(REPO / \"audit\" / \"events.jsonl\", self.tmp)\n"
               "    def test_a_copy(self):\n"
               "        ledger.status(self.tmp)\n")
        self.assertEqual([], corpus_blind_methods(src))

