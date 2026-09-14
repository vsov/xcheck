"""0.9.1 phase 9: the container image digest pin is an invariant, not a recommendation.

Every row of the adversarial escape matrix in `SECURITY.md` was measured against
*specific bytes*. `alpine/git:latest` is whatever the registry serves today, so a
mutable reference makes the whole table a claim about an image nobody can name. 0.9.0
said "PIN IT BY DIGEST" in a comment. A comment is not a gate.

Three things are proved here, and they are deliberately separate:

* **The refusal fires**, on five shapes of unpinned reference, each named.
* **The waiver reaches the LOG.** Criterion 2 is specifically not about console output:
  a console scrolls away, and the operator who has to know the guarantee was dropped is
  reading the log file a month later. `run_child` opens the log with `"w"`, which
  truncates — so a waiver written to that path beforehand would be erased by the very
  session it is about. The test reads the file off disk, after a real `run_session`.
* **The waiver widens NOTHING else.** The last time a containment knob was added it
  quietly revoked the per-role read-only default, because one enum was carrying two
  meanings ([[containment-profile-is-not-authorization]]). So the waived and pinned
  argv are compared token for token with only the image allowed to differ, the resolved
  profiles are compared, and `CONF_DEFAULTS` is compared against a recorded snapshot.

The docker-backed arms name their skip and are counted; `TheBackendArmsAreCounted`
prints whether they ran, because absence must never read as a pass.
"""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.harness import complete_conf, xcheck_submodule

runner = xcheck_submodule("runner")
util = xcheck_submodule("util")

DOCKER_OK, DOCKER_WHY = runner.backend_probe("docker")
SKIP_REASON = (f"this arm needs a working docker backend and {DOCKER_WHY}. The pin "
               f"refusal, the malformed-digest cases and the waiver-in-the-log arm "
               f"still ran — they are docker-free by construction.")
_SKIPPED = []

PINNED = util.CONTAINER_IMAGE
DIGEST = PINNED.split("@", 1)[1]                  # sha256:<64 hex>, the real one

# The five shapes that are not a pin. Each is a (label, reference, expected phrase).
UNPINNED = [
    ("a bare tag", "alpine/git:latest", "names no digest at all"),
    ("no tag at all", "alpine/git", "names no digest at all"),
    ("wrong algorithm", "alpine/git@md5:" + "ab" * 16, "digest algorithm is 'md5'"),
    ("wrong length", "alpine/git@sha256:abc123", "not the 64 lowercase hex"),
    ("upper-cased hex", "alpine/git@sha256:" + "AB" * 32, "not the 64 lowercase hex"),
    ("a digest on a tag", "alpine/git:3.19@" + DIGEST, "BOTH a tag"),
]

# A fake `docker` that runs the entrypoint on the host. It exists so the waiver-in-log
# arm needs no daemon and pulls no image — what that arm measures is xcheck's own
# plumbing (does the sentence survive the log being opened for writing?), and a real
# daemon would only add a network fetch of a mutable tag to the same question.
#
# It reads the argv the way docker does: `--entrypoint PROG ... IMAGE ARGS...`.
DOCKER_SHIM = r"""#!/bin/sh
case "$1" in
  info) echo "shim-1.0"; exit 0 ;;
  rm)   exit 0 ;;
  run)  ;;
  *)    exit 0 ;;
esac
shift
prog=""
while [ $# -gt 0 ]; do
  if [ "$1" = "--entrypoint" ]; then prog="$2"; shift 3; break; fi
  shift
done
[ -n "$prog" ] || { echo "shim: no --entrypoint in argv" >&2; exit 2; }
exec "$prog" "$@"
"""


class ThePinIsEnforced(unittest.TestCase):
    """Criteria 1 and 5. No docker: this is a config gate, and it refuses at the door —
    before a daemon is asked for anything."""

    maxDiff = None

    def test_an_unpinned_image_refuses_naming_the_image_and_the_remedy(self):
        conf = complete_conf(sandbox_profile="container",
                             container_image="alpine/git:latest")
        with self.assertRaises(SystemExit) as cm:
            runner.container_image_ref(conf)
        msg = str(cm.exception)
        print("\n  the refusal, verbatim:\n    " + msg.replace("\n", "\n    "))
        self.assertIn("alpine/git:latest", msg, "the refusal must name the image")
        self.assertIn("docker inspect", msg, "the refusal must name the remedy")
        self.assertIn("RepoDigests", msg)
        self.assertIn("unsafe_allow_unpinned_image=on", msg,
                      "an operator who has decided must be told the one way through")

    def test_the_five_unpinned_shapes_each_refuse_by_name(self):
        for label, ref, phrase in UNPINNED:
            with self.subTest(shape=label):
                conf = complete_conf(sandbox_profile="container", container_image=ref)
                with self.assertRaises(SystemExit) as cm:
                    runner.container_image_ref(conf)
                msg = str(cm.exception)
                print(f"  {label:<20} {ref[:46]:<48} -> {phrase}")
                self.assertIn(phrase, msg)
                self.assertIn(ref, msg)

    def test_a_registry_port_is_not_mistaken_for_a_tag(self):
        """`host:5000/team/img@sha256:…` is pinned. The colon is in the HOST.

        A tag check that scans the whole reference for `:` rejects every private
        registry on earth, and the operator's fix would be to waive the pin — the
        gate would have argued them out of the guarantee it exists to keep."""
        ref = f"registry.example.com:5000/team/img@{DIGEST}"
        self.assertIsNone(runner.unpinned_reason(ref), ref)
        image, waiver = runner.container_image_ref(
            complete_conf(sandbox_profile="container", container_image=ref))
        self.assertEqual(ref, image)
        self.assertIsNone(waiver)

    def test_the_shipped_default_is_pinned(self):
        self.assertIsNone(runner.unpinned_reason(PINNED))
        self.assertIsNone(runner.container_image_ref(complete_conf())[1])

    def test_the_waiver_permits_it_and_says_what_it_costs(self):
        conf = complete_conf(sandbox_profile="container",
                             container_image="alpine/git:latest",
                             unsafe_allow_unpinned_image="on")
        image, waiver = runner.container_image_ref(conf)
        self.assertEqual("alpine/git:latest", image)
        self.assertIsNotNone(waiver)
        print("\n  the waiver sentence:\n    " + waiver)
        self.assertIn("WAIVED", waiver)
        self.assertIn("alpine/git:latest", waiver)

    def test_the_capability_report_says_pinned_only_when_it_is(self):
        """`"@sha256:" in image` answered True for a short digest and for a tag with a
        digest hung off it — the report would have said pinned about a reference the
        launch path now refuses."""
        good = runner.container_capabilities(complete_conf())
        self.assertTrue(good["image_pinned_by_digest"])
        self.assertFalse(good["image_pin_waived"])
        bad = runner.container_capabilities(
            complete_conf(container_image="alpine/git@sha256:abc123",
                          unsafe_allow_unpinned_image="on"))
        self.assertFalse(bad["image_pinned_by_digest"],
                         "a truncated digest contains '@sha256:' and is not a pin")
        self.assertTrue(bad["image_pin_waived"])

    def test_the_argv_builder_refuses_too_not_only_the_preflight(self):
        """Two gates, because the argv builder is reachable on its own.

        A refusal that lives only in the preflight is a refusal a caller can walk
        past — and `container_argv` is public, called by `Sandbox.wrap` and by tests."""
        with self.assertRaises(SystemExit):
            runner.container_argv(["sh", "-c", "true"],
                                  complete_conf(container_image="alpine/git:latest"),
                                  "/tmp/w", "/tmp/o", "/tmp/s")


class TheWaiverReachesTheLog(unittest.TestCase):
    """Criterion 2, read from the FILE.

    `run_child` opens the log with `"w"`. That truncates — so the interesting failure
    is not "the sentence was never composed" but "the sentence was composed, printed,
    and then erased by the session it describes". Only reading the file after the run
    tells those apart."""

    maxDiff = None

    def bin_with_shim(self):
        d = Path(tempfile.mkdtemp(prefix="xcheck-dockershim-"))
        self.addCleanup(shutil.rmtree, str(d), ignore_errors=True)
        shim = d / "docker"
        shim.write_text(DOCKER_SHIM, encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return d

    def project(self):
        d = Path(tempfile.mkdtemp(prefix="xcheck-pinproj-"))
        self.addCleanup(shutil.rmtree, str(d), ignore_errors=True)
        (d / "audit").mkdir()
        (d / "audit" / "XCHECK.md").write_text("# XCHECK\n", encoding="utf-8")
        (d / "material.txt").write_text("original\n", encoding="utf-8")
        for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                     ["config", "user.name", "test"], ["add", "-A"],
                     ["commit", "-qm", "base"]):
            subprocess.run(["git", *args], cwd=str(d), capture_output=True,
                           timeout=60, check=True)
        return d

    def session(self, **over):
        """One real session under the container profile, through the shim.

        `Sandbox.enter()`, `Sandbox.wrap()` and `run_child()` are the real ones — the
        only substitution is the `docker` binary itself, and what this arm measures is
        entirely on xcheck's side of that line."""
        project = self.project()
        conf = complete_conf(sandbox_profile="container", **over)
        profile = runner.PROFILES["container"]
        path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.bin_with_shim()}{os.pathsep}{path}"
        self.addCleanup(os.environ.__setitem__, "PATH", path)
        log = Path(tempfile.mkdtemp(prefix="xcheck-pinlog-")) / "session.log"
        self.addCleanup(shutil.rmtree, str(log.parent), ignore_errors=True)
        sandbox = runner.Sandbox(project, profile, "Remediator")
        sandbox.enter()
        try:
            env = runner.child_environment(conf, {})
            launch = sandbox.wrap(["sh", "-c", "echo THE-SESSION-SPOKE"], conf, env)
            rc, outcome, _ = runner.run_child(
                launch, sandbox.workdir, log, env, conf, profile=profile,
                header=[f"xcheck: {sandbox.image_waiver}"] if sandbox.image_waiver
                else ())
        finally:
            sandbox.leave()
        return rc, outcome, sandbox.image_waiver, log.read_text(encoding="utf-8")

    def test_the_waiver_is_in_the_log_file_and_the_session_did_not_erase_it(self):
        rc, outcome, waiver, text = self.session(
            container_image="alpine/git:latest", unsafe_allow_unpinned_image="on")
        print("\n  log file, read back off disk after the session:")
        for line in text.splitlines():
            print(f"    | {line}")
        self.assertEqual(0, rc)
        self.assertEqual("ok", outcome)
        self.assertIsNotNone(waiver)
        first = text.splitlines()[0]
        self.assertTrue(first.startswith("xcheck: WAIVED"), first)
        self.assertIn("alpine/git:latest", first)
        self.assertIn("THE-SESSION-SPOKE", text,
                      "the child must have run AFTER the header — a waiver in an "
                      "empty log would mean the session never started")

    def test_a_pinned_run_writes_no_waiver_anywhere_in_the_log(self):
        """The counterfactual. A header that is always written is not evidence of
        anything: the log has to be silent when there is nothing to confess."""
        rc, outcome, waiver, text = self.session()
        print(f"  pinned run — waiver={waiver!r}, log={text.strip()!r}")
        self.assertEqual(0, rc)
        self.assertIsNone(waiver)
        self.assertNotIn("WAIVED", text)
        self.assertEqual("THE-SESSION-SPOKE", text.strip())

    def test_an_unpinned_run_without_the_waiver_never_reaches_a_log(self):
        with self.assertRaises(SystemExit) as cm:
            self.session(container_image="alpine/git:latest")
        self.assertIn("not pinned by digest", str(cm.exception))


class TheWaiverIsPinningAndNothingElse(unittest.TestCase):
    """Criteria 3 and 4 — the ones that matter more than they look.

    `containment-profile-is-not-authorization`: adding `sandbox_profile=container`
    once revoked the per-role read-only default, because a single enum was answering
    two unrelated questions. A new key gets checked against that failure by
    construction, not by intent."""

    maxDiff = None

    # Keys added AFTER this phase. The snapshot below is the pre-phase-9 default set,
    # kept as a fixed baseline: a later phase adds its key here and the comparison
    # keeps meaning "nothing else moved" instead of being re-recorded from the code it
    # is supposed to be checking ([[verifiable-claims-check-structure-not-self-counts]]).
    # `output_deadline` was added by that phase as `first_action_deadline` and RENAMED
    # in phase 8 of the third audit — it counts bytes, and the old name promised
    # semantic progress detection. `activity_deadline` is the new bound that makes the
    # promise the old name made. Both are listed by the names the code uses today: this
    # tuple names keys that exist, and a stale spelling here would exempt nothing.
    LATER = ("egress_allowlist", "egress_uplink", "egress_broker_image",
             "embargo", "embargo_after", "audit_over_critical",
             "startup_deadline", "output_deadline", "activity_deadline",
             "idle_deadline", "log_dir", "log_retention_days", "telemetry_adapter",
             "cheap_model", "strong_model",
             # phase 12/13 (third audit) added `diff_scope` and `evidence_cache`;
             # phase 2 of the FOURTH audit withdrew both — they were public keys over
             # libraries no production path called. A withdrawn key is not a LATER key:
             # this tuple names keys that exist, and one that does not would exempt
             # nothing. `policy.WITHDRAWN_KEYS` is where they are now accounted for.
             "evidence_bundle",
             # phase 14 (third audit): model routing, off by default.
             "model_routing",
             # phase 15 (third audit): the budget ceilings. The flag is off and every
             # ceiling is 0, which means off — a project already inside them is unaffected.
             "budgets", "tokens_per_session", "tokens_per_pass", "tokens_per_audit",
             "no_progress_share_pct", "charter_repeat_limit", "marginal_value_window",
             # phase 16 (third audit): the derived knowledge bundle, off by default.
             "okf")

    # A later phase may also CHANGE a default, and that is a different act from adding
    # one: the snapshot must not be quietly re-recorded from the code it checks. Each
    # entry is declared here with the value it had, the value it has, and why — so the
    # comparison still means "nothing else moved" and every deliberate move is named.
    CHANGED_LATER = {
        # phase 8 (backpressure): unbounded triage debt is what produced this project's
        # own 133 reported / 0 triaged corpus.
        "triage_batch_cap": ("0", "20"),
    }

    def test_the_defaults_are_byte_identical_apart_from_the_new_key(self):
        before = dict(util.CONF_DEFAULTS)
        for k in self.LATER:
            before.pop(k, None)
        added = before.pop("unsafe_allow_unpinned_image", "<<missing>>")
        for key, (was, now) in self.CHANGED_LATER.items():
            self.assertEqual(now, util.CONF_DEFAULTS[key],
                             f"{key} no longer holds the value this pin declares it "
                             f"was changed to; declare the new change or revert it")
            before[key] = was
        recorded = {
            "batch_size": "8", "max_sessions": "20", "loop_progress_limit": "2",
            "session_note": "", "triage_batch_cap": "0", "push_after_commit": "",
            "scope_typing": "off", "construal_gate": "off", "sandbox_profile": "",
            "env_allowlist": "", "session_timeout": "3600", "kill_grace": "10",
            "cpu_seconds": "0", "address_space_mb": "0", "lease_ttl": "300",
            "container_image": util.CONTAINER_IMAGE, "container_cpus": "2",
            "container_memory_mb": "2048", "container_pids": "256",
            "parallel_passes": "off", "parallel_workers": "2",
            "max_sessions_per_run": "8", "parallel_confirm_above": "4",
            "parallel_budget_minutes": "480", "retry_limit": "0",
        }
        print(f"\n  defaults before phase 9: {len(recorded)}; now: "
              f"{len(util.CONF_DEFAULTS)}; this phase added "
              f"unsafe_allow_unpinned_image={added!r}; later phases added "
              f"{', '.join(self.LATER)}; later phases CHANGED "
              f"{', '.join(f'{k} {w}->{n}' for k, (w, n) in self.CHANGED_LATER.items())}")
        self.assertEqual("off", added, "the new key must default to OFF")
        self.assertEqual(recorded, before,
                         "this phase may add a key and change NO other default")

    def test_the_resolved_profiles_are_unchanged_with_the_waiver_on(self):
        """Criterion 4. The read-only roles stay read-only; the writing roles stay
        `worktree`. A pinning knob has no opinion about who may write."""
        rows = {}
        for role in ("Auditor", "Verifier", "Remediator", "Planner", "Triage"):
            off = runner.resolve_profile(complete_conf(), role)
            on = runner.resolve_profile(
                complete_conf(unsafe_allow_unpinned_image="on"), role)
            rows[role] = (off.name, on.name, off.material_writable,
                          on.material_writable)
            self.assertEqual(off.name, on.name, role)
            self.assertEqual(off.material_writable, on.material_writable, role)
        for role, (a, b, wa, wb) in sorted(rows.items()):
            print(f"  {role:<11} waiver off: {a:<9} writable={wa!s:<5} "
                  f"| waiver on: {b:<9} writable={wb}")

    def test_the_argv_differs_in_the_image_and_in_nothing_else(self):
        """Token for token. The network, the mounts, the dropped capabilities and the
        limits are the same argv under a waived reference as under a pinned one."""
        base = complete_conf(sandbox_profile="container")
        waived = complete_conf(sandbox_profile="container",
                               container_image="alpine/git:latest",
                               unsafe_allow_unpinned_image="on")
        a = runner.container_argv(["sh", "-c", "true"], base, "/tmp/w", "/tmp/o",
                                  "/tmp/s", name="fixed")
        b = runner.container_argv(["sh", "-c", "true"], waived, "/tmp/w", "/tmp/o",
                                  "/tmp/s", name="fixed")
        diff = [(x, y) for x, y in zip(a, b) if x != y]
        print(f"  argv tokens: {len(a)} vs {len(b)}; differing: {diff}")
        self.assertEqual(len(a), len(b))
        self.assertEqual([(PINNED, "alpine/git:latest")], diff)
        self.assertIn("--network=none", b)
        self.assertIn("ALL", b)
        self.assertEqual([], runner.host_socket_mounts(b))

    def test_the_waiver_does_not_touch_the_capability_report_beyond_the_pin(self):
        pinned = runner.container_capabilities(complete_conf(), verified="probe")
        waived = runner.container_capabilities(
            complete_conf(container_image="alpine/git:latest",
                          unsafe_allow_unpinned_image="on"), verified="probe")
        moved = {k for k in pinned if pinned[k] != waived[k]}
        print(f"  capability keys that moved: {sorted(moved)}")
        self.assertEqual({"image", "image_pinned_by_digest", "image_pin_waived"}, moved)


class TheBackendArmsAreCounted(unittest.TestCase):
    """Criterion 6. The real-daemon arm, and the counter-assertion that says whether it
    ran — absence must never read as a pass."""

    def test_a_real_docker_refuses_an_unpinned_launch_before_it_pulls_anything(self):
        if not DOCKER_OK:
            _SKIPPED.append("real-daemon refusal")
            self.skipTest(SKIP_REASON)
        d = Path(tempfile.mkdtemp(prefix="xcheck-pinreal-"))
        self.addCleanup(shutil.rmtree, str(d), ignore_errors=True)
        (d / "audit").mkdir()
        for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                     ["config", "user.name", "test"], ["commit", "-qm", "base",
                                                       "--allow-empty"]):
            subprocess.run(["git", *args], cwd=str(d), capture_output=True,
                           timeout=60, check=True)
        conf = complete_conf(sandbox_profile="container",
                             container_image="xcheck-no-such-image:latest")
        sandbox = runner.Sandbox(d, runner.PROFILES["container"], "Remediator")
        sandbox.enter()
        try:
            with self.assertRaises(SystemExit) as cm:
                sandbox.wrap(["sh", "-c", "true"], conf, {})
        finally:
            sandbox.leave()
        print(f"\n  real docker ({DOCKER_WHY}): the launch refused before any pull — "
              f"{str(cm.exception).splitlines()[0]}")
        self.assertIn("not pinned by digest", str(cm.exception))
        self.assertIsNone(sandbox.image_waiver)

    def test_zz_report_whether_the_backend_arms_ran(self):
        if DOCKER_OK:
            print(f"\n  docker backend: {DOCKER_WHY} — 0 arms skipped; the real-daemon "
                  f"refusal ran against a live docker.")
            return
        print(f"\n  docker backend UNAVAILABLE: {DOCKER_WHY}."
              f"\n  {len(_SKIPPED)} arm(s) SKIPPED: {', '.join(_SKIPPED) or 'none'}."
              f"\n  The pin gate itself was measured (it is config, not daemon), but "
              f"nothing here observed a real docker refuse. That is UNMEASURED on this "
              f"machine, not confirmed.")


if __name__ == "__main__":
    unittest.main()
