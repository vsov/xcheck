# xcheck bootstrap

Install xcheck into a target project, then launch each role from there.
`XCHECK.md` defines the methodology; this file only gets you from zero to a
running cycle.

## 1. Install

Run once, from the xcheck repo root, per audited project. The block below
copies the text-corpus template by default — swap `AUDIT-text.md` for
`AUDIT-code.md` in the second `cp` line when auditing a codebase.

```bash
# from the xcheck repo root; TARGET = audited project root
TARGET=/path/to/project
mkdir -p "$TARGET/audit/findings" "$TARGET/audit/passes" "$TARGET/audit/plans"
cp XCHECK.md "$TARGET/audit/XCHECK.md"
[ -f "$TARGET/audit/AUDIT.md" ] || cp templates/AUDIT-text.md "$TARGET/audit/AUDIT.md"   # or AUDIT-code.md for a codebase
cp templates/finding.md templates/class-finding.md templates/pass.md templates/plan.md "$TARGET/audit/templates/" 2>/dev/null || { mkdir -p "$TARGET/audit/templates"; cp templates/finding.md templates/class-finding.md templates/pass.md templates/plan.md "$TARGET/audit/templates/"; }
[ -f "$TARGET/audit/LEDGER.md" ] || printf '| id | title | severity | status | next | updated |\n|---|---|---|---|---|---|\n' > "$TARGET/audit/LEDGER.md"
```

Result: `$TARGET/audit/` holds `XCHECK.md`, `AUDIT.md`, an empty `LEDGER.md`,
`templates/`, and empty `findings/`, `passes/`, `plans/`. Nothing is audited
yet — the Planner fills `AUDIT.md` next. Re-running upgrades XCHECK.md and
templates without touching audit state.

## 2. Launch one-liners

A human starts every session by pasting one line into a fresh agent CLI
session, opened with the audited project (`$TARGET`, not the xcheck repo) as
its working directory — the one-liner format is XCHECK.md §4 rule 7. The
session reads only XCHECK.md, AUDIT.md, its charter, whatever the ledger
points to, and the artifact templates it instantiates — nothing carried over
from earlier sessions (§4 rules 2–3).
Default agent is Codex for Planner, Auditor, and Verifier, and Claude for
Remediator, but any capable agent can play any role; the same line works
verbatim in either CLI.

| Role | Codex CLI | Claude Code |
|---|---|---|
| Planner | `Read audit/XCHECK.md. Role: Planner. Charter: inventory this project and its norms; produce audit/AUDIT.md. Do not audit anything yet.` | `Read audit/XCHECK.md. Role: Planner. Charter: inventory this project and its norms; produce audit/AUDIT.md. Do not audit anything yet.` |
| Auditor | `Read audit/XCHECK.md and audit/AUDIT.md. Role: Auditor. Charter: pass P-03. Stop conditions per charter.` | `Read audit/XCHECK.md and audit/AUDIT.md. Role: Auditor. Charter: pass P-03. Stop conditions per charter.` |
| Triage | — human, no agent session — | — human, no agent session — |
| Remediator | `Read audit/XCHECK.md and audit/AUDIT.md. Role: Remediator. Charter: findings F-0012..F-0019 (see LEDGER).` | `Read audit/XCHECK.md and audit/AUDIT.md. Role: Remediator. Charter: findings F-0012..F-0019 (see LEDGER).` |
| Verifier | `Read audit/XCHECK.md and audit/AUDIT.md. Role: Verifier. Charter: all findings in status fixed. You did not write these fixes; try to prove them wrong.` | `Read audit/XCHECK.md and audit/AUDIT.md. Role: Verifier. Charter: all findings in status fixed. You did not write these fixes; try to prove them wrong.` |

Triage has no launch line: open `audit/LEDGER.md` and set each `reported`
row's status to `accepted`, `rejected`, or `deferred` directly — open the
finding file too when the title alone doesn't settle the call. The human
edits only `LEDGER.md`; the next agent session that touches a finding
carries a newer ledger decision into the finding file before doing any
other work (XCHECK.md §2). Batch decisions are fine, e.g. accept every
`critical` and `major` row in one pass. Rejecting a CF also reverts every
finding in its `members:` list to `accepted` (write the ledger rows; agents
apply to files per §2).

The Auditor/Remediator/Verifier charter text above is an example — replace
the pass ID or finding-ID range with the actual scope before pasting;
Planner's charter is fixed and needs no edits. A session handed a one-liner
with no charter should refuse to start (XCHECK.md §4 rule 1).

An Auditor charter is not only a fresh pass — a dispute round is a valid
charter too: `Read audit/XCHECK.md and audit/AUDIT.md. Role: Auditor.
Charter: respond to disputed findings (see LEDGER).`

## 3. A typical cycle

1. Install (§1) into the target project.
2. Planner session produces `AUDIT.md` — norms catalog, dimensions, unit map, pass queue.
3. Human skims the pass queue and trims it: drop or reorder passes; cheap, no gate, just judgment.
4. Auditor sessions work the queue one charter at a time, filing findings and a pass report per charter.
5. Triage: human sets every `reported` finding to `accepted`, `rejected`, or `deferred`.
6. Remediator sessions take batches of `accepted` findings through validate → census → plan → fix → self-check.
7. Verifier sessions take the `fixed` batch and give each finding a `closed` or `reopened` verdict.
8. Repeat from step 4 with the next queued pass. `reopened` findings re-enter the cycle at step 6 (Remediator), not step 4; `reopen_limit` consecutive `reopened` verdicts on one finding (2 by default) sets `⚠ needs-human` in the ledger and a human decides what happens to it next.
9. The audit is complete when the AUDIT.md pass queue is empty and every ledger row is terminal (closed, rejected, withdrawn, obsolete, or superseded-by-class with a closed CF).

## 4. Rules of the road

- One active writing session per project at a time — no concurrent agents editing `audit/`.
- The human is the courier: relay the baton between sessions; agents do not hand off to each other directly.
- Findings, plans, and reports are written in the operator's working language (pick one per audit and stay consistent); evidence quotes stay verbatim in the material's own language.
- The Verifier is never the fixer — a different agent, or at minimum a different fresh session of the same agent.

## 5. Launchers (optional)

Instead of pasting one-liners by hand, install the thin CLI wrappers — `/xcheck-plan`, `/xcheck-audit`, `/xcheck-triage`, `/xcheck-remediate`, `/xcheck-verify`, `/xcheck-status` — for Claude Code, Codex, and OpenCode: see `launchers/README.md` (`launchers/install-launchers.sh`). They auto-pick the next charter and contain no methodology content.

## 6. Field notes (pilot-tested)

- Codex CLI sessions run in a sandbox that keeps `.git` read-only: Verifier sessions may be unable to commit their verdict updates. The courier finishes those commits — check `git status` after each Codex session.
- Resist launching extra writing sessions (micro-passes, side tasks) while an agent session is active — §4 rule 8 exists because concurrent writers corrupt each other's view. Pilot 1 got away with it three times on luck and worktree isolation.
- On security-sensitive dimensions (memory-safety, UB, exploitability), a provider's content moderation may block an agent from doing the defensive review — OpenAI/Codex flagged first-party memory-safety auditing as "cybersecurity risk" and aborted mid-session in pilot 2, disclaimers notwithstanding. The methodology is agent-agnostic: switch that dimension's Auditor/Verifier to an agent that isn't blocked (one line in `orchestrator.conf`, or a different launcher). Verifier ≠ fixer still holds via distinct fresh sessions; note the reduced cross-agent independence in the report.
