"""The no-progress detector counts canonical state, not files. F-0117, F-0118.

`audit_state_digest` used to hash every file under `audit/` with a growing exclusion
list — orchestrator-logs/, .lock/, events.jsonl. An inclusion rule with holes patched
into it, and F-0117 is the hole nobody had patched: an unrelated `audit/progress.log`
changed the digest and read as progress. A detector that counts a stray file as work is a
detector that never fires.

Both arms are asserted here, because either alone is worthless: an irrelevant file must
NOT move the digest, and a real state change MUST.
"""

import json
import unittest
from pathlib import Path

from tests.harness import Fixture, finding_record, queue_pass, state_doc, xcheck_submodule

runner = xcheck_submodule("runner")
state_mod = xcheck_submodule("state")


class TheDigestCountsCanonicalStateAndNothingElse(unittest.TestCase):

    def setUp(self):
        self.fx = Fixture(state_doc(
            findings=[finding_record("F-0001", "reported")],
            queue=[queue_pass("P-01", done=False)]))
        self.addCleanup(self.fx.cleanup)
        self.before = runner.audit_state_digest(self.fx.root)

    def digest(self):
        return runner.audit_state_digest(self.fx.root)

    def write(self, rel, text):
        p = self.fx.audit / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def edit_state(self, mutate):
        doc = json.loads((self.fx.audit / "state.json").read_text(encoding="utf-8"))
        mutate(doc)
        (self.fx.audit / "state.json").write_text(
            json.dumps(doc, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    def test_f0117_an_irrelevant_file_under_audit_is_not_progress(self):
        """The audit's own example, by name."""
        self.write("progress.log", "session 3: thinking about it\n")
        after = self.digest()
        print(f"\n  F-0117 ARM 1  audit/progress.log written -> digest changed: "
              f"{after != self.before}")
        self.assertEqual(self.before, after,
                         "an unrelated file under audit/ counted as progress")

    def test_f0117_arm_two_a_real_state_change_still_is_progress(self):
        """The control. Without it, "the file did not move the digest" is satisfied by a
        digest that never moves at all."""
        self.edit_state(lambda d: d["findings"][0].__setitem__("status", "accepted"))
        after = self.digest()
        print(f"  F-0117 ARM 2  F-0001 reported -> accepted -> digest changed: "
              f"{after != self.before}")
        self.assertNotEqual(self.before, after,
                            "a canonical status change did not register as progress — "
                            "the detector would stop the loop on real work")

    def test_the_orchestrators_own_bookkeeping_is_not_progress(self):
        """Every dispatch writes `sessions[]` and moves `state_revision`. Counting them
        would make an agent that did nothing look productive forever — the detector
        reporting on its own footprints."""
        def add_session(doc):
            doc["state_revision"] = doc["state_revision"] + 1
            doc["sessions"] = [{
                "session_id": "a" * 16, "role": "Auditor", "provider": "p",
                "agent_model": "m", "executable": "x", "executable_version": "v",
                "charter_hash": "0" * 64, "prompt_hash": "0" * 64,
                "state_revision": 1, "head_before": "unknown",
                "sandbox_profile": "readonly", "started": "2026-09-03"}]
        self.edit_state(add_session)
        self.assertEqual(self.before, self.digest(),
                         "the orchestrator's own dispatch record read as progress")

    def test_a_log_a_view_and_a_lock_are_not_progress(self):
        """The three the old rule excluded by hand, still excluded — now because they are
        not canonical state rather than because someone listed them."""
        for rel, text in (("orchestrator-logs/20260903-auditor.log", "tokens used\n99\n"),
                          ("LEDGER.md", "| id | title |\n|---|---|\n"),
                          ("events.jsonl", '{"event":"session_dispatched"}\n')):
            with self.subTest(path=rel):
                self.write(rel, text)
                self.assertEqual(self.before, self.digest(), f"{rel} read as progress")

    def test_an_evidence_body_is_not_machine_progress_and_that_is_the_design(self):
        """A rewritten finding body is evidence, and evidence no verb recorded moved no
        machine state (§2 rule 1). Stated as a test so the choice is visible rather than
        implied by an absence."""
        body = self.fx.audit / self.fx.doc["findings"][0]["body_path"]
        body.write_text(body.read_text(encoding="utf-8") + "\n## More thoughts\n",
                        encoding="utf-8")
        self.assertEqual(self.before, self.digest())

    def test_an_unreadable_state_document_does_not_collapse_to_the_empty_digest(self):
        """Broken canonical state is a different problem — `load_state` reports it — but
        it must not read as "identical to the last session", which would stop the loop
        for the wrong reason."""
        (self.fx.audit / "state.json").write_text("{ not json", encoding="utf-8")
        broken = self.digest()
        self.assertNotEqual(self.before, broken)
        empty = Path(self.fx.root, "no-audit-here")
        empty.mkdir()
        self.assertNotEqual(broken, runner.audit_state_digest(empty))


class TheLoopStopsOnRepeatedNoProgress(unittest.TestCase):
    """F-0118, unchanged by the narrowing: the detector still fires on a real no-op
    streak. Driven through `cmd_loop` with a stub session so the wiring is exercised,
    not just the digest."""

    def test_two_sessions_that_change_nothing_stop_the_loop(self):
        import contextlib
        import io
        cli = xcheck_submodule("cli")
        fx = Fixture(state_doc(findings=[finding_record("F-0001", "reported")],
                               queue=[queue_pass("P-01", done=False)]))
        self.addCleanup(fx.cleanup)
        calls = []
        saved = cli.cmd_next
        cli.cmd_next = lambda *a, **k: calls.append(1) or "run-auditor"
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), fx.profile_env():
                cli.cmd_loop(fx.root, {"loop_progress_limit": "2", "session_timeout": "30"},
                             False, False, 5)
            out = buf.getvalue()
        finally:
            cli.cmd_next = saved
        print(f"\n  F-0118  {len(calls)} session(s) then: {out.strip().splitlines()[-1][:90]}")
        self.assertEqual(2, len(calls), "the no-progress detector did not stop the loop")
        self.assertIn("no progress", out)


if __name__ == "__main__":
    unittest.main()
