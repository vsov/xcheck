---
key: 0000000000000000 # 16 lowercase hex — construal_key(role, charter); the filename is <key>.md
role: Auditor # the canonical role receiving the charter (Planner | Auditor | Triage | Remediator | Verifier)
charter: # the charter text AS ISSUED, verbatim — the key is derived from role + this text
session: 0000000000000000 # the PRODUCING session's id (16 lowercase hex), i.e. whoever wrote this file
created: YYYY-MM-DD
status: proposed # proposed | admitted | refused
admitted-by: null # the ADMITTING session id, or `envelope:<name>` — never equal to `session` (§4 rule 9)
admitted-at: null # YYYY-MM-DD, set when admitted
envelope: null # which §10 `construal_envelope` covered this, if any
---

<!-- XCHECK.md §4 rule 9. Write this BEFORE any effect on the material, then stop.
     The construal is admitted as EVIDENCE, never as authority: it is what the
     admitter inspects, not what authorizes the work. Never set `status: admitted`
     yourself — a producer that admits its own construal has licensed its own
     misreading, and lint refuses it. All five sections below are required and must
     carry real content; an empty one is a silent gap, not a short answer. -->

## Task frame
<!-- The task in YOUR OWN WORDS, not a restatement of the charter. What do you
     understand you are being asked to change or find, and why? If your words and
     the charter's words diverge, that divergence is the point of this file. -->

## Approach
<!-- How you intend to do it: what you will read, in what order, what you will
     change, what you will run to check yourself. -->

## Assumptions
<!-- What you are taking as given that the charter does not state. Every one of
     these is a place the work can go wrong silently. -->

## Stop conditions
<!-- What ends this session: the charter's own stop conditions, plus any you are
     adding (context budget, a cap, a blocking dependency). -->

## Out of scope
<!-- What you are deliberately NOT doing, including things a reader might assume
     are included. The residue half: state it even when it feels obvious. -->
