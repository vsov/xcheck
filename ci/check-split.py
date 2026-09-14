#!/usr/bin/env python3
"""Prove the package is a MOVE of the pre-split monolith, not a rewrite.

Extracts the sorted set of top-level `def`/`class` names from the single-file
orchestrator at a given git ref, extracts the union of the same across
`xcheck/*.py` in the working tree, and asserts set equality. A non-empty
symmetric difference means a symbol was invented, dropped or renamed in a phase
whose whole contract was that nothing changes but location.

    python3 ci/check-split.py [REF]      # REF defaults to the pre-split commit

Kept after phase 3 rather than thrown away: phases 5-7 delete symbols on
purpose, and this is where that deletion gets declared instead of discovered.
Names listed in EXPECTED_REMOVED are allowed to be missing, and the script fails
if a name in that list is still present — so the allowance cannot outlive the
removal it documents.
"""

import ast
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
PRE_SPLIT_REF = "c04034d"          # phase-2 HEAD: the last single-file bin/xcheck

# name -> why the phase removes it. A deletion is declared HERE or it is a
# regression: the script fails on an undeclared missing name AND on a declared
# name that still exists, so an allowance cannot outlive its removal.
#
# Phase 5 removes exactly one KIND of symbol: a reader or validator whose subject
# was Markdown machine state. Each one's guarantee moved to `xcheck/state.py`,
# where it is enforced once for every consumer instead of once per call site.
_P5 = "phase 5"
EXPECTED_REMOVED = {
    # --- the frontmatter value gate ---------------------------------------
    "_field_gate": f"{_P5} — the shared canonical-value gate over frontmatter; the "
                   f"closed schema in state.py validates typed fields instead",
    "canonical_record_issues": f"{_P5} — 'is this frontmatter block canonical' is "
                              f"answered by the schema at the reading boundary",
    "construal_issues": f"{_P5} — construal records go through the same closed schema "
                        f"as every other record (CONSTRUAL_FIELDS)",
    # --- finding FILES as the unit of machine state ------------------------
    "finding_files_malformed": f"{_P5} — a finding is a record in `findings[]`, not a "
                               f"file with a frontmatter block to be malformed",
    "finding_files_duplicate_ids": f"{_P5} — duplicate ids are refused at load "
                                   f"(`is declared twice`), before any consumer runs",
    "finding_files_id_issues": f"{_P5} — `id` is pattern-checked by the schema; a "
                               f"filename no longer carries identity",
    "cf_member_identity_issues": f"{_P5} — duplicate and shared class members are a "
                                 f"cross-record rule in state.py",
    # --- LEDGER.md as authority -------------------------------------------
    "ledger_structure_issue": f"{_P5} — LEDGER.md is generated output; its table has no "
                              f"structure the tool believes",
    "ledger_malformed_rows": f"{_P5} — there are no rows to be malformed",
    "ledger_duplicate_ids": f"{_P5} — duplicates are a load-time refusal",
    "ledger_rows_without_file": f"{_P5} — `body_path` is a required field, so a record "
                                f"with no file behind it is unrepresentable",
    "ledger_integrity_stop": f"{_P5} — every condition it reported is now a StateError, "
                             f"not a decision outcome a consumer could ignore",
    # --- routing values read out of two surfaces ---------------------------
    "next_value_issues": f"{_P5} — `next` is a schema enum",
    "next_owner_issues": f"{_P5} — status-owns-next is a cross-record rule in state.py",
    "next_owners": f"{_P5} — the owner map moved to state.py beside the rule that uses it",
    "queue_charter_issues": f"{_P5} — a queue entry is a typed record with `dimension`, "
                            f"`units`, `charter`, `stop` and `done`; a line of prose "
                            f"cannot be a charter (F-0156)",
    # --- the two-surface reconciliation ------------------------------------
    "sync_terminal_triage": f"{_P5} — there is ONE record per finding, so a triage "
                            f"decision has no second copy to be synced into (F-0114)",
    "committed_finding_status": f"{_P5} — it read git HEAD to decide which of two "
                                f"disagreeing surfaces was true; there is one surface",
}

# def/class symbols the split introduced, with the reason. Every entry is a place
# where a pure move would have silently changed meaning; see the phase-3 transcript.
# (Module-level assignments such as `_G` are not tracked — this compares def/class
# names, which is where a rewrite would show.)
EXPECTED_ADDED = {
    "dispatch_route": "phase 5 (sixth audit) — the route and the model it plans, "
                      "resolved once for EITHER dispatcher. The parallel path resolved "
                      "no route at all, so one configuration launched a child "
                      "sequentially and refused on an unfilled `{model}` in parallel. "
                      "The monolith had one dispatcher and no routing, so there was "
                      "nothing to share",
    "prepare_logs": "phase 5 (sixth audit) — the log directory and this session's log "
                    "file, for either dispatcher. `parallel.py` wrote to a hardcoded "
                    "`audit/orchestrator-logs` while the sequential path asked "
                    "`retention.log_root`, which is where the OPERATOR says logs go and "
                    "which refuses a path inside the project. A second dispatcher "
                    "choosing its own directory is a second policy",
    "prepare_launch": "phase 5 (sixth audit) — THE build: one per dispatch, carrying the "
                      "route AND the sidecar. The fourth audit's routing defect was a "
                      "second build in this module that took the sidecar and dropped the "
                      "route; the sixth audit's was a second build in `parallel.py` that "
                      "took neither. One function with both inputs is the only shape in "
                      "which `the route reaches the provider` is a property of xcheck "
                      "rather than of one code path",
    "finish_session": "phase 5 (sixth audit) — THE telemetry finish, including the "
                      "`RouteError` handling that turns a provider mismatch into "
                      "`outcome=refused` instead of losing the envelope. The parallel "
                      "path called `envelope.finish` with no adapter, no planned model "
                      "and no route, and had no handler at all — so the same mismatch "
                      "the sequential path recorded escaped it as an exception",
    "result_problem": "phase 5 (sixth audit) — the pre-merge check on a session's "
                      "result, asked by both dispatchers. The fifth audit moved this out "
                      "of `envelope.finish` (which runs after `sandbox.collect()`) for "
                      "the sequential path; the parallel path had no such check, so a "
                      "pass whose actual model disagreed with its plan was merged",
    "printable_cmd": "phase 2 (fifth audit) — the dispatch argv as one printable line, "
                     "prompt redacted. The preview printed `cmd[:4]`, which stops one "
                     "token short of where `--model` sits, so `--dry-run` could not show "
                     "the model it had routed to. That mattered: the audit's routing "
                     "defect was a dry run previewing one command while the real run "
                     "built another, and the surface whose whole job is to preview could "
                     "not show the difference. `tests/test_smoke_e2e.py` compares the "
                     "printed line against the argv a real child recorded",
    "_class_lines": "phase 15 (fourth audit) — the recurring-class block, produced for "
                    "`xcheck status` and for the triage gate. A separate helper because "
                    "the gate must not be stopped by a detector: it returns nothing "
                    "rather than raising, since a class report that suppressed the "
                    "triage message would have made the audit worse than not having it. "
                    "The monolith had no class detector at all",
    "cmd_doctor": "phase 16 (fourth audit) — the preflight verb. Ten launch-door "
                  "refusals asked at once and for free: an operator used to learn that "
                  "their profile resolved inside the subject, or their image was "
                  "unpinned, or their telemetry was unconfigured, from a session that "
                  "had already been paid for. It calls the refusals rather than "
                  "restating them, so a green preflight cannot disagree with the door. "
                  "The monolith had no preflight of any kind",
    "cmd_reconcile": "phase 14 (fourth audit) — the verb that classifies the backlog "
                     "against HEAD and prints a proposal. Read-only by construction: a "
                     "status transition is human-owned, and a verb that moved findings by "
                     "reading the code would decide the one thing the design reserves for "
                     "a person. The monolith could not tell a finding filed last month "
                     "from one filed against code that has since been deleted",
    "_telemetry_adapter": "phase 11 (fourth audit) — resolves the provider adapter from the OPERATOR PROFILE at the point a session finishes, refusing an unknown name rather than falling back. Never inferred from the role command: a command reading `codex exec …` may be a wrapper, a proxy or a shim, and concluding a provider from a program name decides something the operator did not say. The monolith read one alias table for every machine and had no notion of an operator declaring what theirs reports",
    "cmd_recover": "phase 10 (fourth audit) — the verb that reports what state the event ledger is in and replays a pending outbox, and refuses to touch a stream whose integrity is in question. Phases 7-9 gave the stream an outbox, a chain, an anchor and an immutable prefix; every one of them is a claim until an operator has a command that reads the answer out. The monolith appended events under `except OSError: print(...)` and had no notion of the stream being in a state at all",
    "_event_prefix_problem": "phase 9 (fourth audit) — the courier's half of F-0104: `audit/events.jsonl` now, against its committed bytes at the commit the session started from. A suffix append is carried; a rewrite or a truncation refuses BEFORE anything reaches history. The monolith's courier applied no append-only authorization to the event stream at all, which is what the finding reproduced",
    "cmd_evidence_bundle": "phase 18 (third audit) — the EXPLICIT verb that exports a checksummed evidence bundle with a standalone checker inside it. Explicit and off by default because exporting evidence decides who gets to see it, and nothing in a run ever reads a bundle back. The monolith produced Markdown a reader had to take on trust",
    "cmd_okf": "phase 16 (third audit) — the EXPLICIT verb that generates the derived knowledge bundle. Explicit because a derived layer that materialised itself on every run is a layer nobody chose, and one more thing in the tree for a session to read as if it were true. The monolith had no derived layer at all",
    "_route_for_dispatch": "phase 14 (third audit) — reads the DIMENSION of the passes a "
                           "charter names and the SEVERITIES of the findings it names, "
                           "and asks the routing table which model does this work. The "
                           "monolith ran every role through one command with one model, "
                           "so there was nothing to resolve and no route to record",
    "_sessions_by_route": "phase 14 (third audit) — sessions counted per route, with a "
                          "session nobody routed labelled `unrouted` rather than folded "
                          "into one, so a cheap scan and a security verification are not "
                          "averaged into a single price",
    "activity_probe": "phase 8 (third audit) — the signal `output_deadline` was mistaken "
                      "for: evidence the child cannot manufacture by printing (a "
                      "canonical transition, a validated telemetry sidecar, a write in "
                      "its own worktree), observed by the parent from outside",
    "cmd_prune_logs": "phase 14 — the explicit retention verb. Dry by default; deleting "
                      "evidence is an operator's act and never a step in a run",
    "capsule_line": "phase 13 — the sentence that replaced the mandatory corpus read: it "
                    "names the session's capsule and says in the same breath that the "
                    "capsule is a subset and where the full text is",
    # The policy split. The monolith had one undifferentiated conf and no notion that
    # part of it decided what executes, so neither of these is a moved symbol.
    "charter_slice": "charter-scoped embargo — the state THIS charter's progress "
                     "depends on (its queue entry, the findings filed against it, the "
                     "policy in force), replacing a global transition counter that let "
                     "any movement anywhere lift a stuck charter",
    "_charter_slice_now": "charter-scoped embargo — the slice as it was AT DISPATCH, "
                          "recorded in the stream beside source_digest because a replay "
                          "cannot recompute a historical slice from present-day state",
    "_checked_protocol": "the action-bound receipt — a third protocol answer means the "
                         "field has a vocabulary, and an unrecognised value reaches the "
                         "loop, the printer and the stream reading as 'not "
                         "no-progress', which is the permissive direction",
    "require_policy": "policy split — the dispatch-time gate: an operator profile "
                      "resolves, or `next`/`loop` refuse. There is no fallback to "
                      "defaults, because CONF_DEFAULTS has a safe value for every "
                      "policy key except the role commands, so falling back would run "
                      "sessions under containment nobody chose",
    "conf_pairs": "policy split — one KEY=VALUE parser for both halves of the "
                  "configuration; two copies would be two vocabularies waiting to "
                  "drift apart",
    "write_split_conf": "policy split — the selftest's fixtures still describe a case "
                        "as one conf block, so this routes each line to the file its "
                        "kind now owns and points XCHECK_OPERATOR_PROFILE at the "
                        "profile, outside the subject",
    # W-04 — the dead-letter embargo. The monolith dispatched whatever `_decide_core`
    # returned and had no memory of what a dispatch had already failed to move, so none
    # of these is a moved symbol; the capability did not exist to move.
    "_embargo_key": "W-04 — what must change before the same charter is worth "
                    "dispatching again, built only from values the ORCHESTRATOR "
                    "computes: no agent-reported field enters it",
    "_embargo_scan": "W-04 — one pass over the event stream yielding the generation "
                     "count and the barren attempts per charter",
    "embargo_hold": "W-04 — the single predicate the queue report and the frozen-corpus "
                    "counterfactual both run through, so a measured claim is a claim "
                    "about shipped behaviour",
    "embargo_report": "W-04 — splits the unchecked queue into takeable and held, "
                      "recounting from events every time so a crash cannot leave a "
                      "stale attempt counter behind",
    "_AllModuleGlobals": "selftest monkeypatching must write every module binding, "
                         "not just the defining module's",
    "orchestrator_sources": "source scans whose subject is 'the orchestrator's code' "
                            "must read the package, not one module of eight",
    "_body_text": "phase 5 — lint reads a finding's BODY for the sections §5/§7 require; "
                  "the monolith reached the same text through the frontmatter parser it "
                  "no longer has",
    "_dispatch": "phase 5 — `main` wraps command dispatch in one `except StateError`, so "
                 "an unreadable document is an addressed refusal for EVERY command at a "
                 "single place rather than per-command",
    "plan_complete": "phase 5 — plan completeness is now a predicate over the typed "
                     "`queue` and `catalogs`, replacing the AUDIT.md table walk",
    "cmd_render_views": "phase 6 — the one verb allowed to overwrite a drifted view, "
                        "kept separate from every command that must refuse instead",
    # phase 8 — containment. The monolith had no sandbox, no env allowlist, no bound
    # on the child and no lease: every one of these is new capability, not a moved
    # symbol, so each is declared here rather than passing as "the split".
    "Profile": "phase 8 — a NAMED capability set, so `container` can be added later "
               "as data instead of as a schema change",
    "Sandbox": "phase 8 — the disposable worktree: where the child runs and how its "
               "changes come back through the courier",
    "Redactor": "phase 8 — child stdout is scrubbed of secret values before it is "
                "written to a log the operator ships",
    "run_child": "phase 8 — the ONE place a child is launched: process-group leader, "
                 "hard timeout, lease heartbeat, cancel poll",
    "kill_group": "phase 8 — SIGTERM->grace->SIGKILL on the process GROUP, so a "
                  "grandchild dies with the session that spawned it",
    "child_environment": "phase 8 — the child environment is BUILT from an allowlist; "
                         "the monolith passed `dict(os.environ)`",
    "classify_outcome": "phase 8 — refused/blocked/provider-error/timeout/crash/ok/"
                        "cancelled, so a retry decision is not read off an exit code",
    "resolve_profile": "phase 8 — the effective profile per role, failing closed on an "
                       "unknown name",
    "uncontained_grant": "phase 8 — which skip-permissions flags a profile does not "
                         "contain (the predicate, separate from the refusal)",
    "require_trust": "phase 3 of the industrial-readiness response — the operator "
                     "classifies the MATERIAL (trust_level=trusted|untrusted) or nothing "
                     "is dispatched, and untrusted forces sandbox_profile=container. The "
                     "monolith had no trust classification at all: containment was a knob "
                     "with a default, and the default was not a machine boundary",
    "refuse_uncontained": "phase 8 — the refusal itself, so the same rule is checked at "
                          "the transition and testable on its own",
    "rlimit_preexec": "phase 8 — RLIMIT_CPU/RLIMIT_AS in the child, failing closed "
                      "where `resource` is unavailable",
    "changed_paths": "0.9.0 phase 1 — which paths a session changed, asked of git in "
                     "`--name-only -z --no-renames` form. Replaces `patch_paths`, which "
                     "parsed the human-readable `diff --git` header and returned NOTHING "
                     "for a quoted path, letting a read-only role write the material",
    "_git": "phase 8 — every git call the sandbox makes, with one explicit timeout",
    "cmd_cancel": "phase 8 — `xcheck cancel`: orderly termination, so ending a session "
                  "does not mean killing the orchestrator and stranding its lock",
    "conf_number": "phase 8 — the numeric twin of `conf_flag`: a NUMERIC_CONF key read "
                   "as an int from a Conf OR a plain dict, since the new keys are read "
                   "inside a running session where a raw string would raise",

    # phase 9 — the invocation envelope and the machine-readable surfaces. The
    # monolith recorded a 16-hex session id and nothing else about what ran, so all
    # of this is new capability rather than moved code. (Names that live in a
    # NEW_MODULE — `envelope.py`, `state.py`, `write.py` — are covered by the module
    # entry below and are deliberately not restated here.)
    "json_output_issues": "phase 9 — the `--json` output contract, checked by the "
                          "PRODUCER: an undeclared key never reaches a consumer",
    "independence_summary": "phase 9 — the distribution of MEASURED independence, "
                            "with the degraded levels counted rather than hidden",
    "session_summary": "phase 9 — recorded sessions by outcome and provider",
    "status_payload": "phase 9 — `status --json`, built once so the human printer and "
                      "the machine surface cannot disagree",
    "_emit_json": "phase 9 — validate-then-print, so the schema is enforced where the "
                  "payload is produced",
    "_gate_detail": "phase 9 — a gate's detail as JSON-safe data for the event stream",
    "_material_bytes": "phase 9 — the no-progress digest ignores the orchestrator's own "
                       "session bookkeeping, so a no-op session cannot look productive",
    # phase 10 — packaging. One added name, and it is an adapter rather than logic:
    # installation ADDS an entry point, it never moves the `bin/xcheck` path the six
    # skills and every README instruction invoke.
    "main_argv": "phase 10 — the console-script entry point declared in pyproject.toml; "
                 "`main(argv)` keeps taking argv explicitly so the tests can drive it",

    # 0.9.0 phase 6 — the `container` profile. The monolith had no OS-enforced
    # containment at all, and `Profile`'s own docstring anticipated this: a new profile
    # is new DATA plus the code that talks to a backend. Every name here is that code.
    "backend_probe": "0.9.0 phase 6 — `(ok, detail)` for a container backend, never "
                     "raising, because `status` asks the same question and a missing "
                     "docker is a fact to report there rather than a failed command",
    "require_backend": "0.9.0 phase 6 — the fail-CLOSED half: requesting `container` "
                       "without a working docker refuses and names the alternatives; "
                       "it never degrades to a weaker profile",
    "container_limits": "0.9.0 phase 6 — cpus/memory/pids, all required non-zero, "
                        "because 0 means unlimited and unlimited under a profile whose "
                        "claim is a bound is the claim without the thing",
    "container_capabilities": "0.9.0 phase 6 — the capability REPORT stored in the "
                              "envelope: what the profile actually enforced at launch, "
                              "including `verified_at_launch`, which is False for every "
                              "process-level profile",
    "container_argv": "0.9.0 phase 6 — the `docker run` argv: no network, synthetic "
                      "tmpfs HOME, read-only source mount, two writable mounts, and "
                      "environment forwarded BY NAME so no secret lands in `ps`",
    "host_socket_mounts": "0.9.0 phase 6 — the predicate a test asserts is empty; "
                          "mounting the docker socket would hand the child the daemon "
                          "that contains it",
    "print_sandbox": "0.9.0 phase 6 — `status` prints the resolved profile AND whether "
                     "the last session's containment was verified at launch, which are "
                     "two different claims and so two lines",

    # 0.9.0 phase 10 — the Ouroboros-4 metrics. The monolith computed two rates
    # inline inside `cmd_metrics`; these three names exist because a pre-registered
    # metric may have exactly ONE implementation — the one that ships — and a number
    # a printer computes for itself is a number no JSON consumer can reproduce.
    "metrics_report": "0.9.0 phase 10 — every pre-registered metric, computed once "
                      "from (state, events) and returned as data, so the human printer "
                      "and `--json` cannot disagree about a published figure",
    "_rate": "0.9.0 phase 10 — a percentage that is None on an empty denominator; "
             "0.0 would report 'never happened' where the truth is 'unmeasurable'",
    "_tally": "0.9.0 phase 10 — a count-by-key that keeps the empty bucket, so an "
              "unrecorded reopen cause is written out rather than silently dropped",

    # 0.9.0 phase 11 — concurrency and retry. The monolith serialized everything and
    # retried nothing, so none of these have a pre-split counterpart to move.
    "serialized": "0.9.0 phase 11 — the in-process merge window; `audit/.lock` names a "
                  "pid and so cannot serialize two threads of ONE process",
    "retry_decision": "0.9.0 phase 11 — one answer per declared outcome, so 'retry' is "
                      "a classification with a reason and not a guess at the log",
    "retry_backoff": "0.9.0 phase 11 — the bounded wait between attempts",
    "_dispatch_with_retry": "0.9.0 phase 11 — the only place a session is re-run; it "
                            "refuses to retry a session that already changed the project",
    "_parallel_group": "0.9.0 phase 11 — the queued passes that may run at once, taken "
                       "greedily from the queue head so the order is still the plan's",
    "cmd_dashboard": "0.9.0 phase 12 — the `dashboard` verb; it refuses to render a "
                     "payload its own `--json` schema would reject, because the page "
                     "publishes the same figures",
    "dashboard_payloads": "0.9.0 phase 12 — builds the two published payloads from the "
                          "functions that already own them, so the page and a CI "
                          "consumer cannot be shown different numbers",
    "_session_log_text": "0.9.0 phase 11 — the newest session log, read to tell a "
                         "transport timeout from a spent wall-clock budget",
    "RunResult": "0.9.1 phase 2 — a session's own account of what it did (outcome, "
                 "applied paths, patch digest, state revision and HEAD at both ends). "
                 "It subclasses `int` because the exit code IS the value: eight call "
                 "sites compare the return to 0, and a record that broke them would "
                 "turn a successful session into a silent halt",
    "_session_result": "0.9.1 phase 2 — builds that account at the three points "
                       "`run_session` returns, so the AFTER half is read once at the "
                       "session boundary rather than re-derived by whoever asks later",
    "quarantine_bundles": "0.9.1 phase 3 — the pending quarantine bundles, read from "
                          "the directory itself; there is no state flag to go stale "
                          "against what is actually on disk",
    "_material_paths": "0.9.1 phase 2 — the paths a session moved MINUS the "
                       "orchestrator's own bookkeeping, and how many it compared; "
                       "without that subtraction every session looks like it changed "
                       "something, because the courier commits the envelope the "
                       "orchestrator itself wrote, and without the count a probe that "
                       "lost its subject reports 'nothing changed' in the same voice",
    "material_effect_probe": "0.9.1 phase 4 — the retry gate's OWN answer to whether "
                             "the session changed anything, from a snapshot it took "
                             "before dispatching; the record is the session's answer "
                             "and the two are AND-ed, both failing closed",
    "orchestration_context": "0.9.1 phase 5 — the block appended to every role prompt, "
                             "lifted out of `run_session` so the executable-contract "
                             "test reads the REAL instruction text and not a copy of it",
    "material_snapshot": "0.9.1 phase 4 — the gate's before-picture (dirty set, HEAD, "
                         "canonical state bytes) taken in one place, so the two halves "
                         "of the comparison cannot drift into different questions",
    "unpinned_reason": "0.9.1 phase 9 — WHY an image reference is not pinned to bytes, "
                       "as one sentence: the refusal, the waiver and the capability "
                       "report all read it, so 'pinned' has exactly one definition",
    "container_image_ref": "0.9.1 phase 9 — the image the container profile will run, "
                           "or a refusal. Digest pinning was a comment in 0.9.0, and a "
                           "comment is not a gate; this is the gate",
    "_allowlist_or_empty": "0.9.1 phase 10 — the egress allowlist for the capability "
                           "REPORT, which must never raise: it is called while building "
                           "a refusal, and a report that explodes turns an addressed "
                           "operator error into a traceback",
}

# Modules that are NEW work, not split-out monolith code. Their names are exempt
# from the before/after diff — but they are still checked for collisions, both
# against the other package modules AND against the pre-split name set, because a
# "new" module that redefines an old symbol is exactly the rewrite this script is
# here to catch, wearing a new filename.
NEW_MODULES = {
    "doctor.py": "phase 16 of the fourth-audit response — the preflight: ten checks "
                 "(operator profile outside the subject, trust classified, the selected "
                 "sandbox actually available, the container image pinned by digest, the "
                 "agent CLI present, the provider reachable only by the permitted path, "
                 "the telemetry sidecar supported, evidence and log roots outside the "
                 "project, budgets set and applicable, HEAD matching the audit scope), "
                 "each DELEGATING to the refusal that already guards its own call site, "
                 "with required-vs-advisory a declared list and a non-zero exit when a "
                 "required check fails. Read-only, and it sends no request: verifying "
                 "provider reachability by making one is how a preflight starts costing "
                 "money. The monolith's every one of these conditions was discoverable "
                 "only by dispatching a session that had already been paid for",
    "reconcile.py": "phase 14 of the fourth-audit response — the backlog against HEAD: "
                    "six closed verdicts over every non-terminal finding, each carrying "
                    "the locator, the source hash then and now, and the commit that "
                    "changed it, keyed on the evidence BODY because 0 of 133 findings "
                    "carry a locator or a source_hash. It PROPOSES and cannot write: it "
                    "imports no writing module and runs only `git log` and `git grep`. "
                    "The monolith had no notion of a finding aging against the code it "
                    "describes, so the decision engine kept routing remediation at "
                    "defects that were already fixed",
    "provider.py": "phase 11 of the fourth-audit response — the provider telemetry seam: "
                   "a declared ten-field usage interface, TWO adapters (the codex CLI this "
                   "project's operator profile dispatches, and an in-memory fake that "
                   "exercises the whole interface where no provider CLI exists), an "
                   "adapter chosen from the operator profile and never inferred from the "
                   "role command, and four named refusals for a usage record that cannot "
                   "be believed. The monolith had one alias table sitting beside the code "
                   "that decides what is trustworthy, so vendor trivia and a security "
                   "property lived in the same file; nothing in the pre-split file had an "
                   "interface, a second implementation, or a way for an operator to say "
                   "what their machine reports",
    "ledger.py": "phase 7 of the fourth-audit response — the append path for the event stream and its declared failure mode: a two-phase unit around the state write (RESERVE into a durable, fsynced outbox and prove the ledger appendable BEFORE the write, COMMIT after it), plus an idempotent replay keyed on a digest of the event's own bytes. The monolith appended events with `except OSError: print(...)`, so a transition could land while its event was dropped and every consumer above the stream — budgets, metrics, independence, receipts, the derived bundle — reads it as complete; nothing in the pre-split file had an outbox, a replay or a failure mode at all",
    "classes.py": "phase 17 of the third-audit response — recurring-class candidates over the finding corpus: a deterministic fingerprint of (root cause, norms, evidence shape) with the UNIT deliberately not keyed on, so a class crosses units instead of restating the directory layout; unclassified findings group with nothing, and the output is a PROPOSAL that no verb acts on. The monolith reported 133 findings and 0 class findings and had nothing that could look across them",
    "okf.py": "phase 16 of the third-audit response — the derived knowledge bundle: six record types and three relations projected from state.json plus the event stream into versioned JSONL with a checksummed manifest, a kept conflict rather than a resolved one, and a back channel that is PROPOSAL-ONLY with six forbidden effects refused by name. The monolith had no derived layer of any kind and no notion of a proposal that is not an action",
    "bundle.py": "phase 18 of the third-audit response — an evidence bundle a third party can verify without this repository: enumerated members, a manifest with a size AND a digest per member, a standalone stdlib checker carried inside the bundle and checksummed like everything else, and two members the tool has never had before — the POPULATION the result covers and the OMISSIONS it makes. The monolith produced Markdown a reader had to trust; nothing in the pre-split file exported, checksummed or stated what it did not cover",
    "benchmark.py": "phase 20 of the third-audit response — the benchmark harness, shipped UNRUN: an ordered walk of the measurement points that records a real artefact at each and stops at `dispatch`, an `unmeasured` sentinel that is never 0 and RAISES when asked for, foreign targets pinned to `untrusted` with no per-target override, and the five metric definitions exercised against a fixture whose ground truth is known by construction. The monolith made quality claims and had nothing that could compare two runs of anything",
    "budget.py": "phase 15 of the third-audit response — ceilings that stop a run: per session, per pass, per audit, a no-progress SHARE bound, a per-charter repeat limit and a marginal-value stop, each evaluated at the decision point and priced on measured sessions only. The monolith had `max_sessions` and nothing else; it could not see what a session cost, what share of the spend bought no canonical transition, or that one charter had been bought four times",
    "routing.py": "phase 14 of the third-audit response — model routing as a CLOSED table keyed on (work kind, risk), failing closed to the strong route and refusing to route a high-risk pass cheap even on an operator entry. The monolith ran every role through one command with one model and had no notion of work kind, risk or route; nothing in the pre-split file chose between models",
    "evidence.py": "phase 13 of the third-audit response — the evidence cache: a key over the six inputs that could make evidence wrong (subject bytes, charter, norms, policy digest, auditor identity, capsule), a store outside the tree, and a refusal to carry a VERDICT across material that may have moved. The monolith re-derived every probe on every session and had no notion of an input identity at all; nothing in the pre-split file cached or keyed anything",
    "scope.py": "phase 12 of the third-audit response — diff-directed scope: the units "
                "a change reaches, expanded across the import edges, plus the staleness "
                "of the last UNSCOPED pass. The monolith audited every queued pass from "
                "scratch and had no notion of a base revision or of a full sweep being "
                "owed; nothing in the pre-split file computed either",
    "preprocess.py": "phase 11 of the third-audit response — the deterministic fact "
                     "sheet built before the model runs: inventory, module map, import "
                     "edges, test-to-source mapping, public interfaces, sink locations, "
                     "build/CI surfaces and changed symbols. The monolith made the model "
                     "rediscover every one of these on every session, which is the "
                     "economics finding this module answers; nothing in the pre-split "
                     "file computed any of it",
    "capsule.py": "phase 13 of the industrial-readiness response — the per-dispatch "
                  "context capsule. The monolith had one instruction, `Read "
                  "audit/XCHECK.md and audit/AUDIT.md`, and 84,610 bytes behind it; "
                  "deriving the subset a single charter is bound by is work nothing in "
                  "the pre-split file did",
    "retention.py": "phase 14 of the industrial-readiness response — where raw session "
                    "logs live and how they stop accumulating. The monolith wrote them "
                    "into the audited tree and never deleted one; a log root resolved "
                    "outside the working tree, a checksummed manifest inside it, and an "
                    "explicit prune verb are all work nothing in the pre-split file did",
    "policy.py": "the P0 policy split — which conf keys are the OPERATOR's (what runs, "
                 "and the boundary it runs inside) and which are the PROJECT's (what "
                 "this audit is about). Nothing in the monolith drew that line: the "
                 "whole configuration lived in the subject under audit, which is the "
                 "loop this module exists to cut",
    "egress.py": "0.9.1 phase 10 — the egress broker: an internal docker network for "
                 "the audit container and a dual-homed sidecar that permits exactly "
                 "the allowlisted hosts by HTTP CONNECT and logs every attempt. The "
                 "monolith had `--network=none` or nothing; there was no third option "
                 "to split out",
    "state.py": "phase 4 — the state.json schema, reader and atomic writer; "
                "nothing in the monolith did this",
    "views.py": "phase 6 — renders LEDGER.md and finding frontmatter FROM state "
                "and refuses on drift; the monolith only ever read them",
    "migrate.py": "phase 6 — the one-shot legacy-tree converter; it is the only "
                  "place allowed to build state from Markdown, and it is a verb "
                  "the operator types, not a path any command takes",
    "write.py": "phase 7 — the write verbs; the monolith had no single write path "
                "at all, which is why an agent could change machine state by "
                "editing a Markdown file",
    "envelope.py": "phase 9 — the invocation envelope, the append-only event stream "
                   "and the measured independence level; the monolith recorded a "
                   "session id and nothing else about what ran",
    "dashboard.py": "phase 12 — a self-contained HTML view over `status --json` and "
                    "`metrics --json` that computes nothing of its own; the monolith "
                    "had no rendered surface at all",
    "parallel.py": "phase 11 — concurrent auditor passes over disjoint units, merged "
                   "one at a time through the single state transition; the monolith ran "
                   "one pass at a time and had nothing to merge",
    "upgrade.py": "phase 10 — the manifest-backed refresh of the installed copy; the "
                  "monolith had no upgrade path at all, which is exactly F-0146: "
                  "install.sh copied the core once and nobody owned it afterwards",
}


def top_level_names(source, where):
    tree = ast.parse(source, filename=where)
    return {n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def history_has(ref):
    """True when THIS clone contains `ref` as a commit.

    The discriminator between "the baseline is gone" and "the baseline was never
    here". The exported public tree is its own repository with its own history: it
    never contained the pre-split monolith, so no clone of it can read
    `c04034d:bin/xcheck`. That is not a regression in the package — it is a question
    that cannot be ASKED there.
    """
    probe = subprocess.run(["git", "cat-file", "-e", f"{ref}^{{commit}}"], cwd=REPO,
                           capture_output=True, text=True, timeout=120)
    return probe.returncode == 0


def main(argv):
    ref = argv[1] if len(argv) > 1 else PRE_SPLIT_REF
    # A check that cannot apply must say so and step aside — not fail closed and block
    # everything behind it. `run-checks.sh` runs this BEFORE the battery, so returning 2
    # in the published tree meant `bash ci/run-checks.sh` — the entry point the README
    # tells a user to run — died before a single test executed.
    #
    # The skip is narrow ON PURPOSE. It fires only when this clone does not contain the
    # baseline commit at all. Where the commit IS present, every previous behaviour is
    # unchanged, including the exit 2 below: a baseline that exists but has lost
    # `bin/xcheck` is a real problem and is still reported as one.
    if not history_has(ref):
        print(f"SKIPPED: this clone has no commit {ref}, so the pre-split bin/xcheck "
              f"cannot be read and the move/rewrite question cannot be asked here. "
              f"This is the exported tree, which carries the package but not the "
              f"history it was split out of; run this check in the development "
              f"repository, where {ref} exists.")
        return 0
    p = subprocess.run(["git", "show", f"{ref}:bin/xcheck"], cwd=REPO,
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        print(f"cannot read bin/xcheck at {ref}: {p.stderr.strip()}")
        return 2
    before = top_level_names(p.stdout, f"{ref}:bin/xcheck")

    after, owner, fresh = set(), {}, set()
    for f in sorted((REPO / "xcheck").glob("*.py")):
        if f.name == "__init__.py":
            continue
        names = top_level_names(f.read_text(encoding="utf-8"), str(f))
        dup = names & (after | fresh)
        if dup:
            print(f"FAIL: {f.name} redefines names already defined elsewhere: {sorted(dup)}")
            return 1
        if f.name in NEW_MODULES:
            shadowed = sorted(names & before)
            if shadowed:
                print(f"FAIL: {f.name} is declared as new work but redefines pre-split "
                      f"name(s): {shadowed}")
                return 1
            fresh |= names
        else:
            after |= names
        for n in names:
            owner[n] = f.name

    missing = sorted(before - after)
    added = sorted(after - before)

    print(f"pre-split ref     : {ref}")
    print(f"top-level names   : {len(before)} before, {len(after)} after")
    print(f"modules           : {len({v for v in owner.values()})}")
    for mod, why in sorted(NEW_MODULES.items()):
        print(f"new module        : {mod} — {why}")

    bad = False

    unexplained_missing = [n for n in missing if n not in EXPECTED_REMOVED]
    if unexplained_missing:
        print("\nMISSING (defined before the split, gone now, undeclared):")
        for n in unexplained_missing:
            print(f"  - {n}")
        bad = True
    for n, phase in sorted(EXPECTED_REMOVED.items()):
        if n in after:
            print(f"\nSTALE ALLOWANCE: {n} is listed as removed in {phase} but still exists")
            bad = True

    unexplained_added = [n for n in added if n not in EXPECTED_ADDED]
    if unexplained_added:
        print("\nADDED (not in the pre-split file, undeclared):")
        for n in unexplained_added:
            print(f"  + {n} (in {owner[n]})")
        bad = True
    for n, why in sorted(EXPECTED_ADDED.items()):
        if n not in after:
            print(f"\nSTALE ALLOWANCE: {n} is declared as added but does not exist")
            bad = True

    if bad:
        return 1

    declared = ", ".join(sorted(EXPECTED_ADDED)) or "none"
    print(f"\nsymmetric difference: EMPTY apart from declared additions ({declared})")
    print("the package is a move of the pre-split orchestrator, not a rewrite")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
