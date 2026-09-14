<!-- The pass REPORT: the human half of a pass. The machine record — id,
     dimension, units, coverage, findings, done — lives in audit/state.json and is
     written by `xcheck record-coverage P-NN --report passes/<this file> --findings F-...`,
     which is also what marks the pass done. Do not add a frontmatter block: nothing
     reads one here, so it would be a field that looks recorded and is not. -->

## Charter
<!-- The Auditor creates this file at session start by copying
     templates/pass.md and pasting the charter from the AUDIT.md queue:
     scope + stop conditions (max_findings_per_pass, unit list). -->

## Coverage
<!-- Auditor, mandatory (XCHECK.md §4 rule 5). Two explicit lists:
     COVERED: units/aspects actually examined attentively.
     NOT COVERED: everything skipped or cut off + why. Silent gaps are
     forbidden; an interrupted session still writes this section, else
     the pass counts as not done. -->

## Findings
<!-- Auditor. One line per finding: id — title. A clean pass writes
     "No findings" — that is a valid result. -->

## Handoff
<!-- Auditor. Leftover scope → new pass added to the AUDIT.md
     queue (id here). Observations that are not evidence-grade findings. -->
