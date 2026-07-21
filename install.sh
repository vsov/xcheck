#!/bin/sh
# Install the xcheck methodology into a target project: ./install.sh /path/to/project
set -e
cd "$(dirname "$0")"
TARGET=${1:?usage: ./install.sh /path/to/project}
mkdir -p "$TARGET/audit/findings" "$TARGET/audit/passes" "$TARGET/audit/plans" "$TARGET/audit/templates"
cp XCHECK.md "$TARGET/audit/XCHECK.md"
[ -f "$TARGET/audit/AUDIT.md" ] || cp templates/AUDIT-text.md "$TARGET/audit/AUDIT.md"   # use AUDIT-code.md for code projects
cp templates/finding.md templates/class-finding.md templates/pass.md templates/plan.md "$TARGET/audit/templates/"
[ -f "$TARGET/audit/LEDGER.md" ] || printf '| id | title | severity | status | next | updated |\n|---|---|---|---|---|---|\n' > "$TARGET/audit/LEDGER.md"
