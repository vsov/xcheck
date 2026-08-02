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
| `bin/xcheck` | The orchestrator (Python 3, stdlib). Drives sessions between human gates. Includes a decision-logic selftest whose pass count is measured from the check lines it emits to `sys.stdout`. |
| `templates/` | Artifact skeletons: `finding.md`, `class-finding.md`, `pass.md`, `plan.md`, `construal.md`, plus `AUDIT-text.md` / `AUDIT-code.md` starting points. |
| `skills/` | Shared launcher skills used by Codex, Claude Code, and both plugin manifests. Thin, but not zero-normative: some methodology rules are duplicated here for safety/ergonomics, so reinstall after any methodology change (§7). |
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

This creates `your-project/audit/` containing `XCHECK.md`, an `AUDIT.md` template, an empty `LEDGER.md`, `templates/`, and empty `findings/`, `passes/`, `plans/`. Nothing is audited yet — the Planner fills `AUDIT.md` in the first session.

Notes:

- The default `AUDIT.md` template is text-oriented; for a codebase, copy `templates/AUDIT-code.md` over `audit/AUDIT.md` before planning.
- The install is **idempotent**: re-running upgrades `XCHECK.md` and the templates without touching audit state (`AUDIT.md`, `LEDGER.md`, findings). This is how you upgrade the methodology mid-audit.
- `audit/` can be committed with the project (recommended — remediation commits referencing finding IDs make verification much cheaper) or added to `.gitignore` if the audit must stay out of history.

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

The session reads the charter's units attentively, checks them against the dimension's norm, writes findings to `audit/findings/`, a pass report to `audit/passes/`, rows to the ledger, and ticks the pass checkbox. Default cap: 15 findings per pass; scope overflow splits the charter and appends the tail to the queue.

**What to check in a pass report:** the Coverage section must explicitly list what was NOT covered. An empty NOT COVERED with half the units skimmed means the session broke protocol — the pass does not count.

You can run several passes back to back and triage the accumulated pile — triaging after every pass is not required.

### Step 3 — Triage (your gate)

- Codex: `$xcheck-triage`; Claude Code / OpenCode: `/xcheck-triage`. In dialogue mode, the agent presents findings in groups (severity × dimension) with quotes; you answer in chat ("accept all critical, reject F-0031, show me CF-0002 in detail"); the agent records exactly what you said into the ledger.
- By hand: open `audit/LEDGER.md` and set each `reported` row's status to `accepted`, `rejected`, or `deferred`. Batch decisions are normal ("all critical and major → accepted" in one sweep). When a title alone doesn't settle it, open the finding file — the quote and reasoning are there.

You write **only the status column of the ledger** — never the finding files. The first agent session that picks a finding up carries your decision into the file (the only sanctioned ledger→file flow; in every other case the finding file is the truth).

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
5. **Self-check** — run the finding's "how to verify" procedure, then set `fixed`.

If the project uses git, the session commits with finding IDs in the message — verification will lean on the diffs.

### Step 5 — Verify

When `fixed` findings have accumulated — a session of a **different** agent (in the cross-agent default this is automatic):

- Codex: `$xcheck-verify`; Claude Code / OpenCode: `/xcheck-verify`
- By hand:

  ```
  Read audit/XCHECK.md and audit/AUDIT.md. Role: Verifier. Charter: all findings in status fixed. You did not write these fixes; try to prove them wrong.
  ```

Per finding: run its verification procedure, then adversarially inspect the surroundings of the change for collateral damage. Verdict `closed` or `reopened`, held to the same evidence standard. For a CF the verification is a fresh census expecting the polarity's clean result — zero instances for a presence class, zero orphans (every anchor's twin-search now returns its twin) for an absence class — which catches both "fixed 12 of 15" and its absence twin "created 12 of 15."

### Step 6 — Repeat and finish

- `reopened` → next Remediator batch (attempt counter grows; two in a row → `⚠ needs-human`).
- `disputed` → one written round of Auditor objection in the finding file, then you decide.
- Passes left in the queue → back to step 2.

**The audit is complete** when the pass queue is empty and every ledger row is terminal. One special case: a remainder of only `deferred` rows is **complete *with* deferred debt** — the automatic cycle is exhausted, but `deferred` is not terminal (§3), so those rows stay as your re-triage debt until you accept or reject them. `$xcheck-status` in Codex, `/xcheck-status` in Claude Code/OpenCode, or `xcheck status` runs this termination check for you.

## 7. Launchers: skills for Codex and Claude Code, commands for OpenCode

Six launchers, same logical names on every platform:

| Codex | Claude Code / OpenCode | Role | Auto-picked charter |
|---|---|---|---|
| `$xcheck-plan` | `/xcheck-plan` | Planner | fill `audit/AUDIT.md` (stops if a complete plan already exists) |
| `$xcheck-audit` | `/xcheck-audit` | Auditor | first unchecked pass (optional pass id, or `disputed` for a dispute-response session) |
| `$xcheck-triage` | `/xcheck-triage` | Triage (dialogue) | all `reported` rows; agent is the pen, human is the decider |
| `$xcheck-remediate` | `/xcheck-remediate` | Remediator | unfinished work first, else accepted CF, else next batch of accepted findings |
| `$xcheck-verify` | `/xcheck-verify` | Verifier | everything in status `fixed`; refuses to judge its own fixes |
| `$xcheck-status` | `/xcheck-status` | — (read-only) | dashboard: queue progress, statuses, whose turn, termination check |

Run them in a session opened **in the audited project root**. In Codex, mention the override alongside the skill (for example, `$xcheck-audit for P-03` or `$xcheck-remediate for CF-0002`). Claude Code and OpenCode accept command arguments directly.

The wrappers are deliberately thin: preflight → pick charter → play the role per `audit/XCHECK.md`, which stays the single normative source. They are not *empty*, though, and not zero-normative: for ergonomics and safety some of the methodology's rules are duplicated inside the skills. Because of that duplication, re-run the launcher installer after *any* change to the methodology (§5.3, `install-launchers.sh` is idempotent), so the installed copies and the generated OpenCode commands never fall behind. Cross-agent protections are preserved: `xcheck-verify` stops when the agent or session that produced the fixes would verify them (a fresh session of the same agent is the disclosed fallback minimum, XCHECK.md §3), and all writing launchers honor the `audit/.lock` protocol.

**Codex note:** launchers install as skills in `~/.agents/skills/`. Codex can invoke them explicitly with `$xcheck-*` or implicitly from a matching request. For non-interactive use, the orchestrator drives Codex through `codex exec` with the same role charters — see the next section.

## 8. The orchestrator

`bin/xcheck` removes the courier work: it decides whose turn it is, launches the right agent CLI with the right charter, commits the session's results, and stops at your gates.

```
xcheck [--project DIR] next               run one session (whoever's turn it is)
xcheck [--project DIR] loop               run until a human gate
xcheck [--project DIR] [--budget S] loop  same, with a wall-clock budget (checked between sessions)
xcheck [--project DIR] status             read-only dashboard
xcheck [--project DIR] unlock [--force]   diagnose a stale lock (--force removes it)
xcheck [--project DIR] lint               ledger ↔ finding-file consistency check
xcheck [--project DIR] metrics            auditor accuracy, fix durability, distributions
xcheck selftest                           decision-logic + invariant self-checks
```

Flags for `loop`: `--step` (pause between sessions), `--max-sessions N` (cost cap), `--budget SECONDS` (wall-clock budget — checked **before each session**, so the loop stops once elapsed time reaches it but a session already running is never interrupted; whichever of `--budget`/`--max-sessions` hits first), `--dry-run` (print the decision and the command without launching).

**Turn priority:** needs-human stop → verification of `fixed` → remediation (unfinished/reopened first, then accepted CF, then accepted findings) → audit passes → triage stop. The loop halts at: triage needed, `⚠ needs-human`, a dispute, a norm-ratification gate, an inconsistent ledger (non-terminal rows with no legal move), a broken/unclosed class dependency, a corrupt ledger or missing coverage report, queue exhausted (or exhausted-with-deferred-debt), a session error, a **non-converging cycle** (`loop_progress_limit` consecutive sessions that repeat the same decision without moving `audit/`), or the **orchestrator's own source changing mid-run** (a self-remediation edited `bin/xcheck`; the loop runs the code image loaded at its start, so restart `loop` to pick up the fix) — each with an explanation of what is expected from you. This is the authoritative, exhaustive list of the **orchestrator's halt points**, not a count of the methodology's human gates: the human gates among these halts (`⚠ needs-human`, a dispute, a norm-ratification gate, and triage) are enumerated canonically in `XCHECK.md` §3 — this list does not re-count them.

**Your touch points in `loop` mode:** the routine one is install → triage sittings → final review, and on a clean run that is all. But every other halt above also needs you: `⚠ needs-human`, a dispute, a norm-ratification gate, a session error, a re-triage of deferred debt (the `exhausted-with-deferred-debt` stop — `deferred` is not terminal, §3, so accept or reject to fully close), or a repair (corrupt ledger / missing coverage / broken class link / an inconsistent ledger — non-terminal rows with no move), a non-converging cycle (sessions repeating without progress), or a restart after a self-edit to `bin/xcheck`. Between halts the loop runs by itself; it never silently works around a gate that wants a human, and it never keeps spinning on unchanged state or runs on a stale image of its own edited source.

### orchestrator.conf

Per-project config at `audit/orchestrator.conf` (`key=value` lines):

| Key | Default | Meaning |
|---|---|---|
| `planner_cmd`, `auditor_cmd`, `verifier_cmd` | Codex (`codex exec ...`) | CLI command per role |
| `remediator_cmd` | `claude -p --dangerously-skip-permissions` | CLI command for the Remediator |
| `batch_size` | 8 | remediation batch size (AUDIT.md `remediation_batch_size` overrides) |
| `max_sessions` | 20 | default session cap for `loop` |
| `loop_progress_limit` | 2 | stop the loop after N consecutive sessions that repeat the same decision and leave `audit/` unchanged — a non-converging cycle, not a cost cap (0 = off) |
| `triage_batch_cap` | 0 (unbounded) | stop for triage once N findings pile up — smaller sittings |
| `push_after_commit` | off | opt-in `git push` after courier commits |
| `scope_typing` | `off` | candidate (§12): require `admitted-scope` + both halves of `## Admitted scope` on a `fixed` finding |
| `construal_gate` | `off` | candidate (§12): require an admitted construal before a charter is dispatched |
| `session_note` | — | extra text appended to every session prompt (e.g. scope exclusions) |

### Lock protocol

All writing sessions — orchestrated and launcher-started — share `audit/.lock`, a DIRECTORY holding an `owner` record (JSON: pid, role, started, host). Acquisition is atomic (`mkdir` — the second writer gets EEXIST); release is owner-checked (a session removes only a lock whose owner record it wrote, so it can never delete a lock it does not own). A live foreign lock aborts the run. Plain `xcheck unlock` never removes a lock — it only diagnoses whether one is provably stale; removing a lock identified by a pathname cannot be made race-free against a concurrent clear + re-acquire (F-0095), so every removal goes through `xcheck unlock --force`, where the operator asserts no writing session is active. `--force`'s own residual `read→unlink` window is a **palliative** — an operator-asserted trade-off, not elimination of the race; the real elimination is a kernel advisory lock (`fcntl.flock`), tracked as backlog **C6** (`docs/backlog-2-plan.md`). This enforces the "one writing session at a time" rule mechanically. A pre-directory `.lock` FILE (old protocol) is a legacy lock: `xcheck unlock --force` clears it, and there is no auto-migration.

### Courier commits

After every session the orchestrator commits **only the session's own new dirt** — a snapshot-diff of `git status` before and after. Pre-existing uncommitted work in your project is never swept into an audit commit — this holds without exception. When a file the session would otherwise write carries pre-existing operator work — a **mixed-ownership path**: e.g. the terminal-triage sync would rewrite a `status:` line in a finding file you had already edited before running `next` — the orchestrator does not try to split the file into its two authors (a self-written diff parser is one more guess it refuses to make), and it does not write its own change into your file either. It leaves that path **entirely untouched**, names it, and stops; the whole file stays in your worktree exactly as you left it. And because the file keeps its old status, the audit will **not report done** while the handoff is open — the same silent-gap rule that governs coverage. Resolve it by committing or discarding your edit; the next `next` then applies the decision cleanly and commits it. The trade-off is deliberate — part of the session's own work waits for you rather than risk carrying your pre-existing edit into an audit commit. So the orchestrator commits every path it touched cleanly, and only those; a mixed path is never one of them, and completion is never certified over one.

### Health checks

- `xcheck lint` — orphaned rows/files, status drift, class-membership breaks, malformed rows, non-canonical `next` tokens. Read-only, exit 1 on issues.
  - Two kinds of check, by design (CF-0001, norm-owner ruling): **single-record** checks (a field's presence, format, enumeration, range, value syntax) run through the *shared* validator that `next`, `status`, and `metrics` also route through — so a value one refuses can never reach another as if valid. **Cross-record semantic** checks that must load and compare *other* records — `members` existence, `next`-owner ↔ status consistency, `class` ↔ CF, a `dimension` slug against the `AUDIT.md` list — stay lint-surface only: the decision path is kept cheap and local rather than loading the whole corpus to route one finding.
- `xcheck metrics` — auditor accuracy (% of accepted findings surviving validation), fix durability (% closed on first verification), findings by dimension/severity/status, class-finding totals, attempts distribution.

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

**Codex sandbox note:** Codex CLI sessions may keep `.git` read-only, so a Codex Verifier sometimes cannot commit its verdict updates. The orchestrator finishes those commits automatically; in manual mode, check `git status` after each Codex session.

## 10. The audit/ directory in your project

```
audit/
├── XCHECK.md            # methodology core (installed copy) — the agents' rulebook
├── AUDIT.md             # this audit's plan: norms, dimensions, unit map, pass queue, limits
├── LEDGER.md            # findings index: | id | title | severity | status | next | updated |
├── findings/            # one file per finding: F-NNNN-<slug>.md / CF-NNNN-<slug>.md
├── passes/              # one report per audit pass: P-NN-<dimension>.md
├── plans/               # remediation plans: RP-NNNN.md
├── construals/          # one construal per (role, charter) — empty unless `construal_gate` is on (§12)
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

**Norm ratification gate — an exceptional human gate distinct from triage (one of those enumerated in `XCHECK.md` §3):** if a (b)/(c) fix would change a norm, or must pick a side in a conflict between norms, the Remediator stops after writing the plan and routes the conflict to **you as the norm owner** (not to triage — triage only writes the status column). The stop is a machine-readable brake recorded in the CF file, not just prose: frontmatter `blocked: norm-ratification` plus `norm-ruling: pending`, and a `## Norm ruling` section stating the conflict and the candidate sides. To lift it you set `norm-ruling` to the winning norm-id token — e.g. `norm-ruling: N1` or `norm-ruling: N1-over-N4` — and write your reasoning into `## Norm ruling`; only then does a Remediator clear `blocked:` and resume per your ruling. The gate is **fail-closed**: an absent, `pending`, or unrecognized `norm-ruling` value keeps the CF stopped, and a `## Norm ruling` note without the frontmatter token does **not** unblock it. A class fix in the wrong direction multiplies one error across the whole corpus — hence the brake.

Verification of a CF re-runs the census expecting the polarity's clean result — zero instances for a presence class, zero orphans (every anchor's twin-search now returns its twin) for an absence class — or an explicit documented-exceptions list. "Fixed 12 of 15" (and its absence twin "created 12 of 15") = `reopened`.

## 12. Candidate mechanisms (default off)

Three mechanisms ship in this release as **candidates**: implemented, tested, and **off by
default**. They are recorded here because they change what an operator can turn on, not
because their effect is proven — the measurement that would settle that has not been run.

### Recorded refusal

A role that accepts a charter and cannot execute it records a refusal instead of halting
silently: `refusal:` in the finding's frontmatter, one value from a closed six-word
vocabulary (`out-of-competence`, `blocked-dependency`, `charter-ambiguous`,
`norm-conflict`, `material-missing`, `cost-exceeded`), plus prose in the finding's
`## Refusal` section saying what is missing.

What the code does: `xcheck lint` rejects a value outside the vocabulary and rejects a
reason code whose `## Refusal` section is empty; `xcheck status`/`next`/`loop` return
`stop-refusal` naming the finding and the reason instead of re-dispatching the same charter.
Recording a refusal does **not** change the finding's `status` — the charter stays in force.

Always on; there is no flag. It adds a field that was previously absent, so a project that
never writes one sees no change.

### Scope typing of a fix — `scope_typing` (default `off`)

When a Remediator sets a finding `fixed`, it declares what its fix admits: `admitted-scope:`
in the frontmatter and an `## Admitted scope` section with `### Covers` and
`### Does not cover`.

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
self-admission — and one that claims `admitted` with no admitter at all.

A `construal_envelope` line in `AUDIT.md` can pre-authorize named roles, but only with a live
`recheck-by` date; absent, malformed, expired, duplicated, or naming a non-role, it is treated
as no envelope and human admission is required.

With both flags `off`, the orchestrator's decision is byte-identical to what it was before
these mechanisms existed — checked directly against the pre-integration decision path.

## 13. Configuration reference

Defaults live in `XCHECK.md` §10; `AUDIT.md` overrides them per project:

| Parameter | Default | Meaning |
|---|---|---|
| `max_findings_per_pass` | 15 | Auditor stop condition; overflow splits the charter |
| `remediation_batch_size` | 8 | findings per Remediator session (large findings → cut to 3–4) |
| `class_threshold` | 3 | census instances at which a class finding opens |
| `reopen_limit` | 2 | consecutive reopens before `⚠ needs-human` |
| `construal_envelope` | none | candidate (§12): pre-authorization for the construal gate; declared in AUDIT.md, needs a live `recheck-by` date, fail-closed |

## 14. Language policy

The methodology and templates are English. **Findings, plans, and reports are written in the operator's working language** — pick one per audit and stay consistent. **Evidence quotes are always verbatim in the material's own language**, regardless of the working language: the quote must remain findable by exact search.

## 15. Troubleshooting & FAQ

| Symptom | What it is | What to do |
|---|---|---|
| `⚠ needs-human` in the ledger | A finding was reopened twice — the auto-cycle isn't converging | Read the finding file (the full history is there); decide by hand: sharpen the requirement, split it, or close it by fiat |
| `disputed` won't resolve | Auditor and Remediator disagree after the written round | You decide; agent ping-pong is forbidden by protocol |
| Quotes "not found" en masse | The material shifted a lot (e.g. after a class fix) | Normal: validation marks such findings `obsolete` or refreshes locators. Do nothing |
| Session exceeded its charter / no coverage report | Protocol violation | The pass does not count — rerun with a smaller charter; if it recurs, reduce the default pass size in AUDIT.md |
| Ledger diverged from finding files | Index drift | Agents repair it on contact; direction is file→ledger always, except your triage decisions (ledger→file). `xcheck lint` reports it |
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
