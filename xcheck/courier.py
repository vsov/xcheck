"""The git side of durability: what a session dirtied, what may be committed on
its behalf, and the commit itself.

The courier is a trust boundary — it decides which paths of a working tree are
this session's deliverable and which are pre-existing operator work it must not
touch — so it is worth testing without the rest of the orchestrator in the way.
"""


import os
import subprocess
import tempfile
from pathlib import Path

from xcheck import ledger
from xcheck.state import STATE_FILENAME, StateError, load_state
from xcheck.util import GIT_TIMEOUT
from xcheck.views import refuse_on_drift


def dirty_paths(project: Path):
    """Set of dirty/untracked paths from git status (None if not a repo).

    Uses `--porcelain -z` (F-0109). The NUL-separated machine format needs NO
    C-unquoting — paths are emitted as verbatim bytes, so a non-ASCII name reaches
    `git add` as its real pathspec (the human format quoted it to `"\\320\\272…"`,
    which `git add` could not match) — and it has NO ` -> ` rename separator to
    mis-split on (a path that literally contains ` -> ` broke the old
    `.split(" -> ")`). F-0109 reopen: a rename/copy record (status `R`/`C`) is
    followed by a second NUL-terminated field, the ORIGIN path; BOTH the destination
    (an addition) and the origin (a deletion) are the session's own dirt, so the
    courier stages the whole rename, not only its new name."""
    r = subprocess.run(
        ["git", "status", "--porcelain", "-z"], cwd=project, capture_output=True,
        timeout=GIT_TIMEOUT,
    )
    if r.returncode != 0:
        return None
    # -z records are NUL-terminated; a rename/copy record is IMMEDIATELY followed by
    # a second NUL-terminated field (the origin path). Walk the fields, consuming that
    # extra field when the status column is R (rename) or C (copy).
    fields = r.stdout.split(b"\0")
    out = set()
    i = 0
    while i < len(fields):
        rec = fields[i]
        if not rec:
            i += 1
            continue
        # `XY <path>`: 2 status chars + a space + the path bytes.
        status, path = rec[:2], rec[3:]
        out.add(path.decode("utf-8", "surrogateescape"))
        if status[:1] in (b"R", b"C") or status[1:2] in (b"R", b"C"):
            i += 1  # the next field is the rename/copy ORIGIN path
            if i < len(fields) and fields[i]:
                out.add(fields[i].decode("utf-8", "surrogateescape"))
        i += 1
    return out


def head_baseline_paths(project: Path):
    """Set of repo-relative paths in the project's HEAD tree — the material that existed
    as tracked, committed files BEFORE the session (F-0120). Asked from git as a FACT
    (`git ls-tree -r HEAD`), never inferred from a working-tree status letter: a path is
    "new this session" iff it is ABSENT from this set, and that verdict is identical whether
    git shows the path as untracked (`??`), intent-to-add (` A`), staged-new (`A `), or any
    future porcelain shape. The three prior reopens all came from decoding the two-letter
    status column and missing the next variant — `git add` flipped `??`->`A `, then
    `git add -N` produced ` A` in the OTHER column (F-0113/F-0114/F-0120 human ruling
    2026-07-28: read the property from its source, do not guess it from an indirect sign).
    Empty set when the project is not a git repo or HEAD has no commit yet — nothing is
    baselined, so every dirty path is honestly new."""
    r = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "-z", "HEAD"],
        cwd=project, capture_output=True, timeout=GIT_TIMEOUT,
    )
    if r.returncode != 0:
        return set()
    return {p.decode("utf-8", "surrogateescape") for p in r.stdout.split(b"\0") if p}


def session_dirt(pre, post):
    """Paths a session left behind: new dirt only — pre-existing dirt is
    someone else's work (pilot lesson: operator's bench/ must never be
    swept into a courier commit)."""
    if pre is None or post is None:
        return set()
    return post - pre


def rename_pairs(project: Path):
    """{destination: origin} for every rename/copy git detects between HEAD and the FULL working
    tree — INCLUDING a plain file-level move (`os.rename`/`mv`) the session never `git add`-ed,
    not only an already-staged `git mv` (F-0120 human ruling #3 2026-07-28). Two uses in the
    courier: (1) a rename DESTINATION is a path absent from HEAD yet is tracked material MOVED,
    not session scratch, so it must not be mistaken for a stray temp file; (2) origin+destination
    are ONE operation the courier must never commit half.

    The pairing is computed in an EPHEMERAL index so the OPERATOR'S real index is NEVER touched
    (the courier only reads inherited state, it must not mutate the staged index): GIT_INDEX_FILE
    points at a throwaway file in the system tmp dir; `git read-tree HEAD` seeds it from HEAD,
    `git add -A` folds the whole worktree into it, then `git diff --cached --find-renames
    --name-status -z` HEAD-vs-that-index reports the moves. A git-computed FACT — the same
    discipline as `head_baseline_paths`, never a porcelain status-letter guess (F-0113/F-0114/
    F-0120: read the property from its source). The previous take read only `git diff --cached`
    of the operator's index, so a plain `mv` with no `git add` produced no pair and the courier
    split the operation (F-0120 round-4 reopen). Empty dict when the project is not a git repo or
    HEAD has no commit yet — no tree to diff against, so nothing is a detected rename. The `-z`
    stream emits each rename record as three NUL fields: the `R<score>`/`C<score>` status token,
    then the ORIGIN path, then the NEW path; a non-rename record is a status token + one path."""
    if subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"],
                      cwd=project, capture_output=True,
                      timeout=GIT_TIMEOUT).returncode != 0:
        return {}
    fd, idx = tempfile.mkstemp(prefix="xcheck-courier-idx-")
    os.close(fd)
    try:
        os.unlink(idx)  # git read-tree writes a FRESH index; a 0-byte file is not a valid one
    except OSError:
        pass
    env = dict(os.environ)
    env["GIT_INDEX_FILE"] = idx
    try:
        if subprocess.run(["git", "read-tree", "HEAD"], cwd=project, env=env,
                          capture_output=True, timeout=GIT_TIMEOUT).returncode != 0:
            return {}
        if subprocess.run(["git", "add", "-A"], cwd=project, env=env,
                          capture_output=True, timeout=GIT_TIMEOUT).returncode != 0:
            return {}
        r = subprocess.run(
            ["git", "diff", "--cached", "--find-renames", "--name-status", "-z"],
            cwd=project, env=env, capture_output=True, timeout=GIT_TIMEOUT,
        )
    finally:
        try:
            os.remove(idx)
        except OSError:
            pass
    if r.returncode != 0:
        return {}
    fields = r.stdout.split(b"\0")
    pairs = {}
    i = 0
    while i < len(fields):
        tok = fields[i]
        if not tok:
            i += 1
            continue
        st = tok[:1]
        if st in (b"R", b"C") and i + 2 < len(fields):
            origin = fields[i + 1].decode("utf-8", "surrogateescape")
            dest = fields[i + 2].decode("utf-8", "surrogateescape")
            pairs[dest] = origin
            i += 3
        else:
            i += 2  # status token + its single path
    return pairs


def _event_prefix_problem(project: Path, pre_head: str, role: str):
    """`audit/events.jsonl` now, against its committed bytes at `pre_head`.

    A stream that did not exist at `pre_head` reads as empty, so the session's first
    events are an append and are carried. `git show` in BYTES: `text=True` would apply
    universal-newline translation to evidence.
    """
    rel = f"audit/{ledger.EVENTS_FILENAME}"
    shown = subprocess.run(["git", "-C", str(project), "show", f"{pre_head}:{rel}"],
                           capture_output=True, timeout=GIT_TIMEOUT)
    before = shown.stdout if shown.returncode == 0 else b""
    path = project / "audit" / ledger.EVENTS_FILENAME
    after = path.read_bytes() if path.is_file() else b""
    return ledger.prefix_problem(before, after, rel, f"the {role} session")


def courier_commit(project: Path, role: str, charter, pre, conf=None, pre_head=None):
    """Pilot lessons: codex sandbox can't commit; full-auto sessions
    under-commit. Finish the session's own commit — new dirt only.

    Returns True when there was nothing to commit or the commit succeeded;
    False when `git add`/`git commit` failed — so the caller can stop instead
    of claiming a durable handoff that did not happen (F-0008)."""
    post = dirty_paths(project)
    new = sorted(session_dirt(pre, post))
    if not new:
        return True
    # PHASE 5 pre-commit check: never ship a state document the tool cannot read.
    # The courier is the last actor before a session's work becomes history, and the
    # state it commits is what the NEXT session loads. Reading it here through the one
    # boundary means a session that corrupted `state.json` stops at the courier with an
    # addressed message instead of committing a tree whose every later command refuses
    # to run. Only a project that HAS a state document is checked — the converter's own
    # first run (phase 6) commits the document into existence.
    # PHASE 9 (fourth audit) — this repository's own F-0104, which reproduced a session
    # changing a COMMITTED event line and `courier_commit` returning True and committing
    # the rewrite. The stream is compared against the version at the commit the session
    # started from: a suffix append passes, a rewrite or a truncation refuses.
    #
    # Also here, and not only in the sandbox, because `sandbox_profile=none` has no
    # sandbox: the session writes the project directly and the courier is the only actor
    # between it and history. The two callers share `ledger.prefix_problem`, so the rule
    # cannot be enforced two different ways.
    if pre_head:
        problem = _event_prefix_problem(project, pre_head, role)
        if problem:
            print(f"courier: {problem}")
            return False
    if (project / "audit" / STATE_FILENAME).exists():
        try:
            # PHASE 6: and never ship a tree whose generated views contradict it —
            # the next session reads the Markdown as its working surface.
            refuse_on_drift(load_state(project / "audit"), project / "audit")
        except StateError as e:
            print(f"courier: refusing to commit — audit/{STATE_FILENAME} is not "
                  f"readable state: {e}")
            return False
    # F-0109 reopen: stage only the paths `git add` can match — those present in the
    # worktree (untracked / modified / a rename DESTINATION) OR still tracked in the
    # index (a plain deletion). A rename ORIGIN is neither: `git mv` already staged its
    # deletion AND removed it from the index, so `git add` would fatal on it ("pathspec
    # did not match any files"), aborting the whole stage. It needs no staging — the
    # commit pathspec below picks up git mv's already-staged deletion. `-A` so a plain
    # deletion (worktree gone, still in the index) stages as a removal. Scoped to these
    # paths, so a pre-existing staged index (F-0046) stays untouched.
    #
    # F-0109 re-take (human ruling 2026-07-28): test the DIRECTORY ENTRY itself, not its
    # target — `os.path.lexists`, not `Path.exists()`. A session's own new broken symlink
    # is dirt git already reported; `Path.exists()` follows the dangling link and returns
    # False, so the filter dropped it and the commit then fatalled on the "unknown"
    # pathspec. This filter exists only to avoid handing git a path it does not know; it
    # must not re-ask the filesystem to dereference what git already named.
    lsf = subprocess.run(["git", "ls-files", "-z"], cwd=project, capture_output=True,
                         timeout=GIT_TIMEOUT)
    raw = lsf.stdout if lsf.returncode == 0 else b""
    if isinstance(raw, str):  # tolerate a text-mode / mocked git
        raw = raw.encode("utf-8", "surrogateescape")
    tracked = set(raw.split(b"\0"))
    # F-0120: distinguish a declared deliverable from a session's scratch. Novelty is asked
    # from HEAD as a fact (`head_baseline_paths`, snapshotted BEFORE the session): a path
    # ABSENT from that HEAD baseline whose top-level component is OUTSIDE audit/ — a temp
    # copy/harness/fixture/marker an interrupted session left inside the project tree — is
    # NOT a deliverable and must NOT be committed silently (the two real incidents: a Verifier
    # left `.xcheck-verify-cf0001.py` and a full mutation copy of bin/xcheck, both couriered
    # into the shipped history). Hold such paths OUT of the stage/commit and NAME them. This
    # question is NOT decoded from the porcelain status column — the previous reopens keyed on
    # `??`, then `A `, and `git add -N`'s ` A` slipped past (F-0120 human ruling 2026-07-28:
    # ask whether the path was in HEAD, do not enumerate status letters). A modified/deleted/
    # renamed TRACKED material file (bin/xcheck, README.md, a skill) WAS in HEAD, so it is the
    # session's declared work and passes through as before; a new finding/plan has top
    # component `audit` and is exempt. Legal remediation — and the F-0109 under-audit/ renames
    # — is untouched; only stray in-tree scratch is held, whatever git's status column shows.
    #
    # F-0120 human ruling #2 (2026-07-28): "absent from HEAD" is NECESSARY but NOT SUFFICIENT
    # for scratch. A rename DESTINATION is also absent from HEAD, but it is tracked material
    # MOVED, not a stray temp file — so a path that is git's rename destination of an origin
    # that WAS in HEAD is declared work, committed, never held. The rename pairing is a
    # git-computed fact (`rename_pairs`, computed in an ephemeral index so a plain unstaged
    # `mv` is paired too, F-0120 human ruling #3), not another status-letter guess.
    head_baseline = pre_head if pre_head is not None else head_baseline_paths(project)
    renames = rename_pairs(project)  # {destination: origin}
    new_set = set(new)
    def _is_session_scratch(p):
        if p.split("/", 1)[0] == "audit":
            return False
        if p in head_baseline:
            return False  # tracked material that existed before the session — declared work
        origin = renames.get(p)
        # F-0120 round-5 reopen (2026-07-28): "rename destination" alone is NOT enough to exempt.
        # `rename_pairs` finds moves across the WHOLE worktree, so git can pair a session-created
        # scratch file against a tracked file the OPERATOR deleted BEFORE the session (matching by
        # content) — a phantom rename the session never performed. Exempting on `origin in
        # head_baseline` alone then ships that scratch, sweeping in pre-existing operator dirt the
        # README contract promises is "never swept into an audit commit — without exception". A
        # GENUINE session rename has BOTH endpoints in the session's own dirt: the destination is
        # new this session AND the origin's disappearance is session dirt too (`origin in new_set`).
        # A phantom's origin predated the session (in `pre`, so absent from `new`), so require both.
        if origin is not None and origin in head_baseline and origin in new_set:
            return False  # a rename destination of tracked material MOVED BY THIS SESSION — declared work
        return True
    held_set = {p for p in new if _is_session_scratch(p)}
    # F-0120 human ruling #2 — ATOMICITY (the point that matters more): a rename's origin and
    # destination are ONE operation and must NEVER be committed half. Committing only the origin
    # deletion drops tracked material from HEAD while reporting success — exactly the silent
    # supply corruption this finding is about. So if EITHER endpoint of a rename is held, hold
    # BOTH and name the whole operation; if neither is, both pass through together. (With the
    # rename-aware predicate above a legal rename is never held, so this is the safety net that
    # keeps a future gap from splitting an operation.)
    for dest, origin in renames.items():
        pair = {dest, origin} & new_set
        if held_set & pair:
            held_set |= pair
    # F-0120 human ruling #3 (2026-07-28) — CONSERVATIVE ROLLBACK, the invariant's safety net.
    # The invariant "no operation is ever committed half" is primary; rename recognition above
    # is only the means. Where a pair still can't be established, fall back conservatively: if
    # the courier is ALREADY holding some path AND a deletion of tracked material has no detected
    # rename partner (a HEAD path now gone from the worktree, not any rename's origin), do NOT
    # commit that lone deletion — committing it would drop tracked material from HEAD while
    # reporting success, the exact silent supply corruption this finding is about. Hold it too
    # and name it: better to over-hand to the operator than half-commit. Gated on `held_set` so
    # a clean remediation that legitimately deletes a tracked file — with no stray scratch — still
    # commits it as before (no false positive on ordinary deletions).
    if held_set:
        rename_origins = set(renames.values())
        for p in new:
            if (p in head_baseline and p not in rename_origins
                    and not os.path.lexists(project / p)):
                held_set |= {p}
    held = sorted(held_set)
    if held:
        new = [p for p in new if p not in held_set]
        # Report honestly (this finding is about NOT mislabeling what the courier does). Three
        # reasons a path is held, three distinct messages so the operator can tell them apart:
        #   * ABSENT from HEAD and NOT git-paired -> a plain stray scratch (harness/marker/copy).
        #   * ABSENT from HEAD but git-PAIRED to an origin that is NOT this session's own work
        #     (it predates the session, or the source is ambiguous among same-content files) ->
        #     the courier cannot tell a legal move from stray scratch, so it holds it for a human.
        #     F-0120 human ruling #4 (2026-07-28): this is a RATIFIED §8-rule-5 hold, not a defect
        #     — HEAD stays intact (the conservative rollback of ruling #3 firing as designed); the
        #     only requirement is that the message NAME this reason so the operator can distinguish
        #     a legal move whose source is ambiguous from a genuine harness. Behaviour is unchanged.
        #   * WAS in HEAD -> tracked material kept back only so no operation is committed half.
        # Name all — nothing held is silent.
        scratch = [p for p in held if p not in head_baseline and p not in renames]
        ambiguous = [p for p in held if p not in head_baseline and p in renames]
        withheld = [p for p in held if p in head_baseline]
        parts = []
        if scratch:
            parts.append("suspected session scratch (temp copies/harnesses/markers create them "
                         "OUTSIDE the project tree, never inside it): " + ", ".join(scratch))
        if ambiguous:
            parts.append("rename whose source is ambiguous or predates the session — git pairs it "
                         "to an origin that is not this session's own work, so the courier cannot "
                         "certify it as a legal move rather than stray scratch; a human must "
                         "resolve it: " + ", ".join(ambiguous))
        if withheld:
            parts.append("tracked material held so no operation commits half: " + ", ".join(withheld))
        print(f"courier: NOT committing {len(held)} path(s) outside audit/ — left for the "
              f"operator to review/resolve; " + "; ".join(parts))
    if not new:
        return True  # nothing left but held scratch — no deliverable to commit
    addable = [p for p in new
               if os.path.lexists(project / p) or p.encode("utf-8", "surrogateescape") in tracked]
    if addable:
        add = subprocess.run(["git", "add", "-A", "--"] + addable, cwd=project,
                             check=False, timeout=GIT_TIMEOUT)
        if add.returncode != 0:
            print(f"courier: git add FAILED (rc={add.returncode}) — state NOT committed for {role}")
            return False
    what = charter or "session artifacts"
    scope = "audit" if all(p.startswith("audit/") for p in new) else "audit+material"
    # F-0046: pass the session's OWN paths as a pathspec. A plain `git commit`
    # snapshots the WHOLE staged index, sweeping in any file the operator staged
    # before the session — exactly what the courier contract promises never to
    # do. The `--` pathspec commits only these paths and leaves a pre-existing
    # staged index untouched (still staged, out of the audit commit).
    commit = subprocess.run(
        ["git", "commit", "-q", "-m",
         f"{'docs' if scope == 'audit' else 'fix'}(audit): {role.lower()} — {what} (orchestrated courier commit)",
         "--"] + new,
        cwd=project,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
        check=False,
    )
    if commit.returncode != 0:
        detail = (commit.stderr or commit.stdout or "").strip()[:120]
        print(f"courier: git commit FAILED (rc={commit.returncode}) — state NOT committed for {role}: {detail}")
        return False
    print(f"courier: committed the session's new dirt ({len(new)} paths, {scope}) for {role}")
    # Held scratch (F-0120) is already named above; exclude it here so it is not
    # mislabeled as pre-existing dirt. leftover = dirt that predated the session.
    leftover = (post - set(new) - set(held)) if post else set()
    if leftover:
        print(f"note: {len(leftover)} dirty paths predated the session — left untouched")
    # B5: opt-in auto-push (default off — never push without explicit opt-in).
    if conf and conf.get("push_after_commit", "").strip().lower() in ("1", "true", "yes", "on"):
        # A push talks to a REMOTE: it is the one git call here that can block on the
        # network, so it gets its own, longer bound rather than the local-git one.
        r = subprocess.run(["git", "push"], cwd=project, capture_output=True, text=True,
                           timeout=GIT_TIMEOUT * 5)
        if r.returncode != 0:
            # F-0110: the operator explicitly opted into the remote handoff, so its
            # failure is a real courier failure — return False (like a failed
            # add/commit) so run_session/cmd_next stop the loop instead of advancing
            # as if the durable handoff succeeded. The local commit already stands.
            print(f"courier: git push FAILED — {r.stderr.strip()[:120]}")
            return False
        print("courier: git push ok")
    return True
