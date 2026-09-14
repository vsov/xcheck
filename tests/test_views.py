"""Phase 6: the Markdown is a mirror, and a mirror that lies stops the tool.

Two properties, and the second is the one that matters.

**The renderer never touches evidence.** A finding file is a machine block on top
of a human argument. `write_views` replaces the block and copies the rest byte for
byte — asserted with sha256 over the body, not by reading the output and agreeing
with it.

**Drift is refused, by every command, at consumer level.** Not "verify_views
returns a Drift" — `xcheck lint`, `status`, `metrics` and `next` each exit
non-zero and name the file and the line. That distinction is the whole point of
retro-6: a predicate that rejects an input proves nothing about whether any
command asks it. Each refusal is paired with a control on the same tree, so a
command failing for an unrelated reason cannot pass as the guard firing.
"""

import hashlib
import unittest

from tests.harness import (Fixture, finding_record, queue_pass, state_doc,
                           xcheck_submodule)

views = xcheck_submodule("views")
state_mod = xcheck_submodule("state")

BODY = "# F-0001\n\n## Evidence\n\nThe quote, the place, and the norm it violates.\n"


def _body_sha(path):
    """sha256 of everything BELOW the frontmatter block — the evidence half."""
    text = path.read_text(encoding="utf-8")
    m = views.FRONTMATTER_RE.match(text)
    return hashlib.sha256((text[m.end():] if m else text).encode()).hexdigest()


class EvidenceIsNeverRewritten(unittest.TestCase):

    def setUp(self):
        self.fx = Fixture(state_doc(findings=[finding_record("F-0001", "accepted")],
                                    queue=[queue_pass("P-01", done=True)]))
        self.addCleanup(self.fx.cleanup)
        self.path = self.fx.audit / "findings" / "F-0001.md"

    def test_the_body_survives_a_render_byte_for_byte(self):
        self.fx.body("findings/F-0001.md", BODY)
        before = _body_sha(self.path)
        views.write_views(state_mod.load_state(self.fx.audit), self.fx.audit)
        self.assertEqual(before, _body_sha(self.path))
        self.assertIn("## Evidence", self.path.read_text(encoding="utf-8"))

    def test_a_file_with_no_block_gets_one_without_losing_its_text(self):
        """The pre-migration shape: prose with no frontmatter at all. Prepending is
        the only safe move — guessing where a block was *meant* to start would edit
        evidence to fit a parser."""
        self.fx.write("findings/F-0001.md", "no frontmatter here, only argument\n")
        views.write_views(state_mod.load_state(self.fx.audit), self.fx.audit)
        text = self.path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("no frontmatter here, only argument", text)

    def test_rendering_twice_changes_nothing(self):
        """Idempotence is what makes `render-views` safe to type when unsure."""
        first = self.path.read_bytes()
        views.write_views(state_mod.load_state(self.fx.audit), self.fx.audit)
        self.assertEqual(first, self.path.read_bytes())


class NextIsDerivedNotStored(unittest.TestCase):
    """Phase 4 found `next` on 0 of 160 real finding files — it only ever existed
    as a LEDGER column, which is exactly how F-0005/F-0014/F-0025 drift got in.
    The rendered column comes from `next_owner(status)`, so the two cannot part."""

    def test_the_ledger_column_follows_the_status(self):
        fx = Fixture(state_doc(
            findings=[finding_record("F-0001", "accepted"),
                      finding_record("F-0002", "fixed")],
            queue=[queue_pass("P-01", done=True)]))
        self.addCleanup(fx.cleanup)
        rows = {ln.split("|")[1].strip(): ln
                for ln in (fx.audit / "LEDGER.md").read_text().splitlines()
                if ln.startswith("| F-")}
        self.assertIn("Remediator", rows["F-0001"])
        self.assertIn("Verifier", rows["F-0002"])


class DriftStopsEveryCommand(unittest.TestCase):
    """The consumer-level half. `next` is included on purpose: a read-only refusal
    that a writing command walks past is F-0147 with a new subject."""

    CONSUMERS = ("lint", "status", "metrics", "next")

    def _fx(self):
        fx = Fixture(state_doc(findings=[finding_record("F-0001", "accepted")],
                               queue=[queue_pass("P-01", done=True)]))
        self.addCleanup(fx.cleanup)
        return fx

    def test_control_an_untouched_tree_is_clean_for_all_four(self):
        fx = self._fx()
        for cmd in self.CONSUMERS:
            with self.subTest(cmd=cmd):
                code, out = fx.run(cmd)
                self.assertNotIn("disagree with state.json", out)

    def test_a_hand_edited_ledger_row_stops_all_four(self):
        fx = self._fx()
        led = fx.audit / "LEDGER.md"
        led.write_text(led.read_text().replace("| F-0001 |", "| F-0001 | EDITED", 1),
                       encoding="utf-8")
        for cmd in self.CONSUMERS:
            with self.subTest(cmd=cmd):
                code, out = fx.run(cmd)
                self.assertNotEqual(code, 0, f"{cmd} ran on a drifted ledger")
                self.assertIn("LEDGER.md:", out)
                self.assertIn("disagree with state.json", out)
                self.assertIn("xcheck render-views", out)

    def test_a_hand_edited_frontmatter_field_stops_all_four(self):
        fx = self._fx()
        p = fx.audit / "findings" / "F-0001.md"
        p.write_text(p.read_text().replace("severity: major", "severity: critical", 1),
                     encoding="utf-8")
        for cmd in self.CONSUMERS:
            with self.subTest(cmd=cmd):
                code, out = fx.run(cmd)
                self.assertNotEqual(code, 0, f"{cmd} ran on a drifted finding file")
                self.assertIn("findings/F-0001.md:", out)
                self.assertIn("severity: critical", out)
                self.assertIn("severity: major", out)

    def test_a_deleted_ledger_is_drift_not_silence(self):
        """An absent mirror is not "no claim" — the next person to open the tree
        reads an empty ledger as an empty audit."""
        fx = self._fx()
        (fx.audit / "LEDGER.md").unlink()
        code, out = fx.run("lint")
        self.assertNotEqual(code, 0)
        self.assertIn("the file is missing", out)

    def test_the_refusal_never_silently_re_syncs(self):
        """The edit is still on disk after the refusal. Discarding it quietly is the
        same class of failure as accepting it: both decide for the human."""
        fx = self._fx()
        led = fx.audit / "LEDGER.md"
        led.write_text(led.read_text().replace("| F-0001 |", "| F-0001 | EDITED", 1),
                       encoding="utf-8")
        fx.run("lint")
        self.assertIn("EDITED", led.read_text(encoding="utf-8"))


class RenderViewsIsTheOnlyWayOut(unittest.TestCase):

    def test_it_rewrites_the_mirror_and_names_what_it_destroyed(self):
        fx = Fixture(state_doc(findings=[finding_record("F-0001", "accepted")],
                               queue=[queue_pass("P-01", done=True)]))
        self.addCleanup(fx.cleanup)
        led = fx.audit / "LEDGER.md"
        led.write_text(led.read_text().replace("| F-0001 |", "| F-0001 | EDITED", 1),
                       encoding="utf-8")
        code, out = fx.run("render-views")
        self.assertEqual(code, 0)
        self.assertIn("had drifted", out)
        self.assertIn("EDITED", out, "the destroyed edit was not shown to the operator")
        self.assertNotIn("EDITED", led.read_text(encoding="utf-8"))
        self.assertEqual(fx.run("lint")[0], 0)

    def test_it_does_not_refuse_on_the_drift_it_exists_to_fix(self):
        """A fail-closed check on the repair verb is a deadlock, not a guard."""
        fx = Fixture(state_doc(findings=[finding_record("F-0001", "accepted")],
                               queue=[queue_pass("P-01", done=True)]))
        self.addCleanup(fx.cleanup)
        (fx.audit / "LEDGER.md").unlink()
        self.assertEqual(fx.run("render-views")[0], 0)


if __name__ == "__main__":
    unittest.main()
