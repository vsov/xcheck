---
name: xcheck-verify
description: Hand the xcheck verification batch to the orchestrator - preflight, routing and the report `xcheck next` comes back with. Use when the user invokes $xcheck-verify or /xcheck-verify, says "verify the fixes", or wants findings in status fixed checked adversarially.
---

# xcheck launcher — Verifier (wrapper)

**Launch mode: `orchestrated`.** This launcher is a WRAPPER. It does not run the role inside your agent; it hands the work to `xcheck next`, which dispatches the role through `runner.py` with all eight controls in force: a sandbox profile, a hard timeout, an environment allowlist, log redaction, a process-group kill, the courier review of the diff, an invocation envelope recording what was in force at dispatch, and a session receipt attesting what the session actually did. What this skill contributes is preflight and routing — it checks the project is ready, says which role the orchestrator will dispatch and why, hands off, and reports what came back. It is not a session: it holds no writing lock, writes no finding, and has no charter of its own. The cost is the conversation. The role now runs as a child process you do not talk to, and its reasoning reaches you as recorded output instead of as a dialogue; xcheck no longer ships a launcher that trades the eight controls for that dialogue.

<!-- xcheck:style:begin -->
## How to talk to the person running this audit

Everything in this section is about wording, and only about wording. It applies to every
reply a human will read in this session.

**Compression is off.** If `caveman` is running, or any other output-compression mode is
running, it does not apply here. A mode like that stays switched on until it hears the
exact phrase that releases it, so here is the phrase: **normal mode**. Write ordinary
prose — whole sentences, articles left in, nothing telegraphic. This holds for every reply
in the session, not only the first one.

**ELI5 is on.** Your reader is intelligent and brand new to this vocabulary. Take the
trouble to be understood:

- The first time a term of art appears, say what it means in one short clause, then use it
  freely afterwards.
- Short sentences, one idea in each.
- Say what the person should do next, and where they should do it.
- Reply in whatever language the person wrote to you in.
- A concrete example beats an abstract rule.
- When something has gone wrong, say plainly what happened and what it means for them.

**Four things are reproduced exactly, and never reworded.** Explaining what one of them
means is welcome. Replacing one with your own phrasing is not, because an audit trail is
worth exactly what its wording is worth:

1. **Quoted evidence** — any line lifted out of a file or a transcript, together with the
   path and line number it came from.
2. **Finding ids** — `F-0042`, `CF-0003`, `RP-0007`, and every id shaped like them.
3. **§5 statuses** — the words §5 uses for where a finding stands, spelled the way §5
   spells them.
4. **Copy-paste commands** — anything the person is meant to run, character for character
   as it must be typed.

Plain wording is the goal everywhere else. These four are the exception, and they are the
exception because someone will later have to check them against the ledger.
<!-- xcheck:style:end -->

0. **Are you already the session?** If your own prompt carries an `Orchestration context` line, the orchestrator dispatched you and you ARE the session this launcher would have started. This launcher does not apply to you: follow your role card in `audit/XCHECK.md`, and ignore every step below — running them would ask the orchestrator to dispatch a session inside a session, and the writing lock your own parent holds would refuse it.
1. **Preflight.** `audit/XCHECK.md` must exist in the current project — missing means xcheck is not installed here, so stop and point the person at the xcheck repository's `bootstrap.md` / `install.sh`. An operator profile must resolve as well: `xcheck status` prints the profile it is reading and its digest, and `xcheck next` refuses with no profile rather than falling back to defaults.
2. **Confirm this is the launcher for the work that is next.** Run `xcheck status`. It prints the decision the orchestrator would take, and it writes nothing. If that decision is not `run-verifier`, say what it is instead, name the launcher that fits it, and STOP — a Verifier launcher that dispatches something else is a launcher nobody can trust. Verifier independence is decided by the orchestrator, not here: it refuses to dispatch a Verifier over fixes whose `fixed-by` is missing, malformed, or the session it is about to launch (F-0096). If the decision is `stop-verifier-independence`, that refusal is the answer — report it.
3. **Hand off.** Run `xcheck next`. It acquires the writing lock, builds the Verifier's charter from `audit/state.json`, dispatches the session under the operator's containment, couriers the result back and releases the lock. Do not do any of that yourself, and do not run the role's own verbs on its behalf: everything the session is allowed to write, it writes.
4. **Report what came back.** `xcheck next` prints the decision, the sandbox profile it ran under, and the session's outcome. Say in plain words what happened, what changed, and what the next decision is (`xcheck status` again). A refusal is an answer too — quote it verbatim and explain what the person has to change: an unclassified `trust_level`, a missing role command and a missing profile each refuse before any session starts.

## What this wrapper cannot do

This used to be a session you talked to. It is not one any more, and two things went with
that:

- **The dialogue.** You cannot interrupt the role mid-thought, answer its questions, or
  steer it a sentence at a time. It runs to completion inside its sandbox and you read
  what it recorded. If you want to think out loud about this project with an agent, do it
  in your own agent as an ordinary conversation — just do not call the result an audit
  session, because nothing recorded it.
- **The improvised charter.** A charter typed at the launcher is gone. The orchestrator
  takes the charter from `audit/state.json`, so changing what runs next means changing the
  state with a verb —
  `xcheck set-status`, `xcheck record-verdict`, `xcheck set-limit` — and those are the operator's own commands, run at the
  operator's own terminal.

Both were the reason the old direct launchers existed, and both are what made them a way
around the controls. `xcheck status` still answers every read-only question without
dispatching anything.
