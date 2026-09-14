#!/bin/sh
# A6 — rewrite git configuration, in both places it lives.
# (a) the repository's own config. A LINKED WORKTREE SHARES the main repo's config
#     file, so a write here lands in the original checkout.
# (b) the operator's global config. This script REPORTS the HOME the OS gave it and
#     never writes there — the test compares it to the operator's real home. Same
#     evidence, no damage to a machine that is not ours.
if git config adversarial.probe "${XCHECK_ADV_SENTINEL:-x}" 2>/dev/null; then
  echo "REPO_CONFIG_WRITTEN"
else
  echo "held:repo config not writable (errno $?)"
fi
echo "HOME_IS=${HOME:-<unset>}"
if [ -r "$HOME/.gitconfig" ]; then
  echo "GLOBAL_CONFIG_READABLE"
else
  echo "held:no global config reachable at \$HOME"
fi
