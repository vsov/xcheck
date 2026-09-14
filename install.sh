#!/bin/sh
# Install the xcheck methodology into a target project: ./install.sh /path/to/project
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
TARGET=${1:?usage: ./install.sh /path/to/project}
mkdir -p "$TARGET/audit/findings" "$TARGET/audit/passes" "$TARGET/audit/plans" "$TARGET/audit/construals" "$TARGET/audit/templates"

# The shipped set — the ONE list, used for both copying and the manifest, so a file
# that is installed but unlisted (invisible to `upgrade`) is not expressible.
SHIPPED="XCHECK.md templates/finding.md templates/class-finding.md templates/pass.md templates/plan.md templates/construal.md"

cp XCHECK.md "$TARGET/audit/XCHECK.md"
cp templates/finding.md templates/class-finding.md templates/pass.md templates/plan.md templates/construal.md "$TARGET/audit/templates/"
# AUDIT.md and LEDGER.md are the OPERATOR's from the moment they exist: created once,
# never refreshed. `upgrade` will not touch them either.
[ -f "$TARGET/audit/AUDIT.md" ] || cp templates/AUDIT-text.md "$TARGET/audit/AUDIT.md"   # use AUDIT-code.md for code projects
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
