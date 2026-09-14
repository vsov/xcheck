"""install → plan → migrate → run → upgrade → resume, on one throwaway project.

Every step here is tested somewhere else in isolation. What is only testable HERE is
the seam between them: an installed copy that a later version upgrades in place, with
a live audit sitting in the middle of it. That is where F-0146's "двойная истина"
actually bites — not during an install, and not during an upgrade, but when the two
happen months apart around state somebody cares about.

The assertion that matters is the last one: after the upgrade, the audit is the same
audit. Same findings, same statuses, same revision, same next decision. An upgrade
that "succeeded" and moved the queue would be worse than one that failed.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.harness import xcheck_submodule, REPO, XCHECK, audit_md, ledger

from xcheck import __version__


class TheInstalledCopySurvivesAnUpgradeMidAudit(unittest.TestCase):

    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="xcheck-lifecycle-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        self.audit = self.project / "audit"

    def sh(self, *args, expect=0):
        p = subprocess.run(list(args), cwd=str(REPO), capture_output=True, text=True,
                           timeout=180)
        if expect is not None:
            self.assertEqual(p.returncode, expect,
                             f"{' '.join(args)} exited {p.returncode}\n{p.stdout}\n{p.stderr}")
        return p.stdout + p.stderr

    def xcheck(self, *args, expect=0):
        return self.sh(sys.executable, str(XCHECK), "--project", str(self.project),
                       *args, expect=expect)

    def write_operator_profile(self):
        """What an operator does once, after install, on their own machine.

        `install.sh` sets up the SUBJECT. It cannot configure what runs, because role
        commands are operator policy and may not live in the repository being audited —
        so a freshly installed project dispatches nothing until its operator names an
        agent CLI in their own profile. This is that step, and its absence is asserted
        below rather than assumed."""
        d = Path(tempfile.mkdtemp(prefix="xcheck-lifecycle-operator-"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        policy = xcheck_submodule("policy")
        path = d / "operator.conf"
        # The template ships `trust_level` UNSET — a writing role refuses until an
        # operator classifies the material. This lifecycle's subject is a fixture this
        # test created, so it makes that declaration the way an operator would.
        path.write_text(policy.OPERATOR_PROFILE_TEMPLATE.replace(
            f"\n{policy.TRUST_KEY}=\n", f"\n{policy.TRUST_KEY}={policy.TRUSTED}\n"),
            encoding="utf-8")
        os.environ[policy.OPERATOR_PROFILE_ENV] = str(path)
        self.addCleanup(os.environ.pop, policy.OPERATOR_PROFILE_ENV, None)
        return path

    def test_install_plan_migrate_run_upgrade_resume(self):
        # 1. install --------------------------------------------------------
        out = self.sh("sh", str(REPO / "install.sh"), str(self.project))
        self.assertIn("provenance:", out)
        self.assertTrue((self.audit / "XCHECK.md").is_file())
        prov = json.loads((self.audit / ".provenance.json").read_text())
        self.assertEqual(prov["xcheck_version"], __version__)

        # 2. a plan the operator writes (the Planner's session, done by hand here:
        #    an agent CLI is not a controllable subject in a test).
        (self.audit / "AUDIT.md").write_text(audit_md(), encoding="utf-8")
        (self.audit / "LEDGER.md").write_text(ledger(), encoding="utf-8")

        # 3. migrate — the one operator-typed verb allowed to build state from Markdown
        out = self.xcheck("--dry-run", "migrate")
        self.assertFalse((self.audit / "state.json").exists(),
                         "a dry run wrote state.json")
        self.xcheck("migrate")
        state_before = json.loads((self.audit / "state.json").read_text())
        self.assertEqual(len(state_before["queue"]), 1)

        # 3b. the operator names an agent CLI. Before this, dispatch refuses — and the
        #     refusal says where the command belongs, not "edit orchestrator.conf",
        #     which is the one file that may not carry it.
        refusal = self.xcheck("next", "--dry-run", expect=1)
        self.assertIn("no operator profile found", refusal)
        # The refusal has to be actionable from the refusal alone: the search order it
        # tried, and the two ways to name a file.
        for line in ("--policy", "$XCHECK_OPERATOR_PROFILE", "$XDG_CONFIG_HOME"):
            self.assertIn(line, refusal)
        self.write_operator_profile()

        # 4. the tool runs on it
        out = self.xcheck("status")
        self.assertIn("passes: 0 done, 1 queued", out)
        decision_before = self.xcheck("next", "--dry-run")
        self.assertIn("Auditor", decision_before)
        self.xcheck("lint")

        # 5. time passes and the installed copy goes stale
        prov["xcheck_version"] = "0.7.1"
        (self.audit / ".provenance.json").write_text(json.dumps(prov, indent=2) + "\n",
                                                     encoding="utf-8")
        (self.audit / "templates" / "pass.md").unlink()      # something upgrade owns

        # 6. upgrade
        out = self.xcheck("upgrade")
        self.assertIn("0.7.1", out)
        self.assertIn(__version__, out)
        self.assertTrue((self.audit / "templates" / "pass.md").is_file())
        # The operator's plan is untouched — it was never a candidate.
        self.assertIn("## 4. Pass queue", (self.audit / "AUDIT.md").read_text())

        # 7. resume — and this is the point of the whole test
        state_after = json.loads((self.audit / "state.json").read_text())
        self.assertEqual(state_after, state_before,
                         "the upgrade moved the audit; an upgrade that succeeds and "
                         "changes the queue is worse than one that fails")
        self.assertEqual(self.xcheck("next", "--dry-run"), decision_before,
                         "the resumed decision differs from the one before the upgrade")
        self.xcheck("lint")
        self.xcheck("status")

    def test_an_upgrade_that_refuses_still_leaves_a_runnable_audit(self):
        # The failure mode worth checking: a refusal must not be a half-upgrade that
        # leaves the project unusable until someone reconciles a file by hand.
        self.sh("sh", str(REPO / "install.sh"), str(self.project))
        (self.audit / "AUDIT.md").write_text(audit_md(), encoding="utf-8")
        (self.audit / "LEDGER.md").write_text(ledger(), encoding="utf-8")
        self.xcheck("migrate")
        core = self.audit / "XCHECK.md"
        core.write_text(core.read_text() + "\n<!-- house rule -->\n", encoding="utf-8")

        out = self.xcheck("upgrade", expect=1)
        self.assertIn("refusing to overwrite", out)
        self.assertIn("house rule", core.read_text())
        self.xcheck("status")
        self.xcheck("lint")


class TheReleaseArtefactIsClean(unittest.TestCase):
    """`ci/check-artifact.py` as a test too, so a developer meets it before CI does."""

    def test_the_tree_carries_no_build_output_or_scratch(self):
        p = subprocess.run([sys.executable, str(REPO / "ci" / "check-artifact.py")],
                           cwd=str(REPO), capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("clean:", p.stdout)


GATE = REPO / "ci" / "release-gate.sh"
COMMENT = re.compile(r"(^|\s)#.*$")
QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")
WRITES_A_REF = re.compile(r"\bgit\b[^|;&]*\b(tag|push)\b")


def executable_lines(text):
    """The script's lines with comments and quoted strings removed.

    Two things in this file talk about tagging without doing it: the header, which
    says at length that the gate never tags, and the message
    `say "not a git repository — tag parity not applicable"`. Both matched a naive
    grep. Comments and string literals are therefore stripped, which leaves the
    argv-shaped text — `git -C "$ROOT" tag "v$VERSION"` survives as `git -C  tag`
    because its command word is not inside quotes, while a sentence about tags does
    not survive at all.

    Stripping is a heuristic, so the counterfactual below plants a real command and
    requires the detector to find exactly it. A detector that stopped matching would
    otherwise read as proof.
    """
    return [QUOTED.sub(" ", COMMENT.sub("", ln)).strip() for ln in text.split("\n")]


class TheGateStopsAtTheGate(unittest.TestCase):
    """Publication is the operator's act. The gate reports; it never releases."""

    def test_the_script_contains_no_tag_or_push_command(self):
        offenders = [ln for ln in executable_lines(GATE.read_text(encoding="utf-8"))
                     if WRITES_A_REF.search(ln)]
        self.assertEqual(offenders, [],
                         "ci/release-gate.sh would create or publish a ref; tagging and "
                         "pushing are the operator's act, not a check's side effect")

    def test_the_detector_catches_a_planted_tag_command(self):
        # Without this, a regex that silently stopped matching would read as proof.
        planted = GATE.read_text(encoding="utf-8") + '\ngit -C "$ROOT" tag "v$VERSION"\n'
        offenders = [ln for ln in executable_lines(planted) if WRITES_A_REF.search(ln)]
        self.assertEqual(len(offenders), 1, f"the detector missed a planted tag: {offenders}")

    def test_it_reads_the_tag_it_reports_on(self):
        # The other half: a gate that reported tag parity without looking would pass
        # the test above trivially.
        self.assertIn("refs/tags/", GATE.read_text(encoding="utf-8"))

    def test_running_the_gate_leaves_the_tag_list_byte_identical(self):
        def tags():
            p = subprocess.run(["git", "-C", str(REPO), "tag"],
                               capture_output=True, timeout=120)
            return p.stdout

        before = tags()
        p = subprocess.run(["bash", str(GATE)], cwd=str(REPO),
                           capture_output=True, text=True, timeout=1800)
        after = tags()
        self.assertEqual(p.returncode, 0,
                         f"the release gate failed:\n{p.stdout[-4000:]}\n{p.stderr[-2000:]}")
        self.assertRegex(p.stdout, r"(?m)^(un)?released: ",
                         "the gate reported no tag parity at all")
        self.assertEqual(before, after,
                         "the gate changed the tag list; it must only report on it")
        # And it must not have left build debris behind in the project tree.
        self.assertFalse((REPO / "xcheck.egg-info").exists(),
                         "the gate left setuptools' egg-info in the project tree")


class TheDeclaredPythonsAreTheTestedPythons(unittest.TestCase):
    """A classifier is a promise to an installer. CI is the only thing that can keep it.

    Reconciled by DROPPING 3.13 from the classifiers rather than adding it to the
    matrix: adding a row promises coverage that has never run even once here, and this
    repository has no remote, so no CI job has ever been observed at all.
    """

    def test_the_classifiers_and_the_ci_matrix_name_the_same_versions(self):
        py = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        classified = set(re.findall(r"Programming Language :: Python :: (\d+\.\d+)", py))
        m = re.search(r"^\s*python:\s*\[([^\]]*)\]", ci, re.M)
        self.assertIsNotNone(m, "the CI workflow declares no python matrix")
        tested = set(re.findall(r"\d+\.\d+", m.group(1)))
        print(f"\nclassifiers: {sorted(classified)}\nci matrix:   {sorted(tested)}")
        self.assertEqual(classified, tested,
                         "the classifiers promise a Python the matrix never runs (or "
                         "the reverse); one of the two files is lying to installers")


class TheSbomDescribesAnEmptyDependencySet(unittest.TestCase):

    def sbom(self, root):
        out = Path(tempfile.mkdtemp(prefix="xcheck-sbom-"))
        self.addCleanup(shutil.rmtree, out, ignore_errors=True)
        p = subprocess.run([sys.executable, str(REPO / "ci" / "make-sbom.py"),
                            str(root), str(out)],
                           capture_output=True, text=True, timeout=120)
        return p, out / "sbom.cdx.json"

    def test_it_states_zero_dependencies_rather_than_omitting_the_key(self):
        p, target = self.sbom(REPO)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        doc = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(doc["components"], [])
        self.assertEqual([d["dependsOn"] for d in doc["dependencies"]], [[]],
                         "an empty `dependsOn` is the claim; an absent key is silence")
        self.assertEqual(doc["metadata"]["component"]["version"], __version__)

    def test_it_refuses_to_describe_a_package_that_grew_a_dependency(self):
        # The generator hard-codes "no dependencies". If pyproject ever declares one,
        # emitting the same document would be emitting a false SBOM.
        root = Path(tempfile.mkdtemp(prefix="xcheck-sbom-src-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        (root / "pyproject.toml").write_text(
            re.sub(r"^dependencies\s*=\s*\[\]", 'dependencies = ["requests"]', text,
                   count=1, flags=re.M), encoding="utf-8")
        p, target = self.sbom(root)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("would emit a document that lies", p.stdout)
        self.assertFalse(target.exists())


class TheInstalledSelftestSkipsOnlyWhatItCannotSee(unittest.TestCase):
    """An installed wheel has no templates/, skills/, install.sh or XCHECK.md.

    The checks that audit those files therefore have no subject. Before phase 9 they
    did not notice: `xcheck selftest` crashed with FileNotFoundError on a pip install.
    They now skip BY NAME — and the two assertions that keep that honest are here:
    nothing is skipped where the files exist, and every check that vanishes from the
    installed run is one of the declared skips. Passes + skips must equal the source
    tree's total, so the skip path cannot quietly drop a check on the floor.
    """

    def run_selftest(self, *, cwd, env=None):
        p = subprocess.run([sys.executable, "-m", "xcheck", "selftest"], cwd=str(cwd),
                           env=env, capture_output=True, text=True, timeout=900)
        lines = p.stdout.split("\n")
        return (p, [ln for ln in lines if ln.startswith(("PASS:", "FAIL:"))],
                [ln[len("SKIP: "):].split(" -> ")[0] for ln in lines
                 if ln.startswith("SKIP:")])

    def test_the_source_tree_skips_nothing_and_the_installed_package_skips_the_declared_set(self):
        from xcheck.selftest import REPO_ONLY_CHECKS

        src, src_checks, src_skips = self.run_selftest(cwd=REPO)
        self.assertEqual(src.returncode, 0, src.stdout[-3000:])
        self.assertEqual(src_skips, [],
                         "a check skipped itself in the repository, where its subject "
                         "IS present — that is a check quietly opting out")

        # An installed package: the code, with nothing of the repository beside it.
        site = Path(tempfile.mkdtemp(prefix="xcheck-site-"))
        self.addCleanup(shutil.rmtree, site, ignore_errors=True)
        shutil.copytree(REPO / "xcheck", site / "xcheck",
                        ignore=shutil.ignore_patterns("__pycache__"))
        env = dict(os.environ, PYTHONPATH=str(site))
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        inst, inst_checks, inst_skips = self.run_selftest(cwd=site, env=env)

        self.assertEqual(inst.returncode, 0,
                         f"the installed selftest failed:\n{inst.stdout[-3000:]}")
        self.assertEqual(sorted(inst_skips), sorted(REPO_ONLY_CHECKS),
                         "the installed run skipped a different set than the package "
                         "declares in REPO_ONLY_CHECKS")
        self.assertEqual(len(inst_checks) + len(inst_skips), len(src_checks),
                         f"{len(src_checks)} checks in the tree, but "
                         f"{len(inst_checks)} ran + {len(inst_skips)} skipped when "
                         f"installed — a check went missing without saying so")
        print(f"\nselftest: {len(src_checks)} in the source tree, "
              f"{len(inst_checks)} + {len(inst_skips)} skipped when installed")


if __name__ == "__main__":
    unittest.main()
