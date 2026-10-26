# Usage patterns

This page shows how to use xcheck in the situations you are most likely to meet. Each pattern is a recipe: when it applies, what to type, what you will see, and what tends to go wrong. Every command below runs `orchestrated` sessions unless it says otherwise (xcheck launches them, inside a sandbox, with a timeout); a session you paste into an agent yourself is `uncontained-direct` (no sandbox, no record), and [How it works](how-it-works.md#two-launch-modes) explains the difference.

The quickest way to see what a pattern does is to ask xcheck before it does anything:

```bash
xcheck --project /path/to/your-project --dry-run next
```

On a test project in mid-audit, with one accepted finding, that printed (on a fresh project it names a Planner session instead):

```
=== Remediator session ===
charter: finding F-0003
sandbox: worktree — a disposable git worktree in a system temp dir; changes return through the courier
command: claude -p [+prompt]
dry-run: not executing.
```

It names the role, the charter, the sandbox and the exact command it would run, and then stops. When a pattern below surprises you, run that first.

## How to read a pattern

Each pattern has four parts:

- **When**: the situation it fits.
- **Do**: the commands, one per block, ready to copy. `xcheck` means the orchestrator: either `bin/xcheck` from the repository, or the `xcheck` on your `PATH` after `./install-launchers.sh orchestrator`.
- **You will see**: what the tool prints, quoted from the tool itself.
- **Watch out**: the mistake that pattern invites.

Commands assume you set `XCHECK_OPERATOR_PROFILE` once, as in Pattern 1, and that `/path/to/your-project` is the project you are auditing.

## Pattern 1: first audit of a code repository

**When.** You have a repository too big to review in one sitting (a few dozen files and up), and you want to know what is systematically wrong with it.

**Before you start.** Run the commands below from your clone of the xcheck repository. The shipped profile runs Codex for the Planner, Auditor and Verifier and `claude -p` for the Remediator, so both CLIs must be installed and signed in. If you have only one, edit the role lines in the profile after you create it (Pattern 4 shows the lines).

**Do.** Install the method into the project, with the code template:

```bash
./install.sh --type code /path/to/your-project
```

Create your [operator profile](README.md#operator-profile), once per machine, outside every project. `trusted` is for code your organisation wrote and reviews; for anything else, see Pattern 5.

```bash
./operator-profile.sh --trust trusted /path/to/operator.conf
```

```bash
export XCHECK_OPERATOR_PROFILE=/path/to/operator.conf
```

Ask whether every role's command and sandbox is ready, without starting anything. Each check prints one line; fix any line that refuses before you go on.

```bash
xcheck --project /path/to/your-project doctor
```

Run the [Planner](README.md#planner):

```bash
xcheck --project /path/to/your-project next
```

When it finishes, `xcheck status` reports `stop-migrate`: the plan waits for you. Read `audit/AUDIT.md`, the plan the Planner wrote: the dimensions, the unit map and the queue of passes. This is the cheapest moment to change the audit. Check two things:

- every dimension names the norm it checks against; a dimension with no norm will only produce opinions;
- each pass is small enough: the [README](../../README.md#step-1-plan)'s rule of thumb is about two thousand lines of code, or three chapters of text, per pass.

Delete the lines for passes you do not want. Then preview the load. The dry run writes nothing and prints what it would load:

```bash
xcheck --project /path/to/your-project --dry-run migrate
```

If it reads right, load it:

```bash
xcheck --project /path/to/your-project migrate
```

For a first run, step through the loop so you see each session before the next one starts (Pattern 3):

```bash
xcheck --project /path/to/your-project loop --step
```

**You will see.** The loop runs Auditor passes, then stops at the first routine gate, [triage](README.md#triage), where you decide which findings are worth fixing: `gate: triage needed — 2 findings in reported.`, with the real count. Triage with `/xcheck-triage` (Claude Code) or `$xcheck-triage` (Codex), then run `loop` again. Pattern 10 lists the other stops.

**Watch out.** A repository of forty thousand lines is about twenty passes by the rule of thumb, and every finding then costs a Remediator and a Verifier session too. Read [Performance](performance.md) before you let the loop run unattended. After `migrate`, the queue changes only through verbs such as `xcheck amend-pass` and `xcheck cancel-pass`.

## Pattern 2: auditing a book or a documentation corpus

**When.** The material is prose: a manuscript, a series of reference volumes, a documentation site. You care about consistency, terminology, factual accuracy, broken cross-references.

**Do.** The same as Pattern 1, with the text template (which is also the default):

```bash
./install.sh --type text /path/to/your-corpus
```

**You will see.** The same cycle. The [units](README.md#unit) are sections or chapters instead of files, and [locators](README.md#locator) are a file, a section and the quote.

**Watch out.** Three things differ from code.

- **Norms are often the material itself.** A glossary, a style guide or an earlier chapter that states a fact is a norm. Make sure the Planner lists them; without them, most findings sink to `info`.
- **There is usually no [oracle](README.md#oracle).** "The paragraph now reads correctly" is not a command, so closures rest on the "how to verify" procedure and the Verifier's reading. That is normal for text. It does mean no closure will reach the `re-observed` evidence level.
- **Quotes stay in the material's language.** Findings and reports are written in your working language, but every quote is copied exactly, in whatever language the source is in (`XCHECK.md`, opening section).

## Pattern 3: supervised loop, one step at a time

**When.** You are new to xcheck, or new to a project, and you want to watch every session before the next one starts.

**Do.** See the next decision without running it:

```bash
xcheck --project /path/to/your-project --dry-run next
```

Run the loop, pausing after every session:

```bash
xcheck --project /path/to/your-project loop --step
```

Or cap the number of sessions:

```bash
xcheck --project /path/to/your-project loop --max-sessions 3
```

**You will see.** With `--step`, the loop pauses between sessions and waits for you. With `--max-sessions`, it stops after that many sessions even if no gate was reached.

**Watch out.** Read each session's diff before you let the next one run; that is the point of stepping. A quarantined session shows up in `xcheck status` as a pending bundle. Look at it before you decide it does not matter.

## Pattern 4: two providers for a real second opinion

**When.** You want the Verifier to run on a different provider from the Remediator, so the verdict does not come from the model that wrote the fix.

**Do.** Give the roles different providers in your operator profile. The shipped template already does this: Codex for the Planner, Auditor and Verifier, Claude for the Remediator. The lines look like this:

```
auditor_cmd=codex exec ...
verifier_cmd=codex exec ...
remediator_cmd=claude -p ...
```

Then check what you got:

```bash
xcheck --project /path/to/your-project metrics
```

**You will see.** `metrics` lists how your verdicts spread across the [independence levels](README.md#independence-level), and marks `same-provider-different-session` and `unrecorded` as degraded.

**Watch out.**

- **A provider may refuse the work.** In one pilot on a C codebase, a provider's moderation repeatedly cut off defensive memory-safety review sessions. The fix was one line of configuration: run that dimension's Auditor and Verifier on another provider. The cost was independence, which dropped to "different fresh sessions", because the Remediator already ran on that provider; the report said so.
- **The provider is recorded, not guessed.** If xcheck cannot tell which provider a command runs, it records `unknown`, and two `unknown` sessions never count as different providers.
- **One vendor still works.** It is the weakest setup that is still sound. Insist on the minimum: the Verifier is a fresh session that never saw the fix.
- **A Codex session may not be able to commit.** Codex's own settings can stop it writing to `.git`, so a Codex Verifier sometimes cannot commit its own verdict. Under `orchestrated` that does not matter: the courier commits the session's changes for it.

## Pattern 5: code you did not write, in a container

**When.** The repository comes from someone else, such as a vendor or an open-source dependency. Its files may be written to manipulate the agent reading them.

**Do.** Create the profile for untrusted material. This sets `sandbox_profile=container`, so docker must work on the machine:

```bash
./operator-profile.sh --trust untrusted /path/to/operator.conf
```

The default container image has no agent program in it. Build one; for Codex the repository ships a recipe:

```bash
docker build -t xcheck-codex images/codex
```

Pin it by its full image ID:

```bash
docker image inspect -f '{{.Id}}' xcheck-codex
```

Put that ID in the profile as `container_image=sha256:<64 hex>` (not `xcheck-codex@sha256:…`, which a Linux docker engine tries to pull from a registry). Then check that every role is ready before dispatching:

```bash
xcheck --project /path/to/your-project doctor
```

**You will see.** `xcheck status` prints the profile in force on its `sandbox:` line, and says whether the last session's containment was verified at launch.

**Watch out.**

- **This is not clearance for a client's code.** `SECURITY.md` still lists untrusted client code among the uses to avoid today. The container profile reduces what a hostile file can do. See [When not to use it](when-not-to-use.md#production-ci-gates-and-regulated-evidence).
- **Containment limits damage; it does not prevent manipulation.** An injected instruction may still succeed. Under `container`, the session cannot reach the network, an unlisted secret or your home directory, cannot outlive its timeout, and arrives as a diff you review. Read [`SECURITY.md`](../../SECURITY.md) first.
- **If the agent needs the network**, allow exactly the hosts it needs with `egress_allowlist` (for Codex, `api.openai.com`). [`docs/codex-provider.md`](../codex-provider.md) walks through the whole path.
- **A live Codex session under `container` has not been verified yet.** The path is tested against a fake image that writes the same event shapes. A real paid session has not been observed.

## Pattern 6: several passes at once

**When.** The queue has many passes over separate parts of the project, and you want them to run side by side.

**Do.** In the operator profile, turn on parallel passes and set the limits:

```
parallel_passes=on
parallel_workers=2
max_sessions_per_run=8
```

Then run the loop. Above `parallel_confirm_above` passes (4 by default) a fan-out needs your explicit word, either `yes` at a terminal or the flag:

```bash
xcheck --project /path/to/your-project --yes loop
```

**You will see.** Only queued Auditor passes whose unit sets do not overlap run together. Their results are merged into the record one at a time. If a pass fails to merge, the run stops with `stop: 1 of 3 parallel passes did not merge (P-04) — investigate before continuing` (with the real counts and ids).

**Watch out.**

- **Concurrency multiplies cost.** Two workers spend tokens twice as fast. `max_sessions_per_run` (8) and `parallel_budget_minutes` (480, priced as sessions × `session_timeout`, the worst case) are checked before anything starts, because a limit enforced after the work would cancel sessions you already paid for.
- **Without a terminal, it refuses rather than asks.** In a cron job there is nobody to type `yes`, and a prompt there would hang while holding the lock. Pass `--yes` deliberately or keep fan-outs small.
- **Only audit passes run in parallel.** Remediation and verification stay one at a time, because they change the same records.

## Pattern 7: picking up an interrupted audit

**When.** A run was killed, the machine rebooted, or you are taking over an audit someone else started.

**Do.** Start with the state, which is always on disk:

```bash
xcheck --project /path/to/your-project status
```

If a lock is left over, ask whether it is stale. Without `--force`, this never removes a lock whose owner is alive:

```bash
xcheck --project /path/to/your-project unlock
```

Check the event log. This only reports:

```bash
xcheck --project /path/to/your-project recover
```

If the report names events xcheck itself reserved and failed to write, put back exactly those:

```bash
xcheck --project /path/to/your-project recover --apply
```

Then carry on:

```bash
xcheck --project /path/to/your-project next
```

**You will see.** `status` lists the queue, the findings by status, any pending quarantine bundles, and the next decision. A lock whose heartbeat stopped more than `lease_ttl` seconds ago (300) is reclaimed by plain `unlock`.

**Watch out.**

- **Look in `audit/quarantine/` before moving on.** Each bundle is a session that did not finish. Its manifest says why, and its note says how to apply or discard it.
- **`unlock --force` is you vouching** that no writing session is running anywhere. Use it only when you are sure.
- **Resuming works because the state is all in files.** In one pilot the cycle picked up an operator's interrupted manual audit: five accepted findings and a finished first pass. It carried on without losing anything.
- **`recover --apply` never invents an event.** It replays only what xcheck recorded that it meant to write. A gap it has no record of stays visible; xcheck never fills it with a guessed event.

## Pattern 8: auditing again after the code moved

**When.** The project has changed since the last audit: new commits, a release, a refactor. You want to know what still stands and what is new.

**Do.** First, see which open findings still match the code. This writes nothing and proposes commands for you to run:

```bash
xcheck --project /path/to/your-project reconcile
```

Decide each proposal yourself. Triage decisions are yours, for example:

```bash
xcheck --project /path/to/your-project set-status F-0031 rejected
```

Then queue fresh passes over the parts that changed. `--base` records which revision the pass looks at changes from, so the record never mistakes it for a full sweep (a pass over everything, with no `--base`):

```bash
xcheck --project /path/to/your-project queue-pass P-07 --dimension error-handling --units U03,U04 --charter "re-check error handling in the reworked loader" --stop "the units are exhausted" --base v1.4.0
```

**You will see.** `xcheck status` prints a `full sweep:` line: how long ago the last pass with no `--base` finished.

**Watch out.**

- **You choose the units.** xcheck does not yet pick units from a diff. A diff-scoped selector exists in the code but is experimental and switched off, and its configuration key is refused ([`docs/experimental.md`](../experimental.md)). `--base` labels a pass; it does not narrow it.
- **A re-audit of changed files never looks at the files nobody touched.** That is how it saves time, and it is also a blind spot. Watch the `full sweep:` age and run a full pass now and then.
- **A recurrence is a new finding.** If a defect you closed comes back, the Auditor files a new finding tagged `recurrence-of:`. The old one stays closed, so your history stays true (`XCHECK.md` §9 rule 8).

## Pattern 9: one bug, many copies — escalating to a class

**When.** The same defect shows up in several places, or you suspect it does.

**Do.** Nothing special: the Remediator's census does this on its own. When it finds the pattern in at least `class_threshold` places (3), it files a [class finding](README.md#class-finding), and the loop stops for triage. When the triage gate fires, xcheck also prints recurring-class candidates it noticed across findings. Those are proposals only; filing a class is a human's call after reading the members.

**You will see.** A new `CF-` finding at triage, holding the pattern, the search that found it, every instance, and a strategy: fix all instances; also fix the norm; also add an automatic check.

**Watch out.**

- **Accepting a class finding sanctions a change across the whole project.** Read its census and strategy before you accept it.
- **A class fix that changes a norm stops again**, at the norm-ratification gate, until you record a ruling with `xcheck record-ruling`. See [the methodology](methodology.md#fixing-the-disease-census-and-class-findings).
- **Rejecting a class sends its members back** to `accepted`, to be fixed one at a time.

## Pattern 10: when the loop hands the turn back

**When.** `xcheck loop` stopped and printed a line starting `gate:` or `stop:`.

**Do.** Read the line. It names what it is waiting for. The common ones:

| The loop says | What it means | What you do |
|---|---|---|
| `gate: triage needed` | Findings are waiting in `reported`. | Triage: `/xcheck-triage` in Claude Code, `$xcheck-triage` in Codex, or `xcheck set-status` per finding. |
| `gate: plan ready` | The Planner finished; the plan is not loaded yet. | Read `AUDIT.md`, then `xcheck migrate`. |
| `⚠ needs-human` | A fix was reopened too many times in a row. | Read the finding and its verdicts; decide. A sanctioned retry is `xcheck set-status F-0012 planned --retake human:<you>`. |
| a dispute | The Remediator could not confirm a finding; the Auditor objected once. | Decide; the Auditor records your decision. |
| a norm ruling | A class fix is blocked on a conflict between norms. | `xcheck record-ruling CF-0003 --norm N1 --ruled-by human:<you>`. |
| a claimed stop | A session said it cannot do its charter. | Fix what it named, then `xcheck resolve-stop F-0003 --resolution "<what changed>"`. |
| an open critical | A `critical` finding is still open; new audit passes wait. | Triage or fix it. Remediation and verification still run. |

**You will see.** The full list is in [README §8](../../README.md#8-the-orchestrator). Each halt explains what it expects.

**Watch out.** Do not clear a gate by editing a view. Editing `LEDGER.md` or a finding's header block changes nothing in the record, and every command refuses to run until the edit is undone or `xcheck render-views` overwrites it.

## Pattern 11: reading the results

**When.** You want to know how the audit is going, or how good its results are.

**Do.** The current state and the next decision:

```bash
xcheck --project /path/to/your-project status
```

The history and the quality figures:

```bash
xcheck --project /path/to/your-project metrics
```

Both, as one self-contained HTML page:

```bash
xcheck --project /path/to/your-project dashboard
```

The record's internal consistency:

```bash
xcheck --project /path/to/your-project lint
```

**You will see.** `status` prints the queue, the findings by status, their evidence levels and the next decision. `metrics` prints auditor accuracy, fix durability, independence levels and token cost per state change. `lint` exits with status 1 if it finds an inconsistency. Every one of them only reads.

**Watch out.** Read the evidence level beside each status. A `closed` finding at `fixed` level was judged by someone; one at `re-observed` was checked again by an oracle on newer code. And a session with no token figure in its log is counted as unmeasured, not as free. [How it works](how-it-works.md#what-the-numbers-in-status-and-metrics-mean) explains each figure.

## Anti-patterns

- **Starting writing roles by hand.** Pasting `Read audit/XCHECK.md. Role: Remediator. …` into an agent runs an `uncontained-direct` session: no sandbox, no timeout, no record of what ran. Use `xcheck next` or a launcher skill, which hands off to it.
- **Changing `state.json` or a view by hand.** Nothing changes in the record, and the tool refuses the tree until you undo it. Every change goes through a verb.
- **Skipping the read of `AUDIT.md`.** A dimension that cites no norm can only produce opinions. Fix it before `migrate`, where it costs nothing.
- **Treating a green `xcheck selftest` as a safety claim.** It says the decision logic behaves. It says nothing about the project you pointed xcheck at.
- **Pointing xcheck at your only copy.** Run it on a clone or a dedicated branch, read every courier commit before you merge it, and keep `push_after_commit` empty so that publishing stays your act.
- **Letting findings pile up.** Triage little and often. `triage_batch_cap` (20) stops the loop for you; do not raise it to avoid the sitting.

Next: [Performance](performance.md) is about making all of this faster and cheaper.

Previous: [How it works](how-it-works.md) · [Guide index](README.md)
