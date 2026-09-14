# xcheck

**Cross-agent audit & remediation methodology for projects too large to trust to a single agent session** — codebases, book manuscripts, reference corpora, documentation sets.

Two AI agents and one human run a cycle:

```
audit (agent A) → triage (you) → remediate (agent B) → verify (agent A) → repeat
```

until the pass queue is empty and every finding reaches a terminal status. Agents communicate **only through files** in an `audit/` directory inside the audited project; you carry the baton between sessions and make one routine mandatory decision — which findings to fix. (Rarer exceptional halts also need you, all distinct from triage — enumerated once in `XCHECK.md` §3 and detailed in §8/§11 below; this contract does not re-count them.)

Repository: **github.com/vsov/xcheck** · License: MIT · Orchestrator: Python 3, stdlib only, zero dependencies.

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

A single long "review my project" conversation over a large body of material fails in a predictable way: the agent loses the thread, invents specifics, and hallucinates both findings and "fixes." xcheck replaces trust in agent memory with four mechanisms:

1. **Narrow chartered sessions.** Every session gets an exact scope ("dimension *terminology*, chapters 4–6, stop after 15 findings") and is obligated to stop rather than skim. A session with no charter must refuse to start.
2. **The evidence standard.** A finding without a character-exact quote, a locator, and a named violated norm is invalid by construction. The quote can be re-checked with `grep` — a hallucination does not survive.
3. **Validation before fixing.** The remediating agent must re-confirm every finding against the source before changing anything. Invented findings die here, marked `disputed`.
4. **Cross-verification.** The fix is judged by an agent that did not write it — a separate session with the standing instruction *"your job is to prove the fix wrong, not to confirm it."*

On top of that, every finding is screened for **systemicity** before it is fixed: a census of the whole corpus asks whether this is one instance of a repeating error. At three or more instances, the whole *class* is fixed — including the norm that produced it.

## 2. When to use it (and when not to)

**Good fit:** a project that does not fit into one attentive reading session — a book, a series of reference volumes, a codebase from ~10 files up, a documentation corpus — where the question is "what is systematically wrong here," not "fix this typo."

**Poor fit:** a point bugfix, a single-diff review, a 200-line file. There the cycle costs more than it returns — ordinary code review is enough.

## 3. Concepts in five minutes

### Roles

| Role | Who (default) | What they do |
|---|---|---|
| Planner | agent, 1 session | Inventories the project and its norms → `AUDIT.md`: dimensions, unit map, pass queue |
| Auditor | agent, N sessions | One pass = one dimension × a batch of units → findings + a coverage report |
| **Triage** | **you** | **`accepted` / `rejected` / `deferred` in the ledger — the one routine mandatory human gate** (rarer exceptional gates exist, all distinct from triage — enumerated in `XCHECK.md` §3) |
| Remediator | agent, N sessions | A batch of accepted findings: validate → census → plan → fix → self-check |
| Verifier | agent, N sessions | A verdict on every fix: `closed` or `reopened`. **Never the one who fixed it** |

Role ≠ agent: any capable agent can play any role. Two rules are hard: the Verifier never judges its own fix, and only one writing session runs at a time.

### Finding lifecycle

```
reported ──(triage: you)──► accepted | rejected | deferred
accepted ──(Remediator)───► validated ► planned ► fixed
                            └► disputed | obsolete | superseded-by-class
fixed ─────(Verifier)─────► closed | reopened
reopened ──(Remediator)───► planned ► fixed again (attempts += 1)
```

Terminal statuses: `closed`, `rejected`, `withdrawn`, `obsolete`, `superseded-by-class` (with a closed class finding). `deferred` is **not** terminal — it is your debt to re-triage.

After `reopen_limit` consecutive `reopened` verdicts on one finding (default 2), the ledger gets `⚠ needs-human` and the cycle stops for your decision.

### The evidence standard

Every finding must contain: (1) a character-exact quote findable by search; (2) a locator — `file:line` + symbol for code, file + section for text; (3) the norm the material violates, with its own quote (style contract, spec, ADR, sourced fact, or a contradicting passage). No norm → severity capped at `info`.

### Agent-as-pen triage

During triage the agent is the pen, not the decider: it presents findings, records **your** stated decisions verbatim, and never fills in what you did not say.

## 4. Repository layout

| Path | What it is |
|---|---|
| `XCHECK.md` | The methodology core. The **only** normative document agents read. Installed into each audited project. |
| `bin/xcheck` | The orchestrator's entry point — a ~20-line bootstrap into `xcheck.cli:main`. |
| `xcheck/` | The orchestrator itself (Python 3, stdlib only), layered `util → md_prose → state → views → validate → courier → runner → decision → write → cli`, plus `envelope.py` (the invocation record), `parallel.py` (concurrent passes, merged one at a time), `dashboard.py` (a view over the two JSON payloads that computes nothing), `migrate.py` and `upgrade.py` (two operator-typed verbs), and `selftest.py`, a decision-logic battery whose pass count is measured from the check lines it emits to `sys.stdout`. |
| `pyproject.toml` | Package metadata and the `xcheck` console script. No runtime dependencies, deliberately (N2). |
| `CHANGELOG.md`, `SECURITY.md` | What changed per release; the trust model — what containment does and does not protect against, scoped per launch mode (`orchestrated` vs `uncontained-direct`). **Read `SECURITY.md` before pointing this at code you did not write.** |
| `tests/` | The external battery. A test passes only when a real command's observable outcome is right — see `tests/README.md`. |
| `ci/` | `run-checks.sh` (the single entry point CI uses, runnable locally) and `check-split.py`. |
| `templates/` | Artifact skeletons: `finding.md`, `class-finding.md`, `pass.md`, `plan.md`, `construal.md`, plus `AUDIT-text.md` / `AUDIT-code.md` starting points. |
| `skills/` | Shared launcher skills used by Codex, Claude Code, and both plugin manifests. Five are wrappers over `xcheck next` and carry no methodology; `xcheck-status` is read-only and direct (§7). |
| `styles/` | `eli5.md`, the single source for how a launcher session words things to a person, and `styles/README.md` explaining it. `ci/render-style.py` copies its marked block into all six `skills/*/SKILL.md`; `--check` compares the six copies byte for byte and exits non-zero on a one-character difference. |
| `launchers/` | Per-CLI installer (`install-launchers.sh`). OpenCode commands are generated from `skills/` at install time. |
| `bootstrap.md` | From zero to a running cycle: install block, session one-liners, the full cycle step by step. |
| `install.sh` | One-command install of the methodology into a target project. |
| `.claude-plugin/`, `.codex-plugin/` | Claude Code and Codex plugin manifests over the checked-in `skills/` payload. |

## 5. Installation

### 5.1 Get the repo

```bash
git clone https://github.com/vsov/xcheck.git
cd xcheck
```

### 5.2 Install the methodology into a target project

```bash
./install.sh /path/to/your-project
```

This creates `your-project/audit/` containing `XCHECK.md`, an `AUDIT.md` template, `templates/`, and empty `findings/`, `passes/`, `plans/`. Nothing is audited yet — the Planner fills `AUDIT.md` in the first session, after which one operator command — `xcheck migrate` — loads that plan into `audit/state.json`, the canonical record.

There is no `LEDGER.md` to create: from 0.8 it is a **generated mirror** of `state.json`, rendered whenever state changes, never read back as authority and never hand-edited.

Notes:

- The default `AUDIT.md` template is text-oriented; for a codebase, copy `templates/AUDIT-code.md` over `audit/AUDIT.md` before planning.
- `install.sh` never touches audit state: `AUDIT.md` and `LEDGER.md` are created once and then left alone, and `state.json`, findings, passes and plans are not its business at all.
- It **does** overwrite `XCHECK.md` and the templates unconditionally — it is an installer, and it assumes those files are still xcheck's. To refresh an *existing* installation, use `xcheck upgrade` (§5.5), which checks that assumption instead of making it.
- `audit/` can be committed with the project (recommended — remediation commits referencing finding IDs make verification much cheaper) or added to `.gitignore` if the audit must stay out of history.

The installer also records what it put there, in two files `xcheck upgrade` later reads as evidence:

| File | What it holds |
|---|---|
| `audit/.provenance.json` | xcheck version, `XCHECK.md` digest, source ref (`git describe`), state schema version, install time |
| `audit/MANIFEST.sha256` | every shipped file and its digest at install time |

If neither `shasum -a 256` nor `sha256sum` is on `PATH`, the install still succeeds and says plainly that it wrote no provenance — a manifest nobody can verify is worse than none, because `upgrade` would trust it.

### 5.3 Install the launchers (optional but recommended)

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

### 5.4 Verify the toolchain

```bash
python3 bin/xcheck selftest    # expect: PASS (N/N) — all checks green, zero failures
```

### 5.5 Upgrading an installed copy

```bash
bin/xcheck --project /path/to/your-project --dry-run upgrade   # report only
bin/xcheck --project /path/to/your-project upgrade
```

`upgrade` exists because of a real finding (F-0146): `install.sh` copied the core into your project and after that nobody owned keeping the two in sync — the agents read `audit/XCHECK.md`, the developer edits the root one, and methodology, implementation and installed contract drift apart with nothing reporting it.

It reports every change **before** making any of them, and it decides from evidence rather than optimism:

| Target file | What happens |
|---|---|
| digest matches `MANIFEST.sha256` | **refreshed** — xcheck shipped it, xcheck owns it |
| absent | **installed** |
| already identical to this version | left alone |
| digest differs from the manifest | **refused**, named, untouched — you changed it, so it is yours |
| no manifest entry (pre-provenance install) | **refused** — xcheck cannot prove it shipped it |
| `AUDIT.md`, `LEDGER.md`, `state.json`, `events.jsonl`, `orchestrator.conf` | never shipped, never candidates |

A refused file keeps its old manifest entry, so it stays refused on the next run instead of being silently adopted. Reconcile it yourself — diff it against the shipped version, keep what is yours — then re-run. `upgrade` exits non-zero whenever anything was refused, so a wrapper cannot mistake "reported problems" for "clean".

If the installed copy is on a **different state schema**, `upgrade` stops without writing anything and names `xcheck migrate`. The schema is what forces a migration, never the release number — that is why the two versions are printed separately.

### 5.6 Compatibility

| | Supported |
|---|---|
| Python | 3.10, 3.11, 3.12 — the versions `.github/workflows/ci.yml` declares. Observed as of 0.9.1: the **release gate** runs green under 3.10 and 3.11 on the development machine — 3.11 in a clean venv carrying only the pinned toolchain from `pyproject.toml`'s `ci` extra, which is the workflow's own install step executed (`tests/test_ci_contract.py` prints the per-version result and refuses to pass if every version was skipped). 3.12 is **absent on that machine and therefore unobserved**. **The workflow itself has never been observed running**: every green figure in this project comes from one machine. The development repository has no remote at all, and 0.9.5 is the first release pushed to the public mirror — which is also the first thing that will run this workflow. What that run reports belongs in the release after it, not in this sentence. 0.9.1 made the workflow self-sufficient — it installs `build` as well as `ruff`, which is what the 0.9.0 audit found missing — but self-sufficient is not observed. 3.13 was dropped from the classifiers in 0.9.0 rather than given a matrix row that would promise coverage nobody has seen. 0.9.2 tiers the workflow by EVENT — a pull request runs the fast battery in one cell, `main` runs the full suite, a tag runs the package gate, and the six-cell OS-by-Python matrix moved to the weekly schedule — which cuts about 43 minutes of aggregate compute per pull request and changes nothing about the sentence above: the workflow has still never been observed running, so this is a change to what it WOULD do. |
| Platforms | macOS and Linux. Windows is not supported: the lock protocol, process-group kill and `resource` limits — the `orchestrated` machinery — are POSIX. |
| Git | 2.20+ — the floor is set by the two commands the tool needs (`git worktree add --detach`, `git describe --tags --always --dirty`), not by a test. No specific old version is verified: the only git ever exercised is the one on the developer machine. |
| State schema | **1** — written by 0.8.0, read unchanged by 0.8.x and 0.9.x. 0.9.0 changes no schema, so no migration |
| Reads schema 1 | xcheck ≥ 0.8.0. Earlier versions have no `state.json` at all and will not understand the tree. |
| Legacy Markdown tree | converted **once**, by `xcheck migrate`, on your command — never automatically |
| Agent CLIs | any headless CLI you can name in `orchestrator.conf`; `claude`, `codex` and `opencode` are the ones exercised in the pilots |

A release bump never implies a migration; a schema bump always does. Both numbers appear in `xcheck status`, in `audit/.provenance.json`, and in `CHANGELOG.md`.

## 6. Quickstart: your first audit

Two ways to run the cycle: **manual** (you paste one-liners or invoke launcher skills/commands per session) and **orchestrated** (`xcheck loop` runs sessions for you and stops at your gates). Both use the same files and rules; you can switch freely mid-audit.

A dry run first is worth 15 minutes: install `audit/` into a throwaway directory and walk one invented finding through every status by hand, including creating and rejecting a class finding. You will learn the mechanics and vocabulary before agents enter the loop.

### Step 1 — Plan

Open a session of your planning agent **in the audited project root** and run:

- Codex with skills: `$xcheck-plan`
- Claude Code / OpenCode with launchers: `/xcheck-plan`
- Any CLI, by hand:

  ```
  Read audit/XCHECK.md. Role: Planner. Charter: inventory this project and its norms; produce audit/AUDIT.md. Do not audit anything yet.
  ```

The session catalogs the project's norms (style contracts, specs, ADRs, glossaries), picks audit dimensions, slices the material into units, and builds the pass queue — a checklist of "dimension × unit batch" passes.

**Your check afterwards (2 minutes):** open `AUDIT.md`. Does every dimension name a norm source? Are passes reasonably sized (heuristic: ≤3 chapters of text or ≤2 kloc of code per pass)? Cross out passes you don't want — trimming scope here is cheap and needs no gate.

### Step 2 — Audit passes

One session per pass, in queue order:

- Codex: `$xcheck-audit` (auto-picks the first unchecked pass; ask it to run `P-03` to override)
- Claude Code / OpenCode: `/xcheck-audit` (`/xcheck-audit P-03` overrides)
- By hand:

  ```
  Read audit/XCHECK.md and audit/AUDIT.md. Role: Auditor. Charter: pass P-01. Stop conditions per charter.
  ```

The session reads the charter's units attentively, checks them against the dimension's norm, writes finding bodies to `audit/findings/` and a pass report to `audit/passes/` — and records each one with `xcheck file-finding`, closing the pass with `xcheck record-coverage`. The ledger row and the pass's completion are *rendered* from that, not written: there is no row to add and no checkbox to tick. Default cap: 15 findings per pass; scope overflow splits the charter and appends the tail to the queue with `xcheck queue-pass`.

**What to check in a pass report:** the Coverage section must explicitly list what was NOT covered. An empty NOT COVERED with half the units skimmed means the session broke protocol — the pass does not count.

You can run several passes back to back and triage the accumulated pile — triaging after every pass is not required.

### Step 3 — Triage (your gate)

- Codex: `$xcheck-triage`; Claude Code / OpenCode: `/xcheck-triage`. In dialogue mode, the agent presents findings in groups (severity × dimension) with quotes; you answer in chat ("accept all critical, reject F-0031, show me CF-0002 in detail"); the agent records exactly what you said with `xcheck set-status`, one invocation per finding.
- By hand: read the pile with `xcheck status` (or `audit/LEDGER.md`, which mirrors it) and record each decision yourself:

```bash
xcheck set-status F-0031 rejected
```

  Only `accepted`, `rejected` and `deferred` are yours. Batch decisions are normal ("all critical and major → accepted" in one sweep), one command each. When a title alone doesn't settle it, open the finding file — the quote and reasoning are there.

The verb is the whole act: it refuses any transition §5 does not allow, writes `audit/state.json`, and re-renders the ledger and the finding's frontmatter block from it. There is no sync step and no carrying your decision into files afterwards — and typing into a mirror instead changes nothing at all.

### Step 4 — Remediate

- Codex: `$xcheck-remediate` (name a finding range or CF id in the request to override the auto-pick)
- Claude Code / OpenCode: `/xcheck-remediate` (`/xcheck-remediate F-0012..F-0019` or a CF id overrides)
- The override changes only *which* IDs are worked, never the lifecycle gate: every named ID must be in a remediation-eligible status (`accepted`, `reopened`, `validated`, `planned`, or an unfinished member of an open RP). A `reported`/`fixed`/`closed` (or any terminal) ID is dropped or stops the session with the offending statuses — a range never rewrites a finding outside a remediation state.
- By hand:

  ```
  Read audit/XCHECK.md and audit/AUDIT.md. Role: Remediator. Charter: findings F-0012..F-0019 (see LEDGER).
  ```

Inside the session, a mandatory sequence per finding:

1. **Validate** — is the quote still there? is the problem real? No → `disputed` (back to the Auditor) or `obsolete` (defect already gone).
2. **Census** — search the whole corpus for the pattern. ≥3 instances → escalate to a class finding (CF); members become `superseded-by-class`; the batch continues without them.
3. **Plan** — `audit/plans/RP-NNNN.md`: exact edits, order, risks.
4. **Fix** the material.
5. **Self-check** — run the finding's "how to verify" procedure, then `xcheck record-fix <ID> --session <your 16-hex session id>`. The verb is what makes the fix provable: it binds the fixing session so §3's Verifier ≠ fixer rule can be checked.

If the project uses git, the session commits with finding IDs in the message — verification will lean on the diffs.

### Step 5 — Verify

When `fixed` findings have accumulated — a session of a **different** agent (in the cross-agent default this is automatic):

- Codex: `$xcheck-verify`; Claude Code / OpenCode: `/xcheck-verify`
- By hand:

  ```
  Read audit/XCHECK.md and audit/AUDIT.md. Role: Verifier. Charter: all findings in status fixed. You did not write these fixes; try to prove them wrong.
  ```

Per finding: run its verification procedure, then adversarially inspect the surroundings of the change for collateral damage. Verdict recorded with `xcheck record-verdict <ID> --verdict closed|reopened --session <your 16-hex session id>`, held to the same evidence standard; the verb refuses a verdict on the verifier's own fix. For a CF the verification is a fresh census expecting the polarity's clean result — zero instances for a presence class, zero orphans (every anchor's twin-search now returns its twin) for an absence class — which catches both "fixed 12 of 15" and its absence twin "created 12 of 15."

### Step 6 — Repeat and finish

- `reopened` → next Remediator batch (attempt counter grows; two in a row → `⚠ needs-human`).
- `disputed` → one written round of Auditor objection in the finding file, then you decide.
- Passes left in the queue → back to step 2.

**The audit is complete** when the pass queue is empty and every ledger row is terminal. One special case: a remainder of only `deferred` rows is **complete *with* deferred debt** — the automatic cycle is exhausted, but `deferred` is not terminal (§3), so those rows stay as your re-triage debt until you accept or reject them. `$xcheck-status` in Codex, `/xcheck-status` in Claude Code/OpenCode, or `xcheck status` runs this termination check for you.

## 7. Launchers: skills for Codex and Claude Code, commands for OpenCode

**`orchestrated` is the authoritative execution path.** §8's `xcheck next` and `xcheck loop` are how xcheck is meant to be run: they are the only path that puts all eight controls in force — sandbox profile, hard timeout, environment allowlist, log redaction, process-group kill, courier review of the diff, invocation envelope, session receipt. Everything in this section is the other path.

**Five of the six launchers are wrappers over that path.** `/xcheck-plan`, `/xcheck-audit`, `/xcheck-triage`, `/xcheck-remediate` and `/xcheck-verify` no longer run a role inside your agent. Each does preflight, says which role the orchestrator will dispatch and why, runs `xcheck next`, and reports what came back — so the work happens under all eight controls, and the launcher is a convenience around the authoritative path instead of a way around it. Each carries that statement in its own body, in one canonical wording enforced byte-for-byte.

That is a real loss, stated where you meet it: **the conversation is gone.** The role runs as a child process you do not talk to. You cannot steer it mid-thought or hand it an improvised charter — a charter change is now a state change (`xcheck queue-pass`, `xcheck set-status`, …), typed by you at your own terminal. The old direct launchers offered that dialogue by giving up all eight controls, and shipping both while calling one of them authoritative is what the third audit called a release blocker: a warning is not a control.

Only `/xcheck-status` is still **`uncontained-direct`**, and it is read-only. It writes nothing and moves no machine state, so there is no diff for a courier to review and no effect for an envelope or a receipt to attest; it carries the full disclosure of what running inside your own tool costs, and the reason it is exempt. See `SECURITY.md`.

`/xcheck-triage` is the one wrapper that hands off to nothing, because it cannot: a triage decision is a human-owned transition and the write boundary allows it only when no session is open. It brings you to the gate, presents the batch, and hands you the exact `xcheck set-status` lines for the decisions you make. You run them.

Six launchers, same logical names on every platform:

| Codex | Claude Code / OpenCode | Role | Auto-picked charter |
|---|---|---|---|
| `$xcheck-plan` | `/xcheck-plan` | Planner | wrapper: refuses unless the decision is `run-planner`, then `xcheck next` |
| `$xcheck-audit` | `/xcheck-audit` | Auditor | wrapper: refuses unless the decision is `run-auditor`, then `xcheck next` |
| `$xcheck-triage` | `/xcheck-triage` | Triage (dialogue) | wrapper: reaches `stop-triage`, presents the batch, hands you the commands |
| `$xcheck-remediate` | `/xcheck-remediate` | Remediator | wrapper: refuses unless the decision is `run-remediator`, then `xcheck next` |
| `$xcheck-verify` | `/xcheck-verify` | Verifier | wrapper: refuses unless the decision is `run-verifier`, then `xcheck next` |
| `$xcheck-status` | `/xcheck-status` | — (read-only) | direct, and the only one: queue progress, statuses, whose turn, termination check |

Run them in a session opened **in the audited project root**. A per-invocation charter override is no longer accepted anywhere: the orchestrator builds every charter from `audit/state.json`, so what runs next changes when the state changes.

The wrappers are now genuinely thin, and thin in a way the previous version only claimed: preflight → confirm the decision → `xcheck next` → report. They carry no role instructions at all, because they do not play a role. Everything a session is bound by — the lock protocol, the construal gate, the §5 refusal route, session hygiene, the Verifier's independence stance — lives where the dispatched child reads it: `runner.PROMPTS` and `audit/XCHECK.md`. The selftest checks that each of those contracts is present at its new home, so emptying the launchers could not quietly empty the contract. Cross-agent protections are stronger than before, not preserved-as-is: `xcheck next` refuses to dispatch a Verifier over fixes whose `fixed-by` is missing, malformed, or its own session, and the writing lock is held by the orchestrator around the whole transaction rather than by an agent following instructions.

Re-run the launcher installer after any change to the skills (§5.3, `install-launchers.sh` is idempotent), so the installed copies and the generated OpenCode commands never fall behind.

**Codex note:** launchers install as skills in `~/.agents/skills/`. Codex can invoke them explicitly with `$xcheck-*` or implicitly from a matching request. For non-interactive use, the orchestrator drives Codex through `codex exec` with the same role charters — see the next section.

**How a launcher session talks to you.** All six skills carry the same block of wording rules, generated from `styles/eli5.md` by `ci/render-style.py`: compression styles such as caveman are off, a term of art gets explained the first time it appears, and four kinds of text are reproduced character for character instead of reworded — quoted evidence, finding ids, §5 statuses, and copy-paste commands. Nothing has to be installed into your own agent for this; the block travels inside each skill file. State the limit plainly: the block is **delivered** to every launcher skill through every packaging path, and this repository proves that by reading the bytes each transform produced, but it **does not verify** that a model then wrote in the style the block asks for. `styles/README.md` has the rest, including the optional standalone install.

## 8. The orchestrator

`bin/xcheck` removes the courier work: it decides whose turn it is, launches the right agent CLI with the right charter, commits the session's results, and stops at your gates.

Everything in §8 — every profile, limit, timeout and commit rule below — describes an **`orchestrated`** session, the kind `xcheck next` and `xcheck loop` launch. A session started by hand through a §7 launcher is `uncontained-direct` and gets none of it; `xcheck status` prints both facts.

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
xcheck [--project DIR] [--dry-run] upgrade   refresh the installed core + templates (§5.5)
xcheck selftest                           decision-logic + invariant self-checks
```

Flags for `loop`: `--step` (pause between sessions), `--max-sessions N` (cost cap), `--budget SECONDS` (wall-clock budget — checked **before each session**, so the loop stops once elapsed time reaches it but a session already running is never interrupted; whichever of `--budget`/`--max-sessions` hits first), `--dry-run` (print the decision and the command without launching).

**Turn priority:** needs-human stop → verification of `fixed` → remediation (unfinished/reopened first, then accepted CF, then accepted findings) → audit passes → triage stop. The loop halts at: triage needed, `⚠ needs-human`, a dispute, a norm-ratification gate, an inconsistent ledger (non-terminal rows with no legal move), a broken/unclosed class dependency, a corrupt ledger or missing coverage report, queue exhausted (or exhausted-with-deferred-debt), a session error, a **non-converging cycle** (`loop_progress_limit` consecutive sessions that repeat the same decision without moving `audit/`), or the **orchestrator's own source changing mid-run** (a self-remediation edited `bin/xcheck`; the loop runs the code image loaded at its start, so restart `loop` to pick up the fix) — each with an explanation of what is expected from you. This is the authoritative, exhaustive list of the **orchestrator's halt points**, not a count of the methodology's human gates: the human gates among these halts (`⚠ needs-human`, a dispute, a norm-ratification gate, and triage) are enumerated canonically in `XCHECK.md` §3 — this list does not re-count them.

**Your touch points in `loop` mode:** the routine one is install → triage sittings → final review, and on a clean run that is all. But every other halt above also needs you: `⚠ needs-human`, a dispute, a norm-ratification gate, a session error, a re-triage of deferred debt (the `exhausted-with-deferred-debt` stop — `deferred` is not terminal, §3, so accept or reject to fully close), or a repair (corrupt ledger / missing coverage / broken class link / an inconsistent ledger — non-terminal rows with no move), a non-converging cycle (sessions repeating without progress), or a restart after a self-edit to `bin/xcheck`. Between halts the loop runs by itself; it never silently works around a gate that wants a human, and it never keeps spinning on unchanged state or runs on a stale image of its own edited source.

### orchestrator.conf

Configuration is split in two, and the split is enforced rather than advised.

**Project keys** live in `audit/orchestrator.conf` inside the repository being audited.
They say what this audit is *about* — how much a pass may file, when to stop for triage,
a note appended to prompts, the optional discipline gates. A wrong value makes an audit
worse, not dangerous.

**Policy keys** live in the **operator profile**, *outside* any audited repository.
It is resolved in a documented order, and the resolved path and its digest are printed
at every dispatch:

1. `xcheck --policy /path/to/operator.conf …` — an explicit path wins;
2. `$XCHECK_OPERATOR_PROFILE`;
3. `$XDG_CONFIG_HOME/xcheck/operator.conf`.

A source that names a file that does not exist does not end the search (an operator who
exported the variable before writing the file gets the next candidate), but a `--policy`
path that names nothing is an error — a path typed on the command line was meant. If
nothing resolves, `next` and `loop` **refuse**, printing the order they tried and the
file to create. There is deliberately no fallback to defaults: every policy key except
the role commands has a safe default, so falling back would run agent sessions under
containment nobody chose. Start from [`docs/operator.conf.example`](docs/operator.conf.example).
The profile is read fail-closed — an unknown key, a project key, or a line that is not
`KEY=VALUE` is refused by name, because a typo'd `sandbox_profil=container` is a
containment setting that silently did nothing. Its sha256 is recorded as `policy_digest`
on every dispatch envelope, so "which policy was in force that day" is answerable
afterwards. The child never learns where the profile is: `XCHECK_OPERATOR_PROFILE` is the
one `XCHECK_*` variable stripped from the session's environment. They say
what the operator's machine *executes* and the boundary it executes inside: the command
each role runs, the containment profile, the environment allowlist, the container image,
the egress allowlist, resource limits, retry, concurrency, and whether commits are
pushed. A session can write anywhere under `audit/`, the courier ships that edit, and the
next dispatch would execute it — so a subject that carried these keys would be setting
the terms of its own audit. Writing one into `audit/orchestrator.conf` is **refused at
load**, naming every offender and the exact lines to write instead.

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
| `budgets` | `audit/orchestrator.conf` | `off` | `off` = nothing stops a run on cost, which is the behaviour before this key existed. `on` = the six ceilings below are evaluated BEFORE each dispatch, not once at the start of a run. Every figure is priced on measured sessions only and every refusal prints its coverage and the key to raise. No money is ever printed |
| `tokens_per_session` | `audit/orchestrator.conf` | `0` (off) | Refuses when the measured mean cost of a session on this audit is over the ceiling |
| `tokens_per_pass` | `audit/orchestrator.conf` | `0` (off) | Refuses when the measured spend per completed pass plus one more session would go over the ceiling |
| `tokens_per_audit` | `audit/orchestrator.conf` | `0` (off) | Refuses when the REMAINING budget cannot cover one more session at the measured mean — before the overspend lands, not after |
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
| `telemetry_adapter` | **operator profile** — outside the subject | _(none)_ | which provider's usage shape this machine's wrapper returns: `codex` or `fake`. Never inferred from the role command — a program named `codex` may be a wrapper or a shim. Unset means sessions record no provider figure |
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

Every key above has a **safe default when absent**, so an existing `orchestrator.conf` keeps working and gets the new protections without an edit.

**Withdrawn keys.** `diff_scope`, `evidence_cache` and `evidence_dir` were public keys until the fourth industrial-readiness audit and are gone now. Each named a library that no production path called, so setting one changed nothing while the documentation said otherwise. Both are now **refused by name** in whichever file they appear in, rather than ignored — a conf key that silently does nothing is worse than one that is not offered. The modules are still in the tree, still tested, and marked EXPERIMENTAL with the condition that would promote them: see [docs/experimental.md](docs/experimental.md).

### Execution model

The child agent CLI reads an **arbitrary repository**, which is untrusted input: a README, a source comment or a test fixture in it can be written to talk the agent into doing something. So the agent is bounded by capability, not only by prompt.

- **Sandbox profiles.** `worktree` (default for writing roles) runs the session in a disposable `git worktree` created off the current HEAD **in a system temp dir**, seeded with your uncommitted work so the session sees the tree it would have seen in place; its changes come back as a patch and are committed by the usual courier. `readonly` (default for Auditor and Verifier, which judge the material and have no business editing it) is the same worktree, except a change outside `audit/` is **refused, not applied** — the patch is kept under `audit/orchestrator-logs/` so the work is not lost. `none` is an explicit opt-out that runs the child in the project itself. If the worktree cannot be created, the runner **refuses to launch**; it never degrades to running in the project.
- **OS-enforced containment (opt-in).** `sandbox_profile=container` runs the session inside docker: `--network=none`, an empty tmpfs `$HOME`, the source checkout mounted **read-only** at `/src`, exactly two writable mounts (a disposable *clone* at `/work` and one output dir), non-zero `--cpus`/`--memory`/`--pids-limit`, `--cap-drop ALL`, `--security-opt no-new-privileges`, and **no host socket** — never the docker socket. The tree is a clone rather than a linked worktree because a worktree's `.git` is a file pointing at a host path that does not exist inside the container. Requesting `container` without a working docker **refuses**, naming docker and the three alternatives; it never falls back. What the profile enforced is recorded in the envelope as a capability report, and `xcheck status` prints whether the last session's containment was *verified at launch*. The guarantees are tested by escape probe — each attempt runs under `container` (must fail) and under `worktree` (must succeed) in the same test — and `SECURITY.md` states per profile which probe proved which row. The default image carries `sh`, `git` and busybox only: set `container_image` to an image with your agent CLI. **The digest pin is enforced** — an unpinned reference refuses rather than launching, because the escape matrix was measured against specific bytes and a mutable tag is not those bytes. `unsafe_allow_unpinned_image=on` waives it and writes the waiver into the run's log.
- **Contained skip-permissions.** `--dangerously-skip-permissions` (and equivalents like `--yolo`) is **permitted only under an isolating profile** and refused with an addressed message otherwise. It is not deleted: removing it would push you to run sessions outside xcheck entirely, which removes every control at once. The ambient grant becomes a contained one.
- **Environment allowlist.** The child environment is *built*, not inherited: `PATH`, `HOME`, `LANG`, `TERM`, `TMPDIR` and friends, plus the `XCHECK_*` orchestration variables and anything you name in `env_allowlist`. Everything else is dropped, so cloud credentials, signing keys and production tokens are not inheritable by accident.
- **Hard timeout and process-group kill.** The child leads its own process group (`start_new_session`); on timeout it gets SIGTERM, `kill_grace` seconds, then SIGKILL — to the **group**, so a grandchild the agent spawned dies with it. Every `subprocess` call in the package carries an explicit `timeout`.
- **Log redaction.** Child output is scrubbed before it is written: values of secret-ish environment variables passed to the child, plus well-known token shapes. Stated honestly — this is best-effort against **accident** (an agent echoing a key it was handed), not against an adversary with write access to the child. The control that bounds an adversary is the allowlist, not the redactor.
- **Session lifecycle.** Every session ends in exactly one typed outcome: `ok`, `refused`, `blocked`, `provider-error`, `timeout`, `crash`, or `cancelled` — so "the agent declined" is not confused with "the provider fell over". `xcheck cancel` requests an orderly stop; the orchestrator kills the child's process group, releases the lock, and does **not** apply the cancelled session's changes.
- **Only an `ok` session's changes are applied (0.9.1).** Every other outcome — `refused`, `blocked`, `provider-error`, `timeout`, `crash` — has its work captured into a **quarantine bundle** under `audit/quarantine/` and applied to nothing: the patch, a manifest naming the outcome, session id, patch sha256, HEAD at capture and the paths it touches, and a README saying how to inspect, apply or discard it. Canonical state does not move, so a session that did not finish decides nothing. Until 0.9.0 these outcomes were couriered into the project like a success, which meant a crashed session silently modified the repository under audit. The console names the bundle and `xcheck status` reports the pending count, human and `--json`, for the operator who was not watching.
- **A retry must prove the absence of material effects (0.9.1).** Before re-dispatching a `provider-error` or a `timeout`, the orchestrator compares the project against the snapshot it took before the session — tracked changes, untracked files, `audit/` artifacts and a moved git HEAD, minus its own bookkeeping (the session log, the envelope journal, `audit/state.json`) — and refuses the retry if anything moved, naming the files. The session's own `RunResult` is consulted too and either answer alone blocks: neither authorises a retry, both can veto it. The console prints how many paths were compared, so "nothing changed" cannot be the answer of a check that looked at nothing. **Residual risk, stated plainly:** this bounds repeated effects *inside the repository*. A session that already sent an email, opened a pull request, posted to an API or spent provider budget before its transport failed has done something xcheck cannot see and cannot undo; a retry re-runs the charter that did it. That is the reason `retry_limit` defaults to `0` and raising it is an operator's decision about their own charters, not a setting to turn on by default.

### Invocation envelope, events, and measured independence

A 16-hex session id proves that *some other session* existed. It does not prove another provider, another model, another toolchain or another operator. So every dispatch is recorded as an **invocation envelope** — thirteen fields, written by the orchestrator, in `state.sessions[]`:

`provider` · `agent_model` · `executable` + `executable_version` · `role` · `charter_hash` · `prompt_hash` · `state_revision` · `head_before` · `head_after` · `session_id` · `sandbox_profile` · `duration_s` + `exit_status` · `log_digest`

The hashes are sha256 over the exact bytes dispatched, and `log_digest` is of the **redacted** log as written, so anyone holding the log can check it. `executable_version` is a sha256 of the resolved executable's own bytes, obtained **without running it**: the orchestrator identifies the agent CLI it is about to launch, it does not interview it. Until 0.9.2 this field held the output of `<exe> --version`, which meant the first thing every dispatch did was execute an operator-configured binary on the host — before `sandbox.enter()`, so under `container` as much as under `none` (F-0098). Where the executable cannot be resolved or read, the field is `unknown`. A value that cannot be determined — an agent CLI in no provider table, a role command that names no model — is recorded as `unknown`, **explicitly**: an absent field and a guessed one are the two ways a provenance record lies.

There is deliberately **no write verb** that reaches `state.sessions[]`. The envelope is the orchestrator's testimony about a session, never the session's own claim about itself — which is what lets `fixed-by` be *bound*: `record-fix` and `record-verdict` take the session id from the open dispatch record, and an agent that supplies a different one is refused with a message naming both. In a project with no envelopes at all (a hand-run audit) the claim still stands, but it is printed as `provenance UNBOUND` rather than passed off as evidence.

**Measured independence.** A verdict now records *how* independent it actually was, computed from the two envelopes:

| Level | Meaning |
|---|---|
| `cross-provider` | two different vendors ran the two sessions |
| `cross-model` | same provider, different model — a weaker claim |
| `same-provider-different-session` | same provider and model, different session — **degraded** |
| `unrecorded` | no envelope on one side — not measurable, **degraded** |
| `same` | the same session — refused (§3: the Verifier is never the fixer) |

`metrics` prints the distribution and marks the degraded levels; a fallback is acceptable only while it is displayed as one.

**The ceiling, stated plainly:** none of these levels proves the absence of shared training data, a shared cache, or the same human driving both sides. `cross-provider` means two different vendors ran the two sessions. That is all it means. Two `unknown` providers are never read as a difference.

**`audit/events.jsonl`** is an append-only stream — opened `"a"`, one compact JSON object per line, each with `ts`, `event`, `session_id` and a typed payload. Events: `session_dispatched`, `session_finished` (with the typed outcome), `state_transition` (one per **applied** write verb — a refusal changed nothing and emits nothing), `gate_reached`, `lease_acquired`, `lease_released`, `session_abandoned`. It **is committed** by the courier, deliberately: durability of the record is the product here, and a gitignored stream does not survive a clone. The cost is a chatty history — the file grows by a handful of lines per session and is never rewritten, so prune it by rotation if it becomes unwieldy, never by editing lines in place.

It is also excluded, along with `state.sessions[]` and `state_revision`, from the no-progress fingerprint the loop uses: the orchestrator's own bookkeeping about a session is not the session's effect on the material, and counting it would make an agent that did nothing look productive forever.

**`--json` output.** `status --json` and `metrics --json` emit a payload carrying `output_schema_version`, validated **by the producer** against a declared closed schema — an unknown key, a missing key or a wrong type is refused rather than printed. Human output is unchanged beside it. That is the lesson of the Markdown control plane applied to the machine surface: a contract only the consumer checks is not a contract.

### Lock protocol (a lease)

All writing sessions — orchestrated and launcher-started — share `audit/.lock`, a DIRECTORY holding an `owner` record (JSON: pid, role, started, host). Acquisition is atomic (`mkdir` — the second writer gets EEXIST); release is owner-checked (a session removes only a lock whose owner record it wrote, so it can never delete a lock it does not own). A live foreign lock aborts the run. Plain `xcheck unlock` never removes a lock — it only diagnoses whether one is provably stale; removing a lock identified by a pathname cannot be made race-free against a concurrent clear + re-acquire (F-0095), so every removal goes through `xcheck unlock --force`, where the operator asserts no writing session is active. `--force`'s own residual `read→unlink` window is a **palliative** — an operator-asserted trade-off, not elimination of the race; the real elimination is a kernel advisory lock (`fcntl.flock`), tracked as backlog **C6** (`docs/backlog-2-plan.md`). This enforces the "one writing session at a time" rule mechanically. A pre-directory `.lock` FILE (old protocol) is a legacy lock: `xcheck unlock --force` clears it, and there is no auto-migration.

The lock is also a **lease**: while a session runs the orchestrator renews `audit/.lock/heartbeat`. A lock whose heartbeat stopped more than `lease_ttl` seconds ago is reclaimed by plain `xcheck unlock` — **no `--force`** — because a stopped heartbeat is positive evidence the owner is gone, which a pathname alone never was. A lock with a *live* heartbeat is never reclaimable that way, and one with *no* heartbeat (an older xcheck, or a hand-run launcher session) falls back to the pid rules above unchanged. So the wedged-session recovery no longer needs the operator to assert something they cannot see.

### Courier commits

After every session the orchestrator commits **only the session's own new dirt** — a snapshot-diff of `git status` before and after. Pre-existing uncommitted work in your project is never swept into an audit commit — this holds without exception. When a file the session would otherwise write carries pre-existing operator work — a **mixed-ownership path**: e.g. the terminal-triage sync would rewrite a `status:` line in a finding file you had already edited before running `next` — the orchestrator does not try to split the file into its two authors (a self-written diff parser is one more guess it refuses to make), and it does not write its own change into your file either. It leaves that path **entirely untouched**, names it, and stops; the whole file stays in your worktree exactly as you left it. And because the file keeps its old status, the audit will **not report done** while the handoff is open — the same silent-gap rule that governs coverage. Resolve it by committing or discarding your edit; the next `next` then applies the decision cleanly and commits it. The trade-off is deliberate — part of the session's own work waits for you rather than risk carrying your pre-existing edit into an audit commit. So the orchestrator commits every path it touched cleanly, and only those; a mixed path is never one of them, and completion is never certified over one.

### Health checks

- `xcheck lint` — orphaned rows/files, status drift, class-membership breaks, malformed rows, non-canonical `next` tokens. Read-only, exit 1 on issues.
  - Two kinds of check, by design (CF-0001, norm-owner ruling): **single-record** checks (a field's presence, format, enumeration, range, value syntax) run through the *shared* validator that `next`, `status`, and `metrics` also route through — so a value one refuses can never reach another as if valid. **Cross-record semantic** checks that must load and compare *other* records — `members` existence, `next`-owner ↔ status consistency, `class` ↔ CF, a `dimension` slug against the `AUDIT.md` list — stay lint-surface only: the decision path is kept cheap and local rather than loading the whole corpus to route one finding.
- `xcheck metrics` — auditor accuracy (% of accepted findings surviving validation), fix durability (% closed on first verification), findings by dimension/severity/status, class-finding totals, attempts distribution, and **token cost per canonical transition** — read from the `tokens used` figure each session log already carries, recorded on the session's own event at finish. Per transition and not per session on purpose: a run can make its sessions cheaper while making everything it actually achieved more expensive. A session whose log reports no figure is counted as unmeasured and contributes to no total — never as a zero. Money cost is still not printed: that needs provider billing data this tool does not see.
- `xcheck dashboard` — writes `audit/dashboard.html`: one self-contained page over `status --json` and `metrics --json`. It is a **view**, and deliberately a thin one — it computes nothing, so every number on it is a number those two documents already publish. `xcheck/dashboard.py` contains no arithmetic and no `len`/`sum`/`round` at all, which `tests/test_dashboard.py` asserts over the module's AST; a rendered figure that appears in neither payload fails a traceability walk over the page. No script, no font, no image, no network request: a page describing a private audit must have no way to phone home. `--json` prints the two payloads instead of rendering. If a figure you want is missing, add it to `metrics` — a second place that computes it is a second source of truth.

## 9. Agent mapping and cross-agent discipline

Default mapping — and the reason this is called *cross*-check:

| Role | Default agent |
|---|---|
| Planner, Auditor, Verifier | Codex |
| Remediator | Claude |

The point: the agent that judges a fix is never the agent (let alone the session) that wrote it. Fresh eyes are the product; everything else is logistics.

All of it is remappable in `orchestrator.conf` — any capable agent can play any role. Legitimate reasons to remap:

- **Provider content policy.** On security-sensitive dimensions (memory-safety, undefined behavior, exploitability) a provider's moderation may refuse the defensive review outright. The methodology is agent-agnostic: point that dimension's Auditor/Verifier at an agent that isn't blocked — one line in the conf.
- **Single-vendor setups.** Running everything on one agent works mechanically, but you lose the strongest safeguard. The minimum worth insisting on: the Verifier is a separate fresh session that has never seen the fix.

**Codex sandbox note:** Codex CLI sessions may keep `.git` read-only, so a Codex Verifier sometimes cannot commit its verdict updates — this is Codex's own sandbox, not an xcheck control, and it applies in both launch modes. Under `orchestrated` the courier finishes those commits automatically; under `uncontained-direct` check `git status` after each Codex session.

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

The **finding file is the single source of truth** for that finding — evidence, validation, remediation, verification all live in it. The ledger is a derived index with one exception: your triage decisions flow ledger→file, applied by the next agent session that touches the finding. `xcheck lint` checks the two never silently diverge.

## 11. Class findings: fixing the disease, not the symptom

When a Remediator's census finds the same defect pattern **at or above `class_threshold` instances** (default 3), it opens a class finding (CF) with the full census, the root cause (usually a missing or ambiguous norm), and a strategy on an escalation ladder:

- **(a)** fix all census instances;
- **(b)** (a) + fix the norm so the class cannot recur;
- **(c)** (b) + an automated guard (grep check, lint rule, CI script) — the class becomes structurally hard to reintroduce.

A CF goes through **your triage** like any finding — accepting it sanctions a global change, which is exactly why it needs the gate even though a Remediator created it mid-batch. Look at the Census section (is the list complete?) and the strategy rung (too aggressive? too timid?). Rejecting a CF loses no work: its members return to the queue as individual fixes.

**Norm ratification gate — an exceptional human gate distinct from triage (one of those enumerated in `XCHECK.md` §3):** if a (b)/(c) fix would change a norm, or must pick a side in a conflict between norms, the Remediator stops after writing the plan and routes the conflict to **you as the norm owner** (not to triage — triage only writes the status column). The stop is a machine-readable brake recorded in the CF file, not just prose: frontmatter `blocked: norm-ratification` plus `norm-ruling: pending`, and a `## Norm ruling` section stating the conflict and the candidate sides. The Remediator raises it with `xcheck block-on-norm <CF-ID>`. To lift it you write your reasoning into `## Norm ruling` and record the decision — `xcheck record-ruling <CF-ID> --norm N1 --ruled-by human:<you>` (or `--norm N1-over-N4` when one norm beats another); only then will `xcheck record-fix` accept a fix on that CF. The verb refuses a `--ruled-by` that is not a human: the norm's owner is a person, and the tool records that decision rather than making it. The gate is **fail-closed**: an absent, `pending`, or unrecognized `norm-ruling` value keeps the CF stopped, and a `## Norm ruling` note with no recorded ruling does **not** unblock it. A class fix in the wrong direction multiplies one error across the whole corpus — hence the brake.

Verification of a CF re-runs the census expecting the polarity's clean result — zero instances for a presence class, zero orphans (every anchor's twin-search now returns its twin) for an absence class — or an explicit documented-exceptions list. "Fixed 12 of 15" (and its absence twin "created 12 of 15") = `reopened`.

## 12. Candidate mechanisms (default off)

Three mechanisms ship in this release as **candidates**: implemented, tested, and **off by
default**. They are recorded here because they change what an operator can turn on, not
because their effect is proven — the measurement that would settle that has not been run.

### Recorded refusal

An agent role that accepts a charter and cannot execute it records a refusal instead of
halting silently: one value from a closed six-word vocabulary (`out-of-competence`,
`blocked-dependency`, `charter-ambiguous`, `norm-conflict`, `material-missing`,
`cost-exceeded`) plus prose saying what is missing. The mechanism binds exactly the four
agent-executed roles, each to the **one durable target** its charter gives it: the Remediator
and the Verifier — whose charter *is* a set of findings — write the typed `refusal:` field
and a `## Refusal` section into the finding; the Planner (charter → `AUDIT.md`) records the
same reason-plus-prose in `AUDIT.md`, and an Auditor pass (charter → a pass report) in its
pass report. Triage is not one of these roles — it is the human decision gate, which parks a
finding as `deferred` or `rejected`, never a refusal.

What the code does: `xcheck lint` rejects a `refusal:` value outside the vocabulary and
rejects a reason code whose `## Refusal` section is empty; `xcheck status`/`next`/`loop` return
`stop-refusal` naming the finding and the reason instead of re-dispatching the same charter.
Recording a refusal does **not** change the finding's `status` — the charter stays in force.

Always on; there is no flag. It adds a field that was previously absent, so a project that
never writes one sees no change.

### Scope typing of a fix — `scope_typing` (default `off`)

When a Remediator sets a finding `fixed`, it declares what its fix admits: the machine half
with `xcheck record-fix <ID> --session <16-hex> --scope "<what the guarantee covers>"`, and
the prose half as an `## Admitted scope` section with `### Covers` and `### Does not cover`.

What the code does with `scope_typing=on`: `xcheck lint` fails a `fixed` finding that has no
`admitted-scope`, no `## Admitted scope` section, or an empty half — naming the missing
residue specifically. `closed` findings are never checked.

What the code does **not** do: it does not judge whether the declaration is true of the fix.
The `demanded ⊆ admitted` comparison is the Verifier's work, written into the Verifier's
contract, not a predicate the tool evaluates.

### Construal gate — `construal_gate` (default `off`)

Before a chartered session may touch the material, it writes its own reading of the charter —
task frame, approach, assumptions, stop conditions, out of scope — to
`audit/construals/<key>.md`, and a **different** party admits it.

What the code does with `construal_gate=on`: the orchestrator returns `run-construal` when no
construal exists for the charter's key (dispatching the role to write it and stop),
`stop-construal` when one exists but is not admitted, and dispatches the real charter only
once it is. `xcheck lint` refuses a construal whose `admitted-by` equals its `session` —
self-admission — and one that claims `admitted` with no admitter at all. The admitter is
named: `human:<label>` when you admit it yourself, or a session id when another session does.

There is no pre-authorization: admission is always a separate act by a party that is not the
producer, so a `proposed` construal always waits for a human (or another non-producing
session). An earlier candidate — a `construal_envelope` pre-authorization declared inside
`AUDIT.md` — was removed: because the courier commits `AUDIT.md` on any writing session's
behalf, every attempt to make an in-tree pre-authorization independent left a route for a
dispatched session to self-admit its own construal.

With both flags `off`, the orchestrator's decision is byte-identical to what it was before
these mechanisms existed — checked directly against the pre-integration decision path.

## 13. Configuration reference

Defaults live in `XCHECK.md` §10. `AUDIT.md` §5 overrides them per project, and `xcheck migrate` reads that section **once** — from then on the limits live in `state.json` and a later edit to `AUDIT.md` changes nothing (it is no longer read as authority). Change a limit with the verb:

```
xcheck set-limit reopen_limit 3
```

| Parameter | Default | Range | Meaning |
|---|---|---|---|
| `max_findings_per_pass` | 15 | 1–999 | Auditor stop condition; overflow splits the charter |
| `remediation_batch_size` | 8 | 1–999 | findings per Remediator session (large findings → cut to 3–4) |
| `class_threshold` | 3 | 1–999 | census instances at which a class finding opens |
| `reopen_limit` | 2 | 1–99 | consecutive reopens before `⚠ needs-human` |

`set-limit` refuses a key no consumer reads, a value that is not a whole number, and a value outside the range — `reopen_limit 0` by name, because below 1 the mandatory human stop never fires. Until 0.9.0 this section pointed the operator at the state file itself and told them to re-render afterwards. That instruction walked around the lock, the atomic writer, `state_revision`, the event stream and immediate validation; the verb has all five. The same applies to the queue and the catalogs: `xcheck amend-pass`, `xcheck cancel-pass` and `xcheck update-catalog` are how those change. Nothing in this tool requires hand-editing machine state.

## 14. Language policy

The methodology and templates are English. **Findings, plans, and reports are written in the operator's working language** — pick one per audit and stay consistent. **Evidence quotes are always verbatim in the material's own language**, regardless of the working language: the quote must remain findable by exact search.

## 15. Troubleshooting & FAQ

| Symptom | What it is | What to do |
|---|---|---|
| `⚠ needs-human` in the ledger | A finding was reopened twice — the auto-cycle isn't converging | Read the finding file (the full history is there); decide by hand: sharpen the requirement, split it, or close it by fiat |
| `disputed` won't resolve | Auditor and Remediator disagree after the written round | You decide; agent ping-pong is forbidden by protocol |
| Quotes "not found" en masse | The material shifted a lot (e.g. after a class fix) | Normal: validation marks such findings `obsolete` or refreshes locators. Do nothing |
| Session exceeded its charter / no coverage report | Protocol violation | The pass does not count — rerun with a smaller charter; if it recurs, reduce the default pass size in AUDIT.md |
| Ledger or a frontmatter block diverged from `state.json` | Somebody hand-edited a rendered view (§2) | Nothing repairs it on contact and nothing reads it back — the tool refuses the tree until it is resolved. `xcheck render-views` is the only repair: it overwrites each view from state and lists by name every one that had drifted, so read that list — those are the edits it destroyed — before re-recording the intent through a verb |
| Stale `audit/.lock` | A session died without releasing | `xcheck unlock` diagnoses it; `xcheck unlock --force` removes it (after confirming no writing session is active) |

**Can one agent do everything?** Mechanically yes (roles are functions), but you lose the main safeguard: cross-verification by fresh eyes. Minimum worth insisting on: the Verifier is a separate fresh session that never saw the fix.

**Why can't triage be delegated to an agent?** It is the routine point where "what counts as a defect and what we spend cycles on" is decided — a value judgment of the material's owner. (The rarer exceptional gates, `XCHECK.md` §3, are the owner's judgment too — a norm-ratification ruling especially; triage is just the one that recurs every cycle.) The rest is execution.

**How much human time does a cycle take?** Target mode: minutes to launch sessions plus one substantive triage sitting per batch of passes. If your per-cycle time grows, protocol is being violated somewhere — see the table above.

**Too many findings, triage is heavy.** Raise the severity bar in charters ("critical/major only"), narrow the dimensions in AUDIT.md, triage in batches by severity, or set `triage_batch_cap` so the orchestrator stops for triage in smaller portions.

**An agent clearly broke protocol** (read "the whole project for context," left no coverage report). Don't count the session's result; rerun it. The protocol works only while violation = rerun, with no "just this once" exceptions.

## 16. License

MIT — see [LICENSE](LICENSE).

---

*Normative documents: `XCHECK.md` (the core, read by agents), `bootstrap.md` (install + session one-liners), `templates/` (artifact skeletons). This README is for you, the human operator; agents never need it.*
