"""A fresh install is planned through the orchestrator — and no other stateless tree is.

Through 0.9.5 a new audit could not reach its first session. `status` and `next` read
`audit/state.json` and refused without it; `install.sh` writes none by design (README
§5.2); and `xcheck migrate` refuses the unwritten plan the installer leaves, because
writing that plan is the Planner's job. The Planner launcher is a wrapper over
`xcheck next`, so it refused on every new project, and the only way to plan was an
`uncontained-direct` session started by hand — the one route with none of the controls.

The door this opens is narrow on purpose. A tree with no state document is ALSO what a
Markdown audit from before 0.8 looks like, and `tests/test_no_bypass.py` holds that such
a tree is refused rather than read. So the routes below are paired with refusals: the
same tree minus ONE of the facts that make it fresh, and the control is the fresh tree.

Nothing here needs a network or a paid call. The installer is the real `install.sh`,
and the dispatch scenarios run `tests/fixtures/fake-agent/agent` as a real child under
the `worktree` profile.
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.harness import REPO, Fixture, audit_md, finding, ledger, xcheck_submodule

PLANNER_CHARTER = xcheck_submodule("util").PLANNER_CHARTER
AGENT = REPO / "tests" / "fixtures" / "fake-agent" / "agent"
NO_STATE = "no state file"


class FreshInstall(Fixture):
    """A project exactly as `install.sh` leaves it, plus an operator profile outside it.

    The installer is run, not imitated: what makes a tree "fresh" is what the real
    installer writes, and a hand-built copy would test the copy. `git=True` makes the
    project a repository FIRST, so `audit/` is untracked the way it is in a project that
    has just been installed into."""

    def __init__(self, conf=None, git=False):
        self.root = Path(tempfile.mkdtemp(prefix="xcheck-fresh-"))
        self.profile_dir = None
        self.audit = self.root / "audit"
        if git:
            self.git_init()
        p = subprocess.run(["sh", str(REPO / "install.sh"), str(self.root)],
                           capture_output=True, text=True, timeout=120)
        if p.returncode != 0:
            raise AssertionError(f"install.sh failed: {p.stdout}\n{p.stderr}")
        self.write_operator_profile()
        if conf:
            self.configure(conf)

    @property
    def state_path(self):
        return self.audit / "state.json"


class AFreshInstallIsPlannedThroughTheOrchestrator(unittest.TestCase):

    def setUp(self):
        self.fx = FreshInstall()
        self.addCleanup(self.fx.cleanup)

    def test_status_says_the_planner_is_next(self):
        code, out = self.fx.run("status")
        print(f"\nstatus on a fresh install:\n{out.strip()[:400]}")
        self.assertEqual(code, 0, out)
        self.assertIn("state: none yet", out)
        self.assertIn("does not convert yet", out)
        self.assertIn("decision: run-planner", out)
        self.assertFalse(self.fx.state_path.exists(), "status wrote a state document")

    def test_next_dispatches_the_planner_charter(self):
        code, out = self.fx.decision()
        self.assertEqual(code, 0, out)
        self.assertIn("=== Planner session ===", out)
        self.assertIn(f"charter: {PLANNER_CHARTER}", out)
        self.assertFalse(self.fx.state_path.exists(), "a dry run wrote a state document")

    def test_status_json_reports_the_empty_state(self):
        """Revision 0 is the one `write_state` never writes, so a machine reader can tell
        "no state yet" from every state that exists."""
        code, out = self.fx.run("status", "--json")
        self.assertEqual(code, 0, out)
        payload = json.loads(out)
        self.assertEqual(payload["decision"]["kind"], "run-planner")
        self.assertEqual(payload["state_revision"], 0)
        self.assertEqual(payload["passes"], {"done": 0, "queued": []})

    def test_an_unfinished_plan_goes_back_to_the_planner(self):
        """F-0077: an under-planned audit routes to the Planner. A plan whose catalogs
        are written and whose queue is not is a Planner that stopped early."""
        self.fx.write("AUDIT.md", audit_md(queue_body=""))
        code, out = self.fx.run("status")
        self.assertEqual(code, 0, out)
        self.assertIn("decision: run-planner", out)


class ACompletePlanStopsAtTheMigrateGate(unittest.TestCase):

    def setUp(self):
        self.fx = FreshInstall()
        self.addCleanup(self.fx.cleanup)
        self.fx.write("AUDIT.md", audit_md())

    def test_the_gate_names_the_migration_and_writes_nothing(self):
        code, out = self.fx.run("status")
        self.assertEqual(code, 0, out)
        self.assertIn("plan: audit/AUDIT.md converts", out)
        self.assertIn("decision: stop-migrate -> ['P-01']", out)

        code, out = self.fx.decision()
        print(f"\nnext on a complete plan:\n{out.strip()[-400:]}")
        self.assertEqual(code, 0, f"a human gate is a normal stop: {out}")
        self.assertIn("gate: plan ready", out)
        self.assertIn("xcheck migrate", out)
        self.assertIn("P-01", out)
        self.assertNotIn("Planner session", out, "a finished plan was planned again")
        self.assertFalse(self.fx.state_path.exists(),
                         "the orchestrator loaded the plan itself; migration is the "
                         "operator's act (XCHECK.md §2)")

    def test_after_the_migration_the_state_decides(self):
        code, out = self.fx.run("migrate")
        self.assertEqual(code, 0, out)
        code, out = self.fx.run("status")
        self.assertEqual(code, 0, out)
        self.assertIn("state: schema 1, revision 1", out)
        self.assertIn("decision: run-auditor -> P-01", out)


class EveryOtherStatelessTreeIsStillRefused(unittest.TestCase):
    """One fact removed per test. Each refusal must hold on BOTH surfaces — `status`
    and `next` — and must not reach a Planner session."""

    def fresh(self, **kw):
        fx = FreshInstall(**kw)
        self.addCleanup(fx.cleanup)
        return fx

    def assertRefused(self, fx, *why):
        for args in (("status",), ("next", "--dry-run")):
            code, out = fx.run(*args)
            print(f"\n{args[0]}: {out.strip()[-300:]}")
            self.assertNotEqual(code, 0, f"{args[0]} accepted a tree that is not fresh")
            self.assertIn(NO_STATE, out)
            self.assertNotIn("Planner session", out)
            self.assertNotIn("stop-migrate", out)
            for w in why:
                self.assertIn(w, out)
        self.assertFalse(fx.state_path.exists())

    def test_CONTROL_the_fresh_tree_itself_is_planned(self):
        code, out = self.fresh().decision()
        self.assertEqual(code, 0, out)
        self.assertIn("=== Planner session ===", out)

    def test_a_tree_with_no_installer_record(self):
        fx = self.fresh()
        (fx.audit / ".provenance.json").unlink()
        self.assertRefused(fx, "install.sh did not create this tree")

    def test_an_unreadable_installer_record(self):
        fx = self.fresh()
        fx.write(".provenance.json", "not json\n")
        self.assertRefused(fx, "install.sh did not create this tree")

    def test_a_complete_plan_without_an_installer_record_is_not_offered_the_gate(self):
        """The case `tests/test_consumer_paths.py` is about: a whole Markdown plan, read
        as nothing. Offering it the migrate gate would be reading it as a plan."""
        fx = self.fresh()
        fx.write("AUDIT.md", audit_md())
        (fx.audit / ".provenance.json").unlink()
        self.assertRefused(fx, "xcheck migrate")

    def test_a_tree_holding_a_finding(self):
        fx = self.fresh()
        fx.write("findings/F-0001-a.md", finding())
        self.assertRefused(fx, "already holds audit records", "findings/")

    def test_a_tree_holding_a_ledger_row(self):
        fx = self.fresh()
        fx.write("LEDGER.md", ledger([("F-0001", "a finding", "major", "reported")]))
        self.assertRefused(fx, "LEDGER.md row")

    def test_a_tree_whose_state_document_was_lost(self):
        """A real transition, then the document deleted. The stream remembers what the
        tree forgot, and re-planning over it would orphan that history."""
        fx = self.fresh()
        fx.write("AUDIT.md", audit_md())
        for args in (("migrate",), ("set-limit", "reopen_limit", "3")):
            code, out = fx.run(*args)
            self.assertEqual(code, 0, out)
        fx.state_path.unlink()
        self.assertRefused(fx, "state transition", "Restore audit/state.json")

    def test_the_construal_gate_refuses_rather_than_waving_the_planner_through(self):
        """§4 rule 9 admits a construal into `state.json`, which does not exist yet. The
        gate cannot be honoured, so it refuses; silently skipping an armed gate would be
        the fail-open answer."""
        fx = self.fresh(conf="construal_gate=on\n")
        self.assertRefused(fx, "construal_gate")


class APlannerSessionRunsBeforeThereIsState(unittest.TestCase):
    """A real child through `xcheck next`. The stand-in agent writes the plan inside its
    sandbox, and the plan has to come back through the courier with no state document
    anywhere on the path."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xcheck-fresh-agent-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.record = self.tmp / "record.json"
        self.spec = self.tmp / "spec.json"

    def project(self, conf="", **behaviour):
        self.spec.write_text(json.dumps(dict({"record": str(self.record)}, **behaviour)),
                             encoding="utf-8")
        fx = FreshInstall(git=True, conf=(
            f"planner_cmd={AGENT} --spec {self.spec} {{prompt}}\n"
            f"sandbox_profile=worktree\nsession_timeout=60\n{conf}"))
        self.addCleanup(fx.cleanup)
        return fx

    def runs(self):
        if not self.record.exists():
            return []
        return json.loads(self.record.read_text(encoding="utf-8"))

    def test_the_plan_lands_and_the_next_decision_is_the_gate(self):
        plan = audit_md()
        fx = self.project(patch={"path": "audit/AUDIT.md", "text": plan})
        code, out = fx.run_subprocess("next")
        print(f"\nnext:\n{out.strip()[-600:]}")
        self.assertEqual(code, 0, out)
        runs = self.runs()
        self.assertEqual(len(runs), 1, "the Planner was not dispatched exactly once")
        self.assertIn(PLANNER_CHARTER, " ".join(runs[0]["argv"]))
        self.assertEqual((fx.audit / "AUDIT.md").read_text(encoding="utf-8"), plan,
                         "the plan the session wrote did not reach the project")
        self.assertFalse(fx.state_path.exists(), "a session created the state document")
        self.assertIn("its work is audit/AUDIT.md", out)
        self.assertNotIn("Traceback", out)

        events = [json.loads(ln)["event"] for ln in
                  (fx.audit / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        for kind in ("session_dispatched", "session_finished", "session_receipt"):
            self.assertIn(kind, events, f"the session left no `{kind}` in the stream")

        code, out = fx.run("status")
        self.assertEqual(code, 0, out)
        self.assertIn("decision: stop-migrate -> ['P-01']", out)

    def test_a_failed_session_stops_without_a_traceback(self):
        """The retry gate read the last session out of `state.sessions[]`, so a failed
        Planner with no state document died on a `StateError` instead of stopping."""
        fx = self.project(patch={"path": "audit/AUDIT.md", "text": "half a plan\n"},
                          exit=1)
        template = (fx.audit / "AUDIT.md").read_text(encoding="utf-8")
        code, out = fx.run_subprocess("next")
        print(f"\nnext (failing agent):\n{out.strip()[-500:]}")
        self.assertEqual(code, 1, out)
        self.assertIn("stop: Planner session returned exit", out)
        self.assertNotIn("Traceback", out)
        self.assertEqual((fx.audit / "AUDIT.md").read_text(encoding="utf-8"), template,
                         "a crashed session's half-plan was applied to the project")
        code, out = fx.run("status")
        self.assertEqual(code, 0, out)
        self.assertIn("quarantine: 1 pending bundle(s)", out)
        self.assertIn("decision: run-planner", out)

    def test_the_charter_repeat_ceiling_holds_before_migration(self):
        """Budgets are evaluated on every dispatch decision, this one included: the
        ceiling counts dispatches in the event stream, which exists before state."""
        fx = self.project(conf="budgets=on\ncharter_repeat_limit=1\n", exit=1)
        code, out = fx.run_subprocess("next")
        self.assertEqual(code, 1, out)
        code, out = fx.decision()
        print(f"\nnext after one barren Planner:\n{out.strip()[-300:]}")
        self.assertEqual(code, 0, out)
        self.assertIn("stop-charter-repeats", out)
        self.assertEqual(len(self.runs()), 1, "the ceiling let a second Planner run")


if __name__ == "__main__":
    unittest.main()
