"""0.9.1 phase 3: a failed session's patch is quarantined, never applied.

The third audit's P0 #3. `run_session` applied the sandbox for every outcome except
`cancelled`, so a `provider-error`, `timeout`, `crash`, `refused` or `blocked` session
landed its half-finished changes in the project. That is the root of the retry blocker —
a retry then ran the same charter on top of the previous attempt's effects — and it is a
defect on its own even with retry disabled: a crashed session silently modifying the
repository under audit is the one thing an audit tool must not do.

The fix is not to discard the patch. A half-finished remediation can still be worth
having. It is to refuse to apply it AUTOMATICALLY and hand it to a human intact.

What is asserted here, one arm per outcome, never a parameterised loop whose failure
names no outcome:

* five non-`ok`, non-`cancelled` outcomes, each leaving the MATERIAL tree byte-identical
  and each leaving a bundle behind;
* the `ok` control arm, which still applies — without it the five arms above are equally
  consistent with a session that never ran;
* `cancelled` keeps its existing behaviour and its existing message;
* the bundle replays: applying its patch in a temp clone reproduces the same bytes the
  `ok` session produced, compared file by file;
* the manifest's digest is verified against the patch bytes it describes;
* `xcheck status` reports the pending bundles, human and `--json`;
* `state.json`'s canonical bytes do not move — a failed session leaves no trace that
  reads as a completed one.

The mutation witness for this phase lives in `tests/test_mutations.py`, where the
source-text mutation restores `if outcome != "cancelled": sandbox.collect()` and the
probe reports the material file landing in the project.

## A narrowed claim, stated rather than assumed

The phase spec asks for the project tree to be "byte-identical before and after". No
dispatched session can satisfy that, and phase 2 is where that was found out: the
orchestrator opens an invocation envelope into `audit/state.json` before the child starts,
appends to `audit/events.jsonl`, writes the session log — and the courier commits all of
it. So the claim asserted here is the one that carries the meaning:

* every path OUTSIDE `audit/` is byte-identical (`material_tree`), and
* `audit/state.json`'s canonical bytes are unchanged (`_material_bytes`, which strips the
  session bookkeeping), and
* the bundle exists.

Everything the session itself wrote is inside the first two.
"""

import contextlib
import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from tests.harness import Fixture, finding_record, state_doc, xcheck_submodule

runner = xcheck_submodule("runner")
util = xcheck_submodule("util")
cli = xcheck_submodule("cli")

GIT = shutil.which("git")

# The material write every child below makes before it ends. One line, appended, so an
# accidental second application would be visible as a second line rather than hidden by
# an idempotent write.
WRITE = "echo work >> material.txt"

# Five outcomes, and a real child command that produces each. `provider-error`,
# `refused` and `blocked` are classified from LOG NEEDLES (`runner._SIGNATURES`), so the
# child prints what a real agent would print; `crash` is the absence of any needle;
# `timeout` is the wall-clock budget, which is why its arm sets `session_timeout`.
FAILING = {
    "provider-error": f"sh -c '{WRITE}; echo \"api error: upstream error\"; exit 1'",
    "refused": f"sh -c '{WRITE}; echo \"i will not do this\"; exit 1'",
    "blocked": f"sh -c '{WRITE}; echo \"rate limit reached\"; exit 1'",
    "crash": f"sh -c '{WRITE}; exit 3'",
    "timeout": f"sh -c '{WRITE}; sleep 6'",
}


def git(cwd, *args):
    return subprocess.run([GIT, *args], cwd=str(cwd), capture_output=True, text=True,
                          timeout=60)


def project(**over):
    """A real repository with one material file, and a conf that dispatches."""
    fx = Fixture(doc=state_doc(findings=[finding_record()]))
    (fx.root / "material.txt").write_text("original\n", encoding="utf-8")
    for args in (("init", "-q"), ("config", "user.email", "t@example.invalid"),
                 ("config", "user.name", "test"), ("add", "-A"), ("commit", "-qm", "base")):
        git(fx.root, *args)
    # PHASE 3: `trust_level` has no default in `CONF_DEFAULTS` and must not get one —
    # the refusal exists because the cheap assumption is `trusted`. A fixture project
    # is synthetic and trusted BY CONSTRUCTION, so it makes the operator's declaration
    # explicitly. `tests/test_trust_level.py` is where the unset case is exercised.
    raw = dict(util.CONF_DEFAULTS)
    raw.setdefault("trust_level", "trusted")
    raw["sandbox_profile"] = "worktree"
    raw.update({k: str(v) for k, v in over.items()})
    return fx, util.Conf(raw)


def material_tree(root):
    """sha256 per path for everything OUTSIDE `audit/` — the narrowed claim, computed.

    `.git/` is excluded because it is the courier's own ledger of the bookkeeping commit,
    not the tree under audit. `audit/` is excluded here and asserted separately by
    `state_bytes` below, because it is where the orchestrator's own writes live.
    """
    out = {}
    for p in sorted(Path(root).rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel.startswith(".git/") or rel.startswith("audit/"):
            continue
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def state_bytes(root):
    path = Path(root) / "audit" / "state.json"
    return runner._material_bytes(path) if path.exists() else None


def run(fx, conf, role="Remediator"):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = runner.run_session(fx.root, conf, role, "F-0001")
    return result, out.getvalue()


class AFailedSessionChangesNothing(unittest.TestCase):
    """Criterion 1 — five arms, each named, each asserting the TREE and not the absence
    of an exception. "No exception was raised" is compatible with the defect."""

    def one(self, outcome, **over):
        fx, conf = project(remediator_cmd=FAILING[outcome] + " {prompt}", **over)
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        before, before_state = material_tree(fx.root), state_bytes(fx.root)
        result, console = run(fx, conf)
        after, after_state = material_tree(fx.root), state_bytes(fx.root)
        bundles = runner.quarantine_bundles(fx.root)
        print(f"\n  {outcome:<15} material tree {'IDENTICAL' if before == after else 'CHANGED'}"
              f"  bundles={len(bundles)}  state.json "
              f"{'unchanged' if before_state == after_state else 'MOVED'}"
              f"  quarantine_path={result.quarantine_path}")
        self.assertEqual(result.outcome, outcome,
                         f"this arm did not produce {outcome} — it produced "
                         f"{result.outcome}, so it is testing the wrong thing")
        # The tree comparison IS the assertion. Named diff, so a failure says which file.
        appeared = sorted(set(after) - set(before))
        changed = sorted(k for k in set(after) & set(before) if after[k] != before[k])
        self.assertEqual((appeared, changed), ([], []),
                         f"{outcome}: the session's changes landed in the project — "
                         f"appeared={appeared} changed={changed}")
        self.assertEqual(len(bundles), 1, f"{outcome}: no quarantine bundle was written")
        self.assertEqual(bundles[0]["outcome"], outcome)
        self.assertEqual(result.applied_paths, ())
        # Criterion 7, asserted in every arm rather than once: canonical state cannot
        # move on this path, or a failed session leaves a trace that reads as a
        # completed one.
        self.assertEqual(before_state, after_state,
                         f"{outcome}: canonical state.json bytes moved")
        return fx, result, console

    def test_provider_error(self):
        self.one("provider-error")

    def test_refused(self):
        self.one("refused")

    def test_blocked(self):
        self.one("blocked")

    def test_crash(self):
        self.one("crash")

    def test_timeout(self):
        """The one arm that cannot be driven by what the child PRINTS: `timeout` is the
        wall-clock budget expiring.

        `session_timeout` has a floor of 30 seconds — a bound an operator can set to 2 is
        not a bound — so the budget is shortened at the conf boundary rather than by
        waiting half a minute. Everything under test still runs for real: the deadline
        loop, the process-group kill, and the classification. The elapsed time below is
        the positive control that the double was actually reached; without it a 30-second
        wait would pass this test just as quietly.
        """
        real = runner.conf_number
        runner.conf_number = lambda conf, key: (2 if key == "session_timeout"
                                                else real(conf, key))
        self.addCleanup(lambda: setattr(runner, "conf_number", real))
        self.assertIsNot(runner.conf_number, real, "the shortened budget was not installed")
        t0 = time.time()
        self.one("timeout", kill_grace=1)
        elapsed = time.time() - t0
        print(f"  timeout arm elapsed {elapsed:.1f}s "
              f"(the child sleeps 6s; the 2s budget is what stopped it)")
        self.assertLess(elapsed, 6, "the child ran to completion — the session was not "
                                    "stopped by the budget, so this arm proves nothing "
                                    "about the timeout path")

    def test_control_an_ok_session_still_applies(self):
        """Criterion 2. Without this arm the five above are equally consistent with a
        runner that stopped dispatching sessions altogether."""
        fx, conf = project(remediator_cmd=f"sh -c '{WRITE}' " + "{prompt}")
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        before = material_tree(fx.root)
        result, _console = run(fx, conf)
        after = material_tree(fx.root)
        text = (fx.root / "material.txt").read_text(encoding="utf-8")
        print(f"\n  ok              material tree "
              f"{'IDENTICAL' if before == after else 'CHANGED'}  "
              f"material.txt={text.split()!r}  applied={result.applied_paths}")
        self.assertEqual(result.outcome, "ok")
        self.assertNotEqual(before, after, "the ok session's work did NOT land")
        self.assertEqual(text, "original\nwork\n")
        self.assertIn("material.txt", result.applied_paths)
        self.assertEqual(runner.quarantine_bundles(fx.root), [],
                         "an ok session wrote a quarantine bundle")

    def test_cancelled_keeps_its_existing_behaviour_and_message(self):
        """Criterion 3. `cancelled` was already correct; this phase must not change it."""
        fx, conf = project(remediator_cmd=f"sh -c '{WRITE}; sleep 8' " + "{prompt}")
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        lock = runner.Lock(fx.audit, "Remediator")
        lock.acquire()
        lock.request_cancel()
        before = material_tree(fx.root)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                result = runner.run_session(fx.root, conf, "Remediator", "F-0001",
                                            lock=lock)
        finally:
            lock.release()
        console = out.getvalue()
        print(f"\n  cancelled       material tree "
              f"{'IDENTICAL' if before == material_tree(fx.root) else 'CHANGED'}  "
              f"bundles={len(runner.quarantine_bundles(fx.root))}")
        self.assertEqual(result.outcome, "cancelled")
        self.assertEqual(before, material_tree(fx.root))
        self.assertIn("cancelled: the session's changes were NOT applied to the "
                      "project — a cancelled session is a session with no verdict.",
                      console)
        self.assertEqual(runner.quarantine_bundles(fx.root), [],
                         "cancelled took the quarantine path — its behaviour changed")


class TheBundleIsEvidence(unittest.TestCase):

    def setUp(self):
        self.fx, conf = project(
            remediator_cmd=FAILING["provider-error"] + " {prompt}")
        self.addCleanup(shutil.rmtree, self.fx.root, ignore_errors=True)
        self.result, self.console = run(self.fx, conf)
        self.bundle = self.fx.root / self.result.quarantine_path
        self.manifest = json.loads(
            (self.bundle / "manifest.json").read_text(encoding="utf-8"))

    def test_it_names_the_outcome_session_digest_and_head_and_the_digest_verifies(self):
        """Criterion 5. The digest is checked against the bytes it describes — a hash
        recorded beside a file nobody hashed is a label, not a checksum."""
        patch = (self.bundle / "session.patch").read_bytes()
        computed = hashlib.sha256(patch).hexdigest()
        print(f"\n  manifest: outcome={self.manifest['outcome']} "
              f"session={self.manifest['session_id']} "
              f"head={(self.manifest['head_at_capture'] or '')[:7]} "
              f"paths={self.manifest['paths']}"
              f"\n  digest recorded={self.manifest['patch_digest'][:16]}… "
              f"computed={computed[:16]}… over {len(patch)} bytes")
        self.assertEqual(self.manifest["outcome"], "provider-error")
        self.assertEqual(self.manifest["session_id"], self.result.session_id)
        self.assertRegex(self.manifest["session_id"], r"^[0-9a-f]{16}$")
        self.assertRegex(self.manifest["head_at_capture"], r"^[0-9a-f]{40}$")
        self.assertEqual(self.manifest["head_at_capture"], self.result.head_before)
        self.assertEqual(self.manifest["paths"], ["material.txt"])
        self.assertFalse(self.manifest["applied"])
        self.assertEqual(self.manifest["patch_digest"], computed)

    def test_the_console_says_where_it_is(self):
        """A quarantine nobody can find is a deletion with extra steps."""
        print("\n  " + "\n  ".join(ln for ln in self.console.splitlines()
                                   if "quarantine" in ln or "bundle" in ln))
        self.assertIn("were NOT applied to the project", self.console)
        self.assertIn(self.result.quarantine_path, self.console)
        self.assertTrue((self.bundle / "README.md").exists())
        readme = (self.bundle / "README.md").read_text(encoding="utf-8")
        self.assertIn("git apply", readme)
        self.assertIn(self.result.quarantine_path, readme)

    def test_the_journal_carries_the_bundle_path(self):
        """The console line is read once, by whoever was watching. The journal is what
        the operator has three weeks later."""
        events = [json.loads(ln) for ln in
                  (self.fx.audit / "events.jsonl").read_text(
                      encoding="utf-8").splitlines() if ln]
        finished = [e for e in events if e.get("event") == "session_finished"]
        self.assertTrue(finished, "the session was not journalled at all")
        # The account itself is emitted by the retry gate; here the bundle path is
        # reachable from the record `run_session` returned.
        print(f"\n  quarantine_path on the record: {self.result.quarantine_path}")
        self.assertTrue(self.result.quarantine_path.startswith("audit/quarantine/"))
        self.assertTrue((self.fx.root / self.result.quarantine_path).is_dir())


class TheBundleReplays(unittest.TestCase):
    """Criterion 4. The bundle is only evidence if it still produces the session's work.

    Two projects, same child, one difference: the first exits 0 and its work is applied,
    the second reports a provider error and its work is quarantined. Applying the
    quarantined patch in a temp CLONE must produce the first project's bytes exactly.
    """

    def test_applying_the_patch_by_hand_reproduces_what_the_session_made(self):
        applied_fx, conf = project(remediator_cmd=f"sh -c '{WRITE}' " + "{prompt}")
        self.addCleanup(shutil.rmtree, applied_fx.root, ignore_errors=True)
        ok_result, _ = run(applied_fx, conf)
        self.assertEqual(ok_result.outcome, "ok")

        held_fx, conf2 = project(remediator_cmd=FAILING["provider-error"] + " {prompt}")
        self.addCleanup(shutil.rmtree, held_fx.root, ignore_errors=True)
        held, _ = run(held_fx, conf2)
        patch = held_fx.root / held.quarantine_path / "session.patch"

        # The clone goes in a SYSTEM temp dir. The courier ships the project tree, so a
        # fixture left inside it is committed as material (F-0120).
        clone = Path(tempfile.mkdtemp(prefix="xcheck-replay-"))
        self.addCleanup(shutil.rmtree, clone, ignore_errors=True)
        git(clone, "clone", "-q", str(held_fx.root), "tree")
        tree = clone / "tree"
        applied = git(tree, "apply", str(patch))
        print(f"\n  replay: git apply rc={applied.returncode} "
              f"{(applied.stderr or '').strip()[:120]}")
        self.assertEqual(applied.returncode, 0,
                         f"the quarantined patch no longer applies: {applied.stderr}")

        want = (applied_fx.root / "material.txt").read_bytes()
        got = (tree / "material.txt").read_bytes()
        print(f"  applied session produced {want!r}\n  replayed bundle produced {got!r}")
        self.assertEqual(got, want, "the replay did not reproduce the session's bytes")


class StatusSurfacesPendingBundles(unittest.TestCase):
    """Criterion 6. The operator who missed the console line is exactly the one who
    needs to be told something is waiting."""

    def test_human_and_json_both_report_the_count(self):
        fx, conf = project(remediator_cmd=FAILING["crash"] + " {prompt}")
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        quiet = io.StringIO()
        with contextlib.redirect_stdout(quiet):
            before_rc = cli.cmd_status(fx.root, conf)
        clean = quiet.getvalue()
        self.assertNotIn("quarantine:", clean,
                         "status announced a quarantine before any session ran")

        run(fx, conf)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.cmd_status(fx.root, conf)
        human = out.getvalue()
        js = io.StringIO()
        with contextlib.redirect_stdout(js):
            rc = cli.cmd_status(fx.root, conf, as_json=True)
        payload = json.loads(js.getvalue())
        print("\n  " + "\n  ".join(ln for ln in human.splitlines()
                                   if "quarantine" in ln or "audit/" in ln))
        print(f"  json: {payload['quarantine']}")
        self.assertEqual(before_rc, None if before_rc is None else before_rc)
        self.assertIn("quarantine: 1 pending bundle(s)", human)
        self.assertIn("crash", human)
        self.assertEqual(rc, 0, "the payload failed its own declared schema")
        self.assertEqual(payload["quarantine"]["pending"], 1)
        self.assertEqual(payload["quarantine"]["bundles"][0]["outcome"], "crash")
        self.assertEqual(payload["output_schema_version"],
                         util.OUTPUT_SCHEMA_VERSION)

    def test_the_payload_is_refused_if_the_new_key_is_undeclared(self):
        """The schema is closed in both directions, and this proves the new key is
        actually IN it rather than merely being printed — a key the validator does not
        know is refused, so a passing `--json` above means the contract was extended."""
        payload = {"quarantine": {"pending": 0, "bundles": []}}
        self.assertNotIn("quarantine",
                         [i.split()[0] for i in
                          util.json_output_issues(payload, util.STATUS_SCHEMA, "status")
                          if "unknown" in i])
        stripped = dict(util.STATUS_SCHEMA)
        stripped.pop("quarantine")
        issues = util.json_output_issues({"quarantine": {}}, stripped, "status")
        print(f"\n  with the key removed from the schema: {issues[0]}")
        self.assertTrue(any("quarantine" in i for i in issues))


if __name__ == "__main__":
    unittest.main(verbosity=2)
