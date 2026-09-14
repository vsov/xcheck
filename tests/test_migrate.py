"""Phase 6: `xcheck migrate`, the one-shot operator-run converter.

The operator's Stage-1 decision was "разовый конвертер" — explicit, reversible,
never automatic. Each of those three words is a test here, because each is a way
the converter could quietly destroy an audit:

- **explicit** — no command runs it on contact; only typing it does;
- **reversible** — the pre-migration bytes come back byte for byte, without git,
  because a third-party tree converted outside a repository has no history;
- **one-shot** — it refuses a tree that already has a state document rather than
  overwriting one.

And the property under all three: a legacy tree does not become a valid state
document by having its problems dropped. Every refusal below is paired with a
control on a tree that differs only in the poison, so "it refused" cannot mean
"it fell over for an unrelated reason".
"""

import hashlib
import unittest

from tests.harness import (LegacyFixture, audit_md, finding, ledger, pass_report,
                           queue_entry, xcheck_submodule)

migrate_mod = xcheck_submodule("migrate")
state_mod = xcheck_submodule("state")
views = xcheck_submodule("views")


def legacy(findings=None, **over):
    """A complete, convertible legacy tree — the control every poison starts from."""
    kw = dict(audit_text=audit_md(queue_body=queue_entry(1, checked=True)),
              ledger_text=ledger([("F-0001", "a finding", "major", "accepted")]),
              findings=findings or {"F-0001.md": finding("F-0001", "accepted")},
              passes={"P-01-x.md": pass_report("P-01")})
    kw.update(over)
    return LegacyFixture(**kw)


def manifest(audit):
    """Every file's path, mtime and bytes. What `--dry-run` must not change."""
    return {str(p.relative_to(audit)): (p.stat().st_mtime_ns, p.read_bytes())
            for p in sorted(audit.rglob("*")) if p.is_file()}


class DryRunWritesNothing(unittest.TestCase):

    def test_not_a_byte_and_not_an_mtime(self):
        fx = legacy()
        self.addCleanup(fx.cleanup)
        before = manifest(fx.audit)
        code, out = fx.run("--dry-run", "migrate")
        self.assertEqual(code, 0)
        self.assertEqual(before, manifest(fx.audit))
        self.assertFalse((fx.audit / "state.json").exists())
        self.assertIn("nothing written", out)

    def test_control_the_same_tree_without_the_flag_does_write(self):
        """Without this, a converter that silently did nothing at all would pass
        the test above."""
        fx = legacy()
        self.addCleanup(fx.cleanup)
        before = manifest(fx.audit)
        self.assertEqual(fx.run("migrate")[0], 0)
        self.assertNotEqual(before, manifest(fx.audit))
        self.assertTrue((fx.audit / "state.json").exists())


class TheConvertedTreeIsReadable(unittest.TestCase):

    def setUp(self):
        self.fx = legacy()
        self.addCleanup(self.fx.cleanup)
        self.body_before = self._body_sha()
        self.assertEqual(self.fx.run("migrate")[0], 0)

    def _body_sha(self):
        text = (self.fx.audit / "findings" / "F-0001.md").read_text(encoding="utf-8")
        m = views.FRONTMATTER_RE.match(text)
        return hashlib.sha256((text[m.end():] if m else text).encode()).hexdigest()

    def test_the_state_document_loads_and_lint_is_clean(self):
        st = state_mod.load_state(self.fx.audit)
        self.assertEqual(len(st.findings), 1)
        self.assertEqual(st.findings[0].id, "F-0001")
        code, out = self.fx.run("lint")
        self.assertEqual(code, 0, out)

    def test_the_evidence_body_is_byte_identical(self):
        self.assertEqual(self.body_before, self._body_sha())

    def test_the_views_it_wrote_are_already_in_sync(self):
        """The converter regenerates the mirrors itself. If it did not, the very
        next command would refuse the tree it had just produced."""
        self.assertEqual(views.verify_views(state_mod.load_state(self.fx.audit),
                                            self.fx.audit), [])


class ItIsReversible(unittest.TestCase):

    def test_the_backup_restores_the_tree_byte_for_byte(self):
        fx = legacy()
        self.addCleanup(fx.cleanup)
        before = {k: v[1] for k, v in manifest(fx.audit).items()}   # bytes only
        self.assertEqual(fx.run("migrate")[0], 0)
        backup = fx.audit / migrate_mod.BACKUP_DIR
        self.assertTrue(backup.is_dir())
        for src in sorted(backup.rglob("*")):
            if src.is_file():
                (fx.audit / src.relative_to(backup)).write_bytes(src.read_bytes())
        (fx.audit / "state.json").unlink()
        after = {k: v[1] for k, v in manifest(fx.audit).items()
                 if not k.startswith(migrate_mod.BACKUP_DIR)}
        self.assertEqual(before, after)


class ItRefusesRatherThanDrops(unittest.TestCase):

    def _refuses(self, fx, needle):
        code, out = fx.run("--dry-run", "migrate")
        self.assertNotEqual(code, 0, f"converted a tree it should refuse:\n{out}")
        self.assertIn(needle, out)
        self.assertIn("Nothing was written", out)
        return out

    def test_a_second_migration_does_not_overwrite_the_first(self):
        fx = legacy()
        self.addCleanup(fx.cleanup)
        self.assertEqual(fx.run("migrate")[0], 0)
        first = (fx.audit / "state.json").read_bytes()
        code, out = fx.run("migrate")
        self.assertNotEqual(code, 0)
        self.assertEqual(first, (fx.audit / "state.json").read_bytes())

    def test_a_unit_that_resolves_to_nothing_stops_the_whole_conversion(self):
        fx = legacy(findings={"F-0001.md": finding("F-0001", "accepted").replace(
            "unit: U01", "unit: some/file/nobody/catalogued.md")})
        self.addCleanup(fx.cleanup)
        self._refuses(fx, "neither a unit id nor a material")

    def test_control_the_same_tree_with_a_catalogued_unit_converts(self):
        fx = legacy()
        self.addCleanup(fx.cleanup)
        self.assertEqual(fx.run("--dry-run", "migrate")[0], 0)

    def test_a_queue_line_that_is_not_a_full_entry_is_a_defect_not_a_skip(self):
        """The ouroboros-2 lesson. Its 32 entries predate the `; stop:` clause, so a
        strict parser reads that queue as EMPTY — and an empty queue is a state
        document that reports the audit finished. Silence is the failure here."""
        fx = legacy(audit_text=audit_md(
            queue_body="- [x] P-01 — invariants × U01: charter with no stop clause"))
        self.addCleanup(fx.cleanup)
        out = self._refuses(fx, "names a pass but is not a full entry")
        self.assertIn("P-01", out)

    def test_control_the_same_queue_line_with_its_stop_clause_converts(self):
        fx = legacy(audit_text=audit_md(queue_body=queue_entry(1, checked=True)))
        self.addCleanup(fx.cleanup)
        code, out = fx.run("--dry-run", "migrate")
        self.assertEqual(code, 0, out)
        self.assertIn("queue            1", out)

    def test_a_checked_pass_with_no_canonical_report_is_a_defect(self):
        fx = legacy(passes={})
        self.addCleanup(fx.cleanup)
        self._refuses(fx, "canonical report(s) match")


class NormalisationsAreReportedNotSilent(unittest.TestCase):

    def test_a_material_resolved_to_a_unit_id_is_named(self):
        """10 of ouroboros-3's 29 findings write `unit: XCHECK.md` — the material,
        not the unit id. Resolving it is right; resolving it invisibly is not, since
        the operator is the one who has to agree the resolution is correct."""
        fx = legacy(findings={"F-0001.md": finding("F-0001", "accepted").replace(
            "unit: U01", "unit: src/core.py")})
        self.addCleanup(fx.cleanup)
        code, out = fx.run("--dry-run", "migrate")
        self.assertEqual(code, 0, out)
        self.assertIn("is a MATERIAL, not a unit id", out)
        self.assertIn("resolves it to U01", out)


if __name__ == "__main__":
    unittest.main()
