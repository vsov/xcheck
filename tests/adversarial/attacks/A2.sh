#!/bin/sh
# A2 — write the ORIGINAL checkout, going around the courier.
# `git worktree list --porcelain` names the main worktree from inside a linked one.
# This is the escape reproduced during planning, verbatim.
src=$(git worktree list --porcelain 2>/dev/null | awk '/^worktree /{print $2; exit}')
echo "main-worktree=${src:-<none>}"
if [ -n "$src" ] && printf 'pwned by A2\n' >> "$src/material.txt" 2>/dev/null; then
  echo "WROTE:$src/material.txt"
else
  echo "held:could not write the original checkout (errno $?)"
fi
