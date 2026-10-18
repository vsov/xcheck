#!/usr/bin/env python3
"""Derive `ouroboros-4-stops.jsonl` — the honest-stop OVERLAY for the frozen corpus.

The frozen corpus (`ouroboros-4-events.jsonl`) predates the `XCHECK_STOP` line, so no
event in it carries a stop. What the 136 barren sessions actually did is in their logs,
which are NOT in this repository (`~/.local/state/xcheck/logs/xcheck/`, joined to a
session by the `log_digest` the orchestrator recorded — the join `logs-manifest.jsonl`
records). This script reads those logs ONCE and writes, per dispatched session, the stop
the session would have emitted had the channel existed:

    reason   `blocked-dependency` when the session's own log shows the write path refusing
             it (`audit/.lock is held`) or the lock owner absent, AND it made no declared
             transition; null otherwise — a session that recorded work did not stop, a
             session cut off by the provider did not stop honestly, a session with no
             surviving log cannot be read.
    honest   whether the LAST 4000 bytes of the log say so in words (blocked / not
             complete / remains queued / unregistered / stopped before ...). Recorded
             beside the stop, not folded into it: the stop is derived from the refusal the
             log shows, the honesty is derived from what the session said about it.

Reproduce: run from the repository root on a machine holding the logs. Where the logs are
absent the fixture is READ, never regenerated — the digest pinned in `test_honest_stop.py`
is the freeze. 2026-09-09."""
import collections
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests" / "fixtures" / "ouroboros-4-events.jsonl"
OUT = ROOT / "tests" / "fixtures" / "ouroboros-4-stops.jsonl"
LOGS = Path(os.path.expanduser("~/.local/state/xcheck/logs/xcheck"))
MANIFEST = ROOT / "audit" / "logs-manifest.jsonl"

LOCK_REFUSAL = "audit/.lock is held"
LOCK_ABSENT = ("audit/.lock/owner` and `audit/.lock` are absent", "lock/owner` is absent",
               "owner record is absent", "are absent")
HONEST = ("blocked, not complete", "not falsely completed", "remains queued", "unregistered",
          "blocked-dependency", "not complete", "could not record", "cannot record",
          "exited 1", "exit 1", "stopped before", "was not started", "blocked before",
          "was not run", "requires stopping", "failed closed")
TOKENS = re.compile(r"(?im)^[ \t]*tokens used[ \t]*:?[ \t]*(?:\r?\n[ \t]*)?"
                    r"(\d[\d   ,_ ]*?)[ \t]*$")


def main():
    events = [json.loads(l) for l in CORPUS.open(encoding="utf-8") if l.strip()]
    dispatched = [e for e in events if e["event"] == "session_dispatched"]
    finished = {e["session_id"]: e for e in events if e["event"] == "session_finished"}
    moved = collections.defaultdict(list)
    for e in events:
        if e["event"] == "state_transition" and e.get("session_id"):
            moved[e["session_id"]].append(e.get("verb"))
    pass_of = {hashlib.sha256(f"P-{n:02d}".encode()).hexdigest(): f"P-{n:02d}"
               for n in range(1, 100)}
    manifest = {}
    for l in MANIFEST.open(encoding="utf-8"):
        m = json.loads(l)
        manifest[m["sha256"]] = m["name"]
    rows, tally = [], collections.Counter()
    for d in dispatched:
        sid = d["session_id"]
        fin = finished.get(sid)
        row = {"session_id": sid, "pass": pass_of.get(d["charter_hash"]),
               "moved": sorted(set(moved.get(sid, []))), "outcome": fin and fin["outcome"],
               "log": None, "tokens": None, "lock_refusal": False, "lock_absent": False,
               "honest": False, "stop": None}
        name = fin and manifest.get(fin["log_digest"])
        path = LOGS / name if name else None
        if path and path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            row["log"] = name
            m = TOKENS.findall(text)
            row["tokens"] = int(re.sub(r"\D", "", m[-1])) if m else None
            row["lock_refusal"] = LOCK_REFUSAL in text
            tail = text[-4000:]
            row["lock_absent"] = any(s in tail for s in LOCK_ABSENT)
            row["honest"] = any(h in tail.lower() for h in HONEST)
            # A stop the session WOULD HAVE EMITTED: it saw the write path refuse it (or
            # found no lock owner), recorded nothing, and SAID SO. A session the provider
            # cut off after the refusal said nothing, so no stop is imputed to it.
            if (not row["moved"] and row["honest"]
                    and (row["lock_refusal"] or row["lock_absent"])):
                blocker = next((ln.strip() for ln in reversed(tail.splitlines())
                                if "lock" in ln.lower()), "")[:160]
                row["stop"] = {"reason": "blocked-dependency", "target": row["pass"],
                               "detail": blocker or "write path refused under the inherited lock"}
        tally[("moved" if row["moved"] else "barren", "stop" if row["stop"] else "no-stop",
               "honest" if row["honest"] else "-")] += 1
        rows.append(row)
    with OUT.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"wrote {len(rows)} rows -> {OUT}")
    for k, v in sorted(tally.items(), key=lambda x: -x[1]):
        print(f"  {v:4d}  {k}")
    print("sha256", hashlib.sha256(OUT.read_bytes()).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main())
