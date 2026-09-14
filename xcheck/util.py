"""Leaf vocabulary shared by every other module: the §2/§5 enumerations, the
canonical-field normalizers, the finding-status semantics, and orchestrator.conf.

Nothing here imports another xcheck module — that is what makes it the bottom of
the layering. When a helper here starts wanting a parser or a decision, the
helper is in the wrong module, not the layering.
"""


import collections.abc
import hashlib
import re
import threading
from datetime import datetime
from pathlib import Path

from xcheck.policy import PolicyError
from xcheck.policy import load as policy_load
from xcheck.policy import policy_keys_in, refuse_policy_in_subject, refuse_withdrawn
from xcheck.policy import resolve as policy_resolve

# ---------------------------------------------------------------- defaults

DEFAULT_CONF = """\
# xcheck PROJECT config (KEY=VALUE, # comments).
#
# What THIS audit is about: how much a pass may file, when to stop for triage, a note
# appended to every role prompt, and the optional discipline gates. A wrong value here
# makes an audit worse, not dangerous, and the project knows these better than the
# operator does.
#
# What is deliberately NOT here: the command each role runs, the containment it runs
# inside, the environment and network it reaches, its resource limits, retry,
# concurrency, and whether commits are pushed. Those decide what executes on the
# operator's machine, and a session can edit any file under audit/ — the courier would
# ship the edit and the next dispatch would execute it. They live in the operator
# profile — $XCHECK_OPERATOR_PROFILE, or $XDG_CONFIG_HOME/xcheck/operator.conf — which
# lives outside the repository being audited. Writing one of them here is REFUSED at load, by name.
#
# session_note: appended to every role prompt (project-specific cautions).
session_note=
batch_size=8
# loop_progress_limit (F-0118): N consecutive sessions that leave audit/ unchanged
# AND repeat the same decision stop the loop as a non-converging cycle (§9 rule 2),
# not a cost cap. Default 2 (like reopen_limit): one retry is legal, a second is
# immobility. 0 disables the no-progress detector.
loop_progress_limit=2
# triage_batch_cap: N = stop for triage once N findings pile up in 'reported'.
# 0 = unbounded, and unbounded is what produced this project's own 133 reported
# findings with 0 triaged: nothing ever asked for the sitting, so the debt only grew
# and every later audit ran against a corpus nobody had judged. 20 is a sitting a
# human finishes in one go (2.5x the batch_size of 8 a Remediator takes), and it is
# the bound the 133-finding backlog would have hit on its 20th finding instead of
# its 133rd.
triage_batch_cap=20
# audit_over_critical: off = an OPEN critical finding stops new Auditor passes at a
# human gate (backpressure: do not manufacture more debt on top of the worst one).
# on = audit anyway. Triage, remediation and verification are never gated — they are
# how the critical stops being open.
audit_over_critical=off
# scope_typing (XCHECK.md §7/§10, candidate): off = default, behaviour unchanged. When on,
# a finding set `fixed` must declare the scope its fix admits — `admitted-scope` plus an
# `## Admitted scope` section carrying BOTH `### Covers` and `### Does not cover`. The
# residue half is the load-bearing one: a coverage claim with no stated limit reads as
# complete while admitting nothing. Unrecognised values are REFUSED, not read as off.
scope_typing=off
# construal_gate (XCHECK.md §4 rule 9/§10, candidate): off = default, behaviour unchanged.
# When on, a chartered session must have an ADMITTED construal on file
# (audit/construals/<key>.md) before it is dispatched: the orchestrator first runs the role
# to WRITE its construal and stops, and a human admits it — admission is always a separate act
# by a party that is not the producer (§4 rule 9). Unrecognised values are REFUSED, not read as off.
construal_gate=off
# embargo (candidate, default off): after `embargo_after` consecutive sessions on one
# charter that declared no transition, refuse to dispatch it again until the charter,
# the role or the orchestrator's own source changes. Measured over the frozen
# Ouroboros-4 corpus it would have refused 109 of 136 barren sessions and killed 8 of 15
# productive ones — which FALSIFIED the pre-registered threshold, and is why it is off.
embargo=off
embargo_after=3
# evidence_bundle (phase 18 of the third-audit response, default off): on = the
# `evidence-bundle` verb exports a self-contained, checksummed result — subject manifest,
# policy digest, events, receipts, findings, patches, verdicts, provenance, the population
# it covers and the omissions it makes — plus a standalone checker that needs neither
# xcheck nor a network. Off by default because exporting evidence is a decision about who
# gets to see it, and nothing in a run ever reads a bundle back.
evidence_bundle=off
# model_routing (phase 14 of the third-audit response, default off): on = each dispatch
# resolves a ROUTE from a closed table keyed on (work kind, risk) — deterministic code, a
# cheap model, or a strong one — and records it on the session so a later reader can ask
# which model produced which evidence. No model chooses the route; it is a dict lookup.
# Unclassified work fails closed to the strong route, and a HIGH-risk pass can never be
# routed cheap, not even by an operator entry: savings are never bought out of a security
# verdict. Independence is NOT decided here and a route can neither grant nor waive it.
model_routing=off
# budgets (phase 15 of the third-audit response, default off): on = six ceilings are
# evaluated BEFORE each dispatch, not once at the start of a run — tokens per session,
# per pass and per audit; the SHARE of measured tokens spent by sessions that recorded no
# canonical transition; how many times one charter may be re-dispatched; and a
# marginal-value window that STOPS generation when the last N finished sessions produced
# no new accepted evidence. Every ceiling is 0 = off, priced on measured sessions only,
# and every refusal prints the coverage of its figure and the key to raise. Ouroboros-4
# spent 85.4% of its tokens on sessions that moved nothing, with nothing able to stop it.
budgets=off
# Each ceiling: 0 means OFF, never `refuse everything`. `xcheck status` prints the
# derived values an operator may adopt, each with the arithmetic it came from.
tokens_per_session=0
tokens_per_pass=0
tokens_per_audit=0
no_progress_share_pct=0
charter_repeat_limit=0
marginal_value_window=0
# okf (phase 16 of the third-audit response, default off): on = `xcheck okf` may generate
# the derived knowledge bundle under `audit/okf/` — six record types and three relations
# projected from state.json plus the event stream, as versioned JSONL with a checksummed
# manifest. It is DERIVED and disposable: if it ever disagrees with state.json it is wrong
# and is regenerated, never merged. Its back channel is PROPOSAL-ONLY — it may not open or
# close a finding, move a lifecycle status, weaken policy, replace evidence or raise its
# own confidence, and a proposal reaches canonical state only when a human reads it and
# runs an existing write verb. Generating it is an explicit verb, never a step in a run.
okf=off
"""

# The TWO ways an xcheck session can start. Defined once, here, because a document
# that invents a third name is describing a guarantee nobody implements — and because
# the difference between these two is the difference between "the runner enforces it"
# and "your agent platform enforces something xcheck cannot see".
#
# `xcheck next` / `xcheck loop` are `orchestrated`. A human typing `/xcheck-audit` in
# their agent is `uncontained-direct`: the same methodology, the same role card, and
# none of the runner's controls, because the runner is not in the picture at all.
ORCHESTRATED = "orchestrated"
UNCONTAINED = "uncontained-direct"

LAUNCH_MODES = {
    ORCHESTRATED:
        "the session is launched by `xcheck next`/`xcheck loop` through `runner.py`: "
        "it gets a sandbox profile, a hard timeout, a built environment, log "
        "redaction, a process-group kill, the courier's review of its diff, an "
        "invocation envelope recording what was in force and a session receipt "
        "attesting what it did",
    UNCONTAINED:
        "a human starts the agent themselves (a launcher skill, `/xcheck-audit` and "
        "friends). xcheck is not in the process: containment is whatever the agent "
        "platform provides, which this tool does not know and cannot report",
}

# The eight controls an `orchestrated` session gets and an `uncontained-direct` one does
# not. Named once so a skill's disclosure and the `status` line cannot list seven.
# PHASE 15: the first six are containment — they answer "what could this session reach".
# The last two are accountability — they answer "what says this session ran, under which
# policy, and did what it claims". The audit found the launchers disclosing only the
# first kind, which reads as "unsandboxed but recorded"; nothing recorded them at all.
ORCHESTRATED_GUARANTEES = (
    "sandbox profile",
    "hard timeout",
    "environment allowlist",
    "log redaction",
    "process-group kill",
    "courier review of the diff",
    "invocation envelope",
    "session receipt",
)

# Every `subprocess` call in xcheck passes an explicit timeout (phase 8): a blocking
# call with no bound is how one hung child holds the writing lock forever. Git commands
# are local and fast, so one shared bound covers them all.
GIT_TIMEOUT = 120

# XCHECK.md §5, as data. `set-status` is checked against this table; a transition
# that is not a key here does not exist, which is the point — the vocabulary of
# legal moves is closed, and it is closed in ONE place that the norm can be read
# against.
TRANSITIONS = {
    "reported": {"accepted", "rejected", "deferred"},
    "deferred": {"accepted", "rejected"},
    "accepted": {"validated", "disputed", "obsolete", "superseded-by-class"},
    "validated": {"planned", "superseded-by-class"},
    "planned": {"fixed", "superseded-by-class"},
    "fixed": {"closed", "reopened"},
    "reopened": {"planned", "superseded-by-class"},
    "disputed": {"accepted", "withdrawn"},
    "superseded-by-class": {"accepted"},
}

RESERVED = {
    ("planned", "fixed"): "record-fix",
    ("fixed", "closed"): "record-verdict",
    ("fixed", "reopened"): "record-verdict",
}

TRIAGE_STATUSES = {"accepted", "rejected", "deferred"}
TERMINAL = {"closed", "rejected", "withdrawn", "obsolete", "superseded-by-class"}
# F-0043: `superseded-by-class` is only conditionally terminal — terminal when its
# CF is closed, non-terminal (a live member of an open CF) otherwise. The four
# below are unconditionally terminal; a recurrence ancestor is judged against
# these plus the closed-CF condition, not the flat TERMINAL set (§3 dedup).
HARD_TERMINAL = TERMINAL - {"superseded-by-class"}

# CF-0001: the canonical-record enumerations, defined ONCE here so every consumer
# (cmd_lint, the decision-path integrity gate, cmd_metrics) validates against the
# SAME set — the root cause was each consumer reading these fields past a predicate
# that only cmd_lint held, a recurrence of F-0055. SEVERITIES is XCHECK.md §2's
# closed severity vocabulary; STATUSES is §5's closed lifecycle vocabulary, built
# from TERMINAL so the terminal set is never restated. Together with NEXT_OWNER
# (the §5 status→owner map, likewise a single module constant) these are the whole
# of the canonical enumerations; a selftest pins STATUSES against NEXT_OWNER so the
# two can never drift apart.
SEVERITIES = {"critical", "major", "minor", "info"}
STATUSES = TERMINAL | {
    "reported", "accepted", "deferred",
    "validated", "planned", "fixed", "reopened", "disputed",
}
# XCHECK.md §5 `refusal`: the closed reason vocabulary for a role that ACCEPTED a
# charter and cannot execute it. Declared ONCE here; the shared value predicate
# (`_canon_refusal`), the decision path and the gate message all read this set, and
# `_schema_enum_binding_issues` binds it back to the §2-shown enumeration so the norm
# and the code cannot drift into two vocabularies (the CF-0001 single-source rule
# applied to an enumeration rather than to a field name).
REFUSAL_REASONS = {
    "out-of-competence", "blocked-dependency", "charter-ambiguous",
    "norm-conflict", "material-missing", "cost-exceeded",
}

# How a dispatched session ENDED, as the orchestrator classified it. Declared here
# rather than in `runner` because the phase-9 envelope stores it in `state.json`, and
# `state` sits below `runner` in the import order — a vocabulary shared by a producer
# and a validator belongs beneath both, or the two grow separate copies.
# `cancelled` is not a failure mode: it is the operator's own act, and calling it a
# timeout would file a false report about the agent.
# `stalled` (phase 12) is NOT `timeout`. A timeout is a session that worked and ran out of
# wall clock; a stall is a session that never started working — no first byte, no useful
# output, or a silence longer than any working session's whole life. They call for
# opposite operator actions (raise the budget vs. look at why nothing happened), so they
# are different values rather than one value with a note.
OUTCOMES = ("ok", "refused", "blocked", "provider-error", "timeout", "crash", "cancelled",
            "stalled")
# Where a session's cost figures came from, and how much the tool had to trust the
# subject to believe them. Declared HERE and not in `envelope` because `state` validates
# the field and cannot import `envelope` (it is the other way round).
#
#   provider        a sidecar file written OUTSIDE the child, at a path the child was
#                   never handed, validated and bound to this session
#   agent-reported  an `XCHECK_TELEMETRY {...}` line in the log — written by the child
#                   itself, so it is a CLAIM by a potentially adversarial agent
#   log-parse       the `tokens used` phrase scraped out of the log text, which is what
#                   this tool did for everything until phase 11
#
# PHASE 6 (third audit, P0) split the first two apart. They were one value, `provider`,
# and the audit forged it: a session that printed
# `XCHECK_TELEMETRY {"total_tokens": 1, "model": "forged-model"}` was recorded as
# provider-native telemetry with a token count of 1. `provider` is the one label that
# means the figure did not come from the thing being audited, so a self-report must not
# be able to wear it. Historical records keep the value they were written with — an
# envelope already admitted is evidence, not a row to relabel.
TELEMETRY_SOURCES = ("provider", "log-parse", "agent-reported")

# How independent a verdict actually was, MEASURED from the two invocation envelopes
# rather than asserted by a prompt. Declared here for the same reason as OUTCOMES: the
# level is stored on the finding, so `state` validates it and `envelope` computes it.
#
# The honest ceiling, which belongs next to the vocabulary and not only in the docs:
# none of these levels proves the absence of shared training data, a shared cache, or
# the same human driving both sides. `cross-provider` means two different vendors ran
# the two sessions. That is all it means.
INDEPENDENCE_LEVELS = (
    "cross-provider",                    # different providers entirely
    "cross-model",                       # same provider, different model
    "same-provider-different-session",   # today's usual case, and honestly the weakest
    "same",                              # the same session — refused
    "unrecorded",                        # no envelope on one side: not measurable
)
# Levels weaker than a genuine cross-vendor check are LABELLED wherever they are shown.
# The external audit's point 8: a fallback is acceptable only when it is displayed as
# a weakened mode.
DEGRADED_INDEPENDENCE = frozenset({"same-provider-different-session", "unrecorded"})

# F-0042: the §8 norm-ratification gate is a fail-closed MACHINE gate on the CF's
# `norm-ruling` frontmatter field (human ruling option a). A ruling is recognized
# only as a norm-id token — one or more norm ids (letters+digits, e.g. N1, ADR3)
# chained by `-over-`, e.g. `N1` or `N1-over-N4`. Everything else (absent,
# `pending`, empty, or any unrecognized token) leaves the gate closed.
NORM_RULING_TOKEN_RE = re.compile(r"^[A-Za-z]+[0-9]+(?:-over-[A-Za-z]+[0-9]+)*$")


# CF-0001 (re-take, human ruling 2026-07-27): the canonical record (§2) is declared
# ONCE, as DATA — a schema table field->(in_§2_block, presence-required, value
# predicate). EVERY consumer (cmd_lint, the decision-path integrity gate, cmd_metrics)
# validates by iterating THIS table; none reads a canonical field past it. That is the
# whole class root — a predicate that lived only in one consumer while another read the
# field raw (recurrence of F-0055) — and it recurred field by field (attempts, filename
# id, missing id) because coverage was a HAND LIST, not derived from §2. The schema
# below is the single source; `canonical_schema_meta_issues` binds it back to the §2
# prose so a field cannot be added to §2 without a validator, nor a validator kept for a
# field §2 dropped (the technique that finally closed F-0077). A field's value predicate
# returns None when the value is acceptable, else a human-readable reason.
#
# NULLABILITY IS SCHEMA DATA (CF-0001 re-take round 3, I-11): the 2nd element says whether
# an empty/absent value is acceptable, and the shared dispatcher (`canonical_record_issues`
# via `_field_gate`) runs a gate over EVERY field value INCLUDING the empty string — no
# `if the value is non-empty` branch is left in the caller to silently skip a missing
# value's predicate (the I-11 hole: an empty `attempts:` slipped past its predicate, was
# published as a KPI bucket, and routed the decision path).
#
# NULLABILITY IS BOUND TO §2, NOT HAND-SET (CF-0001 human rulings #4/#5): a field is nullable
# IFF its §2-shown VALUE offers `null` as an option (`class: null | CF-NNNN`,
# `recurrence-of`/`blocked`/`norm-ruling: null`). A substantive shown value is NEVER nullable,
# whatever the comment says (ruling #5: nullability derives from the value, not from a `list`
# annotation — the earlier `list`-in-comment rule wrongly made `unit` nullable and let a
# missing/empty/`[]` unit skip the shared gate on every consumer). EVERY other §2 field is
# required and NON-nullable: `id`, `title`, `severity`, `dimension`, `unit` (§2 `<unit path>`
# — its `_canon_unit` predicate also rejects an empty list `[]`, and `_parse_frontmatter`
# folds a block YAML list into a non-empty scalar so real multi-unit findings pass), `status`,
# `attempts`, `pass`, `updated`. "Required AND nullable" is a self-contradiction (ruling #4
# diagnosis: `title`/`severity`/`pass`/`updated` sat required-yet-nullable, so an empty value
# skipped the shared gate — missing `pass`/`updated` certified clean on all three consumers,
# empty `title`/`severity` on the decision path/metrics). `canonical_schema_nullability_issues`
# DERIVES nullability from §2 and REFUSES any field whose schema flag disagrees, so the
# contradiction cannot recur field by field — the same schema-drives-validator discipline the
# meta-check already applies to field names and value predicates. `members` is a CF-only
# field declared in its OWN §2 block (F-0140), NOT the finding canonical-record block the
# bijection reads, so it is validated but excluded from the §2<->table bijection and the
# nullability bind.
def _canon_severity(v):
    return None if v in SEVERITIES else f"severity '{v}' is not in the §2 vocabulary ({'/'.join(sorted(SEVERITIES))})"


def _canon_status(v):
    return None if v in STATUSES else f"status '{v}' is not in the §5 lifecycle vocabulary"


def _canon_id(v):
    return None if re.fullmatch(r"(?:F|CF)-\d{4}", v) else f"id '{v}' is not a canonical F-NNNN/CF-NNNN"


def _canon_attempts(v):
    # I-7 (CF-0001 reopen): non-negative integer only. `isdigit()` rejects a sign, a
    # float, and `banana` — the exact poison that certified as a KPI and crashed the
    # decision path's `int(attempts)` with a raw ValueError.
    return None if v.isdigit() else f"attempts '{v}' is not a non-negative integer (§2 `attempts: 0`)"


def _canon_pass(v):
    return None if re.fullmatch(r"P-\d{2}", v) else f"pass '{v}' is not a P-NN pass id"


def _canon_calendar_date(field, v, ref=""):
    # F-0119: the ONE calendar-date predicate. A shaped-but-impossible date (`2026-02-30`,
    # `2026-13-01`) must be refused wherever a date is canonical — `updated` on a finding,
    # `created`/`admitted-at` on a construal. Writing a second date parser for the new fields
    # is exactly the forked-validation shape CF-0001 closed, so every caller comes through here
    # and only the field NAME differs in the message.
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return f"{field} '{v}' is not a YYYY-MM-DD date"
    try:
        datetime(int(v[0:4]), int(v[5:7]), int(v[8:10]))
    except ValueError:
        return f"{field} '{v}' is not an existing calendar date{ref}"
    return None


def _canon_updated(v):
    # F-0119 (missed CF-0001 instance): the FORM `NNNN-NN-NN` is necessary but not
    # sufficient — a value-constrained §2 field's predicate must also check RANGE, and
    # for a date the range IS calendar validity (month 1–12, day within the month's
    # length). Checking the shape alone let `2026-02-30` / `2026-13-01` / `2026-99-99`
    # certify 'clean' and publish an impossible audit chronology (the same predicate-
    # promises-more-than-it-checks root CF-0001 closed). Parse the shaped value as a real
    # date (`datetime(y, m, d)` raises ValueError for a non-existent one); a leap day like
    # `2024-02-29` stays legal because a real calendar accepts it. The parse itself lives
    # in the ONE shared `_canon_calendar_date`, so the construal dates cannot drift onto a
    # second, weaker reading of what a date is.
    return _canon_calendar_date("updated", v, " (§2 `updated: <date>`)")


def _canon_blocked(v):
    # I-10 (CF-0001 reopen): §2 constrains `blocked` to `null` or `norm-ratification`.
    # It was originally left to the §8 routing gate — but that gate ACTS only on the
    # EXACT `norm-ratification`, so a garbage value read as "not blocked" silently SKIPS
    # the mandatory human norm gate and let the CF proceed. That delegation was NOT
    # fail-closed, so the field needs its own shared predicate like any value-constrained
    # field; the routing gate still acts on the now-validated value, unchanged.
    # The `null` spelling never reaches here: it is folded to "" at the parser (ruling #6)
    # and accepted by the nullable gate, so this predicate only ever sees a PRESENT value
    # and no per-field predicate mentions a YAML null.
    return None if v == "norm-ratification" else f"blocked '{v}' is not `null` or `norm-ratification` (§2/§8)"


def _canon_refusal(v):
    # XCHECK.md §5: `refusal` is a value-constrained §2 field — a reason from the ONE
    # closed vocabulary (REFUSAL_REASONS) or nothing. A free-text reason would be read
    # raw by the decision path's gate message and by any successor session inheriting
    # it, so it gets a shared predicate like every other constrained canonical field
    # (the CF-0001 class root: a constrained field with no predicate is read past
    # validation). The `null` spelling never reaches here — the parser folds it to ""
    # and the nullable gate accepts it, so this predicate only ever sees a present value.
    return None if v in REFUSAL_REASONS else f"refusal '{v}' is not in the §5 vocabulary ({'/'.join(sorted(REFUSAL_REASONS))})"


def _canon_unit(v):
    # CF-0001 human ruling #5: §2 shows `unit: <unit path>` (a substantive value; the "YAML
    # list" is only the multi-unit FORM, still non-empty), so `unit` is NON-nullable and must
    # name at least one addressable unit. It had NO predicate and was declared nullable, so a
    # missing `unit:`, a bare `unit:`, or `unit: []` certified clean while all three consumers
    # (lint/decision/metrics) read past the absent value — the class root. The empty scalar is
    # already caught by `_field_gate` (non-nullable); this predicate additionally rejects an
    # empty list `[]`/`[ ]` and a value with no path token. `_parse_frontmatter` folds a block
    # YAML list into a comma-joined scalar, so a real multi-unit finding reaches here non-empty.
    s = (v or "").strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    toks = [t for t in re.split(r"[,\s]+", s.strip()) if t]
    return None if toks else "unit has no addressable path (§2 `unit: <unit path>`; a YAML list must carry at least one unit)"


def _canon_admitted_scope(v):
    # XCHECK.md §7/§10 `scope_typing`: the scope a fix ADMITS — which routes, inputs and
    # call sites its guarantee covers. Set-valued, in the declared list FORM settled for
    # `unit` (F-0112 / CF-0001 round 9): a scalar, or a flat list of scalars, never a
    # nested or flow-mapping form. Nullable — the field is absent on every finding closed
    # under the old norm and on every finding while `scope_typing` is off — but a PRESENT
    # value must name at least one thing, because an empty list is a scope declaration
    # that declares nothing while reading as filled. `_parse_frontmatter` folds a block
    # YAML list into a comma-joined scalar, so a real multi-route value arrives non-empty.
    s = (v or "").strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    toks = [t for t in re.split(r"[,\s]+", s.strip()) if t]
    return None if toks else "admitted-scope is present but names nothing (§7: a scalar, or a flat list with at least one element)"


def _canon_dimension(v):
    # I-15 (CF-0001 re-take round 3, human ruling #3): §2 declares `dimension: <dimension
    # slug>`. It had NO value predicate, so an empty or non-slug value certified 'clean'
    # and `cmd_metrics` published it as a raw `by dimension:` KPI bucket (`:1`, `Invalid
    # Slug!:1`) — a canonical field read past validation, the class root. The predicate is
    # SYNTACTIC only: a non-empty canonical slug (§2: ASCII, lowercase, hyphenated). The
    # ruling scopes the cross-record check (slug ∈ AUDIT.md dimensions) OUT as a declared
    # §8-rule-5 exception, so it stays lint-surface, not here. Non-nullable (see schema),
    # like `attempts`, because a consumer reads the value raw.
    return None if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", v or "") else f"dimension '{v}' is not a canonical slug (§2: non-empty ASCII lowercase, digits, hyphens)"


def _canon_class(v):
    # F-0150: §2 closes the form — `class: null | CF-NNNN`. The value was a DELEGATED
    # gate (`sole_cf`), but that gate fires only where `class` is READ (class-membership
    # states), so `class: garbage` on a reported/accepted finding certified as a clean
    # canonical record and rode the lifecycle until some consumer finally read it. The
    # syntactic half is now universal — every status, every consumer, through the shared
    # gate; the contextual half (the named CF exists, bidirectional membership) stays in
    # lint/routing where it belongs.
    return None if sole_cf(v) else f"class '{v}' is not a single CF-NNNN id (§2: `class: null | CF-NNNN`)"


def _canon_norm_ruling(v):
    # F-0150: §2/§8 rule 8 close the form — `pending` or a norm-id token (N1,
    # N1-over-N4). The §8 machine gate is fail-closed only WHERE it runs (a CF with
    # `blocked: norm-ratification`); with `blocked: null` a garbage `norm-ruling`
    # certified clean. Universal syntactic check here; the blocked/pending pairing
    # stays with the §8 gate and lint.
    s = (v or "").strip()
    if s.lower() == "pending" or NORM_RULING_TOKEN_RE.match(s):
        return None
    return f"norm-ruling '{v}' is neither 'pending' nor a norm-id token (e.g. N1 / N1-over-N4) (§2, §8 rule 8)"


def _canon_members(v):
    # CF-0001 re-take round 3 (I-13): a CF `members:` value is a list of canonical ids
    # (§2 `F-NNNN`/`CF-NNNN`). It had NO value predicate and was not a declared delegation,
    # so `cmd_metrics` read it through `parse_members`, silently DROPPED a non-canonical
    # element, and published a false `total members: 0` — a consumer reading a canonical
    # field past validation, the class root. The shared value gate (canonical_record_issues,
    # run by lint + the decision path + metrics) now rejects any non-canonical element with
    # the SAME parse `cmd_metrics`/`cmd_lint` use, so no consumer can drop it silently.
    _, bad = parse_members(v)
    return None if not bad else f"members entry(ies) {bad} are not canonical F-NNNN/CF-NNNN ids (§2)"


# field -> (in_§2_yaml_block, nullable, shared-gate-value-predicate-or-None).
# Mirror of the XCHECK.md §2 canonical-record block; `canonical_schema_meta_issues`
# proves the mirror holds (in-block field <-> table entry, bijectively). This ONE table
# is the map of the whole canonical record; a §2 field with no entry — or an entry §2
# dropped — fails the meta-check, so coverage is DERIVED from §2, never a hand list (the
# CF-0001 re-take: coverage-by-enumeration is what let attempts/filename-id/missing-id
# recur field by field).
#
# The third element is the predicate the SHARED value gate (canonical_record_issues, run
# by lint + the decision path + metrics) applies to a present value. It is None ONLY for a
# field whose §2 value is a `<free-form>` placeholder (title, unit — no closed
# constraint, garbage harmless) OR one already validated by an existing fail-CLOSED shared
# gate the whole tool routes through — the same one-predicate discipline this class
# enforces, just predating this table. Those delegations are DECLARED and PROVEN in
# DELEGATED_VALUE_GATES below (a bare None on a value-constrained §2 field is exactly what
# `canonical_schema_meta_issues` now REFUSES — the I-10 reopen: `blocked` sat here with a
# None predicate on a delegation that was NOT actually fail-closed). Re-homing a delegated
# field into a flat value check here would FORK validation (the very anti-pattern) and
# preempt its context-specific gate. `members` is a CF-only field declared in its OWN §2
# block (F-0140); the finding canonical-record block the bijection reads does not carry it,
# so it is excluded from the §2<->table bijection (in_block=False).
# 2nd element = NULLABLE (empty/absent acceptable). Non-nullable — fields a consumer reads
# RAW so an empty value corrupts it: `status` (routed on), `attempts` (§2 `attempts: 0`,
# read raw by routing/metrics — the I-11 empty poison), and `dimension` (read raw into the
# `by dimension` KPI bucket — the I-15 empty/garbage poison, human ruling #3).
CANONICAL_SCHEMA = {
    "id":            (True,  False, _canon_id),
    "title":         (True,  False, None),
    "severity":      (True,  False, _canon_severity),
    "dimension":     (True,  False, _canon_dimension),
    "unit":          (True,  False, _canon_unit),
    "status":        (True,  False, _canon_status),
    "class":         (True,  True,  _canon_class),        # F-0150: universal syntactic gate
    "attempts":      (True,  False, _canon_attempts),
    "recurrence-of": (True,  True,  None),
    "blocked":       (True,  True,  _canon_blocked),
    "norm-ruling":   (True,  True,  _canon_norm_ruling),  # F-0150: universal syntactic gate
    "refusal":       (True,  True,  _canon_refusal),
    "admitted-scope": (True, True,  _canon_admitted_scope),
    "pass":          (True,  False, _canon_pass),
    "updated":       (True,  False, _canon_updated),
    "members":       (False, True,  _canon_members),
}
SCHEMA_BLOCK_FIELDS = frozenset(f for f, spec in CANONICAL_SCHEMA.items() if spec[0])

# CF-0001 (I-10 reopen): a value-constrained §2 field (its §2 value is a literal/pattern,
# not a `<free-form>` placeholder) that carries NO shared value predicate above is an
# ESCAPE only if a DECLARED, PROVEN fail-closed gate already validates it wherever a
# consumer reads it. Otherwise it IS the class root — a canonical field read past
# validation, as `blocked` was (its routing gate acted only on an exact match, so a
# garbage value unblocked the CF and skipped the human norm gate). This map names each
# such gate and the selftest that proves it fail-closed. `canonical_schema_meta_issues`
# REFUSES a constrained field that is neither predicated nor listed here (so a future field
# cannot repeat `blocked`); the selftest plants garbage in each listed field and requires
# lint to reject it, so a delegation entry can never be the silent no-op `blocked`'s was.
DELEGATED_VALUE_GATES = {
    # F-0150: `class` and `norm-ruling` are no longer delegations — their delegated gates
    # fired only where the field was READ (class-membership states; a CF at the §8 gate),
    # so garbage in any other status certified as a clean canonical record. Both now carry
    # universal syntactic predicates in CANONICAL_SCHEMA (`_canon_class`,
    # `_canon_norm_ruling`); their contextual halves stay in lint/routing. `recurrence-of`
    # remains a true delegation: lint validates it UNCONDITIONALLY (F-0043 block, every
    # status), and no routing/metrics consumer reads the value, so nothing reads it past
    # validation.
    "recurrence-of": "cmd_lint F-0043 ancestor/acyclic check — no routing/metrics consumer reads it (F-0043 selftest)",
}


def sole_cf(value):
    """A canonical class link (§2: `class: null | CF-NNNN`) parsed fail-CLOSED:
    the WHOLE stripped value must be exactly one `CF-NNNN`, else it names no
    valid CF (F-0043/F-0050). A loose first-match search would wrongly accept
    `junk-CF-0001`, `CF-0001-junk`, or ambiguous `CF-0001 CF-0002`."""
    v = (value or "").strip()
    return v if re.fullmatch(r"CF-\d{4}", v) else None


def parse_members(value):
    """Canonical member ids from a CF `members:` frontmatter value, returned as
    (valid, non-canonical) — parsed as a LIST with EXACT id boundaries.

    XCHECK.md §2 fixes ids at exactly 4 digits (`F-NNNN`/`CF-NNNN`) and
    templates/class-finding.md writes `members:` as a bracketed list. A loose
    `re.findall(r"F-\\d{4}", ...)` substring search extracts `F-0001` from the
    longer `F-00010` and from the framed `junk-F-0001-junk`, so a CF that does
    NOT actually list `F-0001` was counted as listing it — letting a broken
    class membership certify a false 'done' AND pass `lint` (F-0050 reopen: the
    human ruling requires exact list-element boundaries, not a substring search).
    Each comma/whitespace-separated element must match a canonical id EXACTLY;
    anything else is returned as a non-canonical token so callers can flag it."""
    v = (value or "").strip()
    if v.startswith("[") and v.endswith("]"):
        v = v[1:-1]
    good, bad = [], []
    for tok in re.split(r"[,\s]+", v.strip()):
        tok = tok.strip()
        if not tok:
            continue
        (good if re.fullmatch(r"(?:F|CF)-\d{4}", tok) else bad).append(tok)
    return good, bad


def _broken_superseded(records):
    """First `superseded-by-class` member NOT genuinely absorbed by a CLOSED class
    finding that lists it.

    Terminality of a superseded member requires a CLOSED CF (README §3,
    bootstrap.md §3) AND a bidirectional `members` link. XCHECK.md §8 rule 4
    makes `members` the record of membership ("Rejecting a CF also reverts every
    finding in its `members` list"), so a one-sided `class` check is not enough:
    a member pointing at a closed CF that does NOT list it is not actually
    absorbed. A member whose CF is not-yet-closed (including a REJECTED CF whose
    members never reverted) or closed-but-does-not-list-this-member is not really
    terminal; counting it terminal via the flat TERMINAL set lets the cycle
    certify a false 'done' over lost/unfinished global remediation (F-0050).

    The "CF missing or unresolvable" case is no longer checked here and no longer
    can be: `class` is a cross-record reference the schema resolves, so a record
    that loaded points at a class finding that exists (phase 5)."""
    by_id = {r.id: r for r in records}
    for r in records:
        if r.status != "superseded-by-class":
            continue
        cf_row = by_id.get(r.cls) if getattr(r, "cls", None) else None
        if not cf_row or cf_row.status != "closed":
            return r.id
        if r.id not in getattr(cf_row, "members", ()):
            return r.id
    return None


def superseded_terminal(fid, by_id):
    """True iff a `superseded-by-class` record is genuinely TERMINAL — absorbed by
    a CLOSED class finding that BIDIRECTIONALLY lists it (README §3; §8 rule 4),
    the same condition `_broken_superseded` enforces for the done gate.

    `superseded-by-class` is only CONDITIONALLY terminal (see HARD_TERMINAL):
    terminal -> its `next` must be `—`/`-`; not-yet-absorbed -> its fate follows
    the CF's resolution and is caught by the broken-class gate, so its `next`
    owner is left unchecked."""
    rec = by_id.get(fid)
    cf_row = by_id.get(rec.cls) if rec is not None and getattr(rec, "cls", None) else None
    if not cf_row or cf_row.status != "closed":
        return False
    return fid in getattr(cf_row, "members", ())


def pending_triage_delta(fstatus, ledger_status, fm, fnd, ledger_status_by_id=None):
    """True iff a ledger status legally leads the finding file per §2 rule 3 — a
    PENDING triage decision the next agent session applies to the file. §2 names
    exactly THREE such deltas, and this is the single definition both the state
    reconciliation (effective_rows) and lint judge against, so neither can drift
    from the other (F-0099: effective_rows modelled only the first and threw away
    the second, overriding a rejected-CF member's ledger `accepted` with the file's
    stale `superseded-by-class` — the very reversion lint already honoured):

      1. the ordinary `reported` → `accepted`/`rejected`/`deferred`;
      2. member reversion `superseded-by-class` → `accepted`, legal ONLY when the
         human REJECTED the member's own class CF (§5, §8 rule 4). That decision is
         LEDGER-leading (§2 rule 3): at reversion time the CF's LEDGER row already
         reads `rejected` while the CF finding-FILE still lags at its pre-triage
         `reported` (a CF reaches Triage as `reported`, §8 rule 4). The reversion is
         therefore keyed on the CF's LEDGER status, never on the stale finding-file
         status — because a pre-sync *accepted* CF file ALSO reads `reported`
         (F-0141 reopen: an F-0099 recurrence), so the finding file cannot tell a
         rejected CF from an accepted one before sync, and keying on it reverts
         members of an accepted CF that §8 requires to stay `superseded-by-class`.
         The CF's ledger status is supplied by `ledger_status_by_id` (every live
         consumer holds the parsed ledger and passes it). A triage-ACCEPTED CF
         keeps its members absorbed, so a member showing `accepted` in the ledger
         under a non-rejected CF is lifecycle corruption, not a pending-triage
         delta; and
      3. RE-TRIAGE `deferred` → `accepted`/`rejected` (F-0131): `deferred` is parked,
         not terminal (§5), so the human revisits it at a later sitting. Without this
         delta effective_rows read the human's re-triage as ordinary ledger/file drift
         and wiped the ledger `accepted`/`rejected` back to the file's stale `deferred`
         — the "never left routeless" promise §5 makes for `deferred` was then false,
         the exact hole the Verifier reopened this finding on. A re-defer
         (`deferred` → `deferred`) is not a delta (same status); it never reaches here.

    `fm` is the member's frontmatter, `fnd` the {id: frontmatter} map, and
    `ledger_status_by_id` the {id: ledger status} map — the last is required to
    judge the member-reversion delta and defaults to empty (no reversion) when a
    caller cannot supply it. Everything else is a real ledger/file disagreement,
    not pending."""
    if fstatus == "reported" and ledger_status in TRIAGE_STATUSES:
        return True
    if fstatus == "deferred" and ledger_status in ("accepted", "rejected"):
        return True
    if fstatus == "superseded-by-class" and ledger_status == "accepted":
        member_cf = sole_cf((fm or {}).get("class"))
        if not member_cf:
            return False
        # F-0141 (reopen): the reversion is legal ONLY when the human REJECTED the
        # CF, and that decision is LEDGER-leading (§2 rule 3). Drive it off the CF's
        # LEDGER status — the sole signal that separates a pre-sync rejected CF from
        # a pre-sync accepted one, since BOTH still read `reported` in the finding
        # file (F-0141 reopen). A triage-`accepted`/`closed` CF keeps its members
        # `superseded-by-class`, so only a ledger `rejected` reverts them.
        return (ledger_status_by_id or {}).get(member_cf) == "rejected"
    return False

# §4 rule 9: the construal dispatch gets its OWN prompt, NOT the role's prompt with a
# substituted charter. Ouroboros-3, first live use: the gate was dispatched through
# PROMPTS["Planner"], whose charter is HARD-CODED ("produce audit/AUDIT.md") with no
# `{charter}` slot at all — so `.format(charter=...)` silently discarded the construal
# instruction and the agent did the ordinary Planner job. The gate was a NO-OP. That is the
# promise-width root again (§7): the norm claimed "the role writes its construal and stops",
# the predicate only renamed a charter inside a prompt that still ordered the work.
#
# A construal session is NOT the role doing its job with different words; it is a different
# session type that happens to be played by the same role. It therefore gets a prompt that
# says so, and `role_prompt_charter_issues` refuses any role prompt that takes a charter
# without a slot to put it in, so the silent-discard half cannot recur either.
# The Planner's charter, as DATA. It used to be prose baked into PROMPTS["Planner"] with no
# `{charter}` slot, which made the Planner the one role whose charter was implicit — and that
# anomaly caused two defects in a row: the construal instruction was silently discarded into
# it (the gate became a no-op), and then `role_and_charter` returned None for it, so the
# Planner could never write a construal carrying the non-nullable `charter` the record
# requires — a permanent stop-construal. A charter that exists but is not written down is
# the same defect shape as a claim that is wider than its predicate: the thing is real, the
# record of it is missing. Now every dispatched role names its charter, and
# `prompt_charter_slot_issues` requires all four to have somewhere to put it.
PLANNER_CHARTER = "inventory this project and its norms; produce audit/AUDIT.md. Do not audit anything yet"

# ---------------------------------------------------------------- parsing


# F-0096: the canonical session-identity schema. BOTH producers of a `fixed-by`
# provenance token emit exactly this — `os.urandom(8).hex()`: the orchestrator's
# per-spawn XCHECK_SESSION_ID (run_session) and a standalone session's lock nonce
# (Lock.acquire) — so a well-formed provenance token is exactly 16 lowercase hex
# characters. The reopen poison proved that accepting any non-empty string (e.g.
# `fixed-by: not-a-session-id`) as proof of independence is a hole: arbitrary
# junk does not prove a producer was ever identified. Validating the FORMAT is
# what turns `fixed-by` into evidence instead of an assertion (§3 Verifier ≠
# fixer, §6 evidence standard).
SESSION_ID_RE = re.compile(r"^[0-9a-f]{16}$")


def is_session_id(s):
    """True iff `s` is a canonical session id (16 lowercase hex chars) — the
    exact shape `os.urandom(8).hex()` produces for XCHECK_SESSION_ID and lock
    nonces (F-0096)."""
    return bool(SESSION_ID_RE.match((s or "").strip()))


# XCHECK.md §4 rule 9 — the CONSTRUAL record. A chartered session states, before any
# effect on the material, how it read its charter; the construal is admitted as EVIDENCE
# the admitter inspects, never as authority the producer grants itself. These constants are
# the closed vocabularies, declared once each.
CONSTRUAL_STATUSES = frozenset({"proposed", "admitted", "refused"})
CONSTRUAL_SECTIONS = ("## Task frame", "## Approach", "## Assumptions",
                      "## Stop conditions", "## Out of scope")
# A HUMAN admitter, written `human:<label>`. §4 rule 9 makes admission a separate act by a
# party that is NOT the producer; with the `construal_envelope` pre-authorization removed
# (F-0138, Ouroboros-3), a human — or another real session — is that party. Until Ouroboros-3
# the record had no form for a human at all: only a 16-hex session id, so an operator admitting
# a construal had to be written down as a session that never ran, a false record, and a
# mechanism whose central act can only be recorded falsely is licensing theatre by construction.
# The label identifies WHO admitted; it carries a colon, so it can never collide with a 16-hex
# session id and the producer != admitter predicate stays sound.
HUMAN_ADMITTER_RE = re.compile(r"^human:[A-Za-z0-9][A-Za-z0-9._-]*$")
# `envelope:<name>` — a HISTORICAL admitter form (F-0138, Ouroboros-3). The
# `construal_envelope` pre-authorization it names was removed (§4 rule 9, §10): three
# attempts to make an in-tree pre-authorization independent each left a self-authorization
# surface, so the mechanism is gone and NO route mints this form anymore. But records that
# were admitted under it while it was live still sit on disk, and rewriting an already-admitted
# record is the exact overwrite §4 rule 9 forbids — so the validator still READS this form as
# a valid admitter identity (keeping those records lint-clean without touching them), while the
# gate never honors it as a LIVE admission (`construal_gate_resolver`). Read-time acceptance,
# mint-/dispatch-time refusal: the split Human ruling #2a demands.
ENVELOPE_ADMITTER_RE = re.compile(r"^envelope:[A-Za-z0-9][A-Za-z0-9._-]*$")


def construal_key(role, charter):
    """The deterministic key for one (role, charter) pair: 16 lowercase hex, the same shape
    as a session id so one id form travels through the whole tool.

    Same role + same charter ⇒ same key; any change to either ⇒ a different key. That is
    the whole contract: the orchestrator looks for `audit/construals/<key>.md` before
    dispatching a charter, so a REWORDED charter is a different charter and needs its own
    construal — a construal admitted for one reading must not silently license another.
    The role and charter are joined by a NUL byte, which neither can contain, so
    ('Auditor', 'x y') and ('Auditor x', 'y') cannot collide."""
    h = hashlib.sha256()
    h.update((role or "").strip().encode("utf-8"))
    h.update(b"\0")
    h.update((charter or "").strip().encode("utf-8"))
    return h.hexdigest()[:16]


def _canon_construal_key(v):
    return None if is_session_id(v) else f"key '{v}' is not a canonical construal key (16 lowercase hex, from construal_key(role, charter))"


def _canon_construal_role(v):
    # Derived from CANONICAL_NEXT (the §2/§5 actor vocabulary) minus the non-role tokens,
    # so the role names are declared once for the whole tool and a renamed role cannot
    # leave a stale second list here.
    roles = CANONICAL_NEXT - {"human", "—", "-"}
    return None if v in roles else f"role '{v}' is not a canonical role ({'/'.join(sorted(roles))})"


def _canon_construal_status(v):
    return None if v in CONSTRUAL_STATUSES else f"status '{v}' is not in the construal vocabulary ({'/'.join(sorted(CONSTRUAL_STATUSES))})"


def _canon_construal_session(v):
    return None if is_session_id(v) else f"session '{v}' is not a canonical session id (16 lowercase hex, the shape XCHECK_SESSION_ID / the lock nonce take)"


def _canon_construal_admitter(v):
    # The admitter is a party that is NOT the producer: either another real session or the
    # operator, written `human:<label>` (§4 rule 9). Anything else is an assertion, not an
    # identity — the same reason `fixed-by` is format-validated (F-0096): junk does not prove
    # anyone admitted anything. `envelope:<name>` is accepted too, but ONLY as a HISTORICAL
    # read-time form (F-0138): it is never a live admission — the gate refuses it — yet records
    # admitted under the removed `construal_envelope` mechanism must stay readable rather than be
    # rewritten (the overwrite §4 rule 9 forbids). No route mints it; new admissions are a session
    # id or `human:<label>`.
    s = v.strip()
    if is_session_id(s) or HUMAN_ADMITTER_RE.match(s) or ENVELOPE_ADMITTER_RE.match(s):
        return None
    return (f"admitted-by '{v}' is not an admitter identity — use a canonical 16-hex session id"
            f" for an admitting session, or `human:<label>` for an operator admission"
            f" (`envelope:<name>` is a historical form only, honored on existing records but never"
            f" a live admission — the construal_envelope mechanism was removed, F-0138) (§4 rule 9)")


def _canon_construal_created(v):
    return _canon_calendar_date("created", v, " (§4 rule 9)")


def _canon_construal_admitted_at(v):
    return _canon_calendar_date("admitted-at", v, " (§4 rule 9)")


# The construal record, declared as DATA in the SAME (in_block, nullable, value-predicate)
# shape as CANONICAL_SCHEMA and validated by the SAME `_field_gate` dispatcher. Forking a
# second validator for a second artifact is precisely what CF-0001 spent nine rulings
# closing; the machinery is reused, not copied.
CONSTRUAL_SCHEMA = {
    "key":         (True, False, _canon_construal_key),
    "role":        (True, False, _canon_construal_role),
    "charter":     (True, False, None),          # free-form: the charter text as issued
    "session":     (True, False, _canon_construal_session),
    "created":     (True, False, _canon_construal_created),
    "status":      (True, False, _canon_construal_status),
    "admitted-by": (True, True,  _canon_construal_admitter),
    "admitted-at": (True, True,  _canon_construal_admitted_at),
}


# The LEDGER column schema, declared ONCE (§2/N1: rows follow one fixed format
# `| id | title | severity | status | next | updated |`). This single tuple is the sole
# source for BOTH the column names a parsed row is keyed by (`parse_ledger`) and the exact
# header a well-formed ledger must carry (`ledger_structure_issue`). A second literal list of
# these names — e.g. baked into a header regex — is forbidden: two copies of one schema drift
# apart (F-0055; F-0123 human ruling 2026-07-28 — header must equal all six names IN ORDER,
# from the same source the rows are parsed from, not a two-column prefix anchor).
LEDGER_COLUMNS = ("id", "title", "severity", "status", "next", "updated")


def _read_audit_text(path: Path):
    """Read a durable audit file as UTF-8, converting an UNDECODABLE file into an
    ADDRESSED SystemExit (rc=1, names the file) instead of a raw UnicodeDecodeError
    traceback (F-0124). Corrupt durable state — a LEDGER/AUDIT/conf that is not valid
    UTF-8 — must fail closed on read-only commands the same way a malformed row does,
    not crash them; N7 (`docs/backlog-closure-plan.md`) requires lint 'Exit 0 clean / 1
    with a findings list', never an uncaught traceback."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise SystemExit(f"{path}: not valid UTF-8 — repair the file (xcheck reads audit state as UTF-8)")


ADMITTED_SCOPE_PARTS = ("### Covers", "### Does not cover")


# §2 fields whose declared value is a CLOSED ENUMERATION, mapped to the ONE module
# constant that holds it. `_schema_enum_binding_issues` proves the two agree, so the
# vocabulary is declared once in code and the §2 prose is a BOUND mirror rather than a
# second declaration that can drift (the CF-0001 single-source discipline, applied to
# an enumeration's MEMBERS instead of to a field name). `null` is a nullability marker,
# not a member, so it is stripped before comparison; a field whose §2 value carries no
# `|` alternatives (a fixed default like `status: reported`, a `<placeholder>`) is not
# an enumeration and is not listed here.
SECTION2_ENUM_FIELDS = {
    "severity": SEVERITIES,
    "refusal": REFUSAL_REASONS,
}


# Public orchestrator.conf keys (README §8) and their fall-back defaults. The
# numeric subset (NUMERIC_CONF) also carries each key's minimum: 0 for the
# still-permitted unbounded triage_batch_cap / off-able loop_progress_limit, 1 for a real
# batch/session count.
# The image the `container` profile runs under, PINNED BY DIGEST. An unpinned tag in a
# security feature is a supply-chain hole: `alpine/git:latest` is whatever the registry
# serves today, and the containment story would rest on it. Declared here rather than in
# `runner` because it is a CONF default and `util` owns those.
#
# What this image contains: `sh`, `git` and busybox (`nc`) — enough for the escape
# probes and for a session that only reads. It does NOT contain python3 or any agent
# CLI, so an operator running real sessions sets `container_image` to an image that
# carries their agent. `SECURITY.md` says so in the same words; the capability report
# records whichever image actually ran.
CONTAINER_IMAGE = ("alpine/git@sha256:"
                   "3b44767883ac77bddae0160cc27b6b039345e23fa3504f4159efaa32264ab57f")

CONF_DEFAULTS = {
    "batch_size": "8",
    "max_sessions": "20",
    "loop_progress_limit": "2",
    "session_note": "",
    "triage_batch_cap": "20",
    "push_after_commit": "",
    "scope_typing": "off",
    "audit_over_critical": "off",
    "construal_gate": "off",
    "embargo": "off",
    "embargo_after": "3",
    "sandbox_profile": "",
    "env_allowlist": "",
    "session_timeout": "3600",
    # PHASE 12 — the three deadlines the hard session budget cannot express. All in
    # seconds; 0 disables one. Derived from the W-06 run, whose eighth session produced
    # 1,764 bytes (the CLI banner and the prompt echoed back) and then held a session
    # slot for a full hour:
    #
    #   worked  8 sessions, 439.3s min / 670.4s median / 1052.7s max, logs 527KB-1.1MB
    #   stalled 1 session,  3600.0s (the hard cap, exactly), log 1,764 bytes
    #
    # startup_deadline=120: every session in that run printed its banner within seconds.
    #   A provider that has not written one byte in two minutes is not starting slowly.
    # output_deadline=600: the SHORTEST working session finished everything in
    #   439.3s, so 600s is longer than the entire life of the fastest real session and
    #   still cuts the stalled one from 3600s to 600s — 50 minutes of budget back.
    #   Renamed in phase 8 of the third audit: it counts BYTES.
    #   HISTORICAL: calling that a "first useful action" promised semantic progress
    #   detection the byte count cannot deliver. Behaviour unchanged; the name stopped
    #   overclaiming.
    # activity_deadline=1200: the deadline the old name pretended to be. Activity is
    #   evidence the child cannot manufacture by printing (see runner.ACTIVITY_SOURCES),
    #   and no per-session first-activity figure existed before this phase — `run_child`
    #   now records `first_activity_s` so the NEXT run derives this bound from measured
    #   first activity instead. Until then it takes the same conservative derivation as
    #   `idle_deadline`: longer than the whole duration of every session anyone has
    #   observed here (1052.7s), because a session that finished at 1052.7s could have
    #   made its first observable write at the very end. It still cuts a padding child
    #   from 3600s to 1200s.
    # idle_deadline=1200: longer than the ENTIRE duration of every session in that run,
    #   the 1052.7s longest included. A silence that outlasts the longest complete
    #   session anyone has observed here is not thinking. Per-gap idle data did not exist
    #   before this phase (`idle_s` is new), so the bound is derived from the figure that
    #   did — total duration — which is the conservative direction: a session cannot have
    #   been idle for longer than it lived.
    #
    # None of them replaces `session_timeout`: a session that is working and slow must
    # keep its hour, which is what the CONTROL in tests/test_watchdog.py asserts.
    "startup_deadline": "120",
    "output_deadline": "600",
    "activity_deadline": "1200",
    "idle_deadline": "1200",
    # PHASE 12 (third audit): scope a pass to what changed since a base revision. OFF,
    # and off is the point — this is an OPTIMISATION, not a policy, and an operator who
    # never opts in must see no behaviour change at all. Incremental auditing without a
    # periodic full sweep is a blind spot by construction, so `xcheck status` reports
    # how old the last unscoped pass is whether this is on or off.
    "evidence_bundle": "off",
    "model_routing": "off",
    "budgets": "off",
    "tokens_per_session": "0",
    "tokens_per_pass": "0",
    "tokens_per_audit": "0",
    "no_progress_share_pct": "0",
    "charter_repeat_limit": "0",
    "marginal_value_window": "0",
    "okf": "off",
    # PHASE 11 (fourth audit). Empty on purpose and NOT a fallback to a working
    # adapter: an unconfigured machine measures nothing and says so, where a default
    # would parse one vendor's numbers under another vendor's name.
    "telemetry_adapter": "",
    "cheap_model": "",
    "strong_model": "",
    "log_dir": "",
    # 30 days: long enough that a finished run is still readable while anyone is arguing
    # about it, short enough that 159 logs at ~900KB do not become the working tree's
    # largest directory again. Pruning is never automatic, so this is a default for a
    # verb the operator runs, not a clock that deletes evidence on its own.
    "log_retention_days": "30",
    "kill_grace": "10",
    "cpu_seconds": "0",
    "address_space_mb": "0",
    "lease_ttl": "300",
    # The `container` profile (phase 6). Non-zero by DESIGN: this profile's whole
    # claim is that the operating system enforces the bound, and a limit of 0 means
    # unlimited, which would be the claim without the thing. `address_space_mb`, when
    # the operator sets it, wins over `container_memory_mb` — one memory knob, and the
    # existing one keeps its meaning.
    "container_image": CONTAINER_IMAGE,
    # 0.9.1 phase 9 (audit P1 #12). Digest pinning was a RECOMMENDATION, and a
    # recommendation is not an invariant: `alpine/git:latest` is whatever the registry
    # serves today, so the adversarial escape matrix was recorded against an image that
    # need not be the one that runs next week. An unpinned reference now refuses. This
    # key waives the refusal and nothing else — it is not a containment knob and not an
    # authorization knob, because one enum carrying two meanings is how a containment
    # setting silently revoked a per-role read-only default once already.
    "unsafe_allow_unpinned_image": "off",
    # 0.9.1 phase 10 (audit P1 #11). Empty = off, and off is byte-identical to what the
    # profile did before: `--network=none`. Set it and the container gets an internal
    # network plus a broker that permits exactly these hosts.
    "egress_allowlist": "",
    "egress_uplink": "bridge",
    "egress_broker_image": CONTAINER_IMAGE,
    "container_cpus": "2",
    "container_memory_mb": "2048",
    "container_pids": "256",
    "parallel_passes": "off",
    # 0.9.1 phase 7 (audit P1 #9). `ThreadPoolExecutor(max_workers=len(pass_ids))`
    # started one worker per non-overlapping pass: a queue with three hundred disjoint
    # passes dispatched — and billed — three hundred agent sessions at once, with no
    # cap, no ceiling and nothing to confirm. These four keys exist so that lifting the
    # parallel refusal in a later release cannot re-enable unbounded fan-out, which is
    # why they land BEFORE the lift rather than beside it.
    "parallel_workers": "2",
    "max_sessions_per_run": "8",
    # The cost ceiling, in the only currency this tool can actually measure. It is NOT
    # money: xcheck has no provider telemetry, and a dollar figure computed from
    # wall-clock would be a number with a false unit. What it bounds is the WORST CASE
    # of a fan-out — sessions × `session_timeout` — checked before anything launches,
    # because a ceiling enforced mid-run kills sessions that have already been paid
    # for. 0 disables it and says so out loud.
    #
    # 480 = `max_sessions_per_run` x the default `session_timeout`, so at the shipped
    # defaults the two ceilings bind at the SAME point and neither shadows the other.
    # A lower number would make this one refuse first and the session cap would be
    # scenery; the ceiling earns its keep the moment an operator raises the count or
    # the timeout and forgets the other, which is the case it is here for.
    "parallel_budget_minutes": "480",
    # Above this many passes, a fan-out needs a human's word. With stdin not a
    # terminal the run REFUSES instead of prompting: a prompt in a cron job is a hang.
    "parallel_confirm_above": "4",
    # 0.9.1: was "2". A session that failed still had its sandbox couriered into the
    # project, and the retry gate asked only whether canonical `state.json` had moved —
    # so a provider-error whose material effect had already landed looked effect-free and
    # was dispatched again. Until phase 4 makes the retry gate read files and HEAD, the
    # honest default for "how many times may xcheck repeat a failed session" is none.
    "retry_limit": "0",
}
NUMERIC_CONF = {
    # Phase 15 budget ceilings. Minimum 0, and 0 means OFF: a budget of zero
    # tokens would refuse every dispatch the moment the key was typed, which is
    # indistinguishable from a typo and is the opposite of what setting a budget
    # is for.
    "tokens_per_session": 0,
    "tokens_per_pass": 0,
    "tokens_per_audit": 0,
    "no_progress_share_pct": 0,
    "charter_repeat_limit": 0,
    "marginal_value_window": 0,
    "batch_size": 1,
    "max_sessions": 1,
    "loop_progress_limit": 0,
    "triage_batch_cap": 0,
    # 1, not 0: `embargo_after=0` would hold every charter on its first appearance,
    # before a single attempt had been bought — a queue that refuses itself.
    "embargo_after": 1,
    # A session bound below 30s would kill every real agent session, and a 0 grace
    # makes SIGTERM meaningless — the minimum is part of the contract, not a taste.
    "session_timeout": 30,
    # 0 is a real value for all three and means "this deadline is off". No floor above
    # that: an operator debugging a hanging provider may legitimately want a 5-second
    # startup deadline, and unlike `session_timeout` (whose floor exists because a bound
    # below 30s would kill every real agent session) a short deadline here kills only a
    # session that is producing nothing.
    "startup_deadline": 0,
    "output_deadline": 0,
    "activity_deadline": 0,
    "idle_deadline": 0,
    "log_retention_days": 1,
    "kill_grace": 1,
    "cpu_seconds": 0,
    "address_space_mb": 0,
    "lease_ttl": 30,
    # 1, not 0: `container` fails closed on a zero limit rather than running unbounded
    # under a name that promises a bound.
    "container_memory_mb": 1,
    "container_pids": 1,
    # 0 is a legal setting and it means "never retry" — the conservative end, which is
    # what this key had implicitly before it existed.
    "retry_limit": 0,
    # 1, not 0: a pool of zero workers runs nothing at all, which is not a smaller
    # amount of concurrency, it is a hang. Same for the session cap.
    "parallel_workers": 1,
    "max_sessions_per_run": 1,
    # 0 is legal for both and means "no ceiling" / "confirm every fan-out" — opposite
    # ends, each the honest reading of zero for its key.
    "parallel_budget_minutes": 0,
    "parallel_confirm_above": 0,
}
# BOOLEAN config keys, coerced+validated at the SAME Conf boundary as the numeric ones
# (F-0113 human ruling: the guarantee travels with the value, not with the spelling of a
# call site). A consumer reading one of these gets a real `bool`, so no `in ("1","true",
# …)` membership test or bare truthiness survives at a call site — the mechanism-gating
# flags decide whether a whole check runs, and a typo silently meaning `off` would leave
# the operator believing a gate is armed when it is not.
#
# `push_after_commit` is DELIBERATELY not here. Its README §8 contract is lenient by
# design — anything that is not an affirmative spelling means "do not push" — which is
# the fail-SAFE reading for an outbound side effect, the opposite of the fail-CLOSED
# reading the gate flags need. Migrating it would change documented behaviour, so it
# stays on its own contract and this comment records that the difference is declared,
# not drift.
BOOLEAN_CONF = frozenset({"scope_typing", "construal_gate", "parallel_passes",
                          "unsafe_allow_unpinned_image", "embargo",
                          "audit_over_critical",
                          "model_routing", "budgets", "okf",
                          "evidence_bundle"})
_CONF_TRUE = frozenset({"1", "true", "yes", "on"})
_CONF_FALSE = frozenset({"0", "false", "no", "off"})


def conf_int(conf, key, default, minimum):
    """Coerce one raw orchestrator.conf value to an int >= minimum, failing with an
    ADDRESSED operator error instead of a raw ValueError traceback (F-0113).

    A single typo in orchestrator.conf must not drop `status`/`next`/`loop` with a
    traceback: README §8 declares batch_size, triage_batch_cap, and max_sessions as
    public config. An empty (or whitespace-only) value falls back to the default; a
    non-integer, a float, or a value below the key's minimum are all rejected. Reads
    the value with `dict.get` (the base accessor) so it is safe to call from the Conf
    boundary below without re-entering Conf.get/__getitem__."""
    raw = dict.get(conf, key, default)
    if raw is None or str(raw).strip() == "":
        raw = default
    try:
        val = int(str(raw).strip())
    except (TypeError, ValueError):
        raise SystemExit(
            f"orchestrator.conf: {key} must be an integer >= {minimum}, got {str(raw)!r}")
    if val < minimum:
        raise SystemExit(
            f"orchestrator.conf: {key} must be an integer >= {minimum}, got {str(raw)!r}")
    return val


def conf_bool(conf, key, default):
    """Coerce one raw orchestrator.conf value to a real bool, failing with an ADDRESSED
    operator error instead of silently reading as `off` (F-0113, applied to the boolean
    keys). An empty/whitespace-only value falls back to the key's default; every other
    value must be a declared affirmative or negative spelling. Anything else — `of`,
    `ON!`, `maybe` — is REFUSED rather than coerced, because these keys gate whether a
    check runs at all: an unrecognised value read as `off` is a gate the operator
    believes is armed and is not. Reads with `dict.get` (the base accessor) so it is safe
    to call from the Conf boundary without re-entering Conf.get/__getitem__."""
    raw = dict.get(conf, key, default)
    if raw is None or str(raw).strip() == "":
        raw = default
    v = str(raw).strip().lower()
    if v in _CONF_TRUE:
        return True
    if v in _CONF_FALSE:
        return False
    raise SystemExit(
        f"orchestrator.conf: {key} must be one of "
        f"{'/'.join(sorted(_CONF_TRUE))} or {'/'.join(sorted(_CONF_FALSE))}, got {str(raw)!r}")


def conf_flag(conf, key):
    """Read a BOOLEAN_CONF key as a real bool from ANY mapping — a `Conf` (which already
    coerces) or a plain dict (which a caller or a test may hand in).

    This exists because the F-0113 trap has one more mouth than the Conf boundary closes:
    `conf.get("construal_gate", "off")` on a PLAIN dict returns the STRING `"off"`, and
    `if "off":` is True. A flag read that way is permanently on — the exact
    bare-truthiness-at-the-consumer shape the human ruling forbade, just with a string
    instead of an int. Every gate-flag read goes through here, so the value a consumer
    branches on is a bool no matter what kind of mapping it came from."""
    default = CONF_DEFAULTS.get(key, "off")
    raw = conf.get(key, default)
    if isinstance(raw, bool):
        return raw
    return conf_bool({key: raw}, key, default)


def conf_number(conf, key):
    """Read a NUMERIC_CONF key as a real int from ANY mapping — the numeric twin of
    `conf_flag`, and it exists for the same reason.

    `conf_int` reads with `dict.get(conf, key, …)`, which is a DICT method: handing it
    a `Conf` (deliberately not a dict) raises TypeError, and handing a plain dict to
    `conf.get` returns the raw STRING. A consumer that does arithmetic on that string
    — `time.time() + conf.get("session_timeout", 3600)` — raises at the worst moment,
    inside a session. One accessor that works on both mappings closes it."""
    raw = conf.get(key, CONF_DEFAULTS.get(key, "0"))
    if isinstance(raw, bool):
        raise SystemExit(f"orchestrator.conf: {key} must be an integer, got a boolean")
    if isinstance(raw, int):
        return raw
    return conf_int({key: raw}, key, CONF_DEFAULTS.get(key, "0"), NUMERIC_CONF.get(key, 0))


class Conf(collections.abc.Mapping):
    """orchestrator.conf as a validating BOUNDARY (F-0113, human ruling 2026-07-28).

    A read of any NUMERIC_CONF key returns an int coerced+validated ONCE, right here,
    with an ADDRESSED operator error — so a consumer that does int(conf["batch_size"])
    can never raise a raw ValueError, because the value handed back is already an int.

    The two prior rounds put the check in a call-site guard (a regex, then an AST scan
    for `int(...)` over a name literally spelled `conf`); each was defeated by ONE alias
    or wrapper — `cfg = conf`, `conf.copy()`, `int(str(...))` — a guard that passes its
    own selftest but not a live mutation is a hole, not a defense (§7). The human ruling
    moved the guarantee onto the value itself. The earlier boundary attempt still
    subclassed `dict`, so the INHERITED `dict.copy()` returned a plain dict of the RAW
    strings and an aliased copy raw-crashed straight through. This one is NOT a dict: it
    wraps a PRIVATE `_raw` and exposes state only through accessors that coerce, so there
    is no standard base-type operation that hands back an unvalidated string. `.copy()`
    returns another Conf; the Mapping mixin's `.get()/.items()/.values()` all route
    through the coercing `__getitem__`. An alias or copy therefore cannot slip past the
    check — the guarantee travels with the mapping, not with the spelling of a call site.

    Validation is PER-READ, not eager, so a read-only command never resolves — and so
    never crashes on — a numeric key it does not consume (F-0018): an unused key is
    simply never accessed. `__contains__` reads membership WITHOUT coercing, so an
    `in conf` test never trips on a malformed value either. Non-numeric keys behave
    exactly like a plain mapping."""

    def __init__(self, raw=None):
        self._raw = dict(raw) if raw else {}

    def __getitem__(self, key):
        if key in NUMERIC_CONF and key in self._raw:
            return conf_int(self._raw, key, CONF_DEFAULTS.get(key, "0"), NUMERIC_CONF[key])
        if key in BOOLEAN_CONF and key in self._raw:
            return conf_bool(self._raw, key, CONF_DEFAULTS.get(key, "off"))
        return self._raw[key]

    def get(self, key, default=None):
        if key in NUMERIC_CONF and key in self._raw:
            d = default if default is not None else CONF_DEFAULTS.get(key, "0")
            return conf_int(self._raw, key, d, NUMERIC_CONF[key])
        if key in BOOLEAN_CONF and key in self._raw:
            d = default if default is not None else CONF_DEFAULTS.get(key, "off")
            return conf_bool(self._raw, key, d)
        return self._raw.get(key, default)

    def __contains__(self, key):
        return key in self._raw

    def __iter__(self):
        return iter(self._raw)

    def __len__(self):
        return len(self._raw)

    def copy(self):
        return Conf(self._raw)


def conf_pairs(text):
    """KEY=VALUE lines, comments and blanks dropped. One parser for both halves of the
    configuration — the project file and the operator profile have the same syntax, and
    two copies of a parser are two vocabularies waiting to drift apart."""
    raw = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        raw[k.strip()] = v.strip()
    return raw


def load_conf(audit_dir: Path, write_default: bool = False, profile: Path = None):
    """The project's configuration, with the operator's policy merged on top.

    Two files, two owners. `audit/orchestrator.conf` lives in the subject and says what
    this audit is about; the operator profile lives outside it and says what runs and
    inside what boundary. A policy key in the subject is REFUSED here — this is the one
    place every reader of configuration passes through, so it is the one place that
    cannot be the place somebody forgot.

    `profile` overrides where the operator's file is read from. It exists for tests and
    for an operator running against a profile other than their default; production
    callers pass nothing and get `policy.operator_profile_path()`."""
    conf_path = audit_dir / "orchestrator.conf"
    if not conf_path.is_file():
        if write_default:
            conf_path.write_text(DEFAULT_CONF, encoding="utf-8")
        text = DEFAULT_CONF
    else:
        text = _read_audit_text(conf_path)  # F-0124: fail-closed on an undecodable conf
    raw = conf_pairs(text)
    # The single enforcement point. Not a consumer's job and not the caller's: every
    # path that reads project configuration comes through here, so this is the one place
    # that cannot be the place somebody forgot. Checked against what the FILE carried —
    # `raw` before defaults are merged — because CONF_DEFAULTS legitimately holds a
    # default for every policy key, and refusing those would refuse every load.
    offenders = policy_keys_in(raw)
    if offenders:
        raise SystemExit(refuse_policy_in_subject(offenders, conf_path, values=raw))
    # A WITHDRAWN key, refused here rather than ignored. `orchestrator.conf` tolerates
    # keys it does not recognise — a reasonable leniency for a file that travels between
    # xcheck versions — but leniency over `evidence_cache=on` is precisely the silent
    # ignore the fourth audit found: the operator reads the documentation, sets the flag
    # and gets nothing, with no way to tell that from a feature that ran and helped.
    withdrawn = refuse_withdrawn(raw, conf_path)
    if withdrawn:
        raise SystemExit(withdrawn)
    # The policy half, from outside the subject. Only POLICY keys are taken from it: a
    # profile is the operator's answer to "what may run here", not a second place to
    # set this project's batch size — and reading project keys from it would give one
    # setting two homes and no rule about which wins.
    profile = Path(profile) if profile is not None else policy_resolve()
    if profile is not None and profile.is_file():
        # Parsed and VALIDATED by the module that owns the vocabulary: a project key or
        # an unknown key in the profile is refused there, by name. Reading it with a
        # second, lenient parser here would mean a typo'd containment setting silently
        # did nothing — the failure this whole split exists to make impossible.
        # PHASE 1: the loader is given the project so it can refuse a profile that
        # resolves INSIDE it. `audit_dir` is always `<project>/audit` (every call site),
        # so the subject's root is its parent — and passing it is what makes the boundary
        # a check rather than a convention.
        try:
            for k, v in policy_load(profile, audit_dir.parent).items():
                raw.setdefault(k, v)
        except PolicyError as e:
            raise SystemExit(str(e)) from None
    for k, v in CONF_DEFAULTS.items():
        raw.setdefault(k, v)
    return Conf(raw)


# ---------------------------------------------------------------- lint / metrics


# CF-0001 human ruling #6: the four YAML null spellings and an empty value, all meaning
# "no value". The flat frontmatter parser is line-based, so `field: null` would otherwise
# yield the STRING "null" — non-empty, so it slipped past the nullability gate and reached a
# per-field predicate as if a real value (`_canon_unit` read "null" as an addressable path;
# lint clean, metrics clean, decision routed — the round-6 reopen). Every absence spelling in
# a DECLARED form (a scalar, or a scalar element of a flat list) is folded to "" at the SINGLE
# parser input — `_yaml_absent`, applied by `_fm_value` — so the existing shared gate then
# treats them uniformly: legal for a nullable §2 field (`class`/`recurrence-of`/`blocked`/
# `norm-ruling`, written `: null` in real findings), fail-closed for a non-nullable one (`unit`
# etc.). No per-field predicate mentions a YAML spelling — the hole is one place, closed once.
# `_FLOW_MARKS` is how a value that is NOT a declared §2 form is recognised: eight rounds of
# folding absence spellings into ever-deeper nesting proved that set unbounded (a top-level fold
# left `[[null]]` addressable — the round-8 reopen), so the fix is finite — parse only the two
# declared forms and reject everything else as bad-form, rather than normalise arbitrary YAML
# (CF-0001 human ruling #9).
_YAML_NULL = frozenset(("null", "Null", "NULL", "~"))
_FLOW_MARKS = re.compile(r"[\[\]{}]")


FNAME_ID_RE = re.compile(r"^((?:F|CF)-\d{4})-")
CANONICAL_NEXT = {"Planner", "Auditor", "Triage", "Remediator", "Verifier", "human", "—", "-"}
NEEDS_HUMAN_NEXT_RE = re.compile(r"^(?:human\s+)?⚠ needs-human$")

TERMINAL_OR_DEFERRED = TERMINAL | {"deferred"}

# F-0102: `next` is not merely a known token (F-0014) — it names the OWNER of the
# finding's CURRENT lifecycle status (§2: "whichever role or the human is expected
# to act on the finding next"; §5 one-status/one-owner). This maps each status to
# the actor(s) legally expected next, so a syntactically valid role on the wrong
# status (e.g. `Verifier` on a `reported` row that still needs human triage, or
# `human` on a terminal `closed` row) is caught instead of passing as canonical.
# Terminal states expect `—`/`-` (no next actor). `superseded-by-class` is NOT in
# this map: its terminality is CONDITIONAL (member of a closed CF or not), so the
# caller COMPUTES it via `superseded_terminal` (F-0102 reopen — excluding the whole
# status let a member of a CLOSED CF carry a foreign role and still pass). A legal
# pending-triage delta and the `⚠ needs-human` exception are skipped by the caller
# before this map is consulted.
NEXT_OWNER = {
    "reported": {"Triage", "human"},
    "accepted": {"Remediator"},
    "validated": {"Remediator"},
    "planned": {"Remediator"},
    "reopened": {"Remediator"},
    "fixed": {"Verifier"},
    "disputed": {"Auditor", "human"},
    "deferred": {"Triage", "human"},
    "closed": {"—", "-"},
    "rejected": {"—", "-"},
    "withdrawn": {"—", "-"},
    "obsolete": {"—", "-"},
}


# ---------------------------------------------------------------------------
# the machine-readable output contract (phase 9)
# ---------------------------------------------------------------------------
#
# `status --json` and `metrics --json` exist so CI and later tooling read state
# WITHOUT re-parsing human output. That only holds if the JSON is a contract rather
# than "whatever the printer happened to build this release", so the shape is declared
# here and checked before it is printed.
#
# The check is deliberately CLOSED in both directions — an unknown key fails and a
# missing one fails — for the same reason `state.json`'s record schema is: a key
# nobody declared is a typo or a private convention no consumer reads, and a key that
# silently disappears breaks every consumer at once. This is the Markdown contract's
# failure mode with the lesson applied, and it is why the version is IN the payload:
# a consumer can refuse a generation it does not understand instead of guessing.
# v2 (0.9.1 phase 3) adds `quarantine` to STATUS_SCHEMA. The schema is CLOSED in both
# directions, so a consumer validating against v1 would refuse a v2 payload — which is
# the version's whole job. Bumped rather than smuggled in: an added key is a contract
# change even when it is only ever additive for a lenient reader.
# v3 (W-02) adds `tokens` to METRICS_SCHEMA: what each session cost, read from the log
# the tool already writes. Bumped for the reason v2 was — an added key is a contract
# change even when it is only ever additive for a lenient reader, and a consumer pinned
# to v2 should refuse a v3 payload rather than silently ignore a figure it does not know.
#
# v4 adds `canonical_transitions_measured`, `coverage_pct` and `scope` to that block, and
# CHANGES what `per_canonical_transition` means: it now divides by the transitions of the
# measured sessions rather than by every transition in the stream. A consumer pinned to v3
# would read the new number as the old one and be wrong by whatever the coverage gap is —
# on this project's own stream the old figure was 6.6x too low — so this is exactly the case the
# version exists for, and the bump is mandatory rather than tidy.
# v5 (phase 11) adds the `telemetry` block: per-FIELD coverage, so a figure measured for
# 7 of 158 sessions cannot be quoted as if it covered all of them, plus the count of
# sessions by telemetry source. One bump for the batch and not one per field — the batch
# is a single contract change, and a version that moves with every key stops being a
# signal. A consumer pinned to v4 must refuse a v5 payload rather than read a per-field
# coverage figure as the run-wide one it used to be.
# 5 -> 6 (phase 6, third audit): `telemetry.by_source` gains an `agent-reported` key,
# and figures that used to be counted under `provider` now land there. A consumer pinned
# to v5 reading a v6 payload would read a self-reported number as a provider-native one,
# which is the whole defect this phase closes — so it must refuse rather than read on.
# 6 -> 7 (phase 8, third audit): `telemetry.by_field` gains `first_activity_s`, the
# seconds to the first observable ACTIVITY as against the first byte. The block is keyed
# by field name and a consumer that enumerates it would silently gain a column, so the
# version moves: a reader pinned to v6 must refuse rather than average a coverage figure
# over a field it does not know the meaning of.
# 7 -> 8 (phase 12, third audit): `status --json` gains `full_sweep` — when a pass last
# ran UNSCOPED, and how long ago. A v7 consumer would not know to look for it, and the
# figure it reports is the one that says whether a diff-scoped audit still covers the
# tree, so the version moves rather than the key appearing silently.
# 9 -> 10 (phase 11, fourth audit): `telemetry.by_field` gains `provider_stop_reason`,
# `limit_input` and `limit_output` — three of the adapter's ten fields that nothing
# recorded before. The block is keyed by field name and a v9 consumer enumerating it
# would silently gain three columns whose coverage is 0% on every historical session, so
# the version moves rather than the keys appearing unannounced.
OUTPUT_SCHEMA_VERSION = 10

_MAYBE_STR = (str, type(None))
_NUM = (int, float, type(None))

STATUS_SCHEMA = {
    "output_schema_version": int,
    "xcheck_version": str,
    "project": str,
    "schema_version": int,
    "state_revision": int,
    "head_before": _MAYBE_STR,
    "passes": {"done": int, "queued": list},
    "findings": {"total": int, "by_status": dict},
    "lock": (dict, type(None)),
    "decision": {"kind": str, "detail": (str, list, type(None))},
    # 0.9.1 phase 3 — sessions whose changes were captured and NOT applied. On the
    # machine surface because an operator who missed the console line at the time is
    # exactly the one who needs to be told something is waiting for a decision.
    "quarantine": {"pending": int, "bundles": list},
    # PHASE 12 (third audit) — the age of the last UNSCOPED pass. On the machine
    # surface, not only the console, because the consumer this matters to is a CI job
    # deciding whether a diff-scoped result is still worth trusting; a number a human
    # has to read off a terminal is a number no gate can act on.
    "full_sweep": {"last": _MAYBE_STR, "pass": _MAYBE_STR,
                   "days": (int, type(None)), "note": str},
}

METRICS_SCHEMA = {
    "output_schema_version": int,
    "project": str,
    "passes_done": int,
    "findings": {"total": int, "by_status": dict, "by_severity": dict,
                 "by_dimension": dict, "attempts": dict},
    "class_findings": {"ids": list, "members": int},
    "auditor_accuracy_pct": _NUM,
    "fix_durability_pct": _NUM,
    "independence": {"by_level": dict, "degraded": int, "measured": int,
                     "levels": list, "degraded_levels": list, "ceiling": str},
    "sessions": {"total": int, "by_outcome": dict, "by_provider": dict},
    # 0.9.0 phase 10 — the eight pre-registered Ouroboros-4 metrics. Two of them
    # (`auditor_accuracy_pct`, `fix_durability_pct`) already had keys above and keep
    # them: adding second spellings so the eight could sit in one block would put the
    # same number on the wire twice, which is the drift this schema exists to stop.
    "fix_durability_baseline_pct": _NUM,
    "reopen_rate_pct": _NUM,
    "human_interventions": {"gates": int, "by_gate": dict, "norm_rulings": int,
                            "human_retakes": int, "total": int},
    "false_closure_rate_pct": _NUM,
    "false_closures": int,
    "closures_recorded": int,
    # W-02. `_NUM` and not `int` on four of these: null is a REAL value meaning the run
    # reported no figure at all, and it is never to be read as zero.
    "tokens": {"sessions_finished": int, "sessions_measured": int,
               "sessions_unmeasured": int, "total": _NUM,
               "canonical_transitions": int, "canonical_transitions_measured": int,
               "coverage_pct": _NUM, "per_canonical_transition": _NUM,
               "on_sessions_that_moved_nothing": _NUM,
               "share_that_moved_nothing_pct": _NUM, "scope": str, "source": str},
    # Phase 11. Coverage is reported PER FIELD because it varies per field: a provider
    # that reports tokens may report no request id, and one number's coverage says
    # nothing about another's. `by_field` maps each telemetry field to
    # {measured, unmeasured, coverage_pct}; `by_source` counts sessions by where their
    # figures came from, so "provider-native" is a claim with a number behind it.
    "telemetry": {"sessions_finished": int, "by_field": dict, "by_source": dict,
                  "disagreements": int, "fields": list, "scope": str},
    "cost_per_accepted_finding": {"proxy": str, "session_seconds": int,
                                  "accepted_findings": int,
                                  "seconds_per_accepted_finding": _NUM},
    "coverage_completeness_pct": _NUM,
    "recovery_after_kill_pct": _NUM,
    "killed_sessions": int,
    # P2 #12. One nested object rather than nine top-level keys, so a consumer can
    # tell a pre-registered metric from a breakdown of one without a lookup table.
    "breakdowns": {"durability_by_dimension": dict, "durability_by_fix_type": dict,
                   "recurrence_rate_pct": _NUM, "reopen_cause": dict,
                   "verification_independence": dict, "human_intervention_rate": _NUM,
                   "seconds_per_closed_finding": _NUM, "residue_pass_share_pct": _NUM,
                   "false_closures_found_later": int,
                   # Phase 14: sessions per ROUTE, so a reader can price the
                   # strong and cheap paths separately instead of averaging
                   # a cheap scan and a security verification into one figure.
                   "sessions_by_route": dict},
}


def json_output_issues(payload, schema, where="payload"):
    """Addressed problems with a `--json` payload, as a list of strings.

    Returns [] when the payload matches exactly. A separate function rather than an
    assertion inside the printer so a test can call it on a hand-built payload — the
    point of the criterion is that an UNKNOWN key fails, and that must be provable
    without producing one from the real command first."""
    issues = []
    if not isinstance(payload, dict):
        return [f"{where}: expected an object, got {type(payload).__name__}"]
    for key in sorted(set(payload) - set(schema)):
        issues.append(f"{where}.{key}: unknown key — the output schema is closed, so a "
                      f"consumer would never read it (declared: "
                      f"{', '.join(sorted(schema))})")
    for key, want in schema.items():
        if key not in payload:
            issues.append(f"{where}.{key}: missing — declared by output schema "
                          f"v{OUTPUT_SCHEMA_VERSION} and every consumer may rely on it")
            continue
        got = payload[key]
        if isinstance(want, dict):
            issues.extend(json_output_issues(got, want, f"{where}.{key}"))
        elif not isinstance(got, want if isinstance(want, tuple) else (want,)):
            names = ((want,) if not isinstance(want, tuple) else want)
            issues.append(f"{where}.{key}: expected {' or '.join(t.__name__ for t in names)}, "
                          f"got {type(got).__name__}")
    return issues


# ------------------------------------------------------- the in-process merge window
#
# `audit/.lock` serializes WRITERS ACROSS PROCESSES. It cannot serialize two threads of
# the SAME orchestrator process: the lock is a directory whose owner file names this
# pid, so a second acquisition from a second thread would either deadlock against its
# own process or — worse — read the owner, recognise itself and proceed. Phase 11 runs
# auditor sessions in threads, so the second half of that guarantee has to exist.
#
# ponytail: one process-wide lock, not one per project. An orchestrator process drives
# exactly one project (the `--project` argument is per-invocation), so a per-project map
# would be a dictionary with one key and a lifetime problem. If a single process ever
# drives two projects concurrently, this is the line to change.
#
# It is entered UNCONDITIONALLY by the runner's write points, including in a
# single-threaded run: an uncontended `threading.Lock` costs about a hundred
# nanoseconds and takes no branch, so `parallel_passes=off` produces byte-identical
# output rather than a differently-shaped code path that happens to agree today.
MERGE_LOCK = threading.RLock()


def serialized():
    """The merge window: hold this while writing the project.

    Reentrant on purpose — `run_session` holds it across a call that takes it again
    (the courier's own commit path), and a non-reentrant lock would deadlock a single
    thread against itself the first time those nested.
    """
    return MERGE_LOCK
