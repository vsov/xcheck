"""The `container` profile, tested by ESCAPE, not by flag inspection.

Reading the argv xcheck builds and asserting `--network=none` is in it proves that
xcheck can spell a docker flag. It does not prove the child cannot open a socket. The
difference matters here more than anywhere else in the suite, because this profile's
entire claim is that the OPERATING SYSTEM enforces the boundary — a claim only the
operating system can settle.

So every test in `TheEscapesAreHeld` runs one escape attempt twice, in the same method:

  - under `container`, where it must FAIL;
  - under `worktree`, where it must SUCCEED.

The second arm is not decoration. A probe that can never fire proves nothing when it
does not fire, and the previous run of this project has already been bitten by exactly
that (`mutation-harness-needs-its-own-control`). The worktree arm is what shows the
probe is live — and, incidentally, it is the audit's finding reproduced as a test: the
worktree isolates the repository, not the machine, so from inside one the child reads
the operator's `$HOME`, opens sockets, and writes the original checkout that the
courier believes it is mediating.

Where docker is absent — GitHub's macOS runners have none — the container arms SKIP
with a stated reason and the count is printed. The fail-closed test still runs there,
because "docker is missing" is precisely its subject.
"""

import os
import shutil
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.harness import complete_conf, xcheck_submodule

runner = xcheck_submodule("runner")
util = xcheck_submodule("util")
policy = xcheck_submodule("policy")

DOCKER_OK, DOCKER_WHY = runner.backend_probe("docker")
SKIP_REASON = (f"the container arm needs a working docker backend and {DOCKER_WHY}. "
               f"The worktree control arm and the fail-closed refusal still ran; the "
               f"container guarantees are UNMEASURED on this machine, not confirmed.")
_SKIPPED = []


class SandboxCase(unittest.TestCase):
    """A real git project, and one helper that runs a shell script inside a real
    sandbox of the named profile through the real `wrap()`."""

    maxDiff = None

    def project(self):
        d = Path(tempfile.mkdtemp(prefix="xcheck-container-proj-"))
        self.addCleanup(shutil.rmtree, str(d), ignore_errors=True)
        (d / "audit").mkdir()
        (d / "audit" / "XCHECK.md").write_text("# XCHECK\n", encoding="utf-8")
        (d / "material.txt").write_text("original\n", encoding="utf-8")
        for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                     ["config", "user.name", "test"], ["add", "-A"],
                     ["commit", "-qm", "base"]):
            subprocess.run(["git", *args], cwd=str(d), capture_output=True, timeout=60,
                           check=True)
        return d

    def run_in(self, profile_name, script, project=None, conf=None, extra_env=None,
               timeout=180):
        """Run `sh -c script` inside a sandbox of `profile_name`. Returns `(rc, out)`.

        The launch goes through `Sandbox.enter()` and `Sandbox.wrap()` — the same two
        calls `run_session` makes — so what is measured is the real argv this profile
        builds, not a hand-written docker line that happens to agree with it."""
        project = project or self.project()
        conf = conf or complete_conf(sandbox_profile=profile_name)
        profile = runner.PROFILES[profile_name]
        sandbox = runner.Sandbox(project, profile, "Remediator")
        sandbox.enter()
        try:
            env = runner.child_environment(conf, extra_env or {})
            cmd = sandbox.wrap(["sh", "-c", script], conf, env)
            p = subprocess.run(cmd, cwd=str(sandbox.workdir), env=env, text=True,
                               capture_output=True, timeout=timeout)
            return p.returncode, (p.stdout + p.stderr).strip()
        finally:
            sandbox.leave()


class TheEscapesAreHeld(SandboxCase):

    def both_arms(self, script, extra_env=None):
        """The container arm and its worktree control, from ONE script.

        Same text in both, so any difference in the result is the containment and not
        the probe. The container arm is skipped (loudly) without a backend; the
        control arm always runs, because a control that does not run is the failure
        mode this whole file is written against."""
        control = self.run_in("worktree", script, extra_env=extra_env)
        if not DOCKER_OK:
            _SKIPPED.append(self.id().rsplit(".", 1)[-1])
            print(f"\n  SKIP (container arm) {self.id().rsplit('.', 1)[-1]}: "
                  f"{SKIP_REASON}\n  worktree control arm ran: {control}")
            self.skipTest(SKIP_REASON)
        return self.run_in("container", script, extra_env=extra_env), control

    def show(self, name, contained, control):
        print(f"\n  escape probe: {name}"
              f"\n    container : rc={contained[0]} {contained[1]!r}"
              f"\n    worktree  : rc={control[0]} {control[1]!r}")

    def test_a_child_cannot_write_the_source_checkout(self):
        """The audit's finding, as a test. From a live worktree the child appended to
        the ORIGINAL checkout and `git status` there showed the change — the courier,
        which only ever sees the patch that comes back, was never consulted."""
        script = (
            'if echo escaped >> "$XCHECK_HOSTSRC/material.txt" 2>/dev/null; '
            'then echo "ESCAPED:host-path"; else echo "held:host-path"; fi; '
            'if [ -d /src ]; then '
            '  if echo escaped >> /src/material.txt 2>/dev/null; '
            '  then echo "ESCAPED:mount"; else echo "held:mount"; fi; '
            'else echo "held:no-mount-visible"; fi')
        project = self.project()
        control = self.run_in("worktree", script, project=project,
                              extra_env={"XCHECK_HOSTSRC": str(project)})
        self.assertIn("ESCAPED:host-path", control[1],
                      "the worktree control arm did not escape, so this probe proves "
                      f"nothing when the container arm holds. control={control!r}")
        self.assertIn("escaped", (project / "material.txt").read_text(encoding="utf-8"),
                      "the control arm reported an escape it did not perform")
        if not DOCKER_OK:
            _SKIPPED.append("write_the_source_checkout")
            self.show("write to the source checkout", ("skipped", SKIP_REASON), control)
            self.skipTest(SKIP_REASON)
        fresh = self.project()
        contained = self.run_in("container", script, project=fresh,
                                extra_env={"XCHECK_HOSTSRC": str(fresh)})
        self.show("write to the source checkout", contained, control)
        self.assertNotIn("ESCAPED", contained[1])
        self.assertIn("held:mount", contained[1],
                      "the /src mount must EXIST and be read-only — a probe that "
                      "found no mount at all would pass for the wrong reason")
        self.assertEqual("original\n", (fresh / "material.txt").read_text(encoding="utf-8"))

    def test_a_child_cannot_open_a_network_socket(self):
        """A listener on the host's loopback, and a child asked to reach it.

        Deliberately not an internet address: a probe that needs the internet fails on
        an offline machine and reports containment that was never tested."""
        if not shutil.which("nc"):
            self.skipTest("no `nc` on the host, so the worktree control arm — the one "
                          "that must SUCCEED — cannot be run; without it the container "
                          "arm would prove nothing")
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        self.addCleanup(srv.close)
        port = srv.getsockname()[1]
        script = (f'if nc -w 3 -z 127.0.0.1 {port} 2>/dev/null; '
                  f'then echo "ESCAPED:connected"; else echo "held:refused"; fi')
        contained, control = self.both_arms(script)
        self.show(f"open a socket to 127.0.0.1:{port}", contained, control)
        self.assertIn("ESCAPED:connected", control[1],
                      "the worktree control arm could not reach the listener, so the "
                      "container arm's failure would prove nothing")
        self.assertIn("held:refused", contained[1])

    def test_git_worktree_list_reveals_nothing_outside_the_mount(self):
        """`git worktree list` is how the planning probe found the original checkout.
        Inside the container the session's tree is a CLONE, so the command can only
        name its own mount."""
        script = 'git worktree list 2>&1 || echo "held:git-failed"'
        project = self.project()
        control = self.run_in("worktree", script, project=project)
        self.assertIn(str(project), control[1],
                      "the worktree control arm did not reveal the original checkout, "
                      "so this probe is not measuring what it claims")
        if not DOCKER_OK:
            _SKIPPED.append("git_worktree_list")
            self.show("git worktree list", ("skipped", SKIP_REASON), control)
            self.skipTest(SKIP_REASON)
        fresh = self.project()
        contained = self.run_in("container", script, project=fresh)
        self.show("git worktree list", contained, control)
        self.assertNotIn(str(fresh), contained[1])
        self.assertNotIn("/Users", contained[1])
        self.assertIn(runner.CONTAINER_WORK, contained[1])

    def test_home_is_synthetic_and_empty(self):
        script = ('echo "HOME=$HOME"; '
                  'echo "entries=$(ls -A "$HOME" 2>/dev/null | wc -l | tr -d " ")"')
        contained, control = self.both_arms(script)
        self.show("read $HOME", contained, control)
        host_home = os.path.expanduser("~")
        self.assertIn(f"HOME={host_home}", control[1],
                      "the worktree control arm did not get the operator's HOME, so "
                      "the container arm's synthetic HOME would prove nothing")
        self.assertIn(f"HOME={runner.CONTAINER_HOME}", contained[1])
        self.assertNotIn(host_home, contained[1])
        self.assertIn("entries=0", contained[1],
                      "the synthetic HOME must be EMPTY — a populated one would mean a "
                      "host directory was bound in under a new name")


class TheProfileFailsClosed(SandboxCase):
    """Criterion 2, and the one test that runs identically with or without docker."""

    def hide_docker(self):
        """Make docker unfindable on PATH — not by uninstalling anything.

        The shim directory keeps `git` (symlinked to the real one) and omits docker, so
        the machine still looks normal in every way except the one under test. Emptying
        PATH outright would hide git too, and the sandbox would then refuse for a
        MISSING GIT — a green test measuring the wrong refusal, which is the exact trap
        `test-conf-is-all-or-nothing` records one file over."""
        shim = tempfile.mkdtemp(prefix="xcheck-nodocker-")
        self.addCleanup(shutil.rmtree, shim, ignore_errors=True)
        for tool in ("git", "sh", "env"):
            real = shutil.which(tool)
            if real:
                os.symlink(real, Path(shim) / tool)
        old = os.environ.get("PATH", "")
        os.environ["PATH"] = shim
        self.addCleanup(os.environ.__setitem__, "PATH", old)
        self.assertIsNotNone(shutil.which("git"), "the shim must keep git findable")
        self.assertIsNone(shutil.which("docker"))

    def test_requesting_container_without_docker_refuses_and_never_falls_back(self):
        self.hide_docker()
        ok, why = runner.backend_probe("docker")
        self.assertFalse(ok, f"docker is still reachable, so this test is not "
                             f"measuring the fail-closed path: {why}")
        with self.assertRaises(SystemExit) as caught:
            runner.require_backend(runner.PROFILES["container"], "Remediator")
        msg = str(caught.exception)
        print(f"\n  fail-closed refusal, verbatim:\n{msg}\n")
        for needle in ("docker", "none, readonly, worktree", "NOT falling back"):
            self.assertIn(needle, msg)

    def test_the_sandbox_itself_refuses_before_creating_anything(self):
        """Defence in depth: `run_session` probes at the door, and `Sandbox.enter()`
        probes again — so a caller that reaches the sandbox by another route still
        cannot get a container-named sandbox with no container in it."""
        project = self.project()
        self.hide_docker()
        sandbox = runner.Sandbox(project, runner.PROFILES["container"], "Remediator")
        with self.assertRaises(SystemExit):
            sandbox.enter()
        self.assertEqual(project, sandbox.workdir,
                         "the sandbox degraded to the project instead of refusing")
        self.assertIsNone(sandbox._tmp,
                          "the refusal must happen BEFORE any temp tree exists")


class TheLaunchArgvCarriesTheDeclaredCapabilities(SandboxCase):
    """The argv is not the proof — the escape probes are — but three properties of it
    are worth asserting directly, because no escape probe can see them: that a HOST
    SOCKET is never mounted, that the image is pinned, and that no forwarded secret is
    written onto a command line other processes can read."""

    def argv(self, **over):
        conf = complete_conf(sandbox_profile="container", **over)
        env = {"XCHECK_SESSION_ID": "a" * 16, "AWS_SECRET_ACCESS_KEY": "AKIAsecret"}
        return runner.container_argv(["claude", "-p", "go"], conf,
                                     "/tmp/work", "/tmp/out", "/tmp/src", env), conf

    def test_no_host_socket_is_ever_mounted(self):
        argv, _ = self.argv()
        self.assertEqual([], runner.host_socket_mounts(argv))
        self.assertNotIn("/var/run/docker.sock", " ".join(argv))

    def test_the_image_is_pinned_by_digest(self):
        argv, conf = self.argv()
        image = str(conf.get("container_image"))
        self.assertIn("@sha256:", image,
                      "an unpinned tag in a security feature is a supply-chain hole: "
                      "the containment story would rest on whatever the registry "
                      "serves today")
        self.assertIn(image, argv)

    def test_a_forwarded_secret_never_reaches_the_argv(self):
        """`-e NAME` (no value) makes the docker CLIENT read the value from its own
        environment. `-e NAME=VALUE` would put it in `ps` output for every user on the
        machine."""
        argv, _ = self.argv()
        self.assertNotIn("AKIAsecret", " ".join(argv))
        self.assertIn("AWS_SECRET_ACCESS_KEY", argv)

    def test_the_declared_limits_are_all_non_zero(self):
        argv, conf = self.argv()
        joined = " ".join(argv)
        cpus, mem, pids = runner.container_limits(conf)
        self.assertGreater(float(cpus), 0)
        self.assertGreater(mem, 0)
        self.assertGreater(pids, 0)
        for flag in (f"--cpus={cpus}", f"--memory={mem}m", f"--pids-limit={pids}",
                     "--network=none", "--cap-drop", "no-new-privileges"):
            self.assertIn(flag, joined)

    def test_a_zero_limit_refuses_rather_than_running_unlimited(self):
        """Two gates, both closed, and they refuse at different depths.

        `container_pids` and `container_memory_mb` are `NUMERIC_CONF` keys with a
        minimum of 1, so `conf_number` refuses a zero first, naming the key and the
        bound. `container_cpus` is fractional and therefore not a NUMERIC_CONF key, so
        it is the runner's own gate that has to catch it — and that gate is the one
        that says why zero is not a small number here."""
        with self.assertRaises(SystemExit) as numeric_gate:
            runner.container_limits(complete_conf(sandbox_profile="container",
                                                  container_pids="0"))
        self.assertIn("container_pids must be an integer >= 1",
                      str(numeric_gate.exception))
        with self.assertRaises(SystemExit) as runner_gate:
            runner.container_limits(complete_conf(sandbox_profile="container",
                                                  container_cpus="0"))
        self.assertIn("0 means UNLIMITED", str(runner_gate.exception))

    def test_only_the_clone_and_the_output_dir_are_writable(self):
        argv, _ = self.argv()
        mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
        self.assertEqual(3, len(mounts), f"exactly three mounts are declared: {mounts}")
        self.assertTrue(any(m.endswith(f"{runner.CONTAINER_SRC}:ro") for m in mounts),
                        f"the source checkout must be READ-ONLY: {mounts}")
        writable = [m for m in mounts if m.endswith(":rw")]
        self.assertEqual(2, len(writable), f"one clone, one output dir: {writable}")


class TheDefaultsAreUnchanged(unittest.TestCase):
    """Criterion 5. An operator without docker must see byte-identical behaviour to
    before this phase, so the check is a byte comparison of the resolved profile for a
    complete conf, not a reading of the code."""

    def resolved(self, conf):
        return "\n".join(
            f"{role}: {runner.resolve_profile(conf, role).name} "
            f"isolating={runner.resolve_profile(conf, role).isolating} "
            f"material_writable={runner.resolve_profile(conf, role).material_writable} "
            f"backend={runner.resolve_profile(conf, role).backend}"
            for role in ("Planner", "Auditor", "Remediator", "Verifier"))

    def test_the_resolved_profile_for_a_default_conf_is_byte_identical(self):
        before = ("Planner: worktree isolating=True material_writable=True backend=None\n"
                  "Auditor: readonly isolating=True material_writable=False backend=None\n"
                  "Remediator: worktree isolating=True material_writable=True backend=None\n"
                  "Verifier: readonly isolating=True material_writable=False backend=None")
        after = self.resolved(complete_conf())
        print(f"\n  resolved profiles for a complete DEFAULT orchestrator.conf:\n"
              f"{after}")
        self.assertEqual(before, after,
                         "phase 6 changed a DEFAULT. `container` is opt-in; an "
                         "operator who does not ask for it must get exactly what they "
                         "got before this phase existed.")

    def test_a_reading_role_never_gains_write_access_to_the_material(self):
        """Asking for stronger CONTAINMENT must not buy weaker AUTHORIZATION.

        `DEFAULT_PROFILE` only applies while `sandbox_profile` is empty, so before this
        phase an operator who set `sandbox_profile=worktree` globally already dropped
        the read-only gate for Auditor and Verifier — and adding `container` would have
        widened that hole rather than closing it. The two questions are separate: the
        profile decides WHERE the child runs, the role decides WHAT it may change."""
        for named in ("container", "worktree", "none"):
            conf = complete_conf(sandbox_profile=named)
            for role in ("Auditor", "Verifier"):
                prof = runner.resolve_profile(conf, role)
                self.assertEqual(named, prof.name,
                                 f"{role} must still get the containment that was asked "
                                 f"for by name")
                self.assertFalse(
                    prof.material_writable,
                    f"{role} under sandbox_profile={named!r} may change the material — "
                    f"the read-only gate was dropped by naming a profile")
            self.assertTrue(runner.resolve_profile(conf, "Remediator").material_writable,
                            "a writing role must keep its write access")

    def test_container_is_reachable_only_by_asking_for_it_by_name(self):
        self.assertEqual("container",
                         runner.resolve_profile(complete_conf(sandbox_profile="container"),
                                                "Remediator").name)
        self.assertNotIn("container", runner.DEFAULT_PROFILE.values())


class TheCapabilityReportIsRecorded(SandboxCase):
    """Criterion 6: the envelope stores what was ENFORCED, and the two kinds of
    profile make two different claims in it."""

    def record(self, profile_name, verified):
        envelope = xcheck_submodule("envelope")
        conf = complete_conf(sandbox_profile=profile_name)
        return envelope.dispatch_record(
            ["claude", "-p", "go"], "Remediator", "charter", "prompt", "a" * 16,
            runner.PROFILES[profile_name], 1, head_before="0" * 40, conf=conf,
            verified=verified)

    def test_a_container_dispatch_records_a_verified_capability_report(self):
        if not DOCKER_OK:
            _SKIPPED.append("capability_report")
            self.skipTest(SKIP_REASON)
        rec = self.record("container", runner.require_backend(
            runner.PROFILES["container"], "Remediator"))
        caps = rec["sandbox_details"]["capabilities"]
        print("\n  capability report from a real dispatch record:\n    "
              + "\n    ".join(f"{k}: {v}" for k, v in sorted(caps.items())))
        self.assertEqual("container", rec["sandbox_profile"])
        self.assertTrue(caps["verified_at_launch"])
        self.assertEqual("docker", caps["enforced_by"])
        self.assertEqual("none", caps["network"])
        self.assertTrue(caps["image_pinned_by_digest"])
        self.assertEqual([], caps["host_sockets_mounted"])

    def test_a_worktree_dispatch_says_containment_was_not_verified(self):
        """The honest half. A worktree profile must not be able to look like a
        contained one in the record — `verified_at_launch` is False and the report
        says the isolation is process-level."""
        caps = self.record("worktree", None)["sandbox_details"]["capabilities"]
        self.assertFalse(caps["verified_at_launch"])
        self.assertEqual("process", caps["enforced_by"])
        self.assertIn("unrestricted", caps["network"])


class TheSkipCountIsPrinted(unittest.TestCase):
    """Absence must never read as a pass. If docker was missing, say so once, loudly,
    with the number of guarantees that went UNMEASURED."""

    def test_zz_report_the_container_skips(self):
        if DOCKER_OK:
            print(f"\n  container backend: {DOCKER_WHY} — 0 container arms skipped, "
                  f"every escape probe ran under both profiles.")
            return
        print(f"\n  container backend UNAVAILABLE: {DOCKER_WHY}."
              f"\n  {len(_SKIPPED)} container arm(s) SKIPPED: {', '.join(_SKIPPED)}."
              f"\n  Their worktree control arms ran and the fail-closed refusal ran."
              f"\n  The container guarantees are UNMEASURED on this machine — that is "
              f"not the same as confirmed.")


if __name__ == "__main__":
    unittest.main()
