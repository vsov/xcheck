"""0.9.1 phase 4: retry proves the absence of material effects.

The third audit's P0 #6. `_dispatch_with_retry` decided whether a failed session had
changed anything by re-reading the canonical bytes of `audit/state.json`, with `sessions`
and `state_revision` stripped. Source files, audit artifacts and git HEAD are invisible to
that comparison, and the audit reproduced the consequence:

    retry: yes — provider-error is transient
    CALLS=[1, 2]
    MATERIAL=base|effect-1|effect-2|

Phase 3 stops the effect landing in the first place. This phase makes the gate *prove* it
rather than assume it, over the surface a human would actually check, and name the file
when it refuses.

Five arms, because "the project changed" has five shapes and a probe can be blind to any
one of them: a tracked file, an untracked file, an `audit/` artifact, a commit that moves
HEAD leaving a clean worktree, and canonical `state.json` moving alone. Each is asserted
separately — a parameterised loop whose failure names no shape tells a reader nothing.

The fifth is the one 0.9.0 already caught, and it is here because widening a gate is how
you narrow one: `state.json` is excluded from the path scan by name (it carries the
orchestrator's own session row), and for one commit this phase traded the old blind spot
for a new one.

The control arm is not optional. A gate that always refuses passes all four arms above
perfectly and is useless, so the same test file proves a session that changed nothing IS
retried.
"""

import contextlib
import io
import json
import shutil
import subprocess
import unittest

from tests.harness import Fixture, finding_record, state_doc, xcheck_submodule

runner = xcheck_submodule("runner")
write_mod = xcheck_submodule("write")
util = xcheck_submodule("util")
cli = xcheck_submodule("cli")
envelope = xcheck_submodule("envelope")

GIT = shutil.which("git")


def add_finding(doc, _state):
    """A `mutate` that files one finding through the real write path."""
    doc["findings"].append(finding_record("F-0002", "reported"))
    return "filed F-0002", {"finding": "F-0002"}


def git(cwd, *args):
    return subprocess.run([GIT, *args], cwd=str(cwd), capture_output=True, text=True,
                          timeout=60)


def project(**over):
    fx = Fixture(doc=state_doc(findings=[finding_record()]))
    (fx.root / "material.txt").write_text("original\n", encoding="utf-8")
    for args in (("init", "-q"), ("config", "user.email", "t@example.invalid"),
                 ("config", "user.name", "test"), ("add", "-A"), ("commit", "-qm", "base")):
        git(fx.root, *args)
    # The conf is written WHOLE. A partial one silently drops `remediator_cmd`, and the
    # refusal then measures a different gate than the one under test.
    # PHASE 3: `trust_level` has no default in `CONF_DEFAULTS` and must not get one —
    # the refusal exists because the cheap assumption is `trusted`. A fixture project
    # is synthetic and trusted BY CONSTRUCTION, so it makes the operator's declaration
    # explicitly. `tests/test_trust_level.py` is where the unset case is exercised.
    raw = dict(util.CONF_DEFAULTS)
    raw.setdefault("trust_level", "trusted")
    raw.update({k: str(v) for k, v in over.items()})
    return fx, util.Conf(raw)


class TheGateBlocksOnEveryShapeOfChange(unittest.TestCase):
    """Criterion 1. The session is a STUB here, and deliberately so.

    Phase 3 stops a real non-`ok` session from applying anything, so a real dispatch can
    no longer produce arms 1-4 — the effects it must refuse to retry over are exactly the
    ones that path no longer creates. The stub puts the effect on disk by hand, which is
    the only way to ask this gate the question it exists to answer: given a project that
    HAS changed under a failed session, does it re-dispatch? Each arm names the shape.
    """

    def arm(self, name, effect, *, retry_limit=2, record_material=(), unnamed=False):
        fx, conf = project(retry_limit=retry_limit)
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        calls = []

        def fake_session(project_, conf_, role, charter, dry_run=False, attempt=0, **kw):
            calls.append(attempt)
            sid = f"{len(calls):016x}"
            rec = envelope.dispatch_record(
                ["true"], role, charter or "", "prompt", sid,
                runner.resolve_profile({}, role), envelope.current_revision(project_),
                head_before="0" * 40, env={}, conf={})
            envelope.store(project_, envelope.finish(rec, 1, "provider-error", 1.0,
                                                     "0" * 40, fx.audit / "state.json"),
                           event="session_finished")
            effect(fx)
            return runner.RunResult(1, "provider-error", session_id=sid, attempt=attempt,
                                    material_paths=record_material,
                                    effects_applied=bool(record_material) or unnamed)

        real = cli.run_session
        cli.run_session = fake_session
        self.addCleanup(lambda: setattr(cli, "run_session", real))
        self.assertIsNot(cli.run_session, real, "the stub was not installed")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli._dispatch_with_retry(fx.root, conf, "Remediator", "F-0001", False, None,
                                     None, sleep=lambda s: None)
        # retry_limit is set explicitly by `project()` above, never inherited.
        console = out.getvalue()
        probe = [ln for ln in console.splitlines() if ln.startswith("probe:")][-1]
        decision = [ln for ln in console.splitlines() if ln.startswith("retry:")][-1]
        print(f"\n  {name}\n    {probe}\n    {decision}\n    dispatches={len(calls)}")
        return calls, console, probe, decision

    def test_a_tracked_file(self):
        calls, console, probe, _d = self.arm(
            "tracked file changed",
            lambda fx: (fx.root / "material.txt").write_text("touched\n", encoding="utf-8"))
        self.assertEqual(len(calls), 1, "the gate re-dispatched over a changed file")
        self.assertIn("material.txt", console)
        self.assertIn("this session already changed the project", console)
        self.assertIn("1 material change(s)", probe)

    def test_an_untracked_file(self):
        calls, console, probe, _d = self.arm(
            "untracked file created",
            lambda fx: (fx.root / "stray.py").write_text("print(1)\n", encoding="utf-8"))
        self.assertEqual(len(calls), 1)
        self.assertIn("stray.py", console)

    def test_an_audit_artifact(self):
        calls, console, probe, _d = self.arm(
            "audit artifact written",
            lambda fx: (fx.audit / "findings").mkdir(exist_ok=True)
            or (fx.audit / "findings" / "F-0001.md").write_text("body\n", encoding="utf-8"))
        self.assertEqual(len(calls), 1, "an audit artifact did not block the retry")
        self.assertIn("audit/findings/F-0001.md", console)

    def test_a_commit_that_leaves_a_clean_worktree(self):
        """The shape `git status` alone cannot see: the session committed its work, so
        the worktree is spotless and only HEAD remembers."""
        def commit(fx):
            (fx.root / "material.txt").write_text("committed\n", encoding="utf-8")
            git(fx.root, "add", "-A")
            git(fx.root, "commit", "-qm", "the session's own commit")

        calls, console, probe, _d = self.arm("commit, clean worktree", commit)
        self.assertEqual(len(calls), 1, "a commit alone did not block the retry")
        self.assertIn("material.txt", console)
        self.assertIn("this session already changed the project", console)

    def test_control_a_session_that_changed_nothing_is_retried(self):
        """Criterion 2. Without this, a gate hard-wired to refuse passes every arm."""
        calls, console, probe, decision = self.arm("CONTROL: nothing changed",
                                                   lambda fx: None)
        self.assertEqual(len(calls), 3, "the gate refused a retry it had no reason to "
                                        "refuse — every arm above is then vacuous")
        self.assertIn("0 material change(s)", probe)
        self.assertIn("retry: yes", console)

    def test_control_the_record_alone_blocks_when_the_probe_is_blind(self):
        """The two answers are AND-ed. This arm changes nothing on disk but has the
        SESSION report a material path, and the retry must still be refused — the record
        is not decoration hanging off a probe that does all the work."""
        calls, console, probe, _d = self.arm(
            "record says changed, disk says clean", lambda fx: None,
            record_material=("src/only_the_record_knows.py",))
        self.assertEqual(len(calls), 1)
        self.assertIn("0 material change(s)", probe)
        self.assertIn("src/only_the_record_knows.py", console)

    def test_canonical_state_moving_on_its_own(self):
        """The fifth shape, and the one 0.9.0 already caught.

        A session that files a finding through the write path moves canonical
        `state.json` and touches nothing else. `_material_paths` excludes `state.json`
        by name — it carries the orchestrator's own session row — so widening the gate
        to files, artifacts and HEAD came within one commit of NARROWING it here. The
        snapshot keeps the canonical bytes for exactly this arm; `tests/test_parallel_
        retry.py` holds the same case driven through the real write path."""
        def file_a_finding(fx):
            write_mod._apply(fx.root, add_finding, verb="file-finding", target="F-0002")

        calls, console, probe, _d = self.arm("canonical state.json moved",
                                             file_a_finding)
        self.assertEqual(len(calls), 1,
                         "a session that filed a finding was dispatched twice — the "
                         "surface 0.9.0 DID cover was lost while widening the gate")
        self.assertIn("audit/state.json", console)

    def test_a_record_that_reports_effects_without_naming_them_still_blocks(self):
        """Fail-closed, the awkward case. A session reporting `effects_applied=True` and
        no paths is a session saying "I changed something" without saying what. The
        cheaper reading — no names, nothing to block on — would retry it."""
        calls, console, probe, _d = self.arm("record reports effects, names none",
                                             lambda fx: None, unnamed=True)
        self.assertEqual(len(calls), 1,
                         "an unnamed reported effect did not block the retry")
        self.assertIn("0 material change(s)", probe)
        self.assertIn("this session already changed the project", console)


class TheProbeCannotPassByLookingAtNothing(unittest.TestCase):
    """Criterion 5. This run's standing lesson: a check that scans nothing is green for
    free, and reports it in the same words it uses when it looked."""

    def test_it_prints_its_coverage_and_the_count_has_a_floor(self):
        fx, conf = project()
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        snapshot = runner.material_snapshot(fx.root)
        # A positive control: one path this run definitely touched, so "compared N" is
        # measuring a real subject rather than counting an empty set.
        (fx.root / "definitely_touched.txt").write_text("x\n", encoding="utf-8")
        material, compared = runner.material_effect_probe(fx.root, snapshot)
        print(f"\n  probe compared {compared} path(s), material={material}")
        self.assertGreaterEqual(compared, 1,
                                "the probe compared nothing, so 'no material effects' "
                                "would be free")
        self.assertIn("definitely_touched.txt", material,
                      "the probe missed a file created under its own nose — the count "
                      "above is therefore not evidence of anything")

    def test_a_blind_probe_is_visible_in_the_count(self):
        """The counterfactual: point the probe at an empty directory and watch `compared`
        fall to zero. Without this, a floor assertion is an assertion about a number
        nobody has seen move."""
        blind = Fixture(doc=state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, blind.root, ignore_errors=True)
        material, compared = runner.material_effect_probe(
            blind.root, runner.material_snapshot(blind.root))
        print(f"  with no git repository: compared {compared}, material={material}")
        self.assertEqual(compared, 0)
        self.assertEqual(material, [])


class TheAuditsScenarioRerun(unittest.TestCase):
    """Criterion 3, read from the file the session wrote.

    The material marker is read off disk, never from a log line: the defective version
    printed its log line correctly too.
    """

    def test_one_dispatch_and_one_effect(self):
        fx, conf = project(retry_limit=2)
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        marker = fx.root / "material.txt"
        marker.write_text("base|", encoding="utf-8")
        git(fx.root, "add", "-A")
        git(fx.root, "commit", "-qm", "marker")
        calls = []
        state_path = fx.audit / "state.json"
        canonical_before = runner._material_bytes(state_path)

        def fake_session(project_, conf_, role, charter, dry_run=False, attempt=0, **kw):
            calls.append(attempt)
            with marker.open("a", encoding="utf-8") as fh:
                fh.write(f"effect-{len(calls)}|")
            sid = f"{len(calls):016x}"
            rec = envelope.dispatch_record(
                ["true"], role, charter or "", "prompt", sid,
                runner.resolve_profile({}, role), envelope.current_revision(project_),
                head_before="0" * 40, env={}, conf={})
            envelope.store(project_, envelope.finish(rec, 1, "provider-error", 1.0,
                                                     "0" * 40, state_path),
                           event="session_finished")
            # The audit's scenario exactly: the session does NOT report the effect. In
            # 0.9.0 nothing else was looking, so the gate re-dispatched.
            return runner.RunResult(1, "provider-error", session_id=sid, attempt=attempt)

        real = cli.run_session
        cli.run_session = fake_session
        self.addCleanup(lambda: setattr(cli, "run_session", real))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli._dispatch_with_retry(fx.root, conf, "Remediator", "F-0001", False, None,
                                     None, sleep=lambda s: None)
        # retry_limit is set explicitly by `project()` above, never inherited.
        material = marker.read_text(encoding="utf-8")
        canonical_after = runner._material_bytes(state_path)
        print(f"\n  CALLS={calls}\n  MATERIAL={material}\n  canonical state.json "
              f"{'unchanged' if canonical_before == canonical_after else 'MOVED'}"
              f"  (the surface 0.9.0 asked, and it says nothing happened)")
        print("  " + "\n  ".join(ln for ln in out.getvalue().splitlines()
                                 if ln.startswith(("probe:", "retry:"))))
        self.assertEqual(canonical_before, canonical_after,
                         "the fixture no longer reproduces the audit's scenario: "
                         "canonical state moved, so the old comparison would have "
                         "caught this too and the test proves nothing")
        self.assertEqual(calls, [0], "the session was dispatched more than once")
        self.assertEqual(material, "base|effect-1|",
                         "the material effect was applied more than once")


class TheDecisionPathNoLongerAsksTheFile(unittest.TestCase):

    def test_the_state_json_reread_is_gone_from_the_gate(self):
        """Criterion: the after-the-fact `_material_bytes` re-read is deleted from the
        decision path. The helper survives — three callers in `runner.py` still need it —
        and this is the grep that proves both halves rather than either."""
        needle = "_material" + "_bytes"
        source = (runner.Path(cli.__file__)).read_text(encoding="utf-8")
        in_cli = [i + 1 for i, ln in enumerate(source.splitlines()) if needle in ln]
        runner_src = (runner.Path(runner.__file__)).read_text(encoding="utf-8")
        in_runner = [i + 1 for i, ln in enumerate(runner_src.splitlines())
                     if needle in ln and "#" not in ln.split(needle)[0]]
        print(f"\n  {needle} in cli.py: {in_cli or 'gone'}")
        print(f"  {needle} in runner.py: {in_runner} (definition + its remaining users)")
        self.assertEqual(in_cli, [], "the retry gate still re-reads state.json")
        self.assertGreater(len(in_runner), 1,
                           "the helper has no users left — it should have been deleted "
                           "rather than kept")

    def test_head_comes_from_the_record_not_from_a_second_derivation(self):
        """Criterion 4. `RunResult` carries both ends of HEAD, and the material list it
        publishes is what the gate reads — the gate never asks git what HEAD was."""
        self.assertIn("head_before", runner.RunResult.FIELDS)
        self.assertIn("head_after", runner.RunResult.FIELDS)
        self.assertIn("material_paths", runner.RunResult.FIELDS)
        r = runner.RunResult(1, "provider-error", head_before="a" * 40,
                             head_after="b" * 40, material_paths=("src/x.py",))
        self.assertTrue(r.effects_applied is False or r.effects_applied is True)
        self.assertEqual(r.material_paths, ("src/x.py",))
        print(f"\n  record: head {r.head_before[:7]}->{r.head_after[:7]} "
              f"material_paths={list(r.material_paths)}")


    def test_the_records_head_pair_is_what_produces_its_material_paths(self):
        """The route by which the record's HEAD reaches the decision.

        The gate never asks git what HEAD was — it reads `material_paths`. This proves
        `material_paths` is DERIVED from the record's two HEAD fields and nothing else:
        a clean worktree, an empty dirty set, and a commit range as the only input.

        The alternative reading of criterion 4 — the gate consuming `head_before` and
        `head_after` directly and diffing them itself — is the shape this project
        rejected in phase 2: `head_before != head_after` is TRUE for every session ever
        run, because the courier commits the orchestrator's own bookkeeping. HEAD is
        only usable as evidence once the bookkeeping is subtracted, and the subtraction
        happens once, at the session boundary, in `_session_result`."""
        fx, _conf = project()
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        head_before = runner.git_head(fx.root)
        (fx.root / "material.txt").write_text("committed by the session\n",
                                              encoding="utf-8")
        git(fx.root, "add", "-A")
        git(fx.root, "commit", "-qm", "the session's commit")
        head_after = runner.git_head(fx.root)
        clean = runner.dirty_paths(fx.root)
        self.assertEqual(list(clean or ()), [],
                         "the worktree is not clean, so this arm would pass on the "
                         "dirty-set half and prove nothing about HEAD")
        material, compared = runner._material_paths(fx.root, head_before, head_after,
                                                    clean)
        print(f"\n  clean worktree, HEAD {head_before[:7]}->{head_after[:7]}: "
              f"material={material} (compared {compared})")
        self.assertEqual(material, ["material.txt"])
        # And the counterfactual: the same call with no HEAD movement sees nothing.
        same, _c = runner._material_paths(fx.root, head_before, head_before, clean)
        print(f"  same HEAD on both ends: material={same}")
        self.assertEqual(same, [])


class TheJournalRecordsWhatBlockedIt(unittest.TestCase):

    def test_the_retry_event_names_the_paths_and_the_coverage(self):
        fx, conf = project(retry_limit=2)
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)

        def fake_session(project_, conf_, role, charter, dry_run=False, attempt=0, **kw):
            sid = "c" * 16
            rec = envelope.dispatch_record(
                ["true"], role, charter or "", "prompt", sid,
                runner.resolve_profile({}, role), envelope.current_revision(project_),
                head_before="0" * 40, env={}, conf={})
            envelope.store(project_, envelope.finish(rec, 1, "provider-error", 1.0,
                                                     "0" * 40, fx.audit / "state.json"),
                           event="session_finished")
            (fx.root / "leftover.txt").write_text("x\n", encoding="utf-8")
            return runner.RunResult(1, "provider-error", session_id=sid, attempt=attempt)

        real = cli.run_session
        cli.run_session = fake_session
        self.addCleanup(lambda: setattr(cli, "run_session", real))
        with contextlib.redirect_stdout(io.StringIO()):
            cli._dispatch_with_retry(fx.root, conf, "Remediator", "F-0001", False, None,
                                     None, sleep=lambda s: None)
        # retry_limit is set explicitly by `project()` above, never inherited.
        events = [json.loads(ln) for ln in
                  (fx.audit / "events.jsonl").read_text(encoding="utf-8").splitlines() if ln]
        considered = [e for e in events if e.get("event") == "retry_considered"]
        print(f"\n  retry_considered: {considered[-1]}")
        self.assertEqual(len(considered), 1)
        self.assertFalse(considered[-1]["retry"])
        self.assertEqual(considered[-1]["blocked_by"], ["leftover.txt"])
        self.assertGreaterEqual(considered[-1]["paths_compared"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
