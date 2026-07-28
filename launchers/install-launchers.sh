#!/bin/sh
# Install xcheck launchers into agent CLIs. Usage: ./install-launchers.sh [claude|codex|opencode|orchestrator|plugin|selftest|all]
# Idempotent: re-running overwrites previously installed launchers with the current versions.
# `selftest` runs the plugin gate against planted poisons and asserts it rejects them.
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

# Single source of truth for how the OpenCode generator reads a skill's
# `description`: the FIRST `description: ` line (literal colon-space prefix),
# prefix stripped. The plugin gate validates by CALLING this same function
# rather than re-expressing the extraction in awk/regex — the validator/consumer
# split that kept F-0055 reopening (gate found ANY non-empty line while the
# consumer took the FIRST) is impossible when both read through one extractor.
opencode_description() {
  sed -n 's/^description: //p' "$1" | head -n 1
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
    desc=$(opencode_description "$src")
    {
      printf -- '---\ndescription: %s\n---\n' "$desc"
      awk 'c==2{print} /^---$/{c++; next}' "$src"
    } > "$destination/$name.md"
  done
}

# F-0055 regression guard (§7 adversarial-guard rule): plant the very defects the
# plugin gate claims to catch — including the duplicate-first-empty `description`
# that repeatedly slipped a re-expressed predicate — into a temp payload and
# assert the gate exits non-zero. A guard that only passes its own happy path is
# a hole; this fails a live mutation.
run_plugin_gate_selftest() {
  src=$(cd .. && pwd)
  work=$(mktemp -d) || { echo "selftest: mktemp failed" >&2; exit 1; }
  trap 'rm -rf "$work"' EXIT INT TERM
  mkdir -p "$work/launchers"
  cp "$src/launchers/install-launchers.sh" "$work/launchers/install-launchers.sh"
  reset_payload() {
    rm -rf "$work/skills" "$work/.claude-plugin" "$work/.codex-plugin"
    cp -R "$src/skills" "$work/skills"
    cp -R "$src/.claude-plugin" "$work/.claude-plugin"
    cp -R "$src/.codex-plugin" "$work/.codex-plugin"
  }
  vf="$work/skills/xcheck-verify/SKILL.md"
  fails=0
  gate() { rc=0; ( cd "$work/launchers" && sh install-launchers.sh plugin >/dev/null 2>&1 ) || rc=$?; }
  expect() { # label want-rc
    if [ "$rc" = "$2" ]; then echo "selftest: ok   — $1 (rc=$rc)";
    else echo "selftest: FAIL — $1 (want rc=$2, got rc=$rc)" >&2; fails=$((fails + 1)); fi
  }

  reset_payload; gate; expect "control: full payload accepted" 0

  # The human-ruling #2 poison: an empty first `description: ` line ahead of the
  # real one. A validator that scans for ANY non-empty description passes; the
  # generator (and now the gate) reads the FIRST line and sees empty.
  reset_payload
  awk 'BEGIN{d=0} /^description: /&&!d{print "description: ";d=1} {print}' "$vf" > "$vf.tmp" && mv "$vf.tmp" "$vf"
  gate; expect "poison: duplicate-first-empty description rejected" 1

  reset_payload
  awk '{ sub(/^description: .*/, "description:x"); print }' "$vf" > "$vf.tmp" && mv "$vf.tmp" "$vf"
  gate; expect "poison: description without colon-space rejected" 1

  reset_payload; : > "$vf"; gate; expect "poison: zero-length SKILL.md rejected" 1

  reset_payload; : > "$work/.claude-plugin/plugin.json"
  gate; expect "poison: zero-length manifest rejected" 1

  # F-0055 reopen re-take #3: an explicit JSON `null` for a declared "skills"
  # member. A predicate using `.get() is not None` treats it like an absent key
  # and skips the path check while claiming the path was checked; membership
  # (`"skills" in obj`) must reject it.
  reset_payload
  python3 - "$work/.codex-plugin/plugin.json" <<'PY'
import json, sys
p = sys.argv[1]
o = json.load(open(p, encoding="utf-8"))
o["skills"] = None
json.dump(o, open(p, "w", encoding="utf-8"), indent=2)
PY
  gate; expect "poison: manifest skills=null rejected" 1

  rm -rf "$work"; trap - EXIT INT TERM
  if [ "$fails" = 0 ]; then echo "selftest: all plugin-gate checks passed"
  else echo "selftest: $fails check(s) FAILED" >&2; exit 1; fi
}

case "$target" in claude|codex|opencode|orchestrator|plugin|selftest|all) ;; *)
  echo "usage: $0 [claude|codex|opencode|orchestrator|plugin|selftest|all]" >&2; exit 2 ;;
esac

if [ "$target" = selftest ]; then
  run_plugin_gate_selftest
  exit 0
fi

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

# F-0039: `orchestrator` is NOT part of `all`. The documented default installs
# only the three CLI launchers ("all three CLIs"); the orchestrator symlink is
# an explicit opt-in target so a no-argument run never silently replaces an
# existing ~/.local/bin/xcheck.
if [ "$target" = orchestrator ]; then
  mkdir -p "$HOME/.local/bin"
  link="$HOME/.local/bin/xcheck"
  # Refuse to clobber an unmanaged regular file; re-linking our own symlink is fine.
  if [ -e "$link" ] && [ ! -L "$link" ]; then
    echo "orchestrator: $link exists and is not a symlink — refusing to overwrite; remove it first" >&2
    exit 1
  fi
  ln -sf "$(cd .. && pwd)/bin/xcheck" "$link"
  echo "orchestrator: $link -> $(cd .. && pwd)/bin/xcheck  (run: xcheck status|next|loop)"
fi

if [ "$target" = plugin ]; then
  root=$(cd .. && pwd)
  # F-0055 (human ruling 2026-07-26, option a): validate and promise EXACTLY
  # what xcheck itself consumes — no more. Full host-loadability is undecidable
  # here (the Claude Code / Codex plugin schemas live outside this repo and
  # evolve); chasing them in an sh installer is a race we lose. So this gate
  # checks the fields our own mechanics and publication depend on, and its
  # wording (here and in launchers/README.md) names exactly those checks —
  # never "well-formed", "loadable", or "incomplete", which promise more than a
  # predicate verifies. Each artifact is reported MISSING, EMPTY, or INVALID so
  # a failure is addressable. Manifest checks need a JSON parser: python3 is
  # already the project runtime (README: "Orchestrator: Python 3"). Require it
  # rather than silently skip — a gate that no-ops when its checker is absent is
  # the same false-green this finding is about.
  command -v python3 >/dev/null 2>&1 || {
    echo "plugin: python3 required to validate plugin manifests (not found on PATH)" >&2; exit 1; }
  for m in .claude-plugin/plugin.json .codex-plugin/plugin.json; do
    [ -f "$root/$m" ] || { echo "plugin: MISSING $root/$m" >&2; exit 1; }
    [ -s "$root/$m" ] || { echo "plugin: EMPTY $root/$m (zero-length manifest)" >&2; exit 1; }
    # Manifest must be a JSON object carrying the fields we depend on: name,
    # version, description (each a non-empty string); and where it declares a
    # "skills" path, that path must resolve to a directory. External platform
    # schema fields are deliberately NOT reproduced here.
    python3 - "$root/$m" "$root" <<'PY' || exit 1
import json, os, sys
path, root = sys.argv[1], sys.argv[2]
def die(msg):
    print('plugin: INVALID %s (%s)' % (path, msg), file=sys.stderr)
    raise SystemExit(1)
try:
    obj = json.load(open(path, encoding="utf-8"))
except Exception:
    die("not valid JSON")
if not isinstance(obj, dict):
    die("not a JSON object")
for k in ("name", "version", "description"):
    v = obj.get(k)
    if not (isinstance(v, str) and v.strip()):
        die('missing or empty required field "%s"' % k)
# Distinguish an ABSENT "skills" key (optional — skip) from a PRESENT one.
# dict.get() returns None for both a missing key and an explicit JSON null, so
# `is not None` would silently skip the check for "skills": null while the gate
# still claims the path was checked (F-0055 reopen re-take #3). Membership test
# is the boundary: any present value — including null — must be a non-empty
# string path resolving to a directory.
if "skills" in obj:
    skills = obj["skills"]
    if not (isinstance(skills, str) and skills.strip()):
        die('"skills" is present but not a non-empty string path')
    if not os.path.isdir(os.path.join(root, skills)):
        die('"skills" path does not resolve to a directory: %s' % skills)
PY
  done
  # F-0040 + F-0055: each of the six named launcher skills must be present,
  # non-empty, carry a `name:` frontmatter line inside a closing `---` block and
  # a non-empty body, AND yield a non-empty `description` when read exactly the
  # way the OpenCode generator reads it. The structural check (name:/frontmatter/
  # body) stays in awk; the `description` check does NOT re-express the extraction
  # — it CALLS opencode_description() (the generator's own reader) and rejects a
  # value with no non-whitespace char. Because gate and consumer share one
  # extractor, no poison can satisfy the gate while the generator emits empty
  # (the duplicate-first-empty split F-0055 human-ruling #2 caught).
  for name in xcheck-plan xcheck-audit xcheck-triage xcheck-remediate xcheck-verify xcheck-status; do
    f="$root/skills/$name/SKILL.md"
    [ -f "$f" ] || { echo "plugin: MISSING required skill $name (skills/$name/SKILL.md)" >&2; exit 1; }
    [ -s "$f" ] || { echo "plugin: EMPTY required skill $name (skills/$name/SKILL.md is zero-length)" >&2; exit 1; }
    awk '
      NR==1 && $0!="---" { exit 1 }
      NR==1 { infm=1; next }
      infm && $0=="---" { infm=0; body=1; next }
      infm && /^name:[ \t]*[^ \t]/ { hasname=1 }
      body && /[^ \t]/ { hasbody=1 }
      END { exit (infm || !hasname || !hasbody) }
    ' "$f" || { echo "plugin: INVALID required skill $name (skills/$name/SKILL.md: needs a name: frontmatter line in a closed --- block and a non-empty body)" >&2; exit 1; }
    desc=$(opencode_description "$f")
    case "$desc" in
      *[![:space:]]*) : ;;
      *) echo "plugin: INVALID required skill $name (skills/$name/SKILL.md: the first 'description: ' line the OpenCode generator reads is empty — it must carry a value)" >&2; exit 1 ;;
    esac
  done
  count=0
  for skill in "$root"/skills/*/SKILL.md; do
    [ -f "$skill" ] || continue
    count=$((count + 1))
  done
  [ "$count" = 6 ] || { echo "plugin: expected exactly 6 skills in $root/skills/, found $count" >&2; exit 1; }
  echo "plugin: manifests carry name/version/description (skills path checked where declared) + six launcher skills have a name: frontmatter line, a non-empty body, and a description the OpenCode generator reads as non-empty"
fi
