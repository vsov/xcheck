#!/bin/sh
# Install xcheck launchers into agent CLIs. Usage: ./install-launchers.sh [claude|codex|opencode|orchestrator|plugin|all]
# Idempotent: re-running overwrites previously installed launchers with the current versions.
set -e
cd "$(dirname "$0")"
target=${1:-all}

install_skills() {
  destination=$1
  mkdir -p "$destination"
  for d in ../skills/*/; do
    name=$(basename "$d")
    mkdir -p "$destination/$name"
    cp "$d/SKILL.md" "$destination/$name/SKILL.md"
  done
}

remove_legacy_codex_prompts() {
  for name in xcheck-plan xcheck-audit xcheck-triage xcheck-remediate xcheck-verify xcheck-status; do
    rm -f "$HOME/.codex/prompts/$name.md"
  done
}

# OpenCode commands are generated from the shared skills/ payload (single
# source of truth): the SKILL.md frontmatter becomes an OpenCode
# `description:` frontmatter; the body is copied verbatim.
generate_opencode_commands() {
  destination=$1
  mkdir -p "$destination"
  for d in ../skills/*/; do
    name=$(basename "$d")
    src="$d/SKILL.md"
    desc=$(sed -n 's/^description: //p' "$src" | head -n 1)
    {
      printf -- '---\ndescription: %s\n---\n' "$desc"
      awk 'c==2{print} /^---$/{c++; next}' "$src"
    } > "$destination/$name.md"
  done
}

case "$target" in claude|codex|opencode|orchestrator|plugin|all) ;; *)
  echo "usage: $0 [claude|codex|opencode|orchestrator|plugin|all]" >&2; exit 2 ;;
esac

if [ "$target" = claude ] || [ "$target" = all ]; then
  install_skills "$HOME/.claude/skills"
  echo "claude:   6 skills -> ~/.claude/skills/xcheck-*  (invoke: /xcheck-audit etc.)"
fi

if [ "$target" = codex ] || [ "$target" = all ]; then
  install_skills "$HOME/.agents/skills"
  remove_legacy_codex_prompts
  echo "codex:    6 skills -> ~/.agents/skills/xcheck-*  (invoke: \$xcheck-audit etc.)"
fi

if [ "$target" = opencode ] || [ "$target" = all ]; then
  generate_opencode_commands "$HOME/.config/opencode/command"
  echo "opencode: 6 commands generated from skills/ -> ~/.config/opencode/command/  (invoke: /xcheck-audit etc.)"
fi

if [ "$target" = orchestrator ] || [ "$target" = all ]; then
  mkdir -p "$HOME/.local/bin"
  ln -sf "$(cd .. && pwd)/bin/xcheck" "$HOME/.local/bin/xcheck"
  echo "orchestrator: ~/.local/bin/xcheck -> $(cd .. && pwd)/bin/xcheck  (run: xcheck status|next|loop)"
fi

if [ "$target" = plugin ]; then
  root=$(cd .. && pwd)
  for m in .claude-plugin/plugin.json .codex-plugin/plugin.json; do
    [ -f "$root/$m" ] || { echo "plugin: MISSING $root/$m" >&2; exit 1; }
  done
  count=0
  for skill in "$root"/skills/*/SKILL.md; do
    [ -f "$skill" ] || continue
    count=$((count + 1))
  done
  [ "$count" = 6 ] || { echo "plugin: expected 6 skills in $root/skills/, found $count" >&2; exit 1; }
  echo "plugin: checked-in skills/ + Claude and Codex manifests are present (6 skills)"
fi
