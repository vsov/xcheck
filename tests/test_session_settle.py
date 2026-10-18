"""0.9.6 phase 2: a dispatched session always SETTLES — record finished, work kept, sandbox gone.

F1 (a malformed stop raised inside `finish`, leaving the record open and the partial work
deleted with the sandbox) and F2 (a parallel pass that did not end `ok` returned before
anything captured its work) were two versions of one missing function. `runner.settle`
is that function; both dispatchers call it. These tests drive the REAL dispatchers — the
in-process CLI where a failure has to be injected, the subprocess CLI otherwise — and read
back what is on disk: the record, the bundle's bytes, the worktree list, the containers.
"""
import ast
import errno
import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from tests.harness import REPO, Fixture, queue_pass, state_doc, xcheck_submodule
from tests.test_parallel_retry import two_pass_doc

EVIDENCE = "PARTIAL EVIDENCE THAT MUST SURVIVE"

# Writes partial evidence naming the pass it was given, then ends the way `mode` says. In
# `mixed` mode the P-01 session stops and every other session finishes normally.
CHILD = textwrap.dedent(f'''\
    import json, os, sys
    from pathlib import Path
    mode, prompt = sys.argv[1], " ".join(sys.argv[2:])
    pid = "P-01" if "P-01" in prompt else "P-02"
    Path("audit/partial-proof.md").write_text("{EVIDENCE} " + pid + "\\n")
    if mode == "stop" or (mode == "mixed" and pid == "P-01"):
        Path(os.environ["XCHECK_STOP_FILE"]).write_text(
            json.dumps({{"reason": "material-missing", "target": pid}}))
    else:
        print("child completed " + pid)
''')


def worktrees(root):
    out = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=root,
                         capture_output=True, text=True).stdout
    return [ln for ln in out.splitlines() if ln.startswith("worktree ")]


def containers_left():
    if not shutil.which("docker"):
        return []
    p = subprocess.run(["docker", "ps", "-a", "--filter", "name=xcheck-", "--format",
                        "{{.Names}}"], capture_output=True, text=True)
    return p.stdout.split() if p.returncode == 0 else []


def fixture(test, mode, parallel):
    tmp = Path(tempfile.mkdtemp(prefix="xc-settle-"))
    test.addCleanup(shutil.rmtree, tmp, True)
    (tmp / "child.py").write_text(CHILD)
    cmd = f"{sys.executable} {tmp / 'child.py'} {mode} {{prompt}}"
    doc = two_pass_doc() if parallel else state_doc(queue=[queue_pass("P-01"),
                                                           queue_pass("P-02")])
    # The parallel prompt carries the queue CHARTER, not the pass id, so the fake child
    # can tell the passes apart only if the charter text names them.
    for q in doc["queue"]:
        q["charter"] += f" ({q['id']})"
    f = Fixture(doc, conf=(f"auditor_cmd={cmd}\nparallel_passes={'on' if parallel else 'off'}\n"
                           f"construal_gate=off\nembargo=off\nlog_root={tmp}/logs\n"))
    test.addCleanup(f.cleanup)
    f.git_init(extra=("README.md", "src/core.py", "a.py", "b.py"))
    return f


def sessions(f):
    return json.loads((f.audit / "state.json").read_text())["sessions"]


def bundles(f):
    q = f.audit / "quarantine"
    return sorted(p.parent for p in q.rglob("manifest.json")) if q.exists() else []


def patch_bytes(bundle):
    return (bundle / "session.patch").read_text(encoding="utf-8")


class TheParserIsTotal(unittest.TestCase):
    """Every JSON value, at the top and in every field, is an answer — never an exception."""

    VALUES = [None, True, 0, 1.5, "s", [], ["x"], {}, {"k": 1}]

    def parse(self, payload):
        return xcheck_submodule("envelope").stop_in_bytes(payload.encode())

    def test_every_json_type_as_the_whole_payload(self):
        print("\n  WHOLE PAYLOAD")
        for v in self.VALUES:
            stop, bad = self.parse(json.dumps(v))
            print(f"    {json.dumps(v):10} -> stop={stop} refusal={bad[:1]}")
            if isinstance(v, dict) and not v:
                self.assertIsNone(stop)        # {} has no reason
            self.assertIsNone(stop)
            self.assertTrue(bad, v)

    def test_every_json_type_in_every_field(self):
        good = {"reason": "material-missing", "target": "P-01", "detail": "why"}
        print("\n  PER FIELD")
        for field in good:
            for v in self.VALUES:
                if v == good[field] or (field == "detail" and isinstance(v, str)):
                    continue
                obj = dict(good, **{field: v})
                if field != "reason" and v is None:
                    continue                    # an absent optional field is legal
                stop, bad = self.parse(json.dumps(obj))
                print(f"    {field:7} {json.dumps(v):10} -> {bad[0][:70] if bad else stop}")
                self.assertIsNone(stop, (field, v))
                self.assertTrue(any(field in b or "reason" in b for b in bad), bad)

    def test_malformed_empty_oversize_unknown_and_repeated(self):
        cases = [
            ("{not json", "not one valid JSON object"),
            ("", "empty"),
            (json.dumps({"reason": "material-missing", "detail": "x" * 401}), "too long"),
            (json.dumps({"reason": "material-missing", "x": "y" * 5000}), "too long"),
            (json.dumps({"reason": "material-missing", "extra": 1}), "does not define"),
        ]
        for payload, want in cases:
            stop, bad = self.parse(payload)
            self.assertIsNone(stop, payload[:40])
            self.assertTrue(any(want in b for b in bad), (payload[:40], bad))
        # Repeated: two objects in one file are not one stop, whether or not they agree.
        line = json.dumps({"reason": "material-missing", "target": "P-01"})
        for payload in (line + "\n" + line, "[" + line + "," + line + "]"):
            stop, bad = self.parse(payload)
            self.assertIsNone(stop, payload)
            self.assertTrue(bad, payload)

    def test_control_a_valid_stop(self):
        stop, bad = self.parse(json.dumps({"reason": "material-missing", "target": "P-01",
                                           "detail": "the dependency is absent"}))
        self.assertEqual("material-missing", stop["reason"])
        self.assertEqual([], bad)


class OneSettleForBothDispatchers(unittest.TestCase):
    """AST: inside both dispatchers, every call that resolves a sandbox (`leave`,
    `quarantine`, `finish_session`) sits in a callback handed to `settle(...)`, and
    `leave` is called nowhere at all — `settle` is the only place teardown happens."""

    def dispatcher(self, rel, name, inner=None):
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
        if inner:
            fn = next(n for n in ast.walk(fn) if isinstance(n, ast.FunctionDef) and n.name == inner)
        return fn

    def check(self, fn):
        settle_calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                        and getattr(n.func, "id", None) == "settle"]
        self.assertEqual(1, len(settle_calls), "exactly one settle() per dispatcher")
        call = settle_calls[0]
        handed = {kw.value.id for kw in call.keywords if isinstance(kw.value, ast.Name)}
        handed |= {a.id for a in call.args if isinstance(a, ast.Name)}
        lambdas = [kw.value for kw in call.keywords if isinstance(kw.value, ast.Lambda)]
        callbacks = [n for n in ast.walk(fn) if isinstance(n, ast.FunctionDef)
                     and n.name in handed] + lambdas

        def inside_a_callback(node):
            return any(node in set(ast.walk(cb)) for cb in callbacks)

        found = []
        for n in ast.walk(fn):
            if not isinstance(n, ast.Call):
                continue
            name = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
            if name == "leave":
                found.append(("leave", n.lineno, False))
            elif name in ("quarantine", "finish_session"):
                found.append((name, n.lineno, inside_a_callback(n)))
        print(f"\n  {fn.name}: {found}")
        self.assertFalse([f for f in found if f[0] == "leave"], "a dispatcher calls leave()")
        self.assertTrue(all(ok for _n, _l, ok in found),
                        f"a dispatcher resolves a sandbox outside settle: {found}")
        self.assertTrue(found, "the check found nothing to check")

    def test_the_sequential_dispatcher(self):
        self.check(self.dispatcher("xcheck/runner.py", "run_session"))

    def test_the_parallel_dispatcher(self):
        self.check(self.dispatcher("xcheck/parallel.py", "agent_pass", inner="session"))


class AFailureAfterTheChildRanStillSettles(unittest.TestCase):
    """Injected: an exception after the child ran, on both paths, through the real CLI."""

    def run_with_failure(self, parallel):
        f = fixture(self, "control", parallel)
        runner = xcheck_submodule("runner")
        with mock.patch.object(runner, "result_problem",
                               side_effect=RuntimeError("injected after the child ran")):
            rc, out = f.run("next", "--yes")
        return f, rc, out

    def assert_settled(self, f, n):
        recs = sessions(f)
        self.assertEqual(n, len(recs))
        for s in recs:
            self.assertEqual("crash", s["outcome"])
            self.assertIn("finalize-error: RuntimeError: injected after the child ran",
                          s.get("note", ""))
        found = bundles(f)
        self.assertEqual(n, len(found), found)
        for b in found:
            self.assertIn(f"+{EVIDENCE} P-0", patch_bytes(b))     # the partial's bytes
        self.assertEqual(1, len(worktrees(f.root)), worktrees(f.root))
        self.assertEqual([], containers_left())

    def test_sequential(self):
        f, rc, out = self.run_with_failure(parallel=False)
        self.assertNotEqual(0, rc)
        self.assertIn("failed after it ran", out)
        self.assert_settled(f, 1)

    def test_parallel(self):
        f, rc, out = self.run_with_failure(parallel=True)
        self.assert_settled(f, 2)

    def test_control_without_the_injection_nothing_is_quarantined(self):
        f = fixture(self, "control", parallel=False)
        rc, out = f.run("next", "--yes")
        self.assertEqual(0, rc, out[-600:])
        self.assertEqual("ok", sessions(f)[-1]["outcome"])
        self.assertEqual([], bundles(f))
        self.assertIn(EVIDENCE, (f.audit / "partial-proof.md").read_text())


class ParallelQuarantinesLikeSequential(unittest.TestCase):
    """Same outcome, same bundle: forced through the real child so the work is real."""

    OUTCOMES = ("refused", "timeout", "provider-error", "crash")
    IGNORED = {"captured_at", "session_id", "head_at_capture", "charter", "patch_digest"}

    def run_forcing(self, outcome, parallel):
        f = fixture(self, "control", parallel)
        runner = xcheck_submodule("runner")
        real = runner.run_child

        def forced(*a, **kw):
            rc, _o, elapsed = real(*a, **kw)
            return (0 if outcome == "refused" else 1), outcome, elapsed
        with mock.patch.object(runner, "run_child", side_effect=forced):
            rc, out = f.run("next", "--yes")
        return f, rc, out

    def test_every_non_ok_outcome_files_the_same_bundle(self):
        for outcome in self.OUTCOMES:
            with self.subTest(outcome=outcome):
                fs, rcs, outs = self.run_forcing(outcome, parallel=False)
                fp, rcp, outp = self.run_forcing(outcome, parallel=True)
                [bs] = bundles(fs)
                bp = bundles(fp)
                self.assertEqual(2, len(bp), bp)
                for b in [bs] + bp:
                    self.assertEqual({"session.patch", "manifest.json", "README.md"},
                                     {p.name for p in b.iterdir()})
                ms = json.loads((bs / "manifest.json").read_text())
                for b in bp:
                    mp = json.loads((b / "manifest.json").read_text())
                    self.assertEqual({k: v for k, v in ms.items() if k not in self.IGNORED},
                                     {k: v for k, v in mp.items() if k not in self.IGNORED})
                    self.assertTrue(b.name.endswith(f"-auditor-{outcome}")
                                    or f"-auditor-{outcome}-" in b.name, b.name)
                self.assertEqual([outcome], [s["outcome"] for s in sessions(fs)])
                self.assertEqual([outcome] * 2, [s["outcome"] for s in sessions(fp)])
                self.assertIn(f"outcome: {outcome}", outs)
                self.assertIn(f"finished — {outcome}", outp)


class AMixedParallelBatch(unittest.TestCase):
    def test_the_ok_pass_lands_and_the_refused_pass_is_filed(self):
        f = fixture(self, "mixed", parallel=True)
        rc, out = f.run_subprocess("next", "--yes")
        by = {s["charter_hash"]: s for s in sessions(f)}
        outcomes = sorted(s["outcome"] for s in sessions(f))
        self.assertEqual(["ok", "refused"], outcomes, out[-800:])
        landed = (f.audit / "partial-proof.md").read_text()
        self.assertIn(f"{EVIDENCE} P-02", landed, "the ok pass's work did not land")
        [b] = bundles(f)
        self.assertIn(f"+{EVIDENCE} P-01", patch_bytes(b))
        self.assertNotIn("P-01", landed, "the refused pass's work was applied")
        self.assertTrue(all(s.get("outcome") for s in by.values()))
        self.assertEqual(1, len(worktrees(f.root)))


class AStoreFailureKeepsTheWorkAndSaysWhere(unittest.TestCase):
    def test_enospc_under_the_finish(self):
        f = fixture(self, "stop", parallel=False)
        envelope = xcheck_submodule("envelope")
        real = envelope.store

        def store(project, rec, event=None, live=()):
            if event == "session_finished":
                raise OSError(errno.ENOSPC, "No space left on device")
            return real(project, rec, event=event, live=live)
        with mock.patch.object(envelope, "store", side_effect=store):
            rc, out = f.run("next", "--yes")
        self.assertNotEqual(0, rc)
        self.assertIn("its record could not be written", out)
        self.assertIn("No space left on device", out)
        [b] = bundles(f)
        self.assertIn(str(b.relative_to(f.root)), out.replace(str(f.root) + "/", ""))
        self.assertIn(f"+{EVIDENCE} P-01", patch_bytes(b))
        self.assertIsNone(sessions(f)[-1].get("outcome"), "the record is left OPEN")
        self.assertEqual(1, len(worktrees(f.root)))
        # The next dispatch (dry runs are read-only by design) closes the open record.
        rc2, out2 = f.run("next", "--yes")
        first = sessions(f)[0]
        self.assertEqual("crash", first["outcome"], out2[-600:])


if __name__ == "__main__":
    unittest.main()
