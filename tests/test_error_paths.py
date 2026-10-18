"""0.9.6 phase 11: the new write paths fail with an ADDRESS, and an old corpus still loads.

Three writes were added this release — a dispatch whose stop is recorded, `resolve-stop`
and `run-oracle`'s `oracle_run` event. Each is driven through the real CLI with audit/
read-only (chmod in a fixture) and with ENOSPC injected under the append, and the test
reads back what the operator would see: a message naming what was not written, a non-zero
exit, no `Traceback`, and the state document's bytes unchanged. Where the refusal prints
a recovery command, the test runs it as printed.

`AnOldCorpus` is the upgrade drill for the fields 0.9.6 added: a 0.9.5-shaped session
record (no `subject_manifest_scheme`, `telemetry_source`, `charter_targets`, capsule or
stop fields) and a stream with no `oracle_run`/`stop_resolved` events are read by every
reading verb and survive a dispatch with their bytes and every finding body unchanged.
"""

import errno
import hashlib
import json
import os
import shlex
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.harness import Fixture, finding_record, queue_pass, state_doc, xcheck_submodule
from tests.test_container_profile import DOCKER_OK, SKIP_REASON
from tests.test_oracle_acceptance import FIXED, ProjectFixture
from tests.test_stop_channel import Run, stop

ledger = xcheck_submodule("ledger")


def read_only(test, audit):
    """chmod audit/ read-only for the test, restored before the fixture's own cleanup."""
    paths = [Path(d) for d, _, _ in os.walk(audit)]
    files = [Path(d) / f for d, _, fs in os.walk(audit) for f in fs]
    for p in files:
        p.chmod(0o444)
    for p in paths:
        p.chmod(0o555)

    def restore():
        for p in paths:
            p.chmod(0o755)
        for p in files:
            p.chmod(0o644)
    test.addCleanup(restore)


def enospc(*a, **kw):
    raise OSError(errno.ENOSPC, "No space left on device")


def printed_command(out, prefix):
    """The backticked command beginning with `prefix` in a refusal — run verbatim."""
    start = out.index("`" + prefix)
    return out[start + 1:out.index("`", start + 1)]


def stopped(test):
    r = Run(test, {"stops": {"P-01": stop(target="P-01")}})
    r.cli("next", "--yes")
    return r


class AReadOnlyAuditDirectory(unittest.TestCase):

    def assert_addressed(self, rc, out, *needles):
        self.assertNotEqual(0, rc, out[-600:])
        self.assertNotIn("Traceback", out)
        for n in needles:
            self.assertIn(n, out)

    def test_dispatch_is_refused_before_anything_runs(self):
        r = Run(self, {})
        before = (r.f.audit / "state.json").read_bytes()
        read_only(self, r.f.audit)
        rc, out = r.cli("next", "--yes")
        self.assert_addressed(rc, out, "needs write access to audit/", "nothing was changed")
        self.assertEqual(before, (r.f.audit / "state.json").read_bytes())

    def test_resolve_stop_is_refused_and_the_stop_stands(self):
        r = stopped(self)
        read_only(self, r.f.audit)
        rc, out = r.cli("resolve-stop", "P-01", "--resolution", "material supplied")
        self.assert_addressed(rc, out, "needs write access to audit/")
        self.assertEqual([], r.events("stop_resolved"))


class AFullDisk(unittest.TestCase):

    def test_resolve_stop_names_the_outbox_and_its_printed_recovery_lifts_the_stop(self):
        r = stopped(self)
        with mock.patch.object(ledger, "append_chained", side_effect=enospc):
            rc, out = r.f.run("resolve-stop", "P-01", "--resolution", "material supplied")
        self.assertNotEqual(0, rc)
        self.assertNotIn("Traceback", out)
        self.assertIn("No space left on device", out)
        self.assertIn("NOT recorded in the stream", out)
        self.assertEqual([], r.events("stop_resolved"), "nothing reached the stream")
        # The stop still gates the next dispatch, so no write follows and nothing replays
        # the outbox on its own: the message has to name the command that does.
        rc, gated = r.cli("next", "--yes")
        self.assertIn("refusal on P-01", gated)
        self.assertEqual([], r.events("stop_resolved"))
        cmd = printed_command(out, "xcheck recover")
        rc, rec = r.cli(*shlex.split(cmd)[1:])
        self.assertEqual(0, rc, rec)
        [res] = r.events("stop_resolved")
        self.assertEqual("material supplied", res["resolution"])
        rc, out = r.cli("next", "--dry-run")
        self.assertNotIn("refusal on P-01", out)

    def test_resolve_stop_with_the_outbox_full_too_says_the_event_is_lost(self):
        r = stopped(self)
        with mock.patch.object(ledger, "append_chained", side_effect=enospc), \
                mock.patch.object(ledger, "_append", side_effect=enospc):
            rc, out = r.f.run("resolve-stop", "P-01", "--resolution", "x")
        self.assertNotEqual(0, rc)
        self.assertNotIn("Traceback", out)
        self.assertIn("This event is LOST", out)
        rc, gated = r.cli("next", "--dry-run")
        self.assertIn("refusal on P-01", gated, "a lost resolution must not lift the stop")
        self.assertIn("run this command again", out)
        rc, out = r.cli("resolve-stop", "P-01", "--resolution", "x")
        self.assertEqual(0, rc, out)
        self.assertEqual(1, len(r.events("stop_resolved")))


@unittest.skipUnless(DOCKER_OK, SKIP_REASON)
class TheOracleRecord(unittest.TestCase):

    def test_a_full_disk_says_the_result_was_not_recorded(self):
        p = ProjectFixture(self)
        (p.f.root / "src" / "auth.py").write_text(FIXED)
        with mock.patch.object(ledger, "append_chained", side_effect=enospc), \
                mock.patch.object(ledger, "_append", side_effect=enospc):
            rc, out = p.cli("run-oracle", "F-0001")
        self.assertNotEqual(0, rc)
        self.assertNotIn("Traceback", out)
        self.assertIn("result for F-0001 was NOT recorded", out)
        self.assertIn("This event is LOST", out)
        self.assertEqual([], p.runs())
        rc, out = p.cli(*shlex.split(printed_command(out, "xcheck run-oracle"))[1:])
        self.assertEqual(0, rc, out)
        self.assertEqual(["defect-absent"], [e["verdict"] for e in p.runs()])

    def test_a_read_only_audit_dir_says_the_same(self):
        p = ProjectFixture(self)
        read_only(self, p.f.audit)
        rc, out = p.cli("run-oracle", "F-0001")
        self.assertNotEqual(0, rc)
        self.assertNotIn("Traceback", out)
        self.assertIn("NOT recorded", out)


# A 0.9.5 record as the live corpus holds it (`audit/state.json`, the last session), with
# the host-specific values replaced. Nothing 0.9.6 writes is on it.
OLD_SESSION = {
    "agent_model": "unknown", "charter_hash": "7" * 64, "duration_s": 3600.015,
    "executable": "codex", "executable_version": "codex-cli 0.144.6", "exit_status": -15,
    "head_after": "e" * 40, "head_before": "e" * 40, "log_digest": "3" * 64,
    "outcome": "timeout", "prompt_hash": "8" * 64, "provider": "openai", "role": "Auditor",
    "sandbox_details": {"isolating": True, "material_writable": False, "workdir": None},
    "sandbox_profile": "readonly", "session_id": "0ead1aa83a2b5b12",
    "started": "2026-09-01T17:52:15+00:00", "state_revision": 1, "tokens": None}


class AnOldCorpus(unittest.TestCase):

    def test_reads_and_a_dispatch_leave_old_records_as_they_were(self):
        tmp = Path(tempfile.mkdtemp(prefix="xc-old-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "child.py").write_text("print('child ran')\n")
        doc = state_doc(findings=[finding_record(), finding_record("F-0002")],
                        queue=[queue_pass("P-01"), queue_pass("P-02")],
                        sessions=[dict(OLD_SESSION)])
        f = Fixture(doc, conf=(f"auditor_cmd={sys.executable} {tmp / 'child.py'} {{prompt}}\n"
                               f"parallel_passes=off\nconstrual_gate=off\nembargo=off\n"
                               f"log_root={tmp}/logs\n"))
        self.addCleanup(f.cleanup)
        f.git_init(extra=("README.md", "src/core.py"))
        bodies = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in (f.audit / "findings").glob("*.md")}
        for argv in (("lint",), ("status",), ("metrics",), ("next", "--dry-run"),
                     ("next", "--yes")):
            rc, out = f.run_subprocess(*argv)
            self.assertNotIn("Traceback", out, argv)
            self.assertEqual(0, rc, f"{argv}: {out[-600:]}")
        after = json.loads((f.audit / "state.json").read_text())
        self.assertEqual(2, len(after["sessions"]), "the dispatch did not land")
        self.assertEqual(OLD_SESSION, after["sessions"][0], "an admitted record was rewritten")
        self.assertEqual(bodies, {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in (f.audit / "findings").glob("*.md")})


if __name__ == "__main__":
    unittest.main()
