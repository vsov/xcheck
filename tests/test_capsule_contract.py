"""F5 (0.9.6): the capsule carries the canonical contract, verbatim, for every role.

Every capsule here is the one the REAL dispatch wrote (`next --yes` over a fake agent that
prints the prompt it was given), read back from `audit/capsules/`. Completeness is asserted
by substring on the rendered text, never by the presence of a key.
"""

import json
import re
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from tests.harness import (CATALOGS, REPO, Fixture, finding_record, needs_repo_file, queue_pass,
                           state_doc, xcheck_submodule)
from tests.test_parallel_retry import two_pass_doc

capsule = xcheck_submodule("capsule")
runner = xcheck_submodule("runner")
state_mod = xcheck_submodule("state")
util = xcheck_submodule("util")

CHILD = textwrap.dedent('''\
    import sys
    print("PROMPT<<" + " ".join(sys.argv[1:]) + ">>PROMPT")
''')

CHARTER = "check that every documented invariant of the core holds, quoting the line"
STOP = "15 findings or the unit is exhausted"
HOW = "Run `python -c 'import core; core.subject()'` and expect 2, not 1."
BODY = f"# F-0001\n\n## Evidence\n\nquote\n\n## How to verify the fix\n\n{HOW}\n\n## Remediation\n\n"


def pass_entry(pid="P-01", charter=CHARTER):
    return dict(queue_pass(pid), charter=charter, stop=STOP)


class Dispatch:
    """One real dispatch of `role`, and the capsule it wrote."""

    def __init__(self, test, doc, parallel=False):
        self.tmp = Path(tempfile.mkdtemp(prefix="xc-capsule-"))
        test.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "child.py").write_text(CHILD)
        cmd = f"{sys.executable} {self.tmp / 'child.py'} {{prompt}}"
        conf = "".join(f"{r}_cmd={cmd}\n" for r in ("planner", "auditor", "remediator",
                                                     "verifier"))
        conf += (f"parallel_passes={'on' if parallel else 'off'}\nconstrual_gate=off\n"
                 f"embargo=off\n")
        self.f = Fixture(doc, conf=conf)
        test.addCleanup(self.f.cleanup)
        for r in doc.get("findings", ()):
            self.f.body(r["body_path"], BODY)
        self.f.git_init(extra=("README.md", "src/core.py", "a.py", "b.py"))
        self.rc, self.out = self.f.run_subprocess("next", "--yes")

    def sessions(self):
        return json.loads((self.f.audit / "state.json").read_text())["sessions"]

    def capsules(self):
        """{session id: rendered capsule} for every session dispatched."""
        return {s["session_id"]: (self.f.audit / "capsules" / f"{s['session_id']}.md")
                .read_text() for s in self.sessions()}

    def prompts(self):
        root = xcheck_submodule("retention").log_root(self.f.root, create=False)
        logs = sorted(Path(root).rglob("*.log"))
        return [m for p in logs for m in re.findall(r"PROMPT<<(.*?)>>PROMPT",
                                                     p.read_text(), re.S)]


def contract_doc(role):
    if role == "Planner":
        return state_doc(queue=[])                       # no queue: the plan is unfinished
    if role == "Auditor":
        return state_doc(queue=[pass_entry()])
    status, extra = (("accepted", {}) if role == "Remediator"
                     else ("fixed", {"fixed_by": "a" * 16}))
    done = dict(pass_entry(), done=True,
                coverage={"report_path": "passes/P-01-report.md", "findings": ["F-0001"],
                          "updated": "2026-08-14", "status": "done"})
    return state_doc(queue=[done], findings=[finding_record(status=status, **extra)])


class EveryRoleGetsTheContract(unittest.TestCase):

    def test_the_rendered_capsule_carries_it_verbatim(self):
        norm, dim, unit = CATALOGS["norms"][0], CATALOGS["dimensions"][0], CATALOGS["units"][0]
        print("\n  CAPSULE CONTRACT (real dispatch, substring on the rendered text)")
        for role in ("Planner", "Auditor", "Remediator", "Verifier"):
            with self.subTest(role=role):
                d = Dispatch(self, contract_doc(role))
                [s] = d.sessions()
                self.assertEqual(role, s["role"], d.out[-600:])
                [text] = d.capsules().values()
                if role == "Planner":
                    want = {"planner charter": util.PLANNER_CHARTER}
                else:
                    want = {"pass charter": CHARTER, "stop": STOP, "norm id": norm["id"],
                            "norm source": norm["source"], "norm scope": norm["scope"],
                            "dimension": dim["key"], "dimension catches": dim["catches"],
                            "source path": unit["material"]}
                if role in ("Remediator", "Verifier"):
                    want.update({"finding id": "F-0001", "finding title": "a finding",
                                 "how to verify": HOW})
                if role == "Remediator":
                    want["body path"] = "findings/F-0001.md"
                missing = [k for k, v in want.items() if v not in text]
                print(f"    {role:10} {len(text):6,} bytes  {len(want)} items  "
                      f"missing={missing}")
                self.assertEqual([], missing)
                if role == "Verifier":
                    # The existing independence rule: no link to the Auditor's argument.
                    self.assertNotIn("findings/F-0001.md", text)


class TheDigestIsTheContract(unittest.TestCase):

    def build(self, doc):
        f = Fixture(doc)
        self.addCleanup(f.cleanup)
        st = state_mod.load_state(f.audit)
        return capsule.capsule_digest(capsule.build(st, f.root, "Auditor", "P-01",
                                                    sheet=None))

    def test_one_character_of_the_charter_or_a_norm_moves_it(self):
        base = self.build(state_doc(queue=[pass_entry()]))
        self.assertEqual(base, self.build(state_doc(queue=[pass_entry()])), "not stable")
        self.assertNotEqual(base, self.build(state_doc(queue=[pass_entry(charter=CHARTER + ".")])))
        cats = json.loads(json.dumps(CATALOGS))
        cats["norms"][0]["scope"] += "!"
        self.assertNotEqual(base, self.build(state_doc(queue=[pass_entry()], catalogs=cats)))


class ParallelIsBriefedLikeSequential(unittest.TestCase):

    def test_same_capsule_and_the_prompt_names_it(self):
        doc = two_pass_doc()
        serial = Dispatch(self, json.loads(json.dumps(doc)))
        par = Dispatch(self, json.loads(json.dumps(doc)), parallel=True)
        s_caps = {re.search(r"contract of (P-\d+)", t).group(1): t
                  for t in serial.capsules().values()}
        p_caps = {re.search(r"contract of (P-\d+)", t).group(1): t
                  for t in par.capsules().values()}
        self.assertEqual(2, len(p_caps), par.out[-600:])
        self.assertEqual(s_caps["P-01"], p_caps["P-01"])
        for s in par.sessions():
            self.assertRegex(s["capsule_digest"], r"^[0-9a-f]{64}$")
        prompts = par.prompts()
        self.assertEqual(2, len(prompts))
        for s in par.sessions():
            self.assertTrue(any(f"audit/capsules/{s['session_id']}.md" in p for p in prompts),
                            "the parallel prompt does not name the session's capsule")


class TheReadingOrderAgrees(unittest.TestCase):

    def order(self, text):
        marks = {"capsule": text.find("capsule"), "XCHECK.md": text.find("XCHECK.md"),
                 "AUDIT.md": text.find("AUDIT.md")}
        self.assertTrue(all(v >= 0 for v in marks.values()), marks)
        return sorted(marks, key=marks.get)

    def test_the_prompt_and_rule_3_name_the_same_sources_in_the_same_order(self):
        xm = (REPO / "XCHECK.md").read_text()
        rule3 = xm[xm.index("3. **Explicit reading list.**"):]
        rule3 = rule3[:rule3.index("\n")]
        prompt = runner.capsule_line("a" * 16)
        print(f"\n  rule 3: {self.order(rule3)}\n  prompt: {self.order(prompt)}")
        self.assertEqual(["capsule", "XCHECK.md", "AUDIT.md"], self.order(rule3))
        self.assertEqual(self.order(rule3), self.order(prompt))

    def test_every_role_reads_the_capsule_first(self):
        xm = (REPO / "XCHECK.md").read_text()
        reads = re.findall(r"^- \*\*Reads:\*\* (.*)$", xm, re.M)
        agent = [r for r in reads if "XCHECK.md" in r]
        self.assertEqual(4, len(agent), reads)
        for r in agent:
            self.assertTrue(r.startswith("the context capsule first"), r)

    def test_the_pass_template_no_longer_points_at_an_audit_md_queue(self):
        text = (REPO / "templates" / "pass.md").read_text()
        # Any mention that puts the queue in AUDIT.md, not only the old opening line —
        # the Handoff comment carried the same stale pointer until the final audit.
        self.assertEqual([], [ln for ln in text.splitlines()
                              if "AUDIT.md" in ln and "not AUDIT.md" not in ln], text)
        self.assertIn("context capsule", text)

    @needs_repo_file("audit/XCHECK.md", "the development corpus's installed copy; audit/ is not exported")
    def test_the_installed_copy_is_the_source(self):
        self.assertEqual((REPO / "XCHECK.md").read_bytes(),
                         (REPO / "audit" / "XCHECK.md").read_bytes())


if __name__ == "__main__":
    unittest.main()
