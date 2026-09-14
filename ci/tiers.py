#!/usr/bin/env python3
"""The check tiers, as DATA, so every invocation derives from one definition.

A six-minute gate on every change teaches people to skip the gate. So there is a fast
tier — and the whole design problem is that a fast tier is also the obvious way to skip
the heavy proofs. Three things stop it becoming that:

1. **Every module is named exactly once.** `PR` and `RELEASE_ONLY` are explicit tuples,
   and their union is checked against the test modules ON DISK, both directions. A module
   in neither tier is a module nobody runs, and a tier naming a module that does not exist
   is worse than useless: `unittest` reports `Ran 1 test / OK` for a mistyped dotted path,
   so a tier that had drifted would read as a passing tier.
2. **The release tier is a superset by construction.** `RELEASE = PR + RELEASE_ONLY`, not
   a second hand-maintained list that could quietly lose a module the PR tier also lost.
3. **The PR tier cannot stand in for a release.** `ci/release-gate.sh` refuses to run with
   `XCHECK_TIER=pr` in the environment. The fast tier is not a smaller release; it is a
   different question, and the gate says so rather than trusting the caller.

Split by MEASURED cost, not by guesswork: the sweep behind these lists timed every module
individually. The PR tier holds the state, write, policy and CLI batteries plus the short
mutation controls; what is release-only is what starts containers, builds venvs, spawns
interpreters or walks the whole shipped corpus.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "tests"

# The fast tier. Compile/lint and audit lint are STEPS (below); these are the batteries.
PR = (
    # state: the schema, the reader, the writer, and the freeze
    "test_state", "test_state_consumers", "test_state_immutability",
    # write: the single authorization point and the verbs that go through it
    "test_write_authorization", "test_write_verbs", "test_write_under_a_held_lock",
    "test_admin_verbs",
    # policy: what the operator owns, where it may live, and the trust classification
    "test_conf_policy", "test_policy_location", "test_trust_level",
    # the four shared-boundary gates: canonical state, transaction identity, the journal
    # door and the dispatch preparation. AST-only and ~0.4s, and on the PR tier rather
    # than the release tier on purpose — a gate that runs late is a gate that catches late
    "test_shared_boundaries",
    "test_live_corpus_gate",
    # the end-to-end smoke set: ten scenarios through the public entry point with a
    # real child process, 8s. The one battery that reads what a CHILD received rather
    # than what a builder returned, which is the class of defect the fourth audit shipped
    "test_smoke_e2e",
    # the command surface a change is most likely to break
    "test_cli_grammar", "test_run_result", "test_envelope", "test_receipt",
    "test_lock_seed",
    # the recovery drill: cheap, and the thing that proves the ledger claims
    "test_ledger_recovery",
    # the telemetry seam: ten fields, two adapters, and the structural no-leak check
    "test_provider_adapter",
    # the reconciler: read-only by construction, and asserted so
    "test_reconcile",
    # the preflight: ten refusals, ten counterfactuals and an all-green control, 2.6s
    "test_doctor",
    # the short mutation controls — the ones that plant a defect and read a red, without
    # starting a container or building a venv
    "test_mutations", "test_no_bypass", "test_consumer_paths",
    # the tier check itself: a guard that only the release tier ran would
    # let a PR add an unassigned module and find out days later
    "test_check_tiers",
    # the feature-truthfulness gate: a pure AST read of the shipped package, so it
    # costs seconds and answers the question a PR is most likely to get wrong —
    # shipping a public flag over library code nothing calls
    "test_feature_truthfulness",
    # the benchmark refusals: foreign code under a container, and a metric
    # that is `unmeasured` rather than 0. Cheap, and the kind of guard that
    # must not first fail on release day
    "test_benchmark_harness",
)

# Everything else. Named, not "the rest of the directory": a module that fell out of both
# lists must fail the check rather than land here by default.
RELEASE_ONLY = (
    "test_adversarial_repo", "test_backpressure", "test_budgets", "test_ci_contract",
    "test_concurrency", "test_container_profile", "test_context_capsule",
    "test_corpus_baseline",
    "test_courier_paths", "test_crash_recovery", "test_dashboard", "test_diff_scope",
    "test_doc_contract", "test_doc_drift", "test_egress_broker", "test_embargo",
    "test_evidence_bundle", "test_evidence_cache", "test_executable_contract",
    "test_fixture_isolation", "test_hardening", "test_human_style", "test_image_pinning",
    "test_injection_surface", "test_launch_surfaces", "test_legacy_selftest",
    "test_metrics", "test_migrate", "test_model_routing", "test_no_progress", "test_okf",
    "test_parallel_limits", "test_parallel_preservation",
    "test_parallel_dispatch", "test_parallel_retry", "test_parallel_state_race",
    "test_preprocess", "test_provenance", "test_quarantine", "test_recurring_classes",
    "test_release_lifecycle", "test_retention", "test_retry_effects",
    "test_runner_sandbox", "test_style_claim_is_narrow", "test_style_delivery",
    "test_style_does_not_widen_a_gate", "test_style_source", "test_telemetry",
    "test_tokens", "test_transaction_identity", "test_upgrade", "test_version_parity",
    "test_views", "test_watchdog", "test_write_path",
)

RELEASE = PR + RELEASE_ONLY

# PHASE 17 (fourth audit). The tag tier: the modules whose SUBJECT is the artifact rather
# than the code inside it — what a version means, what the wheel contains, whether the
# installed copy runs, whether the docs agree with the thing being shipped. A subset of
# RELEASE by construction, asserted, because a tag tier that could name a module the full
# suite does not would be a second definition of what a release is.
PACKAGE = (
    "test_release_lifecycle", "test_version_parity", "test_executable_contract",
    "test_doc_contract", "test_ci_contract",
)

# The nightly tier IS the full suite. What makes it nightly is the MATRIX — every
# supported OS by every supported Python — and a matrix is a property of the workflow,
# not of a module list. A second tuple here would be a copy of RELEASE that could lose a
# module RELEASE kept.
NIGHTLY = RELEASE

# The steps each tier runs, in order. The shell asks this module which ones apply rather
# than carrying a second copy of the answer in an `if`.
STEPS = (
    ("preflight", ("pr", "release", "package", "nightly")),
    ("ruff", ("pr", "release", "package", "nightly")),
    ("check-split", ("pr", "release", "package", "nightly")),
    ("artifact", ("release", "package", "nightly")),
    ("audit-lint", ("pr", "release", "package", "nightly")),
    ("battery", ("pr", "release", "package", "nightly")),
    ("selftest", ("release", "package", "nightly")),
    ("release-gate", ("release", "package", "nightly")),
)

TIERS = {"pr": PR, "release": RELEASE, "package": PACKAGE, "nightly": NIGHTLY}

# PHASE 17: which tier each GitHub EVENT runs, and why. The workflow derives its
# `--tier=` arguments from this table; `tests/test_check_tiers.py` asserts that every
# tier here is named by the workflow and that the workflow names no tier that is not
# here, in both directions — a tier nothing triggers is the same defect class as a
# public flag with no call site, which is what phase 1 of this response was about.
#
# The audit's complaint was the opposite shape: six matrix cells each ran the WHOLE
# suite on every pull request, so the tiers that existed changed nothing about the only
# gate that is supposed to be slow.
EVENTS = (
    ("pull_request", "pr", "one cell",
     "the fast consumer and contract battery: state, write, policy, the CLI surface, "
     "the short mutation controls and the structural gates"),
    ("push:main", "release", "one cell",
     "the full suite, including the adversarial battery and the docker escape matrix"),
    ("push:tags", "package", "one cell",
     "the package gate: the artifact builds, installs and runs, and says what version "
     "it is"),
    ("schedule|workflow_dispatch", "nightly", "matrix",
     "the full suite across every supported OS by every supported Python — the two "
     "variables a laptop cannot vary"),
)


def event_tiers():
    """The tiers some event actually triggers."""
    return tuple(dict.fromkeys(tier for _event, tier, _scope, _why in EVENTS))


def coverage_problems():
    """Why the EVENT tiers cover less than the tiers do, as a list of sentences.

    [[widening-a-gate-is-how-you-narrow-one]]: three new surfaces once dropped the one
    the old gate had. Tiering by event is exactly that shape of change — it adds three
    triggers and could silently remove the only one that ran a module. So coverage is
    checked over the EVENTS table rather than over `TIERS`: a tier no event names runs
    nowhere, and a module in that tier alone is a proof that has quietly stopped.
    """
    reachable = set()
    for tier in event_tiers():
        reachable.update(TIERS[tier])
    out = []
    for module in sorted(on_disk() - reachable):
        out.append(f"tests/{module}.py is in no tier that any EVENT runs — it is "
                   f"assigned, and nothing triggers it")
    for step, tiers in STEPS:
        if not set(tiers) & set(event_tiers()):
            out.append(f"the {step!r} step runs in no tier that any EVENT runs")
    for tier in TIERS:
        if tier not in event_tiers():
            out.append(f"tier {tier!r} is orphaned: no event runs it")
    extra = set(PACKAGE) - set(RELEASE)
    for module in sorted(extra):
        out.append(f"the package tier names {module}, which the full suite does not run")
    return out
TIER_ENV = "XCHECK_TIER"
DELEGATED_ENV = "XCHECK_GATE_DELEGATED"


def on_disk():
    """The test modules that exist, from the filesystem — never from a list."""
    return {p.stem for p in TESTS.glob("test_*.py")}


def unassigned():
    """(modules in no tier, modules named by a tier that do not exist)."""
    named = set(PR) | set(RELEASE_ONLY)
    disk = on_disk()
    return sorted(disk - named), sorted(named - disk)


def duplicates():
    """A module in BOTH lists: `RELEASE` would then run it twice and the union check
    would still pass, so it is caught by name rather than by set arithmetic."""
    return sorted(set(PR) & set(RELEASE_ONLY))


def modules(tier):
    return TIERS[tier]


def targets(tier):
    return [f"tests.{m}" for m in modules(tier)]


def has_step(tier, step):
    for name, tiers in STEPS:
        if name == step:
            return tier in tiers
    raise KeyError(f"no such step: {step}")


def changed_modules(base="HEAD"):
    """Test modules a change touches: the test files themselves, plus the test module
    named after each changed `xcheck/<mod>.py`. Advisory — the PR tier runs its whole
    battery regardless, because a tier that shrank to the diff would let a change with no
    test of its own run nothing at all."""
    try:
        out = subprocess.run(["git", "diff", "--name-only", base], cwd=REPO,
                             capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    hits = set()
    disk = on_disk()
    for path in out.split():
        p = Path(path)
        if p.parent.name == "tests" and p.stem in disk:
            hits.add(p.stem)
        elif p.parent.name == "xcheck" and p.suffix == ".py":
            hits.update(m for m in disk if m == f"test_{p.stem}"
                        or m.startswith(f"test_{p.stem}_"))
    return sorted(hits)


def main(argv):
    if len(argv) >= 3 and argv[1] == "--targets":
        print(" ".join(targets(argv[2])))
        return 0
    if len(argv) >= 4 and argv[1] == "--has-step":
        return 0 if has_step(argv[2], argv[3]) else 1
    if len(argv) >= 2 and argv[1] == "--events":
        for event, tier, scope, why in EVENTS:
            print(f"{event:<28} --tier={tier:<8} {scope:<7} "
                  f"{len(TIERS[tier]):>2} module(s)  {why}")
        return 0
    if len(argv) >= 2 and argv[1] == "--check":
        missing, phantom = unassigned()
        dupes = duplicates()
        for m in missing:
            print(f"FAIL: tests/{m}.py is in NO tier — nothing runs it")
        for m in phantom:
            print(f"FAIL: a tier names tests/{m}.py, which does not exist "
                  f"(unittest reports `Ran 1 test / OK` for a name like this)")
        for m in dupes:
            print(f"FAIL: {m} is in both PR and RELEASE_ONLY")
        coverage = coverage_problems()
        for line in coverage:
            print(f"FAIL: {line}")
        if missing or phantom or dupes or coverage:
            return 1
        print(f"tiers: pr {len(PR)} modules, release {len(RELEASE)} modules, "
              f"package {len(PACKAGE)}, nightly {len(NIGHTLY)}, "
              f"{len(on_disk())} on disk, every one assigned exactly once and every "
              f"one reachable from an event")
        return 0
    print(f"usage: {argv[0]} (--targets TIER | --has-step TIER STEP | --check "
          f"| --events)",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
