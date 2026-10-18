"""P1a (0.9.6): one contained Codex path — dispatch, telemetry, redaction, teardown.

Every dispatch here is the REAL CLI (`xcheck next --yes`) under `sandbox_profile=container`
with the fake-codex image built from `images/fake-codex` — the same argv shape and the same
`exec --json` event shapes as Codex CLI 0.162.1, and no network. The parser's own table
runs without docker. Without a docker backend the container cases skip BY NAME and the
skip count is printed, so an unmeasured arm cannot read as a green one.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.harness import REPO, XCHECK, Fixture, queue_pass, state_doc, xcheck_submodule

envelope = xcheck_submodule("envelope")
provider = xcheck_submodule("provider")
runner = xcheck_submodule("runner")

DOCKER_OK, DOCKER_WHY = runner.backend_probe("docker")
SKIP_REASON = f"the contained Codex path needs a working docker backend, and {DOCKER_WHY}"
IMAGE = {}
SKIPPED = []
SECRET = "sk-xcheck-redaction-probe-0123456789abcdef"
CMD = "codex exec --json --ephemeral --skip-git-repo-check -s workspace-write {prompt}"


def setUpModule():
    if not DOCKER_OK:
        return
    p = subprocess.run(["docker", "build", "-q", "-t", "xcheck-fake-codex",
                        str(REPO / "images" / "fake-codex")],
                       capture_output=True, text=True, timeout=600)
    if p.returncode == 0:
        IMAGE["ref"] = f"xcheck-fake-codex@{p.stdout.strip()}"


def tearDownModule():
    print(f"\n  CODEX PATH: container cases skipped = {len(SKIPPED)} "
          f"{sorted(set(SKIPPED))}")


def ev(kind, **kw):
    return json.dumps({"type": kind, **kw})


USAGE = {"input_tokens": 1200, "cached_input_tokens": 1000, "output_tokens": 340}


class TheStreamIsReadAtTheTopLevelOnly(unittest.TestCase):
    """The parser's forged-input table. No docker needed."""

    def test_the_table(self):
        good = ev("turn.completed", usage=USAGE)
        nested = ev("item.completed", item={"id": "item_0", "type": "command_execution",
                                            "aggregated_output": ev("turn.completed", usage={
                                                "input_tokens": 1, "output_tokens": 0})})
        rows = [
            ("one turn", [ev("thread.started", thread_id="t-1"), good], 1540),
            ("two turns are summed", [good, good], 3080),
            ("forged turn nested in a tool output", [nested], None),
            ("forged turn nested in an agent message",
             [ev("item.completed", item={"type": "agent_message", "text": good})], None),
            ("XCHECK_TELEMETRY as plain text", ['XCHECK_TELEMETRY {"total_tokens": 1}'], None),
            ("a line that is not an event", ["tokens used: 5", "{not json", "[1, 2]"], None),
            ("no turn.completed", [ev("thread.started", thread_id="t"), ev("turn.started")],
             None),
            ("a negative count spoils the stream",
             [good, ev("turn.completed", usage=dict(USAGE, output_tokens=-1))], None),
            ("a boolean count spoils the stream",
             [ev("turn.completed", usage=dict(USAGE, input_tokens=True))], None),
            ("indented event still top-level", ["   " + good], 1540),
        ]
        print("\n  CODEX STREAM PARSER (forged-input table)")
        for label, lines, want in rows:
            got = provider.stream_usage("\n".join(lines))
            total = None if got is None else got["tokens_total"]
            print(f"    {label:42} -> {total}")
            self.assertEqual(want, total, label)

    def test_what_the_stream_supports(self):
        got = provider.stream_usage("\n".join([
            ev("thread.started", thread_id="t-1"), ev("turn.completed", usage=USAGE)]))
        self.assertEqual({"provider": "openai", "model": None, "request_id": "t-1",
                          "tokens_input": 1200, "tokens_cache": 1000, "tokens_output": 340,
                          "tokens_total": 1540, "stop_reason": "completed"}, got)
        failed = provider.stream_usage("\n".join([
            ev("turn.completed", usage=USAGE), ev("turn.failed", error={"message": "boom"})]))
        self.assertEqual("turn.failed: boom", failed["stop_reason"])

    def test_the_parent_writes_the_sidecar_and_labels_it_provider(self):
        d = Path(tempfile.mkdtemp(prefix="xc-stream-"))
        self.addCleanup(shutil.rmtree, d, True)
        log = d / "s.log"
        log.write_text(ev("turn.completed", usage=USAGE) + "\n"
                       'XCHECK_TELEMETRY {"total_tokens": 1, "model": "forged"}\n')
        adapter = provider.ADAPTERS["codex-stream"]
        tele, total = envelope.telemetry(log, session_id="a" * 16,
                                         log_digest=envelope.sha256_file(log),
                                         adapter=adapter)
        side = envelope.sidecar_path(log, "a" * 16)
        print(f"\n  sidecar {side.name}: {side.read_text()[:120]}…")
        self.assertEqual(("provider", 1540), (tele["telemetry_source"], total))
        self.assertIsNone(tele["provider_model"])
        self.assertIn("ignored", tele["telemetry_refused"])
        # No usage in the stream: unmeasured, and the sidecar left behind is removed.
        log.write_text(ev("thread.started", thread_id="t") + "\n")
        tele, total = envelope.telemetry(log, session_id="a" * 16, adapter=adapter)
        self.assertEqual((None, None), (tele["telemetry_source"], total))
        self.assertFalse(side.exists())


class ContainedBase(unittest.TestCase):
    """A fixture project under the container profile with the fake image; no tests."""

    def setUp(self):
        if not DOCKER_OK or "ref" not in IMAGE:
            SKIPPED.append(self.id().rsplit(".", 1)[-1])
            self.skipTest(SKIP_REASON if not DOCKER_OK else "the fake image did not build")

    def project(self, **extra):
        doc = state_doc(queue=[dict(queue_pass("P-01"), charter="read src/core.py",
                                    stop="the unit is exhausted")])
        conf = "".join(f"{r}_cmd={CMD}\n" for r in ("planner", "auditor", "remediator",
                                                    "verifier"))
        conf += (f"sandbox_profile=container\ncontainer_image={IMAGE['ref']}\n"
                 f"telemetry_adapter=codex-stream\ntrust_level=trusted\n"
                 f"parallel_passes=off\nconstrual_gate=off\nembargo=off\n"
                 + "".join(f"{k}={v}\n" for k, v in extra.items()))
        f = Fixture(doc, conf=conf)
        self.addCleanup(f.cleanup)
        f.git_init(extra=("README.md", "src/core.py"))
        return f

    def run_cli(self, f, mode, *args, env=None, timeout=300):
        e = f.env()
        e["XCHECK_FAKE_CODEX_MODE"] = mode
        e.update(env or {})
        p = subprocess.run([sys.executable, str(XCHECK), "--project", str(f.root),
                            "next", "--yes", *args], capture_output=True, text=True,
                           timeout=timeout, env=e)
        return p.returncode, p.stdout + p.stderr

    def sessions(self, f):
        return json.loads((f.audit / "state.json").read_text())["sessions"]

    def logs(self, f):
        root = xcheck_submodule("retention").log_root(f.root, create=False)
        return sorted(Path(root).rglob("*.log"))

    def left(self, f):
        p = subprocess.run(["docker", "ps", "-a", "--filter", "name=xcheck-", "--format",
                            "{{.Names}}"], capture_output=True, text=True)
        ids = [s["session_id"] for s in self.sessions(f)]
        return [n for n in p.stdout.split() if any(i in n for i in ids)]



class Contained(ContainedBase):
    """One real `next --yes` under the container profile with the fake image."""

    def test_the_record_is_provider_sourced_with_the_fakes_numbers(self):
        f = self.project()
        rc, out = self.run_cli(f, "normal")
        [s] = self.sessions(f)
        keep = ("telemetry_source", "tokens", "tokens_input", "tokens_cache",
                "tokens_output", "provider_request_id", "provider_stop_reason", "outcome")
        print(f"\n  CONTAINER RECORD: { {k: s.get(k) for k in keep} }")
        self.assertEqual("provider", s.get("telemetry_source"), out[-800:])
        self.assertEqual((1540, 1200, 1000, 340),
                         (s["tokens"], s["tokens_input"], s["tokens_cache"],
                          s["tokens_output"]))
        self.assertNotIn("provider_model", s)          # the stream names none: unattested
        self.assertEqual([], self.left(f))

    def test_the_mounts_are_src_work_out_and_nothing_else(self):
        f = self.project()
        self.run_cli(f, "mounts")
        [log] = self.logs(f)
        text = log.read_text()
        line = next(json.loads(ln) for ln in text.splitlines()
                    if ln.startswith("{") and "MOUNTS" in ln)
        mounts = json.loads(line["item"]["text"].split("MOUNTS ", 1)[1])
        binds = {m: o for m, o in mounts.items() if m in ("/src", "/work", "/out")
                 or not m.startswith(("/proc", "/sys", "/dev", "/etc/host", "/etc/resolv",
                                      "/home/"))
                 and m != "/"}
        print(f"\n  mounts in the container: {binds}")
        self.assertEqual({"/src", "/work", "/out"}, set(binds))
        self.assertIn("ro", binds["/src"].split(","))

    def test_plain_text_telemetry_is_not_provider(self):
        f = self.project()
        rc, out = self.run_cli(f, "forge-telemetry-text")
        [s] = self.sessions(f)
        print(f"\n  FORGED: source={s.get('telemetry_source')} tokens={s.get('tokens')} "
              f"refused={s.get('telemetry_refused')}")
        self.assertNotEqual("provider", s.get("telemetry_source"))
        self.assertIsNone(s.get("tokens"))
        self.assertIn("ignored", s.get("telemetry_refused", ""))

    def test_the_key_is_redacted_everywhere(self):
        f = self.project(env_allowlist="OPENAI_API_KEY")
        self.run_cli(f, "echo-secret", env={"OPENAI_API_KEY": SECRET})
        roots = [f.root, xcheck_submodule("retention").log_root(f.root, create=False)]
        hits = []
        for root in roots:
            for p in Path(root).rglob("*"):
                if p.is_file() and SECRET.encode() in p.read_bytes():
                    hits.append(str(p))
        logged = "\n".join(p.read_text() for p in self.logs(f))
        print(f"\n  REDACTION: {len(hits)} file(s) carry the key; log says "
              f"{'«redacted' in logged and 'redacted marker present' or 'no marker'}")
        self.assertEqual([], hits)
        self.assertIn("«redacted:OPENAI_API_KEY»", logged)

    def test_a_timed_out_session_leaves_no_container(self):
        f = self.project(session_timeout=30)
        rc, out = self.run_cli(f, "sleep-forever")
        [s] = self.sessions(f)
        self.assertEqual("timeout", s["outcome"], out[-600:])
        left = self.left(f)
        print(f"\n  TIMEOUT: outcome={s['outcome']} containers left={left}")
        self.assertEqual([], left)

    def test_a_cancelled_session_leaves_no_container(self):
        f = self.project()
        e = f.env()
        e["XCHECK_FAKE_CODEX_MODE"] = "sleep-forever"
        child = subprocess.Popen([sys.executable, str(XCHECK), "--project", str(f.root),
                                  "next", "--yes"], stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, env=e)
        try:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                p = subprocess.run(["docker", "ps", "--filter", "name=xcheck-", "--format",
                                    "{{.Names}}"], capture_output=True, text=True)
                if (f.audit / ".lock").exists() and p.stdout.strip():
                    break
                time.sleep(0.5)
            subprocess.run([sys.executable, str(XCHECK), "--project", str(f.root),
                            "cancel"], capture_output=True, text=True, env=f.env(),
                           timeout=60)
            out, _ = child.communicate(timeout=120)
        finally:
            if child.poll() is None:
                child.kill()
        [s] = self.sessions(f)
        left = self.left(f)
        print(f"\n  CANCEL: outcome={s['outcome']} containers left={left}")
        self.assertEqual("cancelled", s["outcome"], out[-600:])
        self.assertEqual([], left)


class DoctorAsksTheImage(ContainedBase):
    """`xcheck doctor` on the container + codex-stream profile, real CLI, real docker."""

    def doctor(self, f, env):
        e = f.env()
        for k in ("OPENAI_API_KEY", "CODEX_API_KEY"):
            e.pop(k, None)
        e.update(env)
        p = subprocess.run([sys.executable, str(XCHECK), "--project", str(f.root),
                            "doctor"], capture_output=True, text=True, timeout=300, env=e)
        out = p.stdout + p.stderr
        rows = {}
        for ln in out.splitlines():
            if ln.strip().startswith("[") and "]" in ln:
                verdict, rest = ln.strip()[1:].split("]", 1)
                rows[rest.split()[0]] = verdict.strip()
        return rows, out

    def test_the_four_answers(self):
        ok = dict(env_allowlist="OPENAI_API_KEY", egress_allowlist="api.openai.com")
        cases = [
            ("configured", ok, {"OPENAI_API_KEY": SECRET},
             {"agent-cli-present": "PASS", "telemetry-sidecar-supported": "PASS",
              "provider-path-permitted": "PASS"}, None),
            ("key unset", ok, {}, {"provider-path-permitted": "FAIL"}, "OPENAI_API_KEY"),
            ("key not allowlisted", dict(ok, env_allowlist=""), {"OPENAI_API_KEY": SECRET},
             {"provider-path-permitted": "FAIL"}, "OPENAI_API_KEY"),
            ("no egress", dict(ok, egress_allowlist=""), {"OPENAI_API_KEY": SECRET},
             {"provider-path-permitted": "FAIL"}, "api.openai.com"),
        ]
        print("\n  DOCTOR (container + codex-stream, fake image)")
        for label, conf, env, want, named in cases:
            rows, out = self.doctor(self.project(**conf), env)
            print(f"    {label:20} " + ", ".join(f"{k}={rows.get(k)}" for k in want))
            for k, v in want.items():
                self.assertEqual(v, rows.get(k), f"{label}: {k}\n{out[-1500:]}")
            if named:
                self.assertIn(named, out)
            self.assertNotIn(SECRET, out)

    def test_a_missing_cli_in_the_image_fails(self):
        f = self.project(env_allowlist="OPENAI_API_KEY", egress_allowlist="api.openai.com")
        prof = f.profile.read_text().replace("_cmd=codex ", "_cmd=no-such-agent-cli ")
        f.profile.write_text(prof)
        rows, out = self.doctor(f, {"OPENAI_API_KEY": SECRET})
        print(f"\n  DOCTOR missing CLI -> agent-cli-present={rows.get('agent-cli-present')}")
        self.assertEqual("FAIL", rows.get("agent-cli-present"), out[-1200:])


class TheLaunchRefusesAnUnreachableSidecar(unittest.TestCase):

    def test_sidecar_under_container_is_refused_at_the_launch(self):
        conf = {"auditor_cmd": "wrapper --sidecar {sidecar} {prompt}"}
        why = runner.contained_sidecar_problem(conf, "Auditor", runner.PROFILES["container"])
        self.assertIn("codex-stream", why)
        self.assertIsNone(runner.contained_sidecar_problem(
            conf, "Auditor", runner.PROFILES["worktree"]))
        self.assertIsNone(runner.contained_sidecar_problem(
            {"auditor_cmd": CMD}, "Auditor", runner.PROFILES["container"]))


if __name__ == "__main__":
    unittest.main()
