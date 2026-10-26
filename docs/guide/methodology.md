# The methodology

This page explains why xcheck works the way it does. Each rule in it exists because something specific goes wrong without it, and this page names that something. The rules themselves live in [`XCHECK.md`](../../XCHECK.md); this page is the reasoning behind them.

The method produces findings. A finding is one reported problem, in one file. The one below is illustrative: invented for this page, on a toy project, to show the shape.

```markdown
id: F-0007
title: config loader swallows a malformed file and returns defaults
severity: major
dimension: error-handling
unit: src/config.py
status: reported

## Evidence
src/config.py:41, in `load_config`:

    except ValueError:
        return DEFAULTS

Norm — docs/ADR-004-errors.md, "Errors": "A configuration file that cannot be
parsed is a fatal error. The program must never start on defaults it was not given."

## Why this is a defect
A typo in the config file silently starts the service with default settings.

## How to verify the fix
Run `python -m app --config tests/bad.toml`. Expected: exit status 2 and a message
naming the file. Before the fix: exit status 0.
```

The finding has three required parts. There is a quote copied character for character from the code, so anyone can search for it. There is a locator, `src/config.py:41` inside the load_config function, saying where the quote is. And there is a [norm](README.md#norm) (a written rule the code breaks), quoted just as exactly. Its `severity` is one of a fixed scale, `critical`, `major`, `minor` or `info` (`XCHECK.md` §2). Everything else in xcheck exists to produce findings like this one, decide which to fix, fix them, and prove the fixes hold.

## The failure this method is built against

Ask one agent, in one long conversation, to "review this whole project". On a small project that works fine. On a big one it fails in the same way every time.

The agent reads a lot, and then it starts to lose the thread. It forgets what it decided an hour ago, misremembers a function, and reports a problem in code it never opened. Then it "fixes" that problem, and the fix looks right, because the same agent that imagined the bug is now grading its own work.

This happens often. It is what happens when a long session has to hold a large project in its head. `XCHECK.md` §1 puts it plainly: long sessions "forget earlier decisions, invent particulars they never actually checked, and hallucinate both findings and fixes."

## Four mechanisms that replace trust

Each mechanism blocks one way the long session fails.

**1. Narrow chartered sessions.** Every session gets a [charter](README.md#charter): an exact job with an exact stop. For example: check the [dimension](README.md#dimension) *terminology* in chapters 4–6, and stop at the findings cap. A session is never long enough to lose the thread, because it is never given a whole project. A session that receives no charter must refuse to start (`XCHECK.md` §4 rule 1). A session that runs past its stop breaks its charter, and a [pass](README.md#pass) that stops early must still say what it did not cover (§4 rule 5).

**2. The evidence standard.** A finding without an exact quote, a locator and a norm is invalid before anyone reads it (`XCHECK.md` §6 rule 4). The quote is the important part. A quote can be checked with a plain text search. An invented problem has no line to quote, so it does not survive the search.

**3. Validation before fixing.** Before the fixer changes anything, it must find the quote again in the source and confirm the problem is real (`XCHECK.md` §7). An invented finding stops here: it is marked `disputed` and never fixed.

**4. Cross-verification.** The fix is judged by a session that did not write it. That session has a standing instruction: "Your job is to prove the fix wrong, not to confirm it" (`XCHECK.md` §3, *Verifier*).

## Who does what: the five roles

A [role](README.md#role) is a job in the cycle; any capable agent can play any role.

| Role | Who | What it does | What it may never do |
|---|---|---|---|
| Planner | an agent, one session | Lists the project's [units](README.md#unit), its norms and the [dimensions](README.md#dimension) to check; writes `AUDIT.md` with the queue of passes | file findings |
| Auditor | an agent, one session per [pass](README.md#pass) | Checks one dimension over a batch of units and files findings | fix anything |
| Triage | you | Decides, finding by finding: `accepted`, `rejected` or `deferred` | edit a finding's evidence |
| Remediator | an agent | Fixes a batch of accepted findings: validate, [census](README.md#census), plan, fix, self-check | verify its own fix |
| Verifier | an agent, never the one that fixed it | Gives each fix a binding verdict: `closed` or `reopened` | close a finding without running its "how to verify" step |

Two rules are hard and have no exceptions:

- **The Verifier is never the session that produced the fix.** A different agent is better. A different, fresh session of the same agent is the minimum (`XCHECK.md` §3).
- **Only one writing session runs at a time** (`XCHECK.md` §4 rule 8). Two sessions writing the same audit at once would each work from a picture of the state the other is changing.

Each status in a finding's life also has exactly one owner. `XCHECK.md` §5 calls this "one status, one owner": triage statuses belong to you, `validated`/`planned`/`fixed` to the Remediator, `closed`/`reopened` to the Verifier. A role never touches a status it does not own. This is what keeps five roles from undoing each other's work.

## What counts as a finding: the evidence standard

A [finding](README.md#finding) is a gap between the material and a norm. A complaint with no norm behind it is an opinion, and its severity is capped at `info` (`XCHECK.md` §6 rule 2).

The norm can be many things: a style guide, a specification, an architecture decision, a fact with a source. It can also be the material itself. If chapter 3 says a protocol has four steps and chapter 7 says it has five, the passage in chapter 3 is the norm for the finding about chapter 7. It gets quoted the same way.

The [evidence standard](README.md#evidence-standard) asks for three things.

1. **A quote, character for character.** It must be findable by a literal search. A line number alone is not enough, because lines move when the file is edited; the quote moves with them (`XCHECK.md` §6 rule 1).
2. **A [locator](README.md#locator).** For code: `file:line` plus the function or class around it. For text: the file, the section, and the quote (§6 rule 5).
3. **The norm, quoted too.** The sentence of the rule itself, quoted exactly; "this violates good practice" does not count.

The Auditor also writes, at the moment it files the finding, how to check that a fix worked (§6 rule 3). It writes this while the defect is in front of it. The Verifier never invents the test afterwards, because a test written after the fix tends to test the fix, not the defect.

Some defects are a missing thing: a function with no test, an `open` with no `close`, a table of contents line with no section. There is no line to quote for something that is not there. So the finding quotes the thing that *is* there and demands a partner. It quotes the rule that makes the partner mandatory. And it gives a search for the partner that comes back empty (`XCHECK.md` §6 rule 7).

A pass may also find nothing. That is a valid result. There is no quota of findings and no minimum (`XCHECK.md` §4 rule 6). A cap on findings per pass exists (`max_findings_per_pass`, 15 by default, `XCHECK.md` §10), and it is an upper bound, never a target.

## Triage: the one routine human decision

[Triage](README.md#triage) is you reading the new findings and deciding which ones get worked. Each finding gets one of three answers:

- `accepted`: fix it.
- `rejected`: do not fix it. It is terminal and never reaches the Verifier.
- `deferred`: not now. It stays open, and you will see it again.

A `deferred` finding stays open as debt. An audit with `deferred` findings left in it is not finished, and the tool says so. You clear the debt at a later sitting by re-triaging each one to `accepted` or `rejected` (`XCHECK.md` §5).

Triage is the only decision the routine cycle needs from you (`XCHECK.md` §3). Everything else passes from agent to agent through files. A few rarer situations also stop and wait for you: a disputed finding, a conflict between two norms, a fix that keeps failing. Those are exceptions, and `XCHECK.md` §3 lists them separately.

You can do triage through an agent session if that is easier. Then the agent only records: it shows you the findings and records your decisions word for word. It must never decide one itself, and it must never fill in a decision you did not say. "Reject everything else" counts only if you said "reject everything else" (`XCHECK.md` §3, *Agent-as-pen*).

A human stays here because "is this worth fixing?" is a question about your project's priorities, and those are not written down anywhere an agent can read them.

## Validate before you fix

The Remediator works in a fixed order: validate → census → plan → fix → self-check. No step is skipped and none is reordered (`XCHECK.md` §3, *Remediator*).

[Validation](README.md#validation) is the first step. Before changing anything, the Remediator opens the source and looks for the quote. Then it asks whether the problem is real. Three outcomes are possible:

- The quote is there and the problem is real: `validated`.
- The quote is not there, or the problem does not hold up: `disputed`.
- The problem was real, but someone else's edit already removed it: `obsolete`.

This costs almost nothing. The Remediator has to read the source to plan the fix anyway. But it is the step that checks an invented finding against the file it names.

A `disputed` finding is not thrown away. The Auditor gets one written round to argue for it, and then you decide (`XCHECK.md` §9 rule 3). There is no back-and-forth between the two agents beyond that one round.

The last step, self-check, means re-running the finding's own "how to verify" procedure before marking it `fixed`. It catches what the Verifier would catch anyway, before it costs a round trip.

## Cross-verification and how independent it really was

The [Verifier](README.md#verifier) does two things for each fixed finding (`XCHECK.md` §7):

1. It runs the "how to verify" procedure the Auditor wrote, against the current material.
2. It looks around the change for damage the procedure would not catch.

Then it gives a verdict: `closed` or `reopened`. A `reopened` verdict needs evidence to the same standard as a finding: a quote and a norm.

When the same finding is reopened twice in a row (`reopen_limit`, 2 by default), the cycle stops on it and marks it `⚠ needs-human` (`XCHECK.md` §9 rule 2). Two failed fixes in a row usually mean the finding, the norm or the approach needs a person to look at it.

"A different session" is the minimum, and xcheck does not take even that on trust. It records which provider, which model and which session ran each side, and it computes an [independence level](README.md#independence-level) for every verdict from those records:

| Level | What it means |
|---|---|
| `cross-provider` | Two different vendors ran the fix and the verdict. |
| `cross-model` | Same vendor, different model. A weaker claim. |
| `same-provider-different-session` | Same vendor, same model, a fresh session. Counted as degraded. |
| `unrecorded` | One side has no record. Cannot be measured; counted as degraded. |
| `same` | The same session. Refused outright. |

`xcheck metrics` shows how your verdicts are spread across these levels and marks the degraded ones. A degraded level blocks nothing: the verdict stands, and the metrics say how weak it is. A team with one AI vendor gets `same-provider-different-session` at best, and that is a sound, displayed fallback.

None of these levels proves that the two sides did not share training data, a cache, or the same person typing at both. `cross-provider` means only that two different vendors ran the two sessions. And two sessions whose provider was recorded as `unknown` are never counted as two different providers.

## Fixing the disease: census and class findings

A wrong pattern that appears once has usually been copied. So the Remediator's second step, the [census](README.md#census), is to search the whole project for the same pattern, beyond the file the finding was about (`XCHECK.md` §8 rule 1). The search is written down precisely enough for someone else to run it again.

What happens next depends on the count:

- **Fewer than the threshold** (`class_threshold`, 3 by default): fix the instance, and note the siblings in the finding.
- **At or above the threshold:** open a [class finding](README.md#class-finding), with an id like `CF-0003`. It holds the pattern, the search, every instance found, and the root cause.

The root cause is most often a norm: one that is missing, unclear, or not enforced. So the Remediator's plan picks a strategy on a rising scale, and you approve it at triage (`XCHECK.md` §8 rule 3):

- (a) fix every instance;
- (b) do (a), and fix the norm so the pattern cannot come back;
- (c) do (b), and add an automatic check (for example a lint rule) that fails if anyone brings the pattern back.

A class finding goes through triage like any other finding. A change that touches the whole project never skips your decision. If the fix changes a norm, or has to pick between two norms that disagree, it also stops at a separate gate where the owner of the norms decides (`XCHECK.md` §8 rule 8). A fix in the wrong direction, applied across the whole project, multiplies one mistake into many.

That gate is recorded in the state. The Remediator writes its plan, then raises the brake with `xcheck block-on-norm CF-0003` and stops. The class finding now shows `blocked: norm-ratification` and `norm-ruling: pending`, and its body has a `## Norm ruling` section that states the conflict and the two sides. Nothing is fixed while it is blocked. You, as the norm's owner, write your reasoning into that section and record the decision with `xcheck record-ruling CF-0003 --norm N1 --ruled-by human:<you>`, where `N1` is the norm's id in `AUDIT.md`'s catalog of norms. The verb refuses a `--ruled-by` that is not a human. The gate fails closed: a ruling that is missing, still `pending`, or not a recognised norm id keeps the class stopped.

Verifying a class is a re-census: run the recorded search again and expect it to find nothing (`XCHECK.md` §8 rule 5). This is what catches "fixed 12 of 15": a re-census that still finds three instances gives a `reopened` verdict.

## Durability: a fix that stays fixed

A `closed` verdict says the fix worked when the Verifier looked; whether it still works next month is a separate question.

[Durability](README.md#durability) means the defect was re-observed absent. Time passing proves nothing: an unchanged record is no more checked a month later than on the day it closed (`xcheck/oracle.py`, module docstring).

The tool that makes this possible is the [oracle](README.md#oracle). A finding may carry one: a small command, plus the exit status that means "the defect is gone". The Auditor writes it, as a `## Oracle` section in the finding, while the defect is in front of it. It is optional, and running it needs docker, because it runs only under the `container` profile. In the example at the top of this page, the oracle would run the program on the bad config file and expect exit status 2. `xcheck run-oracle F-0007` runs it inside a container, with no network, on a copy of the current material, and records what it saw.

That record changes what a closure needs. To record `closed`, the oracle must have seen the defect present on earlier material and absent on the current one. The alternative is to state the evidence yourself with `--evidence manual:`. That closure is labelled manual and never counts as durable. A closure counts as durable when the oracle runs again on newer material and still sees the defect absent (`CHANGELOG.md`, 0.9.6).

Text findings usually have no oracle, because "the chapter reads clearly now" is not a command. For those, the "how to verify" procedure and the Verifier's reading are the evidence, and the metrics say so.

## What the method does not promise

xcheck makes some failures hard, and some it leaves to you. 
- It does not prove your project is correct. It checks the dimensions the Planner listed, over the units in the unit map. A problem in a dimension nobody listed, or a file in no unit, is never looked at.
- A pass with no findings is a statement about that charter. It says this dimension found nothing in these units. It says nothing about the rest.
- The evidence standard stops invented quotes; it does not stop wrong judgement. A real quote can still be paired with a norm that does not apply. Validation and your triage are where that gets caught, if it is caught at all.
- Independence is measured; the ceiling above says what that leaves out.
- The agents can still be talked into things. The material under audit is untrusted input. A file in it can contain instructions aimed at the agent. What the orchestrator adds against that is described in [`SECURITY.md`](../../SECURITY.md), and it limits what a misled agent can do; it does not stop the agent from being misled.

## Where the rules live

- [`XCHECK.md`](../../XCHECK.md) is the normative document. It is the only one the agents read, and a copy lives in every audited project. This page explains it and loses to it on any disagreement.
- `AUDIT.md`, written by the Planner, applies the method to one project: its dimensions, its units, its norms, its limits. `XCHECK.md` knows nothing about any particular kind of material; everything specific to a project goes in `AUDIT.md` (`XCHECK.md` §1).
- `audit/state.json` is the record of where every finding stands. The ledger and each finding's header are generated from it. Nobody changes it by hand; every change goes through a write verb such as `xcheck set-status`, which checks that the role making it is allowed to.

Next: [How it works](how-it-works.md) shows what the orchestrator does with all of this when you run `xcheck next`.

[Guide index](README.md)
