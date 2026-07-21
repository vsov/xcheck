# AUDIT — <project name> (text corpus)

<!-- Filled by the Planner in one session (XCHECK.md §3). Sections 1–3 are
     inventory; 4–5 are the working queue. Findings/reports in the operator's working language. -->

## 1. Norms catalog
<!-- Every norm source with path + one-line scope. For a text corpus
     typically: STYLE contracts, terminology glossaries, CONTEXT.md,
     table of contents / structural promises, cited-facts sources.
     Do not read norms fully — catalog them. -->

## 2. Dimensions
<!-- Pick from the library below; add project-specific ones. Every
     dimension MUST name its norm source from §1. Delete unused ones. -->

| key | what it catches | norm source |
|---|---|---|
| facts | wrong numbers, dates, formulas, misattributed claims | cited-facts sources |
| terminology | one concept named differently across units; term misuse | glossary / STYLE |
| contradictions | unit A asserts X, unit B asserts not-X | internal consistency (both passages quoted) |
| style | violations of the project's STYLE contract | STYLE contract |
| repetition | self-plagiarism, duplicated passages | internal |
| structure | promised in TOC/intro but missing; orphan sections | TOC / intro |
| currency | stale dates, versions, regulations | external sources |

## 3. Unit map
<!-- Natural text units (files/sections) with size estimates. Pass-size
     rule (XCHECK.md §4 rule 4): split so one pass reads attentively in one
     session (heuristic: ≤3 units) — split large units, batch small ones. -->

## 4. Pass queue
<!-- The dimension × unit-batch matrix, prioritized: critical dimensions
     first. Cross-unit dimensions — ones whose norm is consistency
     across units, not any single one — get dedicated passes over unit
     PAIRS/TRIPLES: pick pairs that share subject matter. One checkbox
     per pass: -->
- [ ] P-01 — <dimension> × <units>: <charter one-liner, stop conditions>

## 5. Limits
<!-- Copy XCHECK.md §10 defaults; override per project if needed. -->
max_findings_per_pass: 15
remediation_batch_size: 8
class_threshold: 3
reopen_limit: 2

## 6. Graph assistance (optional)
<!-- If graphify-out/ exists or the graphify skill is available:
     communities → unit candidates; god nodes → audit hubs first;
     edge map → which unit pairs need contradiction passes.
     Verifier: graph neighbors of a changed unit = blast radius to inspect.
     Census: graph queries supplement grep (text paraphrases defeat grep).
     Without a graph: same steps manually. -->
