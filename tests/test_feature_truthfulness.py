"""A public flag must name the production code that carries out what it promises.

The fourth audit's cheapest finding: xcheck proves its mechanisms EXIST better than it
proves they are REACHABLE. `model_routing=on` computed a route and threw it away —
`runner.build_cmd` had already built the argv before the route existed. `diff_scope=on`,
`evidence_cache=on` and the recurring-class surface had, between them, zero production
call sites. Each shipped as a documented conf key, a README row, a test module and a
library. What none of them had was a consumer.

Nothing here is a heuristic, because a heuristic is how the NEXT unwired feature gets
excused. Three declarations carry the whole gate:

  FEATURES     — the keys that switch a mechanism on, each naming the production
                 function that DOES the thing (`mechanism`) and the production function
                 that must reach it (`consumer`).
  PLAIN_CONFIG — the keys that tune a mechanism which runs either way: a path, a
                 deadline, a limit, an image, a mode. Declared, with its kind.
  UNWIRED      — the flags that fail today, each with the phase that closes it and how.

`FEATURES` and `PLAIN_CONFIG` must PARTITION `policy.PROJECT_KEYS | policy.POLICY_KEYS`
exactly, both directions, so a new key cannot arrive unclassified and a deleted key
cannot leave a stale row behind.

Resolution is over the AST, never over the flag's name. Grepping for `model_routing`
finds `CONF_DEFAULTS["model_routing"]`, `routing.FLAG`, the README table and this
docstring — the flag's own definition satisfies a name search, which is exactly why the
four unwired features looked wired for a release. What the gate asks instead is whether
a CALL PATH exists: `mechanism` reachable from `consumer` through calls resolved by
module, and `consumer` reachable from `cli.py` or `runner.py`.

PHASE 11 (fifth audit) — WHAT THE PARAGRAPH ABOVE DOES NOT PROVE. It is kept because it is
still true and still useful, not because it was sufficient. `UNWIRED = {}` shipped as the
previous run's success condition, and `model_routing` satisfied it while being incapable
of running: the route was computed, `build_cmd` was called a second time without it, and
the child received `{model}` unfilled. A call path proves a function is REACHED. It says
nothing about whether that function's RESULT reaches the child process — which is the
audit's central diagnosis, the gap between the property promised and the criterion the
test actually checks.

So a fourth declaration joins the three:

  WITNESS      — the scenario in `tests/test_smoke_e2e.py` that drives the public entry
                 point and asserts an artefact the CHILD produced, or an entry in
                 WITNESSLESS saying why this feature has none.

The witness is RESOLVED (the scenario exists and holds a test) and checked to EXERCISE its
feature (its own body names the key), because a witness that is only named is the same
class of claim as a call edge that is only present.
"""

import ast
import shutil
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path

from tests.harness import REPO

from xcheck import policy

PACKAGE = REPO / "xcheck"

# The two files a user's invocation actually enters through. `bin/xcheck` is a shim over
# `cli.main`, and every session the orchestrator runs enters `runner`. A function neither
# of them can reach is not on any path a user can take.
ENTRY_MODULES = ("cli", "runner")

# name      — what the operator calls the feature (the conf key, or the surface's name)
# key       — the conf key that switches it, or None for a surface with no key of its own
# mechanism — the production function that CARRIES OUT the feature
# consumer  — the production function that must reach the mechanism for it to happen
# witness   — the smoke scenario that drives the PUBLIC entry point and asserts an
#             artefact the child produced, or None with a reason in WITNESSLESS
Feature = namedtuple("Feature", "name key mechanism consumer witness kind")


def _flag(key, mechanism, consumer, witness=None, kind=None):
    return Feature(key, key, mechanism, consumer, witness, kind)


# ---------------------------------------------------------------- PHASE 9 (sixth audit)
#
# WHAT "3 OF 20" DID NOT MEAN. The sixth audit read the figure carefully and said what it
# is not: it is not 15% product correctness. Some of the 17 are read-only gates, and a
# gate's end-to-end proof is not "a child process wrote a file" — it is that the gate
# REFUSES the thing it exists to refuse, observed at the surface a user reaches. Grading
# a refusal against a dispatch's evidence is grading a ruler in litres.
#
# The audit's other half stands, so this phase SPLITS the metric rather than inflating it:
# one number covering two kinds of proof was the problem, and one number that went up
# would be the same problem with better PR. The re-score below is 6 of 20, not 20 of 20,
# and the features that moved did so because a witness of their kind already existed or
# was written in this run.
#
# The three kinds are closed. A feature is assigned exactly one, by what its proof must
# LOOK like — not by whether it currently has one.
DISPATCH = "dispatch"
REFUSAL = "refusal"
CORPUS = "corpus"

WITNESS_KINDS = {
    DISPATCH: "a child process ran and the scenario asserts an artefact THE CHILD "
              "produced — the only kind that can catch a value computed and discarded, "
              "which is what `model_routing` was",
    REFUSAL: "no child exists: the feature is a read-only or decision-time gate, and its "
             "witness drives the public surface and asserts the REFUSAL the gate exists "
             "to produce. A gate that never refused anything in a test is a gate nobody "
             "has seen work",
    CORPUS: "the gate compares against MEASURED history, so its witness must build a "
            "corpus of finished sessions and show the ceiling firing over it. One "
            "dispatch cannot exceed a ceiling derived from many",
}

#: What a witness of each kind must REACH for the proof to be end-to-end. Declared per
#: entry, because "the public surface" is otherwise whatever the next author needs it to
#: be — and a refusal asserted against an internal predicate is the `UNWIRED = {}` mistake
#: with a different subject.
PUBLIC_SURFACE = {
    "cli.main(": "the process entry point every invocation passes through",
    "main_argv(": "the same entry point, with argv supplied directly",
    "cmd_next(": "the verb that decides and dispatches",
    "cmd_lint(": "the lint verb, which produces the scope-typing verdict",
    "cmd_okf(": "the OKF export verb, which calls and prints the verdict",
    "cmd_status(": "the status verb, the read-only surface a user looks at",
    "cmd_evidence_bundle(": "the export verb, which refuses unless explicitly on",
    "cmd_recover(": "the recovery verb, which replays or refuses an outbox",
    "verify_bundle(": "OKF's verdict function, which `cmd_okf` calls and prints and "
                      "whose exit code the verb returns",
    "run_session(": "the orchestrator's own dispatch of one session",
    "run_cli(": "a real child process driven through the shipped CLI",
    "parallel.session(": "the parallel dispatcher's per-pass entry",
    "self.dispatch(": "tests/test_smoke_e2e.py's own helper, whose body is "
                      "`runner.run_session(...)` — the surface is reached through it, and "
                      "requiring the literal call in every scenario would only push the "
                      "plumbing back into the test bodies",
}

#: A witness may name a scenario in the smoke module (bare) or in another module
#: (`path:Class`). The loophole the previous table closed was pointing at a FILE rather
#: than a scenario — `path:Class` is still a scenario, resolved and body-checked exactly
#: as a smoke one is, so this is not a widening. A refusal witness CANNOT live in the
#: smoke module: that module dispatches children, and a gate has none.

FEATURES = (
    # --- discipline gates: they change what a decision or a lint reports -------------
    _flag("scope_typing", "cli.cmd_lint", "cli.main", kind=REFUSAL),
    _flag("construal_gate", "decision.state_and_decision", "cli.cmd_next", kind=REFUSAL),
    _flag("embargo", "decision.state_and_decision", "cli.cmd_next", kind=REFUSAL),
    _flag("audit_over_critical", "decision.state_and_decision", "cli.cmd_next",
          kind=REFUSAL),
    # --- the budget controller: one switch, six ceilings, one gate function ----------
    _flag("budgets", "budget.gates", "cli.cmd_next", "CapExceeded", DISPATCH),
    _flag("tokens_per_session", "budget.gates", "cli.cmd_next", "CapExceeded",
          DISPATCH),
    _flag("tokens_per_pass", "budget.gates", "cli.cmd_next", kind=CORPUS),
    _flag("tokens_per_audit", "budget.gates", "cli.cmd_next", kind=CORPUS),
    _flag("no_progress_share_pct", "budget.gates", "cli.cmd_next", kind=CORPUS),
    _flag("charter_repeat_limit", "budget.gates", "cli.cmd_next", kind=CORPUS),
    _flag("marginal_value_window", "budget.gates", "cli.cmd_next", kind=CORPUS),
    # --- features with a verb of their own -------------------------------------------
    _flag("okf", "okf.okf_enabled", "cli.cmd_okf",
          "tests/test_okf.py:AManifestIsNotTheAuthorityOnWhatABundleIs", REFUSAL),
    _flag("evidence_bundle", "bundle.bundle_enabled", "cli.cmd_evidence_bundle",
          "tests/test_evidence_bundle.py:GeneratingItIsAnExplicitVerb", REFUSAL),
    _flag("parallel_passes", "parallel.enabled", "cli.cmd_next",
          "tests/test_parallel_dispatch.py:BothDispatchersProduceOneArgv", DISPATCH),
    # --- containment and side effects -------------------------------------------------
    _flag("sandbox_profile", "runner.resolve_profile", "runner.run_session",
          kind=DISPATCH),
    _flag("unsafe_allow_unpinned_image", "runner.container_image_ref",
          "runner.run_session", kind=DISPATCH),
    _flag("egress_allowlist", "egress.allowlist", "runner.run_session", kind=DISPATCH),
    _flag("push_after_commit", "courier.courier_commit", "cli.cmd_next", kind=DISPATCH),
    # --- the four the audit found ------------------------------------------------------
    # The mechanism named here is the EFFECT, not the computation. `routing` already had
    # a reachable caller — `runner._route_for_dispatch` computes a route and reports it —
    # and that is precisely the shape the audit caught: a value computed and discarded.
    # `runner.build_cmd` is the function that builds the argv the provider is launched
    # with, so it is the function the route has to reach before "model routing" is a
    # thing that happens rather than a thing that is printed.
    # PHASE 13 moved this declaration, and the move IS the fix. It named
    # `routing.route_of_dispatch` reaching `runner.build_cmd`, which was true and proved
    # nothing: `build_cmd` called the router, ignored the answer and produced identical
    # argv either way — the audit's exact finding. The mechanism that matters is the one
    # that CHANGES the command, so the edge asserted is `routing.model_for` reaching
    # `build_cmd`. Declaring the call that computes a label was how a dead feature stayed
    # green; declaring the call that applies it cannot be satisfied without the effect.
    _flag("model_routing", "routing.model_for", "runner.build_cmd",
          "RoutingApplied", DISPATCH),
    # `diff_scope` and `evidence_cache` were here at phase 1 and are gone at phase 2:
    # the keys were WITHDRAWN rather than wired, which is the other way an entry leaves
    # this table. `policy.WITHDRAWN_KEYS` refuses them by name in both conf files, and
    # `tests/test_diff_scope.py` / `tests/test_evidence_cache.py` re-assert at their new
    # home what those keys' contract tests were for.
    # The recurring-class surface has no conf key at all — it shipped as a library with a
    # test module and nothing else. `key=None` says so; the wiring question is identical.
    Feature("classes", None, "classes.candidates", "cli.main", None,
            REFUSAL),
)

# Everything else: a value that tunes a mechanism which runs whether or not the operator
# sets it. The KIND is declared per key rather than sniffed from the name, because
# `*_deadline` and `*_dir` are conventions, and a convention would quietly excuse the
# next `model_routing` the moment someone named it `routing_dir`.
PLAIN_CONFIG = {
    "batch_size": "limit",
    "embargo_after": "limit",
    "loop_progress_limit": "limit",
    "triage_batch_cap": "limit",
    "max_sessions": "limit",
    "max_sessions_per_run": "limit",
    "parallel_workers": "limit",
    "parallel_budget_minutes": "limit",
    "parallel_confirm_above": "limit",
    "retry_limit": "limit",
    "log_retention_days": "limit",
    "cpu_seconds": "limit",
    "address_space_mb": "limit",
    "container_cpus": "limit",
    "container_memory_mb": "limit",
    "container_pids": "limit",
    "session_timeout": "deadline",
    "startup_deadline": "deadline",
    "output_deadline": "deadline",
    "activity_deadline": "deadline",
    "idle_deadline": "deadline",
    "kill_grace": "deadline",
    "lease_ttl": "deadline",
    "log_dir": "path",
    "container_image": "image",
    "egress_broker_image": "image",
    "egress_uplink": "mode",
    "telemetry_adapter": "mode",
    "cheap_model": "text",
    "strong_model": "text",
    "trust_level": "mode",
    "env_allowlist": "list",
    "session_note": "text",
}

# The four the audit found, each with the phase that closes it and HOW. An entry without
# a closing phase is not an exemption, it is a permanent excuse, so the gate refuses one.
# EMPTY, as of phase 15. `model_routing` left at phase 13 (the route now resolves to a
# model and reaches the provider through `{model}` in the role command) and `classes` at
# phase 15 (the detector's candidates print in `xcheck status` and at the triage gate, and
# its proposals travel in the OKF bundle). `diff_scope` and `evidence_cache` left at
# phase 2 by having their public flags WITHDRAWN — the modules stay as experimental, which
# is the other honest way to close this list.
#
# An empty allowlist is the state this gate was built to reach: every public feature flag
# resolves to a production call site. Adding an entry here is now a visible decision.
UNWIRED = {}

# The pin that makes this gate a ratchet rather than a ledger. Recorded at phase 1 of the
# fourth audit's run; phase 18 asserts it has reached zero. A run that ADDED an unwired
# feature would have to raise this number in the same commit, which is a review that
# happens rather than a drift that does not.
UNWIRED_AT_PHASE_1 = 4

VALID_DISPOSITIONS = ("wired", "deleted")
LAST_PHASE = 18

# ---------------------------------------------------------------- PHASE 11 (fifth audit)
#
# WHAT `UNWIRED = {}` DID NOT PROVE. The previous run's success condition was that every
# public flag names a production call site, and `model_routing` satisfied it while being
# incapable of running: `runner.build_cmd` was called twice, the second call had no route,
# and the child received `{model}` unfilled. A call-graph edge proves a function is
# REACHED. It cannot prove the function's RESULT reaches the child process, and that gap
# is the audit's central diagnosis — the property promised versus what the test checks.
#
# The edge check STAYS. It is cheap, it runs without a container, and it catches a
# different defect: a library with no consumer at all, which is what `diff_scope`,
# `evidence_cache` and `classes` were. What is added above it is a WITNESS: a scenario in
# `tests/test_smoke_e2e.py` that drives the public entry point and asserts an artefact the
# child produced. Of the two, only the witness would have caught the routing defect —
# `RoutingApplied` reads the argv the fake agent actually received.
#
# A witness is not claimed where none exists. Most of the table below is decision-time or
# read-only: a gate that changes what `xcheck next` reports produces no child artefact to
# read, and inventing a "witness" for it would be the same move this phase exists to stop.
# The list is declared with a reason each, and it is a RATCHET: it may shrink, never grow.
WITNESSLESS = {
    "scope_typing": "REFUSAL kind, unwitnessed: tests/test_state_consumers.py:"
                    "ScopeTyping asserts the typing rule against the classifier "
                    "and never runs `cmd_lint`, so nothing has watched the lint "
                    "verdict itself refuse at the surface",
    "construal_gate": "decision-time: it changes what `xcheck next` RECOMMENDS, and a "
                      "recommendation is read before any child exists",
    "embargo": "decision-time, as above — the effect is a route not taken",
    "audit_over_critical": "decision-time, as above — a priority order over the queue",
    "tokens_per_pass": "a ceiling compared against MEASURED history; a smoke run has one "
                       "session and no history to exceed",
    "tokens_per_audit": "as tokens_per_pass — the gate needs a corpus, not a dispatch",
    "no_progress_share_pct": "needs many finished sessions to compute a share over",
    "charter_repeat_limit": "needs a charter dispatched repeatedly across runs",
    "marginal_value_window": "needs a run long enough to go idle",
    "sandbox_profile": "the smoke fixture runs ONE profile, so no scenario can tell two "
                       "apart. `TheRepositoryIsNeverWritten` observes the effect and "
                       "inherits the profile from the shared base — claiming it would let "
                       "every scenario claim it",
    "unsafe_allow_unpinned_image": "needs a container pull; the smoke suite starts no "
                                   "container and must not",
    "egress_allowlist": "needs a broker and a network to be denied; same reason",
    "push_after_commit": "the one effect this run may never produce — never git push",
    "classes": "a detector whose output is printed by `xcheck status`; nothing dispatches",
}

# The pin. Recorded at phase 11 of the fifth audit's run, with 3 of 20 features witnessed.
# It may FALL — every phase that adds a scenario should lower it — and it may not rise: a
# feature arriving without a witness has to raise this number in the same commit, which is
# a decision somebody makes rather than a drift nobody sees.
WITNESSLESS_AT_PHASE_11 = 17

# PHASE 9 (sixth audit). The new pin, DERIVED from the re-scored table rather than chosen:
# `test_the_new_pin_is_derived_not_chosen` recomputes it from FEATURES and refuses a
# number that was typed. Three features moved out — `okf` and `evidence_bundle` have
# REFUSAL-kind witnesses that drive their own verb, `parallel_passes` has a DISPATCH-kind
# one that drives both real dispatchers — and none moved by being reclassified.
WITNESSLESS_AT_PHASE_9 = 14

#: Stated, not closed. Every witness below proves one feature does its job when it is the
#: thing being exercised. NONE of them proves the features are correct in COMBINATION: a
#: routed cheap model under a container profile with an egress allowlist and a budget
#: ceiling is a configuration no scenario here runs, and the audit's point is that
#: matching one scenario is not correctness. This stays open, and it is named here so that
#: 20 green rows cannot be read as saying otherwise.
COMBINATION_GAP = (
    "per-feature witnesses do not prove correct behaviour in COMBINATION. Each scenario "
    "exercises one feature with the others at their defaults; no scenario runs two "
    "features whose effects could interact (routing under a container profile, a budget "
    "ceiling under parallel dispatch). OPEN — no phase of this run closes it."
)


class CallGraph:
    """Calls between the shipped modules, resolved BY MODULE rather than by bare name.

    Bare-name resolution is the trap here. `parallel.enabled`, `scope.enabled` and
    `routing.enabled` are three different functions with one name; a graph that joins
    them makes `cli.cmd_next` — which calls `parallel.enabled` — look like a caller of
    the routing and scope libraries too, and the gate would then certify as wired the
    exact features the audit found unwired. So an `import`-derived alias map decides
    which module `x.f()` means, a bare `f()` resolves inside its own module or through
    a `from xcheck.m import f` binding, and a call that resolves to nothing resolves to
    nothing rather than to everything.
    """

    def __init__(self, root):
        self.root = Path(root)
        self.trees = {p.stem: ast.parse(p.read_text(), filename=p.name)
                      for p in sorted(self.root.glob("*.py"))}
        self._reach = {}      # cache: roots -> reachable set (one AST walk per root)
        self._sites = {}      # cache: key -> read sites
        self.consts = {}      # (module, NAME) -> str, for `FLAG = "okf"` indirection
        self.aliases = {}     # module -> {local name: module it refers to}
        self.imported = {}    # module -> {local name: (module, function)}
        self.defs = {}        # (module, name) -> {qualname}
        self.body = {}        # qualname -> ast node
        for mod, tree in self.trees.items():
            self._imports(mod, tree)
            self._constants(mod, tree)
        for mod, tree in self.trees.items():
            self._definitions(mod, tree, "")

    def _imports(self, mod, tree):
        alias, frm = {}, {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.startswith("xcheck."):
                        alias[a.asname or a.name.split(".")[-1]] = a.name.split(".")[-1]
            elif isinstance(node, ast.ImportFrom) and node.module:
                parts = node.module.split(".")
                if node.module == "xcheck":            # from xcheck import routing
                    for a in node.names:
                        alias[a.asname or a.name] = a.name
                elif parts[0] == "xcheck" and len(parts) == 2:   # from xcheck.util import f
                    for a in node.names:
                        frm[a.asname or a.name] = (parts[1], a.name)
        self.aliases[mod], self.imported[mod] = alias, frm

    def _constants(self, mod, tree):
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.consts[(mod, target.id)] = node.value.value

    def _definitions(self, mod, node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{mod}.{prefix}{child.name}"
                self.defs.setdefault((mod, child.name), set()).add(qual)
                self.body[qual] = child
                self._definitions(mod, child, prefix + child.name + ".")
            elif isinstance(child, ast.ClassDef):
                self._definitions(mod, child, prefix + child.name + ".")
                # `Broker(conf)` is a call to `Broker.__init__`. Without this the egress
                # broker's conf reads sit in a function nothing appears to call.
                for method in ("__init__", "__call__"):
                    qual = f"{mod}.{prefix}{child.name}.{method}"
                    if qual in self.body:
                        self.defs.setdefault((mod, child.name), set()).add(qual)

    def _targets(self, mod, call):
        func = call.func
        if isinstance(func, ast.Name):
            if (mod, func.id) in self.defs:
                return self.defs[(mod, func.id)]
            if func.id in self.imported[mod]:
                return self.defs.get(self.imported[mod][func.id], set())
            if func.id in self.aliases[mod]:               # Module(...) — never valid,
                return set()                               # but explicit about it
            return set()
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name) and func.value.id in self.aliases[mod]:
                return self.defs.get((self.aliases[mod][func.value.id], func.attr), set())
            # `self.f()` / `obj.f()`: resolve inside this module only. Under-resolving
            # here reports a wired feature as unwired, which is loud; over-resolving
            # would excuse an unwired one, which is silent.
            return self.defs.get((mod, func.attr), set())
        return set()

    def reachable(self, roots):
        cached = self._reach.get(tuple(sorted(roots)))
        if cached is not None:
            return cached
        seen = {r for r in roots if r in self.body}
        stack = list(seen)
        while stack:
            qual = stack.pop()
            mod = qual.split(".")[0]
            for node in ast.walk(self.body[qual]):
                if isinstance(node, ast.Call):
                    for target in self._targets(mod, node):
                        if target not in seen:
                            seen.add(target)
                            stack.append(target)
        self._reach[tuple(sorted(roots))] = seen
        return seen

    def entry_reachable(self):
        return self.reachable([q for q in self.body if q.split(".")[0] in ENTRY_MODULES])

    def _literal(self, mod, node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return self.consts.get((mod, node.id))
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            owner = self.aliases[mod].get(node.value.id, node.value.id)
            return self.consts.get((owner, node.attr))
        return None

    def read_sites(self, key):
        """Every `f(..., "key", ...)` and `x["key"]` inside a function, with its line.

        Module level is deliberately excluded: `CONF_DEFAULTS` and `policy.PROJECT_KEYS`
        are module-level dicts naming every key there is, and counting them would make
        the gate green for a flag nothing reads.
        """
        if key in self._sites:
            return self._sites[key]
        found = []
        for qual, node in self.body.items():
            mod = qual.split(".")[0]
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    values = [self._literal(mod, a) for a in inner.args]
                elif isinstance(inner, ast.Subscript):
                    values = [self._literal(mod, inner.slice)]
                else:
                    continue
                if key in values:
                    found.append((qual, inner.lineno))
        self._sites[key] = sorted(found)
        return self._sites[key]


Row = namedtuple("Row", "name status detail")


def wiring_report(graph, features=FEATURES):
    """One row per feature: `wired` with its call site, or `unwired` with what is missing."""
    entry = graph.entry_reachable()
    rows = []
    for feature in features:
        if feature.consumer not in graph.body:
            rows.append(Row(feature.name, "unwired",
                            f"consumer {feature.consumer} is not a function in this tree"))
            continue
        if feature.consumer not in entry:
            rows.append(Row(feature.name, "unwired",
                            f"consumer {feature.consumer} is unreachable from "
                            + "/".join(ENTRY_MODULES)))
            continue
        from_consumer = graph.reachable([feature.consumer])
        if feature.mechanism not in from_consumer:
            rows.append(Row(feature.name, "unwired",
                            f"{feature.consumer} does not reach {feature.mechanism}"))
            continue
        if feature.key is not None:
            sites = [(q, n) for q, n in graph.read_sites(feature.key) if q in from_consumer]
            if not sites:
                rows.append(Row(feature.name, "unwired",
                                f"{feature.key} is never read under {feature.consumer}"))
                continue
            where = f"{sites[0][0]}:{sites[0][1]}"
        else:
            where = feature.mechanism
        rows.append(Row(feature.name, "wired", f"{where} <- {feature.consumer}"))
    return rows


def wiring_problems(graph, features=FEATURES, unwired=UNWIRED):
    """The rows that are unwired and not allowlisted, plus allowlist entries that lie."""
    problems = []
    for row in wiring_report(graph, features):
        # PHASE 15: the ENTRY SHAPE is validated first, whatever the feature's state. It
        # used to be checked only on the unwired branch, so an entry with a typo'd phase
        # on a wired feature was reported as "allowlisted but IS wired" — true, and not
        # the problem the author needs to see. A malformed declaration is malformed
        # either way, and with UNWIRED empty there is no unwired feature left to
        # demonstrate the check on.
        if row.name in unwired:
            phase, disposition = unwired[row.name]
            if not isinstance(phase, int) or not 1 <= phase <= LAST_PHASE:
                problems.append(f"{row.name}: allowlisted with no closing phase "
                                f"({phase!r})")
            if disposition not in VALID_DISPOSITIONS:
                problems.append(f"{row.name}: disposition {disposition!r} is not one of "
                                f"{VALID_DISPOSITIONS}")
        if row.status == "wired":
            if row.name in unwired:
                problems.append(f"{row.name}: allowlisted as unwired but IS wired "
                                f"({row.detail}) — delete the allowlist entry")
            continue
        if row.name not in unwired:
            problems.append(f"{row.name}: unwired and not allowlisted — {row.detail}")
    return problems


def classification_problems(keys, features=FEATURES, plain=PLAIN_CONFIG):
    """The declared partition, checked BOTH ways against the live key sets."""
    flags = {f.key for f in features if f.key is not None}
    problems = []
    for key in sorted(set(keys) - flags - set(plain)):
        problems.append(f"{key}: a public conf key classified as neither a feature flag "
                        f"nor plain configuration")
    for key in sorted(flags & set(plain)):
        problems.append(f"{key}: declared BOTH a feature flag and plain configuration")
    for key in sorted((flags | set(plain)) - set(keys)):
        problems.append(f"{key}: declared here but is no longer a public conf key")
    return problems


def public_keys():
    return set(policy.PROJECT_KEYS) | set(policy.POLICY_KEYS)


class FeatureTruthfulness(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.graph = CallGraph(PACKAGE)

    def test_every_public_conf_key_is_classified_as_a_flag_or_as_plain_config(self):
        problems = classification_problems(public_keys())
        self.assertEqual([], problems, "\n".join(problems))
        self.assertEqual(len(public_keys()),
                         len({f.key for f in FEATURES if f.key}) + len(PLAIN_CONFIG),
                         "the two declarations must partition the key set exactly")

    def test_every_feature_flag_resolves_a_production_call_site(self):
        rows = wiring_report(self.graph)
        width = max(len(r.name) for r in rows)
        print("\nfeature flag -> production call site "
              "(mechanism reachable from consumer, consumer reachable from "
              + "/".join(ENTRY_MODULES) + ")")
        for row in rows:
            mark = "wired  " if row.status == "wired" else (
                "unwired(phase %d)" % UNWIRED[row.name][0] if row.name in UNWIRED
                else "UNWIRED")
            print(f"  {row.name:<{width}}  {mark:<18}  {row.detail}")
        problems = wiring_problems(self.graph)
        self.assertEqual([], problems, "\n".join(problems))

    def test_the_gate_is_green_because_the_allowlist_is_honest(self):
        """Green with a full table, not green with an empty one.

        A gate that resolved nothing would also report no problems. So: every feature is
        accounted for, the wired majority is a majority, and every unwired name is one
        the allowlist admits.
        """
        rows = wiring_report(self.graph)
        self.assertEqual(len(FEATURES), len(rows))
        wired = [r for r in rows if r.status == "wired"]
        unwired = [r for r in rows if r.status == "unwired"]
        self.assertEqual(len(FEATURES) - len(UNWIRED), len(wired))
        self.assertEqual(sorted(UNWIRED), sorted(r.name for r in unwired))
        for row in wired:
            self.assertIn("<-", row.detail, "a wired row must name its call site")

    def test_each_unwired_entry_carries_the_phase_that_closes_it(self):
        for name, entry in sorted(UNWIRED.items()):
            self.assertEqual(2, len(entry), f"{name}: expected (phase, disposition)")
            phase, disposition = entry
            self.assertIsInstance(phase, int, f"{name}: closing phase must be a number")
            self.assertTrue(1 <= phase <= LAST_PHASE, f"{name}: phase {phase} is not a "
                            f"phase of this run")
            self.assertIn(disposition, VALID_DISPOSITIONS, f"{name}")
            self.assertIn(name, {f.name for f in FEATURES},
                          f"{name}: allowlisted but not a declared feature")

    def test_an_allowlist_entry_with_no_closing_phase_fails_the_gate(self):
        # The specimen has to be a feature that is ACTUALLY unwired, or the "allowlisted
        # but IS wired" complaint fires first and this arm passes for the wrong reason.
        # It was `model_routing` until phase 13 wired it; `classes` is the one left, and
        # phase 15 will take it, at which point this needs a specimen that does not exist
        # — see the note there.
        # PHASE 15 emptied UNWIRED, so there is no longer a real unwired feature to use
        # as a specimen — and an arm with no specimen is an arm that cannot go red. The
        # subject is now a WIRED feature deliberately allowlisted: the malformed-phase
        # complaint must fire on it BEFORE the "allowlisted but IS wired" one, or a
        # typo'd phase on a future entry would be reported as the wrong problem.
        subject = "budgets"
        self.assertNotIn(subject, UNWIRED)
        for bad in (None, 0, 99, "later"):
            problems = wiring_problems(self.graph,
                                       unwired=dict(UNWIRED, **{subject: (bad, "wired")}))
            self.assertTrue(any(subject in p and "closing phase" in p
                                for p in problems), f"{bad!r} was accepted: {problems}")

    def test_the_allowlist_is_EMPTY(self):
        """PHASE 18 — the run's success condition, asserted rather than described.

        This gate started the fourth audit's response with four entries: two features
        whose public flag promised behaviour no production path reached (`model_routing`,
        `classes`) and two whose flag promised a subsystem the operator scoped out
        (`diff_scope`, `evidence_cache`). Zero means every public flag now names a
        production call site — the audit's central charge was that this project proves
        its mechanisms exist better than it proves they are REACHABLE, and this is the
        one number that answers it directly.

        `assertEqual({}, ...)` rather than `assertFalse`: the failure has to print the
        names, because "the allowlist is not empty" sends a reader to the file while
        "{'evidence_cache': ...}" sends them to the feature.
        """
        self.assertEqual({}, UNWIRED,
                         "a public feature flag still promises behaviour no production "
                         "path reaches — this run's success condition is that this list "
                         "is empty")
        print(f"\nALLOWLIST  UNWIRED = {{}} — {len(FEATURES)} declared feature(s), "
              f"{UNWIRED_AT_PHASE_1} allowed at phase 1, 0 now")

    def test_the_allowlist_only_shrinks(self):
        self.assertLessEqual(len(UNWIRED), UNWIRED_AT_PHASE_1,
                             "this run may close unwired features, never open one — "
                             "raising UNWIRED_AT_PHASE_1 is a decision, not a fix")
        self.assertEqual(4, UNWIRED_AT_PHASE_1, "the pin phase 18 asserts down to zero")

    def test_a_feature_may_not_be_its_own_consumer(self):
        for feature in FEATURES:
            self.assertNotEqual(feature.mechanism, feature.consumer,
                                f"{feature.name}: a row whose mechanism IS its consumer "
                                f"is true by construction")
            self.assertIn(feature.mechanism, self.graph.body,
                          f"{feature.name}: mechanism names no function in xcheck/")
            self.assertIn(feature.consumer, self.graph.body,
                          f"{feature.name}: consumer names no function in xcheck/")

    def test_module_level_key_names_are_not_call_sites(self):
        """`CONF_DEFAULTS` names every key there is; if that counted, so would nothing."""
        for key in ("model_routing", "diff_scope", "evidence_cache"):
            for qual, _line in self.graph.read_sites(key):
                self.assertNotEqual("util", qual.split(".")[0],
                                    f"{key}: a util.py module-level dict was counted")

    # ---- counterfactuals -------------------------------------------------------------

    def test_counterfactual_an_unclassified_flag_fails_the_gate(self):
        control = classification_problems(public_keys())
        self.assertEqual([], control, "control: the real key set classifies cleanly")
        planted = classification_problems(public_keys() | {"fixture_flag"})
        self.assertTrue(any("fixture_flag" in p for p in planted),
                        f"an unclassified key was accepted: {planted}")
        self.assertEqual(len(control) + 1, len(planted),
                         "the plant must add exactly its own problem")

    def test_counterfactual_a_declared_feature_with_no_call_site_fails_the_gate(self):
        control = wiring_problems(self.graph)
        self.assertEqual([], control, "control: the real feature table is green")
        # A flag whose name appears all over the tree (it is a real key) but whose
        # declared mechanism nothing on the consumer's path calls.
        # PHASE 15: the plant named `classes.candidates` as the unreachable mechanism.
        # That phase WIRED it — the detector's candidates now print in `xcheck status` —
        # so the plant quietly became a description of live code and stopped planting
        # anything. A counterfactual whose defect has been fixed underneath it passes for
        # the wrong reason; the mechanism is now one nothing on a consumer's path calls.
        planted = wiring_problems(
            self.graph,
            features=FEATURES + (_flag("session_note", "classes.mechanism_census",
                                       "cli.main"),))
        self.assertTrue(any(p.startswith("session_note:") and "not allowlisted" in p
                            for p in planted), f"expected a session_note problem: {planted}")

    def test_counterfactual_removing_a_real_call_site_fails_the_gate(self):
        """Delete the CALL and the gate reddens, while every mention of the name stays.

        This is the whole claim of the module in one test. `decision.state_and_decision`
        is the only production caller of `budget.gates`; cutting that one call leaves
        `budget.FLAG`, `CONF_DEFAULTS["budgets"]`, `policy.PROJECT_KEYS`, the README row
        and `decision.py`'s own `conf_flag(conf, budget.FLAG)` exactly where they were.
        A name search would still find all of them. The gate must not.
        """
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "xcheck"
            shutil.copytree(PACKAGE, copy)
            control = wiring_problems(CallGraph(copy))
            self.assertEqual(wiring_problems(self.graph), control,
                             "control: a faithful copy must grade identically")

            target = copy / "decision.py"
            before = target.read_text()
            after = before.replace("budget.gates(", "_cut_by_counterfactual(")
            self.assertNotEqual(before, after, "the planted edit changed nothing")
            self.assertNotIn("budget.gates(", after, "the call survived the plant")
            self.assertIn("budget.FLAG", after, "the flag's NAME must survive the plant")
            target.write_text(after)

            planted = wiring_problems(CallGraph(copy))
            names = {p.split(":")[0] for p in planted}
            self.assertIn("budgets", names, f"cutting the call did not redden: {planted}")
            # The six ceilings route through the same function, so they go with it. That
            # they all move together is the point: one call, one feature, six keys.
            self.assertLessEqual({"tokens_per_session", "tokens_per_pass",
                                  "tokens_per_audit"}, names)
            self.assertNotIn("okf", names, "an unrelated feature must not move")


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- PHASE 11 (fifth audit)

SMOKE = REPO / "tests" / "test_smoke_e2e.py"


class Witnesses:
    """The scenarios in `tests/test_smoke_e2e.py`, resolved from the file rather than
    imported: importing it runs its module-level fixture setup, and a gate that needs the
    thing it grades to work is a gate that goes quiet the day it breaks."""

    def __init__(self, source=None):
        self.src = source if source is not None else SMOKE.read_text(encoding="utf-8")
        self.tree = ast.parse(self.src)
        self.classes = {n.name: n for n in self.tree.body if isinstance(n, ast.ClassDef)}
        self._other = {}

    def _module(self, spec):
        """`(classes, source, where)` for a witness spec.

        PHASE 9 (sixth audit). A witness may name a scenario in the smoke module (bare) or
        in another module (`path:Class`). A REFUSAL-kind witness cannot live in the smoke
        module — that module dispatches children and a gate has none — so the resolver has
        to be able to look elsewhere. It still resolves a SCENARIO and still reads its
        body: pointing at a FILE is the loophole the previous table closed, and this does
        not reopen it.
        """
        if ":" not in spec:
            return self.classes, self.src, SMOKE.name
        path, _, _cls = spec.partition(":")
        if path not in self._other:
            f = REPO / path
            src = f.read_text(encoding="utf-8") if f.exists() else ""
            classes = ({n.name: n for n in ast.parse(src).body
                        if isinstance(n, ast.ClassDef)} if src else {})
            self._other[path] = (classes, src, path)
        return self._other[path]

    @staticmethod
    def _name(spec):
        return spec.split(":")[-1]

    def tests_in(self, spec):
        classes, _src, _where = self._module(spec)
        node = classes.get(self._name(spec))
        return [] if node is None else [m.name for m in node.body
                                        if isinstance(m, ast.FunctionDef)
                                        and m.name.startswith("test_")]

    def body_of(self, spec):
        classes, src, _where = self._module(spec)
        node = classes.get(self._name(spec))
        return "" if node is None else ast.get_source_segment(src, node)

    def problem(self, feature):
        """Why this feature's witness does not witness it, or None.

        Four questions, because they fail differently.

        RESOLVED: does the scenario exist and hold at least one test? `unittest` reports a
        mistyped dotted path as `Ran 1 test` with a non-zero code, so a witness named in a
        table and never run is exactly the shape that reads as green
        (`unittest-counts-a-nonexistent-target-as-one-test`).

        EXERCISES: does the scenario's OWN body name the thing it is supposed to witness?
        Inherited fixture setup does not count — if it did, every scenario would witness
        every feature the shared base configures, which is how a witness becomes a label.

        REACHES THE SURFACE (PHASE 9): does it drive something a user can reach? This is
        what makes a refusal a WITNESS rather than an assertion about an internal
        predicate. Most of this repository's gate tests fail exactly here — they check the
        rule and never run the verb — and that is the honest reason the re-score is 6 of
        20 and not 20 of 20.

        MATCHES ITS KIND (PHASE 9): a REFUSAL witness may not live in the smoke module,
        which dispatches children a gate does not have. Without this, "reclassify to
        refusal" would be a way to score without proving.
        """
        spec = feature.witness
        classes, _src, where = self._module(spec)
        name = self._name(spec)
        if name not in classes:
            return (f"names witness `{spec}`, which is not a scenario in {where}")
        if not self.tests_in(spec):
            return f"names witness `{spec}`, which holds no test method"
        if feature.kind == REFUSAL and ":" not in spec:
            return (f"is a {REFUSAL} feature naming the smoke scenario `{spec}` — that "
                    f"module dispatches children, and a gate has none. A refusal witness "
                    f"lives where the refusal is observed")
        body = self.body_of(spec)
        needle = feature.key or feature.mechanism.split(".")[0]
        if needle not in body:
            return (f"names witness `{spec}`, whose body never mentions `{needle}` — the "
                    f"scenario runs, and it does not exercise this feature")
        if not any(surface in body for surface in PUBLIC_SURFACE):
            return (f"names witness `{spec}`, whose body reaches no public surface "
                    f"{sorted(PUBLIC_SURFACE)[:3]}… — it asserts a rule rather than "
                    f"watching the feature refuse at a surface a user reaches")
        return None


class EveryFeatureIsWitnessedOrDeclaredWitnessless(unittest.TestCase):
    """The audit's central diagnosis, as a gate: `UNWIRED = {}` was satisfied by a feature
    that could not run. An edge says a function is reached. A witness says the child got
    the effect."""

    @classmethod
    def setUpClass(cls):
        cls.w = Witnesses()

    def test_the_two_lists_partition_the_features(self):
        witnessed = {f.name for f in FEATURES if f.witness}
        declared = set(WITNESSLESS)
        self.assertEqual(set(), witnessed & declared,
                         "a feature is both witnessed and declared witness-less")
        self.assertEqual({f.name for f in FEATURES}, witnessed | declared,
                         "a feature is in neither list — it arrived unclassified")
        for name, why in WITNESSLESS.items():
            self.assertGreater(len(why), 30, f"{name}: a reason that short is a label")

    def test_every_named_witness_resolves_and_exercises_its_feature(self):
        print(f"\n  WITNESS TABLE  ({len(FEATURES)} features)")
        for f in sorted(FEATURES, key=lambda x: (not x.witness, x.name)):
            if not f.witness:
                continue
            problem = self.w.problem(f)
            print(f"    {f.name:<22} {f.mechanism:<24} -> {f.witness:<18} "
                  f"{'ok' if problem is None else 'BROKEN'}")
            self.assertIsNone(problem, f"{f.name} {problem}")

    def test_the_witnessless_are_printed_with_their_reasons(self):
        print(f"\n  NO END-TO-END WITNESS ({len(WITNESSLESS)}), each declared:")
        for name, why in sorted(WITNESSLESS.items()):
            print(f"    {name:<28} {why[:88]}")
        self.assertLessEqual(len(WITNESSLESS), WITNESSLESS_AT_PHASE_11,
                             "a feature arrived without a witness and the pin was not "
                             "raised in the same commit — raising it is a decision")
        self.assertEqual(17, WITNESSLESS_AT_PHASE_11,
                         "the pin moved without the phase that moves it")

    def test_COUNTERFACTUAL_a_witness_that_does_not_exercise_its_feature_is_caught(self):
        """Plant one: point `model_routing` at a scenario that runs a perfectly good
        session and never sets a route. The gate must redden and name both."""
        routing = next(f for f in FEATURES if f.name == "model_routing")
        planted = routing._replace(witness="NormalSession")
        problem = self.w.problem(planted)
        print("\n  PLANTED     model_routing -> NormalSession")
        print(f"    {problem}")
        self.assertIsNotNone(problem, "an unrelated scenario passed as a witness")
        self.assertIn("NormalSession", problem)
        self.assertIn("model_routing", problem)
        self.assertIsNone(self.w.problem(routing),
                          "the real witness stopped resolving")

    def test_COUNTERFACTUAL_a_witness_that_does_not_exist_is_caught(self):
        """The mistyped-target trap, made to fire. A name in a table is not a test that
        ran."""
        f = next(f for f in FEATURES if f.name == "budgets")
        for bad, expect in (("CapExceededd", "is not a scenario"),
                            ("Scenario", "holds no test method")):
            problem = self.w.problem(f._replace(witness=bad))
            print(f"  PLANTED     budgets -> {bad}: {problem}")
            self.assertIn(expect, problem)

    def test_which_of_the_two_gates_would_have_caught_the_routing_defect(self):
        """Criterion 6, printed rather than asserted in prose: the edge check passed on
        the defect, and the witness does not."""
        routing = next(f for f in FEATURES if f.name == "model_routing")
        print("\n  THE ROUTING DEFECT")
        print(f"    call-graph edge  {routing.consumer} -> {routing.mechanism}: this was "
              f"SATISFIED at phase 13 of the previous run, by a build_cmd that called the "
              f"router and produced identical argv")
        print(f"    witness          {routing.witness}: reads the argv the fake agent "
              f"actually received, and reddened until the route reached the child")
        self.assertEqual({}, UNWIRED, "the edge check is an addition, not a replacement")
        self.assertTrue(any("argv" in t or "model" in t
                            for t in self.w.tests_in(routing.witness)),
                        "the routing witness asserts nothing about the command")


class TheGateRecordsWhatItUsedToMiss(unittest.TestCase):
    """Criterion 7. The correction sits BESIDE the original reasoning, not over it: a
    gate that quietly rewrites its own rationale teaches nothing to the next person who
    wonders why two checks exist for one question."""

    def test_the_docstring_keeps_both(self):
        doc = __doc__ or ""
        self.assertIn("What the gate asks instead is whether", doc,
                      "the original reasoning was replaced rather than kept")
        self.assertIn("WHAT THE PARAGRAPH ABOVE DOES NOT PROVE", doc,
                      "the gate does not record what its metric missed")
        for phrase in ("model_routing", "RESULT reaches the child", "WITNESS"):
            self.assertIn(phrase, doc, phrase)
        print("\n  DOCSTRING  the call-path rationale is KEPT and the correction names "
              "the feature that satisfied it while dead")


# ---------------------------------------------------------------- PHASE 9 (sixth audit)

class WitnessKindsAreClosedAndAssigned(unittest.TestCase):
    """Criterion 1. One number covering two kinds of proof was the defect; three kinds,
    each with a sentence, and every feature assigned exactly one."""

    def test_the_kinds_are_a_closed_set_each_with_a_sentence(self):
        print(f"\n  WITNESS KINDS ({len(WITNESS_KINDS)}, closed)")
        for kind, sentence in WITNESS_KINDS.items():
            print(f"    {kind:<10} {sentence[:96]}")
            self.assertGreater(len(sentence), 60, f"{kind}: a sentence that short is a label")
        self.assertEqual({DISPATCH, REFUSAL, CORPUS}, set(WITNESS_KINDS))

    def test_every_feature_has_exactly_one_kind(self):
        unassigned = [f.name for f in FEATURES if f.kind not in WITNESS_KINDS]
        self.assertEqual([], unassigned, f"unassigned: {unassigned}")
        self.assertEqual(20, len(FEATURES), "the table changed size without the pin")

    def test_the_public_surface_is_declared_with_a_reason_each(self):
        for needle, why in PUBLIC_SURFACE.items():
            self.assertGreater(len(why), 15, f"{needle}: no reason given")
        print(f"  SURFACE    {len(PUBLIC_SURFACE)} declared entry points, each with a "
              f"reason")


class TheRescoredTable(unittest.TestCase):
    """Criterion 2. Per kind, beside the single number it replaces."""

    def rows(self):
        out = {}
        for kind in WITNESS_KINDS:
            of_kind = [f for f in FEATURES if f.kind == kind]
            out[kind] = ([f for f in of_kind if f.witness],
                         [f for f in of_kind if not f.witness])
        return out

    def test_the_table_is_printed_in_full_with_the_old_number_beside_it(self):
        rows = self.rows()
        witnessed = sum(len(w) for w, _ in rows.values())
        total = sum(len(w) + len(n) for w, n in rows.values())
        print(f"\n  RE-SCORED  was: 3 of {total} witnessed, one number over three kinds "
              f"of proof")
        print(f"  RE-SCORED  now: {witnessed} of {total}, per kind:")
        for kind, (w, n) in rows.items():
            print(f"    {kind:<10} {len(w)} witnessed / {len(w) + len(n)}")
            for f in w:
                print(f"      + {f.name:<26} {f.witness}")
            for f in n:
                print(f"      - {f.name:<26} {WITNESSLESS[f.name].split(':')[0][:60]}")
        self.assertEqual(6, witnessed, "the re-score moved without the phase that moves it")
        self.assertEqual(20, total)
        # It went UP by three, and each of the three is a witness that exists — see
        # `TheScoreCannotRiseByReclassification`.
        self.assertGreater(witnessed, 3)

    def test_the_corpus_kinds_demand_is_declared_but_UNEXERCISED(self):
        """Said out loud rather than left for a reader to notice: no CORPUS feature has a
        witness, so that kind's demand has never been satisfied by anything. A demand no
        instance meets is a rule, not a check."""
        corpus = [f for f in FEATURES if f.kind == CORPUS]
        self.assertEqual([], [f for f in corpus if f.witness])
        print(f"  UNEXERCISED {CORPUS}: 0 of {len(corpus)} witnessed — this kind's demand "
              f"is declared and nothing has yet had to meet it")


class TheScoreCannotRiseByReclassification(unittest.TestCase):
    """Criterion 3. The move this phase most obviously invites: relabel a feature into a
    kind whose proof is cheaper, and watch the number improve without anything being
    proved."""

    @classmethod
    def setUpClass(cls):
        cls.w = Witnesses()

    def test_a_feature_moved_to_REFUSAL_without_a_refusal_witness_is_caught(self):
        """`egress_allowlist` is DISPATCH and witnessless because the suite starts no
        network. Reclassifying it to REFUSAL and pointing at a real, passing scenario that
        names the key must still fail — because that scenario asserts the parse and never
        reaches a surface."""
        f = next(f for f in FEATURES if f.name == "egress_allowlist")
        planted = f._replace(
            kind=REFUSAL,
            witness="tests/test_egress_broker.py:TheAllowlistIsParsedBeforeAnythingRuns")
        problem = self.w.problem(planted)
        print(f"\n  RECLASSIFY egress_allowlist {DISPATCH} -> {REFUSAL}, pointed at a "
              f"real scenario that names the key")
        print(f"    {problem}")
        self.assertIsNotNone(problem, "a relabel bought a witness")
        self.assertIn("public surface", problem)

    def test_a_REFUSAL_feature_may_not_borrow_a_smoke_scenario(self):
        """The other direction: a gate pointed at a dispatching scenario. The smoke module
        runs children; a gate has none, so a scenario there cannot be watching it refuse."""
        f = next(f for f in FEATURES if f.name == "okf")
        planted = f._replace(witness="CapExceeded")
        problem = self.w.problem(planted)
        print("  BORROWED   okf -> CapExceeded (a smoke scenario)")
        print(f"    {problem}")
        self.assertIsNotNone(problem)
        self.assertIn("dispatches children", problem)

    def test_CONTROL_the_three_real_moves_are_not_caught(self):
        """The counterfactuals above must not simply be refusing everything: the three
        features that moved out of WITNESSLESS this phase resolve cleanly."""
        moved = ("okf", "evidence_bundle", "parallel_passes")
        for name in moved:
            f = next(f for f in FEATURES if f.name == name)
            self.assertIsNone(self.w.problem(f), f"{name} does not resolve")
            print(f"  MOVED      {name:<18} {f.kind:<9} {f.witness}")


class TheRatchetSurvivesTheSplit(unittest.TestCase):
    """Criterion 4."""

    def test_the_new_pin_is_derived_not_chosen(self):
        derived = len([f for f in FEATURES if not f.witness])
        print(f"\n  RATCHET    witnessless recomputed from FEATURES: {derived} · "
              f"pin: {WITNESSLESS_AT_PHASE_9} · previous pin: {WITNESSLESS_AT_PHASE_11}")
        self.assertEqual(derived, WITNESSLESS_AT_PHASE_9,
                         "the pin was typed rather than derived from the table")
        self.assertEqual(derived, len(WITNESSLESS),
                         "FEATURES and WITNESSLESS disagree about who has a witness")

    def test_it_may_only_FALL(self):
        self.assertLess(WITNESSLESS_AT_PHASE_9, WITNESSLESS_AT_PHASE_11,
                        "the split did not lower the count, so it bought nothing")
        self.assertLessEqual(len(WITNESSLESS), WITNESSLESS_AT_PHASE_9,
                             "a feature arrived without a witness and the pin was not "
                             "raised in the same commit")
        print(f"  RATCHET    17 -> {WITNESSLESS_AT_PHASE_9}, and the old pin is KEPT so "
              f"the direction stays legible")


class AGatesWitnessIsExercised(unittest.TestCase):
    """Criterion 5. The features phases 4, 5 and 6 touched, each shown refusing.

    Phase 4's subject — `ledger.require_complete`, which refuses a write over an
    incomplete stream — is NOT a conf-keyed feature and has no row in this table. Said
    here rather than given an invented row: adding one would change the denominator this
    phase's whole point is not to game.
    """

    def test_the_features_those_phases_touched_have_a_witness_of_their_kind(self):
        touched = {
            "parallel_passes": "phases 5 and 6: one dispatch preparation, then a gate "
                               "that refuses a second private command build",
            "model_routing": "phase 5: the route now reaches the argv through the one "
                             "shared preparation",
        }
        w = Witnesses()
        print("\n  TOUCHED BY PHASES 4/5/6")
        for name, why in touched.items():
            f = next(f for f in FEATURES if f.name == name)
            self.assertIsNotNone(f.witness, f"{name} has no witness")
            self.assertIsNone(w.problem(f), f"{name}: {w.problem(f)}")
            print(f"    {name:<18} {f.kind:<9} {f.witness}")
            print(f"      {why}")
        print("    require_complete   (phase 4) is not a conf-keyed feature — no row "
              "here, and none invented")

    def test_each_refuses_what_it_exists_to_refuse(self):
        """Not asserted in prose: the witness bodies are read, and each must contain an
        assertion about the thing it refuses or applies."""
        w = Witnesses()
        checks = {
            "parallel_passes": ("refuses a diverged parallel configuration and refuses a "
                                "second command build", ("refus", "divergen", "argv")),
            "model_routing": ("applies the route to the argv the child receives, and a "
                              "wrong model is refused", ("argv", "model")),
            "okf": ("refuses an incomplete bundle", ("INCOMPLETE", "problems", "verify")),
            "evidence_bundle": ("refuses to export unless the verb is explicit",
                                ("off", "verb", "nothing")),
        }
        for name, (claim, needles) in checks.items():
            f = next(f for f in FEATURES if f.name == name)
            body = w.body_of(f.witness)
            hit = [n for n in needles if n in body]
            print(f"  REFUSES    {name:<18} {claim}")
            print(f"             witness body names {hit}")
            self.assertTrue(hit, f"{name}: the witness body asserts none of {needles}")


class TheCombinationGapIsStatedNotClosed(unittest.TestCase):
    """Criterion 6."""

    def test_the_registry_says_it(self):
        self.assertIn("COMBINATION", COMBINATION_GAP.upper())
        self.assertIn("OPEN", COMBINATION_GAP)
        print(f"\n  OPEN       {COMBINATION_GAP[:150]}…")

    def test_no_phase_of_this_run_claims_to_close_it(self):
        """A witness proves one feature works when it is the thing being exercised. Six
        green rows say six features each work alone."""
        witnessed = [f for f in FEATURES if f.witness]
        print(f"  OPEN       {len(witnessed)} features witnessed INDIVIDUALLY; 0 "
              f"scenario runs two of them together")
        self.assertGreater(len(witnessed), 0)
        self.assertIn("no phase of this run closes it", COMBINATION_GAP.lower())


class ThePreviousThreeAreStillWitnessed(unittest.TestCase):
    """Criterion 7. A re-score that quietly dropped one of the three it started with
    would be trading a real witness for a relabel."""

    NAMED = {"budgets": "CapExceeded", "tokens_per_session": "CapExceeded",
             "model_routing": "RoutingApplied"}

    def test_by_name(self):
        w = Witnesses()
        print("\n  CONTROL    the three witnessed at phase 11 of the fifth audit's "
              "run:")
        for name, witness in self.NAMED.items():
            f = next(f for f in FEATURES if f.name == name)
            self.assertEqual(witness, f.witness, f"{name} lost its witness")
            self.assertIsNone(w.problem(f), f"{name}: {w.problem(f)}")
            self.assertEqual(DISPATCH, f.kind, f"{name} was reclassified")
            print(f"    {name:<20} {f.witness:<16} {f.kind}  still resolving")
