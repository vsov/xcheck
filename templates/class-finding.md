---
id: CF-XXXX
title: # short title of the systemic defect (operator's working language)
severity: # highest severity among members
dimension: # dimension key from AUDIT.md
unit: [] # YAML list of every unit the class spans (from the census); canonical field per §2
status: reported
class: null # a CF is itself the class — it is not absorbed by another class
members: [] # F-XXXX ids absorbed by this class (they get status superseded-by-class)
attempts: 0
recurrence-of: null # or F-XXXX/CF-XXXX — set when this class is itself a fresh recurrence of a TERMINAL ancestor (§3 dedup, §9 rule 8); the ancestor stays terminal
blocked: null # or `norm-ratification` — set when this planned CF halts at the §8 gate (paired with `norm-ruling`)
norm-ruling: null # §8 rule 8 machine gate: `pending` while blocked; the winning norm id (e.g. N1 or N1-over-N4) once the norm owner rules — fail-closed, this field is what lifts the gate
pass: P-XX # the pass whose finding triggered the census escalation (— if surfaced purely in remediation)
created-by: RP-XXXX # the remediation session whose census escalated
updated: YYYY-MM-DD
---

## Pattern
<!-- Remediator. Exact definition of the recurring defect + the reproducible
     search procedure used (grep/AST query/graph query/targeted reading list).
     The Verifier will re-run exactly this procedure. -->

## Census
<!-- Remediator. ALL instances with locators per XCHECK.md §6 — including
     ones no audit pass reported. This list is the fix's scope. -->

## Validation
<!-- Remediator, step 1 for an accepted CF (§8): re-run the Pattern search
     procedure to confirm the class is real and the census still holds
     (validated / disputed / obsolete + the grounds). This is the accepted-CF
     "validate" step and MUST precede remediation. -->

## Objection
<!-- Auditor — only when the accepted-CF Validation = disputed (§9 Agent
     dispute). Exactly ONE written round: why the class stands, evidence per §6.
     Append-only; then the human decides. No further agent ping-pong. Leave
     empty otherwise. -->

## Root cause
<!-- Remediator. Usually: a norm that is missing, ambiguous, or was
     systematically violated. Name the norm document. -->

## Global strategy
<!-- Remediator. The chosen rung and why:
     (a) fix all census instances;
     (b) (a) + fix the norm so the class cannot recur;
     (c) (b) + automated guard (grep check, lint rule, style script).
     Norm/guard changes are part of the fix.
     NORM RATIFICATION GATE (§8 rule 8): if a (b)/(c) fix changes a norm, relies
     on a norm another normative source contradicts, or must pick a side in any
     norm conflict — STOP after writing this strategy. Set frontmatter
     `blocked: norm-ratification` AND `norm-ruling: pending`, fill `## Norm ruling`
     with the conflict + candidate sides, and do NOT execute until the norm owner
     rules. Documenting a conflict and proceeding anyway is a protocol violation
     for class fixes: a class fix in the wrong direction multiplies one error
     across the corpus. -->

## Norm ruling
<!-- Norm owner (human), only when `blocked: norm-ratification` is set (§8 rule
     8). The MACHINE GATE is the `norm-ruling` frontmatter field, not this prose:
     it stays `pending` until decided, then the norm owner sets it to the winning
     norm id (e.g. `norm-ruling: N1` or `norm-ruling: N1-over-N4`) — fail-closed,
     any absent/pending/unrecognized value keeps the CF stopped. This section
     records the human-readable reasoning and any plan amendment. NOT a Triage
     write — Triage touches the status column only. Once `norm-ruling` holds a
     norm id the Remediator clears `blocked:` and executes per it (or re-plans
     per the winning norm if the ruling rejects the direction — the Remediator
     never sets `withdrawn`; §5 owns it to the disputed resolution). -->

## Remediation
<!-- Remediator. What was changed, instance by instance + norm/guard edits. -->

## Verification
<!-- Verifier. Re-run the Pattern search procedure. Expected: the polarity's
     clean result (§8 rule 5) — zero instances for a presence class, zero
     orphans (every anchor's twin now present) for an absence class — or an
     explicit documented-exceptions list. "Fixed 12 of 15" (or "created 12 of
     15") = reopened. -->
