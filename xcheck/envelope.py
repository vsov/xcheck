"""The invocation envelope and the append-only event stream.

Phase 9 answers a question the tool could not answer before: *what actually ran?*
Until now a session left a log file and a 16-hex id, and "independent verification"
meant a rule in a prompt plus proof that some other session existed. An id proves a
different session; it does not prove a different provider, a different model, or a
different anything else. The envelope records what was dispatched — provider, model,
executable and its version, role, the exact bytes of the charter and the prompt, the
state revision, HEAD before and after, sandbox profile, duration, exit status and the
digest of the redacted log — so independence becomes a MEASURED level instead of an
assertion, and every claim about a session can be checked against a record.

Two deliberate boundaries:

- **The envelope is the ORCHESTRATOR's testimony, never the agent's.** It is written
  here, from the dispatch the orchestrator performed, and there is no write verb for
  it. That is the structural closure of F-0159 (`fixed-by` bound to nothing): a
  session's provenance is no longer whatever the agent typed, because the value the
  agent types is checked against a record it cannot write.
- **`events.jsonl` is append-only.** One compact JSON object per line, opened `"a"`,
  never rewritten. A stream you can rewrite is a story, not a record.

What the level does NOT prove is written into the docs beside it: even
`cross-provider` says nothing about shared training data, shared caching, or the same
operator driving both sides. The level says what it measures and no more.
"""

import hashlib
import json
import os
import re
import shlex
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from xcheck import ledger, provider
from xcheck.ledger import OUTBOX_FILENAME  # noqa: F401  (re-export)
from xcheck.state import StateError, freeze, load_state, write_state
from xcheck.util import serialized
from xcheck.util import (DEGRADED_INDEPENDENCE, INDEPENDENCE_LEVELS,  # noqa: F401 re-export
                         TELEMETRY_SOURCES)

# The two filenames live in `ledger`, which owns the append path; re-exported here
# because `envelope.EVENTS_FILENAME` is what four call sites already say.
EVENTS_FILENAME = ledger.EVENTS_FILENAME
UNKNOWN = "unknown"

# The thirteen declared fields of the envelope, in the order the roadmap names them.
# Two are pairs (an executable and its version; a duration and an exit status), so the
# record has more KEYS than fields — this list is what `describe()` prints, which makes
# "all thirteen are populated" checkable by reading the output rather than by trusting
# a claim about it.
ENVELOPE_FIELDS = (
    ("provider", ("provider",)),
    ("agent_model", ("agent_model",)),
    ("executable", ("executable", "executable_version")),
    ("role", ("role",)),
    ("charter_hash", ("charter_hash",)),
    ("prompt_hash", ("prompt_hash",)),
    ("state_revision", ("state_revision",)),
    ("head_before", ("head_before",)),
    ("head_after", ("head_after",)),
    ("session_id", ("session_id",)),
    ("sandbox_profile", ("sandbox_profile",)),
    ("duration_s/exit_status", ("duration_s", "exit_status")),
    ("log_digest", ("log_digest",)),
)
assert len(ENVELOPE_FIELDS) == 13

# Executable basename -> provider. A name this map does not know yields `unknown`,
# recorded EXPLICITLY: an absent field and a guessed one are the two ways a
# provenance record lies, and "unknown" is neither.
PROVIDERS = {
    "claude": "anthropic",
    "anthropic": "anthropic",
    "codex": "openai",
    "openai": "openai",
    "gemini": "google",
    "cursor-agent": "cursor",
    "aider": "aider",
    "opencode": "opencode",
    "goose": "goose",
    "amp": "amp",
    "q": "aws",
}

_MODEL_FLAGS = ("--model", "-m", "--model-name")
_VERSION_CACHE = {}

def sha256_text(text):
    return hashlib.sha256((text or "").encode("utf-8", "surrogateescape")).hexdigest()


def sha256_file(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return UNKNOWN


def provider_of(cmd):
    """The provider behind a role command, or `unknown`."""
    if not cmd:
        return UNKNOWN
    exe = Path(str(cmd[0])).name.lower()
    return PROVIDERS.get(exe, UNKNOWN)


def model_of(cmd):
    """The model named in a role command, or `unknown`.

    Read from the ARGUMENTS the operator configured, because that is the only model
    statement that is true by construction. A CLI's default model is a moving target
    the tool must not pretend to know."""
    for i, tok in enumerate(cmd or []):
        tok = str(tok)
        for flag in _MODEL_FLAGS:
            if tok == flag and i + 1 < len(cmd):
                return str(cmd[i + 1])
            if tok.startswith(flag + "="):
                return tok.split("=", 1)[1]
    return UNKNOWN


def executable_version(exe, env=None):
    """What the configured executable IS, obtained without running it.

    F-0098, the audit's only critical. This used to be `subprocess.run([exe,
    "--version"])`, and `dispatch_record` runs BEFORE `sandbox.enter()` — so the first
    thing a `container` dispatch did was execute an operator-configured binary directly
    on the host, outside the containment the operator asked for. A profile that does not
    contain the first program it runs is not a profile. The probe was also worth very
    little: an agent CLI's `--version` string is self-reported text, while the bytes are
    the thing a later reader would want to compare.

    So: resolve the executable on PATH and record `sha256:<64 hex>` of its bytes. No
    execution, on any path. Where the identity cannot be established — not on PATH, a
    directory, unreadable, a shell builtin — the answer is `unknown` (UNKNOWN), and the
    envelope records that rather than guessing. Both forms are self-describing: a reader
    can tell a digest from `unknown` from a legacy `--version` string in an old envelope,
    which is why the field keeps its name and stays free text.

    `env` is accepted and unused. It is kept because the parameter is part of this
    function's published signature and a caller passing the child environment is not
    wrong — there is simply no longer a child. Removing it would make the F-0098 fix a
    call-site change too, and the point of the fix is that there is nothing to configure.

    Memoized per executable, as before: `loop` dispatches many sessions and a changed
    own-source already stops the loop.
    """
    if exe in _VERSION_CACHE:
        return _VERSION_CACHE[exe]
    out = UNKNOWN
    try:
        # `which` respects PATH the way the child launch will. An absolute or relative
        # path is returned as itself when it is executable, which is what an operator
        # who configured one means.
        resolved = shutil.which(str(exe)) if exe else None
        if resolved:
            path = Path(resolved)
            digest = sha256_file(path) if path.is_file() else UNKNOWN
            # `sha256_file` answers UNKNOWN for an unreadable file. Concatenating that
            # onto the prefix would produce `sha256:unknown`, a string shaped like a
            # measurement and carrying none — the whole point of this field is that the
            # two cases stay distinguishable.
            if digest != UNKNOWN:
                out = f"sha256:{digest}"
    except OSError:
        out = UNKNOWN
    _VERSION_CACHE[exe] = out
    return out


def current_revision(project):
    """The state revision this session was dispatched against, or 0 when there is no
    readable state (a bare fixture; `next`/`loop` refuse such a project anyway)."""
    try:
        return load_state(Path(project) / "audit").state_revision
    except StateError:
        return 0


def _policy_digest():
    """sha256 of the operator profile in force, or `unknown`."""
    from xcheck import policy
    resolved = policy.resolve()
    if resolved is None:
        return UNKNOWN
    return policy.profile_digest(resolved) or UNKNOWN


def dispatch_record(cmd, role, charter, prompt, session_id, profile, state_revision,
                    head_before, env=None, conf=None, verified=None,
                    concurrent_group=None, subject_commit=None, subject_manifest=None,
                    controller_commit=None, capsule_digest=None,
                    trust_level=None, route=None):
    """The envelope as it stands BEFORE the child runs: everything already known.

    `exit_status`/`head_after`/`duration_s`/`log_digest`/`outcome` are `None` here and
    filled by `finish()`. A record with a null `exit_status` is the OPEN dispatch — the
    one the writing lock guarantees is unique, and the one `record-fix` binds a
    finding's provenance to."""
    exe = str(cmd[0]) if cmd else UNKNOWN
    return {
        "session_id": session_id,
        "role": role,
        "provider": provider_of(cmd),
        "agent_model": model_of(cmd),
        "executable": exe,
        "executable_version": executable_version(exe, env) if cmd else UNKNOWN,
        # WHICH policy actually ran. The envelope already records what was executed and
        # inside what profile; without this it does not record where those instructions
        # came from, and "the containment was wrong that day" becomes unanswerable. The
        # digest is over the file's exact bytes: an operator comparing two runs wants to
        # know whether the same file was in force, and a hash of a parsed mapping would
        # call two different files the same. `unknown` when no profile resolves — which
        # a dispatch refuses on anyway, so it is reachable only by a direct caller.
        "policy_digest": _policy_digest(),
        "charter_hash": sha256_text(charter or ""),
        "prompt_hash": sha256_text(prompt or ""),
        "state_revision": state_revision,
        "head_before": head_before or UNKNOWN,
        "head_after": None,
        # PHASE 9. `head_before` alone could not say WHAT was audited: inside a worktree
        # it is the sandbox's own seed commit, reachable from nothing. These are passed
        # IN rather than looked up here — `state.subject_commit` and friends run git, and
        # this module has no execution sites at all (F-0098). `unknown` is a real answer
        # and is recorded as one; `sandbox_seed` is filled by `finish()`, because the
        # worktree does not exist yet when this record is written.
        "subject_commit": subject_commit or UNKNOWN,
        "subject_manifest": subject_manifest or UNKNOWN,
        "controller_commit": controller_commit or UNKNOWN,
        # PHASE 13: the context this session was actually given, by content.
        "capsule_digest": capsule_digest or UNKNOWN,
        "sandbox_profile": profile.name,
        # PHASE 3: the containment decision has two halves and both are
        # recorded. The profile says what was enforced; the trust level says
        # what the operator classified the material as, which is the half a
        # reader cannot reconstruct afterwards.
        "trust_level": trust_level,
        # Phase 6: `details()` carries the CAPABILITY REPORT — what the profile
        # enforced at launch, including `verified_at_launch`, which is False for every
        # process-level profile and True only for one whose backend answered a probe.
        "sandbox_details": profile.details(conf=conf, verified=verified),
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_s": None,
        "exit_status": None,
        "outcome": None,
        "log_digest": None,
        # Phase 11: present ONLY when the parallel dispatcher launched this session
        # alongside others. It is what makes several open dispatches representable, so
        # it is deliberately absent from every serial dispatch rather than defaulted to
        # a group of one — a field that is always there stops meaning anything.
        **({"concurrent_group": concurrent_group} if concurrent_group else {}),
        # Phase 14: the ROUTE this dispatch resolved to, present only when routing is
        # on. Absent rather than defaulted, for the same reason as `concurrent_group`:
        # a field that is always there stops meaning anything, and `route: strong` on
        # every pre-phase-14 session would be a claim nobody made.
        **({"route": route} if route else {}),
    }


def provider_claim(log_path, session_id, adapter=None):
    """`(actual_model, telemetry_source)` — who said which model ran, and on what channel.

    PHASE 3 (fifth audit). The route mismatch used to be discovered inside `finish`,
    which runs AFTER `sandbox.collect()` — so a session whose provider reported a
    different model had already had its patch carried into the project by the time
    anything refused it. `run_session` asks THIS before the patch lands, and `finish`
    asks the same question of the same files when it builds the record, so the two
    cannot answer differently: one rule (`routing.attestation_problem`), one reader.
    """
    tele, _total = telemetry(log_path, session_id=session_id,
                             log_digest=sha256_file(log_path), adapter=adapter)
    return tele.get("provider_model"), tele.get("telemetry_source")


def finish(rec, rc, outcome, duration_s, head_after, log_path, sandbox_seed=None,
           first_output_s=None, idle_s=None, note=None, first_activity_s=None,
           adapter=None, planned_model=None, route=None):
    rec = dict(rec)
    if sandbox_seed:
        # The DISPOSABLE commit the child actually ran against, labelled as such by the
        # field it is in. It is known only now — the worktree is created after dispatch —
        # and it is kept rather than dropped: it is the value that used to masquerade as
        # `head_before`, and a reader tracing an old record needs somewhere to find it.
        rec["sandbox_seed"] = sandbox_seed
    rec.update({
        "exit_status": int(rc),
        "outcome": outcome,
        "duration_s": round(float(duration_s), 3),
        "head_after": head_after or UNKNOWN,
        "log_digest": sha256_file(log_path),
        # None, not 0, when the log carries no figure. `describe` prints it as `null`,
        # which is the record's existing way of saying "not known" as against `unknown`
        # ("asked and undeterminable"). It is NOT one of the thirteen declared fields:
        # those are pinned by the roadmap and by test, and a measurement the provider may
        # simply not report does not belong among fields whose claim is that all are
        # populated.
        "tokens": log_tokens(log_path),
    })
    # PHASE 11. The provider's own figures where it reported them, the log parse where it
    # did not, and the SOURCE recorded either way. `tokens` keeps its meaning — the
    # headline total — but it is now the provider's total when there is one, so the
    # figure and its provenance travel together instead of the provenance being implied
    # by a regex nobody can see from the record.
    if note:
        rec["note"] = note
    tele, total = telemetry(log_path, first_output_s=first_output_s, idle_s=idle_s,
                            session_id=rec.get("session_id"),
                            log_digest=rec.get("log_digest"),
                            first_activity_s=first_activity_s, adapter=adapter)
    if total is not None:
        rec["tokens"] = total
    for k, v in tele.items():
        if v is not None:
            rec[k] = v
    # PHASE 13 (fourth audit): the route is recorded as an ATTESTED fact or not at all.
    # `planned_model` is what xcheck put in the argv; `provider_model` is what the
    # provider says ran, read back through the phase-11 adapter — a channel the child
    # does not write. A disagreement REFUSES the record and names both, because a
    # `cheap` label over a session that ran the strong model is the figure a cost
    # comparison gets built on. Silence is neither: a session with no attestation is
    # `unattested` and says so.
    from xcheck import routing
    # PHASE 3 (fifth audit): the SOURCE goes with the model. Both functions used to read
    # the model alone, so an agent that printed the routed model's name into its own log
    # attested its own route — `telemetry_source` was recorded correctly and consulted by
    # nobody.
    source = rec.get("telemetry_source")
    bad = routing.attestation_problem(planned_model, rec.get("provider_model"), route,
                                      source=source)
    if bad:
        raise routing.RouteError(bad)
    if route:
        rec["route_attestation"] = routing.attestation(planned_model,
                                                       rec.get("provider_model"),
                                                       source=source)
    return rec



# ------------------------------------------------------- what a session cost, in tokens

# `tokens used` on its own line, the figure on the next, thousands separated by U+00A0 —
# the shape Codex writes, measured across all 151 logs of the Ouroboros-4 run: 150 carry
# it exactly once, the 151st carries it not at all because that session was killed before
# its summary was printed. A same-line `tokens used: 1234` is accepted too, because the
# separator and the placement are the provider's choice and not a contract.
#
# Deliberately STRICT. A line the pattern does not recognise yields None, never a guess
# and never 0: an absent measurement is not a measurement of zero, and a metric built on
# guessed figures is worse than one that names what it could not read. The provider's
# structured-output mode is not used to make this easier — `classify_outcome` reads the
# same log text, so changing the output format would risk a working classifier to reach
# data that is already present.
_TOKENS_RE = re.compile(
    r"(?im)^[ \t]*tokens used[ \t]*:?[ \t]*(?:\r?\n[ \t]*)?"
    r"(\d[\d\u00a0\u202f\u2009,_ ]*?)[ \t]*$")


def tokens_in_log(text):
    """The token figure a session log reports, or None when it reports none.

    The LAST match wins. No log in the measured corpus carries the phrase twice, but a
    session that quotes an earlier log in its own output would put an older figure above
    its own summary, and the summary is what this session cost.
    """
    last = None
    for m in _TOKENS_RE.finditer(text or ""):
        digits = "".join(c for c in m.group(1) if c.isdigit())
        if digits:
            last = int(digits)
    return last


# ---------------------------------------------------------------- telemetry
#
# PHASE 11. `tokens_in_log` below parses the phrase `tokens used` out of human-readable
# log text. That is a FALLBACK wearing a primary's clothes: the provider already reports
# input, output and cache tokens, the model it actually served, the reasoning effort it
# actually applied and a request id that can be quoted in a support ticket — and a
# regular expression over prose loses every one of those the day the CLI reformats a line.
#
# The parse stays exactly as it was, and stays green: it is the answer for a session whose
# provider said nothing. What changes is that the record now SAYS which source a figure
# came from, so a reader can tell a reported number from a scraped one.
#
# WHERE PROVIDER FIGURES COME FROM. Two places, both written by the operator's own role
# command, because xcheck never talks to a provider itself:
#
#   1. a sidecar `<log>.telemetry.json` — a wrapper that has the provider's response
#      object writes it out beside the log;
#   2. a line in the log of the form `XCHECK_TELEMETRY {"input_tokens": 1200, ...}` — for
#      a CLI that can be asked to echo its usage block.
#
# The sidecar wins when both exist: a file written on purpose outranks a line that could
# have been quoted from somewhere else. The LAST such line wins, for the same reason
# `tokens_in_log` takes the last match — a session that quotes an earlier log would
# otherwise report an older run's numbers.
PROVIDER, LOG_PARSE, AGENT_REPORTED = TELEMETRY_SOURCES

# PHASE 6 (third audit, P0). The two channels above are NOT the same evidence and used to
# produce the same label. The audit forged it in one line of child output:
#
#     XCHECK_TELEMETRY {"total_tokens": 1, "model": "forged-model"}
#
# and the record read `telemetry_source=provider, tokens=1, provider_model=forged-model`.
# `provider` is the one label that means the figure did NOT come from the thing being
# audited, so the channel the subject can write is now called what it is:
# `agent-reported`. Nothing about it is discarded — a cooperative agent's own count is
# useful — it simply stops borrowing the word that means "not self-reported".
#
# `provider` now requires a SIDECAR the child was never handed: `<log_root>/.telemetry/
# <session>.json`, in a directory the parent creates 0700, at a path that appears in no
# child environment variable. The operator's role command can be given it explicitly with
# a `{sidecar}` placeholder — an operator wrapper is trusted by policy, the agent inside
# it is not. Under `container` the directory is not mounted at all, so nothing inside the
# sandbox can reach it whether it knows the path or not. Under `worktree`/`none` there is
# no OS boundary and this project's own escape matrix says so; the guarantee there is that
# the path is not disclosed, which is weaker and is stated rather than implied.
SIDECAR_DIRNAME = ".telemetry"


def sidecar_path(log_path, session_id):
    """Where the parent puts this session's sidecar. Never inside the sandbox."""
    if not log_path or not session_id:
        return None
    return Path(log_path).parent / SIDECAR_DIRNAME / f"{session_id}.json"


def make_sidecar_dir(log_path):
    """Create the parent-owned directory, readable and writable by this user only."""
    if not log_path:
        return None
    d = Path(log_path).parent / SIDECAR_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(0o700)
    except OSError:                                            # pragma: no cover
        pass
    return d


# Every numeric field is checked at the boundary and REFUSED rather than coerced. The
# audit's forged object was believed in full; a real provider object that has gone wrong
# should not be either.
#
# PHASE 11 (fourth audit): the CHECK moved to `xcheck.provider`, and this name stays as
# the way the rest of the tree reaches it. Value validation and shape knowledge belong on
# the same side of the seam — a rule about what counts as a legal token count is only
# meaningful next to the list of keys that carry one.
usage_refusals = provider.usage_refusals


def sidecar_refusals(got, session_id, log_digest=None):
    """Why this sidecar is not THIS session's, on top of the value checks.

    A sidecar carries the session it belongs to. Without that, a file left behind by an
    earlier run — or copied from one — is read as the current session's cost.
    """
    bad = list(usage_refusals(got))
    if not isinstance(got, dict):
        return bad
    claimed = got.get("session_id")
    if claimed is None:
        bad.append("the sidecar names no session_id, so it cannot be bound to this one")
    elif str(claimed) != str(session_id):
        bad.append(f"the sidecar is for session {claimed!r}, not {session_id!r}")
    said = got.get("log_digest")
    if said is not None and log_digest is not None and str(said) != str(log_digest):
        bad.append(f"the sidecar names log_digest {str(said)[:12]}, but this session's log "
                   f"hashes to {str(log_digest)[:12]}")
    return bad

# PHASE 11: the alias table and the `XCHECK_TELEMETRY` pattern moved to
# `xcheck.provider`, which is now the only module that knows a vendor's key names —
# asserted structurally by `tests/test_provider_adapter.py`. What stays HERE is the part
# that is not vendor trivia: which CHANNEL an object arrived on, and therefore which label
# it may wear. Those are different kinds of knowledge and used to share a file.
# The adapter's ten fields, mapped onto the names the session record already uses. Two of
# the ten are NOT here: `provider` (the session record has carried one since phase 9, and
# a second copy is a second thing to disagree) and `tokens_total` (which IS the session's
# `tokens`, the W-02 headline figure). Declared as a table rather than done by matching
# names so that a rename on either side is a visible edit.
USAGE_TO_RECORD = (
    ("model", "provider_model"),
    ("request_id", "provider_request_id"),
    ("tokens_input", "tokens_input"),
    ("tokens_cache", "tokens_cache"),
    ("tokens_output", "tokens_output"),
    ("stop_reason", "provider_stop_reason"),
    ("limit_input", "limit_input"),
    ("limit_output", "limit_output"),
)

TELEMETRY_FIELDS = tuple(f for _s, f in USAGE_TO_RECORD) + (
    "reasoning_effort",
    "tokens_log", "telemetry_source", "telemetry_disagreement", "telemetry_refused",
    "first_output_s", "idle_s", "first_activity_s")


def _coerce(value, kind):
    """A declared value or None. A figure that will not coerce is NOT MEASURED, never 0."""
    if value is None:
        return None
    try:
        return kind(value)
    except (TypeError, ValueError):
        return None


def _read_sidecar(log_path, session_id, log_digest=None):
    """`(usage, refusals)` from the parent-owned sidecar, or `(None, [])` if there is none.

    Never raises: a session that has already finished its real work must not be lost to a
    bad number. But a sidecar that is THERE and wrong is not the same as no sidecar, so
    its refusals come back to be recorded — silence about a rejected figure is how the
    rejection becomes invisible.
    """
    side = sidecar_path(log_path, session_id)
    if side is None or not side.is_file():
        return None, []
    try:
        got = json.loads(side.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as e:
        return None, [f"the sidecar at {side} is not readable JSON — {e}"]
    bad = sidecar_refusals(got, session_id, log_digest)
    return (None, bad) if bad else (got, [])


def _read_agent_line(log_path):
    """`(usage, refusals)` from the LAST `XCHECK_TELEMETRY` line the child printed.

    The last one wins, for the same reason `tokens_in_log` takes the last match: a session
    that quotes an earlier log would otherwise report an older run's numbers.
    """
    if not log_path:
        return None, []
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None, []
    got = provider.last_line_object(text)
    if got is None:
        return None, []
    bad = usage_refusals(got)
    return (None, bad) if bad else (got, [])


def telemetry(log_path, first_output_s=None, idle_s=None, session_id=None,
              log_digest=None, first_activity_s=None, adapter=None):
    """Everything known about what one session COST and how it behaved, with the source
    of each figure recorded rather than implied.

    Every field is null when it is not known. Null is a real value here and means NOT
    MEASURED — it is never to be read as zero, because a measured zero is not something
    an agent session produces, and a run that reports 0 for an absent measurement has
    thrown away the difference between "cost nothing" and "we do not know".

    `first_output_s` and `idle_s` are measured by the ORCHESTRATOR while it streams the
    child's output, not reported by anyone: they are the two figures a provider cannot
    supply and the two a header-only session is diagnosed by.
    """
    rec = {f: None for f in TELEMETRY_FIELDS}
    rec["first_output_s"] = first_output_s
    rec["idle_s"] = idle_s
    # PHASE 8 (third audit). Seconds from dispatch to the first OBSERVABLE activity —
    # `runner.ACTIVITY_SOURCES` — as against the first byte. Null on every session that
    # ran before this phase, and null is not zero: `activity_deadline`'s default is
    # derived from total session duration precisely because this figure did not exist,
    # and it can only stop being a conservative guess once runs have recorded it.
    rec["first_activity_s"] = first_activity_s
    parsed = log_tokens(log_path)
    rec["tokens_log"] = parsed
    # The sidecar OUTRANKS the child's own line, and the two are labelled differently.
    # A file the parent's directory holds, bound to this session, is a different kind of
    # evidence from a line the subject printed — which is the whole finding.
    side, side_bad = _read_sidecar(log_path, session_id, log_digest)
    said, said_bad = _read_agent_line(log_path)
    got = side if side is not None else (said or {})
    source_of_object = PROVIDER if side is not None else (
        AGENT_REPORTED if said is not None else None)
    refused = side_bad + said_bad
    if refused:
        # Recorded, not swallowed. A refused figure that leaves no trace is a figure the
        # operator never learns was offered.
        rec["telemetry_refused"] = "; ".join(refused)
    # PHASE 11: the adapter is not consulted about the LABEL — a declared adapter does not
    # make a line the child printed into provider truth, and an undeclared one does not
    # stop a parent-owned sidecar from being one.
    #
    # PHASE 3 (fifth audit): an object arriving on the PROVIDER channel goes through
    # `provider.read`, which is where the refusals live. This used to call `adapter.parse`
    # directly and so bypassed every one of them: `read` rejected a usage record naming no
    # actual model while production recorded exactly that record as `provider`. A line the
    # AGENT printed keeps the alias reader on purpose — `refusals()` requires an actual
    # model because `provider` is a claim that a vendor vouched for the figure, and an
    # agent-reported line makes no such claim, so a missing model there is a gap and not a
    # forgery.
    if got and side is not None:
        usage, side_refused = provider.read(adapter, got)
        if side_refused:
            # The whole object is refused, not partly believed. Recorded below beside the
            # sidecar's own refusals so the operator sees the figure that was offered.
            refused = refused + side_refused
            rec["telemetry_refused"] = "; ".join(refused)
            usage, got, source_of_object = provider.Usage(), {}, None
    else:
        usage = provider.alias_usage(got) if got else provider.Usage()
    for src, field in USAGE_TO_RECORD:
        rec[field] = _coerce(getattr(usage, src), int if field.startswith(
            ("tokens_", "limit_")) else str)
    rec["reasoning_effort"] = _coerce(provider.reasoning_effort(got), str)
    provider_total = usage.tokens_total
    if provider_total is None and (rec["tokens_input"] is not None
                                   or rec["tokens_output"] is not None):
        provider_total = (rec["tokens_input"] or 0) + (rec["tokens_output"] or 0)
    try:
        provider_total = None if provider_total is None else int(provider_total)
    except (TypeError, ValueError):
        provider_total = None

    if provider_total is not None and source_of_object is not None:
        rec["telemetry_source"] = source_of_object
        total = provider_total
    elif parsed is not None:
        rec["telemetry_source"] = LOG_PARSE
        total = parsed
    else:
        total = None
    # BOTH figures are kept and the disagreement is stated. Silently preferring one would
    # hide the case that matters: the two sources disagreeing means one of them is
    # measuring something other than what its name says, and that is a defect to find,
    # not a tie to break.
    if provider_total is not None and parsed is not None and provider_total != parsed:
        rec["telemetry_disagreement"] = (
            f"{source_of_object} reports {provider_total} total tokens, the log's "
            f"`tokens used` line says {parsed}; both are recorded and `tokens` carries "
            f"the {source_of_object} figure")
    return rec, total


def log_tokens(log_path):
    """`tokens_in_log` over a session log on disk; None when the log is unreadable.

    An unreadable log is the same claim as a log with no figure — not measured — so it
    takes the same value rather than raising into a session that has already finished
    its real work.
    """
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError, TypeError):
        return None
    return tokens_in_log(text)

def describe(rec):
    """The record as the thirteen declared fields, one per line, plus what else is
    stored. `None` prints as `null` and `unknown` prints as `unknown` — the two are
    different claims (not yet known vs. asked and undeterminable) and the record keeps
    them apart."""
    out = []
    for label, keys in ENVELOPE_FIELDS:
        vals = []
        for k in keys:
            v = rec.get(k)
            vals.append("null" if v is None else
                        json.dumps(v) if isinstance(v, (dict, list)) else str(v))
        out.append(f"  {label:<22} {' / '.join(vals)}")
    for k in ("outcome", "started", "tokens", "sandbox_details", "note"):
        if rec.get(k) is not None:
            v = rec[k]
            out.append(f"  {'(' + k + ')':<22} "
                       f"{json.dumps(v) if isinstance(v, (dict, list)) else v}")
    return "\n".join(out)


# ---------------------------------------------------------------- the event stream


def event_line(event, session_id=None, **payload):
    """The event record itself, built once so both append paths write the same bytes."""
    line = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": event, "session_id": session_id}
    line.update(payload)
    return line


def emit(project, event, session_id=None, txn=None, **payload):
    """Append one OBSERVATIONAL event. Still never raises into the caller — and no longer
    a silent success either.

    PHASE 7 (fourth audit). This swallowed `OSError` and printed a note. The reasoning
    was sound as far as it went: losing the orchestrator to a full disk, while a
    session's real work is already committed, trades a durable outcome for a log line.
    What it produced was a stream every consumer above it reads as COMPLETE — budgets,
    metrics, independence, receipts, the derived bundle — quietly missing rows, with
    nothing anywhere saying a figure had become an undercount.

    The trade-off is now ANSWERED rather than reversed: a failed append goes to the
    durable outbox, and the next run that writes replays it. The durable outcome is still
    not lost; the event is no longer lost either.

    A CANONICAL TRANSITION does not come through here. `write.py` uses the two-phase unit
    in `ledger` and is refused outright when the event cannot be made durable, because a
    transition applied without its event is the case this whole phase is about. See
    `ledger`'s module docstring for the policy in full.
    """
    line = event_line(event, session_id, **payload)
    # PHASE 2 (sixth audit): `append_chained` reads the chain head and appends off it.
    # Two threads that both read the same head write two events claiming one
    # predecessor. The append is short, but "short" is not a synchronisation primitive.
    with serialized():
        try:
            # PHASE 4 (sixth audit): the same completeness question the write path asks.
            # An observational event is not lost when it is refused — it goes to the
            # durable outbox below and is replayed once the stream is a record again,
            # which is the behaviour a full disk already gets.
            ledger.require_complete(project, ledger.committed_anchor(project),
                                    action="emit")
            ledger.append_chained(project, line)
        except (OSError, ledger.IncompleteStream) as e:
            entry = {"event_id": ledger.event_id(line), "line": line}
            # PHASE 3: the row names the transaction whose write already landed, so
            # recovery can prove it rather than guessing from a revision number. Events
            # emitted outside a state write carry no txn and make no claim about one.
            if txn is not None:
                entry["txn"] = txn
            try:
                ledger._append(ledger.outbox_path(project),
                               json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
            except OSError as outbox_error:
                print(f"note: audit/{EVENTS_FILENAME} could not be appended ({e}) and "
                      f"neither could the outbox ({outbox_error}). This event is LOST and "
                      f"the stream is now an undercount — free space on audit/ before the "
                      f"next run.")
                return False
            print(f"note: audit/{EVENTS_FILENAME} could not be appended ({e}); the event is "
                  f"in audit/{OUTBOX_FILENAME} and the next xcheck run that writes will "
                  f"replay it.")
            return False
        return True


def read_events(project):
    """Every event in the stream — an ADAPTER over `ledger.read`, which validates.

    PHASE 5 (fifth audit). This was a plain `json.loads` loop, and it was the reader
    every consumer above the stream actually used: metrics, budgets, independence,
    receipts, OKF and the evidence bundle all reached the events through here. So a
    stream `ledger.status` called `broken` was read happily by all of them and a write
    verb ran over it and exited 0. Two readers, two answers, and the strict one guarded
    a door nobody walked through.

    Kept as a name rather than deleted: `read_events` is what four modules say, and the
    thing worth changing was which code it runs, not how many call sites spell it.
    """
    return ledger.read_stream(project)


# ---------------------------------------------------------------- state.sessions[]


def store(project, rec, event=None, live=()):
    """Append or update this session's envelope in `state.sessions[]`.

    There is deliberately NO write verb for this. Phase 7's rule is that an AGENT
    changes machine state only through a verb; the envelope is the orchestrator's
    record of a dispatch it performed, and an agent that could write it could forge
    its own provenance — which is the whole defect F-0159 named.

    `live` names the session ids that are RUNNING RIGHT NOW. Opening a dispatch closes
    every other open envelope as abandoned, which is right when the only way to find one
    is that a previous orchestrator died mid-session — and wrong the moment two auditor
    passes run concurrently (phase 11), where each would declare the other crashed
    while it was still working. The parallel dispatcher is the one caller that knows
    which sessions are alive, so it is the one caller that passes this; every other
    caller keeps the previous behaviour exactly.

    Returns the new `state_revision`, or None when there is no readable state."""
    audit = Path(project) / "audit"
    # PHASE 2 (sixth audit). This is a read-modify-write of canonical state with NO lock
    # of any kind: `write._apply` at least held `audit/.lock`, which excludes another
    # process. Two auditor passes running as threads both loaded `sessions[]`, both
    # appended their own, and the second write erased the first — measured as two of
    # four events reaching the stream while `ledger status` still said `intact`.
    # The window covers the LOAD as well as the write, because two readers of one
    # revision overwrite each other no matter how atomic each write is on its own.
    with serialized():
        try:
            state = load_state(audit)
        except StateError as e:
            # A project with no readable state cannot be dispatched by `next`/`loop` at
            # all — this path is reachable only by calling the runner directly (a test, a
            # manual harness). Say so out loud rather than aborting a session that is
            # already under way for a condition its own dispatcher already refuses.
            print(f"note: envelope not stored in state.json ({e}); audit/{EVENTS_FILENAME} "
                  f"still carries this session")
            if event:
                emit(project, event, rec.get("session_id"), **_event_payload(rec))
            return None

        sessions = [dict(s) for s in state.sessions]
        if rec.get("outcome") is None:
            live = set(live) | {rec.get("session_id")}
            sessions = [s if s.get("session_id") in live else _abandon(s, project)
                        for s in sessions]
        for i, s in enumerate(sessions):
            if s.get("session_id") == rec["session_id"]:
                sessions[i] = rec
                break
        else:
            sessions.append(rec)
        # `freeze`, not `tuple`: `replace()` bypasses `_build`, so without this the
        # returned State would hold plain dicts again — immutable on load, mutable
        # here, which is the invariant holding only where nobody was looking.
        # PHASE 3 (sixth audit). This write bumps the revision, and until now it left
        # `ledger_anchor` alone — so recovery, which read the anchor, saw a stale number
        # and DELETED the `session_finished` of a session that really had finished. The
        # write now commits an identity, and the event carries the same one, so a failed
        # append is recognised as belonging to a transaction that landed.
        txn = ledger.new_txn()
        rev = write_state(audit, replace(state, sessions=freeze(sessions)), txn=txn)
        if event:
            emit(project, event, rec.get("session_id"), state_revision=rev, txn=txn,
                 **_event_payload(rec))
        return rev


def _abandon(rec, project):
    """Close an envelope the orchestrator never saw end.

    Reachable when a previous run was killed between `enter` and `finish` (SIGKILL,
    a power cut, `kill -9` on the orchestrator itself). The record is closed as
    `crash` and `exit_status` stays NULL, because no exit status was ever observed and
    inventing one — `-1`, `127`, anything — would put a fact in the provenance record
    that nobody witnessed."""
    if rec.get("outcome") is not None:
        return rec
    rec = dict(rec)
    rec["outcome"] = "crash"
    rec["note"] = ("the orchestrator was never able to finalise this dispatch — no exit "
                   "status, duration or log digest was observed")
    emit(project, "session_abandoned", rec.get("session_id"), role=rec.get("role"))
    return rec


def _event_payload(rec):
    keep = ("role", "provider", "agent_model", "executable_version", "policy_digest",
            "sandbox_profile", "trust_level", "subject_commit", "subject_manifest",
            "sandbox_seed",
            "controller_commit", "capsule_digest",
            "charter_hash", "prompt_hash", "head_before", "head_after",
            "duration_s", "exit_status", "outcome", "log_digest", "tokens")
    return {k: rec[k] for k in keep if rec.get(k) is not None}


def session_moved(project, session_id):
    """Has this session recorded a canonical state transition yet?

    The half of "useful action" that does not depend on how chatty an agent CLI is: a
    session that filed a finding has acted, whatever its log looks like. Read from the
    append-only stream, which every write verb appends to before the orchestrator's
    watchdog next wakes, so it is current without asking the session anything.

    Never raises: a watchdog that dies reading its own evidence would leave the session
    running unbounded, which is the failure this whole mechanism exists to end."""
    try:
        declared = declared_verbs()
        return any(e.get("event") == "state_transition"
                   and e.get("session_id") == session_id
                   and e.get("verb") in declared
                   for e in read_events(project))
    except Exception:
        return False


def open_dispatch(state):
    """The in-flight dispatch (an envelope with no `outcome`), or None.

    This is what makes `fixed-by` structural: the value is READ from the dispatch the
    orchestrator recorded, not taken from the agent's argv.

    Phase 11 lets a DECLARED concurrent group hold several dispatches open at once, and
    that is precisely when this question stops having one answer — so it refuses instead
    of returning the first, which would bind a fix to whichever record happened to sort
    earliest. Concurrency is only permitted for auditor passes, which record no fix, so
    a caller meeting this refusal is doing something the group was never opened for."""
    live = [s for s in getattr(state, "sessions", ()) if s.get("outcome") is None]
    if len(live) > 1:
        raise StateError(
            f"{len(live)} dispatches are open at once "
            f"({', '.join(s['session_id'] for s in live)}, concurrent group "
            f"{live[0].get('concurrent_group')!r}) — provenance cannot be bound to one "
            f"of them. Wait for the concurrent passes to finish, then record the work.")
    return live[0] if live else None


def session_by_id(state, session_id):
    for s in getattr(state, "sessions", ()):
        if s.get("session_id") == session_id:
            return s
    return None


# ---------------------------------------------------------------- independence


def independence_level(fixer_env, verifier_env, fixer_id=None, verifier_id=None):
    """One of INDEPENDENCE_LEVELS for a (fixing, verifying) pair of envelopes.

    Honest ceiling, and it belongs in the code as much as in the docs: NONE of these
    levels proves the absence of shared training data, a shared cache, or the same
    human driving both sides. `cross-provider` means two different vendors ran the two
    sessions — that is all it means."""
    fid = (fixer_env or {}).get("session_id", fixer_id)
    vid = (verifier_env or {}).get("session_id", verifier_id)
    if fid and vid and fid == vid:
        return "same"
    if not fixer_env or not verifier_env:
        return "unrecorded"
    fp, vp = fixer_env.get("provider", UNKNOWN), verifier_env.get("provider", UNKNOWN)
    fm, vm = fixer_env.get("agent_model", UNKNOWN), verifier_env.get("agent_model", UNKNOWN)
    if UNKNOWN not in (fp, vp) and fp != vp:
        return "cross-provider"
    if fp == vp and UNKNOWN not in (fm, vm) and fm != vm:
        return "cross-model"
    return "same-provider-different-session"


def independence_note(level):
    """The one-line label a degraded level must carry wherever it is displayed."""
    if level == "cross-provider":
        return "different providers (does NOT prove independent training data or operator)"
    if level == "cross-model":
        return "same provider, different model — a weaker claim than cross-provider"
    if level == "same-provider-different-session":
        return "DEGRADED: same provider and model, only a different session"
    if level == "unrecorded":
        return "DEGRADED: no envelope for one side — independence is not measurable here"
    return "the same session — refused (§3: the Verifier is never the fixer)"


def command_of(conf, role):
    """The configured role command as a token list, for provider/model derivation
    without launching anything."""
    tmpl = conf.get(f"{role.lower()}_cmd") if conf else None
    return shlex.split(tmpl) if tmpl else []


def orchestrated_session_id():
    """This process's dispatched session id, when the orchestrator set one."""
    return os.environ.get("XCHECK_SESSION_ID") or None


# ---------------------------------------------------------------------------
# the receipt: what a session actually moved, as opposed to how its process ended
# ---------------------------------------------------------------------------

# The protocol answers, kept OUT of `OUTCOMES`. `OUTCOMES` says how the child process
# ended; this says whether the role did its job, and the defect being repaired is
# precisely that one field was being asked both questions. `classify_outcome` returns
# `ok` for any child that exits zero, so a session that wandered, refused in prose, or
# was refused by the lock came back indistinguishable from one that filed a finding.
PROTOCOL_RECORDED = "recorded"
PROTOCOL_NO_PROGRESS = "protocol-no-progress"
# The third answer. `protocol-no-progress` says the session moved nothing; this says it
# moved something that was not its charter's work. Until it existed, any permitted CLI
# command bought a session the status `recorded` — the audit's words — so a session that
# wandered off and changed a limit was indistinguishable from one that filed the finding
# it was dispatched for.
PROTOCOL_VIOLATION = "protocol-violation"

# Enumerated once. A protocol value that is not in here is refused rather than passed
# along: an unknown answer in this field reaches the loop, the printer and the event
# stream, none of which have a branch for it.
PROTOCOLS = frozenset({PROTOCOL_RECORDED, PROTOCOL_NO_PROGRESS, PROTOCOL_VIOLATION})

# What each role's charter work LOOKS LIKE in the record — the postcondition a receipt
# checks for. Verbs a role owns but that are not its charter's point (an Auditor
# admitting another role's construal, say) are legitimate and simply do not satisfy the
# charter on their own.
#
# `creates` are the verbs whose target is a record that does not exist yet: a finding the
# Auditor is filing, a pass it is queueing, a plan the Remediator is opening. Their
# targets cannot be checked against the charter's ids — the id is new — so they are
# bound by the ROLE's charter instead: the session was dispatched to file findings, and
# it filed one.
#
# `bound` verbs act on a record that already exists, so their target MUST be named in
# the charter. That is the half that catches a Remediator fixing a finding it was not
# given.
CHARTER_WORK = {
    "Planner": {"creates": {"propose-construal"}, "bound": set(),
                "names": "an operational construal for its own charter"},
    "Auditor": {"creates": {"file-finding", "queue-pass"},
                "bound": {"record-coverage"},
                "names": "a finding filed, a queued remainder, or the coverage report "
                         "for a pass in its charter"},
    "Triage": {"creates": set(), "bound": {"set-status"},
               "names": "a triage decision on a finding in its charter"},
    "Remediator": {"creates": {"record-plan", "file-finding"},
                   "bound": {"set-status", "record-fix", "record-refusal",
                             "block-on-norm"},
                   "names": "a plan, a class finding, or a status/fix/refusal/norm-gate "
                            "on a finding in its charter"},
    "Verifier": {"creates": set(), "bound": {"record-verdict", "record-refusal"},
                 "names": "a verdict or a typed refusal on a finding in its charter"},
}

_CHARTER_ID_RE = re.compile(r"\b(?:F|CF)-\d{4}\b|\bP-\d{2,}\b|\bRP-\d{4}\b")


def charter_targets(charter):
    """The record ids a charter names: findings, class findings, passes, plans.

    Read from the charter TEXT, which is what the orchestrator handed the session — not
    from `charter_hash`, which is a digest and cannot be reversed into a list. This is
    why the receipt takes the charter and the authorizer (one layer down) does not."""
    return frozenset(_CHARTER_ID_RE.findall(charter or ""))


def declared_verbs():
    """The verbs the orchestrator itself declares. Imported late: `write` imports this
    module, so a module-scope import here would be a cycle."""
    from xcheck.write import VERBS
    return frozenset(VERBS)


def receipt(events, session_id, declared=None, role=None, charter=None):
    """What `session_id` moved, derived from the event stream rather than reported.

    A session's own account of its work is testimony; the transitions it left behind are
    evidence. `state_transition` is emitted by `write._apply` AFTER the state write is
    durable, so a verb that refused emits nothing and cannot appear here.

    Only a verb the orchestrator DECLARES counts as progress (W-07). Two records in the
    Ouroboros-4 corpus carry verbs — `repair-body-paths`, `repair-p13-artifact-paths` —
    that appear nowhere in `write.VERBS`: a session imported `write._apply`, which takes
    `verb` as a free string, and `emit` never validates a payload. A gate that counted any
    transition would let a subject mint its own progress, which is the `rc=0` defect one
    level down. Undeclared records are REPORTED rather than dropped, because a bypass that
    leaves no trace in the receipt is a bypass nobody will notice.
    """
    declared = declared_verbs() if declared is None else frozenset(declared)
    moved, minted = [], []
    for e in events:
        if e.get("event") != "state_transition" or e.get("session_id") != session_id:
            continue
        row = {"verb": e.get("verb"), "target": e.get("target"),
               "state_revision": e.get("state_revision")}
        (moved if row["verb"] in declared else minted).append(row)

    work = CHARTER_WORK.get(role or "")
    ids = charter_targets(charter)
    satisfying = [r for r in moved
                  if work and (r["verb"] in work["creates"]
                               or (r["verb"] in work["bound"] and r["target"] in ids))]
    if not moved:
        protocol, met = PROTOCOL_NO_PROGRESS, None
    elif work is None:
        # No role or no charter to bind against — a direct caller, or a stream written
        # before the binding existed. The answer is the old two-value one, and the
        # receipt SAYS the binding was not applied rather than implying a check that
        # did not happen. `charter_hash` is in the envelope but a digest cannot be
        # reversed into a list of ids, so there is nothing to recover here.
        protocol, met = PROTOCOL_RECORDED, None
    elif satisfying:
        protocol, met = PROTOCOL_RECORDED, work["names"]
    else:
        # Authorized, and not the charter's work. This is the case the audit named: any
        # permitted command used to buy `recorded`.
        protocol, met = PROTOCOL_VIOLATION, None
    return {
        "session_id": session_id,
        "role": role,
        "transitions": moved,
        "undeclared": minted,
        "charter_targets": sorted(ids),
        "charter_bound": work is not None,
        "satisfying": satisfying,
        "postcondition_met": met,
        "protocol": protocol,
    }


def session_receipt(project, session_id, declared=None, role=None, charter=None):
    """`receipt()` over this project's own stream."""
    return receipt(read_events(project), session_id, declared, role=role,
                   charter=charter)
