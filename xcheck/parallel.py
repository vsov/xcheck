"""Concurrent auditor passes behind ONE serialized state transition (P2 #12).

The audit that asked for this also said why it could not have been done earlier:
parallel *writers* on the old Markdown control plane would only have multiplied the
races that plane already had. What makes it reachable now is that there is exactly one
write path (`write._apply`) and exactly one authority (`state.json`) carrying a
`state_revision`. So the concurrency here is deliberately the narrowest kind that buys
anything:

    the agent SESSIONS run at the same time, each in its own disposable worktree;
    every write to the project is serialized, one pass at a time;
    each merge declares the revision it read, and a merge computed against a revision
    that is no longer current REFUSES and is re-derived against fresh state.

Nothing merges by guessing. The refusal is the point: two passes that both edited the
same record would otherwise resolve by whichever thread reached `write_state` last,
which is not a merge policy, it is a race with a tidy name.

**Ceilings, stated rather than discovered later.**

* Passes may run concurrently only when their unit sets are DISJOINT. Overlap is
  refused before anything is launched, because two passes reporting on the same unit
  produce findings a merge would have to choose between, and choosing is a human's job.
* The concurrency is between threads of ONE orchestrator process. Two `xcheck` processes
  are still serialized by `audit/.lock`, exactly as before.
* The merge window is short but it is not instantaneous: it spans a patch application, a
  state write and a courier commit. Throughput scales with the session time, not with
  the merge time — which is the right way round, since sessions take minutes.

Off by default (`parallel_passes=off`). A concurrency change enabled by default in a
beta tool is how a tool loses somebody's audit.
"""

import concurrent.futures as _futures
import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

from xcheck.state import StateError, load_state, to_document
from xcheck.util import conf_flag, conf_number
from xcheck.write import StaleRevision, _apply

# How many times one pass's merge may be re-derived against fresher state before the
# dispatcher gives up and hands the operator the refusal. Three is not a tuning
# parameter: with N passes merging serially, a merge can be overtaken at most N-1
# times, and a run wide enough to exceed this has a scheduling problem a retry cannot
# fix.
MERGE_ATTEMPTS = 3

# 0.9.1 phase 1 refused `parallel_passes=on` outright, because a parallel pass was not
# couriered: `harvest` lifted the finding RECORDS and the coverage record out of the
# session's `state.json`, and `sandbox.leave()` then deleted the worktree holding the
# finding bodies, the pass report and any split remainder. `write_views` regenerated a
# finding FILE from frontmatter, so the record looked filed and its evidence was gone.
#
# Phase 8 carries the artifacts (`stage_artifacts` -> `apply_artifacts`), so the refusal
# is no longer unconditional. It is not simply deleted either: what lifts it is a
# PROBE that runs the real apply path at the moment of the decision. A tool that decides
# from a version number decides from a memory of a fix; this one decides from a byte it
# just carried.
REFUSAL = """parallel_passes=on is refused: this build cannot preserve a pass's evidence.

  why     {reason}

          A parallel pass is not couriered — its findings are merged as records and its
          worktree is then destroyed. Artifact preservation is what carries the finding
          BODIES, the pass report and any split remainder across that teardown, and the
          probe above says this build's does not work. Running anyway would file
          findings with no evidence under them and turn `xcheck lint` red.

  fix     set `parallel_passes=off` in audit/orchestrator.conf. Queued passes run one at
          a time, exactly as they did before 0.9.0.

  return  the refusal lifts by itself when the probe passes; tests/test_parallel_
          preservation.py is what proves every artifact survives the teardown."""

DIVERGENCE = """parallel_passes=on is refused for this configuration: the two dispatchers
would not do the same thing.

  why     {reason}

          The sequential and parallel dispatchers share one launch preparation, one
          telemetry finish and one result check. This configuration does not survive that
          preparation, so running it in parallel would launch children under a command the
          sequential path would have refused — or refuse where the sequential path would
          have launched, which is the shape the sixth audit reported.

  fix     correct the configuration the reason names, or set `parallel_passes=off`. A
          sequential run is refused by the SAME preparation, so it will tell you the same
          thing before spending anything.

  return  the refusal lifts by itself: it is computed by running the preparation, not by
          remembering that something was once broken."""

# The probe's subject: bytes that only the apply path can put on disk. Deliberately
# shaped like the artifact whose loss the audit reproduced — a finding body with an
# evidence section under generated frontmatter.
PROBE_ARTIFACT = "audit/findings/PRESERVATION-PROBE.md"
PROBE_BYTES = b"---\nid: PROBE\n---\n\n## Evidence\n\nthe byte that must survive\n"


def preservation_probe():
    """Carry one artifact through the REAL apply path. `None` when it survived.

    Answering "can this build preserve evidence?" by consulting a flag, a version or a
    comment would put the answer at the mercy of the next edit. This calls
    `apply_artifacts` — the same function the merge calls — against a throwaway project
    in a system temp dir, and reads the byte back off disk. Stub the apply path out and
    the probe finds nothing on disk; break the hashing and it reports the mismatch. In
    both cases `enabled` refuses again, which is the coupling phase 8 owes the operator:
    the capability is available exactly while the machinery behind it works.
    """
    tmp = Path(tempfile.mkdtemp(prefix="xcheck-preservation-"))
    try:
        staged = tmp / "staged"
        staged.mkdir()
        blob = staged / "probe.blob"
        blob.write_bytes(PROBE_BYTES)
        man = Manifest("preservation-probe", staged)
        man.entries.append({
            "path": PROBE_ARTIFACT,
            "digest": hashlib.sha256(PROBE_BYTES).hexdigest(),
            "before": None,
            "staged": str(blob)})
        project = tmp / "project"
        (project / "audit").mkdir(parents=True)
        apply_artifacts(project, man)
        landed = project / PROBE_ARTIFACT
        if not landed.is_file():
            return (f"the apply path reported success and wrote nothing: "
                    f"{PROBE_ARTIFACT} is not on disk")
        got = landed.read_bytes()
        if got != PROBE_BYTES:
            return (f"the artifact landed with different bytes: {len(got)} on disk vs "
                    f"{len(PROBE_BYTES)} staged")
        return None
    except Exception as e:                     # a probe that raised has not preserved
        return f"the apply path raised {type(e).__name__}: {e}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def dispatch_divergence(conf):
    """Why the two dispatchers would not do the same thing with this configuration.

    PHASE 5 (sixth audit). The audit found one: `model_routing=on` with `{model}` in the
    role command launched a child sequentially and refused on an unfilled placeholder in
    parallel, because `parallel.py` built its own command with neither the route nor the
    sidecar, finished its envelopes without adapter or route, and chose its own log
    directory.

    It returns None for every configuration now, and that is a MEASUREMENT rather than an
    opinion: both dispatchers call `prepare_logs`, `dispatch_route`, `prepare_launch`,
    `finish_session` and `result_problem`, so a configuration either survives that
    preparation on both paths or is refused on both — including a configuration with no
    role command at all, which is refused identically and is not a divergence.

    It is kept, and asked before any child starts, because it is where a divergence WOULD
    be reported if one returned; what it must never become is a refusal that remembers a
    fix instead of measuring for one. `tests/test_parallel_dispatch.py` is the other half:
    a structural gate that fails if a dispatcher stops calling the shared preparation,
    which is the thing this function cannot see.
    """
    from xcheck.runner import dispatch_route, prepare_launch, resolve_profile
    probe = Path(tempfile.mkdtemp(prefix="xcheck-equivalence-"))
    try:
        (probe / "audit").mkdir(parents=True)
        try:
            route, _planned = dispatch_route(probe, conf, "Auditor", "P-01")
            plan = prepare_launch(conf, "Auditor", "PROMPT", "0" * 16,
                                  resolve_profile(conf, "Auditor"),
                                  probe / "logs" / "probe.log", route)
        except SystemExit:
            # The shared preparation refused. The SEQUENTIAL dispatcher calls the same
            # function and is refused the same way, so this is a bad configuration and
            # not a divergence — and `run_session` says why, in its own words, at the
            # point the operator asked for a session.
            return None
        # Read off the argv that preparation just produced, rather than assumed from the
        # fact that it ran: an unfilled placeholder reaching a provider fails for a reason
        # nothing in the log explains, and a missing sidecar means every parallel pass is
        # unmeasured while the sequential path measures.
        unfilled = [a for a in plan.cmd if "{" in a and "}" in a]
        if unfilled:
            return (f"the prepared command still carries unfilled placeholder(s) "
                    f"{unfilled}")
        if "{sidecar}" in conf.get("auditor_cmd", "") \
                and str(plan.sidecar_path) not in plan.cmd:
            return ("the role command names {sidecar} and the prepared command does not "
                    "carry the path: provider telemetry would be unmeasured for every "
                    "parallel pass while the sequential path measured it")
        return None
    finally:
        shutil.rmtree(probe, ignore_errors=True)


def enabled(conf):
    """True when parallel passes are on AND this build can preserve their evidence.

    The question is asked at the flag read rather than at the dispatch branch so that
    every caller which asks it gets the same answer, including one written after this.
    """
    if not conf_flag(conf, "parallel_passes"):
        return False
    broken = preservation_probe()
    if broken:
        raise SystemExit(REFUSAL.format(reason=broken))
    # PHASE 5 (sixth audit): and the second question, asked at the same place and before
    # any child starts — is this configuration one both dispatchers handle the same way?
    divergent = dispatch_divergence(conf)
    if divergent:
        raise SystemExit(DIVERGENCE.format(reason=divergent))
    return True


def queued_passes(state):
    """The queued entries, in queue order."""
    return [q for q in state.queue if not q.done]


def unit_overlaps(state, pass_ids):
    """`[(pid_a, pid_b, shared_units)]` for every overlapping pair. Empty is disjoint."""
    entries = {}
    for pid in pass_ids:
        entry = state.queue_entry(pid)
        if entry is None:
            raise StateError(f"no pass {pid!r} in the queue — a pass cannot be "
                             f"dispatched before it is queued")
        entries[pid] = set(entry.units)
    overlaps = []
    ids = list(pass_ids)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            shared = entries[a] & entries[b]
            if shared:
                overlaps.append((a, b, tuple(sorted(shared))))
    return overlaps


def refuse_overlaps(state, pass_ids):
    """Refuse BEFORE launch when two passes would report on the same unit.

    Before launch, not at merge time: by merge time two agent sessions have already
    spent their minutes producing findings that cannot both be right about the same
    unit, and the only remaining options are to discard work or to guess.
    """
    overlaps = unit_overlaps(state, pass_ids)
    if not overlaps:
        return list(pass_ids)
    detail = "; ".join(f"{a} and {b} both cover {', '.join(units)}"
                       for a, b, units in overlaps)
    raise StateError(
        f"refusing to run {len(pass_ids)} passes concurrently: their unit sets overlap "
        f"({detail}). Two passes reporting on one unit produce findings the merge would "
        f"have to choose between. Run them in sequence, or amend one pass's units.")


def id_block(state):
    """How many ids one pass reserves — DERIVED from the limit that bounds its filing.

    0.9.1 phase 7 (audit P1 #9, the arithmetic half). This was hard-coded at 50 while
    `max_findings_per_pass` is an operator-set limit whose schema range is 1..999. Set
    it to 200 and a pass could legitimately file its 51st finding into the NEXT pass's
    reserved block — two passes filing the same id, resolvable only by renumbering ids
    that the evidence bodies and every cross-reference already use.

    Deriving it makes that unrepresentable rather than recoverable: the block IS the
    limit, so a pass can exhaust its block only by exceeding a limit it was told, and
    `harvest` already refuses an id outside the block BY NAME. Two guards, and the
    second is why the first can be trusted: the reservation is arithmetic done before
    the session, the refusal is a check on what the session actually filed.
    """
    return int(state.limits.get("max_findings_per_pass", 50) or 50)


def reserve_ids(state, pass_ids, block=None):
    """A DISJOINT finding-id block per pass: `{pid: (first, last)}`.

    Without this, parallel passes collide by construction. Both sessions read the same
    state, both see the same highest id, and both file their first finding as the next
    one — a collision the merge could only resolve by renumbering, which would rewrite
    ids that the findings' own evidence bodies and every cross-reference already use.
    Reserving the block up front and putting it in the charter makes the collision
    unrepresentable instead of recoverable.

    `block` defaults to `id_block(state)` — the limit, not a constant. It stays an
    argument so a test can reserve a small block deliberately, and that override is
    visible at its call site rather than being a second source of truth.
    """
    if block is None:
        block = id_block(state)
    used = [int(r.id.rsplit("-", 1)[1]) for r in state.findings
            if r.id.rsplit("-", 1)[-1].isdigit()]
    start = max(used, default=0) + 1
    reserved = {}
    for pid in pass_ids:
        reserved[pid] = (start, start + block - 1)
        start += block
    return reserved


def worker_count(conf, n_passes):
    """How many sessions may be IN FLIGHT at once.

    0.9.1 phase 7 (audit P1 #9). The pool was `max_workers=len(pass_ids)` — one worker
    per non-overlapping pass, so the queue's length WAS the concurrency. Three hundred
    disjoint passes meant three hundred agent sessions dispatched and billed at once.

    Bounded below at 1 (a pool of zero workers is a hang, not less concurrency) and
    above by the number of passes (asking for eight workers to run two passes reserves
    six threads that can never receive work).
    """
    return max(1, min(conf_number(conf, "parallel_workers"), n_passes))


def _confirmed(prompt, assume_yes, stdin, announce):
    """Whether a human said yes — never by blocking a run that has no human.

    The order matters and is the whole point: an explicit flag first, a terminal
    second, and REFUSE third. A prompt reached from cron is a hang, and a hang in an
    orchestrator that holds `audit/.lock` is a hang that also blocks the next run.
    """
    if assume_yes:
        announce("fan-out confirmed by --yes")
        return True
    stream = stdin if stdin is not None else __import__("sys").stdin
    if stream is None or not getattr(stream, "isatty", lambda: False)():
        return False
    announce(prompt)
    return (stream.readline() or "").strip().lower() in ("y", "yes")


def refuse_oversized_run(conf, pass_ids, assume_yes=False, stdin=None, announce=print):
    """Three ceilings, all checked BEFORE anything is dispatched.

    Before, because every one of them is about work that costs money the moment it
    starts. A cap enforced at merge time cancels sessions that have already been paid
    for, which is not a cap, it is a way of wasting the budget more neatly.
    """
    n = len(pass_ids)

    limit = conf_number(conf, "max_sessions_per_run")
    if n > limit:
        raise StateError(
            f"refusing to dispatch {n} sessions in one run: max_sessions_per_run={limit}"
            f". Each pass is a billed agent session and this run would start {n - limit} "
            f"more than the ceiling allows. Raise max_sessions_per_run in "
            f"audit/orchestrator.conf if that is what you mean, or queue fewer passes.")

    ceiling = conf_number(conf, "parallel_budget_minutes")
    timeout = conf_number(conf, "session_timeout")
    worst = n * timeout / 60.0
    if ceiling and worst > ceiling:
        raise StateError(
            f"refusing to dispatch {n} sessions: worst case {n} x session_timeout "
            f"{timeout}s = {worst:.0f} min, over parallel_budget_minutes={ceiling}. "
            f"This is a WALL-CLOCK ceiling, not a monetary one — xcheck has no provider "
            f"cost telemetry and will not print a dollar figure it cannot measure. "
            f"Lower session_timeout, dispatch fewer passes, or raise the ceiling.")

    above = conf_number(conf, "parallel_confirm_above")
    if n <= above:
        return worker_count(conf, n)
    ok = _confirmed(
        f"about to dispatch {n} concurrent agent sessions "
        f"({', '.join(pass_ids)}), worst case {worst:.0f} min of billed time. "
        f"Type 'yes' to proceed: ", assume_yes, stdin, announce)
    if not ok:
        raise StateError(
            f"refusing to dispatch {n} sessions without confirmation "
            f"(parallel_confirm_above={above}). Re-run with `--yes` to confirm a "
            f"fan-out this size. A run with no terminal is REFUSED rather than "
            f"prompted: a prompt nobody can answer is a hang holding audit/.lock.")
    return worker_count(conf, n)


def block_charter(charter, first, last):
    """The charter plus its reserved id block, said as a bound the merge enforces."""
    return (f"{charter}\n\nThis pass runs CONCURRENTLY with other passes. File your "
            f"findings with ids F-{first:04d} through F-{last:04d} and no others — that "
            f"block is reserved for this pass alone, and the merge refuses any id "
            f"outside it.")


# --------------------------------------------------------------------------
# artifacts: what a pass MADE, as opposed to what it recorded
# --------------------------------------------------------------------------

# Four paths under `audit/` are never carried as bytes, each for a reason that is about
# who owns them rather than about tidiness:
#
#   state.json    the canonical document every pass merges INTO. Carrying one pass's
#                 copy over another's is precisely the overwrite this module refuses.
#   LEDGER.md     a view. `write_views` regenerates it from the merged state.
#   events.jsonl  an append-only stream with TWO writers — the session's verbs inside
#                 the sandbox and the dispatcher's own envelopes in the project. Copying
#                 the session's file over the project's would delete the dispatch record
#                 of the very session that wrote it. The merge emits its own transition
#                 events, so nothing that actually happened to the project is lost.
#   .lock         the lock. Carrying a lock file is carrying a claim on a lock.
#
# `orchestrator-logs/` is excluded by prefix for the same two-writer reason: the child's
# log is written project-side by the runner while the seeded copy sits in the worktree.
NOT_AN_ARTIFACT = ("audit/state.json", "audit/LEDGER.md", "audit/events.jsonl",
                   "audit/.lock")
# `capsules/` for a different reason: the capsule is an INPUT the dispatcher wrote before
# the session started, so a worktree copy travelling back as an artifact would be the
# session handing its own brief back as work.
NOT_AN_ARTIFACT_UNDER = ("audit/orchestrator-logs/", "audit/capsules/")


class Manifest:
    """Every artifact one pass produced, staged OUTSIDE the worktree.

    Outside is the whole point: the loss the audit reproduced happened because the only
    copy of the evidence was inside a directory the runner deletes in a `finally`. The
    staging directory is a system temp dir (never the project tree — F-0120), and it
    outlives `sandbox.leave()` by construction because nothing in the teardown path can
    name it.
    """

    def __init__(self, pid, staged_dir):
        self.pid = pid
        self.dir = Path(staged_dir)
        self.entries = []          # {path, digest, before, staged}

    def __len__(self):
        return len(self.entries)

    def paths(self):
        return [e["path"] for e in self.entries]

    def discard(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def __repr__(self):                                        # pragma: no cover
        return f"<Manifest {self.pid}: {len(self.entries)} artifact(s)>"


def _staged_changes(workdir, base):
    """Every path the session changed, asked of git with the tree fully staged.

    The `git add -A` is not a formality. `changed_paths` compares the INDEX against the
    seed commit, and a session's new files are not in the index until something stages
    them — so without this the answer is an empty list. 0.9.0's "did this auditor edit
    the material?" guard in `agent_pass` read exactly that empty list, which made it a
    guard that could not fire; the same call now feeds both the guard and the manifest,
    so the authorization input and the carried bytes cannot describe two different sets
    of changes (`runner.changed_paths` carries the scar from the time they did).
    """
    from xcheck.runner import changed_paths

    subprocess.run(["git", "add", "-A"], cwd=str(workdir), capture_output=True,
                   timeout=120)
    return changed_paths(workdir, base)


def _digest_at(workdir, base, rel):
    """The sha256 of `rel` as it stood at the seed commit, or None if it was absent.

    This is the compare-and-set input: the project is expected to still hold these bytes
    at merge time, because the seed is a copy of the project as the session found it.
    """
    p = subprocess.run(["git", "show", f"{base}:{rel}"], cwd=str(workdir),
                       capture_output=True, timeout=120)
    if p.returncode != 0:
        return None
    return hashlib.sha256(p.stdout).hexdigest()


def stage_artifacts(workdir, base, pid, changed=None):
    """Copy every audit artifact the session wrote out of the worktree, with hashes.

    `changed` may be passed by a caller that already asked (`agent_pass` asks once and
    uses the answer twice); it is derived here otherwise.

    A pass that DELETED an audit artifact is refused by name rather than carried. A
    concurrent auditor reports; removing another pass's evidence is not a report, and a
    deletion merged transactionally is still a deletion.
    """
    workdir = Path(workdir)
    if changed is None:
        changed = _staged_changes(workdir, base)
    rels = [p for p in changed
            if p.startswith("audit/") and p not in NOT_AN_ARTIFACT
            and not p.startswith(NOT_AN_ARTIFACT_UNDER) and not p.endswith(".tmp")]
    gone = [p for p in rels if not (workdir / p).is_file()]
    if gone:
        raise StateError(
            f"refusing to merge pass {pid}: it DELETED {', '.join(gone[:5])} under "
            f"audit/. A concurrent pass may add its own evidence; removing evidence "
            f"that was there before it started is a change no merge will make.")
    staged = Path(tempfile.mkdtemp(prefix=f"xcheck-artifacts-{pid.lower()}-"))
    man = Manifest(pid, staged)
    for i, rel in enumerate(rels):
        data = (workdir / rel).read_bytes()
        blob = staged / f"{i:03d}.blob"
        blob.write_bytes(data)
        man.entries.append({
            "path": rel,
            "digest": hashlib.sha256(data).hexdigest(),
            "before": _digest_at(workdir, base, rel),
            "staged": str(blob)})
    return man


def _restore(project, backup):
    """Put back exactly what was there: bytes for a file that existed, absence for one
    that did not. Directories this apply created are removed too, up to `audit/`."""
    project = Path(project)
    for rel, data in backup.items():
        p = project / rel
        if data is None:
            if p.is_file():
                p.unlink()
            d = p.parent
            while d != project / "audit" and d != project and not any(d.iterdir()):
                d.rmdir()
                d = d.parent
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)


def apply_artifacts(project, manifest):
    """Land every artifact, or none of them. Returns an `undo()` for the caller.

    Three checks, in an order that is the transaction:

    1. compare-and-set on EVERY path first, before a byte is written. A path whose
       current content is not what the session started from was written by somebody
       else, and two passes writing one path with different content is a conflict a
       merge must not resolve by ordering.
    2. write, keeping the previous bytes (or the fact of absence) for each path.
    3. verify what LANDED, read back off disk. A truncated or corrupted write is caught
       here rather than by `lint` a week later.

    Any failure in 2 or 3 restores everything written so far and raises: the project is
    byte-identical to before the call.

    The window between this call and the state merge is not locked. It does not need to
    be here: merges run one at a time in the dispatcher's own thread, and two `xcheck`
    processes are serialized by `audit/.lock`. The compare-and-set is what makes that a
    checked assumption rather than a hoped-for one.
    """
    project = Path(project)
    if manifest is None or not manifest.entries:
        return lambda: None

    conflicts = []
    todo = []
    for e in manifest.entries:
        p = project / e["path"]
        now = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
        if now == e["digest"]:
            continue                       # already exactly these bytes: nothing to do
        if now != e["before"]:
            conflicts.append(
                f"{e['path']} (the pass started from "
                f"{'no file' if e['before'] is None else e['before'][:12]}, the project "
                f"now holds {'no file' if now is None else now[:12]})")
            continue
        todo.append(e)
    if conflicts:
        raise StateError(
            f"refusing to merge pass {manifest.pid}'s artifacts: {len(conflicts)} path"
            f"(s) changed under it — {'; '.join(conflicts[:5])}. Another pass or a human "
            f"wrote them after this session read them, and choosing between two versions "
            f"of one piece of evidence is a human's decision. Nothing was written.")

    backup = {}
    try:
        for e in todo:
            p = project / e["path"]
            backup[e["path"]] = p.read_bytes() if p.is_file() else None
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(Path(e["staged"]).read_bytes())
        for e in todo:
            landed = hashlib.sha256((project / e["path"]).read_bytes()).hexdigest()
            if landed != e["digest"]:
                raise StateError(
                    f"refusing pass {manifest.pid}'s artifacts: {e['path']} landed as "
                    f"{landed[:12]} but the session produced {e['digest'][:12]}. The "
                    f"bytes changed between the sandbox and the project, so what would "
                    f"be filed as evidence is not the evidence.")
    except Exception:
        _restore(project, backup)
        raise
    written = dict(backup)
    return lambda: _restore(project, written)


def harvest(base_state, session_state, pid, reserved=None):
    """What one pass PRODUCED, as a `mutate` for the single write path.

    Deliberately not a patch. Two sessions that both edited `state.json` produce two
    diffs against the same lines, and the second `git apply` fails — the file is one
    JSON document, not a set of independent hunks. So the pass's product is READ from
    the session's own state (the records it filed, its coverage report) and re-applied
    to whatever the project's state is by the time the merge runs.
    """
    base_ids = {r["id"] for r in to_document(base_state)["findings"]}
    sdoc = to_document(session_state)
    new = [r for r in sdoc["findings"] if r["id"] not in base_ids]
    foreign = [r["id"] for r in new if r.get("pass") != pid]
    if foreign:
        raise StateError(
            f"refusing to merge pass {pid}: it filed {', '.join(foreign)} against another "
            f"pass. A concurrent pass may only report on its own charter — a finding "
            f"attributed elsewhere would land in a pass this session never ran.")
    if reserved:
        first, last = reserved
        outside = [r["id"] for r in new
                   if not (r["id"].rsplit("-", 1)[-1].isdigit()
                           and first <= int(r["id"].rsplit("-", 1)[1]) <= last)]
        if outside:
            raise StateError(
                f"refusing to merge pass {pid}: {', '.join(outside)} fall outside its "
                f"reserved id block F-{first:04d}..F-{last:04d}. The block is what makes "
                f"two concurrent passes unable to collide; a finding outside it may be "
                f"an id another pass is already using.")
    entry = next((q for q in sdoc["queue"] if q["id"] == pid), None)
    # A split remainder is a pass's product too. The audit named it beside the finding
    # bodies and the report for a reason: a pass that discovers its charter is twice the
    # size it was scoped for queues the rest, and 0.9.0 dropped that queue entry on the
    # floor with the worktree. It is a RECORD, not a file, so it merges here rather than
    # through the manifest.
    base_pids = {q["id"] for q in to_document(base_state)["queue"]}
    remainder = [q for q in sdoc["queue"] if q["id"] not in base_pids]

    def mutate(doc, state):
        for rec in new:
            if any(x["id"] == rec["id"] for x in doc["findings"]):
                raise StateError(
                    f"refusing to merge pass {pid}: {rec['id']} already exists in the "
                    f"current state. Nothing was written.")
            doc["findings"].append(dict(rec))
        for q in remainder:
            if any(x["id"] == q["id"] for x in doc["queue"]):
                raise StateError(
                    f"refusing to merge pass {pid}: it queued {q['id']}, which already "
                    f"exists in the current queue. Two passes cannot both define one "
                    f"pass id. Nothing was written.")
            doc["queue"].append(dict(q))
        covered = False
        if entry is not None and entry.get("coverage"):
            for q in doc["queue"]:
                if q["id"] == pid:
                    q["coverage"] = dict(entry["coverage"])
                    q["done"] = entry["done"]
                    covered = True
        return (f"merged pass {pid}: {len(new)} finding(s)"
                + (f", {len(remainder)} pass(es) queued" if remainder else "")
                + (", coverage recorded" if covered else ", no coverage report"),
                {"pass": pid, "merged_findings": len(new),
                 "queued_passes": [q["id"] for q in remainder]})

    return mutate


def merge(project, mutate, expect_revision, verb="merge-pass", target=None,
          attempts=MERGE_ATTEMPTS, announce=print, lock=None):
    """Apply one pass's result through the single write path, optimistically.

    `mutate(doc, state)` is the pass's change, and it is called AGAIN on each retry —
    against the document as it actually is now, never against a stale copy. That is what
    "retries against fresh state" has to mean: replaying a diff computed earlier would
    be the overwrite this refuses.

    Returns the number of attempts it took. Raises `StaleRevision` if it never landed.
    """
    from xcheck.envelope import current_revision

    for attempt in range(1, attempts + 1):
        try:
            _apply(project, mutate, role="merge", verb=verb, target=target,
                   expect_revision=expect_revision, lock=lock)
            return attempt
        except StaleRevision as stale:
            announce(f"merge refused ({verb}{' ' + target if target else ''}, attempt "
                     f"{attempt} of {attempts}): {stale}")
            if attempt == attempts:
                raise
            # Re-read: the next attempt is computed against what is there NOW.
            expect_revision = current_revision(project)
            if expect_revision is None:                        # pragma: no cover
                raise
    raise AssertionError("unreachable")                        # pragma: no cover


def run_passes(project, conf, pass_ids, session, announce=print, assume_yes=False,
               stdin=None, lock=None):
    """Run `pass_ids` concurrently and merge their results one at a time.

    `session(pid)` runs one pass — the agent session, in its own worktree — and returns
    `(mutate, manifest)`: the `mutate(doc, state)` that carries its findings into state,
    and the `Manifest` of every artifact it wrote, staged outside the worktree. A bare
    `mutate` is accepted for a session that produces no files, and None when the pass
    produced nothing at all. It is called from a worker thread and must not write the
    project itself; that is what the merge is for.

    Artifacts land BEFORE the state merge, and are rolled back if the merge does not.
    Before, because `write_views` renders each record's frontmatter over whatever body
    is on disk: land the body first and the merge tops it with generated frontmatter,
    land it after and it overwrites the frontmatter the state just rendered.

    Returns `[(pid, attempts, error)]` in MERGE order, which is completion order: a pass
    that finishes first merges first, so a slow pass never holds a finished one's
    findings hostage.
    """
    from xcheck.envelope import current_revision

    state = load_state(Path(project) / "audit")
    refuse_overlaps(state, pass_ids)
    # The ceilings come BEFORE the announcement, because the announcement reads like a
    # commitment and this is the last point at which nothing has been spent.
    workers = refuse_oversized_run(conf, pass_ids, assume_yes, stdin, announce)
    base = state.state_revision
    announce(f"parallel: {len(pass_ids)} passes with disjoint unit sets "
             f"({', '.join(pass_ids)}), at most {workers} in flight at once, "
             f"all computed against state revision {base}")

    results = []
    with _futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(session, pid): pid for pid in pass_ids}
        for done in _futures.as_completed(futures):
            pid = futures[done]
            try:
                produced = done.result()
            except Exception as e:                    # a failed pass is not a failed run
                announce(f"parallel: pass {pid} failed before merging — {e}")
                results.append((pid, 0, e))
                continue
            mutate, manifest = produced if isinstance(produced, tuple) else (produced,
                                                                            None)
            if mutate is None:
                announce(f"parallel: pass {pid} produced nothing to merge")
                if manifest is not None:
                    manifest.discard()
                results.append((pid, 0, None))
                continue
            undo = None
            try:
                undo = apply_artifacts(project, manifest)
                if manifest:
                    announce(f"parallel: pass {pid} carried {len(manifest)} artifact(s) "
                             f"out of its worktree — {', '.join(manifest.paths()[:4])}")
                # Every pass declares the revision IT read. The first merge matches and
                # lands; the second is behind by exactly the first merge and is refused,
                # then re-derived. That refusal on the ordinary two-pass path is not a
                # rare corner — it is the normal case, which is why it must be a
                # first-class outcome rather than an error.
                attempts = merge(project, mutate, base, verb="merge-pass", target=pid,
                                 announce=announce, lock=lock)
                results.append((pid, attempts, None))
            except (StaleRevision, StateError) as e:
                # The artifacts came in ahead of the state that explains them. A merge
                # that did not land must leave none of them behind, or the next `lint`
                # reports evidence for a finding no record names.
                if undo is not None:
                    undo()
                    announce(f"parallel: pass {pid} did not merge — its artifacts were "
                             f"rolled back")
                results.append((pid, MERGE_ATTEMPTS if isinstance(e, StaleRevision) else 0,
                                e))
            finally:
                if manifest is not None:
                    manifest.discard()
    final = current_revision(project)
    announce(f"parallel: state revision {base} -> {final} "
             f"({sum(1 for _, a, err in results if a and err is None)} merges applied)")
    return results



def agent_pass(project, conf, pass_ids, live=(), announce=print):
    """The production `session(pid)` for `run_passes`: one Auditor session per pass.

    Built from the same primitives as `runner.run_session` — the same profile
    resolution, the same uncontained-grant refusal, the same invocation envelope, the
    same bounded and redacted child — and differing in exactly one way, which is the
    reason it exists: **it never calls `sandbox.collect()`**. `collect()` brings a
    session's work back as a patch, and two concurrent passes both edit
    `audit/state.json`; the second patch would not apply, because the file is one JSON
    document and not a set of independent hunks. The pass's product is harvested from
    the session's own state instead and merged through the single write path.

    A pass that changed the MATERIAL is refused: an auditor pass reports, it does not
    edit what it is auditing, and a concurrent auditor editing the material would be a
    second writer on the one tree the merge cannot serialize.
    """
    import os

    from xcheck import envelope
    # PHASE 5 (sixth audit): the shared preparation, not a second copy of it.
    # `build_cmd` is deliberately NOT imported here — a dispatcher that can reach the
    # builder can build without a route, which is exactly what this phase removed.
    from xcheck.runner import (PROMPTS, Sandbox, child_environment, dispatch_route,
                               prepare_launch, prepare_logs, require_trust,
                               finish_session, resolve_profile, result_problem,
                               run_child)
    from xcheck.state import git_head
    from xcheck.util import serialized

    project = Path(project)
    base_state = load_state(project / "audit")
    pass_ids = list(pass_ids)
    reserved = reserve_ids(base_state, pass_ids)
    # One name for this launch, carried by every envelope in it. Several dispatches may
    # be open at once ONLY because they say so: `state._validate` allows it when every
    # open record declares the same group, and refuses undeclared concurrency exactly
    # as it did before this phase.
    group = "grp-" + os.urandom(4).hex()
    # Which sessions of this group are RUNNING. Mutated only inside the merge window,
    # so the set a store() reads is never half-updated.
    live_ids = set(live)

    def session(pid):
        entry = base_state.queue_entry(pid)
        charter = block_charter(entry.charter if entry else pid, *reserved[pid])
        prompt = PROMPTS["Auditor"].format(charter=charter)
        profile = resolve_profile(conf, "Auditor")
        # PHASE 3: the parallel dispatcher is a second door to the same room, so it
        # carries the same lock. A fan-out that skipped the trust gate would be the
        # cheapest way to audit untrusted material under a worktree.
        require_trust(conf, "Auditor", profile)
        session_id = os.urandom(8).hex()
        child_env = child_environment(conf, {"XCHECK_SESSION_ID": session_id})
        # PHASE 5 (sixth audit). Three shared functions, in the order the sequential
        # dispatcher calls them: the operator's log root (this path used to hardcode
        # `audit/orchestrator-logs`), the route at the door (this path used to resolve
        # none), and the one build that carries the route AND the sidecar (this path
        # used to pass neither, which is why the same configuration launched a child
        # sequentially and refused on an unfilled `{model}` in parallel).
        logs, log_path = prepare_logs(project, conf, f"auditor-{pid.lower()}")
        route, planned_model = dispatch_route(project, conf, "Auditor", charter)
        plan = prepare_launch(conf, "Auditor", prompt, session_id, profile, log_path,
                              route)
        cmd = plan.cmd
        logs.mkdir(parents=True, exist_ok=True)
        with serialized():
            rec = envelope.dispatch_record(
                cmd, "Auditor", charter, prompt, session_id, profile,
                envelope.current_revision(project), head_before=git_head(project),
                env=child_env, conf=conf, concurrent_group=group,
                # PHASE 5 (sixth audit): the ROUTE is recorded here, not by `finish` —
                # which is why a parallel pass's envelope named no route at all and
                # every metric aggregating by route silently excluded it.
                route=route)
            envelope.make_sidecar_dir(log_path)
            live_ids.add(session_id)
            envelope.store(project, rec, event="session_dispatched", live=live_ids)
        rc, outcome, elapsed = 1, "crash", 0.0
        sandbox = Sandbox(project, profile, f"Auditor-{pid}", logs, conf=conf)
        try:
            sandbox.enter()
            launch = sandbox.wrap(cmd, conf, child_env)
            rc, outcome, elapsed = run_child(
                launch, sandbox.workdir, log_path, child_env, conf, profile=profile,
                # Phase 9: the image-pin waiver goes into THIS pass's log too. A
                # parallel run's console interleaves several passes; the log is the
                # only place the sentence is unambiguously about one session.
                header=[f"xcheck: {sandbox.image_waiver}"] if sandbox.image_waiver
                else (),
                broker=sandbox.broker)
            # PHASE 5 (sixth audit): the same result check the sequential path makes,
            # and BEFORE the merge — a pass whose actual model disagrees with its plan
            # is not merged, it is answered. The sequential path got this in the fifth
            # audit; a guarantee that held on one of two dispatchers was never a
            # guarantee about xcheck.
            refusal = result_problem(conf, log_path, session_id, planned_model, route)
            if refusal and outcome == "ok":
                announce(f"parallel: pass {pid} REFUSED before the merge — {refusal}")
                outcome = "refused"
            announce(f"parallel: pass {pid} finished — {outcome} "
                     f"(exit={rc}, {elapsed / 60:.1f} min)")
            if outcome != "ok":
                return None
            # ONE question to git, two answers derived from it: what the session may
            # change, and what it made. Asking twice is how the courier's authorization
            # bypass survived — the gate read one derivation and the patch carried
            # another.
            changed = _staged_changes(sandbox.workdir, sandbox.base)
            touched = [f for f in changed if not f.startswith("audit/")]
            if touched:
                raise StateError(
                    f"refusing to merge pass {pid}: the session changed material outside "
                    f"audit/ ({', '.join(touched[:5])}). A concurrent auditor pass "
                    f"reports; it does not edit the material it is auditing.")
            mutate = harvest(base_state, load_state(sandbox.workdir / "audit"), pid,
                             reserved[pid])
            # Staged BEFORE the `finally` runs, because the `finally` is what deletes
            # the only other copy. This line is the phase-8 fix in one place.
            return mutate, stage_artifacts(sandbox.workdir, sandbox.base, pid, changed)
        finally:
            # The envelope is closed on EVERY path, including the refusal above: an
            # open dispatch that nobody finished is what the next run reports as an
            # abandoned session, and this one was not abandoned, it was answered.
            with serialized():
                # PHASE 5 (sixth audit): the same telemetry fields the sequential path
                # records. Without the adapter the provider's usage object is never
                # parsed; without planned_model and route the envelope of a routed pass
                # says nothing about the route it was dispatched under, and every metric
                # aggregating by route silently excluded every parallel session.
                envelope.store(project,
                               finish_session(conf, rec, rc, outcome, elapsed,
                                              git_head(project), log_path,
                                              sandbox_seed=getattr(sandbox, "base", None),
                                              planned_model=planned_model, route=route),
                               event="session_finished", live=live_ids)
            sandbox.leave()

    return session
