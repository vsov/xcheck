---
id: F-XXXX
title: # short title of the defect (operator's working language)
severity: # critical | major | minor | info
dimension: # one dimension key from AUDIT.md
unit: # material unit; YAML list if the finding spans units
status: reported
class: null # CF-XXXX once absorbed by a class finding
attempts: 0 # the Remediator increments this when re-taking a reopened finding
recurrence-of: null # or F-XXXX/CF-XXXX — set when this finding is a fresh recurrence of a TERMINAL ancestor (§3 dedup, §9 rule 8); the ancestor stays terminal
blocked: null # or `norm-ratification` — a planned CF halted at the §8 gate (paired with `norm-ruling`); null for an ordinary finding
norm-ruling: null # §8 rule 8 machine gate on a blocked CF: `pending`, then the winning norm id (e.g. N1 or N1-over-N4) once the norm owner rules; null for an ordinary finding
refusal: null # §5 typed refusal — one of out-of-competence | blocked-dependency | charter-ambiguous | norm-conflict | material-missing | cost-exceeded when a role ACCEPTED this charter and cannot execute it; fill `## Refusal` too, and leave `status` untouched
admitted-scope: null # §7 — the routes/inputs/call sites this fix's guarantee covers (a scalar or a flat list); required with `## Admitted scope` when the Remediator sets `fixed` and §10 `scope_typing` is on
pass: P-XX # the audit pass that produced this finding
updated: YYYY-MM-DD
---

## Evidence
<!-- Auditor. Operator's working language; quotes verbatim in the material's language.
     Required by XCHECK.md §6: (1) character-exact quote, findable by search;
     (2) locator: code — file:line + symbol; text — file + section;
     (3) the norm this material violates, with its own quote
         (style contract / spec / ADR / sourced fact / contradicting passage).
     No norm → severity at most info.
     ABSENCE defect (§6 rule 7 — a required twin is missing, so there is no
     defective line to quote): (1) quote the PRESENT anchor that demands the
     twin (the norm's promise, or the unpaired element — `open` w/o `close`, TOC
     line w/o section, spec clause w/o implementing unit); (2) the norm or
     pairing rule that makes the twin mandatory; (3) the expected twin locator
     plus a reproducible twin-search whose result is EMPTY. No present anchor to
     quote → not a finding: route to the Planner as a norm-gap note. -->

## Why this is a defect
<!-- Auditor. 1–3 sentences. -->

## How to verify the fix
<!-- Auditor, at creation time. A reproducible procedure (search command,
     section to re-read, check to run) + the expected result. State the result's
     polarity: a presence defect is fixed when the quote-search no longer finds
     the defect; an absence defect (§6 rule 7) when the twin-search now RETURNS
     the once-missing artifact. -->

## Validation
<!-- Remediator, step 1. validated / disputed / obsolete + the grounds.
     Quote re-found? Problem real? -->

## Refusal
<!-- Any role that ACCEPTED this charter and cannot execute it (§5). Set the
     `refusal:` frontmatter field to one reason code, then state HERE what is
     actually missing and what would unblock it — a bare code names a category
     and carries no obstacle forward. Do NOT change `status`: a refusal is about
     this attempt, not about the finding; the charter stays in force and the next
     session inherits both the work and these reasons. Not a dispute (that
     contests the finding) and not a rejection (that is Triage's call). Leave
     empty otherwise. -->

## Objection
<!-- Auditor — only when Validation = disputed (§9 Agent dispute). Exactly ONE
     written round: why the finding stands, evidence per §6. Append-only; then
     the human decides. No further agent ping-pong. Leave empty otherwise. -->

## Remediation
<!-- Remediator. Link to RP-XXXX, what was changed and where
     (files/sections; commit IDs if the project uses git). -->

## Admitted scope
<!-- Remediator, together with `status: fixed` (§7; enforced when §10 `scope_typing`
     is on). Declare what this fix's guarantee covers and — mandatory — what it does
     not. BOTH subsections are required: a coverage claim with no stated limit reads
     as complete while admitting nothing about its edges, which is the promise-width
     defect §7 names. The Verifier checks demanded ⊆ admitted: any claim the material
     itself makes (a docstring, a message, a README line) that reaches past what is
     admitted here is a promise-width defect and reopens the finding. -->

### Covers
<!-- Which routes, inputs, call sites, units. Be specific enough to be checkable. -->

### Does not cover
<!-- The residue. What a reader could reasonably assume is fixed and is not:
     other call sites of the same claim, adjacent inputs, related units,
     the general case behind the reported instance. "Nothing" is not an answer
     unless the fix genuinely poisons the claim at every route (§7). -->

## Verification
<!-- Verifier — never the fixer. Verdict (closed / reopened) + how the
     "How to verify" procedure was executed + what was inspected around
     the change for collateral damage. Evidence standard applies to
     reopening: exact quote of the surviving/new defect. -->
