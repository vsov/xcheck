---
name: xcheck-triage
description: Run xcheck triage as a dialogue - present reported findings, record the human's accept/reject/defer decisions in the ledger. Use when the user says /xcheck-triage, "triage", "let's go through the findings". The agent is the pen; the human is the decider.
---

# xcheck launcher — Triage (dialogue mode)

You are the **pen, not the decider** (XCHECK.md §3 Triage, Agent-as-pen rule). The human makes every decision; you present, record, and never fill gaps with your own judgment.

1. **Preflight.** `audit/XCHECK.md` and `audit/LEDGER.md` must exist. Missing → stop, point to xcheck `bootstrap.md`.
2. Read `audit/XCHECK.md` §2–§3 (triage write surface, agent-as-pen) and `audit/LEDGER.md`.
3. **Collect:** all `reported` rows — this is the triage queue, and the ONLY group triage transitions. Also note, for optional review (NOT triage rows): `⚠ needs-human` rows, the `deferred` backlog, unresolved `disputed` rows.
   **Legal transitions by source status (§5) — never cross groups:** a `reported` row → `accepted`/`rejected`/`deferred` only; a `disputed` row is resolved only to `accepted` (human sides with the finding) or `withdrawn` (human sides against), and only on an explicit dispute ruling; `deferred` rows and findings stopped at `reopen_limit` have NO triage transition — route them per §9, never assign a new status here. A batch statement ("all critical accepted") applies only to `reported` rows in that group, never to a `disputed`/`deferred` one.
4. **Present** compactly in the human's language, grouped by severity then dimension: id, title, unit, and a one-line gist of the evidence (open finding files to quote — reading is allowed; you WRITE only the ledger). CF rows get their own presentation: pattern, census size, strategy rung, member ids.
5. **Collect decisions conversationally.** Batch statements apply exactly as stated ("all critical accepted" = accepted for those rows and nothing else). Ambiguous statement → ask; unstated → row stays `reported`. You may give an opinion when asked, clearly labeled as opinion; the recorded status is only what the human states.
6. **Write** to `audit/LEDGER.md` only, and only the **status** column of affected rows, per stated decisions (§3 Triage write surface: status column only). A rejected CF also reverts every finding in its `members:` list to `accepted`. Do NOT write the `next` column or any other column — the `next` index is derived later by agent/orchestrator sessions, not by triage. Never touch finding files — agent sessions sync frontmatter later per §2 rule 3.
7. **Close** with a written summary: rows changed (id → status), rows left undecided, and the suggested next step (e.g. "N accepted → /xcheck-remediate").

## Lock discipline

`audit/.lock` serializes writing sessions (§4 rule 8) and is shared with the orchestrator (`bin/xcheck`), which acquires it atomically with `open(O_CREAT|O_EXCL)`. Match that — never check-then-create (the gap between an existence check and a separate create lets two sessions both win):

1. **Acquire atomically:** create `audit/.lock` in one exclusive step that FAILS if the file already exists — e.g. `(set -C; printf '%s' '{"pid": <pid or 0>, "role": "<Role>", "started": "<ISO>", "host": "<host>"}' > audit/.lock)`, where `set -C` (noclobber) makes the redirect fail atomically when the file exists. Set `pid` to a real process id if obtainable, else 0. If acquisition fails, another writing session holds the lock — do not start; report the conflict to the human.
2. **Owner-checked release:** hold the lock for the whole session; before deleting, re-read `audit/.lock` and confirm its `pid`+`started` still match the lock you wrote — delete only then, and on every exit path including early stop. Never delete a lock you do not own.
3. **Stale lock:** a lock whose `pid` is not alive is stale — clear it with `xcheck unlock`, never silently steal it.
