# xcheck

**Cross-agent audit & remediation methodology for projects too large to trust to a single agent session**: codebases, book manuscripts, reference corpora, documentation sets.

Two AI agents and one human run a cycle:

```
audit (agent A) → triage (you) → remediate (agent B) → verify (agent A) → repeat
```

The cycle repeats until the pass queue is empty and every finding reaches a terminal status. The agents never talk to each other: they communicate only through files in an `audit/` directory inside the audited project. You make one routine decision: which findings are worth fixing. A few rarer halts also need you; `XCHECK.md` §3 lists them, and §8 below says where the loop stops for each.

Repository: github.com/vsov/xcheck · This README describes 0.9.8 · License: MIT · Orchestrator: Python 3, stdlib only, zero dependencies.

> **Read next.** This README installs xcheck and walks you through a first audit. The [guide](docs/guide/README.md) explains the rest: [the methodology](docs/guide/methodology.md) behind it, [how it works](docs/guide/how-it-works.md) inside a run, [usage patterns](docs/guide/usage-patterns.md) for common situations, [performance](docs/guide/performance.md) for faster and cheaper audits, and [when not to use it](docs/guide/when-not-to-use.md).

---

## Table of contents

1. [Why not "just ask an agent to review"](#1-why-not-just-ask-an-agent-to-review)
2. [When to use it (and when not to)](#2-when-to-use-it-and-when-not-to)
3. [Concepts in five minutes](#3-concepts-in-five-minutes)
4. [Repository layout](#4-repository-layout)
5. [Installation](#5-installation)
6. [Quickstart: your first audit](#6-quickstart-your-first-audit)
7. [Launchers: skills for Codex and Claude Code, commands for OpenCode](#7-launchers-skills-for-codex-and-claude-code-commands-for-opencode)
8. [The orchestrator](#8-the-orchestrator)
9. [Agent mapping and cross-agent discipline](#9-agent-mapping-and-cross-agent-discipline)
10. [The audit/ directory in your project](#10-the-audit-directory-in-your-project)
11. [Class findings: fixing the disease, not the symptom](#11-class-findings-fixing-the-disease-not-the-symptom)
12. [Candidate mechanisms (default off)](#12-candidate-mechanisms-default-off)
13. [Configuration reference](#13-configuration-reference)
14. [Language policy](#14-language-policy)
15. [Troubleshooting & FAQ](#15-troubleshooting--faq)
16. [License](#16-license)

---

## 1. Why not "just ask an agent to review"

Ask one agent to "review my project" in one long conversation, and on a large project it fails in a predictable way. It loses the thread and reports problems and "fixes" that are not real. xcheck replaces trust in the agent's memory with four mechanisms:

1. **Narrow chartered sessions.** Every session gets an exact job ("dimension *terminology*, chapters 4–6, stop after 15 findings") and must stop rather than skim. A session with no charter must refuse to start.
2. **The evidence standard.** A finding needs a character-exact quote, a locator and the rule it breaks, or it is invalid. Anyone can re-check the quote with `grep`, so an invented finding does not survive.
3. **Validation before fixing.** The agent that fixes must first re-confirm the finding against the source. Invented findings die here, marked `disputed`.
4. **Cross-verification.** A fix is judged by an agent that did not write it: a separate session told *"your job is to prove the fix wrong, not to confirm it."*

Before a finding is fixed, a census of the whole project asks whether it is one copy of a repeating error. At three or more copies, the whole *class* is fixed, including the rule that let it happen. [The methodology](docs/guide/methodology.md) explains what each mechanism protects against.

## 2. When to use it (and when not to)

**Good fit:** a project too big to read attentively in one sitting : a book, a series of reference volumes, a codebase from ~10 files up, a documentation corpus. You want to know what is systematically wrong across it.

**Poor fit:** a point bugfix, a single-diff review, a 200-line file. There the cycle costs more than it returns, and ordinary code review is enough. [When not to use it](docs/guide/when-not-to-use.md) covers the other bad fits. Among them: no written rules, nobody to triage, hostile code with no container, production gates, small budgets.

## 3. Concepts in five minutes

### Roles

| Role | Who (default) | What they do |
|---|---|---|
| Planner | agent, 1 session | Inventories the project and its norms → `AUDIT.md`: dimensions, unit map, pass queue |
| Auditor | agent, N sessions | One pass = one dimension × a batch of units → findings + a coverage report |
| **Triage** | you | `accepted` / `rejected` / `deferred` in the ledger: the one routine mandatory human gate (rarer exceptional gates exist, all distinct from triage, and `XCHECK.md` §3 lists them) |
| Remediator | agent, N sessions | A batch of accepted findings: validate → census → plan → fix → self-check |
| Verifier | agent, N sessions | A verdict on every fix: `closed` or `reopened`. Never the one who fixed it |

Role ≠ agent: any capable agent can play any role. Two rules are hard: the Verifier never judges its own fix, and only one writing session runs at a time.

### Finding lifecycle

```
reported ──(triage: you)──► accepted | rejected | deferred
accepted ──(Remediator)───► validated ► planned ► fixed
                            └► disputed | obsolete | superseded-by-class
fixed ─────(Verifier)─────► closed | reopened
reopened ──(Remediator)───► planned ► fixed again (attempts += 1)
```

Terminal statuses: `closed`, `rejected`, `withdrawn`, `obsolete`, and `superseded-by-class`. A superseded finding is terminal only once a closed class finding lists it as a member. `deferred` is not terminal: it is your debt to re-triage.

After `reopen_limit` consecutive `reopened` verdicts on one finding (default 2), it gets `⚠ needs-human` and the cycle stops for your decision.

### The evidence standard

Every finding must contain: (1) a character-exact quote findable by search; (2) a locator (`file:line` + symbol for code, file + section for text); (3) the norm the material violates, with its own quote (style contract, spec, ADR, sourced fact, or a contradicting passage). No norm → severity capped at `info`.

### Agent-as-pen triage

During triage the agent only records: it presents findings, records your stated decisions word for word, and never fills in what you did not say.

Every term above, and the others the guide uses, is defined in the [glossary](docs/guide/README.md#glossary).

## 4. Repository layout

| Path | What it is |
|---|---|
| `XCHECK.md` | The methodology core. The only normative document agents read. Installed into each audited project. |
| `bin/xcheck` | The orchestrator's entry point, a ~20-line bootstrap into `xcheck.cli:main`. |
| `xcheck/` | The orchestrator itself (Python 3, stdlib only), layered `util → md_prose → state → views → validate → courier → runner → decision → write → cli`, plus `envelope.py` (the invocation record), `parallel.py` (concurrent passes, merged one at a time), `dashboard.py` (a view over the two JSON payloads that computes nothing), `migrate.py` and `upgrade.py` (two operator-typed verbs), and `selftest.py`, a decision-logic battery whose pass count is measured from the check lines it emits to `sys.stdout`. |
| `pyproject.toml` | Package metadata and the `xcheck` console script. No runtime dependencies, deliberately (N2). |
| `CHANGELOG.md`, `SECURITY.md` | What changed per release; the trust model: what containment does and does not protect against, scoped per launch mode (`orchestrated` vs `uncontained-direct`). Read `SECURITY.md` before pointing this at code you did not write. |
| `tests/` | The external battery. A test passes only when a real command's observable outcome is right (see `tests/README.md`). |
| `ci/` | `run-checks.sh` (the single entry point CI uses, runnable locally) and `check-split.py`. |
| `templates/` | Artifact skeletons: `finding.md`, `class-finding.md`, `pass.md`, `plan.md`, `construal.md`, plus `AUDIT-text.md` / `AUDIT-code.md` starting points. |
| `skills/` | Shared launcher skills used by Codex, Claude Code, and both plugin manifests. Five are wrappers over `xcheck next` and carry no methodology; `xcheck-status` is read-only and direct (§7). |
| `styles/` | `eli5.md`, the single source for how a launcher session words things to a person, and `styles/README.md` explaining it. `ci/render-style.py` copies its marked block into all six `skills/*/SKILL.md`; `--check` compares the six copies byte for byte and exits non-zero on a one-character difference. |
| `launchers/` | Per-CLI installer (`install-launchers.sh`). OpenCode commands are generated from `skills/` at install time. |
| `bootstrap.md` | From zero to a running cycle: install block, session one-liners, the full cycle step by step. |
| `install.sh` | One-command install of the methodology into a target project; `--type code\|text` picks the `AUDIT.md` template. |
| `operator-profile.sh` | Writes the operator profile, once per machine and outside every audited repository (§5.3). Nothing dispatches without one. |
| `.claude-plugin/`, `.codex-plugin/` | Claude Code and Codex plugin manifests over the checked-in `skills/` payload. |

## 5. Installation

You need Python 3.10 or later, git 2.20 or later, and the agent CLIs the role commands name: by default both `codex` and `claude`, installed and signed in. If you have only one, edit the role lines after §5.3. Docker is needed only for `--trust untrusted`.

### 5.1 Get the repo

```bash
git clone https://github.com/vsov/xcheck.git
cd xcheck
```

### 5.2 Install the methodology into a target project

```bash
./install.sh --type code /path/to/your-project    # a codebase
./install.sh --type text /path/to/your-project    # a text corpus (the default)
```

This creates `your-project/audit/` with `XCHECK.md`, an `AUDIT.md` template, `templates/`, and empty `findings/`, `passes/` and `plans/`. Nothing is audited yet. The Planner fills `AUDIT.md` in the first session; then one operator command, `xcheck migrate`, loads that plan into `audit/state.json`, the canonical record. Until then the project has no state document, and that is expected: on a tree `install.sh` created, `xcheck status` reports `decision: run-planner` and `xcheck next` dispatches the Planner. Any other tree without `state.json` is refused.

There is no `LEDGER.md` to create: from 0.8 it is a **generated mirror** of `state.json`, rendered whenever state changes, never read back as authority and never hand-edited.

Notes:

- `--type` picks the template `AUDIT.md` starts from: `code` is `templates/AUDIT-code.md`, `text` is `templates/AUDIT-text.md`, and `text` is the default. It applies only when `audit/AUDIT.md` does not exist yet. An existing one is never replaced; if it is still an unfilled template of the other type, the installer prints the `cp` that switches it.
- The last lines it prints are the next steps, and the first of them is the operator profile (§5.3).
- `install.sh` never touches audit state. It does overwrite `XCHECK.md` and the templates, because it assumes those are still xcheck's. To refresh an *existing* installation, use `xcheck upgrade` (§5.6), which checks that assumption instead of making it.
- `audit/` can be committed with the project (recommended: commits that name finding IDs make verification much cheaper) or added to `.gitignore`.

The installer records what it put there, in two files `xcheck upgrade` later reads as evidence:

| File | What it holds |
|---|---|
| `audit/.provenance.json` | xcheck version, `XCHECK.md` digest, source ref (`git describe`), state schema version, install time |
| `audit/MANIFEST.sha256` | every shipped file and its digest at install time |

If neither `shasum -a 256` nor `sha256sum` is on `PATH`, the install still succeeds and says plainly that it wrote no provenance: a manifest nobody can verify is worse than none, because `upgrade` would trust it.

### 5.3 Create your operator profile (required, once per machine)

Nothing dispatches without one. The profile governs `orchestrated` sessions: the ones `xcheck next`, `xcheck loop` and the launchers that wrap them (§7) start, each inside a sandbox, with a timeout and a record. Each of those refuses before a session starts until a profile resolves, and prints where it looked. `xcheck status` names the profile in force on its `policy:` line, or says there is none.

The profile is your own file: which command each role runs, the containment it runs inside, and how far you trust the material. It lives outside every audited repository, because a session can write anywhere under `audit/` (§8), and a project must not set the terms of its own audit. So `install.sh` cannot create it for you. `operator-profile.sh` writes one from the template:

```bash
./operator-profile.sh --trust trusted /path/to/operator.conf
export XCHECK_OPERATOR_PROFILE=/path/to/operator.conf    # and in your shell's startup file
```

- `--trust` is required, and it has no default. Use `trusted` for code you or your organisation wrote and review. Use `untrusted` for anything else; that also sets `sandbox_profile=container`, so docker must work on the machine. `SECURITY.md` says what each boundary does and does not stop.
- You name the path; xcheck never derives one from your home directory. With `$XDG_CONFIG_HOME` set you can leave the path out: the profile goes to `$XDG_CONFIG_HOME/xcheck/operator.conf`, where xcheck finds it with no export.
- The role commands come from the template: Codex for the Planner, Auditor and Verifier, and `claude -p` for the Remediator. Edit the file to change them; every key is explained beside it.
- It never overwrites a profile. It validates the file with xcheck's own loader first, and with `--project DIR` it refuses a path inside that project. It ends by printing the exact line that points xcheck at the file.

To do it by hand instead, copy `docs/operator.conf.example`, set `trust_level`, and point at the copy the same way. §8 lists every key. For a real model in a container, [docs/codex-provider.md](docs/codex-provider.md) is the one supported path: build `images/codex`, run `xcheck doctor`, dispatch.

Optional, for the operator who pays: a token ceiling the audited repository cannot raise: `tokens_per_audit` with `tokens_per_session` beside it, in the operator profile ([docs/payer-ceiling.md](docs/payer-ceiling.md)). Unit: tokens. Guarantee: an ADMISSION ceiling — no session is dispatched unless the operator's remaining tokens cover its reservation (tokens_per_session); one in-flight session may exceed its reservation; money is not guaranteed.

### 5.4 Install the launchers (optional but recommended)

From the xcheck repo:

```bash
cd launchers
./install-launchers.sh            # all three CLIs
./install-launchers.sh claude     # → ~/.claude/skills/xcheck-*/SKILL.md
./install-launchers.sh codex      # → ~/.agents/skills/xcheck-*/SKILL.md
./install-launchers.sh opencode   # generated from skills/ → ~/.config/opencode/command/xcheck-*.md
./install-launchers.sh orchestrator  # → symlink ~/.local/bin/xcheck
```

Idempotent; re-run after updating the repo. Uninstall = delete the copied files.

The checked-in `skills/` directory is also the payload for `.claude-plugin/plugin.json` and `.codex-plugin/plugin.json`. `./install-launchers.sh plugin` checks that both manifests and all six skills are present; a marketplace can package the repository directly without a generated payload. Re-running the Codex installer removes the six legacy xcheck files from `~/.codex/prompts/`.

### 5.5 Verify the toolchain

```bash
python3 bin/xcheck selftest    # expect: PASS (N/N) — all checks green, zero failures
```

### 5.6 Upgrading an installed copy

```bash
bin/xcheck --project /path/to/your-project --dry-run upgrade   # report only
bin/xcheck --project /path/to/your-project upgrade
```

`upgrade` exists because of a finding from xcheck's audit of itself (F-0146): after `install.sh` copied the core into your project, nobody owned keeping the two in sync. The agents read `audit/XCHECK.md`, the developer edits the root one, and the two drift apart with nothing reporting it.

It reports every change before making any of them, and it decides from evidence:

| Target file | What happens |
|---|---|
| digest matches `MANIFEST.sha256` | **refreshed**: xcheck shipped it, xcheck owns it |
| absent | **installed** |
| already identical to this version | left alone |
| digest differs from the manifest | **refused**, named, untouched: you changed it, so it is yours |
| no manifest entry (pre-provenance install) | **refused**: xcheck cannot prove it shipped it |
| `AUDIT.md`, `LEDGER.md`, `state.json`, `events.jsonl`, `orchestrator.conf` | never shipped, never candidates |

A refused file keeps its old manifest entry, so it stays refused on the next run instead of being silently adopted. Reconcile it yourself (diff it against the shipped version, keep what is yours), then re-run. `upgrade` exits non-zero whenever anything was refused.

If the installed copy is on a different state schema, `upgrade` stops without writing anything and names `xcheck migrate`. Only a schema change forces a migration, so the two versions are printed separately.

### 5.7 Compatibility

| | Supported |
|---|---|
| Python | 3.10, 3.11, 3.12: the versions `.github/workflows/ci.yml` declares. **Observed green** on GitHub Actions at 0.9.8 (2026-10-25, commit 60ff90b on the public mirror): the full suite on `main` (run 40188197283) and the weekly matrix of ubuntu and macOS under all three versions (run 40188197103). Before 0.9.8 the weekly matrix had failed every run since its first, and every green figure came from one development machine, where the **release gate** runs green under 3.10 and 3.11. 3.13 is not in the classifiers, because no matrix row covers it. |
| Platforms | macOS and Linux. Windows is not supported: the lock protocol, process-group kill and `resource` limits (the `orchestrated` machinery) are POSIX. |
| Git | 2.20+. The floor is set by the two commands the tool needs (`git worktree add --detach`, `git describe --tags --always --dirty`), not by a test. No specific old version is verified: the only git ever exercised is the one on the developer machine. |
| State schema | 1: written by 0.8.0, read unchanged by 0.8.x and 0.9.x. 0.9.0 changes no schema, so no migration |
| Reads schema 1 | xcheck ≥ 0.8.0. Earlier versions have no `state.json` at all and will not understand the tree. |
| Legacy Markdown tree | converted once, by `xcheck migrate`, on your command and never automatically |
| Agent CLIs | any headless CLI you can name in `orchestrator.conf`; `claude`, `codex` and `opencode` are the ones exercised in the pilots |

A release bump never implies a migration; a schema bump always does. Both numbers appear in `xcheck status`, in `audit/.provenance.json`, and in `CHANGELOG.md`.

## 6. Quickstart: your first audit

You can run the cycle one session at a time (`xcheck next`, or a launcher that wraps it) or let `xcheck loop` run sessions until it reaches a gate. Both are `orchestrated`. Pasting a role's one-liner into an agent yourself also works, but that session is `uncontained-direct`: no sandbox, no timeout, no record. Use it only on purpose. The steps below show all three; for a first audit, use the launchers or `xcheck next`.

Optional, and worth 15 minutes: install `audit/` into a throwaway directory and walk one invented finding through every status by hand, including creating and rejecting a class finding. `bootstrap.md` has the session one-liners.

The commands below are written as `xcheck`. Run `./install-launchers.sh orchestrator` once (§5.4) to put it on your `PATH`, or use `bin/xcheck` from your clone. For a sense of how long and how expensive an audit will be before you start, read [Performance](docs/guide/performance.md#where-the-time-actually-goes).

### Step 1: Plan

Do §5.3 first. Without an operator profile, `/xcheck-plan` and `xcheck next` refuse before any session starts.

Open a session of your planning agent in the audited project root and run:

- Codex with skills: `$xcheck-plan`
- Claude Code / OpenCode with launchers: `/xcheck-plan`
- Any CLI, by hand:

  ```
  Read audit/XCHECK.md. Role: Planner. Charter: inventory this project and its norms; produce audit/AUDIT.md. Do not audit anything yet.
  ```

The session catalogs the project's norms (style contracts, specs, ADRs, glossaries), picks audit dimensions, slices the material into units, and builds the pass queue, a list of "dimension × unit batch" passes.

**Your check afterwards (2 minutes):** open `AUDIT.md`. Does every dimension name a norm source? Are passes reasonably sized (heuristic: ≤3 chapters of text or ≤2 kloc of code per pass)? Cross out passes you don't want; trimming scope here is cheap and needs no gate.

Once the plan is complete, `xcheck status` reports `stop-migrate`: the plan is waiting for you. Load it with `xcheck --dry-run migrate` and then `xcheck migrate`. From then on the queue changes through `xcheck queue-pass`, `amend-pass` and `cancel-pass`, not by editing `AUDIT.md`.

### Step 2: Audit passes

One session per pass, in queue order:

- Codex: `$xcheck-audit`; Claude Code / OpenCode: `/xcheck-audit`. Either runs the next queued pass. To change which pass runs next, change the queue (`xcheck amend-pass`, `xcheck cancel-pass`); a pass id typed at the launcher is ignored.
- By hand:

  ```
  Read audit/XCHECK.md and audit/AUDIT.md. Role: Auditor. Charter: pass P-01. Stop conditions per charter.
  ```

The session reads the charter's units attentively, checks them against the dimension's norm, writes finding bodies to `audit/findings/` and a pass report to `audit/passes/`, and records each one with `xcheck file-finding`, closing the pass with `xcheck record-coverage`. The ledger row and the pass's completion are *rendered* from that: there is no row to add and no checkbox to tick. Default cap: 15 findings per pass; what does not fit goes back into the queue with `xcheck queue-pass`.

**What to check in a pass report:** the Coverage section must list what was NOT covered. An empty NOT COVERED with half the units skimmed means the session broke protocol, and the pass does not count.

You can run several passes back to back and triage the pile afterwards; triaging after every pass is not required.

### Step 3: Triage (your gate)

- Codex: `$xcheck-triage`; Claude Code / OpenCode: `/xcheck-triage`. The agent presents findings in groups (severity × dimension) with quotes. You answer in chat ("accept all critical, reject F-0031, show me CF-0002 in detail"), and it hands you the exact `xcheck set-status` lines for what you said.
- By hand: read the pile with `xcheck status` (or `audit/LEDGER.md`, which mirrors it) and record each decision yourself:

```bash
xcheck set-status F-0031 rejected
```

  Only `accepted`, `rejected` and `deferred` are yours. Batch decisions are normal ("all critical and major → accepted"), one command each. When a title alone doesn't settle it, open the finding file: the quote and reasoning are there.

Recording the decision takes only the verb. It refuses any transition `XCHECK.md` §5 does not allow, writes `audit/state.json`, and re-renders the ledger and the finding's header block. There is no sync step, and typing into a mirror instead changes nothing.

### Step 4: Remediate

- Codex: `$xcheck-remediate`; Claude Code / OpenCode: `/xcheck-remediate`. The orchestrator picks the batch from `audit/state.json`: unfinished and reopened findings first, then accepted class findings, then accepted findings, up to `remediation_batch_size`.
- By hand:

  ```
  Read audit/XCHECK.md and audit/AUDIT.md. Role: Remediator. Charter: findings F-0012..F-0019 (see LEDGER).
  ```

Inside the session, a fixed sequence per finding:

1. **Validate**: is the quote still there? is the problem real? No → `disputed` (back to the Auditor) or `obsolete` (defect already gone).
2. **Census**: search the whole corpus for the pattern. ≥3 instances → escalate to a class finding (CF); members become `superseded-by-class`; the batch continues without them.
3. **Plan**: `audit/plans/RP-NNNN.md`, with exact edits, order, risks.
4. **Fix** the material.
5. **Self-check**: run the finding's "how to verify" procedure, then `xcheck record-fix <ID> --session <your 16-hex session id>`. An orchestrated session finds its id in the Orchestration context line of its prompt (`$XCHECK_SESSION_ID`). The verb binds the fixing session, so the `XCHECK.md` §3 rule that the Verifier is never the fixer can be checked.

If the project uses git, the session commits with finding IDs in the message; verification leans on those diffs.

### Step 5: Verify

When `fixed` findings have accumulated, a session of a different agent judges them (in the cross-agent default this is automatic):

- Codex: `$xcheck-verify`; Claude Code / OpenCode: `/xcheck-verify`
- By hand:

  ```
  Read audit/XCHECK.md and audit/AUDIT.md. Role: Verifier. Charter: all findings in status fixed. You did not write these fixes; try to prove them wrong.
  ```

Per finding: run its verification procedure, then look around the change for collateral damage. The verdict is recorded with `xcheck record-verdict <ID> --verdict closed|reopened --session <your 16-hex session id>`, held to the same evidence standard; the verb refuses a verdict on the verifier's own fix. For a class finding the check is a fresh census expecting a clean result, which catches "fixed 12 of 15".

### Step 6: Repeat and finish

- `reopened` → next Remediator batch (attempt counter grows; two in a row → `⚠ needs-human`).
- `disputed` → one written round of Auditor objection in the finding file, then you decide.
- Passes left in the queue → back to step 2.

The audit is complete when the pass queue is empty and every ledger row is terminal. One special case: a remainder of only `deferred` rows is complete *with* deferred debt: the automatic cycle is exhausted, but `deferred` is not terminal (§3), so those rows stay as your re-triage debt until you accept or reject them. `$xcheck-status` in Codex, `/xcheck-status` in Claude Code/OpenCode, or `xcheck status` runs this termination check for you.

## 7. Launchers: skills for Codex and Claude Code, commands for OpenCode

`orchestrated` is the supported execution path. §8's `xcheck next` and `xcheck loop` are how xcheck is meant to be run. They are the only path that puts all eight controls in force: sandbox profile, hard timeout, environment allowlist, log redaction, process-group kill, courier review of the diff, invocation envelope, session receipt.

Five of the six launchers are wrappers over that path. `/xcheck-plan`, `/xcheck-audit`, `/xcheck-triage`, `/xcheck-remediate` and `/xcheck-verify` do not run a role inside your agent. Each does preflight, says which role the orchestrator will dispatch and why, runs `xcheck next`, and reports what came back, so the work happens under all eight controls. Each carries that statement in its own body, in one canonical wording enforced byte for byte.

You lose the conversation: the role runs as a child process you do not talk to. You cannot steer it mid-thought or hand it an improvised charter. A charter change is a state change (`xcheck queue-pass`, `xcheck set-status`, …), typed by you at your own terminal.

Only `/xcheck-status` is still `uncontained-direct`, and it is read-only. It writes nothing and moves no machine state, so there is no diff for a courier to review and no effect for an envelope to record. See `SECURITY.md`.

`/xcheck-triage` hands off to nothing, because it cannot: a triage decision is yours, and the write boundary allows it only when no session is open. It brings you to the gate, presents the batch, and hands you the `xcheck set-status` lines for your decisions. You run them.

Six launchers, same logical names on every platform:

| Codex | Claude Code / OpenCode | Role | Auto-picked charter |
|---|---|---|---|
| `$xcheck-plan` | `/xcheck-plan` | Planner | wrapper: refuses unless the decision is `run-planner`, then `xcheck next` |
| `$xcheck-audit` | `/xcheck-audit` | Auditor | wrapper: refuses unless the decision is `run-auditor`, then `xcheck next` |
| `$xcheck-triage` | `/xcheck-triage` | Triage (dialogue) | wrapper: reaches `stop-triage`, presents the batch, hands you the commands |
| `$xcheck-remediate` | `/xcheck-remediate` | Remediator | wrapper: refuses unless the decision is `run-remediator`, then `xcheck next` |
| `$xcheck-verify` | `/xcheck-verify` | Verifier | wrapper: refuses unless the decision is `run-verifier`, then `xcheck next` |
| `$xcheck-status` | `/xcheck-status` | — (read-only) | direct, and the only one: queue progress, statuses, whose turn, termination check |

Run them in a session opened in the audited project root. There is no per-invocation charter override: the orchestrator builds every charter from `audit/state.json`, so what runs next changes when the state changes. The wrappers carry no role instructions; everything a session is bound by lives where the dispatched child reads it, in `runner.PROMPTS` and `audit/XCHECK.md`. The selftest checks that each of those contracts is present there, and `xcheck next` refuses to dispatch a Verifier over fixes whose `fixed-by` is missing, malformed, or its own session.

Re-run the launcher installer after any change to the skills (§5.4, `install-launchers.sh` is idempotent), so the installed copies and the generated OpenCode commands never fall behind.

**Codex note:** launchers install as skills in `~/.agents/skills/`. Codex can invoke them explicitly with `$xcheck-*` or implicitly from a matching request. For non-interactive use, the orchestrator drives Codex through `codex exec` with the same role charters (see §8).

**How a launcher session talks to you.** All six skills carry the same block of wording rules, generated from `styles/eli5.md` by `ci/render-style.py`: compression styles such as caveman are off, a term of art is explained the first time it appears, and four kinds of text are reproduced character for character: quoted evidence, finding ids, §5 statuses, and copy-paste commands. Nothing has to be installed into your own agent for this; the block travels inside each skill file. The block is **delivered** to every launcher skill through every packaging path, and this repository proves that by reading the bytes each transform produced, but it **does not verify** that a model then wrote in the style the block asks for. `styles/README.md` has the rest, including the optional standalone install.

## 8. The orchestrator

`bin/xcheck` does the courier work for you: it decides whose turn it is, launches the right agent CLI with the right charter, commits the session's results, and stops at your gates.

Every profile, limit, timeout and commit rule in §8 describes an `orchestrated` session, the kind `xcheck next` and `xcheck loop` launch. A session started by hand is `uncontained-direct` and gets none of it; `xcheck status` prints both facts. [How it works](docs/guide/how-it-works.md) walks through one session end to end.

```
xcheck [--project DIR] next               run one session (whoever's turn it is)
xcheck [--project DIR] loop               run until a human gate
xcheck [--project DIR] [--budget S] loop  same, with a wall-clock budget (checked between sessions)
xcheck [--project DIR] status [--json]    read-only dashboard
xcheck [--project DIR] unlock [--force]   diagnose a stale lock (--force removes it)
xcheck [--project DIR] cancel             ask the running session to stop (orderly)
xcheck [--project DIR] lint               ledger ↔ finding-file consistency check
xcheck [--project DIR] metrics [--json]   auditor accuracy, fix durability, distributions
xcheck [--project DIR] dashboard [--json] render audit/dashboard.html from the two payloads above
xcheck [--project DIR] [--dry-run] migrate   one-shot: legacy Markdown tree → state.json
xcheck [--project DIR] [--dry-run] upgrade   refresh the installed core + templates (§5.6)
xcheck selftest                           decision-logic + invariant self-checks
```

Flags for `loop`: `--step` (pause between sessions), `--max-sessions N` (cost cap), `--budget SECONDS` (wall-clock budget, checked before each session; a session already running is never interrupted). The first session always starts; after that, a session is refused when the time left is less than one `session_timeout`, because it could run that long. Whichever of `--budget`/`--max-sessions` hits first stops the loop. `--dry-run` prints the decision and the command without launching.

**Turn priority:** needs-human stop → verification of `fixed` → remediation (unfinished/reopened first, then accepted CF, then accepted findings) → audit passes → triage stop. Before any of that, a new audit with no `state.json` runs its Planner until the plan is complete. The loop halts at: a new audit's plan ready for `xcheck migrate`, triage needed, `⚠ needs-human`, a dispute, a norm-ratification gate, an inconsistent ledger (non-terminal rows with no legal move), a broken/unclosed class dependency, a corrupt ledger or missing coverage report, queue exhausted (or exhausted-with-deferred-debt), a session error, a non-converging cycle (`loop_progress_limit` consecutive sessions that repeat the same decision without moving `audit/`), or the orchestrator's own source changing mid-run (a self-remediation edited `bin/xcheck`; the loop runs the code image loaded at its start, so restart `loop` to pick up the fix). Each comes with an explanation of what is expected from you. This is the authoritative, exhaustive list of the orchestrator's halt points. It does not count the methodology's human gates: the human gates among these halts (`⚠ needs-human`, a dispute, a norm-ratification gate, and triage) are enumerated canonically in `XCHECK.md` §3, and this list does not re-count them.

**Your touch points in `loop` mode:** the routine ones are install → migrate the plan → triage sittings → final review, and on a clean run that is all. Every other halt above also needs you, and each prints what it expects. Between halts the loop runs by itself. [Usage patterns](docs/guide/usage-patterns.md#pattern-10-when-the-loop-hands-the-turn-back) has a table of the common halts and what to type.

### orchestrator.conf

Configuration is split in two, and xcheck enforces the split.

**Project keys** live in `audit/orchestrator.conf` inside the repository being audited. They say what this audit is *about*: how much a pass may file, when to stop for triage, a note appended to prompts, the optional discipline gates. A wrong value can make an audit worse; it cannot widen what a session may do.

**Policy keys** live in the operator profile, *outside* any audited repository. They say what your machine *executes* and the boundary it executes inside. That means the command each role runs, the containment profile, the environment allowlist, the container image, the egress allowlist, resource limits, retry, concurrency, and whether commits are pushed. A session can write anywhere under `audit/`, so a project that carried these keys would be setting the terms of its own audit. Writing one into `audit/orchestrator.conf` is refused at load, naming every offender and the lines to write instead.

The profile is found in this order, and the resolved path and its digest are printed at every dispatch:

1. `xcheck --policy /path/to/operator.conf …`: an explicit path wins;
2. `$XCHECK_OPERATOR_PROFILE`;
3. `$XDG_CONFIG_HOME/xcheck/operator.conf`.

A missing file does not end the search, but a `--policy` path that names nothing is an error. If nothing resolves, `next` and `loop` refuse, printing the order they tried. There is no fallback to defaults: that would run agent sessions under containment nobody chose. `operator-profile.sh` writes one from the template (§5.3); by hand, start from [`docs/operator.conf.example`](docs/operator.conf.example). The profile is read fail-closed: an unknown key, a project key, or a line that is not `KEY=VALUE` is refused by name, because a typo'd `sandbox_profil=container` is a containment setting that silently did nothing. Its sha256 is recorded as `policy_digest` on every dispatch, and the child never learns where the profile is.

| Key | Where | Default | Meaning |
|---|---|---|---|
| `planner_cmd`, `auditor_cmd`, `verifier_cmd` | **operator profile** — outside the subject | Codex (`codex exec ...`) | CLI command per role |
| `remediator_cmd` | **operator profile** — outside the subject | `claude -p --dangerously-skip-permissions` | CLI command for the Remediator (the flag is permitted only under an isolating sandbox profile — see *Execution model*) |
| `batch_size` | `audit/orchestrator.conf` | 8 | remediation batch size (AUDIT.md `remediation_batch_size` overrides) |
| `max_sessions` | **operator profile** — outside the subject | 20 | default session cap for `loop` |
| `loop_progress_limit` | `audit/orchestrator.conf` | 2 | stop the loop after N consecutive sessions that repeat the same decision and leave `audit/` unchanged — a non-converging cycle, not a cost cap (0 = off) |
| `triage_batch_cap` | `audit/orchestrator.conf` | 20 | stop for triage once N findings pile up in `reported` — smaller sittings. `0` is unbounded and is still a legal explicit choice; it is no longer the default, because unbounded is what produced this project's own 133 reported / 0 triaged backlog |
| `evidence_bundle` | `audit/orchestrator.conf` | `off` | `off` = the `evidence-bundle` verb writes nothing and says so. `on` = it exports a self-contained, checksummed bundle under the retention log root — subject manifest, policy digest, events, receipts, findings, patches, verdicts, provenance, the POPULATION it covers and the OMISSIONS it makes — plus a standalone `verify.py` that needs neither xcheck nor a network. Off by default because exporting evidence decides who sees it; no command ever reads a bundle back |
| `model_routing` | `audit/orchestrator.conf` | `off` | `off` = every role runs through one command with one model, which is the behaviour before this key existed. `on` = each dispatch resolves a route from a closed table keyed on (work kind, risk) — deterministic, cheap or strong — and records it on the session so a reader can ask which model produced which evidence. No model chooses the route. Unclassified work fails closed to the strong route, and a HIGH-risk pass can never be routed cheap, not even by an operator entry: savings are never bought out of a security verdict. Independence is decided by the independence machinery, not here |
| `budgets` | `audit/orchestrator.conf`; also the **operator profile**, as a ceiling the subject can only tighten (0.9.6, [docs/payer-ceiling.md](docs/payer-ceiling.md)) | `off` | `off` = nothing stops a run on cost, which is the behaviour before this key existed. `on` = the six ceilings below are evaluated BEFORE each dispatch, not once at the start of a run. Every figure is priced on measured sessions only and every refusal prints its coverage and the key to raise. No money is ever printed |
| `tokens_per_session` | `audit/orchestrator.conf`; also the **operator profile**, as a ceiling the subject can only tighten (0.9.6, [docs/payer-ceiling.md](docs/payer-ceiling.md)) | `0` (off) | Refuses when the measured mean cost of a session on this audit is over the ceiling |
| `tokens_per_pass` | `audit/orchestrator.conf`; also the **operator profile**, as a ceiling the subject can only tighten (0.9.6, [docs/payer-ceiling.md](docs/payer-ceiling.md)) | `0` (off) | Refuses when the measured spend per completed pass plus one more session would go over the ceiling |
| `tokens_per_audit` | `audit/orchestrator.conf`; also the **operator profile**, as a ceiling the subject can only tighten (0.9.6, [docs/payer-ceiling.md](docs/payer-ceiling.md)) | `0` (off) | Refuses when the REMAINING budget cannot cover one more session at the measured mean — before the overspend lands, not after. Set by the operator (with `tokens_per_session` as the reservation), it is an ADMISSION ceiling: `stop-ceiling` when the remaining tokens no longer cover one reservation |
| `no_progress_share_pct` | `audit/orchestrator.conf` | `0` (off) | Refuses when the share of measured tokens spent by sessions that recorded no canonical transition is over the bound. Ouroboros-4 reached 85.4% with nothing able to stop it |
| `charter_repeat_limit` | `audit/orchestrator.conf` | `0` (off) | How many times one charter may be dispatched. Distinct from `embargo`, which counts CONSECUTIVE sessions that declared no progress and clears when one does |
| `marginal_value_window` | `audit/orchestrator.conf` | `0` (off) | STOPS generation when the last N finished sessions produced no new ACCEPTED evidence. `xcheck status` prints the derived value and the arithmetic it came from |
| `okf` | `audit/orchestrator.conf` | `off` | `off` = no derived knowledge bundle, which is the behaviour before this key existed. `on` = `xcheck okf` may generate `audit/okf/` — six record types and three relations projected from state.json plus the event stream, as versioned JSONL with a checksummed manifest. It is DERIVED and disposable: if it disagrees with state.json it is wrong and is regenerated, never merged. Its back channel is PROPOSAL-ONLY and six forbidden effects are refused by name; a proposal reaches canonical state only when a human reads it and runs an existing write verb |
| `audit_over_critical` | `audit/orchestrator.conf` | `off` | `off` = an **open `critical` finding stops new Auditor passes** at a human gate naming it (open = non-terminal, not merely `reported`). Triage, remediation and verification are never gated — they are how the critical stops being open. `on` = keep auditing over it deliberately |
| `startup_deadline` | **operator profile** — outside the subject | 120 | kill a session that has written no output at all after N seconds (0 = off) |
| `output_deadline` | **operator profile** — outside the subject | 600 | kill a session that has written nothing beyond the banner and its own prompt echoed back, and recorded no canonical transition, after N seconds. It counts BYTES — a hang detector, named for what it measures. Derived from the W-06 run: the fastest working session finished everything in 439.3s, and the one that stalled held a slot for 3600s having written 1,764 bytes (0 = off) |
| `activity_deadline` | **operator profile** — outside the subject | 1200 | kill a session that has done nothing OBSERVABLE after N seconds. Activity is evidence the child cannot manufacture by printing: a canonical transition, a validated telemetry sidecar, or a write inside its own worktree. A child padding its log clears `output_deadline` and is caught here (0 = off) |
| `idle_deadline` | **operator profile** — outside the subject | 1200 | kill a session that has been silent for N seconds — longer than the entire duration of every session in that run (0 = off) |
| `log_dir` | **operator profile** — outside the subject | (resolved) | where RAW session logs are written — always outside the audited tree. Empty resolves `$XCHECK_LOG_DIR` → `$XDG_STATE_HOME/xcheck/logs/<project>` → the system temp directory, printed at each dispatch. `audit/logs-manifest.jsonl` keeps each log's name, size and sha256 in the repository |
| `cheap_model` | **operator profile** — outside the subject | _(none)_ | the model the `cheap` route runs. Unset REFUSES the dispatch: a route that records a label and runs the same model is worse than no routing |
| `strong_model` | **operator profile** — outside the subject | _(none)_ | the model the `strong` route runs. Reaches the provider through `{model}` in the role command |
| `telemetry_adapter` | **operator profile** — outside the subject | _(none)_ | which provider's usage shape this machine's wrapper returns: `codex`, `codex-stream` (the parent reads `codex exec --json`'s own event stream — the one that works under `container`; see [docs/codex-provider.md](docs/codex-provider.md)) or `fake`. Never inferred from the role command — a program named `codex` may be a wrapper or a shim. Unset means sessions record no provider figure |
| `log_retention_days` | **operator profile** — outside the subject | 30 | how old a log must be before `xcheck prune-logs --apply` deletes it. Pruning is never automatic |
| `push_after_commit` | **operator profile** — outside the subject | off | opt-in `git push` after courier commits |
| `scope_typing` | `audit/orchestrator.conf` | `off` | candidate (§12): require `admitted-scope` + both halves of `## Admitted scope` on a `fixed` finding |
| `construal_gate` | `audit/orchestrator.conf` | `off` | candidate (§12): require an admitted construal before a charter is dispatched |
| `embargo` | `audit/orchestrator.conf` | `off` | refuse to dispatch a charter whose last `embargo_after` sessions each ended WITHOUT recording a canonical transition. The held pass is removed from the queue so the loop routes to the next takeable one — a veto that answered "no" after the pick would freeze every other pass behind one bad charter — and a queue held end to end stops at a human gate naming the key. That key is `(role, charter, charter slice, orchestrator source digest)` — the charter slice being a digest of the pass's own queue entry, the findings filed against it and the policy in force, so a transition in a DIFFERENT pass no longer lifts this one's hold: every part computed by the orchestrator, none reported by the agent, because `rc=0` is exactly such a self-report. It lifts when canonical state moves or the orchestrator's own code changes; it does **not** lift on a rewritten report, a new log, a timestamp or a courier commit. **Off by default, and that is a measured result rather than caution:** replayed session by session over the one corpus that can score it, the gate refuses 117 of 136 barren sessions against a bar of 120 declared before the measurement — and kills 9 of the 15 productive ones. That corpus was produced by a build carrying a lock defect that made persistence the only way to succeed (10 of its 15 successes came after a barren streak, up to 53 long; 7 reached state only through a bypass), so it cannot tell a hopeless charter from a charter fighting a bug, and neither can this gate. `on` is one line and is how a run on a repaired build scores it |
| `embargo_after` | `audit/orchestrator.conf` | 3 | barren attempts against one charter bought before the next is refused. Read only when `embargo` is `on`; minimum 1, since 0 would hold every charter before a single attempt had been bought |
| `parallel_workers` | **operator profile** — outside the subject | 2 | how many agent sessions a parallel run may have IN FLIGHT at once. Before 0.9.1 the pool was one worker per non-overlapping pass, so a queue of three hundred disjoint passes dispatched — and billed — three hundred sessions simultaneously. Bounded below at 1: a pool of zero workers is a hang, not less concurrency |
| `max_sessions_per_run` | **operator profile** — outside the subject | 8 | the total number of sessions one parallel dispatch may start. Over it, the run refuses and names both the count and the limit — before anything launches, because a cap enforced at merge time cancels work that has already been paid for |
| `parallel_budget_minutes` | **operator profile** — outside the subject | 480 | the cost ceiling, in the only currency this tool can measure. Checked as `sessions × session_timeout` — the WORST case — before dispatch. It is **not money**: xcheck has no provider cost telemetry and will not print a dollar figure it cannot measure. 480 is `max_sessions_per_run × session_timeout`, so at the shipped defaults both ceilings bind at the same point and neither shadows the other; it starts to matter the moment one of those is raised without the other. `0` disables the ceiling |
| `parallel_confirm_above` | **operator profile** — outside the subject | 4 | above this many passes a fan-out needs a human's word: an explicit confirmation flag, or a `yes` typed at a terminal. With stdin not a terminal the run **refuses** rather than prompting — a prompt in a cron job is a hang, and a hang holding `audit/.lock` blocks the next run too. `0` means confirm every fan-out |
| `parallel_passes` | **operator profile** — outside the subject | `off` | run queued passes whose unit sets are disjoint at the same time. **Refused for most of 0.9.1's development, and the refusal is still live code:** a parallel pass is not couriered, so before artifact-aware merge its finding bodies, pass report and split remainder died with its worktree while the finding records survived — findings with no evidence under them, and a red `lint`. What lifts the refusal is not the version number but `parallel.preservation_probe()`, which carries a real artifact through the real apply path at the moment of the decision; break that machinery and `on` refuses again on the next call, naming what the probe found |
| `retry_limit` | **operator profile** — outside the subject | 0 | how many extra attempts a session gets after a `provider-error` or a `timeout` whose log names a transport failure. Every other outcome — `refused`, `blocked`, `crash`, `cancelled` — is never retried. **Default 0 since 0.9.1**: the retry gate decided whether anything had happened by re-reading canonical `state.json`, which is blind to a changed source file, an audit artifact and a commit, so a failure whose material effect had already landed looked effect-free and was dispatched again. A non-`ok` session no longer applies anything (see *quarantine* below), and a session now returns an account of what it did rather than leaving a caller to infer it |
| `session_note` | `audit/orchestrator.conf` | — | extra text appended to every session prompt (e.g. scope exclusions) |
| `sandbox_profile` | **operator profile** — outside the subject | per role: `readonly` for Auditor/Verifier, `worktree` for the rest | `worktree` \| `readonly` \| `none` \| `container` — see *Execution model*. An unknown name is refused, never guessed. `container` is opt-in and needs a working docker; without one it refuses rather than falling back |
| `env_allowlist` | **operator profile** — outside the subject | — | extra environment names (comma-separated) the child may inherit on top of the built-in allowlist |
| `session_timeout` | **operator profile** — outside the subject | 3600 | hard wall-clock bound per session, seconds; on expiry the child's whole process group is terminated |
| `kill_grace` | **operator profile** — outside the subject | 10 | seconds between SIGTERM and SIGKILL when a session is terminated |
| `cpu_seconds`, `address_space_mb` | **operator profile** — outside the subject | 0 (unset) | per-child `RLIMIT_CPU` / `RLIMIT_AS` (POSIX only; refuses to launch where `resource` is unavailable rather than pretending the limit applied) |
| `lease_ttl` | **operator profile** — outside the subject | 300 | seconds after which a lock whose heartbeat stopped is reclaimable by plain `xcheck unlock` |
| `container_image` | **operator profile** — outside the subject | `alpine/git@sha256:3b447678…` | the image `sandbox_profile=container` runs in, **pinned by digest — enforced, not recommended**. A bare tag, a short or upper-cased digest, a non-sha256 algorithm, or a tag with a digest hung off it are each refused at the door, naming the image and the `docker inspect` line that resolves it. The default carries `sh`, `git` and busybox — not python3 and not any agent CLI, so set this to an image carrying your agent before running real sessions under this profile |
| `unsafe_allow_unpinned_image` | **operator profile** — outside the subject | `off` | waives the pin refusal, and nothing else. The waiver is **written into the session log**, not merely printed, so a run carries its own record that the guarantee was dropped for it. It does not widen the network, the mounts, the dropped capabilities or any role's read-only default |
| `container_cpus`, `container_memory_mb`, `container_pids` | **operator profile** — outside the subject | 2, 2048, 256 | the bounds docker enforces on the child under `container`. All three must be non-zero: 0 means unlimited, and unlimited under a profile whose claim is a bound is refused rather than applied. `address_space_mb`, when set, wins over `container_memory_mb` |
| `egress_allowlist` | **operator profile** — outside the subject | *(empty)* | **off by default**, and off is byte-identical to before: the container keeps `--network=none`. Set it to hostnames or IPs (comma-separated) and the audit container joins an **internal** docker network with no route off it, alongside a dual-homed broker sidecar that permits exactly those hosts by HTTP `CONNECT` and appends host+verdict for every attempt to an egress log. Fails closed: a broker that cannot be established, or that dies mid-session, refuses the run rather than degrading to an open network. Only `sandbox_profile=container` can enforce it — under `worktree` it refuses, because the child has the host's network there. It does **not** stop prompt injection, shorten credential lifetimes, or proxy a package registry |
| `egress_uplink` | **operator profile** — outside the subject | `bridge` | the docker network the broker's second leg attaches to — where the allowlisted hosts actually are |
| `egress_broker_image` | **operator profile** — outside the subject | `alpine/git@sha256:3b447678…` | what the sidecar runs, **pinned by digest** for the same reason the audit container is: the broker is the thing deciding what leaves the run |

Every key above has a safe default when absent. An existing `orchestrator.conf` keeps working and gets new protections without an edit. [Performance](docs/guide/performance.md) explains which of these keys make an audit faster or cheaper, and what each costs.

**Withdrawn keys.** `diff_scope`, `evidence_cache` and `evidence_dir` were public keys until xcheck's fourth audit of itself. Each named a library that no production path called, so setting one changed nothing. All three are now refused by name in whichever file they appear in, rather than ignored. The modules are still in the tree, still tested, and marked EXPERIMENTAL with the condition that would promote them: see [docs/experimental.md](docs/experimental.md).

### Execution model

The agent reads an arbitrary repository, which is untrusted input: a README, a source comment or a test fixture in it can be written to talk the agent into doing something. So the sandbox limits what the agent can do, whatever its prompt says:

- **Sandbox profiles.** `worktree` (default for writing roles) runs the session in a disposable `git worktree` in a system temp dir; its changes come back as a patch the courier commits. `readonly` (default for Auditor and Verifier) is the same, except a change outside `audit/` is refused, not applied. `none` is an explicit opt-out. `container` runs the session inside docker with no network, an empty `$HOME`, the source mounted read-only, and bounded CPU, memory and processes. It is opt-in, and it refuses rather than falling back when docker is missing. If a worktree cannot be created, the runner refuses to launch. `SECURITY.md` states per profile which escape probe proved which guarantee.
- **Contained skip-permissions.** `--dangerously-skip-permissions` and its equivalents are permitted only under an isolating profile and refused otherwise.
- **Environment allowlist.** The child environment is *built*, not inherited: `PATH`, `HOME`, `LANG`, `TERM`, `TMPDIR` and friends, the `XCHECK_*` variables, and anything you name in `env_allowlist`. Cloud credentials and production tokens are not inheritable by accident.
- **Hard timeout and process-group kill.** On `session_timeout` the child's whole process group gets SIGTERM, then SIGKILL after `kill_grace` seconds, so a grandchild the agent spawned dies with it. Four deadlines kill a session earlier when it shows no sign of working.
- **Log redaction.** Child output is scrubbed of known secret values and token shapes before it is written. It is best-effort against accident, not against an adversary; the allowlist is the control that bounds an adversary.
- **Session lifecycle.** Every session ends in exactly one typed outcome: `ok`, `refused`, `blocked`, `provider-error`, `timeout`, `crash`, `cancelled` or `stalled` (killed by a deadline). Only an `ok` session's changes are applied; any other outcome's work goes into a **quarantine bundle** under `audit/quarantine/`, and `xcheck status` counts the bundles waiting for you.
- **A retry must prove nothing changed.** A `provider-error` or a transport `timeout` is retried only if `retry_limit` allows it and the project matches the snapshot taken before the session. **Residual risk:** this sees the repository only. A session that already sent an email, opened a pull request, posted to an API or spent provider budget has done something xcheck cannot see; a retry re-runs the charter that did it. That is why `retry_limit` defaults to `0`.

[How it works](docs/guide/how-it-works.md#sandbox-profiles-and-what-each-one-holds) has the details of each profile, and [`SECURITY.md`](SECURITY.md) the full trust model.

### Invocation envelope, events, and measured independence

Every dispatch is recorded as an **invocation envelope**, written by the orchestrator, in `state.sessions[]`:

`provider` · `agent_model` · `executable` + `executable_version` · `role` · `charter_hash` · `prompt_hash` · `state_revision` · `head_before` · `head_after` · `session_id` · `sandbox_profile` · `duration_s` + `exit_status` · `log_digest`

The hashes are sha256 over the exact bytes dispatched, and `log_digest` is of the redacted log, so anyone holding the log can check it. `executable_version` is a sha256 of the agent program's own bytes, taken without running it. A value that cannot be determined is recorded as `unknown`, explicitly. No write verb reaches `state.sessions[]`: the envelope is the orchestrator's testimony, never the session's claim about itself. That is what lets `record-fix` and `record-verdict` bind `fixed-by` to the open dispatch.

**Measured independence.** Each verdict records how independent it was, computed from the two envelopes:

| Level | Meaning |
|---|---|
| `cross-provider` | two different vendors ran the two sessions |
| `cross-model` | same provider, different model; a weaker claim |
| `same-provider-different-session` | same provider and model, different session; degraded |
| `unrecorded` | no envelope on one side; not measurable, degraded |
| `same` | the same session; refused (§3: the Verifier is never the fixer) |

`metrics` prints the distribution and marks the degraded levels; a fallback is acceptable only while it is displayed as one.

None of these levels proves the absence of shared training data, a shared cache, or the same human driving both sides. `cross-provider` means only that two different vendors ran the two sessions. Two `unknown` providers are never read as a difference.

**`audit/events.jsonl`** is an append-only stream, one JSON object per line: sessions dispatched and finished, every applied write verb, gates, leases. The courier commits it, so the record survives a clone. Its hash chain and its anchor in `state.json` make a rebuilt stream detectable, and `xcheck recover` reports a gap it can prove. **`--json` output** from `status` and `metrics` is validated by the producer against a declared closed schema. [How it works](docs/guide/how-it-works.md#the-record-events-hash-chain-recovery) explains the chain.

### Lock protocol (a lease)

All writing sessions share `audit/.lock`, a directory created atomically, so only one writing session runs at a time. While a session runs, the orchestrator renews a heartbeat inside it. A lock whose heartbeat stopped more than `lease_ttl` seconds ago is reclaimed by plain `xcheck unlock`, because a stopped heartbeat is evidence the owner is gone. A lock with a live heartbeat is never reclaimed that way. `xcheck unlock --force` removes a lock on your word that no writing session is running anywhere; use it only when you are sure.

### Courier commits

After every session the orchestrator commits only the session's own new dirt: the difference between `git status` before and after. Your pre-existing uncommitted work is never swept into an audit commit. If a path the session would write already carries your uncommitted edit, the orchestrator leaves that path untouched, names it, and stops; the audit will not report done until you commit or discard your edit. On a project without git no commit is made, and the session's changed files are the handoff.

### Health checks

- `xcheck lint`: orphaned rows or files, status drift, class-membership breaks, malformed rows. Read-only, exit 1 on issues. Single-record checks share the validator that `next`, `status` and `metrics` use; checks that compare records stay in `lint`.
- `xcheck metrics`: auditor accuracy, fix durability (closures an oracle re-observed on newer material), each finding's evidence level, findings by dimension, severity and status, class-finding totals, attempts, and token cost per canonical transition. A session that reported no token figure is counted as unmeasured, never as zero. No money figure is printed: that needs billing data this tool does not see.
- `xcheck dashboard`: writes `audit/dashboard.html`, one self-contained page over `status --json` and `metrics --json`. It is a static view: no network access, and no number those two payloads do not already publish.

## 9. Agent mapping and cross-agent discipline

Default mapping, and the reason for the name *cross*-check:

| Role | Default agent |
|---|---|
| Planner, Auditor, Verifier | Codex |
| Remediator | Claude |

The agent that judges a fix is never the agent (let alone the session) that wrote it.

All of it is remappable in the operator profile; any capable agent can play any role. Legitimate reasons to remap:

- **Provider content policy.** On security-sensitive dimensions (memory-safety, undefined behavior, exploitability) a provider's moderation may refuse the defensive review outright. The methodology is agent-agnostic: point that dimension's Auditor/Verifier at an agent that isn't blocked. It is one line in the operator profile.
- **Single-vendor setups.** Running everything on one agent works mechanically, but you lose the strongest safeguard. The minimum worth insisting on: the Verifier is a separate fresh session that has never seen the fix.

**Codex sandbox note:** Codex CLI sessions may keep `.git` read-only, so a Codex Verifier sometimes cannot commit its verdict updates. This is Codex's own sandbox, not an xcheck control, and it applies in both launch modes. Under `orchestrated` the courier finishes those commits automatically; under `uncontained-direct` check `git status` after each Codex session.

[Usage patterns](docs/guide/usage-patterns.md#pattern-4-two-providers-for-a-real-second-opinion) shows how to check which independence you got.

## 10. The audit/ directory in your project

```
audit/
├── XCHECK.md            # methodology core (installed copy) — the agents' rulebook
├── AUDIT.md             # this audit's plan: norms, dimensions, unit map, pass queue, limits
├── state.json           # THE canonical record: findings, queue, statuses, limits, coverage, session envelopes
├── LEDGER.md            # generated MIRROR of state.json: | id | title | severity | status | next | updated |
├── findings/            # one file per finding: F-NNNN-<slug>.md / CF-NNNN-<slug>.md
├── passes/              # one report per audit pass: P-NN-<dimension>.md
├── plans/               # remediation plans: RP-NNNN.md
├── construals/          # one construal per (role, charter) — empty unless `construal_gate` is on (§12)
├── events.jsonl         # append-only event stream, committed (§8) — never rewritten
├── templates/           # artifact skeletons (installed copy)
└── orchestrator.conf    # optional; per-project orchestrator config
```

`state.json` is the canonical record: statuses, the queue, limits, class membership, sessions. Each **finding file** holds that finding's whole story in prose: the evidence, validation, remediation and verification. `LEDGER.md` and each finding's header block are rendered from `state.json`; editing them changes nothing, and every command refuses the tree until the edit is undone or `xcheck render-views` overwrites it. `xcheck lint` checks the record and the files never silently diverge.

## 11. Class findings: fixing the disease, not the symptom

When a Remediator's census finds the same defect pattern at or above `class_threshold` instances (default 3), it opens a class finding (CF) with the full census, the root cause (usually a missing or ambiguous norm), and a strategy on an escalation ladder:

- **(a)** fix all census instances;
- **(b)** (a) + fix the norm so the class cannot recur;
- **(c)** (b) + an automated guard (grep check, lint rule, CI script), so the class becomes structurally hard to reintroduce.

A CF goes through your triage like any finding: accepting it sanctions a change across the whole project. Check the Census section (is the list complete?) and the strategy rung (too aggressive? too timid?). Rejecting a CF loses no work: its members return to the queue as individual fixes.

**Norm ratification gate.** An exceptional human gate, distinct from triage (`XCHECK.md` §3). If a (b)/(c) fix would change a norm, or must pick a side in a conflict between norms, the Remediator records the stop with `xcheck block-on-norm <CF-ID>` and the CF waits for you as the norm owner. Write your reasoning into the CF's `## Norm ruling` section and record the decision with `xcheck record-ruling <CF-ID> --norm N1 --ruled-by human:<you>` (or `--norm N1-over-N4` when one norm beats another). Only then will `xcheck record-fix` accept a fix on that CF. The gate is fail-closed: an absent, `pending` or unrecognised ruling keeps the CF stopped, and a note with no recorded ruling does not unblock it.

Verification of a CF re-runs the census expecting the clean result (zero instances for a presence class, zero orphans for an absence class) or an explicit list of documented exceptions. "Fixed 12 of 15" (and its absence twin "created 12 of 15") is `reopened`. [The methodology](docs/guide/methodology.md#fixing-the-disease-census-and-class-findings) explains why the class is the unit.

## 12. Candidate mechanisms (default off)

Three mechanisms ship as candidates: implemented, tested, and off by default except where noted. They are listed so you can turn them on; their effect has not been measured.

### Recorded refusal

A role that accepts a charter and cannot do it records a refusal instead of halting silently: one value from a closed vocabulary (`out-of-competence`, `blocked-dependency`, `charter-ambiguous`, `norm-conflict`, `material-missing`, `cost-exceeded`) plus prose saying what is missing. The Remediator and Verifier record it on the finding with `xcheck record-refusal`; the Planner records it in `AUDIT.md`, an Auditor in its pass report. `xcheck lint` rejects a value outside the vocabulary and a reason with an empty `## Refusal` section. `xcheck status`, `next` and `loop` then stop with `stop-refusal` naming the finding and the reason instead of re-dispatching the same charter. A refusal does not change the finding's status: the charter stays in force. Always on; there is no flag, and a project that never records one sees no change.

### Scope typing of a fix: `scope_typing` (default `off`)

When a Remediator records a fix, it declares what the fix covers: the machine half with `xcheck record-fix <ID> --session <16-hex> --scope "<what the guarantee covers>"`, the prose half as an `## Admitted scope` section with `### Covers` and `### Does not cover`. With `scope_typing=on`, `xcheck lint` fails a `fixed` finding missing either half. It does not judge whether the declaration is true; that comparison is the Verifier's work.

### Construal gate: `construal_gate` (default `off`)

Before a session may touch the material, it writes its own reading of the charter (task, approach, assumptions, stop conditions, out of scope) to `audit/construals/<key>.md`, and a different party admits it. With `construal_gate=on`, the orchestrator returns `run-construal` when no construal exists, `stop-construal` when one is not admitted yet, and dispatches the real charter only once it is. `xcheck lint` refuses a construal admitted by its own session. There is no pre-authorization: admission is always a separate act by someone who did not write it.

With both flags `off`, the orchestrator's decision is byte-identical to what it was before these mechanisms existed. This is checked directly against the pre-integration decision path.

## 13. Configuration reference

Defaults live in `XCHECK.md` §10. `AUDIT.md` §5 overrides them per project, and `xcheck migrate` reads that section once; from then on the limits live in `state.json`, and a later edit to `AUDIT.md` changes nothing. Change a limit with the verb:

```
xcheck set-limit reopen_limit 3
```

| Parameter | Default | Range | Meaning |
|---|---|---|---|
| `max_findings_per_pass` | 15 | 1–999 | Auditor stop condition; overflow splits the charter |
| `remediation_batch_size` | 8 | 1–999 | findings per Remediator session (large findings → cut to 3–4) |
| `class_threshold` | 3 | 1–999 | census instances at which a class finding opens |
| `reopen_limit` | 2 | 1–99 | consecutive reopens before `⚠ needs-human` |

`set-limit` refuses a key no consumer reads, a value that is not a whole number, and a value outside the range; it refuses `reopen_limit 0` by name, because below 1 the mandatory human stop never fires. The verb takes the lock, writes atomically, bumps `state_revision`, emits an event and validates at once. The queue and the catalogs change the same way, through `xcheck amend-pass`, `xcheck cancel-pass` and `xcheck update-catalog`. Nothing in this tool requires hand-editing machine state.

## 14. Language policy

The methodology and templates are English. Findings, plans, and reports are written in the operator's working language. Pick one per audit and stay consistent. Evidence quotes are always verbatim in the material's own language, regardless of the working language: the quote must remain findable by exact search.

## 15. Troubleshooting & FAQ

| Symptom | What it is | What to do |
|---|---|---|
| `⚠ needs-human` in the ledger | A finding was reopened twice, so the auto-cycle isn't converging | Read the finding file (the full history is there); decide by hand: sharpen the requirement, split it, or close it by fiat |
| `disputed` won't resolve | Auditor and Remediator disagree after the written round | You decide; agent ping-pong is forbidden by protocol |
| Quotes "not found" en masse | The material shifted a lot (e.g. after a class fix) | Normal: validation marks such findings `obsolete` or refreshes locators. Do nothing |
| Session exceeded its charter / no coverage report | Protocol violation | The pass does not count. Rerun with a smaller charter; if it recurs, reduce the default pass size in AUDIT.md |
| Ledger or a frontmatter block diverged from `state.json` | Somebody hand-edited a rendered view (§10) | Nothing repairs it on contact and nothing reads it back: the tool refuses the tree until it is resolved. `xcheck render-views` is the only repair: it overwrites each view from state and lists by name every one that had drifted, so read that list (those are the edits it destroyed) before re-recording the intent through a verb |
| `no operator profile found` from `xcheck next` or a launcher; `policy: none found` in `xcheck status` | This machine has no operator profile, so nothing may dispatch (§5.3) | `./operator-profile.sh --trust trusted\|untrusted /path/to/operator.conf` from the xcheck repository, then the `export` line it prints |
| Stale `audit/.lock` | A session died without releasing | `xcheck unlock` reclaims a lock whose heartbeat stopped more than `lease_ttl` seconds ago; `xcheck unlock --force` removes any lock, on your word that no writing session is active |

**Can one agent do everything?** Mechanically yes (roles are functions), but you lose the main safeguard: cross-verification by fresh eyes. Minimum worth insisting on: the Verifier is a separate fresh session that never saw the fix.

**Why can't triage be delegated to an agent?** It is the routine point where "what counts as a defect and what we spend cycles on" is decided, a value judgment that belongs to the material's owner. (The rarer exceptional gates in `XCHECK.md` §3 are the owner's judgment too, a norm ruling especially; triage is the one that recurs every cycle.)

**How much human time does a cycle take?** Target mode: minutes to launch sessions plus one substantive triage sitting per batch of passes. If your per-cycle time grows, protocol is being violated somewhere; see the table above.

**Too many findings, triage is heavy.** Raise the severity bar in charters ("critical/major only"), narrow the dimensions in AUDIT.md, or triage in batches by severity. `triage_batch_cap` makes the orchestrator stop for triage in smaller portions.

**An agent clearly broke protocol** (read "the whole project for context," left no coverage report). Don't count the session's result; rerun it. The protocol works only while violation = rerun, with no "just this once" exceptions.

**Where do I go next?** The [guide](docs/guide/README.md). Read [usage patterns](docs/guide/usage-patterns.md) for your situation and [performance](docs/guide/performance.md) when an audit is slow or expensive. Read [when not to use it](docs/guide/when-not-to-use.md) before you start on something unusual, and [how it works](docs/guide/how-it-works.md) when something surprises you.

## 16. License

MIT. See [LICENSE](LICENSE).

---

*Normative documents: `XCHECK.md` (the core, read by agents), `bootstrap.md` (install + session one-liners), `templates/` (artifact skeletons). This README is for you, the human operator; agents never need it.*
