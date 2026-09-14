<!-- The remediation plan: the human half of a batch. The machine record — id, the
     findings it covers, its state — lives in audit/state.json and is written by
     `xcheck record-plan RP-NNNN --findings F-...,F-... --body plans/<this file>`.
     Do not add a frontmatter block: nothing reads one here. -->

## Batch validation results
<!-- Remediator, step 1 per finding: validated / disputed / obsolete.
     Disputed and obsolete findings leave the batch here.
     For an accepted CF (§8): validate = re-run the recorded census procedure
     (record it in the CF file's Validation section); the steps below map as
     census = already done (the CF's census IS the scope), plan = the CF's
     Global strategy. -->

## Census results
<!-- Remediator, step 2 per validated finding: pattern formulated, corpus
     searched (procedure recorded). Two branches (§8):
     • < class_threshold instances → fix the instance found AND record every
       sibling in the FINDING FILE (not only here);
     • ≥ class_threshold instances → create CF-XXXX, member findings →
       superseded-by-class, note it here; the batch continues without them. -->

## Fix plan
<!-- Remediator, step 3. Per remaining finding: exact edit
     intended, order, risks (what could break nearby).
     NORM RATIFICATION GATE (§8 rule 8) for any class fix: if a (b)/(c) strategy
     changes a norm, relies on a norm another source contradicts, or must pick a
     side in a norm conflict — STOP after writing the plan and do NOT enter the
     Execution log until the norm owner rules. This is NOT a Triage route (Triage
     writes the status column only, §3): record the stop durably in the CF file —
     frontmatter `blocked: norm-ratification` + `norm-ruling: pending` and a
     `## Norm ruling` section stating the conflict — and wait for the norm owner
     (human, not Triage) to set `norm-ruling` to the winning norm id (e.g. N1 /
     N1-over-N4). The `norm-ruling` field is the fail-closed machine gate; resume
     only once it holds a norm id (§8 rule 8 sync/resume). -->

## Execution log
<!-- Remediator, steps 4–5. What was actually changed (files/sections/
     commits) + self-check result per finding before setting status fixed.
     When setting a finding `fixed`, also fill its `admitted-scope:` and its
     `## Admitted scope` section — BOTH `### Covers` and `### Does not cover`
     (§7; enforced by lint when §10 `scope_typing` is on). If you accepted a
     finding in this batch and cannot execute it, record a `refusal:` on that
     finding with its `## Refusal` prose and leave its `status` untouched (§5) —
     the charter stays in force and the batch continues with the rest. -->
