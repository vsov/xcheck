"""F4 (0.9.6): the material digest identifies what the agent was shown.

Scheme 2 (`state.material_identity`): every tracked and untracked non-ignored file outside
the audit workspace, `path\\0mode\\0sha256(bytes)` per entry (a symlink hashes its link
text and is not followed), sorted. Read from the bytes, so git's diff configuration cannot
move it; `unknown` with a reason whenever the tree could not be read faithfully.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.harness import xcheck_submodule

state = xcheck_submodule("state")


def git(root, *argv):
    subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)


class Repo:
    def __init__(self, test):
        self.root = Path(tempfile.mkdtemp(prefix="xc-material-"))
        test.addCleanup(shutil.rmtree, self.root, True)
        for argv in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                     ["config", "user.name", "t"]):
            git(self.root, *argv)
        (self.root / "tracked.py").write_text("x = 1\n")
        (self.root / "payload.bin").write_bytes(b"\x00base\x00")
        (self.root / ".gitignore").write_text("ignored.log\n")
        (self.root / "audit").mkdir()
        (self.root / "audit" / "state.json").write_text("{}")
        os.symlink("tracked.py", self.root / "link")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "base")

    def digest(self):
        d, why = state.material_identity(self.root)
        assert d is not None, why
        return d


class EveryPresentedChangeMovesTheDigest(unittest.TestCase):

    def test_the_table(self):
        r = Repo(self)
        p = r.root
        steps = [
            # (label, mutation, moves?)
            ("new untracked file", lambda: (p / "untracked.py").write_text("result = 1\n"), True),
            ("edit the untracked file", lambda: (p / "untracked.py").write_text("result = 999\n"), True),
            ("tracked text edit", lambda: (p / "tracked.py").write_text("x = 2\n"), True),
            ("binary edit, unstaged", lambda: (p / "payload.bin").write_bytes(b"\x00one\x00"), True),
            ("binary edit, staged", lambda: ((p / "payload.bin").write_bytes(b"\x00two\x00"),
                                             git(p, "add", "payload.bin")), True),
            ("rename", lambda: git(p, "mv", "tracked.py", "renamed.py"), True),
            ("delete", lambda: (p / "renamed.py").unlink(), True),
            ("symlink retarget", lambda: ((p / "link").unlink(),
                                          os.symlink("payload.bin", p / "link")), True),
            ("mode change", lambda: os.chmod(p / "payload.bin", 0o755), True),
            ("git add of unchanged content", lambda: git(p, "add", "-A"), False),
            ("diff.noprefix", lambda: git(p, "config", "diff.noprefix", "true"), False),
            ("textconv", lambda: ((p / ".git" / "info" / "attributes").write_text("*.bin diff=hex\n"),
                                  git(p, "config", "diff.hex.textconv", "xxd")), False),
            ("bookkeeping only", lambda: (p / "audit" / "state.json").write_text('{"r": 2}'), False),
            ("ignored file", lambda: (p / "ignored.log").write_text("noise\n"), False),
        ]
        prev = r.digest()
        print("\n  MATERIAL IDENTITY (scheme 2)")
        print(f"    {'base':32} {prev[:16]}")
        for label, mutate, moves in steps:
            mutate()
            now = r.digest()
            print(f"    {label:32} {now[:16]}  {'moved' if now != prev else 'same'}")
            self.assertEqual(moves, now != prev, label)
            prev = now

    def test_the_symlink_is_not_followed(self):
        r = Repo(self)
        before = r.digest()
        (r.root / "tracked.py").write_text("x = 3\n")         # the link's target changes
        after = r.digest()
        self.assertNotEqual(before, after)                      # the target itself moved it
        os.remove(r.root / "link")
        os.symlink("tracked.py", r.root / "link")               # same link text
        self.assertEqual(after, r.digest())


class ItSaysUnknownInsteadOfGuessing(unittest.TestCase):

    def test_not_a_git_repository(self):
        d = Path(tempfile.mkdtemp(prefix="xc-nogit-"))
        self.addCleanup(shutil.rmtree, d, True)
        digest, why = state.material_identity(d)
        print(f"\n  not a repo -> {digest} {why}")
        self.assertIsNone(digest)
        self.assertIn("git", why)
        self.assertIsNone(state.subject_manifest(d))

    def test_git_exits_non_zero(self):
        r = Repo(self)
        real = subprocess.run

        def broken(argv, *a, **kw):
            if argv[:2] == ["git", "ls-files"]:
                return subprocess.CompletedProcess(argv, 128, b"", b"fatal: index broken")
            return real(argv, *a, **kw)
        with mock.patch.object(state.subprocess, "run", side_effect=broken):
            digest, why = state.material_identity(r.root)
        print(f"\n  git failed -> {digest} {why}")
        self.assertIsNone(digest)
        self.assertIn("index broken", why)

    def test_a_file_that_changes_while_it_is_read(self):
        r = Repo(self)
        target = str(r.root / "tracked.py")
        real = os.lstat
        seen = {"n": 0}

        def lstat(path, *a, **kw):
            st = real(path, *a, **kw)
            if str(path) == target:
                seen["n"] += 1
                if seen["n"] == 2:                  # the AFTER stat: the file grew
                    with open(target, "a") as f:
                        f.write("# appended mid-read\n")
                    return real(path, *a, **kw)
            return st
        with mock.patch.object(state.os, "lstat", side_effect=lstat):
            digest, why = state.material_identity(r.root)
        print(f"\n  changed mid-read -> {digest} {why}")
        self.assertIsNone(digest)
        self.assertIn("tracked.py", why)
        self.assertIn("changed while it was read", why)
        self.assertEqual(2, seen["n"])


from tests.test_container_profile import DOCKER_OK, SKIP_REASON, SandboxCase  # noqa: E402

# What a child can see, from its working directory and from `/src`.
LOOK = ('for p in ignored.log /src/ignored.log untracked.py /src/untracked.py; do '
        '[ -e "$p" ] && echo "SEEN:$p" || echo "ABSENT:$p"; done')


class PresentationAgreesWithTheDigest(SandboxCase):
    """Per profile: a git-ignored file is neither presented nor hashed; an untracked
    non-ignored one is both (the positive control)."""

    def material(self):
        d = self.project()
        (d / ".gitignore").write_text("ignored.log\n")
        git(d, "add", ".gitignore")
        git(d, "commit", "-qm", "ignore")
        (d / "ignored.log").write_text("secret-ish noise\n")
        (d / "untracked.py").write_text("result = 1\n")
        return d

    def check(self, profile):
        d = self.material()
        before = state.material_identity(d)[0]
        (d / "ignored.log").write_text("changed noise\n")
        self.assertEqual(before, state.material_identity(d)[0], "the digest hashes ignored files")
        rc, out = self.run_in(profile, LOOK, project=d)
        print(f"\n  {profile:9} {out.split()}")
        self.assertIn("SEEN:untracked.py", out, "control: untracked material not presented")
        self.assertIn("ABSENT:ignored.log", out)
        self.assertIn("ABSENT:/src/ignored.log", out)
        return out

    def test_worktree(self):
        self.check("worktree")

    def test_readonly(self):
        self.check("readonly")

    def test_container(self):
        if not DOCKER_OK:
            self.skipTest(SKIP_REASON)
        out = self.check("container")
        # `/src` exists under the container, so ABSENT there is the seed, not no mount.
        self.assertIn("SEEN:/src/untracked.py", out)


class BothDispatchersRecordIt(unittest.TestCase):

    def test_serial_and_parallel(self):
        from tests.test_stop_channel import Run
        for parallel in (False, True):
            with self.subTest(parallel=parallel):
                r = Run(self, {}, parallel=parallel)
                expect = state.material_identity(r.f.root)[0]
                r.cli("next", "--yes")
                recs = json.loads((r.f.audit / "state.json").read_text())["sessions"]
                print(f"\n  parallel={parallel}: " + "; ".join(
                    f"manifest {s['subject_manifest'][:12]} scheme "
                    f"{s.get('subject_manifest_scheme')} commit {s['subject_commit'][:7]} "
                    f"controller {s['controller_commit'][:7]}" for s in recs))
                self.assertEqual(2 if parallel else 1, len(recs))
                for s in recs:
                    self.assertEqual(2, s["subject_manifest_scheme"])
                    self.assertRegex(s["subject_manifest"], r"^[0-9a-f]{64}$")
                    self.assertRegex(s["subject_commit"], r"^[0-9a-f]{40}$")
                    self.assertNotEqual("unknown", s["controller_commit"])
                # The first dispatch saw the tree as it stood before any session ran.
                self.assertEqual(expect, recs[0]["subject_manifest"])


class OldRecordsKeepTheirMeaning(unittest.TestCase):

    def test_a_v1_session_without_a_scheme_loads(self):
        from tests.harness import Fixture, state_doc
        f = Fixture(state_doc())
        self.addCleanup(f.cleanup)
        doc = json.loads((f.audit / "state.json").read_text())
        envelope = xcheck_submodule("envelope")
        runner = xcheck_submodule("runner")
        rec = envelope.dispatch_record(["x"], "Auditor", "P-01", "p", "d" * 16,
                                       runner.resolve_profile({}, "Auditor"), 0, "unknown",
                                       subject_manifest="a" * 64)
        rec.pop("subject_manifest_scheme", None)
        doc["sessions"].append(rec)
        f.write_state(doc)
        st = state.load_state(f.audit)
        self.assertEqual("a" * 64, st.sessions[0]["subject_manifest"])
        self.assertNotIn("subject_manifest_scheme", st.sessions[0])

    def test_v1_and_v2_are_not_compared(self):
        bench = xcheck_submodule("benchmark")
        v1 = {"subject_manifest": "a" * 64}
        v2 = {"subject_manifest": "a" * 64, "subject_manifest_scheme": 2}
        self.assertIsNone(bench.material_comparable(v1, v2))
        self.assertTrue(bench.material_comparable(v2, dict(v2)))
        self.assertFalse(bench.material_comparable(v2, dict(v2, subject_manifest="b" * 64)))
        self.assertIsNone(bench.material_comparable(v2, dict(v2, subject_manifest="unknown")))

    def test_benchmark_and_bundle_name_the_scheme(self):
        bench = xcheck_submodule("benchmark")
        r = Repo(self)
        prov = bench.run_provenance(r.root, {})
        self.assertEqual(2, prov["subject_manifest_scheme"])
        self.assertEqual(r.digest(), prov["subject_manifest"])
        src = (Path(state.__file__).parent / "bundle.py").read_text()
        self.assertIn('"subject_manifest_scheme": state_mod.MANIFEST_SCHEME', src)


if __name__ == "__main__":
    unittest.main()
