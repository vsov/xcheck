# xcheck launchers

Thin per-CLI wrappers over the methodology. Each one (1) checks that `audit/XCHECK.md` is installed in the current project, (2) auto-picks the next charter from the pass queue / ledger, (3) starts the role exactly per `audit/XCHECK.md`, which stays the single normative source. They are **not empty** and **not zero-normative**, though: for safety and ergonomics some of the methodology's rules are duplicated inside the skills. Because of that duplication, re-run `install-launchers.sh` (idempotent) after *any* change to the methodology, so the installed copies and the generated OpenCode commands never fall behind. The methodology itself stays in the audited project.

**The five writing launchers are `orchestrated`.** Each one is a WRAPPER: it does preflight, says which role is next and why, then hands the work to `xcheck next`, which dispatches the role through `runner.py` with all eight controls in force — sandbox profile, hard timeout, environment allowlist, log redaction, process-group kill, courier review of the diff, invocation envelope, session receipt. The role runs as a child process you do not talk to, and its reasoning reaches you as recorded output instead of as a dialogue. **Only `/xcheck-status` is `uncontained-direct`**, and it is read-only: it writes nothing and moves no machine state, so there is no diff for a courier to review and no effect for an envelope or a receipt to attest. Starting a role by hand — `Read audit/XCHECK.md. Role: <Role>.` — is still `uncontained-direct` and still legitimate when done deliberately; no shipped launcher does it. Each skill declares its own mode in its own body, and `tests/test_launch_surfaces.py` checks that declaration against what the body actually does.

Six launchers per platform, with Codex skill syntax and slash-command syntax elsewhere:

| Codex | Claude Code / OpenCode | Role | Auto-picked charter |
|---|---|---|---|
| `$xcheck-plan` | `/xcheck-plan` | Planner | fill `audit/AUDIT.md` (stops only when it is a complete plan — a norm-backed dimension, a unit map, and a non-empty pass queue, §3; a partial/malformed AUDIT.md is not done) |
| `$xcheck-audit` | `/xcheck-audit` | Auditor | first unchecked pass in the queue (optional pass id, or `disputed`) |
| `$xcheck-triage` | `/xcheck-triage` | Triage (dialogue) | all `reported` rows; agent is the pen, human is the decider |
| `$xcheck-remediate` | `/xcheck-remediate` | Remediator | resume unfinished work first (reopened / left-over validated·planned / open RP, §5), else an accepted CF, else next ≤ batch-size accepted findings |
| `$xcheck-verify` | `/xcheck-verify` | Verifier | everything in status `fixed`; refuses to judge its own fixes |
| `$xcheck-status` | `/xcheck-status` | — (read-only) | dashboard: queue progress, statuses, whose turn, termination check |

## Install

```sh
./install-launchers.sh          # all three CLIs
./install-launchers.sh claude   # ~/.claude/skills/xcheck-*/SKILL.md
./install-launchers.sh codex    # ~/.agents/skills/xcheck-*/SKILL.md
./install-launchers.sh opencode # generated from ../skills/ -> ~/.config/opencode/command/xcheck-*.md
./install-launchers.sh orchestrator # symlink ~/.local/bin/xcheck
./install-launchers.sh plugin       # check both manifests carry name/version/description (+ skills path where declared) and all six launcher skills have a name: frontmatter line, a body, and a description the OpenCode generator reads as non-empty; exits non-zero naming the first bad artifact
./install-launchers.sh selftest     # regression: plant the poisons the plugin gate must catch (incl. duplicate-first-empty description) and assert it rejects them
```

Idempotent; re-run after updating the xcheck repo to refresh launchers. The Codex target removes the six legacy xcheck custom prompts from `~/.codex/prompts/` during migration. OpenCode commands are generated at install time from the shared `skills/` payload — there is no second checked-in copy to drift. Uninstall = delete the copied skill or command files.

## Cross-agent discipline

The launchers preserve the methodology's defenses; they do not replace them:

- Default mapping stays Codex = Planner/Auditor/Verifier, Claude = Remediator. Every launcher runs on every platform, but `xcheck-verify` stops when the agent or session that produced the fixes would verify them; a fresh session of the same agent is the fallback minimum (XCHECK.md §3) and must be disclosed to the human.
- `xcheck-triage` operates under the Agent-as-pen rule (XCHECK.md §3): it records the human's stated decisions verbatim and never decides on its own.
- One writing session per project at a time (XCHECK.md §4 rule 8) applies to launcher-started sessions too. `xcheck-status` is read-only and exempt.

## Orchestrator

This section is the `orchestrated` mode — the other launch surface, and the one with the controls.

`bin/xcheck` (python3, stdlib) drives the cycle between human gates: `xcheck next` runs one session (whoever's turn), `xcheck loop` runs sessions until it hits a human gate — see README §8 ("The loop halts at …") for the authoritative, exhaustive list of orchestrator halt points; this file deliberately does not duplicate it, since two copies drift — the methodology's human gates are enumerated canonically in `XCHECK.md` §3, not here (`--step` to pause between sessions, `--max-sessions N` cost cap, `--dry-run` to preview), `xcheck status` read-only dashboard, `xcheck unlock` diagnoses a stale lock (`--force` removes it), `xcheck selftest` tests the decision logic. Configuration is split across two files with two owners: role→command mapping, containment and every other executable setting live in the **operator profile OUTSIDE the audited repository** (`$XCHECK_OPERATOR_PROFILE`, or `$XDG_CONFIG_HOME/xcheck/operator.conf` — see `docs/operator.conf.example`), because a repository that could name the commands its own audit runs could rewrite the terms of that audit; `audit/orchestrator.conf` keeps only what this audit is ABOUT, and a policy key found there is refused by name. Extra subcommands: `xcheck lint` (ledger↔finding-file consistency — orphans, status drift, class-membership breaks), `xcheck metrics` (spec §11: auditor accuracy, fix durability, findings by dimension/severity). Config knobs: `triage_batch_cap` (project — stop for triage once N findings pile up), `push_after_commit` (operator profile — opt-in auto-push, default off). `loop` also takes `--budget SECONDS` (wall-clock budget checked between sessions, alongside `--max-sessions` — a session already running is not interrupted). All sessions and the orchestrator share the `audit/.lock` protocol; on a project under Git, after every session the orchestrator commits the session's OWN new dirt (snapshot-diff of git status before/after — pre-existing operator dirt is never swept in). On a non-Git project no commit is made — the session's own changed files are the durable handoff, exactly per XCHECK.md §9 rule 7 ("Git is an amplifier, not a requirement"). The Remediator role-prompt instructs committing under that same rule.
