"""0.9.1 phase 8: a parallel pass's evidence survives its worktree. The blocker.

The third audit's first release blocker, reproduced by the auditor and by this project:

    merged pass P-01: 1 finding(s), coverage recorded
    BODY_HAS_EVIDENCE=False
    REPORT_EXISTS=False
    LINT_RC=1

A pass ran, its finding was merged as a RECORD, and everything the session actually
wrote — the evidence body, the pass report, the split remainder — was deleted with the
worktree. `write_views` then regenerated a finding file from frontmatter, so the finding
looked filed and its evidence was gone.

This module is the proof that it no longer happens, and it is deliberately built to be
hard to satisfy dishonestly:

* **The agent is real.** `auditor_cmd` is a shell script run as a child inside a real
  git worktree, which writes its artifacts and files its finding through the shipped
  CLI verbs. Nothing here reaches into the sandbox from the test process.
* **Bytes, never existence.** The audit's failure produced a finding file that EXISTED.
  Every assertion below compares the artifact's bytes against what the session wrote,
  captured from inside the worktree before teardown.
* **The teardown is real.** `agent_pass` calls `sandbox.leave()` in its `finally`, and
  the tests assert the worktree is gone before they look at the project.

`TheLiftIsCoupledToTheProof` is the phase's other half: the capability is available
only while the machinery behind it works, and that is checked by a probe that carries a
byte at the moment of the decision — not by a flag, a version, or this comment.
"""

import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.harness import (Fixture, REPO, XCHECK, queue_pass, state_doc,
                           xcheck_submodule)

parallel = xcheck_submodule("parallel")
runner = xcheck_submodule("runner")
state_mod = xcheck_submodule("state")
util = xcheck_submodule("util")
StateError = state_mod.StateError

# The bytes that must survive. Long enough that a truncation is visible, and shaped like
# what an auditor actually writes: a claim, a location, an observation.
EVIDENCE = (
    "The unit reads `state.json` twice and compares the two reads for equality,\n"
    "which is not a check on anything: nothing writes between them. Observed at\n"
    "`src/u1.py:41-58`, reproduced by deleting the second read (no test reddens).\n")
REPORT = (
    "# Pass P-01 — coverage report\n\n"
    "Read `src/u1.py` in full against dimension `invariants`. One finding filed.\n"
    "Not covered: the module's error paths, which belong to the remainder pass.\n")


def one_pass_doc(pid="P-01", unit="U01"):
    """A project with one queued pass, and the catalogs that make it legal."""
    catalogs = {
        "norms": [{"id": "N1", "source": "README.md", "scope": "what it claims"}],
        "dimensions": [{"key": "invariants", "catches": "broken invariants",
                        "norms": ["N1"]}],
        "units": [{"id": "U01", "material": "src/u1.py", "size": "0.4 kloc",
                   "responsibility": "one"},
                  {"id": "U02", "material": "src/u2.py", "size": "0.4 kloc",
                   "responsibility": "two"}],
    }
    return state_doc(queue=[dict(queue_pass(pid, done=False), units=[unit])],
                     catalogs=catalogs)


def two_pass_doc():
    doc = one_pass_doc()
    doc["queue"].append(dict(queue_pass("P-02", done=False), units=["U02"]))
    return doc


def commit(root):
    for args in (("init", "-q"), ("config", "user.email", "t@example.invalid"),
                 ("config", "user.name", "test"), ("add", "-A"),
                 ("commit", "-qm", "base")):
        subprocess.run(["git", *args], cwd=str(root), capture_output=True, timeout=60)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_digest(root, skip=()):
    """Every file under `root`, by content. The comparison criterion 5 needs."""
    root = Path(root)
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if not p.is_file() or rel.startswith((".git/",)) or rel in skip:
            continue
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


# The fake Auditor: a real child process, in the real worktree, using the real verbs.
#
# `$1` is the prompt, ignored — this agent already knows what it found. Everything it
# does, a genuine session does the same way: write the evidence body, file the finding
# through the write path, write the coverage report, bind it to the pass, and queue what
# it could not cover.
AGENT = r"""
set -e
mkdir -p audit/findings audit/passes
cat > audit/findings/__FID__.md <<'BODY_EOF'
## Evidence

__EVIDENCE__
BODY_EOF
cat > audit/passes/__PID__-report.md <<'REPORT_EOF'
__REPORT__
REPORT_EOF
X="$PYBIN $XCHECK_BIN --project ."
# PHASE 9: the locator is resolved against the recorded subject commit at the write
# boundary. `audit/XCHECK.md` is in the seed this worktree was made from, so it locates,
# and the hash is computed here rather than pinned — the fixture must not carry a digest
# of a file it does not control.
SHA=$($PYBIN -c "import hashlib,subprocess,sys; sys.stdout.write(hashlib.sha256(subprocess.run(['git','cat-file','blob','HEAD:audit/XCHECK.md'],capture_output=True).stdout).hexdigest())")
$X file-finding --id __FID__ --title "a comparison that checks nothing" \
   --severity major --dimension invariants --unit __UNIT__ --pass __PID__ \
   --body findings/__FID__.md \
   --locator audit/XCHECK.md:1 --source-hash "$SHA"
$X record-coverage __PID__ --report passes/__PID__-report.md --findings __FID__
$X queue-pass __REMAINDER__ --dimension invariants --units __UNIT__ \
   --charter "the error paths of __MATERIAL__, uncovered by __PID__" \
   --stop "when every raise in the module has been read"
"""


class PreservationCase(unittest.TestCase):
    """Shared machinery: build a project, run one real pass through `agent_pass`."""

    maxDiff = None

    def project(self, doc=None, extra_conf=""):
        fx = Fixture(doc or one_pass_doc())
        self.addCleanup(fx.cleanup)
        (fx.root / "src").mkdir(exist_ok=True)
        (fx.root / "src" / "u1.py").write_text("def u1():\n    return 1\n",
                                               encoding="utf-8")
        (fx.root / "src" / "u2.py").write_text("def u2():\n    return 2\n",
                                               encoding="utf-8")
        commit(fx.root)
        return fx

    def agent_script(self, body=EVIDENCE, report=REPORT, pid="P-01", fid="F-0001",
                     unit="U01", remainder="P-03", material="src/u1.py"):
        """The child the sandbox runs. Written to a system temp dir, never the project.

        `fid` is not free: the dispatcher reserves a disjoint id block per pass and the
        merge refuses a finding outside it, so a second agent has to file into its own
        block exactly as a real session would after reading its charter."""
        d = Path(tempfile.mkdtemp(prefix="xcheck-agent-"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        script = d / "agent.sh"
        script.write_text(
            AGENT.replace("__EVIDENCE__", body.rstrip("\n"))
            .replace("__REPORT__", report.rstrip("\n"))
            .replace("__FID__", fid).replace("__PID__", pid).replace("__UNIT__", unit)
            .replace("__REMAINDER__", remainder).replace("__MATERIAL__", material),
            encoding="utf-8")
        return script

    def conf(self, script, **over):
        raw = dict(util.CONF_DEFAULTS,
                   # This fixture dispatches, so it classifies. See test_trust_level.py.
                   trust_level="trusted",
                   auditor_cmd=f"sh {script} {{prompt}}",
                   sandbox_profile="worktree",
                   parallel_passes="on",
                   **{k: str(v) for k, v in over.items()})
        return util.Conf(raw)

    def env_for_child(self):
        """`PYBIN`/`XCHECK_BIN` reach the child through the env allowlist.

        `child_environment` strips everything not allowlisted, so a script needing the
        interpreter and the CLI has to be handed them the way the runner hands a session
        its own variables — through the conf, not through the test's environment."""
        return {"PYBIN": sys.executable, "XCHECK_BIN": str(XCHECK)}

    def run_one_pass(self, fx, conf, pass_ids=("P-01",), announce=None):
        """One real dispatch through `agent_pass` + `run_passes`, and what it left."""
        said = announce if announce is not None else []
        session = parallel.agent_pass(fx.root, conf, list(pass_ids), announce=said.append)
        results = parallel.run_passes(fx.root, conf, list(pass_ids), session,
                                      announce=said.append)
        return results, said

    def run_cli(self, root, *argv):
        return subprocess.run([sys.executable, str(XCHECK), "--project", str(root),
                               *argv], capture_output=True, text=True, timeout=300)


# --------------------------------------------------------------------------
# criteria 1 and 2: end to end, byte for byte
# --------------------------------------------------------------------------


def WORKTREE_GLOB():
    """Every dispatch worktree for P-01 currently on disk, live ones only (`tree/`)."""
    return sorted(p for p in Path(tempfile.gettempdir()).glob("xcheck-auditor-p-01-*")
                  if (p / "tree").exists())


class TheArtifactsSurviveTheWorktree(PreservationCase):

    def setUp(self):
        self.fx = self.project()
        # The child needs the interpreter and the CLI path; the runner's env allowlist
        # is what decides whether they arrive, so they go through the same door a real
        # session's variables go through.
        self.real_env = runner.child_environment
        extra = self.env_for_child()
        runner.child_environment = lambda conf, add=None: dict(
            self.real_env(conf, add), **extra)
        self.addCleanup(lambda: setattr(runner, "child_environment", self.real_env))
        self.conf_ = self.conf(self.agent_script())
        # PHASE 16. The leak check below globs the SHARED system temp directory, so it
        # was history-dependent: one worktree left behind by an earlier killed run — not
        # by this test, not even by this suite — reddened it forever, and a real leak by
        # this run looked identical. Record what was there BEFORE the dispatch; the
        # assertion is about the delta, which is the only part this test produced.
        self.worktrees_before = set(WORKTREE_GLOB())
        self.results, self.said = self.run_one_pass(self.fx, self.conf_)

    def test_the_pass_merged_at_all(self):
        """Everything below is meaningless if the fixture never ran a session."""
        print("\n=== the dispatcher said ===")
        for line in self.said:
            print("  " + line)
        pid, attempts, err = self.results[0]
        self.assertIsNone(err, f"the pass did not merge: {err}")
        self.assertEqual(pid, "P-01")
        self.assertGreaterEqual(attempts, 1)

    def test_the_worktree_is_gone(self):
        """The teardown is real. A test that ran without one proves nothing about
        surviving it.

        Scoped to the delta (see `setUp`): a worktree this run created and did not
        remove fails; residue from some other run is reported and not charged here.
        """
        strangers = self.worktrees_before
        left = [p for p in WORKTREE_GLOB() if p not in strangers]
        print(f"\nworktrees left behind by THIS pass: {left}"
              + (f"   (ignored, not this run's: {len(strangers)})" if strangers else ""))
        self.assertEqual(left, [], "the worktree outlived the pass")

    def test_control_the_leak_check_still_catches_a_leak_and_ignores_a_stranger(self):
        """Scoping to a delta is how a check quietly stops checking. Both arms, here.

        ARM 1: a worktree that appears AFTER the dispatch is this run's and is caught.
        ARM 2: one that was already there when the dispatch began is somebody else's
        residue — reported, never charged to this pass.
        """
        planted = Path(tempfile.mkdtemp(prefix="xcheck-auditor-p-01-"))
        (planted / "tree").mkdir()
        self.addCleanup(shutil.rmtree, planted, True)
        caught = [q for q in WORKTREE_GLOB() if q not in self.worktrees_before]
        print(f"  CONTROL arm 1  a leak by this run -> {caught}")
        self.assertEqual([planted], caught, "a real leak is no longer caught")

        stranger = Path(tempfile.mkdtemp(prefix="xcheck-auditor-p-01-"))
        (stranger / "tree").mkdir()
        self.addCleanup(shutil.rmtree, stranger, True)
        before = set(self.worktrees_before) | {stranger}
        ignored = [q for q in WORKTREE_GLOB() if q not in before]
        print(f"  CONTROL arm 2  residue from another run -> ignored, still {ignored}")
        self.assertEqual([planted], ignored,
                         "a worktree that predates the dispatch was charged to it")

    def test_the_evidence_body_survives_byte_for_byte(self):
        """Criterion 2. The reproduced failure produced a body that EXISTED."""
        body = self.fx.audit / "findings" / "F-0001.md"
        self.assertTrue(body.is_file(), "no finding body at all")
        text = body.read_text(encoding="utf-8")
        print(f"\n=== audit/findings/F-0001.md ({len(text)} bytes) ===\n{text}")
        self.assertIn(EVIDENCE, text,
                      "the evidence section is not present byte for byte — this is the "
                      "audit's BODY_HAS_EVIDENCE=False")
        self.assertTrue(text.startswith("---"),
                        "the merge did not render the record's frontmatter over the body")

    def test_the_pass_report_survives_byte_for_byte(self):
        report = self.fx.audit / "passes" / "P-01-report.md"
        self.assertTrue(report.is_file(), "the audit's REPORT_EXISTS=False")
        got = report.read_text(encoding="utf-8")
        print(f"\n=== audit/passes/P-01-report.md ===\n{got}")
        self.assertEqual(got, REPORT, "the report changed between sandbox and project")

    def test_the_remainder_pass_survives(self):
        state = state_mod.load_state(self.fx.audit)
        ids = [q.id for q in state.queue]
        entry = state.queue_entry("P-03")
        print(f"\nqueue after the merge: {ids}")
        self.assertIn("P-03", ids, "the split remainder was dropped with the worktree")
        self.assertIn("error paths", entry.charter)

    def test_the_finding_and_its_coverage_merged(self):
        state = state_mod.load_state(self.fx.audit)
        entry = state.queue_entry("P-01")
        print(f"findings: {[f.id for f in state.findings]} · "
              f"P-01 done={entry.done} coverage={entry.coverage}")
        self.assertEqual([f.id for f in state.findings], ["F-0001"])
        self.assertTrue(entry.coverage, "no coverage record merged")

    def test_lint_is_green_after_the_merge(self):
        """Criterion 1's last clause, and the audit's LINT_RC=1."""
        p = self.run_cli(self.fx.root, "lint")
        print(f"\n=== xcheck lint exit={p.returncode} ===\n"
              + (p.stdout + p.stderr).strip()[-800:])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


# --------------------------------------------------------------------------
# criterion 3: two passes, and the refused conflict
# --------------------------------------------------------------------------


class TwoPassesKeepTheirOwnArtifacts(PreservationCase):

    def test_both_passes_preserve_and_neither_overwrites_the_other(self):
        fx = self.project(two_pass_doc())
        real = runner.child_environment
        extra = self.env_for_child()
        runner.child_environment = lambda conf, add=None: dict(real(conf, add), **extra)
        self.addCleanup(lambda: setattr(runner, "child_environment", real))

        # Two agents, two different bodies, two different pass ids. The scripts differ
        # only in what they write, which is what makes "neither overwrote the other"
        # observable rather than assumed.
        a = self.agent_script(body=EVIDENCE, report=REPORT)
        b_body = "The second pass found something else entirely, in `src/u2.py:12`.\n"
        b_report = "# Pass P-02 — coverage report\n\nRead src/u2.py. One finding.\n"
        b = self.agent_script(body=b_body, report=b_report, pid="P-02", fid="F-0016",
                              unit="U02", remainder="P-04", material="src/u2.py")
        scripts = {"P-01": a, "P-02": b}

        conf = self.conf(a, parallel_workers=2, parallel_confirm_above=10)
        said = []
        # One `agent_pass` per pass id, because the two use different agent commands —
        # the production dispatcher runs one command for the whole group. Each is still
        # built over the WHOLE group, so both compute the same disjoint id blocks:
        # P-01 gets F-0001.., P-02 gets F-0051.., which is why the second agent files
        # F-0051 above rather than F-0002.
        sessions = {pid: parallel.agent_pass(fx.root, self.conf(scripts[pid]),
                                             ["P-01", "P-02"], announce=said.append)
                    for pid in ("P-01", "P-02")}
        results = parallel.run_passes(fx.root, conf, ["P-01", "P-02"],
                                      lambda pid: sessions[pid](pid),
                                      announce=said.append)
        print("\n=== two passes ===")
        for line in said:
            print("  " + line)
        errors = [(pid, err) for pid, _a, err in results if err is not None]
        self.assertEqual(errors, [], f"a pass failed to merge: {errors}")

        one = (fx.audit / "findings" / "F-0001.md").read_text(encoding="utf-8")
        two = (fx.audit / "findings" / "F-0016.md").read_text(encoding="utf-8")
        r1 = (fx.audit / "passes" / "P-01-report.md").read_text(encoding="utf-8")
        r2 = (fx.audit / "passes" / "P-02-report.md").read_text(encoding="utf-8")
        print(f"F-0001 has P-01's evidence: {EVIDENCE.strip()[:40]!r} -> "
              f"{EVIDENCE in one}")
        print(f"F-0002 has P-02's evidence: {b_body.strip()[:40]!r} -> {b_body in two}")
        self.assertIn(EVIDENCE, one)
        self.assertIn(b_body, two)
        self.assertNotIn(b_body, one, "P-02's evidence overwrote P-01's")
        self.assertEqual(r1, REPORT)
        self.assertEqual(r2, b_report)
        state = state_mod.load_state(fx.audit)
        self.assertEqual(sorted(f.id for f in state.findings), ["F-0001", "F-0016"])
        self.assertEqual(sorted(q.id for q in state.queue),
                         ["P-01", "P-02", "P-03", "P-04"],
                         "both remainders did not survive")
        p = self.run_cli(fx.root, "lint")
        print(f"lint after two passes: exit={p.returncode}")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_a_same_path_conflict_is_refused_by_name(self):
        """Two manifests for one path, with different bytes. The second is refused.

        Built as manifests rather than as two sessions, because a session cannot be made
        to produce a colliding path on demand — the id block prevents it. What is under
        test is the merge's compare-and-set, and this reaches it directly."""
        fx = self.project()
        rel = "audit/findings/F-0001.md"
        first = self.manifest(rel, b"# the first pass wrote this\n", before=None)
        second = self.manifest(rel, b"# the second pass wrote something else\n",
                               before=None)
        parallel.apply_artifacts(fx.root, first)
        with self.assertRaises(StateError) as caught:
            parallel.apply_artifacts(fx.root, second)
        print(f"\n=== the conflict refusal ===\n{caught.exception}")
        self.assertIn(rel, str(caught.exception))
        self.assertIn("changed under it", str(caught.exception))
        self.assertEqual((fx.root / rel).read_bytes(), b"# the first pass wrote this\n",
                         "the refused pass still overwrote the first one's artifact")

    def manifest(self, rel, data, before=None, pid="P-XX"):
        d = Path(tempfile.mkdtemp(prefix="xcheck-manifest-"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        blob = d / "blob"
        blob.write_bytes(data)
        man = parallel.Manifest(pid, d)
        man.entries.append({"path": rel, "digest": hashlib.sha256(data).hexdigest(),
                            "before": before, "staged": str(blob)})
        return man


# --------------------------------------------------------------------------
# criteria 4 and 5: hashes, and the transaction
# --------------------------------------------------------------------------


class TheApplyIsTransactional(PreservationCase):

    def stage(self, pid, files, before=None):
        """A manifest over several paths, staged like a real one."""
        d = Path(tempfile.mkdtemp(prefix="xcheck-manifest-"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        man = parallel.Manifest(pid, d)
        for i, (rel, data) in enumerate(files.items()):
            blob = d / f"{i:03d}.blob"
            blob.write_bytes(data)
            man.entries.append({"path": rel,
                                "digest": hashlib.sha256(data).hexdigest(),
                                "before": (before or {}).get(rel),
                                "staged": str(blob)})
        return man

    def test_a_corrupted_artifact_is_detected_and_refused(self):
        """Criterion 4. The manifest's hash is what the session produced; the check is
        against what LANDED. Corrupt the staged bytes after hashing and the mismatch is
        the only thing that can catch it."""
        fx = self.project()
        man = self.stage("P-01", {"audit/findings/F-0001.md": b"the real evidence\n",
                                  "audit/passes/P-01-report.md": REPORT.encode()})
        Path(man.entries[0]["staged"]).write_bytes(b"corrupted in transit\n")
        before = tree_digest(fx.root)
        with self.assertRaises(StateError) as caught:
            parallel.apply_artifacts(fx.root, man)
        print(f"\n=== the corruption refusal ===\n{caught.exception}")
        self.assertIn("audit/findings/F-0001.md", str(caught.exception))
        self.assertIn("not the evidence", str(caught.exception))
        after = tree_digest(fx.root)
        print(f"tree identical after the refusal: {before == after}")
        self.assertEqual(before, after,
                         "the refused apply left the second artifact behind")

    def test_a_failure_partway_through_leaves_the_project_byte_identical(self):
        """Criterion 5. The failure is INJECTED into the production path — one staged
        blob is removed, so the real `read_bytes` raises on the third of four writes.
        No flag, no branch that exists only for this test."""
        fx = self.project()
        files = {f"audit/findings/F-{i:04d}.md": f"evidence {i}\n".encode()
                 for i in range(1, 5)}
        man = self.stage("P-01", files)
        Path(man.entries[2]["staged"]).unlink()          # the injection
        before = tree_digest(fx.root)
        with self.assertRaises(FileNotFoundError):
            parallel.apply_artifacts(fx.root, man)
        after = tree_digest(fx.root)
        landed = sorted(set(after) - set(before))
        print(f"\ninjected a missing blob at artifact 3 of 4 · "
              f"paths left behind: {landed}")
        self.assertEqual(before, after,
                         f"the partial apply left {landed} in the project")
        self.assertFalse((fx.audit / "findings" / "F-0001.md").exists()
                         and "audit/findings/F-0001.md" not in before,
                         "the first two artifacts were not rolled back")

    def test_the_rollback_restores_previous_bytes_not_just_absence(self):
        """A path the pass MODIFIED must come back as it was, not vanish."""
        fx = self.project()
        target = fx.audit / "findings" / "F-9001.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("the operator's own notes\n", encoding="utf-8")
        old = target.read_bytes()
        man = self.stage("P-01",
                         {"audit/findings/F-9001.md": b"the session's version\n",
                          "audit/findings/F-9002.md": b"a new one\n"},
                         before={"audit/findings/F-9001.md":
                                 hashlib.sha256(old).hexdigest()})
        Path(man.entries[1]["staged"]).unlink()
        with self.assertRaises(FileNotFoundError):
            parallel.apply_artifacts(fx.root, man)
        print(f"\nmodified path restored: {target.read_bytes() == old}")
        self.assertEqual(target.read_bytes(), old)

    def test_a_merge_that_does_not_land_rolls_its_artifacts_back(self):
        """The transaction spans BOTH halves. A state merge that is refused must not
        leave the evidence for a record nothing admitted."""
        fx = self.project()
        rel = "audit/findings/F-0001.md"
        man = self.stage("P-01", {rel: b"evidence for a finding that never merged\n"})
        # `events.jsonl` is subtracted, and naming it is the point: the merge attempt is
        # itself an event, so a run that refused still appends one line. Comparing the
        # whole tree without subtracting the orchestrator's own bookkeeping would make
        # "nothing changed" false for every run, refused or not.
        bookkeeping = ("audit/events.jsonl",)
        before = tree_digest(fx.root, skip=bookkeeping)

        def refuses(doc, state):
            raise StateError("the merge refuses, for this test's purposes")

        said = []
        results = parallel.run_passes(fx.root, self.conf(self.agent_script()), ["P-01"],
                                      lambda pid: (refuses, man), announce=said.append)
        print("\n=== merge refused ===")
        for line in said:
            print("  " + line)
        after = tree_digest(fx.root, skip=bookkeeping)
        self.assertIsNotNone(results[0][2], "the merge did not fail — nothing to roll back")
        self.assertEqual(before, after,
                         f"artifacts stayed after a refused merge: "
                         f"{sorted(set(after) - set(before))}")
        self.assertFalse((fx.root / rel).exists(), "the evidence body stayed behind")


# --------------------------------------------------------------------------
# criterion 6: the lift is coupled to the proof
# --------------------------------------------------------------------------


class TheLiftIsCoupledToTheProof(unittest.TestCase):
    """The capability is available exactly while the machinery works.

    Not "while a flag says it works", and not "while this comment says it was fixed in
    0.9.1". `enabled` runs the real apply path against a throwaway project and reads the
    byte back — so breaking preservation closes the branch again, in the same process,
    with no release in between."""

    def test_the_flag_is_honoured_when_the_probe_passes(self):
        self.assertIsNone(parallel.preservation_probe(),
                          "the probe fails on an unmodified build")
        self.assertTrue(parallel.enabled({"parallel_passes": "on"}))
        self.assertFalse(parallel.enabled({"parallel_passes": "off"}),
                         "the flag is ignored — `on` is not what opens this branch")

    def test_breaking_the_apply_path_closes_the_branch_again(self):
        """The coupling, as a mutation: stub the transactional apply and the operator
        gets the phase-1 refusal back, naming what the probe observed."""
        real = parallel.apply_artifacts
        parallel.apply_artifacts = lambda project, manifest: (lambda: None)
        self.addCleanup(lambda: setattr(parallel, "apply_artifacts", real))
        reason = parallel.preservation_probe()
        print(f"\nprobe with the apply path stubbed: {reason}")
        self.assertIsNotNone(reason, "a no-op apply path was reported as preserving")
        with self.assertRaises(SystemExit) as caught:
            parallel.enabled({"parallel_passes": "on"})
        text = str(caught.exception)
        print(f"\n=== the refusal that returns ===\n{text}")
        self.assertIn("cannot preserve a pass's evidence", text)
        self.assertIn("wrote nothing", text)
        self.assertIn("parallel_passes=off", text)

    def test_breaking_the_hash_check_closes_the_branch_too(self):
        """A second, different break: the bytes land but corrupted. The probe compares
        content, so a refusal here proves it is not merely checking existence."""
        real = parallel.apply_artifacts

        def corrupting(project, manifest):
            for e in manifest.entries:
                p = Path(project) / e["path"]
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"truncated")
            return lambda: None

        parallel.apply_artifacts = corrupting
        self.addCleanup(lambda: setattr(parallel, "apply_artifacts", real))
        reason = parallel.preservation_probe()
        print(f"\nprobe with a truncating apply path: {reason}")
        self.assertIsNotNone(reason)
        self.assertIn("different bytes", reason)

    def test_the_dispatcher_reads_the_capability_at_exactly_one_gate(self):
        """One gate is the whole argument. A second caller added later would be a path
        around the probe, and this fails the moment one appears."""
        sites = []
        for py in sorted((REPO / "xcheck").glob("*.py")):
            for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if "parallel.enabled(" in line and not line.lstrip().startswith("#"):
                    sites.append(f"xcheck/{py.name}:{i}")
        print(f"\nparallel.enabled call sites ({len(sites)}): {', '.join(sites)}")
        self.assertEqual(len(sites), 1, f"more than one gate reads the flag: {sites}")


# --------------------------------------------------------------------------
# what the manifest carries, and what it refuses to
# --------------------------------------------------------------------------


class TheMergeRunsInsideTheLockTheDispatcherHolds(PreservationCase):
    """Found the moment the ban lifted: the branch had never been run end to end.

    `cmd_next` holds `audit/.lock` across the whole next-transaction (F-0009), and the
    parallel branch then merged each pass through `_apply`, which acquired the lock
    again — and refused its own process with "audit/.lock is held". Every parallel run
    through the CLI would have ended that way, and no test could see it while the flag
    was refused two frames earlier.
    """

    def test_a_merge_under_the_held_lock_lands(self):
        fx = self.project()
        lock = runner.Lock(fx.audit, "Auditor")
        lock.acquire()
        try:
            attempts = parallel.merge(
                fx.root, lambda doc, state: ("merged under the caller's lock", {}),
                state_mod.load_state(fx.audit).state_revision,
                target="P-01", announce=lambda m: None, lock=lock)
        finally:
            lock.release()
        print(f"\nmerge under the held lock: attempts={attempts}")
        self.assertEqual(attempts, 1)

    def test_the_same_merge_without_the_lock_refuses_itself(self):
        """The counterfactual, so the fix above is not decoration: the identical merge
        with the lock NOT handed in is refused by the lock the caller is holding."""
        fx = self.project()
        lock = runner.Lock(fx.audit, "Auditor")
        lock.acquire()
        try:
            with self.assertRaises(SystemExit) as caught:
                parallel.merge(fx.root, lambda doc, state: ("never reached", {}),
                               state_mod.load_state(fx.audit).state_revision,
                               target="P-01", announce=lambda m: None)
        finally:
            lock.release()
        print(f"\nwithout the held lock: {str(caught.exception)[:120]}")
        self.assertIn("audit/.lock is held", str(caught.exception))

    def test_an_unacquired_lock_object_is_not_trusted(self):
        """Fail-safe: handing in a lock nobody acquired must not produce an UNLOCKED
        write. `_apply` acquires its own, so the write is still serialized."""
        fx = self.project()
        never = runner.Lock(fx.audit, "Auditor")
        self.assertIsNone(never.token)
        attempts = parallel.merge(
            fx.root, lambda doc, state: ("merged with its own lock", {}),
            state_mod.load_state(fx.audit).state_revision,
            target="P-01", announce=lambda m: None, lock=never)
        print(f"\nunacquired lock handed in: attempts={attempts}, "
              f"lock left behind={ (fx.audit / '.lock').exists() }")
        self.assertEqual(attempts, 1)
        self.assertFalse((fx.audit / ".lock").exists(),
                         "the write left a lock behind — it released one it did not own")


class TheManifestKnowsWhatIsAnArtifact(PreservationCase):

    def worktree(self, fx, files, delete=()):
        """A real sandbox, written into, staged and hashed — then torn down."""
        sb = runner.Sandbox(fx.root, runner.resolve_profile({}, "Auditor"), "Auditor-P-01")
        sb.enter()
        try:
            for rel, text in files.items():
                p = Path(sb.workdir) / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text, encoding="utf-8")
            for rel in delete:
                (Path(sb.workdir) / rel).unlink()
            return parallel.stage_artifacts(sb.workdir, sb.base, "P-01")
        finally:
            sb.leave()

    def test_the_canonical_document_and_its_view_are_never_carried_as_bytes(self):
        fx = self.project()
        man = self.worktree(fx, {"audit/state.json": "{}\n",
                                 "audit/LEDGER.md": "# not this either\n",
                                 "audit/findings/F-0001.md": "## Evidence\n\nkeep me\n"})
        print(f"\ncarried: {man.paths()}")
        self.assertEqual(man.paths(), ["audit/findings/F-0001.md"])
        man.discard()

    def test_material_outside_audit_is_not_carried(self):
        fx = self.project()
        man = self.worktree(fx, {"src/u1.py": "def u1():\n    return 999\n",
                                 "audit/passes/P-01-report.md": REPORT})
        print(f"carried: {man.paths()}")
        self.assertEqual(man.paths(), ["audit/passes/P-01-report.md"])
        man.discard()

    def test_a_deleted_artifact_is_refused_by_name(self):
        fx = self.project()
        (fx.audit / "findings").mkdir(parents=True, exist_ok=True)
        (fx.audit / "findings" / "F-9001.md").write_text("older evidence\n",
                                                         encoding="utf-8")
        commit(fx.root)
        with self.assertRaises(StateError) as caught:
            self.worktree(fx, {}, delete=("audit/findings/F-9001.md",))
        print(f"\n=== the deletion refusal ===\n{caught.exception}")
        self.assertIn("F-9001.md", str(caught.exception))
        self.assertIn("DELETED", str(caught.exception))

    def test_the_staging_directory_is_outside_the_project(self):
        """F-0120: the courier ships the project tree. A staged artifact inside it would
        be committed as though the pass had filed it twice."""
        fx = self.project()
        man = self.worktree(fx, {"audit/findings/F-0001.md": "## Evidence\n\nx\n"})
        print(f"\nstaged at: {man.dir}")
        self.assertFalse(str(man.dir).startswith(str(fx.root)),
                         f"artifacts staged inside the project at {man.dir}")
        self.assertTrue(man.dir.is_dir(), "the staging directory did not survive leave()")
        self.assertEqual(Path(man.entries[0]["staged"]).read_bytes(),
                         b"## Evidence\n\nx\n",
                         "the staged bytes did not survive the teardown")
        man.discard()
        self.assertFalse(man.dir.exists(), "discard() left the staging directory behind")


# --------------------------------------------------------------------------
# the guard that could not fire
# --------------------------------------------------------------------------


class TheMaterialGuardCanFire(PreservationCase):
    """`changed_paths` compares the INDEX against the seed commit. 0.9.0's guard in
    `agent_pass` never staged, so it read an empty list on every run — a refusal that
    could not happen. Found while wiring the manifest through the same call."""

    def test_an_auditor_that_edits_the_material_is_refused(self):
        fx = self.project()
        script = self.agent_script()
        # An agent that edits the material it is auditing, then reports normally.
        text = script.read_text(encoding="utf-8")
        script.write_text("echo 'auditor edit' >> src/u1.py\n" + text, encoding="utf-8")
        real = runner.child_environment
        extra = self.env_for_child()
        runner.child_environment = lambda conf, add=None: dict(real(conf, add), **extra)
        self.addCleanup(lambda: setattr(runner, "child_environment", real))
        said = []
        results, said = self.run_one_pass(fx, self.conf(script), announce=said)
        print("\n=== material guard ===")
        for line in said:
            print("  " + line)
        pid, _a, err = results[0]
        self.assertIsNotNone(err, "an auditor edited src/u1.py and the pass merged")
        self.assertIn("src/u1.py", str(err))
        self.assertIn("does not edit the material", str(err))

    def test_the_unstaged_read_is_the_defect_this_closes(self):
        """The counterfactual: ask git the way 0.9.0 asked, and the answer is empty."""
        fx = self.project()
        sb = runner.Sandbox(fx.root, runner.resolve_profile({}, "Auditor"), "Auditor-P-01")
        sb.enter()
        try:
            (Path(sb.workdir) / "src" / "u1.py").write_text("edited\n", encoding="utf-8")
            unstaged = runner.changed_paths(sb.workdir, sb.base)
            staged = parallel._staged_changes(sb.workdir, sb.base)
        finally:
            sb.leave()
        print(f"\n0.9.0 (no `git add -A`): {unstaged}\n0.9.1 (staged):          {staged}")
        self.assertEqual(unstaged, [],
                         "the unstaged read now sees the change — this counterfactual "
                         "no longer demonstrates the defect it names")
        self.assertEqual(staged, ["src/u1.py"])


if __name__ == "__main__":
    unittest.main()
