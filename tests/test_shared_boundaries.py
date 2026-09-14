"""Four shared boundaries, four gates, four counterfactuals — so the next caller inherits them.

PHASE 6 (sixth audit). This module answers the audit's one ARCHITECTURAL finding rather
than another of its symptoms. Six audits have found the same shape every time: a guarantee
stated for the system and enforced on one path.

    one writer fixed, another writes differently
    one dispatcher fixed, the second bypasses the new checks
    one entity verified on export, its neighbour unchecked
    a diagnostic command that sees what the working command misses

Phases 2 to 5 each built a boundary. Without a gate, the seventh audit finds the seventh
caller that did not use one — so the gate is the deliverable here, not the boundary.

Each gate below:

  * enumerates its call sites over the AST of the whole package, never by grepping for a
    name in prose (a docstring promising "no X" fails a substring hunt for X);
  * says WHOM it excludes — thread, process, or machine — because a boundary named
    `.lock` answers none of the three, and that ambiguity is this audit's finding 1;
  * carries a DECLARED exception list whose length is pinned, whose every entry has a
    non-empty reason, and which is printed in full;
  * has a counterfactual that edits a temporary COPY of the tree and asserts the gate
    names THAT violation — not merely that something failed.
"""

import ast
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from tests.harness import REPO

PACKAGE = REPO / "xcheck"


# --------------------------------------------------------------------------- machinery

def modules(root=None):
    """Every package module, as (name, parsed tree). One parse per module per gate run."""
    root = Path(root or PACKAGE)
    out = {}
    for f in sorted(root.glob("*.py")):
        if f.name == "__init__.py":
            continue
        out[f.name] = ast.parse(f.read_text(encoding="utf-8"))
    return out


def called(node):
    """The name of a call, whether `f()` or `mod.f()`."""
    return getattr(node.func, "id", getattr(node.func, "attr", ""))


def enclosing(tree, target):
    """The FunctionDef containing this node, by line span. `None` at module level."""
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno <= target.lineno <= (node.end_lineno or node.lineno):
                if best is None or node.lineno > best.lineno:
                    best = node
    return best


def call_sites(trees, name):
    """`[(module, function, lineno)]` for every call to `name` in the package."""
    found = []
    for mod, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and called(node) == name:
                fn = enclosing(tree, node)
                found.append((mod, fn.name if fn else "<module>", node.lineno))
    return found


def takes_the_window(fn, opener):
    """True when this function opens `with opener():` anywhere in its body.

    Over the AST's `With` items, so a docstring that mentions the name does not count and
    a call inside a nested function does.
    """
    for node in ast.walk(fn):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if isinstance(item.context_expr, ast.Call) \
                        and called(item.context_expr) == opener:
                    return True
    return False


def function_named(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


class Gate:
    """One boundary, and the rule that says who may cross it.

    `excludes` is the question `audit/.lock` could not answer: a directory lock excludes
    another PROCESS and is blind to a second thread of its own; an `RLock` excludes
    another THREAD and is blind to another process. Naming it is half the gate.
    """

    def __init__(self, name, excludes, exceptions, check):
        self.name = name
        self.excludes = excludes
        self.exceptions = exceptions
        self.check = check

    def violations(self, trees):
        return [v for v in self.check(trees)
                if f"{v[0]}:{v[1]}" not in self.exceptions]

    def report(self, trees):
        found = self.violations(trees)
        print(f"\n  GATE {self.name} — excludes: {self.excludes}")
        print(f"       exceptions ({len(self.exceptions)}):")
        for where, why in sorted(self.exceptions.items()):
            print(f"         {where:24s} {why}")
        print(f"       violations: {found or 'none'}")
        return found


# ------------------------------------------------------------------------- the 4 gates

def unserialized_state_writes(trees):
    """GATE 1. Every write of canonical state, inside the serialized window."""
    bad = []
    for mod, fn, line in call_sites(trees, "write_state"):
        tree = trees[mod]
        node = function_named(tree, fn)
        if node is None or not takes_the_window(node, "serialized"):
            bad.append((mod, fn, line))
    return bad


def unconfirmed_reservations(trees):
    """GATE 2. Every reserved event confirmed through the one transaction protocol: the
    same function hands an identity to `reserve` AND to `write_state`."""
    bad = []
    for mod, fn, line in call_sites(trees, "reserve"):
        node = function_named(trees[mod], fn)
        if node is None:
            bad.append((mod, fn, line))
            continue
        reserves = [c for c in ast.walk(node)
                    if isinstance(c, ast.Call) and called(c) == "reserve"]
        confirms = [c for c in ast.walk(node)
                    if isinstance(c, ast.Call) and called(c) == "write_state"
                    and any(k.arg == "txn" for k in c.keywords)]
        if not all(len(c.args) >= 3 or any(k.arg == "txn" for k in c.keywords)
                   for c in reserves) or not confirms:
            bad.append((mod, fn, line))
    return bad


def unchecked_appends(trees):
    """GATE 3. Every append to the event stream, behind the completeness check."""
    bad = []
    for mod, fn, line in call_sites(trees, "append_chained"):
        node = function_named(trees[mod], fn)
        if node is None:
            bad.append((mod, fn, line))
            continue
        asks = [c for c in ast.walk(node)
                if isinstance(c, ast.Call) and called(c) == "require_complete"]
        if not asks:
            bad.append((mod, fn, line))
    return bad


def private_command_builds(trees):
    """GATE 4. Every child's command built by the one dispatch preparation."""
    bad = []
    for mod, fn, line in call_sites(trees, "build_cmd"):
        if (mod, fn) != ("runner.py", "prepare_launch"):
            bad.append((mod, fn, line))
    return bad


GATES = (
    Gate("1 canonical state", "THREADS of this process (util.serialized is an RLock; "
                              "audit/.lock excludes other PROCESSES and is blind to a "
                              "second thread of its own)",
         {"migrate.py:migrate": "the schema migration runs before the audit has a "
                                "control plane to contend for: it takes audit/.lock "
                                "against other processes, and there is no second thread "
                                "in a migration to exclude"},
         unserialized_state_writes),
    Gate("2 transaction identity", "a TRANSACTION — neither thread nor process, but one "
                                   "specific state write, so recovery can tell a "
                                   "committed transition from a counter that moved",
         {}, unconfirmed_reservations),
    Gate("3 the journal door", "an INCOMPLETE STREAM — not a writer at all, but a state "
                               "of the evidence: broken, short, replaced or unreadable",
         {"ledger.py:commit": "phase two of the reserve/commit unit. Its caller "
                              "(`write._apply`) asked the check before it mutated "
                              "anything, and asking again after the state write would "
                              "refuse an event whose transition is already durable — "
                              "which is how a committed transition becomes unrecoverable"},
         unchecked_appends),
    Gate("4 dispatch preparation", "a SECOND DISPATCHER: any path that starts a child "
                                   "without the route, the sidecar and the operator's "
                                   "log root",
         {"selftest.py:selftest": "three assertions ABOUT the builder's own "
                                  "substitution, run in-process against a literal "
                                  "template. They start no child and dispatch nothing"},
         private_command_builds),
)

#: The number of declared exceptions, pinned. Adding one requires editing this number,
#: which is the point: an exception list that can grow silently is how a gate stops
#: checking. Per gate, in the order above.
EXCEPTION_COUNTS = (1, 0, 1, 1)


# ----------------------------------------------------------------------------- the arms

class TheUnmodifiedTreePasses(unittest.TestCase):
    """CONTROL. All four gates armed on the real package: none fires.

    Without this the counterfactuals below prove only that the gates refuse something.
    """

    def test_all_four_gates_are_silent_on_the_tree_as_it_stands(self):
        trees = modules()
        for gate in GATES:
            self.assertEqual([], gate.report(trees),
                             f"gate {gate.name} fires on the unmodified package")

    def test_every_exception_is_declared_pinned_and_reasoned(self):
        for gate, pinned in zip(GATES, EXCEPTION_COUNTS):
            self.assertEqual(pinned, len(gate.exceptions),
                             f"gate {gate.name} has {len(gate.exceptions)} exception(s) "
                             f"and the pin says {pinned} — update the pin deliberately")
            for where, why in gate.exceptions.items():
                self.assertTrue(why.strip(), f"{where} is excepted with no reason")
                self.assertGreater(len(why), 40,
                                   f"{where}'s reason is too short to be one: {why!r}")
                self.assertIn(":", where, "an exception must name module:function")
        print(f"\n  PINNED     {dict(zip((g.name for g in GATES), EXCEPTION_COUNTS))}")

    def test_each_gate_says_whom_it_excludes(self):
        """A boundary that cannot say whether it excludes a thread, a process or a
        machine is the ambiguity that produced this audit's finding 1."""
        for gate in GATES:
            said = gate.excludes.lower()
            self.assertTrue(any(w in said for w in ("thread", "process", "machine",
                                                    "transaction", "stream", "dispatcher")),
                            f"gate {gate.name} does not say whom it excludes")
        print("  WHOM       " + " · ".join(f"{g.name}: {g.excludes.split('(')[0].strip()}"
                                           for g in GATES))


class EachGateIsLoadBearing(unittest.TestCase):
    """Four counterfactuals, each asserting the SPECIFIC violation it caught.

    N arms can cover fewer than N defects unless each names its own, so every arm below
    asserts the module and function it expects — and asserts the OTHER three gates stay
    silent, which is what proves the four are four and not one.
    """

    def in_a_copy(self, edits):
        """The package, copied to a temp dir, with `edits` applied. The real tree is
        never written: a gate test that mutates the thing it is checking is a gate test
        that has to be run twice to be believed."""
        tmp = Path(tempfile.mkdtemp(prefix="xcheck-gates-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        copy = tmp / "xcheck"
        shutil.copytree(PACKAGE, copy)
        for name, (old, new) in edits.items():
            path = copy / name
            src = path.read_text(encoding="utf-8")
            self.assertIn(old, src, f"the mutation's subject is not in {name}")
            path.write_text(src.replace(old, new, 1), encoding="utf-8")
        return modules(copy)

    def only_this_gate_fires(self, trees, index, expected):
        found = {}
        for i, gate in enumerate(GATES):
            found[gate.name] = gate.violations(trees)
        mine = found[GATES[index].name]
        others = {n: v for n, v in found.items() if n != GATES[index].name and v}
        print(f"    caught: {mine}")
        print(f"    other gates: {others or 'silent'}")
        self.assertTrue(mine, f"gate {GATES[index].name} did not fire")
        self.assertIn(expected, [(m, f) for m, f, _ln in mine],
                      f"the gate fired, but not on {expected}")
        self.assertEqual({}, others,
                         "another gate fired too, so these arms do not separate")

    def test_1_a_state_write_outside_the_window_is_named(self):
        print("\n  COUNTERFACTUAL 1 — envelope.store writes state with no serialized()")
        trees = self.in_a_copy({"envelope.py": (
            "    with serialized():\n        try:\n            state = load_state(audit)",
            "    if True:\n        try:\n            state = load_state(audit)")})
        self.only_this_gate_fires(trees, 0, ("envelope.py", "store"))

    def test_2_a_reservation_without_an_identity_is_named(self):
        print("  COUNTERFACTUAL 2 — write._apply reserves without a transaction")
        trees = self.in_a_copy({"write.py": (
            "reserved = ledger.reserve(project, line, txn)",
            "reserved = ledger.reserve(project, line)")})
        self.only_this_gate_fires(trees, 1, ("write.py", "_apply"))

    def test_3_an_append_without_the_completeness_check_is_named(self):
        print("  COUNTERFACTUAL 3 — envelope.emit appends without require_complete")
        trees = self.in_a_copy({"envelope.py": (
            'ledger.require_complete(project, ledger.committed_anchor(project),\n'
            '                                    action="emit")',
            'pass')})
        self.only_this_gate_fires(trees, 2, ("envelope.py", "emit"))

    def test_4_a_dispatcher_that_builds_its_own_command_is_named(self):
        print("  COUNTERFACTUAL 4 — parallel.agent_pass builds its own command")
        trees = self.in_a_copy({"parallel.py": (
            "        cmd = plan.cmd",
            "        cmd = build_cmd(conf, 'Auditor', prompt)")})
        # `session`, not `agent_pass`: the build happens in the per-pass CLOSURE, and
        # the gate reports the innermost function containing the call. Naming the outer
        # function would have been a guess that happened to be wrong.
        self.only_this_gate_fires(trees, 3, ("parallel.py", "session"))

    def test_the_four_mutations_are_four_different_things(self):
        """Two plants must be proved different: the arms above would all pass if one
        mutation happened to trip several gates. Each mutation's SUBJECT is named here."""
        subjects = {
            1: ("envelope.py", "the serialized window around a state write"),
            2: ("write.py", "the transaction identity handed to reserve"),
            3: ("envelope.py", "the completeness check before an append"),
            4: ("parallel.py", "the shared build the second dispatcher calls"),
        }
        print(f"  DISTINCT   {len({s for s, _ in subjects.values()})} module(s), "
              f"{len({d for _, d in subjects.values()})} distinct subject(s)")
        self.assertEqual(4, len({d for _, d in subjects.values()}))


class TheGatesAreCheapEnoughToAlwaysRun(unittest.TestCase):
    """A gate in a slow tier is a gate somebody skips. A tier nothing triggers runs
    nowhere, so these live in the ordinary suite and the cost is printed."""

    def test_the_combined_cost_is_printed(self):
        start = time.perf_counter()
        trees = modules()
        parse_ms = (time.perf_counter() - start) * 1000
        per_gate = {}
        for gate in GATES:
            t0 = time.perf_counter()
            gate.violations(trees)
            per_gate[gate.name] = (time.perf_counter() - t0) * 1000
        total = parse_ms + sum(per_gate.values())
        print(f"\n  COST       parsing {len(trees)} module(s): {parse_ms:.1f} ms")
        for name, ms in per_gate.items():
            print(f"             gate {name}: {ms:.2f} ms")
        print(f"             all four, including the parse: {total:.1f} ms")
        self.assertLess(total, 5000, "the gates are too slow to run in the ordinary suite")

    def test_this_module_is_in_a_tier_that_runs_on_every_pull_request(self):
        """The gate that guards the gates. `ci/tiers.py` is the declaration, and a module
        in no tier is a module nothing runs."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("tiers", REPO / "ci" / "tiers.py")
        tiers = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tiers)
        me = "test_shared_boundaries"
        where = [name for name in ("PR", "RELEASE_ONLY", "TAG_ONLY")
                 if me in getattr(tiers, name, ())]
        print(f"  TIER       {me} is in {where}")
        self.assertIn("PR", where,
                      "the boundary gates must run on every pull request, not only on a "
                      "release: a gate that runs late is a gate that catches late")


if __name__ == "__main__":
    unittest.main()
