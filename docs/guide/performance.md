# Performance

This page is about making an audit faster and cheaper without making it worse. It covers `orchestrated` [sessions](README.md#session), the ones `xcheck next` and `xcheck loop` start. A session you paste into an agent by hand is `uncontained-direct`: none of the limits below apply to it, because xcheck never sees it run.

For a first audit, keep the defaults, run it, then come back with `xcheck metrics`. These three numbers show where tuning can help:

| What was measured | Figure | Where it comes from |
|---|---|---|
| xcheck's own work before a session | 3.30 s, or 0.77% of a median session | xcheck's self-audit, 0.9.6 |
| A median session | 427.5 s | the same corpus, 158 sessions |
| Tokens spent by sessions that changed nothing in the record | 85.4% | Ouroboros-4, xcheck's fourth audit of its own repository |

The first two put xcheck's own share under 1%. The third shows where the time goes: agent sessions that cost money and changed nothing.

## Where the time actually goes

Almost all of an audit's cost is agent sessions. A [session](README.md#session) is one run of one agent on one [charter](README.md#charter). The orchestrator around it (reading the state, building the brief, checking the result) takes about three seconds.

Each session is billed in tokens. The [passes](README.md#pass) this repository has measured cost from 197,699 to 275,469 tokens each, median 231,754. That figure rests on 7 of 39 passes, because most sessions did not report their usage. A pass is one Auditor session over one batch of units, so this is roughly the price of one look at one slice of a project.

To size your own audit, start from about two thousand lines per pass. A repository of forty thousand lines is then twenty passes. At the median, that is over four million tokens for one dimension, before any remediation or verification. Treat it as a floor.

Three levers act on sessions directly:

1. **Ask each session for less**, so it reads fewer tokens and finishes sooner.
2. **Run fewer sessions that change nothing.** In the Ouroboros-4 run, these were most of the bill.
3. **Stop a session early** once it is clearly not working.

The sections below cover those three, then triage, ceilings, parallel passes and model choice. Every setting lives in one of three places. It is either an `AUDIT.md` limit, changed after `migrate` with `xcheck set-limit`; a line in `audit/orchestrator.conf` (the project's file); or a line in your [operator profile](README.md#operator-profile), your machine's file, which `./operator-profile.sh` creates ([README §5.3](../../README.md#53-create-your-operator-profile-required-once-per-machine)). The table says which.

| Lever | Default | What it changes | Measured? | Risk if pushed too far |
|---|---|---|---|---|
| `max_findings_per_pass` (AUDIT.md limit) | 15 | findings one Auditor pass may file | declared | a small cap splits a pass into many sessions |
| `remediation_batch_size` (AUDIT.md limit) | 8 | findings one Remediator session works | declared | a large batch makes one session long and its failure expensive |
| `loop_progress_limit` (project) | 2 | stops the loop after N sessions that repeat the decision and change nothing | declared | 1 stops on a single unlucky session |
| `triage_batch_cap` (project) | 20 | stops for triage once N findings wait | measured: unbounded gave 133 reported, 0 triaged | 0 means "never stop for triage" |
| `startup_deadline`, `output_deadline`, `activity_deadline`, `idle_deadline` (profile) | 120, 600, 1200, 1200 s | kill a session that shows no sign of working | derived from the measured W-06 run | below the measured session lengths, they kill real work |
| `session_timeout` (profile) | 3600 s | hard limit on one session | declared | too low cuts working sessions; it also prices every worst case below |
| `embargo`, `embargo_after` (project) | `off`, 3 | holds a charter whose last N attempts moved nothing | measured on one replay | it also kills some productive sessions |
| `marginal_value_window`, `no_progress_share_pct`, `charter_repeat_limit` (project, with `budgets=on`) | 0 = off; `status` prints derived values 8, 50, 3 | stop the loop when sessions stop paying for themselves | derived from Ouroboros-4 | too tight stops a healthy audit |
| `tokens_per_session`, `tokens_per_pass`, `tokens_per_audit` (project or profile, with `budgets=on`) | 0 = off | token ceilings | `tokens_per_pass` derived as 300000 from 7 measured passes | a ceiling under normal spend stops normal work |
| `parallel_passes`, `parallel_workers` (profile) | `off`, 2 | runs disjoint Auditor passes at once | not measured | spends tokens faster; more to review at once |
| `retry_limit` (profile) | 0 | extra attempts after a transport failure | declared | a retry pays for the same session twice |
| `model_routing`, `cheap_model`, `strong_model` (project, profile) | `off`, none, none | sends simple work to a cheaper model | not measured | a weaker model on hard work |

"Declared" means the code enforces it but nobody has measured its effect on cost. "Measured" means a run was counted. "Derived" means the default was worked out from a measured run, and `xcheck status` prints the working.

## Measure first

Before you change anything, find out what your own audit costs. Two commands; neither changes anything:

```bash
xcheck --project /path/to/your-project status
```

```bash
xcheck --project /path/to/your-project metrics
```

`status` says whether budgets are on and which ceilings are armed, and prints every derived ceiling with the reasoning behind its number. When your operator profile sets a ceiling, it also prints the token account. `metrics` prints the token cost per state change: tokens spent, divided by the number of changes the sessions made to the record.

Two rules for reading these numbers:

- **Cost per change, not cost per session.** A session that ran for an hour and recorded nothing is cheap per session and infinitely expensive per change.
- **`unmeasured` is not zero.** A session whose agent reported no token count is counted as unmeasured. The spend is then a lower bound: the unmeasured sessions can only add to it. Every figure prints how many sessions were measured, out of how many finished. If most were not measured, treat the figure as a floor.

To get measured sessions, set `telemetry_adapter` in your operator profile. It accepts `codex`, `codex-stream` (for `codex exec --json`) and `fake`. With another agent, a session is measured only if its log prints a `tokens used` figure; otherwise it stays unmeasured, and every cost figure is a floor.

## Ask each session for less

**When.** Sessions are long, or they hit `session_timeout`.

The cheapest saving is a smaller charter. A session that reads two files costs less than one that reads twenty, and it is more likely to finish.

- **Size passes when you plan.** The README's rule of thumb: about two thousand lines of code, or three chapters of text, per pass. Fix pass sizes in `AUDIT.md` before `migrate`, when it costs nothing.
- **Cap findings per pass.** `max_findings_per_pass` (15) stops an Auditor that keeps finding things. What it did not cover goes back into the queue as a new pass.
- **Cap the Remediator's batch.** The batch size, `remediation_batch_size` (8), is how many findings one Remediator session works. After `migrate`, change it with a verb:

```bash
xcheck --project /path/to/your-project set-limit remediation_batch_size 4
```

The [capsule](README.md#capsule) already helps here, and it is on for every orchestrated session. Instead of handing each session the whole methodology and plan, xcheck writes it a short brief for its charter. On this repository's 39 queued passes, the largest brief was 3,334 bytes, against 84,610 for the full documents. Bytes were measured; the effect on tokens was not.

**Watch out.** A pass that is too small has a cost too. Each session pays to start, read its brief and learn the context. Many tiny passes can cost more than a few right-sized ones.

## Stop the loop from spinning

**When.** The loop keeps running sessions, and `status` shows the same decision again and again. A **decision** is what xcheck says the cycle needs next: which role runs, on which charter.

A loop that dispatches the same charter over and over, and nothing changes, is the most expensive failure this repository has measured. In one run, a single charter was attempted 54 times. Three settings stop it, and each catches a different shape:

- **`loop_progress_limit` (2, always on).** Stops the loop after N sessions in a row that repeat the same decision and leave `audit/` unchanged.
- **`embargo` (off, `embargo_after=3`).** Takes a charter out of the queue once its last N attempts each recorded nothing. The rest of the queue keeps moving. On one replayed run, it refused 117 of 136 sessions that did nothing. It also stopped 9 of 15 sessions that did useful work. Because of that, it is off by default.
- **The budget gates (`budgets=on`).** `charter_repeat_limit` caps how often one charter may be dispatched at all. `marginal_value_window` stops when the last N sessions produced no new accepted evidence. `no_progress_share_pct` stops when too much of the measured spend went to sessions that moved nothing.

Every one of these is a **stop gate**: the loop halts and prints which key stopped it and which number to raise. The loop then waits for you.

The budget gates ship at 0, which means off. `xcheck status` prints the value each would take, worked out from the Ouroboros-4 run: 8 for `marginal_value_window`, 50 for `no_progress_share_pct`, 3 for `charter_repeat_limit`. To adopt them, write them into `audit/orchestrator.conf`:

```
budgets=on
marginal_value_window=8
no_progress_share_pct=50
charter_repeat_limit=3
```

**Watch out.** A loop that stops on a gate is working as intended. Read why before you raise the number: a charter that cannot make progress usually needs its charter rewritten.

## Kill dead sessions early

**When.** Sessions sometimes hang: they start, print a banner, and then do nothing until `session_timeout` ends them.

Four deadlines watch every orchestrated session. A **deadline** is a number of seconds after which a session that shows no sign of working is killed. Each one looks for a different sign, and setting one to 0 turns it off:

| Deadline | Default | Kills a session that |
|---|---|---|
| `startup_deadline` | 120 s | has written no output at all |
| `output_deadline` | 600 s | has written nothing past its banner and its own prompt, and changed nothing in the record |
| `activity_deadline` | 1200 s | has done nothing observable: no change to the record, no telemetry, no file written in its worktree |
| `idle_deadline` | 1200 s | has been silent for that long |

The defaults come from a measured run of eight working sessions, called W-06. Its eight working sessions took between 439.3 s and 1052.7 s. Each deadline is longer than what the matching working session needed: the output deadline is longer than the whole life of the fastest one, and the activity and idle deadlines are longer than the slowest. In that same run, one stuck session held its slot for the full 3600 s and wrote 1,764 bytes. The output deadline would have ended it at 600 s.

A session killed this way ends as `stalled`, one of the fixed [outcomes](README.md#outcome). A `stalled` session is never retried, even with `retry_limit` set: it produced no sign of work, so trying again would most likely buy the same silence.

**Watch out.** Do not set a deadline below what your own working sessions need. Find out how long your own working sessions run first; each session's log and envelope record it.

## Triage little and often

**When.** Findings are piling up in `reported`, and the Auditor keeps filing more.

Every pass over findings nobody has judged is a pass that may be wasted: the Remediator cannot touch a finding until you accept it. In this repository, an unbounded setting once left 133 findings reported and none triaged.

`triage_batch_cap` (20) is the **triage cap**: once that many findings are waiting, the loop stops for you. Twenty findings is one sitting. Keep it there or lower. Setting it to 0 is legal, and it means the loop never stops for triage.

**Watch out.** Raising the cap to avoid the sitting moves the same reading later, when there is more of it, and lets the Auditor spend tokens on a project nobody has made decisions about.

## Run passes in parallel — and what it costs

**When.** The queue holds many Auditor passes over separate parts of the project, and wall-clock time matters more than tokens.

With **parallel passes** on, xcheck runs queued Auditor passes whose unit sets do not overlap at the same time, then merges their results one at a time. Turn it on in your operator profile:

```
parallel_passes=on
parallel_workers=2
```

Three limits are checked before anything starts, because a limit enforced afterwards would cancel sessions you had already paid for:

- `max_sessions_per_run` (8): the most sessions one fan-out may start.
- `parallel_budget_minutes` (480): the worst case, priced as sessions × `session_timeout`. The defaults fit together exactly: 8 sessions × 3600 s is 480 minutes.
- `parallel_confirm_above` (4): above this many passes, you must confirm with `--yes` or by typing `yes` at a terminal.

**Watch out.**

- **Parallel saves wall-clock time, not tokens.** Two workers spend tokens about twice as fast.
- **The saving is not measured.** No run has timed a parallel audit against a serial one.
- **Only Auditor passes run in parallel.** Remediation and verification stay one at a time.

## Put a ceiling on spend

**When.** You need a hard answer to "how much can this cost?"

There are two kinds of ceiling: time and tokens.

**Time: `--budget`.** A wall-clock budget, in seconds, for one run of the loop:

```bash
xcheck --project /path/to/your-project --budget 7200 loop
```

The first session is always dispatched. After that, a session starts only if the time left covers a whole `session_timeout`, because a session that might run over is refused rather than started. With the default `session_timeout` of 3600 s, a 7200 s budget keeps starting sessions while at least 3600 s remain; with sessions near the 427.5 s median that is several sessions, not two. For a cap that does not depend on time, `loop --max-sessions N` stops after N sessions. A session already running is never interrupted. Up to one `session_timeout` of the budget can go unused; that is deliberate.

**Tokens: `budgets=on` and a token ceiling.** A **token ceiling** is one of `tokens_per_session`, `tokens_per_pass` or `tokens_per_audit`. Each is 0, meaning off, until you set it. Before each dispatch, xcheck checks the spend recorded so far plus the cost of one more session.

If you pay for the model, put the ceiling in your operator profile, not in the project. There the audited repository can only make it tighter, never looser. [`docs/payer-ceiling.md`](../payer-ceiling.md) shows the lines to write.

This is an **admission** ceiling: it decides whether a session may start. It cannot stop a session that is already running, so one session may overshoot. And it is in tokens, never money: xcheck has no way to measure what you are billed.

The reservation is `tokens_per_session`: what each dispatch is charged until the provider reports what it really cost. Once a ceiling is set, `xcheck status` prints the guarantee, word for word:

> Unit: tokens. Guarantee: an ADMISSION ceiling — no session is dispatched unless the operator's remaining tokens cover its reservation (tokens_per_session); one in-flight session may exceed its reservation; money is not guaranteed.

**Watch out.**

- **Predicting gates need measured data; overspend checks do not.** A gate that predicts "one more session would go over" uses the average of measured sessions. It gives no verdict until at least half the finished sessions were measured. A gate that sees "this pass has already spent more than its ceiling" needs no prediction, and fires at any coverage.
- **A ceiling under normal spend stops normal work.** For `tokens_per_pass`, `xcheck status` derives 300,000 from this repository's measured passes. Derive your own from your `metrics` before you pick a number.

## Choosing models

**When.** You want to send simple work to a cheaper model.

**Model routing** sends each dispatch to a `cheap` or `strong` model by a fixed table: the kind of work and its risk decide the route, and no model is ever asked. Turn it on with `model_routing=on` in `audit/orchestrator.conf`, and name both models in your operator profile with `cheap_model` and `strong_model`. The role command receives the name through `{model}`.

Three rules hold no matter what you configure:

- **High-risk work never routes to the cheap model.** That holds for the shipped table and for any line you add. The dispatch is refused and the refusal names the rule.
- **Unknown work goes to the strong model.** Work nobody classified is not assumed to be easy.
- **An unset `cheap_model` refuses the dispatch.** A route that recorded "cheap" and ran the same model would be a lie in the record.

**Watch out.** No saving is claimed. A protocol for measuring one against a single-model run is written, but it has not been run. Until it is, routing is an experiment. Routing also does not change how independent a verdict is: a cheaper model from the same provider is not a second opinion.

## What not to optimise

Some savings make the audit worse, and xcheck refuses several of them by design:

- **The orchestrator.** At 0.77% of a session, making it faster cannot change your bill.
- **Verification.** Skipping the Verifier, or letting the fixer judge its own fix, saves one session per finding and loses the only check that the fix is real. The [methodology](methodology.md) explains why the second look is the point.
- **The evidence standard.** A finding without a quote, a locator and a norm is cheaper to write and costs more later: it cannot be validated, and it wastes a Remediator session.
- **The triage gate.** See [above](#triage-little-and-often): a cap of 0 only postpones the cost.
- **Deadlines below your real sessions.** They kill sessions you needed.

## Levers that are not available yet

Two savings that people expect are not in the shipped tool:

- **Auditing only what a diff touched.** A diff-scoped selector exists in the code, but it is experimental, has no production caller, and its configuration key is refused. `queue-pass --base` labels a pass as scoped; you still pick its units yourself.
- **Reusing earlier evidence.** An evidence cache exists as experimental code too, with no caller and no key.

The keys `diff_scope`, `evidence_cache` and `evidence_dir` are refused by name if you write them, so a typo cannot make you believe one is on. [`docs/experimental.md`](../experimental.md) says what each would need before it ships.

## A tuning checklist

Work down the list. Stop at the first item that fixes your problem.

1. **Measure.** Run `xcheck metrics` and `xcheck status`. Note the token cost per change and how many sessions were measured. If most were not, set `telemetry_adapter` first; without measured sessions, every figure above is a floor.
2. **Look for spinning.** In `status`, is the same decision repeating? If so, read the charter before anything else, then consider `embargo=on` or the budget gates.
3. **Look for hangs.** Did sessions end as `timeout` after a full `session_timeout`, with tiny logs? The deadlines catch that. Check they are not set to 0.
4. **Triage.** If findings are waiting in `reported`, triage them before running more passes.
5. **Shrink the charters.** If working sessions are long, split passes in `AUDIT.md`, or lower `max_findings_per_pass` and `remediation_batch_size` with `xcheck set-limit`.
6. **Set a ceiling.** Choose `loop --max-sessions N` for a session count, `loop --budget` for time, or `budgets=on` with a token ceiling in your operator profile, priced from your own `metrics`.
7. **Go parallel only for wall-clock time.** Turn on `parallel_passes` when you need results sooner and can pay the same tokens faster.
8. **Try model routing last,** and measure it yourself. Nobody has measured its saving yet.

Next: [When not to use it](when-not-to-use.md) says when the best saving is not running xcheck at all.

Previous: [Usage patterns](usage-patterns.md) · [Guide index](README.md)
