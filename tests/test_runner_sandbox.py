"""Phase 8: the runner's containment, proved against real child processes.

Every test here launches an actual `sh` child. That is deliberate. The property
under test is not "the function returns the right string" — it is "the operating
system did what we claimed": a grandchild really dies, a secret really never
enters the child's environment, a worktree really disappears. A mocked
`subprocess` would assert our own beliefs back to us.

The one thing NOT tested with a real child is the agent CLI itself. It is not a
controllable subject (it needs credentials, a network and minutes per run), so
the children here are synthetic scripts whose behaviour we choose: one that
outlives its timeout, one that spawns a grandchild, one that echoes a secret, one
that exits with a provider-shaped error.
"""

import ast
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.harness import REPO, xcheck_submodule

runner = xcheck_submodule("runner")
util = xcheck_submodule("util")
cli = xcheck_submodule("cli")

GIT = shutil.which("git")


def git(cwd, *args):
    return subprocess.run([GIT] + list(args), cwd=str(cwd), capture_output=True,
                          text=True, timeout=60)


def make_repo():
    """A throwaway git repo in a SYSTEM temp dir (never in the project tree)."""
    tmp = Path(tempfile.mkdtemp(prefix="xcheck-sandbox-test-"))
    (tmp / "audit").mkdir()
    (tmp / "audit" / "XCHECK.md").write_text("# XCHECK\n", encoding="utf-8")
    (tmp / "material.txt").write_text("original\n", encoding="utf-8")
    git(tmp, "init", "-q")
    git(tmp, "config", "user.email", "t@example.invalid")
    git(tmp, "config", "user.name", "test")
    git(tmp, "add", "-A")
    git(tmp, "commit", "-qm", "base")
    return tmp


def conf(**over):
    # PHASE 3: `trust_level` has no default in `CONF_DEFAULTS` and must not get one —
    # the refusal exists because the cheap assumption is `trusted`. A fixture project
    # is synthetic and trusted BY CONSTRUCTION, so it makes the operator's declaration
    # explicitly. `tests/test_trust_level.py` is where the unset case is exercised.
    raw = dict(util.CONF_DEFAULTS)
    raw.setdefault("trust_level", "trusted")
    raw.update({k: str(v) for k, v in over.items()})
    return util.Conf(raw)


class TheWorktreeIsDisposableAndOutsideTheProject(unittest.TestCase):
    """The default profile must run the child somewhere else, and must clean up on
    BOTH exits — a sandbox that leaks a worktree per session is a disk leak the
    operator finds weeks later, and `git worktree list` grows without bound."""

    def setUp(self):
        self.project = make_repo()
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def worktrees(self):
        return [l for l in git(self.project, "worktree", "list").stdout.splitlines()
                if "(bare)" not in l]

    def test_workdir_is_outside_the_project_and_removed_on_success(self):
        sb = runner.Sandbox(self.project, runner.PROFILES["worktree"], "Remediator")
        wt = sb.enter()
        self.assertNotEqual(wt.resolve(), self.project.resolve())
        self.assertFalse(str(wt.resolve()).startswith(str(self.project.resolve())),
                         f"the worktree {wt} is INSIDE the project — the courier would "
                         f"ship it (F-0120)")
        self.assertEqual(len(self.worktrees()), 2, "worktree not registered")
        sb.leave()
        self.assertFalse(wt.exists(), "worktree directory survived leave()")
        self.assertEqual(len(self.worktrees()), 1, "worktree still registered with git")

    def test_removed_on_the_failure_path_too(self):
        sb = runner.Sandbox(self.project, runner.PROFILES["worktree"], "Remediator")
        wt = sb.enter()
        try:
            raise RuntimeError("the session blew up")
        except RuntimeError:
            sb.leave()
        self.assertFalse(wt.exists())
        self.assertEqual(len(self.worktrees()), 1)

    def test_run_session_removes_the_worktree_on_both_child_outcomes(self):
        """The real path: run_session's own try/finally, once for a child that
        succeeds and once for a child that fails."""
        for script, label in (("exit 0", "success"), ("exit 3", "failure")):
            with self.subTest(label):
                runner.run_session(
                    self.project, conf(remediator_cmd=f"sh -c '{script}'"),
                    "Remediator", "charter")
                self.assertEqual(len(self.worktrees()), 1,
                                 f"a worktree leaked on the {label} path")

    def test_the_session_sees_uncommitted_work_and_its_changes_come_back(self):
        """The worktree is seeded from the working tree, not from HEAD: a session
        that could not see the human's pending triage edits would silently work
        from a stale state — and `collect()` must bring only ITS changes back."""
        (self.project / "material.txt").write_text("edited by the human\n", encoding="utf-8")
        (self.project / "audit" / "untracked.md").write_text("pending\n", encoding="utf-8")
        sb = runner.Sandbox(self.project, runner.PROFILES["worktree"], "Remediator")
        wt = sb.enter()
        try:
            self.assertEqual((wt / "material.txt").read_text(), "edited by the human\n")
            self.assertTrue((wt / "audit" / "untracked.md").is_file())
            (wt / "audit" / "session.md").write_text("the session's own work\n",
                                                     encoding="utf-8")
            sb.collect()
        finally:
            sb.leave()
        self.assertEqual((self.project / "audit" / "session.md").read_text(),
                         "the session's own work\n")
        self.assertEqual((self.project / "material.txt").read_text(),
                         "edited by the human\n", "the seed was re-applied on top")

    def test_readonly_refuses_a_material_change_and_keeps_the_patch(self):
        sb = runner.Sandbox(self.project, runner.PROFILES["readonly"], "Auditor")
        wt = sb.enter()
        try:
            (wt / "material.txt").write_text("an Auditor should not do this\n",
                                             encoding="utf-8")
            with self.assertRaises(SystemExit) as cm:
                sb.collect()
        finally:
            sb.leave()
        self.assertIn("material.txt", str(cm.exception))
        self.assertEqual((self.project / "material.txt").read_text(), "original\n")
        patches = list((self.project / "audit" / "orchestrator-logs").glob("*.patch"))
        self.assertTrue(patches, "the refused work was thrown away instead of kept")


class FailClosedWhenIsolationIsUnavailable(unittest.TestCase):
    """An isolation that silently becomes none is the failure the profile exists to
    prevent, so every unavailable-sandbox path must REFUSE, not degrade."""

    def test_not_a_git_repository(self):
        tmp = Path(tempfile.mkdtemp(prefix="xcheck-nogit-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        sb = runner.Sandbox(tmp, runner.PROFILES["worktree"], "Remediator")
        with self.assertRaises(SystemExit) as cm:
            sb.enter()
        msg = str(cm.exception)
        print("\nFAIL-CLOSED (no git repo):\n" + msg)
        self.assertIn("refusing to launch", msg)
        self.assertEqual(sb.workdir, tmp, "workdir must not silently be the project")

    def test_worktree_creation_failure_refuses_and_leaves_no_temp_dir(self):
        project = make_repo()
        self.addCleanup(shutil.rmtree, project, ignore_errors=True)
        real = runner._git
        seen = {}

        def broken(cwd, args, check=False):
            if args and args[0] == "worktree" and args[1] == "add":
                raise RuntimeError("fatal: could not create worktree (disk full)")
            return real(cwd, args, check=check)

        runner._git = broken
        self.addCleanup(setattr, runner, "_git", real)
        sb = runner.Sandbox(project, runner.PROFILES["worktree"], "Remediator")
        with self.assertRaises(SystemExit) as cm:
            sb.enter()
        seen["tmp"] = sb._tmp
        print("\nFAIL-CLOSED (worktree creation forced to fail):\n" + str(cm.exception))
        self.assertIn("NOT falling back to running in the project", str(cm.exception))
        self.assertIsNone(seen["tmp"], "the temp dir was not cleaned up on refusal")

    def test_run_session_exits_non_zero_when_the_sandbox_refuses(self):
        project = make_repo()
        self.addCleanup(shutil.rmtree, project, ignore_errors=True)
        real = runner.Sandbox.enter

        def refuse(self):
            raise SystemExit("refusing to launch: forced")

        runner.Sandbox.enter = refuse
        self.addCleanup(setattr, runner.Sandbox, "enter", real)
        with self.assertRaises(SystemExit):
            runner.run_session(project, conf(remediator_cmd="sh -c 'exit 0'"),
                               "Remediator", "charter")


class TheAmbientGrantBecomesAContainedGrant(unittest.TestCase):
    """`--dangerously-skip-permissions` is not deleted — it is CONTAINED. Deleting it
    would push the operator to run sessions outside xcheck, losing every control at
    once; permitting it uncontained is the 2/10 execution-safety score."""

    def test_refused_outside_an_isolating_profile(self):
        cmd = ["claude", "-p", "--dangerously-skip-permissions", "prompt"]
        with self.assertRaises(SystemExit) as cm:
            runner.refuse_uncontained(cmd, runner.PROFILES["none"], "Remediator")
        print("\nUNCONTAINED-GRANT REFUSAL:\n" + str(cm.exception))
        self.assertIn("--dangerously-skip-permissions", str(cm.exception))

    def test_permitted_under_an_isolating_profile(self):
        cmd = ["claude", "-p", "--dangerously-skip-permissions", "prompt"]
        for name in ("worktree", "readonly"):
            runner.refuse_uncontained(cmd, runner.PROFILES[name], "Remediator")
        self.assertEqual(runner.uncontained_grant(cmd, runner.PROFILES["worktree"]), [])

    def test_equivalent_flags_and_the_flag_value_form(self):
        for flag in runner.SKIP_PERMISSION_FLAGS:
            self.assertEqual(
                runner.uncontained_grant([flag], runner.PROFILES["none"]), [flag])
        self.assertEqual(
            runner.uncontained_grant(["--yolo=1"], runner.PROFILES["none"]), ["--yolo=1"])

    def test_run_session_refuses_before_launching_anything(self):
        project = make_repo()
        self.addCleanup(shutil.rmtree, project, ignore_errors=True)
        with self.assertRaises(SystemExit):
            runner.run_session(
                project,
                conf(sandbox_profile="none",
                     remediator_cmd="sh -c 'touch launched' --dangerously-skip-permissions"),
                "Remediator", "charter")
        self.assertFalse((project / "launched").exists(), "the child was launched anyway")

    def test_an_unknown_profile_is_refused_not_guessed(self):
        with self.assertRaises(SystemExit) as cm:
            runner.resolve_profile(conf(sandbox_profile="containerish"), "Remediator")
        self.assertIn("is not a profile", str(cm.exception))

    def test_the_read_only_roles_default_to_readonly(self):
        for role in ("Auditor", "Verifier"):
            self.assertEqual(runner.resolve_profile(conf(), role).name, "readonly")
        for role in ("Planner", "Remediator"):
            self.assertEqual(runner.resolve_profile(conf(), role).name, "worktree")


class TheChildEnvironmentIsBuiltNotInherited(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xcheck-env-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.log = self.tmp / "child.log"

    def test_an_injected_cloud_credential_never_reaches_the_child(self):
        os.environ["AWS_SECRET_ACCESS_KEY"] = "wJalrXUtnFEMI-fake-secret-key"
        os.environ["XCHECK_MARKER"] = "orchestration"
        self.addCleanup(os.environ.pop, "AWS_SECRET_ACCESS_KEY", None)
        self.addCleanup(os.environ.pop, "XCHECK_MARKER", None)
        env = runner.child_environment(conf(), {"XCHECK_SESSION_ID": "abc"})
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
        rc, outcome, _ = runner.run_child(
            ["sh", "-c", "env | sort"], self.tmp, self.log, env, conf(), timeout=30)
        dump = self.log.read_text()
        print("\nCHILD ENV DUMP:\n" + dump)
        self.assertEqual(outcome, "ok")
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", dump)
        self.assertNotIn("wJalrXUtnFEMI-fake-secret-key", dump)
        self.assertIn("XCHECK_SESSION_ID=abc", dump)
        self.assertIn("XCHECK_MARKER=orchestration", dump)
        names = {l.split("=", 1)[0] for l in dump.splitlines() if "=" in l}
        # `sh` sets a few of its own (PWD, SHLVL, _), so subtract what a shell adds.
        extra = names - runner.ENV_ALLOWLIST - {"PWD", "SHLVL", "_", "OLDPWD"}
        self.assertEqual(sorted(n for n in extra if not n.startswith("XCHECK_")), [],
                         f"non-allowlisted names reached the child: {extra}")

    def test_the_operator_can_name_an_extra_key(self):
        os.environ["MY_AGENT_TOKEN"] = "operator-named-value"
        self.addCleanup(os.environ.pop, "MY_AGENT_TOKEN", None)
        env = runner.child_environment(conf(env_allowlist="MY_AGENT_TOKEN, OTHER"))
        self.assertEqual(env["MY_AGENT_TOKEN"], "operator-named-value")


class TheLogNeverCarriesAValueTheRedactorWasAskedToHide(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xcheck-redact-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.log = self.tmp / "child.log"

    def test_an_echoed_secret_is_replaced(self):
        os.environ["MY_API_KEY"] = "hunter2-not-a-real-key-9f3c"
        self.addCleanup(os.environ.pop, "MY_API_KEY", None)
        env = runner.child_environment(conf(env_allowlist="MY_API_KEY"))
        runner.run_child(["sh", "-c", 'echo "the key is $MY_API_KEY"; echo sk-ABCDEFGHIJKLMNOPQRSTUV'],
                         self.tmp, self.log, env, conf(), timeout=30)
        text = self.log.read_text()
        print("\nREDACTED LOG EXCERPT:\n" + text)
        self.assertNotIn("hunter2-not-a-real-key-9f3c", text)
        self.assertIn("«redacted:MY_API_KEY»", text)
        self.assertIn("«redacted:token-shape»", text, "a bare token shape survived")

    def test_a_non_secret_name_is_left_alone(self):
        r = runner.Redactor({"PATH": "/usr/bin:/bin", "GITHUB_TOKEN": "ghp_" + "x" * 20})
        out = r.scrub("PATH=/usr/bin:/bin TOKEN=ghp_" + "x" * 20)
        self.assertIn("/usr/bin:/bin", out)
        self.assertNotIn("ghp_" + "x" * 20, out)


class AChildOutlivingItsTimeoutDiesWithItsWholeGroup(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xcheck-timeout-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_the_grandchild_stops_advancing(self):
        marker = self.tmp / "marker"
        log = self.tmp / "child.log"
        # The child spawns a GRANDCHILD that keeps writing, then sleeps past the
        # timeout. Killing only the child would leave the grandchild writing forever —
        # which is exactly the runaway a timeout is supposed to end.
        script = (f"( while true; do echo tick >> {marker}; sleep 0.1; done ) & "
                  f"sleep 30")
        t0 = time.time()
        rc, outcome, elapsed = runner.run_child(
            ["sh", "-c", script], self.tmp, log, runner.child_environment(conf()),
            conf(kill_grace=1), timeout=2)
        self.assertEqual(outcome, "timeout")
        self.assertLess(time.time() - t0, 20, "the timeout did not bound the session")
        first = marker.stat().st_size
        time.sleep(1.5)
        second = marker.stat().st_size
        print(f"\nGRANDCHILD-KILLED PROOF: marker {first} bytes at kill, "
              f"{second} bytes 1.5s later (equal => the process group is gone)")
        self.assertEqual(first, second, "the grandchild outlived the group kill")

    def test_resource_limits_fail_closed_when_unavailable(self):
        import builtins
        real_import = builtins.__import__

        def no_resource(name, *a, **kw):
            if name == "resource":
                raise ImportError("no resource module here")
            return real_import(name, *a, **kw)

        builtins.__import__ = no_resource
        self.addCleanup(setattr, builtins, "__import__", real_import)
        self.assertIsNone(runner.rlimit_preexec(conf()), "limits unset must not refuse")
        with self.assertRaises(SystemExit) as cm:
            runner.rlimit_preexec(conf(cpu_seconds=10))
        self.assertIn("worse than none", str(cm.exception))

    @unittest.skipUnless(sys.platform != "win32", "POSIX only")
    def test_a_cpu_limit_actually_applies(self):
        log = self.tmp / "cpu.log"
        rc, outcome, _ = runner.run_child(
            ["sh", "-c", "python3 -c 'while True: pass'"], self.tmp, log,
            runner.child_environment(conf()), conf(cpu_seconds=1, kill_grace=1),
            timeout=25)
        self.assertNotEqual(outcome, "timeout",
                            "RLIMIT_CPU did not stop a spinning child before the timeout")


class EveryOutcomeIsClassified(unittest.TestCase):
    """`refused` (the agent declined), `blocked` (the provider's policy), and
    `provider-error` (the provider fell over) all exit non-zero. Collapsing them into
    `crash` would make the loop retry a refusal and give up on an outage."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xcheck-classify-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_five_synthetic_children(self):
        cases = [
            ("refused", "echo 'I cannot complete this charter: material-missing'; exit 3", None),
            ("blocked", "echo 'error: rate limit exceeded, retry later'; exit 1", None),
            ("provider-error", "echo 'API error: Service Unavailable'; exit 1", None),
            ("timeout", "sleep 30", 2),
            ("crash", "echo 'Traceback: ValueError'; exit 2", None),
            ("ok", "echo done", None),
        ]
        results = []
        for expected, script, timeout in cases:
            log = self.tmp / f"{expected}.log"
            rc, outcome, _ = runner.run_child(
                ["sh", "-c", script], self.tmp, log, runner.child_environment(conf()),
                conf(kill_grace=1), timeout=timeout or 30)
            results.append((script[:38], rc, outcome))
            self.assertEqual(outcome, expected, f"{script!r} classified {outcome}")
        print("\nOUTCOME CLASSIFICATION:")
        for script, rc, outcome in results:
            print(f"  {script:<40} rc={rc:<4} -> {outcome}")

    def test_cancellation_is_not_disguised_as_a_timeout(self):
        self.assertEqual(runner.classify_outcome(-15, timed_out=True, cancelled=True),
                         "cancelled")
        self.assertIn("cancelled", runner.OUTCOMES)


class TheLockIsALease(unittest.TestCase):

    def setUp(self):
        self.project = make_repo()
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        self.audit = self.project / "audit"

    def test_a_live_heartbeat_is_not_reclaimable_and_a_dead_one_is(self):
        lock = runner.Lock(self.audit, "Remediator")
        lock.acquire()
        self.assertFalse(lock.lease_dead(300), "a fresh heartbeat read as dead")
        with self.assertRaises(SystemExit) as cm:
            cli.cmd_unlock(self.project, force=False, conf=conf())
        self.assertNotIn("reclaimed", str(cm.exception))
        self.assertTrue(lock.dir.exists(), "a live lease was removed")

        lock.heartbeat_file.write_text(f"{time.time() - 4000:.0f}\n", encoding="utf-8")
        self.assertTrue(lock.lease_dead(300))
        cli.cmd_unlock(self.project, force=False, conf=conf())   # no --force needed
        self.assertFalse(lock.dir.exists(), "a dead lease was not reclaimed")

    def test_no_heartbeat_falls_back_to_the_pid_rules(self):
        lock = runner.Lock(self.audit, "Remediator")
        lock.acquire()
        lock.heartbeat_file.unlink()
        self.assertIsNone(lock.heartbeat_age())
        self.assertFalse(lock.lease_dead(0), "an absent heartbeat must not read as dead")
        with self.assertRaises(SystemExit):
            cli.cmd_unlock(self.project, force=False, conf=conf())

    def test_release_removes_the_lease_files_with_the_lock(self):
        lock = runner.Lock(self.audit, "Remediator")
        lock.acquire()
        lock.request_cancel()
        lock.release()
        self.assertFalse(lock.dir.exists(),
                         "the heartbeat/cancel files kept the lock directory alive")

    def test_cancel_asks_the_running_session_to_stop(self):
        lock = runner.Lock(self.audit, "Remediator")
        lock.acquire()
        self.assertFalse(lock.cancel_requested())
        self.assertEqual(cli.cmd_cancel(self.project), 0)
        self.assertTrue(lock.cancel_requested())

    def test_a_cancelled_session_is_killed_and_its_changes_are_not_applied(self):
        lock = runner.Lock(self.audit, "Remediator")
        lock.acquire()
        self.addCleanup(lock.release)
        log = self.audit / "cancel.log"

        def cancel_soon():
            time.sleep(1.0)
            lock.request_cancel()

        import threading
        threading.Thread(target=cancel_soon, daemon=True).start()
        rc, outcome, elapsed = runner.run_child(
            ["sh", "-c", "sleep 30"], self.project, log,
            runner.child_environment(conf()), conf(kill_grace=1), lock=lock, timeout=60)
        self.assertEqual(outcome, "cancelled")
        self.assertLess(elapsed, 20)

    def test_the_orchestrator_beats_while_the_child_runs(self):
        lock = runner.Lock(self.audit, "Remediator")
        lock.acquire()
        self.addCleanup(lock.release)
        lock.heartbeat_file.write_text("0\n", encoding="utf-8")   # ancient
        runner.run_child(["sh", "-c", "sleep 6"], self.project, self.audit / "b.log",
                         runner.child_environment(conf()), conf(), lock=lock, timeout=30)
        self.assertLess(lock.heartbeat_age(), 30, "the lease was never renewed")


class NoBlockingCallWithoutABound(unittest.TestCase):
    """One hung child holding the global lock forever is the operational failure this
    phase exists to end, and a `subprocess.run` with no `timeout=` is exactly that
    hazard. The listing is PRINTED so a reviewer reads what was compared."""

    BLOCKING = {"run", "call", "check_call", "check_output", "communicate"}

    def sites(self):
        found = []
        for path in sorted((REPO / "xcheck").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            parents = {}
            for node in ast.walk(tree):
                for child in ast.iter_child_nodes(node):
                    parents[child] = node
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                        and f.value.id == "subprocess"):
                    continue
                if f.attr not in self.BLOCKING and f.attr != "Popen":
                    continue
                kw = {k.arg for k in node.keywords}
                enclosing = node
                fn = None
                while enclosing in parents:
                    enclosing = parents[enclosing]
                    if isinstance(enclosing, ast.FunctionDef):
                        fn = enclosing.name
                        break
                found.append((path.name, node.lineno, f.attr, fn,
                              "timeout" in kw))
        return found

    def test_every_site_is_bounded(self):
        sites = self.sites()
        print("\nSUBPROCESS CALL SITES (xcheck/):")
        unbounded = []
        for name, line, attr, fn, bounded in sites:
            how = "timeout=" if bounded else "UNBOUNDED"
            if not bounded and attr == "Popen" and fn == "run_child":
                # The one declared exception: `run_child`'s Popen is bounded by its own
                # wait loop (which also polls the lease and the cancel request) and then
                # by a process-GROUP kill. `timeout=` on Popen does not exist, and
                # `run(timeout=)` cannot kill a grandchild — so the bound lives in the
                # loop, and this test pins WHERE it is allowed to live.
                how = "wait-loop + killpg (declared)"
            elif not bounded:
                unbounded.append(f"{name}:{line} subprocess.{attr} in {fn or '<module>'}")
            print(f"  {name}:{line:<5} subprocess.{attr:<6} in {str(fn):<24} {how}")
        print(f"  -> {len(sites)} sites, {len(unbounded)} unbounded")
        self.assertEqual(unbounded, [], "a blocking subprocess call has no bound")

    def test_popen_is_confined_to_the_declared_helper(self):
        elsewhere = [s for s in self.sites()
                     if s[2] == "Popen" and s[3] != "run_child"]
        self.assertEqual(elsewhere, [], "Popen outside run_child bypasses the bound")


if __name__ == "__main__":
    unittest.main()


class NothingConfiguredRunsOnTheHostBeforeContainment(unittest.TestCase):
    """F-0098, the audit's only critical, witnessed against a real child.

    `dispatch_record` ran `subprocess.run([exe, "--version"])`, and it runs BEFORE
    `sandbox.enter()`. So the first thing a dispatch did was execute an
    operator-configured binary directly on the host — under `container` as much as
    under `none`. A profile that does not contain the first program it runs is not a
    profile, and the version string it bought was self-reported text.

    The witness is a shim whose `--version` writes a marker into a SYSTEM temp dir
    (F-0120: never the project tree). Fixed, the marker never appears. The control
    restores the old probe in-process and the same shim DOES create it — which is what
    makes the absence above evidence rather than a coincidence."""

    SHIM = """#!/bin/sh
for a in "$@"; do
  if [ "$a" = "--version" ]; then
    echo "host-probe executed" > "{marker}"
    echo "shim 1.0"
    exit 0
  fi
done
exit 0
"""

    def setUp(self):
        self.project = make_repo()
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="xcheck-f0098-witness-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.marker = self.tmp / "host-marker"
        self.shim = self.tmp / "agent-shim"
        self.shim.write_text(self.SHIM.format(marker=self.marker), encoding="utf-8")
        self.shim.chmod(0o755)
        self.envelope = xcheck_submodule("envelope")
        self.envelope._VERSION_CACHE.clear()
        self.addCleanup(self.envelope._VERSION_CACHE.clear)

    def dispatch(self):
        """One real dispatch through `run_session` with the shim as the Auditor
        command. The session itself is allowed to run — the claim is about what happens
        BEFORE containment, so the run must be the real sequence, not a stub of it."""
        c = conf(auditor_cmd=f"{self.shim} {{prompt}}", sandbox_profile="worktree",
                 session_timeout=60)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            runner.run_session(self.project, c, "Auditor", "witness charter")
        return buf.getvalue()

    def test_the_shims_version_is_never_executed_on_the_host(self):
        self.dispatch()
        print(f"\n  WITNESS  marker after a real dispatch: "
              f"{'PRESENT' if self.marker.exists() else 'absent'}  ({self.marker})")
        self.assertFalse(self.marker.exists(),
                         "the configured executable was run on the host before any "
                         "sandbox existed (F-0098)")

    def test_control_the_restored_probe_creates_the_marker(self):
        """CONTROL. The old body, reinstated in-process — never by `git checkout`, the
        tree is uncommitted mid-phase. If this arm did not create the marker the witness
        above would be measuring nothing."""
        import subprocess as sp
        saved = self.envelope.executable_version

        def old_probe(exe, env=None):
            p = sp.run([exe, "--version"], capture_output=True, text=True,
                       timeout=15, env=env, stdin=sp.DEVNULL)
            line = (p.stdout or p.stderr or "").strip().splitlines()
            return line[0].strip()[:120] if p.returncode == 0 and line else "unknown"

        self.envelope.executable_version = old_probe
        try:
            self.dispatch()
        finally:
            self.envelope.executable_version = saved
        print(f"  CONTROL  marker with the old probe restored: "
              f"{'PRESENT' if self.marker.exists() else 'absent'}")
        self.assertTrue(self.marker.exists(),
                        "the control did not reproduce the defect, so the witness "
                        "above proves nothing")

    def test_what_it_records_instead_is_a_digest_of_the_bytes(self):
        """The replacement is not `unknown` for everything: it identifies the binary
        by its content, which is stronger than a self-reported string and costs no
        execution. Determinism is part of the claim — an envelope field that changes
        between two reads of one file is not provenance."""
        first = self.envelope.executable_version(str(self.shim))
        self.envelope._VERSION_CACHE.clear()
        second = self.envelope.executable_version(str(self.shim))
        want = "sha256:" + self.envelope.sha256_file(self.shim)
        print(f"  RECORDED  {first}")
        self.assertEqual(want, first)
        self.assertEqual(first, second, "the recorded identity is not deterministic")
        self.assertFalse(self.marker.exists(), "identifying it executed it")

    def test_an_unresolvable_executable_is_unknown_not_a_guess(self):
        for exe in ("no_such_executable_xyzzy", "", str(self.tmp)):
            with self.subTest(exe=exe):
                self.envelope._VERSION_CACHE.clear()
                self.assertEqual("unknown", self.envelope.executable_version(exe))

    def test_no_execution_site_precedes_sandbox_enter(self):
        """The static half. Walks `run_session`'s AST from its start to the
        `sandbox.enter()` call and asserts no `subprocess.*` / `os.system` / `os.exec*`
        / `Popen` call appears in that prefix, in this module or through
        `envelope.dispatch_record`. A witness proves one shim was not run; this proves
        there is no second door."""
        launchers = ("run", "call", "check_call", "check_output", "Popen", "system",
                     "popen", "execv", "execvp", "execve", "spawnv")
        tree = ast.parse(Path(runner.__file__).read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "run_session")
        enter_line = min(
            n.lineno for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "enter")
        before = [n for n in ast.walk(fn)
                  if isinstance(n, ast.Call) and n.lineno < enter_line
                  and isinstance(n.func, ast.Attribute) and n.func.attr in launchers]
        env_tree = ast.parse(Path(self.envelope.__file__).read_text(encoding="utf-8"))
        env_calls = [n for n in ast.walk(env_tree)
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                     and n.func.attr in launchers
                     and getattr(n.func.value, "id", "") in ("subprocess", "os", "sp")]
        print(f"  STATIC   run_session: sandbox.enter() at line {enter_line}; "
              f"{len(before)} execution site(s) before it; "
              f"envelope.py: {len(env_calls)} execution site(s) anywhere")
        self.assertEqual([], [f"{n.func.attr}@{n.lineno}" for n in before])
        self.assertEqual([], [f"{n.func.attr}@{n.lineno}" for n in env_calls],
                         "envelope.py can execute something again — the module the "
                         "dispatch record is built in must not launch anything")


class TheSandboxRefusesARewrittenEventStream(unittest.TestCase):
    """PHASE 9 (fourth audit). The courier is one gate; this is the earlier one. A
    session under an isolating profile has its own copy of `audit/events.jsonl`, and
    `collect()` compares it to the SEED before `git apply` touches the project — because
    a rejected stream that lands and is then reverted has already happened."""

    def setUp(self):
        self.project = make_repo()
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        (self.project / "audit").mkdir(exist_ok=True)
        (self.project / "audit" / "events.jsonl").write_text(
            '{"event":"lease_acquired","session_id":"a","ts":"2026-09-01T00:00:00+00:00"}\n'
            '{"event":"state_transition","session_id":"a","ts":"2026-09-01T00:00:01+00:00"}\n',
            encoding="utf-8")
        git(self.project, "add", "-A")
        git(self.project, "commit", "-qm", "events")

    def collect_after(self, mutate):
        sb = runner.Sandbox(self.project, runner.PROFILES["worktree"], "Remediator")
        wt = sb.enter()
        self.addCleanup(sb.leave)
        mutate(wt / "audit" / "events.jsonl")
        return sb

    def test_CONTROL_an_append_inside_the_worktree_is_carried_back(self):
        def append(path):
            with path.open("a", encoding="utf-8") as f:
                f.write('{"event":"lease_released","session_id":"a",'
                        '"ts":"2026-09-01T00:00:02+00:00"}\n')
        sb = self.collect_after(append)
        self.assertIsNotNone(sb.collect(), "the append was not carried back")
        landed = (self.project / "audit" / "events.jsonl").read_text(encoding="utf-8")
        self.assertEqual(3, len(landed.splitlines()))
        print(f"\n  SANDBOX OK an appended event landed; {len(landed.splitlines())} "
              f"events in the project")

    def test_a_rewritten_stream_is_refused_before_the_patch_is_applied(self):
        before = (self.project / "audit" / "events.jsonl").read_bytes()

        def rewrite(path):
            path.write_text(path.read_text(encoding="utf-8")
                            .replace('"state_transition"', '"rewritten"'),
                            encoding="utf-8")
        sb = self.collect_after(rewrite)
        with self.assertRaises(SystemExit) as caught:
            sb.collect()
        message = str(caught.exception)
        self.assertIn("REWROTE", message)
        self.assertIn("Nothing was applied", message)
        self.assertEqual(before, (self.project / "audit" / "events.jsonl").read_bytes(),
                         "the rewrite reached the main checkout")
        self.assertIn("refused.patch", message,
                      "the refused work was not kept for the operator")
        print(f"  SANDBOX    {message.splitlines()[0][:140]}")
        print(f"             project stream unchanged, {len(before)} bytes")
