---
name: xcheck-status
description: Show the read-only xcheck audit dashboard - pass queue progress, findings by status, and whose turn is next. Use when the user invokes $xcheck-status or /xcheck-status, or says "audit status" or "where are we in the audit".
---

# xcheck launcher — Status (read-only)

Reporting only. This session **writes nothing** — it is not a writing session in the §4 rule 8 sense, so it also does not fix ledger drift; it reports drift instead.

1. **Preflight.** `audit/XCHECK.md`, `audit/AUDIT.md`, `audit/LEDGER.md` must exist. Missing → say what's missing and stop.
2. Read `audit/XCHECK.md` fully (§4 rules 1/3 — every session reads it before acting; esp. §5, the lifecycle whose status vocabulary and terminality you report in step 3), `audit/AUDIT.md` (pass queue, limits) and `audit/LEDGER.md`. The finding file — not the ledger row — is the canonical status for every state except a pending triage decision (§2), so read each finding file's frontmatter `status:` and reconcile it against its ledger row before reporting drift or any completion verdict. Do NOT trust a ledger row that merely *looks* unambiguous on its own: a lone `closed` row can hide a canonical `reopened` in the file, exactly the drift a dashboard must catch. Reading finding-file frontmatter only where the ledger already looks ambiguous cannot detect drift the ledger conceals.
3. **Report in the human's language:**
   - Pass queue: done/total, per dimension; the next queued pass.
   - Findings by status (counts); list ids explicitly for `⚠ needs-human`, `disputed`, `reopened`.
   - Triage backlog: `reported` count; `deferred` backlog (a debt — not terminal).
   - Drift (ledger vs finding files) from the step-2 reconciliation — report only, with the §2 rule 3 direction that applies (a canonical `reopened`/`accepted` behind a stale terminal ledger row blocks completion).
   - **Whose turn:** one actionable launcher suggestion (e.g. `xcheck-remediate` when accepted findings await remediation, or `xcheck-triage` when the queue is empty and findings are reported).
   - **Termination check:** audit complete = pass queue empty AND every finding TERMINAL by its canonical finding-file status (`closed`, `rejected`, `withdrawn`, `obsolete`, or `superseded-by-class` with a closed CF) — never off the ledger row alone, which can lag behind the file. Special case: a remainder of only `deferred` rows is *complete with deferred debt* — the automatic cycle is exhausted, but `deferred` is not terminal, so it stays as re-triage debt (accept or reject to fully close).

## Lock discipline

Read-only role: do not create `audit/.lock`. If it exists, report it (role, age, pid liveness) as part of the dashboard.
