"""F6 / P1 acceptance (0.9.6): a fix is accepted by a result, and durable only when re-observed.

The smoke at the bottom runs the real CLI end to end in a disposable project whose defect
is `def authorized(t): return True`, with the oracle executed by `xcheck run-oracle` under
the container profile. Without a docker backend that class is skipped loudly and the rest
— parser, gate, levels, the refusal on every other profile — still run.
"""

import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from tests.harness import Fixture, finding_record, state_doc, xcheck_submodule
from tests.test_container_profile import DOCKER_OK, SKIP_REASON

oracle = xcheck_submodule("oracle")
envelope = xcheck_submodule("envelope")
runner = xcheck_submodule("runner")
benchmark = xcheck_submodule("benchmark")
decision = xcheck_submodule("decision")

ORACLE = {"argv": ["sh", "check_auth.sh"], "expect_absent_exit": 0, "timeout_s": 30,
          "scope": ["src/auth.py"]}
# Whitespace-blind on purpose: reformatting the defect must not read as fixing it.
CHECK = "tr -d ' \\t\\n' < src/auth.py | grep -q 'returnTrue' && exit 1\nexit 0\n"
DEFECT = "def authorized(t):\n    return True\n"
REFORMATTED = "def authorized(t):\n    return   True\n"
FIXED = "def authorized(t):\n    return t == 'admin-token'\n"
A, B = "a" * 16, "b" * 16


def body(orc=ORACLE):
    block = "" if orc is None else f"## Oracle\n\n```json\n{json.dumps(orc)}\n```\n\n"
    return (f"# F-0001\n\n## Evidence\n\n`return True`\n\n## How to verify the fix\n\n"
            f"run check_auth.sh\n\n{block}## Validation\n\n")


class TheBlockIsReadStrictly(unittest.TestCase):

    def test_the_table(self):
        good = dict(ORACLE)
        rows = [
            ("valid", good, True),
            ("argv a string", dict(good, argv="sh check.sh"), False),
            ("argv empty", dict(good, argv=[]), False),
            ("argv non-string", dict(good, argv=["sh", 1]), False),
            ("no timeout", {k: v for k, v in good.items() if k != "timeout_s"}, False),
            ("timeout zero", dict(good, timeout_s=0), False),
            ("timeout huge", dict(good, timeout_s=10 ** 9), False),
            ("timeout bool", dict(good, timeout_s=True), False),
            ("exit out of range", dict(good, expect_absent_exit=300), False),
            ("unknown key", dict(good, shell=True), False),
            ("scope not list", dict(good, scope="src"), False),
            ("a list", [good], False),
        ]
        print("\n  ORACLE PARSER")
        for label, obj, ok in rows:
            got, why = oracle.read_block(body(None) + f"## Oracle\n\n```json\n{json.dumps(obj)}\n```\n")
            print(f"    {label:20} -> {'ok' if got else why}")
            self.assertEqual(ok, got is not None, (label, why))
            if not ok:
                self.assertTrue(why, label)
        self.assertEqual((None, None), oracle.read_block(body(None)), "no block is not an error")
        self.assertEqual((None, None), oracle.read_block("## Oracle\n<!-- guidance -->\n"),
                         "the template's unfilled section is not an oracle")
        self.assertIn("not valid JSON", oracle.read_block("## Oracle\n```json\n{x\n```\n")[1])

    def test_the_digest_covers_the_checker(self):
        d = Path(tempfile.mkdtemp(prefix="xc-oracle-"))
        self.addCleanup(shutil.rmtree, d, True)
        (d / "check_auth.sh").write_text(CHECK)
        one = oracle.block_digest(ORACLE, d)
        self.assertEqual(one, oracle.block_digest(ORACLE, d))
        (d / "check_auth.sh").write_text(CHECK + "# edited\n")
        self.assertNotEqual(one, oracle.block_digest(ORACLE, d))
        self.assertNotEqual(one, oracle.block_digest(dict(ORACLE, timeout_s=31), d))


def run_event(verdict, digest="d1", snapshot="s1"):
    return {"event": "oracle_run", "finding": "F-0001", "verdict": verdict,
            "oracle_digest": digest, "snapshot": snapshot}


class EachMissingConditionHasItsOwnRefusal(unittest.TestCase):

    def problems(self, events, independence="cross-provider"):
        return oracle.closing_problems(events, "F-0001", "d1", "s2", independence)

    def test_control_all_four_present_closes(self):
        self.assertEqual([], self.problems([run_event("defect-present"),
                                            run_event("defect-absent", snapshot="s2")]))

    def test_no_defect_absent_run_on_the_current_material(self):
        bad = self.problems([run_event("defect-present"),
                             run_event("defect-absent", snapshot="s1")])
        self.assertEqual(1, len(bad))
        self.assertIn("no `defect-absent` oracle run on the current material", bad[0])

    def test_a_different_oracle_passed(self):
        bad = self.problems([run_event("defect-present"),
                             run_event("defect-absent", digest="d2", snapshot="s2")])
        self.assertEqual(1, len(bad))
        self.assertIn("by a different oracle", bad[0])

    def test_the_oracle_never_failed(self):
        bad = self.problems([run_event("defect-absent", snapshot="s2")])
        self.assertEqual(1, len(bad))
        self.assertIn("never recorded `defect-present`", bad[0])

    def test_independence_same_or_unrecorded(self):
        ok = [run_event("defect-present"), run_event("defect-absent", snapshot="s2")]
        for level in ("same", "unrecorded"):
            bad = self.problems(ok, independence=level)
            self.assertEqual(1, len(bad), level)
            self.assertIn(f"independence is `{level}`", bad[0])


class TheLevelIsDerivedNotAged(unittest.TestCase):

    def rec(self, status):
        return SimpleNamespace(id="F-0001", status=status, attempts=0, updated="2026-01-01")

    def closed(self, **extra):
        return {"event": "state_transition", "target": "F-0001", "verb": "record-verdict",
                "verdict": "closed", **extra}

    def test_a_closure_without_a_new_run_is_never_durable_however_old(self):
        st = SimpleNamespace(findings=[self.rec("closed")], class_findings=[])
        ev = [run_event("defect-present"), run_event("defect-absent", snapshot="s2"),
              self.closed(snapshot="s2", oracle_digest="d1")]
        for days in (0, 14, 400):
            n, kinds = benchmark.useful_outcomes(st, ev, now=date.fromordinal(
                date(2026, 1, 1).toordinal() + days))
            self.assertEqual(0, kinds["durable_fix"], days)
        self.assertEqual(("independently-verified", False),
                         oracle.evidence_level(self.rec("closed"), ev))

    def test_a_later_run_on_newer_material_is_re_observed(self):
        ev = [self.closed(snapshot="s2"), run_event("defect-absent", snapshot="s3")]
        self.assertEqual("re-observed", oracle.evidence_level(self.rec("closed"), ev)[0])
        self.assertTrue(oracle.durable(self.rec("closed"), ev))
        # The same material again is not a new observation.
        ev = [self.closed(snapshot="s2"), run_event("defect-absent", snapshot="s2")]
        self.assertFalse(oracle.durable(self.rec("closed"), ev))

    def test_manual_is_marked_and_never_durable(self):
        ev = [self.closed(evidence_manual="read the paragraph"),
              run_event("defect-absent", snapshot="s9")]
        level, manual = oracle.evidence_level(self.rec("closed"), ev[:1])
        self.assertEqual(("independently-verified", True), (level, manual))

    def test_the_lower_levels(self):
        self.assertEqual("reported", oracle.evidence_level(self.rec("reported"), [])[0])
        self.assertEqual("confirmed", oracle.evidence_level(self.rec("accepted"), [])[0])
        self.assertEqual("confirmed", oracle.evidence_level(
            self.rec("reported"), [run_event("defect-present")])[0])
        self.assertEqual("fixed", oracle.evidence_level(self.rec("fixed"), [])[0])

    def test_m2_and_the_benchmark_call_one_predicate(self):
        import ast
        import inspect
        for mod, fn in ((benchmark, "useful_outcomes"), (decision, "metrics_report")):
            src = inspect.getsource(getattr(mod, fn))
            names = {n.names[0].asname or n.names[0].name for n in ast.walk(ast.parse(
                src.strip() if src.startswith("def") else src)) if isinstance(n, ast.ImportFrom)
                     and n.module == "xcheck.oracle"}
            self.assertTrue(names, f"{fn} does not import from xcheck.oracle")
        self.assertNotIn("_closed_long_enough", dir(benchmark))
        self.assertNotIn("DURABILITY_WINDOW_DAYS", dir(benchmark))


def sessions_for_verdict(tmp):
    """A finished fixer session (A, codex) and the OPEN verifier session (B, claude)."""
    prof = runner.resolve_profile({}, "Verifier")
    log = Path(tmp) / "fixer.log"
    log.write_text("fixed\n")
    fixer = envelope.dispatch_record(["codex", "exec"], "Remediator", "finding F-0001", "p",
                                     A, prof, 1, "unknown")
    fixer = envelope.finish(fixer, 0, "ok", 1.0, "unknown", log)
    verifier = envelope.dispatch_record(["claude", "-p"], "Verifier", "finding F-0001", "p",
                                        B, prof, 2, "unknown")
    return [fixer, verifier]


class ProjectFixture:
    def __init__(self, test, profile="container", orc=ORACLE):
        doc = state_doc(findings=[finding_record(status="fixed", fixed_by=A)])
        tmp = tempfile.mkdtemp(prefix="xc-oracle-log-")
        test.addCleanup(shutil.rmtree, tmp, True)
        doc["sessions"] = sessions_for_verdict(tmp)
        self.f = Fixture(doc, conf=f"sandbox_profile={profile}\n")
        test.addCleanup(self.f.cleanup)
        self.f.body("findings/F-0001.md", body(orc))
        (self.f.root / "src").mkdir(exist_ok=True)
        (self.f.root / "src" / "auth.py").write_text(DEFECT)
        (self.f.root / "check_auth.sh").write_text(CHECK)
        self.f.git_init(extra=("README.md",))

    def cli(self, *args):
        return self.f.run(*args)

    def close(self, *extra):
        return self.f.run("record-verdict", "F-0001", "--verdict", "closed",
                          "--session", B, *extra)

    def runs(self):
        return [e for e in envelope.read_events(self.f.root) if e.get("event") == "oracle_run"]


class RunOracleRefusesEverywhereButTheContainer(unittest.TestCase):

    def test_every_other_profile(self):
        for profile in ("worktree", "readonly", "none"):
            with self.subTest(profile=profile):
                p = ProjectFixture(self, profile=profile)
                rc, out = p.cli("run-oracle", "F-0001")
                self.assertNotEqual(0, rc)
                self.assertIn(f"refused under sandbox_profile={profile!r}", out)
                self.assertEqual([], p.runs(), "nothing may be recorded")

    def test_a_bad_block_is_refused_before_any_run(self):
        p = ProjectFixture(self, orc=dict(ORACLE, argv="sh check_auth.sh"))
        rc, out = p.cli("run-oracle", "F-0001")
        self.assertNotEqual(0, rc)
        self.assertIn("non-empty JSON list", out)

    def test_an_old_finding_without_an_oracle_needs_manual_evidence(self):
        p = ProjectFixture(self, orc=None)
        rc, out = p.close()
        self.assertNotEqual(0, rc)
        self.assertIn("carries no ## Oracle block", out)
        rc, out = p.close("--evidence", "manual:re-read the clause; it now says what N1 asks")
        self.assertEqual(0, rc, out)
        self.assertIn("evidence: MANUAL", out)
        st = xcheck_submodule("state").load_state(p.f.audit)
        self.assertEqual(("independently-verified", True),
                         oracle.evidence_level(st.findings[0], envelope.read_events(p.f.root)))

    def test_an_empty_manual_claim_is_refused(self):
        p = ProjectFixture(self, orc=None)
        rc, out = p.close("--evidence", "manual:")
        self.assertNotEqual(0, rc)
        self.assertIn("empty manual claim", out)


@unittest.skipUnless(DOCKER_OK, SKIP_REASON)
class TheSmoke(unittest.TestCase):
    """(a) a real fix closes; (b) a reformat does not; (c) an edited checker is a different
    oracle; (d) material edited after the passing run is not the material that passed;
    (e) record-verdict alone moves no evidence."""

    def setUp(self):
        self.p = ProjectFixture(self)
        rc, out = self.p.cli("run-oracle", "F-0001")
        print(f"\n  defect      : {self.line(out)}")
        self.assertEqual("defect-present", self.p.runs()[-1]["verdict"], out)

    @staticmethod
    def line(out):
        return next((ln for ln in out.splitlines() if ln.startswith("oracle ")), out[-200:])

    def write(self, rel, text):
        (self.p.f.root / rel).write_text(text)

    def oracle_says(self, label):
        rc, out = self.p.cli("run-oracle", "F-0001")
        print(f"  {label:12}: {self.line(out)}")
        return self.p.runs()[-1]["verdict"]

    def test_a_real_fix_is_closable(self):
        self.write("src/auth.py", FIXED)
        self.assertEqual("defect-absent", self.oracle_says("fixed"))
        rc, out = self.p.close()
        print(f"  close       : rc={rc} {out.strip().splitlines()[-1]}")
        self.assertEqual(0, rc, out)
        self.assertIn("defect-absent on", out)

    def test_a_reformat_is_not_a_fix(self):
        self.write("src/auth.py", REFORMATTED)
        self.assertEqual("defect-present", self.oracle_says("reformatted"))
        rc, out = self.p.close()
        self.assertNotEqual(0, rc)
        self.assertIn("no `defect-absent` oracle run on the current material", out)

    def test_an_edited_checker_is_a_different_oracle(self):
        self.write("src/auth.py", FIXED)
        self.write("check_auth.sh", "exit 0\n")              # a checker that always passes
        self.assertEqual("defect-absent", self.oracle_says("lax checker"))
        rc, out = self.p.close()
        self.assertNotEqual(0, rc)
        self.assertIn("never recorded `defect-present`", out)

    def test_material_edited_after_the_pass(self):
        self.write("src/auth.py", FIXED)
        self.assertEqual("defect-absent", self.oracle_says("fixed"))
        self.write("src/auth.py", FIXED + "# touched after the run\n")
        rc, out = self.p.close()
        self.assertNotEqual(0, rc)
        self.assertIn("no `defect-absent` oracle run on the current material", out)

    def test_the_verdict_alone_moves_no_evidence(self):
        st = xcheck_submodule("state").load_state(self.p.f.audit)
        before = oracle.evidence_level(st.findings[0], envelope.read_events(self.p.f.root))
        rc, out = self.p.close()
        self.assertNotEqual(0, rc)
        st = xcheck_submodule("state").load_state(self.p.f.audit)
        self.assertEqual(before, oracle.evidence_level(st.findings[0],
                                                       envelope.read_events(self.p.f.root)))
        self.assertEqual("fixed", st.findings[0].status)

    def test_the_run_records_what_it_ran_on(self):
        e = self.p.runs()[-1]
        state = xcheck_submodule("state")
        self.assertEqual(state.material_identity(self.p.f.root)[0], e["snapshot"])
        self.assertEqual(oracle.block_digest(ORACLE, self.p.f.root), e["oracle_digest"])
        self.assertEqual(2, e["snapshot_scheme"])
        self.assertEqual(1, e["exit"])
        self.assertEqual("operator", e["actor"])


if __name__ == "__main__":
    unittest.main()
