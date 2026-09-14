"""0.9.1 phase 10 (audit P1 #11): the container may reach its provider and nothing else.

The audit's finding was an operational one: `--network=none` is real isolation and it is
also why no cloud agent runs inside the container, so the honest choice was a safe
container that cannot work or a working `worktree` with the host's network. This module
is the evidence for the third option.

**Criterion 2 is the spine of the file, and it is why almost nothing here reads a docker
flag.** Asserting that `--network` names an internal network proves that xcheck can
spell a docker flag. It does not prove the child cannot reach the provider directly. So
the bypass is ATTEMPTED — by name, and by the provider's real IP address — and the same
attempt is then made from the uplink network as a CONTROL, because a probe that can
never succeed proves nothing when it fails ([[spy-needs-a-positive-control]],
[[mutation-harness-needs-its-own-control]]).

The stand-in provider is a busybox `nc` listener on the uplink network. Nothing here
touches the internet, no credential is used, and no provider session is required.

Every docker object is named `xcheck-` and removed in teardown; `TheTeardownIsClean`
counts what is left. `--rm` is a client-side promise and this file kills a client on
purpose ([[docker-rm-does-nothing-when-the-client-is-killed]]).
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.harness import complete_conf, xcheck_submodule

egress = xcheck_submodule("egress")
runner = xcheck_submodule("runner")
util = xcheck_submodule("util")

DOCKER_OK, DOCKER_WHY = runner.backend_probe("docker")
SKIP_REASON = (f"every arm in this class starts real containers and {DOCKER_WHY}. The "
               f"allowlist parser, the profile refusal and the default-flags comparison "
               f"still ran — they need no daemon. The BOUNDARY ITSELF is UNMEASURED on "
               f"this machine, which is not the same as confirmed.")
_SKIPPED = []
_RAN = []

IMAGE = util.CONTAINER_IMAGE
PROVIDER_SAYS = "HELLO-FROM-THE-STAND-IN-PROVIDER"


def docker(*args, timeout=180):
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          timeout=timeout)


class TheAllowlistIsParsedBeforeAnythingRuns(unittest.TestCase):
    """No daemon. A malformed allowlist must be refused at the door, because the
    alternative is a broker enforcing a list nobody could read."""

    def test_empty_is_off(self):
        self.assertEqual((), egress.allowlist(complete_conf()))

    def test_hosts_are_split_deduplicated_and_kept_in_order(self):
        got = egress.allowlist(complete_conf(
            egress_allowlist="api.anthropic.com, 10.0.0.7 ,api.anthropic.com"))
        print(f"\n  parsed allowlist: {got}")
        self.assertEqual(("api.anthropic.com", "10.0.0.7"), got)

    def test_a_url_or_a_cidr_is_refused_by_name(self):
        for bad in ("https://api.anthropic.com/v1", "10.0.0.0/8"):
            with self.subTest(entry=bad):
                with self.assertRaises(egress.EgressError) as cm:
                    egress.allowlist(complete_conf(egress_allowlist=bad))
                print(f"  refused {bad!r}: {str(cm.exception)[:70]}…")
                self.assertIn(bad, str(cm.exception))

    def test_a_port_is_refused_because_the_broker_does_not_check_one(self):
        """A rule that reads narrower than what is enforced is worse than a broad rule
        an operator can see. The allowlist answers WHO, not on which port."""
        with self.assertRaises(egress.EgressError) as cm:
            egress.allowlist(complete_conf(egress_allowlist="api.anthropic.com:443"))
        self.assertIn("carries a port", str(cm.exception))

    def test_an_allowlist_without_the_container_profile_refuses(self):
        """Criterion: only `container` can enforce this. Under `worktree` the child has
        the HOST's network, and an allowlist there would be the word without the
        thing — the same failure shape as a sandbox profile that degrades."""
        sandbox = runner.Sandbox(
            Path(tempfile.mkdtemp(prefix="xcheck-egp-")), runner.PROFILES["worktree"],
            "Remediator", conf=complete_conf(sandbox_profile="worktree",
                                             egress_allowlist="api.anthropic.com"))
        with self.assertRaises(SystemExit) as cm:
            sandbox.enter()
        print(f"\n  {str(cm.exception).splitlines()[0]}")
        self.assertIn("only sandbox_profile='container'", str(cm.exception))


class TheDefaultIsUnchanged(unittest.TestCase):
    """Criterion 6. No allowlist, no broker, and the flags are byte-identical to what
    the profile built before this phase existed."""

    maxDiff = None

    def test_the_argv_is_network_none_and_matches_the_recorded_flags(self):
        argv = runner.container_argv(["sh", "-c", "true"], complete_conf(),
                                     "/tmp/w", "/tmp/o", "/tmp/s", name="fixed")
        print(f"\n  default network flags: {argv[3]!r}")
        self.assertIn("--network=none", argv)
        self.assertNotIn("--network", argv, "the `none` form, not the two-token form")
        self.assertEqual([], [a for a in argv if "PROXY" in a.upper()])
        # The whole prefix, in order, as it was before phase 10.
        self.assertEqual(["docker", "run", "--rm", "--network=none"], argv[:4])

    def test_the_capability_report_says_none_until_an_allowlist_is_set(self):
        self.assertEqual("none", runner.container_capabilities(complete_conf())["network"])
        self.assertEqual([], runner.container_capabilities(complete_conf())
                         ["egress_allowlist"])
        with_list = runner.container_capabilities(
            complete_conf(egress_allowlist="api.anthropic.com"))
        print(f"  with an allowlist: network={with_list['network']!r}")
        self.assertIn("internal", with_list["network"])
        self.assertEqual(["api.anthropic.com"], with_list["egress_allowlist"])

    def test_a_malformed_allowlist_does_not_make_the_report_explode(self):
        """The report is called while BUILDING a refusal elsewhere. A report that
        raises turns an addressed operator error into a traceback."""
        caps = runner.container_capabilities(
            complete_conf(egress_allowlist="https://nope/"))
        self.assertEqual("none", caps["network"])


class TheBoundaryHolds(unittest.TestCase):
    """Every arm here starts real containers on real docker networks."""

    maxDiff = None
    uplink = None
    provider = None
    provider_ip = None

    @classmethod
    def setUpClass(cls):
        if not DOCKER_OK:
            return
        cls.uplink = f"xcheck-uplink-{os.urandom(4).hex()}"
        cls.provider = f"xcheck-provider-{os.urandom(4).hex()}"
        docker("network", "create", cls.uplink)
        # A stand-in provider: answers once per connection, forever. On the UPLINK
        # network only — the audit container must have no way to it except the broker.
        docker("run", "-d", "--name", cls.provider, "--network", cls.uplink,
               "--network-alias", "provider", "--entrypoint", "sh", IMAGE,
               "-c", f'while true; do printf "{PROVIDER_SAYS}\\n" | nc -l -p 443 '
                     f'>/dev/null; done')
        p = docker("inspect", "-f",
                   '{{(index .NetworkSettings.Networks "%s").IPAddress}}' % cls.uplink,
                   cls.provider)
        cls.provider_ip = p.stdout.strip()

    @classmethod
    def tearDownClass(cls):
        if not DOCKER_OK:
            return
        docker("rm", "-f", cls.provider)
        docker("network", "rm", cls.uplink)

    def broker(self, allow, **over):
        if not DOCKER_OK:
            _SKIPPED.append(self._testMethodName)
            self.skipTest(SKIP_REASON)
        conf = complete_conf(sandbox_profile="container", egress_allowlist=allow,
                             egress_uplink=self.uplink, **over)
        b = egress.Broker(conf, os.urandom(5).hex())
        self.addCleanup(b.stop)
        b.start()
        _RAN.append(self._testMethodName)
        return b

    def from_internal(self, network, script, timeout=25):
        """Run a probe INSIDE the broker's internal network — where the audit container
        lives — and report `(rc, output)`."""
        p = docker("run", "--rm", "--network", network, "--entrypoint", "sh", IMAGE,
                   "-c", script, timeout=timeout + 120)
        return p.returncode, (p.stdout + p.stderr).strip()

    def from_uplink(self, script, timeout=25):
        """The CONTROL. The same probe from the uplink network, where it must work."""
        p = docker("run", "--rm", "--network", self.uplink, "--entrypoint", "sh", IMAGE,
                   "-c", script, timeout=timeout + 120)
        return p.returncode, (p.stdout + p.stderr).strip()

    # -- criterion 1 --------------------------------------------------------------

    def test_a_listed_host_is_reached_and_an_unlisted_one_is_refused(self):
        b = self.broker(self.provider_ip)
        allowed = b.connect_probe(self.provider_ip)
        denied = b.connect_probe("10.255.255.1")
        print(f"\n  ALLOW {self.provider_ip} ->\n    "
              + allowed.strip().replace("\n", "\n    "))
        print("  DENY  10.255.255.1 ->\n    " + denied.strip().replace("\n", "\n    "))
        self.assertIn("200 Connection established", allowed)
        self.assertIn(PROVIDER_SAYS, allowed,
                      "the tunnel must carry the provider's own bytes, not only a 200")
        self.assertIn("403 Forbidden", denied)
        self.assertNotIn(PROVIDER_SAYS, denied)

    # -- criterion 2, the spine ---------------------------------------------------

    def test_the_provider_cannot_be_reached_around_the_broker(self):
        """Three attempts and one control. The flags are never read."""
        b = self.broker(self.provider_ip)
        by_name = self.from_internal(b.network, 'nc -w 4 provider 443 </dev/null')
        by_ip = self.from_internal(
            b.network, f'nc -w 4 {self.provider_ip} 443 </dev/null')
        public = self.from_internal(b.network, 'nc -w 4 1.1.1.1 80 </dev/null')
        control = self.from_uplink(f'nc -w 4 {self.provider_ip} 443 </dev/null')
        print("\n  from the internal network:")
        print(f"    provider by name : rc={by_name[0]} {by_name[1][:60]!r}")
        print(f"    provider by IP   : rc={by_ip[0]} {by_ip[1][:60]!r}")
        print(f"    1.1.1.1:80       : rc={public[0]} {public[1][:60]!r}")
        print("  CONTROL, same probe from the uplink network:")
        print(f"    provider by IP   : rc={control[0]} {control[1][:60]!r}")
        self.assertNotEqual(0, by_name[0])
        self.assertNotIn(PROVIDER_SAYS, by_name[1])
        self.assertNotEqual(0, by_ip[0])
        self.assertNotIn(PROVIDER_SAYS, by_ip[1])
        self.assertNotEqual(0, public[0])
        self.assertEqual(0, control[0],
                         "THE PROBE IS DEAD: the same connection fails from the uplink "
                         "too, so its failure from the internal network says nothing")
        self.assertIn(PROVIDER_SAYS, control[1])

    # -- criterion 3 --------------------------------------------------------------

    def test_dns_for_an_unlisted_name_does_not_resolve(self):
        b = self.broker(self.provider_ip)
        rc, out = self.from_internal(
            b.network, 'nslookup api.anthropic.com 2>&1; echo "---"; '
                       'nc -w 4 api.anthropic.com 443 </dev/null; echo "nc=$?"')
        print("\n  DNS from inside the internal network:\n    "
              + out.replace("\n", "\n    ")[:600])
        self.assertTrue(
            any(m in out for m in ("SERVFAIL", "can't find", "NXDOMAIN",
                                   "bad address", "No answer")),
            f"an external name resolved from an --internal network: {out!r}")
        self.assertIn("nc=1", out.replace("nc=0", "NC-SUCCEEDED"))

    # -- criterion 4 --------------------------------------------------------------

    def test_the_egress_log_matches_the_attempts_made(self):
        b = self.broker(self.provider_ip)
        attempts = [self.provider_ip, "10.255.255.1", self.provider_ip, "evil.invalid"]
        for host in attempts:
            b.connect_probe(host)
        text = b.log_path.read_text(encoding="utf-8")
        print("\n  egress log, line for line:")
        for line in text.strip().splitlines():
            print(f"    | {line}")
        lines = [ln for ln in text.strip().splitlines() if ln]
        # The health probe is the FIRST line: xcheck's own request is logged like
        # anyone else's, because a log that hides one caller is a summary, not a record.
        self.assertEqual(f"host={egress.PROBE_HOST} port=443 verdict=DENY", lines[0])
        self.assertEqual(
            [f"host={h} port=443 verdict={'ALLOW' if h == self.provider_ip else 'DENY'}"
             for h in attempts], lines[1:],
            "the log must name every attempt, in order, with its verdict")

    # -- criterion 5 --------------------------------------------------------------

    def test_killing_the_sidecar_mid_run_refuses_the_run(self):
        """The kill is real: `docker rm -f` on the sidecar while a child is running.

        The child here is a `sleep` — what matters is that `run_child` notices the
        boundary is gone, kills the process group, and RAISES rather than returning an
        outcome that would let the session's patch be applied."""
        b = self.broker(self.provider_ip)
        log = Path(tempfile.mkdtemp(prefix="xcheck-eglog-")) / "session.log"
        self.addCleanup(shutil.rmtree, str(log.parent), ignore_errors=True)

        import threading
        killed = threading.Event()

        def kill_it():
            for _ in range(200):
                if killed.wait(0.1):
                    return
                docker("rm", "-f", b.container, timeout=60)
                return
        t = threading.Thread(target=kill_it, daemon=True)
        t.start()
        try:
            with self.assertRaises(SystemExit) as cm:
                runner.run_child(["sh", "-c", "sleep 60"], log.parent, log, {},
                                 complete_conf(session_timeout=120, kill_grace=1),
                                 broker=b)
        finally:
            killed.set()
        print("\n  the sidecar was killed mid-session, and the run refused:\n    "
              + str(cm.exception))
        self.assertIn("stopped while the session was running", str(cm.exception))

    def test_a_live_sidecar_lets_the_same_session_finish(self):
        """The control for the arm above. Without it, 'the run refused' could be the
        broker check refusing every session it ever sees."""
        b = self.broker(self.provider_ip)
        log = Path(tempfile.mkdtemp(prefix="xcheck-eglog-")) / "session.log"
        self.addCleanup(shutil.rmtree, str(log.parent), ignore_errors=True)
        rc, outcome, _ = runner.run_child(
            ["sh", "-c", "echo THE-SESSION-FINISHED"], log.parent, log, {},
            complete_conf(session_timeout=120), broker=b)
        print(f"  live sidecar: rc={rc} outcome={outcome} "
              f"log={log.read_text(encoding='utf-8').strip()!r}")
        self.assertEqual(0, rc)
        self.assertEqual("ok", outcome)

    # -- fail-closed at the door ---------------------------------------------------

    def test_an_unpinned_broker_image_refuses(self):
        if not DOCKER_OK:
            _SKIPPED.append(self._testMethodName)
            self.skipTest(SKIP_REASON)
        b = egress.Broker(complete_conf(sandbox_profile="container",
                                        egress_allowlist="example.invalid",
                                        egress_broker_image="alpine/git:latest"),
                          os.urandom(5).hex())
        self.addCleanup(b.stop)
        with self.assertRaises(egress.EgressError) as cm:
            b.start()
        print(f"\n  {str(cm.exception).splitlines()[0]}")
        self.assertIn("not pinned by digest", str(cm.exception))
        _RAN.append(self._testMethodName)

    def test_an_unreachable_uplink_refuses_instead_of_running_without_one(self):
        if not DOCKER_OK:
            _SKIPPED.append(self._testMethodName)
            self.skipTest(SKIP_REASON)
        b = egress.Broker(complete_conf(sandbox_profile="container",
                                        egress_allowlist="example.invalid",
                                        egress_uplink="xcheck-no-such-network"),
                          os.urandom(5).hex())
        self.addCleanup(b.stop)
        with self.assertRaises(egress.EgressError) as cm:
            b.start()
        print(f"  {str(cm.exception).splitlines()[0][:150]}")
        self.assertIn("uplink", str(cm.exception))
        _RAN.append(self._testMethodName)


class TheTeardownIsClean(unittest.TestCase):
    """Criterion 7. `--rm` is a client-side promise and this file kills a client on
    purpose, so every object is named and removed explicitly."""

    def test_zz_no_xcheck_container_or_network_is_left_behind(self):
        if not DOCKER_OK:
            print(f"\n  docker UNAVAILABLE ({DOCKER_WHY}) — nothing was started, so "
                  f"nothing can be left.")
            return
        c = docker("ps", "-a", "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}")
        n = docker("network", "ls", "--format", "{{.Name}}")
        mine = [ln for ln in c.stdout.splitlines()
                if ln.split("\t")[0].startswith(("xcheck-broker-", "xcheck-provider-",
                                                 "xcheck-uplink-", "xcheck-egress-"))]
        nets = [ln for ln in n.stdout.splitlines()
                if ln.startswith(("xcheck-egress-", "xcheck-uplink-"))]
        print(f"\n  docker ps -a: {len(c.stdout.splitlines())} container(s) total on "
              f"this machine, {len(mine)} of them this phase's.")
        for ln in c.stdout.splitlines():
            print(f"    | {ln}")
        print(f"  docker networks left by this phase: {nets or 'none'}")
        self.assertEqual([], mine, "a container this phase started outlived it")
        self.assertEqual([], nets, "a network this phase created outlived it")

    def test_zz_report_whether_the_boundary_was_measured(self):
        if DOCKER_OK:
            print(f"\n  docker backend: {DOCKER_WHY} — {len(set(_RAN))} boundary arm(s) "
                  f"ran against real containers, 0 skipped.")
            return
        print(f"\n  docker backend UNAVAILABLE: {DOCKER_WHY}."
              f"\n  {len(set(_SKIPPED))} boundary arm(s) SKIPPED: "
              f"{', '.join(sorted(set(_SKIPPED))) or 'none'}."
              f"\n  The allowlist parser, the profile refusal and the unchanged-default "
              f"flags were measured. The BOUNDARY was not. That is unmeasured on this "
              f"machine, not confirmed.")


if __name__ == "__main__":
    unittest.main()
