"""Auditing what changed, and the blind spot that buys.

The audit's economics point is that auditing an unchanged tree costs the same as
auditing a changed one. Its WARNING about the fix is louder, and this module is built
around the warning rather than the optimisation: *incremental mode without a periodic
full sweep becomes a blind spot.* A defect in a file nobody has touched for a year is
exactly the defect no diff will ever select.

So four things are asserted here, in the order they could go wrong:

  1. the expansion is a SUPERSET of the naive changed-file set, with a case where the
     neighbourhood catches a unit the diff alone would have missed;
  2. `xcheck status` reports how old the last unscoped pass is — WHETHER OR NOT diff
     mode is on, because the operator this protects is the one who turned it on months
     ago and forgot;
  3. the flag is OFF and nothing changes for an operator who never opts in;
  4. an empty selection is reported as "nothing changed in scope since <base>" and
     never as an audit that found nothing.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

from tests.harness import (needs_live_corpus, 
    Fixture, REPO, XCHECK, queue_pass, state_doc, xcheck_submodule,
)

scope = xcheck_submodule("scope")
pp = xcheck_submodule("preprocess")
state_mod = xcheck_submodule("state")
util = xcheck_submodule("util")

# A unit map over a fixture tree whose import shape is the whole point:
#
#   src/leaf.py     imported by NOBODY      -> U-LEAF
#   src/hub.py      imported by two files   -> U-HUB
#   src/a.py        imports hub             -> U-A
#   src/b.py        imports hub             -> U-B
#
# A change to `leaf` selects exactly one unit. A change to `hub` selects three: itself
# and the two that import it — and NEITHER of those files appears in the git diff.
CATALOGS = {
    "norms": [{"id": "N1", "source": "README.md", "scope": "what the tool claims"}],
    "dimensions": [{"key": "invariants", "catches": "broken invariants",
                    "norms": ["N1"]}],
    "units": [
        {"id": "U-LEAF", "material": "src/leaf.py", "size": "0.1 kloc",
         "responsibility": "a module nobody imports"},
        {"id": "U-HUB", "material": "src/hub.py", "size": "0.1 kloc",
         "responsibility": "the module two others import"},
        {"id": "U-A", "material": "src/a.py", "size": "0.1 kloc",
         "responsibility": "an importer of the hub"},
        {"id": "U-B", "material": "src/b.py", "size": "0.1 kloc",
         "responsibility": "the other importer"},
    ],
}
TREE = {
    "src/leaf.py": "def leaf():\n    return 1\n",
    "src/hub.py": "def hub():\n    return 2\n",
    "src/a.py": "from src.hub import hub\n\n\ndef a():\n    return hub()\n",
    "src/b.py": "from src.hub import hub\n\n\ndef b():\n    return hub()\n",
}


def project(case):
    """A fixture that is a git repository with the tree above committed."""
    doc = state_doc(catalogs=CATALOGS,
                    queue=[dict(queue_pass("P-01", done=False), units=["U-LEAF"]),
                           dict(queue_pass("P-02", done=False), units=["U-HUB"]),
                           dict(queue_pass("P-03", done=False), units=["U-A", "U-B"])],
                    findings=[])
    fx = Fixture(doc)
    case.addCleanup(fx.cleanup)
    for rel, text in TREE.items():
        p = fx.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    fx.git_init(extra=())
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=fx.root,
                          capture_output=True, text=True).stdout.strip()
    return fx, base


def touch(fx, rel, extra="\n\n\ndef added():\n    return 0\n"):
    p = fx.root / rel
    p.write_text(p.read_text(encoding="utf-8") + extra, encoding="utf-8")


def select(fx, base):
    st = state_mod.load_state(fx.audit)
    return st, scope.select(st, pp.build_sheet(fx.root, base=base), base)


class TheNeighbourhoodIsASupersetOfTheDiff(unittest.TestCase):

    def test_control_a_unit_nobody_imports_selects_exactly_one(self):
        fx, base = project(self)
        touch(fx, "src/leaf.py")
        _st, sel = select(fx, base)
        print(f"\n  LEAF       changed {sel['changed']} -> units {sel['units']} "
              f"(reached by dependency: {sel['reached_by_dependency']})")
        self.assertEqual(["src/leaf.py"], sel["changed"])
        self.assertEqual(["U-LEAF"], sel["units"])
        self.assertEqual([], sel["reached_by_dependency"])

    def test_a_widely_imported_unit_selects_its_dependents(self):
        """The case the diff alone would miss: `a.py` and `b.py` are BYTE-IDENTICAL to
        their committed versions and appear in no diff, yet a change to what they import
        is a change to them."""
        fx, base = project(self)
        touch(fx, "src/hub.py")
        _st, sel = select(fx, base)
        print(f"  HUB        changed {sel['changed']} -> units {sel['units']}")
        print(f"             reached ONLY by dependency: "
              f"{sel['reached_by_dependency']}")
        self.assertEqual(["src/hub.py"], sel["changed"])
        self.assertEqual(["U-A", "U-B", "U-HUB"], sel["units"])
        self.assertEqual(["src/a.py", "src/b.py"], sel["reached_by_dependency"])
        # And the naive answer, stated beside it, so the difference is the measurement.
        naive = scope.units_for(_st, sel["changed"])
        print(f"             the diff alone would have selected {naive}, "
              f"missing {sorted(set(sel['units']) - set(naive))}")
        self.assertEqual(["U-HUB"], naive)

    def test_the_expansion_is_always_a_superset_of_the_changed_set(self):
        """Asserted as a PROPERTY over several diffs, not on one example: an expansion
        that ever dropped a changed file would be auditing less than the diff while
        calling itself an expansion."""
        for rel in TREE:
            with self.subTest(changed=rel):
                fx, base = project(self)
                touch(fx, rel)
                _st, sel = select(fx, base)
                self.assertTrue(set(sel["changed"]) <= set(sel["neighbourhood"]))
                self.assertGreaterEqual(len(sel["neighbourhood"]), len(sel["changed"]))

    def test_the_counts_for_both_control_arms_are_printed(self):
        rows = []
        for rel, label in (("src/leaf.py", "no dependents"),
                           ("src/hub.py", "two dependents")):
            fx, base = project(self)
            touch(fx, rel)
            _st, sel = select(fx, base)
            rows.append((label, rel, len(sel["changed"]), len(sel["neighbourhood"]),
                         len(sel["units"]), len(sel["passes"])))
        print("\n  SCOPE TABLE (fixture)")
        print("    change             changed  expanded  units  passes")
        for label, _rel, c, n, u, p in rows:
            print(f"    {label:<18} {c:>7}  {n:>8}  {u:>5}  {p:>6}")
        self.assertEqual([1, 1], [r[2] for r in rows])
        self.assertEqual([1, 3], [r[3] for r in rows])
        self.assertEqual([1, 3], [r[4] for r in rows])


class AnEmptySelectionIsNotACleanAudit(unittest.TestCase):

    def test_nothing_changed_is_reported_with_the_base_named(self):
        fx, base = project(self)
        _st, sel = select(fx, base)
        print(f"\n  EMPTY      {sel['note']}")
        self.assertEqual([], sel["changed"])
        self.assertIn("nothing changed in scope since", sel["note"])
        self.assertIn(base, sel["note"])
        self.assertIn("not an audit result", sel["note"])

    def test_a_change_outside_every_units_material_says_so(self):
        """The quieter emptiness: files DID change, and none of them is declared
        material of any unit. That is a gap in the unit map, and reporting it as
        "nothing to audit" would hide the gap behind a clean-looking result."""
        fx, base = project(self)
        (fx.root / "untracked_by_the_map.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=fx.root, capture_output=True)
        _st, sel = select(fx, base)
        print(f"  UNMAPPED   {sel['note']}")
        self.assertTrue(sel["changed"])
        self.assertEqual([], sel["units"])
        self.assertIn("gap in the unit map", sel["note"])


class TheFullSweepIsScheduledNotOptional(unittest.TestCase):

    @needs_live_corpus
    def test_the_status_output_reports_staleness_even_with_the_flag_off(self):
        """The operator this protects is the one who turned diff mode on months ago and
        forgot. So the line prints either way."""
        p = subprocess.run([sys.executable, str(XCHECK), "--project", str(REPO),
                            "status"], capture_output=True, text=True, timeout=180)
        line = next(ln for ln in p.stdout.splitlines() if ln.startswith("full sweep:"))
        print(f"\n  STALENESS  {line[:150]}")
        self.assertIn("diff_scope is off", line)
        self.assertIn("age of the last look at everything", line)

    @needs_live_corpus
    def test_the_figure_is_on_the_machine_surface_too(self):
        """A number a human has to read off a terminal is a number no CI gate can act
        on, and deciding whether a diff-scoped result still covers the tree is exactly
        a gate's question."""
        p = subprocess.run([sys.executable, str(XCHECK), "--project", str(REPO),
                            "status", "--json"], capture_output=True, text=True,
                           timeout=180)
        self.assertEqual(0, p.returncode, p.stderr[-400:])
        payload = json.loads(p.stdout)
        sweep = payload["full_sweep"]
        print(f"  JSON       full_sweep={ {k: v for k, v in sweep.items() if k != 'note'} }"
              f" at v{payload['output_schema_version']}")
        self.assertEqual([], util.json_output_issues(payload, util.STATUS_SCHEMA,
                                                     "status"))
        self.assertIn("days", sweep)

    def test_a_scoped_pass_does_not_count_as_a_full_sweep(self):
        """The whole mechanism: `base` on a done pass means it looked at a diff, so it
        cannot be the last look at everything."""
        fx, _base = project(self)
        doc = json.loads((fx.audit / "state.json").read_text(encoding="utf-8"))
        for q in doc["queue"]:
            q["done"] = True
            q["coverage"] = {"report_path": f"passes/{q['id']}-r.md", "findings": [],
                             "updated": "2026-09-01", "status": "done"}
        fx.write_state(doc)
        unscoped = scope.sweep_status(state_mod.load_state(fx.audit), today="2026-09-03")
        print(f"  SWEEP      all passes unscoped -> {unscoped['pass']} "
              f"{unscoped['days']} day(s) ago")
        self.assertEqual(2, unscoped["days"])

        for q in doc["queue"]:
            q["base"] = "deadbeef" * 5
        fx.write_state(doc)
        scoped = scope.sweep_status(state_mod.load_state(fx.audit), today="2026-09-03")
        print(f"  SWEEP      every pass diff-scoped -> {scoped['note'][:88]}")
        self.assertIsNone(scoped["last"])
        self.assertIn("no full sweep to be stale", scoped["note"])

    def test_the_pass_records_the_base_it_was_scoped_against(self):
        fx, base = project(self)
        code, out = fx.run("queue-pass", "P-09", "--dimension", "invariants",
                           "--units", "U-LEAF", "--charter", "c", "--stop", "s",
                           "--base", base)
        self.assertEqual(0, code, out)
        st = state_mod.load_state(fx.audit)
        q = next(q for q in st.queue if q.id == "P-09")
        print(f"  RECORDED   {q.id} base={q.base[:12]}  ({out.strip()[:70]})")
        self.assertEqual(base, q.base)
        # CONTROL: without --base the field is ABSENT, not empty — an unscoped pass and
        # a pass scoped to nothing are different claims.
        fx.run("queue-pass", "P-10", "--dimension", "invariants", "--units", "U-LEAF",
               "--charter", "c", "--stop", "s")
        doc = json.loads((fx.audit / "state.json").read_text(encoding="utf-8"))
        row = next(q for q in doc["queue"] if q["id"] == "P-10")
        self.assertNotIn("base", row)


class ItIsWithdrawnAndTheRefusalSaysWhy(unittest.TestCase):
    """HISTORICAL: these two asserted `diff_scope` was a live PROJECT key defaulting to
    off — the right contract for an optimisation an operator opts into. The fourth audit
    found the reason the contract was not enough: `scope.selection()` had zero production
    call sites, so `diff_scope=on` narrowed nothing and the key promised a feature the
    program did not have. Phase 2 withdrew the key rather than leaving the promise
    standing. The contract is re-asserted here at its new home — the flag must not be a
    public key, and it must not have quietly become ON on its way out."""

    def test_the_key_is_gone_from_every_conf_surface(self):
        self.assertNotIn(scope.FLAG, util.CONF_DEFAULTS)
        self.assertNotIn(scope.FLAG, util.BOOLEAN_CONF)
        self.assertNotIn(scope.FLAG, util.DEFAULT_CONF)
        # And withdrawal did not flip the behaviour: an absent key still reads off.
        self.assertFalse(util.conf_flag(util.Conf(dict(util.CONF_DEFAULTS)), scope.FLAG))

    def test_it_is_no_longer_a_project_key_and_setting_it_is_refused(self):
        policy = xcheck_submodule("policy")
        self.assertEqual("unknown", policy.classify(scope.FLAG))
        self.assertNotIn(scope.FLAG, policy.PROJECT_KEYS)
        self.assertNotIn(scope.FLAG, policy.POLICY_KEYS)
        self.assertIn(scope.FLAG, policy.WITHDRAWN_KEYS)
        refusal = policy.refuse_withdrawn({scope.FLAG: "on"}, "audit/orchestrator.conf")
        self.assertIn(scope.FLAG, refusal)
        self.assertIn("EXPERIMENTAL", refusal)
        print(f"\n  REFUSAL    {refusal}")

    def test_an_operator_who_never_opts_in_gets_no_selection_at_all(self):
        fx, base = project(self)
        touch(fx, "src/hub.py")
        st = state_mod.load_state(fx.audit)
        off = util.Conf(dict(util.CONF_DEFAULTS))
        self.assertIsNone(scope.selection(fx.root, st, off, base),
                          "diff scope engaged for an operator who never turned it on")
        # The module still reads its own flag, so the library keeps working for
        # whoever promotes it; what no longer exists is the PUBLIC key that said so.
        on = util.Conf(dict(util.CONF_DEFAULTS, diff_scope="on"))
        sel = scope.selection(fx.root, st, on, base)
        print(f"\n  OFF/ON     off -> None; on -> {len(sel['units'])} unit(s)")
        self.assertIsNotNone(sel)


@needs_live_corpus
class ItRunsOnARealDiffInThisRepository(unittest.TestCase):

    def test_the_scope_table_for_the_last_three_commits_is_printed(self):
        base = subprocess.run(["git", "rev-parse", "HEAD~3"], cwd=str(REPO),
                              capture_output=True, text=True).stdout.strip()
        if not base:
            self.skipTest("no history to diff against")
        st = state_mod.load_state(Path(REPO, "audit"))
        sel = scope.select(st, pp.build_sheet(REPO, base=base), base)
        rows = scope.scope_rows(st, sel)
        print(f"\n  REAL DIFF vs {base[:12]}: {len(sel['changed'])} changed file(s) "
              f"-> {len(sel['neighbourhood'])} after expansion "
              f"({len(sel['reached_by_dependency'])} reached only by dependency)")
        print(f"    {'unit':<8} {'dimension(s)':<34} passes  material in scope")
        for r in rows[:10]:
            print(f"    {r['unit']:<8} {','.join(r['dimensions'])[:33]:<34} "
                  f"{len(r['passes']):>6}  {', '.join(r['files'][:2])}")
        print(f"    ... {len(rows)} unit(s) selected over {len(sel['passes'])} pass(es)")
        print(f"    {sel['note']}")
        self.assertTrue(set(sel["changed"]) <= set(sel["neighbourhood"]))

        # NOT `assertTrue(rows)` on the three-commit window. That assertion presumed any
        # three commits touch unit material, and this repository's own cadence disproves
        # it: a supergoal run commits `feat(phase-N)` and then `chore(phase-N): state`, so
        # the window can hold nothing but orchestration files (`.supergoal/STATE.md`,
        # `ci/tiers.py`, a test module) and the selector correctly returns no unit.
        # Selecting nothing from a diff that touches no material is the CORRECT answer,
        # and a test that reddens on it is measuring the commit cadence.
        #
        # The property worth asserting is that the selector selects SOMETHING from real
        # history — so widen the window until it does, and say which one answered. If no
        # window within the search does, that IS the defect and this still fails.
        window, found = None, []
        for n in range(3, 16):
            older = subprocess.run(["git", "rev-parse", f"HEAD~{n}"], cwd=str(REPO),
                                   capture_output=True, text=True).stdout.strip()
            if not older:
                break
            wider = scope.select(st, pp.build_sheet(REPO, base=older), older)
            found = scope.scope_rows(st, wider)
            if found:
                window = (n, older)
                break
        print(f"  WIDENED    HEAD~3 selected {len(rows)} unit(s); "
              f"{'HEAD~%d (%s)' % (window[0], window[1][:12]) if window else 'no window'}"
              f" selected {len(found)}")
        self.assertTrue(found, "no window in the last 15 commits selected a single unit "
                               "— the selector, not the cadence, is the problem")


if __name__ == "__main__":
    unittest.main()
