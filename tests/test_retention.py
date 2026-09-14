"""Artifact retention. The audit measured the growth: `audit/` is 104 MB, of which
`audit/orchestrator-logs/` is 102 MB — 159 session logs at roughly 900 KB each, one more
per dispatch, forever.

They were already gitignored, so `.git` never carried them. What pays is everything that
walks the working tree: `git status`, `ls-files --others`, worktree creation, backups,
every corpus scan. Three separate decisions — where new logs go, what stays in the
repository, and when anything is deleted — and only the third involves losing bytes.

History rewriting is OUT of scope and stays the operator's call: it is destructive and it
invalidates every digest already recorded.
"""

import collections
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.harness import needs_live_corpus, Fixture, finding_record, state_doc, xcheck_submodule

cli = xcheck_submodule("cli")
envelope = xcheck_submodule("envelope")
policy = xcheck_submodule("policy")
retention = xcheck_submodule("retention")
util = xcheck_submodule("util")

REPO = Path(__file__).resolve().parent.parent


def clean_env(**over):
    saved = {k: os.environ.get(k)
             for k in (retention.LOG_DIR_ENV, retention.XDG_STATE_ENV)}
    for k in saved:
        os.environ.pop(k, None)
    os.environ.update({k: v for k, v in over.items() if v is not None})
    return saved


def restore(saved):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


class RawLogsLeaveTheWorkingTree(unittest.TestCase):

    def setUp(self):
        self.fx = Fixture(state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, self.fx.root, True)
        self.saved = clean_env()
        self.addCleanup(restore, self.saved)

    def test_the_default_root_is_outside_the_project(self):
        root = retention.log_root(self.fx.root)
        self.addCleanup(shutil.rmtree, root, True)
        print(f"\n  ROOT       {retention.describe_log_root(self.fx.root)}")
        self.assertFalse(str(root).startswith(str(Path(self.fx.root).resolve())),
                         "session logs still land inside the audited working tree")

    def test_the_search_order_is_stated_and_never_derives_a_home(self):
        """The same rule as the operator profile: a tool that resolves `~` has decided
        something about the machine it was not told."""
        src = (REPO / "xcheck" / "retention.py").read_text(encoding="utf-8")
        # Split needles: a detector written as one literal matches its own source, and
        # the fix is never to exclude the file — it is to make the needle unwritable by
        # accident. Same lesson as the self-scanning detector in test_no_bypass.
        for a, b in (("expand", "user"), ("Path.", "home()"), ("environ[\"", "HOME\"]")):
            self.assertNotIn(a + b, src, f"{a + b} resolves the operator's home")
        order = retention.log_search_order(self.fx.root)
        print("  SEARCH ORDER")
        for source, path in order:
            print(f"    {source:<45} {path}")
        self.assertEqual(4, len(order))
        self.assertIsNotNone(order[-1][1], "the search order can answer nothing")

    def test_an_explicit_log_dir_wins(self):
        with tempfile.TemporaryDirectory() as td:
            root = retention.log_root(self.fx.root, {"log_dir": td})
            self.assertEqual(Path(td), root)

    def test_the_env_var_beats_xdg(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            saved = clean_env(**{retention.LOG_DIR_ENV: a, retention.XDG_STATE_ENV: b})
            try:
                self.assertEqual(Path(a), retention.log_root(self.fx.root))
            finally:
                restore(saved)

    def test_it_is_a_policy_key_so_the_subject_cannot_redirect_its_own_logs(self):
        for key in ("log_dir", "log_retention_days"):
            self.assertEqual("policy", policy.classify(key), key)
        (self.fx.audit / "orchestrator.conf").write_text("log_dir=/tmp/anywhere\n",
                                                         encoding="utf-8")
        with self.assertRaises(SystemExit) as e:
            util.load_conf(self.fx.audit)
        self.assertIn("log_dir", str(e.exception))


class TheManifestStaysAndTheBytesDoNot(unittest.TestCase):

    def setUp(self):
        self.fx = Fixture(state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, self.fx.root, True)
        self.saved = clean_env()
        self.addCleanup(restore, self.saved)
        self.root = Path(tempfile.mkdtemp(prefix="xcheck-logs-test-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def write_log(self, name="20260903-120000-auditor.log", text="tokens used\n99\n"):
        p = self.root / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_a_row_carries_the_name_size_and_checksum(self):
        p = self.write_log()
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        row = retention.record(self.fx.root, "a" * 16, p, sha256=digest, role="Auditor")
        self.assertEqual(digest, row["sha256"])
        self.assertEqual(p.stat().st_size, row["bytes"])
        stored = json.loads(retention.manifest_path(self.fx.root)
                            .read_text(encoding="utf-8").splitlines()[0])
        print(f"\n  MANIFEST   {json.dumps(stored, sort_keys=True)[:150]}")
        self.assertEqual(digest, stored["sha256"])

    def test_the_manifest_lives_in_the_repository_and_the_log_does_not(self):
        p = self.write_log()
        retention.record(self.fx.root, "a" * 16, p)
        m = retention.manifest_path(self.fx.root)
        self.assertTrue(str(m).startswith(str(Path(self.fx.root))))
        self.assertFalse(str(p).startswith(str(Path(self.fx.root))))

    def test_the_log_digest_join_is_unaffected(self):
        """The frozen Ouroboros-4 table is keyed by `log_digest`, which comes from the
        session's own event and not from a path. Moving the file cannot break it."""
        table = json.loads((REPO / "tests" / "fixtures" / "ouroboros-4-tokens.json")
                           .read_text(encoding="utf-8"))
        by_digest = table["tokens_by_log_digest"]
        p = self.write_log(text="tokens used\n1 234\n")
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        retention.record(self.fx.root, "b" * 16, p, sha256=digest)
        envelope.emit(self.fx.root, "session_finished", "b" * 16, log_digest=digest,
                      tokens=envelope.log_tokens(p))
        joined = [e for e in envelope.read_events(self.fx.root)
                  if e.get("log_digest") == digest]
        print(f"  JOIN       frozen table has {len(by_digest)} digests; a log written "
              f"OUTSIDE the tree joins by digest: {len(joined) == 1}")
        self.assertEqual(1, len(joined))
        self.assertEqual(1234, joined[0]["tokens"])
        self.assertTrue(all(len(k) == 64 for k in by_digest),
                        "the frozen table is not keyed by digest any more")
        # PHASE 5 pins the COUNT as well as the shape. This phase rewrote the manifest
        # reader, and the frozen join deliberately does not use the manifest — it joins on
        # `log_digest` from the event stream. Asserting the number is how "the join is
        # untouched" stops being a claim about code nobody counted.
        self.assertEqual(151, len(by_digest),
                         "the frozen Ouroboros-4 table changed size; it is evidence, not "
                         "a fixture to regenerate")


class PruningIsAVerbNeverAStepInARun(unittest.TestCase):

    def setUp(self):
        self.fx = Fixture(state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, self.fx.root, True)
        self.root = Path(tempfile.mkdtemp(prefix="xcheck-logs-test-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.old = self.root / "old.log"
        self.old.write_text("x" * 4096, encoding="utf-8")
        row = retention.record(self.fx.root, "old" + "0" * 13, self.old,
                               sha256="a" * 64)
        # backdate it by rewriting the one row: the fixture is building a past, not
        # editing evidence.
        m = retention.manifest_path(self.fx.root)
        row["at"] = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(
            timespec="seconds")
        m.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")
        self.new = self.root / "new.log"
        self.new.write_text("y" * 100, encoding="utf-8")
        retention.record(self.fx.root, "new" + "0" * 13, self.new, sha256="b" * 64)

    def test_the_default_is_a_dry_run_that_deletes_nothing(self):
        report, freed = retention.prune(self.fx.root, 30)
        print(f"\n  DRY RUN    {report.splitlines()[0]}")
        self.assertTrue(self.old.is_file(), "a dry run deleted a log")
        self.assertEqual(0, freed)
        self.assertIn("dry run", report)

    def test_apply_deletes_only_what_is_past_the_retention(self):
        report, freed = retention.prune(self.fx.root, 30, apply=True)
        print(f"  APPLIED    freed {freed:,} bytes; old kept={self.old.is_file()}, "
              f"new kept={self.new.is_file()}")
        self.assertFalse(self.old.is_file())
        self.assertTrue(self.new.is_file(), "a log inside the retention window was deleted")
        self.assertEqual(4096, freed)

    def test_a_pruned_log_reads_as_pruned_not_missing_and_not_empty(self):
        retention.prune(self.fx.root, 30, apply=True)
        state, row = retention.find(self.fx.root, "old" + "0" * 13)
        said = retention.describe_log(self.fx.root, "old" + "0" * 13)
        print(f"  READER     {said[:150]}")
        self.assertEqual("pruned", state)
        self.assertEqual(4096, row["bytes"])
        self.assertIn("PRUNED", said)
        self.assertNotIn("missing", said.lower())

    def test_a_log_nobody_recorded_is_a_third_answer(self):
        self.assertEqual(("unrecorded", None), retention.find(self.fx.root, "z" * 16))

    def test_the_retention_default_is_documented_and_finite(self):
        self.assertEqual("30", util.CONF_DEFAULTS["log_retention_days"])
        self.assertIn("log_retention_days", policy.OPERATOR_PROFILE_TEMPLATE)
        self.assertIn("prune", policy.OPERATOR_PROFILE_TEMPLATE)

    def test_nothing_in_a_run_calls_prune(self):
        """A retention policy that fired during a session would make the audit's own
        record depend on when someone last ran the tool."""
        for mod in ("runner.py", "parallel.py", "decision.py", "write.py"):
            src = (REPO / "xcheck" / mod).read_text(encoding="utf-8")
            self.assertNotIn("retention.prune", src, f"{mod} prunes during a run")


class TheArchiveIsNeverWritten(unittest.TestCase):
    """`audit-archive/` is frozen evidence: read and copy, never write. A retention path
    that resolved into it would delete the only copy of a finished run while reporting it
    as routine housekeeping."""

    def test_a_log_root_inside_the_archive_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "audit-archive" / "logs"
            bad.mkdir(parents=True)
            with self.assertRaises(SystemExit) as e:
                retention.refuse_frozen(bad)
            print(f"\n  ARCHIVE    refused: {str(e.exception)[:110]}")
            self.assertIn("frozen", str(e.exception))

    def test_the_resolved_root_for_this_repository_is_not_in_the_archive(self):
        root = retention.log_root(REPO, create=False)
        self.assertNotIn("audit-archive", str(root))

    def test_the_archive_is_untouched_by_a_prune(self):
        fx = Fixture(state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, fx.root, True)
        archive = Path(fx.root) / "audit-archive" / "ouroboros-9"
        archive.mkdir(parents=True)
        keep = archive / "session.log"
        keep.write_text("frozen evidence\n", encoding="utf-8")
        before = keep.read_bytes()
        retention.record(fx.root, "c" * 16, keep, sha256="c" * 64)
        m = retention.manifest_path(fx.root)
        row = json.loads(m.read_text(encoding="utf-8").splitlines()[0])
        row["at"] = (datetime.now(timezone.utc) - timedelta(days=999)).isoformat(
            timespec="seconds")
        m.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            retention.prune(fx.root, 30, apply=True)
        print(f"  ARCHIVE    a prune that reached audit-archive/ refused; file intact: "
              f"{keep.read_bytes() == before}")
        self.assertEqual(before, keep.read_bytes())


@needs_live_corpus
class TheExistingTreeIsMeasuredAndMigratedByAVerb(unittest.TestCase):

    # The bytes this test used to measure. Phase 21 ran the migration for real, so the
    # figures are pinned here rather than recomputed: they are what was moved, and a
    # measurement of an emptied directory cannot re-derive them.
    MIGRATED_FILES = 304
    MIGRATED_BYTES = 106_664_401

    def test_the_legacy_directory_is_empty_and_the_manifest_accounts_for_it(self):
        """This test used to measure 106.7 MB sitting in the working tree and assert the
        measurement was non-zero. Phase 21 moved those bytes, so `total > 0` became a
        test that fails BECAUSE the work was done.

        The subject moved, so the check moves with it. What mattered was never "the logs
        are large" — it was "the tool can account for every one of them". That is now
        checkable in a stronger form: the tree is empty AND the manifest resolves every
        migrated log as `present` with a matching digest.
        """
        legacy = retention.legacy_logs(REPO)
        audit_bytes = sum(p.stat().st_size for p in (REPO / "audit").rglob("*")
                          if p.is_file())
        rows = [r for r in retention.manifest_rows(REPO)
                if r.get("kind") == "log" and str(r.get("session_id", "")).startswith(
                    "legacy:")]
        sessions = sorted({r["session_id"] for r in rows})
        print(f"\n  MIGRATED   {self.MIGRATED_FILES} file(s), "
              f"{self.MIGRATED_BYTES:,} bytes moved out of the working tree "
              f"(phase 21, `xcheck prune-logs --migrate --apply`)")
        print(f"             audit/ is now {audit_bytes:,} bytes "
              f"({audit_bytes / 1e6:.1f} MB); "
              f"audit/{retention.LEGACY_LOG_DIRNAME}/ holds {len(legacy)} file(s)")
        self.assertEqual([], legacy,
                         "legacy logs are back in the working tree — either the "
                         "migration was undone or a session wrote in-tree again")
        self.assertEqual(self.MIGRATED_FILES, len(sessions),
                         f"the manifest accounts for {len(sessions)} migrated logs, not "
                         f"{self.MIGRATED_FILES} — bytes that moved without a row are "
                         f"bytes nobody can find again")
        for r in rows:
            self.assertRegex(r.get("sha256", ""), r"^[0-9a-f]{64}$",
                             f"{r.get('session_id')} has no usable digest")
            self.assertGreater(r.get("bytes", 0), 0)

        # Resolution is reported, and only TWO states are acceptable. `present` is this
        # machine; `missing` is any other machine, because the manifest is tracked in git
        # and the 106.7 MB deliberately is not — a checkout elsewhere has the record and
        # not the bytes, which is the honest answer and exactly what `missing` means.
        #
        # What must never appear is `corrupt` (the bytes are there and wrong) or
        # `unrecorded` (a log nobody wrote a row for). Pinning `present` here would make
        # this test pass only on the laptop that ran the migration.
        states = {s: retention.find(REPO, s)[0] for s in sessions}
        tally = collections.Counter(states.values())
        print("             resolution: " +
              ", ".join(f"{v} {k}" for k, v in tally.most_common()))
        wrong = {s: st for s, st in states.items()
                 if st not in (retention.PRESENT, retention.MISSING)}
        self.assertEqual({}, wrong,
                         f"a migrated log resolved as something other than present or "
                         f"missing — `corrupt` means the bytes changed, `unrecorded` "
                         f"means the row is gone: {list(wrong.items())[:5]}")
        self.assertEqual(len(sessions), sum(tally.values()))

    def test_migrate_moves_the_bytes_and_records_every_one(self):
        """A real before/after, on a fixture, because moving THIS repository's 100 MB is
        the operator's decision and not a test's."""
        fx = Fixture(state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, fx.root, True)
        legacy = Path(fx.root) / "audit" / retention.LEGACY_LOG_DIRNAME
        legacy.mkdir(parents=True)
        for i in range(3):
            (legacy / f"2026090{i}-auditor.log").write_text("z" * 1000, encoding="utf-8")
        root = Path(tempfile.mkdtemp(prefix="xcheck-logs-test-"))
        self.addCleanup(shutil.rmtree, root, True)
        conf = {"log_dir": str(root)}

        before = sum(p.stat().st_size for p in (Path(fx.root) / "audit").rglob("*")
                     if p.is_file())
        report, moved = retention.migrate_logs(fx.root, conf, apply=False)
        self.assertEqual(3, len(list(legacy.iterdir())), "a dry run moved files")

        report, moved = retention.migrate_logs(fx.root, conf, apply=True)
        after = sum(p.stat().st_size for p in (Path(fx.root) / "audit").rglob("*")
                    if p.is_file())
        print(f"  MIGRATE    audit/ {before:,} -> {after:,} bytes; moved {moved:,}")
        self.assertEqual(3, len(list(root.iterdir())))
        self.assertEqual(0, len(list(legacy.iterdir())))
        rows = [r for r in retention.manifest_rows(fx.root) if r.get("kind") == "log"]
        # SIX rows for three files, and the pairing is the point (phase 21): each log is
        # recorded at its IN-TREE path with its digest BEFORE anything moves, then again
        # at its destination. "Verified before the move" is a fact in the manifest rather
        # than a sentence in a docstring, and `find` reads the last row so the
        # post-move one wins.
        self.assertEqual(6, len(rows), "the pre-move record is missing — nothing proves "
                                       "the bytes were verified before they were moved")
        for r in rows:
            self.assertRegex(r["sha256"], r"^[0-9a-f]{64}$")
        pre = [r for r in rows if retention.LEGACY_LOG_DIRNAME in r["path"]]
        post = [r for r in rows if retention.LEGACY_LOG_DIRNAME not in r["path"]]
        self.assertEqual(3, len(pre), "no in-tree row: the pre-move verification left "
                                      "no evidence it happened")
        self.assertEqual(3, len(post))
        self.assertEqual({r["sha256"] for r in pre}, {r["sha256"] for r in post},
                         "the digest changed between the pre-move record and the "
                         "destination — the bytes are not the bytes that were verified")
        print(f"  RECORDED   {len(pre)} in-tree row(s) before the move, {len(post)} "
              f"after; same digests both sides")

    def test_the_verb_is_reachable_and_dry_by_default(self):
        import contextlib
        import io
        fx = Fixture(state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, fx.root, True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["xcheck", "--project", str(fx.root), "prune-logs"])
        out = buf.getvalue()
        print(f"  CLI        {out.strip().splitlines()[-1][:110]}")
        self.assertEqual(0, rc)
        self.assertIn("dry run", out)

    def test_the_apply_flag_is_its_own_word_not_a_reuse_of_force(self):
        with self.assertRaises(SystemExit) as e:
            cli.main(["xcheck", "--apply", "lint"])
        self.assertIn("prune-logs", str(e.exception))


if __name__ == "__main__":
    unittest.main()


class FourStatesInsteadOfOneWord(unittest.TestCase):
    """Phase 5, third audit. `find()` had two answers where the evidence has four.

    A recorded log whose file was simply gone returned `pruned`, with no prune receipt
    anywhere; a present file whose bytes had changed returned `present`, because the
    recorded sha256 was never compared to anything. So deletion, corruption, a wrong
    path, a moved machine and a deliberate prune all arrived as one word — and the
    forensic difference between them is the entire reason the manifest exists.

    Four fixtures, four states, and — the part that makes the four arms worth having —
    a proof that the four fixtures are DIFFERENT ON DISK. Four arms that collapse to two
    cover two states and print four lines about it.
    """

    def setUp(self):
        self.fx = Fixture(state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, self.fx.root, True)
        self.root = Path(tempfile.mkdtemp(prefix="xcheck-logs-states-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.ids = {}

    def plant(self, state):
        """Build the on-disk world that MUST produce `state`, and return its session id.

        Every arm records an honest digest first — the digest of the bytes as written —
        so the only thing that differs between arms is what happens afterwards. An arm
        that recorded a wrong digest to begin with would be testing the fixture.
        """
        # A 16-hex id derived from the state NAME. Not `state[0] * 16`: `present` and
        # `pruned` both start with `p`, so that gave the two arms one session id and the
        # "four different worlds" control caught it — which is what it is for.
        sid = hashlib.sha256(state.encode()).hexdigest()[:16]
        p = self.root / f"{state}.log"
        p.write_text(f"session output for the {state} case\n", encoding="utf-8")
        retention.record(self.fx.root, sid, p,
                         sha256=hashlib.sha256(p.read_bytes()).hexdigest())
        if state == retention.PRESENT:
            pass                                   # left exactly as recorded
        elif state == retention.PRUNED:
            with retention.manifest_path(self.fx.root).open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"kind": "pruned", "session_id": sid,
                                     "path": str(p), "pruned_at": "2026-09-03T00:00:00+00:00"},
                                    sort_keys=True) + "\n")
            p.unlink()
        elif state == retention.MISSING:
            p.unlink()                             # gone, and NOTHING recorded deleting it
        elif state == retention.CORRUPT:
            p.write_text("something else entirely\n", encoding="utf-8")
        self.ids[state] = (sid, p)
        return sid

    def test_each_of_the_four_states_is_produced_and_named(self):
        print()
        for state in (retention.PRESENT, retention.PRUNED, retention.MISSING,
                      retention.CORRUPT):
            sid = self.plant(state)
            got, _row = retention.find(self.fx.root, sid)
            said = retention.describe_log(self.fx.root, sid)
            print(f"  {state.upper():<10} {said[:132]}")
            self.assertEqual(state, got, f"the {state} fixture read as {got}")

    def test_the_four_fixtures_are_different_from_each_other(self):
        """CONTROL. Four arms that produce the same world prove one thing four times.
        The distinguishing property is asserted directly, on the bytes and the rows —
        not inferred from the four answers, which is the thing under test."""
        for state in (retention.PRESENT, retention.PRUNED, retention.MISSING,
                      retention.CORRUPT):
            self.plant(state)
        rows = retention.manifest_rows(self.fx.root)
        facts = {}
        for state, (sid, p) in self.ids.items():
            recorded = next(r["sha256"] for r in rows
                            if r.get("session_id") == sid and r.get("kind") == "log")
            facts[state] = (
                p.is_file(),
                any(r.get("session_id") == sid and r.get("kind") == "pruned" for r in rows),
                (hashlib.sha256(p.read_bytes()).hexdigest() == recorded) if p.is_file() else None,
            )
        print("\n  (file on disk, prune receipt, digest matches)")
        for state, f in facts.items():
            print(f"    {state:<9} {f}")
        self.assertEqual((True, False, True), facts[retention.PRESENT])
        self.assertEqual((False, True, None), facts[retention.PRUNED])
        self.assertEqual((False, False, None), facts[retention.MISSING])
        self.assertEqual((True, False, False), facts[retention.CORRUPT])
        self.assertEqual(4, len(set(facts.values())),
                         f"the four fixtures are not four different worlds: {facts}")

    def test_a_present_file_with_the_wrong_bytes_is_no_longer_present(self):
        """The audit's own probe, by name: the digest is compared now."""
        sid = self.plant(retention.CORRUPT)
        state, row = retention.find(self.fx.root, sid)
        self.assertEqual(retention.CORRUPT, state)
        self.assertNotEqual(row["sha256"], row["sha256_now"])
        self.assertIn("no longer evidence", retention.describe_log(self.fx.root, sid))

    def test_a_vanished_log_with_no_receipt_is_missing_not_pruned(self):
        """The other half of the audit's probe. `pruned` is a claim that someone deleted
        it deliberately, and only a receipt can support that claim."""
        sid = self.plant(retention.MISSING)
        state, _row = retention.find(self.fx.root, sid)
        said = retention.describe_log(self.fx.root, sid)
        self.assertEqual(retention.MISSING, state)
        self.assertIn("NOTHING recorded deleting it", said)
        self.assertNotIn("PRUNED", said)

    def test_the_states_are_enumerated_in_one_place(self):
        self.assertEqual(("present", "pruned", "missing", "corrupt", "unrecorded"),
                         retention.LOG_STATES)


class ADamagedManifestFailsClosed(unittest.TestCase):
    """An evidence index that reads as EMPTY when it is damaged is worse than none: every
    query against it answers "nothing was ever recorded", which is the most reassuring
    possible reading of corruption. The audit's probe: a one-line `{not json}` manifest
    returned `[]`."""

    def setUp(self):
        self.fx = Fixture(state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, self.fx.root, True)

    def write_manifest(self, text):
        m = retention.manifest_path(self.fx.root)
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(text, encoding="utf-8")
        return m

    def test_an_unparseable_line_is_refused_and_names_its_line_number(self):
        self.write_manifest('{"kind": "log", "session_id": "a"}\n{not json}\n')
        with self.assertRaises(retention.RetentionError) as cm:
            retention.manifest_rows(self.fx.root)
        said = str(cm.exception)
        print(f"\n  REFUSAL    {said.splitlines()[0][:160]}")
        self.assertIn(":2:", said, "the refusal does not name the offending line")
        self.assertIn("refused rather than skipped", said)

    def test_the_old_behaviour_would_have_returned_an_empty_index(self):
        """CONTROL, as the audit reproduced it: the previous rule — skip what does not
        parse — turns a damaged one-line manifest into a confident 'no logs here'."""
        self.write_manifest("{not json}\n")
        old_style = []
        for line in retention.manifest_path(self.fx.root).read_text(
                encoding="utf-8").splitlines():
            try:
                old_style.append(json.loads(line))
            except ValueError:
                continue
        print(f"  CONTROL    the skipping reader returns {old_style!r}; the shipped one "
              f"raises")
        self.assertEqual([], old_style)
        with self.assertRaises(retention.RetentionError):
            retention.manifest_rows(self.fx.root)

    def test_a_query_against_a_damaged_manifest_answers_corrupt_not_unrecorded(self):
        self.write_manifest("{not json}\n")
        state, row = retention.find(self.fx.root, "a" * 16)
        said = retention.describe_log(self.fx.root, "a" * 16)
        print(f"  QUERY      {said[:150]}")
        self.assertEqual(retention.CORRUPT, state)
        self.assertIn("manifest_error", row)
        self.assertIn("cannot be read", said)

    def test_a_line_that_is_json_but_not_a_row_is_refused_too(self):
        self.write_manifest('["not", "an", "object"]\n')
        with self.assertRaises(retention.RetentionError) as cm:
            retention.manifest_rows(self.fx.root)
        self.assertIn("not a row", str(cm.exception))


class APartialPruneIsReportedAsPartial(unittest.TestCase):
    """`deleted len(victims)` was a count of what was SELECTED. The two numbers differ
    exactly when something went wrong, which is the case where a confident total is a
    lie."""

    def setUp(self):
        self.fx = Fixture(state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, self.fx.root, True)
        self.root = Path(tempfile.mkdtemp(prefix="xcheck-logs-partial-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.logs = {}
        old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(timespec="seconds")
        rows = []
        for name in ("first", "second"):
            p = self.root / f"{name}.log"
            p.write_text("x" * 1024, encoding="utf-8")
            row = retention.record(self.fx.root, (name[0] * 16)[:16], p,
                                   sha256=hashlib.sha256(p.read_bytes()).hexdigest())
            row["at"] = old
            rows.append(row)
            self.logs[name] = p
        retention.manifest_path(self.fx.root).write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")

    def test_one_failed_unlink_is_counted_and_named(self):
        real = Path.unlink

        def unlink(self, *a, **k):                 # the second file refuses to go
            if self.name == "second.log":
                raise OSError(13, "Permission denied")
            return real(self, *a, **k)
        Path.unlink = unlink
        self.addCleanup(setattr, Path, "unlink", real)
        report, freed = retention.prune(self.fx.root, 30, apply=True)
        Path.unlink = real
        print("\n  PARTIAL    " + [l for l in report.splitlines() if "PARTIAL" in l][0][:180])
        self.assertIn("deleted 1 of 2 log(s)", report)
        self.assertIn("second.log", report)
        self.assertEqual(1024, freed, "the freed total counted a file that is still there")
        self.assertTrue(self.logs["second"].is_file())

    def test_the_survivor_keeps_reading_as_present_because_it_has_no_receipt(self):
        """A receipt written for a deletion that did not happen would make the survivor
        read as `pruned` — the exact confusion this phase exists to remove."""
        real = Path.unlink

        def unlink(self, *a, **k):
            if self.name == "second.log":
                raise OSError(13, "Permission denied")
            return real(self, *a, **k)
        Path.unlink = unlink
        self.addCleanup(setattr, Path, "unlink", real)
        retention.prune(self.fx.root, 30, apply=True)
        Path.unlink = real
        self.assertEqual(retention.PRESENT, retention.find(self.fx.root, "s" * 16)[0])
        self.assertEqual(retention.PRUNED, retention.find(self.fx.root, "f" * 16)[0])

    def test_control_with_no_failure_the_count_is_the_whole_batch(self):
        report, freed = retention.prune(self.fx.root, 30, apply=True)
        print(f"  CONTROL    {report.splitlines()[-1][:120]}")
        self.assertIn("deleted 2 log(s)", report)
        self.assertNotIn("PARTIAL", report)
        self.assertEqual(2048, freed)


class ARootMayNotResolveInsideTheSubject(unittest.TestCase):
    """PHASE 4 (fourth audit). Both root resolvers said "always outside the working tree"
    in their own docstrings and checked only that `audit-archive` was absent from the
    path — so `log_dir=<project>/audit/logs` was ACCEPTED. Reproduced below as the first
    arm, because a refusal test that never saw the old behaviour is a refusal test that
    could be passing over a typo in its own fixture.

    Same defect the operator profile had one directory over (`policy` phase 1 of the
    third-audit run), and deliberately the same fix: canonical resolution through one
    shared predicate, `policy.is_inside`."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xcheck-roots-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = self.tmp / "proj"
        (self.project / "audit").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True,
                       capture_output=True, timeout=30)
        self.outside = self.tmp / "outside"
        self.outside.mkdir()

    def refusal(self, fn, **conf):
        with self.assertRaises(SystemExit) as caught:
            fn(self.project, conf)
        return str(caught.exception)

    def test_a_log_root_inside_the_project_is_refused(self):
        text = self.refusal(retention.log_root, log_dir=str(self.project / "audit" / "logs"))
        self.assertIn("resolves INSIDE the repository being audited", text)
        self.assertIn(str(self.project.resolve()), text, "the project root is not named")
        self.assertIn("audit/logs", text, "the offending path is not named")
        self.assertIn("log_dir", text, "the refusal does not say what to set instead")
        print("\n  REFUSED    " + text.splitlines()[0][:150])

    def test_an_evidence_root_inside_the_project_is_refused(self):
        evidence = xcheck_submodule("evidence")
        text = self.refusal(evidence.cache_root,
                            evidence_dir=str(self.project / "audit" / "cache"))
        self.assertIn("resolves INSIDE the repository being audited", text)
        self.assertIn("evidence_dir", text)
        print("  REFUSED    " + text.splitlines()[0][:150])

    def test_a_worktree_of_the_project_is_inside_it_too(self):
        """A session's disposable worktree is the subject as much as the checkout is:
        the courier carries what a session wrote there home."""
        (self.project / "seed").write_text("x", encoding="utf-8")
        for args in (["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t",
                                     "commit", "-qm", "seed"]):
            subprocess.run(["git", "-C", str(self.project)] + args, check=True,
                           capture_output=True, timeout=30)
        tree = self.tmp / "wt"
        subprocess.run(["git", "-C", str(self.project), "worktree", "add", "-q",
                        str(tree)], check=True, capture_output=True, timeout=60)
        self.assertIn(tree.resolve(), retention.subject_roots(self.project))
        text = self.refusal(retention.log_root, log_dir=str(tree / "logs"))
        self.assertIn("a worktree of the project", text,
                      "the refusal did not say WHICH root it landed in")
        print("  WORKTREE   " + text.splitlines()[1].strip()[:150])

    def test_a_symlink_whose_target_is_inside_the_project_is_refused(self):
        link = self.outside / "logs-link"
        link.symlink_to(self.project / "audit")
        text = self.refusal(retention.log_root, log_dir=str(link))
        self.assertIn("reached through a symlink", text,
                      "the refusal did not say the path was a symlink")
        print("  SYMLINK    " + [ln for ln in text.splitlines()
                                 if "symlink" in ln][0].strip()[:150])

    def test_a_symlinked_PARENT_is_refused_the_same_way(self):
        """The arm a final-component check would miss: the leaf is an ordinary
        directory name, and it is the parent that points back inside."""
        parent = self.outside / "parent-link"
        parent.symlink_to(self.project / "audit")
        text = self.refusal(retention.log_root, log_dir=str(parent / "logs"))
        self.assertIn("resolves INSIDE the repository being audited", text)
        print("  VIA PARENT " + text.splitlines()[2].strip()[:150])

    def test_CONTROL_a_sibling_whose_name_starts_with_the_project_path_is_accepted(self):
        """`<project>-notes` beside `<project>`. A `startswith` check calls this inside;
        a path-component comparison calls it what it is. This control is the whole
        difference between the predicate and a substring."""
        sibling = self.tmp / "proj-notes"
        sibling.mkdir()
        self.assertTrue(str(sibling).startswith(str(self.project)),
                        "the control is not testing what it claims to test")
        got = retention.log_root(self.project, {"log_dir": str(sibling)})
        self.assertEqual(sibling.resolve(), got.resolve())
        print(f"  CONTROL    accepted sibling {got}")

    def test_CONTROL_every_legitimate_source_still_resolves(self):
        """The refusal must not have made the feature unusable: each source in the
        search order, exercised, and each one answers."""
        env = self.outside / "env-logs"
        xdg = self.outside / "xdg"
        cases = [("log_dir in the operator profile", {"log_dir": str(self.outside / "conf-logs")}, {}),
                 (f"${retention.LOG_DIR_ENV}", {}, {retention.LOG_DIR_ENV: str(env)}),
                 (f"${retention.XDG_STATE_ENV}/xcheck/logs/<project>", {},
                  {retention.XDG_STATE_ENV: str(xdg)}),
                 ("the system temp directory", {}, {})]
        print("  SEARCH ORDER, each source exercised:")
        for label, conf, environ in cases:
            keep = {k: os.environ.pop(k, None)
                    for k in (retention.LOG_DIR_ENV, retention.XDG_STATE_ENV)}
            os.environ.update(environ)
            try:
                root = retention.log_root(self.project, conf)
                source = retention.describe_log_root(self.project, conf)
            finally:
                for k, v in keep.items():
                    os.environ.pop(k, None)
                    if v is not None:
                        os.environ[k] = v
            self.assertTrue(root.is_dir(), f"{label}: {root} was not created")
            self.assertIn(label, source)
            print(f"    {label:<44} {root}")

    def test_the_frozen_check_still_fires_and_is_the_one_that_answers(self):
        """`refuse_frozen` was not replaced. `<project>/audit-archive` trips BOTH checks
        and must get the frozen-evidence answer, which is the more useful of the two; an
        `audit-archive` OUTSIDE any project trips only the frozen one, which is the arm
        proving it is still an independent check rather than a dead branch."""
        inside = self.refusal(retention.log_root,
                              log_dir=str(self.project / "audit-archive" / "logs"))
        self.assertIn("frozen", inside)
        self.assertNotIn("resolves INSIDE", inside)
        with self.assertRaises(SystemExit) as caught:
            retention.log_root(self.project,
                               {"log_dir": str(self.outside / "audit-archive" / "x")})
        self.assertIn("frozen", str(caught.exception))
        print("  FROZEN     " + inside.splitlines()[0][:150])

    def test_the_already_migrated_logs_still_resolve(self):
        """The 304 logs the previous run migrated must not be invalidated by a check
        added afterwards. Machine-portable: `present` on the machine that migrated them,
        `missing` on any other — never `corrupt` and never `unrecorded`, which would mean
        the manifest or the resolver had stopped agreeing with the move."""
        rows = retention.manifest_rows(REPO)
        logs = [r for r in rows if r.get("kind") == "log"]
        if not logs:
            self.skipTest("this checkout carries no migrated log manifest")
        states = collections.Counter(retention.find(REPO, r["session_id"])[0] for r in logs)
        print(f"  MIGRATED   {len(logs)} log(s): {dict(states)}")
        self.assertEqual(set(), set(states) - {retention.PRESENT, retention.MISSING},
                         f"the new refusal changed how migrated logs resolve: {states}")
