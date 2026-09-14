---
name: xcheck-status
description: Show the read-only xcheck audit dashboard - pass queue progress, findings by status, and whose turn is next. Use when the user invokes $xcheck-status or /xcheck-status, or says "audit status" or "where are we in the audit".
---

# xcheck launcher — Status (read-only)

**Launch mode: `uncontained-direct`.** None of the runner controls are in force here, because xcheck is not in the process: no sandbox profile, no hard timeout, no environment allowlist, no log redaction, no process-group kill, no courier review of the diff, no invocation envelope and no session receipt. The first six mean containment is whatever your agent platform provides — xcheck does not know what that is and cannot report it. The last two mean this session leaves nothing machine-checkable behind: no record of which policy, charter and executable were in force when it started, and no attestation afterwards that what it did matched them. A human vouches for this work; the tool does not. The two that would still have mattered are named above and are gone anyway: nothing here is running under an environment allowlist, and nothing redacts this transcript. **Routed as far as it can be:** every state read here goes through `xcheck status` and `xcheck metrics`, the tool's own validated reader, so this skill never parses machine state itself. That is the one launcher whose work genuinely passes through xcheck code.

**Exempt, and only this one:** this launcher stays direct because it is read-only — it writes nothing and moves no machine state, so there is no diff for a courier to review and no effect for an envelope or a receipt to attest.

Reporting only. This session **writes nothing** — it is not a writing session in the §4 rule 8 sense, so it also does not fix ledger drift; it reports drift instead.

<!-- xcheck:style:begin -->
## How to talk to the person running this audit

Everything in this section is about wording, and only about wording. It applies to every
reply a human will read in this session.

**Compression is off.** If `caveman` is running, or any other output-compression mode is
running, it does not apply here. A mode like that stays switched on until it hears the
exact phrase that releases it, so here is the phrase: **normal mode**. Write ordinary
prose — whole sentences, articles left in, nothing telegraphic. This holds for every reply
in the session, not only the first one.

**ELI5 is on.** Your reader is intelligent and brand new to this vocabulary. Take the
trouble to be understood:

- The first time a term of art appears, say what it means in one short clause, then use it
  freely afterwards.
- Short sentences, one idea in each.
- Say what the person should do next, and where they should do it.
- Reply in whatever language the person wrote to you in.
- A concrete example beats an abstract rule.
- When something has gone wrong, say plainly what happened and what it means for them.

**Four things are reproduced exactly, and never reworded.** Explaining what one of them
means is welcome. Replacing one with your own phrasing is not, because an audit trail is
worth exactly what its wording is worth:

1. **Quoted evidence** — any line lifted out of a file or a transcript, together with the
   path and line number it came from.
2. **Finding ids** — `F-0042`, `CF-0003`, `RP-0007`, and every id shaped like them.
3. **§5 statuses** — the words §5 uses for where a finding stands, spelled the way §5
   spells them.
4. **Copy-paste commands** — anything the person is meant to run, character for character
   as it must be typed.

Plain wording is the goal everywhere else. These four are the exception, and they are the
exception because someone will later have to check them against the ledger.
<!-- xcheck:style:end -->

1. **Preflight.** `audit/XCHECK.md`, `audit/AUDIT.md`, `audit/LEDGER.md` must exist. Missing → say what's missing and stop.
2. Read `audit/XCHECK.md` fully (§4 rules 1/3 — every session reads it before acting; esp. §5, the lifecycle whose status vocabulary and terminality you report in step 3) and `audit/AUDIT.md` (the plan a human rules on). For state, run `xcheck status` and `xcheck metrics` rather than reading `LEDGER.md`: both are computed from `audit/state.json`, the one canonical record. Do NOT reconstruct state by reading ledger rows and frontmatter blocks and comparing them — they are generated mirrors of that same document (§2), so there is nothing to reconcile, and a command that runs at all has already refused any tree where a mirror disagrees. If a command refuses for drift, report the refusal verbatim; do not re-render it away.
3. **Report in the human's language:**
   - Pass queue: done/total, per dimension; the next queued pass.
   - Findings by status (counts); list ids explicitly for `⚠ needs-human`, `disputed`, `reopened`.
   - Triage backlog: `reported` count; `deferred` backlog (a debt — not terminal).
   - Drift, if any command refused for it — report the refusal verbatim and stop there. There is no reconciliation to report: `state.json` is the record, the ledger row and the frontmatter block are rendered from it (§2 rule 3), and a disagreement is a view that needs re-rendering by a writing session, not two sources to weigh against each other.
   - **Whose turn:** one actionable launcher suggestion (e.g. `xcheck-remediate` when accepted findings await remediation, or `xcheck-triage` when the queue is empty and findings are reported).
   - **Termination check:** audit complete = pass queue empty AND every finding TERMINAL by its status in `xcheck status` (`closed`, `rejected`, `withdrawn`, `obsolete`, or `superseded-by-class` with a closed CF) — that command reads `state.json`, the one canonical record. Never judge it off a ledger row or a frontmatter block: both are rendered views, and either can lag. Special case: a remainder of only `deferred` rows is *complete with deferred debt* — the automatic cycle is exhausted, but `deferred` is not terminal, so it stays as re-triage debt (accept or reject to fully close).

## Lock discipline

Read-only role: do not create `audit/.lock`. If it exists, report it (role, age, pid liveness) as part of the dashboard.
