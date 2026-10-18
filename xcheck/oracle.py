"""Acceptance by result — the oracle a finding carries, how it runs, and what it proves.

0.9.6 (F6, P1 acceptance). A `closed` record used to become a "durable fix" by AGE: one
unchanged record read `durable_fix=0` on 2026-10-14 and `1` on 2026-10-16, with nothing
re-run in between. Elapsed time establishes nothing about a fix. This module is the
smallest thing that does:

  - a finding may carry an `## Oracle` block — one fenced JSON object naming an argv, the
    exit status that means "the defect is absent", a timeout and the scope it tests;
  - `xcheck run-oracle F-NNNN` runs it ONLY under the container profile (pinned image,
    no network), on a seeded clone of the current material, and records an `oracle_run`
    event: the oracle's digest, the material's identity (scheme 2), the exit, a verdict;
  - `record-verdict --verdict closed` then needs a `defect-absent` run on the CURRENT
    material by an oracle whose digest once recorded `defect-present` on EARLIER
    material — or the operator's explicit `--evidence manual:"…"`, which is labelled so;
  - `evidence_level` derives the level from those events, for `status`, M2 and the
    benchmark alike. `re-observed` — a later `defect-absent` run on newer material — is
    the only thing that makes a fix durable.

The oracle is the AUDITOR's procedure, written when the defect was in front of them
(XCHECK.md §5 rule 3); this module never runs a command from a finding on the host.
"""

import hashlib
import json
import re
from pathlib import Path

ORACLE_KEYS = {"argv", "expect_absent_exit", "timeout_s", "scope"}
TIMEOUT_MAX_S = 3600
VERDICTS = ("defect-present", "defect-absent", "error")
LEVELS = ("reported", "confirmed", "fixed", "independently-verified", "re-observed")
CONFIRMED_STATUSES = {"accepted", "validated", "planned"}
EVENT = "oracle_run"

_BLOCK_RE = re.compile(r"(?ms)^## Oracle[^\n]*\n(.*?)(?=^## |\Z)")
_FENCE_RE = re.compile(r"(?s)```(?:json)?\s*\n(.*?)\n```")


def read_block(body_text):
    """`(oracle, None)`, `(None, None)` when the body has no `## Oracle`, or `(None, why)`.

    Strict, like the stop parser: an oracle that would run something other than what it
    says, or without a bound, is refused with its reason — never coerced."""
    m = _BLOCK_RE.search(body_text or "")
    if not m:
        return None, None
    section = re.sub(r"(?s)<!--.*?-->", "", m.group(1)).strip()
    if not section:
        return None, None                    # the template's guidance, left unfilled
    fence = _FENCE_RE.search(section)
    if not fence:
        return None, "the ## Oracle section holds no fenced JSON block"
    try:
        obj = json.loads(fence.group(1))
    except ValueError as e:
        return None, f"the ## Oracle block is not valid JSON ({e.msg})"
    if not isinstance(obj, dict):
        return None, f"the ## Oracle block is a JSON {type(obj).__name__}, not an object"
    extra = sorted(set(obj) - ORACLE_KEYS)
    if extra:
        return None, f"the ## Oracle block names keys it does not define: {', '.join(extra)}"
    argv = obj.get("argv")
    if (not isinstance(argv, list) or not argv
            or not all(isinstance(a, str) and a for a in argv)):
        return None, "`argv` must be a non-empty JSON list of non-empty strings"
    t = obj.get("timeout_s")
    if isinstance(t, bool) or not isinstance(t, int) or not 1 <= t <= TIMEOUT_MAX_S:
        return None, f"`timeout_s` must be an integer from 1 to {TIMEOUT_MAX_S}"
    x = obj.get("expect_absent_exit", 0)
    if isinstance(x, bool) or not isinstance(x, int) or not 0 <= x <= 255:
        return None, "`expect_absent_exit` must be an integer exit status 0-255"
    scope = obj.get("scope", [])
    if not isinstance(scope, list) or not all(isinstance(s, str) for s in scope):
        return None, "`scope` must be a JSON list of paths"
    return {"argv": argv, "expect_absent_exit": x, "timeout_s": t, "scope": scope}, None


def block_digest(oracle, project):
    """sha256 over the oracle AND the bytes of every argv element that is a file in the
    project — the checker it runs. Editing the block or the checker is a different
    oracle, and a `defect-present` run by the old one proves nothing for the new one."""
    h = hashlib.sha256(json.dumps(oracle, sort_keys=True).encode())
    for a in oracle["argv"]:
        p = Path(project) / a
        try:
            if p.is_file():
                h.update(b"\0" + a.encode() + b"\0" + hashlib.sha256(p.read_bytes()).digest())
        except OSError:
            pass
    return h.hexdigest()


def verdict(rc, expect_absent_exit, timed_out=False):
    if timed_out or rc in (125, 126, 127) or rc < 0:
        return "error"                       # the runner, not the oracle, answered
    return "defect-absent" if rc == expect_absent_exit else "defect-present"


def runs(events, fid):
    return [e for e in events if e.get("event") == EVENT and e.get("finding") == fid]


def closing_problems(events, fid, oracle_digest, snapshot, independence):
    """Every reason an oracle-backed `closed` is not earned, each with its own sentence."""
    mine = runs(events, fid)
    bad = []
    absent = [e for e in mine if e.get("verdict") == "defect-absent"
              and e.get("snapshot") == snapshot]
    if not absent:
        bad.append(f"no `defect-absent` oracle run on the current material ({snapshot}); "
                   f"run `xcheck run-oracle {fid}` on it first")
    elif not any(e.get("oracle_digest") == oracle_digest for e in absent):
        bad.append(f"the `defect-absent` run on this material was by a different oracle "
                   f"than the one in {fid} now — the block or its checker changed")
    present = [e for e in mine if e.get("verdict") == "defect-present"
               and e.get("oracle_digest") == oracle_digest and e.get("snapshot") != snapshot]
    if not present:
        bad.append("this oracle never recorded `defect-present` on earlier material — an "
                   "oracle that was never seen to fail proves nothing by passing")
    if independence in ("same", "unrecorded"):
        bad.append(f"verdict independence is `{independence}`: an oracle-backed closure "
                   f"needs a verifier whose session can be told apart from the fixer's")
    return bad


def events_by_finding(events):
    """`{finding id: [its oracle runs and transitions, in stream order]}` — so a caller
    asking about every finding reads the stream once. `evidence_level` over one
    finding's slice answers exactly what it answers over the whole stream."""
    out = {}
    for e in events:
        kind = e.get("event")
        key = e.get("finding") if kind == EVENT else (
            e.get("target") if kind == "state_transition" else None)
        if key:
            out.setdefault(key, []).append(e)
    return out


def evidence_level(rec, events):
    """`(level, manual)` for one finding, from its status and the stream.

    `re-observed` — a `defect-absent` run on material NEWER than the closure's — is the
    only durable level; a closed record that nobody re-ran stays `independently-verified`
    however old it is. `manual` marks a closure recorded on the operator's word."""
    status = getattr(rec, "status", None)
    fid = getattr(rec, "id", None)
    mine = runs(events, fid)
    closes = [i for i, e in enumerate(events)
              if e.get("event") == "state_transition" and e.get("target") == fid
              and e.get("verb") == "record-verdict" and e.get("verdict") == "closed"]
    if status == "closed":
        if not closes:
            return "independently-verified", True       # closed before this record existed
        i = closes[-1]
        close = events[i]
        manual = bool(close.get("evidence_manual"))
        later = [e for e in events[i + 1:] if e.get("event") == EVENT
                 and e.get("finding") == fid and e.get("verdict") == "defect-absent"
                 and e.get("snapshot") not in (None, close.get("snapshot"))]
        return ("re-observed" if later else "independently-verified"), manual
    if status == "fixed":
        return "fixed", False
    if status in CONFIRMED_STATUSES or any(e.get("verdict") == "defect-present"
                                           for e in mine):
        return "confirmed", False
    return "reported", False


def durable(rec, events):
    """THE durable-fix predicate — M2 and the benchmark both call this one."""
    return evidence_level(rec, events)[0] == "re-observed"
