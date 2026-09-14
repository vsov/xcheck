"""`install.sh` ships it, `xcheck upgrade` refreshes it — and neither one gets to
decide that the operator's edit was unimportant.

F-0146 named the defect as "no sync owner" for the installed copy. The tempting fix
is an upgrade that overwrites, which converts a drift problem into a data-loss
problem: `audit/XCHECK.md` is the file an operator most reasonably annotates with a
house rule, and `audit/AUDIT.md` is theirs outright.

So the tests here are mostly about REFUSAL, and every one of them runs the real
`install.sh` first. A test that hand-built the manifest would be checking upgrade
against its own assumptions about the installer rather than against the installer.
"""

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.harness import REPO, xcheck_module  # noqa: F401  (path bootstrap)

from xcheck import __version__
from xcheck.upgrade import SHIPPED, upgrade
from xcheck.state import SCHEMA_VERSION

HOUSE_RULE = "\n<!-- house rule: no remediation on Fridays -->\n"


def install(target):
    p = subprocess.run(["sh", str(REPO / "install.sh"), str(target)],
                       cwd=str(REPO), capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise AssertionError(f"install.sh failed: {p.stdout}\n{p.stderr}")
    return p.stdout


class Installed:
    """A throwaway project with a real installed copy. Temp dir, never the tree."""

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="xcheck-upgrade-"))
        install(self.dir)
        self.audit = self.dir / "audit"
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run(self, dry_run=False):
        lines = []
        rc = upgrade(self.dir, dry_run=dry_run, root=REPO, out=lines.append)
        return rc, "\n".join(lines)

    def stamp(self, **fields):
        p = self.audit / ".provenance.json"
        prov = json.loads(p.read_text())
        prov.update(fields)
        p.write_text(json.dumps(prov, indent=2) + "\n")


class TheInstallerAndTheUpgraderShipTheSameSet(unittest.TestCase):
    """Two lists that disagree would install a file `upgrade` cannot see — and a file
    outside the manifest is a file nothing ever refreshes or protects."""

    def test_shipped_lists_agree(self):
        sh = (REPO / "install.sh").read_text(encoding="utf-8")
        m = re.search(r'^SHIPPED="([^"]+)"', sh, re.M)
        self.assertIsNotNone(m, "install.sh no longer declares a SHIPPED list")
        self.assertEqual(sorted(m.group(1).split()), sorted(SHIPPED))

    def test_every_shipped_file_exists_in_the_repo(self):
        for rel in SHIPPED:
            self.assertTrue((REPO / rel).is_file(), f"{rel} is shipped but not present")

    def test_the_installer_writes_a_manifest_entry_per_shipped_file(self):
        with Installed() as inst:
            names = [ln.split(None, 1)[1].strip()
                     for ln in (inst.audit / "MANIFEST.sha256").read_text().splitlines()]
            self.assertEqual(sorted(names), sorted(SHIPPED))


class TheUpgradeRefusesWhatTheOperatorTouched(unittest.TestCase):

    def test_a_modified_core_is_named_and_left_exactly_as_it_is(self):
        with Installed() as inst:
            core = inst.audit / "XCHECK.md"
            core.write_text(core.read_text() + HOUSE_RULE)
            before = core.read_bytes()
            inst.stamp(xcheck_version="0.7.1")
            rc, out = inst.run()
            self.assertEqual(core.read_bytes(), before, "the operator's edit was lost")
            self.assertIn("refusing to overwrite", out)
            self.assertIn("audit/XCHECK.md", out)
            self.assertEqual(rc, 1, "a refusal must reach the exit code, not only stdout")

    def test_audit_md_is_never_shipped_so_it_is_never_a_candidate(self):
        # AUDIT.md is the operator's from the moment it exists: the installer creates
        # it once from a template and upgrade has no entry for it at all. The strongest
        # form of "will not overwrite" is "cannot address".
        self.assertNotIn("AUDIT.md", SHIPPED)
        with Installed() as inst:
            plan = inst.audit / "AUDIT.md"
            plan.write_text("# our own audit plan\n")
            rc, out = inst.run()
            self.assertEqual(plan.read_text(), "# our own audit plan\n")
            self.assertIn("refusing to overwrite", out)
            self.assertIn("audit/AUDIT.md", out)
            self.assertIn("never shipped, never overwritten, at any version", out)
            # …and it is not an ERROR: nothing xcheck was asked to refresh was refused.
            self.assertEqual(rc, 0)

    def test_a_refusal_survives_a_second_upgrade(self):
        # The manifest must keep the OLD digest for a refused file. Restamping it with
        # what is on disk would silently adopt the operator's version as "shipped",
        # and the next upgrade would overwrite it without a word.
        with Installed() as inst:
            core = inst.audit / "XCHECK.md"
            core.write_text(core.read_text() + HOUSE_RULE)
            (inst.audit / "templates" / "pass.md").unlink()   # force a real write
            inst.run()
            rc, out = inst.run()
            self.assertEqual(rc, 1)
            self.assertIn("audit/XCHECK.md", out)

    def test_a_file_with_no_manifest_entry_is_treated_as_the_operators(self):
        with Installed() as inst:
            (inst.audit / "MANIFEST.sha256").unlink()
            core = inst.audit / "XCHECK.md"
            core.write_text("someone's own core\n")
            rc, out = inst.run()
            self.assertEqual(core.read_text(), "someone's own core\n")
            self.assertIn("cannot prove xcheck shipped this file", out)
            self.assertEqual(rc, 1)


class TheUpgradeRefreshesWhatItOwns(unittest.TestCase):

    def test_an_unmodified_file_is_restored_and_reported_first(self):
        with Installed() as inst:
            gone = inst.audit / "templates" / "pass.md"
            gone.unlink()
            rc, out = inst.run()
            self.assertEqual(rc, 0)
            self.assertEqual(gone.read_bytes(), (REPO / "templates/pass.md").read_bytes())
            # The report precedes the write in the output, because that is the order in
            # which they happened.
            self.assertLess(out.index("what this changes"), out.index("upgraded:"))

    def test_dry_run_writes_nothing(self):
        with Installed() as inst:
            gone = inst.audit / "templates" / "pass.md"
            gone.unlink()
            prov_before = (inst.audit / ".provenance.json").read_bytes()
            rc, out = inst.run(dry_run=True)
            self.assertFalse(gone.exists())
            self.assertEqual((inst.audit / ".provenance.json").read_bytes(), prov_before)
            self.assertIn("dry-run: nothing was written", out)

    def test_an_up_to_date_copy_says_so_and_exits_clean(self):
        with Installed() as inst:
            rc, out = inst.run()
            self.assertEqual(rc, 0)
            self.assertIn("nothing to do", out)

    def test_identical_files_with_a_stale_stamp_are_restamped(self):
        # Nothing to copy, but the record was wrong. Restamping here is reporting what
        # the digests already prove; NOT restamping would leave a permanent lie.
        with Installed() as inst:
            inst.stamp(xcheck_version="0.7.1")
            rc, out = inst.run()
            self.assertEqual(rc, 0)
            self.assertIn("restamped from 0.7.1", out)
            prov = json.loads((inst.audit / ".provenance.json").read_text())
            self.assertEqual(prov["xcheck_version"], __version__)

    def test_a_stale_stamp_is_kept_while_anything_is_refused(self):
        # The inverse, and the one that matters: a copy holding a file this version did
        # not ship is not this version, and its record must not say it is.
        with Installed() as inst:
            core = inst.audit / "XCHECK.md"
            core.write_text(core.read_text() + HOUSE_RULE)
            inst.stamp(xcheck_version="0.7.1")
            rc, out = inst.run()
            self.assertEqual(rc, 1)
            self.assertIn("stays at 0.7.1", out)
            prov = json.loads((inst.audit / ".provenance.json").read_text())
            self.assertEqual(prov["xcheck_version"], "0.7.1")

    def test_a_partial_upgrade_updates_the_manifest_but_not_the_version_stamp(self):
        # The same rule on the WRITE path, which is where it is easy to get wrong:
        # files really did land, so the manifest must record them — but one refusal
        # means the copy as a whole is still not this version.
        with Installed() as inst:
            core = inst.audit / "XCHECK.md"
            core.write_text(core.read_text() + HOUSE_RULE)
            (inst.audit / "templates" / "pass.md").unlink()
            inst.stamp(xcheck_version="0.7.1")
            rc, out = inst.run()
            self.assertEqual(rc, 1)
            self.assertIn("stays at 0.7.1", out)
            prov = json.loads((inst.audit / ".provenance.json").read_text())
            self.assertEqual(prov["xcheck_version"], "0.7.1",
                             "a partial upgrade must not claim the whole version")
            man = {ln.split(None, 1)[1].strip(): ln.split(None, 1)[0]
                   for ln in (inst.audit / "MANIFEST.sha256").read_text().splitlines()}
            landed = inst.audit / "templates" / "pass.md"
            self.assertTrue(landed.is_file(), "the file xcheck owns was not refreshed")
            self.assertEqual(man["templates/pass.md"],
                             hashlib.sha256(landed.read_bytes()).hexdigest())

    def test_the_provenance_is_restamped_only_when_something_landed(self):
        with Installed() as inst:
            inst.stamp(xcheck_version="0.7.1")
            (inst.audit / "templates" / "pass.md").unlink()
            inst.run()
            prov = json.loads((inst.audit / ".provenance.json").read_text())
            self.assertEqual(prov["xcheck_version"], __version__)
            self.assertEqual(prov["schema_version"], SCHEMA_VERSION)


class TheSchemaIsWhatForcesAMigration(unittest.TestCase):
    """A release bump is not a migration and a migration is not a release bump. The
    stop message must name which of the two numbers stopped it."""

    def test_a_schema_delta_stops_the_upgrade_and_names_migrate(self):
        with Installed() as inst:
            inst.stamp(schema_version=SCHEMA_VERSION + 1)
            (inst.audit / "templates" / "pass.md").unlink()
            rc, out = inst.run()
            self.assertEqual(rc, 1)
            self.assertIn("xcheck migrate", out)
            self.assertFalse((inst.audit / "templates" / "pass.md").exists(),
                             "a stopped upgrade must not have written anything")

    def test_a_version_delta_alone_does_not_stop_it(self):
        with Installed() as inst:
            inst.stamp(xcheck_version="0.1.0")
            (inst.audit / "templates" / "pass.md").unlink()
            rc, out = inst.run()
            self.assertEqual(rc, 0)
            self.assertNotIn("xcheck migrate", out)
            self.assertIn("0.1.0", out)          # the delta is REPORTED, just not fatal
            self.assertIn(__version__, out)


class TheCommandIsReachableFromTheCli(unittest.TestCase):

    def test_upgrade_on_a_project_with_no_installed_copy_says_what_to_run(self):
        d = Path(tempfile.mkdtemp(prefix="xcheck-bare-"))
        try:
            p = subprocess.run([sys.executable, str(REPO / "bin" / "xcheck"),
                                "--project", str(d), "upgrade"],
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(p.returncode, 1)
            self.assertIn("install.sh", p.stdout + p.stderr)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_the_usage_text_lists_the_command(self):
        p = subprocess.run([sys.executable, str(REPO / "bin" / "xcheck"), "--help"],
                           capture_output=True, text=True, timeout=120)
        self.assertIn("upgrade", p.stdout)


if __name__ == "__main__":
    unittest.main()
