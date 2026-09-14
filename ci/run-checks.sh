#!/usr/bin/env bash
# The single entry point for every check CI runs, so CI is reproducible locally
# by one command. Order is cheapest-first: a syntax error should not wait behind
# a 7-second selftest.
#
# FOUR TIERS, one definition. `ci/tiers.py` holds which modules and which steps belong to
# which tier, and which EVENT runs which tier; this script asks it rather than carrying a
# second copy of the answer. The fast tier exists because a six-minute gate on every
# change teaches people to skip the gate — and the obvious failure of a fast tier is that
# it becomes the way the heavy proofs get skipped, so:
#
#   * `--tier=pr` never runs the release gate, and exports XCHECK_TIER=pr, which
#     `ci/release-gate.sh` REFUSES. The fast tier cannot stand in for a release even if
#     someone calls the gate by hand from inside it.
#   * the default is the full tier. A caller who types nothing gets everything.
#   * `--tier=package` (a tag) and `--tier=nightly` (the scheduled matrix) both run the
#     gate: they are releases, or rehearsals of one. Only `pr` is fast.
set -euo pipefail

cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"

TIER="release"
for arg in "$@"; do
    case "$arg" in
        --tier=pr|--tier=release|--tier=package|--tier=nightly) TIER="${arg#--tier=}" ;;
        *) echo "usage: $0 [--tier=pr|--tier=release|--tier=package|--tier=nightly]" >&2
           exit 2 ;;
    esac
done
export XCHECK_TIER="$TIER"
step() { "$PY" ci/tiers.py --has-step "$TIER" "$1"; }

echo "== tier: $TIER =="
"$PY" ci/tiers.py --check
CHANGED="$("$PY" ci/tiers.py --targets "$TIER" | wc -w | tr -d ' ')"
echo "battery: $CHANGED module(s) in this tier"

echo
echo "== ci preflight: the toolchain this script needs =="
# FIRST, because the alternative is what the 0.9.0 audit reproduced: the run gets all
# the way to the release gate — past the battery, past the selftest — and dies there on
# a missing `build`. One second here, with the install command in the message.
"$PY" ci/preflight.py

echo
echo "== ruff (E9,F: syntax errors and undefined names) =="
# Deliberately narrow. bin/xcheck is 11.6k lines and will not pass a full
# ruleset today; widening the gate now would force a cosmetic sweep that hides
# the real work. Phase 10 widens it against the split package, where it is cheap.
if command -v ruff >/dev/null 2>&1; then
    ruff check --select E9,F bin/xcheck xcheck/ tests/ ci/
else
    echo "ruff not installed — falling back to a compile check"
    "$PY" -m py_compile bin/xcheck xcheck/*.py tests/*.py ci/*.py
fi

echo
echo "== package is a move of the pre-split orchestrator, not a rewrite =="
"$PY" ci/check-split.py

if step artifact; then
    echo
    echo "== release artefact is clean =="
    # Here rather than on release day: a cleanliness rule nobody runs until release day is
    # a rule that fails on release day. This project has committed scratch artefacts twice.
    "$PY" ci/check-artifact.py
fi

echo
echo "== shipped audit corpus is internally consistent =="
# The audit's finding: a GREEN software CI beside a RED audit corpus. `xcheck lint`
# reported 20 orphan finding files for the whole 0.9.1 line and nothing in CI ran it, so
# the release gate could pass on a repository that contradicted itself — and the corpus
# is shipped, in the sdist and in the wheel's source tree.
#
# Placed HERE, above the build, for the same reason `check-artifact.py` is: a red corpus
# must stop a release while there is still a release to stop. Below the build it would
# only ever report on a wheel that had already been produced.
#
# The corpus is THIS repository's. The exported tree ships the package and its tests
# and no `audit/` at all — `install.sh` creates one — so there is nothing here to be
# consistent or inconsistent, and `xcheck lint` correctly says "install xcheck first".
# That is the right answer to a user and the wrong exit code for a gate: a check whose
# subject is absent says so and steps aside. Same decision, same wording, as
# `ci/check-split.py` makes about a commit the clone does not carry.
if [ -f audit/state.json ]; then
    "$PY" -m xcheck lint
else
    echo "SKIPPED: this checkout has no audit/state.json, so there is no corpus to lint."
    echo "         This is the exported tree, which ships the package and its tests but"
    echo "         not this repository's own audit history; run install.sh to start one,"
    echo "         or run this check in the development repository."
fi

echo
echo "== external test battery ($TIER tier) =="
if [ "$TIER" = "release" ] || [ "$TIER" = "nightly" ]; then
    # Discovery, not the list: the release tier must run what is ON DISK. If the list and
    # the directory ever disagree, `--check` above has already failed — but a release that
    # ran a stale list rather than the directory would be the worse failure of the two.
    #
    # The gate is DELEGATED to the final step below for this interpreter, so
    # `test_ci_contract`'s per-Python matrix does not build and run it a second time. That
    # matrix ran it once per available interpreter (3.10 and 3.11 here) on top of the
    # step at the end of this script: three runs of a 24-second gate to answer two
    # questions.
    export XCHECK_GATE_DELEGATED="$("$PY" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
    "$PY" -m unittest discover -s tests
else
    # shellcheck disable=SC2046
    "$PY" -m unittest $("$PY" ci/tiers.py --targets "$TIER")
fi

if step selftest; then
    echo
    echo "== embedded selftest =="
    "$PY" bin/xcheck selftest | tail -1
fi

if step release-gate; then
    echo
    echo "== release gate: the artifact builds, installs and runs =="
    # Last because it is the most expensive step, and because everything above it is a
    # precondition: there is no point installing a wheel whose source does not lint.
    #
    # This is now the ONLY run of the gate for this interpreter. `test_ci_contract`'s
    # matrix still runs it for every OTHER supported Python present, which is the
    # question this step cannot answer, and skips the delegated one by name.
    bash ci/release-gate.sh | tail -4
fi

echo
if [ "$TIER" = "pr" ]; then
    echo "pr tier passed — NOT a release. The adversarial battery, the docker escape"
    echo "matrix, the selftest, the wheel and the sdist were not run. Run this script"
    echo "with no arguments before releasing anything."
elif [ "$TIER" = "package" ]; then
    echo "package tier passed — the artifact builds, installs, runs and names its"
    echo "version. The battery this ran is the one ABOUT the artifact; the full suite"
    echo "runs on main and nightly. Run this script with no arguments to get it here."
else
    echo "all checks passed"
fi
