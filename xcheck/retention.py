"""Where raw session logs live, and how they stop accumulating.

The audit measured the growth: `audit/` is 104 MB, of which 102 MB is
`audit/orchestrator-logs/` — 159 session logs, roughly 900 KB each, one more per
dispatch, forever. They are already gitignored, so `.git` does not carry them; what pays
is everything that walks the working tree: `git status`, `ls-files --others`, worktree
creation, backups, any corpus scan, and the operator's own `du`.

Three decisions, and they are separate on purpose:

  1. WHERE new logs go. Outside the working tree by default, at a path resolved in a
     stated order. The digest stays in the event stream, so the `log_digest` join that
     every existing reader and the frozen Ouroboros-4 fixture depend on is untouched.
  2. WHAT stays in the repository. A manifest with checksums — small, append-only,
     durable — and never the raw bytes.
  3. WHEN anything is deleted. Never during a run. `xcheck prune-logs` is an explicit
     verb and reports by default; deleting requires `--apply`.

NOT in scope, deliberately: rewriting git history. It is destructive, it invalidates
every digest already recorded, and it is the operator's call, not a tool's.
"""

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import subprocess

from xcheck import policy
from xcheck.util import GIT_TIMEOUT

class RetentionError(Exception):
    """A manifest that cannot be read as an index. Raised rather than returned, because
    every caller's honest fallback would otherwise be an empty list — the one answer that
    reads exactly like "this project has no logs"."""


# The four answers `find` distinguishes, plus the fifth for a session the manifest never
# recorded. Named ONCE: the audit's finding was that deletion, corruption, a wrong path, a
# moved machine and a deliberate prune all arrived as the single word `pruned`, which is
# the forensic difference the manifest exists to keep.
PRESENT, PRUNED, MISSING, CORRUPT, UNRECORDED = (
    "present", "pruned", "missing", "corrupt", "unrecorded")
LOG_STATES = (PRESENT, PRUNED, MISSING, CORRUPT, UNRECORDED)

LOG_DIR_ENV = "XCHECK_LOG_DIR"
XDG_STATE_ENV = "XDG_STATE_HOME"
MANIFEST_NAME = "logs-manifest.jsonl"
LEGACY_LOG_DIRNAME = "orchestrator-logs"
# Frozen evidence. Nothing here may write under it, and a test asserts it.
FROZEN = ("audit-archive",)


def log_search_order(project, conf=None):
    """Where a log root is looked for, in order, as (source, path-or-None) pairs.

    The home directory is never derived here. Same rule as the operator profile and for
    the same reason: a tool that works out where the operator lives has decided something
    about the machine it was not told. A shipped test greps this module for the calls
    that would do it — and greps for them by a split needle, because a module that names
    them in its own prose fails its own check.
    """
    conf_dir = str((conf or {}).get("log_dir", "") or "").strip() if conf else ""
    env_dir = os.environ.get(LOG_DIR_ENV)
    xdg = os.environ.get(XDG_STATE_ENV)
    name = Path(project).resolve().name
    return [
        ("log_dir in the operator profile", Path(conf_dir) if conf_dir else None),
        (f"${LOG_DIR_ENV}", Path(env_dir) if env_dir else None),
        (f"${XDG_STATE_ENV}/xcheck/logs/<project>",
         Path(xdg) / "xcheck" / "logs" / name if xdg else None),
        ("the system temp directory",
         Path(tempfile.gettempdir()) / "xcheck-logs" / name),
    ]


def log_root(project, conf=None, create=True):
    """The directory raw session logs are written to. Always outside the working tree.

    The last entry in the search order is a temp directory, so this always answers —
    losing a log to an unset environment variable would be worse than putting it
    somewhere the operator can be told about. The chosen source is printed once per
    dispatch by `describe`, so "somewhere in /tmp" is never a surprise.
    """
    for _source, path in log_search_order(project, conf):
        if path is not None:
            # Frozen first: `<project>/audit-archive` trips both checks, and "that is
            # finished evidence" is the more useful of the two answers.
            refuse_frozen(path)
            refuse_outside_subject(path, project, "log_dir", LOG_DIR_ENV,
                                   "session logs")
            if create:
                path.mkdir(parents=True, exist_ok=True)
            return path
    raise RuntimeError("unreachable: the search order ends with a temp directory")


def describe_log_root(project, conf=None):
    """One line naming where logs are going and why that path won."""
    for source, path in log_search_order(project, conf):
        if path is not None:
            return f"session logs: {path} (from {source})"
    return "session logs: nowhere"


def refuse_frozen(path):
    """`audit-archive/` is frozen evidence: read and copy, never write.

    A retention path that resolved into it would delete the one copy of a finished run
    while reporting it as routine housekeeping."""
    parts = Path(path).resolve().parts
    for frozen in FROZEN:
        if frozen in parts:
            raise SystemExit(
                f"refusing to use {path} for session logs: `{frozen}/` is frozen "
                f"evidence — it is read and copied, never written. Point `log_dir` "
                f"(or ${LOG_DIR_ENV}) somewhere else.")
    return path


# PHASE 4 (fourth audit). Both root resolvers said "always outside the working tree" in
# their own docstrings and checked only that `audit-archive` was absent from the path. So
# `log_dir=<project>/audit/logs` and `evidence_dir=<project>/audit/cache` were ACCEPTED —
# reproduced before this was written — and a derived store the audited tree can write is
# a store the next session can edit. Same defect as the operator profile had one
# directory over, and this is the same fix: canonical resolution, one predicate.
#
# ONE interface, two callers. `log_root` and `cache_root` both come through here, so the
# rule cannot drift between them — which is how one of the two ends up with the string
# compare that calls `<project>-notes` a subdirectory of `<project>`.


def subject_roots(project):
    """Every checkout of the subject a session could be writing through.

    Not only the main working tree. The orchestrator runs writing roles in a disposable
    `git worktree`, and a worktree is inside the subject for this purpose: a log root
    pointed at one is a log root the session under audit can rewrite, and the courier
    carries it home. `git worktree list` is what "the worktrees the orchestrator knows
    about" means concretely; a project that is not a git repository has exactly one root
    and the call is allowed to fail saying so.
    """
    root = Path(project).resolve()
    roots = [root]
    try:
        out = subprocess.run(["git", "-C", str(root), "worktree", "list", "--porcelain"],
                             capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return roots
    if out.returncode != 0:
        return roots
    for line in out.stdout.splitlines():
        if line.startswith("worktree "):
            other = Path(line[len("worktree "):].strip()).resolve()
            if other not in roots:
                roots.append(other)
    return roots


def root_location_problem(path, project, key, env, what):
    """Why this root may not be used for this project, or None.

    Canonical on both sides via `policy.is_inside`, which is also what refuses an
    operator profile inside the subject. A `startswith` here would refuse `<project>-notes`
    beside `<project>` — a directory that is a sibling, not a child — and that control is
    in the test file for exactly this reason.
    """
    given = Path(path)
    for root in subject_roots(project):
        if policy.is_inside(given, root):
            target = given.resolve()
            via = ""
            if given.is_symlink() or target != (given.parent.resolve() / given.name):
                via = f"\n    reached through a symlink: {given} -> {target}"
            main = Path(project).resolve()
            label = "the project" if root == main else "a worktree of the project"
            return (
                f"refusing to use {given} for {what}: it resolves INSIDE the repository "
                f"being audited.\n"
                f"    {'project root':<14}{root}   ({label})\n"
                f"    {'resolved to':<14}{target}   (inside it, at "
                f"{target.relative_to(root)}){via}\n\n"
                f"A directory inside the subject is writable by the session under "
                f"audit and is carried home by the courier, so {what} kept there is "
                f"material the last run wrote about itself. Point `{key}` (or "
                f"${env}) at a directory outside this repository — any location outside "
                f"it; xcheck never derives one from the operator home directory.")
    return None


def refuse_outside_subject(path, project, key, env, what):
    """Raise if this root is inside the subject. The one enforcement point for both."""
    problem = root_location_problem(path, project, key, env, what)
    if problem:
        raise SystemExit(problem)
    return path


def manifest_path(project):
    return Path(project) / "audit" / MANIFEST_NAME


def record(project, session_id, log_path, sha256=None, role=None):
    """Append one row: this session's log, where it went, and what it hashed to.

    Append-only, like the event stream. Pruning appends a second row rather than editing
    this one — a record that a file WAS here and is gone is a different fact from the
    file never having existed, and only one of the two is honest after a prune.
    """
    p = Path(log_path)
    row = {"kind": "log", "session_id": session_id, "role": role,
           "path": str(p), "name": p.name,
           "bytes": p.stat().st_size if p.is_file() else None,
           "sha256": sha256,
           "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    m = manifest_path(project)
    m.parent.mkdir(parents=True, exist_ok=True)
    with m.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def manifest_rows(project):
    """Every row, or a refusal naming the line that is not one.

    This used to `continue` past an unparseable line. A one-line `{not json}` manifest
    therefore returned `[]`, and an evidence index that reads as EMPTY when it is damaged
    is worse than no index at all: every query against it answers "nothing was ever
    recorded here", which is the most reassuring possible reading of corruption.
    """
    m = manifest_path(project)
    if not m.is_file():
        return []
    out = []
    for n, line in enumerate(m.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError as e:
            raise RetentionError(
                f"{m}:{n}: the log manifest is not readable as an index — {e}. This file "
                f"is the record of which session logs existed and what they hashed to, so "
                f"a damaged line is refused rather than skipped: skipping it would report "
                f"the logs it names as never recorded. Repair or remove the line (the "
                f"manifest is append-only, so the rows above it are still good), then "
                f"re-run.") from None
        if not isinstance(row, dict):
            raise RetentionError(
                f"{m}:{n}: the log manifest holds a {type(row).__name__}, not a row. Every "
                f"line must be one JSON object.")
        out.append(row)
    return out


def _digest_on_disk(path):
    """The sha256 of what is actually there now, or None if it cannot be read."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def find(project, session_id):
    """`(state, row)` for one session's log — one of `LOG_STATES`.

    The whole point is that these are FIVE different facts and used to be two:

      present     the file is where the manifest says, and it still hashes to what the
                  manifest recorded
      pruned      an explicit append-only prune receipt says a verb deleted it, and when
      missing     the manifest recorded it, nobody recorded deleting it, and it is not
                  there — a deletion outside the tool, a wrong path, a moved machine
      corrupt     the file is there and its digest does NOT match, or the manifest itself
                  cannot be read
      unrecorded  no row for this session at all

    The audit's reproduction: a recorded log whose file was simply gone read as `pruned`,
    and a present file with the wrong bytes read as `present`, because the digest was
    never compared. Both are the same defect — an answer that cannot be wrong is an
    answer that is not being computed.
    """
    try:
        rows = manifest_rows(project)
    except RetentionError as e:
        # Fail closed at the query too. A damaged index makes EVERY answer from it
        # untrustworthy, so this does not pretend to answer for one session.
        return CORRUPT, {"session_id": session_id, "manifest_error": str(e)}
    log = pruned = None
    for r in rows:
        if r.get("session_id") != session_id:
            continue
        if r.get("kind") == "log":
            log = r
        elif r.get("kind") == "pruned":
            pruned = r
    if log is None:
        return UNRECORDED, None
    if pruned is not None:
        return PRUNED, {**log, **pruned}
    path = Path(log.get("path", ""))
    if not path.is_file():
        return MISSING, log
    recorded = log.get("sha256")
    if recorded:
        now = _digest_on_disk(path)
        if now != recorded:
            return CORRUPT, {**log, "sha256_now": now}
    return PRESENT, log


def describe_log(project, session_id):
    """The human answer to "where is that session's log?" — one sentence per state.

    Each says what the operator does next, because the four states differ exactly in what
    there is to do about them: nothing, nothing, go looking, and stop trusting the bytes.
    """
    state, row = find(project, session_id)
    if state == UNRECORDED:
        return (f"{session_id}: NO LOG RECORDED in the manifest. Either no session with "
                f"that id ever ran here, or it ran before the manifest existed — check "
                f"`audit/{LEGACY_LOG_DIRNAME}/` and `xcheck prune-logs --migrate`.")
    if state == CORRUPT and "manifest_error" in row:
        return (f"{session_id}: UNKNOWN — the manifest itself cannot be read, so no "
                f"answer about any session can be trusted. {row['manifest_error']}")
    if state == PRUNED:
        when = row.get("pruned_at") or "an earlier prune"
        return (f"{session_id}: log PRUNED ({when}) — {row.get('bytes')} bytes, "
                f"sha256 {(row.get('sha256') or 'unknown')[:12]}, was at {row.get('path')}. "
                f"Deleted on purpose by a verb someone ran; the digest is still in the "
                f"event stream, so every join still works. Nothing to do.")
    if state == MISSING:
        return (f"{session_id}: log MISSING — the manifest recorded {row.get('bytes')} "
                f"bytes at {row.get('path')} and NOTHING recorded deleting it. This is not "
                f"a prune: look for a deletion outside the tool, a log root that moved, or "
                f"a machine that is not this one. The recorded sha256 is "
                f"{(row.get('sha256') or 'unknown')[:12]}, so a copy found elsewhere can "
                f"still be proved to be the one.")
    if state == CORRUPT:
        return (f"{session_id}: log CORRUPT — {row.get('path')} is there, but it hashes to "
                f"{(row.get('sha256_now') or 'unreadable')[:12]} and the manifest recorded "
                f"{(row.get('sha256') or 'unknown')[:12]}. The bytes changed after they "
                f"were recorded, so this file is no longer evidence of what the session "
                f"printed. Do not quote it; the event stream's digest is the one that "
                f"still holds.")
    return (f"{session_id}: log PRESENT at {row['path']} ({row.get('bytes')} bytes), "
            f"digest verified against the manifest.")


def legacy_logs(project):
    """Session logs still sitting in the working tree, from before this phase."""
    d = Path(project) / "audit" / LEGACY_LOG_DIRNAME
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file())


def premigration_check(project, files=None):
    """Hash every in-tree legacy log and check it against any row already recorded.

    Returns `(digests, problems)`. A problem is FATAL and the caller must not move
    anything: a digest that disagrees with a recorded one means the bytes on disk are not
    the bytes the manifest says were written, and moving that file would file the wrong
    content under the right name — destroying exactly the forensic difference between
    `present` and `corrupt` that this module exists to keep.

    The audit's instruction was to check the manifest BEFORE migrating. When the manifest
    is empty there is nothing to disagree with, which is not the same as verified: so
    `migrate_logs` records every file, at its in-tree path, with the digest computed here,
    and asserts each one reads back as `present` before a single byte moves. Recording is
    what makes the later verification mean anything.
    """
    files = list(files if files is not None else legacy_logs(project))
    try:
        rows = manifest_rows(project)
    except RetentionError as e:
        return {}, [f"the manifest itself is unreadable ({e}) — nothing may move until "
                    f"it is repaired or replaced deliberately"]
    recorded = {}
    for r in rows:
        if r.get("kind") == "log" and r.get("sha256"):
            recorded[Path(r.get("path", "")).name] = r
    digests, problems = {}, []
    for src in files:
        try:
            digests[src.name] = hashlib.sha256(src.read_bytes()).hexdigest()
        except OSError as e:
            problems.append(f"{src.name}: unreadable ({e}) — a log that cannot be read "
                            f"cannot be verified, and an unverified log is not moved")
            continue
        prior = recorded.get(src.name)
        if prior and prior["sha256"] != digests[src.name]:
            problems.append(
                f"{src.name}: recorded sha256 {prior['sha256'][:16]}… but the file on "
                f"disk hashes to {digests[src.name][:16]}… — this is `corrupt`, not "
                f"`present`, and migration STOPS rather than moving it")
    return digests, problems


def migrate_logs(project, conf=None, apply=False):
    """Move logs already in the working tree out to the log root, recording each.

    An existing project has yesterday's logs in `audit/orchestrator-logs/` and gets
    today's outside the tree; without this, the 102MB that started this phase would sit
    there forever while the tool congratulated itself. A MOVE, not a delete: the bytes
    are preserved, the manifest gains a row per file with its size and sha256, and
    nothing in git changes — those files were always untracked (`.gitignore:2`).
    """
    import shutil
    files = legacy_logs(project)
    total = sum(p.stat().st_size for p in files)
    root = log_root(project, conf, create=apply)
    lines = [f"migrate: {len(files)} log(s) in audit/{LEGACY_LOG_DIRNAME}/, "
             f"{total:,} bytes -> {root}"]

    # BEFORE anything moves. A mismatch here names the file and stops the whole
    # migration: partially moving a set of logs one of which is already wrong is worse
    # than not starting, because afterwards nobody can tell which half was checked.
    digests, problems = premigration_check(project, files)
    if problems:
        raise RetentionError(
            f"migration REFUSED: {len(problems)} log(s) failed verification before any "
            f"file moved.\n  " + "\n  ".join(problems))
    lines.append(f"  verified: {len(digests)} of {len(files)} log(s) hashed; "
                 f"no digest disagrees with a recorded one")
    if not apply:
        lines.append("  (dry run — nothing moved. Re-run with --apply to move them.)")
        return "\n".join(lines), total

    # Recorded at their IN-TREE path first, so "verified before the move" is a fact in
    # the manifest rather than a sentence in this docstring. `find` reads the LAST
    # `kind=log` row, so the post-move row below supersedes this one.
    for src in files:
        record(project, f"legacy:{src.name}", src, sha256=digests[src.name])
    unverified = [src.name for src in files
                  if find(project, f"legacy:{src.name}")[0] != PRESENT]
    if unverified:
        raise RetentionError(
            f"migration REFUSED: {len(unverified)} log(s) do not read back as "
            f"`{PRESENT}` from the manifest they were just recorded in, so the record "
            f"cannot be trusted to describe what is about to move: "
            f"{', '.join(unverified[:5])}")
    lines.append(f"  recorded: {len(files)} log(s) read back as `{PRESENT}` at their "
                 f"in-tree path BEFORE the move")

    moved, failed = 0, []
    for src in files:
        dst = root / src.name
        try:
            shutil.move(str(src), str(dst))
        except OSError as e:
            failed.append(f"{src.name}: {e}")
            continue
        moved += 1
        # Session id unknown for a legacy file — the name carries the timestamp and role,
        # which is what the operator has. Recorded as such rather than invented.
        record(project, f"legacy:{src.name}", dst, sha256=digests[src.name])
    for f in failed:
        lines.append(f"  ! {f}")
    lines.append(f"  moved {moved} file(s); every one is in "
                 f"audit/{MANIFEST_NAME} with its size and sha256.")
    if failed:
        raise RetentionError(
            f"migration INCOMPLETE: {moved} moved, {len(failed)} failed. The manifest "
            f"records where each file actually is, so nothing is lost — but do not "
            f"treat this as a finished migration:\n  " + "\n  ".join(failed))
    return "\n".join(lines), total


def _age_days(row, now=None):
    try:
        at = datetime.fromisoformat(row["at"])
    except (KeyError, ValueError):
        return None
    now = now or datetime.now(timezone.utc)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return (now - at).total_seconds() / 86400.0


def prunable(project, days, now=None):
    """Rows whose log is still on disk and older than `days`."""
    seen = {r["session_id"] for r in manifest_rows(project)
            if r.get("kind") == "pruned"}
    out = []
    for r in manifest_rows(project):
        if r.get("kind") != "log" or r.get("session_id") in seen:
            continue
        age = _age_days(r, now)
        if age is None or age < days:
            continue
        if Path(r.get("path", "")).is_file():
            out.append((r, age))
    return out


def prune(project, days, apply=False, now=None):
    """Report what would be deleted; delete only when `apply` is true.

    Dry by default and never called from a run. Deleting evidence is an operator's
    decision, and a retention policy that fires while a session is being audited would
    make the audit's own record depend on when someone last ran the tool.
    """
    victims = prunable(project, days, now)
    freed = sum(r.get("bytes") or 0 for r, _age in victims)
    lines = [f"prune-logs: retention {days} day(s), "
             f"{len(victims)} log(s) older than that, {freed:,} bytes"]
    for r, age in victims:
        lines.append(f"  {r['session_id']}  {age:.1f}d  {r.get('bytes') or 0:>9,}  "
                     f"{r.get('path')}")
    if not apply:
        lines.append("  (dry run — nothing deleted. Re-run with --apply to delete.)")
        return "\n".join(lines), 0
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # Count what was actually UNLINKED, not what was selected. The two differ exactly when
    # something went wrong — a read-only directory, a file already gone, a permission — and
    # that is the case where a confident total is a lie. A prune receipt is written per
    # successful unlink, so a file that survived keeps reading as `present` rather than
    # acquiring a receipt for a deletion that did not happen.
    deleted, survived, freed_now = 0, [], 0
    with manifest_path(project).open("a", encoding="utf-8") as fh:
        for r, _age in victims:
            p = Path(r["path"])
            refuse_frozen(p)
            try:
                p.unlink()
            except OSError as e:
                lines.append(f"  ! {p}: {e}")
                survived.append(r)
                continue
            deleted += 1
            freed_now += r.get("bytes") or 0
            fh.write(json.dumps({"kind": "pruned", "session_id": r["session_id"],
                                 "path": r["path"], "sha256": r.get("sha256"),
                                 "bytes": r.get("bytes"), "pruned_at": stamp},
                                sort_keys=True) + "\n")
    if survived:
        lines.append(f"  PARTIAL: deleted {deleted} of {len(victims)} log(s), "
                     f"{freed_now:,} of {freed:,} bytes. {len(survived)} still on disk "
                     f"and still recorded as present: "
                     f"{', '.join(str(Path(r['path']).name) for r in survived[:4])}"
                     f"{' …' if len(survived) > 4 else ''}. Re-run after fixing the cause; "
                     f"no receipt was written for a file that is still there.")
    else:
        lines.append(f"  deleted {deleted} log(s); the manifest keeps the name, size "
                     f"and sha256 of every one, and the event stream keeps the digest.")
    return "\n".join(lines), freed_now
