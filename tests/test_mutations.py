"""Mutation witnesses: proof that each guard is LOAD-BEARING, not merely present.

A test that calls a predicate and sees it return False proves the predicate works. It
does not prove the predicate is wired to anything — `retro-6.md` records exactly that
failure in this project: a rule was tested directly while nothing checked it was
consulted by `lint`, and deleting the real hook broke no test.

So each witness here does four things, in one test method:

1. copies the package into a system temp directory (never in-tree — the courier ships
   the project tree, and this project has twice committed scratch artefacts);
2. mutates ONE real guard: a deleted validation call, an inverted comparison, a
   removed hook, a weakened schema;
3. runs a REAL command against a fixture that exercises that guard, and asserts the
   observable outcome changed;
4. runs the CONTROL arm — the unmutated copy, the same probe, the same fixture — and
   asserts it behaves as the guard promises.

Point 4 is the whole discipline. `mutation-harness-needs-its-own-control`: this
project once ran a harness whose baseline was truncated, which made "only its own case
reddened" mean nothing at all — everything reddens against a broken baseline. The
control arm is what separates "this guard is load-bearing" from "this fixture never
worked". It lives in the same method as the mutation so it cannot be skipped alone.

The control copy is made by the SAME copy routine as the mutants, deliberately. A
control that ran from the repo would be green even if the copy routine were dropping
files, and every mutant would then "redden" for the wrong reason.

If a guard cannot be mutated into failure, that is the most valuable output this file
can produce, and it is reported as a failure naming the guard: it means either the
guard is dead code or the command does not consult it.

One honest limitation, visible in the table: several mutants do not sail through — they
fall through to a LATER refusal (usually view drift, because a document the schema
would have rejected cannot render its own views). That is still the guard doing work,
and the witness asserts what actually matters — the guard's own ADDRESSED message is
gone. The audit's phrase for this is exactly right: "missing addressed error". A tool
that refuses for an unrelated reason has not caught the problem, it has tripped over
it, and the operator gets a message about `LEDGER.md` when the truth is a malformed
finding record.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from tests import mutation_report as report
from tests.harness import (
    REPO, Fixture, finding_record, queue_pass, state_doc, construal_record,
    xcheck_submodule,
)

util = xcheck_submodule("util")

PACKAGE_PARTS = ("xcheck", "bin")
PROBE = "_mutation_probe.py"

# One id per role, fixed so a witness's expectation can name it.
SESSION_A = "a" * 16
SESSION_B = "b" * 16

_pristine = None                      # the unmutated copy, shared by every control arm


def copy_tree():
    """The package, in a system temp dir. The one routine both arms go through."""
    dst = Path(tempfile.mkdtemp(prefix="xcheck-mutant-"))
    for part in PACKAGE_PARTS:
        shutil.copytree(REPO / part, dst / part,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (dst / PROBE).write_text(PROBE_SRC, encoding="utf-8")
    return dst


def pristine():
    global _pristine
    if _pristine is None:
        _pristine = copy_tree()
        # A truncated copy would make every mutant look successful. Check the copy
        # itself before trusting anything measured through it.
        missing = [m.name for m in (REPO / "xcheck").glob("*.py")
                   if not (_pristine / "xcheck" / m.name).is_file()]
        if missing:
            raise AssertionError(f"the copy routine dropped {missing} — every witness "
                                 f"measured through it would be meaningless")
    return _pristine


def mutant(module, old, new):
    """A copy with exactly one edit, asserted to be exactly one edit."""
    tree = copy_tree()
    p = tree / "xcheck" / module
    src = p.read_text(encoding="utf-8")
    n = src.count(old)
    if n != 1:
        shutil.rmtree(tree, ignore_errors=True)
        raise AssertionError(
            f"the guard being mutated is not where this witness says it is: "
            f"{module} contains {n} occurrences of {old!r}, expected exactly 1. "
            f"Either the guard moved (update the witness) or it is gone (that is a "
            f"finding, not a test bug).")
    p.write_text(src.replace(old, new), encoding="utf-8")
    return tree


# --------------------------------------------------------------------------
# the probe: identical source in both arms, so only the package differs
# --------------------------------------------------------------------------

PROBE_SRC = textwrap.dedent('''
    """Run one named probe against one project and report the observable outcome.

    Written into BOTH trees byte-identically. Whatever differs between the two runs
    is therefore the mutation and nothing else."""
    import contextlib, io, json, os, sys, time
    from pathlib import Path

    HERE = Path(__file__).resolve().parent
    sys.path.insert(0, str(HERE))

    name, project = sys.argv[1], sys.argv[2]


    def cli(*args):
        from xcheck.cli import main
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                code = main(["xcheck", "--project", project, *args]) or 0
            return code, buf.getvalue()
        except SystemExit as e:
            if isinstance(e.code, int):
                return e.code, buf.getvalue()
            return 1, buf.getvalue() + str(e.code)
        except Exception as e:                      # an UNaddressed failure is data too
            return 3, buf.getvalue() + f"{type(e).__name__}: {e}"


    def probe_status():          return cli("status")
    def probe_lint():            return cli("lint")
    def probe_next():            return cli("next", "--dry-run")
    def probe_unlock():          return cli("unlock")


    def probe_render_then_status():
        """Sync the views FIRST, then ask a consumer to decide. PHASE 5's crux.

        Without this step the drift refusal intercepts every reading-boundary mutant
        before any decision is reached: the fixture cannot render views from a
        document its own reader rejects, so it ships stale ones and `status` refuses
        for drift — the witness then proves only that some message changed.

        `render-views` is the one command that deliberately does not refuse on drift
        (refusing to re-render because the views need re-rendering is a deadlock), so
        it puts both arms in front of the same consumer honestly: in the CONTROL arm
        it fails with the guard's OWN addressed message and renders nothing, and in
        the MUTANT arm it succeeds and `status` goes on to a real decision.

        The payload carries the rendered LEDGER too, because "the invalid record got
        PUBLISHED" is the wrong outcome several of these witnesses need to assert."""
        rc, rout = cli("render-views")
        code, sout = cli("status", "--json")
        led = Path(project) / "audit" / "LEDGER.md"
        return code, json.dumps({
            "render_rc": rc, "render": rout.strip(),
            "status_rc": code, "status": sout.strip(),
            "ledger": led.read_text(encoding="utf-8") if led.is_file() else "",
        })


    def probe_status_beside_the_ledger():
        """`status --json` with whatever LEDGER.md is on disk, side by side.

        No re-render here, deliberately: the hand-edited view IS the subject, so
        repairing it would delete the thing under test. The pair is what shows the
        wrong outcome — two answers about one finding, neither flagged."""
        code, sout = cli("status", "--json")
        led = Path(project) / "audit" / "LEDGER.md"
        return code, json.dumps({
            "status_rc": code, "status": sout.strip(),
            "ledger": led.read_text(encoding="utf-8") if led.is_file() else "",
        })


    def probe_limit_reaches_the_decision():
        """P0 state-immutability, carried to the decision the limit governs.

        Proving `state.limits[...] = 999` is *possible* is half the invariant. The
        half that matters is that the mutated value is then OBEYED: `decision.py`
        reads `reopen_limit` off the same State object, so a value no verb ever wrote
        decides whether the mandatory human stop fires."""
        from xcheck.decision import state_and_decision
        from xcheck.state import load_state
        from xcheck.util import CONF_DEFAULTS, Conf
        import xcheck.state as state_mod

        from dataclasses import fields

        audit = Path(project) / "audit"
        conf = Conf(dict(CONF_DEFAULTS, trust_level="trusted"))
        _s, _u, _c, kind_before, detail_before = state_and_decision(Path(project), conf)

        real = load_state(audit)
        # The walk's own hole predicate: is ANY writable container reachable from a
        # top-level field. Reported on both arms so the table shows the shape of the
        # object, not only the one key this probe pushes on.
        writable = sorted(f.name for f in fields(real)
                          if type(getattr(real, f.name)) in (dict, list, set))
        try:
            real.limits["reopen_limit"] = 99          # no verb wrote this
        except TypeError as e:
            return 1, (f"REFUSED: {type(e).__name__}: {e} | decision stays "
                       f"{kind_before}/{detail_before} | writable fields: {writable}")

        # The consumer decision, re-taken through the real code path against a State
        # whose limit was changed in memory. `load_state` is stubbed to hand back the
        # object we just mutated -- that is exactly the bypass being tested: a
        # consumer holding a validated State and changing it before deciding.
        orig = state_mod.load_state
        try:
            state_mod.load_state = lambda *a, **k: real
            import xcheck.decision as dec
            dec.load_state = lambda *a, **k: real
            _s, _u, _c, kind_after, detail_after = state_and_decision(Path(project), conf)
        finally:
            state_mod.load_state = orig
        return 0, (f"OBEYED an unwritten limit: reopen_limit=99 flipped the decision "
                   f"{kind_before}/{detail_before} -> {kind_after}/{detail_after} | "
                   f"writable fields: {writable}")


    def probe_persist_an_invalid_status():
        """P0 single-write-path: what becomes durable passes the reader's own check.

        A verb builds a valid document, so mutating the writer's validation is only
        observable if something asks it to persist an invalid one. That is precisely
        the one-boundary promise: `write_state` refuses to make durable anything its
        own reader would reject, whoever is calling."""
        from dataclasses import replace
        from xcheck.state import StateError, load_state, write_state

        audit = Path(project) / "audit"
        before = load_state(audit)
        bad = replace(before, findings=tuple(
            replace(f, status="released") for f in before.findings))   # not a §5 status
        try:
            rev = write_state(audit, bad)
        except StateError as e:
            code, out = cli("status")
            return 1, (f"REFUSED at the boundary: {e} | tree still readable: "
                       f"status rc={code}")
        code, out = cli("status")
        doc = json.loads((audit / "state.json").read_text(encoding="utf-8"))
        got = [f"{r['id']}={r['status']}" for r in doc["findings"]]
        return 0, (f"PERSISTED an invalid document: revision {before.state_revision}"
                   f" -> {rev}, on disk {got} | the reader now refuses this tree: "
                   f"status rc={code}")


    def probe_record_verdict_same_session():
        return cli("record-verdict", "F-0001", "--verdict", "closed",
                   "--session", "a" * 16)


    def probe_record_verdict_reopen():
        return cli("record-verdict", "F-0001", "--verdict", "reopened",
                   "--session", "b" * 16)


    def probe_record_fix_wrong_session():
        return cli("record-fix", "F-0001", "--session", "b" * 16)


    def probe_admit_own_construal():
        from xcheck.util import construal_key
        return cli("admit-construal", construal_key("Remediator", "finding F-0001"),
                   "--admitter", "a" * 16)


    def probe_profile_typo():
        from xcheck.runner import resolve_profile
        from xcheck.util import CONF_DEFAULTS, Conf
        raw = dict(CONF_DEFAULTS, trust_level="trusted"); raw["sandbox_profile"] = "wortkree"
        try:
            p = resolve_profile(Conf(raw), "Remediator")
            return 0, f"profile={p.name} isolating={p.isolating}"
        except SystemExit as e:
            return 1, str(e)
        except Exception as e:
            return 3, f"{type(e).__name__}: {e}"


    def probe_skip_permissions():
        from xcheck.runner import PROFILES, refuse_uncontained
        cmd = ["claude", "-p", "--dangerously-skip-permissions"]
        try:
            refuse_uncontained(cmd, PROFILES["none"], "Remediator")
            return 0, "launched with the flag, uncontained"
        except SystemExit as e:
            return 1, str(e)


    def probe_env_allowlist():
        from xcheck.runner import child_environment
        from xcheck.util import CONF_DEFAULTS, Conf
        os.environ["AWS_SECRET_ACCESS_KEY"] = "AKIAsecretvalue"
        env = child_environment(Conf(dict(CONF_DEFAULTS, trust_level="trusted")))
        leaked = "AWS_SECRET_ACCESS_KEY" in env
        return (0 if not leaked else 1), f"leaked={leaked}"


    def probe_set_limit_unknown_key():
        return cli("set-limit", "reopen_limitt", "3")


    def probe_readonly_quoted_path():
        """A read-only Auditor writes a file git has to QUOTE in a diff header.

        The whole 0.8.0 bypass in one probe: the courier decided authorization from the
        human-readable `diff --git` line, and a quoted path left that line with nothing
        the parser could extract, so an empty touched-set read as "changed nothing
        material"."""
        from xcheck.runner import PROFILES, Sandbox
        rel = "\\u00e9.txt"
        sb = Sandbox(Path(project), PROFILES["readonly"], "Auditor")
        wt = sb.enter()
        try:
            (wt / rel).write_text("an Auditor must not be able to write this\\n",
                                  encoding="utf-8")
            sb.collect()
        except SystemExit as e:
            return 1, str(e)
        except Exception as e:
            return 3, f"{type(e).__name__}: {e}"
        finally:
            sb.leave()
        landed = (Path(project) / rel).is_file()
        return 0, (f"APPLIED: the readonly Auditor's change to {rel!r} — a path "
                   f"outside audit/ — went through, in-project={landed}")


    def probe_quarantine_not_applied():
        """A Remediator session ends `provider-error` AFTER changing the material.

        The probe's own answer is the TREE COMPARISON — which material paths moved —
        because "no exception was raised" is compatible with the defect this guard
        stands in front of."""
        import hashlib
        from xcheck.runner import quarantine_bundles, run_session
        from xcheck.util import CONF_DEFAULTS, Conf

        root = Path(project)

        def tree():
            out = {}
            for p in sorted(root.rglob("*")):
                rel = p.relative_to(root).as_posix()
                if not p.is_file() or rel.startswith((".git/", "audit/")):
                    continue
                out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
            return out

        raw = dict(CONF_DEFAULTS, trust_level="trusted")
        raw["sandbox_profile"] = "worktree"
        raw["remediator_cmd"] = ("sh -c 'echo mutant >> material.txt; "
                                 "echo api error: upstream error; exit 1' {prompt}")
        before = tree()
        try:
            rc = run_session(root, Conf(raw), "Remediator", "F-0001")
        except SystemExit as e:
            return 1, str(e)
        after = tree()
        changed = sorted(k for k in after if before.get(k) != after[k])
        return 0, (f"outcome={rc.outcome} tree-changed={changed} "
                   f"bundles={len(quarantine_bundles(root))}")


    def probe_retry_material_effect():
        """The audit's reproduction, driven through the REAL retry loop.

        The session is a double, and it has to be: phase 3 stops a genuinely failed
        session applying anything, so the effect this gate must refuse to repeat can
        now only be put on disk by hand. Everything downstream of the double is the
        shipped code — the gate's own material probe, `retry_decision`, and the
        dispatch loop that acts on its answer.

        The observable is the MARKER FILE, read back off disk. `retry: yes` in the
        console is the defect announcing itself; `effect-2|` in the file is the defect
        happening."""
        import xcheck.cli as cli
        from xcheck import envelope
        from xcheck.runner import RunResult, resolve_profile
        from xcheck.util import CONF_DEFAULTS, Conf

        root = Path(project)
        marker = root / "material.txt"
        calls = []

        def stub(project_, conf_, role, charter, dry_run=False, attempt=0, **kw):
            calls.append(attempt + 1)
            with marker.open("a", encoding="utf-8") as fh:
                fh.write("effect-%d|" % len(calls))
            sid = "%016x" % len(calls)
            rec = envelope.dispatch_record(["true"], role, charter or "", "p", sid,
                                           resolve_profile({}, role),
                                           envelope.current_revision(project_),
                                           head_before="0" * 40, env={}, conf={})
            envelope.store(project_,
                           envelope.finish(rec, 1, "provider-error", 1.0, "0" * 40,
                                           root / "audit" / "state.json"),
                           event="session_finished")
            # The audit's scenario exactly: the session does NOT report the effect,
            # so the project probe is the only thing left that can see it.
            return RunResult(1, "provider-error", session_id=sid, attempt=attempt)

        cli.run_session = stub
        raw = dict(CONF_DEFAULTS, trust_level="trusted")
        raw["retry_limit"] = "1"     # explicit: one retry, so the audit's CALLS=[1, 2]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli._dispatch_with_retry(root, Conf(raw), "Remediator", "F-0001", False,
                                     None, None, sleep=lambda s: None)
        # retry_limit="1" is set explicitly above; nothing here reads the default.
        console = [ln for ln in buf.getvalue().splitlines()
                   if ln.startswith(("probe:", "retry:"))]
        return 0, ("CALLS=%s MATERIAL=%s | %s"
                   % (calls, marker.read_text(encoding="utf-8"), " | ".join(console[:2])))


    def probe_parallel_evidence_survives():
        """The audit's first release blocker, driven through the real dispatcher.

        A session writes its evidence body and its pass report inside a REAL worktree
        and files the finding through the shipped verbs; `run_passes` merges; the
        `finally` destroys the worktree. What is reported is what a human would look at
        afterwards: does the body still carry the session's evidence, is the report
        there, and what does `lint` say.

        The sentinel is read back off disk rather than trusted from the console, because
        the reproduced failure printed `merged pass P-01: 1 finding(s)` on its way to
        losing everything under it."""
        import hashlib
        import subprocess

        from xcheck import parallel
        from xcheck.cli import main
        from xcheck.runner import Sandbox, resolve_profile
        from xcheck.state import load_state
        from xcheck.util import CONF_DEFAULTS, Conf

        root = Path(project)
        base = load_state(root / "audit")
        body_rel, report_rel = "findings/F-0001.md", "passes/P-01-report.md"
        sentinel = "EVIDENCE-SENTINEL-7f3a"

        def session(pid):
            sb = Sandbox(root, resolve_profile({}, "Auditor"), "Auditor-%s" % pid)
            sb.enter()
            try:
                work = Path(sb.workdir) / "audit"
                for rel in (body_rel, report_rel):
                    p = work / rel
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text("## Evidence\\n\\n%s\\n" % sentinel, encoding="utf-8")
                # PHASE 9: the locator is resolved against the subject commit at the
                # write boundary. `audit/XCHECK.md` is in the seed the sandbox was made
                # from, so it locates; the point of this witness is the ARTIFACTS being
                # carried out, and a finding that cannot be filed measures nothing.
                blob = subprocess.run(
                    ["git", "cat-file", "blob", "HEAD:audit/XCHECK.md"],
                    cwd=str(sb.workdir), capture_output=True).stdout
                with contextlib.redirect_stdout(io.StringIO()):
                    main(["xcheck", "--project", str(sb.workdir), "file-finding",
                          "--id", "F-0001", "--title", "a comparison that checks nothing",
                          "--severity", "major", "--dimension", "invariants",
                          "--unit", "U01", "--pass", pid, "--body", body_rel,
                          "--locator", "audit/XCHECK.md:1",
                          "--source-hash", hashlib.sha256(blob).hexdigest()])
                    main(["xcheck", "--project", str(sb.workdir), "record-coverage", pid,
                          "--report", report_rel, "--findings", "F-0001"])
                mutate = parallel.harvest(base, load_state(work), pid, (1, 50))
                return mutate, parallel.stage_artifacts(sb.workdir, sb.base, pid)
            finally:
                sb.leave()

        conf = Conf(dict(CONF_DEFAULTS, trust_level="trusted", parallel_confirm_above="10"))
        with contextlib.redirect_stdout(io.StringIO()):
            parallel.run_passes(root, conf, ["P-01"], session, announce=lambda *a: None)
        audit = root / "audit"
        body = audit / body_rel
        text = body.read_text(encoding="utf-8") if body.is_file() else ""
        missing = [r for r in (body_rel, report_rel) if not (audit / r).is_file()]
        if sentinel not in text:
            missing.append(body_rel + " (present, evidence gone)")
        rc, _out = cli("lint")
        merged = [f.id for f in load_state(audit).findings]
        return 0, ("MERGED=%s BODY_HAS_EVIDENCE=%s REPORT_EXISTS=%s LINT_RC=%s "
                   "MISSING=%s" % (merged, sentinel in text,
                                   (audit / report_rel).is_file(), rc, missing))


    def probe_parallel_worker_peak():
        """P1 #9: the pool bound, MEASURED from inside the sessions.

        `parallel_workers` is a ceiling only if something counts how many sessions were
        genuinely in flight; reading `pool._max_workers` back proves the constructor
        got an argument, not that fewer threads ran. The counter is a read-modify-write
        under one lock, because two threads incrementing an unlocked counter can lose
        an increment — and an undercount is exactly how a violated bound comes to look
        respected."""
        import threading
        from xcheck.parallel import run_passes
        from xcheck.util import CONF_DEFAULTS, Conf

        lock, live, peak = threading.Lock(), [0], [0]

        def session(pid):
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            time.sleep(0.25)              # long enough that overlap is visible, not luck
            with lock:
                live[0] -= 1
            return None                   # nothing to merge: the pool is the subject

        raw = dict(CONF_DEFAULTS, trust_level="trusted")
        raw["parallel_workers"] = "2"
        raw["max_sessions_per_run"] = "10"    # explicit: the OTHER ceilings must not
        raw["parallel_confirm_above"] = "10"  # fire, or they, not the pool, are tested
        pids = ["P-%02d" % i for i in range(1, 7)]
        run_passes(Path(project), Conf(raw), pids, session, announce=lambda *a: None)
        return 0, "workers=2 passes=%d PEAK=%d" % (len(pids), peak[0])


    def probe_timeout_kill():
        from xcheck.runner import run_child
        from xcheck.util import CONF_DEFAULTS, Conf
        raw = dict(CONF_DEFAULTS, trust_level="trusted"); raw["kill_grace"] = "1"
        log = Path(project) / "child.log"
        t0 = time.time()
        rc, outcome, _d = run_child(["sh", "-c", "sleep 6"], Path(project), log,
                                    dict(os.environ), Conf(raw), timeout=2)
        return 0, f"outcome={outcome} elapsed={'short' if time.time()-t0 < 4.5 else 'long'}"


    PROBES = {k[6:]: v for k, v in list(globals().items()) if k.startswith("probe_")}
    code, out = PROBES[name]()
    print(json.dumps({"code": code, "out": out}))
''').lstrip()


def run_probe(tree, name, project, timeout=120):
    p = subprocess.run([sys.executable, str(tree / PROBE), name, str(project)],
                       capture_output=True, text=True, timeout=timeout)
    line = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""
    try:
        r = json.loads(line)
    except json.JSONDecodeError:
        return {"code": p.returncode, "out": (p.stdout + p.stderr)[-800:]}
    return r


# --------------------------------------------------------------------------
# the witness
# --------------------------------------------------------------------------


class MutationWitness(unittest.TestCase):
    """Base for the witnesses. `witness()` is the whole protocol, in one call."""

    maxDiff = None

    def witness(self, *, guard, module, old, new, probe, build, expect_control,
                timeout=120, p0=None, reaches=None, wrong_outcome=None,
                proves=None, barrier=None):
        """Mutate one guard, run one real command, and prove BOTH arms.

        `build()` returns a fresh project path — called once per arm, because a write
        verb changes the state it was given and the two arms must meet the same input.

        PHASE 5 added the strength half. Every witness must declare, by construction,
        which of two things it proves:

        - `wrong_outcome=` + `proves=` + `reaches=` — an oracle run against the MUTANT
          asserting the specific wrong outcome the missing guard permits, at a named
          consumer. Passing that oracle is what classifies the row
          `consumer-reaching`; there is no argument that merely asserts the label.
        - `barrier=("module.py", "symbol", "reason")` — the honest alternative: the
          mutant fell to a later barrier, so all that can be asserted is that the
          guard's own addressed message is gone. The row is `message-only` and the
          intercepting barrier is named, resolved to its CURRENT line when printed.

        Exactly one of the two, because "neither" is how the previous battery came to
        report message-only witnesses as though they were proofs.
        """
        if bool(wrong_outcome) == bool(barrier):
            raise AssertionError(
                f"{guard!r}: a witness declares EITHER a wrong-outcome oracle (with "
                f"`proves` and `reaches`) OR the barrier that intercepts it. It gave "
                f"{'both' if wrong_outcome else 'neither'}.")
        if wrong_outcome and not (proves and reaches):
            raise AssertionError(
                f"{guard!r}: a consumer-reaching witness must name the consumer it "
                f"reached (`reaches`) and state the wrong outcome in words "
                f"(`proves`) — the table is read by people, not by the oracle.")
        ctrl_project = build()
        control = run_probe(pristine(), probe, ctrl_project, timeout)

        # The control arm FIRST, and its failure is the witness's failure: a guard that
        # does not fire on the unmutated code was never being measured.
        self.assertTrue(expect_control(control),
                        f"CONTROL ARM FAILED for {guard!r}: the unmutated package did "
                        f"not behave as the guard promises against this fixture, so "
                        f"nothing this witness reports about the mutant means anything. "
                        f"control={control!r}")

        tree = mutant(module, old, new)
        self.addCleanup(shutil.rmtree, tree, ignore_errors=True)
        mut_project = build()
        mutated = run_probe(tree, probe, mut_project, timeout)

        shown = brief_pair(control, mutated)

        # The COMPLETE observable outcome, not a prefix of it. A truncated comparison
        # is the truncated-baseline mistake in miniature: two runs that differ only
        # past the cut read as identical, and the guard reports itself dead.
        self.assertNotEqual(
            (control["code"], control["out"]), (mutated["code"], mutated["out"]),
            f"THE GUARD IS NOT LOAD-BEARING: {guard!r} was mutated in {module} and "
            f"`{probe}` produced the identical observable outcome. Either the command "
            f"does not consult this guard, or the guard is dead code — both are "
            f"findings worth more than a green test. control={control!r}")
        self.assertFalse(
            expect_control(mutated),
            f"{guard!r}: the mutant still shows the guard's own refusal, so the "
            f"mutation did not reach it. mutated={mutated!r}")

        # The strength half. Only a PASSING oracle earns `consumer-reaching`, so the
        # row cannot claim more than the assertion that just ran.
        if wrong_outcome:
            self.assertTrue(
                wrong_outcome(mutated),
                f"{guard!r} is declared consumer-reaching, but the mutant did not "
                f"produce the wrong outcome it promises ({proves}). Either the mutant "
                f"was intercepted before the decision — in which case this witness is "
                f"message-only and must name its barrier — or the oracle is wrong. "
                f"mutated={mutated!r}")
        report.record(
            guard=guard, module=f"{module}: {old.strip().splitlines()[-1][:46]}",
            probe=probe, p0=p0, reaches=reaches, wrong_outcome=proves,
            barrier=barrier, control=shown[0], mutated=shown[1])


TMPPATH = re.compile(r"/\S*xcheck-(test|mut)[-\w]*\S*")


def _flat(r):
    return TMPPATH.sub("<project>", " ".join(r["out"].split()))


def brief_pair(control, mutated, n=104):
    """The two rows the TABLE prints, ANCHORED AT THE DIVERGENCE.

    A fixed-width prefix is not evidence when the two outcomes agree for the first 200
    characters and differ after — the reader sees two identical strings under a heading
    that claims they differ. So the window starts just before the first differing
    character. (The assertion still compares the outcomes in full; this only decides
    what a human is shown.)"""
    a, b = _flat(control), _flat(mutated)
    i = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]), 0)
    start = 0 if i < n - 24 else max(0, i - 24)
    lead = "" if start == 0 else "…"
    return ((control["code"], lead + a[start:start + n]),
            (mutated["code"], lead + b[start:start + n]))


def refuses_with(*needles):
    def check(r):
        return r["code"] != 0 and all(n in r["out"] for n in needles)
    return check


# --------------------------------------------------------------------------
# 1-4: the reading boundary — schema, cross-record, duplicates, view drift
# --------------------------------------------------------------------------


def payload(r):
    """The `render_then_status` probe's structured result."""
    try:
        return json.loads(r["out"])
    except (json.JSONDecodeError, TypeError):
        return {}


def published(r, *needles):
    """Did the invalid record reach the PUBLISHED index — the artifact a human and
    every downstream reader treat as the audit's answer?"""
    led = payload(r).get("ledger", "")
    return bool(led) and all(n in led for n in needles)


class TheReadingBoundaryIsLoadBearing(MutationWitness):
    """Each of these carries its mutant through `render-views` first, so the views are
    in sync with the document the mutated reader accepted, and only then asks a
    consumer to decide. That ordering is what upgraded these four from message-only:
    before it, the fixture's stale views made every one of them trip the drift refusal
    and the witness could only report that some message had changed."""

    def test_schema_validation_is_consulted_by_every_command(self):
        def build():
            doc = state_doc(findings=[finding_record()])
            doc["findings"][0]["severity"] = "enormous"      # not a §2 severity
            return Fixture(doc=doc, bodies=True).root
        self.witness(
            guard="schema validation (per-record field types)",
            module="state.py", old="    _validate(doc, str(path))",
            new="    pass  # MUTATED: validation deleted at the reading boundary",
            probe="render_then_status", build=build,
            expect_control=refuses_with("severity"),
            reaches="`render-views` then `status --json`",
            proves="F-0001 is published in LEDGER.md carrying severity 'enormous', "
                   "which is not one of the four §2 severities, and `status` counts "
                   "it as a finding and exits 0",
            wrong_outcome=lambda r: (
                published(r, "F-0001", "enormous")
                and payload(r).get("status_rc") == 0
                and '"total": 1' in payload(r).get("status", "")))

    def test_cross_record_validation_is_consulted(self):
        def build():
            doc = state_doc(findings=[finding_record(**{"pass": "P-99"})])
            return Fixture(doc=doc).root
        self.witness(
            guard="cross-record validation (a finding's pass exists in the queue)",
            module="state.py",
            old='        if rec["pass"] not in pids:',
            new='        if False:  # MUTATED: cross-record check removed',
            probe="render_then_status", build=build,
            expect_control=refuses_with("P-99", "not a pass in the queue"),
            reaches="`render-views` then `status --json`",
            proves="F-0001 is published and counted while the pass it claims to come "
                   "from, P-99, is in neither the done nor the queued list — a "
                   "finding produced by no pass, which no coverage report can cover",
            wrong_outcome=lambda r: (
                published(r, "F-0001")
                and payload(r).get("status_rc") == 0
                and "P-99" not in payload(r).get("status", "")))

    def test_duplicate_ids_are_rejected(self):
        def build():
            doc = state_doc(findings=[finding_record(), finding_record()])
            return Fixture(doc=doc).root
        self.witness(
            guard="duplicate rejection (two records, one id)",
            module="state.py",
            old="def _check_unique(records, key, where, what):",
            new=("def _check_unique(records, key, where, what):\n"
                 "    return {r[key] for r in records if key in r}  # MUTATED\n"
                 "def _check_unique_dead(records, key, where, what):"),
            probe="render_then_status", build=build,
            expect_control=refuses_with("is declared twice", "there is no last-wins"),
            reaches="`render-views` then `status --json`",
            proves="two records answer to the id F-0001: LEDGER.md publishes it "
                   "twice and `status` reports 2 findings, so every later lookup by "
                   "that id silently picks one of two records",
            wrong_outcome=lambda r: (
                payload(r).get("ledger", "").count("| F-0001 |") == 2
                and '"total": 2' in payload(r).get("status", "")))

    def test_view_drift_refusal_is_consulted(self):
        def build():
            fx = Fixture(doc=state_doc(findings=[finding_record()]))
            (fx.audit / "LEDGER.md").write_text(
                "| id | title | severity | status | next | updated |\n"
                "|---|---|---|---|---|---|\n"
                "| F-0001 | EDITED BY HAND | critical | closed | — | 2026-08-14 |\n",
                encoding="utf-8")
            return fx.root
        self.witness(
            guard="view-drift refusal (LEDGER.md is a view, not authority)",
            module="views.py", old="    drift = verify_views(state, audit_dir)\n    if drift:",
            new=("    drift = verify_views(state, audit_dir)\n"
                 "    if False:  # MUTATED: drift no longer refused"),
            probe="status_beside_the_ledger", build=build,
            expect_control=refuses_with("LEDGER.md"),
            p0="view-drift-refusal",
            reaches="`status --json`, with the hand-edited LEDGER.md left on disk",
            proves="the tree gives two answers about F-0001 and flags neither: "
                   "`status` exits 0 reporting it `reported`, while the published "
                   "LEDGER.md row a human reads says `closed`",
            wrong_outcome=lambda r: (
                r["code"] == 0
                and '"reported": 1' in payload(r).get("status", "")
                and "closed" in payload(r).get("ledger", "")))


# --------------------------------------------------------------------------
# 5-9: the methodology's own rules, enforced in the write path and the schema
# --------------------------------------------------------------------------


class TheMethodologyRulesAreLoadBearing(MutationWitness):

    def fixed_finding(self, **over):
        rec = finding_record(status="fixed", fixed_by=SESSION_A, **over)
        return state_doc(findings=[rec], **{})

    def test_verifier_is_not_the_fixer(self):
        def build():
            return Fixture(doc=self.fixed_finding()).root
        self.witness(
            guard="verifier != fixer (§7 independence)",
            module="write.py", old="    if fixer and fixer == session:",
            new="    if fixer and fixer is None:  # MUTATED: self-verdict allowed",
            probe="record_verdict_same_session", build=build,
            expect_control=refuses_with("cannot also return the verdict"),
            reaches="`xcheck record-verdict` (the write path, to completion)",
            proves="F-0001 moves fixed -> closed on the verdict of session aaaa…, "
                   "the same session its own record names as the fixer — the audit's "
                   "one structural independence claim, closed by the party it "
                   "exists to exclude",
            wrong_outcome=lambda r: (r["code"] == 0
                                     and "fixed -> closed" in r["out"]
                                     and SESSION_A in r["out"]))

    def test_a_session_may_not_admit_its_own_construal(self):
        def build():
            doc = state_doc(construals=[construal_record(session=SESSION_A)])
            return Fixture(doc=doc).root
        self.witness(
            guard="producer != admitter (§4 rule 9)",
            module="write.py", old='    if admitter == rec["session"]:',
            new="    if False:  # MUTATED: a session may admit its own construal",
            probe="admit_own_construal", build=build,
            expect_control=refuses_with("licensing theatre"),
            barrier=("state.py", 'rec["admitted_by"] == rec["session"]',
                     "the schema's own producer != admitter rule refuses the "
                     "document the verb would write, so the mutant never reaches a "
                     "consumer — defence in depth, and the operator is told their "
                     "document is invalid instead of who may admit a construal"))

    def test_the_reopen_limit_raises_the_human_gate(self):
        def build():
            rec = finding_record(status="fixed", fixed_by=SESSION_A, attempts=1)
            return Fixture(doc=state_doc(findings=[rec])).root
        self.witness(
            guard="reopen_limit gate (the automatic cycle stops for a human)",
            module="write.py", old='        if rec["attempts"] >= limit:',
            new='        if rec["attempts"] > limit:  # MUTATED: off-by-one, gate skipped',
            probe="record_verdict_reopen", build=build,
            expect_control=lambda r: "reopen_limit" in r["out"],
            reaches="`xcheck record-verdict --verdict reopened` (the write path)",
            proves="F-0001 reaches its second consecutive reopen — the point at "
                   "which §9 rule 2 says the automatic cycle stops — and the verdict "
                   "lands with no `⚠ needs-human` and no mention of the limit, so "
                   "the loop keeps spinning unsupervised",
            wrong_outcome=lambda r: (r["code"] == 0
                                     and "reopen_limit" not in r["out"]
                                     and "needs-human" not in r["out"]))

    def test_a_class_finding_must_carry_members(self):
        def build():
            cf = finding_record(fid="CF-0001", members=[], body_path="findings/CF-0001.md")
            return Fixture(doc=state_doc(class_findings=[cf])).root
        self.witness(
            guard="class finding requires a non-empty `members` (F-0151)",
            module="state.py",
            old=('    "members": _list_of(_pattern(ANY_FINDING_ID_RE, "a canonical finding id"),\n'
                 "                        allow_empty=False),"),
            new=('    "members": _list_of(_pattern(ANY_FINDING_ID_RE, "a canonical finding id"),\n'
                 "                        allow_empty=True),  # MUTATED: empty class allowed"),
            probe="render_then_status", build=build,
            expect_control=refuses_with("members"),
            reaches="`render-views` then `status --json`",
            proves="CF-0001 is published as a class finding that absorbs nothing — "
                   "an empty census, so §8 rule 5's re-census closes it vacuously "
                   "and no member is ever fixed",
            wrong_outcome=lambda r: (published(r, "CF-0001")
                                     and payload(r).get("status_rc") == 0))

    def test_a_done_pass_must_carry_its_coverage_record(self):
        def build():
            q = queue_pass(done=True, coverage=False)
            return Fixture(doc=state_doc(queue=[q])).root
        self.witness(
            guard="coverage binding (§4 rule 5: one done pass, one report)",
            module="state.py", old='        if q["done"] and cov is None:',
            new="        if False:  # MUTATED: a done pass needs no coverage record",
            probe="render_then_status", build=build,
            expect_control=refuses_with("carries no coverage record"),
            reaches="`render-views` then `status --json`",
            proves="pass P-01 is reported `done` with no coverage record at all — "
                   "`status` counts 1 done, 0 queued, so the audit reads as complete "
                   "over a pass that never said what it did not cover",
            wrong_outcome=lambda r: (
                payload(r).get("status_rc") == 0
                and '"done": 1' in payload(r).get("status", "")
                and '"queued": []' in payload(r).get("status", "")))


# --------------------------------------------------------------------------
# 10-14: containment — the runner's own promises
# --------------------------------------------------------------------------


class TheContainmentIsLoadBearing(MutationWitness):

    def empty_project(self):
        d = Path(tempfile.mkdtemp(prefix="xcheck-mut-proj-"))
        (d / "audit").mkdir()
        (d / "audit" / "XCHECK.md").write_text("# XCHECK\n", encoding="utf-8")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    def test_an_unknown_sandbox_profile_fails_closed(self):
        self.witness(
            guard="worktree fail-closed (an unreadable profile never becomes `none`)",
            module="runner.py", old="    if name not in PROFILES:",
            new="    if False:  # MUTATED: guess which containment was meant",
            probe="profile_typo", build=self.empty_project,
            expect_control=lambda r: r["code"] == 1 and "is not a profile" in r["out"],
            barrier=("runner.py", "PROFILES[name]",
                     "the dict lookup one line down raises KeyError, so the run dies "
                     "instead of silently downgrading to `none` — the containment "
                     "holds, but by crash rather than by an addressed refusal naming "
                     "the three profiles"))

    def test_skip_permissions_is_refused_outside_containment(self):
        self.witness(
            guard="skip-permissions containment (the grant is contained or refused)",
            module="runner.py", old="    flags = uncontained_grant(cmd, profile)\n    if flags:",
            new=("    flags = uncontained_grant(cmd, profile)\n"
                 "    if False:  # MUTATED: launch it uncontained"),
            probe="skip_permissions", build=self.empty_project,
            expect_control=refuses_with("unattended write access"),
            reaches="`runner.refuse_uncontained`, the launch decision itself",
            proves="a Remediator is launched with --dangerously-skip-permissions "
                   "under profile `none` — unattended write access to the whole "
                   "machine, which is the 0.8.0 default the audit scored 2/10",
            wrong_outcome=lambda r: (r["code"] == 0
                                     and "uncontained" in r["out"])),

    def test_the_child_environment_is_built_not_inherited(self):
        self.witness(
            guard="env allowlist (a cloud credential never reaches the agent)",
            module="runner.py", old="    names = set(ENV_ALLOWLIST)",
            new="    names = set(os.environ)  # MUTATED: inherit everything",
            probe="env_allowlist", build=self.empty_project,
            expect_control=lambda r: r["out"] == "leaked=False",
            reaches="`runner.child_environment`, the environment actually handed "
                    "to the agent process",
            proves="AWS_SECRET_ACCESS_KEY is present in the child's environment — a "
                   "named cloud credential reaching an agent that is about to read "
                   "an untrusted repository",
            wrong_outcome=lambda r: r["out"] == "leaked=True"),

    def test_the_timeout_actually_kills_the_child(self):
        self.witness(
            guard="hard timeout + process-group kill",
            module="runner.py", old="        deadline = t0 + timeout",
            new="        deadline = t0 + timeout + 60  # MUTATED: the bound never arrives",
            probe="timeout_kill", build=self.empty_project,
            expect_control=lambda r: "outcome=timeout" in r["out"], timeout=60,
            reaches="`runner.run_child`, the supervision loop around a live process",
            proves="a child that runs past its deadline is reported `ok` and allowed "
                   "to finish — the hang the lock is held across is never bounded",
            wrong_outcome=lambda r: ("outcome=ok" in r["out"]
                                     and "elapsed=long" in r["out"])),

    def test_a_dead_lease_is_what_makes_a_lock_reclaimable(self):
        def build():
            d = self.empty_project()
            lock = d / "audit" / ".lock"
            lock.mkdir()
            (lock / "owner").write_text(json.dumps({
                "pid": 999999, "role": "Remediator", "started": "2026-08-14T00:00:00Z",
                "host": "elsewhere.invalid", "nonce": "n"}), encoding="utf-8")
            (lock / "heartbeat").write_text(str(time.time() - 4000), encoding="utf-8")
            return d
        self.witness(
            guard="lease TTL (a stopped heartbeat is what reclaims a lock)",
            module="runner.py", old="        return age is not None and age > ttl",
            new="        return False  # MUTATED: no heartbeat is ever stale",
            probe="unlock", build=build,
            expect_control=lambda r: "lock reclaimed" in r["out"],
            reaches="`xcheck unlock`, the reclaim decision",
            proves="a lock whose heartbeat stopped 4000s ago on a dead pid at a "
                   "foreign host is reported not-provably-dead and left standing, "
                   "so the project stays blocked with no route back short of --force",
            wrong_outcome=lambda r: (r["code"] != 0
                                     and "not provably dead" in r["out"])),


# --------------------------------------------------------------------------
# 15: the courier's path authorization (0.9.0 phase 1)
# --------------------------------------------------------------------------


class TheCourierPathAuthorizationIsLoadBearing(MutationWitness):
    """The mutation is a RESTORATION: it puts the pre-0.9.0 header-text parser back,
    inline and complete, rather than deleting a call. A deletion would have crashed with
    a NameError, and a crash differs from the control arm for the wrong reason — the
    witness would go green while proving nothing about the bypass. Restoring the old
    code proves the specific thing the audit claimed: with that parser in the gate, a
    read-only role's edit to the material is APPLIED to the project."""

    def git_project(self):
        """A real repository — the gate asks git, so a directory is not a fixture."""
        d = Path(tempfile.mkdtemp(prefix="xcheck-mut-proj-"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / "audit").mkdir()
        (d / "audit" / "XCHECK.md").write_text("# XCHECK\n", encoding="utf-8")
        (d / "material.txt").write_text("original\n", encoding="utf-8")
        for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                     ["config", "user.name", "test"], ["add", "-A"],
                     ["commit", "-qm", "base"]):
            subprocess.run(["git", *args], cwd=str(d), capture_output=True, timeout=60,
                           check=True)
        return d

    def test_the_read_only_gate_asks_git_not_the_diff_header(self):
        self.witness(
            guard="read-only path authorization (a quoted filename is still a path)",
            module="runner.py",
            # 0.9.1: the git-derived list is now hoisted to `paths` (one call feeding
            # both the authorization and `RunResult.applied_paths`), so the guard's text
            # moved. The MUTATION is unchanged: replace the git-derived source with the
            # 0.8.0 header-text parser and watch the quoted filename walk through.
            old=('            outside = [f for f in paths '
                 'if not f.startswith("audit/")]'),
            new=('            outside = [f for f in [  # MUTATED: 0.8.0 header-text parser\n'
                 '                       ln.partition(" b/")[2] for ln in patch.splitlines()\n'
                 '                       if ln.startswith("diff --git ")\n'
                 '                       and ln.partition(" b/")[2]]\n'
                 '                       if not f.startswith("audit/")]'),
            probe="readonly_quoted_path", build=self.git_project,
            expect_control=refuses_with("é.txt", "does not let this role change the "
                                                 "material"),
            p0="courier-path-authorization",
            reaches="`runner.Sandbox.collect`, the courier's apply decision",
            proves="the file 'é.txt' — a path outside audit/, written by a role whose "
                   "profile is `readonly` — is APPLIED into the project checkout, "
                   "verified by its presence on disk afterwards",
            wrong_outcome=lambda r: (r["code"] == 0
                                     and "in-project=True" in r["out"]
                                     and "é.txt" in r["out"])),


# --------------------------------------------------------------------------
# 20: a failed session's patch is quarantined, never applied (0.9.1 phase 3)
# --------------------------------------------------------------------------


class TheQuarantineGateIsLoadBearing(MutationWitness):
    """The mutation is the DEFECT, restored verbatim.

    0.9.0 applied the sandbox for every outcome except `cancelled`, so a session that
    crashed, timed out, hit a provider error, refused or was blocked landed its
    half-finished work in the project under audit. Putting `if outcome != "cancelled"`
    back is not an invented mutation — it is the shipped code the third audit reproduced,
    and the probe watches the file appear."""

    def git_project(self):
        d = Path(tempfile.mkdtemp(prefix="xcheck-mut-quar-"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / "audit").mkdir()
        (d / "audit" / "XCHECK.md").write_text("# XCHECK\n", encoding="utf-8")
        (d / "material.txt").write_text("original\n", encoding="utf-8")
        for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                     ["config", "user.name", "test"], ["add", "-A"],
                     ["commit", "-qm", "base"]):
            subprocess.run(["git", *args], cwd=str(d), capture_output=True, timeout=60,
                           check=True)
        return d

    def test_restoring_the_old_apply_condition_lands_a_failed_sessions_work(self):
        self.witness(
            guard="only an `ok` session's patch is applied (the rest is quarantined)",
            module="runner.py",
            old=('                    if outcome == "ok":\n'
                 '                        sandbox.collect()'),
            new=('                    if outcome != "cancelled":  # MUTATED: 0.9.0\n'
                 '                        sandbox.collect()'),
            probe="quarantine_not_applied", build=self.git_project,
            expect_control=lambda r: ("tree-changed=[]" in r["out"]
                                      and "bundles=1" in r["out"]),
            p0="failed-session-effects",
            reaches="`runner.run_session`'s apply decision, read off the project tree",
            proves="a provider-error session's half-finished edit to `material.txt` is "
                   "APPLIED to the project — the tree comparison names the file that "
                   "appeared and no quarantine bundle is written",
            wrong_outcome=lambda r: ("tree-changed=['material.txt']" in r["out"]
                                     and "bundles=0" in r["out"]))


class TheRetryMaterialProbeIsLoadBearing(MutationWitness):
    """The mutation narrows the gate's surface back to what 0.9.0 asked.

    0.9.0 decided "did this session already change the project?" from the canonical
    bytes of `audit/state.json` and nothing else. Source files, audit artifacts and git
    HEAD were outside the question, and the audit reproduced the consequence: a session
    whose material effect had already landed, dispatched a second time on the strength
    of a file that had not moved.

    The mutation keeps the probe running — it still walks the project, and still prints
    a truthful `compared N` — and merely refuses to let anything but `audit/state.json`
    block the retry. That is the 0.9.0 surface exactly, and it is the sharper witness:
    the console still shows a gate that looked at three paths, and it decided on none
    of them."""

    def audit_project(self):
        fx = Fixture(doc=state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        (fx.root / "material.txt").write_text("base|", encoding="utf-8")
        for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                     ["config", "user.name", "test"], ["add", "-A"],
                     ["commit", "-qm", "base"]):
            subprocess.run(["git", *args], cwd=str(fx.root), capture_output=True,
                           timeout=60, check=True)
        return fx.root

    def test_narrowing_the_gate_back_to_state_json_repeats_the_effect(self):
        self.witness(
            guard="a retry is refused when the PROJECT changed, not only when "
                  "canonical `state.json` did",
            module="cli.py",
            old=('        blocking = sorted(set(probed) '
                 '| set(getattr(rc, "material_paths", ()) or ()))'),
            new=('        blocking = [p for p in sorted(set(probed)\n'
                 '                                      | set(getattr(rc, '
                 '"material_paths", ()) or ()))\n'
                 '                    if p == "audit/state.json"]  # MUTATED: 0.9.0'),
            probe="retry_material_effect", build=self.audit_project,
            expect_control=lambda r: ("CALLS=[1] MATERIAL=base|effect-1|" in r["out"]
                                      and "retry: no" in r["out"]),
            p0="retry-material-effect",
            reaches="`cli._dispatch_with_retry`'s dispatch loop, read off the marker "
                    "file the session wrote",
            proves="the failed session is dispatched a SECOND time and its material "
                   "effect is applied twice — `CALLS=[1, 2]` and "
                   "`MATERIAL=base|effect-1|effect-2|`, the audit's reproduced output "
                   "verbatim",
            wrong_outcome=lambda r: ("CALLS=[1, 2] MATERIAL=base|effect-1|effect-2|"
                                     in r["out"] and "retry: yes" in r["out"]))


class TheEvidencePreservationIsLoadBearing(MutationWitness):
    """The mutation is the 0.9.0 merge, restored: records out, artifacts left behind.

    The audit's reproduction is the mutant's expected output, line for line —
    `BODY_HAS_EVIDENCE=False`, `REPORT_EXISTS=False`, `LINT_RC=1` — and the control arm
    is the same run on the unmutated package, where all three are the other way round.

    The mutation is deliberately at the DISPATCHER, not at `agent_pass`: what 0.9.0 did
    was merge a pass's records and carry none of its files, and dropping the
    `apply_artifacts` call is that behaviour exactly, with the staging still happening
    so the loss is the merge's and not the session's."""

    def queued_project(self):
        catalogs = {
            "norms": [{"id": "N1", "source": "README.md", "scope": "what it claims"}],
            "dimensions": [{"key": "invariants", "catches": "broken invariants",
                            "norms": ["N1"]}],
            "units": [{"id": "U01", "material": "src/u1.py", "size": "0.4 kloc",
                       "responsibility": "one"}],
        }
        doc = state_doc(queue=[dict(queue_pass("P-01", done=False), units=["U01"])],
                        catalogs=catalogs)
        fx = Fixture(doc)
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        (fx.root / "src").mkdir(exist_ok=True)
        (fx.root / "src" / "u1.py").write_text("def u1():\n    return 1\n",
                                               encoding="utf-8")
        for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                     ["config", "user.name", "test"], ["add", "-A"],
                     ["commit", "-qm", "base"]):
            subprocess.run(["git", *args], cwd=str(fx.root), capture_output=True,
                           timeout=60, check=True)
        return fx.root

    def test_merging_records_without_artifacts_loses_the_evidence(self):
        self.witness(
            guard="a merged pass's ARTIFACTS are carried out of the worktree, not "
                  "only its records",
            module="parallel.py",
            old="                undo = apply_artifacts(project, manifest)",
            new="                undo = None  # MUTATED: 0.9.0 merged records only",
            probe="parallel_evidence_survives", build=self.queued_project,
            expect_control=lambda r: ("BODY_HAS_EVIDENCE=True REPORT_EXISTS=True "
                                      "LINT_RC=0" in r["out"]),
            p0="parallel-evidence-preservation",
            reaches="`xcheck lint` and the finding body on disk, read after the "
                    "worktree was destroyed",
            proves="the finding merges as a record and its evidence is gone with the "
                   "worktree — BODY_HAS_EVIDENCE=False, REPORT_EXISTS=False, LINT_RC=1, "
                   "the audit's reproduced output verbatim, and MISSING names the "
                   "artifacts that vanished",
            wrong_outcome=lambda r: ("MERGED=['F-0001']" in r["out"]
                                     and "BODY_HAS_EVIDENCE=False" in r["out"]
                                     and "REPORT_EXISTS=False" in r["out"]
                                     and "LINT_RC=1" in r["out"]))


class TheParallelWorkerCeilingIsLoadBearing(MutationWitness):
    """The mutation is the shipped 0.9.0 line, restored verbatim.

    `ThreadPoolExecutor(max_workers=len(pass_ids))` made the QUEUE's length the
    concurrency: every disjoint pass got a thread, so a queue of three hundred started
    three hundred billed agent sessions at once. The audit filed it beside the evidence
    loss deliberately — it is the hazard that survives the evidence fix.

    This witness is a load-bearing check on a NUMBER, not on a refusal, so its oracle
    has to be a measurement: the peak concurrency observed from inside the sessions.
    Nothing in the console would have differed — the mutant announces the same passes
    and merges the same nothing. What differs is how many ran at once, and that is only
    visible to a counter the sessions themselves increment."""

    def queued_project(self, n=6):
        queue = [dict(queue_pass("P-%02d" % i, done=False), units=["U%02d" % i])
                 for i in range(1, n + 1)]
        doc = state_doc(queue=queue)
        # A pass naming a unit the catalog does not have refuses to LOAD, and the
        # witness would then measure the schema instead of the pool.
        doc["catalogs"] = dict(doc["catalogs"], units=[
            {"id": "U%02d" % i, "material": "src/u%d.py" % i, "size": "0.4 kloc",
             "responsibility": "a unit"} for i in range(1, n + 1)])
        fx = Fixture(doc)
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        return fx.root

    def test_restoring_the_unbounded_pool_starts_every_queued_pass_at_once(self):
        self.witness(
            guard="the worker pool is bounded by `parallel_workers`, not by the "
                  "length of the queue",
            module="parallel.py",
            old="    with _futures.ThreadPoolExecutor(max_workers=workers) as pool:",
            new="    with _futures.ThreadPoolExecutor(max_workers=len(pass_ids)) as "
                "pool:  # MUTATED: 0.9.0",
            probe="parallel_worker_peak", build=self.queued_project,
            expect_control=lambda r: "PEAK=2" in r["out"],
            reaches="the sessions themselves — the peak is recorded inside "
                    "`session(pid)` under a lock, not read off the executor",
            proves="all six queued passes run CONCURRENTLY against a configured "
                   "ceiling of two: the observed peak is the queue length, which at "
                   "audit scale is one billed agent session per queued pass",
            wrong_outcome=lambda r: "PEAK=6" in r["out"])


# --------------------------------------------------------------------------
# 16: the admin verbs refuse BY NAME (0.9.0 phase 3)
# --------------------------------------------------------------------------


class TheAdminVerbKeyCheckIsLoadBearing(MutationWitness):
    """`set-limit` is the verb that replaced a documented hand-edit, so its refusals
    are the reason the replacement is an improvement rather than a relocation.

    This witness shows the "missing addressed error" shape this module's docstring
    names, and shows it honestly: with the key check stubbed out the tool still exits
    non-zero, because the schema rejects an unknown limit when the document is
    re-validated. What DISAPPEARS is the verb's own message — the operator is told
    their document is invalid instead of being told which keys exist. A tool that
    refuses for a downstream reason has tripped over the problem, not caught it."""

    def test_stubbing_the_key_check_removes_the_addressed_refusal(self):
        self.witness(
            guard="`set-limit` unknown-key refusal (names the key AND the valid set)",
            module="write.py", old="    bounds = LIMIT_FIELDS.get(key)",
            new="    bounds = LIMIT_FIELDS.get(key, (0, 999))  # MUTATED: be liberal",
            probe="set_limit_unknown_key", build=lambda: Fixture(doc=state_doc()).root,
            expect_control=refuses_with("is not a limit this tool reads",
                                        "reopen_limitt"),
            barrier=("state.py", "unknown = sorted(set(limits) - set(LIMIT_FIELDS))",
                     "the schema refuses the unknown limit when `write_state` "
                     "re-validates what it is about to persist, so nothing is "
                     "written — the operator is told their document is invalid "
                     "instead of which keys exist"))


# --------------------------------------------------------------------------
# 17: the loaded State is frozen all the way down (0.9.0 phase 2)
# --------------------------------------------------------------------------


class TheFreezeOnTheLoadedStateIsLoadBearing(MutationWitness):
    """Same restoration discipline as witness 15: the mutant puts the pre-0.9.0
    `limits=dict(...)` back, so the outcome it produces is the defect the audit
    reproduced — not a crash that merely differs from the control.

    P0 `state-immutability`. Showing the assignment SUCCEEDS is only half of it, and
    the weaker half: a writable container nothing reads is a wart, not a hole. So the
    fixture is placed exactly on the gate the limit governs — one finding `reopened`
    with `attempts: 1`, which at `reopen_limit: 2` is the mandatory human stop — and
    the probe re-takes the decision through `decision.state_and_decision` afterwards.
    The wrong outcome is the gate DISAPPEARING on a value no verb ever wrote."""

    def test_a_limit_changed_in_memory_is_obeyed_by_the_decision(self):
        def build():
            rec = finding_record(status="reopened", attempts=1)
            return Fixture(doc=state_doc(findings=[rec])).root
        self.witness(
            guard="deep freeze of a validated State (`limits` / `sessions` containers)",
            module="state.py",
            old="        limits=freeze(doc.get(\"limits\") or {}), catalogs=catalogs,",
            new="        limits=dict(doc.get(\"limits\") or {}), catalogs=catalogs,  "
                "# MUTATED: 0.8.0 plain dict",
            probe="limit_reaches_the_decision", build=build,
            expect_control=refuses_with("REFUSED", "does not support item assignment",
                                        "decision stays stop-needs-human"),
            p0="state-immutability",
            reaches="`decision.state_and_decision` — the routing decision itself",
            proves="`reopen_limit` is raised to 99 in memory by code holding a "
                   "validated State, and the very next decision OBEYS it: F-0001's "
                   "mandatory `stop-needs-human` — §9 rule 2's stop after repeated "
                   "failed remediations — vanishes, and the loop is handed work again",
            wrong_outcome=lambda r: (
                r["code"] == 0
                and "stop-needs-human/F-0001 ->" in r["out"]
                and not r["out"].split("->")[-1].strip().startswith("stop-needs-human")))


# --------------------------------------------------------------------------
# 18: provenance
# --------------------------------------------------------------------------


class TheProvenanceBindingIsLoadBearing(MutationWitness):

    def test_fixed_by_is_bound_to_the_orchestrators_dispatch_record(self):
        def build():
            envelope = xcheck_submodule("envelope")
            doc = state_doc(findings=[finding_record(status="planned")])
            doc["sessions"] = [{
                "session_id": SESSION_A, "role": "Remediator", "provider": "anthropic",
                "agent_model": "unknown", "executable": "claude",
                "executable_version": "unknown", "charter_hash": "0" * 64,
                "prompt_hash": "0" * 64, "state_revision": 1, "head_before": "0" * 40,
                "sandbox_profile": "worktree", "started": "2026-08-14T00:00:00Z",
            }]
            assert envelope.open_dispatch                      # the reader under test
            return Fixture(doc=doc).root
        self.witness(
            guard="`fixed-by` bound to the open dispatch record (F-0159)",
            module="write.py", old="    if claimed != real:",
            new="    if False:  # MUTATED: believe whatever the agent typed",
            probe="record_fix_wrong_session", build=build,
            expect_control=refuses_with("Provenance is taken from the dispatch record"),
            reaches="`xcheck record-fix` (the write path, to completion)",
            proves="F-0001 is recorded fixed by session aaaa… on the word of a "
                   "session that claimed bbbb… — the provenance the verifier != "
                   "fixer rule is enforced against becomes whatever the agent typed",
            wrong_outcome=lambda r: (r["code"] == 0
                                     and "planned -> fixed" in r["out"])),


# --------------------------------------------------------------------------
# 19: the write boundary is the only way state becomes durable (0.9.0 phase 3)
# --------------------------------------------------------------------------


class TheSingleWritePathIsLoadBearing(MutationWitness):
    """P0 `single-write-path`.

    Every verb builds a valid document, so mutating the writer's validation is
    invisible from the CLI: there is nothing invalid for it to let through. The
    promise is stronger than "the verbs behave" — it is that `write_state` refuses to
    make durable anything its own reader would reject, WHOEVER calls it. So the probe
    calls it the way the next contributor's helper script will: load, edit the
    dataclass, write.

    The wrong outcome is not an error message. It is a project that cannot be read
    again: the invalid document is durable at a bumped revision, and every consumer
    now refuses the tree with no verb able to walk it back."""

    def test_the_writer_refuses_to_persist_what_its_reader_would_reject(self):
        def build():
            return Fixture(doc=state_doc(findings=[finding_record()])).root
        self.witness(
            guard="`write_state` re-validates the document it is about to persist",
            module="state.py", old="    _validate(doc, str(final))",
            new="    pass  # MUTATED: persist whatever the caller built",
            probe="persist_an_invalid_status", build=build,
            expect_control=refuses_with("REFUSED at the boundary"),
            p0="single-write-path",
            reaches="`state.write_state` — the durability boundary — then `xcheck "
                    "status` against what actually landed on disk",
            proves="F-0001 is durable at status `released`, which is not one of §5's "
                   "statuses, at a bumped `state_revision` — and `status` then "
                   "refuses the tree it just created, so the write bricked the "
                   "project rather than being rejected at the boundary",
            wrong_outcome=lambda r: (r["code"] == 0
                                     and "PERSISTED an invalid document" in r["out"]
                                     and "F-0001=released" in r["out"]))


# --------------------------------------------------------------------------


class TheWitnessTableIsReported(unittest.TestCase):
    """Printed last (alphabetically after every witness class), so a reader sees the
    whole battery rather than 19 dots — and so the P0 gate sees every row.

    The table is not decoration. Its two columns are the phase-5 claim: a battery may
    only report what its oracles actually asserted, and for the four P0 invariants
    "the error message changed" is not an acceptable standard of proof."""

    def test_zz_print_the_mutation_table(self):
        # "Alphabetically after every witness class" is an invariant held up by NAMING,
        # and a witness named past this one runs after the table is rendered: its row
        # is missing from the table and from the P0 gate below, silently. Phase 7 hit
        # exactly that ("TheWorker…" sorts after "TheWitness…"), so the naming rule is
        # now checked rather than remembered.
        late = sorted(c.__name__ for c in MutationWitness.__subclasses__()
                      if c.__name__ > type(self).__name__)
        self.assertEqual(
            [], late,
            f"{late} sort after {type(self).__name__}, so unittest runs them after the "
            f"table is rendered and their rows never reach it — nor the P0 gate. "
            f"Rename them to sort earlier.")
        rows = report.rows()
        self.assertGreaterEqual(
            len(rows), 23,
            f"only {len(rows)} witnesses reached the table; the battery is declared "
            f"as at least 23 and a shrinking battery must fail, not shrink quietly")
        print(report.render())

    def test_zzz_no_p0_invariant_is_message_only(self):
        """Criterion 4. Derived from the rows, so it cannot be argued with: a P0 row
        is `consumer-reaching` only because an oracle ran against the mutant and
        passed, and a P0 invariant with no witness at all fails here too — an
        invariant nothing mutates is an invariant nothing measures."""
        failures = report.p0_failures()
        c = report.counts()
        self.assertEqual(
            [], failures,
            "the P0 gate is red:\n  - " + "\n  - ".join(failures) +
            f"\n(battery: {c['consumer-reaching']} consumer-reaching, "
            f"{c['message-only']} message-only)")


def tearDownModule():
    if _pristine is not None:
        shutil.rmtree(_pristine, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
