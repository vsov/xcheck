---
name: xcheck-triage
description: Bring the person to the xcheck triage gate and hand them the exact commands for the decisions they make - the wrapper presents, the human decides and records. Use when the user invokes $xcheck-triage or /xcheck-triage, says "triage the findings", or wants reported findings accepted, rejected or deferred.
---

# xcheck launcher — Triage (wrapper)

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

Triage is the one gate the orchestrator does not dispatch. A triage decision is a
human-owned transition (§5), and `write.authorize_dispatch_write` allows it only when no
session is open — so there is no contained session to hand this to, and this wrapper does
not try to invent one. It brings you to the gate and gets out of the way.

0. **Are you already the session?** If your own prompt carries an `Orchestration context` line, the orchestrator dispatched you and you ARE the session this launcher would have started. This launcher does not apply to you: follow your role card in `audit/XCHECK.md`, and ignore every step below — running them would ask the orchestrator to dispatch a session inside a session, and the writing lock your own parent holds would refuse it.
1. **Preflight.** `audit/XCHECK.md` and `audit/state.json` must exist in the current
   project. Missing → stop and point the person at the xcheck repository's `bootstrap.md`
   / `install.sh`. Read `audit/XCHECK.md` §2, §3 (the Triage role card) and §5 (the
   lifecycle) before presenting anything: what you may present and what the human may
   decide are both defined there.
2. **Reach the gate.** Run `xcheck status`. If the decision is not `stop-triage` the
   orchestrator has other work queued first — say what it is, name the launcher that fits
   it, and stop. `xcheck next` prints the same gate and dispatches nothing while it
   stands.
3. **Present the batch, compactly, in the person's language.** Group by severity, then
   dimension: id, title, unit, and a one-line gist of the evidence, opening the finding
   bodies to quote them. Reading is the whole job here. Class findings get their own
   presentation: pattern, census size, strategy rung, member ids.
4. **Let the person decide, one row at a time or in batches they state.** A batch
   statement applies exactly as stated and to nothing else. An ambiguous statement is a
   question, not an inference. An unstated row stays `reported`. You may give an opinion
   when you are asked for one, labelled as an opinion.
   The human makes every decision; you present, record, and never fill gaps with your own judgment.
5. **Hand over the commands — you do not run them.** For each decision the person states,
   give the exact line to run: `xcheck set-status <ID> accepted|rejected|deferred`, one
   per finding, character for character. Those three are the entire triage vocabulary; the
   verb refuses anything §5 does not allow from a row's current status, so a mistyped
   transition stops instead of landing.
   Never touch finding files: their frontmatter is a generated mirror of `audit/state.json`, and `xcheck set-status` is the only thing that moves a status. A rejected class finding also reverts every member
   in its `members:` list to `accepted` — one line per member.
6. **Close** with a written summary: which rows the person decided and what they decided,
   which rows they left undecided, and the next decision (`xcheck status`).

## What this wrapper cannot do

It writes nothing at all — not the statuses it just helped decide. Triage moves findings
between states that only a human may move them between, so the commands go to the person,
who runs them at their own terminal where there is no open dispatch to authorize. That is
not friction for its own sake: an agent that could record triage decisions is an agent
that could record decisions nobody made.
