"""Phase 3, third audit: the machine will not dispatch until the operator has said what
kind of material this is.

The audit's finding was not that `worktree` is weak — the project documents exactly what
it does not hold. It was that the weak profile is what an operator gets by SAYING NOTHING,
while the shipped example paired it with an agent command carrying
`--dangerously-skip-permissions`. A warning next to a default is not a control.

So `trust_level` has no default. `trusted` keeps today's behaviour, unchanged, and says
process-level isolation was accepted deliberately. `untrusted` makes `container`
mandatory. Unset refuses, and names both values.

The one thing this must NOT become is a second authorization enum. `trust_level` answers
"inside what boundary does this run", never "what may it change" — the F-0069 defect was
one enum carrying two meanings, and `TheTwoEnumsStayOrthogonal` is here to keep the fix
from growing the same shape back.
"""

import contextlib
import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.harness import complete_conf, xcheck_submodule

policy = xcheck_submodule("policy")
runner = xcheck_submodule("runner")
util = xcheck_submodule("util")
write = xcheck_submodule("write")

REPO = Path(__file__).resolve().parent.parent
WRITING, READING = ("Planner", "Remediator"), ("Auditor", "Verifier")

# Recorded from `git show 8b8930c:xcheck/runner.py` BEFORE this phase — the four roles'
# resolved profile under the shipped empty `sandbox_profile`, as
# (name, isolating, material_writable, backend). The `trusted` arm below asserts each is
# unchanged: a trust classification that quietly moved anyone's containment would be a
# behaviour change wearing a safety label.
PRE_PHASE_PROFILE = {
    "Planner": ("worktree", True, True, None),
    "Remediator": ("worktree", True, True, None),
    "Auditor": ("readonly", True, False, None),
    "Verifier": ("readonly", True, False, None),
}


def shape(p):
    return (p.name, p.isolating, p.material_writable, p.backend)


def make_repo():
    """A throwaway git repo in a SYSTEM temp dir (never in the project tree, F-0120)."""
    tmp = Path(tempfile.mkdtemp(prefix="xcheck-trust-"))
    (tmp / "audit").mkdir()
    (tmp / "audit" / "XCHECK.md").write_text("# XCHECK\n", encoding="utf-8")
    (tmp / "material.txt").write_text("original\n", encoding="utf-8")
    for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                 ["config", "user.name", "test"], ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", "-C", str(tmp), *args], check=True,
                       capture_output=True, text=True)
    return tmp


class AnUnclassifiedMachineWillNotDispatch(unittest.TestCase):

    def test_unset_refuses_and_names_both_values(self):
        for role in WRITING + READING:
            with self.subTest(role=role):
                conf = complete_conf(trust_level="")
                with self.assertRaises(SystemExit) as cm:
                    runner.require_trust(conf, role, runner.resolve_profile(conf, role))
                msg = str(cm.exception)
                self.assertIn(f"refusing to dispatch {role}", msg)
                for value in policy.TRUST_LEVELS:
                    self.assertIn(f"{policy.TRUST_KEY}={value}", msg,
                                  "the refusal did not say what to write instead")

    def test_an_unknown_value_refuses_rather_than_being_guessed(self):
        """`trust_level=probably-fine` must not read as either one. A vocabulary that
        silently falls back has no vocabulary."""
        conf = complete_conf(trust_level="probably-fine")
        with self.assertRaises(SystemExit) as cm:
            runner.require_trust(conf, "Remediator", runner.resolve_profile(conf, "Remediator"))
        self.assertIn("probably-fine", str(cm.exception))

    def test_the_key_is_operator_policy_and_the_subject_may_not_set_it(self):
        """A subject that could classify itself trusted is the whole loop again."""
        self.assertEqual("policy", policy.classify(policy.TRUST_KEY))


class UntrustedMaterialRequiresAMachineBoundary(unittest.TestCase):
    """The matrix. Every profile that is not `container` is refused for every role, and
    the refusal names the profile it refused — an operator reading "refused" without
    "you asked for readonly" has to go looking for which of their files was in force."""

    def refusal(self, role, profile_name):
        conf = complete_conf(trust_level=policy.UNTRUSTED, sandbox_profile=profile_name)
        with self.assertRaises(SystemExit) as cm:
            runner.require_trust(conf, role, runner.resolve_profile(conf, role))
        return str(cm.exception)

    def test_every_non_container_profile_is_refused_for_every_role(self):
        shown = 0
        for role in WRITING + READING:
            for name in ("", "none", "worktree", "readonly"):
                with self.subTest(role=role, sandbox_profile=name or "(unset)"):
                    msg = self.refusal(role, name)
                    self.assertIn(f"refusing to dispatch {role}", msg)
                    self.assertIn(name or "(unset)", msg,
                                  "the refusal did not name the profile it refused")
                    self.assertIn("network", msg,
                                  "the refusal did not say what the profile fails to hold")
                    shown += 1
        print(f"\n  MATRIX   {shown} role x profile combinations refused under "
              f"{policy.TRUST_KEY}={policy.UNTRUSTED}")

    def test_container_is_accepted_and_is_the_only_one(self):
        """The control arm. Without it, "everything was refused" is satisfied by a gate
        that refuses everything, which is a different bug with the same green tests."""
        accepted = []
        for role in WRITING + READING:
            conf = complete_conf(trust_level=policy.UNTRUSTED, sandbox_profile="container")
            level = runner.require_trust(conf, role, runner.resolve_profile(conf, role))
            self.assertEqual(policy.UNTRUSTED, level)
            accepted.append(role)
        self.assertEqual(list(WRITING + READING), accepted)
        print(f"  MATRIX   container accepted for all {len(accepted)} roles")


class TrustedKeepsTodaysBehaviourExactly(unittest.TestCase):

    def test_the_resolved_profile_is_what_it_was_before_this_phase(self):
        for role, expected in PRE_PHASE_PROFILE.items():
            with self.subTest(role=role):
                conf = complete_conf(trust_level=policy.TRUSTED)
                profile = runner.resolve_profile(conf, role)
                self.assertEqual(policy.TRUSTED, runner.require_trust(conf, role, profile))
                self.assertEqual(expected, shape(profile),
                                 "a trust classification moved this role's containment")

    def test_declaring_trust_changes_no_profile_at_all(self):
        """Directly: the resolution is identical with the key set and unset, so the only
        thing `trust_level` does is decide whether the dispatch happens."""
        for role in PRE_PHASE_PROFILE:
            unset = runner.resolve_profile(complete_conf(trust_level=""), role)
            trusted = runner.resolve_profile(complete_conf(trust_level=policy.TRUSTED), role)
            untrusted = runner.resolve_profile(
                complete_conf(trust_level=policy.UNTRUSTED, sandbox_profile="container"), role)
            self.assertEqual(shape(unset), shape(trusted))
            self.assertEqual("container", untrusted.name)


class TheTwoEnumsStayOrthogonal(unittest.TestCase):
    """CONTROL, and the reason this class exists at all: F-0069 was one enum answering
    both "how contained" and "how privileged". `trust_level` must never grant or revoke
    a write route."""

    def verdicts(self, level):
        """Every role x verb verdict from the authorizer, with the trust level recorded
        on the open dispatch — the place a conflation would leak in, since the dispatch
        record now carries `trust_level`."""
        out = {}
        for role in write.ROLE_ROUTES:
            for verb in sorted({v for r in write.ROLE_ROUTES.values() for v in r}):
                # `session_id` is part of every refusal message, so a dispatch without
                # one makes the authorizer raise KeyError and every verdict reads the
                # same — a dead probe that agrees with itself. See
                # `roles-are-capabilities-only-at-a-boundary`: satisfy the
                # preconditions or the refusal is recorded as "touches nothing".
                dispatch = {"role": role, "session_id": "s" * 16, "trust_level": level}
                try:
                    write.authorize_dispatch_write(dispatch, verb, {}, None)
                    out[(role, verb)] = "allowed"
                except write.WriteRefused:                   # the refusal IS the verdict
                    out[(role, verb)] = "refused"
        return out

    def test_no_verdict_moves_with_the_trust_level(self):
        base = self.verdicts(None)
        # A probe that returns one verdict everywhere is identical under every trust
        # level for the wrong reason. Both answers must be present before the comparison
        # below means anything.
        self.assertEqual({"allowed", "refused"}, set(base.values()),
                         f"the authorizer gave one answer to everything: {set(base.values())}")
        for level in policy.TRUST_LEVELS:
            self.assertEqual(base, self.verdicts(level),
                             f"a write route changed under trust_level={level}")
        print(f"\n  ORTHOGONAL  {len(base)} role x verb verdicts identical for "
              f"unset/{'/'.join(policy.TRUST_LEVELS)}")

    def test_the_authorizer_does_not_read_the_key(self):
        """Structural arm: the behavioural one above can only see the routes that exist
        today, and the next verb added would not be covered by it."""
        self.assertNotIn(policy.TRUST_KEY,
                         Path(REPO, "xcheck", "write.py").read_text(encoding="utf-8"),
                         "the authorizer consults the containment classification")


class TheRefusalHappensBeforeAnyChildExists(unittest.TestCase):
    """A gate that fires after the process is running is not a gate. The command here is
    a shim that leaves a marker on the host; if it ever ran, the marker is there."""

    SHIM = "#!/bin/sh\ntouch {marker}\n"

    def setUp(self):
        self.project = make_repo()
        self.addCleanup(shutil.rmtree, str(self.project), ignore_errors=True)
        box = Path(tempfile.mkdtemp(prefix="xcheck-trust-shim-"))
        self.addCleanup(shutil.rmtree, str(box), ignore_errors=True)
        self.marker = box / "the-child-ran"
        self.shim = box / "agent-shim"
        self.shim.write_text(self.SHIM.format(marker=self.marker), encoding="utf-8")
        self.shim.chmod(0o755)

    def dispatch(self, dry_run, **over):
        conf = complete_conf(auditor_cmd=f"{self.shim} {{prompt}}", session_timeout=60, **over)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            runner.run_session(self.project, conf, "Auditor", "trust charter", dry_run=dry_run)
        return buf.getvalue()

    def test_a_real_run_refuses_and_the_shim_never_ran(self):
        with self.assertRaises(SystemExit) as cm:
            self.dispatch(False, trust_level="")
        self.assertIn(policy.TRUST_KEY, str(cm.exception))
        print(f"\n  DOOR     marker after the refused dispatch: "
              f"{'PRESENT' if self.marker.exists() else 'absent'}")
        self.assertFalse(self.marker.exists(),
                         "the agent command ran before the trust gate refused it")

    def test_dry_run_reports_the_same_refusal(self):
        """A dry run that reports a decision the real run would refuse is the one answer
        it must never give."""
        with self.assertRaises(SystemExit) as real:
            self.dispatch(False, trust_level="")
        with self.assertRaises(SystemExit) as dry:
            self.dispatch(True, trust_level="")
        self.assertEqual(str(real.exception), str(dry.exception))

    def test_control_a_classified_machine_does_dispatch_the_shim(self):
        """Without this arm, "the marker is absent" is what a broken dispatch path also
        produces."""
        out = self.dispatch(False, trust_level=policy.TRUSTED)
        self.assertTrue(self.marker.exists(),
                        f"the trusted arm did not reach the child at all: {out[-300:]}")


class TheDecisionIsReadableAfterwards(unittest.TestCase):
    """A containment decision nobody can read afterwards is not a decision anyone can
    audit. It goes on the envelope, and `status` says it out loud."""

    def test_the_envelope_carries_the_level(self):
        envelope = xcheck_submodule("envelope")
        rec = envelope.dispatch_record(
            ["/bin/true"], "Auditor", "charter", "prompt", "0123456789abcdef",
            runner.PROFILES["container"], 1, "unknown", trust_level=policy.UNTRUSTED)
        self.assertEqual(policy.UNTRUSTED, rec["trust_level"])
        state = xcheck_submodule("state")
        state._check_record(rec, state.SESSION_FIELDS, "sessions[0]")

    def test_status_says_unclassified_and_then_says_the_level(self):
        from tests.harness import Fixture, finding_record, queue_pass, state_doc
        cli = xcheck_submodule("cli")
        fx = Fixture(state_doc(findings=[finding_record("F-0001", "reported")],
                               queue=[queue_pass("P-01", done=False)]))
        self.addCleanup(fx.cleanup)
        seen = {}
        for level in ("", policy.TRUSTED, policy.UNTRUSTED):
            conf = complete_conf(trust_level=level,
                                 **({"sandbox_profile": "container"} if level == policy.UNTRUSTED
                                    else {}))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cli.cmd_status(fx.root, conf)
            seen[level or "(unset)"] = [ln for ln in buf.getvalue().splitlines()
                                        if policy.TRUST_KEY in ln]
        print("\n  STATUS   " + "\n  STATUS   ".join(
            f"{k:11s}{v[0].strip() if v else 'SAID NOTHING'}" for k, v in seen.items()))
        self.assertIn("NOT CLASSIFIED", seen["(unset)"][0])
        for level in policy.TRUST_LEVELS:
            self.assertIn(level, seen[level][0])
            self.assertNotIn("NOT CLASSIFIED", seen[level][0])

    def test_status_itself_never_refuses(self):
        """A reading surface that moves no machine state has nothing for containment to
        mediate — gating it would only teach operators to classify at random to get an
        answer out of the tool."""
        from tests.harness import Fixture, state_doc
        cli = xcheck_submodule("cli")
        fx = Fixture(state_doc())
        self.addCleanup(fx.cleanup)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.cmd_status(fx.root, complete_conf(trust_level=""))
        self.assertIn("trust_level", buf.getvalue())


class TheShippedExampleCannotPairSkipPermissionsWithAWeakProfile(unittest.TestCase):
    """The audit read the example an operator copies, not the code. So does this."""

    def pairing_problem(self, text):
        """The defect, as a predicate over profile TEXT: a role command carrying a
        permission-skipping flag while the profile is not an OS boundary."""
        keys = {}
        for line in text.splitlines():
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            keys[k.strip()] = v.strip()
        contained = keys.get("sandbox_profile", "") == "container"
        for k, v in keys.items():
            if k.endswith(policy.COMMAND_SUFFIX) and not contained:
                for flag in runner.SKIP_PERMISSION_FLAGS:
                    if flag in v:
                        return f"{k} carries {flag} under sandbox_profile={keys['sandbox_profile']!r}"
        return None

    def test_the_shipped_example_does_not_pair_them(self):
        text = Path(REPO, "docs", "operator.conf.example").read_text(encoding="utf-8")
        self.assertIsNone(self.pairing_problem(text))
        self.assertEqual(policy.OPERATOR_PROFILE_TEMPLATE, text,
                         "the example and the template have drifted apart again")

    def test_control_the_previous_example_would_fail_this_test(self):
        """The exact pairing the audit reproduced, reinstated here as a string. If this
        arm passed, the check above would be measuring nothing."""
        old = ("remediator_cmd=claude -p --dangerously-skip-permissions {prompt}\n"
               "sandbox_profile=\n")
        self.assertIsNotNone(self.pairing_problem(old))

    def test_both_keys_are_explicit_in_the_example(self):
        text = Path(REPO, "docs", "operator.conf.example").read_text(encoding="utf-8")
        for key in (policy.TRUST_KEY, "sandbox_profile"):
            self.assertIn(f"\n{key}=", text, f"{key} is not written out in the example")
        self.assertIn(f"{policy.TRUST_KEY}=\n", text,
                      "the example ships a trust classification the operator did not make")


if __name__ == "__main__":
    unittest.main()
