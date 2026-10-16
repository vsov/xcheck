#!/bin/sh
# Install the xcheck methodology into a target project:
#   ./install.sh [--type code|text] /path/to/project
#
# `--type` picks the template AUDIT.md starts from: `code` for a codebase, `text` for a
# text corpus. It has a default (`text`, what this script always installed) where the
# operator profile's trust level has none, because a wrong template is cheap to undo
# until the Planner runs: one `cp` over a file that holds nothing yet.
#
# This sets up the SUBJECT only. The operator profile — what each role runs, and inside
# what boundary — lives outside every audited repository, so `operator-profile.sh`
# writes it, once per machine; the closing lines below say so.
#
# Deliberately shell-only and small. This runs in target projects that may have no
# Python environment configured, and its simplicity is a feature — an installer that
# needs a toolchain is one more thing that can fail before the tool has started.
#
# It writes two provenance artifacts beside the installed copy (phase 10, F-0146 —
# "двойная истина" between the root and the installed tree):
#
#   audit/.provenance.json   what was installed, from where, at what version
#   audit/MANIFEST.sha256    every shipped file and its digest
#
# Without them `xcheck upgrade` cannot tell a file it shipped from a file the
# operator has since made their own, and an upgrade that cannot tell the difference
# either overwrites the operator's work or refuses everything.
set -e
cd "$(dirname "$0")"
SRC=$(pwd)

usage() {
    echo "usage: ./install.sh [--type code|text] /path/to/project"
    echo "  --type code   AUDIT.md starts from templates/AUDIT-code.md: a codebase"
    echo "  --type text   AUDIT.md starts from templates/AUDIT-text.md: a text corpus (default)"
}
TYPE=
TARGET=
while [ $# -gt 0 ]; do
    case "$1" in
        --type)    [ $# -ge 2 ] || { usage >&2; exit 2; }; TYPE=$2; shift 2 ;;
        --type=*)  TYPE=${1#--type=}; shift ;;
        -h|--help) usage; exit 0 ;;
        -*)        echo "install: unknown option $1" >&2; usage >&2; exit 2 ;;
        *)         [ -z "$TARGET" ] || { usage >&2; exit 2; }; TARGET=$1; shift ;;
    esac
done
[ -n "$TARGET" ] || { usage >&2; exit 2; }
# Checked before anything is written: a refused install leaves no `audit/` behind.
case "${TYPE:-text}" in
    code|text) ;;
    *) echo "install: --type $TYPE is not a project type (code or text)" >&2; exit 2 ;;
esac

# What the operator does next, printed on both ways out of this script. The profile step
# comes first because without it `xcheck next` and every launcher that wraps it refuse
# before a session starts, and nothing on the install path used to say so.
finish() {
    echo "         $AUDIT_NOTE"
    echo "next:    1. once per machine, an operator profile, outside every audited repository."
    echo "            \`xcheck next\` and the launchers dispatch nothing until one exists:"
    echo "              $SRC/operator-profile.sh --trust trusted|untrusted /path/to/operator.conf"
    echo "         2. plan the audit: README §6, Step 1."
}

mkdir -p "$TARGET/audit/findings" "$TARGET/audit/passes" "$TARGET/audit/plans" "$TARGET/audit/construals" "$TARGET/audit/templates"

# The shipped set — the ONE list, used for both copying and the manifest, so a file
# that is installed but unlisted (invisible to `upgrade`) is not expressible.
SHIPPED="XCHECK.md templates/finding.md templates/class-finding.md templates/pass.md templates/plan.md templates/construal.md"

cp XCHECK.md "$TARGET/audit/XCHECK.md"
cp templates/finding.md templates/class-finding.md templates/pass.md templates/plan.md templates/construal.md "$TARGET/audit/templates/"
# AUDIT.md and LEDGER.md are the OPERATOR's from the moment they exist: created once,
# never refreshed. `upgrade` will not touch them either, and neither does `--type`: an
# AUDIT.md that exists is reported on, never replaced — even one that still holds
# nothing but a template, for which the report names the `cp` that switches it.
if [ ! -f "$TARGET/audit/AUDIT.md" ]; then
    cp "templates/AUDIT-${TYPE:-text}.md" "$TARGET/audit/AUDIT.md"
    AUDIT_NOTE="audit/AUDIT.md from templates/AUDIT-${TYPE:-text}.md"
    [ -n "$TYPE" ] || AUDIT_NOTE="$AUDIT_NOTE (the default; --type code for a codebase)"
else
    AUDIT_NOTE="audit/AUDIT.md kept: it exists, and it is the operator's"
    for t in code text; do
        cmp -s "$TARGET/audit/AUDIT.md" "templates/AUDIT-$t.md" || continue
        AUDIT_NOTE="audit/AUDIT.md kept: it is the unfilled $t template"
        if [ -n "$TYPE" ] && [ "$TYPE" != "$t" ]; then
            AUDIT_NOTE="$AUDIT_NOTE, and install.sh never replaces an AUDIT.md. To switch:
           cp $SRC/templates/AUDIT-$TYPE.md $TARGET/audit/AUDIT.md"
        fi
    done
fi
[ -f "$TARGET/audit/LEDGER.md" ] || printf '| id | title | severity | status | next | updated |\n|---|---|---|---|---|---|\n' > "$TARGET/audit/LEDGER.md"

# ---- provenance -----------------------------------------------------------------
# `shasum -a 256` (BSD/macOS) or `sha256sum` (GNU). If neither exists we say so and
# stop writing provenance rather than writing a manifest with empty digests — a
# manifest nobody can verify is worse than no manifest, because `upgrade` would trust
# it. The install itself already succeeded and stays valid.
if command -v shasum >/dev/null 2>&1; then
    SHA="shasum -a 256"
elif command -v sha256sum >/dev/null 2>&1; then
    SHA="sha256sum"
else
    echo "install: no shasum/sha256sum on PATH — audit/.provenance.json and"
    echo "         audit/MANIFEST.sha256 were NOT written. The install is complete and"
    echo "         usable; \`xcheck upgrade\` will report the copy as unprovenanced and"
    echo "         refuse to refresh files it cannot prove it shipped."
    finish
    exit 0
fi

digest() { $SHA "$1" | cut -d' ' -f1; }

VERSION=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' xcheck/__init__.py | head -1)
SCHEMA=$(sed -n 's/^SCHEMA_VERSION = \([0-9][0-9]*\)$/\1/p' xcheck/state.py | head -1)
CORE_DIGEST=$(digest XCHECK.md)
SOURCE_REF=$(git -C "$SRC" describe --tags --always --dirty 2>/dev/null || echo unknown)
INSTALLED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# The manifest is built from SHIPPED, in the SOURCE tree, so a digest describes what
# was shipped rather than what happens to be in the target now.
: > "$TARGET/audit/MANIFEST.sha256"
for f in $SHIPPED; do
    printf '%s  %s\n' "$(digest "$f")" "$f" >> "$TARGET/audit/MANIFEST.sha256"
done

NEW_PROV="$TARGET/audit/.provenance.json.tmp"
cat > "$NEW_PROV" <<JSON
{
  "xcheck_version": "$VERSION",
  "core_digest": "$CORE_DIGEST",
  "source_ref": "$SOURCE_REF",
  "schema_version": $SCHEMA,
  "installed_at": "$INSTALLED_AT"
}
JSON

# A re-run of the SAME version over an unchanged copy reports "unchanged" instead of
# restamping `installed_at` — an install time that moves every time somebody re-runs
# the installer is not a fact about the installation.
if [ -f "$TARGET/audit/.provenance.json" ] \
   && [ "$(sed '/installed_at/d' "$TARGET/audit/.provenance.json")" = "$(sed '/installed_at/d' "$NEW_PROV")" ]; then
    rm -f "$NEW_PROV"
    echo "install: unchanged — xcheck $VERSION (schema $SCHEMA) was already installed in $TARGET/audit"
else
    mv "$NEW_PROV" "$TARGET/audit/.provenance.json"
    echo "install: xcheck $VERSION (schema $SCHEMA, source $SOURCE_REF) -> $TARGET/audit"
fi
echo "         provenance: audit/.provenance.json, manifest: audit/MANIFEST.sha256 ($(wc -l < "$TARGET/audit/MANIFEST.sha256" | tr -d ' ') files)"
finish
