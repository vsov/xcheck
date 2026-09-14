"""xcheck orchestrator — drives the audit cycle between its human gates.

Usage:
  xcheck [--project DIR] [--dry-run] [--yes] next  run ONE session (whoever's turn it is)
  xcheck [--project DIR] [--dry-run] [--yes] loop [--step] [--max-sessions N]
                                                   run sessions until a human gate
  xcheck [--project DIR] [--budget SECONDS] loop   (wall-clock budget, checked between sessions)
  xcheck [--project DIR] status [--json]           read-only: state + decision preview
  xcheck [--project DIR] lint                      read-only: ledger<->finding-file consistency
  xcheck [--project DIR] metrics [--json]          read-only: spec §11 metrics
  xcheck [--project DIR] dashboard [--json]        read-only: render audit/dashboard.html
  xcheck [--project DIR] unlock [--force]          diagnose a stale lock (--force removes it)
  xcheck [--project DIR] cancel                    ask the running session to stop (orderly)
  xcheck [--project DIR] render-views              rewrite LEDGER.md + frontmatter from state
  xcheck [--project DIR] [--dry-run] migrate       one-shot: legacy Markdown tree -> state.json
  xcheck [--project DIR] [--dry-run] upgrade       refresh the installed core+templates
  xcheck selftest                                  run the built-in decision-logic tests
  xcheck --version                                 print the installed version and exit

The orchestrator is a courier, not a brain: all methodology lives in the
project's audit/XCHECK.md. This tool only reads audit/state.json, decides
whose turn it is, launches the corresponding agent
CLI headlessly, and stops whenever the methodology hands the turn back to a
human — see README §8 ("The loop halts at …") for the authoritative, exhaustive
list of ORCHESTRATOR halt points, which this docstring deliberately does not
duplicate (a second copy is exactly what drifts out of sync). That is the
orchestrator's halt list, not the methodology's human gates: those are
enumerated canonically in audit/XCHECK.md §3 (F-0084).

Config: audit/orchestrator.conf (KEY=VALUE), created with defaults on first
run. {prompt} in a *_cmd value is replaced with the role prompt; a command
without {prompt} gets the prompt appended as its final argument.

Lock: audit/.lock is a DIRECTORY, created atomically with mkdir (the second
writer's mkdir gets EEXIST — that is the acquisition). Inside it, `owner` is
the owner record (JSON: pid, role, started, host, nonce). Created for every
writing session, removed on exit (including Ctrl-C) with an owner-checked
rmdir — a session removes only a lock whose owner record it wrote, so no
session can ever delete a lock it does not own (§4 rule 8). A live foreign
lock aborts the run; a provably-dead one is diagnosed by `xcheck unlock` and
cleared only with `xcheck unlock --force` (plain `unlock` never removes — F-0095).
A pre-directory `.lock` FILE (old protocol) is a legacy lock: `xcheck unlock
--force` clears it, there is no auto-migration.

Lease (phase 8): while a session runs, the orchestrator renews `.lock/heartbeat`.
A lock whose heartbeat stopped more than `lease_ttl` seconds ago is a DEAD LEASE
and plain `xcheck unlock` reclaims it — no `--force`, because a stopped heartbeat
is positive evidence the owner is gone, which a pathname alone never was. A lock
with a LIVE heartbeat is never reclaimable, and a lock with NO heartbeat (an
older xcheck, a hand-run session) falls back to the pid rules unchanged.
`xcheck cancel` asks the running session to stop: the orchestrator sees the
request, terminates the child's process group, and releases the lock itself.

Source of truth (phase 6): audit/state.json, and nothing else. LEDGER.md and
every finding's frontmatter block are VIEWS rendered from it by `views.py`;
they are never read back as authority, so the whole reconcile-the-two ritual
this docstring used to describe — take the frontmatter for non-triage, take
the ledger for a pending triage decision (F-0131) — no longer has two things
to reconcile. A view that disagrees with the state document is DRIFT: every
command refuses on it by name rather than silently re-syncing, because an edit
there means somebody believed they were writing authority. Re-render with
`xcheck render-views`, or change the state through a write verb.

This module is the command-line boundary: argument grammar, dispatch, and the
commands. The grammar is hand-rolled rather than argparse because every
rejection has to be an ADDRESSED error naming the offending token (README §8).
The usage text above is what `--help` prints, so it lives here and nowhere else.
"""


import json
import math
import os
import socket
import time
from pathlib import Path

from xcheck.util import (
    CONF_DEFAULTS, DEGRADED_INDEPENDENCE, INDEPENDENCE_LEVELS, METRICS_SCHEMA,
    NORM_RULING_TOKEN_RE, ORCHESTRATED, ORCHESTRATED_GUARANTEES, OUTPUT_SCHEMA_VERSION,
    STATUS_SCHEMA, UNCONTAINED, conf_flag, conf_number, construal_key,
    json_output_issues, load_conf
)
from xcheck import __version__, budget, envelope, ledger, policy, scope
from xcheck.state import StateError, load_state
from xcheck.views import refuse_on_drift, verify_views, write_views
from xcheck.write import VERBS as WRITE_VERBS
from xcheck.validate import (
    admitted_scope_issues, canonical_schema_meta_issues, refusal_section_issues
)
from xcheck.runner import (
    Lock, RunResult, audit_state_digest, backend_probe, lock_provably_dead,
    material_effect_probe, material_snapshot, orchestrator_source_digest,
    quarantine_bundles, resolve_profile, retry_backoff, retry_decision, run_session
)
from xcheck.decision import metrics_report, role_and_charter, state_and_decision
from xcheck import parallel

def _emit_json(payload, schema, what):
    """Validate a `--json` payload against its declared schema, then print it.

    The validation is not decoration. `status --json` exists so CI stops scraping
    human output; a contract that is only checked by the consumer is the Markdown
    situation again with the roles swapped, so the PRODUCER refuses to publish a
    payload its own declared schema does not describe."""
    issues = json_output_issues(payload, schema, what)
    if issues:
        print(f"{what} --json: the payload does not match output schema "
              f"v{OUTPUT_SCHEMA_VERSION} — refusing to print a contract-breaking "
              f"document:")
        for i in issues:
            print(f"  - {i}")
        return 1
    print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


def status_payload(project, state, unchecked, checked, kind, detail, lock):
    bundles = quarantine_bundles(project)
    records = list(state.findings) + list(state.class_findings)
    counts = {}
    for r in records:
        counts[r.status] = counts.get(r.status, 0) + 1
    return {
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        # WHICH xcheck produced this reading, beside WHICH schema it read. A consumer
        # holding a status payload with no tool version has to guess what wrote it, and
        # the installed copy's `.provenance.json` answers a different question (what was
        # installed, not what just ran — those disagree exactly when it matters).
        "xcheck_version": __version__,
        "project": str(project),
        "schema_version": state.schema_version,
        "state_revision": state.state_revision,
        "head_before": state.head_before,
        "passes": {"done": len(checked), "queued": list(unchecked)},
        "full_sweep": scope.sweep_status(state),
        "findings": {"total": len(records), "by_status": counts},
        "lock": (None if not lock else
                 dict(lock, live=not lock_provably_dead(lock))),
        "decision": {"kind": kind, "detail": _gate_detail(detail)},
        "quarantine": {"pending": len(bundles), "bundles": bundles},
    }


def cmd_status(project: Path, conf, as_json=False):
    state, unchecked, checked, kind, detail = state_and_decision(project, conf)
    records = list(state.findings) + list(state.class_findings)
    counts = {}
    for r in records:
        counts[r.status] = counts.get(r.status, 0) + 1
    if as_json:
        return _emit_json(
            status_payload(project, state, unchecked, checked, kind, detail,
                           Lock(project / "audit", "?").read()),
            STATUS_SCHEMA, "status")
    print(f"project: {project}  (xcheck {__version__})")
    print(f"state: schema {state.schema_version}, revision {state.state_revision}"
          + (f", head {state.head_before[:7]}" if state.head_before else ""))
    print(f"passes: {len(checked)} done, {len(unchecked)} queued {unchecked or ''}")
    print(f"findings: {len(records)} total — " + ", ".join(f"{k}:{v}" for k, v in sorted(counts.items())) or "none")
    # PHASE 12 (third audit). Printed WHETHER OR NOT diff scope is on, because the thing
    # it guards against is the operator who turned it on months ago and forgot: a
    # diff-scoped run never looks at a file nobody has touched, so the age of the last
    # unscoped pass is the age of the last look at everything. The audit's own warning
    # about incremental mode is that it becomes a blind spot, and this line is where
    # that stops being silent.
    sweep = scope.sweep_status(state)
    on = conf_flag(conf, scope.FLAG)
    print(f"full sweep: {sweep['note']}"
          + (f"  [{scope.FLAG}=on]" if on else f"  [{scope.FLAG} is off — every pass "
                                              f"reads its whole unit]"))
    # Phase 15: the budget ceilings, and the DERIVED values an operator may adopt. The
    # derivations are printed rather than living in a commit message, because a number
    # nobody can re-derive is folklore and this one decides when a run stops.
    if conf_flag(conf, budget.FLAG):
        armed = [(k, conf_number(conf, k)) for k in budget.CEILING_KEYS
                 if conf_number(conf, k)]
        print(f"budgets: on — {len(armed)} of {len(budget.CEILING_KEYS)} ceiling(s) "
              f"armed" + (f" ({', '.join(f'{k}={v}' for k, v in armed)})" if armed
                          else "; every ceiling is 0, which means off"))
        # PHASE 12: whether the statistical half of those ceilings can be believed at all.
        # Printed under `on`, because that is where an operator reads a ceiling and
        # concludes they are protected by it.
        thin = budget.coverage_floor_problem(
            budget.spend(state, envelope.read_events(project)))
        if thin:
            print(f"  {thin.splitlines()[0]}")
            print(f"  statistical (no verdict): {', '.join(budget.STATISTICAL_GATES)}")
            print(f"  hard (unaffected): {', '.join(budget.HARD_GATES)}")
    else:
        print(f"budgets: off — no ceiling stops this run [{budget.FLAG} is off]")
    # PHASE 15 (fourth audit): the recurring-class detector reaches the human who
    # triages. It found four real classes in this corpus and was called by nothing —
    # a library behind a public flag, which is a promise the tool was not keeping.
    from xcheck import classes as classes_mod
    for line in classes_mod.report(state, project):
        print(line)
    for line in budget.derived_report():
        print(f"   derived: {line}")
    bundles = quarantine_bundles(project)
    if bundles:
        # An alarm, not a status line: each of these is a session whose work is sitting
        # unapplied waiting for a human to decide. Printed before the lock and the
        # decision because it outranks both.
        print(f"quarantine: {len(bundles)} pending bundle(s) — session changes captured "
              f"and NOT applied:")
        for b in bundles:
            print(f"  {b['path']} ({b.get('outcome', '?')}, {b.get('role', '?')}, "
                  f"{len(b.get('paths') or [])} path(s))")
    lock = Lock(project / "audit", "?").read()
    if lock:
        # F-0047: label STALE only when the pid is provably dead; a malformed/EPERM
        # pid reads LIVE, matching `unlock`'s fail-closed refusal to clear it.
        # F-0094: host-aware — a foreign-host owner reads LIVE (a local ESRCH says
        # nothing about another host's pid namespace), matching `unlock` too.
        print(f"lock: {lock} ({'STALE' if lock_provably_dead(lock) else 'LIVE'})")
    print_sandbox(conf, state)
    print(f"decision: {kind}" + (f" -> {detail}" if detail else ""))


def print_sandbox(conf, state):
    """The resolved profile for the next session, and whether the LAST session's
    containment was verified at launch.

    Two different questions, so two lines. "What would run now" comes from the conf;
    "what was actually enforced" comes from the envelope's capability report, which is
    the only one of the two that is evidence."""
    writing = resolve_profile(conf, "Remediator")
    reading = resolve_profile(conf, "Auditor")
    line = (f"sandbox: {writing.name} for writing roles, {reading.name} for reading "
            f"roles")
    # PHASE 3: a containment decision nobody can read afterwards is not a decision anyone
    # can audit. The profile says what would be enforced; the trust level says what the
    # operator classified the material as, and only the operator can supply that half.
    try:
        level = policy.trust_level(conf)
    except policy.PolicyError as e:
        level = f"INVALID ({e})"
    line += (f"\n  trust_level: {level}" if level
             else "\n  trust_level: NOT CLASSIFIED — a writing role will refuse to "
                  "dispatch until the operator profile says trusted or untrusted")
    backend = writing.backend or reading.backend
    if backend:
        ok, why = backend_probe(backend)
        line += f" — backend {'READY' if ok else 'UNAVAILABLE'} ({why})"
    print(line)
    last = state.sessions[-1] if state.sessions else None
    if last:
        caps = dict((last.get("sandbox_details") or {}).get("capabilities") or {})
        verified = caps.get("verified_at_launch")
        how = caps.get("enforced_by", "unknown")
        print(f"  last session ({last.get('role', '?')}): profile "
              f"{last.get('sandbox_profile', '?')} — containment "
              + ("VERIFIED at launch" if verified else
                 f"NOT verified ({how}-level isolation only)"))
    # Everything above describes ONE of the two launch modes. Say which, and say what
    # the other one is, or a reader takes the profile above for a property of the
    # project rather than of `xcheck next`. The list is the constant, not a copy of it:
    # a guarantee added to the runner shows up here without anyone remembering to.
    print(f"  the above covers `{ORCHESTRATED}` sessions only — those `xcheck next` "
          f"and `xcheck loop` launch.")
    # NOT "the /xcheck-* launcher skills". The five writing launchers are WRAPPERS over
    # `xcheck next` and are `orchestrated`; naming them here as the direct surface told
    # a reader the shipped launchers had no controls, which stopped being true at the
    # wrapper rewrite. `xcheck-status` is the one direct launcher and it is read-only.
    print(f"  the five writing /xcheck-* launcher skills are WRAPPERS over `xcheck "
          f"next`, so they are `{ORCHESTRATED}` too; `/xcheck-status` is read-only.")
    print(f"  a session a human starts by hand — the role card pasted into an agent — "
          f"is `{UNCONTAINED}`: it does not pass through the profile above, gets none "
          f"of [{', '.join(ORCHESTRATED_GUARANTEES)}], and leaves no session record "
          f"here.")


def _parallel_group(state, unchecked):
    """The queued passes that may run together: the first, plus every later pass whose
    units are disjoint from all of them.

    Greedy from the head of the queue, deliberately. The queue is ordered by the
    Planner, and a scheduler that reordered it to pack more passes in would be making
    the Planner's decision on throughput grounds.
    """
    group = []
    taken = set()
    for pid in unchecked:
        entry = state.queue_entry(pid)
        if entry is None:
            continue
        units = set(entry.units)
        if group and (units & taken):
            continue
        group.append(pid)
        taken |= units
    return group


def _session_log_text(project, limit=8000):
    """The tail of the newest session log, for the ONE retry question that needs it.

    A `timeout` that is the transport's and a `timeout` that is the session's own
    wall-clock budget are the same outcome and opposite decisions, and only the log
    tells them apart. Reading the newest log file is a heuristic — said plainly, because
    the envelope stores the log's DIGEST and not its path — and it is used for nothing
    else: every other outcome decides without it.
    """
    # PHASE 14: logs live outside the working tree now. Both roots are searched — the
    # current one and the legacy in-tree directory — because an upgraded project has
    # yesterday's logs in the old place and today's in the new one, and this reader has
    # no business caring which.
    from xcheck import retention
    files = []
    for logs in (retention.log_root(project, create=False),
                 Path(project) / "audit" / retention.LEGACY_LOG_DIRNAME):
        if logs.is_dir():
            files.extend(logs.glob("*.log"))
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return ""
    return files[0].read_text(encoding="utf-8", errors="replace")[-limit:]


def _dispatch_with_retry(project, conf, role, charter, dry_run, lock, ckey,
                         construal_prompt=False, sleep=time.sleep):
    """Run one session, and re-dispatch it only for a fault the policy calls transient.

    The retry gets a FRESH session — `run_session` mints a new id and opens a new
    envelope every call — because two attempts that shared an id would be
    indistinguishable in the provenance record, which is the one thing the envelope
    exists to prevent.
    """
    limit = conf_number(conf, "retry_limit")
    attempt = 0
    while True:
        # The gate's OWN before-picture, taken here rather than read back from the
        # record afterwards. 0.9.0 asked `audit/state.json` — and only its canonical
        # bytes — whether anything had happened, which is blind to a changed source
        # file, an audit artifact and a commit; the audit reproduced a session whose
        # material effect had already landed being dispatched a second time on the
        # strength of that answer. A file cannot report what a session did to its
        # neighbours. The snapshot ADDS three surfaces to that question; it does not
        # replace the one 0.9.0 asked.
        snapshot = material_snapshot(project)
        rc = run_session(project, conf, role, charter, dry_run, lock=lock,
                         construal=ckey, construal_prompt=construal_prompt,
                         attempt=attempt)
        # The session's own account of what it did, recorded beside the session that
        # made it. Emitted HERE rather than inside `run_session` because this call is
        # inside the writing lock that `cmd_next` holds, and a state write outside that
        # lock is the one-writer violation F-0009 closed.
        if isinstance(rc, RunResult):
            print(f"result: {rc!r}")
            # The protocol answer in words, because `protocol='protocol-no-progress'`
            # inside a repr is a field, not a report. This is the line that tells an
            # operator the difference between "the provider fell over" and "the session
            # ran fine and moved nothing", which is what 136 of the 151 Ouroboros-4
            # sessions did while every one of them printed `ok`.
            if rc.protocol == envelope.PROTOCOL_NO_PROGRESS:
                print(f"protocol: this {role} session ended {rc.outcome} without recording "
                      f"a canonical transition — no finding, coverage, verdict or typed "
                      f"refusal reached audit/state.json, so the charter is exactly where "
                      f"it was. Re-dispatching it unchanged repeats this session.")
            elif rc.protocol == envelope.PROTOCOL_VIOLATION:
                # The third answer, and the one that reads worst: the session DID write
                # to canonical state, and none of it was the work it was dispatched for.
                # Reporting that as `recorded` is what the audit called out — a
                # permitted command buying the session a clean receipt.
                print(f"protocol: this {role} session ended {rc.outcome} and recorded "
                      f"transition(s) — {', '.join(rc.transitions) or 'none'} — but none "
                      f"of them is its charter's work. Expected "
                      f"{envelope.CHARTER_WORK.get(role, {}).get('names', 'its charter')}. "
                      f"The charter is exactly where it was, and something else moved: "
                      f"read the receipt before re-dispatching.")
            envelope.emit(project, "session_result", rc.session_id,
                          role=role, **{k: (list(v) if isinstance(v, tuple) else v)
                                        for k, v in rc.record().items()
                                        if k != "session_id"})
        if rc == 0 or dry_run:
            return rc
        last = (load_state(Path(project) / "audit").sessions or ({},))[-1]
        outcome = last.get("outcome") or "crash"
        # TWO answers, AND-ed, both failing closed. `probed` is what the PROJECT says,
        # over tracked changes, untracked files, audit artifacts and git HEAD; the
        # record is what the SESSION says. Neither authorises anything — either one
        # naming a path blocks the retry — so this is defence in depth rather than the
        # split authority that makes two derivations dangerous. It also closes a real
        # hole: `getattr(rc, "effects_applied", False)` is False for any caller that
        # hands back a plain int, and a record that never arrives cannot block anything.
        probed, compared = material_effect_probe(project, snapshot)
        print(f"probe: compared {compared} path(s) against the session's start — "
              f"{len(probed)} material change(s)"
              + (f": {', '.join(probed[:5])}" if probed else ""))
        blocking = sorted(set(probed) | set(getattr(rc, "material_paths", ()) or ()))
        # A record that reports effects but NAMES none still reports effects. Honouring
        # the bare flag is what "fail closed" means here: the alternative is a session
        # that says "I changed something" being retried because it did not say what.
        changed = bool(blocking) or bool(getattr(rc, "effects_applied", False))
        retry, reason = retry_decision(outcome, attempt, limit,
                                       log_text=_session_log_text(project),
                                       state_changed=changed,
                                       blocked_by=blocking)
        envelope.emit(project, "retry_considered", last.get("session_id"),
                      role=role, outcome=outcome, attempt=attempt, limit=limit,
                      retry=retry, reason=reason, blocked_by=blocking,
                      paths_compared=compared,
                      state_revision=envelope.current_revision(project))
        print(f"retry: {'yes' if retry else 'no'} — {reason}")
        if not retry:
            return rc
        wait = retry_backoff(attempt)
        print(f"retry: waiting {wait}s before attempt {attempt + 2} of {limit + 1}")
        sleep(wait)
        attempt += 1


def cmd_next(project: Path, conf, dry_run, assume_yes=False):
    # F-0009: hold the writing lock across the WHOLE next-transaction — persist
    # the default orchestrator.conf, read state, decide, run the session, and
    # courier-commit — so a concurrent `next` cannot read the same queue and
    # launch a duplicate/stale session, and an abort on a live foreign lock
    # writes nothing (the conf write used to escape the lock, in main()).
    lock = None
    if not dry_run:
        lock = Lock(project / "audit", "orchestrator")
        lock.acquire()
    role = None
    try:
        if lock:
            # Under the lock: this is the only in-transaction default-conf write.
            # Moved out of main(), where it preceded acquire and let an aborting
            # `next` still create the file (§4 one-writer serialization).
            load_conf(project / "audit", write_default=True)
            # F-0009 hardening: re-read conf from disk UNDER the lock so the
            # decision and the launched session act on the current file, not the
            # pre-lock snapshot main() loaded (§4 rule 2, state from files). The
            # lock is per-writing-session by design; a concurrent orchestrator
            # mutating conf is already excluded by that lock — this only closes
            # the same-process stale-snapshot window.
            conf = load_conf(project / "audit")
            # PHASE 5. What stood here was the terminal-triage sync (F-0100/F-0114/
            # F-0131) and the integrity gate that had to run before it: the human's
            # `rejected`/`deferred` decision lived in a LEDGER row that no cycle role
            # would ever copy onto the finding file, so the orchestrator applied it
            # itself, inside the lock, carefully avoiding operator dirt, and the
            # decision path carried `stop-triage-unsynced` as the backstop for the
            # paths that could not write.
            #
            # All of it is gone, because its subject is gone. There is no derived
            # index to fall out of step with a canonical file — one record holds the
            # status, so a triage decision IS the state the moment it is written.
            # This is the clearest measure of what moving the control plane bought:
            # not a fixed sync, no sync, and no mixed-ownership hazard from a write
            # the orchestrator only made to reconcile two representations.
        _state, unchecked, checked, kind, detail = state_and_decision(project, conf)
        if kind.startswith("stop-") or kind.startswith("done"):
            # Phase 9: a gate is the most consequential thing the cycle does — it hands
            # control to a human — so it is the one outcome a machine reader most needs
            # without re-parsing the prose above.
            envelope.emit(project, "gate_reached", None, gate=kind,
                          detail=_gate_detail(detail))
            report_gate(kind, detail)
            return kind
        role, charter = role_and_charter(kind, detail)
        if lock:
            lock.relabel(role)
        # Phase 11: several queued auditor passes with DISJOINT unit sets may run at
        # once. The branch is entered only with `parallel_passes` on AND more than one
        # dispatchable pass AND no overlap — so with the flag off, or with a single
        # pass, or with any overlap, control falls through to exactly the code that ran
        # before this phase. `dry_run` never takes it: a dry run reports the decision,
        # and the decision is still "audit", not "audit these three at once".
        #
        # 0.9.1: `parallel.enabled` now REFUSES rather than answering, so the flag is read
        # BEFORE `not dry_run` — an operator checking their configuration with `--dry-run`
        # must learn that the flag is refused, not be told the decision would have been
        # "audit" and left to discover it on the real run.
        if kind == "run-auditor" and parallel.enabled(conf) and not dry_run:
            group = _parallel_group(_state, unchecked)
            if len(group) > 1:
                # `--yes` is the operator's answer to `parallel_confirm_above`, and it
                # has to be an explicit flag: an orchestrator run normally has nobody
                # at the terminal, so the ceiling REFUSES rather than prompting when
                # stdin is not a tty. Without the flag reaching here, that refusal
                # would name a route that does not exist.
                results = parallel.run_passes(
                    project, conf, group,
                    parallel.agent_pass(project, conf, group, live=()),
                    assume_yes=assume_yes, lock=lock)
                failed = [pid for pid, _a, err in results if err is not None]
                if failed:
                    print(f"stop: {len(failed)} of {len(group)} parallel passes did not "
                          f"merge ({', '.join(failed)}) — investigate before continuing")
                    return "stop-error"
                return kind
        # §4 rule 9: hand the child the construal key for the charter it is about to work
        # on. For a `run-construal` dispatch that key is the one the gate resolved (the
        # ORIGINAL charter's key, not a key of the write-and-stop instruction); for an
        # ordinary dispatch under the gate it is this charter's own key. None with the gate
        # off, so nothing is injected and the child prompt is unchanged.
        ckey = (detail[2] if kind == "run-construal"
                else (construal_key(role, charter or "") if conf_flag(conf, "construal_gate") else None))
        rc = _dispatch_with_retry(project, conf, role, charter, dry_run, lock, ckey,
                                  construal_prompt=(kind == "run-construal"))
    finally:
        if lock:
            lock.release()
    if rc != 0:
        print(f"stop: {role} session returned exit {rc} — investigate before continuing")
        return "stop-error"
    return kind


def cmd_loop(project: Path, conf, dry_run, step, max_sessions, budget=None,
             clock=None, src_digest=None, assume_yes=False):
    clock = clock or time.time
    src_digest = src_digest or orchestrator_source_digest
    start = clock()
    # F-0117: fingerprint the orchestrator's own source at loop start; if it
    # changes mid-run (a self-remediation edited bin/xcheck) STOP rather than keep
    # executing the stale in-memory image.
    src_baseline = src_digest()
    # F-0118: no-progress detector, independent of role. `loop_progress_limit`
    # consecutive sessions that repeat the same decision AND leave audit/ unchanged
    # stop the loop as a non-converging cycle (§9 rule 2), not a cost cap. 0 = off.
    # F-0113: the third public orchestrator.conf numeric key (README §8) — validated
    # at the load_conf boundary like batch_size/triage_batch_cap/max_sessions, so this
    # read returns an already-validated int (0 = off, empty -> default 2). The bare
    # int() cannot raise a raw ValueError here: a Conf never hands back a bad value.
    progress_limit = int(conf.get("loop_progress_limit", "2"))
    prev_kind = None
    stall = 0
    for i in range(max_sessions):
        # A budget that only gates the START of a session is not a budget on the run.
        # The W-06 run measured that exactly: with `--budget 7200`, session 8 was
        # dispatched at 6900s because 6900 < 7200, then ran its full 3600s
        # `session_timeout` — 8055.8s of session time against a 7200s ceiling, 855.8s
        # over, and the run's own report called the budget unexhausted. So the check is
        # on whether the session can FINISH inside the budget, not on whether it can
        # start.
        #
        # The cost is deliberate and named in the message: up to `session_timeout` of
        # budget goes unspent, because a session that would overrun is refused rather
        # than started. An operator who wants that tail back lowers `session_timeout` or
        # raises the budget — both are printed. The alternative, starting it and warning,
        # leaves a ceiling that anything can walk through, which is what was measured.
        if budget is not None:
            spent = clock() - start
            if spent >= budget:
                print(f"stop: --budget {budget}s reached (spent {int(spent)}s)")
                return
            # The FIRST session is always dispatched. A budget below `session_timeout`
            # would otherwise refuse every session and return a run that did nothing,
            # which is a worse answer than one session that may overrun — the operator
            # asked for work, and the first session is the one they can least do without.
            remaining = budget - spent
            worst_case = conf_number(conf, "session_timeout")
            if i and remaining < worst_case:
                print(f"stop: --budget {budget}s has {int(remaining)}s left and a session "
                      f"may run for session_timeout={worst_case}s, so dispatching one "
                      f"could end the run {int(worst_case - remaining)}s over the ceiling. "
                      f"Refusing to start it: a budget a session can walk through is not a "
                      f"budget. Spent {int(spent)}s over {i} session(s). Raise --budget to "
                      f"at least {int(spent + worst_case)}s, or lower session_timeout.")
                return
        current_src = src_digest()
        if (src_baseline is not None and current_src is not None
                and current_src != src_baseline):
            print("stop: the orchestrator's own source changed on disk during the "
                  "run (F-0117) — the "
                  "loop is executing the stale code image loaded at its start, so a "
                  "fix to the orchestrator itself has NOT taken effect. Restart "
                  "`xcheck loop` to pick up the new code (§4 rule 2, state from files).")
            return
        # F-0118: fingerprint the durable audit/ state BEFORE the session so the
        # digest is taken before AND after the SAME session. A session that leaves
        # audit/ (everything decide() reads) byte-identical made no progress and the
        # next iteration will re-dispatch the identical charter. The very first no-op
        # session counts — `loop_progress_limit=N` stops on the N-th no-progress
        # session, not the N+1-th. (README §8: "stop after N consecutive sessions
        # that repeat the same decision and leave audit/ unchanged".)
        pre_digest = audit_state_digest(project)
        kind = cmd_next(project, conf, dry_run, assume_yes)
        if kind.startswith("stop-") or kind.startswith("done") or dry_run:
            # F-0122: return the terminal kind so the CLI boundary can map a session
            # error to a NON-ZERO exit, exactly as the single-`next` branch does
            # (F-0108). A legitimate gate (triage/done/norm-ratification/…) is a normal
            # stop and stays exit 0; only "stop-error" is a failure exit.
            return kind
        if audit_state_digest(project) != pre_digest:
            stall = 0                             # session moved state decide() reads
        elif prev_kind is not None and kind != prev_kind:
            stall = 1                             # no progress, but a fresh decision
        else:
            stall += 1                            # no progress, same decision (or first session)
        prev_kind = kind
        if progress_limit and stall >= progress_limit:
            print(f"stop: no progress (F-0118) — {stall} consecutive "
                  f"'{kind}' sessions left audit/ unchanged; the loop is "
                  f"re-dispatching the same charter on identical state, a "
                  f"non-converging cycle (§9 rule 2). This is neither a session "
                  f"error nor a Verifier verdict: investigate why '{kind}' makes "
                  f"no progress (raise loop_progress_limit only if a legitimate "
                  f"no-op streak is expected).")
            return
        if step:
            input("— session finished; Enter = next, Ctrl-C = stop —")
    print(f"stop: max_sessions={max_sessions} reached (cost cap)")


def _gate_detail(detail):
    """A gate's detail as JSON-safe data. Tuples become lists, Paths become strings,
    and anything else becomes its repr — the stream must never fail to record a gate
    because the gate carried a type nobody anticipated."""
    if detail is None or isinstance(detail, (str, int, float, bool)):
        return detail
    if isinstance(detail, (list, tuple, set)):
        return [_gate_detail(d) for d in detail]
    if isinstance(detail, dict):
        # The embargo gate carries {pass: (attempts, key, last_session)}. Without this
        # branch it reached the stream as a Python repr — recorded, unparseable, and
        # exactly the shape of evidence that cannot be replayed later.
        return {str(k): _gate_detail(v) for k, v in detail.items()}
    return str(detail)


def cmd_prune_logs(project, conf, apply=False, migrate=False):
    """Delete raw session logs older than the retention policy. Reports by default.

    An explicit verb, never a step in a run: a policy that deleted evidence while a
    session was being audited would make the audit's own record depend on when someone
    last ran the tool. `--force` is the apply switch, reusing the flag this CLI already
    has for "yes, actually do the destructive thing"."""
    from xcheck import retention
    days = conf_number(conf, "log_retention_days")
    print(retention.describe_log_root(project, conf))
    if migrate:
        report, _bytes = retention.migrate_logs(project, conf, apply=apply)
        print(report)
        return 0
    report, freed = retention.prune(project, days, apply=apply)
    print(report)
    if apply and freed:
        print(f"freed {freed:,} bytes")
    return 0


def cmd_reconcile(project, conf):
    """`xcheck reconcile` — classify the backlog against HEAD and PROPOSE. Writes nothing.

    PHASE 14 (fourth audit). 133 findings route work against commits that have moved, so
    the decision engine keeps proposing remediation for defects that are already fixed.
    This reads state and the tree and prints a document; every action in it is a command
    the operator types. A status transition is human-owned, and a verb that moved
    findings by reading the code would be deciding the one thing reserved for a person.
    """
    from xcheck import reconcile as reconcile_mod
    state = load_state(Path(project) / "audit")
    rows = reconcile_mod.reconcile(state, project)
    print(reconcile_mod.proposal(rows, reconcile_mod.pass_report(state, project)))
    return 0


def cmd_doctor(project, conf, policy_path=None):
    """`xcheck doctor` — every launch-door refusal, asked at once, for free.

    PHASE 16 (fourth audit). The refusals this prints already existed; what did not was a
    way to hit all of them without dispatching. An operator learned that their profile was
    inside the subject, or their image unpinned, or their telemetry unconfigured, from a
    session that had already been paid for. Read-only: it starts nothing and sends nothing.
    """
    from xcheck import doctor as doctor_mod
    text, code = doctor_mod.transcript(doctor_mod.run_checks(conf, project, policy_path))
    print(text)
    return code


def cmd_recover(project, conf, apply=False):
    """Check the event ledger, and put back only what this tool can prove it wrote.

    Reports by default. `--apply` is the only way it writes, and the one thing it writes
    is a replay of the durable outbox — events xcheck itself reserved and failed to
    append. It never reconstructs an event it has no record of: a plausible fabrication
    is worse than the gap, because the gap is visible."""
    from xcheck import ledger
    from xcheck.runner import Lock
    from xcheck.state import load_state
    anchor = None
    try:
        anchor = load_state(project / "audit").ledger_anchor
    except (StateError, OSError):
        print("note: canonical state is unreadable, so the recorded chain digest cannot "
              "be checked — the chain itself still can be.")
    # PHASE 6 (fifth audit): `--apply` APPENDS, so it takes the single-writer lock every
    # other writer takes. It did not, and a second process appended to the stream while
    # an Auditor held a live lock — the one invariant `audit/.lock` exists to hold.
    #
    # The lock is taken HERE, in the verb, and never inside `ledger.replay`: the ordinary
    # write path replays on its way past, already holding the lock, and a lock acquired
    # down there would be this process deadlocking against itself. One rule, stated at
    # the level where a human typed a command.
    #
    # Without `--apply` nothing is written, so nothing is locked: a read-only report is
    # exactly what an operator worried about a contended ledger needs to be able to run.
    # Taken only when there is something to write. Acquiring the lock EMITS a lease pair
    # into the very stream this verb exists to repair, so a `--apply` with an empty outbox
    # would append two events to say it had nothing to do — and `recover --apply` twice
    # would no longer be the byte-identical no-op the drill asserts. Nothing to replay is
    # nothing to serialize.
    try:
        waiting = bool(ledger.pending(project))
    except ledger.ChainError:
        # The outbox itself is unreadable. Report rather than lock: `recover_report`
        # below is what names the problem, and locking to describe a file we cannot read
        # would be a write in exchange for nothing.
        waiting = False
    lock = Lock(project / "audit", "Recover") if (apply and waiting) else None
    if lock is not None:
        lock.acquire()
    try:
        report, _replayed = ledger.recover_report(project, anchor, apply=apply)
        print(report)
        if not apply:
            print("  (read-only: no lock taken, nothing written — `--apply` is what "
                  "writes, and it takes the writer's lock)")
        # Recomputed AFTER the replay: this asks what the stream is now, not what it was
        # when the command started. See `ledger.EXIT_CODES` for the closed set and why
        # `short` is no longer a success — the events it is missing are still missing.
        state, _detail = ledger.status(project, anchor)
        if state != ledger.INTACT:
            print(f"  exit 1 — {ledger.EXIT_CODES[1]} ({state})")
        return 0 if state == ledger.INTACT else 1
    finally:
        if lock is not None:
            lock.release()


def _class_lines(project=None, state=None):
    """The recurring-class block, or nothing if it cannot be produced.

    Never raises into a gate: a detector that stopped the triage message from printing
    would have made the audit worse than not having it. Reads only.
    """
    from xcheck import classes as classes_mod
    try:
        project = Path(project or ".")
        state = state or load_state(project / "audit")
        return classes_mod.report(state, project)
    except Exception:                                          # pragma: no cover
        return []


def report_gate(kind, detail):
    if kind == "stop-triage":
        print(f"gate: triage needed — {len(detail)} findings in reported. Dialogue: $xcheck-triage (Codex) or /xcheck-triage")
        # PHASE 15: the one moment a human is being asked to sort findings is the moment
        # the recurring-class candidates are worth seeing — triaging four instances of
        # one defect as four unrelated findings is the work this detector exists to save.
        for line in _class_lines():
            print(line)
    elif kind == "stop-needs-human":
        print(f"gate: ⚠ needs-human on {detail} — the human decides (see the finding file)")
    elif kind == "stop-norm-ratification":
        # 0.9.1 phase 5: this told the norm owner to set a FRONTMATTER field. Frontmatter
        # is rendered from state, so the ruling was written into a view, the gate went on
        # reading state, and the block did not lift — the operator's correct action had
        # no effect and no error.
        print(f"gate: norm-ratification on {detail} — a class fix is blocked at the §8 gate; the norm owner (human, NOT Triage) records the winning norm with `xcheck record-ruling {detail if isinstance(detail, str) else '<CF-ID>'} --norm <N1|N1-over-N4> --ruled-by human:<label>` — fail-closed, a `## Norm ruling` note alone does not lift it — then a Remediator resumes")
    elif kind == "stop-refusal":
        _fid, _reason = detail
        print(f"gate: refusal on {_fid} — reason `{_reason}` (§5). The role accepted this charter and could not execute it; the charter STAYS IN FORCE and the finding's status is unchanged. Read the finding's `## Refusal` section, then remove the obstacle it names (supply the material, resolve the dependency, narrow or reword the charter, rule on the norm conflict) and clear the record with `xcheck record-refusal <ID> --clear` so the same charter is dispatched again — re-running it untouched only replays the same refusal")
    elif kind == "stop-construal":
        _role, _charter, _path, _why = detail
        # 0.9.1 phase 5: three frontmatter fields, hand-set, in a construal file whose
        # frontmatter is generated. `admit-construal` writes all three through the
        # validated path and enforces producer != admitter while it does it.
        print(f"gate: construal on the {_role} charter — {_why}. The construal is EVIDENCE, never authority: the producing session may not admit its own (§4 rule 9). Read `{_path}`, and if it is a faithful reading of the charter run `xcheck admit-construal <KEY> --admitter human:<label>` (or your own 16-hex session id — never the producer's); if it is not, fix the CHARTER rather than the construal — the misreading is the finding. Charter: {_charter}")
    elif kind == "stop-critical":
        _fid, _status = detail
        print(f"gate: ⚠ open critical — {_fid} is `{_status}`. No NEW audit pass is "
              f"dispatched while a critical finding is open: another pass adds findings "
              f"on top of the worst one and buys nothing until it is dealt with. "
              f"Triage, remediation and verification are NOT gated — they are the way "
              f"out. Human action: deal with {_fid} (triage it, fix and close it, or "
              f"reject it), or set `audit_over_critical=on` in audit/orchestrator.conf "
              f"to keep auditing over it deliberately.")
    elif kind == "stop-inconsistent":
        print(f"stop: non-terminal statuses with no move {detail} — xcheck status")
    elif kind == "stop-verifier-independence":
        reason, offender = detail
        if reason == "provenance":
            print(f"stop: {offender} is 'fixed' but records no `fixed-by` provenance (F-0096) — the Verifier ≠ fixer rule (§3) cannot be proven for it. `xcheck record-fix {offender} --session <16-hex>` is the only writer of that provenance, so a 'fixed' row without it did not come from the verb — it predates the binding or was migrated in. Nothing here is repairable by hand: `fixed` moves only through `xcheck record-verdict`, which is the transition this gate is refusing, so a human decides — re-fix it under a session that records itself, or rule on it as a §5 exception.")
        else:
            print(f"stop: {offender}'s `fixed-by` is the same session about to verify it (F-0096) — the Verifier must not be the session that produced the fix (§3). Route verification to a different session/agent.")
    elif kind == "stop-broken-class":
        print(f"stop: {detail} is superseded-by-class but its class finding is not closed, or does not list it as a member — a superseded finding is terminal only when a CLOSED CF bidirectionally lists it (README §3, XCHECK.md §8 rule 4). The audit cannot be certified complete over unfinished/broken global remediation; run `xcheck lint` and repair the class dependency (reopen/close the CF, fix its `members:`, or revert the member if the CF was rejected).")
    elif kind == "stop-disputed":
        # §5: disputed resolution is decided by the HUMAN, recorded agent-as-pen
        # in the Auditor dispute-round (§9.3) — Triage never writes it. Resolves
        # to accepted (human sides with the finding) or withdrawn (against it).
        print(f"gate: dispute — {detail} in disputed. The human decides (§5, §9.3), recorded agent-as-pen in the Auditor dispute-round (xcheck-audit disputed) — Triage never writes it: accepted (side with the finding) | withdrawn (against it)")
    elif kind == "done-deferred-debt":
        # F-0049: README §3 and retro-2 (N5) both state `deferred` is NON-terminal
        # debt. The automatic cycle is exhausted (nothing runnable, queue empty),
        # so this is the N5-ratified "complete WITH debt" outcome — NOT a
        # fully-terminal close. Reviving a deferred finding is a human re-triage,
        # outside the automatic cycle. Do not mislabel deferred as "terminal".
        print(f"audit complete WITH deferred debt: {detail} still deferred — NON-terminal re-triage debt (README §3), not a terminal close. The automatic cycle is exhausted; re-triage each (accept to fix, reject to drop) to reach a fully-terminal audit.")
    elif kind == "stop-embargo":
        # §10 `embargo`. Reached only when EVERY takeable pass is held, because a held
        # pass is removed from the queue and the loop routes to the next one — one bad
        # charter must not freeze the rest behind it.
        rows = sorted(detail.items()) if isinstance(detail, dict) else []
        worst = max((a for a, _k, _s in detail.values()), default=0) if rows else 0
        print(f"gate: embargo — every takeable pass is held; {len(rows)} charter(s), "
              f"up to {worst} barren attempt(s) each. Each of those sessions ended "
              f"WITHOUT recording a canonical transition, so dispatching them unchanged "
              f"buys the same nothing again.")
        for pass_id, (attempts, key, last) in rows:
            _role, _charter, _slice, _digest = key
            print(f"  {pass_id}: {attempts} barren attempt(s) as {_role}, last session "
                  f"{last} — key (charter {_charter[:12]}, charter slice "
                  f"{(_slice or 'unrecorded')[:12]}, orchestrator {_digest[:12]})")
        print("An embargo lifts by itself when canonical state moves or the "
              "orchestrator's own code changes — it does NOT lift on a rewritten report, "
              "a new log, a timestamp or a commit. So the human action is one of: fix "
              "what is blocking the charter (a defect in the orchestrator lifts every "
              "held pass at once), narrow or reword the charter so it is executable, "
              "close the pass if the coverage it asks for is genuinely unreachable, or "
              "set `embargo=off` in orchestrator.conf to dispatch it anyway.")
    elif kind == "done":
        print("audit complete: queue empty, all records terminal")
    else:
        print(kind)


def cmd_cancel(project: Path):
    """Ask the running session to stop, without killing the orchestrator by hand.

    Cancel is a REQUEST, not a kill: the orchestrator polls it while waiting on the
    child, terminates the child's whole process group, and then still runs its own
    release — so the lock is freed and the audit state is left where the session left
    it. `kill -9` on the orchestrator would strand both."""
    lock = Lock(project / "audit", "?")
    if not lock.dir.exists():
        print("no lock present — no session to cancel")
        return 0
    info = lock.read() or {}
    if lock.cancel_requested():
        print(f"cancel already requested for the {info.get('role', '?')} session; "
              f"it stops at the next poll (within ~5s of the child's next wait tick)")
        return 0
    lock.request_cancel()
    print(f"cancel requested for the {info.get('role', '?')} session. The orchestrator "
          f"terminates the child's process group at its next poll, then releases the "
          f"lock. The session's changes are NOT applied — a cancelled session has no "
          f"verdict. If no orchestrator is running, this request is inert: clear the "
          f"lease with `xcheck unlock` once its heartbeat goes stale.")
    return 0


def cmd_unlock(project: Path, force, conf=None):
    lock = Lock(project / "audit", "?")
    if not lock.dir.exists():
        print("no lock present")
        return
    legacy = lock.legacy_file()
    info = lock.read()
    # Phase 8 lease. A stopped heartbeat is POSITIVE evidence: the orchestrator that
    # holds this lock renews it every few seconds while its child runs, so a beat older
    # than `lease_ttl` means that renewer is gone. That is a stronger fact than the
    # pathname-staleness F-0095 refused to act on, and it is what makes reclaiming safe
    # WITHOUT `--force`: the check is on the owner's own liveness signal, not on an
    # inference from a name. A LIVE heartbeat is never reclaimable, here or with --force
    # semantics changed — `--force` still overrides, as the operator's own assertion.
    try:
        ttl = conf_number(conf or {}, "lease_ttl")
    except SystemExit:
        # The recovery path must not be blocked by an unrelated conf typo: unlock is
        # what an operator reaches for when everything else is wedged.
        ttl = int(CONF_DEFAULTS["lease_ttl"])
    if not force and not legacy and lock.lease_dead(ttl):
        age = lock.heartbeat_age()
        lock.remove()
        print(f"lock reclaimed: its lease heartbeat stopped {age:.0f}s ago (> lease_ttl "
              f"{ttl}s), so the orchestrator holding it is gone ({info}). No --force "
              f"needed — a dead heartbeat is evidence, not an assumption.")
        return
    if not force:
        # F-0047 + F-0095: non-force `unlock` NEVER removes a lock — it only
        # diagnoses. A directory-protocol lock whose owner record has a PROVABLY-dead
        # pid (a real integer pid the OS reports gone) is reported provably stale and
        # deferred to `--force` (see the F-0095 block below); everything else fails
        # closed with a distinct refusal message:
        #  - a legacy FILE lock (old protocol) — not auto-migrated, needs --force;
        #  - an absent/UNPARSEABLE owner record — a writer may be mid-acquire
        #    (between mkdir and the owner write), so it is not provably dead;
        #  - a lock that is live, an EPERM foreign owner, OR carries a SCHEMA-INVALID
        #    pid ("not-a-pid"): a malformed pid is not a real dead PID, so it is not
        #    provably dead either (reopen poison #1 — the old guard used
        #    `pid_alive()==False`, which is True for a malformed pid, and removed it).
        #  - F-0094: an owner written on a DIFFERENT host — a local ESRCH proves
        #    nothing about a process in another host's pid namespace on a shared
        #    checkout, so it is not provably dead; needs --force.
        if legacy:
            raise SystemExit(
                "audit/.lock is a legacy FILE lock (pre-directory protocol). It is "
                "not auto-migrated; remove with --force only if you are sure no "
                "writing session is active."
            )
        if info is None:
            raise SystemExit(
                "audit/.lock is held but its owner record is missing or UNPARSEABLE "
                "— cannot prove it is dead (a writer may be mid-acquire). Remove with "
                "--force only if you are sure no writing session is active."
            )
        if not lock_provably_dead(info):
            raise SystemExit(
                f"audit/.lock is not provably dead ({info}) — its pid is live, an "
                f"unsignalable foreign owner, not a valid integer, or owned by a "
                f"different host ({info.get('host')!r} != {socket.gethostname()!r}; a "
                f"local liveness check cannot prove a remote process gone). Remove "
                f"with --force only if you are sure no writing session is active."
            )
        # F-0095 (reopen): the lock IS provably stale, but non-force `unlock` no
        # longer removes it. Removal of a directory identified by a pathname is
        # inherently racy: between proving the owner stale and unlinking it, a
        # concurrent unlock could clear the lock and a NEW writer re-acquire the
        # same pathname with a fresh nonce — the unlink then deletes that live
        # lock. POSIX offers no atomic compare-and-delete on a pathname, so an
        # owner-checked re-read before the unlink only narrowed the window, it did
        # not close it (the reopen poison drove two non-force unlocks + a
        # re-acquire straight through it). Rather than CLAIM an atomicity POSIX
        # cannot give, plain `unlock` gives up automatic removal entirely: it
        # diagnoses staleness and defers the delete to the single confirmed
        # administrative path, `--force`, where the operator asserts no writing
        # session is active (§4 rule 8). With no non-force delete there is no
        # check-then-delete race to lose. `--force`'s residual read->unlink window
        # remains: that is a PALLIATIVE — an operator-asserted trade-off, NOT
        # elimination of the race. The real elimination is a kernel advisory lock
        # (`fcntl.flock` on a session-lived fd, released on process death), tracked
        # as backlog C6 (docs/backlog-2-plan.md); until then no path-based unlock,
        # forced or not, can claim atomic compare-and-delete.
        raise SystemExit(
            f"audit/.lock is provably stale ({info}) but non-force `unlock` no "
            f"longer removes it — clearing a lock by pathname cannot be made "
            f"race-free against a concurrent clear + re-acquire (F-0095). Confirm "
            f"no writing session is active, then clear it with "
            f"`xcheck unlock --force`."
        )
    # --force: the operator asserts no writing session is active — unconditional.
    lock.remove()
    print(f"lock removed ({info})")


def cmd_render_views(project: Path):
    """Rewrite every generated view from the state document. Exit 1 if state is unreadable.

    This is the ONE command that is allowed to overwrite a drifted mirror, and it
    is deliberately a separate verb rather than a repair every command performs on
    contact. `refuse_on_drift` stops the others precisely so a human decides which
    of the two texts is true; if the answer is "the state document", running this
    is how they say so — and it is destructive to the edit, which is why it has to
    be typed rather than inferred.

    Note it does NOT call `refuse_on_drift`: refusing to re-render because the
    views need re-rendering is the deadlock version of a fail-closed check."""
    audit = project / "audit"
    try:
        state = load_state(audit)
    except StateError as e:
        print(f"xcheck render-views: {e}")
        return 1
    before = verify_views(state, audit)
    written = write_views(state, audit)
    if before:
        print(f"xcheck render-views: {len(written)} view(s) rewritten, "
              f"{len(before)} of them had drifted:")
        for d in before:
            print(f"  - {d}")
    else:
        print(f"xcheck render-views: {len(written)} view(s) rewritten (none had drifted)")
    return 0


def cmd_lint(project: Path):
    """Read-only report over the HUMAN-facing tree. Exit 1 on issues.

    Phase 5 changed what this command is. It used to be the only place full
    validation lived — 302 lines of ledger-row, frontmatter, queue-entry and
    construal checks that `next`, `loop` and `metrics` never ran (F-0147). Every
    one of those checks now runs at `load_state`, for every command, so lint no
    longer owns them and cannot own them: by the time this function has a `State`,
    the document already passed the schema.

    What is left has a subject Markdown still owns, and it is reported in two
    parts. First, whether the state document loads at all — that is the ONE thing
    lint can say that a successful `state_and_decision` cannot, because a failing
    document stops every command including this one. Then the tree: body files
    that a record points at but that are not there, files sitting in the tree that
    no record claims, evidence-body sections a status requires, and the binding
    between XCHECK.md §2 prose and the code's canonical schema."""
    audit = project / "audit"
    try:
        state = refuse_on_drift(load_state(audit), audit)
    except StateError as e:
        print(f"xcheck lint: {e}")
        return 1

    issues = []
    records = list(state.findings) + list(state.class_findings)

    # A record names its own evidence body. A dangling body_path is not a schema
    # error — the schema validates the document, not the filesystem — so it is
    # exactly the kind of tree defect that belongs here.
    claimed = set()
    for r in records:
        claimed.add(r.body_path)
        if not (audit / r.body_path).is_file():
            issues.append(f"{r.id}: `body_path` names {r.body_path}, which does not exist — "
                          f"the record's evidence is unreadable")
    for pl in state.plans:
        claimed.add(pl.body_path)
        if not (audit / pl.body_path).is_file():
            issues.append(f"{pl.id}: `body_path` names {pl.body_path}, which does not exist")
    for c in state.construals:
        claimed.add(c.body_path)
        if not (audit / c.body_path).is_file():
            issues.append(f"{c.key}: `body_path` names {c.body_path}, which does not exist")
    for q in state.queue:
        if q.coverage:
            claimed.add(q.coverage.report_path)
            if not (audit / q.coverage.report_path).is_file():
                issues.append(f"{q.id}: coverage report {q.coverage.report_path} does not exist "
                              f"— a pass counts as done only with its report (§4 rule 5)")

    # The other direction: a file nobody claims. Under Markdown-as-database an
    # orphan file was ambiguous (was it state? was it a draft?); with one state
    # document it is unambiguous — nothing reads it, so it is either an unfinished
    # record or a leftover, and both want a human.
    for sub in ("findings", "plans", "construals", "passes"):
        d = audit / sub
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            rel = f"{sub}/{f.name}"
            if rel not in claimed:
                issues.append(f"{rel}: no record in state.json points at this file — "
                              f"nothing reads it (finish the record, or remove the file)")

    # §5: a refusal is a reason code AND the prose stating what is missing.
    issues += refusal_section_issues(state, audit)

    # §7 admitted scope, gated by §10 `scope_typing` (default off). Read through the
    # Conf BOUNDARY, so the flag arrives as a real bool and a typo is an addressed
    # error rather than a silent `off` (F-0113). Lint-surface only: this checks that a
    # scope DECLARATION exists with both halves, a document-completeness property; the
    # demanded ⊆ admitted judgement is the Verifier's and is not modelled in code.
    if conf_flag(load_conf(audit), "scope_typing"):
        issues += admitted_scope_issues(state, audit)

    # F-0042: a CF blocked at the §8 norm-ratification gate must carry a syntactically
    # valid `norm_ruling` once the norm owner has ruled. `pending`/empty is the legal
    # waiting state (the decision path stops on it); anything else that is not a norm-id
    # token is a ruling nobody can read.
    for r in records:
        val = (r.norm_ruling or "").strip()
        if val and val.lower() != "pending" and not NORM_RULING_TOKEN_RE.match(val):
            issues.append(f"{r.id}: `norm_ruling` '{val}' is not a norm-id token "
                          f"(e.g. `N1` or `N1-over-N4`) — the §8 gate cannot read it")

    # F-0146: the installed copy (§2: "copy of this file — the audit is self-contained")
    # had NO owner and NO route that updates it after install — a core edit left every
    # session reading a superseded charter while the schema meta-check below fed on the
    # stale copy's §2. Where the project ships the core itself (a root XCHECK.md exists
    # beside audit/), lint OWNS the divergence as an addressed error; in an installed
    # project with no root copy there is no source to drift from and nothing is checked.
    root_core, copy_core = project / "XCHECK.md", audit / "XCHECK.md"
    if root_core.is_file() and copy_core.is_file() and \
            root_core.read_text(encoding="utf-8", errors="replace") != copy_core.read_text(encoding="utf-8", errors="replace"):
        issues.append("XCHECK.md: the root core and the installed audit/XCHECK.md copy diverge — the copy has no other sync owner, and stale §2 prose feeds the schema meta-check; re-copy the core (install.sh / bootstrap step) so sessions and lint read the current charter (F-0146)")

    # CF-0001 (meta-check, human ruling point 3): bind the code's schema table to the §2
    # prose in this project's audit/XCHECK.md — a field declared in §2 with no validator,
    # or a validator for a field §2 dropped, is drift lint must surface. This is what
    # makes 'add a field to §2 without a validator' impossible.
    issues += canonical_schema_meta_issues(audit / "XCHECK.md")

    if issues:
        print(f"xcheck lint: {len(issues)} issue(s)")
        for it in issues:
            print(f"  - {it}")
        return 1
    print(f"xcheck lint: clean ({len(records)} records, {len(state.queue)} passes, "
          f"{len(state.plans)} plans, {len(state.construals)} construals; "
          f"state revision {state.state_revision})")
    return 0


def independence_summary(state):
    """The distribution of MEASURED independence over recorded verdicts.

    A verdict that predates phase 9 carries no level; it is counted as `unrecorded`
    rather than dropped, because a metric that quietly ignores the cases it cannot
    measure reports a cleaner picture than the evidence supports — the promise-width
    defect this project keeps finding in its own fixes."""
    by_level = {}
    for r in list(state.findings) + list(state.class_findings):
        if r.status not in ("closed", "reopened"):
            continue
        level = r.independence or "unrecorded"
        by_level[level] = by_level.get(level, 0) + 1
    return {
        "by_level": by_level,
        "measured": sum(by_level.values()),
        "degraded": sum(v for k, v in by_level.items() if k in DEGRADED_INDEPENDENCE),
        "levels": list(INDEPENDENCE_LEVELS),
        "degraded_levels": sorted(DEGRADED_INDEPENDENCE),
        "ceiling": ("a level describes WHICH provider and model ran each side and "
                    "nothing more — none of them proves the absence of shared training "
                    "data, a shared cache, or the same operator driving both sessions"),
    }


def session_summary(state):
    by_outcome, by_provider = {}, {}
    for s in state.sessions:
        key = s.get("outcome") or "in-flight"
        by_outcome[key] = by_outcome.get(key, 0) + 1
        p = s.get("provider") or "unknown"
        by_provider[p] = by_provider.get(p, 0) + 1
    return {"total": len(state.sessions), "by_outcome": by_outcome,
            "by_provider": by_provider}



def cmd_okf(project, conf):
    """Generate the derived knowledge bundle. An EXPLICIT verb, never a step in a run.

    A derived layer that materialised itself on every dispatch would be a layer nobody
    chose, and one more thing in the tree for a session to read as if it were true.
    """
    from xcheck import okf
    if not okf.okf_enabled(conf):
        print(f"okf: off — nothing generated [{okf.FLAG} is off]")
        print("   The bundle is derived and disposable. Turn it on only if someone will "
              "read it; it changes nothing either way.")
        return 0
    from xcheck.envelope import read_events
    state = load_state(project / "audit")
    records = okf.project_bundle(state, read_events(project))
    pairs = okf.conflicts(records)
    # PHASE 15 (fourth audit): the recurring-class candidates travel as PROPOSALS in the
    # bundle. `classes.proposals_for` had no caller at all — the detector found four real
    # classes in this corpus and nothing ever asked it for the proposal form, so the one
    # artefact a human could act on was never produced.
    from xcheck import classes as classes_mod
    found, _ungrouped = classes_mod.candidates(state, project)
    # The proposals cite records from THIS projection — see `proposals_for`. Handing it
    # the findings instead is what let it mint evidence the bundle never exported.
    class_proposals = classes_mod.proposals_for(found, records) if found else []
    manifest = okf.write_bundle(project, records, proposals=class_proposals)
    print(f"okf: {sum(manifest['record_types'].values())} record(s) -> "
          f"{okf.bundle_dir(project)}")
    for kind in okf.RECORD_TYPES:
        print(f"   {kind:<12} {manifest['record_types'][kind]}")
    print(f"   conflicts KEPT: {len(pairs)} pair(s) — both records survive, joined by "
          f"`{okf.CONFLICTS_WITH}`; nothing here resolves one")
    problems = okf.verify_bundle(project)
    print("   manifest: " + ("ok, every checksum matches"
                             if not problems else f"PROBLEMS {problems}"))
    print("   This bundle is DERIVED from audit/state.json and audit/events.jsonl. It is "
          "never an input, and it proposes only — a proposal reaches state.json when a "
          "human reads it and runs a write verb.")
    return 1 if problems else 0


def cmd_evidence_bundle(project, conf, policy_path=None):
    """Export a self-contained, checksummed evidence bundle. An EXPLICIT verb, off by
    default, written OUTSIDE the working tree by the retention rules already in force.

    Off by default because an export is a decision about who sees the evidence, and a
    tool that made that decision on every run would be making it for the operator.
    """
    from xcheck import bundle, policy as policy_mod
    if not bundle.bundle_enabled(conf):
        print(f"evidence-bundle: off — nothing written [{bundle.FLAG} is off]")
        print("   Turn it on when a result has to leave this machine. Nothing else "
              "changes: no command reads a bundle, ever.")
        return 0
    resolved = policy_path or policy_mod.resolve(project)
    digest = policy_mod.profile_digest(resolved) if resolved else None
    root, manifest = bundle.write_evidence_bundle(project, conf, policy_digest=digest,
                                         policy_source=str(resolved) if resolved else None)
    print(f"evidence-bundle: {len(manifest['members'])} member(s) -> {root}")
    for name in sorted(manifest["members"]):
        meta = manifest["members"][name]
        print(f"   {name:<24} {meta['bytes']:>10,} bytes  sha256:{meta['sha256'][:16]}")
    print(f"   population: {manifest['population']['summary']}")
    print("   omitted: " + ", ".join(what for what, _why in bundle.OMISSIONS))
    print(f"   verify it anywhere: cd {root} && python3 {bundle.CHECKER_NAME}")
    print("   The checker imports the standard library only. It does not need xcheck, "
          "this repository, or a network — which is the whole point of a bundle.")
    return 0


def cmd_metrics(project: Path, as_json=False):
    """Read-only spec §11 metrics, computed from the one state document (B3).

    The old integrity gate at the top of this function is gone: `load_state` IS
    the gate, and it is the same one `next` and `loop` pass through. A KPI over a
    corrupt record was possible only while metrics had its own reading path
    (CF-0001, F-0055); there is one path now, so "metrics published numbers lint
    would have refused" is not a defect that was fixed, it is a sentence that no
    longer parses."""
    try:
        state = refuse_on_drift(load_state(project / "audit"), project / "audit")
    except StateError as e:
        print(f"metrics unavailable: {e}")
        return 1
    records = list(state.findings) + list(state.class_findings)
    total = len(records)
    by_status, by_dim, by_sev, attempts_hist = {}, {}, {}, {}
    for r in records:
        by_status[r.status] = by_status.get(r.status, 0) + 1
        by_sev[r.severity] = by_sev.get(r.severity, 0) + 1
        by_dim[r.dimension] = by_dim.get(r.dimension, 0) + 1
        attempts_hist[r.attempts] = attempts_hist.get(r.attempts, 0) + 1

    # auditor accuracy (F-0015) and fix durability (F-0016) used to be computed HERE,
    # in the printer. They are now two of the eight pre-registered metrics and live in
    # `decision.metrics_report` with the other six, for the reason the audit gave about
    # everything else in this tool: one definition, one reader. Their meaning is
    # unchanged — the denominator of accuracy is only findings that actually reached
    # validation (F-0061: `withdrawn` belongs in it, and dropping it once turned a real
    # 50% into 100%), and durability counts a first-pass close, never a reopened one.
    passes_done = sum(1 for q in state.queue if q.done)
    cfs = [c.id for c in state.class_findings]
    # F-0017, F-0050, F-0125: DISTINCT member ids across all CFs — N7's `total members`
    # is the count of member findings, not repetitions of one id. The schema refuses a
    # repeated or doubly-claimed member, so on any state that loads this equals the
    # naive sum; the set is kept because the KPI's definition is a set, not because a
    # duplicate could still arrive.
    cf_members = len({m for c in state.class_findings for m in c.members})

    indep = independence_summary(state)
    # 0.9.0 phase 10: the eight pre-registered metrics and the P2 breakdowns, computed
    # by `decision.metrics_report` over the state AND the event stream. `acc`/`dur`
    # above are the same two numbers by the same definitions; the payload takes them
    # from the report, so a divergence between the human printer and the machine
    # surface is not something this function is able to express.
    report = metrics_report(state, envelope.read_events(project), independence=indep)
    acc, dur = report["auditor_accuracy_pct"], report["fix_durability_pct"]

    if as_json:
        payload = {
            "output_schema_version": OUTPUT_SCHEMA_VERSION,
            "project": str(project),
            "passes_done": passes_done,
            "findings": {"total": total, "by_status": by_status, "by_severity": by_sev,
                         "by_dimension": by_dim,
                         "attempts": {str(k): v for k, v in attempts_hist.items()}},
            "class_findings": {"ids": cfs, "members": cf_members},
            "independence": indep,
            "sessions": session_summary(state),
        }
        payload.update(report)
        return _emit_json(payload, METRICS_SCHEMA, "metrics")

    def _pct(v, why="no findings reached this stage"):
        # `None` is "unmeasurable", and WHICH empty denominator made it unmeasurable
        # differs per metric: a project with no queued pass and one with no killed
        # session are both n/a, and saying "no findings reached this stage" for either
        # would be a wrong reason printed with the confidence of a right one.
        return f"{v:.1f}%" if v is not None else f"n/a ({why})"

    print(f"project: {project}")
    print(f"passes done: {passes_done}")
    print(f"findings: {total} total")
    print("  by status:    " + ", ".join(f"{k}:{v}" for k, v in sorted(by_status.items())))
    print("  by severity:  " + ", ".join(f"{k}:{v}" for k, v in sorted(by_sev.items())))
    print("  by dimension: " + ", ".join(f"{k}:{v}" for k, v in sorted(by_dim.items())))
    print(f"class findings: {len(cfs)} ({', '.join(cfs) or 'none'}), total members: {cf_members}")
    print("attempts distribution: " + ", ".join(f"{k}x:{v}" for k, v in sorted(attempts_hist.items())))
    # The eight pre-registered metrics, in the order `docs/ouroboros-4-preregistration.md`
    # numbers them, so a reader can hold the document beside this output.
    print(f"1 auditor accuracy (survived validation): {_pct(acc)}  (target >80)")
    print(f"2 fix durability (closed first pass):     {_pct(dur)}  "
          f"(prior {report['fix_durability_baseline_pct']}% — Ouroboros-2)")
    print(f"3 reopen rate (of findings remediated):   {_pct(report['reopen_rate_pct'])}")
    hi = report["human_interventions"]
    print(f"4 human interventions: {hi['total']} ({hi['gates']} gate stops, "
          f"{hi['norm_rulings']} norm rulings, {hi['human_retakes']} re-takes)")
    if report["false_closure_rate_pct"] is None and not report["closures_recorded"]:
        print("5 false-closure rate: n/a (no closure has been recorded in "
              "audit/events.jsonl, so nothing could have been undone)")
    else:
        print(f"5 false-closure rate: {_pct(report['false_closure_rate_pct'])} "
              f"({report['false_closures']} of {report['closures_recorded']} closures undone)")
    cost = report["cost_per_accepted_finding"]
    print(f"6 cost per accepted finding: {cost['seconds_per_accepted_finding']}s over "
          f"{cost['accepted_findings']} findings — PROXY: {cost['proxy']}")
    tok = report["tokens"]
    if not tok["sessions_measured"]:
        print(f"6b tokens: n/a (no finished session reported a figure; "
              f"{tok['sessions_finished']} finished) — NOT zero, unmeasured")
    else:
        # Per CANONICAL TRANSITION, deliberately: a run can make its sessions cheaper
        # while making everything it actually achieved more expensive, and a per-session
        # figure would applaud exactly that.
        # The caption divides by the SAME number the value divided by. It used to say
        # "over {canonical_transitions}" — every transition in the stream — beside a
        # value computed from `canonical_transitions_measured`. On the live stream that
        # printed "1,666,799 tokens over 159 transition(s)" next to 69,450, which is
        # 1,666,799/24: the value was right and the sentence under it was 6.6x wrong.
        # That is the worse half to get wrong, because the sentence is what gets quoted.
        meas, fin = tok["sessions_measured"], tok["sessions_finished"]
        cm, ct = tok["canonical_transitions_measured"], tok["canonical_transitions"]
        if tok["per_canonical_transition"] is None:
            # Measured sessions that moved nothing. Not 0 tokens per transition and not
            # an error: a real cost bought no canonical work, and dividing by zero
            # transitions would print the opposite of what happened.
            print(f"6b tokens per canonical transition: n/a (the {meas} measured "
                  f"session(s) recorded no canonical transition of the {ct} in the "
                  f"stream; {tok['total']:,} tokens bought none of them)")
        else:
            print(f"6b tokens per canonical transition: "
                  f"{tok['per_canonical_transition']:,}")
            print(f"   = {tok['total']:,} tokens / {cm} canonical transition(s), "
                  f"both from the same {meas} measured session(s)")
        print(f"   measured: {meas} of {fin} finished session(s) "
              f"({tok['coverage_pct']:.1f}%), {cm} of {ct} canonical transition(s)")
        if tok["share_that_moved_nothing_pct"] is not None:
            print(f"   of which {tok['on_sessions_that_moved_nothing']:,} "
                  f"({tok['share_that_moved_nothing_pct']:.1f}%) went to sessions that "
                  f"recorded no canonical transition at all")
        if tok["sessions_unmeasured"]:
            print(f"   the other {tok['sessions_unmeasured']} finished session(s) "
                  f"reported no figure — unmeasured, not zero — so every figure above "
                  f"speaks for {tok['coverage_pct']:.1f}% of the run; the remaining "
                  f"{ct - cm} canonical transition(s) were not made by a measured "
                  f"session and are outside the ratio")
    print("7 coverage completeness: "
          + _pct(report["coverage_completeness_pct"], "no pass is queued"))
    print("8 recovery after a killed session: "
          + _pct(report["recovery_after_kill_pct"], "no session was killed")
          + f" ({report['killed_sessions']} killed)")
    if indep["measured"]:
        # A degraded level is NAMED here, beside the number it qualifies. The audit's
        # point 8 is that the fallback is acceptable only while it is displayed as one.
        parts = ", ".join(f"{k}:{v}" + ("  ⚠ degraded" if k in DEGRADED_INDEPENDENCE else "")
                          for k, v in sorted(indep["by_level"].items()))
        print(f"verdict independence ({indep['measured']} verdicts): {parts}")
        print(f"  ceiling: {indep['ceiling']}")
    sess = session_summary(state)
    if sess["total"]:
        print(f"sessions recorded: {sess['total']} — "
              + ", ".join(f"{k}:{v}" for k, v in sorted(sess["by_outcome"].items()))
              + " | providers: "
              + ", ".join(f"{k}:{v}" for k, v in sorted(sess["by_provider"].items())))
    return 0


def dashboard_payloads(project, conf):
    """The two payloads `xcheck dashboard` renders, built by the two commands that
    already own them.

    Not a third reader. `status_payload` and the `metrics --json` body are the
    published contracts; the dashboard takes them as they are printed, so a figure it
    shows and a figure a CI consumer parses cannot be different numbers.
    """
    state, unchecked, checked, kind, detail = state_and_decision(project, conf)
    status = status_payload(project, state, unchecked, checked, kind, detail,
                            Lock(project / "audit", "?").read())
    indep = independence_summary(state)
    report = metrics_report(state, envelope.read_events(project), independence=indep)
    attempts, by_status, by_sev, by_dim = {}, {}, {}, {}
    for r in list(state.findings) + list(state.class_findings):
        by_status[r.status] = by_status.get(r.status, 0) + 1
        by_sev[r.severity] = by_sev.get(r.severity, 0) + 1
        by_dim[r.dimension] = by_dim.get(r.dimension, 0) + 1
        attempts[str(r.attempts)] = attempts.get(str(r.attempts), 0) + 1
    metrics = {
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "project": str(project),
        "passes_done": sum(1 for q in state.queue if q.done),
        "findings": {"total": status["findings"]["total"], "by_status": by_status,
                     "by_severity": by_sev, "by_dimension": by_dim,
                     "attempts": attempts},
        "class_findings": {
            "ids": [c.id for c in state.class_findings],
            "members": len({m for c in state.class_findings for m in c.members})},
        "independence": indep,
        "sessions": session_summary(state),
    }
    metrics.update(report)
    return status, metrics


def cmd_dashboard(project: Path, conf, as_json=False):
    """Render `audit/dashboard.html` from the two published payloads.

    Every number on the page comes from one of them; `xcheck/dashboard.py` performs no
    arithmetic at all, which `tests/test_dashboard.py` asserts over the module's AST.
    The file is self-contained on purpose: a dashboard describing a private audit must
    not reach the network, so it carries no script, no font and no image.
    """
    from xcheck import dashboard as dash
    try:
        status, metrics = dashboard_payloads(project, conf)
    except StateError as e:
        print(f"dashboard unavailable: {e}")
        return 1
    for payload, schema, what in ((status, STATUS_SCHEMA, "status"),
                                  (metrics, METRICS_SCHEMA, "metrics")):
        issues = json_output_issues(payload, schema, what)
        if issues:
            # The dashboard publishes the same figures `--json` publishes. If a payload
            # would not pass its own contract, rendering it as a page would put a
            # number on screen that no consumer is allowed to be given.
            print(f"dashboard: the {what} payload does not match output schema "
                  f"v{OUTPUT_SCHEMA_VERSION} — refusing to render:")
            for i in issues:
                print(f"  - {i}")
            return 1
    if as_json:
        return _emit_json({"output_schema_version": OUTPUT_SCHEMA_VERSION,
                           "status": status, "metrics": metrics},
                          {"output_schema_version": int, "status": dict,
                           "metrics": dict}, "dashboard")
    out = project / "audit" / "dashboard.html"
    out.write_text(dash.render(status, metrics), encoding="utf-8")
    print(f"dashboard: {out}")
    print("  rendered from `status --json` and `metrics --json`; computes nothing of "
          "its own, requests nothing over the network")
    return 0


# The largest `--max-sessions` a real run could reach. Ten thousand sessions at the
# observed ~15 minutes each is over a hundred days of wall clock: far past anything an
# operator means, and still a bound rather than none.
MAX_SESSIONS_CEILING = 10_000


def parse_budget(raw):
    """Parse a `--budget SECONDS` value into a usable wall-clock cap (F-0062).

    `float()` accepts `nan`/`inf` and negative numbers, but the loop's only test
    is `elapsed >= budget` (cmd_loop): `elapsed >= nan` and `elapsed >= inf` are
    never true, so a non-finite budget SILENTLY disables the advertised cap and
    the loop runs bounded only by `--max-sessions`. Reject non-finite and negative
    durations with an addressed error; `0` stays legal (stops the loop at once)."""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise SystemExit(f"--budget must be a number of seconds, got {raw!r}")
    if not math.isfinite(v) or v < 0:
        raise SystemExit(f"--budget must be a finite, non-negative number of seconds, got {raw!r}")
    return v


# F-0090: which command(s) actually CONSUME each flag (N8 grammar in the module
# docstring). `--project` is universal (omitted). The parser used to accept every
# flag regardless of command and the dispatch silently dropped the ones a command
# does not read (`--budget 0 next` parsed but never reached cmd_next); an
# inapplicable flag is now an addressed error, not a silent no-op.
FLAG_COMMANDS = {
    "--dry-run": {"next", "loop", "migrate", "upgrade"},
    "--step": {"loop"},
    "--max-sessions": {"loop"},
    "--budget": {"loop"},
    "--yes": {"next", "loop"},
    "--force": {"unlock"},
    # PHASE 14. Its own flag, not a reuse of `--force`: `unlock --force` breaks a lease
    # that may still be held, and `prune-logs --apply` deletes files. Sharing one word
    # for two different destructive acts is how an operator learns the wrong reflex.
    "--apply": {"prune-logs", "recover"},
    "--migrate": {"prune-logs"},
    "--json": {"status", "metrics", "dashboard"},
    # Every command loads configuration, and a project whose operator profile is
    # elsewhere must be readable as well as runnable — `status` and `lint` on a machine
    # with no exported variable would otherwise be unusable for no security gain.
    "--policy": {"next", "loop", "status", "lint", "metrics", "dashboard", "migrate",
                 "upgrade", "unlock", "cancel", "okf",
                 "evidence-bundle", "recover", "reconcile", "doctor"} | set(WRITE_VERBS),
}


def main(argv):
    args = list(argv[1:])
    if not args or args[0] in ("-h", "--help"):
        # READ from the package, never restated — `tests/test_version_parity.py` exists
        # because five hand-maintained copies of one number had already drifted apart,
        # and a sixth copy in the help text would drift the same way.
        print(f"xcheck {__version__}\n")
        print(__doc__)
        # The verb block is PRINTED from `write.VERBS`, never restated here. A
        # second hand-written list is exactly the drift `tests/test_doc_drift.py`
        # exists to catch, and the fastest way to make that test lie is to give
        # `--help` its own copy of the answer.
        from xcheck.write import help_text
        print(help_text())
        return 0
    if args[0] == "--version":
        # The release gate runs exactly this from inside a clean venv: it is the one
        # question an INSTALLED artifact can be asked without a project on disk.
        if len(args) > 1:
            raise SystemExit(f"--version takes no arguments (got: {' '.join(args[1:])})")
        print(f"xcheck {__version__}")
        return 0

    if args[0] == "selftest":
        # F-0019: reject a trailing tail so a typo/glued token can't ride
        # along and still report PASS with exit 0.
        if len(args) > 1:
            raise SystemExit(f"selftest takes no arguments (got: {' '.join(args[1:])})")
        # Imported HERE, not at module scope: the battery imports every production
        # symbol including this module's own commands (it monkeypatches cmd_next),
        # so `cli -> selftest -> cli` is a real cycle. A deferred import is the
        # honest break — the battery is not part of the CLI's normal surface.
        from xcheck.selftest import selftest
        return selftest()

    project = Path.cwd()
    dry_run = False
    step = False
    assume_yes = False
    max_sessions = None
    budget = None
    force = False
    apply_prune = False
    migrate_logs = False
    as_json = False
    policy_path = None
    cmd = None
    verb_argv = []
    seen_flags = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--project":
            # F-0121: a value-taking option at the END of argv read args[i] past the
            # list and crashed with a raw IndexError traceback. Bound the read and turn
            # a missing value into an addressed CLI error (the N8 grammar: DIR/N/SECONDS).
            i += 1
            if i >= len(args):
                raise SystemExit(f"{a} requires a DIR value (see --help)")
            # Phase 12: `resolve()` is a SYSCALL on an operator-supplied string, and a
            # string is not a path. An embedded NUL raises ValueError and an
            # over-long component raises OSError — both escaped as raw tracebacks
            # until the CLI fuzz went looking. Same shape as F-0121 on the same line:
            # the value was bounded, the value's CONTENT was not.
            try:
                project = Path(args[i]).resolve()
            except (ValueError, OSError) as e:
                raise SystemExit(f"{a} {args[i]!r} is not a usable directory path: {e}")
        elif a == "--dry-run":
            dry_run = True
            seen_flags.append(a)
        elif a == "--step":
            step = True
            seen_flags.append(a)
        elif a == "--yes":
            # 0.9.1 phase 8: the one thing `parallel_confirm_above` cannot get from a
            # run with no terminal. The ceiling REFUSES rather than prompting when
            # stdin is not a tty, and its refusal names this flag — so the flag has to
            # exist, or the refusal is a route to nowhere.
            assume_yes = True
            seen_flags.append(a)
        elif a == "--json":
            as_json = True
            seen_flags.append(a)
        elif a == "--max-sessions":
            i += 1
            if i >= len(args):
                raise SystemExit(f"{a} requires an N value (see --help)")
            # F-0121: a bare int() dumped a raw ValueError on `--max-sessions banana`;
            # convert it to an addressed error naming the option, like every other
            # CLI-value contract (parse_budget, conf_int).
            try:
                max_sessions = int(args[i])
            except ValueError:
                raise SystemExit(f"{a} must be an integer, got {args[i]!r} (see --help)")
            # Phase 12: a NEGATIVE cap was accepted and reported as `stop:
            # max_sessions=-3 reached (cost cap)` with exit 0 — a script passing a
            # computed -3 saw success and no sessions. `--budget` already refuses a
            # negative number of seconds (F-0062); this is the same contract on the
            # neighbouring option. Zero stays legal: "decide but run nothing" is a
            # real thing to ask for.
            if max_sessions < 0:
                raise SystemExit(f"{a} must be zero or more, got {max_sessions} "
                                 f"(0 means decide but run nothing)")
            # 0.9.1: the same disease `parse_budget` already treats, in integers.
            # `--budget inf` is refused because a non-finite cap SILENTLY disables the
            # advertised bound; a Python int has no width, so `--max-sessions
            # 99999999999999999999999999` is that same `inf` and was accepted. The
            # ceiling then bounded nothing, and since `--budget` is optional the loop
            # had no other wall — measured: one such invocation ran real sessions for
            # 15.5 hours before a human noticed. A cap needs a number a run could
            # plausibly reach; anything past that is a typo or a computed overflow,
            # and refusing it costs an operator nothing.
            if max_sessions > MAX_SESSIONS_CEILING:
                raise SystemExit(
                    f"{a} must be at most {MAX_SESSIONS_CEILING}, got {max_sessions}. "
                    f"A cap that large bounds nothing — it is the integer form of "
                    f"`--budget inf`, which this CLI already refuses. Use a real "
                    f"ceiling, or `--budget SECONDS` for a wall-clock one.")
            seen_flags.append("--max-sessions")
        elif a == "--budget":
            i += 1
            if i >= len(args):
                raise SystemExit(f"{a} requires a SECONDS value (see --help)")
            budget = parse_budget(args[i])  # already addressed on a non-number (F-0062)
            seen_flags.append("--budget")
        elif a == "--policy":
            # The operator profile: what runs, and the boundary it runs inside. First
            # in the documented resolution order, ahead of $XCHECK_OPERATOR_PROFILE and
            # $XDG_CONFIG_HOME — an explicit path on the command line is the least
            # ambiguous thing an operator can say.
            i += 1
            if i >= len(args):
                raise SystemExit(f"{a} requires a FILE value (see --help)")
            try:
                policy_path = str(Path(args[i]))
            except (ValueError, OSError) as e:
                raise SystemExit(f"{a} {args[i]!r} is not a usable file path: {e}")
            seen_flags.append(a)
        elif a == "--force":
            force = True
            seen_flags.append(a)
        elif a == "--apply":
            apply_prune = True
            seen_flags.append(a)
        elif a == "--migrate":
            migrate_logs = True
            seen_flags.append(a)
        elif a in ("next", "loop", "status", "unlock", "cancel", "lint", "metrics",
                   "dashboard", "migrate", "upgrade", "prune-logs",
                   "okf", "evidence-bundle", "recover", "reconcile",
                   "doctor") or a in WRITE_VERBS:
            # F-0019: a second command word must not silently overwrite the
            # first — the declared grammar is one command per invocation.
            if cmd is not None:
                raise SystemExit(f"multiple commands: {cmd!r} and {a!r} (only one allowed)")
            cmd = a
        elif cmd in WRITE_VERBS:
            # Everything after a write verb is that verb's own grammar, parsed by
            # `write.parse` against the verb's declaration. The global loop keeps
            # owning `--project` so `--project DIR set-status …` still works.
            verb_argv.append(a)
        else:
            raise SystemExit(f"unknown arg: {a} (see --help)")
        i += 1

    # F-0090: a flag is legal only for the command(s) that consume it (N8). Reject
    # an inapplicable flag with an addressed error instead of silently ignoring it
    # (e.g. `--budget 0 next` parsed but never reached cmd_next; `--force metrics`
    # did nothing). Duplicate flags collapse via the set — order-independent.
    for flag in dict.fromkeys(seen_flags):
        allowed = FLAG_COMMANDS[flag]
        if cmd not in allowed:
            where = f"command {cmd!r}" if cmd else "no command"
            raise SystemExit(
                f"{flag} is not valid for {where} (only: {', '.join(sorted(allowed))})")

    audit = project / "audit"
    # `upgrade` is exempt because it OWNS this diagnosis: a project with no installed
    # copy gets "run install.sh first" from the command that would have done the
    # refreshing, not a generic bootstrap pointer from the argument parser.
    if cmd not in ("unlock", "cancel", "upgrade"):
        # Phase 12: `is_file()` swallows ENOENT/ENOTDIR/ELOOP and nothing else, so a
        # path the filesystem cannot even evaluate (ENAMETOOLONG, EACCES on a parent)
        # came out as a raw OSError. "I cannot look" and "it is not there" are
        # different answers and get different messages — reporting the first as the
        # second would send the reader to `install.sh` for a problem install.sh
        # cannot fix.
        try:
            installed = (audit / "XCHECK.md").is_file()
        except OSError as e:
            raise SystemExit(f"--project {project}: cannot be read: {e}")
        if not installed:
            raise SystemExit(
                f"{audit}/XCHECK.md not found — install xcheck first (bootstrap.md)")
    # F-0009: do NOT persist the default orchestrator.conf here — that write
    # must live inside cmd_next's lock, or a concurrent `next` aborting on a
    # live foreign lock still writes it (a write outside the critical section,
    # violating §4's one-writer serialization). Load for the decision only.
    # An explicit `--policy` path that names nothing is an error, not a fall-through to
    # the next source: an operator who typed a path meant that path, and silently
    # auditing under someone else's policy is the failure mode this flag exists to
    # remove.
    # `--policy` wins the resolution order, and it wins it in ONE place: exported here
    # so every later reader — load_conf, the envelope's digest, a nested call — resolves
    # the same file without a path threaded through six signatures. The child never sees
    # it (`runner.child_environment` drops this name explicitly).
    if policy_path is not None:
        os.environ[policy.OPERATOR_PROFILE_ENV] = str(policy_path)
    if policy_path is not None and not Path(policy_path).is_file():
        raise SystemExit(
            f"--policy {policy_path}: no such file. An operator profile is what says "
            f"which command each role runs and inside what boundary; it is not "
            f"guessed.\n\nStart from this template:\n\n"
            + policy.OPERATOR_PROFILE_TEMPLATE)
    conf = load_conf(audit, profile=policy_path)
    # F-0018: max_sessions is consumed ONLY by `loop`. Resolving it here, before
    # dispatch, made every read-only command (status/lint/metrics) crash with a
    # raw traceback on a malformed orchestrator.conf value. Defer the conf-derived
    # coercion into the loop branch and turn a bad value into an addressed error.

    # PHASE 5: `StateError` is an ADDRESSED refusal, not a crash. Every command
    # below reaches machine state through `load_state`, which raises rather than
    # returning a partial reading; converting that to a SystemExit here gives the
    # message and a non-zero exit at the one place all six commands pass through,
    # so no command can half-run on a document the reader rejected.
    try:
        return _dispatch(cmd, project, conf, dry_run, step, max_sessions, budget, force,
                         verb_argv, as_json, assume_yes, policy_path=policy_path,
                         apply_prune=apply_prune, migrate_logs=migrate_logs)
    except StateError as e:
        raise SystemExit(f"xcheck {cmd}: {e}")
    except ledger.ChainError as e:
        # PHASE 5 (fifth audit): the same treatment as `StateError`, at the same one
        # place all commands pass through. Before the read boundary existed this could
        # not happen — every consumer parsed the stream itself and none of them looked —
        # and the failure it replaces is worse than a traceback: a metric, a budget or a
        # receipt computed over a stream somebody edited, reported as a fact.
        raise SystemExit(
            f"xcheck {cmd}: the event stream is not a record this tool can stand "
            f"behind.\n    {e}\n{ledger.RECOVERABLE[ledger.BROKEN]}\n"
            f"`xcheck recover` reports the stream's state without writing to it.")


def require_policy(policy_path=None, project=None):
    """The operator profile, or an addressed refusal. Called before anything dispatches.

    There is no fallback to defaults, and that is deliberate: CONF_DEFAULTS holds a safe
    value for every policy key except the role commands, so falling back would mean
    running agent sessions under containment nobody chose, on a machine whose operator
    never said this tool may run agents here. "No policy" is not "the default policy".

    `next --dry-run` passes through here too — a dry run exists to tell an operator what
    a real run would do, and reporting a decision the real run would refuse to act on is
    the one answer it must never give.

    The resolved path and its digest are PRINTED. An operator with two profiles (a
    laptop one and a locked-down one) otherwise has no way to see which was in force,
    and the log of the run is where that question gets asked afterwards."""
    resolved = policy.resolve(policy_path)
    if resolved is None:
        raise SystemExit(policy.no_profile_refusal(policy_path))
    # PHASE 1: the location gate again, here, in front of the dispatch verbs. `load_conf`
    # already refuses a profile inside the subject and runs earlier, so this is the second
    # of two gates on the same rule — kept because this is the function whose whole job is
    # "may this run start", and a reader of THIS function should not have to know that
    # something upstream checked. `--policy` is not an override for location: an operator
    # who types the path is still handing the subject its own policy.
    if project is not None:
        try:
            policy.refuse_profile_inside_subject(resolved, project)
        except policy.PolicyError as e:
            raise SystemExit(str(e)) from None
    print(f"policy: {resolved}  sha256:{policy.profile_digest(resolved)[:16]}")
    return resolved


def _dispatch(cmd, project, conf, dry_run, step, max_sessions, budget, force,
              verb_argv=(), as_json=False, assume_yes=False, policy_path=None,
              apply_prune=False, migrate_logs=False):
    if cmd in WRITE_VERBS:
        from xcheck.ledger import LedgerError
        from xcheck.write import run
        try:
            return run(project, cmd, list(verb_argv))
        except LedgerError as e:
            # PHASE 7 (fourth audit). The refusal an operator reads, not a traceback.
            # It is raised BEFORE the state write, so the message's claim that canonical
            # state was not changed is a property of where it is raised.
            raise SystemExit(str(e)) from None
    if cmd == "status":
        return cmd_status(project, conf, as_json) or 0
    elif cmd == "next":
        require_policy(policy_path, project)
        # F-0108 reopen: cmd_next converts a session error (spawn failure or a
        # non-zero child exit) into the string "stop-error" and prints the addressed
        # halt, but the CLI boundary discarded that return and fell through to
        # `return 0` — so a caller/CI saw success despite the halt. Propagate it to a
        # NON-ZERO exit so the README §8 session-error contract reaches the exit code,
        # not only stdout. Legitimate gates (triage/done/norm-ratification/coverage/…)
        # are normal stops and keep exit 0.
        kind = cmd_next(project, conf, dry_run, assume_yes)
        if kind == "stop-error":
            return 1
    elif cmd == "loop":
        require_policy(policy_path, project)
        if max_sessions is None:
            # F-0113: validated at the load_conf boundary — this read returns an
            # already-validated int (a bad value became an addressed error on access,
            # not a raw traceback). status/lint/metrics never reach this branch, so a
            # malformed max_sessions never crashes a read-only command (F-0018).
            max_sessions = int(conf["max_sessions"])
        # F-0122: `loop` is the primary automated route; a session error inside it must
        # reach the shell/CI exit code, not look like success. cmd_loop returns the
        # terminal kind — propagate "stop-error" to a NON-ZERO exit like the `next`
        # branch (README §8 session-error contract). Every other stop is a normal exit 0.
        kind = cmd_loop(project, conf, dry_run, step, max_sessions, budget,
                        assume_yes=assume_yes)
        if kind == "stop-error":
            return 1
    elif cmd == "unlock":
        cmd_unlock(project, force, conf)
    elif cmd == "cancel":
        return cmd_cancel(project)
    elif cmd == "lint":
        return cmd_lint(project)
    elif cmd == "prune-logs":
        return cmd_prune_logs(project, conf, apply=apply_prune,
                              migrate=migrate_logs)
    elif cmd == "reconcile":
        return cmd_reconcile(project, conf)
    elif cmd == "doctor":
        return cmd_doctor(project, conf, policy_path)
    elif cmd == "recover":
        return cmd_recover(project, conf, apply=apply_prune)
    elif cmd == "okf":
        return cmd_okf(project, conf)
    elif cmd == "evidence-bundle":
        return cmd_evidence_bundle(project, conf, policy_path)
    elif cmd == "metrics":
        return cmd_metrics(project, as_json)
    elif cmd == "dashboard":
        return cmd_dashboard(project, conf, as_json)
    elif cmd == "migrate":
        from xcheck.migrate import migrate
        return migrate(project, dry_run)
    elif cmd == "upgrade":
        from xcheck.upgrade import upgrade
        return upgrade(project, dry_run)
    else:
        raise SystemExit("no command (next|loop|status|unlock|cancel|lint|metrics|"
                         "dashboard|migrate|upgrade|prune-logs|okf|evidence-bundle|"
                         "reconcile|recover|doctor|render-views|selftest)")
    return 0


def main_argv():
    """Console-script entry point (`pyproject.toml` `[project.scripts]`).

    `main` takes argv explicitly — that is what lets the tests drive it without
    touching `sys.argv` — so the installed script needs this one-line adapter rather
    than a second copy of the grammar. `bin/xcheck` keeps calling `main(sys.argv)`
    directly and is unaffected: installation ADDS an entry point, it never replaces
    the path invocation the six skills and every README instruction use."""
    import sys
    return main(sys.argv)
