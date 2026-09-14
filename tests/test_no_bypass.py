"""The central witness of phase 5: there is no second way in.

F-0147 was "full validation lives only in `lint`, and the other commands walk
past it". The fix is not a better lint. It is that `load_state` is the only door
into machine state, so a command that cannot open it has nothing to run on.

The proof is a counterfactual, not an inspection: stub `load_state` to raise and
invoke each consumer for real. A consumer that still produces its normal output
is reading state from somewhere else — a surviving Markdown path — and the
assertion names which one. `grep` for an import proves nothing about a call that
reaches the old reader through a helper; running the command does.
"""

import ast
import io
import contextlib
import subprocess
import unittest

from tests.harness import REPO, Fixture, xcheck_submodule


class Poison(Exception):
    """What the stubbed reader raises. Distinct from StateError so a consumer
    that catches StateError and continues anyway cannot swallow it silently."""


def _poisoned(*_a, **_kw):
    raise Poison("load_state was called")


class BypassImpossible(unittest.TestCase):
    """With the one reader stubbed out, all six consumers must fail."""

    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)
        # The courier returns early when the session produced no dirt, so its state
        # read would never be reached in a pristine directory. A real repo with the
        # fixture's own files untracked is the state the courier actually runs in.
        subprocess.run(["git", "init", "-q"], cwd=self.fx.root, check=True)
        self.state = xcheck_submodule("state")
        self.cli = xcheck_submodule("cli")
        self.decision = xcheck_submodule("decision")
        self.courier = xcheck_submodule("courier")
        self.saved = {}
        # `from xcheck.state import load_state` binds the name in each importer, so
        # patching only the defining module would leave every call site on the real
        # reader — the patch would no-op and this test would measure the production
        # path while claiming to measure the failure path.
        for mod in (self.state, self.cli, self.decision, self.courier):
            if hasattr(mod, "load_state"):
                self.saved[mod] = mod.load_state
                mod.load_state = _poisoned
        self.assertTrue(self.saved, "load_state is bound in no module — nothing patched")
        self.addCleanup(self._restore)

    def _restore(self):
        for mod, fn in self.saved.items():
            mod.load_state = fn

    def _fails(self, name, call):
        """Run one consumer; return the exception it raised, or fail the test."""
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                result = call()
        except Poison:
            return "raised Poison"
        except SystemExit as e:
            return f"exited: {e.code!r}"
        self.fail(
            f"{name} produced a result with load_state stubbed out — it reads machine "
            f"state through a second path. returned {result!r}; printed "
            f"{buf.getvalue()[:400]!r}")

    def test_all_six_consumers_fail(self):
        proj = self.fx.root
        conf = {"batch_size": "8", "triage_batch_cap": "0", "session_note": "",
                "max_sessions": "1"}
        cases = [
            ("status", lambda: self.cli.cmd_status(proj, conf)),
            ("next", lambda: self.decision.state_and_decision(proj, conf)),
            ("loop", lambda: self.cli.cmd_loop(proj, conf, True, False, 1)),
            ("lint", lambda: self.cli.cmd_lint(proj)),
            ("metrics", lambda: self.cli.cmd_metrics(proj)),
            ("courier", lambda: self.courier.courier_commit(
                proj, "Remediator", "charter", pre=set())),
        ]
        outcomes = {}
        for name, call in cases:
            outcomes[name] = self._fails(name, call)
        print("\nbypass impossibility — each consumer with load_state stubbed to raise:")
        for name, how in outcomes.items():
            print(f"  {name:<8} {how}")
        self.assertEqual(len(outcomes), 6)

    def test_courier_refuses_to_commit_unreadable_state(self):
        """The courier's own check, with the real reader: a `state.json` that does
        not load is never committed. Ships-what-it-cannot-read is the one way a
        broken document could reach the next session's history."""
        for mod, fn in self.saved.items():
            mod.load_state = fn                      # real reader for this one
        fx = Fixture(raw='{"schema_version": 1}\n')
        self.addCleanup(fx.cleanup)
        subprocess.run(["git", "init", "-q"], cwd=fx.root, check=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = self.courier.courier_commit(fx.root, "Remediator", "charter", pre=set())
        self.assertFalse(ok, "the courier committed a state document nothing can read")
        self.assertIn("refusing to commit", buf.getvalue())


class NoConsumerImportsTheLegacyReader(unittest.TestCase):
    """The static half. `migrate_readers.py` exists for phase 6's one-shot
    converter; a consumer importing it would be a Markdown path back into the
    control plane, which is the whole defect this phase removes."""

    # `migrate.py` is the sanctioned importer — it IS the converter the quarantined
    # module exists for. Everything else importing it would be the bypass.
    ALLOWED = ("xcheck/migrate_readers.py:", "xcheck/migrate.py:")

    def test_grep_names_only_the_module_itself(self):
        out = subprocess.run(
            ["grep", "-rn", "--exclude-dir=__pycache__", "migrate_readers", "xcheck/"],
            cwd=REPO, capture_output=True, text=True).stdout
        hits = [ln for ln in out.splitlines() if ln.strip()]
        offenders = [ln for ln in hits
                     if not ln.startswith(self.ALLOWED)
                     and ("import" in ln)]
        self.assertEqual(offenders, [], f"a consumer imports the legacy reader:\n{out}")
        print("\ngrep -rn 'migrate_readers' xcheck/ ->")
        for ln in hits:
            print("  " + ln[:160])

    def test_the_converter_is_not_on_any_command_path(self):
        """Allowing `migrate.py` to import the legacy readers only holds if nothing
        imports `migrate.py` at module scope. `cli.py` imports it INSIDE the dispatch
        branch, so the readers are loaded when — and only when — an operator types
        `xcheck migrate`; importing the package for `next` or `lint` never pulls a
        Markdown reader into the process at all."""
        module_level = []
        for f in sorted((REPO / "xcheck").glob("*.py")):
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=f.name)
            for node in tree.body:                       # top level ONLY, not nested
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                if any(n in ("xcheck.migrate", "xcheck.migrate_readers") for n in names):
                    module_level.append(f"{f.name}:{node.lineno}")
        self.assertEqual(
            [m for m in module_level if not m.startswith("migrate.py")], [],
            "the converter is reachable by importing the package, not only by "
            f"typing `xcheck migrate`: {module_level}")
        print(f"\nmodule-scope importers of the converter: "
              f"{module_level or 'none outside migrate.py'}")

    def test_the_module_is_still_there_for_phase_6(self):
        self.assertTrue((REPO / "xcheck" / "migrate_readers.py").is_file())
        self.assertFalse((REPO / "xcheck" / "legacy_md.py").exists())


class MissingStateIsAnAddressedRefusal(unittest.TestCase):
    """No fallback. A tree with no `state.json` is not silently re-parsed from
    Markdown — it names `xcheck migrate` and stops."""

    def test_every_command_names_the_migration(self):
        fx = Fixture()
        self.addCleanup(fx.cleanup)
        (fx.audit / "state.json").unlink()
        for cmd in ("status", "lint", "metrics", "next"):
            code, out = fx.run(cmd) if cmd != "next" else fx.decision()
            self.assertNotEqual(code, 0, f"{cmd} succeeded with no state document")
            self.assertIn("xcheck migrate", out,
                          f"{cmd} did not name the migration: {out[:200]!r}")


if __name__ == "__main__":
    unittest.main()
