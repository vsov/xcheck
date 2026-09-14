"""Reusing evidence, and the six ways that would be wrong.

The audit named the cache and named its key in the same sentence, and the key is the
whole feature: a cache that ignores one input is not a faster audit, it is an audit that
reports evidence it did not gather. So the shape of this module is

  1. the key's components, ENUMERATED from the shipped tuple rather than retyped here,
     so dropping one reddens instead of silently narrowing the key;
  2. six one-at-a-time invalidations, each mutating exactly one component and asserting
     the other five are untouched — N arms can cover fewer than N inputs unless the
     arms are proved different;
  3. the verdict refusal, which is the load-bearing rule and is enforced at the WRITE;
  4. a hit and a miss, printed, over a real store;
  5. off by default, outside the tree, and fail-closed on corruption.
"""

import json
import unittest
from pathlib import Path

from tests.harness import (require_live_corpus, 
    Fixture, REPO, state_doc, queue_pass, xcheck_submodule,
)

evidence = xcheck_submodule("evidence")
capsule = xcheck_submodule("capsule")
state_mod = xcheck_submodule("state")
util = xcheck_submodule("util")

_SHEET = None


def sheet():
    # Built once. The capsule tests learned this the expensive way: a per-call tree walk
    # turned a 0.7s module into a 400s one.
    global _SHEET
    if _SHEET is None:
        _SHEET = xcheck_submodule("preprocess").build_sheet(REPO)
    return _SHEET


def live_capsule(charter="pass P-05"):
    require_live_corpus()
    return capsule.build(state_mod.load_state(Path(REPO, "audit")), REPO, "Auditor",
                         charter, sheet=sheet())


AUDITOR = {"model": "claude-opus-5", "provider": "claude",
           "executable": "sha256:" + "a" * 64, "implementation": "0.9.1"}


def parts(charter="pass P-05", policy="d" * 64, auditor=None):
    return evidence.key_components(live_capsule(charter), charter, policy,
                                   auditor or AUDITOR)


class TheKeyIsTheWholeTuple(unittest.TestCase):
    """The audit enumerated six inputs. The key is read from the shipped tuple, so a
    component deleted in the module is a failure here rather than a narrower key."""

    def test_the_six_components_the_audit_named_are_all_in_the_key(self):
        self.assertEqual(
            {"subject", "charter", "norms", "policy", "auditor", "capsule"},
            set(evidence.KEY_COMPONENTS))
        got = parts()
        self.assertEqual(set(evidence.KEY_COMPONENTS), set(got))
        for name, value in got.items():
            self.assertTrue(value, f"{name} derived to something falsy")
        print("KEY COMPONENTS", " ".join(
            f"{c}={got[c][:8]}" for c in evidence.KEY_COMPONENTS))

    def test_a_key_missing_a_component_is_refused_not_defaulted(self):
        for drop in evidence.KEY_COMPONENTS:
            short = {k: v for k, v in parts().items() if k != drop}
            with self.assertRaises(ValueError) as caught:
                evidence.cache_key(short)
            self.assertIn(drop, str(caught.exception))
        self.assertTrue(evidence.key_problems({**parts(), "mood": "x"}))

    def test_the_auditor_identity_carries_the_model_and_the_implementation(self):
        ident = evidence.auditor_identity(
            ["claude", "--model", "claude-opus-5", "-p"], executable="sha256:" + "b" * 64)
        self.assertEqual("claude-opus-5", ident["model"])
        self.assertTrue(ident["implementation"], "no implementation version in the key")
        # Upgrading either half invalidates every entry: an old model's evidence is not
        # this model's evidence, and an older xcheck gathered it differently.
        other_model = dict(ident, model="claude-sonnet-5")
        other_impl = dict(ident, implementation=ident["implementation"] + ".1")
        keys = {evidence.cache_key(parts(auditor=a))
                for a in (ident, other_model, other_impl)}
        self.assertEqual(3, len(keys), "model or implementation did not reach the key")
        print(f"AUDITOR    model+implementation both invalidate "
              f"({ident['model']} / {ident['implementation']})")


class EverySingleComponentInvalidates(unittest.TestCase):
    """Six mutations, one component each. The arms are asserted DIFFERENT from one
    another as well as from the base, because six arms that collapse onto one changed
    value would pass while covering one input."""

    def test_changing_any_one_component_changes_the_key(self):
        base = parts()
        base_key = evidence.cache_key(base)
        mutated, keys = {}, {}
        for component in evidence.KEY_COMPONENTS:
            arm = dict(base)
            arm[component] = "0" * len(str(base[component]))
            differ = [c for c in evidence.KEY_COMPONENTS if arm[c] != base[c]]
            self.assertEqual([component], differ,
                             "the arm moved more than the component it names")
            mutated[component] = arm[component]
            keys[component] = evidence.cache_key(arm)
            self.assertNotEqual(base_key, keys[component],
                                f"{component} does not reach the key")
            print(f"INVALIDATES {component:<9} {base_key[:8]} -> {keys[component][:8]}")
        self.assertEqual(len(evidence.KEY_COMPONENTS), len(set(keys.values())),
                         "two arms produced the same key — they are not six inputs")

    def test_the_arms_are_the_real_inputs_not_just_dict_edits(self):
        """A component mutated by hand proves the KEY reads it. These two prove the
        DERIVATION reads the world: a different charter and a different policy digest
        each produce a different key without touching the parts dict."""
        self.assertNotEqual(evidence.cache_key(parts(charter="pass P-05")),
                            evidence.cache_key(parts(charter="pass P-06")))
        self.assertNotEqual(evidence.cache_key(parts(policy="d" * 64)),
                            evidence.cache_key(parts(policy="e" * 64)))

    def test_the_same_inputs_produce_the_same_key(self):
        self.assertEqual(evidence.cache_key(parts()), evidence.cache_key(parts()))


class AVerdictIsNeverCached(unittest.TestCase):
    """The load-bearing rule. Evidence describes what was observed; a verdict is what
    someone concluded, and a conclusion outlives the material it was about."""

    def test_only_evidence_probes_and_coverage_may_be_stored(self):
        self.assertEqual(("evidence", "probes", "coverage"),
                         evidence.CACHEABLE_SECTIONS)
        self.assertEqual([], evidence.entry_problems(
            {"evidence": ["read src/hub.py"], "probes": {"grep": 3}, "coverage": {}}))

    def test_a_payload_carrying_a_verdict_is_refused_at_the_write(self):
        fx = Fixture(state_doc(queue=[queue_pass("P-01")]))
        conf = {"evidence_dir": str(Path(fx.root).parent / "evstore")}
        poisoned = [
            {"evidence": [], "verdict": "confirmed"},
            {"evidence": [{"probe": "grep", "status": "accepted"}]},
            {"coverage": {"units": [{"findings": [{"severity": "critical"}]}]}},
        ]
        for payload in poisoned:
            problems = evidence.entry_problems(payload)
            self.assertTrue(problems, f"{payload} was not refused")
            with self.assertRaises(ValueError):
                evidence.write_entry(fx.root, "k" * 64, parts(), payload, conf=conf)
            print("REFUSED    ", problems[0][:96])

    def test_a_cached_entry_cannot_supply_a_findings_status(self):
        """Asserted directly, as the phase asks: there is no path from a stored entry to
        a status. The stored payload has no status-shaped key at any depth, and the
        refusal that guarantees it is the one exercised above."""
        fx = Fixture(state_doc(queue=[queue_pass("P-01")]))
        conf = {"evidence_dir": str(Path(fx.root).parent / "evstore2")}
        key = evidence.cache_key(parts())
        evidence.write_entry(fx.root, key, parts(),
                             {"evidence": ["src/hub.py:1 read"], "coverage": {"n": 1}},
                             conf=conf)
        entry, _note = evidence.read_entry(fx.root, key, conf=conf)
        self.assertEqual([], evidence._verdict_fields_in(entry["payload"]))
        for field in ("status", "verdict", "severity"):
            self.assertNotIn(field, json.dumps(entry["payload"]))


class HitAndMiss(unittest.TestCase):
    """The phase's control, and it is run against a real tree rather than an edited dict:
    the material file U01 declares is written, hashed through a real capsule, and then
    ONE BYTE of it is changed. Mutating the parts dict would prove the key reads its own
    fields; this proves the derivation reads the disk."""

    def setUp(self):
        self.fx = Fixture(state_doc(queue=[queue_pass("P-01")]))
        self.conf = {"evidence_dir": str(Path(self.fx.root).parent / "store")}
        self.material = Path(self.fx.root, "src", "core.py")
        self.material.parent.mkdir(parents=True, exist_ok=True)
        self.material.write_text("def core():\n    return 1\n", encoding="utf-8")
        self.payload = {"evidence": ["src/core.py:1 read"],
                        "probes": {"imports": 2}, "coverage": {"files": 1}}

    def project_parts(self):
        st = state_mod.load_state(self.fx.audit)
        cap = capsule.build(st, self.fx.root, "Auditor", "pass P-01")
        return evidence.key_components(cap, "pass P-01", "d" * 64, AUDITOR)

    def test_nothing_changed_is_a_hit_and_one_changed_byte_is_a_miss(self):
        before = self.project_parts()
        key = evidence.cache_key(before)
        evidence.write_entry(self.fx.root, key, before, self.payload, tokens=41_000,
                             conf=self.conf)

        # Nothing changed: the key re-derives to the same value and the entry is served.
        self.assertEqual(key, evidence.cache_key(self.project_parts()))
        entry, note = evidence.read_entry(self.fx.root, key, conf=self.conf)
        self.assertIsNotNone(entry)
        print("CONTROL ON ", evidence.cache_report(entry, key, note))

        # One byte of the subject changes on disk. Nothing else is touched.
        text = self.material.read_text(encoding="utf-8")
        self.material.write_text(text.replace("return 1", "return 2"), encoding="utf-8")
        after = self.project_parts()
        moved = [c for c in evidence.KEY_COMPONENTS if after[c] != before[c]]
        # `capsule` moves with it, and that is correct rather than redundant: the
        # capsule carries the material hashes, so a byte change is two inputs changing.
        # The one-at-a-time proof that each reaches the key alone is the arms above.
        self.assertEqual(["capsule", "subject"], sorted(moved))
        miss_key = evidence.cache_key(after)
        self.assertNotEqual(key, miss_key)
        miss, note = evidence.read_entry(self.fx.root, miss_key, conf=self.conf)
        self.assertIsNone(miss)
        print(f"CONTROL OFF one byte of {self.material.name} changed -> components "
              f"{sorted(moved)} moved")
        print("CONTROL OFF", evidence.cache_report(miss, miss_key, note))

    def test_the_saving_is_measured_in_bytes_and_never_estimated_in_tokens(self):
        key = evidence.cache_key(self.project_parts())
        evidence.write_entry(self.fx.root, key, self.project_parts(), self.payload,
                             conf=self.conf)
        entry, note = evidence.read_entry(self.fx.root, key, conf=self.conf)
        line = evidence.cache_report(entry, key, note)
        self.assertIn("bytes of evidence reused", line)
        self.assertIn("tokens not measured", line)
        self.assertEqual(entry["bytes"], len(json.dumps(
            self.payload, sort_keys=True, separators=(",", ":")).encode("utf-8")))
        print("UNMEASURED ", line)


class OffOutsideAndFailClosed(unittest.TestCase):

    def test_the_flag_is_withdrawn_and_an_absent_key_is_still_off(self):
        """HISTORICAL: this asserted `evidence_cache` was a live BOOLEAN_CONF key
        defaulting to off. The fourth audit found that `read_entry`/`write_entry` had
        zero production call sites, so the key reused nothing and the documentation
        promised a cache the program never consulted. Phase 2 withdrew it. What the test
        was FOR is re-asserted here: absent must still read off, and the module's own
        reader must still work for whoever promotes it."""
        policy = xcheck_submodule("policy")
        self.assertNotIn(evidence.CACHE_FLAG, util.CONF_DEFAULTS)
        self.assertNotIn(evidence.CACHE_FLAG, util.BOOLEAN_CONF)
        self.assertIn(evidence.CACHE_FLAG, policy.WITHDRAWN_KEYS)
        self.assertFalse(evidence.cache_enabled({}))
        self.assertFalse(evidence.cache_enabled({evidence.CACHE_FLAG: "off"}))
        self.assertTrue(evidence.cache_enabled({evidence.CACHE_FLAG: "on"}))
        refusal = policy.refuse_withdrawn({evidence.CACHE_FLAG: "on"},
                                          "audit/orchestrator.conf")
        self.assertIn("EXPERIMENTAL", refusal)
        print(f"REFUSAL    {refusal}")

    def test_the_store_is_outside_the_working_tree(self):
        fx = Fixture(state_doc(queue=[queue_pass("P-01")]))
        root = evidence.cache_root(fx.root, {}, create=False)
        self.assertNotIn(Path(fx.root).resolve(), [root, *root.parents],
                         f"the cache store {root} is inside the audited tree")
        sources = [s for s, p in evidence.cache_search_order(fx.root, {}) if p]
        self.assertEqual("the system temp directory", sources[-1],
                         "the search order must always answer")
        print("STORE      ", root)

    def test_audit_archive_is_refused_as_a_store(self):
        fx = Fixture(state_doc(queue=[queue_pass("P-01")]))
        with self.assertRaises(SystemExit):
            evidence.cache_root(
                fx.root, {"evidence_dir": str(Path(fx.root) / "audit-archive" / "c")})

    def test_a_corrupt_entry_is_a_miss_and_never_a_wrong_answer(self):
        fx = Fixture(state_doc(queue=[queue_pass("P-01")]))
        conf = {"evidence_dir": str(Path(fx.root).parent / "rot")}
        key = evidence.cache_key(parts())
        good = evidence.write_entry(fx.root, key, parts(),
                                    {"evidence": ["ok"]}, conf=conf)
        self.assertIsNotNone(evidence.read_entry(fx.root, key, conf=conf)[0])

        for label, text in (
                ("truncated json", "{not json"),
                ("wrong schema", json.dumps({"schema": 99, "key": key, "payload": {}})),
                ("a file that does not know its own key",
                 json.dumps({"schema": evidence.CACHE_SCHEMA, "key": "z" * 64,
                             "payload": {"evidence": []}})),
                ("a stored verdict",
                 json.dumps({"schema": evidence.CACHE_SCHEMA, "key": key,
                             "payload": {"evidence": [{"verdict": "confirmed"}]}}))):
            Path(good).write_text(text, encoding="utf-8")
            entry, note = evidence.read_entry(fx.root, key, conf=conf)
            self.assertIsNone(entry, f"{label} was served as a hit")
            self.assertTrue(note.startswith("miss:"))
            print(f"FAIL CLOSED {label:<38} -> {note}")


if __name__ == "__main__":
    unittest.main()
