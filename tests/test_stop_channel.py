"""F3 (0.9.6): a stop comes from a deliberate channel, is bound to what the session was
given, halts the same unchanged work in every role, and is lifted by a command that works.

Every case runs the real CLI over a fake agent. The agent's stop is one JSON object in
`$XCHECK_STOP_FILE`; the spec file says what it writes there and what it prints.
"""

import ast
import json
import os
import shlex
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from tests.harness import REPO, Fixture, finding_record, queue_pass, state_doc, xcheck_submodule
from tests.test_fresh_install import FreshInstall
from tests.test_parallel_retry import two_pass_doc

envelope = xcheck_submodule("envelope")
decision = xcheck_submodule("decision")
runner = xcheck_submodule("runner")
cli = xcheck_submodule("cli")

# `{"P-01": {...stop...}}`: the stop to write when the prompt names that key; `"*"` for
# any prompt. `print` is echoed to stdout verbatim — the spoof.
CHILD = textwrap.dedent('''\
    import json, os, sys
    from pathlib import Path
    spec = json.loads(Path(sys.argv[1]).read_text())
    prompt = " ".join(sys.argv[2:])
    Path("audit").mkdir(exist_ok=True)
    Path("audit/partial-proof.md").write_text("partial\\n")
    if spec.get("print"):
        print(spec["print"])
    for key, stop in spec.get("stops", {}).items():
        if key == "*" or key in prompt:
            Path(os.environ["XCHECK_STOP_FILE"]).write_text(json.dumps(stop))
            break
''')

SPOOF = 'XCHECK_STOP {"reason": "material-missing", "target": "P-01", "detail": "quoted"}'
STOPPED = {"reason": "material-missing", "detail": "the dependency is absent"}


def stop(**over):
    return dict(STOPPED, **over)


class Run:
    """One project, one fake agent for every role, and the real CLI over it."""

    def __init__(self, test, spec, doc=None, parallel=False, fresh=False):
        self.tmp = Path(tempfile.mkdtemp(prefix="xc-stop-"))
        test.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "child.py").write_text(CHILD)
        (self.tmp / "spec.json").write_text(json.dumps(spec))
        cmd = f"{sys.executable} {self.tmp / 'child.py'} {self.tmp / 'spec.json'} {{prompt}}"
        conf = "".join(f"{r}_cmd={cmd}\n" for r in ("planner", "auditor", "remediator",
                                                     "verifier"))
        conf += (f"parallel_passes={'on' if parallel else 'off'}\nconstrual_gate=off\n"
                 f"embargo=off\nlog_root={self.tmp}/logs\n")
        if fresh:
            self.f = FreshInstall(git=True, conf=conf + "sandbox_profile=worktree\n")
        else:
            if doc is None:
                doc = two_pass_doc() if parallel else state_doc(
                    queue=[queue_pass("P-01"), queue_pass("P-02")])
            if parallel:
                for q in doc["queue"]:      # the parallel prompt carries the charter text
                    q["charter"] += f" ({q['id']})"
            self.f = Fixture(doc, conf=conf)
            self.f.git_init(extra=("README.md", "src/core.py", "a.py", "b.py"))
        test.addCleanup(self.f.cleanup)

    def cli(self, *args):
        return self.f.run_subprocess(*args)

    def events(self, kind=None):
        rows = [json.loads(ln) for ln in
                (self.f.audit / "events.jsonl").read_text().splitlines() if ln.strip()]
        return [e for e in rows if kind is None or e.get("event") == kind]

    def finished(self):
        return self.events("session_finished")

    def dispatched(self):
        return self.events("session_dispatched")


# ------------------------------------------------------------------ the channel

class TheChannelIsAFileNotTheLog(unittest.TestCase):

    def test_a_quoted_stop_line_is_not_a_stop_serial_and_parallel(self):
        for parallel in (False, True):
            with self.subTest(parallel=parallel):
                r = Run(self, {"print": SPOOF}, parallel=parallel)
                rc, out = r.cli("next", "--yes")
                fin = r.finished()
                print(f"\n  spoof parallel={parallel}: outcomes "
                      f"{[e['outcome'] for e in fin]}, stops {[e.get('stop') for e in fin]}")
                self.assertTrue(fin, out[-600:])
                self.assertEqual({"ok"}, {e["outcome"] for e in fin}, out[-600:])
                self.assertEqual([None] * len(fin), [e.get("stop") for e in fin])

    def test_control_the_same_child_writing_the_file_stops(self):
        for parallel in (False, True):
            with self.subTest(parallel=parallel):
                r = Run(self, {"print": SPOOF, "stops": {"P-01": stop(target="P-01")}},
                        parallel=parallel)
                r.cli("next", "--yes")
                mine = [e for e in r.finished() if (e.get("stop") or {}).get("target")]
                print(f"\n  control parallel={parallel}: {[(e['outcome'], e['stop']) for e in mine]}")
                self.assertEqual(1, len(mine))
                self.assertEqual("refused", mine[0]["outcome"])

    def test_the_file_never_reaches_the_project(self):
        r = Run(self, {"stops": {"P-01": stop(target="P-01")}})
        r.cli("next", "--yes")
        self.assertFalse((r.f.root / envelope.STOP_FILE).exists())
        for p in (r.f.audit / "quarantine").rglob("session.patch"):
            self.assertNotIn(".xcheck-stop.json", p.read_text())

    def test_a_stop_file_present_before_the_session_is_not_its_stop(self):
        r = Run(self, {})
        (r.f.root / envelope.STOP_FILE).write_text(json.dumps(stop(target="P-01")))
        rc, out = r.cli("next", "--yes")
        self.assertEqual("ok", r.finished()[-1]["outcome"], out[-600:])
        self.assertIn("present before the session started", out)

    def test_no_code_path_reads_a_stop_from_log_text(self):
        names = {"STOP_LINE_RE", "stop_in_log", "stop_in_text"}
        hits = []
        for path in sorted((REPO / "xcheck").glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                name = (node.id if isinstance(node, ast.Name) else node.attr
                        if isinstance(node, ast.Attribute) else node.name
                        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) else None)
                if name in names:
                    hits.append(f"{path.name}:{node.lineno} {name}")
        self.assertEqual([], hits)
        # Positive control: the walk does see the name that replaced them.
        self.assertIn("read_stop_file", (REPO / "xcheck" / "runner.py").read_text())


# ------------------------------------------------------------------ binding

class TheStopIsBound(unittest.TestCase):

    def test_the_binding_table(self):
        given, P = ["P-01"], "Auditor"
        rows = [
            ("own target", stop(target="P-01"), given, "P-01", None),
            ("foreign target", stop(target="P-02"), given, None, "P-02"),
            ("no target, one given", stop(), given, "P-01", None),
            ("no target, two given", stop(), ["F-0001", "F-0002"], None, "ambiguous"),
            ("planner", stop(target="PLAN"), ["PLAN"], "PLAN", None),
            ("record before the field", stop(target="P-01"), None, None, "no charter targets"),
        ]
        print("\n  BINDING")
        for label, s, targets, want, why in rows:
            bound, bad = envelope.bind_stop(s, targets, P)
            print(f"    {label:26} -> {bound and bound['target']!s:6} {bad[:1]}")
            self.assertEqual(want, bound and bound["target"], label)
            if why:
                self.assertTrue(bad and why in bad[0], (label, bad))

    def test_serial_a_foreign_target_is_refused_naming_both_and_halts_nothing(self):
        r = Run(self, {"stops": {"*": stop(target="P-02")}})
        r.cli("next", "--yes")
        s = json.loads((r.f.audit / "state.json").read_text())["sessions"][-1]
        self.assertEqual(["P-01"], s["charter_targets"])
        self.assertNotIn("stop", s)
        self.assertIn("P-02", s["note"])
        self.assertIn("P-01", s["note"])
        rc, out = r.cli("next", "--dry-run")
        self.assertNotIn("refusal on", out)
        self.assertIn("Auditor", out)

    def test_parallel_a_foreign_target_is_refused_naming_both(self):
        # Each pass names the OTHER one.
        r = Run(self, {"stops": {"(P-01)": stop(target="P-02"),
                                 "(P-02)": stop(target="P-01")}}, parallel=True)
        r.cli("next", "--yes")
        sessions = json.loads((r.f.audit / "state.json").read_text())["sessions"]
        self.assertEqual(2, len(sessions))
        for s in sessions:
            self.assertNotIn("stop", s)
            self.assertEqual(1, len(s["charter_targets"]))
            self.assertIn("which this Auditor was not given", s["note"])
        rc, out = r.cli("next", "--dry-run")
        self.assertNotIn("refusal on", out)

    def test_parallel_an_own_target_binds(self):
        r = Run(self, {"stops": {"(P-01)": stop(target="P-01")}}, parallel=True)
        r.cli("next", "--yes")
        rc, out = r.cli("next", "--dry-run")
        self.assertIn("refusal on P-01", out)

    def test_a_missing_target_binds_to_the_one_target(self):
        r = Run(self, {"stops": {"*": stop()}})
        r.cli("next", "--yes")
        self.assertEqual("P-01", r.finished()[-1]["stop"]["target"])

    def test_a_missing_target_with_several_is_ambiguous(self):
        doc = state_doc(findings=[finding_record("F-0001", status="accepted"),
                                  finding_record("F-0002", status="accepted")])
        r = Run(self, {"stops": {"*": stop()}}, doc=doc)
        r.cli("next", "--yes")
        s = json.loads((r.f.audit / "state.json").read_text())["sessions"][-1]
        self.assertEqual("Remediator", s["role"])
        self.assertEqual(["F-0001", "F-0002"], s["charter_targets"])
        self.assertNotIn("stop", s)
        self.assertIn("ambiguous", s["note"])


# ------------------------------------------------------------------ every role

ROLE_CASES = {
    # role: (Run kwargs, the stop's target)
    "Planner": (dict(fresh=True), "PLAN"),
    "Auditor": (dict(), "P-01"),
    "Remediator": (dict(doc=state_doc(findings=[finding_record(status="accepted")])),
                   "F-0001"),
    "Verifier": (dict(doc=state_doc(findings=[finding_record(status="fixed",
                                                             fixed_by="a" * 16)])),
                 "F-0001"),
}


class EveryRoleIsHaltedOnce(unittest.TestCase):

    def test_the_roles_are_the_ones_the_prompt_promises_a_stop_to(self):
        promised = {r for r, p in runner.PROMPTS.items() if runner.HONEST_STOP in p}
        self.assertEqual(set(ROLE_CASES), promised)

    def test_loop_dispatches_a_stopped_charter_exactly_once(self):
        print("\n  PER-ROLE LOOP (--max-sessions 3)")
        for role, (kw, target) in ROLE_CASES.items():
            with self.subTest(role=role):
                r = Run(self, {"stops": {"*": stop(target=target)}}, **kw)
                rc, out = r.cli("loop", "--yes", "--max-sessions", "3")
                n = len(r.dispatched())
                print(f"    {role:10} target {target:6} sessions={n} "
                      f"stop={r.finished()[-1].get('stop', {}).get('target')}")
                self.assertEqual(1, n, out[-800:])
                self.assertEqual(target, r.finished()[-1]["stop"]["target"])
                self.assertIn(f"refusal on {target}", out)
                rc, out = r.cli("next", "--dry-run")
                self.assertIn(f"refusal on {target}", out)
                self.assertNotIn(f"=== {role} session ===", out)


# ------------------------------------------------------------------ resolution

class TheStopIsResolvedByTheOperator(unittest.TestCase):

    def stopped(self):
        r = Run(self, {"stops": {"P-01": stop(target="P-01")}})
        r.cli("next", "--yes")
        return r

    def test_the_printed_command_works_verbatim_and_leaves_history(self):
        r = self.stopped()
        rc, out = r.cli("next", "--dry-run")
        hint = out[out.find("refusal on P-01"):]
        cmd = hint[hint.find("`xcheck resolve-stop"):].split("`")[1]
        print(f"\n  hint: {hint.strip()[:400]}\n  running: {cmd}")
        rc, out = r.cli(*shlex.split(cmd)[1:])
        print(f"  -> rc={rc} {out.strip()}")
        self.assertEqual(0, rc, out)
        rc, out = r.cli("next", "--dry-run")
        self.assertNotIn("refusal on P-01", out)
        [res] = r.events("stop_resolved")
        self.assertEqual("P-01", res["target"])
        self.assertEqual("<what changed>", res["resolution"])
        self.assertEqual(r.finished()[-1]["session_id"], res["stop_session"])
        self.assertEqual("P-01", r.finished()[-1]["stop"]["target"], "the stop is kept")

    def test_refused_inside_a_session(self):
        r = self.stopped()
        with mock.patch.dict(os.environ, {"XCHECK_SESSION_ID": "b" * 16}):
            rc, out = r.f.run("resolve-stop", "P-01", "--resolution", "x")
        self.assertNotEqual(0, rc)
        self.assertIn("refused inside a dispatched session", out)
        self.assertEqual([], r.events("stop_resolved"))

    def test_refused_without_a_live_stop(self):
        r = self.stopped()
        rc, out = r.f.run("resolve-stop", "P-02", "--resolution", "x")
        self.assertNotEqual(0, rc)
        self.assertIn("no live stop on 'P-02'", out)

    def test_an_identical_amendment_is_refused_and_lifts_nothing(self):
        r = self.stopped()
        q = json.loads((r.f.audit / "state.json").read_text())["queue"][0]
        rc, out = r.f.run("amend-pass", "P-01", "--charter", q["charter"])
        self.assertNotEqual(0, rc)
        self.assertIn("changed nothing", out)
        rc, out = r.cli("next", "--dry-run")
        self.assertIn("refusal on P-01", out)

    def test_a_changed_charter_lifts_it(self):
        r = self.stopped()
        rc, out = r.f.run("amend-pass", "P-01", "--charter", "a narrower charter")
        self.assertEqual(0, rc, out)
        rc, out = r.cli("next", "--dry-run")
        self.assertNotIn("refusal on P-01", out)

    def test_one_table_for_the_decision_and_the_hint(self):
        self.assertIs(cli.CLEARS_STOP, decision.CLEARS_STOP)
        r = self.stopped()
        rc, out = r.cli("next", "--dry-run")
        for verb in decision.CLEARS_STOP["pass"]:
            self.assertIn(verb, out)

    def test_status_says_who_claimed_it(self):
        r = self.stopped()
        sid = r.finished()[-1]["session_id"]
        rc, out = r.cli("status")
        line = next(ln for ln in out.splitlines() if ln.startswith("stop: P-01"))
        print(f"\n  status: {line}")
        self.assertIn(f"CLAIMED by the Auditor session {sid}, unverified", line)
        self.assertIn('xcheck resolve-stop P-01 --resolution "<what changed>"', line)
        self.assertIn("the dependency is absent", line)
        self.assertIn("the operator lifts it", line)

    def test_a_planner_stop_is_resolved_before_any_state_exists(self):
        r = Run(self, {"stops": {"*": stop(target="PLAN")}}, fresh=True)
        r.cli("next", "--yes")
        self.assertFalse((r.f.audit / "state.json").exists())
        rc, out = r.cli("next", "--dry-run")
        self.assertIn("refusal on PLAN", out)
        rc, out = r.cli("resolve-stop", "PLAN", "--resolution", "material supplied")
        self.assertEqual(0, rc, out)
        rc, out = r.cli("next", "--dry-run")
        self.assertNotIn("refusal on PLAN", out)


# ------------------------------------------------------------------ the schema

class TheFieldIsOptional(unittest.TestCase):

    def test_a_session_recorded_before_the_field_loads(self):
        f = Fixture(state_doc())
        self.addCleanup(f.cleanup)
        prof = runner.resolve_profile({}, "Auditor")
        rec = envelope.dispatch_record(["x"], "Auditor", "P-01", "p", "c" * 16, prof, 0,
                                       "unknown")
        self.assertNotIn("charter_targets", rec)
        envelope.store(f.root, rec, event="session_dispatched")
        st = xcheck_submodule("state").load_state(f.audit)
        self.assertEqual(1, len(st.sessions))


if __name__ == "__main__":
    unittest.main()
