"""0.9.1 phase 10 (audit P1 #11): a container that may reach its provider and nothing else.

The `container` profile runs `--network=none`. That is real isolation, and it is also
why no cloud agent can run inside it: Codex and Claude CLI need their provider API. The
operator's choice was therefore a safe container that cannot run the agent, or a working
agent in a `worktree` with the host's network and the host's filesystem. There was no
safe-by-default industrial path, and `SECURITY.md` said so.

The shape of the answer:

    audit container ──(internal network, no route off it)──▶ broker ──▶ the allowlist
                                                              │
                                                       (second leg, uplink)

The audit container joins an `--internal` docker network. Nothing on that network has a
route anywhere else — not to the host, not to the internet, not to the operator's other
containers. The broker is dual-homed: on the internal network with the audit container,
and on an uplink network where the outside is. It speaks HTTP `CONNECT`, permits exactly
the hosts named in `egress_allowlist`, and appends one line per attempt with the host and
the verdict.

Three things this deliberately is NOT:

* It is not a defence against prompt injection. An agent talked into exfiltrating through
  the *permitted* provider endpoint is not stopped by an allowlist that permits it.
* It is not short-lived credential handling. Credentials still travel by the environment
  allowlist for the whole session. The audit named that as part of the full design and it
  is NOT BUILT.
* It is not a package-registry proxy. `pip install` inside the container reaches nothing
  unless the registry is in the allowlist, which is a blunt instrument compared to the
  proxy the audit describes. Also NOT BUILT.

Fail-closed everywhere. If the broker cannot be established, the run refuses. If it dies
mid-session, the session is killed and the run refuses. It never degrades to an open
network, and it never quietly degrades to `--network=none` either — a session that
silently cannot reach its provider looks exactly like a hung agent.
"""

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from xcheck.util import CONF_DEFAULTS

DOCKER_TIMEOUT = 120
BROKER_PORT = 3128

# The name the health check asks for. It is NOT in any allowlist, so a live broker
# answers 403 and a dead one answers nothing — the probe distinguishes "listening and
# refusing" from "not there", which is the only pair that matters at start-up.
#
# It is logged like every other attempt. Suppressing xcheck's own request from the
# egress log would make the log a summary rather than a record, and this is the one
# file whose whole value is being a record.
PROBE_HOST = "__xcheck_broker_probe__"

# The broker, as one POSIX-sh program handling ONE connection on stdin/stdout.
# `nc -lk -p PORT -e` runs it per connection, so there is no concurrency to get wrong
# here and no state to keep between connections.
#
# Written to a temp dir and bind-mounted READ-ONLY, so the broker cannot rewrite its own
# allowlist and the file never touches the project tree (F-0120).
BROKER_SH = r"""#!/bin/sh
# xcheck egress broker — one CONNECT, one verdict, one log line.
host=""; port=""
IFS= read -r line
line=$(printf '%s' "$line" | tr -d '\r')
set -- $line
if [ "$1" = "CONNECT" ]; then
  host=${2%%:*}; port=${2##*:}
  [ "$port" = "$2" ] && port=443
fi
# Drain the remaining request headers up to the blank line. Whatever follows is the
# tunnelled payload and must NOT be read here — it belongs to the splice below.
while IFS= read -r h; do
  h=$(printf '%s' "$h" | tr -d '\r')
  [ -z "$h" ] && break
done
verdict=DENY
if [ -n "$host" ]; then
  for a in $(cat /broker/allow); do
    if [ "$a" = "$host" ]; then verdict=ALLOW; break; fi
  done
fi
echo "host=$host port=$port verdict=$verdict" >> /log/egress.log
if [ "$verdict" != "ALLOW" ]; then
  printf 'HTTP/1.1 403 Forbidden\r\nX-xcheck-egress: denied %s\r\n\r\n' "$host"
  exit 0
fi
printf 'HTTP/1.1 200 Connection established\r\n\r\n'
exec nc "$host" "$port"
"""


class EgressError(RuntimeError):
    """The broker could not be established, or stopped being what it claimed."""


def _docker(args, timeout=DOCKER_TIMEOUT):
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          timeout=timeout)


def allowlist(conf):
    """The hosts this run may reach, as a tuple. Empty tuple = the feature is off.

    Entries are hosts or IPs, comma-separated. A port is not part of an entry: the
    allowlist answers "may this run talk to that party at all", and a party reachable
    on 443 but not on 8443 is a firewall rule, not an audit boundary."""
    raw = str((conf or {}).get("egress_allowlist", "") or "").strip()
    if not raw:
        return ()
    out = []
    for part in raw.replace(";", ",").split(","):
        host = part.strip()
        if not host:
            continue
        if "/" in host or " " in host:
            raise EgressError(
                f"egress_allowlist entry {host!r} is not a host: entries are hostnames "
                f"or IP addresses, one per comma. A URL, a path or a CIDR range is not "
                f"something this broker can check a CONNECT line against.")
        if ":" in host:
            raise EgressError(
                f"egress_allowlist entry {host!r} carries a port. The allowlist names "
                f"WHO this run may talk to, not on which port — an entry with a port "
                f"reads like a rule that is narrower than what is actually enforced.")
        out.append(host)
    return tuple(dict.fromkeys(out))       # de-duplicated, order kept


class Broker:
    """One internal network, one sidecar, and the teardown that survives a kill.

    Every docker object this class creates is NAMED. `--rm` is a promise the docker
    CLIENT makes, and the client is exactly what dies when a session times out — the
    daemon owns the container and would keep it, with its uplink, after xcheck is gone.
    """

    def __init__(self, conf, run_id, uplink=None):
        self.conf = conf if conf is not None else dict(CONF_DEFAULTS)
        self.hosts = allowlist(self.conf)
        self.run_id = run_id
        self.network = f"xcheck-egress-{run_id}"
        self.container = f"xcheck-broker-{run_id}"
        self.uplink = uplink or str(self.conf.get("egress_uplink", "")
                                    or CONF_DEFAULTS["egress_uplink"]).strip()
        self.image = str(self.conf.get("egress_broker_image", "")
                         or CONF_DEFAULTS["egress_broker_image"]).strip()
        self._tmp = None
        self.log_path = None
        self.started = False

    # -- lifecycle ---------------------------------------------------------------

    def start(self):
        """Networks, sidecar, and a probe that proves it is answering. Or refuse."""
        from xcheck.runner import unpinned_reason
        why = unpinned_reason(self.image)
        if why:
            raise EgressError(
                f"refusing to start the egress broker: egress_broker_image="
                f"{self.image!r} is not pinned by digest — {why}. The broker is the "
                f"thing deciding what leaves this run; an image that can change under "
                f"it is not a boundary anyone can name.")
        self._tmp = Path(tempfile.mkdtemp(prefix="xcheck-egress-"))
        (self._tmp / "broker").mkdir()
        (self._tmp / "log").mkdir()
        script = self._tmp / "broker" / "broker.sh"
        script.write_text(BROKER_SH, encoding="utf-8")
        # `nc -e` EXECS this file. Without the mode bit it fails per connection, the
        # listener stays up, and every probe gets an empty reply — a broker that is
        # running and answering nothing, which reads exactly like a network problem.
        script.chmod(0o755)
        (self._tmp / "broker" / "allow").write_text(
            "\n".join(self.hosts) + "\n", encoding="utf-8")
        self.log_path = self._tmp / "log" / "egress.log"
        self.log_path.write_text("", encoding="utf-8")
        try:
            self._create_network()
            self._start_sidecar()
            self._probe()
        except Exception:
            self.stop()
            raise
        self.started = True
        return self

    def _create_network(self):
        p = _docker(["network", "create", "--internal", self.network])
        if p.returncode != 0:
            raise EgressError(
                f"refusing to launch with egress_allowlist set: the internal docker "
                f"network {self.network!r} could not be created — "
                f"{(p.stderr or p.stdout).strip()[:200]}. NOT falling back: an "
                f"allowlist that cannot be enforced is a claim, and a session running "
                f"without the network it asked for looks like a hung agent.")

    def _start_sidecar(self):
        p = _docker([
            "run", "-d", "--name", self.container,
            "--network", self.network, "--network-alias", "broker",
            "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
            "-v", f"{self._tmp / 'broker'}:/broker:ro",
            "-v", f"{self._tmp / 'log'}:/log:rw",
            "--entrypoint", "sh", self.image,
            "-c", f"nc -lk -p {BROKER_PORT} -e /broker/broker.sh",
        ])
        if p.returncode != 0:
            raise EgressError(
                f"refusing to launch with egress_allowlist set: the broker sidecar "
                f"would not start — {(p.stderr or p.stdout).strip()[:200]}")
        # The second leg. Without it the broker is on the internal network only and
        # every ALLOW would fail at the far end — an allowlist that permits nothing,
        # which is worse than a refusal because it looks like it is working.
        p = _docker(["network", "connect", self.uplink, self.container])
        if p.returncode != 0:
            raise EgressError(
                f"refusing to launch with egress_allowlist set: the broker could not "
                f"be attached to the uplink network {self.uplink!r} — "
                f"{(p.stderr or p.stdout).strip()[:200]}. Without it the broker has no "
                f"route to anything on the allowlist.")

    def _probe(self, attempts=20, wait=0.25):
        """Ask the broker for a host nobody allowed and require a 403.

        A connection refused, or a timeout, or a 200 are all failures: the first two
        say it is not there, and the third says it is not a broker."""
        want = "403"
        last = ""
        for _ in range(attempts):
            p = self.connect_probe(PROBE_HOST, timeout=8)
            last = (p or "").strip()
            if want in last:
                return last
            time.sleep(wait)
        raise EgressError(
            f"refusing to launch with egress_allowlist set: the broker sidecar did not "
            f"answer a CONNECT probe with 403 (got {last[:120]!r}). It is either not "
            f"listening or not the program this run expects, and either way the "
            f"allowlist would be a claim about a thing that is not there.")

    def connect_probe(self, host, port=443, timeout=8):
        """One CONNECT through the broker, from a throwaway container on the internal
        network. Returns whatever came back, as text. This is also what the tests use:
        the same path a real agent's proxy client takes."""
        req = (f'printf "CONNECT {host}:{port} HTTP/1.1\\r\\nHost: {host}\\r\\n\\r\\n" '
               f'| nc -w {timeout} broker {BROKER_PORT}')
        p = _docker(["run", "--rm", "--network", self.network,
                     "--entrypoint", "sh", self.image, "-c", req],
                    timeout=timeout + DOCKER_TIMEOUT)
        return (p.stdout or "") + (p.stderr or "")

    def alive(self):
        p = _docker(["inspect", "-f", "{{.State.Running}}", self.container], timeout=30)
        return p.returncode == 0 and p.stdout.strip() == "true"

    def assert_alive(self):
        """Called while the session runs. A broker that died mid-session leaves the
        audit container on an internal network with no route out — which is not an
        open network, but is also not the boundary the operator was promised, and a
        session that silently loses its provider is a session whose result means
        nothing."""
        if not self.alive():
            raise EgressError(
                f"the egress broker {self.container!r} stopped while the session was "
                f"running. The session is being killed: whatever it did after the "
                f"broker died happened under a boundary nobody was enforcing, and a "
                f"result from a run whose containment lapsed is not a result.")

    def stop(self):
        """Every path out, including the one where start() raised halfway."""
        text = ""
        if self.log_path and self.log_path.exists():
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        _docker(["rm", "-f", self.container], timeout=60)
        _docker(["network", "rm", self.network], timeout=60)
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None
        self.started = False
        return text

    # -- what the audit container is launched with -------------------------------

    def container_flags(self):
        """The docker flags the audit container gets INSTEAD of `--network=none`.

        The proxy variables travel as values, not by name: they are addresses, not
        secrets, and an agent CLI that cannot see them will happily open a direct
        connection that the internal network then drops with no explanation."""
        proxy = f"http://broker:{BROKER_PORT}"
        return ["--network", self.network,
                "-e", f"HTTP_PROXY={proxy}", "-e", f"HTTPS_PROXY={proxy}",
                "-e", f"http_proxy={proxy}", "-e", f"https_proxy={proxy}",
                "-e", "NO_PROXY=localhost,127.0.0.1"]

    def summary(self):
        return (f"egress: broker {self.container} on internal network {self.network}, "
                f"uplink {self.uplink}, allowlist {', '.join(self.hosts)}")
