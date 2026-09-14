"""Session dispatch and the writing lock: build the child command, hold the lock
across the transaction, run the agent CLI, fingerprint the orchestrator's own
state and source.

This is the module phase 8 replaces with an isolated runner (worktree, env
allowlist, timeout, resource limits, process-group kill, log redaction). It is
deliberately separate from `courier.py` so the sandbox boundary and the commit
boundary can be tested apart.
"""


import collections
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from xcheck import envelope
from xcheck import budget
from xcheck import policy
from xcheck import provider
from xcheck.courier import courier_commit, dirty_paths, head_baseline_paths
from xcheck import retention
from xcheck import routing
from xcheck.state import (SUBJECT_COMMIT_ENV, StateError, controller_commit,
                          git_head, load_state, subject_manifest)
from xcheck import ledger
from xcheck.util import (CONF_DEFAULTS, GIT_TIMEOUT, OUTCOMES, conf_flag, conf_number,
                         serialized)

# F-0120 (level 1, the contract half): every writing role is told to keep temporary
# artifacts OUT of the project tree. The four writing roles the orchestrator LAUNCHES
# (Planner/Auditor/Remediator/Verifier) get SESSION_HYGIENE appended to their prompts
# below; the fifth writing role, Triage, is human-run (agent-as-pen, never launched from
# here) and carries the same discipline in its shared skill (skills/xcheck-triage). So all
# five writing roles are covered — four via prompt, one via skill. The orchestrator's courier commits the session's
# new dirt, and the project tree IS the shipped deliverable (bin/xcheck, README.md,
# skills/, templates/) — a scratch file left inside it gets couriered into the public
# history (two real incidents: a Verifier's harness and a full mutation copy of
# bin/xcheck). The courier's mechanical guard (courier_commit) is the level-2 backstop;
# this line is the level-1 discipline the guard exists to enforce.
SESSION_HYGIENE = (
    " Session hygiene (F-0120): create every temporary artifact (scratch copies, test "
    "harnesses, fixtures, mutation copies, marker files) OUTSIDE the project tree — in a "
    "system temp dir (e.g. Python tempfile) — NEVER inside the project. The orchestrator "
    "ships the project tree; a stray in-tree file is committed as material. Clean up "
    "before you exit."
)

# 0.9.1 phase 5 (P0 #4). ONE executable contract: the prompt a child actually receives
# says how state moves, and it says the same thing `XCHECK.md` §2 rule 3 says. Until this
# release the Remediator prompt told the agent to write `fixed-by:` into a finding's
# frontmatter — a GENERATED view, rendered from state and never read back — so an obedient
# agent produced a session that succeeded and changed nothing. That is this tool's worst
# failure class, and it survived a green CI because the contract test checked that verb
# NAMES appeared somewhere, and the stale instruction mentioned verbs too.
#
# The rule is stated once, here, and appended to every writing role's prompt. A second
# copy is a second thing to forget to update.
STATE_MOVES_BY_VERB = (
    " Machine state moves ONLY through `xcheck` commands. `audit/state.json` is the "
    "canonical record; `audit/LEDGER.md` and every finding's frontmatter block are "
    "rendered FROM it and are never read back as authority (§2 rule 3), so editing one "
    "records nothing and is silently discarded at the next render. Your role card in §3 "
    "lists the verbs you may use; `xcheck help` prints their grammar. If the change you "
    "need has no verb, that is a finding to report, not a file to edit. "
)

def orchestration_context(orch, session_id):
    """The block appended to EVERY role prompt, whatever the role.

    A module-level function rather than a literal buried in `run_session` because it is
    executable instruction text — the same kind of thing as `PROMPTS` — and the contract
    test has to read the real one. A test that reads a copy of a prompt is a test of the
    copy; this block carried a stale instruction through 0.9.0 precisely because nothing
    could see it without running a session.
    """
    return (
        "\n\n[Orchestration context — the orchestrator (bin/xcheck) set these; "
        "your shell `env` may not expose them, so take their values from this "
        "line, which always reaches you] " + "; ".join(orch) + ". "
        "If XCHECK_LOCK_INHERITED is present it is the nonce of the "
        "audit/.lock/owner the orchestrator already holds around you: confirm "
        "it equals that owner's nonce, then SKIP lock acquisition and do NOT "
        "remove the lock on exit — the orchestrator owns its release (F-0093). "
        # 0.9.1 phase 5: this line used to repeat the frontmatter instruction the role
        # prompt gave — the same stale mechanic, twice, so fixing one left the other
        # telling the agent to edit a generated view.
        "Provenance is recorded by the verb, not by hand: `xcheck record-fix <ID> "
        "--session " + session_id + "` (§7, F-0096)."
    )


# PHASE 13. These used to open with "Read audit/XCHECK.md and audit/AUDIT.md" — 84,610
# bytes of normative text before the session looked at the project, once per session, and
# multiplied rather than amortised by parallelism. The capsule replaces the INSTRUCTION,
# not the documents: `capsule_line()` names a per-session file carrying this charter's
# verbs, norms, material hashes, findings, stop and postconditions, and says in the same
# breath that it is a subset and where the full text is.
PROMPTS = {
    "Construal": (
        "Read audit/XCHECK.md §4 rule 9. You are the {role}, but THIS SESSION DOES NOT DO "
        "THE {role}'S WORK: do not plan, audit, remediate or verify anything, and create or "
        "edit NO file except the one named below. Writing that one file IS the whole "
        "session. {charter}" + SESSION_HYGIENE
    ),
    "Planner": (
        "Role: Planner. Charter: {charter}." + SESSION_HYGIENE
    ),
    "Auditor": (
        "Role: Auditor. "
        "Charter: pass {charter}. Stop conditions per charter." + SESSION_HYGIENE
    ),
    "Remediator": (
        "Role: Remediator. "
        "Charter: {charter} (see LEDGER). Follow the XCHECK.md §3 sequence "
        "strictly." + STATE_MOVES_BY_VERB +
        "To mark a fix done, run `xcheck record-fix <ID> --session "
        "$XCHECK_SESSION_ID` — take that id from the Orchestration context line "
        "appended below, since your shell `env` may not show it. The verb records "
        "the provenance the §3 Verifier ≠ fixer rule is checked against (F-0096) "
        "and refuses a session id other than the one the orchestrator dispatched, "
        "so there is nothing to write by hand and nothing to get wrong. "
        "If the project uses git, COMMIT your changes before finishing, with "
        "the finding ids in the message (XCHECK.md §9 rule 7) — stage only "
        "the files you yourself changed, never pre-existing dirt. "
        "Note (F-0117): if your fix edits the orchestrator's own source "
        "(anything under xcheck/, or bin/xcheck), it does NOT take effect until "
        "`xcheck loop` is restarted — "
        "the running loop executes the code image loaded at its start; it detects "
        "a changed own-source and stops so a stale image never keeps running." + SESSION_HYGIENE
    ),
    "Verifier": (
        "Role: Verifier. "
        "Charter: {charter}. Before verifying, confirm each finding's `fixed-by` "
        "provenance is a canonical 16-hex-digit token and NOT your own session "
        "($XCHECK_SESSION_ID — from the Orchestration context line below) — the "
        "orchestrator has already refused this batch if provenance was missing, "
        "malformed, or matched you (§3 Verifier ≠ fixer, F-0096). Try to prove the "
        "fixes wrong." + STATE_MOVES_BY_VERB +
        "Record each verdict with `xcheck record-verdict <ID> --verdict "
        "closed|reopened --session $XCHECK_SESSION_ID`; the verb re-checks the "
        "independence rule at the write boundary and refuses your own fix. "
        "If your sandbox cannot commit, leave changes unstaged and say so." + SESSION_HYGIENE
    ),
}


# ---------------------------------------------------------------- lock


def _read_lock(path):
    """Parse a lock file to a dict, or None if absent/empty/unparseable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


class Lock:
    # F-0047 (human ruling 2026-07-25, option a: lock DIRECTORY + owner token).
    # `audit/.lock` is a DIRECTORY created with mkdir — an atomic operation whose
    # second caller gets EEXIST, so mkdir IS the acquisition. The owner record
    # (pid, role, started, host, nonce) lives in `.lock/owner`. Release is
    # owner-checked: a session removes the lock ONLY when the on-disk owner record
    # still carries the nonce it wrote, so no session can ever delete a lock it
    # does not own (§4 rule 8; the launchers' "never delete a lock you do not own"
    # contract). This replaces the earlier O_EXCL FILE + rename-release design,
    # which the Verifier broke twice: rename(2) is an atomic MOVE but not an atomic
    # compare-and-delete, so releasing a foreign lock briefly emptied the canonical
    # path and let a third writer slip in. With a directory there is never a window
    # in which the canonical `.lock` disappears for another owner: a foreign owner
    # record is never touched, so its `.lock/owner` keeps the directory non-empty
    # and rmdir cannot remove it.
    def __init__(self, audit_dir: Path, role: str):
        self.dir = audit_dir / ".lock"
        self.owner_file = self.dir / "owner"
        # Phase 8 turns the lock into a LEASE. A lock is a claim that lasts until
        # someone removes it; a lease is a claim that must be RENEWED. The pid-based
        # liveness check cannot see a hung child (the pid is alive, the session is
        # dead) and cannot see across a container or pid namespace at all, so a lock
        # whose owner wedged needed a human with `--force`. A heartbeat the
        # orchestrator writes while the child runs makes "still working" observable:
        # once it stops advancing past the TTL, a plain `unlock` may reclaim it.
        self.heartbeat_file = self.dir / "heartbeat"
        self.cancel_file = self.dir / "cancel"
        self.role = role
        # The unique owner token (nonce) of the lock THIS instance wrote. `release`
        # removes the on-disk lock only when its owner record still carries this
        # nonce, so a late release from a previous owner can never remove a lock a
        # new writer has since acquired. A nonce (not bare pid+started) is the
        # identity because the same process can re-acquire within the same clock
        # second, which pid+started alone cannot tell apart. None until acquire().
        self.token = None

    def legacy_file(self):
        """True iff `.lock` exists as a pre-directory-protocol FILE (old lock)."""
        return self.dir.exists() and not self.dir.is_dir()

    def acquire(self):
        try:
            os.mkdir(self.dir)  # atomic acquire; EEXIST => already held
        except FileExistsError:
            if self.legacy_file():
                raise SystemExit(
                    "audit/.lock exists as a legacy FILE lock (pre-directory "
                    "protocol). It is not auto-migrated; clear it with "
                    "`xcheck unlock --force` if you are sure no writing session is active."
                )
            info = self.read()
            # F-0047: fail closed on the liveness label. Only a PARSEABLE owner
            # record with a provably-dead real pid is "stale"; a live pid, an
            # unsignalable foreign owner (EPERM), a malformed pid, or an
            # absent/unparseable owner record (a writer may be mid-acquire, between
            # mkdir and the owner write) are all "not provably dead" — advise
            # wait / --force, never a plain `unlock`.
            # F-0094: host-aware. A foreign-host owner is never "STALE" here — a
            # local ESRCH says nothing about a process in another host's pid
            # namespace on a shared checkout.
            dead = lock_provably_dead(info)
            state = "STALE (pid dead)" if dead else "LIVE or unverifiable"
            hint = (
                "Run `xcheck unlock` to diagnose it; clear it with `xcheck unlock --force`."
                if dead
                else "Another writing session may be active; wait, or `xcheck unlock --force` if you are sure."
            )
            raise SystemExit(f"audit/.lock is held — {state}: {info}. {hint}")
        except OSError as e:
            # Phase 12: every OTHER mkdir failure — a read-only filesystem, a
            # read-only `audit/`, a full disk — used to escape as a raw traceback.
            # Fail closed WITH the diagnosis: the acquire did not happen, so no lock
            # is held and nothing was written, and the reader needs to know it is a
            # permission/space problem rather than a contended lock.
            raise SystemExit(
                f"cannot acquire {self.dir}: {e}. xcheck needs write access to "
                f"audit/ for every writing session; nothing was changed.")
        nonce = os.urandom(8).hex()
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "role": self.role,
                "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "host": socket.gethostname(),
                "nonce": nonce,
            }
        )
        try:
            self.owner_file.write_text(payload, encoding="utf-8")
        except OSError as e:
            # F-0107: mkdir above already atomically ACQUIRED the lock, but writing
            # the owner record failed — leaving `.lock/` with no owner. That is
            # unrecoverable by the normal paths: `release` is owner-checked (no token
            # to match, so it never removes it), and the next writer fails closed on
            # the unparseable/absent owner ("LIVE or unverifiable"), wedging the whole
            # cycle until a manual `unlock --force`. Roll the half-acquire back.
            #
            # F-0107 reopen: a bare `rmdir` is NOT enough. `write_text` is not atomic —
            # a real failing write can leave a PARTIAL owner file (e.g. the single byte
            # `{`) on disk, and `rmdir` on a non-empty directory raises ENOTEMPTY,
            # which the old `except: pass` swallowed — leaving exactly the orphan (a
            # `.lock/` with an unparseable owner) the rollback is meant to prevent. The
            # directory we created holds at most the one `owner` file, so unlink it
            # first, then rmdir removes the now-empty directory. Result: either no
            # `.lock` at all, or (never here) a fully valid owner — the procedure's
            # own contract.
            try:
                if self.owner_file.exists():
                    self.owner_file.unlink()
                os.rmdir(self.dir)
            except OSError:
                pass
            raise SystemExit(
                f"audit/.lock: acquired the directory but could NOT write its owner "
                f"record ({e}); rolled back the half-acquired lock so it is not "
                f"orphaned. Retry; if this persists, check permissions on {self.dir}."
            )
        self.token = nonce
        self.beat()
        envelope.emit(self.dir.parent.parent, "lease_acquired",
                      envelope.orchestrated_session_id(), role=self.role, nonce=nonce)

    def beat(self):
        """Renew the lease. Cheap enough to call from the session's wait loop."""
        try:
            self.heartbeat_file.write_text(
                f"{time.time():.0f}\n", encoding="utf-8")
        except OSError:
            pass

    def heartbeat_age(self):
        """Seconds since the last beat, or None when there is no heartbeat at all.

        None is NOT "old": a lock written by an older xcheck (or by a hand-run session
        following the skill protocol) has no heartbeat, and reclaiming it on that
        absence would break every pre-lease writer. Absence falls back to the pid rules."""
        try:
            beat = float(self.heartbeat_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        return max(0.0, time.time() - beat)

    def lease_dead(self, ttl):
        """True iff this lock carries a heartbeat that STOPPED more than `ttl` ago.

        A live heartbeat is never reclaimable, and a missing one is never a dead lease
        — both fail closed, so the only reclaimable case is one the orchestrator itself
        stopped renewing."""
        age = self.heartbeat_age()
        return age is not None and age > ttl

    def request_cancel(self):
        """Ask the session holding this lock to stop (`xcheck cancel`).

        The request is a FILE rather than a signal because the canceller is usually
        another terminal — and on a shared checkout may not even be able to signal the
        orchestrator's pid. The orchestrator polls it while waiting on the child and
        terminates the child's whole process group, so the session ends at a point where
        the courier and the lock release still run."""
        self.dir.mkdir(exist_ok=True)
        self.cancel_file.write_text(
            datetime.now(timezone.utc).isoformat(timespec="seconds"), encoding="utf-8")

    def cancel_requested(self):
        return self.cancel_file.exists()

    def _clear_lease_files(self):
        # The heartbeat and cancel files live INSIDE the lock directory, so they must go
        # before any rmdir — otherwise the directory is never empty and the lock outlives
        # its owner, which is exactly the wedge the lease exists to prevent.
        for f in (self.heartbeat_file, self.cancel_file):
            try:
                f.unlink()
            except (FileNotFoundError, OSError):
                pass

    def relabel(self, role: str):
        """Rewrite the held lock's role label (pid/started/host/nonce preserved).
        Used when cmd_next acquires the lock before the role is decided."""
        info = self.read() or {}
        info["role"] = role
        try:
            self.owner_file.write_text(json.dumps(info), encoding="utf-8")
        except OSError:
            pass
        self.role = role

    def read(self):
        """The owner record dict, or None. Reads `.lock/owner` for the directory
        protocol; falls back to the legacy `.lock` FILE body so status/unlock can
        still describe an old-protocol lock. None while a writer is mid-acquire
        (directory present, owner record not yet written)."""
        info = _read_lock(self.owner_file)
        if info is not None:
            return info
        if self.legacy_file():
            return _read_lock(self.dir)
        return None

    def release(self):
        # F-0047: owner-checked release. Remove the lock ONLY when its on-disk
        # owner record still carries our nonce — a late release from a previous
        # owner whose lock was force-cleared (so a NEW writer could acquire) reads
        # the new owner's record, sees a different nonce, and touches nothing (§4
            # rule 8). Because we never unlink a foreign `owner` file, a foreign lock
        # directory stays non-empty and our rmdir cannot remove it.
        # SCOPE (F-0047 human ruling 2026-07-25): this holds for every ordinary
        # interleaving — a second acquire() fails EEXIST, and `unlock` without
        # --force refuses anything not provably pid-dead. It does NOT hold across a
        # concurrent `xcheck unlock --force`: the force removes the canonical path
        # itself, so a later writer can acquire while we are still live, and our
        # read->unlink is not atomic against that. Under --force the operator has
        # already asserted "no writing session is active"; if that assertion is
        # false, mutual exclusion is broken by the override, not by this path.
        # POSIX offers no atomic compare-and-delete (nor compare-and-rmdir): a
        # per-owner pathname does NOT close it either (rmdir then races a fresh
        # mkdir). Closing it for real means kernel advisory locking (fcntl.flock on
        # a session-lived fd) — tracked as backlog C6, not a defect of this design.
        if self.token is None:
            return
        info = self.read()
        if not info or info.get("nonce") != self.token:
            return  # not ours (or already gone) — never delete a lock we don't own
        self._clear_lease_files()
        try:
            self.owner_file.unlink()
        except FileNotFoundError:
            pass
        envelope.emit(self.dir.parent.parent, "lease_released",
                      envelope.orchestrated_session_id(), role=self.role,
                      nonce=self.token)
        try:
            os.rmdir(self.dir)
        except OSError:
            # Non-empty (a foreign owner slipped a record in) or already gone —
            # leave it; we removed only our own owner record.
            pass

    def remove(self):
        # F-0047: administrative UNCONDITIONAL removal — the `xcheck unlock` path,
        # where a human clears a lock they may not own. Distinct from the
        # owner-checked `release`; gated by cmd_unlock's fail-closed liveness
        # check, never called on a session's normal exit. Handles both the
        # directory protocol (owner file + rmdir) and a legacy FILE lock (unlink).
        if self.legacy_file():
            try:
                self.dir.unlink()
            except FileNotFoundError:
                pass
            return
        self._clear_lease_files()
        try:
            self.owner_file.unlink()
        except (FileNotFoundError, OSError):
            pass
        try:
            os.rmdir(self.dir)
        except (FileNotFoundError, OSError):
            pass


def pid_alive(pid):
    # F-0010: os.kill(pid, 0) distinguishes three cases, and only ONE means
    # dead. ProcessLookupError (ESRCH) => no such process. PermissionError
    # (EPERM) => the process EXISTS but we may not signal it (foreign owner on a
    # shared checkout) — that is alive, not stale; treating it as dead would
    # mislabel a live foreign lock STALE and lure an operator into `unlock
    # --force` while a writing session is active (§4 rule 8). A bad
    # pid value (ValueError/TypeError) is not provably alive, so: not alive.
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False
    return True


def pid_provably_dead(pid):
    # F-0047: the fail-CLOSED dual of pid_alive, used by `unlock`/`acquire` to
    # decide whether a lock is provably dead (F-0095: the label that gates
    # DIAGNOSIS, not a non-force clear — only `--force` ever removes a lock).
    # True ONLY for a REAL
    # integer pid the OS confirms has no process (ProcessLookupError/ESRCH).
    # Everything else is NOT provably dead: a live pid, an EPERM foreign owner, an
    # unsignalable pid, AND crucially a MALFORMED/non-integer pid — a schema-invalid
    # pid like "not-a-pid" is not a real dead PID, so treating pid_alive()==False
    # as "safe to remove" would clear a possibly-live lock (reopen poison #1). Note
    # this is NOT the boolean complement of pid_alive: a malformed pid is neither
    # alive nor provably dead.
    try:
        pidn = int(pid)
    except (ValueError, TypeError):
        return False
    try:
        os.kill(pidn, 0)
    except ProcessLookupError:
        return True
    except (PermissionError, OSError):
        return False
    return False


def lock_provably_dead(info):
    """F-0094: fail-CLOSED staleness for a WHOLE owner record — host included.
    A lock is provably dead (F-0095: hence diagnosable as STALE, NOT clearable
    without `--force` — only `--force` removes) ONLY when its owner record is a
    dict, was written on THIS host (`socket.gethostname()`), and carries a real integer
    pid the local OS confirms gone. A lock owned by a DIFFERENT host is never
    provably dead here: `os.kill` checks only the LOCAL pid namespace, so a local
    ESRCH proves nothing about a process on another host that shares this
    checkout/volume — labelling it STALE would lure an operator into `--force`
    while the remote orchestrator is live (§4 rule 8). A missing/blank host cannot
    be confirmed local either, so it too is not provably dead. This is the
    host-aware dual of pid_provably_dead, used by `unlock`/`acquire`/`status` for
    the STALE-vs-LIVE diagnosis (F-0095: never a non-force clear decision — removal
    is `--force` only); pid_provably_dead stays the raw local-pid predicate for
    callers that already hold the host context."""
    if not isinstance(info, dict):
        return False
    host = (info.get("host") or "").strip()
    if host != socket.gethostname():
        return False
    return pid_provably_dead(info.get("pid", -1))


# ---------------------------------------------------------------- sandbox

# Phase 8 inverts the execution default. Until now the child agent CLI inherited the
# operator's whole environment, ran in the project itself, had no time bound and — by
# the documented default — was launched with `--dangerously-skip-permissions`. For a
# tool whose input is an ARBITRARY repository, that is an ambient grant: whatever the
# repository can talk the agent into doing, it does with the operator's credentials, in
# the operator's tree, for as long as it likes.
#
# The move is containment, not prohibition. `--dangerously-skip-permissions` still
# works — inside an isolating profile — because deleting it would push the operator to
# run sessions outside xcheck entirely, which removes every control at once instead of
# one. A contained grant survives contact with the real workflow.


class Profile:
    """A NAMED capability set, not a boolean.

    `container` (or anything else) can be added later without a schema change: phase 9
    stores the profile NAME plus this `details()` object, so a new profile is new data,
    not a new field."""

    def __init__(self, name, isolating, material_writable, summary, backend=None):
        self.name = name
        self.isolating = isolating                    # child runs OUTSIDE the project tree
        self.material_writable = material_writable    # may change files outside audit/
        self.summary = summary
        self.backend = backend                        # None = process-level; "docker" = OS-enforced

    def details(self, workdir=None, conf=None, verified=None):
        """The envelope's `sandbox_details`, extended in phase 6 with a CAPABILITY
        REPORT: what this profile actually enforced at launch, not what it is called.

        `verified_at_launch` is the honest discriminator. A worktree profile isolates
        the repository and nothing else — the child can still read `$HOME`, write
        `/tmp` and open a socket — so it reports `False` and says why. Only a profile
        whose backend was probed and answered gets `True`."""
        d = {"isolating": self.isolating,
             "material_writable": self.material_writable,
             "workdir": str(workdir) if workdir else None}
        if self.backend:
            d["capabilities"] = container_capabilities(
                conf, workdir=workdir, verified=verified)
        else:
            d["capabilities"] = {
                "enforced_by": "process",
                "verified_at_launch": False,
                "network": "host (unrestricted)",
                "home": "the operator's HOME",
                "filesystem": "the whole machine is readable and /tmp is writable; "
                              "only the REPOSITORY is isolated",
                "note": "worktree/readonly confine what comes BACK (the courier's "
                        "diff), not what the child can reach",
            }
        return d


PROFILES = {
    "worktree": Profile(
        "worktree", True, True,
        "a disposable git worktree in a system temp dir; changes return through the courier"),
    "readonly": Profile(
        "readonly", True, False,
        "a disposable worktree whose changes outside audit/ are refused, not applied"),
    "none": Profile(
        "none", False, True,
        "no isolation — the child runs in the project itself (explicit opt-out)"),
    # OPT-IN, and never a default. The worktree profiles isolate the REPOSITORY; this
    # one asks the operating system to isolate the MACHINE — no network, a synthetic
    # HOME, the source checkout mounted read-only, and bounded CPU/memory/PIDs. It
    # needs a working docker and refuses without one rather than degrading (see
    # `require_backend`).
    "container": Profile(
        "container", True, True,
        "a disposable clone inside a docker container: no network, synthetic HOME, "
        "the source checkout mounted read-only, bounded CPU/memory/PIDs",
        backend="docker"),
}

# Roles that only READ the material get `readonly` by default: an Auditor files findings
# and a Verifier writes verdicts, and neither has any business editing the code it is
# judging. The default is per ROLE rather than global so the safe case needs no
# configuration — an existing orchestrator.conf gets the protection without an edit,
# which is the point of every default in this phase.
DEFAULT_PROFILE = {"Auditor": "readonly", "Verifier": "readonly"}

SKIP_PERMISSION_FLAGS = (
    "--dangerously-skip-permissions",
    "--dangerously-bypass-approvals-and-sandbox",
    "--yolo",
    "--full-auto",
)

# The child's environment is BUILT, not inherited. Everything not named here (or in
# `env_allowlist`, or starting with `XCHECK_`) is dropped, so cloud credentials, signing
# keys and production tokens cannot reach the agent by accident. An agent CLI that
# genuinely needs a key in its environment gets it by the operator NAMING that key — a
# deliberate act with a record.
ENV_ALLOWLIST = frozenset({
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TMPDIR", "TZ",
    "USER", "LOGNAME", "SHELL", "SSL_CERT_FILE", "SSL_CERT_DIR",
})

SECRET_NAME_RE = re.compile(
    r"(SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|APIKEY|API_KEY|AUTH|COOKIE|_KEY$|^KEY$)",
    re.IGNORECASE)

# Well-known token SHAPES, for values that never passed through this process's
# environment at all (a key the agent reads out of a file and echoes). Best-effort
# against ACCIDENT — see `Redactor`.
TOKEN_SHAPES = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)

# The six declared session outcomes, plus `cancelled` — declared in `util` (phase 9)
# because `state.json` validates the value the envelope stores, and `state` cannot
# import `runner`. Bound here so every existing importer keeps working.
OUTCOMES = OUTCOMES

# Log signatures, checked in this order. The ORDER is the semantics: a run that was both
# rate-limited and non-zero is `blocked`, not a generic `crash`, because what the
# operator does next differs (wait vs. investigate).
_SIGNATURES = (
    ("blocked", ("content policy", "moderation", "flagged as", "usage limit",
                 "rate limit", "quota exceeded", "not allowed to assist")),
    ("provider-error", ("api error", "internal server error", "service unavailable",
                        "overloaded", "connection reset", "502 bad gateway",
                        "upstream error", "temporarily unavailable")),
    ("refused", ("i cannot complete", "i will not", "refusing this charter",
                 "out-of-competence", "charter-ambiguous", "material-missing",
                 "blocked-dependency", "norm-conflict", "cost-exceeded")),
)


# ------------------------------------------------------------- container backend
#
# The worktree profiles isolate the REPOSITORY. They do not isolate the MACHINE, and
# the planning probe for this phase showed exactly how thin that is: from inside a live
# disposable worktree, `git worktree list --porcelain` prints the ORIGINAL checkout's
# absolute path, and appending to a file there succeeded — the courier was never
# consulted, because the courier only sees what comes back through the patch. A child
# that writes the original tree directly has gone around it.
#
# Containment is enforced by the operating system or it is not containment. This
# backend asks docker for: no network, an empty synthetic HOME, the source checkout
# mounted read-only, exactly two writable mounts, bounded CPU/memory/PIDs, no
# capabilities, no new privileges, and no host sockets — in particular never the docker
# socket, which would hand the child the host it is contained by.

CONTAINER_SRC, CONTAINER_WORK, CONTAINER_OUT = "/src", "/work", "/out"
CONTAINER_HOME = "/home/xcheck"

# Host-specific names that must NOT travel into the container: PATH and SHELL name
# binaries that are not there, HOME is the whole point of the synthetic one, and
# TMPDIR/USER/LOGNAME describe the host account. Everything else the allowlist already
# let through is forwarded BY NAME (`-e VAR`, no value), so no secret is ever written
# onto the docker argv where `ps` would show it.
CONTAINER_ENV_BLOCK = frozenset({"PATH", "HOME", "SHELL", "TMPDIR", "USER", "LOGNAME"})


def backend_probe(binary="docker", timeout=20):
    """`(ok, detail)` for a container backend. NEVER raises.

    `status` asks this too, and on a machine without docker that is a fact to report,
    not a reason to fail a read-only command. The refusal belongs at the launch door
    (`require_backend`), which is a different question from "is it there"."""
    exe = shutil.which(binary)
    if not exe:
        return False, f"{binary} is not on PATH"
    try:
        p = subprocess.run([exe, "info", "--format", "{{.ServerVersion}}"],
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"{binary} is on PATH but could not be run ({e})"
    if p.returncode != 0:
        why = (p.stderr or p.stdout or "").strip().splitlines()
        return False, (f"`{binary} info` failed: {why[-1][:160] if why else 'no output'}")
    return True, f"{binary} server {p.stdout.strip() or 'unknown'}"


def require_backend(profile, role="the session"):
    """Fail CLOSED. Returns the backend detail string, or raises."""
    if not profile.backend:
        return None
    ok, detail = backend_probe(profile.backend)
    if not ok:
        raise SystemExit(
            f"refusing to launch {role}: sandbox_profile='container' needs a working "
            f"{profile.backend}, and {detail}. NOT falling back to a weaker profile — "
            f"`container` is the only profile whose containment the operating system "
            f"enforces, and running under worktree|readonly|none instead would give "
            f"you the word without the thing. Start {profile.backend}, or set "
            f"sandbox_profile to one of: none, readonly, worktree.")
    return detail


def container_limits(conf):
    """`(cpus, memory_mb, pids)`, every one of them non-zero, or refuse.

    `address_space_mb` wins over `container_memory_mb` when the operator set it: one
    memory knob, and the pre-existing one keeps its meaning. A limit of 0 means
    UNLIMITED everywhere else in this file, and unlimited under a profile whose claim
    is a bound is the claim without the thing — so 0 refuses here."""
    conf = conf if conf is not None else dict(CONF_DEFAULTS)
    raw_cpus = str(conf.get("container_cpus", "") or CONF_DEFAULTS["container_cpus"]).strip()
    try:
        cpus = float(raw_cpus)
    except ValueError:
        cpus = 0.0
    mem = conf_number(conf, "address_space_mb") or conf_number(conf, "container_memory_mb")
    pids = conf_number(conf, "container_pids")
    # `container_memory_mb` and `container_pids` are NUMERIC_CONF keys with a minimum
    # of 1, so `conf_number` above already refused a zero and named the bound. Only
    # `container_cpus` reaches this list from a conf file — it is fractional, so it is
    # not a NUMERIC_CONF key. The other two stay in the list as a fail-closed guard for
    # a caller that builds the mapping itself and skips that coercion.
    bad = [n for n, v in (("container_cpus", cpus),
                          ("container_memory_mb/address_space_mb", mem),
                          ("container_pids", pids)) if not v or v <= 0]
    if bad:
        raise SystemExit(
            f"refusing to launch under sandbox_profile='container': "
            f"{', '.join(bad)} is zero or unreadable, and 0 means UNLIMITED. This "
            f"profile's whole claim is that the operating system bounds the child, so "
            f"an unlimited limit is refused rather than applied.")
    return raw_cpus, mem, pids


# 0.9.1 phase 9 (audit P1 #12). An image reference is PINNED when it names the exact
# bytes docker will run: `name@sha256:<64 lowercase hex>`. Anything else — a bare tag, a
# short digest, a non-sha256 algorithm, or a tag with a digest hung off it — leaves the
# containment claim resting on whatever the registry serves that day.
#
# `name:tag@sha256:...` is legal to docker (the digest wins, the tag is decoration) and
# is refused here anyway, because the reference is then TWO answers to one question and
# every reader has to know which one docker picks. A reference an operator can misread
# is not a pin.
_DIGEST_REF = re.compile(r"^(?P<name>[^@]+)@(?P<algo>[A-Za-z0-9][A-Za-z0-9+._-]*)"
                         r":(?P<hex>[^@:]*)$")


def unpinned_reason(image):
    """Why `image` is not pinned to bytes, or None when it is."""
    image = (image or "").strip()
    if not image:
        return "it is empty"
    m = _DIGEST_REF.match(image)
    if not m:
        return ("it names no digest at all — it is a mutable reference, and what it "
                "resolves to is whatever the registry serves at pull time")
    algo, hexpart = m.group("algo"), m.group("hex")
    if algo != "sha256":
        return (f"its digest algorithm is {algo!r}, and this profile pins by sha256 "
                f"only — an algorithm nobody verifies is a digest-shaped string")
    if len(hexpart) != 64 or any(c not in "0123456789abcdef" for c in hexpart):
        return (f"its digest is {len(hexpart)} character(s) of {hexpart[:12]!r}…, not "
                f"the 64 lowercase hex characters of a sha256 — a truncated or "
                f"upper-cased digest does not identify one image")
    last = m.group("name").rsplit("/", 1)[-1]
    if ":" in last:
        return (f"it carries BOTH a tag ({last.split(':', 1)[1]!r}) and a digest. "
                f"docker resolves the digest and ignores the tag, so the reference "
                f"says one thing to docker and another to the person reading it")
    return None


def container_image_ref(conf):
    """`(image, waiver)` — the image this profile will run, or refuse.

    `waiver` is None when the image is pinned. When the operator set
    `unsafe_allow_unpinned_image=on` it is the sentence that goes into the run's LOG,
    not merely onto the console: an operator reading that log a month later has to be
    able to see that the guarantee was waived for that run."""
    conf = conf if conf is not None else dict(CONF_DEFAULTS)
    image = str(conf.get("container_image", "") or CONF_DEFAULTS["container_image"]).strip()
    why = unpinned_reason(image)
    if why is None:
        return image, None
    if not conf_flag(conf, "unsafe_allow_unpinned_image"):
        raise SystemExit(
            f"refusing to launch under sandbox_profile='container': "
            f"container_image={image!r} is not pinned by digest — {why}.\n"
            f"  This profile's containment was MEASURED against a specific image. A "
            f"reference that can resolve to different bytes tomorrow makes that "
            f"measurement a statement about an image nobody can name.\n"
            f"  Resolve the tag to a digest and set the result:\n"
            f"    docker pull {image}\n"
            f"    docker inspect --format='{{{{index .RepoDigests 0}}}}' {image}\n"
            f"  Or, deliberately and at a stated cost, set "
            f"unsafe_allow_unpinned_image=on in audit/orchestrator.conf — the waiver is "
            f"written into the session log so the run carries its own record of it.")
    return image, (f"WAIVED: container image pinning. container_image={image!r} is not "
                   f"pinned by digest — {why}. Permitted by "
                   f"unsafe_allow_unpinned_image=on. The containment this profile "
                   f"reports was measured against an image this run cannot name by "
                   f"its bytes.")


def _allowlist_or_empty(conf):
    """The egress allowlist, or `()` if it is off or unreadable.

    The capability REPORT must never be the thing that raises: it is called while
    building a refusal message elsewhere, and a report that explodes turns an addressed
    error into a traceback. The launch path validates the same string and refuses
    there, where refusing is the job."""
    from xcheck.egress import EgressError, allowlist
    try:
        return allowlist(conf)
    except EgressError:
        return ()


def container_capabilities(conf, workdir=None, out_dir=None, source=None, verified=None):
    """What the container profile enforced, as data. Stored in the envelope's
    `sandbox_details` (a free-form object in the schema, so this needs no migration)."""
    conf = conf if conf is not None else dict(CONF_DEFAULTS)
    image, waiver = container_image_ref(conf)
    try:
        cpus, mem, pids = container_limits(conf)
    except SystemExit as e:
        cpus, mem, pids = None, None, None
        verified = verified or f"limits refused: {e}"
    return {
        "enforced_by": "docker",
        "verified_at_launch": bool(verified) and not str(verified).startswith("limits refused"),
        "backend_detail": verified,
        "image": image,
        # Not `"@sha256:" in image` — that answered True for a short digest, an
        # upper-cased one, and a tag with a digest hung off it. The report says
        # pinned exactly when the launch path would accept the reference unwaived.
        "image_pinned_by_digest": unpinned_reason(image) is None,
        "image_pin_waived": bool(waiver),
        "network": ("none" if not _allowlist_or_empty(conf)
                    else f"internal + broker allowlist: "
                         f"{', '.join(_allowlist_or_empty(conf))}"),
        "egress_allowlist": list(_allowlist_or_empty(conf)),
        "home": f"{CONTAINER_HOME} (tmpfs, empty — not the operator's HOME)",
        "source_mount": f"{source or '<project>'} -> {CONTAINER_SRC} (read-only)",
        "writable": [f"{workdir or '<disposable clone>'} -> {CONTAINER_WORK}",
                     f"{out_dir or '<session output>'} -> {CONTAINER_OUT}"],
        "cpus": cpus,
        "memory_mb": mem,
        "pids_limit": pids,
        "capabilities_dropped": "ALL",
        "no_new_privileges": True,
        "host_sockets_mounted": [],
    }


def container_argv(cmd, conf, workdir, out_dir, source, env=None, name=None,
                   broker=None):
    """The `docker run …` argv that runs `cmd` under the container profile.

    `--entrypoint cmd[0]` rather than relying on the image's own entrypoint: the
    profile must behave the same whatever image the operator names.

    Environment travels BY NAME (`-e VAR`), so the value is read from this process's
    environment by the docker client and never appears in the argv — `ps` on a shared
    machine would otherwise show every forwarded token."""
    cpus, mem, pids = container_limits(conf)
    image, _waiver = container_image_ref(conf)     # refuses an unpinned image here too
    uid, gid = os.getuid(), os.getgid()
    argv = [
        "docker", "run", "--rm",
        # Phase 10: `--network=none` unless an egress broker was established for this
        # run, in which case the container joins that broker's INTERNAL network — no
        # route off it, and the broker is the only thing on it that has one. With no
        # `egress_allowlist` there is no broker and this list is what it always was.
        *(broker.container_flags() if broker is not None else ["--network=none"]),
        f"--cpus={cpus}",
        f"--memory={mem}m",
        f"--pids-limit={pids}",
        "--security-opt", "no-new-privileges",
        "--cap-drop", "ALL",
        # tmpfs, not a host bind: an empty HOME that has no path on the host at all,
        # owned by the invoking uid so the child can actually use it.
        "--tmpfs", f"{CONTAINER_HOME}:rw,size=64m,mode=0700,uid={uid},gid={gid}",
        "-e", f"HOME={CONTAINER_HOME}",
        "-v", f"{Path(source)}:{CONTAINER_SRC}:ro",
        "-v", f"{Path(workdir)}:{CONTAINER_WORK}:rw",
        "-v", f"{Path(out_dir)}:{CONTAINER_OUT}:rw",
        "-w", CONTAINER_WORK,
        # The child writes into the mounted clone; those files must belong to the
        # operator, or `collect()` — which runs as the operator — cannot stage them.
        "--user", f"{uid}:{gid}",
    ]
    # A NAME, so the container can be reached after its client is gone.
    # `--rm` cleans up when `docker run` exits normally. It does nothing when the
    # client is KILLED: the daemon owns the container, not the CLI, so a session that
    # times out leaves the workload running with the whole network, CPU and PID budget
    # it was given — which is exactly the escape adversarial attack A5 makes, and how
    # this was found. `Sandbox.leave()` removes it by this name, on every path out.
    if name:
        argv += ["--name", str(name)]
    for name in sorted(env or {}):
        if name not in CONTAINER_ENV_BLOCK:
            argv += ["-e", name]
    argv += ["--entrypoint", str(cmd[0]), image]
    argv += [str(c) for c in cmd[1:]]
    return argv


def host_socket_mounts(argv):
    """Every `-v` in `argv` whose host side is a socket or a well-known daemon socket.

    A test asserts this is empty. Mounting the docker socket into the child would hand
    it the daemon that contains it — the classic container escape, and the one mistake
    that would make this whole profile decorative."""
    out = []
    for i, a in enumerate(argv):
        if a != "-v" or i + 1 >= len(argv):
            continue
        host = argv[i + 1].split(":", 1)[0]
        if host.endswith(".sock") or "docker.sock" in host or (
                Path(host).exists() and Path(host).is_socket()):
            out.append(argv[i + 1])
    return out


# WHERE the child runs and WHAT it may change are two different questions, and a single
# enum conflated them. `DEFAULT_PROFILE` gives the reading roles `readonly` only while
# `sandbox_profile` is EMPTY, so an operator who set `sandbox_profile=worktree` — or,
# after phase 6, the stronger `container` — silently dropped the read-only gate for the
# two roles that exist to judge code without editing it. Asking for better containment
# must never buy an Auditor write access to the material.
#
# The twin keeps the profile's NAME, because the containment genuinely IS that profile
# and the envelope and `status` should say so; only the authorization differs.
READONLY_TWINS = {
    name: Profile(p.name, p.isolating, False,
                  p.summary + "; changes outside audit/ are refused for this "
                              "reading role",
                  backend=p.backend)
    for name, p in PROFILES.items() if p.material_writable}


def resolve_profile(conf, role):
    """The effective profile for `role`, failing closed on an unknown name."""
    name = str(conf.get("sandbox_profile", "") or "").strip() or DEFAULT_PROFILE.get(role, "worktree")
    if name not in PROFILES:
        raise SystemExit(
            f"orchestrator.conf sandbox_profile={name!r} is not a profile "
            f"({', '.join(sorted(PROFILES))}). Refusing to launch: guessing which "
            f"containment was meant is how an isolation setting silently becomes none.")
    if DEFAULT_PROFILE.get(role) == "readonly" and PROFILES[name].material_writable:
        return READONLY_TWINS[name]
    return PROFILES[name]


def require_trust(conf, role, profile):
    """Refuse to dispatch until the operator has classified the material — and, if it is
    untrusted, until the profile is an actual OS boundary.

    This is a CONTAINMENT question and nothing else. It grants no verb and revokes none:
    `write.authorize_dispatch_write` decides who may change what, and the two must stay
    orthogonal or the F-0069-era conflation returns in a new enum (one value meaning both
    "how contained" and "how privileged" is how a containment setting silently became an
    authorization one last time).

    `Status` is not dispatched at all, so it never reaches here — a read-only surface that
    moves no machine state has nothing for containment to mediate.
    """
    try:
        level = policy.trust_level(conf)
    except policy.PolicyError as e:
        raise SystemExit(str(e)) from None
    if level is None:
        raise SystemExit(policy.no_trust_level_refusal(role))
    if level == policy.UNTRUSTED and profile.name != "container":
        raise SystemExit(policy.untrusted_needs_container_refusal(
            role, str(conf.get("sandbox_profile", "") or "").strip()))
    return level


def uncontained_grant(cmd, profile):
    """The skip-permissions flags in `cmd` that this profile does not contain."""
    if profile.isolating:
        return []
    return [c for c in cmd
            if any(c == f or c.startswith(f + "=") for f in SKIP_PERMISSION_FLAGS)]


def printable_cmd(cmd, prompt):
    """The dispatch argv as one line, with the prompt itself replaced by a marker.

    PHASE 2 (fifth audit): this used to print `cmd[:4]`, which stops one token short of
    where a `--model` usually sits — so `--dry-run` could not show an operator the model
    it had routed to, and the audit's routing defect (a dry run that previewed one
    command while the real run built another) was invisible from the surface that exists
    to preview it. Everything but the prompt is shown; the prompt is thousands of
    characters of role instructions and is the one part that is not a decision.
    """
    return " ".join("[+prompt]" if prompt and prompt in p else shlex.quote(p)
                    for p in cmd)


def refuse_uncontained(cmd, profile, role):
    flags = uncontained_grant(cmd, profile)
    if flags:
        raise SystemExit(
            f"refusing to launch {role}: {' '.join(flags)} grants the agent unattended "
            f"write access, and sandbox_profile={profile.name!r} does not contain it "
            f"({profile.summary}). Either drop the flag from {role.lower()}_cmd, or run "
            f"an isolating profile (sandbox_profile=worktree|readonly). The flag is "
            f"supported — ambiently, it is not.")


def child_environment(conf, extra=None):
    """The child's environment, BUILT from the allowlist rather than inherited."""
    names = set(ENV_ALLOWLIST)
    names |= {n.strip() for n in str(conf.get("env_allowlist", "") or "").split(",") if n.strip()}
    env = {k: v for k, v in os.environ.items() if k in names or k.startswith("XCHECK_")}
    # The one XCHECK_* variable the child must NOT inherit. It points at the operator
    # profile — the file that says what may run on this machine and inside what
    # boundary — which lives outside the subject precisely so a session cannot reach
    # it. `XCHECK_*` is copied wholesale by design (session id, lock nonce, construal
    # key are all things the child needs); this one is the exception, and it is an
    # exception about a PATH: a session that never learns where the profile is cannot
    # read the terms of its own audit, let alone go looking for a way to write them.
    from xcheck.policy import OPERATOR_PROFILE_ENV
    env.pop(OPERATOR_PROFILE_ENV, None)
    env.update(extra or {})
    return env


class Redactor:
    """Replaces secret VALUES in the child's output with `«redacted:NAME»`.

    Two sources: the values of environment variables PASSED TO THE CHILD whose name
    looks secret-ish, and a small set of well-known token shapes.

    SCOPE, stated honestly: this is best-effort against ACCIDENT — an agent that echoes
    a key it was handed, a stack trace that prints the environment. It is NOT a control
    against an adversary with write access to the child, who can split, encode or
    transform a value into something no matcher recognises. The control that bounds an
    adversary is the ALLOWLIST above (the secret never reaches the child), not this."""

    def __init__(self, env=None):
        self.pairs = []
        for name, value in sorted((env or {}).items()):
            if value and len(value) >= 8 and SECRET_NAME_RE.search(name):
                self.pairs.append((value, f"«redacted:{name}»"))
        self.pairs.sort(key=lambda p: len(p[0]), reverse=True)   # longest match first

    def scrub(self, text):
        for value, mask in self.pairs:
            text = text.replace(value, mask)
        for shape in TOKEN_SHAPES:
            text = shape.sub("«redacted:token-shape»", text)
        return text


def rlimit_preexec(conf):
    """A `preexec_fn` applying RLIMIT_CPU / RLIMIT_AS, or None when neither is set.

    POSIX-only. If limits ARE configured and `resource` is unavailable, this fails
    CLOSED rather than pretending the limit applied — an unenforced limit the operator
    believes in is worse than a declared absence."""
    cpu = conf_number(conf, "cpu_seconds")
    mem_mb = conf_number(conf, "address_space_mb")
    if not cpu and not mem_mb:
        return None
    try:
        import resource
    except ImportError:
        raise SystemExit(
            "orchestrator.conf sets cpu_seconds/address_space_mb, but this platform has "
            "no `resource` module, so the limit cannot be applied. Refusing to launch: a "
            "limit that silently does not apply is worse than none. Unset both keys to "
            "run without resource limits.")

    def apply():                                    # pragma: no cover - runs in the child
        if cpu:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        if mem_mb:
            n = mem_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (n, n))
    return apply


def classify_outcome(rc, timed_out=False, cancelled=False, log_text="", stalled=False):
    """One of OUTCOMES for a finished child. Phase 9 stores this in the envelope; the
    loop uses it to tell "the agent declined" from "the provider fell over"."""
    if cancelled:
        return "cancelled"
    # Before `timeout`, and deliberately: a stall is diagnosed by a deadline that fires
    # INSIDE the session budget, so the wall-clock cap has not been reached and the two
    # can never both be true — but if a future bound ever made them overlap, the specific
    # answer is the useful one.
    if stalled:
        return "stalled"
    if timed_out:
        return "timeout"
    if rc == 0:
        return "ok"
    low = (log_text or "").lower()
    for outcome, needles in _SIGNATURES:
        if any(n in low for n in needles):
            return outcome
    return "crash"


# The two outcomes a retry may follow, and the log needles that separate a TRANSPORT
# timeout (the connection died; the work was never done) from the session's own
# wall-clock budget (the work ran and did not finish). They are the same `timeout`
# outcome and they call for opposite decisions: re-dispatching the first is a retry,
# re-dispatching the second spends the same budget again on the same charter.
RETRYABLE = ("provider-error", "timeout")
# A bound that can be set to 10**21 is not a bound. `retry_limit` is a NUMERIC_CONF key
# with a minimum and no maximum, so the fuzz of phase 13 asked what a huge one does: it
# is accepted, and a transient provider fault then re-dispatches a paid session for as
# long as the fault lasts. Ten is far above any real use (the backoff alone reaches its
# 60s ceiling at attempt 6) and finite, which is the property that matters.
RETRY_LIMIT_MAX = 10
_TRANSPORT_TIMEOUT = ("connection timed out", "connect timeout", "read timed out",
                      "etimedout", "handshake timed out", "request timed out",
                      "timed out while connecting", "socket timeout")


def retry_decision(outcome, attempt=0, limit=2, log_text="", state_changed=False,
                   blocked_by=()):
    """`(retry, reason)` for one finished session. TOTAL over `OUTCOMES`.

    Every declared outcome is answered by name here rather than by falling through a
    default, because the failure mode of a retry policy is the outcome nobody thought
    about inheriting whichever branch happened to be last. An outcome this function does
    not know refuses — a policy that guesses is not a policy.

    `state_changed` is the hard bar and it is checked first: a session that already
    changed the project has effects a second run would duplicate, whatever it returned.
    """
    if limit > RETRY_LIMIT_MAX:
        raise SystemExit(
            f"orchestrator.conf: retry_limit must be at most {RETRY_LIMIT_MAX}, got "
            f"{limit} — a retry bound that large is not a bound, and a transient "
            f"provider fault would re-dispatch a paid session until the fault ends. "
            f"Set a smaller retry_limit, or set it to 0 to stop retrying at all.")
    if outcome not in OUTCOMES:
        return False, (f"{outcome!r} is not a declared outcome — refusing to retry "
                       f"something this policy has no classification for")
    if state_changed:
        # Name the files. "Something changed" makes the operator re-derive by hand the
        # one fact the gate already had in its hand.
        named = ", ".join(list(blocked_by)[:5]) if blocked_by else ""
        if blocked_by and len(blocked_by) > 5:
            named += f" (+{len(blocked_by) - 5} more)"
        return False, ("this session already changed the project — a retry would run "
                       "its charter a second time on top of its own effects"
                       + (f"; changed: {named}" if named else ""))
    if outcome == "ok":
        return False, "the session succeeded; there is nothing to retry"
    if outcome == "refused":
        return False, ("the role refused its charter — a refusal retried is a refusal "
                       "ignored; answer the refusal instead")
    if outcome == "blocked":
        return False, ("the provider blocked this dispatch (moderation, quota or rate "
                       "limit) — retrying hammers the same wall; a human decides")
    if outcome == "crash":
        return False, ("the session crashed — a schema error and a capability violation "
                       "both land here, and retrying either gives a second attempt to "
                       "exactly the thing that must not get one")
    if outcome == "cancelled":
        return False, "the operator cancelled this session; a retry would overrule them"
    if outcome == "stalled":
        # Not retried, and not by omission. A stall is the provider or the agent failing
        # to begin at all, and the same charter under the same command is the most likely
        # thing to stall again — this is the outcome the deadline exists to stop PAYING
        # for, so re-dispatching it automatically would undo the phase that added it.
        return False, ("the session hit one of its deadlines having produced no output "
                       "or no observable activity — the record's note says which — and "
                       "a stall retried is the same stall paid for twice; look at why "
                       "nothing happened (provider, prompt, or containment) before "
                       "dispatching this charter again")
    if outcome == "timeout" and not any(n in (log_text or "").lower()
                                        for n in _TRANSPORT_TIMEOUT):
        return False, ("the session exhausted its own wall-clock budget rather than "
                       "losing a connection — retrying spends it again on the same "
                       "charter; raise session_timeout or narrow the charter")
    if attempt >= limit:
        return False, (f"retry limit reached ({attempt} of {limit}) — a transient fault "
                       f"that survives {limit} attempts is not transient")
    return True, (f"{outcome} is transient; attempt {attempt + 1} of {limit}")


def retry_backoff(attempt):
    """Seconds to wait before attempt `attempt` (0-based). Grows, and is bounded.

    A provider that just failed is the one thing a retry should not hit immediately —
    the same instant that failed is the instant most likely to fail again.
    """
    return min(2 ** attempt, 60)


def kill_group(proc, grace):
    """SIGTERM the child's process GROUP, wait `grace`, then SIGKILL what is left."""
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        return
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 5)):
        try:
            os.killpg(pgid, sig)
        except OSError:
            return
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


# PHASE 12, renamed in phase 8 of the third audit. What separates a session that is
# PRODUCING OUTPUT from one that is silent — and, separately, one that is WORKING from
# one that is merely noisy.
#
# The mechanical definition, stated here because neither bound may be a judgement about
# content: a session has produced output when the bytes it has written to its log exceed
# what it would have written by doing nothing at all — the agent CLI's banner plus the
# prompt echoed back — or when it has recorded a canonical state transition. Both are
# counted by the orchestrator, from outside; neither reads what the session said.
#
# That is a hang detector and nothing more, which is what the third audit found.
# HISTORICAL: the old name, `first useful action`, promised semantic progress detection
# while measuring chattiness. A child that prints 2,049 bytes of nothing clears it, and
# can then hold its slot to the hard cap by printing again before every idle bound. The mechanism is kept —
# it is a GOOD hang detector — under the name of the thing it counts, and the signal it
# was mistaken for is below.
#
# The allowance below is the banner half. Measured on the W-06 run: the stalled session's
# ENTIRE log was 1,764 bytes, of which the echoed prompt was most of it, while the eight
# that worked wrote 527KB to 1.1MB. The prompt's own length is known exactly and is passed
# in per session, so this constant covers only the CLI's preamble, and 2048 is roughly
# four times the largest banner in that run.
BANNER_ALLOWANCE = 2048

# The evidence a child cannot manufacture by printing. Every source here is something the
# child had to DO, observed by the parent from outside the sandbox — not something it
# said, and not a volume of bytes. Enumerated rather than described, so a test can hold
# the list and a future source has to be added on purpose.
#
#   canonical-transition  a declared write verb reached `audit/events.jsonl`. The
#                         strongest: the session changed the audit's canonical state.
#   telemetry-sidecar     the PARENT-owned sidecar for this session validates (phase 6).
#                         Its path never enters the child environment.
#   worktree-write        the session's own worktree is dirty in a way it was not at
#                         dispatch. An agent that has read, thought and written a single
#                         file has done this; one padding its log has not.
ACTIVITY_SOURCES = ("canonical-transition", "telemetry-sidecar", "worktree-write")


def activity_probe(project, session_id, workdir, baseline, sidecar=None):
    """The first of `ACTIVITY_SOURCES` observed, or None. Never raises.

    Called from the wait loop, so a probe that raised would kill a working session on a
    transient git error — the one failure this must not have. Fails toward "no activity
    seen yet", which only ever costs a stall the operator can read in the record; the
    opposite default would let a broken probe declare every session productive.
    """
    try:
        if envelope.session_moved(project, session_id):
            return ACTIVITY_SOURCES[0]
    except Exception:
        pass
    if sidecar:
        try:
            got = json.loads(Path(sidecar).read_text(encoding="utf-8"))
            if isinstance(got, dict) and not envelope.sidecar_refusals(got, session_id):
                return ACTIVITY_SOURCES[1]
        except (OSError, ValueError, TypeError):
            pass
    try:
        if dirty_paths(workdir) != baseline:
            return ACTIVITY_SOURCES[2]
    except Exception:
        pass
    return None


def run_child(cmd, cwd, log_path, env, conf, lock=None, timeout=None, profile=None,
              header=(), broker=None, timings=None, echoed_bytes=0, moved=None,
              activity=None):
    """Run the agent CLI as its own process-group leader, bounded and redacted.
    Returns `(rc, outcome, elapsed_seconds)`.

    `start_new_session=True` is the load-bearing part: the child leads its own process
    group, so a timeout kills the GROUP — a grandchild the agent spawned (a dev server, a
    runaway build) dies with it instead of outliving the session and holding whatever it
    holds. Killing only the child would leave exactly the process that made the timeout
    necessary."""
    timeout = conf_number(conf, "session_timeout") if timeout is None else timeout
    grace = conf_number(conf, "kill_grace")
    redactor = Redactor(env)
    # Under a container profile the process this code spawns is the docker CLIENT, not
    # the agent. An RLIMIT on the client bounds the wrong process — it would look like
    # a limit and be none — so the limits travel as `--cpus/--memory/--pids-limit` on
    # the container instead, and the capability report says which was applied.
    preexec = None if (profile is not None and profile.backend) else rlimit_preexec(conf)
    t0 = time.time()
    startup_deadline = conf_number(conf, "startup_deadline")
    output_deadline = conf_number(conf, "output_deadline")
    activity_deadline = conf_number(conf, "activity_deadline")
    idle_deadline = conf_number(conf, "idle_deadline")
    useful_bytes = int(echoed_bytes or 0) + BANNER_ALLOWANCE
    stalled_by = None
    cancelled = timed_out = False
    egress_lapsed = None
    with open(log_path, "w", encoding="utf-8") as log:
        # `header` lands BEFORE the child's first byte, and it is written HERE because
        # this is the one place that opens the log — `open(..., "w")` truncates, so a
        # line written to the path beforehand would be erased by the session it is
        # about. The container image-pin waiver travels this way: the console prints it
        # too, but a console scrolls away and the log is the run's record.
        for line in header or ():
            log.write(line.rstrip("\n") + "\n")
        log.flush()
        proc = subprocess.Popen(          # bounded by the wait loop below, not by timeout=
            cmd, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, env=env, text=True, bufsize=1,
            start_new_session=True, preexec_fn=preexec)

        # PHASE 11/12: the two figures no provider can report, measured where the bytes
        # actually arrive. `first_output_s` is the wait for the child's FIRST line after
        # the header — the header is written above by this process, so it is not the
        # child's output and does not count. `idle_s` is the LONGEST gap between
        # consecutive lines, which is what "the session stopped doing anything" looks
        # like from outside a process whose thinking is invisible.
        marks = {"first_output_s": None, "idle_s": None, "lines": 0, "last": t0,
                 "bytes": 0, "first_activity_s": None, "activity_source": None}

        def pump():
            for line in proc.stdout:               # scrubbed BEFORE it is written
                now = time.time()
                if marks["first_output_s"] is None:
                    marks["first_output_s"] = round(now - t0, 3)
                gap = round(now - marks["last"], 3)
                if marks["idle_s"] is None or gap > marks["idle_s"]:
                    marks["idle_s"] = gap
                marks["last"] = now
                marks["lines"] += 1
                marks["bytes"] += len(line)
                log.write(redactor.scrub(line))
                log.flush()

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        deadline = t0 + timeout
        while True:
            try:
                proc.wait(timeout=max(0.2, min(5, deadline - time.time())))
                break
            except subprocess.TimeoutExpired:
                pass
            if lock is not None:
                lock.beat()                        # the lease is alive while we wait
                if lock.cancel_requested():
                    cancelled = True
                    break
            if broker is not None:
                # Phase 10, criterion 5. The boundary is a live process, so it is
                # checked while the session runs and not only at the door. A broker
                # that died leaves the child on an internal network with no route —
                # not an open network, but not the boundary the operator was promised
                # either, and results produced after it lapsed are not results.
                try:
                    broker.assert_alive()
                except Exception as e:                 # EgressError, or docker itself
                    egress_lapsed = str(e)
                    break
            # The four deadlines, checked in the order a session passes them. Each is
            # skipped once the session is past it (a working session must never be
            # re-judged on a bar it already cleared), and 0 disables one.
            #
            # Startup still SUPPRESSES the rest: a session that has not written a byte is
            # that bound's business and nobody else's. Past it, the remaining three are
            # independent checks rather than an `elif` chain. They were a chain until
            # phase 8, and adding a fourth bound to a chain silently disables the fifth —
            # the activity branch matches whenever no activity has been seen yet, which
            # is most of a session's life, so an `elif idle_deadline` behind it would
            # never have been reached and the idle bound would have quietly stopped
            # existing.
            now = time.time()
            if startup_deadline and marks["first_output_s"] is None:
                if now - t0 >= startup_deadline:
                    stalled_by = ("startup", startup_deadline,
                                  "no output at all")
            elif output_deadline and marks["bytes"] <= useful_bytes and not (
                    moved is not None and moved()):
                if now - t0 >= output_deadline:
                    stalled_by = ("output", output_deadline,
                                  f"{marks['bytes']} bytes written, which is no more "
                                  f"than the banner and the prompt echoed back "
                                  f"({useful_bytes}), and no canonical transition")
            if not stalled_by and activity_deadline and (
                    marks["first_activity_s"] is None) and (
                    marks["first_output_s"] is not None or not startup_deadline):
                # The signal the byte count is not. Probed only while no activity has
                # been seen: once a session has acted, it is never re-judged on this bar
                # — a working session that goes on to be slow is the idle deadline's
                # business, and re-probing would cost a `git status` a second.
                seen = activity() if activity is not None else None
                if seen:
                    marks["first_activity_s"] = round(now - t0, 3)
                    marks["activity_source"] = seen
                elif now - t0 >= activity_deadline:
                    stalled_by = ("activity", activity_deadline,
                                  f"{marks['bytes']} bytes written and nothing done: no "
                                  f"canonical transition, no validated telemetry "
                                  f"sidecar, and no write inside its own worktree. "
                                  f"Printing is not working")
            if not stalled_by and idle_deadline and (
                    marks["first_output_s"] is not None or not startup_deadline
                    ) and now - marks["last"] >= idle_deadline:
                stalled_by = ("idle", idle_deadline,
                              f"silent for {round(now - marks['last'])}s")
            if stalled_by:
                break
            if time.time() >= deadline:
                timed_out = True
                break
        if timed_out or cancelled or egress_lapsed or stalled_by:
            why = ("egress broker lost" if egress_lapsed else
                   f"{stalled_by[0]} deadline ({stalled_by[1]}s): {stalled_by[2]}"
                   if stalled_by else "timeout" if timed_out else "cancelled")
            print(f"  ! {why}: terminating the session's whole process group")
            # The SAME kill as every other bound: the group, not the child. A grandchild
            # the stalled agent spawned is exactly the process that would outlive it.
            kill_group(proc, grace)
        reader.join(timeout=5)
        try:
            proc.stdout.close()
        except OSError:
            pass
    rc = proc.returncode if proc.returncode is not None else -9
    # The tail gap counts too: a child that printed a line and then sat silent until the
    # timeout was idle for that whole stretch, and a reader who only saw gaps BETWEEN
    # lines would be told its longest idle period was the short one in the middle.
    if marks["lines"]:
        tail = round(time.time() - marks["last"], 3)
        if marks["idle_s"] is None or tail > marks["idle_s"]:
            marks["idle_s"] = tail
    if timings is not None:
        timings.update({k: v for k, v in marks.items() if k != "last"})
    text = log_path.read_text(encoding="utf-8", errors="replace")
    if egress_lapsed:
        # NOT an outcome. The session did not fail, refuse, or time out — its
        # containment stopped existing, and a run that cannot say what boundary it ran
        # under has nothing to report. `run_session` never sees this, so no patch is
        # applied and no envelope is closed with a verdict.
        raise SystemExit(f"refusing to finish this session: {egress_lapsed}")
    if stalled_by and timings is not None:
        timings["stalled_by"] = stalled_by[0]
        timings["stall_note"] = (f"killed at the {stalled_by[0]} deadline "
                                 f"({stalled_by[1]}s): {stalled_by[2]}")
    return (rc, classify_outcome(rc, timed_out, cancelled, text, stalled_by is not None),
            time.time() - t0)


def _git(cwd, args, check=False):
    p = subprocess.run(["git"] + list(args), cwd=str(cwd), capture_output=True,
                       text=True, timeout=GIT_TIMEOUT)
    if check and p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip()[:400])
    return p


QUARANTINE_DIRNAME = "quarantine"

QUARANTINE_README = """\
# Quarantined session changes — `{outcome}`

A {role} session ended `{outcome}`. Its changes were captured and **not applied** to the
project. This directory is the whole of what that session produced.

Nothing here has touched `audit/state.json`: no finding moved, no pass was recorded, no
lifecycle advanced. As far as the audit's canonical state is concerned this session did
not happen — which is the point, because a session that did not finish did not decide
anything.

    outcome           {outcome}
    role              {role}
    HEAD at capture   {head}
    patch sha256      {digest}

Paths the session changed:

{paths}

## Inspect it

    less {rel}/session.patch
    git apply --stat {rel}/session.patch     # what it would touch
    git apply --check {rel}/session.patch    # whether it still applies cleanly

## Apply it, if you decide the work is sound

    git apply {rel}/session.patch

Read the patch first. A `{outcome}` session stopped part-way through its charter, so its
work may be half of a change rather than a small one — that is exactly why the tool will
not apply it for you.

## Discard it

    rm -r {rel}

## Re-run the charter instead

Only after the project is back where you want it. Re-running a charter on top of this
session's half-finished work runs it against a tree it never saw.
"""


def quarantine_bundles(project):
    """Every pending quarantine bundle, oldest first.

    "Pending" means present: the operator resolves a bundle by applying it and deleting
    it, or by deleting it. There is deliberately no state flag to go stale against the
    directory it describes.
    """
    root = Path(project) / "audit" / QUARANTINE_DIRNAME
    if not root.is_dir():
        return []
    out = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        manifest = d / "manifest.json"
        try:
            rec = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A bundle whose manifest will not parse is still a bundle a human must
            # look at. Reporting it as unreadable beats dropping it from the count.
            rec = {"outcome": "unreadable", "role": "?", "session_id": None}
        out.append(dict(rec, path=str(d.relative_to(Path(project)))))
    return out


class Sandbox:
    """Where the child runs, and how its changes come back.

    The isolating profiles run the session in a disposable `git worktree` created off
    the current HEAD in a system temp dir — never inside the project, because the
    project tree is the shipped deliverable (F-0120). The worktree is then SEEDED with
    the project's uncommitted work and that seed is committed inside the worktree, so
    the session sees exactly the tree it would have seen in place (a human's pending
    triage edits included) and `collect()` can diff against the seed to get the
    session's OWN changes. Those come back as a patch applied to the project, so the
    courier sees what it would have seen and every existing courier guard still applies.

    Fail closed: if the worktree cannot be created, the runner REFUSES. It never
    degrades to running in the project — a sandbox that silently becomes no sandbox is
    worse than one that was never claimed."""

    def __init__(self, project: Path, profile: Profile, role: str, log_dir=None,
                 conf=None):
        self.project = Path(project)
        self.profile = profile
        self.role = role
        self.log_dir = Path(log_dir) if log_dir else self.project / "audit" / "orchestrator-logs"
        # Phase 10 needs the conf at enter() time (the egress broker is
        # established before the worktree). `None` means an empty conf, so every
        # caller that never asked for egress keeps working unchanged.
        self.conf = conf if conf is not None else {}
        self.workdir = self.project
        self.base = None          # the seed commit the session's diff is taken against
        self._tmp = None
        self.out_dir = None       # the container profile's one writable output mount
        self.container_name = None   # set by wrap(), removed by leave()
        # The image-pin waiver, if this run has one. Set by wrap() because that is
        # where the image reference is resolved, and read by the caller to put the
        # sentence into the session log.
        self.image_waiver = None
        # Phase 10: the egress broker, when `egress_allowlist` is set. Started by
        # enter() and stopped by leave() on EVERY path, because the sidecar outlives
        # the client that started it exactly like the audit container does.
        self.broker = None
        self.egress_log = None
        # What `collect()` actually applied, recorded by `collect()` itself so nobody
        # downstream has to re-derive it. Two derivations of "which paths did this
        # session change" is how the authorization bypass survived: the gate read one
        # source and the patch carried another. `RunResult` reads these.
        self.applied_paths = ()
        self.patch_digest = None

    def enter(self):
        # The backend probe comes FIRST — before git, before any temp directory. A
        # profile whose containment is unavailable must refuse having created nothing,
        # and asking git for HEAD first would report the wrong problem on a machine
        # that has neither.
        require_backend(self.profile, self.role)
        self._start_broker()
        if not self.profile.isolating:
            print(f"sandbox: {self.profile.name} — {self.profile.summary}")
            return self.workdir
        head = _git(self.project, ["rev-parse", "HEAD"])
        if head.returncode != 0:
            raise SystemExit(
                f"refusing to launch {self.role}: sandbox_profile={self.profile.name!r} "
                f"needs a git worktree, and {self.project} has no HEAD commit (not a git "
                f"repository, or an empty one). Initialise and commit first, or set "
                f"sandbox_profile=none deliberately to run in the project itself.")
        self._tmp = tempfile.mkdtemp(prefix=f"xcheck-{self.role.lower()}-")
        wt = Path(self._tmp) / "tree"
        self.out_dir = Path(self._tmp) / "out"
        self.out_dir.mkdir(exist_ok=True)
        try:
            self._checkout(wt, head.stdout.strip())
            self._seed(wt)
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            self.leave()
            raise SystemExit(
                f"refusing to launch {self.role}: could not create the disposable "
                f"worktree the {self.profile.name!r} profile requires — {e}. NOT falling "
                f"back to running in the project: an isolation that silently becomes "
                f"none is the failure this profile exists to prevent.")
        self.workdir = wt
        print(f"sandbox: {self.profile.name} at {wt}")
        return wt

    def _checkout(self, wt, head):
        """The disposable tree. A linked `git worktree` for the process-level profiles;
        a self-contained CLONE for a container.

        The difference is not taste. A linked worktree's `.git` is a FILE pointing at
        an absolute path inside the project (`…/.git/worktrees/tree`), which does not
        exist inside the container — every git command in the session would fail, and
        mounting that path back in would re-import the host paths this profile exists
        to remove. A clone carries its own `.git`, so the mount is self-sufficient and
        `git worktree list` inside it can see nothing outside its own mount. `origin`
        is dropped for the same reason: it would name a host path that is not there."""
        if not self.profile.backend:
            _git(self.project, ["worktree", "add", "--detach", str(wt), head], check=True)
            return
        _git(self.project.parent, ["clone", "--no-hardlinks", "--quiet", "--no-checkout",
                                   str(self.project), str(wt)], check=True)
        _git(wt, ["checkout", "--detach", head], check=True)
        _git(wt, ["remote", "remove", "origin"])

    def _start_broker(self):
        """Phase 10. Established BEFORE the worktree, so a run whose egress boundary
        cannot be built refuses having created nothing."""
        from xcheck.egress import Broker, EgressError, allowlist
        conf = self.conf
        try:
            hosts = allowlist(conf)
        except EgressError as e:
            raise SystemExit(f"refusing to launch {self.role}: {e}")
        if not hosts:
            return
        if not self.profile.backend:
            raise SystemExit(
                f"refusing to launch {self.role}: egress_allowlist is set, and only "
                f"sandbox_profile='container' can enforce it. Under "
                f"{self.profile.name!r} the child has the HOST's network — the "
                f"allowlist would name hosts nothing prevents it from leaving, which "
                f"is the word without the thing. Set sandbox_profile=container, or "
                f"clear egress_allowlist and accept that this profile does not bound "
                f"the network.")
        broker = Broker(conf, os.urandom(6).hex())
        try:
            broker.start()
        except EgressError as e:
            raise SystemExit(f"refusing to launch {self.role}: {e}")
        self.broker = broker
        print(f"sandbox: {broker.summary()}")

    def wrap(self, cmd, conf, env=None):
        """The argv actually launched. Unchanged for every profile without a backend —
        this is the one place the container profile changes what runs."""
        if not self.profile.backend:
            return list(cmd)
        _image, self.image_waiver = container_image_ref(conf)   # refuses if unpinned
        self.container_name = f"xcheck-{os.urandom(6).hex()}"
        return container_argv(cmd, conf, self.workdir, self.out_dir, self.project, env,
                              name=self.container_name, broker=self.broker)

    def _seed(self, wt):
        """Copy the project's UNCOMMITTED work into the fresh worktree and commit it.

        Without this the session would read the tree as of HEAD and silently miss the
        operator's pending triage edits — a stale-state no-op, the failure class this
        project trusts least. Tracked modifications travel as a patch; untracked files
        are copied, since no diff carries them.

        `--exclude-standard` is what carries the second copy below. It honours
        `.gitignore`, whose first line is `audit/.lock` — so the owner record the
        orchestrator holds around this very session was the one file the seed could
        never reach. The prompt (see `_orch_suffix`) tells the child to confirm its
        inherited nonce against `audit/.lock/owner` before skipping acquisition, and
        F-0093 makes a missing owner a fail-closed stop. Two locally-correct decisions
        met on one process and made that gate unsatisfiable: measured on Ouroboros-4,
        four of five sessions refused to start, and the fifth reported a match it had
        no file to match against. Copy the owner record in by name. It stays ignored
        inside the worktree too, so `git add -A` below never commits it and `capture()`
        never sees it as the session's work — the record travels IN as readable
        evidence and does not travel back OUT as a claim on a lock."""
        diff = _git(self.project, ["diff", "HEAD", "--binary"], check=True).stdout
        if diff.strip():
            patch = Path(self._tmp) / "seed.patch"
            patch.write_text(diff, encoding="utf-8")
            _git(wt, ["apply", "--whitespace=nowarn", str(patch)], check=True)
        others = _git(self.project, ["ls-files", "--others", "--exclude-standard"],
                      check=True).stdout.split("\n")
        for rel in filter(None, (o.strip() for o in others)):
            src = self.project / rel
            if src.is_file():
                dst = wt / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        owner = self.project / "audit" / ".lock" / "owner"
        if owner.is_file():
            dst = wt / "audit" / ".lock" / "owner"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(owner, dst)
        _git(wt, ["add", "-A"], check=True)
        _git(wt, ["-c", "user.name=xcheck", "-c", "user.email=xcheck@localhost",
                  "commit", "--allow-empty", "--no-verify", "-q", "-m",
                  f"xcheck sandbox seed ({self.role})"], check=True)
        self.base = _git(wt, ["rev-parse", "HEAD"], check=True).stdout.strip()

    def capture(self):
        """The session's own work as `(patch_text, paths)`, or None if it changed nothing.

        ONE derivation, two destinations: `collect()` applies it, `quarantine()` files it
        for a human. A second capture written for the quarantine path would be a second
        answer to "what did this session change" — the exact shape of the authorization
        bug this module already carries a scar from (see `changed_paths`).
        """
        if not self.profile.isolating:
            return None
        _git(self.workdir, ["add", "-A"])
        patch = _git(self.workdir, ["diff", "--binary", "--cached", self.base]).stdout
        if not patch.strip():
            return None
        # Ask git which paths changed. Do NOT re-derive them from `patch` — see
        # `changed_paths`. Same `--cached <base>`, so the authorization input and
        # the patch cannot describe two different sets of changes.
        return patch, tuple(changed_paths(self.workdir, self.base))

    def quarantine(self, outcome, session_id=None, head_at_capture=None, charter=None):
        """File a non-`ok` session's work where a human can find it. Nothing is applied.

        The bundle lives under `audit/quarantine/` rather than a temp dir, because a
        quarantine that evaporates is a deletion with extra steps, and rather than the
        project source, because it is audit evidence and the courier ships it as such.

        Written ONCE and never rewritten: an amended quarantine record is amended
        evidence.
        """
        cap = self.capture()
        if cap is None:
            print(f"quarantine: the {outcome} session changed nothing — "
                  f"there is nothing to quarantine.")
            return None
        patch, paths = cap
        digest = hashlib.sha256(patch.encode("utf-8")).hexdigest()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bundle = (self.project / "audit" / QUARANTINE_DIRNAME /
                  f"{stamp}-{self.role.lower()}-{outcome}")
        bundle.mkdir(parents=True, exist_ok=True)
        (bundle / "session.patch").write_text(patch, encoding="utf-8")
        manifest = {
            "outcome": outcome,
            "role": self.role,
            "charter": charter or "",
            "session_id": session_id,
            "patch_digest": digest,
            "head_at_capture": head_at_capture,
            "paths": list(paths),
            "captured_at": stamp,
            "sandbox_profile": self.profile.name,
            "applied": False,
        }
        (bundle / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (bundle / "README.md").write_text(QUARANTINE_README.format(
            outcome=outcome, role=self.role, digest=digest,
            head=head_at_capture or "(unknown)",
            paths="\n".join(f"  - {p}" for p in paths) or "  (none)",
            rel=bundle.relative_to(self.project)), encoding="utf-8")
        print(f"quarantine: the {self.role} session ended `{outcome}` — its changes were "
              f"NOT applied to the project.")
        print(f"  bundle: {bundle.relative_to(self.project)} "
              f"({len(paths)} path(s), sha256 {digest[:12]}…)")
        print(f"  read {bundle.relative_to(self.project)}/README.md to inspect it, or "
              f"apply it yourself with `git apply`.")
        return bundle

    def collect(self):
        """Bring the session's changes back into the project. Returns the patch path
        when something was applied, None when the session changed nothing."""
        cap = self.capture()
        if cap is None:
            if self.profile.isolating:
                print("sandbox: the session changed nothing")
            return None
        patch, paths = cap
        # PHASE 9 (fourth audit / F-0104). BEFORE the patch reaches the main checkout: a
        # session may append to the event stream and may not rewrite a byte of it. Here
        # rather than after the apply, because a rejected stream that lands and is then
        # reverted has already happened — and reverting evidence is itself an event.
        problem = self._event_prefix_problem()
        if problem:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            kept = self.log_dir / f"{stamp}-{self.role.lower()}-refused.patch"
            kept.write_text(patch, encoding="utf-8")
            raise SystemExit(f"{problem}\n    the session's patch is kept at {kept}")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        patch_path = self.log_dir / f"{stamp}-{self.role.lower()}.patch"
        patch_path.write_text(patch, encoding="utf-8")
        if not self.profile.material_writable:
            outside = [f for f in paths if not f.startswith("audit/")]
            if outside:
                raise SystemExit(
                    f"refusing to apply the {self.role} session's changes: "
                    f"sandbox_profile={self.profile.name!r} does not let this role change "
                    f"the material, but the session touched {', '.join(outside[:5])}"
                    f"{' …' if len(outside) > 5 else ''}. Nothing was applied to the "
                    f"project; the full patch is kept at {patch_path} so the work is not "
                    f"lost — review it, then apply it yourself or rerun the role under a "
                    f"profile that permits it.")
        applied = _git(self.project, ["apply", "--whitespace=nowarn", str(patch_path)])
        if applied.returncode != 0:
            raise SystemExit(
                f"the {self.role} session's changes could not be applied to the project: "
                f"{(applied.stderr or applied.stdout).strip()[:300]}. The patch is kept "
                f"at {patch_path} — the usual cause is work in the project that changed "
                f"under the session. Nothing was applied.")
        print(f"sandbox: applied the session's changes from {patch_path.name}")
        # Set only AFTER the apply succeeded: "applied_paths" has to mean applied, or a
        # refused patch would be indistinguishable from a landed one in the record.
        self.applied_paths = paths
        self.patch_digest = hashlib.sha256(patch.encode("utf-8")).hexdigest()
        return patch_path

    def _event_prefix_problem(self):
        """The session's `audit/events.jsonl` against the one it was seeded with.

        Against the SEED commit, not against the project as it stands: the orchestrator
        appends its own events to the project while the session runs, so the project is
        legitimately ahead. What the session is answerable for is the stream it was
        given.
        """
        rel = f"audit/{ledger.EVENTS_FILENAME}"
        seeded = subprocess.run(["git", "-C", str(self.workdir), "show",
                                 f"{self.base}:{rel}"],
                                capture_output=True, timeout=GIT_TIMEOUT)
        before = seeded.stdout if seeded.returncode == 0 else b""
        path = Path(self.workdir) / "audit" / ledger.EVENTS_FILENAME
        after = path.read_bytes() if path.is_file() else b""
        return ledger.prefix_problem(before, after, rel, f"the {self.role} session")

    def leave(self):
        """Remove the worktree — and the container — on BOTH the success and the
        failure path.

        The container removal comes first and is unconditional: a killed `docker run`
        client leaves its container running, so this is the only place that ends it."""
        if self.container_name:
            subprocess.run(["docker", "rm", "-f", self.container_name],
                           capture_output=True, timeout=60)
            self.container_name = None
        if self.broker is not None:
            # The log is READ before the sidecar is removed, because removing it takes
            # its bind-mounted temp dir with it. An egress record that the teardown
            # deletes is the phase-8 failure wearing a different hat.
            self.egress_log = self.broker.stop()
            self.broker = None
        if self._tmp is None:
            return
        wt = Path(self._tmp) / "tree"
        if wt.exists() and not self.profile.backend:
            _git(self.project, ["worktree", "remove", "--force", str(wt)])
        shutil.rmtree(self._tmp, ignore_errors=True)
        if not self.profile.backend:
            _git(self.project, ["worktree", "prune"])   # a clone was never registered
        self._tmp = None
        self.out_dir = None
        self.workdir = self.project


def changed_paths(workdir, base):
    """The repo-relative paths the STAGED tree changes against `base`, asked of git.

    This replaces reading them out of the patch text, which is what the courier's
    read-only gate did until 0.9.0. A `diff --git` header is a HUMAN-readable line,
    not a data format: git quotes any path containing non-ASCII or control bytes, so
    a file named `é.txt` arrives as `diff --git "a/é.txt" "b/é.txt"` and a parser
    partitioning on the literal `" b/"` returns NOTHING. An empty path set read as
    "this session touched no material", and a read-only Auditor's change to the
    project was applied. The same parser also mis-read any unquoted name containing
    `" b/"` — `diff --git a/x b/y b/x b/y` yielded `y b/x b/y`.

    `-z` is the machine format: NUL-delimited, never quoted, no escaping to undo and
    therefore no unescaping to get wrong.

    `--no-renames` is load-bearing, not tidiness. With rename detection on, moving
    `material.c` to `audit/material.c` reports ONLY the destination; the gate would
    see a path under `audit/` and approve a change that deleted a material file. With
    detection off the move is a delete plus an add, and the delete is refused.

    Run in BYTES. `_git` uses `text=True`, whose universal-newline translation would
    rewrite a `\\r` inside a filename; `surrogateescape` then keeps a name that is not
    valid UTF-8 round-trippable, so it can still be compared and named in the refusal.
    """
    p = subprocess.run(
        ["git", "diff", "--name-only", "-z", "--no-renames", "--cached", base],
        cwd=str(workdir), capture_output=True, timeout=GIT_TIMEOUT)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", "replace").strip()[:400])
    # `-z` terminates every record, so the split's last element is always empty.
    return [b.decode("utf-8", "surrogateescape") for b in p.stdout.split(b"\0") if b]


# ---------------------------------------------------------------- sessions


def _telemetry_adapter(conf):
    """The adapter the operator declared, or None. An unknown name REFUSES.

    Resolved from the profile and never from the command string, per phase 11: a role
    command reading `codex exec …` may be a wrapper, a proxy, a shim or a different build
    of the same name, and concluding a provider from a program name is deciding something
    the operator did not say. Nothing here executes the provider to ask what it is —
    `provenance-must-not-interview-the-subject`, which is F-0098.
    """
    adapter, problem = provider.adapter_for(conf)
    if problem:
        raise SystemExit(f"xcheck: {problem}")
    return adapter


def build_cmd(conf, role, prompt, sidecar=None, route=None):
    """The argv for one dispatch. See `{sidecar}` and `{token_cap}` below."""
    tmpl = conf.get(f"{role.lower()}_cmd")
    if not tmpl:
        # Since the policy split this is almost always a missing operator profile, not a
        # missing line: role commands may not live in the subject, so a project that was
        # only ever `install.sh`-ed has nowhere for them to come from. Saying
        # "orchestrator.conf lacks auditor_cmd" would send the operator to edit the one
        # file where writing it is refused.
        from xcheck.policy import (OPERATOR_PROFILE_ENV, XDG_CONFIG_ENV,
                                   operator_profile_path)
        where = operator_profile_path()
        raise SystemExit(
            f"no command configured for the {role} role: {role.lower()}_cmd is unset.\n"
            f"Role commands are OPERATOR policy — they say what runs on this machine — "
            f"so they are read from the operator profile, outside the repository being "
            f"audited, and are refused inside it.\n"
            + (f"Add `{role.lower()}_cmd=<your agent CLI> {{prompt}}` to {where}.\n"
               if where is not None else
               f"This machine names no profile: set {OPERATOR_PROFILE_ENV} to a path "
               f"(or {XDG_CONFIG_ENV}, and use $({XDG_CONFIG_ENV})/xcheck/operator.conf), "
               f"then put `{role.lower()}_cmd=<your agent CLI> {{prompt}}` in it.\n"))
    parts = shlex.split(tmpl)
    # PHASE 6: `{sidecar}` is how an operator's wrapper is TOLD where to write the
    # provider's usage object. Deliberately a placeholder in the command and NOT an
    # environment variable: `child_environment` copies `XCHECK_*` wholesale, so an env
    # var would hand the path to the agent as well, and the whole point is that the file
    # the parent believes is one the subject was never handed. A template with no
    # `{sidecar}` gets nothing, and provider telemetry for that role is simply
    # unmeasured — which is the honest answer, not zero.
    parts = [p.replace("{sidecar}", str(sidecar)) for p in parts] if sidecar else [
        p for p in parts if "{sidecar}" not in p or True]
    # PHASE 12 (fourth audit): `{token_cap}` carries the HARD per-dispatch ceiling into
    # the invocation, before the model runs. A ceiling compared against a measured mean
    # after the fact is a forecast — the audit's exact complaint — and this is the only
    # form of it that stops a session rather than reporting on one.
    #
    # xcheck supplies the NUMBER and never the flag: which option a CLI takes its output
    # limit under is a fact about the operator's provider, and a tool that wrote
    # `--max-tokens` itself would be choosing a provider it was not told about. The
    # operator writes the flag; `budget.cap_problem` refuses a ceiling with nowhere to go.
    cap = budget.dispatch_cap(conf) if budget.budget_enabled(conf) else 0
    if cap:
        parts = [p.replace(budget.CAP_PLACEHOLDER, str(cap)) for p in parts]
    # PHASE 13 (fourth audit): the route reaches the provider, or it is not a route.
    # With `model_routing` on and off this function produced byte-identical argv — the
    # route was computed, recorded and discarded, so every metric aggregating by it
    # described a world that did not happen. `routing.routing_problem` refuses a route
    # that cannot change anything; here the model it resolved to is substituted.
    if route is not None and routing.routing_enabled(conf):
        model = routing.model_for(conf, route)
        if model:
            parts = [p.replace(routing.MODEL_PLACEHOLDER, model) for p in parts]
    # An UNFILLED placeholder is refused rather than passed through. `--model {model}`
    # reaching a provider verbatim is a request for a model literally named `{model}`:
    # the CLI rejects it, the session fails for a reason nothing in the log explains, and
    # the operator's actual mistake — routing off, or no model assigned to the route — is
    # invisible. Named here for the two placeholders that carry a policy DECISION;
    # `{sidecar}` is optional by design and its absence means telemetry is unmeasured.
    for placeholder, why in ((routing.MODEL_PLACEHOLDER,
                              f"`{routing.FLAG}` is off or this route has no model "
                              f"assigned"),
                             (budget.CAP_PLACEHOLDER,
                              f"`{budget.FLAG}` is off or `tokens_per_session` is unset")):
        if any(placeholder in p for p in parts):
            raise SystemExit(
                f"{role.lower()}_cmd carries `{placeholder}` and nothing filled it: "
                f"{why}. The provider would be handed the placeholder verbatim. Remove "
                f"it from the command, or configure what fills it.")
    # F-0092: {prompt} substitution must work for an EMBEDDED placeholder
    # (`--prompt={prompt}`), not only a standalone argv token. `"{prompt}" in
    # parts` tested LIST MEMBERSHIP (exact token equality), so `--prompt={prompt}`
    # matched nothing: the literal placeholder was kept AND the prompt was
    # appended as a stray positional arg — silently breaking the sole agent-CLI
    # adaptation boundary for any CLI taking a `--flag=<value>` form (§3
    # agent-independence). Substitute inside every token that contains the
    # placeholder; a standalone `{prompt}` token still becomes exactly the prompt.
    if any("{prompt}" in p for p in parts):
        return [p.replace("{prompt}", prompt) for p in parts]
    return parts + [prompt]


def prompt_charter_slot_issues(prompts=None):
    """Templates that would SILENTLY DISCARD a charter handed to them.

    `str.format(charter=...)` on a template with no `{charter}` placeholder is not an
    error — it returns the template unchanged. So a dispatch can hand a role its charter
    and have the charter vanish, with the agent receiving only the template's own
    hard-coded orders. That is exactly how the §4 rule 9 gate became a no-op for the
    Planner on its first live use: the construal instruction was passed and dropped.

    Rule, derived from what `role_and_charter` actually returns: EVERY dispatched role gets
    a charter, so every role template must have a `{charter}` slot, and the construal
    template must have both `{role}` and `{charter}`. The Planner used to be the exception —
    its charter was prose baked into its own prompt — and that exception is precisely what
    let the construal instruction be discarded, so there is no exception any more."""
    prompts = PROMPTS if prompts is None else prompts
    issues = []
    if "{charter}" not in prompts.get("Construal", ""):
        issues.append("PROMPTS['Construal'] has no `{charter}` slot — the construal instruction would be discarded (§4 rule 9)")
    if "{role}" not in prompts.get("Construal", ""):
        issues.append("PROMPTS['Construal'] has no `{role}` slot — the session would not know which role it is construing for")
    for role in ("Planner", "Auditor", "Remediator", "Verifier"):
        if "{charter}" not in prompts.get(role, ""):
            issues.append(f"PROMPTS[{role!r}] receives a charter from role_and_charter but has no `{{charter}}` slot — it would be silently discarded")
    return issues


def _checked_protocol(value):
    """`value` if it is a declared protocol answer (or None), else a refusal."""
    from xcheck.envelope import PROTOCOLS
    if value is not None and value not in PROTOCOLS:
        raise SystemExit(
            f"internal: {value!r} is not a declared protocol answer "
            f"(known: {', '.join(sorted(PROTOCOLS))}). A session's protocol reaches the "
            f"loop, the printer and the event stream; an unrecognised one would be "
            f"treated as 'not no-progress', which is the permissive direction.")
    return value


class RunResult(int):
    """What a session DID, carried by the exit code it already returned.

    0.9.1 (audit item P1 #8). Before this, the only thing a session handed back was an
    integer, so every caller that needed to know whether anything had happened inferred
    it after the fact — the retry gate by re-reading the canonical bytes of
    `state.json`, which is blind to source files, audit artifacts and git HEAD. That
    blindness is not a bug in the comparison; it is a consequence of asking a FILE what
    a SESSION did. The session has to say.

    It subclasses `int` deliberately. The exit code is not an incidental part of this
    value, it is the value — `rc == 0`, `if rc != 0`, `return rc` and `sys.exit(rc)` are
    the contract every caller already has, and a record that broke them would convert a
    successful session into a silent halt. Subclassing means no caller can be wrong
    about it, rather than every caller having to be checked.

    Immutable, like every other record in this codebase: a blocked `__setattr__`, and
    `int` itself does the rest. (`__slots__` is not available here — CPython refuses a
    non-empty `__slots__` on a subtype of a variable-length built-in — so the fields are
    written once through `object.__setattr__` and the door is shut behind them.)
    """

    FIELDS = ("outcome", "protocol", "transitions", "applied_paths", "patch_digest",
              "state_revision_before", "state_revision_after", "head_before",
              "head_after", "effects_applied", "attempt", "session_id",
              "quarantine_path", "material_paths")

    def __new__(cls, code, outcome, *, applied_paths=(), patch_digest=None,
                state_revision_before=None, state_revision_after=None,
                head_before=None, head_after=None, effects_applied=False,
                attempt=0, session_id=None, quarantine_path=None, material_paths=(),
                protocol=None, transitions=()):
        self = super().__new__(cls, code)
        for name, value in (("outcome", outcome),
                            # TWO answers, never one. `outcome` says how the CHILD
                            # PROCESS ended; `protocol` says whether the ROLE did its
                            # job. Collapsing them is the defect: `classify_outcome`
                            # returns `ok` for any exit-zero child, so 136 of the 151
                            # Ouroboros-4 sessions came back `ok` having moved nothing.
                            # Enumerated in `envelope.PROTOCOLS`, and CHECKED here: an
                            # unknown value in this field reaches the loop, the printer
                            # and the event stream, none of which have a branch for it,
                            # and would read as "not no-progress" — the permissive
                            # direction.
                            ("protocol", _checked_protocol(protocol)),
                            ("transitions", tuple(transitions)),
                            ("applied_paths", tuple(applied_paths)),
                            ("patch_digest", patch_digest),
                            ("state_revision_before", state_revision_before),
                            ("state_revision_after", state_revision_after),
                            ("head_before", head_before),
                            ("head_after", head_after),
                            ("effects_applied", bool(effects_applied)),
                            ("attempt", attempt),
                            ("session_id", session_id),
                            # Where this session's UNAPPLIED work went (phase 3). The
                            # journal is how an operator months later answers "the
                            # session crashed — where did its changes go?" without
                            # having the console output that said so at the time.
                            ("quarantine_path", quarantine_path),
                            # The receipts behind `effects_applied`. A gate that blocks
                            # a retry has to be able to say WHICH file blocked it.
                            ("material_paths", tuple(material_paths))):
            object.__setattr__(self, name, value)
        return self

    def __setattr__(self, name, value):
        raise AttributeError(
            f"RunResult is immutable: {name!r} cannot be rebound. A session's account of "
            f"what it did is evidence, and evidence a consumer can edit is testimony.")

    def __delattr__(self, name):
        raise AttributeError("RunResult is immutable")

    def record(self):
        """The serialisable form — what goes in the event log beside the session."""
        return dict({name: getattr(self, name) for name in self.FIELDS},
                    code=int(self))

    def __repr__(self):
        return (f"RunResult(code={int(self)}, outcome={self.outcome!r}, "
                f"protocol={self.protocol!r}, moved={len(self.transitions)}, "
                f"effects_applied={self.effects_applied}, "
                f"applied_paths={len(self.applied_paths)}, "
                f"head {self.head_before and self.head_before[:7]}"
                f"->{self.head_after and self.head_after[:7]}, "
                f"revision {self.state_revision_before}->{self.state_revision_after})")


# What the ORCHESTRATOR writes on the session's behalf, on every single run: the log,
# the collected patch beside it, and the session's own row in `state.json`. None of it is
# the session's effect on the project, and all of it moves HEAD, because the courier
# commits it. Naming the bookkeeping is what makes "did this session change anything?"
# answerable at all — without this list the answer is `True` for every session ever run,
# which is a gate that has stopped asking.
BOOKKEEPING_PREFIXES = (
    "audit/orchestrator-logs/",     # the session log, and the collected patch beside it
    # PHASE 13: the context capsule, written BY THE ORCHESTRATOR before the child starts.
    # It is the brief the session was handed, never something the session did — and
    # forgetting it here made `effects_applied` True for every dispatch, including a
    # cancelled one that applied nothing. Caught by the cancelled-session arm, which is
    # what that arm is for.
    "audit/capsules/",
)
BOOKKEEPING_FILES = (
    "audit/state.json",             # asked separately, below — it holds BOTH kinds
    "audit/events.jsonl",           # the envelope journal: an append per dispatch/finish
    # PHASE 14: the log manifest. It gains a row per session for a file the ORCHESTRATOR
    # wrote outside the tree, so like the log itself it is bookkeeping — and like the
    # capsule in phase 13, forgetting it made every session look as though it had changed
    # the project. Two phases, the same lesson, the same two arms catching it.
    "audit/logs-manifest.jsonl",
)


def _material_paths(project, head_before, head_after, pre_dirty):
    """`(material, compared)` — the paths this session moved, and how many it looked at.

    Two sources, because a change can land in either: paths carried by the courier's
    commit, and paths still dirty now that were not dirty when the session started. The
    orchestrator's own bookkeeping is then subtracted by name.

    `compared` is returned because a check that scans nothing is green for free. The
    caller prints it and a test asserts a floor: without that number, a probe that lost
    its subject — a bad `head_before`, an empty status, the wrong project — reports "no
    material effects" in exactly the voice it uses when it looked and found none.

    `audit/state.json` is excluded HERE and asked separately by the caller, because it
    holds both kinds of change: the session's findings (material) and the session row the
    orchestrator appended (not). `_material_bytes` is the existing answer to that split.
    """
    changed, now = set(), set(dirty_paths(project) or ())
    if head_before and head_after and head_before != head_after:
        diff = _git(project, ["diff", "--name-only", head_before, head_after])
        changed |= {ln.strip() for ln in diff.stdout.splitlines() if ln.strip()}
    # NEWLY dirty only. A path that was already dirty before the session and is dirty
    # still was not moved by it; a symmetric difference would call the courier's commit
    # of PRE-EXISTING dirt this session's effect.
    changed |= now - set(pre_dirty or ())
    material = sorted(c for c in changed
                      if c not in BOOKKEEPING_FILES
                      and not c.startswith(BOOKKEEPING_PREFIXES))
    return material, len(changed | now | set(pre_dirty or ()))


def material_snapshot(project):
    """The retry gate's BEFORE picture — the three things a session can move.

    Handed back opaque and taken in ONE place, because the before-half and the
    after-half of a comparison drifting apart is how a gate comes to compare two
    different questions. `material_effect_probe` is the only reader.

    The third element is the canonical half of `state.json` — `sessions` and
    `state_revision` stripped — which is what 0.9.0 compared. Keeping it is the point:
    0.9.0's answer was too NARROW, not wrong. A session that filed a finding and then
    hit a provider error moved canonical state and nothing else, and dropping this
    element to make room for the other three would have traded one blind spot for
    another.
    """
    state_path = Path(project) / "audit" / "state.json"
    return (dirty_paths(project), git_head(project),
            _material_bytes(state_path) if state_path.exists() else None)


def material_effect_probe(project, snapshot):
    """The RETRY GATE's own answer to "did this session change anything?".

    Deliberately a second answer to the question `RunResult.material_paths` already
    carries, and deliberately not the same inputs: the record is what the session says
    about itself, computed at the session boundary; this is what the project says,
    computed from a snapshot the gate took before dispatching. The two are AND-ed and
    both fail CLOSED, so this is defence in depth rather than a split authority — the
    failure mode that costs you is two derivations where one authorises and the other
    merely describes, and neither of these authorises anything.

    The concrete hole it closes: `getattr(rc, "effects_applied", False)` defaults to
    False for any caller that hands back a plain int. A record that never arrives cannot
    block a retry; the project's own state can.
    """
    pre_dirty, head_before, pre_state = snapshot
    material, compared = _material_paths(project, head_before, git_head(project),
                                         pre_dirty)
    # The fourth surface, kept from 0.9.0 rather than replaced by the other three:
    # `state.json` is excluded from `_material_paths` because it carries the session row
    # the orchestrator appends, but its CANONICAL half is the session's own work.
    state_path = Path(project) / "audit" / "state.json"
    if (_material_bytes(state_path) if state_path.exists() else None) != pre_state:
        material = sorted(set(material) | {"audit/state.json"})
    return material, compared


def _charter_slice_now(project, charter):
    """This charter's own state slice at dispatch, or None when there is nothing to
    slice.

    Recorded in the stream beside `source_digest` because a replay cannot recompute a
    historical slice: present-day state answers a different question than the one the
    session was dispatched under. None is a real answer — a charter that names no pass
    (a Remediator's finding list) has no queue entry to slice, and the embargo's
    fail-closed rule already treats an absent component as matching.
    """
    from xcheck.decision import charter_slice
    from xcheck.policy import profile_digest, resolve as resolve_policy
    passes = [t for t in envelope.charter_targets(charter) if t.startswith("P-")]
    if not passes:
        return None
    try:
        state = load_state(Path(project) / "audit")
    except StateError:
        return None
    profile = resolve_policy()
    return charter_slice(state, passes[0],
                         profile_digest(profile) if profile else None)


def _session_result(project, code, outcome, *, sandbox, pre_dirty, pre_state, rev_before,
                    head_before, attempt, session_id, quarantined=None,
                    role=None, charter=None):
    """Close the session's account: the AFTER half, and the one derived field.

    `effects_applied` is what the retry gate turns on, so it is computed over the surface
    a human would check — the courier's own applied path set, every path that moved in
    the commit or is still dirty, and the material half of `state.json`. The canonical
    bytes of `state.json` ALONE were the reproduced defect: they are blind to a changed
    source file, to an audit artifact and to a commit.
    """
    head_after = git_head(project)
    applied = tuple(getattr(sandbox, "applied_paths", ()) or ())
    moved, _compared = _material_paths(project, head_before, head_after, pre_dirty)
    state_path = Path(project) / "audit" / "state.json"
    state_moved = (_material_bytes(state_path) if state_path.exists() else None) != pre_state
    # ONE list, and the boolean derived from it. `effects_applied` used to be computed
    # independently, which meant a gate could be told "something changed" and have
    # nothing to tell the operator about WHAT. A refusal that cannot name the file is a
    # refusal the operator has to reproduce by hand before they can act on it.
    material = sorted(set(applied) | set(moved)
                      | ({"audit/state.json"} if state_moved else set()))
    # The receipt, read AFTER the courier commit — by now the session's own transitions
    # have travelled back from the sandbox into the project's stream, so this asks the
    # evidence rather than the session. Emitted as its own event because `emit` takes a
    # free-form payload and the stream is already excluded from the progress digest: no
    # schema change, and no new file for that digest to mistake for work.
    # Bound to the ROLE and the CHARTER this session was dispatched with. Without them
    # the receipt can only say "something moved", which is what let any permitted
    # command buy a session the status `recorded`. The charter TEXT is what the
    # orchestrator handed the child, and it is in hand here — `charter_hash` in the
    # envelope is a digest and cannot be reversed into a list of ids.
    receipt = envelope.session_receipt(project, session_id, role=role, charter=charter)
    envelope.emit(project, "session_receipt", session_id, role=role,
                  protocol=receipt["protocol"], transitions=receipt["transitions"],
                  undeclared=receipt["undeclared"],
                  charter_targets=receipt["charter_targets"],
                  postcondition_met=receipt["postcondition_met"])
    if receipt["undeclared"]:
        minted = ", ".join(sorted({r["verb"] for r in receipt["undeclared"]}))
        print(f"receipt: this session wrote transition(s) under verb(s) the orchestrator "
              f"does not declare ({minted}) — recorded, but not counted as progress")
    return RunResult(
        code, outcome,
        protocol=receipt["protocol"],
        transitions=tuple(r["verb"] for r in receipt["transitions"]),
        applied_paths=applied,
        patch_digest=getattr(sandbox, "patch_digest", None),
        state_revision_before=rev_before,
        state_revision_after=envelope.current_revision(project),
        head_before=head_before, head_after=head_after,
        material_paths=tuple(material),
        effects_applied=bool(material),
        attempt=attempt, session_id=session_id,
        quarantine_path=(str(Path(quarantined).relative_to(project))
                         if quarantined else None))


def capsule_line(session_id):
    """The sentence that replaced 84,610 bytes of mandatory reading.

    It names the capsule AND says it is a subset, in the same breath. A brief that does
    not admit its own limits is how a session confidently audits half a charter."""
    return (f" Your context capsule is `audit/capsules/{session_id}.md` — read it FIRST. "
            f"It carries this charter's allowed verbs, the norms it is judged against, "
            f"the material with the hash it had at dispatch, the findings already filed "
            f"against it, the stop conditions and the postcondition this session must "
            f"reach. It is a SUBSET, not a replacement: when it does not answer a "
            f"question your charter raises, the full methodology is at `audit/XCHECK.md` "
            f"and the plan at `audit/AUDIT.md`.")



def _route_for_dispatch(project, conf, role, charter):
    """The route for one dispatch, or None when `model_routing` is off.

    The inputs are DATA read off the state: the dimension of the passes the charter
    names, and the severities of the findings it names. No model is consulted — the
    router is a dict lookup, and a router that asked a model which model to use would
    have bought the expensive call it exists to avoid.

    A state that cannot be read leaves both inputs empty, which routes on the role
    alone. That is the fail-closed direction: an unclassified pass takes the STRONG
    route, so a broken read costs money rather than a security verdict.
    """
    if not routing.routing_enabled(conf):
        return None
    dimension, severities = None, []
    try:
        st = load_state(Path(project) / "audit")
        pass_ids = set(re.findall(r"\bP-\d{2,}\b", charter or ""))
        for q in st.queue:
            if q.id in pass_ids and dimension is None:
                dimension = q.dimension
        finding_ids = set(re.findall(r"\b(?:F|CF)-\d{4}\b", charter or ""))
        severities = [f.severity for f in st.findings if f.id in finding_ids]
    except (OSError, ValueError, StateError):
        pass
    return routing.route_of_dispatch(conf, role=role, dimension=dimension,
                                     severities=severities,
                                     where=charter or role)


#: One dispatch's launch preparation, as data. Both dispatchers build this and neither
#: builds a command any other way — see `prepare_launch`.
LaunchPlan = collections.namedtuple("LaunchPlan", "cmd sidecar_path")


def dispatch_route(project, conf, role, charter):
    """The route and the model it plans, resolved once, for either dispatcher.

    PHASE 5 (sixth audit). The parallel path resolved no route at all, so on a
    configuration with `model_routing=on` and `{model}` in the role command the two
    dispatchers gave different answers: the sequential run launched a child with
    `--model strong-1`, the parallel run refused on an unfilled placeholder and started
    nothing. Two implementations of one guarantee is what this phase removes.
    """
    route = _route_for_dispatch(project, conf, role, charter)
    bad_route = routing.routing_problem(conf, route, conf.get(f"{role.lower()}_cmd"))
    if bad_route:
        raise SystemExit(f"xcheck: {bad_route}")
    return route, (routing.model_for(conf, route) if route else None)


def prepare_logs(project, conf, stem, dry_run=False):
    """The log directory and this session's log file, for either dispatcher.

    PHASE 5 (sixth audit): the parallel path wrote to a hardcoded
    `audit/orchestrator-logs` while the sequential path asked `retention.log_root`,
    which is where the OPERATOR says logs go — and which refuses a path inside the
    project. A second dispatcher choosing its own directory is a second policy.

    `create` is the one thing a dry run changes: it needs the PATH (the sidecar is
    derived from it and the sidecar reaches the command) and must not make the
    directory.
    """
    logs = retention.log_root(project, conf, create=not dry_run)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return logs, logs / f"{stamp}-{stem}.log"


def prepare_launch(conf, role, child_prompt, session_id, profile, log_path, route):
    """THE build: one per dispatch, with the sidecar and the route.

    Both dispatchers call this, which is the point. The fourth audit's routing defect
    was a SECOND build in this module that took the sidecar and dropped the route; the
    sixth audit's was a second build in `parallel.py` that took NEITHER. One function
    with both inputs is the only shape in which "the route reaches the provider" is a
    property of xcheck rather than of one code path.
    """
    # PHASE 6: the sidecar reaches the role command only through `{sidecar}`, never
    # through the environment — `child_environment` copies `XCHECK_*` wholesale, so an
    # env var would hand the agent the very file it must not be able to write.
    sidecar_path = envelope.sidecar_path(log_path, session_id)
    cmd = build_cmd(conf, role, child_prompt, sidecar=sidecar_path, route=route)
    refuse_uncontained(cmd, profile, role)
    return LaunchPlan(cmd=cmd, sidecar_path=sidecar_path)


def finish_session(conf, rec, rc, outcome, elapsed, head_after, log_path, *,
                   planned_model=None, route=None, **extra):
    """THE telemetry finish, for either dispatcher.

    PHASE 5 (sixth audit). The parallel path called `envelope.finish` with none of
    these: no adapter, so the provider's usage object was never parsed and every
    parallel session's cost was `agent-reported` at best; no planned model and no route,
    so a routed pass's envelope said nothing about the route it ran under and every
    metric aggregating by route silently excluded half the corpus.

    The adapter is resolved HERE, from the operator's declaration, so a caller cannot
    supply one — parsing one vendor's numbers with another's key names produces figures
    that are wrong in silence.

    A route the provider contradicts makes `envelope.finish` raise, and the session is
    then finished WITHOUT the route: the work may be real, the route claim about it is
    not, and a record that kept the label is the number a cost comparison rests on. That
    handling used to live in `run_session` alone, which is why the same mismatch on the
    parallel path escaped as an unhandled `RouteError` and lost the whole envelope —
    including the finding that a session had run at all.
    """
    try:
        return envelope.finish(rec, rc, outcome, elapsed, head_after, log_path,
                               adapter=_telemetry_adapter(conf),
                               planned_model=planned_model, route=route, **extra)
    except routing.RouteError as e:
        # PHASE 2 (fifth audit): `refused`, not `protocol-violation`. The latter is a
        # PROTOCOL answer (`envelope.PROTOCOLS`), not a session OUTCOME
        # (`util.OUTCOMES`), so this branch wrote a value the state schema refuses and
        # the whole session record was lost to a StateError.
        print(f"route: REFUSED — {e}")
        extra.pop("note", None)
        return envelope.finish(rec, rc, "refused", elapsed, head_after, log_path,
                               adapter=_telemetry_adapter(conf),
                               note=f"route attestation refused: {e}", **extra)


def result_problem(conf, log_path, session_id, planned_model, route):
    """Why this session's result may not be applied, or None.

    The route is judged BEFORE the patch lands — the fifth audit moved this out of
    `envelope.finish`, which runs after `sandbox.collect()`, so a session the
    orchestrator was about to refuse had already had its changes carried into the
    project. PHASE 5 (sixth audit) gives the parallel path the same check: it had none,
    so a pass whose actual model disagreed with its plan was merged.
    """
    said_model, said_source = envelope.provider_claim(
        log_path, session_id, adapter=_telemetry_adapter(conf))
    return routing.attestation_problem(planned_model, said_model, route,
                                       source=said_source)


def run_session(project: Path, conf, role, charter, dry_run=False, lock=None, construal=None,
                construal_prompt=False, live=(), attempt=0):
    """Run one agent session. If `lock` is given, the caller already holds the
    writing lock and this call runs (subprocess + courier) inside it — the whole
    next-transaction is serialized (F-0009). If `lock` is None (standalone call),
    the session acquires and releases its own lock, with the courier commit still
    inside the locked region: acquire -> subprocess -> courier -> release."""
    if construal_prompt:
        # §4 rule 9: the gate's own prompt, never the role's (see PROMPTS["Construal"]).
        prompt = PROMPTS["Construal"].format(role=role, charter=charter)
    else:
        prompt = PROMPTS[role].format(charter=charter) if charter else PROMPTS[role]
    note = conf.get("session_note", "").strip()
    if note:
        prompt += f" Project note from the operator: {note}"
    # PHASE 13 (fourth audit): the route is resolved BEFORE the command is built, because
    # it is now an INPUT to the command. It used to be computed after `build_cmd` and
    # recorded in the envelope, which is exactly how argv came out byte-identical with
    # routing on and off — the route had nothing left to change by the time it existed.
    # PHASE 5 (sixth audit): through `dispatch_route`, which is what the parallel
    # dispatcher calls too. Resolved at the door, before anything is built or written.
    route, planned_model = dispatch_route(project, conf, role, charter)
    # Containment is resolved BEFORE anything is launched or written, so an
    # unknown profile or an uncontained grant refuses at the door rather than
    # halfway through a session (and `--dry-run` reports the same refusal).
    profile = resolve_profile(conf, role)
    # PHASE 3: the operator classified the MATERIAL, or nothing is dispatched. Checked
    # here, beside the other door checks, so `--dry-run` reports the same refusal and no
    # child process has been created when it fires.
    trust = require_trust(conf, role, profile)
    # Fail closed AT THE DOOR, so `--dry-run` reports the same refusal a real run would
    # get and an operator finds out before a charter is dispatched, not halfway through.
    backend = require_backend(profile, role)
    print(f"\n=== {role} session ===")
    print(f"charter: {charter or '(role default)'}")
    print(f"sandbox: {profile.name} — {profile.summary}")
    if backend:
        caps = container_capabilities(conf, verified=backend)
        print(f"containment: VERIFIED at launch — {backend}, image {caps['image']} "
              f"({'pinned by digest' if caps['image_pinned_by_digest'] else 'UNPINNED TAG'}), "
              f"network={caps['network']}, cpus={caps['cpus']}, "
              f"memory={caps['memory_mb']}m, pids={caps['pids_limit']}")
    # PHASE 2 (fifth audit): the command is built ONCE, below, where every input to it
    # exists. It used to be built here for the door checks and AGAIN at the dispatch —
    # and the second build was handed no route, so `--dry-run` printed a routed command
    # and the real run refused on its own unfilled `{model}` with no child ever created.
    # A dry run therefore has to reach the same build, which is why it now runs to the
    # line below rather than returning here. It still takes no lock and writes nothing:
    # `create=False` asks for the log path without making the directory, and the capsule
    # is built and stored only on the executing path.
    own_lock = lock is None and not dry_run
    if own_lock:
        lock = Lock(project / "audit", role)
        lock.acquire()
    try:
        # F-0009: EVERY orchestrator write — including creating the log dir —
        # must happen inside the critical section. In the standalone branch this
        # dir was created before acquire(), so a write escaped the lock. Do it
        # after acquire so the whole session (log dir, subprocess, courier) is
        # serialized under one lock: acquire -> writes -> courier -> release.
        # PHASE 14: OUTSIDE the working tree by default. 159 session logs at roughly
        # 900KB each made `audit/orchestrator-logs/` 102MB of the audit directory's
        # 104MB — already gitignored, so `.git` never carried them, but every walk of the
        # tree did: `git status`, `ls-files --others`, worktree creation, backups. The
        # digest still goes on the session's own event, so every `log_digest` join —
        # including the frozen Ouroboros-4 fixture — is untouched, and
        # `audit/logs-manifest.jsonl` keeps the name, size and hash in the repository.
        # `create` is the one thing a dry run changes here: it needs the PATH the logs
        # would go to (the sidecar path is derived from it, and the sidecar reaches the
        # command) and must not make the directory.
        # PHASE 5 (sixth audit): through `prepare_logs`, which the parallel dispatcher
        # calls too — one resolver, so both paths land in the operator's log root.
        logs, log_path = prepare_logs(project, conf, role.lower(), dry_run=dry_run)
        print(f"  {retention.describe_log_root(project, conf)}")
        # F-0093: the orchestrator ALWAYS holds audit/.lock around the child
        # session (own_lock acquired just above, or the caller's lock in
        # next/loop). Signal that inherited ownership so the launched
        # launcher/skill does NOT run a second `mkdir audit/.lock` and abort on
        # its own parent's lock — the shared lock protocol cannot otherwise tell
        # an orchestrated child from a rival writer. XCHECK_LOCK_INHERITED carries
        # the held lock's nonce; the child verifies it against audit/.lock/owner
        # and skips acquisition (a standalone `/xcheck-*` run has no such signal
        # and acquires atomically).
        # F-0096: give the child a fresh per-session identity (XCHECK_SESSION_ID —
        # a canonical 16-hex token) so a Remediator records it as the fix's
        # `fixed-by` provenance, and a later Verifier session is provably a
        # different session (§3 Verifier ≠ fixer). A fresh id per spawn means an
        # orchestrated Verifier can never share the Remediator's identity.
        #
        # F-0093 reopen fix: the SAME signal travels through TWO channels, because
        # process env alone does not reach the launcher-agent on every platform.
        # A sandboxed command runner (e.g. Codex) executes the agent's shell in an
        # environment where `env` shows nothing of this child CLI process's
        # variables, so a skill that reads only `$XCHECK_LOCK_INHERITED` sees it
        # empty and wrongly treats the orchestrator's own lock as a rival's. The
        # role-PROMPT is the one channel guaranteed to reach the agent on every
        # platform, so carry the same two tokens there too; the skills consume the
        # signal from the prompt (verifying the nonce against audit/.lock/owner,
        # which IS readable) with env as a fallback where it does propagate.
        token = getattr(lock, "token", None)
        session_id = os.urandom(8).hex()
        # Phase 8: the child environment is BUILT from an allowlist, not inherited.
        # `dict(os.environ)` handed an agent reading an arbitrary repository every
        # cloud credential, signing key and production token the operator's shell
        # happened to carry — an ambient grant nothing in the prompt could take back.
        # PHASE 9: the child runs inside a disposable worktree, where `git rev-parse
        # HEAD` answers the sandbox's own seed commit. Every write verb it runs recorded
        # THAT as the audited commit — a commit in no branch and no tag, gone at the next
        # `git gc`. The orchestrator knows the real one, so it hands it down; `write.run`
        # reads it here and falls back to the ambient answer only for the operator at
        # their own terminal, where the working directory IS the subject.
        child_env = child_environment(conf, {"XCHECK_SESSION_ID": session_id,
                                             SUBJECT_COMMIT_ENV: git_head(project) or ""})
        if token:
            child_env["XCHECK_LOCK_INHERITED"] = token
        orch = [f"XCHECK_SESSION_ID={session_id}"]
        if token:
            orch.append(f"XCHECK_LOCK_INHERITED={token}")
        # §4 rule 9: the construal key for THIS charter travels the same two channels as
        # the lock nonce, and for the same F-0093 reason — a sandboxed command runner shows
        # the agent nothing of this child CLI's environment, so env alone does not reach it
        # and the role-prompt line always does. Without the key the role cannot name the
        # file the gate is waiting for, and would write its construal under a key the
        # orchestrator never looks up.
        if construal:
            child_env["XCHECK_CONSTRUAL_KEY"] = construal
            orch.append(f"XCHECK_CONSTRUAL_KEY={construal}")
        # PHASE 13: derived here, from state and the charter, with no model and no
        # network — and written INSIDE the project, because the child runs in a sandbox
        # seeded from the project tree and a file outside it is one the child cannot
        # open. It is untracked and deliberately not ignored, so the seed's
        # `--exclude-standard` copy reaches it (the F-0093 trap, in the other direction).
        capsule_digest = None
        if not dry_run:
            try:
                from xcheck import capsule as capsule_mod
                cap = capsule_mod.build(load_state(project / "audit"), project, role,
                                        charter or "")
                capsule_mod.write(project, session_id, cap)
                capsule_digest = capsule_mod.capsule_digest(cap)
            except (StateError, OSError) as e:
                # A capsule that cannot be built must not stop a session: the corpus is
                # still there and the prompt still names it. The envelope records
                # `unknown`, the difference between "briefed with this" and "we do not
                # know".
                print(f"  ! context capsule not generated ({e}); the session reads the "
                      f"full corpus instead")
        child_prompt = prompt + (capsule_line(session_id) if capsule_digest else "") \
            + orchestration_context(orch, session_id)
        # THE build, through `prepare_launch` — the one both dispatchers call. Every
        # input it has: the route resolved at the door, the sidecar this session's
        # parent owns, and the prompt the child will actually read.
        # `tests/test_model_routing.py` asserts over the AST that there is one build and
        # that it is handed both; `tests/test_parallel_dispatch.py` asserts the other
        # dispatcher reaches the same function.
        plan = prepare_launch(conf, role, child_prompt, session_id, profile, log_path,
                              route)
        cmd, sidecar_path = plan.cmd, plan.sidecar_path
        print(f"command: {printable_cmd(cmd, child_prompt)}")
        if dry_run:
            print("dry-run: not executing.")
            # A dry run has an account too, and it is the honest one: nothing ran,
            # nothing was applied, and HEAD is where it was. `outcome="dry-run"` is
            # deliberately not one of OUTCOMES — no session finished, so none of the
            # seven applies.
            return RunResult(0, "dry-run", head_before=git_head(project),
                             head_after=git_head(project),
                             state_revision_before=envelope.current_revision(project),
                             state_revision_after=envelope.current_revision(project),
                             attempt=attempt)
        # PHASE 6: the parent creates the sidecar directory, 0700, beside the logs and
        # OUTSIDE the project — so it is outside every sandbox too. A dry run has already
        # returned, so this is the first thing the dispatch writes.
        envelope.make_sidecar_dir(log_path)
        pre_dirty = dirty_paths(project)
        # F-0120: snapshot the HEAD tree BEFORE the session so the courier can tell a
        # tracked-material change (in this baseline) from stray session scratch (absent
        # from it), asked from git as a fact rather than decoded from a status letter.
        pre_head = head_baseline_paths(project)
        t0 = time.time()
        # Phase 9: the invocation envelope is opened BEFORE the sandbox, so the record
        # of this dispatch is part of the tree the child is seeded with and the child
        # cannot be the thing that creates it. `fixed-by` is then bound to a record the
        # agent can read but has no verb to write (the structural closure of F-0159).
        #
        # Phase 11: the three points below are where this function writes the PROJECT —
        # the dispatch record, the collected patch plus the finished envelope, and the
        # courier commit. Under `parallel_passes` several of these run in threads of one
        # orchestrator process, and `audit/.lock` cannot separate them (it is a directory
        # whose owner file names this pid, so the second thread would recognise itself).
        # Each window is entered here and is deliberately SHORT — the agent session, the
        # part that takes minutes, is outside all three, which is where the parallelism
        # actually is.
        with serialized():
            # Read once, and keep them: these two are the BEFORE half of this session's
            # account, and re-deriving them after the session would answer a different
            # question (what is HEAD now, not what was it when we started).
            rev_before = envelope.current_revision(project)
            head_before = git_head(project)
            state_path = project / "audit" / "state.json"
            pre_state = _material_bytes(state_path) if state_path.exists() else None
            env_rec = envelope.dispatch_record(
                cmd, role, charter or "", child_prompt, session_id, profile,
                rev_before, head_before=head_before,
                env=child_env, conf=conf, verified=backend,
                # This IS the outer repository here — `run_session` is the orchestrator,
                # never the child — so the subject commit is the ambient one, and it is
                # the value handed to the child above.
                subject_commit=head_before,
                subject_manifest=subject_manifest(project),
                controller_commit=controller_commit(),
                capsule_digest=capsule_digest,
                trust_level=trust,
                route=route)
            envelope.store(project, env_rec, event="session_dispatched", live=live)
            # The one component of the embargo key that is not already in the dispatch
            # record and cannot go into it: `state.sessions[]` is a closed schema, and
            # this is not a property of the session, it is a property of the CODE that
            # ran it. Without it a pass blocked by an orchestrator defect would stay
            # embargoed after the defect was fixed, and the operator would read a
            # permanently silent queue as "this audit is impossible".
            envelope.emit(project, "dispatch_key", session_id, role=role,
                          source_digest=orchestrator_source_digest(),
                          charter_slice=_charter_slice_now(project, charter))
        sandbox = Sandbox(project, profile, role, logs, conf=conf)
        quarantined = None
        try:
            sandbox.enter()                        # fails CLOSED; never degrades to none
            try:
                # The container profile is the only one that changes what is LAUNCHED;
                # every other profile gets `cmd` back unchanged.
                launch = sandbox.wrap(cmd, conf, child_env)
                if launch is not cmd and launch != cmd:
                    env_rec = dict(env_rec)
                    env_rec["sandbox_details"] = profile.details(
                        workdir=sandbox.workdir, conf=conf, verified=backend)
                if sandbox.image_waiver:
                    print(f"containment: {sandbox.image_waiver}")
                timings = {}
                rc, outcome, elapsed = run_child(
                    launch, sandbox.workdir, log_path, child_env, conf, lock,
                    profile=profile, timings=timings,
                    # The prompt this session was given, in bytes: a CLI that echoes it
                    # back has written that much without doing anything, and the output
                    # deadline must not count it as work. Known exactly here, so it is
                    # passed rather than guessed at by a constant.
                    echoed_bytes=len((child_prompt or "").encode("utf-8")),
                    # The other half of the OUTPUT bound, and the one that does not
                    # depend on how chatty a CLI is: a session that recorded a canonical
                    # transition has acted, however little it printed.
                    moved=lambda: envelope.session_moved(project, session_id),
                    # The activity bound's own evidence. The worktree baseline is taken
                    # HERE, before the child starts, so "dirty in a way it was not at
                    # dispatch" compares against the state this session inherited rather
                    # than against a clean tree it never had.
                    activity=lambda _b=dirty_paths(sandbox.workdir): activity_probe(
                        project, session_id, sandbox.workdir, _b, sidecar=sidecar_path),
                    header=[f"xcheck: {sandbox.image_waiver}"] if sandbox.image_waiver
                    else (),
                    broker=sandbox.broker)
                print(f"outcome: {outcome} (exit={rc}, {elapsed / 60:.1f} min)")
                # PHASE 3 (fifth audit): the route is judged BEFORE the patch lands. This
                # check used to live inside `envelope.finish`, which runs after
                # `sandbox.collect()` — so a session the orchestrator was about to refuse
                # had already had its changes carried into the project. Same rule, same
                # reader, asked at the point where the answer changes what happens: a
                # refused route makes the outcome non-`ok`, and the branch below then
                # quarantines the work for a human instead of applying it.
                route_refusal = result_problem(conf, log_path, session_id,
                                               planned_model, route)
                if route_refusal and outcome == "ok":
                    print(f"route: REFUSED before the patch — {route_refusal}")
                    outcome = "refused"
                with serialized():                 # merge window 2: the patch lands
                    # 0.9.1 phase 3 (P0 #3). This was `if outcome != "cancelled"`, so a
                    # provider-error, timeout, crash, refused or blocked session landed
                    # its half-finished changes in the project — a crashed session
                    # silently modifying the repository under audit, and the root of the
                    # retry blocker. Only `ok` applies. The rest is not DISCARDED (a
                    # half-finished remediation can still be worth having); it is handed
                    # to a human intact.
                    if outcome == "ok":
                        sandbox.collect()
                    elif outcome == "cancelled":
                        print("cancelled: the session's changes were NOT applied to the "
                              "project — a cancelled session is a session with no verdict.")
                    else:
                        quarantined = sandbox.quarantine(
                            outcome, session_id=session_id, head_at_capture=head_before,
                            charter=charter)
            finally:
                sandbox.leave()                    # BOTH paths: success and failure
            # Closed AFTER collect(), so the session's own writes are already in the
            # tree and the envelope is finalised on top of them rather than being
            # overwritten by the patch that carries them back.
            with serialized():
                retention.record(project, session_id, log_path,
                                 sha256=envelope.sha256_file(log_path), role=role)
                # PHASE 13: a session whose ACTUAL model disagrees with its planned route
                # is recorded as a protocol violation rather than losing the session. The
                # work may be real; the ROUTE claim about it is not, and a record that
                # kept the label would be the number a cost comparison rests on.
                try:
                    env_rec = finish_session(conf, env_rec, rc, outcome, elapsed,
                                              git_head(project), log_path,
                                              sandbox_seed=getattr(sandbox, "base", None),
                                              # PHASE 11 (fourth audit): the adapter the
                                              # OPERATOR declared, resolved once here and
                                              # never inferred from `cmd`. An unknown name is
                                              # a refusal at load, not a fallback: parsing one
                                              # vendor's numbers with another's key names
                                              # produces figures that are wrong silently.
                                              # PHASE 13: what xcheck put in the argv. The
                                              # provider says what actually ran; `finish`
                                              # refuses to record a route the two disagree on.
                                              # The adapter is resolved inside
                                              # `finish_session`, for both dispatchers.
                                              planned_model=planned_model, route=route,
                                              first_output_s=timings.get("first_output_s"),
                                              idle_s=timings.get("idle_s"),
                                              # Recorded so the NEXT run derives
                                              # `activity_deadline` from measured first
                                              # activity. Today's default is derived from
                                              # total session duration because this figure
                                              # did not exist — a bound guessed once may
                                              # stay guessed forever unless the run that
                                              # uses it also measures it.
                                              first_activity_s=timings.get(
                                                  "first_activity_s"),
                                              # WHICH deadline fired, on the record. The
                                              # outcome says a stall happened; without this
                                              # the operator cannot tell "never started"
                                              # from "went quiet after an hour of work".
                                              note=timings.get("stall_note"))
                except routing.RouteError as e:
                    # PHASE 2 (fifth audit): `refused`, not `protocol-violation`. The
                    # latter is a PROTOCOL answer (`envelope.PROTOCOLS`), not a session
                    # OUTCOME (`util.OUTCOMES`), so this branch wrote a value the state
                    # schema refuses — and the whole session record was then lost to a
                    # StateError. It had never run: the route could not reach a child, so
                    # no provider could disagree with it. Two enums, one field, and the
                    # only thing that found it was dispatching a real mismatch.
                    print(f"route: REFUSED — {e}")
                    env_rec = envelope.finish(
                        env_rec, rc, "refused", elapsed, git_head(project),
                        log_path, sandbox_seed=getattr(sandbox, "base", None),
                        adapter=_telemetry_adapter(conf),
                        first_output_s=timings.get("first_output_s"),
                        idle_s=timings.get("idle_s"),
                        first_activity_s=timings.get("first_activity_s"),
                        note=f"route attestation refused: {e}")
                    outcome = "refused"
                # PHASE 3 (fifth audit): when a route was planned and the session comes
                # back UNATTESTED, say why in the one word that decides it. `unattested`
                # on its own reads as "the provider was quiet"; the operator needs to know
                # the difference between a machine with no telemetry configured and an
                # agent that named a model on a channel it writes itself.
                if route and env_rec.get("route_attestation") == routing.UNATTESTED:
                    print(f"route: UNATTESTED — telemetry_source="
                          f"{env_rec.get('telemetry_source') or 'none'!r}; only "
                          f"{routing.PROVIDER_SOURCE!r} can confirm a model.")
                envelope.store(project, env_rec, event="session_finished", live=live)
        except OSError as e:
            # F-0108: the agent CLI could not be SPAWNED (a missing/mistyped
            # executable in <role>_cmd) — subprocess.run raised before returning a
            # code, so the addressed session-error halt in cmd_next (which reads a
            # non-zero rc) was bypassed by a raw traceback. Convert the spawn failure
            # into that same addressed stop: name the unrunnable command and return
            # non-zero so cmd_next reports `stop-error` and the loop halts cleanly.
            # The lock is still released by the `finally` below.
            print(f"stop: could not launch {role} session — {role.lower()}_cmd is not "
                  f"runnable: {cmd[0]!r} ({e}). Fix the command in audit/orchestrator.conf.")
            # The dispatch was recorded a moment ago; close it here rather than leaving
            # an open envelope for the NEXT session to find and abandon.
            with serialized():
                envelope.store(project, envelope.finish(env_rec, 1, "crash", 0.0,
                                                        git_head(project), log_path),
                               event="session_finished", live=live)
            return _session_result(project, 1, "crash", sandbox=None,
                                   pre_dirty=pre_dirty, pre_state=pre_state,
                                   quarantined=quarantined, rev_before=rev_before,
                                   head_before=head_before, attempt=attempt,
                                   session_id=session_id, role=role,
                                   charter=charter)
        # PHASE 14: the log is OUTSIDE the project now, so `relative_to(project)` raises
        # rather than shortening. Print the path it actually has.
        try:
            shown = log_path.relative_to(project)
        except ValueError:
            shown = log_path
        print(f"log: {shown} ({(time.time() - t0) / 60:.1f} min)")
        tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-8:]
        for line in tail:
            print(f"  | {line}")
        with serialized():                         # merge window 3: the courier commit
            committed = courier_commit(project, role, charter, pre_dirty, conf, pre_head)
    finally:
        if own_lock:
            lock.release()
    if not committed:
        # courier could not durably commit; do not let next/loop advance to a
        # role (e.g. Verifier) that needs the diff (F-0008).
        return _session_result(project, rc if rc != 0 else 1, outcome, sandbox=sandbox,
                               pre_dirty=pre_dirty, pre_state=pre_state,
                               quarantined=quarantined, rev_before=rev_before,
                               head_before=head_before, attempt=attempt,
                               session_id=session_id, role=role,
                               charter=charter)
    return _session_result(project, rc, outcome, sandbox=sandbox, pre_dirty=pre_dirty,
                           pre_state=pre_state, quarantined=quarantined,
                           rev_before=rev_before, head_before=head_before,
                           attempt=attempt, session_id=session_id,
                           role=role, charter=charter)


def orchestrator_source_digest(path=None):
    """sha256 of the orchestrator's OWN source (F-0117).

    The loop runs the code image loaded into this process at import; module
    constants (PROMPTS, DEFAULT_CONF, the decision tables) and every function's
    bytecode are that snapshot, refreshed by nothing during a run — only conf is
    re-read per session (F-0009). When the orchestrator itself is under audit its fix
    lands on disk yet the running loop keeps executing the stale image (§4 rule 2
    is honored for files the child reads, violated for the orchestrator's own
    code). Fingerprinting the source catches exactly that: an unchanged source
    yields an identical hash (no false trigger); a mid-run edit diverges. Returns
    None when the source cannot be read, so an unreadable file never trips it.

    The subject is the WHOLE code image, so after the package split it hashes every
    `xcheck/*.py` rather than one file — scoping it to `__file__` alone would leave
    an edit to `decision.py` or `legacy_md.py` invisible to a loop running from
    `runner.py`, which is the very blindness this check exists to remove. An
    explicit `path` still hashes that one file (the selftest's fixture path)."""
    try:
        if path:
            return hashlib.sha256(Path(path).read_bytes()).hexdigest()
        h = hashlib.sha256()
        for p in sorted(Path(__file__).resolve().parent.glob("*.py")):
            h.update(p.name.encode("utf-8"))
            h.update(b"\0")
            h.update(p.read_bytes())
            h.update(b"\0")
        return h.hexdigest()
    except OSError:
        return None


def audit_state_digest(project: Path):
    """sha256 of the CANONICAL state the decision reads (F-0118, F-0117).

    It hashes `audit/state.json` with the orchestrator's own bookkeeping removed —
    `sessions` and `state_revision` — and nothing else. `decide()` reads canonical state
    and only canonical state: LEDGER.md and the frontmatter blocks are rendered FROM it
    and are never read back as authority (§2 rule 3), so two sessions that leave the
    document identical get the identical charter routed at them again no matter what
    else moved under `audit/`.

    It used to hash every FILE under `audit/` except a growing exclusion list —
    orchestrator-logs/, .lock/, events.jsonl. That was an inclusion rule with holes
    patched into it, and F-0117 is the hole nobody had patched: an unrelated
    `audit/progress.log`, a scratch note, a re-rendered view, any file at all changed
    the digest and read as progress. A no-progress detector that counts a stray file as
    work is a detector that never fires.

    The exclusions are the same two, for the same reason, and now they are the only
    ones: every dispatch writes `sessions[]` and moves `state_revision`, so counting
    them would make an agent that did nothing look productive forever — the detector
    would be reporting on its own footprints. The findings, queue, limits, catalogs,
    plans and construals it accompanies are hashed in full.

    What this deliberately does NOT count as progress: a rewritten finding body, a fresh
    pass report, an edited AUDIT.md. Those are evidence, and evidence that no verb
    recorded moved no machine state — which is the whole design (§2 rule 1), not an
    oversight of this digest."""
    audit = project / "audit"
    h = hashlib.sha256()
    if not audit.is_dir():
        return h.hexdigest()
    state_path = audit / "state.json"
    if state_path.is_file():
        try:
            doc = json.loads(_material_bytes(state_path).decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            # Unreadable canonical state is not "no progress" — it is a different
            # problem, and `load_state` will report it. Hash the raw bytes so a broken
            # document at least differs from a working one instead of collapsing to the
            # empty digest.
            h.update(b"unreadable-state\0")
            try:
                h.update(_material_bytes(state_path))
            except OSError:
                pass
            return h.hexdigest()
        for key in ("sessions", "state_revision"):
            doc.pop(key, None)
        h.update(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return h.hexdigest()


def _material_bytes(path):
    """A file's bytes, with the orchestrator's own session bookkeeping removed from
    `state.json`. A file that is not state.json is returned untouched."""
    raw = path.read_bytes()
    if path.name != "state.json":
        return raw
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw          # unreadable state is not this function's problem to report
    doc.pop("sessions", None)
    doc.pop("state_revision", None)
    # PHASE 3 (sixth audit). The third field of the same kind: `ledger_commits` records
    # that a write committed, and EVERY write leaves one — including the orchestrator's
    # own envelope store. Left in, an idle session's `effects_applied` is True because
    # the orchestrator wrote down that it had written something down. `ledger_anchor` is
    # deliberately NOT popped: it moves only when a verb applies a transition, which is
    # exactly the material change this function exists to notice.
    doc.pop("ledger_commits", None)
    return json.dumps(doc, sort_keys=True).encode("utf-8")
