---
name: xcheck-status
description: Show the read-only xcheck audit dashboard - pass queue progress, findings by status, and whose turn is next. Use when the user invokes $xcheck-status or /xcheck-status, or says "audit status" or "where are we in the audit".
---

# xcheck launcher — Status (read-only)

Reporting only. This session **writes nothing** — it is not a writing session in the §4 rule 8 sense, so it also does not fix ledger drift; it reports drift instead.

1. **Preflight.** `audit/XCHECK.md`, `audit/AUDIT.md`, `audit/LEDGER.md` must exist. Missing → say what's missing and stop.
2. Read `audit/AUDIT.md` (pass queue, limits) and `audit/LEDGER.md`. Open finding-file frontmatter only where a ledger row is ambiguous.
3. **Report in the human's language:**
   - Pass queue: done/total, per dimension; the next queued pass.
   - Findings by status (counts); list ids explicitly for `⚠ needs-human`, `disputed`, `reopened`.
   - Triage backlog: `reported` count; `deferred` backlog (a debt — not terminal).
   - Drift, if noticed (ledger vs finding files) — report only, with the §2 rule 3 direction that applies.
   - **Whose turn:** one actionable launcher suggestion (e.g. `xcheck-remediate` when accepted findings await remediation, or `xcheck-triage` when the queue is empty and findings are reported).
   - **Termination check:** audit complete = pass queue empty AND every ledger row terminal (`closed`, `rejected`, `withdrawn`, `obsolete`, or `superseded-by-class` with a closed CF).

## Lock discipline

Read-only role: do not create `audit/.lock`. If it exists, report it (role, age, pid liveness) as part of the dashboard.
