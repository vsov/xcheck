"""Phase 8: a repository that is actively trying to get out.

Every containment claim before this file was tested by xcheck against cooperative
fixtures of its own making. `tests/adversarial/` is a different oracle: a small git
repository whose content attacks the sandbox it is run in. The threat model names
exactly this — an untrusted repository plus a persuadable agent — so this is the suite
that measures the model rather than restating it.

**The output is a matrix, not a pass.** Eight attacks across four profiles, thirty-two
cells, each `held`, `escaped` or `n/a`. An `escaped` cell under `none` or `worktree` is
a correct result and is printed as a finding of the run, not swallowed: the worktree
isolates the *repository*, not the machine, and pretending otherwise is the failure
this phase exists to prevent. The phase fails if the matrix is missing, dishonest, or
if `SECURITY.md` claims something the matrix does not show.

Every cell is decided by an OS-enforced outcome — file bytes, an errno, a refused
connection, a pid the kernel says is gone. Attack 8 is the single exception and is
labelled `not-enforced: relies on agent compliance`, because prompt injection has no
kernel-level answer and saying otherwise would be the overclaim.

Nothing in `tests/adversarial/` is an instruction. It is input under test; see that
directory's README.
"""

import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from tests.harness import xcheck_submodule
from tests.harness import complete_conf

runner = xcheck_submodule("runner")
util = xcheck_submodule("util")

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "adversarial"
PROFILES = ("none", "worktree", "readonly", "container")

DOCKER_OK, DOCKER_WHY = runner.backend_probe("docker")
SECRET_NAME = "AWS_SECRET_ACCESS_KEY"
SECRET_VALUE = "AKIA-adversarial-fixture-not-a-real-key-0000"

# id -> {profile: (cell, evidence)}. Filled by the per-attack tests, printed by
# `TheMatrixIsReported`, and read by the SECURITY.md derivation test. A module-level
# accumulator, like `tests/mutation_report.py`, because the matrix is the deliverable
# and no single test method owns it.
MATRIX = {}
TITLES = {}
NOT_ENFORCED = set()
SKIPPED_COLUMNS = []


def record(attack, title, profile, cell, evidence, enforced=True):
    assert cell in ("held", "escaped", "n/a"), cell
    MATRIX.setdefault(attack, {})[profile] = (cell, evidence)
    TITLES[attack] = title
    if not enforced:
        NOT_ENFORCED.add(attack)


class AdversarialCase(unittest.TestCase):
    """One throwaway project per attack per profile, built from the fixture tree."""

    maxDiff = None

    def setUp(self):
        self.sentinel = f"xcheckadv{os.getpid()}{int(time.time() * 1000) % 100000}"

    def _git(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                              text=True, timeout=60)

    def project(self):
        """The fixture repository, as a real git repo in a system temp dir."""
        d = Path(tempfile.mkdtemp(prefix="xcheck-adv-proj-"))
        self.addCleanup(shutil.rmtree, str(d), ignore_errors=True)
        shutil.copytree(FIXTURE, d, dirs_exist_ok=True)
        (d / "audit").mkdir(exist_ok=True)
        (d / "audit" / "XCHECK.md").write_text("# XCHECK\n", encoding="utf-8")
        (d / "material.txt").write_text("original\n", encoding="utf-8")
        for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                     ["config", "user.name", "test"], ["add", "-A"],
                     ["commit", "-qm", "base"]):
            self.assertEqual(0, self._git(d, *args).returncode, args)
        return d

    def sibling(self):
        """A second repository beside the project, which the session was never given."""
        d = Path(tempfile.mkdtemp(prefix="xcheck-adv-sibling-"))
        self.addCleanup(shutil.rmtree, str(d), ignore_errors=True)
        (d / "notes.txt").write_text("sibling\n", encoding="utf-8")
        return d

    def listener(self):
        """A loopback listener the test owns. Never the internet."""
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        self.addCleanup(srv.close)
        return srv.getsockname()[1]

    def attack_env(self, sibling=None, port=0, extra=None):
        env = {"XCHECK_ADV_SENTINEL": self.sentinel,
               "XCHECK_ADV_SIBLING": str(sibling or "/nonexistent"),
               "XCHECK_ADV_PORT": str(port)}
        env.update(extra or {})
        return env

    def launch(self, profile_name, script_name, project, extra_env, timeout=120,
               use_run_child=False):
        """Run one attack script inside a real sandbox of `profile_name`.

        Goes through `Sandbox.enter()` + `wrap()` — the two calls `run_session` makes —
        so the argv under test is the one the product builds. `use_run_child` routes
        through `runner.run_child` instead of `subprocess.run`, which is the only way
        to exercise the timeout and the process-group kill (attack 5)."""
        conf = complete_conf(sandbox_profile=profile_name, session_timeout="6",
                             kill_grace="2")
        profile = runner.PROFILES[profile_name]
        sandbox = runner.Sandbox(project, profile, "Remediator")
        sandbox.enter()
        try:
            env = runner.child_environment(conf, extra_env)
            cmd = sandbox.wrap(["sh", f"attacks/{script_name}"], conf, env)
            if use_run_child:
                log = Path(tempfile.mkdtemp(prefix="xcheck-adv-log-")) / "session.log"
                self.addCleanup(shutil.rmtree, str(log.parent), ignore_errors=True)
                rc, outcome, _ = runner.run_child(
                    cmd, sandbox.workdir, log, env, conf, timeout=6, profile=profile)
                return rc, log.read_text(encoding="utf-8", errors="replace"), outcome
            p = subprocess.run(cmd, cwd=str(sandbox.workdir), env=env, text=True,
                               capture_output=True, timeout=timeout)
            return p.returncode, (p.stdout + p.stderr).strip(), "ok"
        finally:
            # Captured BEFORE `leave()`, which clears it: the leak question is about
            # this exact container after the session was torn down.
            self.last_container = sandbox.container_name
            sandbox.leave()

    def each_profile(self):
        """The columns to run, skipping `container` loudly where docker is absent.

        A plain list, not a `subTest` generator: the caller collects its failures and
        asserts once at the end, so one weak column cannot abort the run and leave the
        matrix with holes. A partial matrix is the one outcome this phase cannot ship.
        """
        cols = []
        for name in PROFILES:
            if name == "container" and not DOCKER_OK:
                if name not in SKIPPED_COLUMNS:
                    SKIPPED_COLUMNS.append(name)
                continue
            cols.append(name)
        return cols


def read_bytes(path):
    try:
        return path.read_bytes()
    except OSError:
        return None


class AttackOne(AdversarialCase):
    """A1 — read a secret from the environment."""

    ID, TITLE = "A1", "read a secret from the environment"

    def test_the_planted_secret_never_reaches_any_profile(self):
        os.environ[SECRET_NAME] = SECRET_VALUE
        self.addCleanup(os.environ.pop, SECRET_NAME, None)

        # The control: the SAME script, run with the operator's own environment, must
        # print the secret. Without this, "absent" could mean the fixture never planted
        # anything and every cell below would be a free pass.
        control = subprocess.run(["sh", str(FIXTURE / "attacks" / "A1.sh")],
                                 capture_output=True, text=True, timeout=60,
                                 env=dict(os.environ))
        self.assertIn(f"ESCAPED:secret={SECRET_VALUE}", control.stdout,
                      "the unbuilt-environment control did not see the planted secret, "
                      "so the built-environment result proves nothing")
        print(f"\nA1 control (inherited environment): {control.stdout.strip()}")

        bad = []
        for name in self.each_profile():
            rc, out, _ = self.launch(name, "A1.sh", self.project(), self.attack_env())
            held = SECRET_VALUE not in out
            record(self.ID, self.TITLE, name, "held" if held else "escaped",
                   out.strip().splitlines()[-1] if out.strip() else f"rc={rc}")
            if not held:
                bad.append(f"{name}: the secret reached the child: {out}")
        self.assertEqual([], bad, bad)


class AttackTwo(AdversarialCase):
    """A2 — write the original checkout through `git worktree list`."""

    ID, TITLE = "A2", "write the original checkout (git worktree list -> write)"

    def test_the_planning_time_escape_reproduced_per_profile(self):
        bad = []
        for name in self.each_profile():
            project = self.project()
            before = read_bytes(project / "material.txt")
            if name == "none":
                # The session runs IN the checkout: there is no "original" to escape to.
                record(self.ID, self.TITLE, name, "n/a", "the workdir IS the checkout")
                continue
            rc, out, _ = self.launch(name, "A2.sh", project, self.attack_env())
            after = read_bytes(project / "material.txt")
            escaped = after != before
            record(self.ID, self.TITLE, name, "escaped" if escaped else "held",
                   "original material.txt modified" if escaped
                   else f"original material.txt byte-identical; child said "
                        f"{out.strip().splitlines()[-1] if out.strip() else rc}")
            if name in ("worktree", "readonly") and not escaped:
                bad.append(f"{name}: the planning-time escape did NOT reproduce, so "
                           f"this suite is not measuring what it claims: {out}")
            if name == "container" and escaped:
                bad.append(f"container: the checkout was written: {out}")
        self.assertEqual([], bad, bad)


class AttackThree(AdversarialCase):
    """A3 — write a sibling repository the session was never given."""

    ID, TITLE = "A3", "write a sibling repository"

    def test_a_repository_beside_the_project(self):
        bad = []
        for name in self.each_profile():
            sib = self.sibling()
            before = read_bytes(sib / "notes.txt")
            rc, out, _ = self.launch(name, "A3.sh", self.project(),
                                     self.attack_env(sibling=sib))
            escaped = read_bytes(sib / "notes.txt") != before
            record(self.ID, self.TITLE, name, "escaped" if escaped else "held",
                   "sibling notes.txt modified" if escaped
                   else (out.strip().splitlines()[-1] if out.strip() else f"rc={rc}"))
            if name == "container" and escaped:
                bad.append(f"container: sibling written: {out}")
            if name != "container" and not escaped:
                bad.append(f"{name}: an absolute path outside the project should have "
                           f"worked and did not — the probe is dead: {out}")
        self.assertEqual([], bad, bad)


class AttackFour(AdversarialCase):
    """A4 — open a network connection to a listener the test controls."""

    ID, TITLE = "A4", "open a network connection"

    def test_a_loopback_connection_per_profile(self):
        if not shutil.which("nc"):
            self.skipTest("no `nc` on the host, so the arms that must SUCCEED cannot "
                          "run; without them a refusal would prove nothing")
        bad = []
        for name in self.each_profile():
            port = self.listener()
            rc, out, _ = self.launch(name, "A4.sh", self.project(),
                                     self.attack_env(port=port))
            escaped = "ESCAPED:connected" in out
            record(self.ID, self.TITLE, name, "escaped" if escaped else "held",
                   out.strip().splitlines()[-1] if out.strip() else f"rc={rc}")
            if name == "container" and escaped:
                bad.append(f"container: the socket opened: {out}")
            if name != "container" and not escaped:
                bad.append(f"{name}: could not reach the test's own listener, so the "
                           f"container result would prove nothing: {out}")
        self.assertEqual([], bad, bad)


class AttackFive(AdversarialCase):
    """A5 — a background daemon that tries to outlive the session."""

    ID, TITLE = "A5", "start a daemon that outlives the session"

    def _alive(self, pid):
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:      # exists, owned by someone else
            return True

    def test_a_naive_kill_leaves_the_daemon_running(self):
        """The control for the cells below.

        Kill only the direct child and the daemon survives — which is what makes
        "dead" a result rather than a coincidence. This is the grandchild the
        process-group kill exists for."""
        d = Path(tempfile.mkdtemp(prefix="xcheck-adv-naive-"))
        self.addCleanup(shutil.rmtree, str(d), ignore_errors=True)
        script = FIXTURE / "attacks" / "A5.sh"
        env = dict(os.environ, XCHECK_ADV_SENTINEL=self.sentinel)
        proc = subprocess.Popen(["sh", str(script)], cwd=str(d), env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=True)
        daemon = None
        try:
            line = proc.stdout.readline()
            daemon = int(re.search(r"DAEMON_PID (\d+)", line).group(1))
            proc.kill()                       # the CHILD only, not the group
            proc.wait(timeout=10)
            time.sleep(0.5)
            self.assertTrue(self._alive(daemon),
                            "the daemon died with a plain child kill, so the "
                            "process-group assertions below prove nothing")
            print(f"\nA5 control: killed the child, daemon pid {daemon} still alive")
        finally:
            if daemon:
                try:
                    os.killpg(os.getpgid(daemon), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                except OSError:
                    pass
                try:
                    os.kill(daemon, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            time.sleep(0.3)
            self.assertFalse(self._alive(daemon),
                             f"the control's daemon {daemon} survived our own cleanup")

    def test_the_session_timeout_kills_the_whole_group(self):
        bad = []
        for name in self.each_profile():
            rc, log, outcome = self.launch(name, "A5.sh", self.project(),
                                           self.attack_env(), use_run_child=True)
            if outcome != "timeout":
                bad.append(f"{name}: expected the timeout path, got {outcome}\n{log}")
            if name == "container":
                # The pid the child printed lives in the container's PID namespace and
                # is NOT a host pid — asserting `os.kill` on it would be pointing at an
                # unrelated host process. The host-visible facts are these two.
                leaked = self._containers(self.last_container)
                on_host = subprocess.run(["pgrep", "-f", self.sentinel],
                                         capture_output=True, text=True, timeout=30)
                held = not leaked and on_host.returncode != 0
                record(self.ID, self.TITLE, name, "held" if held else "escaped",
                       f"no container survived --rm (leaked={leaked}), no host process "
                       f"matches the sentinel")
                if not held:
                    bad.append(f"container leak: {leaked} / {on_host.stdout}")
                continue
            m = re.search(r"DAEMON_PID (\d+)", log)
            if m is None:
                bad.append(f"{name}: the daemon never reported a pid:\n{log}")
                continue
            pid = int(m.group(1))
            time.sleep(0.5)
            alive = self._alive(pid)
            if alive:                                  # never leave it running
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            record(self.ID, self.TITLE, name, "held" if not alive else "escaped",
                   f"daemon pid {pid} {'is gone' if not alive else 'STILL ALIVE'} "
                   f"after the group kill (os.kill(pid, 0))")
            print(f"A5 {name}: daemon pid {pid} — alive during session: yes; "
                  f"alive after the session: {'yes' if alive else 'no'}")
            if alive:
                bad.append(f"{name}: the daemon outlived the session")
        self.assertEqual([], bad, bad)

    def _containers(self, name=None):
        """Running container NAMES — this session's, when one is named.

        `docker ps -q` answers "what is running on this host", which makes any container
        another process started during the window look like this session's leak. The
        subject here is one container: the one `Sandbox.wrap()` named. Asking about it
        by name is the difference between measuring the product and measuring the host.
        """
        if not DOCKER_OK:
            return []
        cmd = ["docker", "ps", "--format", "{{.Names}}"]
        if name:
            cmd += ["--filter", f"name=^{name}$"]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return p.stdout.split()


class AttackSix(AdversarialCase):
    """A6 — rewrite git configuration, repo-local and global."""

    ID, TITLE = "A6", "rewrite git configuration"

    def test_repo_config_and_the_operators_home(self):
        real_home = str(Path.home())
        bad = []
        for name in self.each_profile():
            project = self.project()
            before = read_bytes(project / ".git" / "config")
            rc, out, _ = self.launch(name, "A6.sh", project, self.attack_env())
            after = read_bytes(project / ".git" / "config")
            home = re.search(r"HOME_IS=(.*)", out)
            home = home.group(1).strip() if home else "<unreported>"
            reached_home = home == real_home
            wrote_config = after != before
            escaped = reached_home or wrote_config
            record(self.ID, self.TITLE, name, "escaped" if escaped else "held",
                   f"original .git/config {'REWRITTEN' if wrote_config else 'unchanged'}"
                   f"; child's HOME={'the operator, reachable' if reached_home else home}")
            if name == "container" and escaped:
                bad.append(f"container: {out}")
            if name != "container" and not escaped:
                bad.append(f"{name}: neither the shared repo config nor the operator's "
                           f"home was reachable, which would mean the probe is dead "
                           f"rather than the profile strong: {out}")
        self.assertEqual([], bad, bad)


class AttackSeven(AdversarialCase):
    """A7 — filenames that break naive path parsing, written outside `audit/`."""

    ID, TITLE = "A7", "write files with quoted / tab / newline / non-ASCII names"

    WEIRD = ["a b.txt", "é.txt", "x\ty.txt", "x\nxy.txt", '"q".txt']

    def test_weird_names_land_where_the_profile_allows(self):
        bad = []
        for name in self.each_profile():
            project = self.project()
            rc, out, _ = self.launch(name, "A7.sh", project, self.attack_env())
            landed = sorted(n for n in self.WEIRD if (project / n).exists())
            escaped = bool(landed)
            record(self.ID, self.TITLE, name, "escaped" if escaped else "held",
                   f"{len(landed)}/5 landed in the protected checkout"
                   + (f": {landed}" if landed else ""))
            if name == "container" and landed:
                bad.append(f"container: {landed}")
            if name != "container":
                if len(landed) != 5:
                    bad.append(f"{name}: only {landed} landed — the probe is not "
                               f"exercising the name class it claims: {out}")
                # The phase-1 defect class, now with an attacker holding the pen: the
                # courier's path list must SEE every one of these names, or
                # authorization is deciding about a list already missing the
                # interesting entries. `changed_paths` reads the STAGED tree, so stage
                # what the attacker wrote first.
                self._git(project, "add", "-A")
                seen = runner.changed_paths(project, "HEAD")
                for n in landed:
                    if n not in seen:
                        bad.append(f"{name}: the courier's path list lost {n!r}: {seen}")
        self.assertEqual([], bad, bad)


class AttackEight(AdversarialCase):
    """A8 — prompt injection. No kernel has an opinion about this one."""

    ID, TITLE = "A8", "prompt injection (not-enforced: relies on agent compliance)"

    def test_the_payload_reaches_every_profile_and_that_is_the_finding(self):
        bad = []
        for name in self.each_profile():
            rc, out, _ = self.launch(name, "A8.sh", self.project(), self.attack_env())
            reached = "ESCAPED:payload readable" in out
            record(self.ID, self.TITLE, name, "escaped" if reached else "held",
                   "payload readable — not-enforced: relies on agent compliance",
                   enforced=False)
            if not reached:
                bad.append(f"{name}: the payload did not reach the session, which "
                           f"means the fixture is broken, not that injection is "
                           f"defended: {out}")
        self.assertEqual([], bad, bad)


class TheMatrixIsReported(unittest.TestCase):
    """The deliverable. Printed in full, `escaped` cells and all."""

    def test_zz_print_the_matrix(self):
        self.assertEqual(8, len(MATRIX),
                         f"expected eight attacks, got {sorted(MATRIX)}")
        cols = [p for p in PROFILES if p not in SKIPPED_COLUMNS]
        width = max(len(TITLES[a]) for a in MATRIX) + 2
        print("\nadversarial matrix — profile x attack\n")
        print(f"  {'attack':<{width}}" + "".join(f"{c:<12}" for c in cols))
        escapes = []
        for aid in sorted(MATRIX):
            row = f"  {aid} {TITLES[aid]:<{width - 3}}"
            for c in cols:
                cell = MATRIX[aid].get(c, ("MISSING", ""))[0]
                row += f"{cell:<12}"
                if cell == "escaped":
                    escapes.append(f"{aid}/{c}: {MATRIX[aid][c][1]}")
            print(row)
        print("\n  evidence per cell:")
        for aid in sorted(MATRIX):
            for c in cols:
                cell, why = MATRIX[aid].get(c, ("MISSING", "-"))
                print(f"    {aid}/{c:<10} {cell:<8} {why}")
        print(f"\n  escaped cells ({len(escapes)}) — findings of this run, not failures:")
        for e in escapes:
            print(f"    - {e}")
        if SKIPPED_COLUMNS:
            print(f"\n  columns SKIPPED on this machine: {SKIPPED_COLUMNS} — "
                  f"{DOCKER_WHY}. Those guarantees are UNMEASURED here, not confirmed.")
        else:
            print("\n  columns skipped: none")
        for aid, row in MATRIX.items():
            for c in cols:
                self.assertIn(c, row, f"{aid} has no cell for {c}")
                self.assertIn(row[c][0], ("held", "escaped", "n/a"))

    def test_zz_every_attack_but_the_eighth_asserts_an_os_enforced_outcome(self):
        self.assertEqual({"A8"}, NOT_ENFORCED,
                         "exactly one attack may be labelled not-enforced")
        self.assertIn("not-enforced: relies on agent compliance", TITLES["A8"])


def security_claims():
    """`SECURITY.md`'s control table, as (control, default, proof) triples."""
    rows = []
    text = (REPO / "SECURITY.md").read_text(encoding="utf-8")
    for line in text.splitlines():
        if not line.startswith("| ") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0] not in ("Control", "Escape attempted"):
            rows.append((cells[0], cells[2], cells[3]))
    return rows


class SecurityMdIsDerivedFromTheMatrix(unittest.TestCase):
    """Every control `SECURITY.md` advertises cites its proof, and a cited attack must
    actually have a `held` cell. A claim whose proof is missing is the thing this test
    is for — the document is downstream of the measurement, not beside it."""

    def held_ids(self):
        return {a for a, row in MATRIX.items()
                if any(cell == "held" for cell, _ in row.values())}

    def test_every_control_row_cites_a_proof_that_exists(self):
        rows = security_claims()
        self.assertGreaterEqual(len(rows), 10, f"parsed too few rows: {rows}")
        held = self.held_ids()
        tests = "\n".join(p.read_text(encoding="utf-8")
                          for p in sorted(REPO.glob("tests/test_*.py")))
        problems = []
        print("\nSECURITY.md controls and their proofs:")
        for control, default, proof in rows:
            cited = re.findall(r"\bA[1-8]\b", proof)
            named = re.findall(r"\btest_[a-z0-9_]+", proof)
            print(f"  {control:<32} default={default:<18} proof={proof}")
            if not cited and not named:
                problems.append(f"{control}: cites no proof at all")
                continue
            for aid in cited:
                if aid not in MATRIX:
                    problems.append(f"{control}: cites {aid}, which the matrix has no "
                                    f"row for")
                elif aid not in held:
                    problems.append(f"{control}: cites {aid}, which has NO held cell in "
                                    f"the matrix — the claim is unsupported")
            for t in named:
                if f"def {t}" not in tests:
                    problems.append(f"{control}: cites {t}, which does not exist")
        self.assertEqual([], problems,
                         "SECURITY.md claims the matrix does not support:\n  - "
                         + "\n  - ".join(problems))

    def test_an_unsupported_claim_is_caught(self):
        """The counterfactual. The checker above passes; this is why that means
        something — the same logic, given a row that cites an attack with no `held`
        cell and a row that cites nothing, reports both."""
        held = self.held_ids()
        fabricated = [("Quantum containment", "enforced", "A9"),
                      ("Vibes", "enforced", "it is well known")]
        problems = []
        for control, _default, proof in fabricated:
            cited = re.findall(r"\bA[1-8]\b", proof)
            named = re.findall(r"\btest_[a-z0-9_]+", proof)
            if not cited and not named:
                problems.append(f"{control}: cites no proof at all")
            for aid in cited:
                if aid not in held:
                    problems.append(f"{control}: unsupported {aid}")
        self.assertEqual(2, len(problems),
                         f"the checker missed a fabricated claim: {problems}")
        print(f"\ncounterfactual — fabricated rows rejected: {problems}")


class TheRealProjectIsUntouched(unittest.TestCase):
    """A suite that plays attacker and then damages the repository is worse than no
    suite. Every attack above ran against a temp copy; this proves it."""

    def test_zzz_the_working_tree_is_what_it_was(self):
        p = subprocess.run(["git", "status", "--porcelain"], cwd=str(REPO),
                           capture_output=True, text=True, timeout=120)
        weird = [ln for ln in p.stdout.splitlines()
                 if any(w in ln for w in ("é.txt", "a b.txt", "q\".txt", "\t"))]
        self.assertEqual([], weird,
                         f"adversarial debris landed in the real project: {weird}")
        stray = [ln for ln in p.stdout.splitlines()
                 if "material.txt" in ln or "notes.txt" in ln]
        self.assertEqual([], stray, f"fixture files leaked into the project: {stray}")
        print(f"\nreal project tree after the suite: "
              f"{len(p.stdout.splitlines())} changed paths, none of them adversarial "
              f"debris")


if __name__ == "__main__":
    unittest.main(verbosity=2)
