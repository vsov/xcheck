"""`audit/state.json` — the single canonical control plane.

Machine state (queue, statuses, limits, coverage, routing, class membership,
construals) lives HERE and nowhere else. Markdown keeps what Markdown is good
at: the human-readable evidence body of a finding, the prose of a pass report,
the reasoning in a plan. `LEDGER.md` becomes a generated view (phase 6) and is
never read back as authority.

Why JSON, and why this must not be "improved" back
--------------------------------------------------
The defect this replaces is not that the Markdown parsers were bad. It is that
Markdown has an unbounded syntax space, so every guard models one more known
construct while the space it must cover does not shrink. Eight rounds went into
`reopen_limit` alone: in an HTML comment, in a fenced block, indented as code,
declared twice, in a second Limits section. F-0130 took twelve rounds for the
same reason.

JSON ends that class rather than another of its members. A JSON object has no
comment that could carry a limit and no line that could pretend to be a queue
entry, so a commented-out override stops being a rejected input and becomes an
UNEXPRESSIBLE one. There is nothing to harden.

This is also why the answer is not "add a real CommonMark or YAML parser". That
would keep machine state in a human-editable prose format and merely make the
ambiguity less visible — the architectural mistake preserved behind a better
implementation of it. JSON is in the stdlib, is unambiguous, and does not
require yet another hand-rolled almost-YAML.

`ensure_ascii=False` on write is deliberate: findings are written in the
operator's working language (Russian, here), and escaping them to `\\uXXXX`
would make the durable record unreadable to the humans who triage from it.

The vocabularies are NOT redeclared here
----------------------------------------
`STATUSES`, `SEVERITIES`, `REFUSAL_REASONS`, `NEXT_OWNER` and the id/session
patterns are imported from `util`. A second copy of a canonical enumeration is
precisely the drift CF-0001 was raised about; the schema must be the same
vocabulary the rest of the tool already speaks, or the state file and the
methodology can disagree about what a status is.

Adequacy against real archived audits (phase-4 criterion 7)
-----------------------------------------------------------
The schema was probed field-by-field against every record in
`audit-archive/ouroboros-3` (29 findings, 4 passes, 11 plans, 24 construals) and
`audit-archive/ouroboros-2` (130 findings, 1 class finding, 32 passes, 47 plans).
ouroboros-3 reports zero gaps: no field on disk is unrepresentable, no required
field is absent, no value is refused.

ouroboros-2 leaves ONE named migration limit, carried to phase 6: five findings
(F-0090, F-0092, F-0094, F-0097, F-0098) record `fixed-by:
claude-remediator-P28-c21ba9500da0`, a pre-F-0096 session-identity form. Today's
tool already refuses that value, so the schema is not loosened to admit it —
`xcheck migrate` must report those records and stop rather than silently mint
provenance that was never canonical.
"""

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from xcheck.policy import TRUST_LEVELS
from xcheck.util import (
    CONSTRUAL_STATUSES, ENVELOPE_ADMITTER_RE, GIT_TIMEOUT, HUMAN_ADMITTER_RE,
    INDEPENDENCE_LEVELS, NEXT_OWNER, OUTCOMES, REFUSAL_REASONS, SESSION_ID_RE,
    SEVERITIES, STATUSES, TELEMETRY_SOURCES, _canon_calendar_date, construal_key
)

SCHEMA_VERSION = 1
STATE_FILENAME = "state.json"

FINDING_ID_RE = re.compile(r"^F-\d{4}$")
CLASS_ID_RE = re.compile(r"^CF-\d{4}$")
ANY_FINDING_ID_RE = re.compile(r"^(?:F|CF)-\d{4}$")
PASS_ID_RE = re.compile(r"^P-\d{2}$")
PLAN_ID_RE = re.compile(r"^RP-\d{4}$")
NORM_ID_RE = re.compile(r"^N\d+$")
CONSTRUAL_KEY_RE = re.compile(r"^[0-9a-f]{16}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA256_OR_UNKNOWN_RE = re.compile(r"^(?:[0-9a-f]{64}|unknown)$")
HEAD_RE = re.compile(r"^(?:[0-9a-f]{7,40}|unknown)$")

ROLES = frozenset({"Planner", "Auditor", "Triage", "Remediator", "Verifier"})
PLAN_STATUSES = frozenset({"open", "done"})
PASS_STATUSES = frozenset({"queued", "done", "split"})
# Every `next` value any status may legally carry, as one flat vocabulary.
NEXT_VALUES = frozenset(v for owners in NEXT_OWNER.values() for v in owners) | {"⚠ needs-human"}

# The methodology's limits — XCHECK.md §10's numeric rows, and exactly those.
# `triage_batch_cap` used to sit here and does not belong: it is an
# ORCHESTRATOR key (`util.CONF_DEFAULTS`), and `decision.py` reads it from
# `conf`, never from `state.limits`. Listing it here made `set-limit` accept a
# key whose value no consumer reads — the very thing that verb's own refusal
# message says it refuses. It is set in `audit/orchestrator.conf`, where
# README §8 documents it.
LIMIT_FIELDS = {
    "reopen_limit": (1, 99),
    "remediation_batch_size": (1, 999),
    "class_threshold": (1, 999),
    "max_findings_per_pass": (1, 999),
}


class StateError(Exception):
    """An addressed refusal to read `audit/state.json`.

    Every message names WHERE (file, record, field), WHAT was found and WHAT was
    expected. A consumer that catches this and continues with partial data has
    reintroduced the fail-open the whole state model exists to remove — so there
    is no partial result to catch: `load_state` either returns a fully validated
    `State` or raises.
    """


# ---------------------------------------------------------------------------
# records — all frozen, tuples not lists, so a consumer cannot mutate a
# validated State back into an unvalidated shape
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Finding:
    id: str
    title: str
    severity: str
    status: str
    dimension: str
    unit: tuple
    pass_id: str
    attempts: int
    updated: str
    body_path: str
    next: str = None                # an OVERRIDE; absent means `NEXT_OWNER[status]`
    fixed_by: str = None
    recurrence_of: str = None
    refusal: str = None
    admitted_scope: str = None
    blocked: str = None
    norm_ruling: str = None
    cls: str = None                 # `class` is a keyword; the JSON key stays "class"
    independence: str = None        # phase 9: measured, not asserted (see util)
    # PHASE 9 (third audit): where the evidence is, and what it hashed to in the subject
    # commit. None on every finding filed before the verb required them.
    locator: str = None
    source_hash: str = None


@dataclass(frozen=True)
class ClassFinding:
    id: str
    title: str
    severity: str
    status: str
    dimension: str
    unit: tuple
    pass_id: str
    attempts: int
    updated: str
    body_path: str
    members: tuple                  # required and non-empty: F-0151 closed by schema
    next: str = None
    created_by: str = None
    fixed_by: str = None
    refusal: str = None
    admitted_scope: str = None
    blocked: str = None
    norm_ruling: str = None
    independence: str = None


@dataclass(frozen=True)
class Coverage:
    report_path: str
    findings: tuple
    updated: str
    status: str


@dataclass(frozen=True)
class QueueEntry:
    id: str
    dimension: str
    units: tuple
    charter: str
    stop: str
    done: bool
    coverage: Coverage = None
    base: str = None                # phase 12: the revision this pass was scoped to


@dataclass(frozen=True)
class Plan:
    id: str
    findings: tuple
    status: str
    updated: str
    body_path: str
    attempts: int = 0


@dataclass(frozen=True)
class Construal:
    key: str
    role: str
    charter: str
    session: str
    status: str
    created: str
    body_path: str
    admitted_by: str = None
    admitted_at: str = None
    envelope: str = None


@dataclass(frozen=True)
class Norm:
    id: str
    source: str
    scope: str


@dataclass(frozen=True)
class Dimension:
    key: str
    catches: str
    norms: tuple                    # ids into `catalogs.norms`


@dataclass(frozen=True)
class Unit:
    id: str
    material: str
    size: str
    responsibility: str


@dataclass(frozen=True)
class Catalogs:
    norms: tuple = ()
    dimensions: tuple = ()
    units: tuple = ()


@dataclass(frozen=True)
class State:
    schema_version: int
    state_revision: int
    generated_by: str
    head_before: str
    # PHASE 8 (fourth audit). What the event stream looked like at the last canonical
    # transition: the chain digest and the event count. Recorded HERE, under the writing
    # lock, because a hash chain alone is rebuildable — anyone who can edit the file can
    # re-link everything after their edit and hand you a self-consistent stream. This is
    # the half of the check that does not live in the file being checked.
    ledger_anchor: dict = None
    # PHASE 3 (sixth audit). The ids of the last COMMIT_WINDOW transactions whose write
    # reached this file. A revision number is a counter: two different transactions can
    # produce the same one, and an unrelated writer advances it. Recovery needs to know
    # whether ONE SPECIFIC transaction committed, so the fact is recorded here, in the
    # same `os.replace` as the change it belongs to, and every writer participates by
    # going through `write_state`.
    ledger_commits: tuple = ()
    findings: tuple = ()
    class_findings: tuple = ()
    queue: tuple = ()
    plans: tuple = ()
    construals: tuple = ()
    # Both are DEEP-FROZEN by `freeze()` at construction — a tuple of mappingproxies
    # and a mappingproxy. `frozen=True` above only stops rebinding the field; it is
    # `freeze()` that stops `state.limits[k] = v` and `state.sessions[0][k] = v`.
    sessions: tuple = ()            # the invocation envelope, written by `envelope.store`
    limits: dict = field(default_factory=dict)
    catalogs: Catalogs = None

    def finding(self, fid):
        """The finding or class finding with this id, or None."""
        for r in self.findings + self.class_findings:
            if r.id == fid:
                return r
        return None

    def queue_entry(self, pid):
        for q in self.queue:
            if q.id == pid:
                return q
        return None


def next_owner(record):
    """Who must act on this record — the stored override, else derived from status.

    The ONE place the question is answered, so the generated LEDGER view and the
    decision path cannot give different answers to it (which is what made
    `next`-as-a-stored-column a recurring finding).
    """
    if record.next is not None:
        return record.next
    owners = NEXT_OWNER.get(record.status, {"—"})
    # `{"Triage", "human"}`-style sets are "either may act"; the default names the
    # ROLE, and a human who takes it instead records that as an explicit override.
    for preferred in ("Triage", "Remediator", "Verifier", "Auditor", "Planner", "—"):
        if preferred in owners:
            return preferred
    return sorted(owners)[0]


# ---------------------------------------------------------------------------
# the declarative schema
# ---------------------------------------------------------------------------

def _enum(values, what):
    def check(v, where):
        if v not in values:
            raise StateError(f"{where}: {v!r} is not a {what} "
                             f"(expected one of: {', '.join(sorted(map(str, values)))})")
    return check


def _pattern(rx, what):
    def check(v, where):
        if not isinstance(v, str) or not rx.match(v):
            raise StateError(f"{where}: {v!r} is not {what} (expected the form {rx.pattern})")
    return check


def _text(v, where):
    if not isinstance(v, str) or not v.strip():
        raise StateError(f"{where}: expected a non-empty string, got {v!r}")


def _date(v, where):
    if not isinstance(v, str):
        raise StateError(f"{where}: expected a YYYY-MM-DD date string, got {v!r}")
    # `_canon_calendar_date` returns an addressed problem string, or None when the
    # date is real — the ONE calendar check the whole tool shares (F-0119), so a
    # date that state.json accepts is a date the Markdown surfaces accepted too.
    problem = _canon_calendar_date("date", v)
    if problem is not None:
        raise StateError(f"{where}: {problem}")


def _nonneg_int(v, where):
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        raise StateError(f"{where}: expected a non-negative integer, got {v!r}")


def _bool(v, where):
    if not isinstance(v, bool):
        raise StateError(f"{where}: expected true or false, got {v!r}")


def _nonneg_number(v, where):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
        raise StateError(f"{where}: expected a non-negative number, got {v!r}")


def _signed_int(v, where):
    if not isinstance(v, int) or isinstance(v, bool):
        raise StateError(f"{where}: expected an integer, got {v!r}")


def _object(v, where):
    if not isinstance(v, dict):
        raise StateError(f"{where}: expected an object, got {type(v).__name__}")


def _list_of(check, allow_empty=True):
    def run(v, where):
        if not isinstance(v, list):
            raise StateError(f"{where}: expected a list, got {type(v).__name__}")
        if not allow_empty and not v:
            raise StateError(f"{where}: expected a non-empty list")
        seen = set()
        for i, item in enumerate(v):
            check(item, f"{where}[{i}]")
            if isinstance(item, str):
                if item in seen:
                    raise StateError(f"{where}: {item!r} listed twice — a duplicate is "
                                     f"ambiguity, not emphasis")
                seen.add(item)
    return run


_STATUS = _enum(STATUSES, "canonical §5 status")
_SEVERITY = _enum(SEVERITIES, "canonical §2 severity")
_NEXT = _enum(NEXT_VALUES, "canonical `next` actor")
_SESSION = _pattern(SESSION_ID_RE, "a canonical 16-lowercase-hex session id")
_REFUSAL = _enum(REFUSAL_REASONS, "canonical §5 refusal reason")

# record type -> (required fields, optional fields); each maps to a value check.
# `next` is NOT required, and that is the point. In the Markdown world it lived
# only in a LEDGER.md column, and a row whose `next` disagreed with its own
# `status` was a whole family of findings (F-0005, F-0014, F-0025): two places
# claiming to say who acts next. Here `next` is an OPTIONAL override — absent, the
# owner is `NEXT_OWNER[status]`, computed. So the redundant copy that could drift
# simply is not part of a record unless a human deliberately routed against the
# default (`human`, `⚠ needs-human`), and even then it must still own the status.
FINDING_FIELDS = ({
    "id": _pattern(FINDING_ID_RE, "a canonical finding id"),
    "title": _text,
    "severity": _SEVERITY,
    "status": _STATUS,
    "dimension": _text,
    "unit": _list_of(_text, allow_empty=False),
    "pass": _pattern(PASS_ID_RE, "a canonical pass id"),
    "attempts": _nonneg_int,
    "updated": _date,
    "body_path": _text,
}, {
    "next": _NEXT,
    "fixed_by": _SESSION,
    "recurrence_of": _pattern(ANY_FINDING_ID_RE, "a canonical finding id"),
    "refusal": _REFUSAL,
    "admitted_scope": _text,
    "blocked": _text,
    "norm_ruling": _text,
    "class": _pattern(CLASS_ID_RE, "a canonical class-finding id"),
    # PHASE 9 (third audit): WHERE the evidence is and WHAT it hashed to in the subject
    # commit this finding was filed against. Optional in the SCHEMA and required by the
    # write verb — the 133 findings already in this corpus were filed before either
    # existed, and a required field would make the existing record unloadable, which is
    # rewriting admitted evidence to make a new rule look older than it is.
    "locator": _text,
    "source_hash": _pattern(SHA256_RE, "a sha-256 digest"),
    # phase 9: how independent the verdict on this finding actually was, MEASURED
    # from the two invocation envelopes. Optional because it only exists once a
    # verdict has been recorded — and absent is a different claim from `unrecorded`.
    "independence": _enum(INDEPENDENCE_LEVELS, "measured independence level"),
})

CLASS_FINDING_FIELDS = ({
    "id": _pattern(CLASS_ID_RE, "a canonical class-finding id"),
    "title": _text,
    "severity": _SEVERITY,
    "status": _STATUS,
    "dimension": _text,
    "unit": _list_of(_text, allow_empty=False),
    "pass": _pattern(PASS_ID_RE, "a canonical pass id"),
    "attempts": _nonneg_int,
    "updated": _date,
    "body_path": _text,
    # F-0151 was "a CF passes validation with no members". In the schema a CF
    # without a non-empty members list is not a rejected record — it is not a
    # representable one.
    "members": _list_of(_pattern(ANY_FINDING_ID_RE, "a canonical finding id"),
                        allow_empty=False),
}, {
    "next": _NEXT,
    "created_by": _text,
    "fixed_by": _SESSION,
    "refusal": _REFUSAL,
    "admitted_scope": _text,
    "blocked": _text,
    "norm_ruling": _text,
    "independence": _enum(INDEPENDENCE_LEVELS, "measured independence level"),
})

COVERAGE_FIELDS = ({
    "report_path": _text,
    "findings": _list_of(_pattern(ANY_FINDING_ID_RE, "a canonical finding id")),
    "updated": _date,
    "status": _enum(PASS_STATUSES, "canonical pass-record status"),
}, {})

QUEUE_FIELDS = ({
    "id": _pattern(PASS_ID_RE, "a canonical pass id"),
    "dimension": _text,
    "units": _list_of(_text, allow_empty=False),
    "charter": _text,
    "stop": _text,
    "done": _bool,
}, {
    "coverage": None,               # a nested record; handled explicitly
    # PHASE 12 (third audit): the revision this pass was SCOPED AGAINST, when it was
    # scoped at all. Absent means the pass looked at its whole unit — which is what
    # every pass before this phase did, and what `scope.sweep_status` counts as a full
    # sweep. A reader must be able to tell an audit of everything from an audit of a
    # diff without asking the person who ran it.
    "base": _text,
})

PLAN_FIELDS = ({
    "id": _pattern(PLAN_ID_RE, "a canonical plan id"),
    "findings": _list_of(_pattern(ANY_FINDING_ID_RE, "a canonical finding id"),
                         allow_empty=False),
    "status": _enum(PLAN_STATUSES, "canonical plan status"),
    "updated": _date,
    "body_path": _text,
}, {
    "attempts": _nonneg_int,
})

CONSTRUAL_FIELDS = ({
    "key": _pattern(CONSTRUAL_KEY_RE, "a canonical 16-lowercase-hex construal key"),
    "role": _enum(ROLES, "canonical role"),
    "charter": _text,
    "session": _SESSION,
    "status": _enum(CONSTRUAL_STATUSES, "canonical construal status"),
    "created": _date,
    "body_path": _text,
}, {
    "admitted_by": None,            # human:<name> | envelope:<name> | a session id
    "admitted_at": _date,
    "envelope": _text,
})

# Phase 9: the invocation envelope — the ORCHESTRATOR's record of one dispatch.
# Required here is exactly what is known BEFORE the child starts; the rest is written
# when it finishes, so a record whose `exit_status` is null is by definition the
# in-flight dispatch. That is not a convention: it is what makes `fixed-by` bindable to
# something the agent cannot forge (F-0159), because there is no write verb that
# reaches this list at all.
SESSION_FIELDS = ({
    "session_id": _SESSION,
    "role": _enum(ROLES, "canonical role"),
    "provider": _text,                  # `unknown` is recorded, never guessed
    "agent_model": _text,
    "executable": _text,
    "executable_version": _text,
    "charter_hash": _pattern(SHA256_RE, "a sha-256 digest"),
    "prompt_hash": _pattern(SHA256_RE, "a sha-256 digest"),
    "state_revision": _nonneg_int,
    "head_before": _pattern(HEAD_RE, "a git object name or `unknown`"),
    "sandbox_profile": _text,
    "started": _text,
}, {
    # PHASE 3: how the OPERATOR classified the material for this dispatch. Optional
    # because every record written before phase 3 has none, and rewriting an admitted
    # envelope to add one would be inventing a decision nobody made.
    "trust_level": _enum(TRUST_LEVELS, "an operator trust classification"),
    "head_after": _pattern(HEAD_RE, "a git object name or `unknown`"),
    "sandbox_details": _object,         # a profile is a named capability SET (phase 8)
    "duration_s": _nonneg_number,
    "exit_status": _signed_int,                # a signal death is negative; null = never observed
    "outcome": _enum(OUTCOMES, "declared session outcome"),
    "log_digest": _pattern(SHA256_OR_UNKNOWN_RE, "a sha-256 digest or `unknown`"),
    "note": _text,                      # why a field is missing, when one is
    # Phase 11: the DECLARED concurrent group a dispatch belongs to. Present only on
    # sessions the parallel dispatcher launched together; absent (and refused as
    # unknown by an older xcheck, which is the fail-closed direction) everywhere else.
    "concurrent_group": _text,
    # W-02: what this session cost, in tokens, read from its own log at `finish`. NULL is
    # a REAL value here and means NOT MEASURED — the provider printed no figure, or the
    # session was killed before it printed one — and is never to be read as zero; a
    # measured zero is not a thing an agent session produces. Optional for the same reason
    # `concurrent_group` is: an older xcheck refuses an unknown field, which is the
    # fail-closed direction, and adding one implies no migration.
    "tokens": _nonneg_int,
    # Which operator profile was in force, by sha256 of its exact bytes. OPTIONAL, and
    # for the same reason `tokens` is: a session recorded before the field existed
    # carries none, and refusing those would rewrite already-admitted evidence. An
    # absent value is "not recorded", never "no policy applied" — a dispatch with no
    # resolvable profile is refused before it reaches this record at all.
    "policy_digest": _text,
    # PHASE 9 — WHAT WAS AUDITED, as four durable facts instead of one ambiguous
    # `head_before`. That field recorded whatever `git rev-parse HEAD` answered where the
    # write happened, and inside a disposable worktree that is the sandbox's own seed
    # commit: `130646f4…` in this repository's state document is in no branch and no tag,
    # `git fsck` lists it as unreachable, and the next `git gc` takes the provenance with
    # it. One field was answering four questions, so it answered all of them badly.
    #
    #   subject_commit    the commit of the project being audited, as it stood at
    #                     dispatch, in the OUTER repository — the one a reader can check
    #                     out. Reachable from a ref, or the provenance is a dead pointer.
    #   subject_manifest  a content digest over the audited tree (the index plus the
    #                     uncommitted delta). Not an object name: it survives GC of every
    #                     commit, and it is the only field that still answers "was this
    #                     the same code" when the branch has been rewritten.
    #   sandbox_seed      the disposable worktree's synthetic commit. Recorded and
    #                     LABELLED disposable rather than deleted — it is what the child
    #                     actually ran against, and the value that used to be filed under
    #                     `head_before` has a home here instead of being silently dropped.
    #   controller_commit the commit of xcheck ITSELF. The subject and the tool are two
    #                     different programs and either can explain a result.
    #
    # All four OPTIONAL, like `policy_digest` and for the same reason: a session recorded
    # before this phase carries none, and refusing those records would rewrite evidence
    # that was already admitted.
    # PHASE 11 — TELEMETRY. `tokens` (W-02) is the headline total and keeps its meaning;
    # these say where it came from and what it was made of. All null-able, and null means
    # NOT MEASURED — never zero. A measured zero is not something an agent session
    # produces, so a run that wrote 0 for an absent figure would have thrown away the
    # difference between "cost nothing" and "we do not know", which is the whole reason
    # the metrics block reports its own coverage.
    #
    # `telemetry_source` is a field and not a naming convention: a reader must be able to
    # tell a number the PROVIDER reported from one scraped out of prose by a regular
    # expression, and a convention that lives in variable names does not reach the
    # record. `telemetry_disagreement` exists because the two sources CAN disagree, and
    # the honest answer is to keep both and say so rather than silently prefer one.
    #
    # `first_output_s` and `idle_s` are measured by the orchestrator while it streams the
    # child, not reported by anyone. They are the two figures no provider can supply and
    # the two that diagnose a session that produced only its own header.
    "telemetry_source": _enum(TELEMETRY_SOURCES, "a telemetry source"),
    "tokens_input": _nonneg_int,
    "tokens_output": _nonneg_int,
    "tokens_cache": _nonneg_int,
    "tokens_log": _nonneg_int,
    "provider_model": _text,
    "reasoning_effort": _text,
    "provider_request_id": _text,
    "telemetry_disagreement": _text,
    # PHASE 11 (fourth audit) — three of the audit's ten adapter fields that nothing here
    # recorded before. `provider_stop_reason` is the provider's own word for why it
    # stopped; the two limits are what was APPLIED to the call. Together they are what
    # makes a budget auditable rather than merely enforced: a session that stopped at
    # `length` against a declared output limit was ended by configuration, and one that
    # stopped at `length` with no limit applied is a defect nobody could previously see.
    # PHASE 13 (fourth audit): whether the PROVIDER confirmed this session ran the model
    # its route planned. `attested` or `unattested` — never absent-meaning-fine, because
    # metrics aggregate by route and a route nobody checked is an annotation.
    "route_attestation": _enum(("attested", "unattested"), "a route attestation"),
    "provider_stop_reason": _text,
    "limit_input": _nonneg_int,
    "limit_output": _nonneg_int,
    # PHASE 6: a usage object that was OFFERED and refused. Recorded rather than dropped,
    # because a figure that fails validation silently is a figure the operator never
    # learns was offered — and the audit's forged object was exactly such an offer.
    "telemetry_refused": _text,
    "first_output_s": _nonneg_number,
    "idle_s": _nonneg_number,
    # PHASE 8: seconds to the first observable ACTIVITY, which is a different question
    # from the first byte — see `runner.ACTIVITY_SOURCES`.
    "first_activity_s": _nonneg_number,
    # Phase 14: the ROUTE this dispatch was resolved to. Optional because every record
    # written before phase 14 has none and because routing is off by default — a session
    # dispatched with the flag off recorded no route, and inventing one afterwards would
    # claim a decision nobody made.
    "route": _text,
    # PHASE 13 — WHICH CONTEXT this session was given, by content. The capsule is derived
    # deterministically from state and the charter, so the same inputs give the same
    # digest: a reader comparing two sessions can tell whether they were briefed the same
    # way, which is not answerable from a prompt hash (that changes with the session id).
    "capsule_digest": _pattern(SHA256_OR_UNKNOWN_RE, "a sha-256 digest or `unknown`"),
    "subject_commit": _pattern(HEAD_RE, "a git object name or `unknown`"),
    "subject_manifest": _pattern(SHA256_OR_UNKNOWN_RE, "a sha-256 digest or `unknown`"),
    "sandbox_seed": _pattern(HEAD_RE, "a git object name or `unknown`"),
    "controller_commit": _pattern(HEAD_RE, "a git object name or `unknown`"),
})

NORM_FIELDS = ({"id": _pattern(NORM_ID_RE, "a canonical norm id"),
                "source": _text, "scope": _text}, {})
DIMENSION_FIELDS = ({"key": _text, "catches": _text,
                     "norms": _list_of(_pattern(NORM_ID_RE, "a canonical norm id"),
                                       allow_empty=False)}, {})
UNIT_FIELDS = ({"id": _text, "material": _text, "size": _text,
                "responsibility": _text}, {})

# The three things the anchor records, and nothing else. A closed record for the same
# reason every other one here is closed: an unknown key means the writer and this reader
# disagree about what is being asserted.
# PHASE 3 (sixth audit). How many committed transaction ids canonical state remembers.
# The outbox is replayed at the start of every write, so a row normally waits for exactly
# one transaction; the window is large enough that a stream unwritable for a long stretch
# still heals, and small enough that the list stays a footnote in the document.
COMMIT_WINDOW = 64
TXN_RE = re.compile(r"^[0-9a-f]{16}$")

LEDGER_ANCHOR_FIELDS = {
    "digest": _pattern(SHA256_RE, "a sha-256 digest"),
    "events": _nonneg_int,
    "state_revision": _nonneg_int,
}

TOP_LEVEL = {
    "schema_version", "state_revision", "generated_by", "head_before", "ledger_anchor",
    "ledger_commits", "findings", "class_findings", "queue", "plans", "construals", "sessions",
    "limits", "catalogs",
}


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def _check_record(rec, spec, where):
    """One record against (required, optional). Unknown and missing both fail."""
    required, optional = spec
    if not isinstance(rec, dict):
        raise StateError(f"{where}: expected an object, got {type(rec).__name__}")
    unknown = sorted(set(rec) - set(required) - set(optional))
    if unknown:
        raise StateError(
            f"{where}: unknown field(s) {', '.join(repr(k) for k in unknown)} — the schema "
            f"is closed, so a field it does not declare is a typo or a private convention "
            f"no consumer reads (known here: {', '.join(sorted(set(required) | set(optional)))})")
    missing = sorted(set(required) - set(rec))
    if missing:
        raise StateError(
            f"{where}: missing required field(s) {', '.join(repr(k) for k in missing)} — a "
            f"machine-meaningful field is never defaulted, because a default is a guess "
            f"about state nobody recorded")
    for key, check in required.items():
        if check is not None:
            check(rec[key], f"{where}.{key}")
    for key, check in optional.items():
        if key in rec and rec[key] is not None and check is not None:
            check(rec[key], f"{where}.{key}")


def _check_unique(records, key, where, what):
    seen = {}
    for i, rec in enumerate(records):
        v = rec.get(key)
        if v in seen:
            raise StateError(
                f"{where}: {what} {v!r} is declared twice (entries {seen[v]} and {i}) — two "
                f"records for one id are two simultaneous states; there is no last-wins")
        seen[v] = i
    return seen


def _validate(doc, where):
    """Full schema + cross-record validation. Raises StateError or returns None.

    Deliberately builds NOTHING: no dataclass may exist for a document that has
    not passed every check, so a bug in a later stage cannot hand a consumer a
    half-validated State.
    """
    if not isinstance(doc, dict):
        raise StateError(f"{where}: expected a JSON object at the top level, "
                         f"got {type(doc).__name__}")

    unknown = sorted(set(doc) - TOP_LEVEL)
    if unknown:
        raise StateError(f"{where}: unknown top-level key(s) "
                         f"{', '.join(repr(k) for k in unknown)} "
                         f"(known: {', '.join(sorted(TOP_LEVEL))})")
    for key in ("schema_version", "state_revision", "generated_by"):
        if key not in doc:
            raise StateError(f"{where}: missing required top-level key {key!r}")

    if doc["schema_version"] != SCHEMA_VERSION:
        raise StateError(
            f"{where}.schema_version: this xcheck reads schema {SCHEMA_VERSION}, the file "
            f"declares {doc['schema_version']!r} — run `xcheck migrate` rather than letting "
            f"two schema generations share one audit")
    _nonneg_int(doc["state_revision"], f"{where}.state_revision")
    _text(doc["generated_by"], f"{where}.generated_by")
    if doc.get("head_before") is not None:
        _pattern(re.compile(r"^[0-9a-f]{7,40}$"), "a git object name")(
            doc["head_before"], f"{where}.head_before")
    if doc.get("ledger_anchor") is not None:
        anchor = doc["ledger_anchor"]
        if not isinstance(anchor, dict):
            raise StateError(f"{where}.ledger_anchor: expected an object, got "
                             f"{type(anchor).__name__}")
        extra = set(anchor) - set(LEDGER_ANCHOR_FIELDS)
        if extra:
            raise StateError(f"{where}.ledger_anchor: unknown key(s) {sorted(extra)} "
                             f"(known: {sorted(LEDGER_ANCHOR_FIELDS)})")
        for key, check in LEDGER_ANCHOR_FIELDS.items():
            if key not in anchor:
                raise StateError(f"{where}.ledger_anchor: missing {key!r}")
            check(anchor[key], f"{where}.ledger_anchor.{key}")

    if doc.get("ledger_commits") is not None:
        commits = doc["ledger_commits"]
        if not isinstance(commits, list):
            raise StateError(f"{where}.ledger_commits: expected a list, got "
                             f"{type(commits).__name__}")
        if len(commits) > COMMIT_WINDOW:
            raise StateError(f"{where}.ledger_commits: {len(commits)} entries, and the "
                             f"window is {COMMIT_WINDOW} — an unbounded confirmation list "
                             f"grows without limit inside a document every verb rewrites")
        for i, txn in enumerate(commits):
            _pattern(TXN_RE, "a transaction id")(txn, f"{where}.ledger_commits[{i}]")
        if len(set(commits)) != len(commits):
            raise StateError(f"{where}.ledger_commits: a transaction id appears twice; "
                             f"two writes cannot share one identity")

    findings = doc.get("findings", [])
    class_findings = doc.get("class_findings", [])
    queue = doc.get("queue", [])
    plans = doc.get("plans", [])
    construals = doc.get("construals", [])
    sessions = doc.get("sessions", [])
    for name, seq in (("findings", findings), ("class_findings", class_findings),
                      ("queue", queue), ("plans", plans), ("construals", construals),
                      ("sessions", sessions)):
        if not isinstance(seq, list):
            raise StateError(f"{where}.{name}: expected a list, got {type(seq).__name__}")

    for i, rec in enumerate(findings):
        _check_record(rec, FINDING_FIELDS, f"{where}.findings[{i}]")
    for i, rec in enumerate(class_findings):
        _check_record(rec, CLASS_FINDING_FIELDS, f"{where}.class_findings[{i}]")
    for i, rec in enumerate(plans):
        _check_record(rec, PLAN_FIELDS, f"{where}.plans[{i}]")
    for i, rec in enumerate(construals):
        _check_record(rec, CONSTRUAL_FIELDS, f"{where}.construals[{i}]")
        adm = rec.get("admitted_by")
        if adm is not None and not (HUMAN_ADMITTER_RE.match(adm)
                                    or ENVELOPE_ADMITTER_RE.match(adm)
                                    or SESSION_ID_RE.match(adm)):
            raise StateError(
                f"{where}.construals[{i}].admitted_by: {adm!r} is not an admitter "
                f"(expected `human:<name>`, `envelope:<name>`, or a 16-hex session id)")
    for i, rec in enumerate(sessions):
        _check_record(rec, SESSION_FIELDS, f"{where}.sessions[{i}]")
    _check_unique(sessions, "session_id", f"{where}.sessions", "session")
    # At most ONE dispatch may be in flight, because the writing lock serializes
    # sessions. Two open envelopes would make `fixed-by` ambiguous again — exactly
    # the unbound provenance F-0159 named — so the state that would allow it is not
    # representable rather than merely unlikely.
    #
    # Phase 11 relaxes this in exactly one direction: several dispatches may be open at
    # once when they all DECLARE the same `concurrent_group`, which is the parallel
    # dispatcher saying "I launched these together and they are all still running".
    # Undeclared concurrency stays unrepresentable, and the ambiguity the rule exists to
    # prevent is caught where it actually bites — `envelope.open_dispatch` refuses to
    # name one dispatch when several are open, so no `fixed-by` can bind to a guess.
    open_sessions = [s for s in sessions if s.get("outcome") is None]
    groups = {s.get("concurrent_group") for s in open_sessions}
    if len(open_sessions) > 1 and (len(groups) > 1 or None in groups):
        raise StateError(
            f"{where}.sessions: {len(open_sessions)} dispatches are still open "
            f"({', '.join(s['session_id'] for s in open_sessions)}) — the writing lock "
            f"allows one at a time unless they declare one `concurrent_group`, so a "
            f"second open envelope means a session ended without being finalised; "
            f"the provenance of any fix recorded now would be ambiguous")

    for i, rec in enumerate(queue):
        _check_record(rec, QUEUE_FIELDS, f"{where}.queue[{i}]")
        cov = rec.get("coverage")
        if cov is not None:
            _check_record(cov, COVERAGE_FIELDS, f"{where}.queue[{i}].coverage")

    cat = doc.get("catalogs") or {}
    if not isinstance(cat, dict):
        raise StateError(f"{where}.catalogs: expected an object, got {type(cat).__name__}")
    unknown = sorted(set(cat) - {"norms", "dimensions", "units"})
    if unknown:
        raise StateError(f"{where}.catalogs: unknown key(s) {', '.join(map(repr, unknown))} "
                         f"(known: dimensions, norms, units)")
    norms = cat.get("norms", [])
    dims = cat.get("dimensions", [])
    units = cat.get("units", [])
    for name, seq, spec in (("norms", norms, NORM_FIELDS),
                            ("dimensions", dims, DIMENSION_FIELDS),
                            ("units", units, UNIT_FIELDS)):
        if not isinstance(seq, list):
            raise StateError(f"{where}.catalogs.{name}: expected a list, "
                             f"got {type(seq).__name__}")
        for i, rec in enumerate(seq):
            _check_record(rec, spec, f"{where}.catalogs.{name}[{i}]")

    limits = doc.get("limits") or {}
    if not isinstance(limits, dict):
        raise StateError(f"{where}.limits: expected an object, got {type(limits).__name__}")
    unknown = sorted(set(limits) - set(LIMIT_FIELDS))
    if unknown:
        raise StateError(
            f"{where}.limits: unknown limit(s) {', '.join(map(repr, unknown))} — a limit no "
            f"consumer reads is a silently-ignored setting (known: "
            f"{', '.join(sorted(LIMIT_FIELDS))})")
    for key, (lo, hi) in LIMIT_FIELDS.items():
        if key in limits:
            v = limits[key]
            _nonneg_int(v, f"{where}.limits.{key}")
            if not lo <= v <= hi:
                raise StateError(f"{where}.limits.{key}: {v} is outside the legal "
                                 f"range {lo}..{hi}")

    # ---- uniqueness: an id on two records is two simultaneous states --------
    fids = _check_unique(findings, "id", f"{where}.findings", "finding id")
    cids = _check_unique(class_findings, "id", f"{where}.class_findings", "class-finding id")
    pids = _check_unique(queue, "id", f"{where}.queue", "pass id")
    norm_ids = _check_unique(norms, "id", f"{where}.catalogs.norms", "norm id")
    dim_keys = _check_unique(dims, "key", f"{where}.catalogs.dimensions", "dimension key")
    unit_ids = _check_unique(units, "id", f"{where}.catalogs.units", "unit id")
    _check_unique(plans, "id", f"{where}.plans", "plan id")
    _check_unique(construals, "key", f"{where}.construals", "construal key")

    known_ids = set(fids) | set(cids)

    # ---- cross-record: a reference that resolves to nothing is not state ----
    # F-0125: one finding belongs to exactly ONE class (§8 rule 4). A member claimed
    # by two CFs inflates the `total members` KPI and breaks the one-finding/one-class
    # invariant; it used to be a lint check that the decision path folded in by hand.
    claimed = {}
    for i, rec in enumerate(class_findings):
        for m in rec["members"]:
            if m in claimed:
                raise StateError(
                    f"{where}.class_findings[{i}].members: {m} is also claimed by "
                    f"{claimed[m]} — a finding has exactly one class (§8 rule 4)")
            claimed[m] = rec["id"]

    for i, rec in enumerate(class_findings):
        for m in rec["members"]:
            if m not in known_ids:
                raise StateError(
                    f"{where}.class_findings[{i}].members: {m} does not exist — a class "
                    f"whose member is absent cannot be closed, and certifying `done` over "
                    f"it hides unfinished global remediation")
            if m == rec["id"]:
                raise StateError(f"{where}.class_findings[{i}].members: {m} lists itself")

    for i, rec in enumerate(findings + class_findings):
        seq = "findings" if i < len(findings) else "class_findings"
        j = i if i < len(findings) else i - len(findings)
        at = f"{where}.{seq}[{j}]"
        if rec["pass"] not in pids:
            raise StateError(f"{at}.pass: {rec['pass']} is not a pass in the queue — a "
                             f"finding produced by no pass has no coverage record behind it")
        if dim_keys and rec["dimension"] not in dim_keys:
            raise StateError(f"{at}.dimension: {rec['dimension']!r} is not in the dimension "
                             f"catalog (known: {', '.join(sorted(dim_keys))})")
        if unit_ids:
            for u in rec["unit"]:
                if u not in unit_ids:
                    raise StateError(f"{at}.unit: {u!r} is not in the unit map "
                                     f"(known: {', '.join(sorted(unit_ids))})")
        ro = rec.get("recurrence_of")
        if ro is not None:
            if ro not in known_ids:
                raise StateError(f"{at}.recurrence_of: {ro} does not exist in this ledger")
            if ro == rec["id"]:
                raise StateError(f"{at}.recurrence_of: {ro} is the record itself")
        cls = rec.get("class")
        if cls is not None and cls not in cids:
            raise StateError(f"{at}.class: {cls} is not a class finding in this state")
        nxt = rec.get("next")
        owners = NEXT_OWNER.get(rec["status"], set())
        if nxt is not None and nxt != "⚠ needs-human" and nxt not in owners:
            raise StateError(
                f"{at}.next: {nxt!r} does not own status {rec['status']!r} "
                f"(expected {' or '.join(sorted(owners))}) — `next` names who must act, "
                f"so a wrong owner routes the work to nobody")

    for i, rec in enumerate(plans):
        for f in rec["findings"]:
            if f not in known_ids:
                raise StateError(f"{where}.plans[{i}].findings: {f} does not exist")

    for i, q in enumerate(queue):
        if dim_keys and q["dimension"] not in dim_keys:
            raise StateError(f"{where}.queue[{i}].dimension: {q['dimension']!r} is not in "
                             f"the dimension catalog")
        if unit_ids:
            for u in q["units"]:
                if u not in unit_ids:
                    raise StateError(f"{where}.queue[{i}].units: {u!r} is not in the unit map")
        cov = q.get("coverage")
        # F-0011/F-0154: a checked pass is documented by exactly one canonical
        # coverage record. In the schema a `done` pass simply cannot exist without
        # its coverage object — there is no partial-file glue to assemble.
        if q["done"] and cov is None:
            raise StateError(
                f"{where}.queue[{i}]: pass {q['id']} is done but carries no coverage record "
                f"— a pass with no report counts as not done at all (§4 rule 5)")
        if cov is not None:
            for f in cov["findings"]:
                if f not in known_ids:
                    raise StateError(f"{where}.queue[{i}].coverage.findings: {f} does not "
                                     f"exist in this state")

    for i, rec in enumerate(construals):
        # §4 rule 9: a session may not admit its own construal. In the Markdown
        # surface this was a check over two parsed fields; here it is the same rule
        # applied at the one boundary every consumer passes through.
        # §4 rule 9: the key IS the charter's fingerprint. A record whose key does not
        # derive from its own (role, charter) construes something other than what it
        # claims to; Ouroboros-3 round 1 produced exactly that when a trailing charter
        # swallowed the surrounding prose. Deriving and comparing here means the binding
        # holds for every consumer, not only for the one that thought to check it.
        expected = construal_key(rec["role"], rec["charter"])
        if rec["key"] != expected:
            raise StateError(
                f"{where}.construals[{i}].key: {rec['key']} is not the key of this "
                f"record's own (role, charter) — that pair derives {expected}. The key "
                f"is the charter's fingerprint (§4 rule 9), so a mismatch means the "
                f"record construes a different charter than it names")
        if rec.get("admitted_by") and rec["admitted_by"] == rec["session"]:
            raise StateError(
                f"{where}.construals[{i}].admitted_by: a session may not admit its own "
                f"construal ({rec['session']}) — admission is what makes the reading "
                f"independent (§4 rule 9)")

    for i, d in enumerate(dims):
        for n in d["norms"]:
            if n not in norm_ids:
                raise StateError(
                    f"{where}.catalogs.dimensions[{i}].norms: {n} is not in the norms "
                    f"catalog — a dimension whose norm nobody can quote is an opinion, "
                    f"not an auditable angle (§6 rule 2)")


# ---------------------------------------------------------------------------
# construction — runs only after _validate returned
# ---------------------------------------------------------------------------

def freeze(obj):
    """Deep-freeze a parsed JSON value: dict -> mappingproxy, list -> tuple.

    Why a proxy and not a typed record for `limits` and `sessions`
    -------------------------------------------------------------
    `@dataclass(frozen=True)` freezes the FIELD BINDINGS, not the objects the
    fields point at. `State` had two fields holding ordinary containers, so
    `state.limits["reopen_limit"] = 999` and `state.sessions[0]["role"] = ...`
    both succeeded on a State the type advertised as immutable.

    A typed record was the alternative. It is the wrong trade here for one
    concrete reason: `sessions[]` records flow through `envelope.open_dispatch`,
    `independence_level` and `_event_payload` alongside records the runner has
    just BUILT as plain dicts, and those readers use the mapping API
    (`s.get(...)`, `dict(s)`, `**rec`). Introducing a record type would put two
    shapes of the same thing into the provenance path — the exact place F-0159
    was found — to gain field-name typo detection that the schema already gives
    by rejecting unknown keys. The proxy freezes without changing the shape.

    RECURSIVE on purpose. A shallow `MappingProxyType(d)` would be decoration
    for `sessions[].sandbox_details`, which the schema declares as an object:
    the proxy would refuse `s["role"] = x` while `s["sandbox_details"]["net"] = x`
    still went through. Tuples of dicts are the shape of the original bug; this
    function must not create a second one.

    The backing containers are built here and dropped here, so nothing reachable
    from a `State` holds a writable reference to them (`mappingproxy` exposes no
    way back to its dict). `tests/test_state_immutability.py` walks the whole
    object graph and asserts that.
    """
    if isinstance(obj, dict):
        return MappingProxyType({k: freeze(v) for k, v in obj.items()})
    if isinstance(obj, (list, tuple)):
        return tuple(freeze(v) for v in obj)
    return obj


def thaw(obj):
    """The inverse, for the serialisation boundary — `json.dumps` cannot encode a
    `mappingproxy`, and writing state is the one place a plain container is wanted."""
    if isinstance(obj, (dict, MappingProxyType)):
        return {k: thaw(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [thaw(v) for v in obj]
    return obj


def _coverage(d):
    return None if d is None else Coverage(
        report_path=d["report_path"], findings=tuple(d["findings"]),
        updated=d["updated"], status=d["status"])


def _build(doc):
    findings = tuple(Finding(
        id=r["id"], title=r["title"], severity=r["severity"], status=r["status"],
        next=r.get("next"), dimension=r["dimension"], unit=tuple(r["unit"]),
        pass_id=r["pass"], attempts=r["attempts"], updated=r["updated"],
        body_path=r["body_path"], fixed_by=r.get("fixed_by"),
        recurrence_of=r.get("recurrence_of"), refusal=r.get("refusal"),
        admitted_scope=r.get("admitted_scope"), blocked=r.get("blocked"),
        norm_ruling=r.get("norm_ruling"), cls=r.get("class"),
        independence=r.get("independence"),
        locator=r.get("locator"), source_hash=r.get("source_hash"),
    ) for r in doc.get("findings", []))

    class_findings = tuple(ClassFinding(
        id=r["id"], title=r["title"], severity=r["severity"], status=r["status"],
        next=r.get("next"), dimension=r["dimension"], unit=tuple(r["unit"]),
        pass_id=r["pass"], attempts=r["attempts"], updated=r["updated"],
        body_path=r["body_path"], members=tuple(r["members"]),
        created_by=r.get("created_by"), fixed_by=r.get("fixed_by"),
        refusal=r.get("refusal"), admitted_scope=r.get("admitted_scope"),
        blocked=r.get("blocked"), norm_ruling=r.get("norm_ruling"),
        independence=r.get("independence"),
    ) for r in doc.get("class_findings", []))

    queue = tuple(QueueEntry(
        id=q["id"], dimension=q["dimension"], units=tuple(q["units"]),
        charter=q["charter"], stop=q["stop"], done=q["done"],
        coverage=_coverage(q.get("coverage")), base=q.get("base"),
    ) for q in doc.get("queue", []))

    plans = tuple(Plan(
        id=p["id"], findings=tuple(p["findings"]), status=p["status"],
        updated=p["updated"], body_path=p["body_path"], attempts=p.get("attempts", 0),
    ) for p in doc.get("plans", []))

    construals = tuple(Construal(
        key=c["key"], role=c["role"], charter=c["charter"], session=c["session"],
        status=c["status"], created=c["created"], body_path=c["body_path"],
        admitted_by=c.get("admitted_by"), admitted_at=c.get("admitted_at"),
        envelope=c.get("envelope"),
    ) for c in doc.get("construals", []))

    cat = doc.get("catalogs") or {}
    catalogs = Catalogs(
        norms=tuple(Norm(**n) for n in cat.get("norms", [])),
        dimensions=tuple(Dimension(key=d["key"], catches=d["catches"],
                                   norms=tuple(d["norms"])) for d in cat.get("dimensions", [])),
        units=tuple(Unit(**u) for u in cat.get("units", [])),
    )

    return State(
        schema_version=doc["schema_version"], state_revision=doc["state_revision"],
        generated_by=doc["generated_by"], head_before=doc.get("head_before"),
        ledger_anchor=freeze(doc["ledger_anchor"]) if doc.get("ledger_anchor") else None,
        ledger_commits=tuple(doc.get("ledger_commits") or ()),
        findings=findings, class_findings=class_findings, queue=queue, plans=plans,
        construals=construals, sessions=freeze(doc.get("sessions", [])),
        limits=freeze(doc.get("limits") or {}), catalogs=catalogs,
    )


# ---------------------------------------------------------------------------
# the public boundary
# ---------------------------------------------------------------------------

def load_state(audit_dir):
    """`bytes -> parse -> schema -> cross-record -> immutable State`, or raise.

    The ONLY reader of machine state in the package. There is deliberately no
    `try_load_state`, no `partial=True`, and no way to get a State out of a
    document that failed validation: "lint knows but next does not" is the
    fail-open the whole model exists to remove.
    """
    path = Path(audit_dir) / STATE_FILENAME
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise StateError(f"{path}: no state file — run `xcheck migrate` to convert an "
                         f"existing Markdown audit, or `install.sh` to start a new one")
    except OSError as e:
        raise StateError(f"{path}: cannot read ({e.strerror})")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise StateError(f"{path}: not valid UTF-8 — durable state is read as UTF-8")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise StateError(f"{path}: not valid JSON at line {e.lineno} column {e.colno}: {e.msg}")

    _validate(doc, str(path))
    return _build(doc)


def to_document(state):
    """A `State` back to the plain dict that `write_state` serialises."""
    def rec(d):
        return {k: v for k, v in d.items() if v is not None}

    doc = {
        "schema_version": state.schema_version,
        "state_revision": state.state_revision,
        "generated_by": state.generated_by,
        "head_before": state.head_before,
        # PHASE 8: omitted entirely when there is none, so every state document written
        # before this phase round-trips byte-identically and nothing already admitted has
        # to grow a field it never had.
        **({"ledger_anchor": dict(state.ledger_anchor)} if state.ledger_anchor else {}),
        # Same rule as the anchor above: omitted entirely when empty, so every document
        # written before this phase round-trips byte-identically.
        **({"ledger_commits": list(state.ledger_commits)} if state.ledger_commits else {}),
        "findings": [rec({
            "id": f.id, "title": f.title, "severity": f.severity, "status": f.status,
            "next": f.next, "dimension": f.dimension, "unit": list(f.unit),
            "pass": f.pass_id, "attempts": f.attempts, "updated": f.updated,
            "body_path": f.body_path, "fixed_by": f.fixed_by,
            "recurrence_of": f.recurrence_of, "refusal": f.refusal,
            "admitted_scope": f.admitted_scope, "blocked": f.blocked,
            "norm_ruling": f.norm_ruling, "class": f.cls,
            "independence": f.independence,
            "locator": f.locator, "source_hash": f.source_hash,
        }) for f in state.findings],
        "class_findings": [rec({
            "id": c.id, "title": c.title, "severity": c.severity, "status": c.status,
            "next": c.next, "dimension": c.dimension, "unit": list(c.unit),
            "pass": c.pass_id, "attempts": c.attempts, "updated": c.updated,
            "body_path": c.body_path, "members": list(c.members),
            "created_by": c.created_by, "fixed_by": c.fixed_by, "refusal": c.refusal,
            "admitted_scope": c.admitted_scope, "blocked": c.blocked,
            "norm_ruling": c.norm_ruling, "independence": c.independence,
        }) for c in state.class_findings],
        "queue": [rec({
            "id": q.id, "dimension": q.dimension, "units": list(q.units),
            "charter": q.charter, "stop": q.stop, "done": q.done,
            "coverage": None if q.coverage is None else {
                "report_path": q.coverage.report_path,
                "findings": list(q.coverage.findings),
                "updated": q.coverage.updated, "status": q.coverage.status},
            "base": q.base,
        }) for q in state.queue],
        "plans": [rec({
            "id": p.id, "findings": list(p.findings), "status": p.status,
            "updated": p.updated, "body_path": p.body_path, "attempts": p.attempts,
        }) for p in state.plans],
        "construals": [rec({
            "key": c.key, "role": c.role, "charter": c.charter, "session": c.session,
            "status": c.status, "created": c.created, "body_path": c.body_path,
            "admitted_by": c.admitted_by, "admitted_at": c.admitted_at,
            "envelope": c.envelope,
        }) for c in state.construals],
        # `thaw`, not `list`/`dict`: both fields are deep-frozen (see `freeze`), and a
        # `mappingproxy` reaching `json.dumps` is a TypeError at the write boundary.
        "sessions": thaw(state.sessions),
        "limits": thaw(state.limits),
        "catalogs": {
            "norms": [{"id": n.id, "source": n.source, "scope": n.scope}
                      for n in (state.catalogs.norms if state.catalogs else ())],
            "dimensions": [{"key": d.key, "catches": d.catches, "norms": list(d.norms)}
                           for d in (state.catalogs.dimensions if state.catalogs else ())],
            "units": [{"id": u.id, "material": u.material, "size": u.size,
                       "responsibility": u.responsibility}
                      for u in (state.catalogs.units if state.catalogs else ())],
        },
    }
    return doc


def serialise(doc):
    """The ONE canonical byte form. Sorted keys and a fixed indent make the file
    diffable in git and make round-trip byte-identity a testable property; a
    second formatting would make every rewrite look like a change."""
    return json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def git_head(project):
    p = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(project),
                       capture_output=True, text=True, timeout=GIT_TIMEOUT)
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None


# PHASE 9. The provenance helpers live HERE, beside `git_head`, and deliberately not in
# `envelope.py`: F-0098 was closed by removing every execution site from that module and a
# test walks its AST to keep it that way. `envelope.dispatch_record` therefore takes these
# values as ARGUMENTS — it records provenance, it does not go and get it.
SUBJECT_COMMIT_ENV = "XCHECK_SUBJECT_COMMIT"


def subject_commit(project, env=None):
    """The commit of the SUBJECT — the repository being audited — as a reader could
    check it out.

    Inside a disposable worktree `git rev-parse HEAD` answers the sandbox's own seed
    commit, which exists in no branch, no tag and no other clone. Every write a session
    made recorded that value as `head_before`, and this repository's own state document
    still carries one: `130646f4…`, unreachable, one `git gc` from gone.

    So the orchestrator EXPORTS the outer commit into the child's environment and this
    reads it there first. The ambient answer is the fallback and is correct for the case
    it covers — an operator running a verb at their own terminal, where the working
    directory IS the subject."""
    named = (env if env is not None else os.environ).get(SUBJECT_COMMIT_ENV)
    if named and HEAD_RE.match(named.strip()):
        return named.strip()
    return git_head(project)


def subject_manifest(project):
    """A content digest over the audited tree, or None where git cannot answer.

    Not an object name. `git gc` can take every commit in this document — a rewritten
    branch, a deleted fork, a squashed history — and this still answers "was the audited
    content the same", because it is computed from content ids rather than from a
    pointer to a commit that has to survive.

    Two parts, and both are needed: the INDEX (`ls-files -s` — mode, blob id, path for
    every tracked file) plus the uncommitted DELTA (`diff HEAD`). The index alone would
    call a dirty tree identical to a clean one, and a session audits the tree as it
    stands, pending edits included."""
    listing = subprocess.run(["git", "ls-files", "-s"], cwd=str(project),
                             capture_output=True, text=True, timeout=GIT_TIMEOUT)
    if listing.returncode != 0:
        return None
    delta = subprocess.run(["git", "diff", "HEAD"], cwd=str(project),
                           capture_output=True, text=True, timeout=GIT_TIMEOUT)
    h = hashlib.sha256()
    h.update(b"index\0")
    h.update(listing.stdout.encode("utf-8", "replace"))
    h.update(b"\0delta\0")
    h.update((delta.stdout if delta.returncode == 0 else "").encode("utf-8", "replace"))
    return h.hexdigest()


def controller_commit():
    """The commit of xcheck ITSELF, or None when it was not installed from a checkout.

    The subject and the tool are two different programs, and either can explain a
    result: "the audit found nothing" is a different fact depending on which version of
    the auditor ran. None (recorded as `unknown`) is the honest answer for a wheel
    install — there is no commit, and inventing one would be worse than saying so."""
    return git_head(Path(__file__).resolve().parent.parent)


def commit_reachable(project, sha):
    """Is `sha` reachable from any ref — a branch, a tag, a remote?

    The question `head_before` could never pass. A commit no ref reaches is deleted by
    the next `git gc`, and a provenance field pointing at it is a dead pointer that
    still LOOKS like evidence."""
    if not sha or sha == "unknown":
        return False
    p = subprocess.run(["git", "for-each-ref", f"--contains={sha}", "--count=1",
                        "--format=%(refname)"],
                       cwd=str(project), capture_output=True, text=True,
                       timeout=GIT_TIMEOUT)
    return p.returncode == 0 and bool(p.stdout.strip())


def next_revision(state):
    """The revision `write_state` will assign to this state.

    One home, and it exists for one caller: PHASE 7 (fourth audit) reserves a
    `state_transition` event BEFORE the state write, and that event names the revision it
    transitions to. Computing the number in two places would let a reserved event name a
    revision the write did not produce — and the whole point of reserving is that the
    event and the write are one unit. The writing lock is held across both, so nothing
    can move the revision in between.
    """
    return state.state_revision + 1


def write_state(audit_dir, state, bump=True, head_before=None, txn=None):
    """Durably replace `audit/state.json`. Returns the new `state_revision`.

    PHASE 3 (sixth audit). `txn` is the identity of the transaction this write commits.
    It is appended to `ledger_commits` inside the SAME `os.replace` as the change, which
    is what makes "did this specific transaction land?" answerable after a crash. Passing
    it is how a writer participates in the confirmation protocol, and this is the only
    function that records one — a caller cannot commit a transaction some other way.

    temp file in the SAME directory -> flush -> fsync -> os.replace -> fsync the
    directory. Same directory because `os.replace` is only atomic within one
    filesystem; the directory fsync because the rename itself is metadata, and
    without it a crash can leave the old inode visible after the new bytes were
    already durable. A reader therefore sees either the whole previous state or
    the whole new one — never a half-written document, and never a temp file
    left behind as a decoy that a later glob might pick up.
    """
    audit_dir = Path(audit_dir)
    final = audit_dir / STATE_FILENAME
    doc = to_document(state)
    if bump:
        doc["state_revision"] = next_revision(state)
    if txn is not None:
        # Newest last, oldest dropped. A row whose transaction has fallen off the end is
        # UNPROVABLE rather than disproved — see `ledger.uncommitted`, which is the one
        # place that reads this list and the one place that decides what it means.
        doc["ledger_commits"] = [t for t in doc.get("ledger_commits", ())
                                 if t != txn][-(COMMIT_WINDOW - 1):] + [txn]
    if head_before is not None:
        doc["head_before"] = head_before
    # Validate what is about to become durable: a writer that can persist a
    # document its own reader would refuse has broken the one-boundary promise.
    _validate(doc, str(final))

    payload = serialise(doc).encode("utf-8")
    tmp = audit_dir / f".{STATE_FILENAME}.tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(payload)
            f.flush()               # user-space buffer -> kernel
            os.fsync(f.fileno())    # kernel page cache -> disk
        os.replace(str(tmp), str(final))
    except BaseException:
        # The temp file must not survive: a stale `.state.json.tmp` is a decoy
        # that looks like durable state to anything scanning the directory.
        try:
            os.unlink(str(tmp))
        except OSError:
            pass
        raise
    dfd = os.open(str(audit_dir), os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    return doc["state_revision"]


def empty_state(generated_by, head_before=None):
    """A valid, empty State — what `install.sh` and the migration start from."""
    return State(schema_version=SCHEMA_VERSION, state_revision=0,
                 generated_by=generated_by, head_before=head_before,
                 catalogs=Catalogs())
