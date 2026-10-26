# When not to use it

xcheck is slow on purpose. It splits a review into many small agent sessions, makes a human decide what is worth fixing, and has a second session check every fix. That is a good trade for a large body of work that has to be right. For many other jobs it is the wrong one.

This page is about those jobs. Its statements about containment describe `orchestrated` sessions, the ones `xcheck next` and `xcheck loop` start (see [launch mode](README.md#launch-mode)). A session started by hand is `uncontained-direct`: an agent you gave a role's instructions to yourself, with none of xcheck's controls around it. Where a limit below gets worse in that mode, the section says so.

## The short answer

| Situation | Why xcheck fails there | Use instead |
|---|---|---|
| One file, one question, a quick look | each finding costs several sessions and a human sitting | one agent session, or a review by a colleague |
| No written rules to check against | a complaint with no [norm](README.md#norm) behind it is capped at `info` | write the rules first, or do an open-ended review |
| Nobody has time to [triage](README.md#triage) | the loop stops and waits for a human | a one-off report, read as input to your own decisions |
| You need the answer in minutes | sessions run one after another, and you cannot steer one mid-way | an interactive agent session |
| Untrusted code, no docker | the default profiles do not isolate the machine or the network | the `container` profile (docker per session, defined below), or not at all |
| Production, a required CI gate, regulated evidence | xcheck is a supervised instrument, not an authority | your existing review and release process |
| The provider refuses the subject | moderation stops the sessions | another provider for those dimensions, or a human |
| Two opinions needed, one provider | a second session of the same model is a weak second opinion | a second provider, or a human Verifier |
| A small, fixed budget | one pass here cost about a quarter of a million tokens, and a full audit is many passes | one pass, measured, before committing to more |
| The effects you fear are outside the repository | xcheck sees the repository, not the emails or API calls a session made | no credentials in the session, and no retries |
| A Windows machine | the locking, process-group kill and resource limits are POSIX | macOS or Linux |

Each row has its own section below.

## The job is too small

**Example.** You changed one function and want someone to look at it before you merge.

xcheck's unit of work is a [session](README.md#session), and a single [finding](README.md#finding) passes through several of them: an Auditor finds it, you triage it, a Remediator fixes it, a Verifier judges the fix. Before any of that, a Planner session writes the plan and you read it. On xcheck's own self-audit, a median session took 427.5 seconds. For one function, the planning alone costs more than the review.

The README's rule of thumb sizes one [pass](README.md#pass) at about two thousand lines of code, or three chapters of text. If the whole job is smaller than one pass, the structure has nothing to organise.

**Use instead.** One agent session with the diff, or a colleague's review. If you want a second opinion on that, ask a second agent separately.

## There is nothing to measure against

**Example.** You want an agent to "make the code better" in a project with no style guide, no specification and no tests.

xcheck checks material against a [norm](README.md#norm): a written rule, a sourced fact, or another passage of the same material that contradicts it. A finding that names no norm is an opinion, and its severity is capped at `info` (`XCHECK.md` §6). Without norms, an audit produces a pile of `info` findings that nobody is obliged to act on, at the full price of the cycle.

The Planner can find norms you did not know you had: a header comment that states a contract, an architecture decision, an earlier chapter. It cannot invent them.

**Use instead.** Write down the two or three rules you care about, then audit against them. For open-ended improvement, an exploratory session is cheaper and more honest about what it is.

## Nobody can do the triage

**Example.** You want to run xcheck overnight on a repository, and nobody will look at the results for a month.

[Triage](README.md#triage) is the one routine decision xcheck does not make for you. You read each finding and decide `accepted`, `rejected` or `deferred`; nothing gets fixed until you do. The loop stops for triage once `triage_batch_cap` findings are waiting (20 by default). When the cap was 0, which means unbounded, on this project, the result was 133 findings reported and none triaged: a lot of tokens spent on a backlog nobody could use.

Setting the cap to 0 only removes the stop. The Auditor keeps filing findings, and they wait for a human just the same.

**Use instead.** If the goal is a report, run the Planner and a few passes, stop, and treat the findings as an advisory list. **Advisory** means you read it as input to your own decisions, not as a verdict. If the goal is fixes, wait until someone can triage.

## You need an answer now

**Example.** Production is broken and you want help finding why.

The loop runs one session at a time, and each one may take many minutes. You cannot talk to a session while it runs: it is a child process, not a conversation. To change what it does, you change the record with a command and wait for the next session ([README §7](../../README.md#7-launchers-skills-for-codex-and-claude-code-commands-for-opencode)). That is what makes the run reproducible, and it is the opposite of what you need in an incident.

**Use instead.** An interactive agent session, a debugger, and the people who know the system. That session is `uncontained-direct` by nature, so give it no more access than you would give a stranger at your keyboard.

## The code is hostile and you have no container

**Example.** You want to audit a third-party library you depend on, and docker is not available on your machine.

The files in an audited project are untrusted input: a comment can be written to talk the agent into doing something else. xcheck cannot prevent that; it can limit the damage. Only the `container` [sandbox profile](README.md#sandbox-profile) isolates the machine. The defaults are `readonly` for the Auditor and Verifier and `worktree` for every other role, and they isolate the repository. Under them, the agent can still:

- reach the network, to fetch and to post;
- read your home directory;
- run any program on your `PATH`.

The **container profile**, `sandbox_profile=container` in your operator profile, runs each session inside docker with no network, an empty home directory and the source mounted read-only. It needs a working docker and an image that carries your agent. Read [`SECURITY.md`](../../SECURITY.md#what-xcheck-does-not-do) for exactly what each profile holds.

Under `uncontained-direct`, there is no profile at all: the agent runs as you, in your project.

**Use instead.** For code you only half trust, such as a dependency or a vendor drop, set up the container profile first; Pattern 5 of the [usage patterns](usage-patterns.md#pattern-5-code-you-did-not-write-in-a-container) walks through it. If you cannot, do not point an agent at the code. For untrusted client code, meaning code a customer handed you that neither of you fully controls, see the next section: `SECURITY.md` says not to use xcheck on it today, container or not.

## Production, CI gates and regulated evidence

**Example.** You want xcheck to fix production code on its own, or to block merges in CI, or to produce evidence for an audit under a regulation.

`SECURITY.md` names five uses to avoid, today. Do not use xcheck as:

- an autonomous remediator on a production repository;
- a tool pointed at untrusted client code;
- a multi-tenant service;
- a required CI gate;
- evidence in a regulated or safety-critical process.

The reason is the same for all five. xcheck is a supervised instrument. It is designed to run on a clone or a dedicated branch, with no production credentials in the shell. Every courier diff is read before it is merged, and xcheck's conclusions are treated as advisory. The full list and the matching safe setup are in [`SECURITY.md`](../../SECURITY.md#running-it-safely).

**Use instead.** Your existing review and release process, with xcheck's findings as one input to it.

## The provider will not do the work

**Example.** You audit C code for memory safety, and the provider's moderation keeps cutting the sessions off.

This happened in a pilot. A provider's moderation interrupted defensive memory-safety review sessions five times, first during verification and then for a whole audit pass. Disclaimers in the prompt did not help. The fix took one line of configuration: the Auditor and Verifier for that work were switched to another provider. The cost was independence: the report recorded that it fell to "different fresh sessions" (`docs/pilot-2-report.md`, in Russian).

**Use instead.** Map the refused dimensions to a provider that accepts the work, and record what that does to independence. If no provider will do it, that dimension needs a human reviewer.

## You need two independent opinions and have one provider

**Example.** Your organisation allows exactly one AI vendor, and you want the fixes checked by someone who is not the fixer.

xcheck records how independent each verdict was, from the records of the two sessions: its [independence level](README.md#independence-level). A Verifier from the same provider as the Remediator gets `same-provider-different-session` at best, and `xcheck metrics` marks that level as degraded. It is still sound, because the Verifier is a fresh session that never saw the fix, but both sessions share one model's habits.

Even `cross-provider`, the best level, has a ceiling. It does not prove the two models were not trained on the same data, or that the same person did not write both prompts.

**Use instead.** If independence is the point, add a second provider for the Verifier, or make a human the Verifier for the findings that matter.

## The budget is fixed and small

**Example.** You have a few dollars of API credit and a large repository.

On this repository, the passes that were measured cost from 197,699 to 275,469 tokens each. That figure rests on 7 of 39 passes. In words: one look at about two thousand lines costs about a quarter of a million tokens and several minutes, and a full audit is many such looks plus the remediation and verification behind them. On your project the cost is unknown until you measure it.

xcheck can stop at a ceiling in tokens, but it cannot promise a sum of money. It has no way to see what you are billed, and the session already running may go over (see [Performance](performance.md#put-a-ceiling-on-spend)).

**Use instead.** Run the Planner and one pass. Read `xcheck metrics`, which prints the token cost per change and how many sessions were measured. Then decide whether the rest is worth it, or audit only the part that matters most.

## The effects you care about leave the repository

**Example.** The agent session has credentials for a ticket system, a mail server or a cloud account.

Everything xcheck checks is in the repository: the record, the files, the commits. A session that sent an email, opened a pull request, called an API or spent money has done something xcheck cannot see and cannot undo. This matters most for retries. If a session's connection drops and you allowed retries with `retry_limit`, the same charter runs again, including whatever it did outside the repository the first time. That is why retries are off by default.

Outside the `container` profile, the session also has the machine's full network access.

**Use instead.** Keep credentials out of the session: the environment is built from an allowlist, so do not add them to `env_allowlist` in your operator profile. Keep `retry_limit` at 0. If the work needs those systems, it is not an audit; do it yourself.

## What to use instead

When xcheck is the wrong tool, one of these is usually the right one:

- **A linter or a static analyser** for rules a machine can check exactly. It is faster, cheaper and deterministic. A class finding's strategy can end in exactly that: adding an automatic check.
- **Tests** for behaviour you can specify. A test that fails on the defect is a better guard than any reviewer.
- **One agent session** for a quick look, a question or a small change.
- **A human reviewer** for judgement, for the dimensions a provider refuses, and for decisions that need someone accountable.
- **Your organisation's own process** for production, release gates and regulated evidence.

xcheck is for what is left: a body of work too large to review in one sitting, with written rules to check it against, and a person who can make the decisions. If that is your job, start with [Pattern 1](usage-patterns.md#pattern-1-first-audit-of-a-code-repository) or the [README quickstart](../../README.md#6-quickstart-your-first-audit).

Previous: [Performance](performance.md) · [Guide index](README.md)
