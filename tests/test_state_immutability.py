"""0.9.0 phase 2: a loaded `State` is immutable ALL THE WAY DOWN, not at its surface.

`State` is `@dataclass(frozen=True)`, which stops `state.state_revision = 5`. It does
not stop anything reachable THROUGH a field, and two fields held ordinary containers:

    st.limits["reopen_limit"] = 999        # succeeded
    st.sessions[0]["role"] = "Remediator"  # succeeded
    st.state_revision = 5                  # FrozenInstanceError

The audit reported this honestly as an invariant that is false rather than an exploit
in flight — no consumer is known to use it. That is precisely when it is cheap to
close, and phase 3's write verbs and phase 11's optimistic concurrency both assume a
State that cannot change behind a check that already passed.

The test is a REACHABILITY WALK, not three assertions about the two known fields. A
walk is what survives a later field being added: the next `dict` anyone puts on a
record is found by the same code, on the day it appears, without anyone remembering to
write a test for it.

Two things keep the walk from being a ceremony that always passes:

* a NODE-COUNT FLOOR derived from the fixture, so a walk that silently visits nothing
  (an exception swallowed, a recursion that never recurses) fails instead of passing;
* a POSITIVE CONTROL — the same walk over a graph with a plain `dict` planted in it,
  asserted to REPORT that dict. A detector that has never been shown detecting is not
  evidence. (`mutation-harness-needs-its-own-control`.)
"""

import unittest
from dataclasses import FrozenInstanceError, fields, is_dataclass
from types import MappingProxyType

from tests.harness import (
    REPO, Fixture, construal_record, finding_record, queue_pass, state_doc,
    xcheck_submodule,
)

state = xcheck_submodule("state")

SENTINEL = "MUTATED-BY-THE-WALK"


def rich_doc():
    """A fixture that reaches every branch of the graph: both record kinds, a done
    pass with its coverage, plans, construals, catalogs, and — the two the audit
    named — `limits` and a `sessions[]` envelope carrying a NESTED object."""
    findings = [finding_record("F-0001"),
                finding_record("F-0002", status="fixed", fixed_by="a" * 16),
                finding_record("F-0003", status="closed")]
    doc = state_doc(
        findings=findings,
        class_findings=[{
            "id": "CF-0001", "title": "a class finding", "severity": "major",
            "status": "reported", "dimension": "invariants", "unit": ["U01"],
            "pass": "P-01", "attempts": 0, "updated": "2026-08-14",
            "body_path": "findings/CF-0001.md", "members": ["F-0001", "F-0002"]}],
        queue=[queue_pass("P-01", done=True)],
        plans=[{"id": "RP-0001", "findings": ["F-0001"], "status": "open",
                "updated": "2026-08-14", "body_path": "plans/RP-0001.md",
                "attempts": 0}],
        construals=[construal_record()],
    )
    doc["sessions"] = [{
        "session_id": "a" * 16, "role": "Remediator", "provider": "anthropic",
        "agent_model": "unknown", "executable": "claude",
        "executable_version": "unknown", "charter_hash": "0" * 64,
        "prompt_hash": "0" * 64, "state_revision": 1, "head_before": "0" * 40,
        "sandbox_profile": "worktree", "started": "2026-08-14T00:00:00Z",
        # The schema declares this one as an object. A SHALLOW freeze of the session
        # record would leave it writable — the original bug, one level down.
        "sandbox_details": {"network": "none", "material_writable": False},
        "outcome": "ok",
    }]
    return doc


# --------------------------------------------------------------------------
# the walk
# --------------------------------------------------------------------------


def attempts_for(node):
    """The mutations worth trying on this node, as (description, thunk) pairs.

    Type-appropriate on purpose: `append` on a tuple raises AttributeError whatever
    the tuple contains, which would count as "refused" without proving anything.
    """
    out = []
    if is_dataclass(node) and not isinstance(node, type):
        names = [f.name for f in fields(node)]
        if names:
            out.append((f"setattr({type(node).__name__}.{names[0]})",
                        lambda: setattr(node, names[0], SENTINEL)))
    elif isinstance(node, MappingProxyType) or isinstance(node, dict):
        key = next(iter(node), None)
        if key is not None:
            out.append((f"proxy[{key!r}] = …", lambda: node.__setitem__(key, SENTINEL)))
        out.append(("map['new-key'] = …", lambda: node.__setitem__("new-key", SENTINEL)))
        # `node.clear` on a proxy raises on ATTRIBUTE ACCESS, which would abort the
        # walk instead of being recorded as a refusal — so the access happens
        # inside the thunk, where the walk catches it like any other refusal.
        out.append(("map.clear()", lambda: node.clear()))
    elif isinstance(node, tuple):
        if node:
            out.append(("tuple[0] = …", lambda: node.__setitem__(0, SENTINEL)))
    elif isinstance(node, list):
        out.append(("list.append(…)", lambda: node.append(SENTINEL)))
    elif isinstance(node, set):
        out.append(("set.add(…)", lambda: node.add(SENTINEL)))
    return out


def walk(root):
    """Every object reachable from `root`, each with the outcome of every mutation
    attempted on it.

    Returns `(rows, holes, paths)`: `rows` is one entry per DISTINCT object, `holes`
    are the mutations that SUCCEEDED, and `paths` is every path the walk reached.
    The two are not the same size, and the difference is not a defect: CPython interns
    small ints and short strings, so `state.schema_version` and `state.state_revision`
    are the same object `1`. Deduplicating by `id` is what stops the walk from
    re-testing that object 40 times; keeping `paths` separately is what lets the test
    still assert that every field was REACHED.
    """
    rows, holes, seen, paths = [], [], set(), set()
    stack = [("state", root)]
    while stack:
        path, node = stack.pop()
        paths.add(path)
        if id(node) in seen:
            continue
        seen.add(id(node))

        kind = type(node).__name__
        results = []
        for desc, thunk in attempts_for(node):
            try:
                thunk()
            except (TypeError, AttributeError, FrozenInstanceError) as e:
                results.append(f"{desc} -> {type(e).__name__}")
            else:
                results.append(f"{desc} -> SUCCEEDED")
                holes.append(f"{path} ({kind}): {desc} succeeded")
        rows.append((path, kind, results))

        # Descend. Scalars are terminal — they have no interior to freeze, and
        # recursing into `str` would never end.
        if is_dataclass(node) and not isinstance(node, type):
            for f in fields(node):
                stack.append((f"{path}.{f.name}", getattr(node, f.name)))
        elif isinstance(node, (MappingProxyType, dict)):
            for k, v in node.items():
                stack.append((f"{path}[{k!r}]", k))
                stack.append((f"{path}[{k!r}]", v))
        elif isinstance(node, (tuple, list, set, frozenset)):
            for i, v in enumerate(node):
                stack.append((f"{path}[{i}]", v))
    return rows, holes, paths


class TheLoadedStateIsImmutableAllTheWayDown(unittest.TestCase):

    def setUp(self):
        self.fx = Fixture(doc=rich_doc())
        self.addCleanup(self.fx.cleanup)
        self.st = state.load_state(self.fx.root / "audit")

    # -- the two the audit reproduced, in the syntax the audit used ----------

    def test_the_two_reported_mutations_now_raise(self):
        with self.assertRaises(TypeError) as limits_exc:
            self.st.limits["reopen_limit"] = 999
        with self.assertRaises(TypeError) as sessions_exc:
            self.st.sessions[0]["role"] = "Remediator"
        with self.assertRaises(TypeError) as nested_exc:
            self.st.sessions[0]["sandbox_details"]["network"] = "all"
        with self.assertRaises(FrozenInstanceError):
            self.st.state_revision = 5
        print("\n[immutability] st.limits['reopen_limit'] = 999          -> "
              f"{type(limits_exc.exception).__name__}: {limits_exc.exception}")
        print("[immutability] st.sessions[0]['role'] = 'Remediator'    -> "
              f"{type(sessions_exc.exception).__name__}: {sessions_exc.exception}")
        print("[immutability] st.sessions[0]['sandbox_details'][…] = … -> "
              f"{type(nested_exc.exception).__name__}: {nested_exc.exception}")
        print(f"[immutability] limits={type(self.st.limits).__name__} "
              f"sessions[0]={type(self.st.sessions[0]).__name__} "
              f"nested={type(self.st.sessions[0]['sandbox_details']).__name__}")

    def test_every_existing_reader_still_works(self):
        """Criterion 4 in one place. `decision.py` and `write.py` reach `limits`
        through the mapping API; freezing must not have changed the shape they read."""
        self.assertEqual(int(self.st.limits.get("reopen_limit", 2)), 2)
        self.assertEqual(self.st.limits.get("absent-key", "fallback"), "fallback")
        self.assertIn("reopen_limit", self.st.limits)
        self.assertEqual(dict(self.st.limits)["reopen_limit"], 2)
        env = self.st.sessions[0]
        self.assertEqual(env.get("session_id"), "a" * 16)
        self.assertEqual(env["role"], "Remediator")
        self.assertIsNone(env.get("never-set"))
        self.assertEqual(dict(env)["provider"], "anthropic")
        self.assertEqual(sorted(env.keys())[:2], ["agent_model", "charter_hash"])

    def test_the_state_survives_a_write_round_trip(self):
        """`json.dumps` cannot encode a `mappingproxy`. If the freeze had been added
        without the matching `thaw` at the serialisation boundary, every write would
        raise TypeError — so this is the test that the fix is not half-applied."""
        doc = state.to_document(self.st)
        text = state.serialise(doc)
        self.assertIn('"sandbox_details"', text)
        again = state.load_state(self.fx.root / "audit")
        self.assertEqual(state.to_document(again), doc)
        self.assertIsInstance(doc["sessions"][0], dict)
        self.assertNotIsInstance(doc["sessions"][0], MappingProxyType)
        print(f"\n[immutability] round trip: to_document -> serialise ({len(text)} bytes) -> "
              f"load_state -> to_document, identical")

    # -- the walk -----------------------------------------------------------

    def test_the_reachability_walk_finds_no_mutable_node(self):
        rows, holes, paths = walk(self.st)
        attempted = sum(len(r[2]) for r in rows)

        # The floor. Three findings, a class finding, a done pass with coverage, a
        # plan, a construal, an envelope with a nested object, limits and catalogs —
        # a walk reporting fewer nodes than 3x the findings has stopped descending.
        floor = 3 * len(self.st.findings)
        print(f"\n[immutability] walk: {len(rows)} nodes (floor {floor}), "
              f"{attempted} mutation attempts, {len(holes)} succeeded")
        for path, kind, results in sorted(rows):
            if results:
                print(f"  {path:<44} {kind:<16} {'; '.join(results)}")

        self.assertGreaterEqual(
            len(rows), floor,
            f"the walk enumerated {len(rows)} nodes against a floor of {floor} — it is "
            f"not descending, so 'zero mutations succeeded' would mean nothing")
        self.assertGreater(attempted, 0, "the walk attempted no mutation at all")
        self.assertEqual(holes, [], f"MUTABLE STATE REACHABLE FROM A LOADED State: {holes}")

        # Every top-level field was REACHED, so no field is quietly outside the walk.
        for f in fields(self.st):
            self.assertIn(f"state.{f.name}", paths, f"the walk never visited {f.name}")

    def test_the_walk_reports_a_planted_hole(self):
        """The walk's own control arm. A plain `dict` one level below a frozen record
        must come back as a hole — otherwise 'zero holes' above is a detector that has
        never been observed detecting anything."""
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class Planted:
            good: tuple = ()
            bad: dict = None

        rows, holes, _paths = walk(Planted(good=(1, 2), bad={"reopen_limit": 2}))
        print(f"\n[immutability] positive control: {len(rows)} nodes, "
              f"{len(holes)} hole(s) reported")
        for h in holes:
            print(f"  HOLE {h}")
        self.assertTrue(holes, "the walk failed to report a plain dict as mutable — "
                               "it cannot be trusted to report zero holes either")
        self.assertTrue(any("reopen_limit" in h or "new-key" in h for h in holes))


class NoLoadedRecordIsCopiedAndMutated(unittest.TestCase):
    """Criterion 5. The write verbs edit the parsed DOCUMENT and re-validate it; they
    never take a loaded record, deep-copy it and patch the copy. That shortcut would
    put the same hole one indirection away, and it would skip validation of the result.
    """

    def test_the_package_never_deepcopies(self):
        hits = []
        for py in sorted((REPO / "xcheck").glob("*.py")):
            for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if "deepcopy" in line:
                    hits.append(f"{py.name}:{n}: {line.strip()}")
        print(f"\n[immutability] grep deepcopy in xcheck/: {len(hits)} hit(s)")
        self.assertEqual(hits, [])

    def test_state_records_are_rebuilt_not_patched(self):
        src = (REPO / "xcheck" / "state.py").read_text(encoding="utf-8")
        self.assertIn("def freeze(obj):", src)
        self.assertIn("def thaw(obj):", src)
        self.assertIn("sessions=freeze(", src)
        self.assertIn("limits=freeze(", src)
        # Criterion 7: the comment claiming `sessions` is reserved for a future phase.
        self.assertNotIn("reserved for the phase-9 invocation envelope", src,
                         "the stale comment on the exact field the audit found mutable "
                         "is still there — phase 9 shipped and populates it")
        env = (REPO / "xcheck" / "envelope.py").read_text(encoding="utf-8")
        self.assertIn("sessions=freeze(sessions)", env,
                      "`envelope.store` rebuilds sessions through `replace()`, which "
                      "bypasses `_build` — without `freeze` there the invariant holds "
                      "only until the first envelope is written")


if __name__ == "__main__":
    unittest.main()
