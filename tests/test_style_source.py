"""The ELI5 style has one source, and its wording is legal in a shipped document.

`styles/eli5.md` is the single definition of how xcheck talks to a human. Two scanners
already read every shipped document in this repository, and a probe run while this file
was being written showed one of them firing on a plausible draft:

    scan_claims("… the read-only roles stay read-only.")
      ->  6 [UNSCOPED] surface and the read-only roles stay read-only.

So the block's wording is validated HERE, against the real scanners — imported, not
reimplemented — rather than discovered later when it is already copied into six files.
The mitigation for a style block that trips the containment scanner is never to scope the
block or to exclude it: a style block has no business making a containment claim at all.
"""

import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# Through the package, not by bare module name — the spelling that resolves the same way
# under `python -m unittest discover` as it does running this file directly.
#
# It used to be load-bearing for a second reason: `ci/preflight.py` derives CI's
# dependency set from the imports in `ci/*.py` and `tests/*.py`, and a bare `import
# test_launch_surfaces` has the top-level name `test_launch_surfaces`, which it read as a
# package CI must install — refusing with `pip install "test_launch_surfaces"`. That is
# fixed at the source: preflight now asks whether `tests/<name>.py` exists and prints
# every name it reclassifies (`tests/test_ci_contract.py`,
# `ASiblingTestImportIsNotAPackageToInstall`). The package spelling stays because it is
# the more correct one, not because the other one breaks the build.
from tests import test_executable_contract as ec
from tests import test_launch_surfaces as ls
from xcheck import write

REPO = pathlib.Path(__file__).resolve().parent.parent
STYLE = REPO / "styles" / "eli5.md"
RENDERER = REPO / "ci" / "render-style.py"
BEGIN = "<!-- xcheck:style:begin -->"
END = "<!-- xcheck:style:end -->"


def source():
    return STYLE.read_text()


def body():
    """The bytes the six skills carry — markers excluded, exactly as the renderer cuts."""
    src = source()
    b = src.index(BEGIN) + len(BEGIN)
    return src[b:src.index(END, b)].strip("\n")


class TheStyleHasOneSource(unittest.TestCase):
    """Criteria 1-4: the file exists and says the things it has to say."""

    def test_the_file_has_frontmatter_and_a_delimited_body(self):
        self.assertTrue(STYLE.exists(), f"{STYLE} does not exist")
        src = source()
        lines = src.splitlines()
        self.assertEqual("---", lines[0].strip(), "no opening frontmatter fence")
        close = next(i for i, ln in enumerate(lines[1:], 1) if ln.strip() == "---")
        fm = "\n".join(lines[1:close])
        for key in ("name:", "description:"):
            self.assertIn(key, fm, f"frontmatter has no `{key}` — an operator who wants "
                                   f"this file in ~/.claude/output-styles/ needs both")
        self.assertEqual(1, src.count(BEGIN), "exactly one begin marker")
        self.assertEqual(1, src.count(END), "exactly one end marker")
        self.assertLess(src.index(BEGIN), src.index(END), "markers are inverted")
        self.assertGreater(src.index(BEGIN), src.index("---", 4),
                           "the block must sit BELOW the frontmatter — what the OpenCode "
                           "stripper drops must never be load-bearing")
        print(f"\n  style source: {STYLE.relative_to(REPO)}, "
              f"{len(src.encode())} bytes, block {len(body().encode())} bytes")

    def test_compression_is_revoked_by_name_and_by_class(self):
        # Two fixed literals. A block that names only caveman is obsolete the first time
        # a differently-named mode ships; naming the class covers the next one with no
        # edit. "or an equivalent phrase" is not something a test can check, so it is not
        # what this asserts.
        self.assertIn("caveman", body(), "the block must name caveman explicitly")
        self.assertIn("output-compression mode", body(),
                      "the block must name the CLASS, not only today's member")

    def test_the_revocation_uses_the_phrase_those_modes_accept(self):
        # caveman's own rule: it stays active until told exactly this.
        self.assertIn("normal mode", body(),
                      "a revocation phrased in words the mode does not recognise is not "
                      "a revocation")

    def test_each_carve_out_is_named(self):
        # One assertion per carve-out, so a failure says WHICH one went missing rather
        # than "a carve-out is absent".
        b = body()
        self.assertIn("Quoted evidence", b)
        self.assertIn("Finding ids", b)
        self.assertIn("§5 statuses", b)
        self.assertIn("Copy-paste commands", b)


class TheScannersAlreadyShippedAgree(unittest.TestCase):
    """Criteria 5-7: the wording is legal in a document these scanners read."""

    def test_the_block_makes_no_containment_claim(self):
        claims = ls.scan_claims(body())
        for n, scope, s in claims:
            print(f"    line {n} scope={scope or '[UNSCOPED]'}: {s}")
        print(f"\n  test_launch_surfaces.scan_claims: {len(claims)} containment claim(s)")
        self.assertEqual([], claims,
                         "a style block that makes a containment claim is a bug in the "
                         "block — fix the wording, never the scanner")

    def test_the_block_orders_no_state_write(self):
        fields = ec.state_field_names()
        off, neg = ec.imperatives_naming_state(body(), fields)
        print(f"  test_executable_contract.imperatives_naming_state: "
              f"{len(off)} offender(s), {len(neg)} negated, over {len(fields)} field "
              f"name(s) derived from state.py at run time")
        for s in off:
            print(f"    OFFENDER: {s}")
        for s in neg:
            print(f"    NEGATED : {s}")
        self.assertEqual([], off)
        self.assertEqual([], neg, "even a negated hit means the block is talking about "
                                  "machine state, which is not its subject")

    def test_the_block_names_no_state_verb_at_all(self):
        # Flat absence, read from the module rather than hand-typed. Binary, where "does
        # this name a verb AS AN INSTRUCTION" would be a judgement about grammatical mood
        # that no test can make.
        hits = sorted(v for v in write.VERBS if v in body())
        print(f"  write.VERBS: {len(write.VERBS)} verb(s) checked, {len(hits)} present")
        self.assertEqual([], hits,
                         f"the style block has no reason to name a state verb in any "
                         f"role; found {hits}")


class TheRendererIsAGateNotADecoration(unittest.TestCase):
    """Criterion 8: --check passes on a rendered tree and fails on a changed byte.

    Both arms run, against a copy in a system temp dir. An arm nobody has watched fail
    proves nothing about the arm that passes.
    """

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="xcheck-style-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "ci").mkdir()
        shutil.copy2(RENDERER, self.tmp / "ci" / "render-style.py")
        shutil.copytree(REPO / "styles", self.tmp / "styles")
        shutil.copytree(REPO / "skills", self.tmp / "skills")

    def run_renderer(self, *args):
        return subprocess.run(
            [sys.executable, str(RENDERER), "--root", str(self.tmp), *args],
            capture_output=True, text=True)

    def test_check_is_clean_on_a_rendered_tree_and_red_on_one_changed_byte(self):
        rendered = self.run_renderer()
        self.assertEqual(0, rendered.returncode, rendered.stdout + rendered.stderr)

        clean = self.run_renderer("--check")
        print(f"\n  --check on a freshly rendered tree: rc={clean.returncode}")
        print("    " + clean.stdout.strip().replace("\n", "\n    "))
        self.assertEqual(0, clean.returncode,
                         "--check disagreed with the render that just ran")

        victim = sorted((self.tmp / "skills").glob("*/SKILL.md"))[0]
        text = victim.read_text()
        at = text.index("normal mode")
        victim.write_text(text[:at] + "normal modE" + text[at + len("normal mode"):])

        dirty = self.run_renderer("--check")
        print(f"  --check after one byte changed inside {victim.parent.name}: "
              f"rc={dirty.returncode}")
        for line in dirty.stdout.splitlines():
            if line.startswith(("+", "-")) and "normal mod" in line:
                print(f"    {line}")
        self.assertEqual(1, dirty.returncode,
                         "one changed byte inside a block did not redden --check, so the "
                         "gate is a substring check wearing an identity check's name")
        self.assertIn("DRIFT in 1", dirty.stdout)
        self.assertIn(victim.parent.name, dirty.stdout)

    def test_rendering_twice_changes_nothing_the_second_time(self):
        """The premise of a drift check is that the renderer agrees with itself.

        It did not, once: the INSERT path kept the blank line before the procedure and
        the REPLACE path ate it, so every render was followed by a `--check` reporting
        drift in all six files forever. Caught by a counterfactual's CONTROL arm, which
        is the only thing that distinguished 'the gate bites' from 'the tree is red'.
        """
        first = self.run_renderer()
        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        after_one = {f: f.read_bytes() for f in sorted((self.tmp / "skills").glob("*/SKILL.md"))}

        second = self.run_renderer()
        self.assertEqual(0, second.returncode, second.stdout + second.stderr)
        moved = [f.parent.name for f, b in after_one.items() if f.read_bytes() != b]
        print(f"  second render over the same tree: {len(moved)} file(s) changed "
              f"{'(none — idempotent)' if not moved else moved}")
        self.assertEqual([], moved, "the renderer is not idempotent, so --check can "
                                    "never agree with a render")
        self.assertIn("updated 0, already current 6", second.stdout)

    def test_a_tree_with_no_skills_refuses_instead_of_passing_empty(self):
        # A check that scans nothing must not report success. This is the control that
        # keeps the clean arm above meaningful.
        shutil.rmtree(self.tmp / "skills")
        (self.tmp / "skills").mkdir()
        empty = self.run_renderer("--check")
        print(f"  --check over zero skills: rc={empty.returncode}")
        self.assertNotEqual(0, empty.returncode,
                            "scanning zero files reported success")
        self.assertIn("nothing was checked", empty.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
