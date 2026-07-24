---
name: xcheck-plan
description: Launch an xcheck Planner session - inventory the project and produce audit/AUDIT.md. Use when the user invokes $xcheck-plan or /xcheck-plan, says "plan the audit" or "fill AUDIT.md", or wants to start an xcheck audit in a project that has audit/XCHECK.md installed.
---

# xcheck launcher — Planner

Thin launcher. The methodology lives in `audit/XCHECK.md`; this file only picks the charter and starts the role. It adds nothing normative.

1. **Preflight.** `audit/XCHECK.md` must exist in the current project. Missing → stop and tell the user to install xcheck first (xcheck repo `bootstrap.md` / `install.sh`).
2. Read `audit/XCHECK.md` fully. Your role: **Planner** (§3 role card governs you — mission, reads, writes, stop conditions, forbidden actions).
3. **Charter auto-pick:** planning is done only if `audit/AUDIT.md` is a complete plan per the Planner stop condition (§3): at least one dimension backed by a norm source, a unit map, AND a non-empty pass queue — then report that and stop (suggest the `xcheck-status` launcher). A partial or malformed AUDIT.md (e.g. a pass queue but no norm-backed dimension or no unit map) is NOT done: continue planning to fill the missing parts, or stop with an exact list of what is missing. Otherwise your charter is: inventory this project and its norms; fill `audit/AUDIT.md` (dimensions with norm sources, unit map, pass queue, limits). A user-supplied argument overrides this charter.
4. Announce the charter in one line, then execute the role exactly per XCHECK.md.
5. Converse with the human in their language; artifact content follows the XCHECK.md language rule.

## Lock discipline

`audit/.lock` serializes writing sessions (§4 rule 8) and is shared with the orchestrator (`bin/xcheck`), which acquires it atomically with `open(O_CREAT|O_EXCL)`. Match that — never check-then-create (the gap between an existence check and a separate create lets two sessions both win):

1. **Acquire atomically:** create `audit/.lock` in one exclusive step that FAILS if the file already exists — e.g. `(set -C; printf '%s' '{"pid": <pid or 0>, "role": "<Role>", "started": "<ISO>", "host": "<host>"}' > audit/.lock)`, where `set -C` (noclobber) makes the redirect fail atomically when the file exists. For `pid`, record a process id ONLY if it stays alive for your whole session (e.g. the orchestrator's own pid); a transient shell `$$` dies the instant the acquire command returns — while your session keeps running — which would make your own live lock look stale and let another session steal it, so never record `$$`. An agent-CLI session has no session-long pid: record `pid: 0`. `bin/xcheck` reads `pid: 0` as a live manual session (`os.kill(0, 0)` never reports it dead), so the lock stands until your owner-checked release removes it, or — if the session died — a human clears it with `xcheck unlock --force`. If acquisition fails, another writing session holds the lock — do not start; report the conflict to the human.
2. **Owner-checked release:** hold the lock for the whole session; before deleting, re-read `audit/.lock` and confirm its `pid`+`started` still match the lock you wrote — delete only then, and on every exit path including early stop. Never delete a lock you do not own.
3. **Stale lock:** a lock carrying a real, dead `pid` is stale — clear it with `xcheck unlock`, never silently steal it. A `pid: 0` manual-session lock never reads as pid-dead, so `xcheck unlock` alone refuses it; clear it only with `xcheck unlock --force`, and only after the human confirms no writing session is active (§4 rule 8).
