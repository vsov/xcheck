"""Two tiers, and neither is a way around the other.

The fast tier's whole risk is that it becomes how the heavy proofs get skipped. Four
things here are about that risk rather than about speed:

- **Every module on disk is in exactly one tier.** Checked against the FILESYSTEM, both
  directions, with a counterfactual that plants a module in a copied tree — "every module
  is in a tier" is otherwise satisfied by a check that enumerates nothing.
- **A tier naming a module that does not exist must fail.** `python3 -m unittest
  tests.test_nope` prints `Ran 1 test` and `OK`; only the return code separates it from a
  real run, so a drifted tier list reads as a passing tier.
- **The release tier is a SUPERSET by construction**, not a second hand-kept list.
- **The gate refuses a PR-tier run**, asserted by running it, not by reading the script.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.harness import REPO, ci_module

tiers = ci_module("tiers")

RUN_CHECKS = REPO / "ci" / "run-checks.sh"
GATE = REPO / "ci" / "release-gate.sh"


class EveryModuleIsInATier(unittest.TestCase):

    def test_the_union_equals_what_is_on_disk(self):
        missing, phantom = tiers.unassigned()
        self.assertEqual([], missing,
                         f"in NO tier, so nothing runs them: {missing}")
        self.assertEqual([], phantom,
                         f"named by a tier and not on disk: {phantom} — `unittest` "
                         f"reports `Ran 1 test / OK` for a name like this")
        self.assertEqual([], tiers.duplicates())
        print(f"\nTIERS  pr {len(tiers.PR)} + release-only {len(tiers.RELEASE_ONLY)} "
              f"= {len(tiers.RELEASE)} modules, {len(tiers.on_disk())} on disk")

    def test_release_is_a_superset_of_pr_by_construction(self):
        self.assertEqual(set(tiers.RELEASE), set(tiers.PR) | set(tiers.RELEASE_ONLY))
        self.assertTrue(set(tiers.PR) <= set(tiers.RELEASE))
        # Not a second list that could quietly lose what the PR list lost too.
        self.assertEqual(tiers.RELEASE, tiers.PR + tiers.RELEASE_ONLY)

    def test_the_check_command_agrees_with_the_functions(self):
        r = subprocess.run([sys.executable, str(REPO / "ci" / "tiers.py"), "--check"],
                           capture_output=True, text=True, cwd=str(REPO))
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        print(f"CHECK  {r.stdout.strip()}")


def copy_tree(case):
    """A throwaway repository holding only `ci/tiers.py` and empty test modules.

    Shared by both counterfactual classes: one plants a module nobody assigned, the other
    unhooks an event from its tier, and two copies of this helper would be two things to
    keep in step.
    """
    box = Path(tempfile.mkdtemp(prefix="xcheck-tiers-"))
    case.addCleanup(shutil.rmtree, box, ignore_errors=True)
    dst = box / "repo"
    dst.mkdir()
    for rel in ("ci/tiers.py",):
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, dst / rel)
    (dst / "tests").mkdir()
    for p in (REPO / "tests").glob("test_*.py"):
        (dst / "tests" / p.name).write_text("", encoding="utf-8")
    return dst


def check_in(root):
    return subprocess.run([sys.executable, str(root / "ci" / "tiers.py"), "--check"],
                          capture_output=True, text=True, cwd=str(root))


class TheCounterfactual(unittest.TestCase):
    """A check that enumerates nothing passes the test above. This one plants."""

    def copy_tree(self):
        return copy_tree(self)

    def check(self, root):
        return check_in(root)

    def test_the_control_arm_a_faithful_copy_still_passes(self):
        """Without this, the plant below could be failing for any reason at all."""
        r = self.check(self.copy_tree())
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        print(f"\nCONTROL  faithful copy -> rc=0  {r.stdout.strip()}")

    def test_a_module_in_neither_tier_fails_the_check(self):
        root = self.copy_tree()
        (root / "tests" / "test_a_module_nobody_assigned.py").write_text("", "utf-8")
        r = self.check(root)
        self.assertEqual(1, r.returncode,
                         "a module in no tier passed the check — the check enumerates "
                         "nothing")
        self.assertIn("test_a_module_nobody_assigned", r.stdout)
        self.assertIn("in NO tier", r.stdout)
        print(f"PLANT    unassigned module -> rc=1  "
              f"{[ln for ln in r.stdout.splitlines() if 'NO tier' in ln][0].strip()}")

    def test_a_tier_naming_a_module_that_does_not_exist_fails(self):
        root = self.copy_tree()
        (root / "tests" / "test_state.py").unlink()          # named by PR, now absent
        r = self.check(root)
        self.assertEqual(1, r.returncode)
        self.assertIn("test_state", r.stdout)
        self.assertIn("does not exist", r.stdout)
        print(f"PLANT    deleted a named module -> rc=1  "
              f"{[ln for ln in r.stdout.splitlines() if 'does not exist' in ln][0].strip()}")

    def test_unittest_really_does_report_ok_for_a_missing_module(self):
        """The reason the phantom arm above exists, demonstrated rather than asserted
        from memory."""
        r = subprocess.run([sys.executable, "-m", "unittest",
                            "tests.test_no_such_module_at_all"],
                           capture_output=True, text=True, cwd=str(REPO))
        self.assertIn("Ran 1 test", r.stderr)
        self.assertNotEqual(0, r.returncode, "only the return code separates these")
        print(f"WHY      `unittest tests.test_no_such_module_at_all` -> "
              f"{[ln for ln in r.stderr.splitlines() if ln.startswith('Ran ')][0]}, "
              f"rc={r.returncode}")


class ThePrTierCannotStandInForARelease(unittest.TestCase):

    def test_the_gate_refuses_a_pr_tier_run(self):
        env = dict(os.environ, XCHECK_TIER="pr")
        r = subprocess.run(["bash", str(GATE)], env=env, capture_output=True,
                           text=True, cwd=str(REPO), timeout=120)
        self.assertEqual(1, r.returncode)
        self.assertIn("refusing to run the release gate inside a PR-tier run",
                      r.stdout)
        # And it refused BEFORE doing the expensive thing: no work dir, no build.
        self.assertNotIn("release gate: work dir", r.stdout)
        print(f"\nREFUSED  {r.stdout.strip().splitlines()[0]}")

    def test_the_release_tier_gets_past_the_refusal(self):
        """The control. A gate that refused everything would pass the test above.

        It does NOT build a wheel to prove this. `PYTHON=/nonexistent` makes the gate
        die at its own toolchain check, one step PAST the refusal — so the assertion is
        that the script reached a different failure, which is exactly what "not refused"
        means and costs under a second. A control that ran the whole gate would have put
        a 24-second build inside the fast tier, which is the cost this phase is removing.
        """
        env = dict(os.environ, XCHECK_TIER="release", PYTHON="/nonexistent/python")
        r = subprocess.run(["bash", str(GATE)], env=env, capture_output=True,
                           text=True, cwd=str(REPO), timeout=120)
        self.assertNotIn("refusing to run the release gate", r.stdout)
        self.assertIn("release gate: work dir", r.stdout)
        self.assertIn("is not importable", r.stdout)
        print(f"ALLOWED  XCHECK_TIER=release -> reached the toolchain check: "
              f"{[ln for ln in r.stdout.splitlines() if 'not importable' in ln][0].strip()}")

    def test_the_refusal_is_the_thing_that_stops_it_not_a_missing_toolchain(self):
        """Both arms with the SAME broken interpreter, so the only difference is the
        tier. Without this pairing the refusal arm could be passing because the gate
        cannot run at all on this machine."""
        outs = {}
        for tier in ("pr", "release"):
            env = dict(os.environ, XCHECK_TIER=tier, PYTHON="/nonexistent/python")
            outs[tier] = subprocess.run(["bash", str(GATE)], env=env, capture_output=True,
                                        text=True, cwd=str(REPO), timeout=120).stdout
        self.assertIn("refusing to run the release gate", outs["pr"])
        self.assertNotIn("release gate: work dir", outs["pr"])
        self.assertNotIn("refusing to run the release gate", outs["release"])
        self.assertIn("release gate: work dir", outs["release"])
        print(f"PAIRED   same broken interpreter, only the tier differs:\n"
              f"         pr      -> {outs['pr'].strip().splitlines()[0]}\n"
              f"         release -> {outs['release'].strip().splitlines()[0]}")

    def test_the_pr_tier_does_not_run_the_gate_at_all(self):
        self.assertFalse(tiers.has_step("pr", "release-gate"))
        self.assertFalse(tiers.has_step("pr", "selftest"))
        self.assertFalse(tiers.has_step("pr", "artifact"))
        self.assertTrue(tiers.has_step("release", "release-gate"))
        for step in ("preflight", "ruff", "check-split", "audit-lint", "battery"):
            self.assertTrue(tiers.has_step("pr", step), f"pr lost {step}")

    def test_the_pr_tier_says_it_is_not_a_release(self):
        text = RUN_CHECKS.read_text(encoding="utf-8")
        self.assertIn("NOT a release", text,
                      "a fast tier that prints `all checks passed` is a fast tier "
                      "someone will hand to a release")


class TheRemoteCiPositionIsStatedHonestly(unittest.TestCase):
    """PHASE 17 changes the remote workflow, and the remote has still never run.

    The previous run's version of this class said "this phase makes the LOCAL gate
    cheaper; it changes nothing about the remote", and that was true of that phase. It is
    not true of this one: the workflow now asks for a tier per event. What has NOT changed
    is the evidence behind it — there is still no git remote, nothing has ever been
    pushed, and no cell of this workflow has ever been observed running. So this phase
    changes what the workflow WOULD do, and every figure it saves is arithmetic over
    locally measured wall clock, not an observation of CI.
    """

    # PHASE 17 (fourth audit) REWRITES the assertion that stood here. The original, kept
    # in full because the reasoning was sound and only its conclusion was wrong:
    #
    #     def test_the_workflow_still_runs_the_FULL_tier(self):
    #         """The fast tier is for a laptop, not for the remote gate.
    #
    #         The six-minute problem this phase solves is a developer's: a gate that
    #         long on every save teaches people to skip it. A remote runner does not get
    #         bored. Making the workflow's pull_request job `--tier=pr` would trade
    #         coverage nobody is waiting on for time nobody is spending — and this CI has
    #         never been observed running even once, so it would be weakening an
    #         unmeasured gate."""
    #         ... assertNotIn("--tier", ln) ...
    #
    # "Do not weaken an unobserved remote gate" is still the rule. What was wrong was
    # reading EVENT-SCOPED tiering as a weakening. It is not one: the pull_request path
    # gets shorter, and every module it stops running is run twice elsewhere — on main,
    # and again nightly across the whole OS-by-Python matrix, which is coverage the old
    # single-tier workflow never had on any schedule. Total coverage goes UP; what goes
    # down is 43 minutes of aggregate compute spent re-answering a pull request six ways.
    # `TheEventTiersCoverEverything` below is what holds that claim, and it fails naming
    # the module if any tier ever stops being reachable.
    #
    # A remote runner does not get bored, but it does bill: the old shape spent about 44
    # minutes of runner time per pull request to run the docker escape matrix six times
    # for a change to a docstring.

    def test_the_workflow_tiers_by_EVENT_and_names_only_declared_tiers(self):
        wf = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        run_lines = [ln.strip() for ln in wf.splitlines()
                     if "ci/run-checks.sh" in ln and not ln.lstrip().startswith("#")]
        self.assertTrue(run_lines, "the workflow no longer runs the script at all")
        asked = sorted({ln.split("--tier=")[1].split()[0] for ln in run_lines
                        if "--tier=" in ln})
        for ln in run_lines:
            self.assertIn("--tier=", ln,
                          f"a workflow step runs the script with no tier: {ln!r} — the "
                          f"tier a job runs is declared in ci/tiers.py::EVENTS, and an "
                          f"un-tiered job is a second answer to that question")
        self.assertEqual(sorted(tiers.event_tiers()), asked,
                         "the workflow and ci/tiers.py::EVENTS disagree about which "
                         "tiers exist — one of them is a second copy of the answer")
        print(f"\nWORKFLOW  {len(run_lines)} job(s), tiers asked for: {asked}")
        for event, tier, scope, _why in tiers.EVENTS:
            print(f"          {event:<28} --tier={tier:<8} {scope}")

    def test_the_workflow_exists_and_has_still_never_been_observed(self):
        wf = REPO / ".github" / "workflows" / "ci.yml"
        self.assertTrue(wf.exists(), "the workflow file is gone")
        remotes = subprocess.run(["git", "remote"], cwd=str(REPO),
                                 capture_output=True, text=True).stdout.strip()
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertIn("never been observed running", readme)
        print(f"\nREMOTE CI  workflow: {wf.relative_to(REPO)} exists; "
              f"git remotes: {remotes or '(none)'}; "
              f"README still says CI has never been observed running")
        if remotes:
            # 0.9.5 is the first release exported into a tree that HAS a remote, so this
            # arm now fires there. "No remote" was never the claim — it was the EVIDENCE
            # for it: nothing pushed, so nothing could have run. Where a remote exists
            # that evidence is gone and nothing local replaces it, because whether a
            # workflow ran is a fact about the forge. So the check says it cannot answer
            # here, and the assertion below stays armed, unrelaxed, in the development
            # repository where the evidence still holds.
            self.skipTest(
                f"this checkout has a git remote ({remotes}), so the absence of a "
                f"workflow run cannot be established from here — that is a fact about "
                f"the forge. In the development repository, which has no remote at all, "
                f"the assertion this guards still runs.")
        self.assertEqual("", remotes,
                         "a remote appeared — the honesty claim above needs rewriting, "
                         "not this assertion relaxing")


class TheEventTiersCoverEverything(unittest.TestCase):
    """PHASE 17. Tiering by event is a widening change, and
    [[widening-a-gate-is-how-you-narrow-one]] is about exactly this shape: three new
    triggers added, and the one that used to run a module quietly removed. So coverage is
    asserted over the EVENTS table — what some event actually runs — and not over the
    tier lists, which can be perfectly assigned while nothing triggers them.
    """

    def test_every_module_on_disk_is_run_by_some_event(self):
        self.assertEqual([], tiers.coverage_problems())
        by_module = {}
        for event, tier, _scope, _why in tiers.EVENTS:
            for m in tiers.TIERS[tier]:
                by_module.setdefault(m, []).append(event)
        self.assertEqual(tiers.on_disk(), set(by_module),
                         "a module on disk is run by no event")
        groups = {}
        for m, events in by_module.items():
            groups.setdefault(tuple(events), []).append(m)
        print("\nEVENT COVERAGE  every module on disk, by the events that run it:")
        for events, mods in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            print(f"  {len(mods):>3} module(s)  <- {', '.join(events)}")
        print(f"  {len(by_module)} modules total, {len(tiers.on_disk())} on disk")

    def test_no_tier_is_orphaned(self):
        """A tier the workflow never names is the same defect class as a public flag
        with no call site — which is what phase 1 of this response was about."""
        for tier in tiers.TIERS:
            self.assertIn(tier, tiers.event_tiers(), f"tier {tier!r} is orphaned")

    def test_every_step_runs_under_some_event(self):
        for step, _t in tiers.STEPS:
            runs = [t for t in tiers.event_tiers() if tiers.has_step(t, step)]
            self.assertTrue(runs, f"the {step!r} step runs under no event")
            print(f"  STEP  {step:<14} {', '.join(runs)}")

    def test_the_package_tier_cannot_name_what_the_full_suite_does_not_run(self):
        self.assertEqual(set(), set(tiers.PACKAGE) - set(tiers.RELEASE))

    def test_REMOVING_a_check_from_every_tier_fails_the_coverage_test(self):
        """The counterfactual the criterion asks for, and it plants the NEW defect: not
        an unassigned module (the arm above already catches that) but a module whose only
        tier no event runs any more."""
        root = copy_tree(self)
        src = (root / "ci" / "tiers.py").read_text(encoding="utf-8")
        # Unhook the two events that run the full suite, leaving `release` assigned and
        # triggered by nothing.
        planted = src.replace('("push:main", "release", "one cell"',
                              '("push:main", "pr", "one cell"')
        planted = planted.replace('("schedule|workflow_dispatch", "nightly", "matrix"',
                                  '("schedule|workflow_dispatch", "pr", "matrix"')
        self.assertNotEqual(src, planted, "the plant edited nothing")
        (root / "ci" / "tiers.py").write_text(planted, encoding="utf-8")
        r = check_in(root)
        self.assertEqual(1, r.returncode,
                         "unhooking the full suite from every event passed the check")
        self.assertIn("no tier that any EVENT runs", r.stdout)
        named = [ln for ln in r.stdout.splitlines() if "no tier that any EVENT" in ln]
        self.assertTrue(any("test_adversarial_repo" in ln for ln in named),
                        "the failure did not NAME a check that stopped running")
        print(f"\nPLANT    full suite unhooked from every event -> rc=1, "
              f"{len(named)} check(s) named, first: {named[0].strip()[:100]}")


class TheComputeThisSaves(unittest.TestCase):
    """The audit's number: more than 40 minutes of aggregate compute to answer one pull
    request. The seconds below are MEASURED, on this machine, with the commands named;
    the multiplier is DERIVED from the workflow, so a matrix that grows does not leave a
    remembered number behind."""

    # Measured 2026-09-04 on the phase-17 machine (Darwin 25.5.0, python3.10), each the
    # wall clock of one full run of the named command. They are recorded rather than
    # re-measured because re-measuring costs the seven minutes being measured, and this
    # module is in the PR tier.
    MEASURED_S = {"release": 438.9, "pr": 67.5}
    COMMANDS = {"release": "bash ci/run-checks.sh",
                "pr": "bash ci/run-checks.sh --tier=pr"}

    def cells(self, job):
        """How many cells a job runs, read from the workflow rather than remembered.

        A job block is every line indented deeper than its own header — split on the
        header string alone and the FIRST nested line ends the block, which is how this
        reported one cell for the six-cell matrix on its first outing.
        """
        wf = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        lines, block, inside = wf.splitlines(), [], False
        for ln in lines:
            if ln.startswith(f"  {job}:"):
                inside = True
                continue
            if inside:
                if ln.strip() and not ln.startswith("    "):
                    break
                block.append(ln)
        self.assertTrue(block, f"no job named {job!r} in the workflow")
        n = 1
        for line in block:
            line = line.strip()
            if line.startswith(("os:", "python:")) and "[" in line:
                n *= len([x for x in line.split("[", 1)[1].rstrip("]").split(",")
                          if x.strip()])
        return n

    def test_the_pull_request_path_costs_one_cell_not_six(self):
        self.assertEqual(1, self.cells("pull-request"))
        self.assertEqual(6, self.cells("nightly"),
                         "the matrix moved — the arithmetic below is about six cells")

    def test_the_saving_is_printed_with_both_figures(self):
        old_cells = self.cells("nightly")          # what EVERY event used to run
        old = self.MEASURED_S["release"] * old_cells
        new = self.MEASURED_S["pr"] * self.cells("pull-request")
        print("\nCOMPUTE PER PULL REQUEST (measured wall clock x cells from the workflow)")
        print(f"  before  {old_cells} cell(s) x {self.MEASURED_S['release']:.1f}s "
              f"({self.COMMANDS['release']})  = {old / 60:.1f} min aggregate")
        print(f"  after   1 cell(s) x {self.MEASURED_S['pr']:.1f}s "
              f"({self.COMMANDS['pr']})  = {new / 60:.1f} min aggregate")
        print(f"  saved   {(old - new) / 60:.1f} min per pull request "
              f"({100 * (old - new) / old:.1f}%), and the full suite still runs on main "
              f"and again nightly in all {old_cells} cells")
        self.assertGreater((old - new) / 60, 40,
                           "the audit's figure was more than 40 minutes; this change no "
                           "longer saves it")
        self.assertLess(len(tiers.PR), len(tiers.RELEASE))


if __name__ == "__main__":
    unittest.main()
