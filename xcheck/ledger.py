"""The append path for the event stream, and what happens when it fails.

`envelope.emit` used to do this:

    except OSError as e:
        print(f"note: could not append to audit/{EVENTS_FILENAME} ({e})")

The reasoning behind it is real and is kept: losing the orchestrator to a full disk, while
a session's actual work is already committed, trades a durable outcome for a log line.
What it produced, though, is the failure every consumer above the ledger is blind to.
Budgets price sessions off `session_finished.tokens`; metrics count transitions;
independence reads two envelopes; receipts attest a dispatch; OKF projects the stream as
if it were the record. Every one of them treats the stream as COMPLETE, and a transition
that lands while its event is dropped makes all of them quietly wrong — with no line
anywhere saying a number is now an undercount.

So the trade-off is answered rather than reversed. A full disk during an append still does
not lose the work; it is now visible and recoverable instead of gone.

THE DECLARED POLICY. The state write and the event append are one unit with two phases,
and which phase fails decides what happens:

    RESERVE   Before the state is written. The event line goes to a durable OUTBOX
              (`audit/events.outbox.jsonl`), fsynced, and the ledger itself is proved
              appendable. If EITHER fails, `reserve()` RAISES: the transition is refused
              and canonical state is not touched. Nothing has happened yet, so refusing
              costs nothing but the operator's time, and applying would cost the audit
              its account of itself.

    COMMIT    After the state write is durable. The reserved line is appended to
              `events.jsonl` and the outbox entry is dropped. If the append fails HERE —
              the disk filled in the window between the two phases — the work has landed
              and the outbox entry survives. `replay()` on any later run appends it. This
              is the case the old `except OSError` was written for, and it is the case
              that now recovers instead of printing.

So: an events file that cannot be appended to refuses the transition; a disk that fills
mid-unit keeps the outcome AND the event. Neither is a silent success.

Replay is keyed on `event_id`, a digest of the event's own bytes, so replaying an outbox
twice appends once. A recovery path that duplicates evidence is a new corruption wearing
a fix's name.

COST: one extra file append plus one fsync per CANONICAL TRANSITION — 162 of them in this
repository's entire history. Ordinary events (`emit`) pay nothing on the happy path; they
reach the outbox only when the ledger append has already failed.
"""

import collections
import hashlib
import json
import os
from pathlib import Path

EVENTS_FILENAME = "events.jsonl"
OUTBOX_FILENAME = "events.outbox.jsonl"

# ---------------------------------------------------------------- the chain (phase 8)

# The event record's own schema version. Exactly ONE version exists today and no second
# one is invented here: what this phase owes is the MECHANISM — a reader that refuses a
# version it does not know, by name and line number — not a migration nobody needs yet.
LEDGER_SCHEMA = 1

# The two fields the chain adds to every new event.
SCHEMA_FIELD = "v"
PREV_FIELD = "prev"

# The chain's zero. Not a digest of anything: the first chained event has no predecessor
# INSIDE the chain, and its `prev` is the digest of everything before the migration point,
# which is computed from the file. This constant is what an empty stream starts from.
GENESIS = "0" * 64


def digest_of(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def link(prev, encoded_line):
    """The chain step: the digest of (everything so far) + (this line's bytes).

    A ROLLING digest rather than a per-event hash of the previous event. The difference
    matters for the attack this exists to catch: a per-event hash is rebuildable from any
    point forward, so an editor who rewrites line 500 can re-link 501..N and produce a
    self-consistent file. A rolling prefix digest can be rebuilt the same way — which is
    why the anchor in canonical state is the other half of this and not decoration.
    """
    return hashlib.sha256((prev + encoded_line).encode("utf-8")).hexdigest()


def chained(line, prev):
    """One event line with its schema version and the prefix digest before it."""
    out = dict(line)
    out[SCHEMA_FIELD] = LEDGER_SCHEMA
    out[PREV_FIELD] = prev
    return out


class LedgerError(RuntimeError):
    """The event could not be made durable, so the transition may not proceed."""


def events_path(project):
    return Path(project) / "audit" / EVENTS_FILENAME


def outbox_path(project):
    return Path(project) / "audit" / OUTBOX_FILENAME


def event_id(line):
    """A digest of the event's own bytes. The idempotency key for replay.

    Of the BYTES, not of a field: two events can legitimately share every field they
    carry — two `lease_acquired` in the same second for the same session would — and a
    key built from a subset would silently drop the second. `ts` carries seconds and the
    line is serialised with sorted keys, so identical bytes mean the same append, which
    is exactly what replay must not do twice.

    Taken over the UNCHAINED line (phase 8). The outbox holds the event; the chain fields
    are decided at APPEND time, because a replayed event links to wherever the chain has
    got to by then. An id that moved when the line was re-linked would replay as a new
    event every time.
    """
    return hashlib.sha256(_encode(_unchained(line)).encode("utf-8")).hexdigest()[:16]


def _encode(line):
    return json.dumps(line, sort_keys=True, separators=(",", ":"))


def _append(path, text):
    """One append, flushed and fsynced. Raises OSError."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


def snapshot(project):
    """What canonical state records about the stream at this moment: the chain head and
    how many events are in it. Read ONCE per transition and written into the state
    document, so the two halves of the tamper check are taken at the same instant."""
    lines = _read_lines(project)
    legacy, chain = split_at_migration(lines)
    prev = prefix_digest(legacy)
    for line in chain:
        prev = link(prev, _encode(_unchained(line)))
    return {"digest": prev, "events": len(lines)}


def new_txn():
    """A fresh transaction identity.

    Not derived from the revision, the timestamp or the event's own bytes: two
    transactions may legitimately share any of those, and the whole point of this value
    is to tell such a pair apart. 8 random bytes, rendered as the 16 hex characters
    `state.TXN_RE` admits.
    """
    return os.urandom(8).hex()


def reserve(project, line, txn=None):
    """Phase one: make this event durable and prove the ledger is appendable.

    Raises `LedgerError` if either fails. The caller has not written state yet, and must
    not.

    `txn` is the identity of the transaction whose state write this event describes. The
    caller mints it, hands it here AND to `write_state`, and recovery later honours the
    row exactly when canonical state proves that transaction committed. A row without one
    is a historical row and falls back to the revision rule in `uncommitted`.
    """
    entry = {"event_id": event_id(line), "line": line}
    if txn is not None:
        entry["txn"] = txn
    try:
        # The ledger's own appendability, checked HERE rather than discovered after the
        # state write. Opening for append creates nothing that a reader would see and
        # writes no bytes; what it answers is the question the old code asked too late.
        events = events_path(project)
        events.parent.mkdir(parents=True, exist_ok=True)
        with open(events, "a", encoding="utf-8"):
            pass
        _append(outbox_path(project), _encode(entry) + "\n")
    except OSError as e:
        raise LedgerError(
            f"refusing the transition: its event could not be made durable ({e}).\n"
            f"    ledger  {events_path(project)}\n"
            f"    outbox  {outbox_path(project)}\n"
            f"Canonical state was NOT changed. Every consumer above the event stream — "
            f"budgets, metrics, independence, receipts, the derived bundle — reads it as "
            f"complete, so a transition applied without its event is an audit that has "
            f"stopped being able to account for itself. Free space or fix permissions on "
            f"audit/ and run the verb again.") from None
    return entry


def append_chained(project, line):
    """Append one event, linked to the current end of the chain. Raises OSError.

    The link is computed HERE, at append time, from the file as it stands — never carried
    from wherever the line has been waiting. A replayed event belongs where it lands.
    """
    _append(events_path(project), _encode(chained(line, head(project))) + "\n")


def commit(project, entry):
    """Phase two: append the reserved event and drop its outbox row.

    Never raises. The state write has already landed by the time this is called, so a
    failure here must not undo it — the outbox entry stays and `replay()` finishes the
    job on any later run. Returns True when the event reached the ledger.
    """
    try:
        append_chained(project, entry["line"])
    except OSError as e:
        print(f"note: audit/{EVENTS_FILENAME} could not be appended ({e}); the event is "
              f"in audit/{OUTBOX_FILENAME} and the next xcheck run will replay it. The "
              f"transition itself is committed.")
        return False
    _drop(project, {entry["event_id"]})
    return True


def access_problem(path):
    """Why this path cannot be read as a stream of events, or None.

    PHASE 6 (fifth audit). `_numbered_rows` caught `OSError` and returned `[]`, so a
    DIRECTORY where `audit/events.jsonl` belongs reported `('intact', '0 event(s), chain
    intact')` — the same answer as a project that has never dispatched a session. An
    audit tool whose evidence file has been replaced by a directory reporting that its
    evidence is intact is the worst available failure.

    ABSENT is not a problem and never becomes one: a fresh project has no stream, and
    failing closed on it would refuse the first verb anyone runs. Everything else — a
    directory, a socket, a file this process may not open — keeps its errno and is an
    error.
    """
    if not path.exists():
        return None
    if path.is_dir():
        return (f"{path} is a DIRECTORY, not the append-only event stream. An empty "
                f"ledger and a ledger that cannot be read are different answers, and "
                f"this is the second one.")
    if not path.is_file():
        return f"{path} is not a regular file, so it is not a readable event stream."
    try:
        with open(path, "rb"):
            pass
    except OSError as e:
        return (f"{path} cannot be read — [Errno {e.errno}] {e.strerror}. Fix the "
                f"permissions; nothing is written until the stream reads.")
    return None


def _numbered_rows(path):
    bad = access_problem(path)
    if bad:
        raise ChainError(bad)
    if not path.exists():
        # The one case that IS an empty list: a project that has written nothing. Said
        # here explicitly rather than falling out of a swallowed OSError, which is how
        # a directory and an unreadable file came to share this answer.
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        # A file that passed `access_problem` a moment ago and fails now: report the
        # errno rather than an empty list, which is what "0 event(s), chain intact" was
        # made of.
        raise ChainError(f"{path} could not be read — [Errno {e.errno}] "
                         f"{e.strerror}") from None
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append((n, json.loads(raw)))
        except ValueError:
            out.append((n, None))
    return out


def pending(project):
    """The outbox entries waiting to be appended, in order."""
    return [row for _n, row in _numbered_rows(outbox_path(project)) if row]


def _drop(project, ids):
    path = outbox_path(project)
    keep = [row for _n, row in _numbered_rows(path) if row and row.get("event_id") not in ids]
    text = "".join(_encode(r) + "\n" for r in keep)
    try:
        if text:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        elif path.exists():
            path.unlink()
    except OSError as e:
        print(f"note: could not prune audit/{OUTBOX_FILENAME} ({e}); replay is "
              f"idempotent, so a stale row costs a re-check and not a duplicate event.")


def _durable_doc(project):
    """`audit/state.json` as it stands on disk, or None if it cannot be read.

    Read with `json` rather than through `state.load_state` on purpose: this module is
    below `state` (`write.py` imports both) and a validating read would refuse a document
    these functions exist to inspect after a crash. One reader, so the three questions
    asked of that document below cost one parse between them.
    """
    try:
        doc = json.loads((Path(project) / "audit" / "state.json")
                         .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def committed_anchor(project):
    """The chain checkpoint canonical state carries, or None when it carries none.

    PHASE 4 (sixth audit). "No checkpoint recorded yet" and "the checkpoint disagrees"
    are DIFFERENT answers, and only the second may refuse a write. This project's own
    1,043 historical events carry no anchor at all, so a rule that could not tell those
    apart would have bricked the tool on its own audit.
    """
    doc = _durable_doc(project)
    anchor = (doc or {}).get("ledger_anchor")
    return anchor if isinstance(anchor, dict) else None


def committed_revision(project):
    """The revision canonical state PROVES it reached, read from durable state alone.

    PHASE 4 (fifth audit), the blocking finding. A row in the outbox says a transition
    was ABOUT to be written; it has never said one was. So `recover --apply` replayed the
    event of a state write that raised, and the ledger claimed `F-0001 reported→accepted,
    revision 2` over a record that said `reported` at revision 1. The system's whole value
    is a history you can check, and it manufactured one.

    The commit fact is already written, atomically, by the state write itself:
    `ledger_anchor.state_revision` is the revision THAT write produced, and it lands in
    the same `os.replace` as the transition. Nothing new is stored and nothing is
    migrated. `state_revision` is the fallback for a document written before the anchor
    existed and for the first transition of a fresh project, where the anchor is absent
    rather than wrong.

    Returns None when canonical state cannot be read — which is neither a proof nor a
    disproof, and the caller must treat it as such rather than guessing in either
    direction.

    Read here with `json` rather than through `state.load_state` on purpose: this module
    is below `state` (`write.py` imports both) and a validating read would refuse a
    document this function's whole job is to inspect after a crash.
    """
    doc = _durable_doc(project)
    if doc is None:
        return None
    anchor = doc.get("ledger_anchor")
    if isinstance(anchor, dict) and isinstance(anchor.get("state_revision"), int):
        return anchor["state_revision"]
    rev = doc.get("state_revision")
    return rev if isinstance(rev, int) else None


def committed_txns(project):
    """The transaction ids canonical state PROVES it committed, newest last.

    PHASE 3 (sixth audit). `committed_revision` above answers with a COUNTER, and a
    counter cannot identify a transaction: `envelope.store` bumps the revision without
    touching the anchor, so a real `session_finished` looked like an event about a change
    that never happened; and any unrelated writer — the audit's `true` session — could
    advance the number past a reservation whose own write had failed, which made a
    transition that never happened replayable. Both errors are the same mistake, made in
    opposite directions.

    Returns None when canonical state cannot be read, and () when it carries no
    confirmations at all — a historical document, or a project that has not written since
    this phase. Those two are different answers and `uncommitted` treats them so.

    Read with `json` for the same reason `committed_revision` is: this module sits below
    `state`, and a validating read would refuse the very document this function exists to
    inspect after a crash.
    """
    doc = _durable_doc(project)
    if doc is None:
        return None
    commits = doc.get("ledger_commits")
    if not isinstance(commits, list):
        return ()
    return tuple(c for c in commits if isinstance(c, str))


def revision_of(anchor):
    """The committed revision an already-loaded anchor names, or the one on disk."""
    if isinstance(anchor, dict) and isinstance(anchor.get("state_revision"), int):
        return anchor["state_revision"]
    return None


#: What `uncommitted` answers when a row may not be appended. `hold` says whether the row
#: stays in the outbox for a run that can decide (unprovable) or is discarded now
#: (disproved). A bare string would have made "leave it" and "drop it" one value again,
#: which is how the counter rule came to make opposite errors in the first place.
Unproved = collections.namedtuple("Unproved", "hold why")


def uncommitted(entry, revision, txns=None):
    """Why this outbox row may not be appended, or None.

    The rule, stated once for both callers. A reserved row names the TRANSACTION whose
    state write it belongs to, and canonical state lists the transactions whose writes
    landed. The row is honoured exactly when its transaction is in that list — not when
    some number is big enough.

    PHASE 3 (sixth audit). The previous rule compared the row's `state_revision` against
    the revision canonical state had reached, and a revision is a counter:

      * `envelope.store` advanced it without updating the anchor the rule read, so a
        `session_finished` for a session that really had finished named a revision higher
        than the anchor and was DELETED as an event about a change that never happened;
      * any unrelated writer advanced it too, so a reservation whose own write had failed
        became replayable the moment something else — the audit used a session whose
        command was `true` — moved the number past it, and the journal claimed a
        transition that never happened.

    Three answers, not two. A row whose transaction has fallen out of the window is
    UNPROVABLE: it is held, reported, and left for a human or a later run, because
    discarding evidence on "we no longer remember" is the same class of error as inventing
    it. Rows without a `txn` are historical and keep the old revision rule, which is how
    an outbox written by an earlier version still drains without anything being migrated.
    """
    txn = entry.get("txn")
    if txn is not None:
        if txns is None:
            return Unproved(True, "canonical state is unreadable, so neither the "
                                  "transition nor its absence can be proved")
        if txn in txns:
            return None
        if not txns:
            # DISPROVED, not unknown. A row carries a `txn` only when this version wrote
            # it, and this version records a confirmation on every commit — so a state
            # document with no confirmations at all cannot be one this transaction wrote.
            return Unproved(False, f"canonical state records no committed transaction at "
                                   f"all, and {txn[:8]} would have left one had its write "
                                   f"landed: the transition it describes did not happen")
        want = (entry.get("line") or {}).get("state_revision")
        if (isinstance(want, int) and isinstance(revision, int)
                and want <= revision - len(txns)):
            return Unproved(True, f"transaction {txn[:8]} is older than the "
                                  f"{len(txns)} confirmations canonical state still "
                                  f"remembers, so this row can be neither proved nor "
                                  f"disproved")
        return Unproved(False, f"its transaction {txn[:8]} is not among the "
                               f"{len(txns)} canonical state committed: the transition it "
                               f"describes did not happen")
    want = (entry.get("line") or {}).get("state_revision")
    if not isinstance(want, int):
        # Nothing to prove. These are events that carry no transition — a session record,
        # a lease, a gate — and they were never the thing that could be false. Checked
        # BEFORE the readability of state, so a project with no canonical state at all
        # (a bare stream being recovered) is not held hostage over events that make no
        # claim about it.
        return None
    if revision is None:
        return Unproved(True, "canonical state is unreadable, so neither the transition "
                              "nor its absence can be proved")
    if want <= revision:
        return None
    return Unproved(False, f"it names state revision {want} and canonical state committed "
                           f"{revision}: the transition it describes did not happen")


def replay(project, revision=None):
    """Append every outbox entry the COMMITTED STATE proves, and discard the rest.

    Returns `(appended, already_there, discarded)`. Called at the start of any run that
    is about to write, so a stream that lost an append heals on the next verb rather than
    on a command somebody has to know about — and so a row from a write that never landed
    is dropped before the next transaction can be confused with it.

    A discarded row is REPORTED, never silently removed: the operator has to be able to
    tell "your ledger is complete" from "your ledger is complete because we threw
    something away". Dropping the row in an `except OSError` was never an option and is
    not one now — the process can be killed before any handler runs, so the rule has to
    be readable from durable state after the fact.
    """
    rows = pending(project)
    if not rows:
        return 0, 0, []
    # PHASE 4 (sixth audit). Nothing is appended onto a stream that is not what it claims
    # to be: a replayed event linked onto a tampered or rebuilt prefix would give the
    # tampering a valid successor. `short` is admitted BY THE TABLE, because these rows
    # are the part of a missing tail this tool can actually reconstruct.
    require_complete(project, committed_anchor(project), action="replay")
    # `revision` is passed by a caller that has already loaded canonical state (the
    # `recover` verb holds the anchor it checked the chain against); read here otherwise.
    # One rule either way — `uncommitted` is the only place that decides.
    revision = committed_revision(project) if revision is None else revision
    txns = committed_txns(project)
    have = {event_id(line) for line in _read_lines(project)}
    appended, present, discarded = 0, 0, []
    held = []
    done = set()
    for entry in rows:
        eid = entry.get("event_id")
        if eid in have or eid in done:
            present += 1
            done.add(eid)
            continue
        why = uncommitted(entry, revision, txns)
        if why is not None:
            if why.hold:
                # Unprovable, not disproved: leave it in the outbox for a run that can
                # decide, and say so rather than deciding on a coin flip. `done` is NOT
                # marked, which is what keeps the row out of `_drop`.
                held.append(f"{eid[:8]} {(entry.get('line') or {}).get('event')} — "
                            f"{why.why}")
                continue
            line = entry.get("line") or {}
            discarded.append(f"{eid[:8]} {line.get('verb') or line.get('event')} "
                             f"{line.get('target') or ''} — {why.why}")
            done.add(eid)
            continue
        try:
            append_chained(project, entry["line"])
        except OSError:
            break
        appended += 1
        done.add(eid)
    _drop(project, done)
    for row in held:
        print(f"note: an outbox row is HELD, not replayed and not discarded — {row}")
    return appended, present, discarded


def _read_lines(project):
    return [line for _n, line in _numbered_rows(events_path(project)) if line]


# PHASE 5 (fifth audit): ONE validated read boundary.
#
# `validate()` was strict and `envelope.read_events()` was a plain `json.loads` loop, so
# a stream this module called BROKEN was read happily by metrics, budgets, independence,
# receipts, OKF and the bundle — and `set-status` proceeded over it. The strict reader
# guarded a door nobody walked through. `read_stream()` is now that door and `read_events` is
# an adapter over it.
#
# The cache exists because the validated read walks the whole chain, and this repository's
# stream is 1,043 events: `status` alone asks for it several times per invocation, and a
# per-ask revalidation is a regression the phase measures. Its KEY is the file's identity
# — the three components below, enumerated rather than described:
#
#   path      two projects in one process are two streams;
#   st_size   any append changes it;
#   mtime_ns  an in-place edit that preserves the size does not change the size.
#
# What is NOT cached is a VERDICT. A failed validation raises every time it is asked,
# because a cached refusal would outlive the repair that fixed it.
_VALIDATED = {}


def read_identity(path):
    """The three components the cache key is made of. Raises OSError if it is gone."""
    st = path.stat()
    return (str(path), st.st_size, st.st_mtime_ns)


def forget_validated():
    """Drop the process's memory of validated streams. For tests and for a long-lived
    process that has just repaired one."""
    _VALIDATED.clear()


def read_stream(project):
    """Every event in the stream, or `ChainError` naming the line that is wrong.

    THE read boundary. A consumer that wants events gets a stream that has been proved
    to be what it claims, or it gets an error it cannot mistake for data.
    """
    path = events_path(project)
    if not path.exists():
        # Absent is not broken: a project that has never dispatched has no stream.
        # A directory or an unreadable file is a different answer and `validate` gives
        # it (phase 6) — this branch is only about a project that has written nothing.
        return []
    try:
        key = read_identity(path)
    except OSError:
        key = None
    if key is not None:
        seen = _VALIDATED.get(key[0])
        if seen is not None and seen[0] == key:
            return seen[1]
    lines = validate(project)
    if key is not None:
        _VALIDATED[key[0]] = (key, lines)
    return lines


# ---------------------------------------------------------------- the migration point

# Every event written before phase 8 carries no `v` and no `prev`, and none of them is
# touched: 1,043 of them are this project's own admitted evidence, and rewriting history
# to make a new check pass is the one thing a ledger may never do. So the chain starts at
# a DECLARED migration point — the first line that carries `v` — and the digest of the
# whole unchained prefix before it is that first event's `prev`.
#
# The prefix is therefore not trusted BY the chain; it is FIXED by it. Nothing can be
# inserted, removed or edited before the migration point without changing that digest,
# and the first chained event names it.


def split_at_migration(lines):
    """`(legacy_prefix, chained_suffix)` for a stream. The migration point is the first
    line carrying a schema version — declared by the data, in one place, rather than by a
    date or a count somebody has to keep current."""
    for i, line in enumerate(lines):
        if isinstance(line, dict) and SCHEMA_FIELD in line:
            return lines[:i], lines[i:]
    return list(lines), []


def prefix_digest(lines):
    """The rolling digest over a list of event lines, from GENESIS."""
    prev = GENESIS
    for line in lines:
        prev = link(prev, _encode(_unchained(line)))
    return prev


def _unchained(line):
    """The line without its chain fields — what the digest is taken over.

    The digest covers the EVENT, not the link: including `prev` in its own input would
    make each step depend on itself, and including `v` would make a future schema bump
    rewrite every historical digest.
    """
    return {k: v for k, v in line.items() if k not in (SCHEMA_FIELD, PREV_FIELD)}


def head(project):
    """The digest the next event must carry as its `prev`."""
    lines = _read_lines(project)
    legacy, chain = split_at_migration(lines)
    prev = prefix_digest(legacy)
    for line in chain:
        prev = link(prev, _encode(_unchained(line)))
    return prev


# ---------------------------------------------------------------- the strict reader

class ChainError(RuntimeError):
    """The stream cannot be read as an unmodified record. Names the line."""


# The kinds the reader knows. A kind it does not know is a REFUSAL, not a skipped row:
# the audit found the same defect one module over, where `retention.manifest_rows` walked
# past invalid JSON and returned a shorter list that read as a complete one.
KNOWN_EVENTS = (
    "session_dispatched", "session_finished", "session_result", "session_receipt",
    "session_abandoned", "state_transition", "write_refused", "lease_acquired",
    "lease_released", "retry_considered", "dispatch_key", "gate_reached",
)

# Present on every event ever written, and asserted over the real stream.
REQUIRED_FIELDS = ("ts", "event")


def validate(project, strict_from=None):
    """Read the stream and refuse it if it is not what it claims to be.

    Returns the lines. Raises `ChainError` naming the LINE NUMBER on:

      * a line that is not JSON, or is not an object;
      * a missing required field, or an event kind nobody emits;
      * a chained event whose schema version this reader does not know;
      * a broken chain link — which is reported at the FIRST line whose `prev` disagrees,
        because that line is where the edit is, not where the file ends.

    Truncation and tampering produce DIFFERENT refusals on purpose. A truncated stream is
    a machine that died; a tampered one is a person who edited. The operator's next move
    is not the same, so the message must not be.
    """
    rows = _numbered_rows(events_path(project))
    for n, line in rows:
        if line is None:
            raise ChainError(f"audit/{EVENTS_FILENAME}:{n}: not valid JSON — the stream "
                             f"is append-only and every line is one event object")
        if not isinstance(line, dict):
            raise ChainError(f"audit/{EVENTS_FILENAME}:{n}: expected an event object, "
                             f"got {type(line).__name__}")
        for field in REQUIRED_FIELDS:
            if field not in line:
                raise ChainError(f"audit/{EVENTS_FILENAME}:{n}: missing required field "
                                 f"{field!r}")
        if line["event"] not in KNOWN_EVENTS:
            raise ChainError(f"audit/{EVENTS_FILENAME}:{n}: unknown event kind "
                             f"{line['event']!r} — the reader refuses rather than "
                             f"skipping, because a skipped row reads as a complete "
                             f"stream that is short")
    lines = [line for _n, line in rows]
    legacy, chain = split_at_migration(lines)
    prev = prefix_digest(legacy)
    first = len(legacy)
    for offset, line in enumerate(chain):
        n = first + offset + 1
        if line.get(SCHEMA_FIELD) != LEDGER_SCHEMA:
            raise ChainError(
                f"audit/{EVENTS_FILENAME}:{n}: event schema "
                f"{line.get(SCHEMA_FIELD)!r}, this reader knows {LEDGER_SCHEMA}. A "
                f"reader that guessed at an unknown version would be reading a format "
                f"it has never seen as if it were this one.")
        if line.get(PREV_FIELD) != prev:
            raise ChainError(
                f"audit/{EVENTS_FILENAME}:{n}: the chain breaks HERE — this event "
                f"records the prefix digest {str(line.get(PREV_FIELD))[:16]}… and the "
                f"{n - 1} line(s) before it hash to {prev[:16]}…. Everything up to line "
                f"{n - 1} is intact; this line, or something before it, was modified "
                f"after it was written. The stream is append-only evidence and is never "
                f"edited in place — restore it from the last known-good copy.")
        prev = link(prev, _encode(_unchained(line)))
    return lines


def anchor_problems(project, anchor, lines=None):
    """Why the recorded chain anchor does not match this stream, or None.

    The chain alone is rebuildable: anyone who can edit the file can re-link every event
    after their edit and hand you a self-consistent stream. What they cannot do is reach
    into `audit/state.json` and change the digest recorded there at the last canonical
    transition, because that document is written under the lock by the orchestrator and
    carries its own revision. So the anchor is the half of this check that is not in the
    file being checked.
    """
    if not anchor:
        return None
    lines = _read_lines(project) if lines is None else lines
    legacy, chain = split_at_migration(lines)
    prev = prefix_digest(legacy)
    seen = {prev}
    for line in chain:
        prev = link(prev, _encode(_unchained(line)))
        seen.add(prev)
    if anchor.get("digest") in seen:
        return None
    return (f"audit/{EVENTS_FILENAME}: canonical state records a chain digest "
            f"{str(anchor.get('digest'))[:16]}… taken at state revision "
            f"{anchor.get('state_revision')}, and no prefix of this stream hashes to it. "
            f"A stream that is internally consistent but does not match the anchor "
            f"written under the writing lock has been REPLACED, not appended to.")


def truncation_problem(project, anchor, lines=None):
    """A stream shorter than the anchor says, reported as a missing tail.

    Separate from `validate` because the operator's next move differs: a stream that lost
    its tail to a crash or a partial copy may be recoverable from a backup or the outbox,
    while a tampered one was edited by someone.

    PHASE 10: this used to end "This is a lost tail, not an edit", which it cannot know.
    A short file whose remainder is a valid chain is EITHER a truncation OR a rebuilt
    stream that happens to be shorter, and nothing recorded distinguishes them: the anchor
    holds one digest taken at one point, not a digest per line, so there is no way to ask
    whether what survives is a prefix of what was recorded. The recovery drill produced
    exactly that case and got the confident wrong answer. The message now names both
    readings and the one command that settles it.
    """
    if not anchor or anchor.get("events") is None:
        return None
    have = len(_read_lines(project) if lines is None else lines)
    if have >= anchor["events"]:
        return None
    return (f"audit/{EVENTS_FILENAME}: TRUNCATED — canonical state recorded "
            f"{anchor['events']} event(s) at state revision "
            f"{anchor.get('state_revision')} and the file holds {have}. "
            f"{anchor['events'] - have} line(s) are missing from the end. That is either "
            f"a lost tail or a stream rebuilt shorter, and this tool cannot tell which: "
            f"compare against `git log -p -- audit/{EVENTS_FILENAME}` or a backup. Replay "
            f"audit/{OUTBOX_FILENAME} before writing again.")


# ---------------------------------------------------------------- the immutable prefix

# PHASE 9 (fourth audit), and the second half of this repository's own F-0104: "Event
# provenance is optional and rewriteable". That finding reproduced a session changing a
# COMMITTED event line from `{"event":"original"}` to `{"event":"rewritten"}`, after which
# `courier_commit` returned True and committed the rewrite. Phase 7 answered the first
# half (an append that fails no longer disappears); this answers the second.
#
# The rule is one sentence: a session may APPEND to the event stream and may not rewrite a
# byte of it. Expressed through `prefix_digest` — the same function the phase-8 reader
# validates with — because two functions computing stream identity is the next version of
# the `charter` versus `charter_hash` defect this run already had to fix.

REWRITTEN, TRUNCATED, APPENDED, UNCHANGED = (
    "rewritten", "truncated", "appended", "unchanged")


def _first_difference(before, after):
    """`(byte offset, line number)` of the first byte that differs."""
    limit = min(len(before), len(after))
    offset = next((i for i in range(limit) if before[i] != after[i]), limit)
    return offset, before[:offset].count(b"\n") + 1


def prefix_change(before_bytes, after_bytes):
    """How `after` relates to `before`: unchanged, appended, truncated or rewritten.

    Byte-level, because that is the claim — `audit/events.jsonl` is append-only and is
    never rewritten — and because a line-level comparison would call a re-serialised line
    with the same fields unchanged. It is not.
    """
    if after_bytes == before_bytes:
        return UNCHANGED, None
    if after_bytes.startswith(before_bytes):
        return APPENDED, {"bytes": len(after_bytes) - len(before_bytes)}
    if before_bytes.startswith(after_bytes):
        offset = len(after_bytes)
        return TRUNCATED, {"offset": offset,
                           "line": before_bytes[:offset].count(b"\n") + 1,
                           "lost": len(before_bytes) - offset}
    offset, line = _first_difference(before_bytes, after_bytes)
    return REWRITTEN, {"offset": offset, "line": line}


def prefix_problem(before_bytes, after_bytes, where, who):
    """The refusal for a rewritten or truncated stream, or None.

    `who` names the actor in the operator's terms ("the Auditor session"), because the
    remedy differs by actor and a message that says only "the stream changed" leaves the
    reader to work out whose change it was.
    """
    kind, detail = prefix_change(before_bytes, after_bytes)
    if kind in (UNCHANGED, APPENDED):
        return None

    # Digest over the surviving prefix, from the SAME function the phase-8 reader
    # validates with. What it adds to the byte compare is a name for the thing that was
    # supposed to be preserved, which is what the operator has to restore.
    # Cut back to the last complete line: the first differing byte is usually INSIDE an
    # event, and half an event is not one. What survives intact is what the operator has
    # to restore, so that is what the digest names.
    kept = before_bytes[:detail["offset"]]
    kept = kept[:kept.rfind(b"\n") + 1] if b"\n" in kept else b""
    kept_lines = []
    for raw in kept.decode("utf-8", "replace").splitlines():
        if raw.strip():
            try:
                kept_lines.append(json.loads(raw))
            except ValueError:
                break
    digest = prefix_digest(kept_lines)

    if kind == TRUNCATED:
        return (f"refusing to carry back {who}'s changes: it TRUNCATED {where}.\n"
                f"    the stream had {len(before_bytes)} byte(s) and now has "
                f"{len(after_bytes)}; {detail['lost']} byte(s) are gone from the end, "
                f"from line {detail['line']} onward\n"
                f"    the surviving {len(kept_lines)}-event prefix hashes to "
                f"{digest[:16]}…\n"
                f"The event stream is append-only evidence. A session that shortens it "
                f"has deleted the record of what it did, which is the one thing the "
                f"record exists to prevent. Nothing was applied.")
    return (f"refusing to carry back {who}'s changes: it REWROTE {where}.\n"
            f"    the first differing byte is at offset {detail['offset']}, in line "
            f"{detail['line']}\n"
            f"    the {len(kept_lines)} event(s) before it are intact and hash to "
            f"{digest[:16]}…\n"
            f"A session may APPEND to the event stream and may not rewrite a byte of it. "
            f"Rewriting is how a session edits the record of what it did. Nothing was "
            f"applied; the change is in the session's own patch if you want to read it.")


# ---------------------------------------------------------------- recovery (phase 10)

# The chain and the outbox are claims until somebody has broken a ledger and put it back.
# `xcheck recover` is that capability, and `tests/test_ledger_recovery.py` is the drill:
# each corruption mode run against a COPY of this repository's real 1,043-event stream,
# with one line per mode saying what is RECONSTRUCTED and what is REFUSED.
#
# The distinction is the whole product. A recovery command that fabricates a plausible
# missing event is worse than the gap it fills: the gap is visible and the fabrication is
# not. So exactly one thing is reconstructed — an event this tool itself wrote to the
# outbox and can prove it wrote — and everything else is reported for a human.

INTACT = "intact"
BROKEN = "broken"
SHORT = "short"
REPLACED = "replaced"
UNREADABLE = "unreadable"

# What `recover` can put back, by state. Declared rather than implied, because "what will
# this command do to my evidence" is the question an operator has to be able to answer
# before running it.
RECOVERABLE = {
    INTACT: "nothing to do",
    BROKEN: "REFUSED — a broken link is an edit, and this tool has no copy of what the "
            "line said before. Restore audit/events.jsonl from a backup or from the "
            "commit before the edit; `git log -p -- audit/events.jsonl` shows it.",
    SHORT: "REFUSED — the missing tail is not in this tool's hands. Pending outbox "
           "entries are replayed (that part IS reconstructed); anything beyond them was "
           "written by a process that is gone, OR was never written at all if the file "
           "was rebuilt. Restore from a backup and compare.",
    REPLACED: "REFUSED — the stream is internally consistent and is not the one canonical "
              "state recorded. Nothing here can tell which of the two is the real "
              "history, and picking one would be inventing an answer.",
    UNREADABLE: "REFUSED — the file cannot be read as a stream of events. Fix "
                "permissions or restore it; nothing is written until it reads.",
}


def status(project, anchor=None):
    """What state the ledger is in, as `(state, detail)`. Reads only.

    Order matters and is declared: UNREADABLE first (nothing else can be said about a
    file that will not parse), then BROKEN (an edit is the most serious thing that can be
    true of a stream that does parse), then SHORT and REPLACED, which are both statements
    about the anchor and not about the file.
    """
    try:
        # PHASE 4 (sixth audit): through `read_stream`, which caches a validated stream on
        # (size, mtime), rather than `validate` directly. `status` used to be a diagnostic
        # somebody ran by hand; it is now on the write path, and re-validating 1,043
        # events on every append is a cost the cache already knows how to avoid. The two
        # checks below are handed the SAME lines, so one parse answers all three
        # questions instead of three parses answering one each.
        lines = read_stream(project)
    except ChainError as e:
        text = str(e)
        state = BROKEN if "the chain breaks HERE" in text else UNREADABLE
        return state, text
    short = truncation_problem(project, anchor, lines=lines)
    if short:
        return SHORT, short
    replaced = anchor_problems(project, anchor, lines=lines)
    if replaced:
        return REPLACED, replaced
    return INTACT, (f"{len(lines)} event(s), chain intact"
                    + (f", matching the digest recorded at state revision "
                       f"{anchor.get('state_revision')}" if anchor else ""))


#: What `xcheck recover` exits with, and what each code MEANS. A closed set of two,
#: declared because the operator's script branches on it.
#:
#: PHASE 4 (sixth audit): `short` used to exit 0 beside `intact`, on the reasoning that a
#: missing tail is not a tamper. But the events are gone, and a 0 tells a CI job that the
#: evidence is fine. The code now reports the state the stream is in AFTER recovery has
#: done what it can: a short stream whose outbox rows filled the gap really is intact and
#: exits 0; one with events still missing exits 1, because that is what is true.
EXIT_CODES = {
    0: "the stream is a complete record of the history canonical state remembers",
    1: "the stream is NOT that record, and this tool could not make it one",
}


class IncompleteStream(ChainError):
    """The stream is not a complete record of the history canonical state remembers.

    Carries the `status` state by name — `broken`, `short`, `replaced`, `unreadable` —
    so a caller can print the operator's next move from `RECOVERABLE` rather than
    inventing its own wording for a condition this module already has words for.
    """

    def __init__(self, state, detail):
        super().__init__(detail)
        self.state = state
        self.detail = detail


#: What each ledger state FORBIDS, per ACTION. One table, so every question a caller can
#: ask is one rule with a declared policy rather than several rules that drift apart.
#:
#:   write   apply a transition and advance the checkpoint
#:   emit    append a fresh observational event
#:   replay  append rows THIS TOOL already reserved and can prove it wrote
#:
#: `short` is the row that makes the table worth having. Nothing new may be written or
#: emitted over a stream that is missing events canonical state remembers — a fresh event
#: appended to it changes the diagnosis from `short` to `replaced` and takes the operator's
#: evidence with it. The reserved rows are different: replaying them is exactly the
#: reconstruction a short stream needs, and is the one thing `RECOVERABLE[SHORT]` promises.
FORBIDS = {
    INTACT: (),
    BROKEN: ("write", "emit", "replay"),
    UNREADABLE: ("write", "emit", "replay"),
    REPLACED: ("write", "emit", "replay"),
    SHORT: ("write", "emit"),
}


def require_complete(project, anchor=None, action="write"):
    """Refuse unless the stream is BOTH internally consistent AND the one canonical state
    recorded. THE completeness check, used before every append, every replay and every
    checkpoint change.

    PHASE 4 (sixth audit), the blocking finding. `read_stream` proves the chain links up;
    it says nothing about whether the stream is the same one the checkpoint in
    `state.json` was taken over. That comparison lived only in `status`, the DIAGNOSTIC
    path — so `xcheck set-status` exited 0 over a truncated journal and over a rebuilt
    one, wrote a NEW checkpoint, and the diagnostic then answered `intact`. Corrupted
    history became the accepted baseline, with the lost evidence never recovered.

    "Internally consistent" and "complete with respect to the stored history" are
    different properties, and only the first was checked before writing.

    `anchor=None` means NO CHECKPOINT IS RECORDED, which is not a disagreement: this
    project's own 1,043 historical events carry none, and a rule that refused every write
    on them would have bricked the tool on its own audit. Read the anchor with
    `committed_anchor` at the call site, and pass what it answers.
    """
    state, detail = status(project, anchor)
    if action in FORBIDS[state]:
        raise IncompleteStream(state, detail)
    return state


def recover_report(project, anchor=None, apply=False):
    """The report `xcheck recover` prints, and the count of events it replayed.

    Read-only unless `apply` — same discipline as `prune-logs`, and for the same reason:
    the dry run has to be the thing an operator can run on a ledger they are worried
    about without making it worse.
    """
    state, detail = status(project, anchor)
    revision = revision_of(anchor)
    if revision is None:
        revision = committed_revision(project)
    waiting = pending(project)
    out = [f"ledger: {state}", f"  {detail}"]
    if waiting:
        out.append(f"  outbox: {len(waiting)} entry/entries pending "
                   f"({', '.join(e['event_id'][:8] for e in waiting[:5])}"
                   f"{' …' if len(waiting) > 5 else ''})")
    else:
        out.append("  outbox: empty")

    replayed = 0
    if state in (BROKEN, UNREADABLE, REPLACED):
        # Never append to a stream whose integrity is in question: a replayed event
        # linked onto a tampered prefix would make the tampering permanent by giving it
        # a valid successor.
        out.append(f"  {RECOVERABLE[state]}")
        if waiting:
            out.append("  the outbox is NOT replayed while the stream is in this state — "
                       "linking a new event onto a prefix under suspicion would make the "
                       "suspicion permanent.")
        return "\n".join(out), 0

    if waiting and not apply:
        # PHASE 3 (sixth audit): the SAME inputs `replay` decides on. A preview computed
        # from a different rule than the one `--apply` runs is worse than no preview — an
        # operator reads it precisely to decide whether to apply.
        txns = committed_txns(project)
        doubtful = [(e, uncommitted(e, revision, txns)) for e in waiting]
        keep = [e for e, why in doubtful if why is None]
        drop = [(e, why) for e, why in doubtful if why is not None and not why.hold]
        held = [(e, why) for e, why in doubtful if why is not None and why.hold]
        out.append(f"  would replay {len(keep)} outbox entry/entries, discard "
                   f"{len(drop)} and hold {len(held)} (re-run with --apply)")
        for e, why in drop:
            out.append(f"    would discard {e['event_id'][:8]} — {why.why}")
        for e, why in held:
            out.append(f"    would hold {e['event_id'][:8]} — {why.why}")
    elif waiting:
        replayed, already, discarded = replay(project, revision=revision)
        out.append(f"  replayed {replayed} entry/entries "
                   f"({already} already in the stream, {len(discarded)} discarded)")
        # Counted AND named. A discarded row is a transition somebody's verb reported
        # and the state never took; an operator who is told only a number has been told
        # the ledger is fine.
        for line in discarded:
            out.append(f"    discarded {line}")
    if state == SHORT:
        out.append(f"  {RECOVERABLE[SHORT]}")
    elif state == INTACT and not waiting:
        out.append(f"  {RECOVERABLE[INTACT]}")
    return "\n".join(out), replayed
