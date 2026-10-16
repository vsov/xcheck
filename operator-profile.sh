#!/bin/sh
# Write an xcheck operator profile, once per machine:
#   ./operator-profile.sh --trust trusted|untrusted [--project DIR] [PATH]
#
# `install.sh` sets up the SUBJECT; this sets up the OPERATOR. They are two scripts
# because they write to two places that must never be the same: the profile says what
# each role runs and inside what boundary, so it lives OUTSIDE every audited repository
# (README §8), and `xcheck next` dispatches nothing until one resolves.
#
# It decides nothing for the operator. `trust_level` has no default in xcheck and none
# here: how much containment a repository needs is a fact about that repository. The
# location is named by the operator too, or by $XDG_CONFIG_HOME — like xcheck itself,
# this script never derives a path from the home directory.
#
# What it does: copies docs/operator.conf.example (byte-identical to the template the
# loader prints; `tests/test_conf_policy.py` holds that), sets `trust_level` — and for
# `untrusted` the `sandbox_profile=container` that classification makes mandatory —
# validates the result with xcheck's own loader, and only then links it into place. It
# never overwrites a profile.
set -e
SRC=$(cd "$(dirname "$0")" && pwd)
PY=${PYTHON:-python3}
ME=operator-profile

usage() {
    cat <<EOF
usage: $0 --trust trusted|untrusted [--project DIR] [PATH]

  --trust trusted     code you or your organisation wrote and review
  --trust untrusted   anything else; also sets sandbox_profile=container (needs docker)
  --project DIR       an audited project; refuse a PATH inside it
  PATH                where the profile goes, outside every audited repository.
                      Optional when \$XDG_CONFIG_HOME is set: then it is
                      \$XDG_CONFIG_HOME/xcheck/operator.conf
EOF
}

TRUST=
PROJECT=
DEST=
while [ $# -gt 0 ]; do
    case "$1" in
        --trust)     [ $# -ge 2 ] || { usage >&2; exit 2; }; TRUST=$2; shift 2 ;;
        --trust=*)   TRUST=${1#--trust=}; shift ;;
        --project)   [ $# -ge 2 ] || { usage >&2; exit 2; }; PROJECT=$2; shift 2 ;;
        --project=*) PROJECT=${1#--project=}; shift ;;
        -h|--help)   usage; exit 0 ;;
        -*)          echo "$ME: unknown option $1" >&2; usage >&2; exit 2 ;;
        *)           [ -z "$DEST" ] || { echo "$ME: one PATH only" >&2; usage >&2; exit 2; }
                     DEST=$1; shift ;;
    esac
done

case "$TRUST" in
    trusted|untrusted) ;;
    "")
        cat >&2 <<EOF
$ME: --trust is required.
xcheck has no default for it, because how much containment a repository needs is a
fact about that repository, and only you know it:

  --trust trusted     code you or your organisation wrote and review. Sessions run in a
                      git worktree, which isolates the repository, not the machine.
  --trust untrusted   anything else. Sessions run in a container, so docker must work
                      on this machine.

SECURITY.md in the xcheck repository says what each boundary does and does not stop.
EOF
        exit 2 ;;
    *)
        echo "$ME: --trust $TRUST is not a trust level (trusted or untrusted)" >&2
        exit 2 ;;
esac

if [ -z "$DEST" ]; then
    if [ -z "$XDG_CONFIG_HOME" ]; then
        cat >&2 <<EOF
$ME: no PATH given, and \$XDG_CONFIG_HOME is not set. xcheck never derives a location
from your home directory, and neither does this script, so name one — any path outside
every audited repository:

    $0 --trust $TRUST /path/to/operator.conf
EOF
        exit 2
    fi
    DEST=$XDG_CONFIG_HOME/xcheck/operator.conf
fi

if [ -e "$DEST" ] || [ -L "$DEST" ]; then
    echo "$ME: $DEST already exists, and this script never overwrites a profile." >&2
    echo "    Edit it by hand, or move it away and run this again." >&2
    exit 1
fi
if [ -n "$PROJECT" ] && [ ! -d "$PROJECT" ]; then
    echo "$ME: --project $PROJECT is not a directory" >&2
    exit 2
fi
command -v "$PY" >/dev/null 2>&1 || {
    echo "$ME: $PY not found. xcheck runs on Python 3; set PYTHON to the interpreter it uses." >&2
    exit 2
}

DIR=$(dirname "$DEST")
mkdir -p "$DIR"
DIR=$(cd "$DIR" && pwd)
DEST=$DIR/$(basename "$DEST")

# Built and validated beside the destination, so the location check below judges the
# directory the profile will really live in, and the final link is one rename away.
TMP=$(mktemp "$DIR/.operator.conf.XXXXXX")
trap 'rm -f "$TMP"' EXIT

if [ "$TRUST" = untrusted ]; then
    sed -e "s/^trust_level=\$/trust_level=$TRUST/" \
        -e 's/^sandbox_profile=$/sandbox_profile=container/' \
        "$SRC/docs/operator.conf.example" > "$TMP"
else
    sed -e "s/^trust_level=\$/trust_level=$TRUST/" "$SRC/docs/operator.conf.example" > "$TMP"
fi
grep -q "^trust_level=$TRUST\$" "$TMP" || {
    echo "$ME: docs/operator.conf.example has no empty trust_level= line to set; it no" >&2
    echo "    longer matches what this script expects, and nothing was written." >&2
    exit 1
}

# xcheck's own loader, so a profile this script writes is one `xcheck next` accepts: the
# location gate, the unknown-key and project-key refusals, and the trust level. With no
# --project there is no subject to measure the location against; xcheck applies the
# same gate again at every dispatch, against the project it is dispatching for.
"$PY" -I - "$SRC" "$TMP" "$PROJECT" <<'EOF' || exit 1
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from xcheck import policy
path, project = Path(sys.argv[2]), sys.argv[3]
if not project:
    project = path.parent / ".no-project-named"
try:
    level = policy.trust_level(policy.load(path, project))
except policy.PolicyError as e:
    sys.exit(f"operator-profile: refused, nothing written.\n{e}")
if not level:
    sys.exit("operator-profile: the loader read no trust_level; nothing written.")
EOF

# A hard link, not a move: it fails if DEST appeared since the check above, so a
# profile somebody wrote in the meantime is never replaced.
ln "$TMP" "$DEST" 2>/dev/null || {
    echo "$ME: $DEST appeared while this ran; it was left alone and nothing was written." >&2
    exit 1
}
rm -f "$TMP"

echo "$ME: wrote $DEST (trust_level=$TRUST)"
if [ "$TRUST" = untrusted ]; then
    echo "  sandbox_profile=container: every session runs in a container, so docker must"
    echo "  work on this machine."
fi
echo "  Role commands, from the template. Edit the file to change them:"
grep '^[a-z]*_cmd=' "$DEST" | sed 's/^/    /'

XDG_DEST=
if [ -n "$XDG_CONFIG_HOME" ] && [ -d "$XDG_CONFIG_HOME" ]; then
    XDG_DEST=$(cd "$XDG_CONFIG_HOME" && pwd)/xcheck/operator.conf
fi
if [ "$DEST" = "$XDG_DEST" ]; then
    echo "  xcheck finds it through \$XDG_CONFIG_HOME, so there is nothing to export."
else
    echo "  xcheck reads it only where it is told to. Run this, and add it to your"
    echo "  shell's startup file:"
    case "${SHELL##*/}" in
        fish) echo "    set -gx XCHECK_OPERATOR_PROFILE \"$DEST\"" ;;
        *)    echo "    export XCHECK_OPERATOR_PROFILE=\"$DEST\"" ;;
    esac
fi
if [ -n "$XCHECK_OPERATOR_PROFILE" ] && [ "$XCHECK_OPERATOR_PROFILE" != "$DEST" ]; then
    echo "  note: XCHECK_OPERATOR_PROFILE is set to $XCHECK_OPERATOR_PROFILE in this shell,"
    echo "  and xcheck reads it first, so this profile is not in force until it points here."
fi
if [ -n "$PROJECT" ]; then
    echo "next: $SRC/bin/xcheck --project $PROJECT status    (its policy: line names the file)"
else
    echo "next: from the audited project's root, \`xcheck status\` names the profile in force"
    echo "      on its policy: line; then plan with /xcheck-plan."
fi
