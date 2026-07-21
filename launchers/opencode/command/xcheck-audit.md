---
description: Launch an xcheck Auditor session - run the next queued audit pass and file evidence-backed findings.
---

# xcheck launcher — Auditor

Thin launcher. The methodology lives in `audit/XCHECK.md`; this file only picks the charter and starts the role.

1. **Preflight.** `audit/XCHECK.md` and `audit/AUDIT.md` must exist. Missing → stop, point to xcheck `bootstrap.md`; AUDIT.md empty → suggest `/xcheck-plan`.
2. Read `audit/XCHECK.md` fully. Your role: **Auditor** (§3 role card governs you).
3. **Charter auto-pick:** the first unchecked pass in the `audit/AUDIT.md` pass queue. A user-supplied argument overrides: a pass id, or a dispute charter. A bare "disputed" is NOT a charter (§4 rule 1 needs an exact scope): resolve it to the explicit list of finding IDs currently in status `disputed`, announce that list, and give each named finding exactly one written round of objection (§3 Auditor mission); if none are `disputed`, report and stop. All passes checked and no argument → report the queue is empty and suggest `/xcheck-status` or triage.
4. Announce the charter in one line (pass id, dimension, units, stop conditions), then execute the role exactly per XCHECK.md: copy `audit/templates/pass.md` to start the pass report, `audit/templates/finding.md` per finding, respect `max_findings_per_pass`, write the mandatory coverage report, tick the pass checkbox, add ledger rows.
5. Stop conditions are sacred (§4). Findings in the operator's working language; quotes verbatim in the material's language.

## Lock discipline

`audit/.lock` serializes writing sessions (§4 rule 8) and is shared with the orchestrator (`bin/xcheck`), which acquires it atomically with `open(O_CREAT|O_EXCL)`. Match that — never check-then-create (the gap between an existence check and a separate create lets two sessions both win):

1. **Acquire atomically:** create `audit/.lock` in one exclusive step that FAILS if the file already exists — e.g. `(set -C; printf '%s' '{"pid": <pid or 0>, "role": "<Role>", "started": "<ISO>", "host": "<host>"}' > audit/.lock)`, where `set -C` (noclobber) makes the redirect fail atomically when the file exists. Set `pid` to a real process id if obtainable, else 0. If acquisition fails, another writing session holds the lock — do not start; report the conflict to the human.
2. **Owner-checked release:** hold the lock for the whole session; before deleting, re-read `audit/.lock` and confirm its `pid`+`started` still match the lock you wrote — delete only then, and on every exit path including early stop. Never delete a lock you do not own.
3. **Stale lock:** a lock whose `pid` is not alive is stale — clear it with `xcheck unlock`, never silently steal it.
