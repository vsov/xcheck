# The xcheck guide

This guide explains how xcheck works and how to use it well. The [README](../../README.md) gets you installed and through a first audit. This guide is for what comes after: why the method is shaped the way it is, what happens inside a run, and how to get good results from it without wasting time or money.

| Read | It answers | Best for |
|---|---|---|
| [The methodology](methodology.md) | Why does xcheck work this way, and what does each rule protect against? | Everyone, after the README quickstart |
| [How it works](how-it-works.md) | What happens when I type `xcheck next`? | Operators who run audits |
| [Usage patterns](usage-patterns.md) | What do I type in my situation? | Operators who run audits |
| [Performance](performance.md) | How do I make an audit faster and cheaper? | Operators who run audits |
| [When not to use it](when-not-to-use.md) | Is xcheck the right tool for this job at all? | Anyone deciding whether to start |

Two other documents sit beside this guide and outrank it:

- [`XCHECK.md`](../../XCHECK.md) is the methodology itself. It is the only methodology document the agents read (per-project detail comes from `AUDIT.md` and each session's capsule), and it is copied into every audited project. If this guide and `XCHECK.md` ever disagree, `XCHECK.md` is right and the guide has a bug.
- [`SECURITY.md`](../../SECURITY.md) is the trust model. It says what xcheck protects you from, and what it leaves to you. Read it before you point xcheck at code you did not write.

## Glossary

Every term below is used across the guide. Where a term is about containment, it describes an `orchestrated` session, meaning one that `xcheck next` or `xcheck loop` started. A session you start by hand is `uncontained-direct` and gets none of those protections. Entries follow the life of an audit, from the project to the session that checks a fix. Each ends with the place where the full rule lives; "README" there means the [project README](../../README.md).

### orchestrator

The `xcheck` command, run as `xcheck next` (one session) or `xcheck loop` (sessions until a human is needed). It gives each session its charter, sandbox and envelope, then commits or quarantines its work. Source: README §8.

### audit

One complete run of the method over one project: plan it, look for problems, decide which to fix, fix them, check the fixes. An audit lives in the `audit/` directory inside the project it examines. Source: `XCHECK.md` §1.

### subject

The project being audited: a code repository, a book manuscript or a documentation set. xcheck treats everything in it as untrusted input, because a file in it can be written to talk an agent into misbehaving. Source: README §8, *Execution model*.

### norm

A written rule the material is measured against: a style guide, a specification, an architecture decision, a sourced fact, or another passage of the same material that contradicts it. A complaint with no norm behind it is an opinion, and its severity is capped at `info`. Source: `XCHECK.md` §6.

### dimension

One angle of inspection, such as terminology, error handling or memory safety. Every dimension is backed by a norm. Source: `XCHECK.md` §2.

### unit

One addressable piece of the material: one file, or one section of continuous text. Source: `XCHECK.md` §2.

### unit map

The list of all units in the project, written by the Planner. A file that is in no unit is invisible to the audit, so a gap in the unit map is a gap in coverage. Source: `XCHECK.md` §3, *Planner*.

### pass

One inspection of one dimension over a batch of units, done by the Auditor in one session. A pass ends with a report that says what it covered and what it did not. Source: `XCHECK.md` §3, *Auditor*.

### charter

The exact job a session is given: which role, which units or findings, and when to stop. For example: "dimension *terminology*, chapters 4–6, stop after a set number of findings." A session without a charter must refuse to start. Source: `XCHECK.md` §4 rule 1.

### session

One run of one agent on one charter. Every session starts from a blank memory and learns the state of the audit only from files. Source: `XCHECK.md` §4 rule 2.

### role

A job in the cycle. Any capable agent can play any role, with one hard exception: the Verifier is never the session that wrote the fix. Source: `XCHECK.md` §3.

### Planner

The role that inventories the project and its norms. It writes `AUDIT.md`: the dimensions, the unit map and the queue of passes. Source: `XCHECK.md` §3, *Planner*.

### Auditor

The role that runs passes and files findings. It never fixes anything. Source: `XCHECK.md` §3, *Auditor*.

### Triage

The human step. You read the findings and decide, for each one, `accepted`, `rejected` or `deferred`. It is the one routine decision the cycle needs from you. Source: `XCHECK.md` §3, *Triage*.

### Remediator

The role that fixes accepted findings, always in the same order: validate, census, plan, fix, self-check. Source: `XCHECK.md` §3, *Remediator*.

### Verifier

The role that judges each fix and gives a binding verdict, `closed` or `reopened`. Its standing instruction is to prove the fix wrong, not to confirm it. Source: `XCHECK.md` §3, *Verifier*.

### finding

One reported problem, in one file, with its whole history inside: the evidence, the norm it breaks, how to check a fix, the fix, the verdict. Its id looks like `F-0042`. Source: `XCHECK.md` §2.

### class finding

A finding about a pattern rather than one instance of it, opened when a census finds the same defect in several places. Its id looks like `CF-0003`, and fixing it usually means fixing the norm that let the pattern happen. Source: `XCHECK.md` §8.

### status

Where a finding stands in its lifecycle, from `reported` to a terminal status such as `closed`. Each status has exactly one owner who may change it. Source: `XCHECK.md` §5.

### ledger

The index of all findings, one row each, in `audit/LEDGER.md`. It is a view rendered from `audit/state.json`, so editing it by hand changes nothing and makes every command refuse the tree until you undo it or run `xcheck render-views`. Source: `XCHECK.md` §2.

### `state.json`

The canonical record of the audit: statuses, limits, the pass queue, class membership, sessions. The ledger and every finding's header block are copies generated from it. Source: `XCHECK.md` §2.

### write verb

A command that changes `state.json`, such as `xcheck set-status` or `xcheck record-fix`. It is the only way machine state changes, and each verb checks that the caller's role is allowed to make that change. Source: `XCHECK.md` §2.1.

### evidence standard

The three things every finding must carry: a quote copied character for character from the material, a locator saying where it is, and the norm it breaks. A finding without them is invalid and never reaches triage. Source: `XCHECK.md` §6.

### locator

Where the quoted problem is. For code it is `file:line` plus the enclosing function or class; for text it is the file, the section and the quote. Source: `XCHECK.md` §6 rule 5.

### validation

The Remediator's first step: re-reading the source to confirm the finding before changing anything. A finding that fails it becomes `disputed` (not confirmed) or `obsolete` (already gone). Source: `XCHECK.md` §7.

### census

The Remediator's second step: searching the whole project for other copies of the same defect. If the count reaches the class threshold, the fix becomes a class finding. Source: `XCHECK.md` §8 rule 1.

### oracle

A small check a finding can carry: a command plus the exit code that means "the defect is gone". `xcheck run-oracle` runs it under the `container` profile, which needs docker, so a closure can rest on a result rather than on someone's reading. Source: `xcheck/oracle.py`; `CHANGELOG.md`, 0.9.6.

### durability

Whether a fix stays fixed. xcheck counts a closure as durable only when an oracle saw the defect absent again on newer material. Source: README §8, *Health checks*.

### independence level

How independent a verdict really was, computed from the records of the two sessions involved. From strongest to weakest: `cross-provider`, `cross-model`, `same-provider-different-session`, `unrecorded`; `same` is refused. Source: README §8, *Invocation envelope*.

### capsule

A short brief written for one session: its charter, the norms its dimension cites, the files it may touch and the findings already filed. It says openly that it is a subset of `XCHECK.md` and `AUDIT.md`, which stay the authority. Source: `XCHECK.md` §4 rule 3; `xcheck/capsule.py`.

### envelope

The record the orchestrator writes about every session it starts: which program and model, which charter, which sandbox, how long it ran, the digest of its log. An agent cannot write its own envelope. Source: README §8, *Invocation envelope*.

### sandbox profile

The containment a session runs under: `worktree`, `readonly`, `none` or `container`. By default the Auditor and Verifier run `readonly` and every other role `worktree`; `container` is opt-in and needs docker. Only `container` gives operating-system isolation; the others isolate the repository, not the machine. Source: README §8, *Execution model*; `SECURITY.md`.

### courier

The orchestrator's step that commits a successful session's changes. It commits only the files that session changed, never your own uncommitted work. Source: README §8, *Courier commits*.

### quarantine bundle

Where the work of an unfinished session goes. Any outcome other than `ok` has its changes saved under `audit/quarantine/` and applied to nothing, so a crashed session cannot change your project without your seeing it. Source: README §8, *Execution model*.

### outcome

How a session ended, always exactly one of a fixed set: `ok`, `refused`, `blocked`, `provider-error`, `timeout`, `crash`, `cancelled` or `stalled`. Source: `xcheck/util.py`.

### lease

The lock that lets only one writing session run at a time. It is a directory, `audit/.lock`, with a heartbeat the orchestrator renews; a lock whose heartbeat stopped can be reclaimed safely. Source: README §8, *Lock protocol*.

### claimed stop

A session's way of saying "I cannot do this charter": it writes a small file naming the reason and the target, then exits. The target (the finding or pass the charter named) stays halted until you lift it with `xcheck resolve-stop`. Source: `XCHECK.md` §5, *refusal*.

### launch mode

Whether a session runs with the orchestrator's controls (`orchestrated`) or without them (`uncontained-direct`). Every writing launcher (the `/xcheck-*` skills and commands that `install-launchers.sh` installs, README §7) goes through the orchestrated path; only the read-only status launcher is direct. Source: `XCHECK.md` §4 rule 7; `SECURITY.md`.

### operator profile

Your machine's own settings file, kept outside every audited project: which commands run each role, the sandbox profile, timeouts, limits. It is required once per machine, and `xcheck next` refuses without it; `./operator-profile.sh` writes one (README §5.3). Keeping it outside means the project under audit cannot set the terms of its own audit. Source: README §5.3 and §8, *orchestrator.conf*.

### `⚠ needs-human`

The flag a finding gets when its fix was reopened too many times in a row. The automatic cycle stops on it and waits for your decision. Source: `XCHECK.md` §9 rule 2.
