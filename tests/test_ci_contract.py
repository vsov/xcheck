"""0.9.1 phase 6: the workflow builds its own environment, and that is checked.

The third audit's P0 #7. `ci/run-checks.sh` always calls `ci/release-gate.sh`; the gate
hard-fails when `build` is not importable and refuses — correctly — to substitute
another build path. The workflow installed `ruff` and nothing else. So every green cell
was a statement about whatever the runner image happened to carry, and the auditor
reproduced the consequence: under Python 3.11 the gate exits 1.

The tests here answer four separate questions, and none of them is "does the yaml look
right":

- Is the workflow's install list the set the check scripts actually need? Derived by
  `ci/preflight.py` from imports and invocations, compared — never hand-listed here,
  because a list typed into a test drifts exactly as fast as one typed into the yaml.
- Do the pins agree with `pyproject.toml`? One place holds versions; two places name
  them.
- Does a missing dependency refuse EARLY, with the command? Proved by masking `build`
  and reading the message.
- Does the gate go green under every supported Python on this machine — and did at
  least one version actually run? A suite of skips is a green suite that measured
  nothing, which is this repository's most-repeated lesson.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.harness import REPO, needs_live_corpus

WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"


def executable_offsets(script, needles):
    """Character offsets of `needles` in the script with COMMENTS blanked out.

    Order-of-steps checks used to read the raw text, so `text.index(x)` found the first
    mention anywhere — including in a comment explaining the step. Phase 19 added a
    header paragraph that names `ci/release-gate.sh` while describing the two tiers, and
    both order assertions went red over a script whose order had not changed.

    Blanking rather than deleting keeps every offset comparable to the others in the same
    call. And this is a STRONGER check than the one it replaces: before, a comment
    mentioning a step in the right place could satisfy the ordering on its own. The
    counterfactual below plants a genuinely reordered script and requires a red.
    """
    stripped = "".join(
        (ln.split("#", 1)[0] + " " * (len(ln) - len(ln.split("#", 1)[0])))
        if not ln.lstrip().startswith("#") else " " * len(ln)
        for ln in script.splitlines(keepends=True))
    return [stripped.index(n) for n in needles]


def preflight():
    """`ci/preflight.py`'s derivation, imported rather than re-derived here."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_preflight", REPO / "ci"
                                                  / "preflight.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tiers():
    """`ci/tiers.py`'s tier data, loaded the same way and for the same reason.

    By path, never `import tiers` with `ci/` on `sys.path`: a bare import of a name that
    lives in a DIFFERENT directory reads to `ci/preflight.py` as a PyPI distribution
    called `tiers`, and the preflight then demands `pip install tiers`. That is not a
    quirk to work around — it is the check working, and this is the shape that does not
    trip it.
    """
    from tests.harness import ci_module
    return ci_module("tiers")


# ---------------------------------------------------------------------------
# a minimal parse of the workflow — enough to enumerate steps, and no more
# ---------------------------------------------------------------------------


def parse_workflow(text):
    """{job: [step-text]} by indentation.

    Not a YAML library: this project is stdlib-only, and PyYAML would itself become an
    undeclared CI dependency — the exact defect this file is about. It is a real parse
    rather than a grep, and that distinction is load-bearing for the publish check: the
    workflow's `on:` block contains the word `push` as a TRIGGER, and a grep would
    either fire on it forever or be taught an exception that also hides a real
    `git push` in a step.
    """
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    jobs, job, in_jobs, in_steps, step = {}, None, False, False, None
    for ln in lines:
        if not ln.strip():
            continue
        indent = len(ln) - len(ln.lstrip())
        if indent == 0:
            in_jobs = ln.startswith("jobs:")
            in_steps = False
            job = None
            continue
        if not in_jobs:
            continue
        if indent == 2 and ln.rstrip().endswith(":"):
            job = ln.strip()[:-1]
            jobs[job] = []
            in_steps = False
            continue
        if job is None:
            continue
        if indent == 4 and ln.strip() == "steps:":
            in_steps = True
            continue
        if indent == 4:
            in_steps = False
            continue
        if not in_steps:
            continue
        if ln.lstrip().startswith("- "):
            step = [ln.strip()[2:]]
            jobs[job].append(step)
        elif step is not None:
            step.append(ln.strip())
    return {j: ["\n".join(s) for s in steps] for j, steps in jobs.items()}


def declared_installs(text):
    """{package: pin-spec} from every `pip install` in the workflow."""
    out = {}
    for m in re.finditer(r"pip install ([^\n]+)", text):
        for spec in re.findall(r"\"([^\"]+)\"|'([^']+)'|(\S+)", m.group(1)):
            s = next(x for x in spec if x)
            if s.startswith("-"):
                continue
            out[re.split(r"[=<>!~ ]", s, 1)[0]] = s
    return out


PUBLISHING = ("git push", "git tag", "upload-artifact", "gh release", "twine",
              "pypi", "softprops/action-gh-release", "peaceiris", "publish")


# ---------------------------------------------------------------------------


class TheWorkflowInstallsWhatTheScriptsNeed(unittest.TestCase):
    """Criteria 1 and 4."""

    def test_the_derived_set_and_the_declared_set_are_the_same(self):
        pf = preflight()
        seen, clis = pf.derive()
        declared = declared_installs(WORKFLOW.read_text(encoding="utf-8"))
        print(f"\nderived from the check scripts : {sorted(seen)}")
        for n in sorted(seen):
            print(f"    {n:<8} <- {', '.join(sorted(set(seen[n])))}")
        print(f"declared by ci.yml             : {declared}")
        self.assertTrue(seen, "the derivation found NOTHING — a comparison against an "
                              "empty set passes for free")
        self.assertEqual(sorted(seen), sorted(declared),
                         "the workflow's install list is not the set the check scripts "
                         "need; a cell would then pass or fail on the runner image's "
                         "ambient state")

    def test_every_derived_name_is_classified_required_or_optional(self):
        """A new dependency must fail CLOSED. If it is missing from `OPTIONAL` it is
        required, and the preflight refuses on it — never the other way round."""
        pf = preflight()
        seen, _clis = pf.derive()
        required = sorted(n for n in seen if n not in pf.OPTIONAL)
        optional = sorted(n for n in seen if n in pf.OPTIONAL)
        print(f"\nrequired: {required}")
        print(f"optional: {[(n, pf.OPTIONAL[n]) for n in optional]}")
        self.assertTrue(required, "nothing is required, so the preflight can never "
                                  "refuse and criterion 3 has no subject")

    def test_the_pins_agree_with_pyproject(self):
        pf = preflight()
        pinned = pf.pins()
        declared = declared_installs(WORKFLOW.read_text(encoding="utf-8"))
        print(f"\npyproject [ci] extra : {pinned}")
        print(f"ci.yml pip install   : {declared}")
        self.assertTrue(pinned, "pyproject has no `ci` extra, so 'the pins agree' is "
                                "a claim about an empty table")
        both = set(pinned) & set(declared)
        self.assertTrue(both, "the two lists name no package in common")
        disagree = {n: (pinned[n], declared[n]) for n in both
                    if pinned[n] != declared[n]}
        self.assertEqual(disagree, {}, f"a pin disagrees between pyproject and the "
                                       f"workflow: {disagree}")

    def test_every_derived_name_is_pinned(self):
        pf = preflight()
        seen, _ = pf.derive()
        unpinned = sorted(n for n in seen if n not in pf.pins())
        self.assertEqual(unpinned, [], f"{unpinned} would install whatever is newest "
                                       f"the day a cell runs")


class TheWorkflowNeverPublishes(unittest.TestCase):
    """Criterion 5. Parsed, and the parse proved to work by planting a step."""

    def steps(self, text=None):
        return parse_workflow(text or WORKFLOW.read_text(encoding="utf-8"))

    def offenders(self, jobs):
        out = []
        for job, steps in jobs.items():
            for i, s in enumerate(steps):
                for bad in PUBLISHING:
                    if bad in s.lower():
                        out.append(f"{job}[{i}]: {bad!r} in {s.splitlines()[0]!r}")
        return out

    def test_the_parse_finds_the_jobs_and_steps(self):
        """The counter-assertion. A parser that returns nothing reports no offenders
        either, and this file's central claim would be an artefact of a bug."""
        jobs = self.steps()
        print(f"\nparsed {len(jobs)} job(s):")
        for job, steps in jobs.items():
            print(f"  {job}: {len(steps)} step(s)")
            for s in steps:
                print(f"    - {s.splitlines()[0][:90]}")
        self.assertGreaterEqual(len(jobs), 1)
        self.assertGreaterEqual(sum(len(s) for s in jobs.values()), 5,
                                "the parse found fewer steps than the workflow has — "
                                "it is reading something other than the steps")

    def test_no_step_pushes_tags_or_uploads(self):
        found = self.offenders(self.steps())
        print(f"\npublishing steps found: {found or 'none'}")
        self.assertEqual(found, [], f"the workflow publishes: {found}")

    def test_the_on_push_trigger_is_not_mistaken_for_a_publish(self):
        """`on: push:` is why this is a parse and not a grep."""
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("push:", text, "the workflow no longer has a push TRIGGER, so "
                                     "this test is checking nothing")
        self.assertEqual(self.offenders(self.steps(text)), [])

    def test_a_planted_publish_step_is_caught(self):
        text = WORKFLOW.read_text(encoding="utf-8").replace(
            "      - name: Run the same checks a developer runs locally",
            "      - name: Ship it\n        run: git push --tags\n"
            "      - name: Run the same checks a developer runs locally")
        found = self.offenders(self.steps(text))
        print(f"\nplanted publish step caught: {found}")
        self.assertTrue(any("git push" in f for f in found),
                        "a planted `git push` step walked past the check, so the green "
                        "result above proves nothing")


class TheMissingDependencyRefusesEarly(unittest.TestCase):
    """Criterion 3, on message content — the audit's reproduced failure, regressed."""

    def run_preflight(self, mask=None):
        env = dict(os.environ)
        if mask:
            tmp = Path(tempfile.mkdtemp(prefix="xcheck-mask-"))
            self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
            (tmp / f"{mask}.py").write_text(
                'raise ImportError("masked by tests/test_ci_contract.py")\n',
                encoding="utf-8")
            env["PYTHONPATH"] = str(tmp) + os.pathsep + env.get("PYTHONPATH", "")
        r = subprocess.run([sys.executable, str(REPO / "ci" / "preflight.py")],
                           cwd=REPO, env=env, capture_output=True, text=True,
                           timeout=120)
        return r.returncode, r.stdout + r.stderr

    def test_the_unmasked_preflight_passes(self):
        """The control: if it refused anyway, the masked arm would prove nothing."""
        rc, out = self.run_preflight()
        self.assertEqual(rc, 0, out[-600:])
        self.assertIn("preflight: ok", out)

    def test_masking_build_refuses_with_the_install_command(self):
        rc, out = self.run_preflight(mask="build")
        print("\n--- preflight with `build` masked ---")
        print(out.strip())
        print("--- end ---")
        self.assertEqual(rc, 1, "a missing REQUIRED dependency did not refuse")
        self.assertIn("FAIL: ci preflight", out)
        self.assertIn("-m pip install", out, "the refusal does not carry the command")
        self.assertIn("build==", out, "the refusal does not carry the PIN")
        self.assertIn("pyproject.toml", out, "the refusal does not say where the pin "
                                             "lives, so the reader cannot change it")

    def test_the_refusal_happens_before_the_expensive_steps(self):
        """`ci/run-checks.sh` must call the preflight FIRST. The audit's complaint was
        not only that the gate failed — it was that it failed last."""
        script = (REPO / "ci" / "run-checks.sh").read_text(encoding="utf-8")
        needles = ("ci/preflight.py", "unittest discover", "ci/release-gate.sh")
        order = executable_offsets(script, needles)
        print(f"\nrun-checks.sh call order (executable offsets): {order}")
        self.assertEqual(order, sorted(order),
                         "the preflight does not run before the battery and the gate")

    def test_the_order_check_reddens_on_a_genuinely_reordered_script(self):
        """Blanking comments could have blanked the calls too, and a check that finds
        nothing raises rather than passing — but a check that found the WRONG things
        would still be sorted by accident. So: plant a real reordering."""
        script = (REPO / "ci" / "run-checks.sh").read_text(encoding="utf-8")
        needles = ("ci/preflight.py", "unittest discover", "ci/release-gate.sh")
        lines = script.splitlines(keepends=True)
        pre = [i for i, ln in enumerate(lines)
               if "ci/preflight.py" in ln and not ln.lstrip().startswith("#")]
        self.assertEqual(1, len(pre), "expected exactly one preflight CALL")
        moved = lines[:pre[0]] + lines[pre[0] + 1:] + [lines[pre[0]]]
        planted = executable_offsets("".join(moved), needles)
        self.assertNotEqual(planted, sorted(planted),
                            "the preflight was moved to the END and the check still "
                            "passed — it is reading comments, not calls")
        print(f"CONTROL  preflight moved to the end -> {planted} (not sorted)")


class ASiblingTestImportIsNotAPackageToInstall(unittest.TestCase):
    """The derivation reads a bare sibling import for what it is.

    `import test_launch_surfaces` — the spelling that resolves when a file is run as
    `python tests/test_x.py` — has the top-level name `test_launch_surfaces`, which no
    index carries. The preflight used to refuse with `pip install
    "test_launch_surfaces"`: right to stop, wrong about why, and the next person to
    write one pays the same hour.

    The rule is one filesystem question — does `tests/<name>.py` exist? — so these
    tests hold BOTH arms of it. Reclassifying a name is how you stop reporting a real
    missing package, so the arm that matters most is the one where nothing is
    reclassified: a name with no file behind it, and `fixtures` (a DIRECTORY in
    `tests/`, not a module file), both still counted as packages to install.
    `TheMissingDependencyRefusesEarly` above holds the end-to-end version — mask
    `build`, watch it exit 1.
    """

    PROBE = REPO / "tests" / "_preflight_probe.py"

    def plant(self, body):
        """A real file, because the derivation reads real files."""
        self.PROBE.write_text(body, encoding="utf-8")
        self.addCleanup(self.PROBE.unlink, missing_ok=True)
        return self.PROBE.relative_to(REPO).as_posix()

    def run_preflight(self):
        r = subprocess.run([sys.executable, str(REPO / "ci" / "preflight.py")],
                           cwd=REPO, capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout + r.stderr

    def test_a_bare_sibling_import_is_reclassified_and_said_out_loud(self):
        before = sorted(preflight().derive()[0])
        rel = self.plant("import test_launch_surfaces\n")

        pf = preflight()
        seen, _clis = pf.derive()
        print(f"\nderived without the probe : {before}")
        print(f"derived with the probe    : {sorted(seen)}")
        print(f"reclassified              : {pf.RECLASSIFIED}")
        self.assertEqual(sorted(seen), before,
                         "a bare sibling import changed the dependency set — the "
                         "preflight would demand `pip install test_launch_surfaces`")
        self.assertIn("test_launch_surfaces", pf.RECLASSIFIED,
                      "the name vanished without being recorded, which is the silent "
                      "drop this record exists to prevent")
        self.assertIn(rel, pf.RECLASSIFIED["test_launch_surfaces"])

        rc, out = self.run_preflight()
        print("\n--- preflight with the sibling-import probe planted ---")
        print(out.strip())
        print("--- end ---")
        self.assertEqual(rc, 0, out[-600:])
        self.assertIn("reclassified as first-party", out,
                      "the reclassification is invisible, so a dependency could "
                      "disappear from the contract with nothing on screen")
        self.assertIn("test_launch_surfaces", out)

    def test_a_name_with_no_file_behind_it_still_refuses(self):
        """The counterfactual. If this passed, the rule would be 'ignore anything a
        test imports' and the preflight would have stopped being a check."""
        name = "xcheck_probe_no_such_distribution"
        self.plant(f"import {name}\n")

        pf = preflight()
        seen, _clis = pf.derive()
        self.assertIn(name, seen, f"{name} was not counted as a dependency")
        self.assertNotIn(name, pf.RECLASSIFIED,
                         "a name with no `tests/` file behind it was reclassified")

        rc, out = self.run_preflight()
        print(f"\n--- preflight with `import {name}` planted ---")
        print(out.strip())
        print("--- end ---")
        self.assertEqual(rc, 1, "a genuinely missing third-party dependency did not "
                                "refuse")
        self.assertIn("FAIL: ci preflight", out)
        self.assertIn(name, out)
        self.assertIn("-m pip install", out, "the refusal does not carry the command")

    def test_a_directory_in_tests_is_not_a_module_file(self):
        """`tests/fixtures/` exists; `tests/fixtures.py` does not. Asserted on the
        derivation rather than on an exit code, so the answer does not depend on
        whether some package named `fixtures` happens to be installed here."""
        self.assertTrue((REPO / "tests" / "fixtures").is_dir(),
                        "tests/fixtures/ is gone, so this test names nothing")
        self.assertFalse((REPO / "tests" / "fixtures.py").exists())
        self.plant("import fixtures\n")

        pf = preflight()
        seen, _clis = pf.derive()
        print(f"\nfixtures classified as: "
              f"{'dependency' if 'fixtures' in seen else 'reclassified'}")
        self.assertIn("fixtures", seen)
        self.assertNotIn("fixtures", pf.RECLASSIFIED,
                         "a DIRECTORY in tests/ was read as a sibling module, so the "
                         "rule is wider than `tests/<name>.py`")


class TheGateRunsUnderEverySupportedPython(unittest.TestCase):
    """Criterion 2. Every supported version present on this machine, reported by name.

    The versions are DERIVED from `pyproject.toml`'s classifiers — the same list
    `tests/test_version_parity.py` holds equal to the workflow matrix — so a version
    added to the promise is a version this test starts demanding.
    """

    maxDiff = None

    def supported(self):
        text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        return re.findall(r"Programming Language :: Python :: (3\.\d+)", text)

    def toolchain(self, py):
        """An interpreter that can run the gate, or None.

        Ambient if it already has the toolchain; otherwise a venv with the DECLARED
        pins installed — which is the workflow's own step, executed. A venv build that
        fails (no network on this machine) is reported and the version is not counted
        as run, rather than being reported as a pass.
        """
        if subprocess.run([py, "-c", "import build"], capture_output=True).returncode == 0:
            return py, "ambient"
        venv = Path(tempfile.mkdtemp(prefix="xcheck-ci-venv-"))
        self.addCleanup(shutil.rmtree, venv, ignore_errors=True)
        if subprocess.run([py, "-m", "venv", str(venv)],
                          capture_output=True).returncode != 0:
            return None, "venv creation failed"
        vpy = venv / "bin" / "python"
        pins = [preflight().pins()[n] for n in sorted(preflight().pins())]
        r = subprocess.run([str(vpy), "-m", "pip", "install", "--quiet",
                            "--disable-pip-version-check", *pins],
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            return None, f"toolchain install failed ({r.stderr.strip()[-120:]})"
        return str(vpy), f"venv with {pins}"

    def test_the_gate_is_green_under_every_supported_python_present_here(self):
        # PHASE 19: `ci/run-checks.sh` runs the gate itself, as its last step, and
        # exports the version it will use. Re-running it here for that SAME interpreter
        # was the third execution of a 24-second gate answering a question the outer
        # step already answers. Delegation is counted as covered, not skipped — the run
        # happens, in the same process tree, a few seconds later.
        #
        # The variable is exported by `ci/run-checks.sh` and by nothing else, so a bare
        # `python3 -m unittest` still runs every interpreter here. That is the
        # fail-closed direction: the delegation exists only when something else has
        # promised to do it.
        delegated = os.environ.get("XCHECK_GATE_DELEGATED")
        results, ran = {}, 0
        for v in self.supported():
            py = shutil.which(f"python{v}")
            if not py:
                results[v] = "absent on this machine"
                continue
            if v == delegated:
                results[v] = ("DELEGATED to ci/run-checks.sh's own release-gate step "
                              "(same interpreter, same gate, one run instead of two)")
                ran += 1
                continue
            interp, how = self.toolchain(py)
            if interp is None:
                results[v] = f"NOT RUN — {how}"
                continue
            env = dict(os.environ, PYTHON=interp)
            r = subprocess.run(["bash", str(REPO / "ci" / "release-gate.sh")],
                               cwd=REPO, env=env, capture_output=True, text=True,
                               timeout=1800)
            ran += 1
            tail = [ln for ln in r.stdout.splitlines() if ln.startswith("release gate:")]
            results[v] = f"ran ({how}) -> rc={r.returncode} · {tail[-1] if tail else ''}"
            if r.returncode != 0:
                results[v] += "\n" + r.stdout[-1500:]
        print("\nrelease gate, per supported Python:")
        for v in sorted(results):
            print(f"  {v}: {results[v]}")
        # The counter-assertion. Three "absent" lines are a green test that measured
        # nothing — the failure mode this repository keeps re-learning.
        self.assertGreaterEqual(ran, 1,
                                f"NO supported Python actually ran the gate, so this "
                                f"test is green over an empty set: {results}")
        # A DELEGATED version counts only while something really performs the run it was
        # delegated to. Asserted against the tier data rather than trusted: if the
        # release tier ever stopped carrying a release-gate step, delegation would be a
        # promise nobody keeps, and every delegated line above would be a green over
        # nothing — the same empty set, one indirection further away.
        if delegated:
            tier_data = tiers()
            self.assertTrue(tier_data.has_step("release", "release-gate"),
                            f"python{delegated} was delegated to ci/run-checks.sh, "
                            f"which no longer runs the gate at all")
            self.assertEqual("release", os.environ.get(tier_data.TIER_ENV),
                             "the gate was delegated inside a run that is not the "
                             "release tier, so nothing will run it")
        bad = {v: r for v, r in results.items() if "rc=0" not in r and "ran" in r}
        self.assertEqual(bad, {}, f"the gate is red under a version the project "
                                  f"promises: {bad}")


class TheReadmeStatesOnlyWhatWasObserved(unittest.TestCase):
    """Criterion 6. This phase makes the workflow self-sufficient. It cannot make it
    OBSERVED — that needs a remote, and the remote is the operator's act."""

    def test_the_compatibility_row_still_says_ci_has_never_been_observed(self):
        text = (REPO / "README.md").read_text(encoding="utf-8")
        row = next(ln for ln in text.splitlines() if ln.startswith("| Python |"))
        print(f"\n{row[:400]}")
        self.assertIn("never been observed running", row,
                      "the compatibility row stopped saying that CI has never been "
                      "observed — nothing in 0.9.1 made it observed")
        for v in ("3.10", "3.11", "3.12"):
            self.assertIn(v, row)

    def test_the_row_names_what_was_actually_run_locally(self):
        text = (REPO / "README.md").read_text(encoding="utf-8")
        row = next(ln for ln in text.splitlines() if ln.startswith("| Python |"))
        self.assertIn("release gate", row.lower(),
                      "the row does not say which versions the gate was observed green "
                      "under, which is the only python-version evidence that exists")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TheGateChecksTheShippedAuditCorpus(unittest.TestCase):
    """The audit's contradiction: a GREEN software CI beside a RED audit corpus. `xcheck
    lint` reported 20 orphan finding files for the whole 0.9.1 line, nothing in CI ran
    it, and the corpus ships inside the sdist and the wheel's source tree — so the
    release gate could certify a repository that disagreed with itself."""

    SCRIPT = Path(__file__).resolve().parent.parent / "ci" / "run-checks.sh"

    def script(self):
        return self.SCRIPT.read_text(encoding="utf-8")

    def test_the_gate_runs_lint(self):
        self.assertIn("-m xcheck lint", self.script(),
                      "the release gate does not look at the audit corpus at all")

    def test_it_runs_before_the_artifact_is_built(self):
        """Criterion 6: placement is a claim and it is checkable. Below the build this
        step would only ever report on a wheel that had already been produced."""
        lint, gate = executable_offsets(self.script(),
                                        ("-m xcheck lint", "ci/release-gate.sh"))
        self.assertLess(lint, gate,
                        "lint runs after the build — too late to stop a release")
        print(f"\n  ORDER  `-m xcheck lint` at {lint} < `ci/release-gate.sh` at {gate} "
              f"(comments blanked, so a mention does not count as a call)")

    @needs_live_corpus
    def test_this_repository_is_green(self):
        rc = subprocess.run([sys.executable, "-m", "xcheck", "lint"],
                            cwd=str(self.SCRIPT.parent.parent),
                            capture_output=True, text=True, timeout=120)
        print(f"\n  LINT (this repo)  rc={rc.returncode}  {rc.stdout.strip()[:110]}")
        self.assertEqual(0, rc.returncode, rc.stdout)

    @needs_live_corpus
    def test_a_planted_orphan_turns_it_red_and_removing_it_turns_it_green(self):
        """The control. Without it, "lint is green" is satisfied by a lint that cannot
        go red — and the whole point of the step is the day it does."""
        root = self.SCRIPT.parent.parent
        planted = root / "audit" / "findings" / "F-9999-planted-orphan-control.md"
        self.assertFalse(planted.exists(), "a previous run left the plant behind")
        planted.write_text("---\nid: F-XXXX\n---\nplanted by the phase-10 control\n",
                           encoding="utf-8")
        try:
            red = subprocess.run([sys.executable, "-m", "xcheck", "lint"], cwd=str(root),
                                 capture_output=True, text=True, timeout=120)
        finally:
            planted.unlink()
        green = subprocess.run([sys.executable, "-m", "xcheck", "lint"], cwd=str(root),
                               capture_output=True, text=True, timeout=120)
        print(f"  PLANTED           rc={red.returncode}  "
              f"{[ln.strip() for ln in red.stdout.splitlines() if 'F-9999' in ln][:1]}")
        print(f"  REMOVED           rc={green.returncode}")
        self.assertEqual(1, red.returncode, "a planted orphan did not turn the gate red")
        self.assertIn("F-9999-planted-orphan-control.md", red.stdout,
                      "the gate went red without naming the file that did it")
        self.assertEqual(0, green.returncode, green.stdout)
