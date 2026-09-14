"""Controller policy may not live inside the thing being audited.

The audit's widest architectural loop, and every link in it is documented behaviour:
`audit/orchestrator.conf` names the command each role runs, the containment it runs
inside, the environment and network it reaches, its limits and whether commits are
pushed; the readonly courier lets a session write anywhere under `audit/`; the courier
ships that edit; the next dispatch executes it. The subject sets the terms of its own
audit.

The split is enforced at exactly one place — `util.load_conf` — because every path that
reads project configuration comes through it. A check spread over consumers is a check
one consumer will be written without.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.harness import needs_live_corpus, REPO, xcheck_submodule

policy = xcheck_submodule("policy")
util = xcheck_submodule("util")

# The audit's own list, transcribed. Asserted against the table rather than derived from
# it: a classification that re-derives itself from the thing it classifies proves only
# that the code agrees with the code.
AUDIT_SAYS_POLICY = (
    "planner_cmd", "auditor_cmd", "verifier_cmd", "remediator_cmd",
    "sandbox_profile", "env_allowlist",
    "container_image", "container_cpus", "container_memory_mb", "container_pids",
    "unsafe_allow_unpinned_image",
    "egress_allowlist", "egress_uplink", "egress_broker_image",
    "session_timeout", "kill_grace", "cpu_seconds", "address_space_mb", "lease_ttl",
    "retry_limit",
    "parallel_passes", "parallel_workers", "parallel_budget_minutes",
    "parallel_confirm_above",
    "max_sessions", "max_sessions_per_run",
    "push_after_commit",
)
AUDIT_SAYS_PROJECT = (
    "batch_size", "triage_batch_cap", "session_note", "scope_typing",
    "construal_gate", "embargo", "embargo_after", "loop_progress_limit",
)


class EveryKeyIsExactlyOneKind(unittest.TestCase):

    def test_the_table_covers_conf_defaults_with_no_gap_and_no_overlap(self):
        pol = sorted(k for k in util.CONF_DEFAULTS if policy.classify(k) == "policy")
        prj = sorted(k for k in util.CONF_DEFAULTS if policy.classify(k) == "project")
        unk = sorted(k for k in util.CONF_DEFAULTS if policy.classify(k) == "unknown")
        print(f"\n  CLASSIFICATION over {len(util.CONF_DEFAULTS)} CONF_DEFAULTS keys")
        for k in sorted(util.CONF_DEFAULTS):
            print(f"    {k:32} {policy.classify(k)}")
        print(f"    {'':32} policy {len(pol)}  project {len(prj)}  unknown {len(unk)}")
        self.assertEqual([], unk, "a shipped conf key is in neither table")
        self.assertEqual(set(), policy.POLICY_KEYS & policy.PROJECT_KEYS,
                         "a key is in both tables")
        self.assertEqual(len(util.CONF_DEFAULTS), len(pol) + len(prj))

    def test_the_classification_matches_the_audits_own_list(self):
        for k in AUDIT_SAYS_POLICY:
            with self.subTest(key=k):
                self.assertEqual("policy", policy.classify(k))
        for k in AUDIT_SAYS_PROJECT:
            with self.subTest(key=k):
                self.assertEqual("project", policy.classify(k))

    def test_only_a_registered_role_command_is_policy(self):
        """REVERSED IN PHASE 2, deliberately, and the old reasoning is kept here because
        a reversal nobody can read is a decision nobody can review.

        The rule used to be the `_cmd` suffix — "a role added next year is policy without
        anyone remembering to add it, which is the difference between a rule and a list."
        That is true, and it is also how `auditro_cmd=...` became a policy key naming no
        role: accepted, inert, and indistinguishable to the operator from a configured
        Auditor. The third audit named it. A suffix cannot tell a new role from a typo,
        so the registry is closed and adding a role is one explicit edit.

        `triage_cmd` is the interesting case and it is `unknown` on purpose: Triage is the
        human gate the orchestrator STOPS for and never dispatches, so a command for it
        would never be run.
        """
        for key in ("planner_cmd", "auditor_cmd", "remediator_cmd", "verifier_cmd"):
            self.assertEqual("policy", policy.classify(key))
        for key in ("triage_cmd", "some_future_role_cmd", "auditro_cmd"):
            self.assertEqual("unknown", policy.classify(key),
                             f"{key} is not a role this orchestrator dispatches")

    def test_an_unrecognised_key_is_unknown_and_not_an_error(self):
        """`load_conf` has always tolerated keys it does not recognise. Turning one into
        a refusal HERE would make this module a second, accidental validator of the
        conf's vocabulary, and it would refuse configurations that work today."""
        self.assertEqual("unknown", policy.classify("some_local_convention"))


class TheSubjectMayNotCarryPolicy(unittest.TestCase):

    def audit_dir(self, body):
        tmp = Path(tempfile.mkdtemp(prefix="xcheck-conf-policy-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "orchestrator.conf").write_text(body, encoding="utf-8")
        return tmp

    def test_the_refusal_names_every_offender_in_one_pass(self):
        """Not one key at a time. A loader that refused the first offender would make a
        migration one run per key, and an operator rediscovering their own configuration
        one refusal at a time will reasonably conclude the tool is broken."""
        d = self.audit_dir("batch_size=8\nsandbox_profile=none\nretry_limit=3\n"
                           "auditor_cmd=evil {prompt}\n")
        with self.assertRaises(SystemExit) as cm:
            util.load_conf(d)
        msg = str(cm.exception)
        for k in ("sandbox_profile", "retry_limit", "auditor_cmd"):
            self.assertIn(k, msg)
        self.assertNotIn("batch_size", msg.split("Everything else")[0],
                         "a project key was named as an offender")

    def test_control_exactly_one_policy_key_is_refused_by_name(self):
        """CONTROL for the arm above: with a single offender the refusal still fires and
        still names it, so 'it refuses' is not an artifact of there being many."""
        d = self.audit_dir("batch_size=8\npush_after_commit=on\n")
        with self.assertRaises(SystemExit) as cm:
            util.load_conf(d)
        print(f"\n  REFUSAL, verbatim:\n{cm.exception}")
        self.assertIn("push_after_commit", str(cm.exception))

    def test_a_project_only_conf_loads(self):
        """The other half of the control. Without this, 'the policy key was refused'
        could be 'every conf is refused'."""
        d = self.audit_dir("batch_size=8\ntriage_batch_cap=4\nembargo=on\n")
        conf = util.load_conf(d)
        # Numeric and boolean keys come back validated (F-0113: the Conf boundary owns
        # the vocabulary), which is why these read 4 and True, not "4" and "on".
        self.assertEqual(4, conf["triage_batch_cap"])
        self.assertIs(True, conf["embargo"])

    def test_the_refusal_is_addressed_enough_to_act_on(self):
        """An operator must be able to migrate without reading source: the exact file,
        the exact lines, and how to put that file somewhere else."""
        d = self.audit_dir("sandbox_profile=container\ncontainer_cpus=4\n")
        with self.assertRaises(SystemExit) as cm:
            util.load_conf(d)
        msg = str(cm.exception)
        where = policy.operator_profile_path()
        # A machine that names no profile is the interesting case, not an edge one: the
        # path is never derived from `~` (see policy.py), so on a machine with neither
        # env var set the refusal must say how to name one instead of printing a guess.
        self.assertIn(str(where) if where is not None else policy.XDG_CONFIG_ENV, msg)
        self.assertIn("sandbox_profile=container", msg, "the operator's own value is "
                                                        "not quoted back for copying")
        self.assertIn("container_cpus=4", msg)
        self.assertIn(policy.OPERATOR_PROFILE_ENV, msg)

    def env(self, **over):
        """Set the named environment variables for one call and restore them after."""
        saved = {k: os.environ.get(k) for k in over}

        def restore():
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.addCleanup(restore)
        for k, v in over.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_the_path_is_named_explicitly_and_never_derived_from_home(self):
        """xcheck holds a tested claim that no shipped file can resolve the operator's
        home directory — that is what makes "it writes nothing into your ~/.claude" a
        property instead of a promise. So the profile is named by env var, and a machine
        that names none HAS none: an invented `~/.config/...` would cost the claim."""
        self.env(**{policy.OPERATOR_PROFILE_ENV: "/tmp/elsewhere/operator.conf",
                    policy.XDG_CONFIG_ENV: "/tmp/xdg"})
        self.assertEqual(Path("/tmp/elsewhere/operator.conf"),
                         policy.operator_profile_path())

        self.env(**{policy.OPERATOR_PROFILE_ENV: None, policy.XDG_CONFIG_ENV: "/tmp/xdg"})
        self.assertEqual(Path("/tmp/xdg/xcheck/operator.conf"),
                         policy.operator_profile_path())

        self.env(**{policy.OPERATOR_PROFILE_ENV: None, policy.XDG_CONFIG_ENV: None})
        self.assertIsNone(policy.operator_profile_path(),
                          "a path was invented for a machine that names none")

    def test_the_shipped_default_conf_carries_no_policy_key(self):
        """`load_conf` writes DEFAULT_CONF for a project that has none, and falls back
        to it when the file is absent. A default that the loader itself refuses would
        make a fresh install unusable."""
        raw = {}
        for line in util.DEFAULT_CONF.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                raw[line.split("=", 1)[0].strip()] = ""
        print(f"  DEFAULT_CONF keys: {sorted(raw)}")
        self.assertEqual([], policy.policy_keys_in(raw))
        self.assertEqual(set(policy.PROJECT_KEYS), set(raw),
                         "DEFAULT_CONF no longer offers every project key")

    def test_the_operator_profile_template_carries_only_policy(self):
        raw = {}
        for line in policy.OPERATOR_PROFILE_TEMPLATE.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                raw[line.split("=", 1)[0].strip()] = ""
        self.assertEqual([], [k for k in raw if policy.classify(k) != "policy"])
        self.assertEqual(set(), policy.POLICY_KEYS - set(raw),
                         "the template omits a policy key, so migrating by copying it "
                         "would silently drop a setting")


@needs_live_corpus
class ThisRepositoryIsMigrated(unittest.TestCase):
    """The project's own conf, not a fixture. A split every project must obey and the
    project that ships it does not is a split that will be reverted."""

    def test_its_own_conf_carries_no_policy_key_and_loads(self):
        path = REPO / "audit" / "orchestrator.conf"
        raw = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                raw[line.split("=", 1)[0].strip()] = line.split("=", 1)[1].strip()
        offenders = policy.policy_keys_in(raw)
        print(f"  THIS REPO  audit/orchestrator.conf: {sorted(raw)}  "
              f"policy offenders: {offenders}")
        self.assertEqual([], offenders)
        util.load_conf(REPO / "audit")      # raises SystemExit if it does not


if __name__ == "__main__":
    unittest.main()


class ThePolicyIsLoadedFromOutsideTheSubject(unittest.TestCase):
    """Classification moves the keys; this gives them a trusted home and a record.

    Without an external source there is nowhere for policy to come from; without a
    digest in the envelope there is no answer to "which policy was in force that day".
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xcheck-policy-load-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.saved = {k: os.environ.get(k) for k in
                      (policy.OPERATOR_PROFILE_ENV, policy.XDG_CONFIG_ENV)}

        def restore():
            for k, v in self.saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.addCleanup(restore)
        for k in self.saved:
            os.environ.pop(k, None)

    def profile(self, name="operator.conf", text=None):
        p = self.tmp / name
        p.write_text(policy.OPERATOR_PROFILE_TEMPLATE if text is None else text,
                     encoding="utf-8")
        return p

    def test_the_resolution_order_is_explicit_then_env_then_xdg(self):
        explicit = self.profile("explicit.conf")
        env = self.profile("env.conf")
        xdg_dir = self.tmp / "xdg"
        (xdg_dir / "xcheck").mkdir(parents=True)
        xdg = xdg_dir / "xcheck" / "operator.conf"
        xdg.write_text(policy.OPERATOR_PROFILE_TEMPLATE, encoding="utf-8")

        os.environ[policy.OPERATOR_PROFILE_ENV] = str(env)
        os.environ[policy.XDG_CONFIG_ENV] = str(xdg_dir)
        print("\n  RESOLUTION ORDER (all three present)")
        print(policy.describe_search(explicit))
        self.assertEqual(explicit, policy.resolve(explicit))

        os.environ.pop(policy.OPERATOR_PROFILE_ENV)
        self.assertEqual(xdg, policy.resolve())
        os.environ[policy.OPERATOR_PROFILE_ENV] = str(env)
        self.assertEqual(env, policy.resolve())

    def test_a_source_that_names_a_missing_file_does_not_end_the_search(self):
        """An operator who exports the variable before writing the file is better served
        by the next candidate than by a stop at the first miss."""
        xdg_dir = self.tmp / "xdg"
        (xdg_dir / "xcheck").mkdir(parents=True)
        real = xdg_dir / "xcheck" / "operator.conf"
        real.write_text(policy.OPERATOR_PROFILE_TEMPLATE, encoding="utf-8")
        os.environ[policy.OPERATOR_PROFILE_ENV] = str(self.tmp / "not-written-yet.conf")
        os.environ[policy.XDG_CONFIG_ENV] = str(xdg_dir)
        self.assertEqual(real, policy.resolve())

    def test_nothing_resolves_is_a_refusal_that_names_the_order(self):
        msg = policy.no_profile_refusal()
        print(f"\n  REFUSAL WHEN NOTHING RESOLVES:\n{msg}")
        self.assertIsNone(policy.resolve())
        for needle in ("--policy", policy.OPERATOR_PROFILE_ENV, policy.XDG_CONFIG_ENV,
                       "no default"):
            self.assertIn(needle, msg)

    def test_a_profile_is_refused_when_it_cannot_be_trusted(self):
        """Three shapes, all fail-closed and all naming the offender. The unknown-key
        arm is the load-bearing one: unlike orchestrator.conf, a typo here would be a
        containment setting that silently did nothing."""
        cases = [
            ("unreadable", self.tmp / "absent.conf", "cannot be read"),
            ("malformed", self.profile("bad.conf", "sandbox_profile\n"), "KEY=VALUE"),
            ("unknown key", self.profile("typo.conf", "sandbox_profil=container\n"),
             "sandbox_profil"),
            ("a project key", self.profile("mixed.conf", "batch_size=8\n"),
             "belong to the PROJECT"),
        ]
        for label, path, needle in cases:
            with self.subTest(case=label):
                with self.assertRaises(policy.PolicyError) as cm:
                    # PHASE 1: `load` now takes the project it is for, so it can refuse a
                    # profile inside it. These profiles sit in the test's own tmp dir and
                    # the "project" here is a sibling — the location gate passes and the
                    # vocabulary refusals below are what is being measured.
                    policy.load(path, self.tmp / "subject")
                print(f"  {label:<14} {str(cm.exception)[:110]}")
                self.assertIn(needle, str(cm.exception))

    def test_a_valid_profile_loads_every_policy_key_and_no_other(self):
        raw = policy.load(self.profile(), self.tmp / "subject")
        self.assertEqual([], [k for k in raw if policy.classify(k) != "policy"])
        self.assertIn("auditor_cmd", raw)
        self.assertIn("sandbox_profile", raw)

    def test_the_digest_is_over_the_bytes_so_two_files_are_two_answers(self):
        a = self.profile("a.conf")
        b = self.profile("b.conf", policy.OPERATOR_PROFILE_TEMPLATE + "\n# a comment\n")
        self.assertEqual(policy.profile_digest(a), policy.profile_digest(self.profile("c.conf")))
        self.assertNotEqual(policy.profile_digest(a), policy.profile_digest(b),
                            "a comment-only difference produced the same digest — the "
                            "digest is not over the bytes")
        self.assertEqual(64, len(policy.profile_digest(a)))


class TheLoopTheAuditNamedIsCut(unittest.TestCase):
    """The whole point, driven end to end through the real refusal path.

        subject under audit -> carries the controller's policy
        -> a session edits audit/orchestrator.conf
        -> the courier ships the edit
        -> the next dispatch executes the new policy

    Here a session writes `sandbox_profile=none` and a role command of its own choosing
    into the subject's conf, exactly as the readonly courier permits. The next dispatch
    must refuse. The control removes the split in-process and shows the same written
    keys being read as configuration — without it, "the next dispatch refused" could be
    a fixture that never got that far.
    """

    WRITTEN = ("sandbox_profile=none\n"
               "auditor_cmd=/bin/echo pwned {prompt}\n"
               "retry_limit=9\n")

    def fixture(self):
        from tests.harness import Fixture, queue_pass, state_doc
        fx = Fixture(state_doc(queue=[queue_pass("P-01", done=False)]))
        self.addCleanup(fx.cleanup)
        return fx

    def session_writes_policy(self, fx):
        """What an agent can do today: append to a file under `audit/`."""
        conf_path = fx.audit / "orchestrator.conf"
        existing = conf_path.read_text(encoding="utf-8") if conf_path.exists() else ""
        conf_path.write_text(existing + self.WRITTEN, encoding="utf-8")
        return conf_path

    def test_the_next_dispatch_refuses_the_policy_the_session_wrote(self):
        fx = self.fixture()
        before = fx.run("next", "--dry-run")
        self.assertEqual(0, before[0], f"the fixture could not dispatch to begin with:\n"
                                       f"{before[1]}")
        self.session_writes_policy(fx)
        code, out = fx.run("next", "--dry-run")
        print(f"\n  WITNESS  after the session wrote {len(self.WRITTEN.splitlines())} "
              f"policy lines into audit/orchestrator.conf:\n"
              f"    exit={code}\n    {out.strip().splitlines()[0]}")
        self.assertNotEqual(0, code, "the dispatch proceeded under policy the subject "
                                     "wrote for itself")
        for k in ("sandbox_profile", "auditor_cmd", "retry_limit"):
            self.assertIn(k, out)
        self.assertNotIn("pwned", out.split("Write exactly these lines")[0],
                         "the value was executed rather than reported")

    def test_control_without_the_split_the_written_keys_are_read_as_configuration(self):
        """CONTROL. `load_conf` with the refusal removed in-process — restored from a
        saved reference, never by `git checkout`, since the tree is uncommitted here.

        It asserts what the OLD build did: the value the subject wrote for itself comes
        back out of `load_conf` as the command to run.
        """
        fx = self.fixture()
        self.session_writes_policy(fx)
        saved = policy.policy_keys_in
        util.policy_keys_in = lambda mapping: []      # the split, removed
        try:
            conf = util.load_conf(fx.audit, profile=fx.profile)
        finally:
            util.policy_keys_in = saved
        print(f"  CONTROL  with the split removed, load_conf returns "
              f"auditor_cmd={conf.get('auditor_cmd')!r} "
              f"sandbox_profile={conf.get('sandbox_profile')!r} "
              f"retry_limit={conf.get('retry_limit')!r}")
        self.assertEqual("/bin/echo pwned {prompt}", conf.get("auditor_cmd"),
                         "the control did not reproduce the loop, so the witness above "
                         "proves nothing")
        self.assertEqual("none", conf.get("sandbox_profile"))
        self.assertEqual(9, conf.get("retry_limit"))

    def test_the_child_never_learns_where_the_profile_is(self):
        """A session that cannot find the profile cannot read the terms of its own
        audit. `XCHECK_*` is copied into the child wholesale by design; this name is
        the declared exception."""
        runner = xcheck_submodule("runner")
        saved = os.environ.get(policy.OPERATOR_PROFILE_ENV)
        os.environ[policy.OPERATOR_PROFILE_ENV] = "/tmp/some/operator.conf"
        os.environ["XCHECK_SESSION_ID"] = "0123456789abcdef"
        try:
            env = runner.child_environment({"env_allowlist": ""})
        finally:
            os.environ.pop("XCHECK_SESSION_ID", None)
            if saved is None:
                os.environ.pop(policy.OPERATOR_PROFILE_ENV, None)
            else:
                os.environ[policy.OPERATOR_PROFILE_ENV] = saved
        self.assertIn("XCHECK_SESSION_ID", env, "the control failed: no XCHECK_* var "
                                                "reached the child at all, so the "
                                                "absence below means nothing")
        self.assertNotIn(policy.OPERATOR_PROFILE_ENV, env)


class TheEnvelopeRecordsWhichPolicyRan(unittest.TestCase):

    def test_policy_digest_reaches_the_dispatch_record_and_the_event(self):
        envelope = xcheck_submodule("envelope")
        tmp = Path(tempfile.mkdtemp(prefix="xcheck-policy-digest-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        prof = tmp / "operator.conf"
        prof.write_text(policy.OPERATOR_PROFILE_TEMPLATE, encoding="utf-8")
        saved = os.environ.get(policy.OPERATOR_PROFILE_ENV)
        os.environ[policy.OPERATOR_PROFILE_ENV] = str(prof)
        try:
            runner = xcheck_submodule("runner")
            rec = envelope.dispatch_record(
                ["true", "x"], "Auditor", "charter", "prompt", "a" * 16,
                runner.PROFILES["readonly"], 1, head_before="0" * 40)
        finally:
            if saved is None:
                os.environ.pop(policy.OPERATOR_PROFILE_ENV, None)
            else:
                os.environ[policy.OPERATOR_PROFILE_ENV] = saved
        want = policy.profile_digest(prof)
        print(f"\n  ENVELOPE  policy_digest={rec['policy_digest'][:16]}…  "
              f"profile sha256={want[:16]}…")
        self.assertEqual(want, rec["policy_digest"])
        payload = envelope._event_payload(rec)
        self.assertEqual(want, payload["policy_digest"],
                         "the digest is on the record but never reaches the stream")

    def test_the_field_is_optional_in_the_closed_session_schema(self):
        """Both halves asked directly. A session recorded before the field existed must
        still load — refusing those would rewrite already-admitted evidence."""
        state = xcheck_submodule("state")
        rec = {"session_id": "a" * 16, "role": "Auditor", "provider": "p",
               "agent_model": "m", "executable": "x", "executable_version": "v",
               "charter_hash": "0" * 64, "prompt_hash": "0" * 64, "state_revision": 1,
               "head_before": "unknown", "sandbox_profile": "readonly", "started": "t"}
        state._check_record(dict(rec, policy_digest="d" * 64),
                            state.SESSION_FIELDS, "sessions[0]")
        state._check_record(rec, state.SESSION_FIELDS, "sessions[0]")
        with self.assertRaises(state.StateError):
            state._check_record(dict(rec, policy_digestt="x"),
                                state.SESSION_FIELDS, "sessions[0]")


class TheShippedExampleIsTheTemplate(unittest.TestCase):
    """A second copy of the profile is a second thing to keep current. The example an
    operator copies must be the template the refusal prints, byte for byte."""

    def test_the_example_file_matches_the_template(self):
        example = REPO / "docs" / "operator.conf.example"
        self.assertTrue(example.is_file(), "the shipped example profile is missing")
        self.assertEqual(policy.OPERATOR_PROFILE_TEMPLATE,
                         example.read_text(encoding="utf-8"),
                         "docs/operator.conf.example has drifted from "
                         "policy.OPERATOR_PROFILE_TEMPLATE — regenerate it rather than "
                         "editing one of the two")

    def test_the_example_loads_as_a_valid_profile(self):
        # The shipped example lives in this repository as DOCUMENTATION, so the project
        # it is validated against is deliberately not this one: what is measured here is
        # the example's vocabulary, and its location is measured in test_policy_location.
        raw = policy.load(REPO / "docs" / "operator.conf.example", REPO / "no-such-subject")
        self.assertEqual([], [k for k in raw if policy.classify(k) != "policy"])


class TheProfileRefusesAmbiguity(unittest.TestCase):
    """Phase 2, third audit: a duplicate key and a misspelled role.

    Both were accepted. `sandbox_profile=container` followed by `sandbox_profile=none`
    loaded as `none` — last-wins, which for a policy deciding what runs and inside what
    boundary is a silent downgrade one careless concatenation away. And any key ending
    `_cmd` counted as a role command, so `auditro_cmd=...` was a policy key that named no
    role: the operator believes they configured the Auditor, and nothing did.
    """

    def setUp(self):
        self.box = Path(tempfile.mkdtemp(prefix="xcheck-amb-"))
        self.addCleanup(lambda: shutil.rmtree(self.box, ignore_errors=True))
        self.project = self.box / "subject"
        self.project.mkdir()

    def load(self, text, name="p.conf"):
        f = self.box / name
        f.write_text(text, encoding="utf-8")
        return policy.load(f, self.project)

    def refusal(self, text, name="p.conf"):
        with self.assertRaises(policy.PolicyError) as cm:
            self.load(text, name)
        return str(cm.exception)

    def test_a_duplicate_key_is_refused_naming_both_values(self):
        msg = self.refusal("sandbox_profile=container\nsandbox_profile=none\n")
        print(f"\n  DUPLICATE  {msg.splitlines()[0][msg.splitlines()[0].find('duplicate'):][:120]}")
        self.assertIn("duplicate key 'sandbox_profile'", msg)
        self.assertIn("'container'", msg)
        self.assertIn("'none'", msg)
        self.assertIn("line 1", msg)

    def test_the_rule_is_every_key_not_the_security_ones(self):
        """A loader that ranks its own keys is a loader that will misrank one."""
        for key, a, b in (("retry_limit", "0", "9"),
                          ("session_timeout", "60", "3600"),
                          ("planner_cmd", "a {prompt}", "b {prompt}")):
            with self.subTest(key=key):
                self.assertIn("duplicate key", self.refusal(f"{key}={a}\n{key}={b}\n"))
        print("  DUPLICATE  refused for a limit and a role command too, not only containment")

    def test_whitespace_does_not_make_two_keys_one_of_them(self):
        """Checked on the raw key before normalisation, or ` k ` sneaks past `k`."""
        self.assertIn("duplicate key",
                      self.refusal("sandbox_profile=container\n  sandbox_profile  =none\n"))
        print("  DUPLICATE  `k` and ` k ` collide")

    def test_a_misspelled_role_command_is_refused_and_a_near_miss_is_named(self):
        msg = self.refusal("auditro_cmd=/bin/true {prompt}\n")
        print(f"  UNKNOWN    {[l.strip() for l in msg.splitlines() if 'did you mean' in l]}")
        self.assertIn("auditro_cmd", msg)
        self.assertIn("auditor_cmd?", msg)

    def test_an_invented_role_command_is_not_a_policy_key(self):
        """The `_cmd` suffix used to BE the rule, so this key was silently accepted."""
        self.assertEqual("unknown", policy.classify("shrubber_cmd"))
        self.assertIn("shrubber_cmd", self.refusal("shrubber_cmd=/bin/true {prompt}\n"))
        print("  UNKNOWN    an invented *_cmd key is refused, not classified as policy")

    def test_registering_a_role_is_one_explicit_place(self):
        """The registry is derived from the roles the orchestrator can dispatch, and
        `runner` looks a command up by exactly that derivation."""
        self.assertEqual({f"{r.lower()}_cmd" for r in policy.DISPATCHABLE_ROLES},
                         set(policy.ROLE_COMMAND_KEYS))
        write = xcheck_submodule("write")
        routed = set(write.ROLE_ROUTES)
        dispatchable = set(policy.DISPATCHABLE_ROLES)
        self.assertTrue(dispatchable <= routed,
                        f"a role has a command but no write routes: {dispatchable - routed}")
        self.assertEqual({"Triage"}, routed - dispatchable,
                         "a role has write routes but no command — legitimate ONLY for "
                         "Triage, which is the human gate the orchestrator stops FOR and "
                         "never dispatches. Any other name here is a role the tool can "
                         "route writes for and cannot launch.")
        print(f"  REGISTRY   dispatchable {sorted(dispatchable)}; "
              f"routed-but-never-dispatched {sorted(routed - dispatchable)}")

    def test_control_a_legitimate_profile_is_unchanged(self):
        """Without this, "refuses ambiguity" is satisfied by refusing everything."""
        raw = self.load("planner_cmd=a {prompt}\nauditor_cmd=b {prompt}\n"
                        "remediator_cmd=c {prompt}\nverifier_cmd=d {prompt}\n"
                        "sandbox_profile=container\nretry_limit=0\n", "ok.conf")
        self.assertEqual(["auditor_cmd", "planner_cmd", "remediator_cmd", "retry_limit",
                          "sandbox_profile", "verifier_cmd"], sorted(raw))
        shipped = policy.load(REPO / "docs" / "operator.conf.example",
                              REPO / "no-such-subject")
        self.assertIn("auditor_cmd", shipped)
        print(f"  CONTROL    a clean profile loads ({len(raw)} keys), and the shipped "
              f"example still loads ({len(shipped)} keys)")
