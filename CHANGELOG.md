# Changelog

Notable changes per release. Dates are the tag dates in this repository's history.

Two numbers move independently and both are stated for every release: the **release
version** (`xcheck.__version__`) and the **state schema version**
(`xcheck.state.SCHEMA_VERSION`). A release bump never implies a migration; a schema
bump always does, and `xcheck upgrade` stops on it by name.

Entries for 0.1.0–0.3.0 are reconstructed from git tags and commit subjects — the
fidelity history supports, and no more. Where a release's detail is not recoverable
from the repository, it is not invented here.

---

## 0.9.5 — 2026-09-15 · schema 1

The response to the **sixth** industrial-readiness audit, which read `15cf5f8` and found
xcheck not yet ready for industrial autonomous operation, with high confidence. Its central
diagnosis: the main limiter is no longer the absence of individual capabilities but the
trustworthiness of the system's own state — fixes covered individual execution paths while
the promised guarantees were stated for the whole system. All six findings were reproduced
in the working tree before this work was planned, and all six are re-run in phase 10 and
shown refused.

Everything below is scoped to `orchestrated` sessions — the ones `xcheck next` and
`xcheck loop` launch through `runner.py`. The only direct surface is read-only `status`,
which dispatches nothing and has no containment claim to make.

10 phases, 95 acceptance criteria. The external battery went from **1,571 tests to 1,711**.
`state.json` written by 0.9.4 is read by 0.9.5 unchanged; 0.9.5 adds one field,
`ledger_commits`, which older readers ignore. `docs/audit-response-6.md` answers the audit
item by item and names every open item as open — including the four priority-1 items this
run does not deliver at all.

### Per phase, with its upgrade cost

| Phase | What changed | Upgrade cost |
|---|---|---|
| 1 | The parallel state-write race measured as OVERLAP rather than as the crash it sometimes causes, with a positive control and a serial control. | None. Tests only. |
| 2 | `serialized()` wraps the whole read-modify-write in `write._apply`, `envelope.store` and `envelope.emit`. | None for a single-session operator. Concurrent passes now queue at the write boundary instead of interleaving; a very write-heavy parallel run may be marginally slower and is now correct. |
| 3 | A durable transaction identity: `ledger.new_txn()`, `write_state(txn=…)`, and `ledger_commits` in canonical state. Recovery answers none / hold / discard. | Additive field — see breaking change 1. A recovery that previously replayed an ambiguous outbox row now HOLDS it and says so. |
| 4 | `ledger.require_complete` is asked before any append; `FORBIDS` refuses `write` in every non-intact state. | A project whose journal is broken, unreadable, replaced or short now has its write verbs REFUSED rather than silently extending a damaged record — see breaking change 2. |
| 5 | One dispatch preparation: `dispatch_route`, `prepare_logs`, `prepare_launch`, `finish_session`, `result_problem`, used by both dispatchers. `parallel.py` can no longer reach `build_cmd`. | None if `parallel_passes=off`. With it on, the parallel path now produces the same argv, envelope and log root as the sequential one. |
| 6 | Four AST boundary gates, each with a counterfactual: no unserialized state write, no unconfirmed reservation, no unchecked append, no private command build. | None. Tests only, in the PR tier (436 ms). |
| 7 | Every economic figure carries its kind — measurement, lower bound or estimate. Provenance and an 80% coverage floor are preconditions; rates are per model and per token category; wall clock is the audit's own span. | A figure that previously printed a number may now refuse with a reason — see breaking change 3. Declared rates must gain a `models` table to price anything. |
| 8 | The OKF bundle's required file set is derived from the exporter; read failures are named absent / is-a-directory / unreadable / malformed; duplicate ids are counted where they can be seen. | A bundle that previously verified clean may now report problems it always had. Regenerate with `xcheck okf`. |
| 9 | Witness kinds — dispatch, refusal, corpus — with a per-kind demand. Honest re-score 3 of 20 to 6 of 20; ratchet 17 to 14. | None. Tests only. |
| 10 | The audit answered item by item, the escape matrix re-derived by running it, 0.9.5 minted. | None. |

### Breaking changes

1. **`state.json` gains `ledger_commits`.** Additive and omitted when empty. A 0.9.4
   reader ignores it; a 0.9.5 reader uses it to tell a committed transaction from an
   intended one. **Migration: none.** No schema bump, and `xcheck upgrade` does not stop.
2. **Write verbs refuse over a damaged journal.** Previously a `broken` journal was read
   happily by `read_events` and a write proceeded. **Migration:** run `xcheck recover` and
   repair the stream; the refusal names which of the four states it found and what to do.
   A project with an intact journal sees no change.
3. **Economic figures refuse where they used to answer.** `tokens_per_outcome` is absent
   below the 80% coverage floor, and `money_per_outcome` requires provider-attested
   sessions. **Migration:** declare per-model rates and set `telemetry_adapter` to get a
   money figure at all. The measured token TOTAL is still reported, as a lower bound.

## 0.9.4 — unreleased · schema 1

The response to the **fifth** industrial-readiness audit, which read `f6fda87` and found
xcheck not yet ready for industrial autonomous operation, with high confidence. Its central
diagnosis: the project's problem is the quality of acceptance criteria and end-to-end
checks — a large green battery coexisting with reproducible violations of the product's
core promises. Every claim it made was reproduced before this work was planned, and every
one is re-run in phase 13 and shown refused.

Everything below is scoped to `orchestrated` sessions — the ones `xcheck next` and
`xcheck loop` launch through `runner.py`. The only direct surface is read-only `status`,
which dispatches nothing and has no containment claim to make.

13 phases, 108 acceptance criteria. The external battery went from **1,463 tests to
1,571**. No schema change: `state.json` written by 0.9.3 is read by 0.9.4 unchanged.
`docs/audit-response-5.md` answers the audit item by item and names every open item as
open — including three of the nine readiness rows this run does not meet at all.

### Per phase, with its upgrade cost

| Phase | What changed | Upgrade cost |
|---|---|---|
| 1 | An end-to-end smoke suite: eleven scenarios drive the real CLI against a controlled fake agent and read the argv, sidecar, outcome, collect and courier. Two arms shipped EXPECTED-RED. | None. Tests only. |
| 2 | One `build_cmd` call site, AST-pinned, handed the route and the sidecar. Model routing reaches the child. | None if `model_routing=off`. With it on, routing now actually applies — see breaking change 1. |
| 3 | Attestation gates on telemetry PROVENANCE: an agent-reported model cannot attest a route. The route is judged before `sandbox.collect()`. | A session whose provider disagrees with the planned route is now refused and quarantined instead of merged. Set `telemetry_adapter` to get provider figures at all. |
| 4 | An outbox row is an intention: it is replayed only when the committed state revision proves its transaction landed. | None. `xcheck recover --apply` discards rows it cannot prove, naming each. |
| 5 | `ledger.read_stream` is the one validated read boundary; every consumer is an adapter over it. | A repository whose event chain is already broken now REFUSES writes instead of accepting them. Run `xcheck recover` first. |
| 6 | Ledger access errors are three answers, not one. Recovery takes the writer's lock when it has something to write. | None. A directory or an unreadable file where `events.jsonl` should be is now an error rather than `intact, 0 events`. |
| 7 | A ceiling that measured spend has already passed is a HARD stop, above the coverage floor. The per-dispatch cap reports `unverified`. | None unless a run is already over a ceiling, in which case the next dispatch stops — which is the point. |
| 8 | `reconcile` speaks about anchors and proposes only transitions §5 allows. | Any tooling parsing its verdicts — see breaking change 2. |
| 9 | One reference table for the whole OKF bundle: records and proposals resolve against the same exported ids. | None. Bundles are derived and disposable; regenerate with `xcheck okf`. |
| 10 | `doctor` answers about the NEXT dispatch, per role and per profile, and gained a fourth verdict — see breaking change 3. | A machine that was passing a partial or containerised configuration will now see FAIL or UNVERIFIED, and `doctor` exits non-zero. Nothing about dispatch changed; the preflight stopped being wrong. |
| 11 | Every declared feature names an end-to-end WITNESS or is declared witness-less with a reason. | None. A gate over the test suite. |
| 12 | Four value metrics priced per useful outcome, each refusing rather than zeroing. `docs/ab-model-routing.md` corrected. | None. `benchmark.value_report` is new and unrun; money needs rates the operator declares. |
| 13 | The audit answered, the escape matrix re-derived by running it, the version minted. | None. |

### Breaking changes, in one place, with their migrations

| # | What breaks | Migration |
|---|---|---|
| 1 | **Model routing now reaches the child process.** It previously computed a route and discarded it, so `model_routing=on` produced identical argv either way. | If `model_routing=on` and your role command carries `{model}`, sessions now run under `cheap_model` / `strong_model` as the table says. That is the feature working; it is listed here because the behaviour of an unchanged configuration changes. Set `model_routing=off` to keep the previous argv. |
| 2 | **`reconcile`'s verdicts are renamed and narrowed** to `anchor-unchanged`, `anchor-changed`, `anchor-missing`, `possible-duplicate`. `already-fixed`, `still-present`, `obsolete`, `source-moved` and `duplicate` are gone. | Nothing to run. If you parse the proposal document, remap: `still-present` → `anchor-unchanged`; `already-fixed` and `source-moved` → `anchor-changed`; `obsolete` and `requires-human-ruling` → `anchor-missing`. The renaming is the fix: one added space made a quoted line vanish and earned `already-fixed` on a defect that still behaves exactly as filed. |
| 3 | **`doctor` gained a fourth verdict, `UNVERIFIED`, and it is BLOCKING for a required check.** A container profile now yields `UNVERIFIED` for `agent-cli-present` (the command runs in the image, which a preflight may not start) and FAIL for `provider-path-permitted` with an empty egress allowlist. | If you use `xcheck doctor` as a gate, expect non-zero on configurations that previously passed. Each prints TO VERIFY with the command that settles it. `SKIP` is unchanged and stays non-blocking. |
| 4 | **A broken event chain refuses ordinary writes.** `envelope.read_events` validates through `ledger.read_stream`. | Run `xcheck recover` to see what is wrong, and `xcheck recover --apply` to replay what can be proved. A chain that cannot be repaired is a chain that should not be written to. |
| 5 | **`xcheck okf` verifies proposals as well as records**, so a bundle with a dangling proposal reference now reports problems and exits non-zero. | Regenerate the bundle. It is derived from `state.json` and the event stream and is never an input. |

## 0.9.3 — unreleased · schema 1

The response to the **fourth** industrial-readiness audit, which read `3019572` and rated
industrial readiness **4,5/10** (core engineering 7/10). Its charge was one sentence: the
project proves its mechanisms exist better than it proves they are REACHABLE — four of the
most valuable subsystems were unit-tested library code behind public flags that promised a
working feature. Every concrete claim it made was reproduced before this work was planned.

18 phases, 151 acceptance criteria. The external battery went from **1,239 tests to
1,463**. No schema change: `state.json` written by 0.9.2 is read by 0.9.3 unchanged.

Everything below is scoped to `orchestrated` sessions — the ones `xcheck next` and
`xcheck loop` launch through `runner.py`. The only direct surface is read-only
`status`, which dispatches nothing and has no containment claim to make.

The run's success condition was a number: the feature-truthfulness allowlist introduced in
phase 1 with **four** entries is **empty**. Every public conf flag now names a production
call site, or the flag is gone.

`docs/audit-response-4.md` answers the audit item by item and answers its staged release
criteria one by one — **stages 1 to 3 met in full, stage 4 written and unrun, stage 5 not
met**. The stage-5 criteria (20–30 independently verified closures, measured precision and
durability, external repositories) are **not met by construction**: they need paid agent
runs the operator scoped out before planning, and this run reports zero closures rather
than awarding itself any.

### Breaking changes, in one place, with their migrations

| # | What breaks | Migration |
|---|---|---|
| 1 | **`diff_scope` and `evidence_cache` are no longer public conf keys**, and neither is `evidence_dir`. The modules remain as experimental library code. | Delete the keys from your configuration — an unknown key is refused, naming it as withdrawn rather than as a typo. Nothing behaved differently when they were set: neither had a production call site, which is why they went. |
| 2 | **A canonical transition fails when its event cannot be appended.** `emit()` used to print a note and return, so a transition could land while its event vanished. | Nothing to do on a healthy machine. On a full or read-only disk the write now REFUSES instead of silently losing evidence, and leaves a durable outbox entry that `xcheck recover --apply` replays. |
| 3 | **Every new event carries `v` and `prev`** (a rolling digest of the whole prefix), and the reader is strict: bad JSON, a missing field, an unknown event kind, an unknown schema version or a broken link is refused by line number. | Nothing to do. Historical events are byte-identical and carry neither field; the migration point is declared by the data, and the unchained prefix is fixed by the digest the first chained event names. |
| 4 | **The courier refuses a session that rewrites any byte of the existing event stream.** A suffix append is carried; a rewrite or a truncation is refused, naming the first differing byte offset. | Nothing to do unless a tool of yours edited `audit/events.jsonl` in place. Append only. |
| 5 | **Model routing collapses from a 15-cell table to two routes**, `cheap` and `strong`, and a session record REFUSES to finish when the model the provider reports disagrees with the planned route. | Set `cheap_model` and `strong_model` in the operator profile and put `{model}` in the role command where your CLI takes its model flag — xcheck supplies the value and never invents the flag. With `model_routing=off` nothing changes. |
| 6 | **`telemetry_adapter` is required for provider telemetry.** An adapter is chosen from the operator profile and never inferred from the role command; an unknown name is refused. | Set `telemetry_adapter=codex` (or `fake` for a machine with no provider CLI) and put `{sidecar}` in the role command. Without it, sessions record no provider figure — which is what they did before, now said out loud. |
| 7 | **`{token_cap}` is a real cap, not a forecast.** With `budgets=on` and `tokens_per_session` set, a role command that does not carry `{token_cap}` is refused. | Put `{token_cap}` where your CLI takes its output limit, or clear `tokens_per_session`. |
| 8 | **Statistical budget gates refuse to judge below 50% telemetry coverage**, returning `no-verdict` rather than a pass. Hard caps are exempt and the list of which is which is declared. | Nothing to do. A strict exit code over 4,4% coverage was a guess wearing authority. |

### One entry per phase, with what it costs to upgrade

| # | Phase | Upgrade cost |
|---|---|---|
| 1 | A feature-truthfulness gate | None. A test: every public conf flag must name a production call site or be declared unwired with the phase that closes it. |
| 2 | Demote `diff_scope` and `evidence_cache` | **Breaking (#1).** Remove the three withdrawn keys. |
| 3 | Documentation matches the wrapper model | None. `launchers/README.md` and `xcheck status` stop calling wrapper launchers `uncontained-direct`. |
| 4 | Evidence and log roots refuse the subject | **Possibly breaking** if a root was configured inside the audited repository — that is now refused by canonical path. Point it outside; any location outside will do. |
| 5 | Charter accounting on real events | None. `charter_repeat_limit` now counts `charter_hash`, which is the field the emitter writes; the gate was unreachable before. |
| 6 | OKF reads production events | None. `okf=off` by default. The projection reads `target`/`ts`, and the verifier checks referential integrity rather than checksums alone. |
| 7 | Events fail closed | **Breaking (#2).** No migration; a refusal where there was a printed note. |
| 8 | A versioned, hash-chained, strictly-read ledger | **Breaking (#3).** No migration: the historical prefix is untouched. |
| 9 | The courier enforces an immutable prefix | **Breaking (#4).** No migration unless something of yours rewrote the stream. |
| 10 | `xcheck recover`, and a recovery drill | None. A new read-only verb; `--apply` replays only what xcheck itself reserved. |
| 11 | A provider telemetry adapter | **Breaking (#6)** for anyone expecting provider figures without declaring an adapter. |
| 12 | Budgets that actually cap | **Breaking (#7, #8).** Add `{token_cap}`; expect `no-verdict` from statistical gates on thin telemetry. |
| 13 | Model routing changes the command | **Breaking (#5).** Add `{model}` and the two model keys, or leave `model_routing=off`. |
| 14 | `xcheck reconcile` | None. A new verb that proposes and cannot write. |
| 15 | Recurring classes reach a consumer | None. `xcheck status` and the triage gate print class candidates as PROPOSALS; nothing writes a class finding. |
| 16 | `xcheck doctor` | None. A new read-only preflight. It exits non-zero when a required check fails — deliberately, so it can gate a script. |
| 17 | CI tiers used by CI | None locally. `bash ci/run-checks.sh` with no arguments behaves as before; `--tier=package` and `--tier=nightly` are new names for the same script. |
| 18 | Polish, harden, and answer the audit | None. The version, the response document, and the escape matrix re-derived by running it. |

---

## 0.9.2 — unreleased · schema 1

The response to the **third** industrial-readiness audit, which read `8b8930c` and rated
industrial readiness **4,5/10** (engineering quality 7,5/10) with 7 P0, 7 P1 and 3 P2
items. Every concrete defect claim it made was reproduced before this work was planned, and
one more was found in passing.

21 phases, 157 acceptance criteria. The external battery went from **954 tests to 1,239**.
No schema change: `state.json` written by 0.9.1 is read by 0.9.2 unchanged.

`docs/audit-response-3.md` answers the audit item by item and answers the thirteen
recommended release criteria one by one — **seven met, five not met, one partly**. Criteria
8–11 (a full lifecycle, 20–30 durable closures, measured precision and durability, and a
measured saving against Ouroboros-4) are **not met by construction**: they need paid agent
runs the operator scoped out before planning.

Everything below is scoped to `orchestrated` sessions — the ones `xcheck next` and
`xcheck loop` launch through `runner.py`. That scope is no longer a narrowing: phase 4 turned
the five writing launcher skills into wrappers over `xcheck next`, so there is no
`uncontained-direct` writing path left to exclude. The only direct surface is read-only
`status`, which dispatches nothing and has no containment claim to make.

### Breaking changes, in one place, with their migrations

| # | What breaks | Migration |
|---|---|---|
| 1 | **`trust_level` is required.** No dispatch happens until the operator classifies the material, and `untrusted` with any profile that is not `container` is refused. | Add `trust_level=trusted` or `trust_level=untrusted` to your operator profile. There is no default: the cheap default (`trusted`) is the expensive one to get wrong. Untrusted material also needs `sandbox_profile=container`. |
| 2 | **An operator profile that resolves inside the audited repository is refused** — symlinks and symlinked parents included, and `--policy` is not an override for location. | Move the profile out of the project. The refusal names the canonical path it resolved to and the project root it landed inside. A profile in the project's *parent* is fine. |
| 3 | **A duplicate key in the profile is refused**, for every key rather than only the containment ones, comparing raw keys so ` k ` and `k` collide. | Delete the loser. The refusal quotes both values and both line numbers. Merging configurations that both set `sandbox_profile` no longer silently downgrades containment. |
| 4 | **The `*_cmd` wildcard is gone.** Role commands come from a closed registry derived from `DISPATCHABLE_ROLES`; an unknown key is refused. | Fix the spelling — the refusal names the near miss (`auditro_cmd -> auditor_cmd?`). Registering a genuinely new role is a deliberate edit to the role list, not a config key that happens to end in `_cmd`. |
| 5 | **The five writing launcher skills are wrappers, not sessions.** They hold no lock, write no finding, and take no improvised charter. | Use them as before; they now run `xcheck next`. What is gone is the dialogue and the typed-at-the-launcher charter. Change what runs next with `xcheck queue-pass` / `amend-pass` / `set-status` at your own terminal. `xcheck status` still answers every read-only question. |
| 6 | **Agent-printed token figures are labelled `agent-reported`, not `provider`.** Impossible values (negatives, booleans in integer fields, totals that cannot be the sum of their parts) are refused rather than recorded. | Nothing to do unless you were reading `telemetry_source` as proof of provider origin. For a trustworthy figure, use the parent-owned sidecar; the child cannot write it. |
| 7 | **Retention has four states where it had two**: `present`, `pruned`, `missing`, `corrupt` (plus `unrecorded`). A recorded log whose file is gone is `missing`, not `pruned`; a file whose digest does not match is `corrupt`, not `present`. | Nothing to do. Expect answers you did not get before: a corrupt-in-place log now says so instead of reading healthy, and a damaged manifest fails closed for every query rather than returning an empty list. |
| 8 | **`ci/release-gate.sh` refuses to run with `XCHECK_TIER=pr`.** | Run `bash ci/run-checks.sh` with no arguments for a releasable result. `--tier=pr` is the fast local loop and says in its own output that it is not a release. |

### One entry per phase, with what it costs to upgrade

| # | Phase | Upgrade cost |
|---|---|---|
| 1 | One profile loader, provably external | **Breaking (#2).** Move the profile out of the audited repository. |
| 2 | Policy keys refuse ambiguity | **Breaking (#3, #4).** Remove duplicate keys; fix misspelled role commands. |
| 3 | Trust level is classified or nothing runs | **Breaking (#1).** Declare `trust_level`; untrusted needs `container`. |
| 4 | The launchers become thin wrappers | **Breaking (#5).** No config change; a workflow change — the role runs as a child process you do not talk to. |
| 5 | Retention tells four states apart | **Breaking (#7)** only in what the answers say. No migration. |
| 6 | Telemetry the subject cannot forge | **Breaking (#6).** No migration; a relabelling plus new refusals. |
| 7 | One denominator, in the caption and the value | None. `metrics` stops printing a caption whose denominator disagrees with its value. |
| 8 | An output watchdog, honestly named | None. The bound is `output_deadline` and the mechanism counts bytes and canonical transitions; the separate real-activity signal is `activity_probe`. The old name `first useful action` promised semantic progress detection while measuring chattiness, and survives only as a `HISTORICAL:` comment. |
| 9 | Project prose is untrusted data | None for existing findings. New findings require a `--locator` and a `--source-hash` that resolve in the recorded subject commit. |
| 10 | The critical finding, triaged | None. A human decision, recorded. |
| 11 | Deterministic preprocessing | None. Off unless asked for; no model and no network when it runs. |
| 12 | Diff-directed scope, with a full sweep that still happens | None. `diff_scope=off` by default. The staleness of the last UNSCOPED pass is reported either way. |
| 13 | An evidence cache keyed on everything that matters | None. `evidence_cache=off` by default. A verdict is never cached, on or off. |
| 14 | Model routing is a table, not another model | None. `model_routing=off` by default. Fails closed to the strong route; a high-risk pass cannot be routed cheap. |
| 15 | Budgets that stop, priced on measured figures | None. `budgets=off` by default. When on, ceilings are priced on measured sessions only. |
| 16 | OKF as a proposal-only bundle | None. `okf=off` by default. The bundle is derived, disposable, and never an input. |
| 17 | Recurring classes, proposed not decided | None. Candidates are proposals; nothing writes a class finding. |
| 18 | An evidence bundle someone else can verify | None. `evidence_bundle=off` by default. Writes outside the working tree when on. |
| 19 | Two tiers, and the release gate runs once | **Breaking (#8)** for anyone scripting the gate. `bash ci/run-checks.sh` with no arguments behaves as before. |
| 20 | A benchmark harness, shipped unrun | None. No entry point spends anything; every metric reads `unmeasured` and refuses when asked. |
| 21 | Migrate the logs, then polish and answer the audit | **Run `xcheck prune-logs --migrate --apply` once.** It verifies every in-tree log against the manifest first and refuses the whole migration on any mismatch, naming the file. Nothing is deleted. In this repository: 304 files, 106,664,401 bytes, `audit/` from 104 MB to 1.7 MB. |

### Also

- New conf keys, all defaulting to `off`: `diff_scope`, `evidence_cache`, `model_routing`,
  `budgets`, `okf`, `evidence_bundle`. New operator key: `evidence_dir`.
- `OUTPUT_SCHEMA_VERSION` moved 7 → 9: `full_sweep` (phase 12) and `sessions_by_route`
  (phase 14) are new keys in the two closed output schemas.
- The adversarial matrix was re-derived by running every probe on 2026-09-04: 8 rows,
  8 hold, 0 changed, 0 unprobed. 18 escaped cells, unchanged, and A8 still escapes under
  every profile including `container`.
- Git history was **not** rewritten. Doing so invalidates every recorded digest — the
  subject commits in 133 findings and the provenance of 159 sessions — and it is the
  operator's call.

---

## 0.9.1 — unreleased · schema 1

The third-audit release. An independent review of `d3475c5` rated the branch **5,5/10** and
named two release blockers: parallel mode destroyed part of the evidence it created, and
retry could re-apply the material effects of a failed session. Both are closed here, each
with a mutation witness that reproduces the audit's own failure text on the mutant and a
clean control beside it. No schema change: `state.json` written by 0.9.0 is read by 0.9.1
unchanged. `docs/audit-response-0.9.1.md` answers the audit item by item, including the
items that stay open.

Everything below is scoped to `orchestrated` sessions — the ones `xcheck next` and
`xcheck loop` launch through `runner.py`. The six launcher skills are still
`uncontained-direct`: a human starts them inside their own agent, and none of the controls
named here exist there.

### Breaking changes, in one place, with their migrations

Nine of them, all from the industrial-readiness response. Each is also described in its own
entry below; this list exists so an upgrading operator does not have to find them there.

| # | What breaks | Migration |
|---|---|---|
| 1 | 23 of the 31 `orchestrator.conf` keys are **refused inside the audited repository** — commands, containment, environment, egress, image, limits, retry, parallelism. | Move them to an operator profile. The refusal names every offending key and quotes your own values back in the form to paste. `docs/operator.conf.example` is the template. |
| 2 | `xcheck next` and `xcheck loop` **refuse to run with no operator profile**, with no fallback to defaults. `--dry-run` refuses identically. | Write `$XDG_CONFIG_HOME/xcheck/operator.conf` (or pass `--policy <path>`, or export `$XCHECK_OPERATOR_PROFILE`). The profile is read fail-closed: an unknown or misspelled key is refused by name. |
| 3 | An **agent session may no longer perform a human-owned transition** or an administrative verb; the write boundary refuses by role. | Run those verbs yourself, at your own terminal, where there is no open dispatch. The refusal names the role that owns the transition. |
| 4 | `triage_batch_cap` default **0 → 20**: a project with 20 or more undecided findings stops at the triage gate instead of opening another pass. | Triage them (`/xcheck-triage`), or set `triage_batch_cap` explicitly in the profile. `0` still means unbounded and is now an operator's stated choice rather than the default. |
| 5 | New `audit_over_critical`, default **off**: an open `critical` finding stops new audit passes at a named gate. | Close the critical finding, or set `audit_over_critical=on` in the operator profile. |
| 6 | `OUTPUT_SCHEMA_VERSION` **3 → 5**. The token ratio key changed meaning (it is now scoped to the measured subset) and a `telemetry` block was added. | Re-read `status --json` / `metrics --json` against the version field. A consumer that read the old ratio key silently read a different quantity; it now reads a bigger, honest one. |
| 7 | `receipt` is **three-valued**: `recorded`, `no-progress`, `protocol-violation`. | A reader that treated "not `recorded`" as "no progress" now mislabels a violation. `PROTOCOLS` is enumerated and an unknown value is refused at the boundary. |
| 8 | `executable_version` records the **sha256 of the executable's bytes**, not a version string — because producing the string meant running an operator-configured binary on the host before containment (F-0098). | Display it as a digest. Records written before this release keep their old form and are still read. |
| 9 | **Raw session logs are written outside the working tree**, and three new deadlines (startup 120s, first action 600s, idle 1200s) can end a session the hard timeout would have let run for an hour. | New logs land at the printed path (`log_dir` → `$XCHECK_LOG_DIR` → `$XDG_STATE_HOME/xcheck/logs/<project>` → the system temp dir); `xcheck prune-logs --migrate --apply` moves an existing project's. A session killed by a deadline records the outcome `stalled` and is never retried. |

One thing that is deliberately NOT breaking: every new per-session field — the provenance
five, the telemetry block, the capsule digest, the deadline timings — is OPTIONAL in
`state.SESSION_FIELDS`. `state.json` written by 0.9.0 is read by 0.9.1 unchanged, and the
state schema stays at **1**.

- **Both blockers fail closed before anything else was built** (phase 1). `parallel_passes=on`
  refused, and `retry_limit` defaulted to `0`, on the first commit of this run — so every
  later phase was developed against a branch that could not lose evidence or double-apply a
  patch. The parallel ban lifted only in phase 8, and only because the end-to-end
  preservation test went green; the lift is decided at runtime by `preservation_probe()`,
  which runs the real `apply_artifacts` at decision time rather than reading a version
  number, and refuses again the instant the machinery breaks.
- **A failed session's patch is quarantined, never applied** (phase 3). Only `ok` transfers.
  Every other outcome — `provider-error`, `timeout`, `crash`, `refused`, `blocked` — writes
  the sandbox diff plus a `manifest.json` under `audit/quarantine/<session>/`, and `xcheck
  status` surfaces the pending bundles. Nothing lands in the project tree without a human
  reading it first.
- **Retry proves the absence of material effects, files *and* HEAD** (phase 4). The old gate
  compared the canonical part of `state.json` and nothing else, so a session that rewrote
  sources and returned `provider-error` looked effect-free. `material_snapshot()` /
  `material_effect_probe()` now hash the tree and read `HEAD` before and after, and subtract
  the orchestrator's own bookkeeping writes by name so the check is not True for every
  session. A dirty tree refuses the retry, naming the paths that moved.
- **The lock owner record now reaches the session that has to read it.** The orchestrator
  holds `audit/.lock` around every child and hands it the nonce; the child must confirm that
  nonce against `audit/.lock/owner` before skipping acquisition, and a missing owner is a
  fail-closed stop (F-0093). But the sandbox seed collects untracked work with `git ls-files
  --others --exclude-standard`, and `--exclude-standard` honours `.gitignore`, whose first
  line is `audit/.lock` — so the one file the gate reads was the one file excluded from the
  tree the gate runs in. Two locally-correct decisions, made in different modules, met on one
  process and left a gate nothing could satisfy. `_seed` copies the owner record in by name.
  It stays ignored inside the worktree, so the seed commit never carries it and `capture()`
  never reports it as the session's work: the record travels IN as readable evidence, never
  back OUT as a claim on a lock. Found by running the tool on itself — the Ouroboros-4 launch
  spent four of its first five sessions refusing to start, and the fifth reported a nonce
  match it had no file to make. `tests/test_lock_seed.py` pins all of it, and its control
  test is the load-bearing one: remove the named copy and exactly one test reddens.
- **A session that obeys the lock protocol can now record what it found.** §4 rule 8 tells a
  writing session to take `audit/.lock` and hold it for the whole session; F-0093 tells an
  orchestrated child that its parent already holds it and hands over the nonce. Either way the
  lock is held while that session's verbs run — and `write.run()` called `_apply()` with no
  lock, so every verb acquired again and was refused by the lock its own session was obeying.
  The documented protocol and the CLI were mutually exclusive, and the CLI won. `run()` now
  resolves the lock the caller is entitled to write under from `XCHECK_LOCK_INHERITED`, the
  nonce the orchestrator already exports to every child and repeats in the role prompt; the
  five role skills that acquire a lock now export it too. It fails closed in the shape the
  skills already describe: a nonce naming no owner record, or naming a different one, is a
  foreign lock and is refused rather than written beside. Naming a nonce is not an escalation
  — only the holder knows it, and a caller that names none is left exactly where it was.
  Measured on the Ouroboros-4 corpus, joining each log to its session by the digest the
  orchestrator recorded: 130 of the 136 sessions that produced nothing were refused this way
  while trying to write down what they had found, and 7 of the 15 that succeeded did so by
  importing `_apply` and handing it the lock object the CLI had no way to build. The witness
  runs the real CLI over all five branches of the table and asserts its shape — exactly two
  write, and one of those is the ordinary standalone case that had to keep working.

  That fix then broke this project's own test suite under a real session, and the live run
  that was measuring it is what found out. `runner.child_environment` copies every `XCHECK_*`
  variable into the child and the orchestrator repeats the nonce in the prompt, so a session
  running the suite handed its audit's nonce to fixtures that are DIFFERENT projects in
  temp directories with no lock of their own — where the guard correctly finds a signal it
  cannot match, and refuses. Filed by the run's own sessions as F-0125 and F-0132, and fixed
  where the findings prescribe: `tests/__init__.py` strips the orchestration environment once
  for the whole test process, so all three routes are covered — in-process fixture commands,
  fixture subprocesses, and the real children the parallel tests launch. `held_lock` is
  untouched, and `tests/test_fixture_isolation.py` asserts it is: an unmatched signal still
  refuses, a disagreeing one still refuses, and a fixture that owns its lock still has its
  own nonce honoured. Each half carries its control arm, which reproduces the findings'
  numbers — undo the strip and U24 goes 50 of 80 red, U26 goes 7 of 48 red, against the 49
  and the 6-plus-1 the sessions reported.
- **A session's success is read from what it moved, not from how its process ended.**
  `classify_outcome` returns `ok` for any child that exits zero. That answers "did the
  process end?" and was being read as "did the role do its job?" — so on the Ouroboros-4 run
  136 of 151 sessions came back `ok` having moved no canonical state, and the loop
  re-dispatched the same charter against the same state. A receipt is now derived after each
  session from the `state_transition` events `write._apply` emits only once a state write is
  durable, so a verb that refused cannot appear in it. `RunResult` carries the answer beside
  the process outcome rather than inside it: `outcome` keeps its meaning and its enum, and
  `protocol` is the second answer, printed on the operator's own result line. Only a verb the
  orchestrator declares counts as progress — `_apply` takes `verb` as a free string and `emit`
  validates no payload, so a session that imports it can mint its own success, and two records
  in the corpus do exactly that; minted transitions are reported rather than dropped, because a
  bypass that leaves no trace is one nobody notices. Nothing is stored for this: the receipt is
  an event, and the event stream is already excluded from the progress digest. Proven by
  reproducing the frozen corpus's 151 / 15 / 136 split through the production derivation, against
  numbers a separate reader counted first. Two counterfactuals, each reddening a disjoint set of
  tests: ignoring the session id makes every session look productive, and dropping the
  declared-verb rule lets a minted verb buy progress.
- **Structured `RunResult`** (phase 2). Outcome, applied paths, patch digest, state delta and
  HEAD before/after travel as one record instead of being re-derived by each caller.
- **One executable contract** (phase 5). The Remediator prompt no longer tells the agent to
  write `fixed-by` into frontmatter, the CLI no longer prints "set `norm-ruling` by hand",
  and the README no longer describes the Auditor ticking a checkbox in `AUDIT.md`. Lifecycle
  transitions happen through `xcheck` verbs, which write `state.json` through the validated
  write path; the documentation contract test now reads the shipped prompts and CLI
  remediation strings, not just the verb names.
- **CI builds its own environment** (phase 6). The workflow installs what
  `ci/release-gate.sh` needs instead of inheriting it from the runner, and the gate names the
  interpreter it ran under. CI has still never been *observed* running — there is no remote.
- **Concurrency and budget ceilings** (phase 7). `parallel_workers` (default 2),
  `max_sessions_per_run` (8), `parallel_budget_minutes` (480) and `parallel_confirm_above`
  (4) replace `ThreadPoolExecutor(max_workers=len(pass_ids))`. A fan-out above the
  confirmation threshold requires an explicit yes.
- **Artifact-aware merge** (phase 8). `harvest()` no longer lifts a few structured fields out
  of the sandbox and deletes the rest: every created or modified audit artifact is manifested
  with its hash and applied transactionally, so finding bodies, pass reports, remainder
  passes and evidence sections survive the worktree's removal. The end-to-end test creates
  all three, deletes the worktree, and asserts the bytes plus a green `lint`.
- **The container image digest pin is an invariant, not advice** (phase 9).
  An unpinned or malformed `container_image` refuses at the door, naming the reference and
  the `docker inspect` line that resolves it; `unsafe_allow_unpinned_image=on` permits it and
  writes the waiver into the run's log file, not just the console. The tag is looked for in
  the last path segment only, so `registry.example.com:5000/team/img@sha256:…` stays pinned —
  a whole-reference scan would have argued private-registry operators into the escape hatch.
- **An egress broker for the container profile** (phase 10, `xcheck/egress.py`).
  `egress_allowlist` puts the audit container on an **internal** docker network with a
  dual-homed sidecar that permits exactly the listed hosts, logs every attempt with its
  verdict, and fails the run closed if it dies mid-session. The default is unchanged:
  with no allowlist the flags are still `--network=none`. The sidecar is ~30 lines of POSIX
  sh run by busybox `nc -lk -e`, so it needs no proxy package and no second image. Two parts
  of the audit's broker design are **NOT BUILT** and named as such in `SECURITY.md`:
  short-lived credentials, and a package-registry proxy.
- **Fuzz and degenerate states extended to every new surface** (phase 11): 111 malformed CLI
  invocations, 31 malformed conf values, and nine degenerate states each of which prints its
  refusal, executes the remedy the refusal named, and shows the remedy working.

- **One wording style for every session a person talks to** (`styles/eli5.md`). The six
  launcher skills each carry a generated block that turns caveman and every other
  output-compression style off and asks for ordinary explained prose — with four kinds of
  text reproduced character for character: quoted evidence, finding ids, §5 statuses, and
  copy-paste commands. `ci/render-style.py` writes the six copies from the one source and
  `--check` compares them byte for byte, so they cannot drift apart;
  `tests/test_style_delivery.py` reads the bytes that arrive at the far end of all four
  packaging paths, including the OpenCode frontmatter stripper — which is why the block sits
  below the frontmatter and not inside it, where that stripper would delete it.
  Nothing has to be installed into your own Claude: `styles/eli5.md` carries frontmatter so
  you *may* drop it into `~/.claude/output-styles/`, and that is optional. Out of scope on
  purpose — xcheck's own command-line text, which dozens of tests compare verbatim and
  release documents quote, and the orchestrated children driven by `xcheck/runner.py`, whose
  output a person reads afterwards as a log rather than talks to. And the honest limit: the
  block is **delivered** through every packaging path, proven here by reading the produced
  bytes, but this repository **does not verify** that a model then wrote in the style the
  block asks for.

Not built, by the audit's own instruction: no new dashboard, no vector database, no graph
database, no distributed orchestration, no plugin abstraction, no multi-user server, no LLM
summary over findings, and no new metrics without a real provider adapter.

Still open, and stated rather than implied: `AUDIT.md` intake is still a prose parser;
prompt injection is undefended; CI has never been observed running; and Ouroboros-4 has not
been run, so the durability figure on the record remains Ouroboros-3's **60.7% at 4 of 39
passes, inconclusive**.

---
- **A charter whose identical attempts moved nothing can be refused — and the gate ships
  `off`, because the measurement says so.** `embargo` and `embargo_after` (§10). The key is
  `(role, charter, declared transitions so far, orchestrator source digest)`: every part
  computed by the orchestrator, none reported by the agent, because `rc=0` is exactly such a
  self-report. `head` and the audit state digest were rejected as components — the courier
  moves both on every session, so a key holding either can never repeat and the gate could
  never fire. Counting only DECLARED verbs is equally load-bearing: `write._apply` takes
  `verb` as a free string, so counting every transition row let a session lift its own
  embargo by inventing one. Replayed session by session over the frozen Ouroboros-4 corpus
  the gate refuses 117/109/102/96 of 136 barren sessions at `embargo_after` 2/3/4/5 — and
  kills 9/8/7/6 of the 15 productive ones, against a threshold registered before the
  measurement of ≥120 refused and 0 killed. It fails both arms at once, and raising the
  bound moves the barren count away from the bar. The corpus cannot arbitrate: it records a
  run whose barren streak before each success was `[0,0,0,0,0,1,2,3,4,9,11,12,12,29,53]`
  and in which 7 of the 15 successes reached state only through a bypass, so those streaks
  are a defect being fought rather than charters being hopeless. The threshold was not
  renegotiated after seeing this; a test pins the falsification, and a change that appears
  to satisfy it has to edit that assertion and name its corpus. Priced by the token
  reader, the gate would have refused sessions worth 16,925,844 tokens and destroyed
  productive ones worth 1,904,083 — a saving stated as a RANGE, 15.0M to 16.9M, whose
  upper bound assumes every refused session was worthless and whose lower bound subtracts
  the destroyed work at its own cost. Neither bound prices the diagnostic evidence inside
  the refused sessions, and that omission is named rather than rounded away: several of
  them are where the lock defect that became the run's most valuable finding was actually
  written down.
- **`metrics` stops saying token cost is unavailable, because half of that was false.**
  MONEY needs provider billing data the tool never sees and no dollar figure is printed —
  that half stands. TOKENS were in the tool's own session logs the whole time: `envelope`
  parses the `tokens used` figure at `finish` and records it on the session's own
  `session_finished` event, so every reader gets one number instead of re-deriving it.
  The figure is reported **per canonical transition**, never per session: a run can make
  its sessions cheaper while making everything it achieved more expensive, and a
  per-session figure would applaud exactly that. A log with no figure is `null`, never
  `0` — an absent measurement is not a measurement of zero, and the payload states its
  own coverage (`sessions_measured`/`sessions_unmeasured`) beside every figure. The
  provider's structured-output mode is deliberately NOT used: `classify_outcome` reads
  the same log text, so changing the output format would risk a working classifier to
  reach data already present. `tokens` is a declared optional field on `state.sessions[]`
  — the closed schema refused it until it was declared, which is how the field was
  caught before it reached a real session — and `OUTPUT_SCHEMA_VERSION` goes to **3**,
  because an added key is a contract change even when it is additive. Priced by this
  reader, the Ouroboros-4 run cost 24,938,795 tokens over 150 of its 151 sessions —
  184,732 per canonical transition, with 21,293,913 of them (85.4%) spent by sessions
  that recorded no canonical transition at all. The one session it cannot price is named
  rather than hidden: `0c7049a9a74137e1`, the single dispatch that never reached
  `session_finished`, whose log ends mid-work with no summary.

- **The two figures the audit falsified are corrected at their source** (phase 1). The
  headline `10,483 tokens per canonical transition` divided a measured numerator by an
  unmeasured denominator: 151 of 158 finished sessions had no token figure at all. The
  ratio is now scoped to the subset that HAS one and reads **69,450**, with the excluded
  count printed beside it, and `OUTPUT_SCHEMA_VERSION` goes to **4** because a reader
  parsing the old key would silently read a different quantity. The W-06 report claimed 75
  minutes inside budget; re-derived from the event stream it is **2h14m38s wall, 8,055.8s
  summed, against a 7,200s ceiling** — the manual report had excluded the 3,600s timeout
  from its own duration. The defect underneath was real: `xcheck loop` checked the budget
  before dispatching, so one session could start inside the ceiling and run a full
  `session_timeout` past it. It now refuses a session that cannot FINISH in what is left.
  *Upgrade cost:* a run that used to start a last long session near the ceiling will now
  stop instead, and any dashboard reading the old ratio key gets a bigger, honest number.

- **No configured executable runs before containment** (phase 2, F-0098, critical).
  `envelope.executable_version` ran `<exe> --version` to record provenance — on the host,
  before `sandbox.enter()`, with `exe` coming from the operator profile. Asking for
  `container` did not prevent it. The agent CLI is now identified by the **sha256 of its
  bytes**, which is a stronger identifier than a self-reported version string and needs no
  execution at all. A witness (marker file absent), a control (the old probe restored,
  marker present), and an AST walk asserting zero execution sites before containment and
  none anywhere in `envelope.py` ship with it. *Upgrade cost:* `executable_version` in new
  records is a digest, not a version string; readers that displayed it as a version should
  say `sha256:…`. Old records keep their old form and are still read.

- **The policy that decides what runs may no longer live inside the audited repository**
  (phase 3, P0). `audit/orchestrator.conf` sat in the tree the agent can write, and a
  `readonly` courier carries any change under `audit/` back out — so a session could edit
  the commands, the sandbox profile, the environment allowlist, the image and the egress
  rules that the next dispatch would then execute. `xcheck/policy.py` splits the 31 keys
  **23 policy / 8 project**; `load_conf` refuses a policy key found in the subject, naming
  every offender and quoting the operator's own values back as the migration. This
  repository migrated: 6 project keys remain in `audit/orchestrator.conf`.
  *Upgrade cost: this is a breaking change and it is the largest one in this release.* An
  existing project whose `orchestrator.conf` carries any of the 23 keys will be refused
  until they move to an operator profile; the refusal prints the file to write and the
  lines to put in it.

- **The operator profile is loaded from outside the subject, refused when absent, and
  recorded** (phase 4, P0). Resolution order is `--policy` → `$XCHECK_OPERATOR_PROFILE` →
  `$XDG_CONFIG_HOME/xcheck/operator.conf`, and the home directory is never derived. There
  is **no fallback to defaults**: `next` and `loop` refuse when nothing resolves, because
  every policy key but the role commands has a safe-looking default and "no policy" would
  quietly become "the default policy" — sessions containing under a profile nobody chose.
  The profile is read fail-closed (an unknown key, a project key, a malformed line each
  refused by name — `sandbox_profil=container` is a containment setting that silently did
  nothing), its sha256 is recorded as `policy_digest` on every dispatch envelope and event,
  and `$XCHECK_OPERATOR_PROFILE` is stripped from the child's environment.
  `docs/operator.conf.example` ships as the starting point. *Upgrade cost:* `xcheck next`
  and `xcheck loop` now fail on a machine with no operator profile. `--dry-run` hits the
  same refusal, deliberately: a dry run that reports a decision the real run would refuse
  is the one answer it must never give.

- **One boundary authorizes every write: role × verb × target × current status** (phase 5,
  F-0069). The status graph was validated and the ACTOR never was, so an open Auditor
  session performed a human-owned transition and nothing objected, and administrative verbs
  like `set-limit` were bound to no identity at all. `write.authorize_dispatch_write` now
  decides at the `run()` boundary, above `_apply`, from a table **derived by executing all
  17 verbs and diffing state** rather than from annotations. A verb in no table refuses,
  proved with a fake verb. Refusals are typed and recorded as `write_refused` events — an
  exception on a terminal nobody watched is not a record. No open dispatch means the
  operator at their own terminal, which is allowed. *Upgrade cost:* an agent session that
  used to perform a human-owned transition now exits non-zero with a refusal naming the
  role that owns it. Charter binding is explicitly NOT enforced here — `charter_hash` is a
  digest and cannot be reversed into ids — which is what phase 6 addresses.

- **The receipt proves the charter's work, not that something moved** (phase 6, P0). Any
  declared `state_transition` by the session counted as progress, so any permitted CLI
  command bought the status `recorded`. `envelope.receipt` gains a third answer,
  **`protocol-violation`**, for a session that moved something that was not its charter's
  work: `CHARTER_WORK` splits each role's verbs into `creates` (a new id, which cannot be
  named in the charter, so it binds by ROLE) and `bound` (an existing record, whose target
  MUST be named in the charter). Binding everything by id would make every Auditor session
  a violation; binding nothing was the original defect. `run_session` passes the charter
  TEXT, not its hash. A call with no role or charter says so (`charter_bound` false) rather
  than returning the old permissive answer. The frozen Ouroboros-4 corpus replays
  **unchanged — 15 recorded, 0 violations, 0 charter-bound** — because that stream carries
  no charter text, and a test asserts the corpus was not edited to add one. *Upgrade cost:*
  `receipt` is a three-value field now; a reader that treats "not `recorded`" as
  "no-progress" will mislabel a violation, so `PROTOCOLS` is enumerated and an unknown value
  is refused at the boundary.

- **Progress and the embargo are scoped to the charter, not to the audit directory**
  (phase 7, F-0117). `audit_state_digest` hashed nearly everything under `audit/` with a
  hand-patched exclusion list, so an unrelated `audit/progress.log` read as progress; the
  embargo keyed on a GLOBAL count of declared transitions, so a transition in any other
  charter lifted the hold on a stuck one. The digest is now canonical `state.json` minus
  the orchestrator's own bookkeeping (`sessions`, `state_revision`), with both arms
  asserted — an irrelevant file must not move it and a real status change must.
  `decision.charter_slice` replaces the global counter with a digest of the pass's own
  queue entry, the findings filed against that pass, and the policy digest. The audit's own
  probe is inverted: an unrelated file-finding now leaves the hold at `True`, while a
  finding against the held pass lifts it. Restated against the frozen corpus the numbers do
  not move, and the reason is measured rather than asserted: 136 barren Auditor dispatches
  over 10 charters contain **0 adjacent pairs on one charter separated by a declared
  transition**. W-05's falsified pre-registration is carried forward unrenegotiated, and
  the embargo default stays `off`. *Upgrade cost:* none for a project that was not relying
  on stray files under `audit/` to keep a loop alive — which is the behaviour this removes.

- **Backpressure on finding debt — this changes behaviour for every existing project on
  upgrade.** `triage_batch_cap` shipped as `0`, which does not mean "no cap": it means the
  orchestrator never asks for a triage sitting, and this repository's own corpus is what
  that produces — 133 reported findings, 0 triaged, over 39 passes. The default is **20**
  now, so an upgraded project that has undecided findings will stop at the triage gate on
  its next `xcheck next` instead of opening another pass. An operator who genuinely wants
  unbounded accumulation writes `triage_batch_cap=0` and has then decided it. A second
  gate answers severity rather than volume: an **open `critical` finding stops new Auditor
  passes** at a human gate naming the finding. "Open" is non-terminal, not merely
  `reported` — an accepted critical is a known hole that is still there. Triage,
  remediation and verification are never gated, because they are the only ways the
  critical stops being open; the gate is read on the auditing branch alone, and a gate
  placed before the decision could only refuse everything and make the critical
  unfixable. `audit_over_critical=on` (a PROJECT key, default `off`) keeps auditing over
  it deliberately.

- **What was audited, as five separate facts — and no schema bump.** `state.json`'s
  `head_before` recorded whatever `git rev-parse HEAD` answered where the write happened,
  and a write verb run by a session happens INSIDE a disposable worktree: this
  repository's own document names `130646f4…`, a commit in no branch, no tag and no other
  clone, which `git fsck` lists as unreachable and the next `git gc` deletes. A dispatch
  now records `subject_commit` (the OUTER repository's commit, reachable from a ref),
  `subject_manifest` (a content digest over index + uncommitted delta, which survives GC
  and history rewriting because it is content rather than a pointer), `sandbox_seed` (the
  disposable worktree commit, kept and labelled rather than deleted — it is what the child
  actually ran against), `controller_commit` (the version of xcheck that did the auditing)
  and `policy_digest`. The orchestrator exports the outer commit to the child, and
  `write.run` reads it there, falling back to the ambient answer for an operator running a
  verb at their own terminal. **Migration: none.** All four fields are OPTIONAL in
  `state.sessions[]`; a record written before this release carries none and loads
  unchanged, and nothing goes back to fill them in — a value invented for an old record is
  not provenance. `head_before` keeps its meaning and its place.

- **The release gate reads the shipped audit corpus.** A green software CI stood beside a
  red audit corpus for the whole 0.9.1 line: `xcheck lint` reported 20 orphan finding
  files, nothing in CI ran it, and that corpus ships inside the sdist and the wheel's
  source tree. `ci/run-checks.sh` runs `xcheck lint` above the build step now, and a
  planted-orphan control in `tests/test_ci_contract.py` proves the step can go red and
  names the file that did it. The 20 orphans were drafts, not findings — each still
  carried the unfilled `id: F-XXXX` template frontmatter, which means no write verb ever
  touched them and no record was ever created. They came from first attempts at P-02 and
  P-04 that filed nothing and were re-dispatched. Preserved verbatim under
  `audit/unadmitted/` with a per-file manifest rather than deleted, and deliberately NOT
  admitted: eighteen name ids that now belong to different findings, and the two whose ids
  are free describe the tree as it stood three weeks and one policy split ago.
  `docs/audit-response-industrial.md` records the decision per group.

- **Provider-native telemetry, with the source recorded rather than implied** (output
  schema **v5**). Everything this tool knew about what a session cost came from a regular
  expression over log prose — the phrase `tokens used`, which loses every structured
  figure the provider already reports and breaks the day a CLI reformats a line. A
  finished session now records `tokens_input`, `tokens_output`, `tokens_cache`,
  `provider_model`, `reasoning_effort`, `provider_request_id`, `tokens_log`,
  `telemetry_source`, `telemetry_disagreement`, `first_output_s` and `idle_s`. The parse
  is unchanged and stays as the labelled FALLBACK: `telemetry_source` is `provider` or
  `log-parse`, and it is a field rather than a naming convention because a reader has to
  be able to tell a reported number from a scraped one. When both sources answer and
  disagree, BOTH are kept and the disagreement is stated — silently preferring one hides
  the case that matters. Figures come from the operator's own role command, via a
  `<log>.telemetry.json` sidecar or an `XCHECK_TELEMETRY {…}` line; xcheck never talks to
  a provider itself. `first_output_s` and `idle_s` are measured by the orchestrator while
  it streams the child, because no provider can report them and they are what diagnoses a
  session that produced only its own header. Null still means NOT MEASURED for every new
  field and is never read as zero, and `metrics` reports coverage PER FIELD — a figure
  measured for 7 of 158 sessions cannot be quoted as if it covered all of them. One
  version bump for the batch, not one per field.

- **Three deadlines the hard timeout could not express, and a `stalled` outcome.** The
  W-06 run's ninth session wrote 1,764 bytes — the agent CLI's banner and the prompt
  echoed back — and then held a session slot for 3600.015s, exactly the wall-clock cap,
  spending an hour of a 7200s budget to produce nothing. `session_timeout` answers "how
  long may a session run"; that session's problem was that it never started.
  `startup_deadline` (120s), `first_action_deadline` (600s) and `idle_deadline` (1200s)
  are POLICY keys, load from the operator profile, and each is derived from that run's own
  numbers: the eight sessions that worked ran 439.3s to 1052.7s and wrote 527KB-1.1MB, so
  600s is longer than the entire life of the fastest real session and 1200s is longer than
  the whole duration of the longest. "Useful action" is mechanical and never a judgement
  about content: bytes written beyond the banner allowance plus the prompt this session
  was handed (a length the orchestrator knows exactly), or a canonical state transition
  recorded by the session — a quiet session that filed a finding has acted. The kill is
  the same `kill_group` as every other bound, so a grandchild dies with it, and the record
  says WHICH deadline fired. `stalled` is a distinct outcome rather than a `timeout` with
  a note, because the two call for opposite operator actions, and it is never retried:
  the same charter under the same command is the most likely thing to stall again.

- **A context capsule per dispatch, instead of 84,610 bytes of mandatory reading.** Every
  role prompt opened with `Read audit/XCHECK.md and audit/AUDIT.md` — 60,112 plus 24,498
  bytes of normative text before the session looked at the project, once per session, and
  multiplied rather than amortised by parallelism. `xcheck/capsule.py` derives, from
  `state.json` and the charter alone, the subset this role on this charter is bound by:
  allowed verbs with their status bounds, the required postcondition, the stop conditions,
  the norms the pass's dimension cites, the material paths its units name with the hash
  each had at dispatch, the findings already filed against it, and the limits in force.
  Measured on this project's own 39 queued passes: the largest capsule is 3,334 bytes
  against 84,610 — a 25x reduction, and 29.1x at worst for the five printed in the test.
  It is derived deterministically, with no model and no network, and content-addressed: the same
  role, charter and state give the same digest, which is recorded on the dispatch envelope
  as `capsule_digest` so a reader can ask whether two sessions were briefed identically.
  **It does not hide the corpus** — every capsule names where the full text lives and says
  it is a subset, and the prompt says so too. The risk of a summary is a session that
  needed something it was not given, which shows up as a worse audit rather than as an
  error, so the completeness control runs all 39 passes and fails on any capsule that
  omits a norm its dimension cites, a finding already filed against it, or a path its
  units name.

- **Raw session logs leave the working tree; a checksummed manifest stays.**
  `audit/orchestrator-logs/` was 106,664,401 bytes across 304 files — 98.6% of everything
  under `audit/`, one more log per dispatch, forever. They were always gitignored, so
  `.git` never carried them, but every walk of the working tree did: `git status`,
  `ls-files --others`, worktree creation, backups, any corpus scan. New logs are written
  outside the tree, at a path resolved `log_dir` (operator profile) → `$XCHECK_LOG_DIR` →
  `$XDG_STATE_HOME/xcheck/logs/<project>` → the system temp directory, printed once per
  dispatch so the location is never a surprise. The home directory is never derived, the
  same rule the operator profile follows. `audit/logs-manifest.jsonl` keeps the name, size
  and sha256 of every log inside the repository; the bytes do not live there, and the
  `log_digest` on the session's own event is untouched, so every existing join — including
  the frozen Ouroboros-4 token table's 151 digests — keeps working. Deleting is an
  explicit verb: `xcheck prune-logs` reports and deletes nothing; `--apply` deletes what
  is past `log_retention_days` (default 30). Nothing in a run ever prunes. `--migrate`
  moves an existing project's in-tree logs out, recording each with its checksum; on this
  repository that is 106.7 MB, taking `audit/` from 108.1 MB to 1.5 MB, and it is the
  operator's command to run rather than something an upgrade does to them.
  `audit-archive/` is refused as a log root and as a prune target — it is frozen evidence.
  **Rewriting git history stays out of scope**: it is destructive, it invalidates every
  digest already recorded, and it is the operator's call.

- **One authoritative execution path; the launchers say they are the escape hatch.**
  Six launcher skills are the product's most visible interface and they run
  `uncontained-direct` — that was already disclosed, but the disclosure named six
  controls, all of them containment, and stopped. The two it omitted are the
  accountability pair: an `uncontained-direct` session writes no invocation envelope and
  produces no session receipt, so nothing records which policy, charter and executable
  were in force and nothing attests what the session did. A reader told about the sandbox
  and not about the receipt concludes the run is unsandboxed but recorded; it is neither.
  `ORCHESTRATED_GUARANTEES` now names all eight, and one canonical disclosure carries them
  into every launcher **byte-for-byte** — the same discipline `_CANON_LOCK_DISCIPLINE`
  already applies to the lock protocol, enforced by the embedded selftest against the
  installed skills and by `tests/test_launch_surfaces.py` against the source tree, with a
  mutation control that reddens naming the file it changed. The five writing launchers
  additionally name `orchestrated` as the authoritative path and themselves as a
  development escape hatch; `/xcheck-status` is the one exemption and states its reason in
  its own body — it is read-only, so it moves no machine state for a courier, an envelope
  or a receipt to mediate or attest. README §7, `XCHECK.md` §4 rule 7 and `SECURITY.md`
  say the same thing in the reader's own document. **No launcher gained or lost a step**:
  the verbs each one runs and its section structure are pinned, and the labelling
  paragraphs are excluded from that pin by construction because they are pinned whole
  elsewhere.

- **Every claim in this release verified by running it, and two controls that were
  missing from the trust model** (phase 16). SECURITY.md's escape matrix was
  **re-derived, not re-read**: `tests.test_container_profile` for the six-row profile
  table and `tests/adversarial` for all 32 cells, against a live docker backend with
  **0 container arms skipped** — a skipped arm reads exactly like a passing one. 38 rows
  and cells, **0 `changed`, 0 `unprobed`**, and the script that prints the verdicts exits
  non-zero on any row whose evidence is absent from the probe output. Two controls the
  code gained during this run were missing from the controls table and are now in it:
  **role authorization** (phase 5) and the **three deadlines** beyond the hard timeout
  (phase 12); every one of the 18 test names the document cites was checked to exist. The
  nine breaking changes are listed together with their migrations at the top of this
  release, so an upgrading operator does not have to reconstruct them from the entries.
  `docs/audit-response-industrial.md` answers the audit item by item, including a section
  naming what this run did NOT do — the lifecycle is still unrun at 133 `reported` / 0
  closed, every P2 item is untouched, incremental audit and model routing are not here,
  history was not rewritten, and CI has still never been observed running. *Upgrade cost:
  none — no behaviour changed in this phase.*

  One test was hardened rather than documented: `test_the_worktree_is_gone` globbed the
  SHARED system temp directory, so a single worktree left behind by an earlier killed run
  reddened it permanently, and a genuine leak by the run under test looked identical. It
  now asserts on the DELTA across the dispatch, with a two-arm control proving a real leak
  is still caught and residue from another run is not charged to this pass.

## 0.9.0 — unreleased · schema 1

The second-audit release. An independent review of `8f244bf` rated xcheck **6/10** as an
engineering product — 7/10 supervised on trusted code, 3/10 autonomous on untrusted
repositories, 2–3/10 as a mandatory release gate. This release works its P0, P1 and P2
lists. No schema change: `state.json` written by 0.8.0 is read by 0.9.0 unchanged.

- **Mutation witnesses classify themselves by what they proved.** A row may call itself
  `consumer-reaching` only when an oracle ran against the mutant and passed; everything
  else is `message-only` and must name the barrier that intercepted it. Sixteen of
  nineteen witnesses are consumer-reaching, all four P0 invariants among them, and the
  gate is red if a P0 is not.
- **An opt-in `container` sandbox profile for `orchestrated` sessions.** `--network=none`, a tmpfs `$HOME`, the
  source checkout mounted read-only, one writable clone, non-zero `--cpus`/`--memory`/
  `--pids-limit`, `--cap-drop ALL`, `--security-opt no-new-privileges`, no host socket,
  and an image pinned by digest. Defaults are byte-identical to 0.8.0; requesting the
  profile without a working docker refuses rather than falling back.
- **Two named launch modes.** `orchestrated` (`xcheck next` / `xcheck loop`) and
  `uncontained-direct` (the six launcher skills). Defined once in `xcheck/util.py`,
  declared by every skill, printed by `xcheck status`, and a structural test refuses any
  containment claim in a shipped document that is not scoped to one of them.
- **An adversarial repository suite** (`tests/adversarial/`). Eight escapes × four
  profiles = 32 measured cells, each decided by an OS-enforced outcome. It found a real
  defect: `docker run --rm` cleans up only when the CLI exits normally, so a `container`
  session killed at its timeout left the container running. Containers are now named at
  launch and removed by name in `Sandbox.leave()` on every path out.
- **Concurrent auditor passes, off by default** (`xcheck/parallel.py`, `parallel_passes`).
  Passes whose unit sets are **disjoint** run at once in separate worktrees; an overlap is
  refused before anything launches, naming the units two passes share. Results merge one at
  a time through the single state transition: each writer declares the `state_revision` it
  read, and a merge computed against a superseded revision is **refused with nothing
  written** and re-derived against fresh state. Concurrent dispatches declare a
  `concurrent_group`, which is what lets several envelopes be open at once without a
  sibling being recorded as crashed — undeclared concurrency is refused exactly as before.
  With the flag off the decision path is byte-identical.
- **A retry policy with an answer for every outcome** (`retry_limit`, default 2). Only
  `provider-error` and a `timeout` whose log names a transport failure are retried;
  `refused`, `blocked`, `crash` and `cancelled` never are, and neither is a session that
  already changed the project — a retry there would run the charter a second time on top of
  its own effects. Waits are `2**attempt` seconds capped at 60, each retried session gets a
  new session id and its own envelope, and every decision is written to `events.jsonl` as
  `retry_considered` with its outcome and reason.
- **`xcheck dashboard`** (`xcheck/dashboard.py`): one self-contained HTML page rendered
  from `status --json` and `metrics --json`, and from nothing else. The constraint is the
  feature — a dashboard that computes its own numbers is the second source of truth this
  whole rebuild removed — so it is asserted three ways: an open-spy showing no file
  outside `audit/` is read, a traceability walk that fails on any figure absent from both
  payloads, and an AST assertion that the module contains no arithmetic and no
  `len`/`sum`/`round`. No script, font, image or network request; the four degenerate
  audits (zero findings, zero passes, not started, `needs-human`) each render a stated
  empty rather than `NaN`, `None` or a bare `%`.
- **A release gate** (`ci/release-gate.sh`): wheel and sdist built, each installed into
  its own clean venv outside the repo, `xcheck --version` and `xcheck selftest` run from
  that venv with the resolved path asserted to be inside it, an undeclared-file check
  over both artifacts, `SHA256SUMS` and a CycloneDX SBOM. The gate never tags and never
  pushes; publishing is the operator's act (`docs/release-checklist.md`).
- `docs/audit-response-0.9.0.md` answers all thirteen items plus the un-numbered mutation-
  battery weakness, each with what was claimed, what was built, and the run or matrix cell
  that proves it — and states the four gaps that stay open: `AUDIT.md` intake is still
  prose, prompt injection is still not defended against, CI has still never been observed
  running, and Ouroboros-4 has not run, so the durability figure on the record is still
  Ouroboros-3's 60.7% from a self-audit frozen at 4 of 39 passes.
- `xcheck --version` exists as a flag for the first time.
- Python classifiers now list exactly what CI runs (3.10–3.12). 3.13 was declared and
  never tested.

---

## 0.8.0 — 2026-08-14 · schema 1

The external-audit release. An independent review rated xcheck **4/10 for production
readiness** (7/10 supervised, 2/10 autonomous) and located the cause precisely:
*Markdown used as a database and a control plane*. State, limits, the queue, pass
coverage and routing lived in Markdown files that several regex readers re-derived
independently — so a queue item inside a code fence dispatched, and `reopen_limit: 999`
inside an HTML comment suppressed a human gate.

This release implements the audit's P0 and P1 lists, eleven items. It is the largest
behavioural change in the project's history and it changes the on-disk contract:
`audit/state.json` is now the only machine state.

Every containment claim in these notes is scoped to the **`orchestrated`** launch mode —
sessions `xcheck next` and `xcheck loop` start. The launcher skills remain
**`uncontained-direct`** and gained no controls in this release; `SECURITY.md` says what
that costs.

### The eleven items

**P0 — without these nothing else counts**

1. **Machine state out of Markdown** (`xcheck/state.py`). `audit/state.json` is the
   single canonical control plane: findings, queue, statuses, limits, class membership
   and pass completion. Markdown keeps the human evidence body. Unknown, missing and
   duplicate entities are refused by one closed schema validator; the schema version
   is stored in the state.
2. **One validated State for every command** (`xcheck/state.py` `load_state`). Every
   consumer — `status`, `next`, `loop`, `lint`, `metrics`, the courier, install and
   upgrade — reaches state through exactly one path: bytes → parse → schema validation
   → cross-record validation → an immutable `State`. There is no longer a reading
   `lint` knows about and `next` does not.
3. **An external test battery and CI** (`tests/`, `ci/run-checks.sh`). `unittest`,
   stdlib only. The green condition is not "the predicate rejects the input" but "the
   real command refused to continue, for the stated reason" — including mutation
   witnesses that delete a real guard and crash/recovery tests between writes.
4. **A contained runner** (`xcheck/runner.py`). The `orchestrated` default is now the
   inverse of what it was: no `--dangerously-skip-permissions`, a disposable git worktree per writing
   session, a minimal environment allowlist, a hard timeout, process-group kill, log
   redaction, per-role capability profiles — and a refusal to run at all when the
   requested containment is unavailable.
5. **Atomic write and recovery** (`xcheck/state.py`). Temp file → `fsync` → atomic
   rename → courier commit, with `state_revision` and the originating HEAD recorded.
   No hand-rolled WAL: one atomic state file is the smaller correct thing.

**P1 — the next real lift**

6. **The monolith split by responsibility** (`xcheck/`). 11.6k lines in one executable
   became a layered package: `util → md_prose → state → views → validate → courier →
   runner → decision → write → cli`, plus `envelope`, `selftest`, `migrate`. The point
   is testable trust boundaries, not tidiness. `bin/xcheck` still works unchanged.
7. **Real packaging** (`pyproject.toml`, this file, `SECURITY.md`). One version source
   with a parity test across all five surfaces, a console script, no runtime
   dependencies, a compatibility matrix, a manifest of shipped file digests, and no
   tracked `.pyc`.
8. **`xcheck upgrade` and installed-core provenance** (`xcheck/upgrade.py`). The
   installed copy records version, digest, source ref, schema version and install
   time. `upgrade` reports every change before making it, refreshes only files whose
   digest still matches the manifest, and **refuses** to overwrite anything the
   operator has modified — `AUDIT.md` is never shipped and never a candidate.
9. **The full invocation envelope** (`xcheck/envelope.py`). Thirteen recorded fields
   per session: provider, agent/model, executable and version, role, charter hash,
   prompt hash, state revision, HEAD before/after, session id, sandbox profile,
   duration and exit status, log digest. Independence is now *measured* from these
   (`cross-provider`, `cross-model`, `same-provider-different-session`, `same`,
   `unrecorded`) instead of asserted by a prompt, and the two weak levels are labelled
   `DEGRADED` everywhere they appear.
10. **A managed session lifecycle** (`xcheck/runner.py`). Heartbeat lease, timeout,
    orderly `cancel`, and outcomes distinguished as `ok` / `refused` / `blocked` /
    `provider-error` / `timeout` / `crash` / `cancelled` rather than collapsed into
    "failed". An abandoned dispatch closes as `crash` with a **null** exit status: an
    exit code nobody observed is never invented.
11. **Machine-readable output** (`xcheck/cli.py`, `audit/events.jsonl`). `status --json`
    and `metrics --json` against a versioned schema the **producer** validates, plus an
    append-only JSONL event stream. A contract only the consumer checks is not a
    contract.

### Also

- `xcheck migrate` converts a legacy Markdown tree to `state.json`. It is a verb the
  operator types — never a path any command takes on its own.
- `LEDGER.md` and finding frontmatter are now *rendered from* state and refuse on
  drift; nothing reads them back as authority.
- Ouroboros-3 (the self-audit) is frozen at 4 of 39 passes with an honest null rather
  than a completion claim.
- `LICENSE` (MIT) is present for the first time. README §16, `pyproject.toml` and both
  plugin manifests had all declared MIT while the tree carried no license text.
- The hardening pass closed four crashes and one silent acceptance that only a fuzz
  finds: `--project` with an embedded NUL or an over-long path escaped as a raw
  `ValueError`/`OSError`; a read-only `audit/` escaped as a raw `PermissionError` from
  the lock acquire; `--max-sessions -3` was accepted and reported a cost cap with exit
  0; and a LEDGER cell escaped `|` but not `\`, so a title containing `\|` opened the
  column the escape existed to close.
- `docs/audit-response-0.8.0.md` states, per audit item, what is satisfied and what is
  only partly satisfied — including the two that are not: there is **no network
  isolation**, and retry classification exists without a retry policy.

### Upgrading from 0.7.x

The audit tree changes shape. Run, in this order, on a clean branch:

```
git checkout -b xcheck-0.8.0
sh install.sh .            # or: bin/xcheck --project . upgrade
bin/xcheck --project . --dry-run migrate
bin/xcheck --project . migrate
bin/xcheck --project . lint
```

`migrate` is not run automatically and never will be. Read its dry-run output first —
it is the only place in the tool allowed to build state from Markdown.

---

## 0.7.0 — 2026-08-02 · schema 0 (pre-`state.json`)

LCC integration: the construal gate (an agent writes its operational reading of a
charter before it may act), scope typing, and typed refusal. Both flags shipped off by
default. Plugin manifests were bumped to 0.7.1 without a corresponding tag — the
version drift the 0.8.0 parity test now makes impossible.

---

## 0.3.0 — 2026-07-21 · schema 0

Claude plugin packaging: `.claude-plugin/plugin.json` and a `skills/` build target.

## 0.2.2 — 2026-07-21 · schema 0

The `stop-disputed` gate. Full-audit report for the reclips project (63 passes, 122
findings, ~110 agent sessions) and retro-3.

## 0.2.1 — 2026-07-20 · schema 0

Courier commits via a dirty-snapshot diff; the remediator commit instruction.

## 0.2.0 — 2026-07-19 · schema 0

The orchestrator itself: `bin/xcheck` with `next` / `loop` / `status` / `unlock` and
the lockfile protocol.

## 0.1.2 — 2026-07-19 · schema 0

Core 0.1.2 — the norm-ratification gate and the adversarial guard. Pilot-1 report and
retro-1.

## 0.1.1 — 2026-07-18 · schema 0

Thin CLI launchers (Claude Code, Codex, OpenCode) and agent-as-pen triage.

## 0.1.0 — 2026-07-18 · schema 0

First tagged release: the methodology core and the pilot-1 watchlist.
