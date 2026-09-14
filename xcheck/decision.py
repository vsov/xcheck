"""The lifecycle decision: given reconciled state, what does the cycle owe next.

`DECIDE_PRIORITY` is the one physical list that orders the branches; `_decide_core`
is generated documentation over it, so the priority cannot drift from the code.
`decide` layers the §4 rule 9 construal gate on top as a filter over a dispatch.
"""


import os
from pathlib import Path

from xcheck.util import (
    ENVELOPE_ADMITTER_RE, NEEDS_HUMAN_NEXT_RE, NORM_RULING_TOKEN_RE, PLANNER_CHARTER,
    TELEMETRY_SOURCES, TERMINAL, _broken_superseded, conf_flag, conf_number,
    construal_key
)
from xcheck.state import load_state, next_owner
# Imported at module level, not inside the gate: `budget` imports `decision`
# only INSIDE its own functions (for `metrics_report`), so this direction of the
# edge is safe and the flag name is readable where the gate reads it.
from xcheck import budget
from xcheck.views import refuse_on_drift

# ---------------------------------------------------------------- decision


# F-0077 (human ruling #4): decide()'s branch priority is ONE physical list.
# Six earlier rounds kept the order as prose in the docstring AND as a separate
# `_priority` literal in selftest, then added a check that the two matched — and
# the check itself kept turning out narrower than the claim (it compared only
# lengths, or never read the docstring at all), drifting a seventh time. Ending
# it: the order lives here once. The docstring's priority line is GENERATED from
# this literal (see the assignment just below decide()), and the selftest derives
# both its order check and the set of expected adjacent-pair cases from the same
# list. There is no second copy to drift against — a divergence is now impossible
# to write, not merely caught. Highest first; each entry is one branch of decide().
DECIDE_PRIORITY = [
    "needs-human", "refusal", "disputed", "construal", "verify", "reopened", "resume",
    "accepted-cf", "accepted-f", "triage-cap", "audit", "triage", "plan-completeness",
    "inconsistent", "deferred-debt", "done",
]


def decide(records, unchecked_passes, batch_size=8, triage_cap=0,
           plan_complete=True, refusals=None, construal=None):
    """Return (kind, detail) — `_decide_core`'s lifecycle decision, with the §4 rule 9
    CONSTRUAL gate applied to a dispatch.

    The gate is a FILTER on a `run-*` outcome, not a lifecycle branch: what the cycle owes
    is unchanged by it, only whether the charter may be handed to an agent yet. So the core
    decides first, `role_and_charter` names the charter that is about to be dispatched, and
    `construal` — a callable `(role, charter) -> (kind, detail) | None` — gets to replace
    the outcome with `run-construal` or `stop-construal`. Its position in DECIDE_PRIORITY
    (below the human gates, above every dispatch branch) is exactly that: it never preempts
    a halt the human already owns, and it always preempts handing work to an agent.

    `construal=None` (the default, and what a gate-off caller passes) makes this the
    identity, so the shipped default behaviour is `_decide_core`'s, byte for byte."""
    kind, detail = _decide_core(records, unchecked_passes, batch_size,
                                triage_cap, plan_complete, refusals)
    if construal is None or not kind.startswith("run-"):
        return kind, detail
    role, charter = role_and_charter(kind, detail)
    verdict = construal(role, charter)
    return verdict if verdict else (kind, detail)


def _decide_core(records, unchecked_passes, batch_size=8, triage_cap=0, plan_complete=True, refusals=None):
    """Return (kind, detail). kind: stop-* | run-* | done.

    Priority, highest first — each branch below is one step. The order is ONE
    physical list, DECIDE_PRIORITY (module level); this line is generated from it
    after the function is defined, and the selftest derives both its order check
    and the expected adjacent-pair cases from the same literal (F-0077 human
    ruling #4). There is no second copy to keep in sync, so nothing can drift —
    changing the priority line means changing DECIDE_PRIORITY, which the selftest
    then measures against decide()'s actual branch behaviour:

        {PRIORITY}

    Closing started work precedes opening new work; triage blocks only when
    nothing else is runnable, so pass batches can accumulate before a triage
    sitting (the pilot's working rhythm) — unless triage_cap bounds the pile.

    `plan_complete` (F-0077): whether AUDIT.md is a COMPLETE plan per the §3
    Planner stop condition (norm-backed dimension + unit map + non-empty queue),
    not merely present. It is load-bearing at exactly TWO points — and this is
    the whole of its reach, NOT before triage (F-0077 human ruling #3: the code
    is right, the earlier docstring was too wide):
      1. it gates the `audit` branch — an incomplete plan routes to the Planner
         instead of auditing against a missing unit map; and
      2. it gates the `done` fall-through — an existing AUDIT.md with an
         empty/unparsed queue (a Planner interrupted before writing it) must not
         report 'done' on an unplanned project (F-0077 human ruling #2).
    Findings already in `reported` came from completed passes, so they are
    triaged even while the plan is partial: plan-completeness sits AFTER the
    `reported`->triage branch, not before it. Defaults True so callers that
    already guarantee a complete plan (and the decision selftest) are
    unaffected; production passes audit_plan_complete().

    `refusals` ({finding id: reason code}, §5) carries the LIVE typed refusals —
    findings whose assigned role accepted the charter and recorded that it cannot
    execute it. Re-dispatching the same charter over unchanged state would replay
    the refusal forever (the F-0118 shape: a role that halts without writing moves
    no counter), so a live refusal halts the cycle for the human exactly as the §8
    norm-ratification block does. It sits BELOW needs-human — a reopen-limit breach
    is the older, harder gate — and ABOVE every dispatch branch, because every one
    of them would hand the refused work straight back. Defaults to None (no
    refusals), so a caller that does not track them gets byte-identical behaviour."""
    for r in records:
        # CF-0001 (I-5): the `⚠ needs-human` flag is legal ONLY on a finding that
        # actually reached the reopen limit — §63: "The Verifier appends ⚠
        # needs-human … when reopen_limit consecutive reopenings are reached", whose
        # status is therefore `reopened`. The old loose substring membership test on
        # the next column honoured the flag on ANY status, so the poison flag on a `closed` (or a
        # fresh `reported`) row halted the WHOLE cycle. Bind the gate to the
        # canonical flag form on a `reopened` row; anywhere else it is a corrupt
        # control field caught by lint, not a stop signal.
        if r.status == "reopened" and NEEDS_HUMAN_NEXT_RE.match((next_owner(r) or "").strip()):
            return "stop-needs-human", r.id

    # §5 typed refusal: the assigned role accepted this charter and recorded that it
    # cannot execute it. Every dispatch branch below would hand the same charter back
    # unchanged, so the cycle stops here for the human, carrying the reason code.
    for r in records:
        reason = (refusals or {}).get(r.id)
        if reason:
            return "stop-refusal", (r.id, reason)

    # F-0143: a `disputed` finding is an EXCEPTIONAL human halt (§3; N2 README §8) —
    # the loop "never silently works around a gate that wants a human". The prior
    # order ran this branch DEAD LAST, after verify/remediation/audit/triage/plan-
    # completeness, so the orchestrator kept launching writing-sessions (and could
    # mutate the material) while a dispute awaited the human's resolution. It sits
    # here with the other human halts, above every dispatch branch, so any runnable
    # work yields to the pending dispute.
    disputed = [r.id for r in records if r.status == "disputed"]
    if disputed:
        return "stop-disputed", disputed

    fixed = [r.id for r in records if r.status == "fixed"]
    if fixed:
        return "run-verifier", fixed

    reopened = [r.id for r in records if r.status == "reopened"]
    # F-0051: "close started work before opening new work" (README §7,
    # launchers/README). RESUME state — a finding an interrupted session left
    # 'validated' or 'planned' (legal durable states owned by the Remediator,
    # §5, NOT ledger corruption; F-0001) — must be picked up BEFORE a newly
    # accepted CF opens a global change. The old order lumped resume state and
    # freshly-accepted F-findings into one `remediable` list checked AFTER
    # accepted_cf, so a new CF preempted stable in-flight remediation. Split them
    # and order: reopened > resume(validated/planned) > accepted CF > accepted F.
    resume = [r.id for r in records if r.status in ("validated", "planned")]
    accepted_cf = [r.id for r in records if r.status == "accepted" and r.id.startswith("CF-")]
    accepted_f = [r.id for r in records if r.status == "accepted" and r.id.startswith("F-")]
    if reopened:
        # F-0003: the remediation_batch_size cap (§10) bounds EVERY Remediator
        # batch, not only the accepted collection — reopened work is a
        # Remediator session too and must respect the same session boundary.
        return "run-remediator", reopened[:batch_size]
    if resume:
        return "run-remediator", resume[:batch_size]
    if accepted_cf:
        return "run-remediator", [accepted_cf[0]]
    if accepted_f:
        return "run-remediator", accepted_f[:batch_size]

    reported = [r.id for r in records if r.status == "reported"]
    # triage_cap (0 = unbounded): when the reported pile reaches the cap,
    # stop for a triage sitting BEFORE opening more audit passes, so the
    # human triages in digestible batches instead of one huge sitting.
    if triage_cap and len(reported) >= triage_cap:
        return "stop-triage", reported

    if unchecked_passes and plan_complete:
        # F-0077: only audit against a COMPLETE plan. An AUDIT.md that exists but is
        # not fully planned (missing dimensions, unit map, or a norm-backed dimension,
        # §3 Planner stop condition) does NOT audit here — the incomplete-plan case
        # falls through to the `reported`->triage branch and then to the
        # plan-completeness guard below, which sends the Planner back to finish it.
        return "run-auditor", unchecked_passes[0]

    # F-0142: `reported` findings came from COMPLETED passes, so they are triaged
    # even while the plan is partial — DECIDE_PRIORITY puts `triage` ABOVE
    # `plan-completeness`, and this function's `plan_complete` contract says
    # plan-completeness sits AFTER the `reported`->triage branch. The prior code
    # nested the incomplete-plan redirect INSIDE the `unchecked_passes` branch, so a
    # queued pass under a partial plan pre-empted the mandatory human Triage gate
    # with a Planner writing-session — the exact order its own priority forbids.
    if reported:
        return "stop-triage", reported

    # F-0077 (human ruling #2, 2026-07-26): plan completeness gates the STATE, not
    # just the unchecked-passes branch. An under-planned audit — no dimensions, no
    # unit map, or an empty queue (a Planner interrupted before writing it) — must
    # route back to the Planner instead of falling through to 'done' on an
    # unplanned project.
    #
    # The old companion parameter `audit_md_exists` is GONE (phase 5). It existed
    # because AUDIT.md could be absent while the ledger was not, so "planned at
    # all" and "planned completely" were two separate questions. With one state
    # document there is no such split: no state file at all is `load_state`'s
    # addressed refusal long before this function runs, and a state file always
    # carries a queue and catalogs — empty ones simply make the plan incomplete.
    # The contradictory exists=False/plan_complete=True input the old selftest
    # pinned is not merely unchecked now, it is unrepresentable.
    if not plan_complete:
        return "run-planner", None

    deferred = [r.id for r in records if r.status == "deferred"]
    non_terminal = [r.id for r in records if r.status not in TERMINAL and r.status not in ("deferred", "disputed")]
    if non_terminal:
        return "stop-inconsistent", non_terminal
    if deferred:
        return "done-deferred-debt", deferred
    return "done", None


# F-0077 (human ruling #4): render the docstring's priority line FROM the one
# literal. Prose and data are now the same object — the line cannot say an order
# DECIDE_PRIORITY does not hold. Guarded because `python -OO` strips docstrings.
if _decide_core.__doc__:
    _decide_core.__doc__ = _decide_core.__doc__.replace("{PRIORITY}", " > ".join(DECIDE_PRIORITY))


def role_and_charter(kind, detail):
    if kind == "run-construal":
        # §4 rule 9: the SAME role, a different charter — write your construal and stop.
        # `detail` is (role, original charter, key, path) as the gate resolved it.
        role, orig, key, path = detail
        return role, (
            f"Before any effect on the material, write your OPERATIONAL CONSTRUAL of this "
            f"charter to `{path}` (copy audit/templates/construal.md), then STOP without "
            f"touching anything else. Set `key: {key}`, `role: {role}`, `status: proposed`, "
            f"`session: <your XCHECK_SESSION_ID>`, and leave `admitted-by` null — you must "
            f"NOT admit your own construal (§4 rule 9). Fill all five sections. "
            # F-0145: the record is the construal OF THE CHARTER, and the admitted record
            # is what a LATER session works under (§4 rule 9) — a session with a different
            # identity than the one writing now. Without this sentence the five sections
            # truthfully described the writing session ("stop without touching anything",
            # "verification is out of scope", a fixed-by of a session that never fixes),
            # and the gate then admitted a record that forbids the chartered work itself.
            f"All five sections construe the CHARTERED work — what the session that "
            f"executes this charter will do, assume, and stop on — not this writing "
            f"session: your own two acts are writing this record and stopping, and your "
            f"session identity will not be the one performing the chartered work. "
            # The charter is DELIMITED and copied verbatim, because the record's `key` is
            # derived from it and is now checked against it (`construal_issues`). Ouroboros-3
            # round 1: an undelimited trailing charter swallowed the session-hygiene text and
            # the operator note, the agent folded the result into a YAML block scalar, and the
            # record ended up claiming a charter that produces a DIFFERENT key. Copy exactly
            # what is between the markers, on ONE line, and nothing else.
            f"Set `charter:` to EXACTLY the text between the markers below — one line, "
            f"verbatim, no block scalar, nothing from outside the markers (the record's key "
            f"is derived from it and is checked against it):\n"
            f"<<<CHARTER>>>{orig or ''}<<<END CHARTER>>>")
    if kind == "run-planner":
        return "Planner", PLANNER_CHARTER
    if kind == "run-auditor":
        return "Auditor", detail
    if kind == "run-remediator":
        ids = detail
        if len(ids) == 1:
            return "Remediator", f"finding {ids[0]}"
        # F-0144: `A..B` is a claim of CONTIGUITY, but the batch is arbitrary — closed
        # findings drop out of the middle after a Verifier round, and a range token then
        # names ids the charter does not contain (terminal ones, §5 immutable). The range
        # form survives ONLY for a set that is actually an unbroken same-prefix run; any
        # gapped set is enumerated with no range token, so no id outside the set is
        # derivable from the charter text (§4 rule 1: an exact scope).
        prefix_num = [(i.rsplit("-", 1)[0], int(i.rsplit("-", 1)[1])) for i in ids]
        contiguous = all(p2 == p1 and n2 == n1 + 1
                         for (p1, n1), (p2, n2) in zip(prefix_num, prefix_num[1:]))
        if contiguous:
            return "Remediator", f"findings {ids[0]}..{ids[-1]} ({len(ids)} items: {', '.join(ids)})"
        return "Remediator", f"findings ({len(ids)} items: {', '.join(ids)})"
    if kind == "run-verifier":
        ids = detail
        # F-0145 (operator charter reissue, §4 rule 9 "fix the CHARTER rather than the
        # construal"): the old text `every finding in status fixed (...)` named a SET and
        # never named the ACT, so a construal written under the write-and-stop gate prompt
        # could truthfully record "verification is out of scope" and then be admitted as the
        # authority FOR verification. The charter now names the act it authorizes.
        return "Verifier", f"verify the fixes and give a binding verdict on every finding in status fixed ({', '.join(ids)})"
    raise ValueError(kind)


def verifier_independence(fixed_ids, by_id, verifier_id):
    """F-0096: fail-closed enforcement of the §3 hard rule — 'the Verifier is
    never the agent or session that produced the fix it verifies.'

    Routing to the Verifier keyed on `status == fixed` alone proves only the
    lifecycle stage, not that the next session is independent; the prompt merely
    ASSERTED 'You did not write these fixes.' Enforce it on the machine-readable
    `fixed_by` provenance the Remediator records (its XCHECK_SESSION_ID / lock
    nonce). Given the ids routed to the Verifier and `verifier_id` (the identity
    of the session about to verify — the orchestrator's own XCHECK_SESSION_ID, or
    None when unset), return (ok, reason, offender):

      - `provenance`: a fixed finding with no `fixed_by` — an unrecorded producer
        cannot be proven independent, so verification is refused, not asserted.
      - `same`: a `fixed_by` equal to `verifier_id` — the producing session is
        verifying its own fix, exactly what §3 forbids.
      - otherwise (True, None, None).

    The old `malformed` verdict is gone, and its absence is the phase-5 point: a
    `fixed_by` that is not 16 lowercase hex cannot reach this function at all,
    because the schema refuses the document that carries it. What was a gate is
    now a property of any state that loads.

    Defaults keep the check honest without over-claiming: the orchestrator spawns
    each role in a FRESH subprocess with a fresh XCHECK_SESSION_ID (run_session),
    so an orchestrated Verifier is structurally a different session; this gate
    additionally blocks the state routing-by-status cannot see — an unrecorded
    producer, and a config that resumes the fixing session as the verifier."""
    for fid in fixed_ids:
        producer = (getattr(by_id.get(fid), "fixed_by", None) or "").strip()
        if not producer:
            return False, "provenance", fid
        if verifier_id is not None and producer == verifier_id:
            return False, "same", fid
    return True, None, None


# ---------------------------------------------------------------- commands


def norm_ruling_pending(record):
    """A CF blocked at the §8 norm-ratification gate is PENDING until the norm
    owner records the winning side in the `norm_ruling` field (XCHECK.md §8 rule
    8, human ruling F-0042 option a). This is a fail-closed MACHINE gate, not the
    `## Norm ruling` prose: it lifts ONLY when `norm_ruling` holds a recognized
    norm-id token (e.g. `N1` or `N1-over-N4`). An absent field, `pending`, an
    empty value, or any unrecognized token keeps the gate closed — a conflict
    description without an explicit decision does not unblock. Returns True while
    still a human gate."""
    val = (getattr(record, "norm_ruling", None) or "").strip()
    if not val or val.lower() == "pending":
        return True
    return not NORM_RULING_TOKEN_RE.match(val)


def construal_gate_resolver(state):
    """The §4 rule 9 gate as a callable `(role, charter) -> (kind, detail) | None`, for
    `decide` to apply to a dispatch. `None` means "nothing to stop for — dispatch".

    Three outcomes, in order:

      - no construal record for this charter's key   -> `run-construal` (write it, stop)
      - a construal that is not `admitted`           -> `stop-construal` (human admits)
      - an admitted construal                        -> None (dispatch the real charter)

    Admission is ALWAYS a separate act by a party that is not the producer (§4 rule 9): the
    tool never admits a construal on the producer's behalf. The `construal_envelope`
    pre-authorization that once short-circuited the middle case was removed (F-0138,
    Ouroboros-3) — three attempts to make an in-tree pre-authorization independent each left a
    self-authorization surface, so a `proposed` construal now always waits for a human.

    The old "the record is invalid, treat it as no admission" branch is gone. It
    was fail-closed handling for a record the tool itself called corrupt; a
    construal that reaches this function came out of `load_state`, so it has a
    16-hex key that MATCHES `construal_key(role, charter)`, a canonical status, a
    session id, and an admitter that is not its own producer — all of it enforced
    at the one boundary. There is no corrupt-record case left to be careful about."""
    by_key = {c.key: c for c in state.construals}

    def resolve(role, charter):
        key = construal_key(role, charter or "")
        rel = f"audit/construals/{key}.md"
        rec = by_key.get(key)
        if rec is None:
            return "run-construal", (role, charter, key, rel)
        if rec.status == "admitted":
            # `envelope:<name>` is a HISTORICAL admitter form only (F-0138): the schema
            # keeps it readable on records admitted while the removed `construal_envelope`
            # mechanism was live, but it is NEVER a live admission and authorizes no dispatch.
            # Only a real non-producing party — a session id or a human — admits for dispatch,
            # so a producer that self-forges `admitted_by: envelope:<name>` on its own construal
            # (a schema-VALID record) still cannot get itself dispatched. This is the mint-/
            # dispatch-time refusal half of the split Human ruling #2a demands (§4 rule 9).
            admitter = (rec.admitted_by or "").strip()
            if ENVELOPE_ADMITTER_RE.match(admitter):
                return "stop-construal", (role, charter, rel,
                    f"its admitter `{admitter}` names the removed `construal_envelope`"
                    f" pre-authorization — a historical record form, never a live admission;"
                    f" a human (or another non-producing session) must admit it (§4 rule 9, F-0138)")
            return None
        if rec.status == "proposed":
            return "stop-construal", (role, charter, rel, "it is `proposed` and a human has not admitted it")
        return "stop-construal", (role, charter, rel, f"its status is `{rec.status}`, not `admitted`")

    return resolve


def plan_complete(state):
    """§3 Planner stop condition, read off the one state document.

    A plan is COMPLETE when it has a norms catalog, at least one dimension, a unit
    map, and a non-empty pass queue. "Norm-backed" needs no check here: the schema
    refuses a document whose dimension cites a norm the catalog does not carry, so
    every dimension in a loaded State is norm-backed by construction (F-0157).
    Likewise every queue entry carries a charter and a stop condition, and its
    dimension and units resolve — the old `queue_charter_issues` funnel, which had
    to decide whether a Markdown line was a real entry, has no analogue: an array
    element either is an entry or is not present (F-0156)."""
    cat = state.catalogs
    return bool(cat and cat.norms and cat.dimensions and cat.units and state.queue)


def state_and_decision(project: Path, conf):
    """Read the ONE canonical state document, then decide. Returns
    `(state, unchecked, checked, kind, detail)`.

    Every consumer of machine state enters here or through `load_state` directly,
    and `load_state` raises rather than returning a partial reading. That is what
    makes F-0147 — "full validation lives only in lint, and consumers walk past
    it" — structurally impossible rather than merely fixed: there is no second
    path to walk past it on.

    Four gates that used to live in this function are gone, each because the state
    it guarded against cannot be written down any more:

      - `ledger_integrity_stop` (F-0004/F-0097/F-0098/F-0103): an unparsable row,
        one id on two rows, a ledger row with no finding file. The schema refuses
        duplicate ids and every record carries its own body_path.
      - `stop-triage-unsynced` (F-0100/F-0131): a ledger decision never applied to
        the finding file. There is no ledger and no file to disagree — one record
        holds the status.
      - `stop-coverage-missing` (F-0011/F-0154/F-0155): a checked pass with no
        report. `done` without a coverage record is not a rejected document, it is
        an unrepresentable one.
      - the `fixed_by` malformed branch (F-0096): see `verifier_independence`."""
    audit = project / "audit"
    # PHASE 6: the generated views are checked against the document the moment it
    # is read. A hand-edited LEDGER.md is not a second opinion to reconcile — it is
    # a person who believed they were writing authority, and the only honest answer
    # is to stop and let them decide which of the two is true.
    state = refuse_on_drift(load_state(audit), audit)
    records = list(state.findings) + list(state.class_findings)
    by_id = {r.id: r for r in records}
    unchecked = [q.id for q in state.queue if not q.done]
    checked = [q.id for q in state.queue if q.done]

    # F-0005: enforce the reopen limit from the record's own attempts even when the
    # `next` override is not set. A finding currently 'reopened' has been reopened
    # attempts+1 times; at reopen_limit it needs a human.
    reopen_limit = int(state.limits.get("reopen_limit", 2))
    for r in records:
        if r.status == "reopened" and not NEEDS_HUMAN_NEXT_RE.match((next_owner(r) or "").strip()):
            if r.attempts + 1 >= reopen_limit:
                return state, unchecked, checked, "stop-needs-human", r.id
    # F-0042: a CF halted at the §8 norm-ratification gate carries
    # `blocked: norm-ratification` and stays `planned`. It is a human gate, like
    # needs-human: executing a class fix before the norm owner rules is exactly
    # what §8 forbids (a wrong-direction class fix multiplies across the corpus).
    for r in records:
        if r.blocked == "norm-ratification" and norm_ruling_pending(r):
            return state, unchecked, checked, "stop-norm-ratification", r.id
    # F-0003: the state document's remediation_batch_size (§10 override) wins over
    # the conf default when present. F-0113 reopen: when the override IS present the
    # conf `batch_size` key is UNUSED — resolve it only in the else-branch, so a typo
    # in an override-shadowed key does not crash a read-only command.
    if "remediation_batch_size" in state.limits:
        batch = int(state.limits["remediation_batch_size"])
    else:
        batch = int(conf.get("batch_size", "8"))
    # §5 typed refusal: a role ACCEPTED the charter and recorded that it cannot execute
    # it. The reason is durable state on the record (like `blocked`), not a status, so
    # the work is still owed — which is the point: the charter stays in force. A refusal
    # counts as LIVE only while the finding is non-terminal; on a closed/rejected/
    # withdrawn record it is history, not a standing obstacle.
    live_refusals = {r.id: r.refusal.strip() for r in records
                     if r.status not in TERMINAL and (r.refusal or "").strip()}
    # The embargo: a charter whose identical previous attempts moved nothing is taken
    # OUT of the queue before the decision is made, not answered "no" afterwards.
    # `_decide_core` returns `unchecked_passes[0]` unconditionally, so a veto that only
    # refuses would freeze every other pass behind one bad charter — which is what P-05
    # did the expensive way, 54 times, while the rest of the queue waited.
    held = {}
    # Read through the SAME boundary as `construal_gate` two lines down, not with a
    # hand-rolled `conf.get(...) == "on"`. That boundary owns the default (CONF_DEFAULTS),
    # works on a `Conf` and a plain dict alike, and REFUSES an unrecognised value with an
    # addressed error instead of reading it as off — F-0113's ruling, and the reason a
    # typo'd `embargo=yes` must not silently disarm a gate the operator believes is armed.
    if conf_flag(conf, "embargo") and unchecked:
        from xcheck.envelope import read_events
        from xcheck.runner import orchestrator_source_digest
        from xcheck.policy import profile_digest, resolve as resolve_policy
        _profile = resolve_policy()
        takeable, held = embargo_report(
            read_events(project), unchecked, orchestrator_source_digest(),
            after=conf_number(conf, "embargo_after"), state=state,
            policy_digest=profile_digest(_profile) if _profile else None)
    else:
        takeable = list(unchecked)
    kind, detail = decide(
        # PHASE 8: `int(conf.get("triage_batch_cap", "0"))` hardcoded a SECOND default
        # here. Harmless while CONF_DEFAULTS also said 0; the moment the shipped default
        # became finite it meant a plain dict (a test, a caller) silently got the old
        # unbounded behaviour while orchestrator.conf said otherwise. `conf_number` reads
        # the ONE default, on a Conf and a plain dict alike (F-0113).
        records, takeable, batch, conf_number(conf, "triage_batch_cap"),
        plan_complete=plan_complete(state),
        refusals=live_refusals,
        # §4 rule 9 / §10 `construal_gate`: None when the flag is off, so decide() is the
        # identity on the core decision and default behaviour is unchanged.
        construal=(construal_gate_resolver(state) if conf_flag(conf, "construal_gate") else None),
    )
    # BACKPRESSURE (audit P0): an OPEN critical finding stops NEW auditing.
    #
    # Read after `decide()` and not with the embargo above it, on purpose. The gate must
    # see WHICH work was chosen: it refuses `run-auditor` — the branch that manufactures
    # more debt — and lets triage, remediation and verification through untouched,
    # because those are the only ways the critical stops being open. A gate placed
    # before the decision could only refuse everything, which would make the critical
    # permanently unfixable and the queue permanently stuck.
    #
    # "Open" is non-terminal, not merely `reported`: a critical the human has ACCEPTED
    # is a hole that is known and still there, and the audit's complaint was that new
    # passes kept running on top of it.
    if kind == "run-auditor" and not conf_flag(conf, "audit_over_critical"):
        worst = next((r for r in records
                      if r.severity == "critical" and r.status not in TERMINAL), None)
        if worst is not None:
            return state, unchecked, checked, "stop-critical", (worst.id, worst.status)
    # F-0096: enforce Verifier ≠ fixer BEFORE dispatch (§3 hard rule).
    if kind == "run-verifier":
        verifier_id = os.environ.get("XCHECK_SESSION_ID")
        ok, reason, offender = verifier_independence(detail, by_id, verifier_id)
        if not ok:
            return state, unchecked, checked, "stop-verifier-independence", (reason, offender)
    # BUDGETS (phase 15, third audit). Placed HERE, after the critical and independence
    # gates and before anything is dispatched, for two reasons that are not the same
    # reason. First, a budget is economics and a critical finding is safety: a run that
    # is both over budget and sitting on an open critical should be told about the
    # critical. Second, this must see WHICH work was chosen — like the critical gate, it
    # refuses only a `run-*` dispatch and lets triage and human gates through, because
    # accepting or rejecting findings is how a marginal-value stall actually clears, and
    # a gate that refused those too would be a deadlock wearing a budget's name.
    #
    # Evaluated on every decision rather than once per run: a ceiling that is only a
    # precondition gets walked through exactly once, by the session already in flight
    # when it was checked.
    if kind.startswith("run-") and conf_flag(conf, budget.FLAG):
        from xcheck.envelope import read_events
        _role, _charter = role_and_charter(kind, detail)
        fired = budget.gates(state, conf, read_events(project), charter=_charter,
                             cmd=conf.get(f"{(_role or '').lower()}_cmd"))
        # PHASE 12: `no-verdict` is a THIRD outcome and is not a stop. Below the telemetry
        # coverage floor the statistical gates cannot honestly extrapolate, so they say so
        # and the dispatch proceeds — refusing to judge is not the same as judging clear,
        # and it is emphatically not grounds to block work on a machine whose only fault
        # is that nobody configured a telemetry adapter. `xcheck status` prints the notice
        # so the absence of a verdict is visible rather than inferred from silence.
        if fired is not None and fired[0] != budget.NO_VERDICT:
            return state, unchecked, checked, fired[0], fired[1]
    # A held pass is still OWED. Every other branch of the decision stays exactly as it
    # was — a Verifier, a Remediator or a human gate outranks this and should run — but
    # `done` over an embargoed queue would certify a plan that has unfinished passes,
    # which is the one way this mechanism could hide the thing it exists to surface.
    if held and kind.startswith("done"):
        return state, unchecked, checked, "stop-embargo", held
    if kind.startswith("done"):
        # F-0050: a 'done' verdict means every record is flat-TERMINAL — but a
        # superseded-by-class record is terminal ONLY with a CLOSED CF that
        # bidirectionally lists it (README §3, bootstrap §3). An orphan member is not
        # really terminal; certifying completion over it hides lost or unfinished
        # global remediation. Fail closed before any 'done' (incl. deferred-debt).
        orphan = _broken_superseded(records)
        if orphan:
            return state, unchecked, checked, "stop-broken-class", orphan
    return state, unchecked, checked, kind, detail


# ---------------------------------------------------------------- metrics

# The eight metrics Ouroboros-4 is pre-registered on, plus the P2 breakdowns, computed
# in ONE place from the two durable sources: the validated `State` and the append-only
# event stream. `docs/ouroboros-4-preregistration.md` states each formula in prose and
# `tests/test_metrics.py` states each expected value by hand; this is the only code
# that computes them, so the run cannot be scored by a second implementation.
#
# Which source answers which question is not arbitrary. `state.json` says what is TRUE
# NOW; `events.jsonl` says what HAPPENED. A false closure — a `closed` that stopped
# being closed — is invisible in the first, because by then the record says `reopened`
# and nothing remembers it was ever anything else.

# Sessions the orchestrator did not end on purpose. `refused` is excluded: a role that
# refuses its charter and says why has done its job, and counting it as a kill would
# make the refusal machinery look like instability.
# `stalled` (phase 12) belongs here: it is a session the orchestrator ended on purpose,
# and a run that stalled repeatedly would otherwise look calm. `refused` is still
# excluded — a role that refuses its charter and says why has done its job.
KILLED_OUTCOMES = ("timeout", "crash", "cancelled", "provider-error", "stalled")
# Statuses that mean validation has been passed, and the ones that mean it killed the
# finding. Kept beside the metric that divides one by the other.
VALIDATION_SURVIVED = ("validated", "planned", "fixed", "closed", "reopened")
VALIDATION_KILLED = ("disputed", "obsolete", "withdrawn")
REMEDIATION_REACHED = ("planned", "fixed", "closed", "reopened")
# Fix durability's published baseline: Ouroboros-2's final number, and the figure the
# pre-registered hypothesis is stated against. Not a target — a prior.
DURABILITY_BASELINE_PCT = 70.3


def _rate(num, den):
    """A percentage, or None when the denominator is empty.

    `None` and not `0.0`: "no finding has reached a verdict yet" and "every finding
    that reached a verdict failed" are opposite facts, and a metric that prints 0 for
    both has thrown away the difference.
    """
    return None if not den else round(100 * num / den, 1)


def _tally(pairs):
    out = {}
    for k in pairs:
        out[str(k)] = out.get(str(k), 0) + 1
    return out


def metrics_report(state, events, independence=None):
    """Every pre-registered metric and breakdown, as JSON-safe data.

    `events` is the list `envelope.read_events` returns; an empty list is legal and
    the metrics that need it report `null` with their reason, rather than 0.

    `independence` is passed IN rather than computed here: the summary lives in
    `cli.py`, which sits above this module, and importing upward to avoid one
    parameter would invert the layering the split exists to establish.
    """
    records = list(state.findings) + list(state.class_findings)
    ev = list(events or [])
    transitions = [e for e in ev if e.get("event") == "state_transition"]

    # --- M1 auditor accuracy: of findings that REACHED validation, the share that
    # survived it. Unchanged from 0.8.0 and restated here so all eight live together.
    pool = [r for r in records
            if r.status in VALIDATION_SURVIVED or r.status in VALIDATION_KILLED]
    survived = [r for r in pool if r.status in VALIDATION_SURVIVED]

    # --- M2 fix durability: of findings that reached a Verifier VERDICT, the share
    # closed on the FIRST attempt. Measured against DURABILITY_BASELINE_PCT.
    reached = [r for r in records if r.status in ("closed", "reopened")]
    first_pass = [r for r in reached if r.status == "closed" and r.attempts == 0]
    durability = _rate(len(first_pass), len(reached))

    # --- M3 reopen rate. A DIFFERENT denominator from durability on purpose: every
    # finding that reached remediation, including the ones still sitting at `planned`
    # or `fixed`. Over the verdict pool alone it would be 100 - durability and would
    # measure nothing new.
    remediated = [r for r in records if r.status in REMEDIATION_REACHED]
    reopened_ever = [r for r in remediated if r.attempts >= 1 or r.status == "reopened"]

    # --- M4 human interventions: gates the cycle stopped at, from the event stream,
    # plus the rulings and admissions a human left in the record.
    gates = [e for e in ev if e.get("event") == "gate_reached"]
    rulings = [r for r in records if getattr(r, "norm_ruling", None)]
    retakes = [e for e in transitions if "human:" in str(e.get("summary", ""))]

    # --- M5 false-closure rate: closures that later stopped being closures. §5 makes
    # `closed` terminal, so the only way out is a human re-take — which is exactly the
    # event worth counting.
    closures = [e for e in transitions if e.get("to_status") == "closed"]
    undone = [e for e in transitions if e.get("from_status") == "closed"]
    # A stream written before phase 10 carries no status fields at all. Say so rather
    # than reporting 0.0% over an empty numerator and denominator.
    statuses_recorded = any("to_status" in e or "from_status" in e for e in transitions)

    # --- M6 cost per accepted finding. The PROXY is wall-clock session seconds: token
    # cost needs provider billing data the tool never sees. Named as a proxy in the
    # payload so no reader can mistake it for money.
    seconds = sum(int(s.get("duration_s") or 0) for s in state.sessions)
    accepted = [r for r in records if r.status not in ("reported", "rejected", "deferred")]

    # --- M6b tokens. `metrics` used to say token cost was unavailable without provider
    # billing data. Half of that was true and half was not: MONEY needs billing data the
    # tool never sees, but the token figure is printed in every session log the tool
    # already writes, and is now read at `finish` and carried on the session's own
    # `session_finished` event — so every reader gets the same number instead of
    # re-deriving it, and a stream written before the field existed simply reports its
    # sessions as unmeasured.
    #
    # The headline is per CANONICAL TRANSITION, not per session, and that is the whole
    # point: a cheap session that moved nothing is not an improvement, and a run can lower
    # its cost-per-session while raising the cost of everything it actually achieved.
    from xcheck.envelope import TELEMETRY_FIELDS, declared_verbs
    _declared = declared_verbs()
    moved_sessions = {e.get("session_id") for e in transitions
                      if e.get("verb") in _declared}
    canonical = [e for e in transitions if e.get("verb") in _declared]
    finished = [e for e in ev if e.get("event") == "session_finished"]
    measured = [e for e in finished if isinstance(e.get("tokens"), int)
                and not isinstance(e.get("tokens"), bool)]
    total_tokens = sum(e["tokens"] for e in measured)
    barren_tokens = sum(e["tokens"] for e in measured
                        if e.get("session_id") not in moved_sessions)

    # The denominator has to come from the SAME sessions as the numerator. The first
    # version of this block divided the measured sessions' tokens by EVERY canonical
    # transition in the stream, so a project with 7 measured sessions of 158 reported
    # "10,483 tokens per canonical transition" — a figure built from 7 sessions' cost and
    # 159 transitions' work, when those 7 sessions made 24 of them. It is not an
    # approximation of the true rate: the denominator was 6.6x too large, so the reported
    # cost was 6.6x too low, and it gets better-looking the more of the run goes
    # unmeasured. Every derived figure below is scoped to `measured`, and `coverage_pct`
    # sits beside them so a reader can see how much of the run they speak for.
    measured_ids = {e.get("session_id") for e in measured}
    canonical_measured = [e for e in canonical if e.get("session_id") in measured_ids]

    # --- M7 coverage completeness: passes whose coverage report is DONE.
    covered = [q for q in state.queue if q.coverage and q.coverage.status == "done"]

    # --- M8 recovery after a killed session: of the sessions that ended in a kill
    # class, the share the run continued past. "Continued" = a later session was
    # dispatched; the lease machinery is what makes that possible, and a run that
    # stopped dead at the kill is the failure this measures.
    killed = [i for i, s in enumerate(state.sessions)
              if (s.get("outcome") or "") in KILLED_OUTCOMES]
    recovered = [i for i in killed if i < len(state.sessions) - 1]

    # --- P2 breakdowns (audit item 12). Same computation surface, so they live here
    # rather than in a second reader.
    by_dim_reached, by_dim_first = {}, {}
    for r in reached:
        by_dim_reached[r.dimension] = by_dim_reached.get(r.dimension, 0) + 1
    for r in first_pass:
        by_dim_first[r.dimension] = by_dim_first.get(r.dimension, 0) + 1
    # "Fix type" is not a field any record carries. The one distinction the record DOES
    # carry is individual vs class remediation — a class fix is a different act with a
    # different failure mode — so that is the split, named for what it is.
    cls_ids = {c.id for c in state.class_findings}
    def _kind(r):
        return "class" if r.id in cls_ids else "individual"
    by_type_reached, by_type_first = {}, {}
    for r in reached:
        by_type_reached[_kind(r)] = by_type_reached.get(_kind(r), 0) + 1
    for r in first_pass:
        by_type_first[_kind(r)] = by_type_first.get(_kind(r), 0) + 1

    recurrences = [r for r in records if getattr(r, "recurrence_of", None)]
    reopen_causes = _tally(e.get("reason") or "unrecorded"
                           for e in transitions if e.get("to_status") == "reopened")
    closed_records = [r for r in records if r.status == "closed"]
    residue = [q for q in state.queue
               if q.coverage and q.coverage.status == "done" and q.coverage.findings]

    return {
        "auditor_accuracy_pct": _rate(len(survived), len(pool)),
        "fix_durability_pct": durability,
        "fix_durability_baseline_pct": DURABILITY_BASELINE_PCT,
        "reopen_rate_pct": _rate(len(reopened_ever), len(remediated)),
        "human_interventions": {
            "gates": len(gates),
            "by_gate": _tally(e.get("gate") or "unknown" for e in gates),
            "norm_rulings": len(rulings),
            "human_retakes": len(retakes),
            "total": len(gates) + len(rulings),
        },
        "false_closure_rate_pct": (
            _rate(len(undone), len(closures)) if statuses_recorded else None),
        "false_closures": len(undone),
        "closures_recorded": len(closures),
        "tokens": {
            # null, never 0, wherever there is nothing to divide: an absent measurement
            # is not a measurement of zero, and that distinction is the reason this
            # block reports its own coverage beside every figure.
            "sessions_finished": len(finished),
            "sessions_measured": len(measured),
            "sessions_unmeasured": len(finished) - len(measured),
            "total": total_tokens if measured else None,
            "canonical_transitions": len(canonical),
            # The raw count above is every canonical transition in the stream; the one
            # below counts only those made by a session whose cost is known. The ratio
            # divides by the SECOND. Both are reported so the gap is visible rather than
            # hidden inside a single number.
            "canonical_transitions_measured": len(canonical_measured),
            "coverage_pct": _rate(len(measured), len(finished)),
            "per_canonical_transition": (
                round(total_tokens / len(canonical_measured))
                if measured and canonical_measured else None),
            "on_sessions_that_moved_nothing": barren_tokens if measured else None,
            "share_that_moved_nothing_pct": (
                _rate(barren_tokens, total_tokens) if measured and total_tokens else None),
            "scope": "every figure in this block is over the MEASURED sessions only, and "
                     "`coverage_pct` says how many of the finished sessions that is. A "
                     "ratio whose numerator covers part of a run and whose denominator "
                     "covers all of it is not a rate.",
            "source": "the `tokens used` figure the session log reports, read at finish "
                      "and carried on the session's own event. A session whose log "
                      "carries no figure is counted in `sessions_unmeasured` and "
                      "contributes to no total — it is not read as zero.",
        },
        # Phase 11. One coverage figure per FIELD, because coverage varies per field: a
        # provider that reports token counts may report no request id, and quoting one
        # field's coverage for another is the same error as quoting the measured
        # sessions' cost over every transition in the run.
        "telemetry": {
            "sessions_finished": len(finished),
            "by_field": {
                f: {"measured": len(seen),
                    "unmeasured": len(finished) - len(seen),
                    "coverage_pct": _rate(len(seen), len(finished))}
                for f, seen in (
                    (f, [e for e in finished if e.get(f) is not None])
                    for f in TELEMETRY_FIELDS)},
            "by_source": {
                src: len([e for e in finished if e.get("telemetry_source") == src])
                for src in TELEMETRY_SOURCES},
            "disagreements": len([e for e in finished
                                  if e.get("telemetry_disagreement")]),
            "fields": list(TELEMETRY_FIELDS),
            "scope": "coverage is per FIELD over the finished sessions. A field measured "
                     "for some of them says nothing about the others: null means NOT "
                     "MEASURED and is never read as zero, and `by_source` says how many "
                     "figures the provider reported rather than a regular expression "
                     "scraped out of the log text.",
        },
        "cost_per_accepted_finding": {
            "proxy": "wall-clock session seconds. MONEY cost is not available to this "
                     "tool without provider billing data and no dollar figure is "
                     "printed; TOKEN cost is available and is reported under `tokens`, "
                     "per canonical transition rather than per session",
            "session_seconds": seconds,
            "accepted_findings": len(accepted),
            "seconds_per_accepted_finding": (
                None if not accepted else round(seconds / len(accepted), 1)),
        },
        "coverage_completeness_pct": _rate(len(covered), len(state.queue)),
        "recovery_after_kill_pct": _rate(len(recovered), len(killed)),
        "killed_sessions": len(killed),
        "breakdowns": {
            "durability_by_dimension": {
                d: _rate(by_dim_first.get(d, 0), n) for d, n in sorted(by_dim_reached.items())},
            "durability_by_fix_type": {
                t: _rate(by_type_first.get(t, 0), n) for t, n in sorted(by_type_reached.items())},
            "recurrence_rate_pct": _rate(len(recurrences), len(records)),
            "reopen_cause": reopen_causes,
            "verification_independence": independence or {},
            "human_intervention_rate": (
                None if not state.queue else round((len(gates) + len(rulings)) / len(state.queue), 2)),
            "seconds_per_closed_finding": (
                None if not closed_records else round(seconds / len(closed_records), 1)),
            "residue_pass_share_pct": _rate(len(residue), len(covered)),
            "false_closures_found_later": len(undone),
            # Phase 14. A session dispatched before routing existed, or with the
            # flag off, recorded no route and is counted as `unrouted` rather
            # than folded into one — averaging a cheap scan and a security
            # verification into a single figure is the thing this breakdown is
            # here to stop.
            "sessions_by_route": _sessions_by_route(state),
        },
    }


def _sessions_by_route(state):
    """How many sessions took each route. `unrouted` is a route name here on purpose:
    it is the honest label for a session nobody routed, and it keeps the denominator
    whole."""
    counts = {}
    for sess in getattr(state, "sessions", ()) or ():
        route = (sess or {}).get("route") or "unrouted"
        counts[route] = counts.get(route, 0) + 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------- embargo


# The count of IDENTICAL no-progress attempts a charter may accumulate before the
# orchestrator stops paying for another one. Three, because that was the operator's
# answer when the map was charted: two failures can be a flake, a third on unchanged
# inputs is a pattern. P-05 was dispatched 54 times.
EMBARGO_AFTER = 3


def charter_slice(state, pass_id, policy_digest=None):
    """The state a charter's own progress depends on, as a digest.

    This replaces a GLOBAL count of declared transitions. That count was the right idea
    at the wrong scope: it asked "has anything moved anywhere", so a `file-finding` in a
    completely different pass lifted the embargo on a charter that was still exactly as
    stuck as before. The audit's probe showed it —

        held_before = True
        unrelated file-finding in another charter
        held_after_unrelated_transition = False

    — and no amount of tuning the counter fixes a scope error.

    The slice is what a re-dispatch of THIS charter would read differently:

      * the queue entry itself — its charter text, its stop condition, whether it is
        done, and the coverage report bound to it;
      * every finding filed against this pass, by id and status: this is the
        "accepted findings/decisions" input, and it is what makes a human triaging the
        pass's findings count as relevant movement;
      * the policy version, because a charter blocked by the containment it ran under
        becomes worth re-dispatching when the operator changes that containment.

    What is deliberately NOT in it: logs, timestamps, courier commits, and any event
    belonging to another pass. Those are the four things the audit named, and
    `test_the_fingerprint_ignores_the_four_named_inputs` enumerates them.
    """
    from xcheck.envelope import sha256_text
    entry = next((q for q in getattr(state, "queue", ()) if q.id == pass_id), None)
    rows = []
    if entry is not None:
        cov = entry.coverage
        rows.append(f"queue:{entry.id}:{entry.charter}:{entry.stop}:{entry.done}:"
                    f"{'' if cov is None else f'{cov.status}:{cov.report_path}'}")
    for rec in sorted(getattr(state, "findings", ()), key=lambda r: r.id):
        if rec.pass_id == pass_id:
            rows.append(f"finding:{rec.id}:{rec.status}:{rec.attempts}")
    rows.append(f"policy:{policy_digest or 'unknown'}")
    return sha256_text("\n".join(rows))


def _embargo_key(role, charter, slice_digest, source_digest):
    """What must change before the same charter is worth dispatching again.

    Every component is computed by the ORCHESTRATOR. The analysis that opened this work
    proposed including a typed `blocker_code` the agent reports; a key that trusts the
    subject's own account of why it failed inherits the disease it is meant to cure —
    `rc=0` is exactly such an account.

    `slice_digest` is `charter_slice` over THIS charter: the pass entry, the findings
    filed against it, and the policy in force. It replaced a global count of declared
    transitions, which lifted an embargo whenever anything moved anywhere. Canonical
    state still moves only through write verbs, so a rewritten pass report, a fresh log,
    a new timestamp and a courier commit leave the slice untouched — which is what must
    NOT lift an embargo. `head` and `state_revision` were considered and rejected for
    the opposite reason: the courier moves both on every session, so a key containing
    either can never repeat and the embargo would never fire.

    `source_digest` is what lets a fix lift the embargo. When a pass is blocked by a
    defect in the orchestrator, repairing the defect changes the code image and every
    held charter becomes takeable again.
    """
    return (role, charter, slice_digest, source_digest)


def _embargo_scan(events, role, declared=None):
    """One pass over the stream: `(sessions_that_moved, failures)`.

    `failures` maps a charter hash to the barren attempts made against it, each as
    `(key, session_id)` in dispatch order. A session counts as an attempt when it
    recorded no DECLARED transition — the same test `envelope.receipt` applies, drawn
    from the same `declared` set so the two cannot drift apart.

    The verb filter is load-bearing in BOTH places. `write._apply` takes `verb` as a free
    string, so a session can write a transition under a verb the orchestrator does not
    declare. If such a row advanced `generation`, a session could lift its own embargo by
    inventing a verb — the receipt would still refuse to call it progress while the
    counter quietly moved the world on. Nothing in the corpus did this deliberately, but
    two of its verbs are minted, and a test plants one.
    """
    from xcheck.envelope import declared_verbs
    declared = declared_verbs() if declared is None else frozenset(declared)
    keys, digests, slices, moved, failures = {}, {}, {}, set(), {}
    for e in events:
        kind = e.get("event")
        if kind == "dispatch_key":
            digests[e.get("session_id")] = e.get("source_digest")
            # The charter's own slice AS IT WAS at dispatch. Recorded in the stream for
            # the same reason `source_digest` is: a replay cannot recompute a historical
            # slice from present-day state, and a key that silently used today's value
            # for yesterday's dispatch would compare a session against a world it never
            # ran in.
            slices[e.get("session_id")] = e.get("charter_slice")
        elif kind == "session_dispatched":
            keys[e.get("session_id")] = (e.get("role"), e.get("charter_hash"))
        elif kind == "state_transition" and e.get("verb") in declared:
            moved.add(e.get("session_id"))
    for sid, (r, charter_hash) in keys.items():
        if r != role or sid in moved:
            continue
        failures.setdefault(charter_hash, []).append(
            (_embargo_key(r, charter_hash, slices.get(sid), digests.get(sid)), sid))
    return len(moved), failures


def embargo_hold(events, role, charter_hash, source_digest, after=EMBARGO_AFTER,
                 scan=None, slice_digest=None):
    """`(attempts, key, last_session)` if this charter is held right now, else `None`.

    The core the queue report and the corpus replay both run through, so a claim measured
    against the frozen stream is a claim about shipped behaviour. `scan` accepts a
    prepared `_embargo_scan` result when a caller is asking about many charters at once.
    """
    _, failures = _embargo_scan(events, role) if scan is None else scan
    now = _embargo_key(role, charter_hash, slice_digest, source_digest)
    # An attempt counts toward the embargo when it was made against the SAME role, the
    # SAME charter and the SAME state slice, under the same orchestrator source. A
    # recorded component that is None — a dispatch from before the field existed —
    # MATCHES, which is the fail-closed direction for a historical replay: it counts
    # such attempts rather than silently exempting them, so a corpus that predates the
    # field is not scored as if every session ran in a different world.
    matching = [(k, sid) for k, sid in failures.get(charter_hash, ())
                if k[:2] == now[:2]
                and (k[2] is None or k[2] == now[2])
                and (k[3] is None or k[3] == now[3])]
    if len(matching) < after:
        return None
    return len(matching), now, matching[-1][1]


def embargo_report(events, unchecked, source_digest, role="Auditor", after=EMBARGO_AFTER,
                   state=None, policy_digest=None):
    """`(takeable, held)` for the queued passes, from the event stream alone.

    `held` maps a pass id to `(attempts, key, last_session)`. Nothing is stored: the
    attempt count is recounted here every time, which is why a crash cannot leave a stale
    counter behind and why this same function replays the frozen corpus.

    A dispatch whose `source_digest` was never recorded — every session before this
    machinery existed — is treated as MATCHING the current one. That is the fail-closed
    direction for a historical replay: it counts such attempts toward the embargo rather
    than silently exempting them, so a corpus that predates the field is not scored as if
    every one of its sessions ran under different code.
    """
    from xcheck.envelope import sha256_text
    scan = _embargo_scan(events, role)
    takeable, held = [], {}
    for pass_id in unchecked:
        # Each pass is asked about its OWN slice. Without `state` there is nothing to
        # slice and the comparison falls back to the recorded value matching anything,
        # which is the same fail-closed rule a pre-field corpus gets.
        now_slice = (charter_slice(state, pass_id, policy_digest)
                     if state is not None else None)
        hold = embargo_hold(events, role, sha256_text(pass_id), source_digest,
                            after=after, scan=scan, slice_digest=now_slice)
        if hold:
            held[pass_id] = hold
        else:
            takeable.append(pass_id)
    return takeable, held
