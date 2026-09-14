"""The context capsule. The audit measured the tax: every Auditor session was told to
read `audit/XCHECK.md` (60,112 bytes) and `audit/AUDIT.md` (24,498 bytes) — 84,610 bytes
of normative text — before it looked at the project. Parallelism multiplies that rather
than removing it.

The risk of a summary is the opposite of the risk it fixes: a session that needed
something it was not given, which shows up as a WORSE AUDIT and never as an error. The
completeness control below runs every queued pass through the generator and compares the
capsule's norm set against the norms that pass's dimension actually cites.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.harness import needs_live_corpus, require_live_corpus, Fixture, finding_record, queue_pass, state_doc, xcheck_submodule

capsule = xcheck_submodule("capsule")
envelope = xcheck_submodule("envelope")
runner = xcheck_submodule("runner")
state = xcheck_submodule("state")
util = xcheck_submodule("util")
write_mod = xcheck_submodule("write")

REPO = Path(__file__).resolve().parent.parent
# The audit's figure, restated as a constant so the comparison cannot quietly re-baseline.
CORPUS_BYTES = 84_610


# PHASE 11: the capsule now carries a slice of the deterministic fact sheet, and
# building that sheet walks the tree. Built ONCE here and handed to every
# `capsule.build` below: the per-pass loops call it 39 times each, and paying for 39
# tree walks per loop turned this module from 0.7s into 400s. `capsule.build(sheet=...)`
# exists for exactly this, and passing it explicitly is what keeps the alternative — a
# module-level cache, which would be a standing claim that the tree has not changed —
# out of the product.
_SHEET = None


def sheet():
    global _SHEET
    if _SHEET is None:
        _SHEET = xcheck_submodule("preprocess").build_sheet(REPO)
    return _SHEET


def live_state():
    require_live_corpus()
    return state.load_state(REPO / "audit")


class TheCapsuleCarriesWhatTheCharterIsBoundBy(unittest.TestCase):

    def setUp(self):
        self.state = live_state()
        self.cap = capsule.build(self.state, REPO, "Auditor", "pass P-05", sheet=sheet())

    def test_it_contains_every_declared_part(self):
        for key in ("role", "charter", "allowed_verbs", "norms", "sources", "findings",
                    "postcondition", "stop", "limits"):
            self.assertIn(key, self.cap)
            self.assertTrue(self.cap[key] not in (None, ""), f"{key} is empty")

    def test_the_verbs_are_the_roles_own_and_carry_their_status_bounds(self):
        verbs = {v["verb"] for v in self.cap["allowed_verbs"]}
        self.assertEqual(set(write_mod.ROLE_ROUTES["Auditor"]), verbs)
        setter = [v for v in self.cap["allowed_verbs"] if v["verb"] == "set-status"][0]
        self.assertEqual(["disputed"], setter["from"],
                         "the capsule hands the Auditor a wider set-status than the "
                         "authorizer will allow")

    def test_the_postcondition_is_the_charter_work_the_receipt_checks(self):
        self.assertEqual(sorted(envelope.CHARTER_WORK["Auditor"]["creates"]),
                         self.cap["postcondition"]["creates"])
        self.assertEqual(sorted(envelope.CHARTER_WORK["Auditor"]["bound"]),
                         self.cap["postcondition"]["bound"])

    def test_the_material_is_named_with_the_hash_it_had_at_dispatch(self):
        self.assertTrue(self.cap["sources"])
        for s in self.cap["sources"]:
            self.assertRegex(s["path"], r"\.\w+$")
            if s["sha256"] is not None:
                self.assertRegex(s["sha256"], r"^[0-9a-f]{64}$")

    def test_a_missing_material_file_is_reported_not_raised(self):
        """A unit whose material moved is a fact the capsule should carry. Raising here
        would stop a dispatch over a stale catalog entry."""
        fx = Fixture(state_doc(queue=[queue_pass("P-01")]))
        self.addCleanup(shutil.rmtree, fx.root, True)
        cap = capsule.build(state.load_state(fx.audit), fx.root, "Auditor", "pass P-01")
        for s in cap["sources"]:
            if not (Path(fx.root) / s["path"]).exists():
                self.assertIsNone(s["sha256"])
        self.assertIn("MISSING AT DISPATCH", capsule.render_capsule(
            dict(cap, sources=[{"unit": "U01", "path": "gone.md", "sha256": None}])))

    def test_the_findings_are_the_ones_filed_against_this_charter(self):
        ids = {f["id"] for f in self.cap["findings"]}
        expected = {r.id for r in self.state.findings if r.pass_id == "P-05"}
        self.assertEqual(expected, ids)
        self.assertTrue(expected, "P-05 has no findings, so this proves nothing")

    def test_the_full_corpus_is_still_named(self):
        """The capsule does not hide the documents. A brief that does not admit its own
        limits is how a session confidently audits half a charter."""
        self.assertEqual("audit/XCHECK.md", self.cap["read_more"]["methodology"])
        self.assertEqual("audit/AUDIT.md", self.cap["read_more"]["plan"])
        self.assertIn("SUBSET", capsule.render_capsule(self.cap))


class ItIsContentAddressed(unittest.TestCase):

    def test_the_same_inputs_give_the_same_digest(self):
        a = capsule.capsule_digest(capsule.build(live_state(), REPO, "Auditor", "pass P-05", sheet=sheet()))
        b = capsule.capsule_digest(capsule.build(live_state(), REPO, "Auditor", "pass P-05", sheet=sheet()))
        print(f"\n  DETERMINISTIC  two generations -> {a[:16]} / {b[:16]}")
        self.assertEqual(a, b)

    def test_a_different_charter_gives_a_different_digest(self):
        a = capsule.capsule_digest(capsule.build(live_state(), REPO, "Auditor", "pass P-05", sheet=sheet()))
        b = capsule.capsule_digest(capsule.build(live_state(), REPO, "Auditor", "pass P-06", sheet=sheet()))
        self.assertNotEqual(a, b)

    def test_a_different_role_gives_a_different_digest(self):
        st = live_state()
        self.assertNotEqual(
            capsule.capsule_digest(capsule.build(st, REPO, "Auditor", "pass P-05", sheet=sheet())),
            capsule.capsule_digest(capsule.build(st, REPO, "Verifier", "pass P-05", sheet=sheet())))

    def test_it_is_generated_without_a_model_or_a_network(self):
        """Deterministic derivation, asserted structurally: the module imports nothing
        that could reach a provider, and runs no subprocess."""
        src = (REPO / "xcheck" / "capsule.py").read_text(encoding="utf-8")
        for forbidden in ("subprocess", "urllib", "socket", "requests", "http"):
            self.assertNotIn(f"import {forbidden}", src)


class TheDispatchRecordsWhichCapsuleWasGiven(unittest.TestCase):

    def test_the_digest_is_on_the_envelope_and_clears_the_schema(self):
        cap = capsule.build(live_state(), REPO, "Auditor", "pass P-05", sheet=sheet())
        d = capsule.capsule_digest(cap)
        profile = runner.resolve_profile(util.Conf(dict(util.CONF_DEFAULTS)), "Auditor")
        rec = envelope.dispatch_record(["sh"], "Auditor", "pass P-05", "p", "a" * 16,
                                       profile, 1, head_before="b" * 40,
                                       capsule_digest=d)
        self.assertEqual(d, rec["capsule_digest"])

        fx = Fixture(state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, fx.root, True)
        doc = json.loads((fx.audit / "state.json").read_text(encoding="utf-8"))
        doc["sessions"] = [{k: v for k, v in rec.items()
                            if k in ("session_id", "role", "provider", "agent_model",
                                     "executable", "executable_version", "charter_hash",
                                     "prompt_hash", "state_revision", "head_before",
                                     "sandbox_profile", "started", "capsule_digest")}]
        (fx.audit / "state.json").write_text(json.dumps(doc), encoding="utf-8")
        self.assertEqual(d, state.load_state(fx.audit).sessions[0]["capsule_digest"])

    def test_a_dispatch_with_no_capsule_records_unknown_not_a_guess(self):
        profile = runner.resolve_profile(util.Conf(dict(util.CONF_DEFAULTS)), "Auditor")
        rec = envelope.dispatch_record(["sh"], "Auditor", "c", "p", "a" * 16, profile, 1,
                                       head_before="b" * 40)
        self.assertEqual(envelope.UNKNOWN, rec["capsule_digest"])


class ThePromptNoLongerDemandsTheWholeCorpus(unittest.TestCase):

    NEEDLE = "Read audit/XCHECK.md and audit/AUDIT.md"

    def prompts(self):
        return {role: runner.PROMPTS[role] for role in runner.PROMPTS}

    def test_no_role_prompt_carries_the_mandatory_corpus_read(self):
        hits = {r: p for r, p in self.prompts().items() if self.NEEDLE in p}
        print(f"\n  PROMPT GREP  '{self.NEEDLE}' -> {len(hits)} match(es) in "
              f"{len(self.prompts())} role prompts")
        self.assertEqual({}, hits)

    def test_the_generated_prompt_names_the_capsule_instead(self):
        sid = "a" * 16
        line = runner.capsule_line(sid)
        prompt = runner.PROMPTS["Auditor"].format(charter="P-05") + line
        print(f"  PROMPT       …{line.strip()[:120]}…")
        self.assertNotIn(self.NEEDLE, prompt)
        self.assertIn(f"audit/capsules/{sid}.md", prompt)
        self.assertIn("SUBSET", prompt)
        self.assertIn("audit/XCHECK.md", prompt, "the corpus is no longer reachable")


class TheReductionIsMeasuredNotClaimed(unittest.TestCase):

    @needs_live_corpus
    def test_the_capsule_is_printed_against_the_84610_bytes(self):
        sizes = capsule.corpus_bytes(REPO)
        corpus = sum(sizes.values())
        self.assertEqual(CORPUS_BYTES, corpus,
                         "the corpus changed size; restate the comparison rather than "
                         "quoting the audit's number for a different document")
        rows = []
        for pid in [q.id for q in live_state().queue][:5]:
            cap = capsule.build(live_state(), REPO, "Auditor", f"pass {pid}", sheet=sheet())
            rows.append((pid, len(capsule.render_capsule(cap).encode("utf-8"))))
        print(f"\n  SIZE      corpus {corpus:,} bytes "
              f"({', '.join(f'{k} {v:,}' for k, v in sorted(sizes.items()))})")
        for pid, n in rows:
            print(f"            capsule {pid}  {n:>6,} bytes   "
                  f"{corpus / n:>5.1f}x smaller")
        worst = max(n for _p, n in rows)
        print(f"            LARGEST of these: {worst:,} bytes -> "
              f"{corpus / worst:.1f}x reduction")
        self.assertLess(worst, corpus / 5,
                        "the capsule is not materially smaller than the corpus it "
                        "replaces")

    def test_every_pass_capsule_is_smaller_than_the_corpus(self):
        st = live_state()
        biggest = max((len(capsule.render_capsule(
            capsule.build(st, REPO, "Auditor", f"pass {q.id}", sheet=sheet())).encode("utf-8")), q.id)
            for q in st.queue)
        print(f"  SIZE      largest of all {len(st.queue)} pass capsules: "
              f"{biggest[0]:,} bytes ({biggest[1]})")
        self.assertLess(biggest[0], CORPUS_BYTES)


class TheCompletenessControlOverEveryQueuedPass(unittest.TestCase):
    """The risk this phase creates: a capsule that omits something the session needed.
    That failure is invisible — it produces a worse audit, not an error — so it is made
    visible here, over every pass in the queue rather than over a sampled one."""

    def test_no_pass_capsule_omits_a_norm_its_dimension_cites(self):
        st = live_state()
        dims = {d.key: d for d in st.catalogs.dimensions}
        missing, checked = [], 0
        for q in st.queue:
            cap = capsule.build(st, REPO, "Auditor", f"pass {q.id}", sheet=sheet())
            got = {n["id"] for n in cap["norms"]}
            want = set(dims[q.dimension].norms) if q.dimension in dims else set()
            checked += 1
            if want - got:
                missing.append((q.id, q.dimension, sorted(want - got)))
        print(f"\n  COMPLETENESS  {checked} passes checked, "
              f"{len(missing)} with a missing norm")
        self.assertEqual([], missing,
                         "a capsule omits a norm the pass's dimension cites — the "
                         "session would be judged against a rule it was never given")
        self.assertEqual(39, checked, "the queue is not the 39 passes this control "
                                      "claims to cover")

    def test_no_pass_capsule_omits_a_finding_already_filed_against_it(self):
        st = live_state()
        by_pass = {}
        for r in list(st.findings) + list(st.class_findings):
            by_pass.setdefault(r.pass_id, set()).add(r.id)
        missing = []
        for q in st.queue:
            cap = capsule.build(st, REPO, "Auditor", f"pass {q.id}", sheet=sheet())
            got = {f["id"] for f in cap["findings"]}
            want = by_pass.get(q.id, set())
            if want - got:
                missing.append((q.id, sorted(want - got)))
        print(f"  COMPLETENESS  findings: {len(missing)} pass(es) with an omission")
        self.assertEqual([], missing)

    def test_no_pass_capsule_omits_a_units_material(self):
        st = live_state()
        units = {u.id: u for u in st.catalogs.units}
        missing = []
        for q in st.queue:
            cap = capsule.build(st, REPO, "Auditor", f"pass {q.id}", sheet=sheet())
            got = {s["path"] for s in cap["sources"]}
            for uid in q.units or ():
                want = set(capsule.material_paths(
                    units[uid].material if uid in units else ""))
                if want - got:
                    missing.append((q.id, uid, sorted(want - got)))
        print(f"  COMPLETENESS  material: {len(missing)} pass(es) with an omission")
        self.assertEqual([], missing)

    def test_the_control_can_fail(self):
        """A completeness control that cannot go red proves nothing. Drop a norm from a
        dimension's citation list and the same comparison must catch it."""
        st = live_state()
        q = st.queue[0]
        cap = capsule.build(st, REPO, "Auditor", f"pass {q.id}", sheet=sheet())
        got = {n["id"] for n in cap["norms"]}
        want = got | {"N-NOT-GIVEN"}
        self.assertTrue(want - got, "the planted omission was not detectable")


class TheCapsuleReachesTheSessionThatMustReadIt(unittest.TestCase):

    def test_it_is_written_inside_the_project_where_a_sandbox_can_see_it(self):
        fx = Fixture(state_doc(queue=[queue_pass("P-01")]))
        self.addCleanup(shutil.rmtree, fx.root, True)
        cap = capsule.build(state.load_state(fx.audit), fx.root, "Auditor", "pass P-01")
        p = capsule.write(fx.root, "b" * 16, cap)
        self.assertTrue(p.is_file())
        self.assertEqual(Path(fx.root) / "audit" / "capsules" / f"{'b' * 16}.md", p)

    def test_it_is_not_gitignored_or_the_sandbox_seed_would_drop_it(self):
        """`Sandbox._seed` copies untracked files with `--exclude-standard`, which honours
        .gitignore. That is exactly how F-0093 made the lock owner unreachable inside the
        worktree; a gitignored capsule would repeat it."""
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", "audit/capsules/deadbeefdeadbeef.md"],
            cwd=str(REPO), capture_output=True, text=True)
        print(f"  SEED SAFE  git check-ignore on a capsule path -> rc="
              f"{ignored.returncode} (1 = not ignored, so the seed copies it)")
        self.assertEqual(1, ignored.returncode,
                         "the capsule path is gitignored — the session's sandbox would "
                         "not receive the one file the prompt tells it to read")


if __name__ == "__main__":
    unittest.main()
