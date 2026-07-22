---
name: xcheck-remediate
description: Launch an xcheck Remediator session - validate, census, plan and fix the next batch of accepted findings. Use when the user invokes $xcheck-remediate or /xcheck-remediate, says "fix the findings" or "remediate", optionally with a findings range (e.g. F-0012..F-0019) or a CF id.
---

# xcheck launcher — Remediator

Thin launcher. The methodology lives in `audit/XCHECK.md`; this file only picks the charter and starts the role.

1. **Preflight.** `audit/XCHECK.md`, `audit/AUDIT.md`, and `audit/LEDGER.md` must exist. Missing → stop, point to xcheck `bootstrap.md`.
2. Read `audit/XCHECK.md` fully, then `audit/AUDIT.md` (take `remediation_batch_size` and any per-project norm/limit overrides from there, §10). Your role: **Remediator** (§3 role card governs you — including the mandatory in-session sequence: validate → census → plan → fix → self-check; no step skipped or reordered).
3. **Sync on contact:** for every finding you pick up, first apply any pending triage decision from the ledger to the finding file per §2 rule 3.
4. **Charter auto-pick (resume unfinished work first):** in priority order — (1) resume in-flight remediation: any `reopened` finding (it returns to you, §5), any finding left `validated` or `planned` by an interrupted session, or an open RP with unfinished members; (2) else an `accepted` CF (oldest first) as a batch of its own; (3) else up to `remediation_batch_size` `accepted` findings in id order not already covered by an open RP. A user-supplied argument (range or CF id) overrides. Nothing to resume AND nothing accepted → report and suggest the `xcheck-triage` or `xcheck-verify` launcher.
5. Announce the charter in one line, then execute the role exactly per XCHECK.md: copy `audit/templates/plan.md` for the RP (and `class-finding.md` if a census escalates), fix the material, self-check, set statuses, update ledger. If the project uses git, commit with finding ids in the message.
6. Findings/plans in the operator's working language; quotes verbatim in the material's language.

## Lock discipline

`audit/.lock` serializes writing sessions (§4 rule 8) and is shared with the orchestrator (`bin/xcheck`), which acquires it atomically with `open(O_CREAT|O_EXCL)`. Match that — never check-then-create (the gap between an existence check and a separate create lets two sessions both win):

1. **Acquire atomically:** create `audit/.lock` in one exclusive step that FAILS if the file already exists — e.g. `(set -C; printf '%s' '{"pid": <pid or 0>, "role": "<Role>", "started": "<ISO>", "host": "<host>"}' > audit/.lock)`, where `set -C` (noclobber) makes the redirect fail atomically when the file exists. Set `pid` to a real process id if obtainable, else 0. If acquisition fails, another writing session holds the lock — do not start; report the conflict to the human.
2. **Owner-checked release:** hold the lock for the whole session; before deleting, re-read `audit/.lock` and confirm its `pid`+`started` still match the lock you wrote — delete only then, and on every exit path including early stop. Never delete a lock you do not own.
3. **Stale lock:** a lock whose `pid` is not alive is stale — clear it with `xcheck unlock`, never silently steal it.
