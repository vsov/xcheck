# How it works

This page explains what xcheck does when you run it: where the state lives, how it decides whose turn it is, and what happens to one session from start to finish. Everything here describes an `orchestrated` session, meaning one that `xcheck next` or `xcheck loop` started. A session you start by hand, by pasting a role card into an agent, is `uncontained-direct`: none of the machinery below touches it. To run a first audit, start with the [quickstart](../../README.md#6-quickstart-your-first-audit); this page explains what that run does.

Here is what `xcheck status` printed for a throwaway test project with two passes and three findings. The output is real; long lines are cut with `…`, and the temp paths are shortened.

```
project: /tmp/…/xcheck-test-43y091yo  (xcheck 0.9.8)
state: schema 1, revision 1, head 0000000
passes: 1 done, 1 queued ['P-02']
findings: 3 total — accepted:1, reported:2
evidence levels: reported 2, confirmed 1, fixed 0, independently-verified 0, re-observed 0; manual 0 — durable = re-observed only
full sweep: the last unscoped pass was P-01 on 2026-08-14, 72 day(s) ago. …
budgets: off — no ceiling stops this run [budgets is off]
policy: /tmp/…/operator.conf  sha256:37e0db9f51d5a919
sandbox: worktree for writing roles, readonly for reading roles
  the above covers `orchestrated` sessions only — those `xcheck next` and `xcheck loop` launch.
  …
decision: run-remediator -> ['F-0003']
```

The output shows a record (`state`) with a revision number, a queue of passes, a set of findings with statuses, and an account of how well each finding is evidenced. It also shows your [operator profile](README.md#operator-profile), with its digest, saying how sessions will be run; [README §8](../../README.md#8-the-orchestrator) lists every key it can hold. And on the last line there is a decision: the next session should be a Remediator working on `F-0003`. The rest of this page explains each of those parts.

## Files are the only channel

xcheck has no server and no database. Agents do not talk to each other. Everything one session hands to the next is a file in the `audit/` directory of the project being audited.

Because of this, a session can start with a blank memory and still do the right thing. It reads the files it is told to read, does its job, and writes its results back as files. The next session reads those. If you stop for a week, nothing is lost: the state of the audit is whatever is on disk (`XCHECK.md` §4 rule 2).

It also means you can always look. Every finding is a Markdown file you can open. The ledger is a table you can read. When something seems wrong, the evidence is still in the directory after the conversation has scrolled away.

## The control plane: state.json and write verbs

The machine state lives in one file, `audit/state.json`: every finding's status, the pass queue, the limits, class membership, the sessions that ran. It is the control plane: the part the tool reads to decide what happens next.

The Markdown files a human reads are generated from it. The ledger, `audit/LEDGER.md`, and the header block at the top of each finding are views. If you change one by hand, nothing changes in the record. Worse, every command refuses to run until the disagreement is gone. You either undo your edit, or regenerate the views with `xcheck render-views`, which throws your edit away and lists what it overwrote (`XCHECK.md` §9 rule 4).

What the files *do* own is the human half: the evidence, the argument, the quote, the ruling. Those live in the body of each finding, below the generated block, and no tool rewrites them.

State changes only through a [write verb](README.md#write-verb), such as `xcheck file-finding`, `xcheck set-status`, `xcheck record-fix` or `xcheck record-verdict`. Each verb checks three things before it changes anything:

1. **Is this a legal move?** `accepted` can become `validated`; `reported` cannot jump to `closed`.
2. **Is this role allowed to make it?** Only the Verifier records a verdict. Only you triage.
3. **Is the record still what the caller thinks it is?** A verb that would overwrite someone else's change is refused.

A refused verb changes nothing and prints why. An agent that tries to close its own fix gets an error naming the rule.

## Whose turn is it: the decision engine

`xcheck next` decides what the cycle owes next, using only `state.json`. The part of xcheck that answers it is the decision engine (`xcheck/decision.py`). The answer follows a fixed priority ([README §8](../../README.md#8-the-orchestrator)). A new audit with no `state.json` first runs its Planner until the plan is complete, then stops for `xcheck migrate`. After that:

1. A finding that needs a human (`⚠ needs-human`) stops everything until you decide.
2. Fixes waiting for a verdict come next: the Verifier runs on `fixed` findings.
3. Then remediation: unfinished and reopened work first, then accepted class findings, then accepted findings.
4. Then new audit passes from the queue.
5. Then triage, when there is nothing else to do.

Started work is finished before new work is opened. That is why the example above says `run-remediator` even though two findings are still waiting for triage: there is accepted work to do first.

Two pressures can change the order. When too many findings pile up undecided (`triage_batch_cap`, 20 by default), the loop stops and asks for triage, so that later passes do not audit a project nobody has judged. And an open `critical` finding stops new Auditor passes until it is dealt with (`audit_over_critical` turns that off); remediation and verification of it carry on, because they are how it stops being open.

## Where the loop stops and hands back to you

`xcheck loop` runs sessions one after another, calling the decision engine each time, until it reaches a point where a person is needed. [README §8](../../README.md#8-the-orchestrator) keeps the complete list of these halt points, and the tool's own help text points there rather than keeping a second copy that could drift. The kinds of halt are:

- **A plan to load.** A new audit's plan is ready for `xcheck migrate`.
- **Your routine decision.** Triage is needed. This is the normal stop.
- **An exceptional decision.** A finding is `⚠ needs-human`; a fix is disputed; a class finding is blocked on a norm ruling.
- **A record that needs repair.** The ledger is inconsistent or corrupt, a coverage report is missing, a class finding's links are broken.
- **The work is done**, or done except for `deferred` findings, which are not finished.
- **Something is not converging.** Sessions keep producing the same decision without changing anything after `loop_progress_limit` such sessions in a row (2 by default).
- **A session error**, or the orchestrator's own code changing while it runs. That second one happens when a fix edited xcheck itself; restart the loop to run the new code.

Each halt prints what it expects from you, and the loop never works around a gate that wants a human.

## One session, end to end

Here is what happens between the decision and the next decision. Every box names the module that does it.

```
xcheck next
   │
   ▼
decision ........ who goes next, on which charter          xcheck/decision.py
   │
   ▼
capsule ......... the short brief for this one session     xcheck/capsule.py
   │
   ▼
envelope ........ record what is about to run              xcheck/envelope.py
   │
   ▼
lock + sandbox .. take the lease, build the sandbox        xcheck/runner.py
   │
   ▼
child agent ..... runs the role on its charter; changes state only through write verbs
   │
   ▼
outcome ......... ok | refused | blocked | provider-error | timeout | crash | cancelled | stalled
   │
   ├── ok ........ courier commits only this session's own changes   xcheck/courier.py
   └── other ..... quarantine bundle; nothing is applied             xcheck/runner.py
   │
   ▼
events .......... one line per transition, chained         xcheck/ledger.py
```

**The [capsule](README.md#capsule).** A session does not read the whole project. It gets a brief derived from `state.json` and the charter, with no model involved. The brief holds the charter's own words, the norms its dimension cites, the files it may touch with their hashes, the findings already filed against it, and its limits. On this project's own queue the largest capsule was 3,334 bytes, against 84,610 bytes of material it summarised (`CHANGELOG.md`, 0.9.1). The capsule says plainly that it is a subset. `XCHECK.md` and `AUDIT.md` stay the authority, and the session reads them when the capsule does not answer a question.

**The [envelope](README.md#envelope).** Before the child starts, the orchestrator writes down what it is about to run. That means the provider, the model, a hash of the program itself, the role, hashes of the charter and of the exact prompt, the [sandbox profile](README.md#sandbox-profile), and the commit before the session. After it ends, the envelope gets the duration, the exit status, the commit after, and the digest of the session's log. No write verb reaches the envelope. The orchestrator writes it and the session cannot change it. It is what the independence levels and the "verifier is never the fixer" check are computed from.

The program is identified by a hash of its own file, read without running it. Anything that cannot be determined is written as `unknown`, explicitly, so that a reader can tell a missing value from a real one. The fixer's identity is bound the same way: `record-fix` and `record-verdict` take the session id from the open dispatch record, and an agent that supplies a different one is refused. In a project audited by hand, with no envelopes at all, a fix still records who claims to have made it, but the verb labels that claim `provenance UNBOUND`, so it does not count as evidence.

**The child.** The agent CLI you configured for that role runs in the sandbox with its prompt. It reads its capsule, does its job, and records its results through write verbs. Its environment is built, not inherited: a short list of variables plus the ones you name in `env_allowlist`. Credentials held in environment variables are not passed down by accident. Files in your home directory are another matter: under the default profiles the child can read them, and only `container` hides them. Its output is scrubbed before the log is written: values of secret-looking variables it was given, and well-known token shapes. That scrubbing is a guard against accident, such as an agent echoing a key it was handed. It does not stop an agent that encodes or splits a secret on purpose; the allowlist is what bounds that, by not handing the secret over in the first place.

**The [outcome](README.md#outcome).** Every session ends in exactly one of eight outcomes. `ok` means it finished. The other seven say why it did not finish:

- the agent declined (`refused`);
- a gate stopped it (`blocked`);
- the provider failed (`provider-error`);
- it ran out of time (`timeout`);
- it died (`crash`);
- you cancelled it (`cancelled`);
- it went quiet past one of its deadlines (`stalled`).

**The [courier](README.md#courier).** If the outcome is `ok`, the orchestrator commits only the session's own changes. It compares `git status` before and after, so your own uncommitted work is never swept into an audit commit. If a file the session touched already had your edits in it, the courier does not try to split it. It leaves that file alone, names it, and the audit will not report itself finished until you commit or discard your edit (README §8, *Courier commits*).

## Sandbox profiles and what each one holds

The material under audit is untrusted. A README or a code comment in it can be written to talk the agent into something. So the sandbox limits what the agent can do, whatever its prompt says. The [sandbox profile](README.md#sandbox-profile) decides how.

| Profile | Default for | What it does | What it does not do |
|---|---|---|---|
| `worktree` | writing roles | Runs the session in a disposable git worktree in a temp directory, seeded with your uncommitted work. Changes come back as a patch, through the courier. | Isolate the machine. The child can read your home directory and reach the network. |
| `readonly` | Auditor, Verifier | The same worktree, but a change outside `audit/` is refused rather than applied. The patch is kept so no work is lost. | Isolate the machine, as above. |
| `container` | nobody; you opt in | Runs the session in docker: no network, an empty home directory, the material mounted read-only, one writable clone and one output directory, CPU, memory and process limits, all capabilities dropped, no docker socket. | Stop prompt injection. It limits what an injected agent can reach. |
| `none` | nobody | Runs the child in the project itself. A recorded opt-out for people who know why they want it. | Almost everything above. |

Five rules hold for every profile:

- **No silent fallback.** If the worktree cannot be created, or `container` is asked for and docker does not answer, the session is refused rather than run with less than you asked for.
- **The container image is pinned by digest.** A tag such as `latest` can change under you, and the protections were measured against specific bytes. An unpinned image is refused unless you waive that with `unsafe_allow_unpinned_image`, and the waiver is written into the session's log.
- **Skipping the agent's own permission prompts is allowed only inside isolation.** Flags such as `--dangerously-skip-permissions` are refused under `none` and accepted under an isolating profile. They are not banned outright: a ban would push people to run sessions outside xcheck, which removes every control at once.
- **What was enforced is recorded.** The envelope carries a capability report of what the profile set up, and `xcheck status` says whether the last session's containment was verified at launch.
- **A hard time limit.** Every session has a wall-clock limit (`session_timeout`, 3600 seconds by default). When it expires, the child's whole process group is stopped, so a program the agent started dies with it.

The default `container` image is a small pinned image with `sh`, `git` and busybox, and no agent program. To run real sessions under `container`, set `container_image` to a pinned image that contains your agent CLI; [`docs/codex-provider.md`](../codex-provider.md) shows one way.

What each profile withstood is measured by escape attempts, each run under `container` (where it must fail) and under `worktree` (where it succeeds), and recorded per row in [`SECURITY.md`](../../SECURITY.md). Read that table before you run xcheck on code you did not write.

## What happens when a session does not finish

Only an `ok` session's changes are applied. Any other outcome has its work captured into a [quarantine bundle](README.md#quarantine-bundle) under `audit/quarantine/`. The bundle holds the patch, a manifest (the outcome, the session id, the patch's hash, the files it touches) and a note on how to inspect, apply or throw it away. The record does not move. `xcheck cancel` asks a running session to stop in an orderly way: the orchestrator stops the child's process group, releases the lock, and applies none of the cancelled session's changes. `xcheck status` counts the bundles waiting for you.

Some sessions also die early on purpose. Four deadlines catch a session that is not working. It is killed and ends `stalled` if it has written nothing at all after `startup_deadline` seconds (120 by default). The same happens if it has written nothing beyond its own echoed prompt after `output_deadline` seconds (600), or done nothing observable after `activity_deadline` seconds (1200), or been silent for `idle_deadline` seconds (1200). These numbers were derived from real sessions: the fastest working session in one measured run finished everything in 439.3 seconds, and the one that stalled held a slot for 3600 seconds having written 1,764 bytes. A `stalled` session is never retried.

A session that failed because of the provider or a timeout may be retried, but only if you allow it (`retry_limit`, 0 by default) and only if the orchestrator can show nothing changed. Before a retry it compares the project with the snapshot it took before the session, minus its own bookkeeping, and refuses the retry if anything moved. It prints how many paths it compared, so "nothing changed" can never be the answer of a check that looked at nothing.

That check sees only the repository. A session that sent an email, opened a pull request, called an API or spent money before its connection dropped has done something xcheck cannot see and cannot undo. A retry runs the same charter again, which is why `retry_limit` is 0 unless you raise it.

## The lock is a lease

Only one writing session may run on a project at a time (`XCHECK.md` §4 rule 8). The orchestrator enforces this with a lock: the directory `audit/.lock`, created atomically, holding a record of its owner. A second writer that tries to create it gets an error and stops.

While a session runs, the orchestrator renews a heartbeat inside the lock. That makes it a [lease](README.md#lease) rather than a plain lock. If the heartbeat has stopped for longer than `lease_ttl` seconds (300 by default), the owner is gone, and `xcheck unlock` can reclaim the lock safely. A lock with a live heartbeat can never be reclaimed that way.

A lock with no heartbeat at all (from an older xcheck, or a session started by hand) falls back to checking whether its owning process is still alive. `xcheck unlock --force` removes any lock, and by using it you are stating that no writing session is running. That last step still has a small window in which a race is possible, which is why the README calls it a palliative.

## The record: events, hash chain, recovery

Next to `state.json` sits `audit/events.jsonl`, an append-only log. Every dispatch, every finished session, every applied state change, every gate reached and every lease taken or released adds one line. A refused verb changed nothing, so it adds nothing.

Each line carries the digest of the line before it, so the log is a hash chain: change or delete a line in the middle and every later link breaks. A chain alone cannot tell a log that was rebuilt from scratch from the real one, so `state.json` keeps an anchor: the digest the log had when the state last changed. A log that disagrees with that anchor is refused.

A state change and its event line are written as one unit in two steps. First the event goes to an outbox file and is flushed to disk; if that fails, the change is refused and nothing happens. Then the state is written, and the event is moved into the log. If the disk fills between those two steps, the outbox keeps the line and a later run puts it back. `xcheck recover` checks the log and, with `--apply`, restores only what it can prove this tool wrote (`xcheck/ledger.py`).

The log is committed with the rest of `audit/`, so it survives a `git clone`.

## When an agent says stop

Sometimes a session cannot do its charter: the material it needs is missing, two norms contradict each other, the job is outside its competence. It can say so in two ways (`XCHECK.md` §5, *refusal*).

It can record a refusal on the finding, with a typed reason and a written explanation. That leaves the finding's status alone and passes the reason to the next attempt.

Or it can stop. It writes a small JSON file with the reason, the target its charter named, and what is missing, and exits. The orchestrator reads that file even if every write verb refused. It checks that the target is one the session was given; a stop naming anything else is refused. Then it halts that target until you lift the stop with `xcheck resolve-stop F-0003 --resolution "the missing schema file is now in docs/"`, or until a write verb changes the finding.

A stop is a [claimed stop](README.md#claimed-stop): a claim you review before trusting it. Text a session prints in its log is never a stop; only the file counts.

## Oracles: re-running the defect

A finding may carry an [oracle](README.md#oracle): one small JSON block naming a command, the exit status that means "the defect is gone", a timeout, and the files it tests. The Auditor writes it when it files the finding, as the machine half of "how to verify the fix".

`xcheck run-oracle F-0003` runs that command only under the `container` profile, with no network, on a copy of the current material. It never runs a command from a finding on your machine directly. Without docker there is no oracle run; a first audit works fine under the default `worktree` profile, and its closures rest on the Verifier's reading, recorded with `--evidence manual:`. It records an `oracle_run` event: which oracle, which material, the exit status, and a verdict: `defect-present`, `defect-absent` or `error`.

Those events decide closure. `record-verdict --verdict closed` needs a `defect-absent` run on the current material, by an oracle that once recorded `defect-present` on earlier material. Without that, the operator must say `--evidence manual:"…"`, and the closure is labelled manual (`xcheck/oracle.py`).

## What the numbers in status and metrics mean

`xcheck status` is the current state and the next decision. `xcheck metrics` is the history: what the audit has achieved so far. Both have a `--json` form that is checked against a fixed schema before it is printed, so a script reading it never gets a field that is not there. `xcheck dashboard` renders the two into one HTML page that computes nothing of its own. The page loads no script, no font, no image and makes no network request: a page about a private audit has no way to phone home.

`xcheck lint` is the consistency check. It only reads, and it exits with status 1 when it finds a problem: an orphaned row or file, a broken class membership, a malformed record. Checks on a single record (a field present, a value in range) run through the same validator `next`, `status` and `metrics` use, so a value one command refuses can never reach another. Checks that compare one record with others, such as whether every member of a class finding exists, run only in `lint`, which keeps the decision path cheap.

The figures most worth reading:

- **Evidence level** of each finding: `reported`, `confirmed`, `fixed`, `independently-verified`, `re-observed`. Only `re-observed`, meaning an oracle saw the defect absent again on newer material, counts as a durable fix.
- **Auditor accuracy**: the share of accepted findings that survived validation. A low number means passes are filing findings that fall apart when someone checks them.
- **Independence**: how verdicts spread across the levels, with the degraded ones marked.
- **Token cost per canonical transition**: tokens spent per state change that happened, counted per change rather than per session. A run can make each session cheaper while making every result more expensive, and this figure shows that. A session whose log reports no token figure is counted as unmeasured, never as zero. xcheck never prints money: it has no billing data.
- **Full sweep**: how long ago the last pass looked at everything rather than a subset.

## Two launch modes

A [launch mode](README.md#launch-mode) is the way a session was started, and there are two. Everything on this page applies to `orchestrated` sessions. That is the mode with the controls: sandbox profile, hard timeout, environment allowlist, log redaction, process-group kill, courier review of the diff, the envelope, the session receipt.

The five writing launcher skills (`/xcheck-plan`, `/xcheck-audit`, `/xcheck-triage`, `/xcheck-remediate`, `/xcheck-verify`) are thin wrappers over `xcheck next`, so they are `orchestrated` too. Four of them hand the work to `xcheck next`; `/xcheck-triage` brings you to the triage stop and hands you the `xcheck set-status` lines to run yourself, because only you triage. The sixth, `/xcheck-status`, only reads; it moves no state, so there is nothing for the controls to guard.

A session you start yourself, by pasting `Read audit/XCHECK.md. Role: Auditor. Charter: …` into an agent, is `uncontained-direct`. It gets none of the controls, nothing records what was in force, and nothing attests what it did. You vouch for the work; the tool does not (`XCHECK.md` §4 rule 7). Use it only when you mean to.

Next: the [usage patterns](usage-patterns.md) put all of this to work on real situations.

Previous: [The methodology](methodology.md) · [Guide index](README.md)
