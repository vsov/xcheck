"""The write verbs: the only way an agent changes machine state.

Phase 5 made `state.json` the one authority and phase 6 made the Markdown a
mirror that refuses to lie. Both are undone the moment an agent keeps editing
frontmatter by hand, because now that edit is not a *conflicting* write — it is
a **no-op**, which is worse. A tool whose product is a reliable record cannot
have a way to write the record that silently does nothing.

So there is exactly one path in, and it is this module. Every verb:

* **refuses an illegal transition, addressed** — naming what was asked, what the
  record actually says, and which §5 rule forbids it;
* **is atomic** — `write_state` then `write_views`, under the same lock every
  writing session takes, so state and mirror move together or not at all;
* **refuses a repeat** — re-running an applied transition is a refusal, not a
  silent second write. `set-status F-1 accepted` twice means somebody has lost
  track of the record, and telling them so is the useful answer.

**Invariants that used to be prompt text are enforced here.** Verifier ≠ fixer,
producer ≠ admitter, the `reopen_limit` stop, a class finding's non-empty
`members`. A rule stated in a prompt is advice; a rule enforced at the
transition is a guarantee. Where the norm gives the decision to a HUMAN, the
verb takes the human's recorded decision as an INPUT and never infers it — the
mechanisation moves the bookkeeping, not the judgement.

`VERBS` below is the single declaration of what exists: `--help` prints it, the
grammar parses from it, and `tests/test_doc_drift.py` compares it against what
XCHECK.md and the skills document, in both directions. A verb nobody documents
and a documented verb that does not exist are the same defect.
"""

import datetime
import hashlib
import os
import re
import subprocess
from pathlib import Path

from xcheck import ledger, util
from xcheck.envelope import (emit, event_line, independence_level, independence_note,
                             open_dispatch, session_by_id)
from xcheck.state import (CLASS_ID_RE, LIMIT_FIELDS, SHA256_RE, STATUSES, StateError,
                          next_revision,
                          _build, _validate, load_state, subject_commit,
                          to_document, write_state)
from xcheck.util import (GIT_TIMEOUT, REFUSAL_REASONS, SEVERITIES, TERMINAL,
                        construal_key, is_session_id)
from xcheck.views import write_views


def today():
    """The `updated` stamp every verb writes. One helper, because a verb that
    stamped a different way would make two records written on one day look like
    two different days to `_canon_calendar_date`."""
    return datetime.date.today().isoformat()


# XCHECK.md §5 lives in `util` — see `util.TRANSITIONS` and `util.RESERVED`. It moved
# there in phase 8 of the fifth-audit response, when `reconcile` became its second
# reader: the reconciler proposes commands an operator types, and a proposal the CLI
# would refuse is a bug. `reconcile` may not import this module (it must hold no write
# capability), so a table in here would have had to be copied, and two copies of a
# closed vocabulary drift. The ENFORCEMENT stays here; the vocabulary is data.

# The §5 stop flag, spelled once. Two spellings of it would mean a record flagged
# by one code path is invisible to the gate in another.
NEEDS_HUMAN = "⚠ needs-human"



class Verb:
    """One write verb's grammar and its one-line summary."""

    def __init__(self, positional, options, required, summary):
        self.positional = positional      # ("ID", "STATUS")
        self.options = options            # {"--next": "ROLE"}; None value => flag
        self.required = required          # ("--session", ...)
        self.summary = summary

    def usage(self, name):
        parts = [f"xcheck {name}"] + list(self.positional)
        for opt, meta in self.options.items():
            body = opt if meta is None else f"{opt} {meta}"
            parts.append(body if opt in self.required else f"[{body}]")
        return " ".join(parts)


VERBS = {
    "set-status": Verb(
        ("ID", "STATUS"), {"--next": "ROLE", "--retake": "human:LABEL"}, (),
        "the §5 lifecycle transition; refuses anything the §5 table does not allow"),
    "record-fix": Verb(
        ("ID",), {"--session": "16-HEX", "--plan": "RP-NNNN", "--scope": "TEXT"},
        ("--session",),
        "planned -> fixed, recording WHICH session did it (§7 provenance)"),
    "record-verdict": Verb(
        ("ID",), {"--verdict": "closed|reopened", "--session": "16-HEX",
                  "--reason": "TEXT"}, ("--verdict", "--session"),
        "the Verifier's binding act on a fixed finding; enforces verifier != fixer"),
    "admit-construal": Verb(
        ("KEY",), {"--admitter": "human:LABEL|16-HEX"}, ("--admitter",),
        "§4 rule 9 admission; enforces producer != admitter"),
    "file-finding": Verb(
        (), {"--id": "F-NNNN|CF-NNNN", "--title": "TEXT",
             "--severity": "|".join(sorted(SEVERITIES)),
             "--dimension": "KEY", "--unit": "U01,U02", "--pass": "P-NN",
             "--body": "PATH", "--locator": "PATH|PATH:LINE|PATH:START-END",
             "--source-hash": "SHA256", "--recurrence-of": "F-NNNN",
             "--members": "F-1,F-2", "--session": "16-HEX"},
        # `--locator`/`--source-hash` are required for an ORDINARY finding and refused
        # for a class one, so the requirement lives in the verb rather than in this
        # grammar: a class finding's evidence is its members' locators plus the census
        # in its body, and one line:number would name one instance of a pattern.
        ("--id", "--title", "--severity", "--dimension", "--unit", "--pass", "--body"),
        "record a new finding at `reported` and link its evidence body; with a CF id "
        "and --members it mints the §8 class, absorbs them in one transition, and "
        "records --session as the census author"),
    "propose-construal": Verb(
        ("KEY",), {"--role": "ROLE", "--charter": "TEXT", "--session": "16-HEX",
                   "--body": "PATH"},
        ("--role", "--charter", "--session", "--body"),
        "register a §4 rule 9 construal at 'proposed'; never admits it"),
    "record-plan": Verb(
        ("RP-ID",), {"--findings": "F-1,F-2", "--body": "PATH"},
        ("--findings", "--body"),
        "register a remediation plan and the findings it covers"),
    "queue-pass": Verb(
        ("P-ID",), {"--dimension": "KEY", "--units": "U01,U02", "--charter": "TEXT",
                    "--stop": "TEXT", "--base": "REV"},
        ("--dimension", "--units", "--charter", "--stop"),
        "append a pass to the queue (the Auditor's uncovered remainder, §4 rule 5)"),
    "record-refusal": Verb(
        ("ID",), {"--reason": "|".join(sorted(REFUSAL_REASONS)), "--clear": None},
        (),
        "a §5 typed refusal on a finding charter; never touches the status"),
    "block-on-norm": Verb(
        ("ID",), {}, (),
        "raise the §8 rule 8 norm-ratification gate; fail-closed until a ruling"),
    "record-ruling": Verb(
        ("ID",), {"--norm": "N1|N1-over-N4", "--ruled-by": "human:LABEL"},
        ("--norm", "--ruled-by"),
        "the norm owner's §8 ruling — the only thing that lifts that gate"),
    "record-coverage": Verb(
        ("P-ID",), {"--report": "PATH", "--findings": "F-1,F-2"}, ("--report",),
        "bind a pass to its ONE canonical coverage report (§4 rule 5)"),
    "render-views": Verb(
        (), {}, (),
        "re-render LEDGER.md and every frontmatter block from state.json"),

    # 0.9.0 phase 3 — the ADMIN verbs. Until now README §13 told the operator to
    # edit `state.json` by hand and re-render, which walks around the lock, the
    # atomic writer, `state_revision`, the event stream and immediate validation
    # — every guarantee this module exists to give. Typed verbs, deliberately not
    # a generic `edit-state`: a verb that can express any change cannot refuse an
    # illegal one BY NAME, and naming the refusal is the whole value of the rest.
    "set-limit": Verb(
        ("KEY", "VALUE"), {}, (),
        "change one limit in state.json (the human gates are limits — see §13)"),
    "amend-pass": Verb(
        ("P-ID",), {"--dimension": "KEY", "--units": "U01,U02", "--charter": "TEXT",
                    "--stop": "TEXT"}, (),
        "amend a QUEUED pass; refuses a done pass, whose report already certified it"),
    "cancel-pass": Verb(
        ("P-ID",), {}, (),
        "drop a queued pass; refuses a done pass and one findings still point at"),
    "update-catalog": Verb(
        ("KIND", "KEY"),
        {"--source": "TEXT", "--scope": "TEXT", "--catches": "TEXT",
         "--norms": "N1,N2", "--material": "TEXT", "--size": "TEXT",
         "--responsibility": "TEXT", "--remove": None}, (),
        "add, amend or remove a norm/dimension/unit in the AUDIT.md catalogs"),
}


def help_text():
    """The verb block `--help` prints. One source, so the help cannot go stale."""
    out = ["Write verbs (the only way machine state changes):"]
    for name, v in VERBS.items():
        out.append(f"  {v.usage(name)}")
        out.append(f"      {v.summary}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# grammar
# ---------------------------------------------------------------------------

def parse(name, argv):
    """`argv` after the verb word -> {positional lowercased: value, option: value}.

    Hand-rolled like the rest of the CLI (README §8): every rejection names the
    offending token. A missing option VALUE is the F-0121 shape — bound the read
    rather than letting `argv[i+1]` throw an IndexError at the user.
    """
    verb = VERBS[name]
    got, positional, i = {}, [], 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            if a not in verb.options:
                raise SystemExit(
                    f"xcheck {name}: unknown option {a} (usage: {verb.usage(name)})")
            if verb.options[a] is None:
                got[a] = True
            else:
                i += 1
                if i >= len(argv):
                    raise SystemExit(f"xcheck {name}: {a} requires a "
                                     f"{verb.options[a]} value (usage: {verb.usage(name)})")
                got[a] = argv[i]
        else:
            positional.append(a)
        i += 1
    if len(positional) != len(verb.positional):
        raise SystemExit(
            f"xcheck {name}: expected {len(verb.positional)} positional argument(s) "
            f"{' '.join(verb.positional) or '(none)'}, got {len(positional)} "
            f"{positional} (usage: {verb.usage(name)})")
    for opt in verb.required:
        if opt not in got:
            raise SystemExit(f"xcheck {name}: {opt} is required "
                             f"(usage: {verb.usage(name)})")
    got.update(dict(zip((p.lower() for p in verb.positional), positional)))
    return got


# ---------------------------------------------------------------------------
# the one write path
# ---------------------------------------------------------------------------

def _record(doc, fid):
    """The finding or class-finding dict with this id, and which list it is in."""
    for key in ("findings", "class_findings"):
        for r in doc[key]:
            if r["id"] == fid:
                return r, key
    known = sorted(r["id"] for k in ("findings", "class_findings") for r in doc[k])
    raise StateError(f"no record {fid!r} in the state document "
                     f"({len(known)} exist: {', '.join(known[:8])}"
                     f"{', …' if len(known) > 8 else ''})")


def _status_of(doc, target):
    """`target`'s status in this document, or None if it has none.

    Deliberately total: `target` may be a finding id, a construal key, a pass id or
    nothing at all, and this is called on the event path where a lookup failure must
    not turn a completed, durable write into a traceback.
    """
    if not target:
        return None
    for key in ("findings", "class_findings"):
        for r in doc.get(key) or ():
            if r.get("id") == target:
                return r.get("status")
    return None


class StaleRevision(StateError):
    """A write whose reader saw revision X arrived when state is already at Y.

    Carries both numbers because the caller's only correct response is to re-read and
    re-derive its change from the state that is actually there — and it cannot do that
    without knowing what it is now behind.
    """

    def __init__(self, expected, actual, what="this write"):
        super().__init__(
            f"refusing {what}: it was computed against state revision {expected}, and "
            f"state.json is now at revision {actual}. Nothing was written — another "
            f"writer got here first, and overwriting it would discard work that is "
            f"already durable. Re-read and re-apply against the current state.")
        self.expected, self.actual = expected, actual


def _apply(project, mutate, role="write", verb=None, target=None, expect_revision=None,
           lock=None):
    """Load, check the mirrors, mutate the document, re-validate, write both.

    The re-validation is not belt-and-braces: a verb builds a dict, and the ONLY
    definition of a legal document is `_validate`. Writing without it would give
    the verbs a second, weaker notion of validity — the F-0147 shape with the
    arrow reversed.

    `expect_revision` is phase 11's optimistic concurrency check, and it lives HERE
    rather than in the parallel dispatcher on purpose: a second writer with its own
    load-mutate-write would be a second write path, which is the failure this module
    exists to prevent. Raised inside the lock and before the mutation, so a refused
    merge writes nothing at all.

    `lock` is an ALREADY-ACQUIRED lock this write happens inside. 0.9.1 phase 8 found
    why it has to exist: `cmd_next` holds the writing lock across the whole
    next-transaction (F-0009), and the parallel branch then merges each pass through
    here — which tried to acquire the same lock a second time and refused its own
    process with "audit/.lock is held". The branch had never been run end to end
    because it was banned, so the deadlock was invisible until the ban lifted.

    Handing the held lock in is narrower than making the lock re-entrant: re-entrancy
    would make a genuine double-acquire silently legal everywhere, while this says
    exactly "the caller is holding it, and here it is". An unacquired lock object is
    NOT trusted — this acquires its own, so the failure mode is a refusal rather than
    an unlocked write.
    """
    from xcheck.runner import Lock
    from xcheck.util import serialized
    from xcheck.views import refuse_on_drift

    audit = Path(project) / "audit"
    inherited = lock if getattr(lock, "token", None) is not None else None
    if inherited is None:
        lock = Lock(audit, role)
        lock.acquire()
    # PHASE 2 (sixth audit). `Lock(audit, role)` above is the `audit/.lock`
    # DIRECTORY: a mutex against another PROCESS, and blind to a second THREAD of
    # this one. `parallel.run_passes` merges on the main thread while worker threads
    # are still storing envelopes for the passes that have not finished, so both
    # writers were inside this read-modify-write at once. The in-process window goes
    # around the WHOLE transaction, not just the write: locking only `write_state`
    # still lets two readers of one revision overwrite each other.
    with serialized():
        try:
            # PHASE 5 (fifth audit): a write over a stream that is not what it claims to be
            # is refused HERE, before anything is mutated — INCLUDING before the outbox replay
            # below, because a replay appends, and appending is the act this refusal
            # exists to prevent. Replaying is not reading: the previous version proceeded
            # happily over a ledger `ledger.status` called broken, appending a new event onto
            # a prefix under suspicion and giving the edit a valid successor.
            #
            # PHASE 4 (sixth audit): and `read_stream` was only half the question. It proves
            # the chain links up; it does not ask whether this is the stream the checkpoint
            # in `state.json` was taken over. A truncated journal and a rebuilt-and-relinked
            # one both passed it, this verb exited 0, a NEW checkpoint was written over the
            # old one and the diagnostic then reported `intact` — corrupted history promoted
            # to baseline by the act of writing. The check is now the same one `status`
            # makes, and it names which of the four states refused.
            try:
                ledger.require_complete(project, ledger.committed_anchor(project),
                                        action="write")
            except ledger.IncompleteStream as e:
                raise SystemExit(
                    f"refusing the transition: the event stream is not a record this tool "
                    f"can stand behind ({e.state}).\n    {e.detail}\n"
                    f"{ledger.RECOVERABLE[e.state]}\n"
                    f"Canonical state was NOT changed, and the checkpoint it carries was "
                    f"NOT advanced. `xcheck recover` reports the stream's state without "
                    f"writing to it.") from None
            # PHASE 7. Any run that is about to write first finishes the last one's unfinished
            # append. Here rather than in a command an operator has to know about: a stream
            # that lost an append heals on the next verb, under the lock that makes the heal
            # safe, and `replay` is idempotent so doing it on every write costs a stat when
            # the outbox is empty.
            appended, _already, discarded = ledger.replay(project)
            if appended:
                print(f"note: replayed {appended} event(s) from audit/"
                      f"{ledger.OUTBOX_FILENAME} that a previous run could not append.")
            for why in discarded:
                # PHASE 4 (fifth audit): a reserved event whose transition never committed is
                # dropped HERE, before this verb reserves anything of its own — and said out
                # loud. Left in place it would be replayed by the next `recover --apply` as a
                # transition that did not happen; removed quietly it would be an audit trail
                # editing itself.
                print(f"note: discarded a reserved event that never committed — {why}")
            state = refuse_on_drift(load_state(audit), audit)
            if expect_revision is not None and state.state_revision != expect_revision:
                raise StaleRevision(expect_revision, state.state_revision,
                                    f"the {verb or role} merge")
            doc = to_document(state)
            before = _status_of(doc, target)
            result = mutate(doc, state)
            # A verb may return `(message, extras)`. `extras` is STRUCTURED metadata about
            # the transition that belongs in the event stream and not in the record: the
            # Verifier's reopen reason is the case that forced it. Storing it as a
            # canonical §2 field would make every finding carry a field that is meaningful
            # for one transition; leaving it in the message would mean the metric that
            # needs it has to parse prose, which is the whole class this rebuild removed.
            message, extras = result if isinstance(result, tuple) else (result, {})
            # PHASE 8. The anchor is taken ONCE, here, and written into the document this
            # transition persists: the chain head and the event count as of the moment before
            # this transition's own event is appended. Both halves from the same read, so a
            # later reader comparing them is comparing one instant and not two.
            doc["ledger_anchor"] = dict(ledger.snapshot(project),
                                        state_revision=next_revision(state))
            _validate(doc, "the document this verb would write")
            new_state = _build(doc)
            # PHASE 9: `git_head(project)` here is the SANDBOX's seed commit whenever a
            # session is running — that is how `130646f4…`, a commit in no branch and no tag,
            # became this audit's recorded provenance. `subject_commit` reads the outer
            # commit the orchestrator exported and falls back to the ambient answer, which is
            # the right one for the operator running a verb at their own terminal.
            # PHASE 7 (fourth audit). RESERVE before the state write. The event line is made
            # durable in the outbox and the ledger is proved appendable HERE, while nothing
            # has happened yet — so an events file that cannot be written refuses the
            # transition instead of applying it into a stream that will not record it.
            # `ledger`'s module docstring carries the policy and the trade-off it answers.
            line = event_line("state_transition", envelope_session(state),
                              verb=verb or role, target=target,
                              state_revision=next_revision(new_state),
                              from_status=before, to_status=_status_of(doc, target),
                              summary=message.splitlines()[0], **extras)
            # PHASE 3 (sixth audit). One identity, minted here, handed to BOTH halves:
            # the reservation carries it and the state write commits it, inside the same
            # `os.replace`. Recovery then asks "did THIS transaction land?" instead of
            # "is the number big enough?", which is the question that let an unrelated
            # `true` session make a failed transition replayable.
            txn = ledger.new_txn()
            reserved = ledger.reserve(project, line, txn)
            revision = write_state(audit, new_state, head_before=subject_commit(project),
                                   txn=txn)
            write_views(_build(doc), audit)
            # Phase 9: one event per APPLIED transition — emitted after the write is
            # durable, so the stream never claims a change that did not land. The verb
            # that refused emits nothing: a refusal changed no state, and an event log
            # of things that did not happen is how a record stops being evidence.
            #
            # 0.9.0 phase 10: `from_status`/`to_status` are read off the record itself,
            # before and after the mutation. Two pre-registered metrics are questions
            # about util.TRANSITIONS, not about the current record — a false closure is a
            # `closed` that stopped being closed, and the state document by then says only
            # `reopened`. Both are null for a target that carries no status (a construal
            # key, a pass id) and for a verb that names no target at all.
            # COMMIT. The state write is durable, so a failure here must not undo it: the
            # outbox entry survives and the next run that writes replays it. That is the
            # exact case the old `except OSError` was written for, recovered instead of
            # printed.
            assert revision == reserved["line"]["state_revision"], (
                "the reserved event names a revision the write did not produce")
            ledger.commit(project, reserved)
            print(f"{message} (state revision {revision})")
            return 0
        finally:
            # Release only what this call acquired. Releasing an inherited lock would end
            # the caller's transaction from inside one of its steps.
            if inherited is None:
                lock.release()


def envelope_session(state):
    """The id of the dispatch this write happened inside, or None when hand-run."""
    d = open_dispatch(state)
    return d["session_id"] if d else None


def held_lock(project):
    """The lock this process is entitled to write under, or None to acquire one.

    The protocol and this CLI used to contradict each other. §4 rule 8 tells a session
    to take `audit/.lock` and hold it for the whole session; an orchestrated child is
    told instead that its parent already holds it and handed it the nonce. Either way
    the lock is held for the duration — and `run()` then called `_apply()` with no lock
    at all, so every verb tried to acquire again and was refused by the very lock its
    own session was obeying. A session that followed the documented protocol could not
    record anything through the supported path.

    Measured on the Ouroboros-4 corpus before this existed: 130 of the 136 sessions that
    produced nothing were refused this way while trying to write down what they had
    found, and 7 of the 15 that succeeded did so by importing `_apply` and passing the
    lock object the CLI had no way to hand it.

    `XCHECK_LOCK_INHERITED` is the nonce of that lock. The orchestrator exports it to
    every child it launches (F-0093) and repeats it in the role prompt, because process
    env does not reach a sandboxed command runner's shell on every platform; a standalone
    session exports the nonce it wrote itself.

    Fail closed, and deliberately in the same shape the role skills already describe: a
    nonce that names no on-disk owner, or names a different one, is a FOREIGN lock and is
    refused here rather than being allowed to write beside its holder. Naming the right
    nonce is not a privilege escalation — the holder is the only party that knows it, and
    a caller who does not name one is left exactly where it was, acquiring for itself.
    """
    nonce = os.environ.get("XCHECK_LOCK_INHERITED", "").strip()
    if not nonce:
        return None
    from xcheck.runner import Lock
    lock = Lock(Path(project) / "audit", "inherited")
    info = lock.read()
    if not info:
        raise SystemExit(
            f"XCHECK_LOCK_INHERITED={nonce} says a writing lock is held around this "
            f"command, but audit/.lock has no readable owner record. Refusing rather "
            f"than acquiring: the signal and the lock disagree, and writing under the "
            f"wrong one is how two sessions end up editing the same state.")
    if info.get("nonce") != nonce:
        raise SystemExit(
            f"XCHECK_LOCK_INHERITED={nonce} does not match the owner of audit/.lock "
            f"({info}). That lock belongs to someone else; this command would be "
            f"writing beside a live writer. Wait for it, or clear it with `xcheck "
            f"unlock --force` once you are sure no writing session is active.")
    # `.token` is what `_apply` reads to mean "inherited": it will neither acquire nor
    # release. Setting it without acquiring is the whole point — the acquire already
    # happened, in the process that owns the release.
    lock.token = nonce
    return lock


# ---------------------------------------------------------------------------
# authorization: role x verb x target state -> allow / refuse
# ---------------------------------------------------------------------------

# Derived by EXECUTION, not by annotation: `scratchpad/derive_routes.py` ran all 17
# verbs against a fixture and diffed canonical state, and the `touches` column below is
# what each one actually moved. That distinction has cost this project before — a route
# annotated as having a write path turned out to have none — so the column is a
# measurement, and the transcript of the run is in the phase notes.
#
#   verb               touches
#   set-status         findings, state_revision
#   record-fix         findings, state_revision
#   record-verdict     findings, state_revision
#   admit-construal    construals, state_revision
#   file-finding       findings, state_revision
#   propose-construal  construals, state_revision
#   record-plan        plans, state_revision
#   queue-pass         queue, state_revision
#   record-refusal     findings, state_revision
#   record-coverage    queue, state_revision
#   block-on-norm      class_findings, state_revision
#   record-ruling      class_findings, state_revision
#   set-limit          limits, state_revision
#   amend-pass         queue, state_revision
#   cancel-pass        queue, state_revision
#   update-catalog     catalogs, state_revision
#   render-views       (nothing — it rewrites views FROM state and moves no record)
#
# WHO may run each is §3's role cards, transcribed. A role card is a capability list;
# until this table existed it was a recommendation, which is F-0069: the transition
# graph was checked and the ACTOR was not, so an open Auditor session performed a
# human-owned transition and nothing objected.
#
# `to` (where given) narrows a verb further by the status it moves a record INTO. That
# is what makes the Auditor's `set-status` grant safe: §3 gives the Auditor `set-status`
# only as agent-as-pen for the human's `disputed` resolution, so the grant is
# `disputed -> accepted|withdrawn` and nothing else.
ROLE_ROUTES = {
    "Planner": {
        "propose-construal": {},
    },
    "Auditor": {
        "file-finding": {},
        "record-coverage": {},
        "queue-pass": {},
        # agent-as-pen for the human's disputed resolution (§5, §9 rule 3) — never on
        # the Auditor's own judgement, which is why the from/to are pinned.
        "set-status": {"from": {"disputed"}, "to": {"accepted", "withdrawn"}},
        "propose-construal": {},
        "admit-construal": {},
    },
    "Triage": {
        "set-status": {"from": {"reported", "deferred"},
                       "to": {"accepted", "rejected", "deferred"}},
    },
    "Remediator": {
        "record-plan": {},
        "set-status": {"to": {"validated", "planned", "disputed", "obsolete",
                              "superseded-by-class"}},
        "record-fix": {},
        "record-refusal": {},
        "file-finding": {},
        "block-on-norm": {},
        "propose-construal": {},
        "admit-construal": {},
    },
    "Verifier": {
        "record-verdict": {},
        "record-refusal": {},
        "propose-construal": {},
        "admit-construal": {},
    },
}

# The operator's surface, and the human's. Not a role: these run with NO dispatch open,
# which is exactly what distinguishes "the person who owns this machine typed it" from
# "a session the orchestrator launched ran it". `record-ruling` is here because §8 rule 8
# gives the ruling to the norm OWNER — the human — in a gate distinct from Triage.
ADMIN_VERBS = frozenset({"set-limit", "amend-pass", "cancel-pass", "update-catalog",
                         "record-ruling"})

# `render-views` moves no record at all (see the derived table above): it rewrites the
# views FROM state and is the declared repair for view drift. Refusing it inside a
# session would leave a role that noticed drift with no way to repair it and no record
# to corrupt by repairing it.
UNRESTRICTED_VERBS = frozenset({"render-views"})


class WriteRefused(StateError):
    """A write the dispatch's role has no capability for. A subclass of StateError so
    every existing caller keeps reporting it as the addressed refusal it is, and a
    distinct type so a test can tell "the authorizer refused" from "the verb refused"."""


def _status_now(state, target):
    """The status a target record is in RIGHT NOW, or None when there is no such record.

    Read from canonical state, never from the arguments: the whole point is that the
    check does not believe the caller about what it is doing."""
    if not target:
        return None
    for rec in list(getattr(state, "findings", ())) + list(
            getattr(state, "class_findings", ())):
        if rec.id == target:
            return rec.status
    return None


def _owners(verb, to_status, have=None):
    """Which roles (plus the operator) may run `verb` on a record that is `have` now and
    would become `to_status`.

    Computed from the table rather than restated, so the refusal cannot name an owner
    the table does not actually grant — and it filters on BOTH halves of a rule. The
    first version filtered only on `to`, which made the F-0069 refusal read "a
    'reported' finding is owned by Auditor, Triage": the Auditor's grant reaches
    `accepted`, but only from `disputed`, so naming it as an owner of a `reported`
    finding sent the reader back to the role that had just been refused."""
    out = [role for role, verbs in ROLE_ROUTES.items()
           if verb in verbs
           and (not verbs[verb].get("to") or to_status in verbs[verb]["to"])
           and (not verbs[verb].get("from") or have is None
                or have in verbs[verb]["from"])]
    if verb in ADMIN_VERBS or to_status is None:
        out.append("the operator (no dispatch open)")
    return out or ["the operator (no dispatch open)"]


def authorize_dispatch_write(dispatch, verb, args, state):
    """The single authorization point: role x verb x target x current state.

    Called once, at the `run()` boundary, above `_apply`. Deliberately NOT spread over
    the 17 handlers — the audit's own words: a check in seventeen places is a check
    that will be missing from the eighteenth.

    `dispatch` is the orchestrator's OPEN envelope, or None. None means no session is
    running, which is the operator at their own terminal: the admin verbs are theirs,
    and so is everything else — a human with shell access on the machine that holds the
    audit is not a threat model this can address, and pretending otherwise would only
    stop them recording work honestly.

    What this does NOT do, stated rather than implied: it does not bind a write to the
    dispatch's CHARTER. The envelope records `charter_hash`, a digest, and a set of
    finding ids cannot be recovered from a hash — so "the Remediator wrote to a finding
    outside its charter" is not decidable here. That is the receipt's job (the next
    phase), and until it lands the charter half of `role x charter x verb x target` is
    unenforced. Saying so is the point: an authorizer that quietly checks four things
    while its name promises five is worse than one that checks four and admits it.
    """
    if verb in UNRESTRICTED_VERBS:
        return
    if dispatch is None:
        return                                   # the operator, at their own terminal
    role = dispatch.get("role")
    target = (args.get("id") or args.get("key") or args.get("pass")
              or args.get("p-id") or args.get("rp-id"))
    to_status = args.get("status")
    routes = ROLE_ROUTES.get(role, {})
    if verb in ADMIN_VERBS:
        raise WriteRefused(
            f"`{verb}` is an administrative verb and the {role} session {dispatch['session_id']} "
            f"may not run it. It changes the operating parameters of the audit rather "
            f"than a finding — {', '.join(_owners(verb, None))} owns that surface. A "
            f"session that could widen its own limits is not bounded by them. Run it "
            f"yourself, outside a dispatch.")
    if verb not in routes:
        raise WriteRefused(
            f"the {role} session {dispatch['session_id']} may not run `{verb}`. §3 gives "
            f"the {role} these verbs: {', '.join(sorted(routes)) or '(none)'}. "
            f"`{verb}` belongs to {', '.join(_owners(verb, to_status))}. A role card is a "
            f"capability list, not a recommendation.")
    rule = routes[verb]
    have = _status_now(state, target)
    if rule.get("from") and have is not None and have not in rule["from"]:
        raise WriteRefused(
            f"the {role} session {dispatch['session_id']} may not run `{verb}` on "
            f"{target}, which is {have!r}. §3 grants the {role} this verb only from "
            f"{', '.join(sorted(rule['from']))} — a {have!r} finding is owned by "
            f"{', '.join(_owners(verb, to_status, have))}.")
    if rule.get("to") and to_status is not None and to_status not in rule["to"]:
        raise WriteRefused(
            f"the {role} session {dispatch['session_id']} may not move {target} to "
            f"{to_status!r}. §3 grants the {role} `{verb}` into "
            f"{', '.join(sorted(rule['to']))}; {to_status!r} is owned by "
            f"{', '.join(_owners(verb, to_status, have))}. This is F-0069: the transition "
            f"graph allows it and the ACTOR does not.")


def run(project, name, argv):
    args = parse(name, argv)
    args["audit_dir"] = Path(project) / "audit"
    if name == "render-views":
        from xcheck.cli import cmd_render_views
        return cmd_render_views(Path(project))
    # Authorization happens HERE, before the lock and before any handler: the one
    # boundary every write verb passes through. A refusal is recorded in the event
    # stream as a typed event rather than only raised, because "a session tried
    # something its role does not have" is exactly the kind of thing an operator wants
    # to find afterwards, and an exception that reached a terminal nobody was watching
    # is not a record.
    audit = Path(project) / "audit"
    state = load_state(audit)
    dispatch = open_dispatch(state)
    try:
        authorize_dispatch_write(dispatch, name, args, state)
    except WriteRefused as e:
        emit(project, "write_refused",
             dispatch["session_id"] if dispatch else None,
             verb=name, role=(dispatch or {}).get("role"),
             target=(args.get("id") or args.get("key") or args.get("pass")
                     or args.get("p-id") or args.get("rp-id")),
             reason=str(e))
        raise
    # PHASE 9: `file-finding` validates its locator against the SUBJECT tree, so it is
    # the one verb that needs the project. Passed explicitly to that verb rather than to
    # every mutator: a mutator that can reach the filesystem is a mutator that can be
    # made to depend on it, and the rest of them are pure functions of the document.
    fn = _VERB_FN[name]
    mutate = ((lambda doc, state: fn(doc, state, args, project))
              if name in _PROJECT_VERBS else (lambda doc, state: fn(doc, state, args)))
    return _apply(project, mutate,
                  verb=name, lock=held_lock(project),
                  target=(args.get("id") or args.get("key") or args.get("pass")
                          or args.get("p-id") or args.get("rp-id")))


# ---------------------------------------------------------------------------
# the verbs
# ---------------------------------------------------------------------------

def _touch(rec):
    rec["updated"] = today()


def _set_status(doc, state, args):
    fid, want = args["id"], args["status"]
    if want not in STATUSES:
        raise StateError(f"{want!r} is not a canonical §5 status "
                         f"(known: {', '.join(sorted(STATUSES))})")
    rec, _ = _record(doc, fid)
    have = rec["status"]
    if have == want:
        raise StateError(
            f"{fid} is already {have!r}. Re-running an applied transition is refused, "
            f"not repeated: a second write would look like a second decision in the "
            f"history when nothing decided anything.")
    if have in TERMINAL and have != "superseded-by-class":
        raise StateError(
            f"{fid} is {have!r}, which is terminal and immutable (§5). A recurrence "
            f"enters as a NEW finding tagged `recurrence-of: {fid}` (§3 dedup) — "
            f"`xcheck file-finding --recurrence-of {fid} …` — never as a transition "
            f"of this one.")
    allowed = util.TRANSITIONS.get(have, set())
    if want not in allowed:
        raise StateError(
            f"{fid} is {have!r} and §5 allows only {sorted(allowed) or 'no transition'} "
            f"from there — {want!r} is not among them.")
    if (have, want) in util.RESERVED:
        verb = util.RESERVED[(have, want)]
        raise StateError(
            f"{have!r} -> {want!r} is a legal §5 transition but `set-status` will not "
            f"perform it: it carries provenance this verb cannot record. Use "
            f"`xcheck {verb}` — {VERBS[verb].summary}.")
    if rec.get("next") == NEEDS_HUMAN:
        # §5 reopen_limit: the automatic cycle already stopped on this record. The
        # flag is not scenery a later transition may walk past — clearing it is a
        # HUMAN's re-take, and the verb takes that decision as an input rather than
        # inferring it from the fact that somebody typed a transition.
        retake = args.get("--retake", "")
        if not retake.startswith("human:") or len(retake) <= len("human:"):
            raise StateError(
                f"{fid} is flagged {NEEDS_HUMAN!r} after {rec.get('attempts', 0)} "
                f"reopens (§5 reopen_limit): the automatic cycle stopped and only a "
                f"human re-take restarts it. Re-run with "
                f"`--retake human:<who sanctioned it>`; that same transition resets "
                f"`attempts` to 0, so the next cycle counts from this decision.")
        rec["attempts"] = 0
    if want == "superseded-by-class" and not rec.get("class"):
        raise StateError(
            f"{fid} cannot become 'superseded-by-class' without naming the class "
            f"finding that absorbs it (§8 rule 4). Set `class` when the CF is opened.")
    rec["status"] = want
    if "--retake" in args:
        return_note = f" (human re-take by {args['--retake']}; attempts reset to 0)"
    else:
        return_note = ""
    if "--next" in args:
        rec["next"] = args["--next"]
    else:
        rec.pop("next", None)              # derived from status unless overridden
    _touch(rec)
    return f"{fid}: {have} -> {want}{return_note}"


def _bound_session(state, claimed, verb, fid):
    """The session id this write is ATTRIBUTED to — read from the orchestrator's open
    dispatch record, not from what the agent typed.

    This is the structural closure of F-0159 ("`fixed-by` provenance is unbound"). The
    envelope is written by the orchestrator in `run_session`; no write verb reaches
    `state.sessions[]`, so an agent cannot mint a dispatch to agree with. If it claims
    a different id, the write is refused rather than believed.

    When the audit has NO envelopes at all — a hand-run audit, a fixture, an install
    predating phase 9 — there is nothing to bind to. The claim then stands as the
    agent's own and is LABELLED as such in the message, because a fallback is
    acceptable only when it is displayed as the weaker mode it is."""
    dispatch = open_dispatch(state)
    if dispatch is None:
        if not state.sessions:
            return claimed, (" — provenance UNBOUND: this audit has no dispatch records, "
                             "so the session id is the agent's own claim, not the "
                             "orchestrator's testimony")
        raise StateError(
            f"`{verb} {fid}` names session {claimed}, but no dispatch is open in "
            f"audit/state.json — every one of the {len(state.sessions)} recorded "
            f"sessions has already been finalised. A write attributed to a session the "
            f"orchestrator is not currently running has no provenance behind it: run "
            f"this inside the session that xcheck dispatched, or record the work by hand "
            f"in a project that keeps no envelopes.")
    real = dispatch["session_id"]
    if claimed != real:
        raise StateError(
            f"`{verb} {fid}` claims session {claimed}, but the session xcheck dispatched "
            f"and is running right now is {real} ({dispatch['role']}, launched "
            f"{dispatch['started']}). Provenance is taken from the dispatch record, not "
            f"from the argument — that is what makes it evidence. Re-run with "
            f"`--session {real}` (your XCHECK_SESSION_ID), or, if you are genuinely a "
            f"different session, stop: the orchestrator did not launch you for this.")
    return real, ""


def _record_fix(doc, state, args):
    fid, session = args["id"], args["--session"]
    if not is_session_id(session):
        raise StateError(f"--session {session!r} is not a canonical 16-hex session id; "
                         f"provenance that cannot identify a session records nothing.")
    session, unbound = _bound_session(state, session, "record-fix", fid)
    rec, _ = _record(doc, fid)
    if rec["status"] != "planned":
        raise StateError(
            f"{fid} is {rec['status']!r}; a fix is recorded only on a 'planned' finding "
            f"(§5: validated -> planned -> fixed). Reach 'planned' first with "
            f"`xcheck set-status {fid} planned`.")
    if rec.get("blocked"):
        raise StateError(
            f"{fid} is blocked ({rec['blocked']!r}) — the §8 norm-ratification gate is "
            f"fail-closed and only a valid `norm-ruling` token clears it. Recording a "
            f"fix now would ship work the norm owner has not ruled on.")
    rec["status"] = "fixed"
    rec["fixed_by"] = session
    if "--scope" in args:
        rec["admitted_scope"] = args["--scope"]
    if "--plan" in args:
        plan = args["--plan"]
        if plan not in {p["id"] for p in doc["plans"]}:
            raise StateError(f"--plan {plan!r} names no plan record in the document.")
        rec["fixed_by"] = f"{session}:{plan}"
    rec.pop("next", None)
    _touch(rec)
    return f"{fid}: planned -> fixed by session {session}{unbound}"


def _record_verdict(doc, state, args):
    fid, verdict, session = args["id"], args["--verdict"], args["--session"]
    # `--reason` was in this verb's grammar from the start and was READ BY NOTHING: an
    # operator could type it, the command would succeed, and the reason went nowhere.
    # An accepted option that changes nothing is the silent no-op this project keeps
    # finding in its own record-keeping. It now travels to the event stream, where the
    # pre-registered `reopen_cause` metric reads it.
    reason = (args.get("--reason") or "").strip() or None
    if verdict not in ("closed", "reopened"):
        raise StateError(f"--verdict must be 'closed' or 'reopened', got {verdict!r}.")
    if not is_session_id(session):
        raise StateError(f"--session {session!r} is not a canonical 16-hex session id; "
                         f"an unidentifiable verifier is not an independent one.")
    session, unbound = _bound_session(state, session, "record-verdict", fid)
    rec, _ = _record(doc, fid)
    if rec["status"] != "fixed":
        raise StateError(
            f"{fid} is {rec['status']!r}; a binding verdict applies only to a 'fixed' "
            f"finding (§5). There is nothing here for a Verifier to confirm or reject.")
    fixer = (rec.get("fixed_by") or "").split(":")[0]
    if fixer and fixer == session:
        raise StateError(
            f"session {session} recorded the fix on {fid} and cannot also return the "
            f"verdict on it (§7: the Verifier is a DIFFERENT session from the fixer). "
            f"An agent grading its own work is the independence this whole cycle exists "
            f"to buy.")
    rec["status"] = verdict
    # Phase 9: how independent this verdict actually was, measured from the two
    # envelopes rather than inferred from "the ids differ". A degraded level is
    # STORED and PRINTED, never quietly treated as if it were cross-provider.
    level = independence_level(session_by_id(state, fixer), session_by_id(state, session),
                               fixer_id=fixer, verifier_id=session)
    rec["independence"] = level
    msg = (f"{fid}: fixed -> {verdict} by session {session}{unbound}\n"
           f"  independence: {level} — {independence_note(level)}")
    if verdict == "reopened":
        rec["attempts"] = int(rec.get("attempts", 0)) + 1
        limit = int(state.limits.get("reopen_limit", 2))
        if rec["attempts"] >= limit:
            # §5: `reopen_limit` consecutive reopens stop the automatic cycle. The
            # flag is set HERE, at the transition that causes it, rather than being
            # recomputed by whoever reads the record later — a derived stop that
            # every reader must remember to derive is the F-0147 shape again.
            rec["next"] = NEEDS_HUMAN
            msg += (f" — attempt {rec['attempts']} of {limit}: the automatic cycle "
                    f"stops here and a human decides what happens next (§5 "
                    f"reopen_limit). Clearing the flag is a human re-take that resets "
                    f"`attempts` in the same transition.")
        else:
            rec.pop("next", None)
    else:
        rec.pop("next", None)
    _touch(rec)
    # `reopen_cause` counts these. `unrecorded` is written out rather than omitted:
    # a reopen with no stated cause is a fact about the run, and a metric that
    # silently dropped those cases would report a tidier picture than the evidence.
    return msg, {"verdict": verdict, "reason": reason or "unrecorded"}


def _admit_construal(doc, state, args):
    key, admitter = args["key"], args["--admitter"]
    recs = [c for c in doc["construals"] if c["key"] == key]
    if not recs:
        raise StateError(f"no construal record keyed {key!r} "
                         f"({len(doc['construals'])} exist).")
    rec = recs[0]
    if rec["status"] == "admitted":
        raise StateError(f"construal {key} is already admitted by "
                         f"{rec.get('admitted_by')!r}; admission happens once.")
    if rec["status"] != "proposed":
        raise StateError(f"construal {key} is {rec['status']!r}; only a 'proposed' "
                         f"construal can be admitted (§4 rule 9).")
    if admitter == rec["session"]:
        raise StateError(
            f"session {admitter} WROTE construal {key} and may not admit it (§4 rule 9: "
            f"admission is a separate act by a party that is not the producer). A "
            f"construal a session grants itself is licensing theatre.")
    rec["status"] = "admitted"
    rec["admitted_by"] = admitter
    rec["admitted_at"] = today()
    return f"construal {key} admitted by {admitter}"


# PHASE 9 (third audit). A finding must say WHERE its evidence is, and the claim must
# be checkable by a human who was not there. `path`, `path:LINE` or `path:START-END`.
LOCATOR_RE = re.compile(r"^(?P<path>[^\s:][^\s]*?)(?::(?P<a>\d+)(?:-(?P<b>\d+))?)?$")


def locator_problems(project, locator, commit):
    """Why this locator does not resolve in `commit`, or [] when it does.

    Checked at the WRITE boundary rather than flagged afterwards: a finding whose
    evidence cannot be found is not a finding with a defect, it is a claim about a place
    that may not exist, and the audit's own corpus is where that becomes 133 of them.

    Fails CLOSED on a commit this repository cannot see. That is the honest answer — a
    locator checked against nothing has been checked against nothing — and the message
    says so rather than accepting the finding with a quieter promise.
    """
    text = (locator or "").strip()
    if not text:
        return ["--locator is empty"]
    m = LOCATOR_RE.match(text)
    if not m:
        return [f"{text!r} is not `path`, `path:LINE` or `path:START-END`"]
    rel = m.group("path")
    if rel.startswith("/") or ".." in Path(rel).parts:
        return [f"{rel!r} is not a path inside the subject"]
    if not commit:
        return ["no subject commit was recorded for this session, so the locator could "
                "not be checked against anything"]

    def _git(argv):
        return subprocess.run(["git"] + argv, cwd=str(project), capture_output=True,
                              timeout=GIT_TIMEOUT)

    if _git(["cat-file", "-e", f"{commit}^{{commit}}"]).returncode != 0:
        return [f"the recorded subject commit {commit[:12]} is not present in this "
                f"repository, so `{text}` was checked against nothing"]
    blob = _git(["cat-file", "blob", f"{commit}:{rel}"])
    if blob.returncode != 0:
        return [f"`{rel}` does not exist in the subject commit {commit[:12]}"]
    a, b = m.group("a"), m.group("b")
    if a:
        lines = blob.stdout.count(b"\n") + (0 if blob.stdout.endswith(b"\n") else 1)
        lo, hi = int(a), int(b or a)
        if lo < 1 or hi < lo:
            return [f"`{text}` names the line range {lo}-{hi}, which is not a range"]
        if hi > lines:
            return [f"`{rel}` has {lines} line(s) in the subject commit "
                    f"{commit[:12]}; `{text}` names line {hi}"]
    return []


def source_hash_problems(project, locator, commit, claimed):
    """The other half: the hash must be the sha256 of what is actually there.

    A hash nobody recomputes is decoration. This one is the sha256 of the blob at the
    locator IN the recorded subject commit, so a reader with the commit and the path can
    reproduce it years later without trusting the session that filed it.
    """
    if not SHA256_RE.match((claimed or "").strip()):
        return [f"--source-hash {claimed!r} is not a sha-256 digest"]
    rel = LOCATOR_RE.match(locator.strip()).group("path")
    blob = subprocess.run(["git", "cat-file", "blob", f"{commit}:{rel}"],
                          cwd=str(project), capture_output=True, timeout=GIT_TIMEOUT)
    if blob.returncode != 0:                     # already reported by locator_problems
        return []
    actual = hashlib.sha256(blob.stdout).hexdigest()
    if actual != claimed.strip():
        return [f"--source-hash {claimed[:12]}… is not the sha-256 of `{rel}` in the "
                f"subject commit {commit[:12]}, which is {actual[:12]}…"]
    return []


def _file_finding(doc, state, args, project=Path(".")):
    fid = args["--id"]
    if any(r["id"] == fid for k in ("findings", "class_findings") for r in doc[k]):
        raise StateError(f"{fid} already exists; filing it again would be a second "
                         f"record with one id, which is not a record.")
    if args["--severity"] not in SEVERITIES:
        raise StateError(f"--severity {args['--severity']!r} is not a canonical §2 "
                         f"severity ({', '.join(sorted(SEVERITIES))}).")
    body = args["--body"]
    # PHASE 9 (third audit). The evidence has to be LOCATABLE, checked here rather than
    # flagged later: "filed and reviewed afterwards" is how a corpus reaches 133 findings
    # nobody has independently checked. `project` is the tree this write is happening in,
    # and the commit is the SUBJECT's — the outer one the orchestrator exported, not the
    # disposable worktree seed a session's own `git rev-parse HEAD` would answer.
    is_class = bool(CLASS_ID_RE.match(fid))
    locator = (args.get("--locator") or "").strip()
    claimed = (args.get("--source-hash") or "").strip()
    if is_class:
        if locator or claimed:
            raise StateError(
                f"{fid} is a class finding: its evidence is the census in its body and "
                f"the locators its members already carry, so --locator and "
                f"--source-hash do not apply. One `path:line` would name one instance "
                f"of a pattern and call it the pattern.")
    else:
        missing = [f for f, v in (("--locator", locator), ("--source-hash", claimed))
                   if not v]
        if missing:
            raise StateError(
                f"{fid} cannot be filed without {' and '.join(missing)}: a finding names "
                f"a place a reader can go and a hash they can recompute, and one that "
                f"does not is a claim nobody can check without re-running the session "
                f"that made it.")
        commit = subject_commit(project)
        problems = locator_problems(project, locator, commit)
        if not problems:
            problems = source_hash_problems(project, locator, commit, claimed)
        if problems:
            raise StateError(
                f"{fid} cannot be filed: " + "; ".join(problems) + ". A finding names a "
                "place a reader can go and a hash they can recompute; one that does not "
                "is a claim, and this is the boundary that tells them apart.")
    rec = {"id": fid, "title": args["--title"], "severity": args["--severity"],
           "status": "reported", "dimension": args["--dimension"],
           "unit": [u for u in args["--unit"].split(",") if u],
           "pass": args["--pass"], "attempts": 0, "updated": today(),
           "body_path": body}
    if not is_class:
        rec["locator"], rec["source_hash"] = locator, claimed
    if "--recurrence-of" in args:
        anc = args["--recurrence-of"]
        if not any(r["id"] == anc for k in ("findings", "class_findings") for r in doc[k]):
            raise StateError(f"--recurrence-of {anc!r} names no existing finding.")
        rec["recurrence_of"] = anc
    members = [m.strip() for m in (args.get("--members") or "").split(",") if m.strip()]
    if not CLASS_ID_RE.match(fid):
        if members:
            raise StateError(
                f"--members is for a class finding; {fid} is an ordinary finding, and a "
                f"finding does not absorb other findings (§8 rule 4). File it as "
                f"`CF-NNNN` if a census escalated it.")
        if "--session" in args:
            raise StateError(
                f"--session records who ran the census that escalated a class finding; "
                f"{fid} is an ordinary finding and the schema gives it no `created_by`. "
                f"Its provenance is the pass it was filed under (`--pass`).")
        doc["findings"].append(rec)
        return f"{fid} filed at 'reported'"

    # 0.9.1 phase 5 (P0 #4). §8 class escalation had NO write path: this verb appended
    # every id to `findings`, where the schema refuses a `CF-` id, and nothing anywhere
    # wrote `findings[].class` or `class_findings[].members`. So `xcheck set-status
    # <ID> superseded-by-class` refused with "Set `class` when the CF is opened" and no
    # way existed to open one. The Remediator's §3 role card, the §8 rule the whole
    # mechanism rests on, and README all routed to this verb; the verb declined.
    #
    # Minting is ONE transaction because its halves are not separately valid: a class
    # finding with no members is not a class, and a member pointing at a CF that does
    # not exist is a dangling reference. §8 rule 4 states them as one act — "on
    # creation, every member finding moves to `superseded-by-class` immediately".
    if not members:
        raise StateError(
            f"{fid} is a class finding and needs `--members F-NNNN,F-NNNN` — the "
            f"findings its census absorbs (§8 rule 4). A class with no members is a "
            f"pattern nobody has found an instance of.")
    by_id = {r["id"]: r for r in doc["findings"]}
    absorbed = []
    for mid in members:
        if mid not in by_id:
            raise StateError(
                f"--members names {mid!r}, which is not an existing finding"
                + (" (a class finding cannot be a member of another class)"
                   if any(r["id"] == mid for r in doc["class_findings"]) else "") + ".")
        m = by_id[mid]
        allowed = util.TRANSITIONS.get(m["status"], set())
        if "superseded-by-class" not in allowed:
            raise StateError(
                f"{mid} is {m['status']!r} and §5 does not allow it to become "
                f"'superseded-by-class' (only {sorted(allowed) or 'no transition'}). "
                f"A census absorbs findings that are still in the cycle; it does not "
                f"reach back into terminal ones.")
        absorbed.append(m)
    # §8 rule 2's threshold is deliberately NOT enforced here. It counts LIVE INSTANCES
    # found by the census of the current material, which is normally larger than the set
    # of instances that already have findings — the census is evidence in the CF body,
    # not a number this verb can see. Refusing on `len(members) < class_threshold` would
    # reject a legitimate class whose census found five instances of which one had been
    # filed. The verb records the escalation; §8 governs whether to make it.
    # `created_by` is a class finding's ONLY provenance field, it is rendered into the
    # frontmatter block agents read (views.py), and until now no verb wrote it — the same
    # zero-route hole as the escalation above, found by the same derived route table.
    # Bound like every other session claim: the orchestrator's envelope decides whether
    # the id is the one it dispatched (F-0096), not the caller.
    unbound = ""
    if "--session" in args:
        session = args["--session"]
        if not is_session_id(session):
            raise StateError(f"--session {session!r} is not a canonical 16-hex session "
                             f"id; provenance that cannot identify a session records "
                             f"nothing.")
        rec["created_by"], unbound = _bound_session(state, session, "file-finding", fid)
    rec["members"] = [m["id"] for m in absorbed]
    doc["class_findings"].append(rec)
    for m in absorbed:
        m["class"] = fid
        m["status"] = "superseded-by-class"
        m.pop("next", None)
        _touch(m)
    return (f"{fid} filed at 'reported' as a class finding absorbing "
            f"{', '.join(rec['members'])} (each now 'superseded-by-class'; §8 rule 4)"
            + unbound)


def _record_coverage(doc, state, args):
    pid = args["p-id"]
    entries = [q for q in doc["queue"] if q["id"] == pid]
    if not entries:
        raise StateError(f"no pass {pid!r} in the queue "
                         f"({', '.join(q['id'] for q in doc['queue'][:8])}…).")
    q = entries[0]
    if q.get("coverage"):
        raise StateError(
            f"pass {pid} already reports coverage from {q['coverage']['report_path']!r}. "
            f"A pass is documented by exactly ONE canonical record (§4 rule 5, F-0154); "
            f"a second one is how a partial report gets synthesised into a full claim.")
    ids = [f for f in (args.get("--findings") or "").split(",") if f]
    known = {r["id"] for k in ("findings", "class_findings") for r in doc[k]}
    missing = [f for f in ids if f not in known]
    if missing:
        raise StateError(f"--findings names {', '.join(missing)}, which no record "
                         f"carries; a coverage report cannot certify findings that "
                         f"are not filed.")
    q["coverage"] = {"report_path": args["--report"], "findings": ids,
                     "updated": today(), "status": "done"}
    q["done"] = True
    return f"pass {pid}: coverage bound to {args['--report']} ({len(ids)} finding(s))"


def _record_refusal(doc, state, args):
    """§5: a refusal is about the ATTEMPT, so the status does not move. Both halves
    are required — the closed-vocabulary reason AND the prose that says what is
    missing — and the verb checks the prose half here rather than leaving it to
    `lint`, because a reason code recorded alone is a session that stopped without
    handing anything forward."""
    from xcheck.validate import _body_text
    from xcheck.md_prose import section_declared_content

    fid = args["id"]
    rec, _ = _record(doc, fid)
    if "--clear" in args:
        if not rec.get("refusal"):
            raise StateError(f"{fid} carries no refusal to clear.")
        was = rec.pop("refusal")
        _touch(rec)
        return (f"{fid}: refusal {was!r} cleared — the charter is dispatched again "
                f"unchanged, so the obstacle had better be gone")
    reason = args.get("--reason")
    if reason not in REFUSAL_REASONS:
        raise StateError(
            f"--reason {reason!r} is not one of the closed §5 vocabulary "
            f"({', '.join(sorted(REFUSAL_REASONS))}); free text carries no obstacle "
            f"forward in a form the next session can act on. Pass --clear to remove "
            f"a refusal instead.")
    if rec["status"] in TERMINAL:
        raise StateError(f"{fid} is {rec['status']!r} (terminal): nothing is owed on "
                         f"it, so there is no attempt to refuse.")
    body = _body_text(args["audit_dir"], _build_record_view(rec))
    if not section_declared_content(body, "## Refusal"):
        raise StateError(
            f"{fid}: write the prose half FIRST — a `## Refusal` section in "
            f"{rec.get('body_path')} saying what is actually missing and what would "
            f"unblock it. A bare category name is a stop with no obstacle attached.")
    rec["refusal"] = reason
    _touch(rec)
    return f"{fid}: refusal recorded ({reason}); status stays {rec['status']!r}"


def _block_on_norm(doc, state, args):
    fid = args["id"]
    rec, kind = _record(doc, fid)
    if kind != "class_findings":
        raise StateError(f"{fid} is a finding, not a class finding; the §8 rule 8 "
                         f"gate is a norm conflict about a CLASS-wide fix.")
    if rec.get("blocked"):
        raise StateError(f"{fid} is already blocked on {rec.get('norm_ruling')!r}.")
    if rec["status"] not in ("validated", "planned"):
        raise StateError(f"{fid} is {rec['status']!r}; the gate is raised while the "
                         f"class fix is being planned (validated or planned), which "
                         f"is when the norm conflict is discovered.")
    rec["blocked"] = "norm-ratification"
    rec["norm_ruling"] = "pending"
    _touch(rec)
    return (f"{fid}: blocked on norm-ratification (§8 rule 8) — fail-closed; "
            f"`xcheck record-fix` refuses until the norm owner rules")


def _record_ruling(doc, state, args):
    fid, norm, by = args["id"], args["--norm"], args["--ruled-by"]
    rec, _ = _record(doc, fid)
    if not rec.get("blocked"):
        raise StateError(f"{fid} is not blocked on a norm; there is no gate to lift.")
    if not by.startswith("human:") or len(by) <= len("human:"):
        raise StateError(
            f"--ruled-by {by!r}: a norm conflict is ruled by the norm's OWNER, who is "
            f"a human (§8 rule 8). Pass `human:<label>` — the verb records the "
            f"decision, it does not make it.")
    if norm == "pending" or not norm.strip():
        raise StateError(f"--norm {norm!r} is not a ruling; name the winning norm "
                         f"(e.g. N1, or N1-over-N4 when one norm beats another).")
    rec["blocked"] = None
    rec["norm_ruling"] = norm
    _touch(rec)
    return f"{fid}: norm ruling {norm} recorded by {by}; the §8 gate is lifted"


def _build_record_view(rec):
    """`validate._body_text` reads `.body_path` off a State record; the verbs work on
    plain dicts. One tiny adapter beats duplicating the path resolution."""
    class _V:
        body_path = rec.get("body_path")
        id = rec.get("id")
    return _V


def _propose_construal(doc, state, args):
    key, role, charter = args["key"], args["--role"], args["--charter"]
    if any(c["key"] == key for c in doc["construals"]):
        raise StateError(f"construal {key} already exists; a second record on one key "
                         f"is two readings of one charter claiming to be one.")
    want = construal_key(role, charter)
    if key != want:
        raise StateError(
            f"the key {key!r} is not derived from this (role, charter): "
            f"construal_key({role!r}, …) is {want!r}. The key is a BINDING, not a name — "
            f"a record keyed on different charter text is about different work (F-0137).")
    if not is_session_id(args["--session"]):
        raise StateError(f"--session {args['--session']!r} is not a canonical 16-hex "
                         f"session id.")
    doc["construals"].append({
        "key": key, "role": role, "charter": charter, "session": args["--session"],
        "status": "proposed", "created": today(), "body_path": args["--body"]})
    return f"construal {key} proposed by session {args['--session']} ({role})"


def _record_plan(doc, state, args):
    pid = args["rp-id"]
    if any(p["id"] == pid for p in doc["plans"]):
        raise StateError(f"plan {pid} already exists.")
    ids = [f for f in args["--findings"].split(",") if f]
    known = {r["id"] for k in ("findings", "class_findings") for r in doc[k]}
    missing = [f for f in ids if f not in known]
    if missing:
        raise StateError(f"--findings names {', '.join(missing)}, which no record "
                         f"carries; a plan cannot cover a finding that is not filed.")
    doc["plans"].append({"id": pid, "findings": ids, "status": "open",
                         "updated": today(), "body_path": args["--body"], "attempts": 0})
    return f"plan {pid} recorded over {len(ids)} finding(s)"


def _queue_pass(doc, state, args):
    pid = args["p-id"]
    if any(q["id"] == pid for q in doc["queue"]):
        raise StateError(f"pass {pid} is already in the queue.")
    dims = {d.key for d in (state.catalogs.dimensions if state.catalogs else ())}
    if dims and args["--dimension"] not in dims:
        raise StateError(f"--dimension {args['--dimension']!r} is not in the AUDIT.md "
                         f"Dimensions catalog ({', '.join(sorted(dims))}); a pass that "
                         f"resolves to no dimension carries no §6 rule 2 evidence.")
    units = [u for u in args["--units"].split(",") if u]
    known = {u.id for u in (state.catalogs.units if state.catalogs else ())}
    unknown = [u for u in units if known and u not in known]
    if unknown:
        raise StateError(f"--units names {', '.join(unknown)}, which the Unit map does "
                         f"not declare.")
    row = {"id": pid, "dimension": args["--dimension"], "units": units,
           "charter": args["--charter"], "stop": args["--stop"], "done": False}
    # PHASE 12: a pass scoped to a diff records WHAT it was scoped against. Absent is
    # the honest default and means the pass looked at its whole unit — the reader can
    # then tell an audit of everything from an audit of a change without asking.
    base = (args.get("--base") or "").strip()
    if base:
        row["base"] = base
    doc["queue"].append(row)
    return (f"pass {pid} queued ({args['--dimension']} × {', '.join(units)})"
            + (f", scoped to changes since {base}" if base else ""))


# ---------------------------------------------------------------------------
# the admin verbs (0.9.0 phase 3)
# ---------------------------------------------------------------------------

def _whole_number(raw, what):
    """`int()` is too permissive for a value an operator types at a gate.

    It accepts `" 2 "`, and — since 3.6 — `"2_0"` as twenty. A limit is read back
    by `decision.py` to decide whether a human must be consulted; a value that
    silently means something other than what was typed is the wrong kind of
    surprise to have at that particular door.
    """
    text = raw.strip() if isinstance(raw, str) else raw
    if not isinstance(text, str) or not text.lstrip("-").isdigit():
        raise StateError(
            f"{what} must be a whole number, got {raw!r}. Pass digits only — "
            f"`2`, not `2.5`, `two`, `0x2` or `2_0`.")
    return int(text)


def _pass_entry(doc, pid, verb):
    for q in doc["queue"]:
        if q["id"] == pid:
            return q
    known = ", ".join(q["id"] for q in doc["queue"][:8]) or "the queue is empty"
    raise StateError(
        f"no pass {pid!r} in the queue ({known}"
        f"{', …' if len(doc['queue']) > 8 else ''}). Check the id with "
        f"`xcheck status`, or queue it first with `xcheck queue-pass {pid} …`; "
        f"`{verb}` changes an existing pass, it does not create one.")


def _refuse_if_done(q, verb):
    """A done pass is certified by its coverage record. Amending or cancelling it
    would rewrite what a report already said was covered (§4 rule 5)."""
    if not q.get("done"):
        return
    cov = q.get("coverage") or {}
    where = cov.get("report_path", "an unrecorded report")
    raise StateError(
        f"pass {q['id']} is done and its coverage record binds it to {where} — "
        f"`{verb}` is refused, because a report that already certified this "
        f"charter would then describe work nobody did. If the charter was wrong, "
        f"queue the remainder as a NEW pass with `xcheck queue-pass` and let its "
        f"own report cover it.")


def _known_dimensions(state):
    return {d.key for d in (state.catalogs.dimensions if state.catalogs else ())}


def _known_units(state):
    return {u.id for u in (state.catalogs.units if state.catalogs else ())}


def _set_limit(doc, state, args):
    key = args["key"]
    # One lookup, not a membership test plus an indexing: the two can disagree, and
    # the mutation witness for this guard is only meaningful if weakening the check
    # leaves running code rather than a KeyError one line later.
    bounds = LIMIT_FIELDS.get(key)
    if bounds is None:
        raise StateError(
            f"{key!r} is not a limit this tool reads (known: "
            f"{', '.join(sorted(LIMIT_FIELDS))}). A limit no consumer reads is a "
            f"silently-ignored setting, which is why the write is refused rather "
            f"than stored — pick one of the known keys.")
    value = _whole_number(args["value"], f"the value for {key}")
    lo, hi = bounds
    if not lo <= value <= hi:
        why = {
            # The two whose lower bound is a GATE, not a preference. The predecessor
            # of this tool could have its human stop suppressed by a limit hidden in
            # a Markdown comment; a limit that can be set to zero suppresses it in
            # the open, which is no better.
            "reopen_limit": ("below 1 there is no reopen budget at all, so the "
                             "mandatory human stop after repeated failures never "
                             "fires"),
            "class_threshold": ("below 2 a single finding is a class, so `census` "
                                "would raise every finding to a class finding"),
        }.get(key)
        raise StateError(
            f"{key}={value} is outside the legal range {lo}..{hi}"
            + (f" — {why}" if why else "")
            + f". Set a value in {lo}..{hi}, or leave the default in place.")
    was = doc["limits"].get(key)
    doc["limits"][key] = value
    return (f"limit {key}: {'unset' if was is None else was} -> {value} "
            f"(legal range {lo}..{hi})")


def _amend_pass(doc, state, args):
    pid = args["p-id"]
    q = _pass_entry(doc, pid, "amend-pass")
    _refuse_if_done(q, "amend-pass")

    fields = {"--dimension": "dimension", "--units": "units",
              "--charter": "charter", "--stop": "stop"}
    given = {opt: args[opt] for opt in fields if opt in args}
    if not given:
        raise StateError(
            f"`amend-pass {pid}` was given nothing to change. Pass at least one of "
            f"{', '.join(fields)} — a transaction that changes nothing would still "
            f"bump state_revision and emit an event, which makes the history claim "
            f"a decision nobody made.")

    if "--dimension" in given:
        dims = _known_dimensions(state)
        if dims and given["--dimension"] not in dims:
            raise StateError(
                f"--dimension {given['--dimension']!r} is not in the Dimensions "
                f"catalog ({', '.join(sorted(dims))}); a pass that resolves to no "
                f"dimension carries no §6 rule 2 evidence. Add it first with "
                f"`xcheck update-catalog dimension {given['--dimension']} "
                f"--catches … --norms …`.")
    if "--units" in given:
        units = [u for u in given["--units"].split(",") if u]
        if not units:
            raise StateError("--units is empty; a pass with no unit has no material "
                             "to audit. Pass a comma-separated list, e.g. `U01,U02`.")
        known = _known_units(state)
        unknown = [u for u in units if known and u not in known]
        if unknown:
            raise StateError(
                f"--units names {', '.join(unknown)}, which the Unit map does not "
                f"declare. Add the unit with `xcheck update-catalog unit "
                f"{unknown[0]} --material … --size … --responsibility …` first.")
        given["--units"] = units

    changed = []
    for opt, field in fields.items():
        if opt in given:
            changed.append(f"{field}: {q[field]!r} -> {given[opt]!r}")
            q[field] = given[opt]
    return f"pass {pid} amended — " + "; ".join(changed)


def _cancel_pass(doc, state, args):
    pid = args["p-id"]
    q = _pass_entry(doc, pid, "cancel-pass")
    _refuse_if_done(q, "cancel-pass")
    holders = [r["id"] for k in ("findings", "class_findings") for r in doc[k]
               if r["pass"] == pid]
    if holders:
        raise StateError(
            f"pass {pid} is refused for cancellation: {len(holders)} record(s) name "
            f"it as the pass that produced them ({', '.join(holders[:8])}"
            f"{', …' if len(holders) > 8 else ''}). Cancelling it would leave those "
            f"findings pointing at a pass that does not exist, which the next "
            f"`load_state` rejects — so the tool would be unreadable rather than "
            f"merely wrong. Move those findings to another pass first, or keep the "
            f"pass and mark it done with its coverage report.")
    doc["queue"] = [e for e in doc["queue"] if e["id"] != pid]
    return f"pass {pid} cancelled ({len(doc['queue'])} left in the queue)"


# kind -> (document key, id field, the options that make up the record)
CATALOG_KINDS = {
    "norm": ("norms", "id", ("--source", "--scope")),
    "dimension": ("dimensions", "key", ("--catches", "--norms")),
    "unit": ("units", "id", ("--material", "--size", "--responsibility")),
}


def _catalog_references(doc, kind, key):
    """Everything that would dangle if this entry were removed.

    Not a courtesy check: `_validate` rejects a finding whose dimension or unit is
    not in the catalog, so removing a referenced entry does not corrupt the state
    quietly — it makes the audit UNREADABLE on the next load. Naming the holders
    here is the difference between a refusal an operator can act on and a schema
    error they have to reverse-engineer.
    """
    out = []
    if kind == "norm":
        out += [f"dimension {d['key']}" for d in doc["catalogs"]["dimensions"]
                if key in d.get("norms", ())]
    if kind == "dimension":
        out += [r["id"] for k in ("findings", "class_findings") for r in doc[k]
                if r["dimension"] == key]
        out += [f"pass {q['id']}" for q in doc["queue"] if q["dimension"] == key]
    if kind == "unit":
        out += [r["id"] for k in ("findings", "class_findings") for r in doc[k]
                if key in r["unit"]]
        out += [f"pass {q['id']}" for q in doc["queue"] if key in q["units"]]
    return out


def _update_catalog(doc, state, args):
    kind, key = args["kind"], args["key"]
    if kind not in CATALOG_KINDS:
        raise StateError(
            f"{kind!r} is not a catalog ({', '.join(sorted(CATALOG_KINDS))}). "
            f"Usage: `xcheck update-catalog dimension invariants --catches … "
            f"--norms N1`.")
    where, id_field, opts = CATALOG_KINDS[kind]
    entries = doc["catalogs"][where]
    existing = next((e for e in entries if e[id_field] == key), None)

    if args.get("--remove"):
        if existing is None:
            raise StateError(f"no {kind} {key!r} in the catalog "
                             f"({', '.join(e[id_field] for e in entries) or 'empty'}); "
                             f"nothing to remove.")
        holders = _catalog_references(doc, kind, key)
        if holders:
            raise StateError(
                f"{kind} {key!r} is refused for removal: {len(holders)} record(s) "
                f"still reference it ({', '.join(holders[:8])}"
                f"{', …' if len(holders) > 8 else ''}). Repoint them first — the "
                f"next `load_state` refuses a record whose {kind} is not in the "
                f"catalog, so removing it now would make the audit unreadable.")
        doc["catalogs"][where] = [e for e in entries if e[id_field] != key]
        return f"{kind} {key} removed from the catalog"

    given = {opt: args[opt] for opt in opts if opt in args}
    if existing is None:
        missing = [o for o in opts if o not in given]
        if missing:
            raise StateError(
                f"{kind} {key!r} does not exist yet, so this is an ADD and every "
                f"field is required — missing {', '.join(missing)}. An entry with a "
                f"blank field would pass the schema and tell a reader nothing.")
        rec = {id_field: key}
        rec.update(_catalog_values(given))
        entries.append(rec)
        return f"{kind} {key} added to the catalog"

    if not given:
        raise StateError(
            f"`update-catalog {kind} {key}` was given nothing to change. Pass one of "
            f"{', '.join(opts)}, or `--remove`.")
    changed = []
    for field, v in _catalog_values(given).items():
        changed.append(f"{field}: {existing.get(field)!r} -> {v!r}")
        existing[field] = v
    return f"{kind} {key} amended — " + "; ".join(changed)


def _catalog_values(given):
    """Option names -> record fields. `--norms` is the one list-valued field."""
    out = {}
    for opt, raw in given.items():
        field = opt[2:].replace("-", "_")
        out[field] = [n for n in raw.split(",") if n] if opt == "--norms" else raw
    return out


# The verbs whose validation needs the tree, not only the document.
_PROJECT_VERBS = frozenset({"file-finding"})

_VERB_FN = {
    "set-status": _set_status,
    "record-fix": _record_fix,
    "record-verdict": _record_verdict,
    "admit-construal": _admit_construal,
    "file-finding": _file_finding,
    "record-coverage": _record_coverage,
    "propose-construal": _propose_construal,
    "record-plan": _record_plan,
    "queue-pass": _queue_pass,
    "record-refusal": _record_refusal,
    "block-on-norm": _block_on_norm,
    "record-ruling": _record_ruling,
    "set-limit": _set_limit,
    "amend-pass": _amend_pass,
    "cancel-pass": _cancel_pass,
    "update-catalog": _update_catalog,
}
