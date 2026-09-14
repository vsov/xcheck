# XCHECK — Cross-Agent Audit & Remediation Methodology

version: 0.9.5

Self-contained. If you are an agent reading this inside a project's `audit/`
directory, this file plus `AUDIT.md` plus your charter is everything you need.
Do not read the whole project "for context" — the Session Protocol (§4) tells
you exactly what to read.

Language rule: this methodology and all templates are English. Findings,
plans, and reports are written in the operator's working language — pick one
per audit and stay consistent. Evidence quotes are always verbatim
in the original language of the material.

The working language covers **prose only**. Every structural token stays in its
canonical English form regardless of the operator's language: frontmatter keys
and their enumerated values (§2), section headings taken from a template, the
`COVERED:` / `NOT COVERED:` coverage markers (§4 rule 5), status names (§5), and
LEDGER column headers. These are read by tooling, not by people: a translated
marker is invisible to the check that depends on it, and the artifact then
carries a silent gap while looking complete.

## 1. System Overview

The methodology runs one cycle, repeatedly, over a project of any size: `audit → human triage → remediate → verify`. An Auditor finds defects and files them as evidence-backed findings. A human triages them. A Remediator fixes the accepted ones. A Verifier gives each fix a binding verdict. Findings that survive verification close; findings that don't reopen and go back to remediation.

The cycle exists because the obvious alternative — one long free-form agent session working through a large project — fails predictably. Long sessions lose the thread: they forget earlier decisions, invent particulars they never actually checked, and hallucinate both findings and fixes. This methodology substitutes three concrete mechanisms for confidence in an agent's memory: narrow chartered sessions that never run long enough to lose the thread, findings anchored to evidence that can be mechanically re-checked, and cross-agent verification where the fixer is never the judge.

Architecture, in short: a findings ledger plus a finding state machine (§5) is the skeleton — durable, file-based state that outlives any single session. Per-session narrow charters (§4) are the working discipline — what makes each individual session reliable even though it starts from a blank context every time.

Universality is a property of one boundary, and it is load-bearing: *"XCHECK.md defines mechanics and knows nothing about domains; AUDIT.md instantiates the methodology for one concrete project."* Everything in this file must hold for a project made of code and a project made of text, unchanged. Anything that depends on what kind of material is being audited belongs in AUDIT.md, not here.

## 2. Artifacts

A **unit** is one addressable piece of the material — one file, or one section of continuous text. A **dimension** is one audit angle, always backed by a norm (§6). AUDIT.md defines both concretely for a given project; this file only assumes they exist.

The methodology's state lives entirely in the `audit/` directory of the audited project:

```
audit/
  XCHECK.md                    # copy of this file — the audit is self-contained
  AUDIT.md                     # charter: dimensions, unit map, pass queue, norms, limits
  LEDGER.md                    # index: one row per finding
  templates/                   # artifact skeletons (finding, class-finding, pass, plan, construal) — copy the matching one when creating a new artifact file
  construals/
    <key>.md                   # one construal per (role, charter) — §4 rule 9; empty or absent unless §10 `construal_gate` is used
  findings/
    F-0001-<slug>.md           # one finding, one file, full biography inside
    CF-0001-<slug>.md          # one class finding
  passes/
    P-01-<dimension>.md        # one pass report: what was covered, what was not
  plans/
    RP-0001.md                 # remediation plan for one batch of findings
```

IDs: `F-NNNN`, `CF-NNNN`, `RP-NNNN` are 4 digits. `P-NN` is 2 digits. Slugs: ASCII,
lowercase, hyphenated English gist of the title (titles themselves may be in the operator's working language).

**`audit/state.json` is the canonical record. `LEDGER.md` and every finding's
frontmatter block are GENERATED MIRRORS of it.**

A mirror is fine; a second authority is not. Machine state — statuses, limits,
queue, class membership, pass completion, construals, plans — lives in one JSON
document with one strict schema, read through one boundary and written through
the verbs of §2.1. Markdown carries the human half: the evidence body, the
argument, the quote, the ruling. Those are what an audit *is*, and no tool
rewrites them.

Never hand-edit a frontmatter block or a `LEDGER.md` row. The edit does not
change anything — and because it does not, every command refuses to run on the
tree until the disagreement is resolved, naming the file, the line and both
values. Re-render with `xcheck render-views` (your edit is discarded,
deliberately) or put the change into the state document through a verb. An edit
is never silently accepted and never silently dropped: both decide for you.

The frontmatter block a finding carries, rendered from its record:

```yaml
id: F-0001            # or CF-0001 for a class finding
title: <short title>
severity: critical | major | minor | info
dimension: <dimension slug>
unit: <unit path>              # a YAML list for a cross-unit finding
status: reported
class: null | CF-NNNN          # which class finding, if any, absorbed this one
attempts: 0                    # incremented on each reopened cycle
recurrence-of: null            # or F-NNNN/CF-NNNN — this finding is a fresh recurrence of a terminal ancestor (§3 dedup, §9 rule 8)
blocked: null                  # or `norm-ratification` — a planned CF halted at the §8 gate (paired with `norm-ruling`)
norm-ruling: null              # §8 rule 8 machine gate on a blocked CF: `pending`, then the winning norm id (e.g. N1 or N1-over-N4) once the norm owner rules
refusal: null | out-of-competence | blocked-dependency | charter-ambiguous | norm-conflict | material-missing | cost-exceeded   # §5: this role accepted the charter and cannot execute it; the reason is carried forward, the status is untouched
admitted-scope: null           # §7: positive half only — routes/inputs/call sites this fix's guarantee covers (scalar or flat list); the mandatory residue lives in the finding's `## Admitted scope` / `### Does not cover` section; required with that section on a `fixed` finding when §10 `scope_typing` is on
pass: P-01                     # which pass discovered it
updated: <date>
```

A class finding (`CF-NNNN`) carries one additional canonical field, absent on an ordinary finding — the record of its membership that §3 triage reversion and §8 census/verification both read:

```yaml
members: [F-NNNN, ...]         # CF-NNNN ONLY: a YAML list of the canonical F-NNNN/CF-NNNN finding ids this class absorbs (§8 rule 4); null/absent on an ordinary finding. §3 ("every finding in its `members:` list") and §8 rules 4–5 read exactly this form.
```

`LEDGER.md` rows follow one fixed format: `| id | title | severity | status | next | updated |`. `next` names whichever role or the human is expected to act on the finding next: a canonical role name, `human`, or `—` for terminal states. `⚠ needs-human` appears in the `next` column when `reopen_limit` consecutive reopenings are reached — the flag is set by the `record-verdict` that causes it and rendered from state, not appended by whoever reads the row later.

Ownership rules:

1. `audit/state.json` is the single source of truth for machine state. There is no second copy to reconcile, so there is no reconciliation rule — the whole "the file wins except for pending triage, where the ledger wins" apparatus is gone, along with the class of drift it existed to manage.
2. A finding file is the single source of truth for that finding's EVIDENCE. Everything argued about it — the quote, the place, the norm, the validation, the plan, the verdict's reasoning — lives in that one file's body, below the generated block.
3. `LEDGER.md` and the frontmatter blocks are views. They are rendered from the state document and never read back as authority. `next` is not stored at all: it is derived from `status` through one function, so a row and a finding can no longer name different owners.

### 2.1 Write verbs — the only way machine state changes

Every change to the record goes through `bin/xcheck`. A verb validates the
transition against the current state, writes `state.json` atomically, and
re-renders the views — all under the writing lock, so state and mirror move
together or not at all.

| verb | what it does | invocation |
|---|---|---|
| `set-status` | the §5 lifecycle transition; refuses anything the §5 table does not allow | `xcheck set-status ID STATUS [--next ROLE] [--retake human:LABEL]` |
| `record-fix` | planned -> fixed, recording WHICH session did it (§7 provenance) | `xcheck record-fix ID --session 16-HEX [--plan RP-NNNN] [--scope TEXT]` |
| `record-verdict` | the Verifier's binding act on a fixed finding; enforces verifier != fixer | `xcheck record-verdict ID --verdict closed|reopened --session 16-HEX [--reason TEXT]` |
| `admit-construal` | §4 rule 9 admission; enforces producer != admitter | `xcheck admit-construal KEY --admitter human:LABEL|16-HEX` |
| `file-finding` | record a new finding at `reported` and link its evidence body | `xcheck file-finding --id F-NNNN --title TEXT --severity critical|info|major|minor --dimension KEY --unit U01,U02 --pass P-NN --body PATH [--recurrence-of F-NNNN]` |
| `propose-construal` | register a §4 rule 9 construal at 'proposed'; never admits it | `xcheck propose-construal KEY --role ROLE --charter TEXT --session 16-HEX --body PATH` |
| `record-plan` | register a remediation plan and the findings it covers | `xcheck record-plan RP-ID --findings F-1,F-2 --body PATH` |
| `queue-pass` | append a pass to the queue (the Auditor's uncovered remainder, §4 rule 5) | `xcheck queue-pass P-ID --dimension KEY --units U01,U02 --charter TEXT --stop TEXT` |
| `record-refusal` | a §5 typed refusal on a finding charter; never touches the status | `xcheck record-refusal ID [--reason blocked-dependency|charter-ambiguous|cost-exceeded|material-missing|norm-conflict|out-of-competence] [--clear]` |
| `block-on-norm` | raise the §8 rule 8 norm-ratification gate; fail-closed until a ruling | `xcheck block-on-norm ID` |
| `record-ruling` | the norm owner's §8 ruling — the only thing that lifts that gate | `xcheck record-ruling ID --norm N1|N1-over-N4 --ruled-by human:LABEL` |
| `record-coverage` | bind a pass to its ONE canonical coverage report (§4 rule 5) | `xcheck record-coverage P-ID --report PATH [--findings F-1,F-2]` |
| `render-views` | re-render LEDGER.md and every frontmatter block from state.json | `xcheck render-views` |

The **admin verbs** change the operating parameters rather than a finding. They
are the operator's surface, not a role's, and they exist so that no flow in this
methodology requires editing the state document by hand:

| verb | what it does | invocation |
|---|---|---|
| `set-limit` | change one limit; refuses an unknown key, a non-integer, and a value outside the key's range | `xcheck set-limit KEY VALUE` |
| `amend-pass` | amend a QUEUED pass; refuses a done pass, whose report already certified its charter | `xcheck amend-pass P-ID [--dimension KEY] [--units U01,U02] [--charter TEXT] [--stop TEXT]` |
| `cancel-pass` | drop a queued pass; refuses a done pass, and one findings still point at | `xcheck cancel-pass P-ID` |
| `update-catalog` | add, amend or remove a norm/dimension/unit; refuses removing an entry records still reference | `xcheck update-catalog KIND KEY [--source TEXT] [--scope TEXT] [--catches TEXT] [--norms N1,N2] [--material TEXT] [--size TEXT] [--responsibility TEXT] [--remove]` |

`reopen_limit` is the one whose lower bound is a gate rather than a preference:
below 1 there is no reopen budget, so the mandatory human stop after repeated
failed remediations never fires. `set-limit` refuses that value by name.

Three properties hold for every verb, and they are enforced in code rather than
asked for in a prompt:

- **An illegal transition is refused, addressed.** The message names what was
  asked, what the record actually says, and which rule of §5 forbids it.
- **A repeat is refused, not repeated.** Re-running an applied transition means
  somebody has lost track of the record; a second write would put a second
  decision in the history where nothing decided anything.
- **Provenance cannot be skipped.** `set-status` will not perform
  `planned → fixed` or `fixed → closed|reopened`: those carry the session
  identity that `record-fix` and `record-verdict` exist to record, and it names
  the right verb instead.

The invariants that used to live only in role prompts are now checked here:
**verifier ≠ fixer** (`record-verdict` refuses the session that recorded the
fix), **producer ≠ admitter** (`admit-construal` refuses the session that wrote
the construal), the **`reopen_limit` stop** (the flag is set by the transition
that causes it, not recomputed by whoever reads the record later), and a class
finding's **non-empty `members`** (the schema makes an empty one unwritable).

Two of those stops take the human's decision as their input rather than
inferring it. A record flagged `⚠ needs-human` by the `reopen_limit` stop does
not resume because somebody typed the next transition: `set-status` refuses it
until `--retake human:<who sanctioned it>` names the person, and that same
transition resets `attempts` to 0, so the next cycle counts from the decision
rather than from the exhausted one. The §8 rule 8 norm gate works the same way:
`block-on-norm` raises it fail-closed — `record-fix` refuses while it stands —
and only `record-ruling --norm <winning norm> --ruled-by human:<owner>` lifts
it. A §5 refusal is the third: `record-refusal` records the typed reason and
leaves the status exactly where it was, because a refusal is about the attempt,
not about the finding, and it refuses a reason code whose `## Refusal` prose
half is missing.

Where the norm gives the decision to a human, the verb takes the human's
recorded decision as an *input* and never infers it. Mechanising the bookkeeping
is not the same as mechanising the judgement, and this contract does not move
one decision out of the operator's hands.

**Starting a new audit.** The Planner writes `AUDIT.md` — norms, dimensions,
unit map, pass queue, limits — as prose a human reads and rules on. The operator
then runs `xcheck migrate` once to load that plan into `state.json`. It is an
explicit operator act, never automatic: silently converting a live audit tree is
exactly the failure this design exists to prevent. Afterwards the queue is
amended with `queue-pass`, not by editing `AUDIT.md` again.

## 3. Roles

A role is a function, not an agent identity. The default assignment is Codex for Planner, Auditor, and Verifier, and Claude for Remediator — but any role can be played by any capable agent, and a third agent can join without changing this file. One rule is hard: **the Verifier is never the agent or session that produced the fix it verifies.** Prefer a different agent; the minimum acceptable substitute is a different, fresh session of the same agent.

Triage is the only mandatory human step in the routine cycle; everything else there runs agent-to-agent through files. Exceptional halts still require the human, in gates distinct from Triage — a disputed resolution (§5), the norm-ratification gate (§8 rule 8), and a non-converging cycle's `⚠ needs-human` flag (§9) — but the routine audit→triage→remediate→verify loop needs the human only at Triage.

Nothing in this document constrains the **register** an agent writes in. What it constrains is what must be reproduced character for character rather than paraphrased — an evidence quote (§6), a finding id, a §5 status, a command a person is meant to run — and that obligation holds in every register. How plainly a session speaks is the launcher's business, not this file's.

### Planner
- **Mission:** inventory the project and its norms; produce dimensions, a unit map, and a pass queue.
- **Default agent:** Codex, one session.
- **Reads:** XCHECK.md; the project's normative documents; a structural listing of the project. Not the full content of every unit.
- **Writes (evidence):** AUDIT.md — the project's inventory in prose: what each dimension catches, what each unit is and who is responsible for it, and which norm backs which dimension.
- **Writes (record):**
  - `propose-construal` — its own operational construal, and only when §10 `construal_gate` is on.

  That is the whole record surface: the Planner runs before there is machine state. Its AUDIT.md becomes `state.json` once, through `xcheck migrate`, which the operator runs; from that moment the catalogs, the queue and the limits change only through `update-catalog`, `queue-pass` and `set-limit`, and a later edit to the plan prose changes nothing.
- **Stop conditions:** AUDIT.md exists, with at least one dimension backed by a norm, a unit map, and a non-empty pass queue.
- **Forbidden:** filing findings; reading unit content end to end "to get a feel for it" — inventory only.

### Auditor
- **Mission:** run one pass — one dimension against a batch of units — and record findings; also give disputed findings one written round of objection.
- **Default agent:** Codex, one session per pass (or a split of one, §4 rule 4).
- **Reads:** XCHECK.md; AUDIT.md; the pass charter; the units in the charter's scope; the norm the charter cites; templates/pass.md and templates/finding.md, copied when starting a pass or filing a finding.
- **Writes (evidence):** the body of each finding it files, `findings/F-NNNN.md`; its pass report, `passes/P-NN-<dimension>.md`; one round of written objection in a finding's `## Objection` section when the Remediator disputes it (§5, §9 rule 3).
- **Writes (record):**
  - `file-finding` — each finding it files, and a recurrence with `--recurrence-of`.
  - `record-coverage` — the completed pass, bound to its one report.
  - `queue-pass` — the remainder of a split charter (§4 rule 4).
  - `set-status` — **only as agent-as-pen for the human's disputed resolution** (§5, §9 rule 3): the `disputed` → `accepted`/`withdrawn` call the human states, recorded verbatim and never on the Auditor's own judgement.
  - `propose-construal` — a §4 rule 9 reading of its own charter.
  - `admit-construal` — another role's construal, never its own.

  §5 one-status-one-owner: the human owns the disputed resolution; the Auditor is the pen.
- **Dedup rule:** before filing a finding, check it against ledger titles only — not finding bodies, not memory of earlier passes. Dedup matches against **non-terminal** findings only. A matching title on a **non-terminal** finding (still in the cycle, including a `superseded-by-class` member whose CF is not yet closed) is a duplicate: append the new evidence to that finding, with no status change and no second file. A title match on a **terminal** finding (`closed`, `rejected`, `withdrawn`, `obsolete`, or `superseded-by-class` with a *closed* CF) is **not** a duplicate — the defect has **recurred**. Terminal is final: never revive the old finding. File a **new** finding with a new id, passing `--recurrence-of <terminal id>` to `file-finding` (the field is rendered from that, never typed into the frontmatter); the terminal ancestor keeps its status, so the ledger records the regression as its own row and the metrics count a fresh cycle. The new finding is `reported` and re-enters the human Triage gate like any other. A class finding escalates on **live** instances in the current material, not on recurrence history: at or above `class_threshold` recurrences of one pattern, the recurrence count raises the closing standard for that root (§8 rule 2, §7) and a class finding opens only if the **live** census of the current material also reaches `class_threshold` (§8) — recurrence history alone, with nothing live to fix as a class, does not mint one, though where a class does open a repeatedly recurring root may sit on a higher strategy rung. Filing a recurrence as a new `reported` finding is the Auditor's call, the same authority that files any `reported` finding (§5, §9 rule 8).
- **Stop conditions:** charter's unit range exhausted, or `max_findings_per_pass` (§10) reached, or context budget exhausted.
- **Forbidden:** fixing anything; exceeding the charter's unit range instead of splitting it; filing a finding that does not meet the Evidence Standard (§6).

### Triage
- **Mission:** decide which findings get worked.
- **Default agent:** human — the one mandatory gate in the routine cycle (the exceptional human gates are enumerated at the head of §3, not re-counted here).
- **Reads:** LEDGER.md; finding files for any finding whose title alone doesn't settle the call.
- **Writes (evidence):** none — Triage decides; it does not author evidence.
- **Writes (record):**
  - `set-status` — `reported` to `accepted`, `rejected` or `deferred`, and re-triaging a previously `deferred` finding when the human revisits it at a later sitting (§5: `deferred` is parked, not terminal — it is always revisitable).

  Batch decisions are allowed, e.g. all `critical` and `major` findings to `accepted` in one action; the verb is run once per finding. Rejecting a CF also reverts every finding that class absorbed back to `accepted` — one `set-status` per member.
- **Stop conditions:** every `reported` row under review has a decision.
- **Forbidden:** editing finding file body sections; hand-editing a LEDGER row or a frontmatter block to carry a decision — both are rendered from `state.json` (§2), so the edit records nothing and the tool refuses the tree until it is undone; setting any status other than `accepted`, `rejected`, or `deferred`.
- **Agent-as-pen:** the human may run triage through an interactive agent session. The agent presents findings and runs `set-status` for the human's stated decisions verbatim; the decisions remain the human's. An agent must never accept, reject, or defer a finding on its own judgment, and must not batch-infer decisions the human did not state ("everything else rejected" counts only if the human said it).

### Remediator
- **Mission:** fix a batch of accepted findings.
- **Default agent:** Claude, one or more sessions.
- **Reads:** XCHECK.md; AUDIT.md; the charter (a list of finding IDs); the finding files in scope; templates/plan.md and templates/class-finding.md, copied when opening a remediation plan or a class finding.
- **Writes (evidence):** `plans/RP-NNNN.md`; edits to the project material; the Validation and Remediation sections of each finding file; the `## Refusal` prose of a finding it accepts but cannot execute (§5); a class finding's body when a census escalates (§8).
- **Writes (record):**
  - `record-plan` — the plan and the findings it covers.
  - `set-status` — `accepted` → `validated` → `planned`, plus the outcomes remediation itself decides: `disputed`, `obsolete`, `superseded-by-class`.
  - `record-fix` — `planned` → `fixed`, carrying the session identity the verifier ≠ fixer rule is enforced against, and `--scope` when §10 `scope_typing` is on.
  - `record-refusal` — the typed reason, which leaves the status where it was.
  - `file-finding` — a class finding minted by the census (§8).
  - `block-on-norm` — the §8 rule 8 gate, raised fail-closed before the fix.
  - `propose-construal` — a §4 rule 9 reading of its own charter.
  - `admit-construal` — another role's construal, never its own.
- **Mandatory in-session sequence:** **validate → census → plan → fix → self-check.** No step is skipped or reordered (§7, §8 rule 1). Self-check means re-running the finding's own "how to verify" procedure before marking it fixed — catching what the Verifier would catch anyway, before it costs a reopen cycle.
- **Stop conditions:** every finding in the charter reaches `fixed`, `disputed`, `obsolete`, or `superseded-by-class`; or `remediation_batch_size` (§10) is exhausted; or context budget exhausted.
- **Forbidden:** touching a finding outside the charter; marking a finding `fixed` without a Remediation section naming what changed and where; verifying its own fix.

### Verifier
- **Mission:** give a binding verdict on a batch of fixed findings.
- **Default agent:** Codex, one or more sessions.
- **Reads:** XCHECK.md; AUDIT.md; the charter; the finding files in scope; the diffs or changes the Remediation section points to.
- **Writes (evidence):** the Verification section of each finding file; the `## Refusal` prose of a finding it accepts but cannot execute (§5).
- **Writes (record):**
  - `record-verdict` — `closed` or `reopened`, the binding act; refused for the session that recorded the fix.
  - `record-refusal` — the typed reason, which leaves the status where it was.
  - `propose-construal` — a §4 rule 9 reading of its own charter.
  - `admit-construal` — another role's construal, never its own.
- **Hard rule:** never the agent or session that produced the fix. **Adversarial stance:** *"Your job is to prove the fix wrong, not to confirm it."*
- **Stop conditions:** every finding in the charter has a verdict.
- **Forbidden:** closing a finding without running its "how to verify" procedure; skipping the adversarial check of the surrounding change; verifying its own fix.

## 4. Session Protocol

Nine rules. They apply to every session, in every role.

1. **Charter required.** No session starts without an exact scope and stop conditions — e.g. "dimension X, units 4–6, stop after 15 findings" for an Auditor, or "findings F-0012..F-0019" for a Remediator.
2. **Fresh eyes.** State comes only from files. A session does not inherit conclusions from earlier sessions by memory. Anything a session acts on is re-verified against its source at the moment it is touched, regardless of what any prior session recorded about it.
3. **Explicit reading list.** Read XCHECK.md, AUDIT.md, your charter, the files the ledger points you to, and the artifact templates you instantiate. Nothing else. Reading the whole project "for context" is forbidden.
4. **Stop conditions are sacred.** Scope overflow: split the charter and queue the remainder with `xcheck queue-pass` — the queue is machine state, and AUDIT.md is the prose the operator migrated from, not the queue; skimming through to the end anyway is forbidden. Context running out: record what was covered and what was not, then exit cleanly.
5. **Coverage report is mandatory.** Every pass states explicitly what it did not cover. A silent gap is a protocol violation, not an acceptable shortcut.
6. **No finding quota.** Caps (§10) are upper bounds, not targets, and there is no minimum. A clean pass — zero findings — is a valid result and is recorded as one.
7. **Role launch goes through the orchestrator.** `xcheck next` and `xcheck loop` are the **authoritative** path and, since 0.9.1, the only one that starts a writing session: they run the role through the runner with all eight controls in force — sandbox profile, hard timeout, environment allowlist, log redaction, process-group kill, courier review of the diff, invocation envelope, session receipt. The five writing launcher skills are wrappers over that path: they do preflight, confirm which role is next, hand off, and report. A session can still be started by hand — `Read audit/XCHECK.md. Role: <Role>. Charter: <charter text>.` — and that form is `uncontained-direct`: it gets none of the eight, nothing records what was in force and nothing attests what it did, so the work is vouched for by the human who ran it and not by the tool. It is a legitimate thing to do deliberately and a bad thing to do by default, which is why no shipped launcher does it any more. A read-only session (Status) is the one that legitimately stays direct: it moves no machine state, so there is nothing for those controls to mediate.
8. **One active writing session per project at a time.** Coordination in v1 is manual: one human relays the baton between sessions. Concurrent writers are out of scope. Launcher tooling and scripted chains must serialize the same way: never start a writing session while another is active.
9. **Construal before effects.** The recipient of a charter states its own operational construal before any effect on the material begins — the task frame in its own words, the approach it intends, the assumptions it is carrying, its stop conditions, and what it treats as out of scope. The construal is admitted as **evidence, never authority**: it is what the admitter inspects, not what authorizes the work, and admission is a separate act by a party that is not the producer — always, with no pre-authorization standing in for it. The mechanism is configurable through §10 `construal_gate`, which is `off` by default until its effect on fix durability is measured. The admitter is named in the record: `human:<label>` for the operator admission that is §4 rule 9's default, or a canonical session id when another session admits. One further form, `envelope:<name>`, is **historical only**: it names the withdrawn `construal_envelope` pre-authorization (§10), and records admitted under it while that mechanism was live still carry it — so the tool keeps reading it as a valid admitter identity, because rewriting an already-admitted record to erase how it was actually admitted is the very overwrite this rule forbids. It is never minted again and the gate never honors it as a live admission: every new admission is a session id or `human:<label>`, and a construal presented for dispatch under an `envelope:<name>` admitter is treated as not admitted, awaiting a real non-producing party. An admission whose admitter cannot be named is not recorded, and an admission recorded under an identity that did not make it is worse than none (§4 rule 9). A construal is stated once per (role, charter) and admitted once — §2 gives that pair a single record. A later session that receives the **identical** (role, charter) neither mints a second construal nor overwrites the admitted one: under §4 rule 2 it re-reads the on-file construal and re-verifies it against its own reading of the charter, adopting it when faithful and halting to the human when its reading diverges (the divergence means the charter or the prior construal is wrong — fix the charter, not the admitted evidence). Re-verifying the file is not inheriting a conclusion by memory (§4 rule 2); overwriting admitted evidence, or proceeding under a construal one has not re-read, is what that rule forbids.

## 5. Finding Lifecycle

```
reported    → (Triage)      → accepted | rejected | deferred
deferred    → (Triage)      → accepted | rejected           (re-triaged later; a parked, non-terminal finding the human revisits — never left routeless)
accepted    → (Remediator)  → validated | disputed | obsolete
validated   →                  planned → fixed
fixed       → (Verifier)    → closed | reopened
reopened    →                  back to the Remediator (planned → fixed again), attempts += 1
disputed    → (human decides, Auditor dispute-round records, agent-as-pen) → accepted | withdrawn
accepted | validated | planned | reopened  → (Remediator)  → superseded-by-class (absorbed by a CF finding, §8; ONLY these four Remediator-held statuses — see below)
superseded-by-class → (Triage)   → accepted (CF rejected at triage, §8 rule 4)
(terminal is final — a recurrence of a terminal defect enters as a NEW finding tagged `recurrence-of:`, §3 dedup, never a transition of the old one)
```

| From | Event / actor | To |
|---|---|---|
| `reported` | Triage decides | `accepted`, `rejected`, or `deferred` |
| `deferred` | Triage revisits the parked finding in a later sitting (owner: human) | `accepted` or `rejected` |
| `accepted` | Remediator confirms the evidence | `validated` |
| `accepted` | Remediator cannot confirm the evidence | `disputed` |
| `accepted` | Remediator finds the defect already gone | `obsolete` |
| `validated` | Remediator writes a plan | `planned` |
| `planned` | Remediator applies the fix | `fixed` |
| `fixed` | Verifier confirms | `closed` |
| `fixed` | Verifier rejects | `reopened` |
| `reopened` | returns to the Remediator | `planned`, then `fixed` again; `record-verdict --verdict reopened` is what increments `attempts` |
| `disputed` | human sides with the finding (Auditor dispute-round records, agent-as-pen) | `accepted` |
| `disputed` | human sides against the finding (Auditor dispute-round records, agent-as-pen) | `withdrawn` |
| a Remediator-held status — `accepted`, `validated`, `planned`, or `reopened` | Remediator opens a class finding for its pattern | `superseded-by-class` |
| `superseded-by-class` | Triage rejects the CF | `accepted` |
| terminal | defect recurs — Auditor files a NEW finding (§3 dedup) | ancestor unchanged; the new finding starts at `reported`, tagged `recurrence-of:` |

Status ownership is the rule that keeps five roles from stepping on each other: *"One status, one owner: triage statuses are changed only by the human; validated/planned/fixed only by the Remediator; closed/reopened only by the Verifier; disputed resolution decided by the human, recorded agent-as-pen in the Auditor dispute-round (§9 rule 3) — Triage never writes it. Never touch a status you do not own."* Terminal statuses are immutable — no role rewrites them. When a terminal defect recurs, the Auditor files a **new** `reported` finding tagged `recurrence-of:` (§3 dedup, §9 rule 8) rather than reviving the old one, so the regression is a fresh row and the terminal history stays intact.

Definitions:

- **`obsolete`** — validation shows the defect is already gone: an unrelated edit removed it before remediation touched it.
- **`disputed`** — the quote is not found in the material, or the problem is not substantively confirmed.
- **`reopen_limit` breach** — `reopen_limit` (default 2, §10) consecutive `reopened` verdicts on the same finding set the ledger flag `⚠ needs-human`; the automatic cycle stops and a human decides what happens next.
- **`withdrawn`** — a `disputed` finding resolved against the Auditor; it closes without a fix.
- **`superseded-by-class`** — set immediately on every member finding when a class finding is opened for its pattern (§8 rule 4). The Remediator absorbs a finding into a class **only from a status it is itself the acting owner of** — `accepted`, `validated`, `planned`, or `reopened` (the non-terminal statuses whose next owner is the Remediator); never from `fixed` (that finding is awaiting the Verifier's binding verdict), never from `reported` or `deferred` (the human's, at Triage), and never from `disputed` (awaiting the human's dispute resolution). Seizing any of those would take over a status another role owns, which the one-status-one-owner rule above forbids; terminal statuses are excluded a fortiori (they are immutable). The member reverts to `accepted` if the human rejects the CF at triage. A CF itself terminates only as `closed` or `rejected`/reverted, and no other terminal is reachable for it: a CF is minted only from the Remediator's own census of live instances (§8 rule 2), so the ordinary `accepted → disputed` and `accepted → obsolete` steps do not apply to it — its own "validate" step is the re-census (§8 rule 7), an empty re-census **closes** the class (§8 rule 5) rather than making it `obsolete`, and there is nothing for the Remediator to fail-to-confirm, so a CF is never `disputed` and therefore never `withdrawn`. A member is thus never stranded under an `obsolete` or `withdrawn` CF, because a CF never reaches either (§8 rule 4).
- **`refusal`** — a frontmatter field (§2), not a status. It is the typed outcome an **agent** role records when it accepts a charter and cannot execute it: one reason drawn from the closed vocabulary, plus prose naming what is missing and what would unblock it. The mechanism binds exactly the **four agent-executed roles**, and each records its refusal in the **one durable target its own charter gives it** (§3) — there is no target common to all of them, and each rule below stands on its own:
  - **The Remediator and the Verifier**, whose charter *is* a set of findings, write the prose in that finding's `## Refusal` section and record the typed code with `xcheck record-refusal <ID> --reason <code>`, which renders it into the `refusal:` field; §3 grants each that write surface, and for these two roles the code and the section prose are both required, in that finding — the verb refuses a code whose prose half is missing, because a code with no prose is the silent gap §4 rule 5 forbids.
  - **The Planner**, whose charter produces AUDIT.md and carries no finding, records the reason and the prose in AUDIT.md itself (§3); the finding-frontmatter `refusal:` field does not apply to it.
  - **An Auditor pass**, whose charter produces a pass report and carries no finding, records the reason and the prose in its `passes/P-NN` report (§3); the finding-frontmatter `refusal:` field does not apply to it.

  **Triage has no refusal at all**: it is the human decision gate, not a chartered agent execution. Its charter is discharged by `accepted`/`rejected`/`deferred` — a human who will not take up a finding parks it as `deferred` (a route it revisits later, §5) or rejects it, and both are decisions *about the finding*, never a refusal *of the attempt* — and its only write surface is `set-status` (§3), so it needs no refusal target. Whatever the role, recording a refusal never changes the finding's `status`, so no status-ownership rule of this section is engaged (§5); the reasons pass forward to every successor attempt on the same charter, and the charter itself stays in force (§4 rule 1), so the work remains owed rather than silently dropped. A refusal is thus distinct from `disputed`, where the finding itself is contested, and from `rejected`, where Triage declined it (§5): those two decide something about the finding, while a refusal decides only about this attempt at it.
- **`blocked: norm-ratification`** — a rendered frontmatter field (not a status), raised by `block-on-norm` on a `planned` CF that stopped at the §8 norm-ratification gate. It withholds the fix while `status` stays `planned`. Its machine-readable pair is the **`norm-ruling`** frontmatter field: `pending` while the decision is outstanding, and the winning norm id (e.g. `N1` or `N1-over-N4`) once the norm owner (human, not Triage) rules. The gate is fail-closed on `norm-ruling` — absent, `pending`, empty, or any unrecognized token keeps the CF stopped; only `record-ruling` with a valid norm-id token clears `blocked:` and lets the Remediator resume (§8 rule 8). The `## Norm ruling` body section carries the human-readable reasoning but is not itself the gate.

## 6. Evidence Standard

Evidence is the anti-hallucination mechanism at the center of this methodology. Seven rules govern every finding:

1. **The quote is the primary anchor.** Character-exact, findable by a literal search in the material. A line number is a hint, not an anchor — text moves as the material is edited.
2. **A norm reference is mandatory.** A finding is a discrepancy between the material and a norm: a style contract, a spec, an ADR, a sourced fact, or internal consistency — in which case the contradicting passage is itself quoted as the norm. No norm, no finding: it is an opinion, and its severity is capped at `info`.
3. **"How to verify the fix" is written by the Auditor at creation time**, while the defect is in front of them. It is never written by the Verifier after the fact.
4. **A finding without evidence meeting this standard is invalid by construction.** It must not reach Triage.
5. **Locators follow the material kind.** Code: `file:line` plus the enclosing symbol name. Text: file, section, and the quote itself.
6. **Auditor-authored sections of a finding are append-only for other roles.** Evidence, Why this is a defect, and How to verify the fix are written once, by the Auditor; other roles may add to them but not rewrite them. The only in-place edit allowed is a locator update (§9 rule 1).
7. **Absence is anchored to its present twin.** Some defects are the absence of something the material requires — a norm promises an artifact that is missing, or a present element implies a partner that does not exist (`open` with no `close`, a table-of-contents line with no section, a spec clause with no implementing unit, a public symbol with no test). Such a defect has no line of its own to quote. Its anchor is instead the *present* element that generates the expectation, quoted character-exact per rule 1; the norm or pairing rule that makes the twin mandatory (rule 2); and a reproducible search for the twin whose **expected result is empty** — the twin is looked for and not found. Rule 4 is not relaxed by this: an absence with no present anchor to quote — a wish for something the material never promised — is not a reportable finding, and is routed to the Planner as a norm-gap note instead.

## 7. Validation & Verification

Two checks apply the Evidence Standard at two different points in the cycle.

**Validation** is the mandatory first step of remediation (§3). Before planning a fix, the Remediator confirms the finding against the source: the quote exists, and the problem is real. This is close to free — the source has to be read to plan the fix anyway. Failure routes the finding to `disputed` (problem not confirmed) or `obsolete` (defect already gone).

**Verification** is the Verifier's binding verdict on a `fixed` finding, and it has two parts. First, run the procedure the Auditor wrote in "How to verify the fix" against the current material. Second, adversarially inspect the surroundings of the change for collateral damage that procedure alone would not catch. The verdict is `closed` or `reopened`. Evidence for a `reopened` verdict is held to the same Evidence Standard (§6) as an original finding — an exact quote and a norm, not an impression. When a fix installed an automated guard, verify the guard adversarially: plant the defect it claims to catch in a temporary copy and expect the guard to fail it — a guard that passes its own selftest but not a live mutation is a hole, not a defense.

**Promise-width defect.** When a finding's defect is that a predicate or guard is *narrower than the claim it enforces* — a docstring, a §-norm, a README line, or a gate message that promises more than the code actually checks — the fix closes only if both the Remediator's self-check and the Verifier's check poison the **claim**, not the one branch the finding happened to report. For every route or call site where the claim is relied upon, an input that satisfies the narrow predicate yet violates the broad claim must be rejected. Fixing only the branch named in the finding is not a fix — the same claim usually leaks through another route. The Verifier poisons the claim across all routes rather than merely re-running the finding's original procedure.

**Admitted scope.** When §10 `scope_typing` is on and a Remediator sets a finding `fixed`, it declares the scope the fix admits: which routes, inputs and call sites the guarantee covers, and — mandatory — which it does not. The Verifier then checks **demanded ⊆ admitted**: any claim the code itself makes, in a docstring, a message or a README line, that reaches past the declared coverage is a promise-width defect under the rule above and reopens the finding. This gives the promise-width standard a written predicate instead of a second standard; the check is governed by §10 `scope_typing`, `off` by default — with the flag off no `admitted-scope` is required, matching §2 and §10, and the obligation is entirely conditional. The declaration has a fixed two-half representation, so a full residue is never confused with an absent one: the **positive** half is recorded by `record-fix --scope` and rendered as the frontmatter `admitted-scope:` scalar-or-list (the routes covered), and the **residue** is the finding's mandatory `## Admitted scope` body section, whose `### Covers` and `### Does not cover` subsections carry both halves. The gate reads the frontmatter list and the `### Does not cover` residue together; coverage with no `### Does not cover` residue is incomplete, not merely terse — a residue-free claim reads as "covers everything". The mandatory shape is:

```
## Admitted scope
### Covers
<the routes/inputs/call sites the fix's guarantee covers — mirrors the frontmatter `admitted-scope:`>
### Does not cover
<the residue — mandatory: what the fix does NOT cover; an empty residue reads as "covers everything", a promise-width defect>
```

## 8. Class Escalation

A finding may be one instance of a systematic defect rather than a one-off. Escalation exists to catch that and fix it globally instead of one instance at a time.

1. **Census is the mandatory second step of remediation**, after validation and before planning. The Remediator formulates the finding's pattern and searches for it across the whole project — not only in the finding's own unit. The search procedure fits the material (a literal-text or pattern search across files; a targeted read across sections where the pattern could recur) and must be recorded precisely enough for someone else to rerun it. The census inherits the finding's evidence polarity (§6 rule 7): for a presence defect the pattern is the defect itself and the census counts its occurrences; for an absence defect the pattern is the anchor→twin pairing, and the census enumerates every anchor and counts the **orphans** — anchors whose mandatory twin is missing.
2. **Escalation threshold: `class_threshold` instances (default 3, §10).** The count is of **live instances found by the census of the current material** (rule 1) — the defects standing in the corpus right now — not the *recurrence* count of the same root across already-terminal findings, which §3 dedup and §9 rule 8 track separately and which counts something different. Below threshold, fix the instance found and note its siblings in the finding file. At or above threshold, open a class finding. When the live census is below threshold but the same root has *recurred* at or above `class_threshold` times across terminal findings, do **not** mint a class finding: there is nothing in the corpus to fix as a class. The recurrence instead raises the **closing standard** for that root — most often the promise-width standard (§7) or the point-fix criterion the recurring finding names — so the point fix is held to a higher bar rather than multiplied into a class. **Declining escalation at or above threshold** is legal in exactly one shape, and only with the norm owner's ratification recorded in the plan: every live instance is *already* an accepted finding inside the current charter (so escalation would surface no unlisted sibling) **and** global strategy (a) is identical to point-fixing them (so there is no norm or guard to fix once). Minting the class would then only move accepted findings to `superseded-by-class` and defer them a triage round. Declining on any other ground, or without the ratification, is a protocol violation — the Remediator may propose the decline, never make it.
3. **A class finding (`CF-NNNN`) contains:** an exact definition of the pattern and its search procedure; a full census of instances with locators, including any the original pass missed; the root cause — most often a norm that is missing, ambiguous, or inconsistently enforced; and a global strategy on an increasing scale:
   - (a) fix every instance;
   - (b) (a), plus fix the norm so the class cannot recur;
   - (c) (b), plus an automated guard where one is possible — a search check, a lint rule, a style script — so the class becomes structurally hard to reintroduce.
4. **A CF finding passes through the same Triage gate** as any other finding — status `reported`, the human decides. The routine triage gate does not multiply — opening class findings adds no extra triage sittings — but a global change never bypasses it. (A class fix that changes a norm or picks between norms additionally stops at the separate norm-ratification gate, §8 rule 8, cleared by the norm owner — a human, not Triage.) On creation, every member finding moves to `superseded-by-class` immediately; if the human rejects the CF at triage, members revert to `accepted` for point fixes instead. Because a member is terminal only under a **closed** CF (§5), a CF has exactly two terminal fates and no others: it **closes** (members stay `superseded-by-class`, genuinely absorbed) or it is **rejected**/reverted (members revert to `accepted`). The member-orphaning transitions the general §5 table lists for an ordinary finding — `accepted → obsolete` and `accepted → disputed → withdrawn` — do **not** apply to a CF, and this is constructive, not a mere prohibition: a CF is minted only from the Remediator's own census of live instances (rule 2), and its five-step sequence maps `validate` onto the re-census (rule 7). So an already-gone class is a re-census whose empty result **closes** it (§8 rule 5, the polarity's clean result) rather than marking it `obsolete`; and a Remediator does not dispute the pattern it has just censused — there is nothing to fail-to-confirm — so a CF is never `disputed` and hence never `withdrawn`. The negative outcome the general table records as `disputed → withdrawn` for an ordinary finding is, for a class, the Triage **rejection** — the only non-closed terminal a CF takes — which reverts every member to `accepted`. No CF ever reaches `obsolete` or `withdrawn`, so no member is ever stranded `superseded-by-class` under a non-closed CF. The Remediator continues the current session with the rest of the batch — the CF waits for the next triage round.
5. **Class verification is a re-census**, performed by the Verifier: rerun the recorded search procedure and expect the polarity's clean result (§6 rule 7) — for a presence class, zero instances of the defect; for an absence class, zero **orphans**, i.e. every anchor's twin-search now returns its twin — or explicitly documented exceptions. Re-running an absence search and finding zero of the still-missing artifacts is not a pass: the orphan count, not the missing-artifact count, must reach zero. This is what catches "fixed 12 of 15" and its absence twin "created 12 of 15."
6. **Norm write-back is part of the fix, not a side effect.** Strategies (b) and (c) edit the project's normative documents in the same remediation, not as optional follow-up work. If the project maintains a persistent agent-memory or lessons store, record the norm change there in the same remediation.
7. **The five-step Remediator sequence maps onto an accepted CF as follows:** validate = re-run the recorded census procedure; census = already done (the CF's census IS the scope); plan = the Global strategy section; fix as usual; self-check = re-run the census procedure expecting the polarity's clean result (zero instances for a presence class, zero orphans for an absence class) or documented exceptions.
8. **Norm ratification gate.** If a class fix under strategy (b) or (c) changes a norm, relies on a norm that another normative source contradicts (including machine registries and configuration files), or must pick a side in any conflict between norms, the Remediator stops after writing the plan — it does not execute. The stop is recorded **durably in the record**, so a fresh session reconstructs it from files alone (design resumability), and the durable record is a **machine gate**, not prose: `xcheck block-on-norm <ID>` raises it — the rendered frontmatter then shows `blocked: norm-ratification` and `norm-ruling: pending` — plus a `## Norm ruling` section in the CF body stating the conflict and the candidate sides. The CF stays `planned`; the block, not a new status, is what withholds the fix. The decision is made by the **norm owner** — the human, in a gate distinct from Triage — who records **which side wins with `xcheck record-ruling <ID> --norm <token> --ruled-by human:<owner>`** (e.g. `--norm N1` or `--norm N1-over-N4`) and writes the reasoning and any plan amendment into `## Norm ruling`. The gate is **fail-closed on `norm-ruling`**: an absent field, `pending`, an empty value, or any unrecognized token keeps the CF stopped — a conflict description with no explicit frontmatter decision does **not** unblock it. Only a recognized norm-id token lifts the gate. This does not run through Triage: Triage's only verb is `set-status` (§3) and the CF is already `planned`, so routing a ratification decision through Triage would exceed its write surface; a verb whose `--ruled-by` names the human keeps every role inside its surface and leaves the side-choosing to the human, never an agent. **Sync/resume rule:** a fresh session reads `blocked:` and `norm-ruling:` together — `blocked: norm-ratification` with `norm-ruling` absent/`pending`/unrecognized means the decision is not yet made (wait for the norm owner); a valid norm-id in `norm-ruling` means it is ratified — `record-ruling` cleared `blocked:` in the same transition — so the Remediator executes per the ruling (or, if the ruling rejects the direction, re-plans the class fix per the winning norm — the Remediator stays inside the remediation loop and never sets `withdrawn`, which §5 owns to the disputed resolution, not to the Remediator). Documenting a norm conflict and proceeding anyway is a protocol violation for class fixes: a class fix in the wrong direction multiplies one error across the whole corpus.

## 9. Failure Modes

1. **Stale finding.** The material moved since the finding was filed, and the quote is no longer found by a literal search. Re-locate by meaning. If the defect is gone, mark `obsolete`. If it moved, update the locator and proceed. Fixing "from memory of where it used to be" is forbidden.
2. **Non-converging cycle.** `reopen_limit` (§10) consecutive `reopened` verdicts on one finding set `⚠ needs-human` in the ledger (§5). The automatic cycle does not keep spinning on its own. **A human-sanctioned re-take resets `attempts` to 0.** The counter measures *unsupervised* non-convergence — how far the agent loop got on its own — not total effort, so once the human has read the reopen, ruled on it and sanctioned another attempt, the supervision the counter guards for has already happened. Clearing the ledger flag by hand is not a re-take at all — the ledger is a rendered view (§2) and the gate is computed from the record's own `attempts` (a `reopened` finding at `attempts + 1 >= reopen_limit` halts). `xcheck set-status <ID> planned --retake human:<who sanctioned it>` is the re-take: it names the person and resets the counter in the same transition, so the reset can never be forgotten.
3. **Agent dispute.** The Auditor insists on a finding the Remediator marked `disputed`. One written round of objection in the finding file, then the human decides. Ping-pong between agents is forbidden.
4. **View drift.** `state.json` is truth; LEDGER.md and every frontmatter block are rendered from it (§2). A hand-edit to either is not repaired on contact and is never read back: the tool **refuses** the tree until the drift is resolved, and `xcheck render-views` is the only repair — it overwrites each view from state and lists by name every one that had drifted, which is the record of what it destroyed. There is no direction in which a view feeds the record, triage decisions included: those are `xcheck set-status`.
5. **Charter overflow.** The remainder of an overflowing charter becomes a new queued pass through `xcheck queue-pass` (§4 rule 4). A session interrupted mid-charter must still leave a coverage report (§4 rule 5) — otherwise the pass counts as not done at all.
6. **Concurrent human edits to the material.** Not forbidden. The Evidence Standard (§6) self-protects: a quote either still matches, or the finding falls into the stale-finding path above.
7. **Git.** If the project is under git: remediation commits reference finding IDs, and verification reads diffs. If not: the finding's Remediation section lists the files or sections changed. Git is an amplifier, not a requirement.
8. **Recurring defect.** A defect that a title-match ties to a **terminal** finding (`closed`, `rejected`, `withdrawn`, `obsolete`, or `superseded-by-class` with a closed CF) has recurred. Terminal is final: reviving the old finding would rewrite closed history, and quietly appending the evidence to it is a silent gap — durable as text but unreachable, because the finding's terminal `status`/`next` never call anyone back. The dedup rule (§3) resolves it: the Auditor files a **new** `reported` finding carrying `recurrence-of: <terminal id>`, leaving the ancestor terminal, so the regression is a fresh ledger row that re-enters the human Triage gate and counts as a new cycle in the metrics. At or above `class_threshold` recurrences of one pattern, the recurrence count raises the **closing standard** for that root (§7, §8 rule 2); it opens a class finding only when the **live** census of the current material also reaches `class_threshold` (§8 rule 2) — recurrence history alone, with nothing live to fix as a class, does not mint one, though where a class does open a repeatedly recurring root may sit on a higher strategy rung. Recording a recurrence only as appended evidence on the terminal finding, without filing the new finding, is the silent gap this rule forbids.

## 10. Configuration Defaults

| Key | Default | Meaning |
|---|---|---|
| `max_findings_per_pass` | 15 | Upper bound on findings recorded by one Auditor pass (§4 rule 6). |
| `remediation_batch_size` | 8 | Upper bound on findings worked by one Remediator session (§3). |
| `class_threshold` | 3 | Minimum instance count that escalates a finding to a class finding (§8 rule 2). |
| `reopen_limit` | 2 | Consecutive `reopened` verdicts on one finding before it is flagged `⚠ needs-human` (§5, §9 rule 2). |
| `construal_gate` | `off` | When `on`, a chartered session must have an admitted construal on file before effects begin (§4 rule 9). |
| `scope_typing` | `off` | When `on`, a finding set `fixed` must carry `admitted-scope` with its mandatory residue (§7). |

AUDIT.md may override any of these defaults per project.

There is no pre-authorization that stands in for construal admission. Admission is always a
separate act by a party that is not the producer (§4 rule 9): when `construal_gate` is `on`,
every `proposed` construal waits for a human — or another non-producing session — to admit it.
An earlier candidate, a `construal_envelope` pre-authorization declared inside AUDIT.md, was
removed: because AUDIT.md is shipped material the orchestrator's courier commits on any writing
session's behalf, three successive attempts to make an in-tree pre-authorization independent —
a roles list, then a narrowed roles list, then an out-of-tree attestation channel — each left a
route by which a dispatched session could self-admit its own construal. The only guarantee that
held was the one the mechanism was trying to avoid: a human admits, every time.
