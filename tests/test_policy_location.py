"""The operator profile is OUTSIDE the subject, and now that is a check.

Phase 1, third industrial audit, release blocker. The previous run moved controller policy
out of the audited repository and documented the boundary; `policy.load()` never received
the project, so it could not check location. Reproduced before this file was written:

    audit/operator.conf:  auditor_cmd=/bin/true {prompt}
                          sandbox_profile=none
    XCHECK_OPERATOR_PROFILE=<project>/audit/operator.conf
    -> load_conf() ACCEPTED it and applied sandbox_profile=none

Which restores the whole loop the split existed to cut: a session writes any path under
`audit/`, the readonly courier carries the edit out, and the next dispatch runs under it.

Two arms are load-bearing throughout. "Refuses a profile inside the subject" is satisfied
by a loader that refuses every profile, so every refusal here is paired with an ACCEPT of
the legitimate case — a profile beside the repository, which is where operators keep them.
"""

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.harness import xcheck_submodule

REPO = Path(__file__).resolve().parent.parent
policy = xcheck_submodule("policy")
util = xcheck_submodule("util")
cli = xcheck_submodule("cli")

PROFILE = "auditor_cmd=/bin/true {prompt}\nsandbox_profile=none\n"


class Subject(unittest.TestCase):
    """A project with an audit directory, and somewhere outside it to put files."""

    def setUp(self):
        self.box = Path(tempfile.mkdtemp(prefix="xcheck-loc-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.box, True))
        self.project = self.box / "subject"
        (self.project / "audit").mkdir(parents=True)
        (self.project / "audit" / "XCHECK.md").write_text("# XCHECK\n", encoding="utf-8")
        self.saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self.saved)))
        for k in ("XCHECK_OPERATOR_PROFILE", "XDG_CONFIG_HOME"):
            os.environ.pop(k, None)

    def write(self, rel, text=PROFILE):
        p = (self.project / rel) if not str(rel).startswith("/") else Path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def outside(self, name="operator.conf", text=PROFILE):
        p = self.box / name
        p.write_text(text, encoding="utf-8")
        return p

    def load(self, profile):
        """Through the real boundary every reader of configuration passes through."""
        os.environ["XCHECK_OPERATOR_PROFILE"] = str(profile)
        return util.load_conf(self.project / "audit")


class TheLoaderKnowsWhatProjectItIsFor(Subject):

    def test_the_project_is_required_not_optional(self):
        """A loader that can be called without the subject is a loader that will be."""
        prof = self.outside()
        with self.assertRaises(TypeError):
            policy.load(prof)                                   # no project
        with self.assertRaises(policy.PolicyError) as cm:
            policy.load(prof, None)                             # explicit None
        print(f"\n  REQUIRED   load(path) -> TypeError; load(path, None) -> "
              f"{str(cm.exception)[:72]}")
        self.assertIn("cannot be validated without the project", str(cm.exception))
        self.assertEqual({"auditor_cmd", "sandbox_profile"},
                         set(policy.load(prof, self.project)),
                         "the legitimate call must still work")


class AProfileInsideTheSubjectIsRefused(Subject):

    def test_the_audits_own_directory(self):
        """The audit's reproduction, by path."""
        inside = self.write("audit/operator.conf")
        with self.assertRaises(SystemExit) as cm:
            self.load(inside)
        msg = str(cm.exception)
        print(f"\n  REFUSED    {msg.splitlines()[0][:100]}")
        for line in msg.splitlines()[1:4]:
            print(f"             {line}")
        self.assertIn("may not live inside the repository being audited", msg)
        self.assertIn("audit/operator.conf", msg)
        self.assertIn(str(self.project.resolve()), msg)

    def test_anywhere_else_in_the_tree_too(self):
        """The rule is the project root, not a list of directories somebody remembered."""
        for rel in ("operator.conf", "src/operator.conf", "audit-archive/operator.conf",
                    ".git/operator.conf", "audit/nested/deep/operator.conf"):
            with self.subTest(path=rel):
                with self.assertRaises(SystemExit) as cm:
                    self.load(self.write(rel))
                self.assertIn("may not live inside", str(cm.exception))
        print("  REFUSED    5 locations inside the tree, including audit-archive/ and .git/")

    def test_a_symlink_that_leads_back_inside(self):
        """A gate that checks the path it was handed and not where it leads is not a gate."""
        inside = self.write("audit/operator.conf")
        link = self.box / "looks-external.conf"
        link.symlink_to(inside)
        with self.assertRaises(SystemExit) as cm:
            self.load(link)
        msg = str(cm.exception)
        print(f"  REFUSED    symlink: "
              f"{[l.strip() for l in msg.splitlines() if 'symlink' in l][0][:110]}")
        self.assertIn("may not live inside", msg)
        self.assertIn("symlink", msg)
        self.assertIn(str(inside.resolve()), msg, "the refusal must name the target")

    def test_a_symlinked_parent_directory_too(self):
        """`resolve()` follows every component, so an intermediate link is the same rule."""
        real = self.project / "audit" / "policies"
        real.mkdir()
        (real / "operator.conf").write_text(PROFILE, encoding="utf-8")
        link_dir = self.box / "policies"
        link_dir.symlink_to(real, target_is_directory=True)
        with self.assertRaises(SystemExit) as cm:
            self.load(link_dir / "operator.conf")
        print("  REFUSED    through a symlinked parent directory")
        self.assertIn("may not live inside", str(cm.exception))

    def test_the_explicit_flag_is_not_an_override(self):
        """`--policy` is a location, not a permission. An operator who types the path is
        still handing the subject its own policy."""
        inside = self.write("audit/operator.conf")
        with self.assertRaises(SystemExit) as cm:
            util.load_conf(self.project / "audit", profile=inside)
        print("  REFUSED    --policy pointing inside the subject")
        self.assertIn("may not live inside", str(cm.exception))


class TheLegitimateCaseStillLoads(Subject):
    """Without these, every refusal above is satisfied by refusing everything."""

    def test_a_profile_beside_the_repository_is_accepted(self):
        conf = self.load(self.outside())
        print(f"\n  ACCEPTED   profile in the project's PARENT -> "
              f"sandbox_profile={conf.get('sandbox_profile')!r}")
        self.assertEqual("none", conf.get("sandbox_profile"))
        self.assertEqual("/bin/true {prompt}", conf.get("auditor_cmd"))

    def test_a_project_named_like_a_prefix_of_the_profile_path_is_accepted(self):
        """`subject-notes/operator.conf` is not inside `subject/`. A string `startswith`
        would call it inside, which is why the check uses path parents."""
        sibling = self.box / "subject-notes"
        sibling.mkdir()
        prof = sibling / "operator.conf"
        prof.write_text(PROFILE, encoding="utf-8")
        conf = self.load(prof)
        print(f"  ACCEPTED   {prof.parent.name}/ is not inside {self.project.name}/")
        self.assertEqual("none", conf.get("sandbox_profile"))

    def test_the_other_refusals_are_unchanged(self):
        """The location gate is a NEW refusal, not a replacement for the fail-closed read."""
        for label, text, needle in (
                ("unknown key", "sandbox_profil=container\n", "sandbox_profil"),
                ("a project key", "batch_size=8\n", "belong to the PROJECT"),
                ("malformed", "sandbox_profile\n", "KEY=VALUE")):
            with self.subTest(case=label):
                with self.assertRaises(SystemExit) as cm:
                    self.load(self.outside(f"{label}.conf", text))
                self.assertIn(needle, str(cm.exception))
        print("  UNCHANGED  unknown key, project key and malformed line still refused")


class TheProfileIsReadOnce(Subject):
    """One read, or the envelope records a digest for a policy the run never applied."""

    def test_the_digest_belongs_to_the_bytes_that_were_parsed(self):
        prof = self.outside()
        first = prof.read_bytes()
        profile = policy.read_profile(prof, self.project)
        self.assertEqual(hashlib.sha256(first).hexdigest(), profile.digest)

        # The window the audit named: parse, then hash in a second read. Change the file
        # in between and a two-read loader records the digest of a policy nobody applied.
        prof.write_text("auditor_cmd=/bin/false {prompt}\nsandbox_profile=container\n",
                        encoding="utf-8")
        after = policy.profile_digest(prof)
        print(f"\n  ONE READ   parsed digest {profile.digest[:16]}  "
              f"asked again after the file changed -> {after[:16]}")
        self.assertEqual(profile.digest, after,
                         "the digest followed the file instead of the configuration that "
                         "is actually in force")
        self.assertEqual("/bin/true {prompt}", profile.keys["auditor_cmd"],
                         "the parse must be of the same bytes the digest covers")

    def test_a_path_never_loaded_still_hashes(self):
        """The operator at their own terminal asking what their profile hashes to."""
        fresh = self.outside("never-loaded.conf", "sandbox_profile=container\n")
        self.assertEqual(hashlib.sha256(fresh.read_bytes()).hexdigest(),
                         policy.profile_digest(fresh))
        print("  ONE READ   a path no load touched is hashed from disk, as before")


class TheRefusalReachesTheDispatchVerbs(Subject):
    """A typed error is not a control until it stops a run."""

    def test_next_and_dry_run_refuse_before_deciding_anything(self):
        inside = self.write("audit/operator.conf")
        for argv in (["next"], ["next", "--dry-run"], ["loop"]):
            with self.subTest(argv=" ".join(argv)):
                r = subprocess.run(
                    [sys.executable, "-m", "xcheck", "--project", str(self.project), *argv],
                    capture_output=True, text=True, timeout=120,
                    env={**os.environ, "XCHECK_OPERATOR_PROFILE": str(inside)})
                out = r.stdout + r.stderr
                self.assertNotEqual(0, r.returncode, out[-400:])
                self.assertIn("may not live inside", out)
                self.assertNotIn("run-auditor", out,
                                 "a dispatch decision was printed before the refusal")
        print("\n  DISPATCH   next, next --dry-run and loop each refuse, exit non-zero, "
              "and print no decision")

    def test_require_policy_refuses_it_too(self):
        """The function whose whole job is 'may this run start' checks the rule itself,
        rather than relying on something upstream having checked."""
        inside = self.write("audit/operator.conf")
        os.environ["XCHECK_OPERATOR_PROFILE"] = str(inside)
        with self.assertRaises(SystemExit) as cm:
            cli.require_policy(None, self.project)
        self.assertIn("may not live inside", str(cm.exception))
        outside = self.outside()
        os.environ["XCHECK_OPERATOR_PROFILE"] = str(outside)
        self.assertEqual(outside, cli.require_policy(None, self.project))
        print("  DISPATCH   require_policy refuses the inside profile, accepts the outside one")


if __name__ == "__main__":
    unittest.main()
