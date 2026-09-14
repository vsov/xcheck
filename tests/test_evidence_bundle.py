"""An evidence bundle someone else can verify — proved by verifying it as someone else.

The claim "portable" cannot be tested by the tool that wrote the bundle. Every check here
that matters runs in a SUBPROCESS started with `-I` (isolated: no `PYTHONPATH`, no user
site directory) whose working directory is the extracted bundle, and the first thing that
subprocess does is prove `import xcheck` FAILS. Without that positive control, a checker
that quietly imported the project would pass every other assertion in this file.

The rest is about the ways a bundle can be dishonest rather than wrong:

- a DROPPED member is a smaller bundle, and looks like success — so `MEMBERS` is asserted
  against what the writer actually wrote, both directions;
- TAMPERED and TRUNCATED must be different failures, proved different by asserting each
  arm's word appears in its own output and NOT in the other's;
- the bundle states the population it covers, including the counts that are zero, because
  133 findings handed over without that statement read as a finished audit.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.harness import (needs_live_corpus, REPO, Fixture, finding_record, queue_pass, state_doc,
                           xcheck_submodule)

bundle = xcheck_submodule("bundle")

# `-I` is the load-bearing flag: it drops `PYTHONPATH` and the user site directory, so a
# subprocess started this way cannot reach an installed or exported xcheck.
ISOLATED = [sys.executable, "-I"]


def run_in(cwd, *args):
    return subprocess.run(ISOLATED + list(args), cwd=str(cwd),
                          capture_output=True, text=True)


class Extracted:
    """A bundle copied somewhere else, the way a recipient would receive it."""

    def __init__(self, src):
        self.dir = Path(tempfile.mkdtemp(prefix="xcheck-extracted-"))
        for f in Path(src).iterdir():
            shutil.copy(f, self.dir / f.name)

    def verify(self):
        return run_in(self.dir, bundle.CHECKER_NAME)

    def read(self, name):
        return (self.dir / name).read_bytes()

    def write(self, name, data):
        (self.dir / name).write_bytes(data)

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def a_bundle(**kw):
    """A bundle over a small synthetic project, not over this repository.

    A test that only ever exports THIS repository cannot tell a writer that reads state
    from a writer that reads whatever it finds — see the fixture-in-the-wrong-directory
    lesson. The real corpus gets its own case at the bottom.
    """
    fx = Fixture(state_doc(
        findings=[finding_record("F-0001", status="reported"),
                  finding_record("F-0002", status="accepted", severity="critical")],
        queue=[queue_pass("P-01", done=True), queue_pass("P-02", done=False)]))
    root, manifest = bundle.write_evidence_bundle(fx.root, {"log_dir": str(fx.root.parent /
                                                                 f"logs-{fx.root.name}")},
                                         **kw)
    return fx, root, manifest


class TheMembersAreEnumerated(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.fx, cls.root, cls.manifest = a_bundle()

    def test_every_declared_member_was_written_and_nothing_else_was(self):
        on_disk = {p.name for p in self.root.iterdir() if p.is_file()}
        expected = set(bundle.MEMBERS) | {bundle.CHECKER_NAME, bundle.MANIFEST_NAME}
        # Both directions. `>=` alone passes over a writer that dropped a member and
        # `<=` alone passes over one that invented a file nobody checksums.
        self.assertEqual(expected, on_disk)
        print("\nMEMBERS on disk:")
        for name in sorted(on_disk):
            print(f"   {name}")

    def test_the_manifest_covers_the_checker_itself(self):
        self.assertIn(bundle.CHECKER_NAME, self.manifest["members"],
                      "the checker is not checksummed — that is the one file an "
                      "attacker rewrites")
        self.assertEqual(set(bundle.MEMBERS) | {bundle.CHECKER_NAME},
                         set(self.manifest["members"]))

    def test_the_audit_named_members_are_all_present(self):
        """The criterion's own list, checked by name rather than by count."""
        for want in ("subject-manifest", "policy", "events", "receipts", "findings",
                     "patches", "verdicts", "provenance"):
            self.assertTrue(any(m.startswith(want) for m in bundle.MEMBERS),
                            f"no member covers `{want}`")
        for name, meta in self.manifest["members"].items():
            self.assertIn("sha256", meta, f"{name} has no checksum")
            self.assertIn("bytes", meta, f"{name} has no length")


class ItVerifiesOutsideTheCheckout(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.fx, cls.root, cls.manifest = a_bundle()
        cls.ex = Extracted(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.ex.cleanup()

    def test_the_positive_control_xcheck_is_not_importable_there(self):
        """Without this, every other test in this class is meaningless.

        A checker that reached the project would verify perfectly well and prove nothing
        about portability — the same shape as the spy that reported "nothing was opened"
        over an empty list."""
        probe = run_in(self.ex.dir, "-c", "import xcheck; print(xcheck.__file__)")
        self.assertNotEqual(0, probe.returncode,
                            f"xcheck IS importable in the checker's process: "
                            f"{probe.stdout.strip()} — the portability claim is untested")
        self.assertIn("No module named 'xcheck'", probe.stderr)
        print(f"\nCONTROL   in {self.ex.dir}: {probe.stderr.strip().splitlines()[-1]}")

    def test_the_checker_runs_there_and_passes(self):
        r = self.ex.verify()
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertIn("OK:", r.stdout)
        print(f"VERIFIED  {r.stdout.strip()}")

    def test_the_checker_imports_only_the_standard_library(self):
        src = (self.ex.dir / bundle.CHECKER_NAME).read_text(encoding="utf-8")
        import ast
        names = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add((node.module or "").split(".")[0])
        self.assertTrue(names <= set(sys.stdlib_module_names),
                        f"the checker imports something outside the standard "
                        f"library: {sorted(names - set(sys.stdlib_module_names))}")
        print(f"IMPORTS   {sorted(names)} — all standard library")


class TamperAndTruncationAreDifferentFailures(unittest.TestCase):

    def setUp(self):
        self.fx, self.root, self.manifest = a_bundle()
        self.ex = Extracted(self.root)

    def tearDown(self):
        self.ex.cleanup()

    def test_a_tampered_member_is_named_and_called_tampered(self):
        data = bytearray(self.ex.read("population.json"))
        i = data.index(b"findings_total")
        data[i:i + 8] = b"XXXXXXXX"                     # same length, different content
        self.assertEqual(len(data), len(self.ex.read("population.json")))
        self.ex.write("population.json", bytes(data))
        r = self.ex.verify()
        self.assertEqual(1, r.returncode)
        self.assertIn("population.json", r.stderr)
        self.assertIn("TAMPERED", r.stderr)
        self.assertNotIn("SHORT", r.stderr)             # the distinguishing assertion
        self.tampered = r.stderr
        print(f"\nTAMPERED  {r.stderr.strip().splitlines()[-1].strip()}")

    def test_a_truncated_member_is_named_and_called_short(self):
        whole = self.ex.read("findings.jsonl")
        self.ex.write("findings.jsonl", whole[:len(whole) // 2])
        r = self.ex.verify()
        self.assertEqual(1, r.returncode)
        self.assertIn("findings.jsonl", r.stderr)
        self.assertIn("SHORT", r.stderr)
        self.assertNotIn("TAMPERED", r.stderr)          # the distinguishing assertion
        print(f"SHORT     {r.stderr.strip().splitlines()[-1].strip()}")

    def test_the_two_failures_are_proved_different_not_merely_both_red(self):
        """N red arms can be one defect. This asserts the OUTPUTS differ, and why.

        A digest-first checker fails both arms — a truncated file has a wrong digest too
        — and prints the same word for each. That checker passes both tests above if
        they only assert `returncode == 1`."""
        data = bytearray(self.ex.read("population.json"))
        i = data.index(b"findings_total")
        data[i:i + 8] = b"XXXXXXXX"
        self.ex.write("population.json", bytes(data))
        tampered = self.ex.verify().stderr
        self.ex.write("population.json", (self.root / "population.json").read_bytes())

        whole = self.ex.read("population.json")
        self.ex.write("population.json", whole[:len(whole) // 2])
        short = self.ex.verify().stderr

        self.assertNotEqual(tampered, short)
        self.assertIn("TAMPERED", tampered)
        self.assertIn("SHORT", short)
        # Same member, same bundle, two different words: the difference is the FAILURE
        # MODE, not which file happened to be picked.
        self.assertIn("population.json", tampered)
        self.assertIn("population.json", short)
        print(f"\nDIFFERENT same member, two modes:\n   {tampered.strip().splitlines()[-1].strip()}"
              f"\n   {short.strip().splitlines()[-1].strip()}")

    def test_a_missing_member_is_a_third_failure(self):
        (self.ex.dir / "patches.jsonl").unlink()
        r = self.ex.verify()
        self.assertEqual(1, r.returncode)
        self.assertIn("MISSING", r.stderr)
        self.assertNotIn("TAMPERED", r.stderr)
        self.assertNotIn("SHORT", r.stderr)
        print(f"MISSING   {r.stderr.strip().splitlines()[-1].strip()}")

    def test_an_unlisted_file_is_reported_rather_than_ignored(self):
        (self.ex.dir / "extra-evidence.json").write_text("{}", encoding="utf-8")
        r = self.ex.verify()
        self.assertEqual(1, r.returncode)
        self.assertIn("UNLISTED", r.stderr)
        print(f"UNLISTED  {r.stderr.strip().splitlines()[-1].strip()}")


class ItSaysWhatItCoversAndWhatItLeavesOut(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.fx, cls.root, cls.manifest = a_bundle()
        cls.pop = json.loads((cls.root / "population.json").read_text(encoding="utf-8"))
        cls.om = json.loads((cls.root / "omissions.json").read_text(encoding="utf-8"))

    def test_the_population_names_findings_passes_and_sessions(self):
        self.assertEqual(2, self.pop["findings_total"])
        self.assertEqual({"reported": 1, "accepted": 1}, self.pop["findings_by_status"])
        self.assertEqual(2, self.pop["passes_total"])
        self.assertEqual(1, self.pop["passes_done"])
        self.assertEqual(1, self.pop["passes_outstanding"])
        self.assertIn("sessions_total", self.pop)
        print(f"\nPOPULATION {self.pop['summary']}")

    def test_the_zero_counts_are_stated_rather_than_omitted(self):
        """A count that is absent reads as a count nobody took."""
        for key in ("class_findings", "plans", "sessions_total",
                    "sessions_with_measured_tokens"):
            self.assertIn(key, self.pop)
            self.assertEqual(0, self.pop[key], f"{key} moved; the assertion needs a look")

    def test_it_names_what_was_never_measured(self):
        text = " ".join(self.pop["unmeasured"])
        self.assertIn("false-positive", text)
        self.assertIn("durability", text)
        print("UNMEASURED")
        for line in self.pop["unmeasured"]:
            print(f"   - {line}")

    def test_the_omissions_ship_inside_the_bundle_with_reasons(self):
        self.assertTrue(self.om)
        for row in self.om:
            self.assertTrue(row["omitted"] and row["why"],
                            "an omission with no reason is a hole, not a disclosure")
        omitted = " ".join(r["omitted"] for r in self.om)
        self.assertIn("raw session logs", omitted)
        self.assertIn("operator profile", omitted)
        print("\nOMISSIONS")
        for row in self.om:
            print(f"   - {row['omitted']}: {row['why'][:96]}…")

    def test_no_raw_log_and_no_profile_bytes_ship(self):
        pol = json.loads((self.root / "policy.json").read_text(encoding="utf-8"))
        text = json.dumps(pol)
        for secret in ("auditor_cmd", "remediator_cmd", "--dangerously"):
            self.assertNotIn(secret, text,
                             f"the operator profile's bytes leaked through: {secret}")
        blob = " ".join(p.read_text(encoding="utf-8", errors="replace")
                        for p in self.root.iterdir() if p.is_file())
        self.assertNotIn("--dangerously-skip-permissions", blob)
        print(f"\nPOLICY    digest only: {sorted(pol)}")

    def test_a_null_digest_says_not_recorded_rather_than_standing_alone(self):
        pol = json.loads((self.root / "policy.json").read_text(encoding="utf-8"))
        if pol["at_export"]["digest"] is None:
            self.assertIn("NOT RECORDED", pol["at_export"]["note"])
        self.assertIn("in_force_during_sessions", pol,
                      "the profile that resolved at export time is not the profile the "
                      "sessions ran under, and a bundle must not conflate them")


class GeneratingItIsAnExplicitVerb(unittest.TestCase):

    def test_it_is_off_by_default_and_writes_nothing(self):
        cli = xcheck_submodule("cli")
        fx = Fixture(state_doc())
        rc = cli.cmd_evidence_bundle(fx.root, {})
        self.assertEqual(0, rc)
        self.assertFalse(bundle.bundle_enabled({}))

    def test_the_flag_turns_it_on(self):
        self.assertTrue(bundle.bundle_enabled({"evidence_bundle": "on"}))
        self.assertFalse(bundle.bundle_enabled({"evidence_bundle": "off"}))

    def test_it_writes_outside_the_working_tree(self):
        fx, root, _m = a_bundle()
        self.assertNotIn(str(fx.root.resolve()), str(root.resolve()),
                         "the bundle landed inside the project it is evidence about")
        print(f"\nOUTSIDE   project {fx.root}\n          bundle  {root}")


@needs_live_corpus
class OverTheRealCorpus(unittest.TestCase):
    """The phase's evidence: a bundle over this repository's own 133 findings."""

    def test_it_exports_and_verifies_and_admits_it_is_not_a_finished_audit(self):
        root, manifest = bundle.write_evidence_bundle(REPO)
        ex = Extracted(root)
        try:
            r = ex.verify()
            self.assertEqual(0, r.returncode, r.stdout + r.stderr)
            pop = json.loads((root / "population.json").read_text(encoding="utf-8"))
            print(f"\nREAL CORPUS -> {root}")
            for name in sorted(manifest["members"]):
                print(f"   {name:<24} {manifest['members'][name]['bytes']:>10,} bytes")
            print(f"   {r.stdout.strip()}")
            # The load-bearing one. 133 findings with no statement of what happened to
            # them reads as a finished audit; this asserts the bundle says otherwise.
            self.assertGreater(pop["passes_outstanding"], 0)
            self.assertIn("reported", pop["findings_by_status"])
            self.assertLess(pop["sessions_with_measured_tokens"], pop["sessions_total"])
            self.assertTrue(pop["unmeasured"])
        finally:
            ex.cleanup()
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
