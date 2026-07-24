# xcheck launchers

Thin per-CLI wrappers over the methodology. They contain **zero normative content** — each one only (1) checks that `audit/XCHECK.md` is installed in the current project, (2) auto-picks the next charter from the pass queue / ledger, (3) starts the role exactly per `audit/XCHECK.md`. The methodology stays in the audited project; launchers are ergonomics.

Six launchers per platform, with Codex skill syntax and slash-command syntax elsewhere:

| Codex | Claude Code / OpenCode | Role | Auto-picked charter |
|---|---|---|---|
| `$xcheck-plan` | `/xcheck-plan` | Planner | fill `audit/AUDIT.md` (stops if the pass queue already exists) |
| `$xcheck-audit` | `/xcheck-audit` | Auditor | first unchecked pass in the queue (optional pass id, or `disputed`) |
| `$xcheck-triage` | `/xcheck-triage` | Triage (dialogue) | all `reported` rows; agent is the pen, human is the decider |
| `$xcheck-remediate` | `/xcheck-remediate` | Remediator | accepted CF, else next ≤ batch-size accepted findings |
| `$xcheck-verify` | `/xcheck-verify` | Verifier | everything in status `fixed`; refuses to judge its own fixes |
| `$xcheck-status` | `/xcheck-status` | — (read-only) | dashboard: queue progress, statuses, whose turn, termination check |

## Install

```sh
./install-launchers.sh          # all three CLIs
./install-launchers.sh claude   # ~/.claude/skills/xcheck-*/SKILL.md
./install-launchers.sh codex    # ~/.agents/skills/xcheck-*/SKILL.md
./install-launchers.sh opencode # generated from ../skills/ -> ~/.config/opencode/command/xcheck-*.md
./install-launchers.sh orchestrator # symlink ~/.local/bin/xcheck
./install-launchers.sh plugin       # validate skills/ and both plugin manifests (fails loudly if incomplete)
```

Idempotent; re-run after updating the xcheck repo to refresh launchers. The Codex target removes the six legacy xcheck custom prompts from `~/.codex/prompts/` during migration. OpenCode commands are generated at install time from the shared `skills/` payload — there is no second checked-in copy to drift. Uninstall = delete the copied skill or command files.

## Cross-agent discipline

The launchers preserve the methodology's defenses; they do not replace them:

- Default mapping stays Codex = Planner/Auditor/Verifier, Claude = Remediator. Every launcher runs on every platform, but `xcheck-verify` warns (and requires acknowledgement) when verification would land on the same agent that produced the fixes.
- `xcheck-triage` operates under the Agent-as-pen rule (XCHECK.md §3): it records the human's stated decisions verbatim and never decides on its own.
- One writing session per project at a time (XCHECK.md §4 rule 8) applies to launcher-started sessions too. `xcheck-status` is read-only and exempt.

## Orchestrator

`bin/xcheck` (python3, stdlib) drives the cycle between human gates: `xcheck next` runs one session (whoever's turn), `xcheck loop` runs until a gate (triage needed / ⚠ needs-human / done / error; `--step` to pause between sessions, `--max-sessions N` cost cap, `--dry-run` to preview), `xcheck status` read-only dashboard, `xcheck unlock` clears a stale lock, `xcheck selftest` tests the decision logic. Config per project: `audit/orchestrator.conf` (role→command mapping; defaults: Codex for Planner/Auditor/Verifier, `claude -p --dangerously-skip-permissions` for Remediator). Extra subcommands: `xcheck lint` (ledger↔finding-file consistency — orphans, status drift, class-membership breaks), `xcheck metrics` (spec §11: auditor accuracy, fix durability, findings by dimension/severity). Config knobs: `triage_batch_cap` (stop for triage once N findings pile up — smaller sittings), `push_after_commit` (opt-in auto-push, default off). `loop` also takes `--budget SECONDS` (wall-clock cap alongside `--max-sessions`). All sessions and the orchestrator share the `audit/.lock` protocol; after every session the orchestrator commits the session's OWN new dirt (snapshot-diff of git status before/after — pre-existing operator dirt is never swept in), and the Remediator role-prompt now instructs committing per XCHECK.md §9 rule 7.
