---
name: xcheck-verify
description: Launch an xcheck Verifier session - give binding verdicts (closed/reopened) on fixed findings. Use when the user says /xcheck-verify, "verify the fixes", "check the fixes".
---

# xcheck launcher — Verifier

Thin launcher. The methodology lives in `audit/XCHECK.md`; this file only picks the charter and starts the role.

1. **Preflight.** `audit/XCHECK.md`, `audit/AUDIT.md`, and `audit/LEDGER.md` must exist. Missing → stop, point to xcheck `bootstrap.md`.
2. Read `audit/XCHECK.md` fully, then `audit/AUDIT.md` (charter limits — `reopen_limit` and any per-project overrides, §10). Your role: **Verifier** (§3 role card governs you). **Hard rule: you must not be the agent or session that produced these fixes.** The default Verifier is Codex precisely because the default Remediator is Claude — if the fixes were made by Claude sessions, tell the user that running verification in Codex preserves cross-agent independence, and that proceeding here is the fallback minimum (a fresh session). Proceed only after saying so.
3. **Charter auto-pick:** every finding and CF in status `fixed`. A user-supplied argument (range) overrides the auto-pick — but only the set of IDs, never the lifecycle gate: every ID must still be in status `fixed` (§5). Drop any non-`fixed` ID from the charter, or stop and list the offending statuses; a range never lets `reported`/`accepted`/`closed` findings into verification. Nothing fixed → report and stop.
4. Announce the charter in one line, then execute the role exactly per XCHECK.md: run each finding's "How to verify the fix" procedure, adversarially inspect the surroundings of each change, re-run the census for CFs expecting zero instances or documented exceptions. Verdicts `closed` or `reopened` with evidence held to the Evidence Standard (§6); `⚠ needs-human` at `reopen_limit`.
5. Your stance: *"Your job is to prove the fix wrong, not to confirm it."* Verdicts in the operator's working language; quotes verbatim in the material's language.

## Lock discipline

`audit/.lock` serializes writing sessions (§4 rule 8) and is shared with the orchestrator (`bin/xcheck`), which acquires it atomically with `open(O_CREAT|O_EXCL)`. Match that — never check-then-create (the gap between an existence check and a separate create lets two sessions both win):

1. **Acquire atomically:** create `audit/.lock` in one exclusive step that FAILS if the file already exists — e.g. `(set -C; printf '%s' '{"pid": <pid or 0>, "role": "<Role>", "started": "<ISO>", "host": "<host>"}' > audit/.lock)`, where `set -C` (noclobber) makes the redirect fail atomically when the file exists. Set `pid` to a real process id if obtainable, else 0. If acquisition fails, another writing session holds the lock — do not start; report the conflict to the human.
2. **Owner-checked release:** hold the lock for the whole session; before deleting, re-read `audit/.lock` and confirm its `pid`+`started` still match the lock you wrote — delete only then, and on every exit path including early stop. Never delete a lock you do not own.
3. **Stale lock:** a lock whose `pid` is not alive is stale — clear it with `xcheck unlock`, never silently steal it.
