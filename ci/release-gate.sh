#!/usr/bin/env bash
# release-gate.sh — build the artifacts, install them, and run the installed tool.
#
# Why this exists
# ---------------
# Before this gate, "0.8.0" was a string in a file. No wheel had ever been built, no
# sdist had ever been installed, the newest tag was v0.3.0, and the classifiers promised
# a Python version CI never ran. Every one of those is invisible to a test suite that
# imports the source tree: `python3 -m unittest` proves the CODE works, and says nothing
# about whether the ARTIFACT does.
#
# So the gate asks the only questions a source-tree test cannot:
#   1. does a wheel build, install into a clean venv, and run FROM that venv?
#   2. does an sdist do the same, from a copy extracted OUTSIDE the git repository, so
#      nothing passes because `.git` happened to be sitting there?
#   3. does either artifact carry a file nobody declared?
#
# The resolved executable path is printed and asserted for both, because the classic way
# to pass this gate accidentally is to run the source tree's `xcheck` from `$PATH` and
# report the venv's success.
#
# What it deliberately does NOT do
# --------------------------------
# It never runs `git tag` and never runs `git push`. A version with no tag is reported —
# `unreleased: X.Y.Z has no tag` — and the gate exits 0. Tagging and publishing are the
# operator's act, and `tests/test_release_gate.sh_never_tags` (in
# tests/test_release_lifecycle.py) greps this file to keep it that way.
#
# Everything temporary goes in a system temp dir. A venv inside the repository is
# exactly the debris `ci/check-artifact.py` exists to catch.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"
FAILED=0

say()  { printf '%s\n' "$*"; }
head2() { printf '\n== %s ==\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*"; FAILED=1; }

# ---------------------------------------------------------------------------
# The PR tier is not a small release.
# ---------------------------------------------------------------------------
# `ci/run-checks.sh --tier=pr` exports XCHECK_TIER=pr and never calls this script. The
# refusal is here anyway, because the fast tier's whole risk is that it becomes the way
# the heavy proofs get skipped — and a caller who invokes the gate by hand from inside a
# PR-tier run would otherwise get a green "release gate: PASS" over a run that never
# executed the adversarial battery, the escape matrix or the selftest.
#
# It refuses rather than warning. A warning in a CI log is a warning nobody reads.
if [ "${XCHECK_TIER:-}" = "pr" ]; then
  printf 'FAIL: refusing to run the release gate inside a PR-tier run.\n'
  printf '      XCHECK_TIER=pr says the adversarial battery, the docker escape matrix,\n'
  printf '      the full selftest and the whole test corpus were NOT run. A gate that\n'
  printf '      passed here would be reporting on proofs nobody executed.\n'
  printf '      Run `bash ci/run-checks.sh` with no arguments for a releasable result.\n'
  exit 1
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/xcheck-release-XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
say "release gate: work dir $WORK"

# ---------------------------------------------------------------------------
# 0. the toolchain, named rather than guessed
# ---------------------------------------------------------------------------
head2 "toolchain"
if ! "$PY" -c "import build" 2>/dev/null; then
  say "FAIL: the \`build\` module is not importable by $PY."
  say "      Install it (\`$PY -m pip install build\`). The gate does NOT fall back to"
  say "      \`setup.py\`: a different build path would produce a different artifact and"
  say "      report it as the one under test."
  exit 1
fi
say "$($PY -c 'import build,sys; print(f"build {build.__version__} on python {sys.version.split()[0]}")')"
say "NOTE: this machine has one interpreter. The 3.10/3.11/3.12 promise is CI's, and"
say "      CI has never been observed running (no git remote). A green gate here is"
say "      evidence about THIS interpreter only."

# ---------------------------------------------------------------------------
# 1. build both artifacts
# ---------------------------------------------------------------------------
head2 "build"
DIST="$WORK/dist"
# setuptools writes `xcheck.egg-info/` INTO the source tree while building an sdist.
# It is gitignored, so nothing would ever complain — which is exactly why it is worth
# removing: a gate that leaves build debris in the project tree is a gate that
# disagrees with the rule it is here to enforce. Removed only if this run created it.
EGGINFO="$ROOT/xcheck.egg-info"
EGG_PREEXISTING=0
[ -e "$EGGINFO" ] && EGG_PREEXISTING=1
cleanup() {
  rm -rf "$WORK"
  if [ "$EGG_PREEXISTING" -eq 0 ] && [ -e "$EGGINFO" ]; then
    rm -rf "$EGGINFO"
  fi
}
trap cleanup EXIT
if ! "$PY" -m build --outdir "$DIST" "$ROOT" > "$WORK/build.log" 2>&1; then
  tail -25 "$WORK/build.log"
  fail "the build itself failed"
  exit 1
fi
[ "$EGG_PREEXISTING" -eq 0 ] && say "note: xcheck.egg-info/ was written into the tree by the build; it is removed on exit"
WHEEL="$(ls "$DIST"/*.whl 2>/dev/null | head -1)"
SDIST="$(ls "$DIST"/*.tar.gz 2>/dev/null | head -1)"
[ -n "$WHEEL" ] || { fail "no wheel produced"; exit 1; }
[ -n "$SDIST" ] || { fail "no sdist produced"; exit 1; }
say "wheel: $(basename "$WHEEL")  ($(wc -c < "$WHEEL" | tr -d ' ') bytes)"
say "sdist: $(basename "$SDIST")  ($(wc -c < "$SDIST" | tr -d ' ') bytes)"

# ---------------------------------------------------------------------------
# 2. install each into its OWN clean venv and run the INSTALLED tool
# ---------------------------------------------------------------------------
install_and_run() {
  local label="$1" artifact="$2" venv="$WORK/venv-$1"
  head2 "$label: clean venv install"
  "$PY" -m venv "$venv" > "$WORK/venv-$label.log" 2>&1 || {
    fail "$label: venv creation failed"; return; }
  "$venv/bin/python" -m pip install --quiet --disable-pip-version-check "$artifact" \
      >> "$WORK/venv-$label.log" 2>&1 || {
    tail -20 "$WORK/venv-$label.log"; fail "$label: pip install failed"; return; }

  # The load-bearing assertion. `xcheck` may well exist on the operator's PATH from the
  # source tree; running THAT and reporting the venv's success is the exact way this
  # gate would lie.
  #
  # PATH is prepended with the venv's bin exactly as `activate` does, and NOT replaced:
  # the rest of the operator's PATH stays visible on purpose. If the artifact failed to
  # install its console script, `which` then finds the SOURCE TREE's `xcheck` and the
  # case below fails — which is the outcome worth detecting. A scrubbed PATH would turn
  # that same defect into a silent `<none>`.
  local resolved
  resolved="$(PATH="$venv/bin:$PATH" "$venv/bin/python" \
      -c 'import shutil; print(shutil.which("xcheck") or "")')"
  say "$label: resolved executable = ${resolved:-<none>}"
  case "$resolved" in
    "$venv"/*) say "$label: the executable is INSIDE the venv" ;;
    *) fail "$label: \`xcheck\` resolved to '$resolved', which is outside $venv — the"
       fail "       source tree, not the artifact, would be what passed"; return ;;
  esac

  local ver
  ver="$("$venv/bin/xcheck" --version 2>&1)"
  say "$label: $ver"
  [ "$ver" = "xcheck $VERSION" ] || fail "$label: installed version reports '$ver', the"$'\n'"       tree says $VERSION"

  if "$venv/bin/xcheck" selftest > "$WORK/selftest-$label.log" 2>&1; then
    say "$label: selftest $(grep -E '^(PASS|FAIL) \(' "$WORK/selftest-$label.log" | tail -1)"
  else
    grep -E '^FAIL' "$WORK/selftest-$label.log" | cut -c1-200
    tail -8 "$WORK/selftest-$label.log"
    fail "$label: the installed selftest failed"
  fi

  # An installed artifact does not carry templates/, skills/, install.sh or XCHECK.md,
  # so the checks that audit those files have no subject and are SKIPPED by name. The
  # gate does not take that on trust: the skipped set must be EXACTLY the set the
  # package declares in `xcheck.selftest.REPO_ONLY_CHECKS`. If a check ever starts
  # skipping itself for some other reason, the artifact's score would quietly shrink
  # and nothing would say why — this is what notices.
  local skipped declared
  skipped="$(sed -n 's/^SKIP: \(.*\) -> .*/\1/p' "$WORK/selftest-$label.log" | sort)"
  declared="$("$venv/bin/python" -c \
      'from xcheck.selftest import REPO_ONLY_CHECKS; print("\n".join(sorted(REPO_ONLY_CHECKS)))')"
  say "$label: skipped $(printf '%s' "$skipped" | grep -c . || true) checks with no subject in an installed artifact:"
  printf '%s\n' "$skipped" | sed 's/^/         - /'
  if [ "$skipped" != "$declared" ]; then
    fail "$label: the skipped set is not the declared REPO_ONLY_CHECKS set"
    diff <(printf '%s\n' "$declared") <(printf '%s\n' "$skipped") | sed 's/^/       /'
  fi
}

VERSION="$("$PY" -c "import sys; sys.path.insert(0, '$ROOT'); import xcheck; print(xcheck.__version__)")"
say "tree version: $VERSION"

install_and_run wheel "$WHEEL"

# The sdist is installed from an EXTRACTED COPY OUTSIDE the repository. Installing the
# tarball in place would leave the build backend standing next to a `.git` directory,
# and any file it picked up from there would be a file the shipped artifact does not
# have.
head2 "sdist: extract outside the repository"
EXTRACT="$WORK/extract"
mkdir -p "$EXTRACT"
tar -xzf "$SDIST" -C "$EXTRACT"
SRCDIR="$(find "$EXTRACT" -maxdepth 1 -mindepth 1 -type d | head -1)"
say "extracted to $SRCDIR (outside $ROOT)"
case "$SRCDIR" in
  "$ROOT"/*) fail "the extraction landed inside the repository" ;;
  *) say "confirmed outside the git repository" ;;
esac
[ -e "$SRCDIR/.git" ] && fail "the extracted sdist carries a .git — it is not a clean copy"
install_and_run sdist "$SRCDIR"

# ---------------------------------------------------------------------------
# 3. undeclared-file check over BOTH artifacts
# ---------------------------------------------------------------------------
head2 "undeclared files in the built artifacts"
"$PY" "$ROOT/ci/check-artifact-contents.py" "$WHEEL" "$SDIST" || fail "undeclared files"

# ---------------------------------------------------------------------------
# 4. checksums and SBOM
# ---------------------------------------------------------------------------
head2 "SHA256SUMS and SBOM"
OUT="$WORK/release"
mkdir -p "$OUT"
cp "$WHEEL" "$SDIST" "$OUT/"
( cd "$OUT" && "$PY" - <<'PYEOF' > SHA256SUMS
import hashlib, pathlib
for f in sorted(pathlib.Path(".").iterdir()):
    if f.name == "SHA256SUMS" or not f.is_file():
        continue
    print(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}")
PYEOF
) || fail "could not write SHA256SUMS"
cat "$OUT/SHA256SUMS"
"$PY" "$ROOT/ci/make-sbom.py" "$ROOT" "$OUT" || fail "SBOM generation failed"
say "--- sbom.cdx.json ---"
cat "$OUT/sbom.cdx.json"
cp "$OUT/SHA256SUMS" "$OUT/sbom.cdx.json" "$WORK/" 2>/dev/null || true

# ---------------------------------------------------------------------------
# 5. tag parity — REPORTED, never created
# ---------------------------------------------------------------------------
head2 "tag parity"
if git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if git -C "$ROOT" rev-parse -q --verify "refs/tags/v$VERSION" >/dev/null; then
    say "released: v$VERSION exists"
  else
    say "unreleased: $VERSION has no tag"
    say "  This is a report, not a problem, and the gate exits 0 on it. Creating the tag"
    say "  is the operator's act; see docs/release-checklist.md."
  fi
else
  say "not a git repository — tag parity not applicable"
fi

head2 "result"
if [ "$FAILED" -eq 0 ]; then
  say "release gate: PASS ($VERSION, wheel + sdist installed and run from clean venvs)"
  say "A green gate is not a published release."
  exit 0
fi
say "release gate: FAIL"
exit 1
