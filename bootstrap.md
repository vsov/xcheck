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
mkdir -p "$TARGET/audit/templates"
cp templates/finding.md templates/class-finding.md templates/pass.md templates/plan.md templates/construal.md "$TARGET/audit/templates/"
```

Result: `$TARGET/audit/` holds `XCHECK.md`, `AUDIT.md`, `templates/`, and empty
`findings/`, `passes/`, `plans/`. Nothing is audited yet — the Planner fills
`AUDIT.md` next. Re-running upgrades XCHECK.md and templates without touching
audit state.

`LEDGER.md` is not seeded here, because it is not a file anyone writes: from
0.8 the canonical record is `audit/state.json`, and `LEDGER.md` plus every
finding's frontmatter block are GENERATED MIRRORS of it (XCHECK.md §2). They
appear the moment there is state to render.

## 1a. From the Planner's AUDIT.md to state

`AUDIT.md` is prose a human reads and rules on. Loading it into the canonical
record is one explicit operator command, run once after the Planner session:

```bash
xcheck --project "$TARGET" migrate
```

It refuses rather than drops: a pass line that is not a full queue entry, a unit
that resolves to nothing, a checked pass with no canonical coverage report are
each reported and nothing is written. Run it with `--dry-run` first to read the
report. It is never automatic — converting a live audit tree behind the
operator's back is the failure this design exists to prevent — and it is
reversible: the pre-migration bytes are kept in `audit/.migration-backup/`, and
the command prints the three lines that restore them.

After that, machine state changes ONLY through the write verbs
(`xcheck set-status`, `record-fix`, `record-verdict`, `file-finding`, …; the
full list is `xcheck --help` and XCHECK.md §2.1). A hand-edited frontmatter
block or ledger row is refused as drift by every command until somebody
re-renders with `xcheck render-views` — and the edit itself records nothing.

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

Triage has no launch line: read `audit/LEDGER.md` (or `xcheck status`) and
record each `reported` row's decision with one command —

```bash
xcheck set-status F-0012 accepted
```

— using only `accepted`, `rejected` or `deferred`; open the finding file when
the title alone doesn't settle the call. Batch decisions are fine, one
invocation each. Rejecting a CF also reverts every finding in its `members:`
list to `accepted` — one `set-status` per member. There is no sync step and
nothing to carry into the finding files afterwards: the verb writes the state
and re-renders the mirrors, so the decision IS the record the moment it
returns.

The Auditor/Remediator/Verifier charter text above is an example — replace
the pass ID or finding-ID range with the actual scope before pasting;
Planner's charter is fixed and needs no edits. A session handed a one-liner
with no charter should refuse to start (XCHECK.md §4 rule 1).

An Auditor charter is not only a fresh pass — a dispute round is a valid
charter too, but it still needs an exact scope and stop condition (§4 rule 1):
name the finding IDs currently in status `disputed` and cap the work at one
written round of objection each, e.g. `Read audit/XCHECK.md and audit/AUDIT.md.
Role: Auditor. Charter: dispute round for F-0007, F-0013 — one written round of
objection each (see LEDGER).` A bare "respond to disputed findings" names no ID,
range, or stop condition and is not a charter; it must not start a session.

## 3. A typical cycle

1. Install (§1) into the target project.
2. Planner session produces `AUDIT.md` — norms catalog, dimensions, unit map, pass queue.
3. Human skims the pass queue and trims it: drop or reorder passes; cheap, no gate, just judgment. Then `xcheck migrate` (§1a) loads it into `audit/state.json` — after this point the queue is amended with `xcheck queue-pass`, not by editing `AUDIT.md`.
4. Auditor sessions work the queue one charter at a time, filing findings and a pass report per charter.
5. Triage: human sets every `reported` finding to `accepted`, `rejected`, or `deferred`.
6. Remediator sessions take batches of `accepted` findings through validate → census → plan → fix → self-check.
7. Verifier sessions take the `fixed` batch and give each finding a `closed` or `reopened` verdict.
8. Repeat from step 4 with the next queued pass. `reopened` findings re-enter the cycle at step 6 (Remediator), not step 4; `reopen_limit` consecutive `reopened` verdicts on one finding (2 by default) sets `⚠ needs-human` on the record and a human decides what happens to it next — the automatic cycle does not resume until somebody names themselves with `xcheck set-status <ID> planned --retake human:<who>`, which resets the attempt count in the same transition.
9. The audit is complete when the AUDIT.md pass queue is empty and every ledger row is terminal (closed, rejected, withdrawn, obsolete, or superseded-by-class with a closed CF). One special case: a remainder of only `deferred` rows is complete *with* deferred debt — the automatic cycle is exhausted, but `deferred` is not terminal, so those rows stay as re-triage debt until you accept or reject them.

## 4. Rules of the road

- One active writing session per project at a time — no concurrent agents editing `audit/`.
- The human is the courier: relay the baton between sessions; agents do not hand off to each other directly.
- Findings, plans, and reports are written in the operator's working language (pick one per audit and stay consistent); evidence quotes stay verbatim in the material's own language.
- The Verifier is never the fixer — a different agent, or at minimum a different fresh session of the same agent.

## 5. Launchers (optional)

Instead of pasting one-liners by hand, install the thin launchers — `$xcheck-plan`, `$xcheck-audit`, `$xcheck-triage`, `$xcheck-remediate`, `$xcheck-verify`, `$xcheck-status` in Codex, or the corresponding `/xcheck-*` commands in Claude Code and OpenCode. See `launchers/README.md` (`launchers/install-launchers.sh`). They auto-pick the next charter and delegate the rules to `audit/XCHECK.md` — not pure delegation, though, and not zero-normative: for safety and ergonomics some of the methodology's rules are duplicated in the skills, so re-run the installer after *any* change to the methodology.

## 6. Field notes (pilot-tested)

- These notes come from `uncontained-direct` runs — sessions a human started in their own agent, with no xcheck sandbox profile, timeout, environment allowlist, log redaction, process-group kill or courier review of the diff. `xcheck next` (`orchestrated`) is the mode that enforces those; `SECURITY.md` scopes every control to one of the two.
- Codex CLI sessions run in a sandbox that keeps `.git` read-only: Verifier sessions may be unable to commit their verdict updates. That is Codex's own containment, not xcheck's. The courier finishes those commits — check `git status` after each Codex session.
- Resist launching extra writing sessions (micro-passes, side tasks) while an agent session is active — §4 rule 8 exists because concurrent writers corrupt each other's view. Pilot 1 got away with it three times on luck and worktree isolation.
- On security-sensitive dimensions (memory-safety, UB, exploitability), a provider's content moderation may block an agent from doing the defensive review — OpenAI/Codex flagged first-party memory-safety auditing as "cybersecurity risk" and aborted mid-session in pilot 2, disclaimers notwithstanding. The methodology is agent-agnostic: switch that dimension's Auditor/Verifier to an agent that isn't blocked (one line in `orchestrator.conf`, or a different launcher). Verifier ≠ fixer still holds via distinct fresh sessions; note the reduced cross-agent independence in the report.
