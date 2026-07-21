#!/bin/sh
# Install xcheck launchers into agent CLIs. Usage: ./install-launchers.sh [claude|codex|opencode|orchestrator|all]
# Idempotent: re-running overwrites previously installed launchers with the current versions.
set -e
cd "$(dirname "$0")"
target=${1:-all}

case "$target" in claude|codex|opencode|orchestrator|plugin|all) ;; *)
  echo "usage: $0 [claude|codex|opencode|orchestrator|plugin|all]" >&2; exit 2 ;;
esac

if [ "$target" = claude ] || [ "$target" = all ]; then
  mkdir -p "$HOME/.claude/skills"
  for d in claude/*/; do
    name=$(basename "$d")
    mkdir -p "$HOME/.claude/skills/$name"
    cp "$d/SKILL.md" "$HOME/.claude/skills/$name/SKILL.md"
  done
  echo "claude:   6 skills -> ~/.claude/skills/xcheck-*  (invoke: /xcheck-audit etc.)"
fi

if [ "$target" = codex ] || [ "$target" = all ]; then
  mkdir -p "$HOME/.codex/prompts"
  cp codex/*.md "$HOME/.codex/prompts/"
  echo "codex:    6 prompts -> ~/.codex/prompts/  (invoke: /xcheck-audit etc.)"
fi

if [ "$target" = opencode ] || [ "$target" = all ]; then
  mkdir -p "$HOME/.config/opencode/command"
  cp opencode/command/*.md "$HOME/.config/opencode/command/"
  echo "opencode: 6 commands -> ~/.config/opencode/command/  (invoke: /xcheck-audit etc.)"
fi

if [ "$target" = orchestrator ] || [ "$target" = all ]; then
  mkdir -p "$HOME/.local/bin"
  ln -sf "$(cd .. && pwd)/bin/xcheck" "$HOME/.local/bin/xcheck"
  echo "orchestrator: ~/.local/bin/xcheck -> $(cd .. && pwd)/bin/xcheck  (run: xcheck status|next|loop)"
fi

if [ "$target" = plugin ]; then
  # Assemble the repo-root Claude plugin skills/ from launchers/claude (source of truth),
  # so `.claude-plugin/plugin.json` + skills/ make xcheck installable via /plugin or a marketplace.
  root=$(cd .. && pwd)
  rm -rf "$root/skills"
  for d in claude/*/; do
    name=$(basename "$d")
    mkdir -p "$root/skills/$name"
    cp "$d/SKILL.md" "$root/skills/$name/SKILL.md"
  done
  echo "plugin: $root/skills/ assembled (6 skills) + .claude-plugin/plugin.json — install via /plugin (marketplace) or copy the repo"
fi
