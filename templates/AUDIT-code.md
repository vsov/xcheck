# AUDIT — <project name> (codebase)

<!-- Filled by the Planner in one session (XCHECK.md §3). Sections 1–3 are
     inventory; 4–5 are the working queue. Findings/reports in the operator's working language. -->

## 1. Norms catalog
<!-- Every norm source with path + one-line scope. For a codebase
     typically: spec/design docs, ADRs, CONTEXT.md, README claims,
     invariant lists, benchmark reports.
     Do not read norms fully — catalog them.
     Keep the column names below: `xcheck migrate` finds each column by
     its header, and a plan that renames one does not load. Ids are N1,
     N2, …, and each dimension's `norm source` in §2 names them. -->

| id | source | scope |
|---|---|---|

## 2. Dimensions
<!-- Pick from the library below; add project-specific ones. Every
     dimension MUST name its norm source from §1. Delete unused ones. -->

| key | what it catches | norm source |
|---|---|---|
| spec-conformance | code contradicts spec/ADR/design doc | spec, ADRs |
| claims-honesty | README/report claims not backed by code or measurements | README, benchmark reports |
| dead-code | unreachable/unused code, stale feature flags | internal |
| memory-safety | UB, leaks, unchecked unsafe blocks | language rules, project invariants |
| test-gaps | critical paths without coverage; tests that assert nothing | test suite vs code map |
| doc-drift | comments/docs describing code that changed | docs vs code |
| invariants | violations of documented project invariants | CONTEXT.md, invariant lists |

## 3. Unit map
<!-- Natural code units (modules/subsystems) with kloc estimates.
     Pass-size rule (XCHECK.md §4 rule 4): split so one pass reads
     attentively in one session (heuristic: ≤2 kloc) — split large units,
     batch small ones.
     Keep the column names below: `xcheck migrate` finds each column by
     its header, and a plan that renames `unit`, `material` or
     `responsibility` does not load. A unit id such as U01 is what §4 and
     every finding name the unit by. -->

| unit | material | approx. size | responsibility |
|---|---|---|---|

## 4. Pass queue
<!-- The dimension × unit-batch matrix, prioritized: critical dimensions
     first. Cross-unit dimensions — ones whose norm is consistency
     across units, not any single one — get dedicated passes over unit
     PAIRS/TRIPLES: pick pairs that share subject matter. One checkbox
     per pass: -->
- [ ] P-01 — <dimension> × <units>: <charter one-liner>; stop: <stop conditions>
<!-- This queue is the PLAN a human reads and rules on. It becomes machine state
     once, when the operator runs `xcheck migrate` after this file is finished:
     from then on the queue lives in audit/state.json, `xcheck next` dispatches
     from there, and a further pass is appended with
     `xcheck queue-pass P-NN --dimension <key> --units U01,U02 --charter "..." --stop "..."`
     rather than by adding a line here. Every entry must carry the FULL form above
     — id, dimension, units, charter AND `; stop:` — because a line the converter
     cannot read is refused, not skipped: a dropped pass is a queue that reports
     itself finished. -->

## 5. Limits
<!-- Copy XCHECK.md §10 defaults; override per project if needed. -->
max_findings_per_pass: 15
remediation_batch_size: 8
class_threshold: 3
reopen_limit: 2

## 6. Graph assistance (optional)
<!-- If graphify-out/ exists or the graphify skill is available:
     communities → module candidates; god nodes → hub files audited
     first; neighbors → verifier blast radius.
     Census: graph queries supplement grep (renames and aliased calls defeat plain grep).
     Without a graph: same steps manually. -->
