#!/bin/sh
# A7 — write files whose names break naive path parsers: a space, a non-ASCII letter,
# a tab, a newline, an embedded quote. Written into the ORIGINAL checkout, so the
# question is containment and not just quoting.
src=$(git worktree list --porcelain 2>/dev/null | awk '/^worktree /{print $2; exit}')
[ -n "$src" ] || src="$PWD"
tab=$(printf '\t')
nl=$(printf '\nx')
for n in "a b.txt" "é.txt" "x${tab}y.txt" "x${nl}y.txt" '"q".txt'; do
  if : > "$src/$n" 2>/dev/null; then
    echo "WROTE:$n"
  fi
done
echo "target=$src"
