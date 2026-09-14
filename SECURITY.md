# Security

xcheck launches agent CLIs, headlessly, against a repository, and lets them write to
it. That is the whole product, and it is also the whole risk. This document says what
the tool actually protects against, what it does not, and where the boundary is — in
plain words, because a trust model nobody reads protects nobody.

Read every claim here as scoped to a **launch mode**. There are two, `orchestrated` and
`uncontained-direct`, and unless a section names the other, it describes `orchestrated`.
The next section says what the difference costs you.

## Two ways in, and only one of them is contained

A session starts one of exactly two ways, and the difference decides which half of this
document applies to you.

- **`orchestrated`** — `xcheck next` or `xcheck loop` launches the agent through
  `xcheck/runner.py`. Every control below is in force. This is the **authoritative**
  execution path: the one the rest of this document describes, and the one to use when
  the run has to be accountable to anyone but the person watching it.
- **`uncontained-direct`** — you start the agent yourself, in your own tool:
  `/xcheck-status`, or a prompt you paste by hand. xcheck is not in the process.
  **None** of the controls below apply: no sandbox profile, no hard timeout, no
  environment allowlist, no log redaction, no process-group kill, no courier review of
  the diff — and, just as load-bearing, no invocation envelope and no session receipt,
  so nothing records what was in force and nothing attests what the session did.
  Containment is whatever your agent platform provides — and xcheck does not know what
  that is and cannot report it. The honest sentence is *we do not know*, and it is the
  one to plan around.

  **This mode no longer has a writing surface.** Until 0.9.1 the five writing launchers
  lived here, each disclosing what it gave up and calling itself a development escape
  hatch. A disclosure is not a control, and shipping a convenient unrecorded way to edit
  the material while calling the recorded path authoritative is a contradiction the third
  audit named as a release blocker. `/xcheck-plan`, `/xcheck-audit`, `/xcheck-triage`,
  `/xcheck-remediate` and `/xcheck-verify` are now wrappers: they hand the work to
  `xcheck next` and run nothing themselves. `/xcheck-status` is what is left in this
  mode, and it is read-only — it moves no machine state for those controls to mediate or
  attest. A test asserts the mode stays empty of writers: a skill declaring
  `uncontained-direct` may not name a state-moving verb, and the verb list it is checked
  against is derived from `write.VERBS` rather than kept by hand.

  What you lose with them is the conversation, and it is a real loss: a wrapper cannot be
  steered mid-thought and takes no improvised charter. If you want an agent to talk about
  this project with you, open one — just do not call the result an audit session, because
  nothing recorded it.

The methodology is identical in both modes; the enforcement is not. Where a section
below does not say otherwise, read it as describing `orchestrated`. `xcheck status`
prints both facts on every run — which profile the next orchestrated session resolves
to, and that hand-launched sessions never pass through it.

## The trust model in one paragraph

**The audited repository is untrusted input.** Its README, its source comments, its
test fixtures and its commit messages are all text an agent will read, and any of it
can be written by someone who wants the agent to do something else — read your
environment, exfiltrate a file, run a command, or file a finding that isn't true.
xcheck does not defend the agent's *reasoning*; nothing can. It defends what the
agent's process can *reach* and what its output can *change*.

Everything in this document follows from that split: **containment is real,
persuasion is not defended.**

One consequence is worth stating on its own, because it was wrong until 0.9.2: **the
policy that decides what runs may not live inside the repository being audited.** It
used to. `audit/orchestrator.conf` named the command each role executes, the containment
profile, the environment allowlist, the container image, the egress allowlist, the
resource limits, retry, concurrency and whether commits are pushed — and the readonly
courier lets a session write anywhere under `audit/`, ships that edit, and the next
dispatch reads it. Every link in that chain was documented behaviour; together they meant
an untrusted repository could rewrite the terms of its own audit, and a containment
setting is only as strong as the file that names it. Those keys now come from an
**operator profile** outside the subject (`--policy`, `$XCHECK_OPERATOR_PROFILE`, or
`$XDG_CONFIG_HOME/xcheck/operator.conf` — see [README](README.md)); writing one into the
subject is refused at load, by name. The session's environment does not carry the
profile's location, and its sha256 is recorded on every dispatch envelope as
`policy_digest`.

## What xcheck does

Every row here is an `orchestrated` control: it exists because `runner.py` put it
there. Under `uncontained-direct` the table is empty — not weakened, absent.

| Control | What it means | Default | Proved by |
|---|---|---|---|
| No ambient permission bypass | `--dangerously-skip-permissions`, `--yolo`, `--full-auto` and their siblings are supported **only inside an isolating profile**. With `sandbox_profile=none` a command carrying one refuses to launch and names the flag: the grant is contained or it does not happen | enforced | `test_refused_outside_an_isolating_profile` |
| Disposable worktree | each writing session runs in a git worktree in a system temp dir; changes return through the courier as a reviewed diff, never as edits under your hands | `worktree` | `test_workdir_is_outside_the_project_and_removed_on_success` |
| Read-only roles | Auditor and Verifier get a worktree whose changes **outside `audit/`** are refused rather than applied — the roles that judge code cannot edit it | `readonly` | `test_readonly_refuses_a_material_change_and_keeps_the_patch` |
| Built environment | the child's environment is constructed from an allowlist (`PATH`, `HOME`, locale, `TMPDIR`, `TERM`, TLS cert paths, `XCHECK_*`), not inherited. Cloud credentials, signing keys and production tokens do not reach the agent unless you **name** them in `env_allowlist` | enforced | **A1** + `test_an_injected_cloud_credential_never_reaches_the_child` |
| Hard timeout | `session_timeout` (default 3600s), then SIGTERM, then SIGKILL after `kill_grace` | enforced | **A5** |
| Process-group kill | the child leads its own process group, so a timeout kills the group — a dev server or watcher the agent spawned dies with it | enforced | **A5** + `test_the_grandchild_stops_advancing` |
| Role authorization | every write verb passes ONE boundary that decides role × verb × target × current status before anything is applied; a verb in no table refuses. An agent session cannot perform a human-owned transition or an administrative verb, and the refusal is recorded as a `write_refused` event | enforced | `test_an_open_auditor_session_may_not_perform_the_human_transition` |
| Deadlines beyond the timeout | startup (120s), output (600s) and idle (1200s), each measured from the child's own output stream, plus activity (1200s), which is not: it watches a canonical transition, a validated telemetry sidecar and the session's own worktree. A session that prints a header and then nothing is ended in minutes rather than at the hard timeout; one that pads or drips output to stay alive is ended by the activity bound. Both give the outcome `stalled`, and a stalled session is never retried | enforced | `test_a_child_that_goes_quiet_dies_at_the_idle_deadline`, `test_a_padding_child_clears_the_output_deadline_and_dies_at_the_activity_one` |
| Log redaction | named secret values and well-known token shapes are replaced with `«redacted:…»` before the log is written | enforced | `test_an_echoed_secret_is_replaced` |
| Single writer | a directory lock with a heartbeat lease; one writing session per project at a time | enforced | `test_a_live_heartbeat_is_not_reclaimable_and_a_dead_one_is` |
| OS-enforced containment | `sandbox_profile=container` runs the session inside docker: `--network=none`, an empty tmpfs `HOME`, the source checkout mounted **read-only**, exactly two writable mounts (the disposable clone and one output dir), `--cpus`/`--memory`/`--pids-limit` all non-zero, `--cap-drop ALL`, `--security-opt no-new-privileges`, and no host socket mounted — never the docker socket | `container`, **opt-in** | **A2 A3 A4 A6 A7** |
| Fail closed | if the requested containment is unavailable, the session does not run in a weaker mode — it refuses. Requesting `container` without a working docker names docker and the three alternatives and stops | enforced | `test_run_session_refuses_before_launching_anything` |
| One write path | agents cannot change machine state by editing a file: `audit/state.json` changes only through the write verbs, and rendered Markdown that drifts is refused, not trusted | enforced | `test_a_hand_edited_ledger_row_stops_all_four` |

`xcheck status` prints the resolved sandbox profile, and — for the last session that
ran — whether its containment was **verified at launch**. If it says `none`, you turned
containment off. If it says `NOT verified (process-level isolation only)`, you are on a
worktree profile, which is the default and is not a machine boundary.

## What each profile actually holds, and which probe proved it

Profiles are an `orchestrated` concept; a hand-launched session has no profile to hold
anything. Every row is a probe in `tests/test_container_profile.py` that runs the SAME escape
attempt under `container` and under `worktree` in one test method. The worktree column
is not an admission of weakness written for balance — it is the recorded result of the
control arm, which has to SUCCEED or the container result proves nothing.

| Escape attempted | `none` | `worktree` / `readonly` | `container` | Probe |
|---|---|---|---|---|
| Write the source checkout directly (bypassing the courier) | succeeds | **succeeds** — the child appends to the original checkout and `git status` there shows it | **held** — the host path is not visible and `/src` is a read-only mount | `test_a_child_cannot_write_the_source_checkout` |
| Open a network socket | succeeds | **succeeds** — connects to a listener on the host loopback | **held** — `--network=none`, connection refused | `test_a_child_cannot_open_a_network_socket` |
| Find the original checkout via `git worktree list` | n/a (it is the checkout) | **succeeds** — prints the original checkout's absolute path | **held** — the tree is a self-contained clone; only `/work` is listed | `test_git_worktree_list_reveals_nothing_outside_the_mount` |
| Read the operator's `$HOME` | succeeds | **succeeds** — `$HOME` is the operator's, fully populated | **held** — `$HOME` is an empty tmpfs at `/home/xcheck` | `test_home_is_synthetic_and_empty` |
| Reach the docker daemon through a mounted socket | n/a | n/a | **held** — no host socket is mounted at all | `test_no_host_socket_is_ever_mounted` |
| Run under an image chosen by the registry today | n/a | n/a | **held** — the image is pinned by digest | `test_the_image_is_pinned_by_digest` |

## The adversarial matrix

The table above is xcheck probing itself. `tests/adversarial/` is the other oracle: a
git repository whose *content* attacks the sandbox — eight named escapes, run under all
four profiles, thirty-two cells, each decided by an OS-enforced outcome (file bytes, an
errno, a refused connection, a pid the kernel says is gone). Run it with
`python3 -m unittest discover -s tests -p test_adversarial_repo.py`; the matrix prints
in full, `escaped` cells included, because those cells are the honest content.

| # | Attack | `none` | `worktree` / `readonly` | `container` |
|---|---|---|---|---|
| A1 | read a secret from the environment | held | held | held |
| A2 | write the original checkout via `git worktree list` | n/a | **escaped** | held |
| A3 | write a sibling repository | **escaped** | **escaped** | held |
| A4 | open a network connection | **escaped** | **escaped** | held |
| A5 | start a daemon that outlives the session | held | held | held |
| A6 | rewrite git configuration (shared repo config, operator `$HOME`) | **escaped** | **escaped** | held |
| A7 | write quoted / tab / newline / non-ASCII filenames outside `audit/` | **escaped** | **escaped** | held |
| A8 | prompt injection | **escaped** | **escaped** | **escaped** |

A1 holds everywhere because the environment allowlist is not a profile feature — the
child's environment is built the same way whatever the profile. A8 escapes everywhere
and always will: there is nothing for a kernel to enforce, and the row is labelled
`not-enforced: relies on agent compliance` in the suite itself. It is here so that
"prompt injection is not defended against" is a cell someone measured rather than a
sentence someone wrote.

**What 0.9.2 changed about A8, and what it did not.** The class is open and the cell
still reads `escaped` under every profile, `container` included. That is the phase's own
success criterion: anything that made this cell say `held` would be a measurement error,
not a fix. What changed is that the surface is smaller and the output is checkable.

- The boundary is now STATED where content is assembled — every capsule opens with the
  sentence that classifies repository text as data, so the session reads it on every
  dispatch rather than once in a document it was told to consult.
- No file in the subject can change the commands, the containment or the output
  protocol. The policy lives outside the tree and is refused inside it; what xcheck
  parses as a session's result is the event stream its verbs wrote, not anything the
  child printed. A planted payload in a README, a source comment and a finding body
  leaves the assembled prompt byte-identical (`tests/test_injection_surface.py`).
- Every finding now carries a `locator` and a `source_hash`, and both are resolved
  against the recorded subject commit AT THE WRITE BOUNDARY. A finding whose evidence
  cannot be found is refused rather than filed and reviewed later — which is how a
  corpus reaches 133 findings nobody has independently checked.
- The Verifier is not handed the Auditor's reasoning: its capsule carries the finding,
  its locator and the material, and no path to the body. Two sessions that agree because
  one read the other's argument are one session with a second opinion attached.

None of that defends the agent's judgement. An injected agent can still be talked into
filing a true-looking finding about a real line of code, or into missing one. What these
buy is that a human who was not there can check where the evidence points and recompute
its hash, and that the injection cannot widen what the session was allowed to do.

**A5 found a real defect, which is why the suite exists.** `docker run --rm` removes a
container when the *client* exits normally; it does nothing when the client is killed,
because the daemon owns the container. A session that hit its timeout under `container`
therefore left its workload running with the full CPU, memory and PID budget it had been
given. The container is now named at launch and removed by name in `Sandbox.leave()`, on
every path out. The A5/`container` cell is the regression test.

Two limits stated rather than promised:

- **The container profile is opt-in and the default did not change.** Without
  `sandbox_profile=container` you get exactly what you got before: `readonly` for
  Auditor and Verifier, `worktree` otherwise. A byte comparison of the resolved
  profiles for a default conf is a test (`test_the_resolved_profile_for_a_default_conf_is_byte_identical`).
- **The shipped default image carries `sh`, `git` and busybox — no python3 and no agent
  CLI.** It is what the escape probes run under. Running real sessions under this
  profile means setting `container_image` to an image that carries your agent, pinned
  by digest; the capability report in the envelope records whichever image actually ran.
- **The digest pin is an invariant, not advice (0.9.1).** Every row of the matrix above
  was measured against *specific bytes*. A mutable tag means the image that held those
  results is not necessarily the image that runs tomorrow, so the whole table would be a
  claim about an image nobody can name. An unpinned `container_image` therefore
  **refuses to launch**, naming the image, the reason, and the `docker inspect` line
  that resolves the tag to a digest. Refused shapes: a bare tag, a digest shorter or
  longer than 64 hex, an upper-cased digest, an algorithm other than `sha256`, and
  `name:tag@sha256:…` — legal to docker, two answers to one question for a reader.
  `unsafe_allow_unpinned_image=on` waives it; the waiver is written **into the session
  log**, so a run that dropped the guarantee says so in its own record rather than in a
  console that has scrolled away. The waiver is about pinning only — it changes no
  mount, no network setting, no dropped capability and no role's read-only default.
- **`--network=none` removes DNS too — unless you set an egress allowlist (0.9.1).**
  By default this profile still has no network at all, and an agent CLI that needs its
  provider will not run under it. `egress_allowlist=api.example.com` is the third
  option: the audit container joins an **internal** docker network with no route off it,
  and a dual-homed broker sidecar permits exactly the listed hosts by HTTP `CONNECT`,
  appending host and verdict for every attempt to an egress log. It fails closed — a
  broker that cannot be established, or that dies mid-session, refuses the run rather
  than degrading to an open network, and the session is killed if the boundary lapses
  while it runs. Only this profile can enforce it; under `worktree` the allowlist
  refuses rather than pretending. The boundary is tested by ATTEMPTING the bypass —
  the provider is reached around the broker by name, by IP, and a public address is
  tried — with the same probe run from the uplink network as a live control.

  Three things it is **not**, stated because a partial control described as a whole one
  is worse than none:

  - It is **no defence against prompt injection.** An agent talked into exfiltrating
    through the *permitted* provider endpoint is permitted.
  - It is **not short-lived credential handling.** Credentials still travel by the
    environment allowlist for the whole session. **NOT BUILT.**
  - It is **not a package-registry proxy.** A registry is reachable only by being on
    the allowlist entirely, which is blunter than the proxy the design calls for.
    **NOT BUILT.**

## What xcheck does **not** do

These limits bind `orchestrated` sessions — the mode with the controls. Under
`uncontained-direct` they are not the limits; they are the ceiling of what you could
have had.

Say these out loud before running the tool on anything you care about.

- **No network isolation on the default profiles.** Under `worktree`, `readonly` and
  `none` the agent has whatever network access the machine has: it can fetch, and it
  can post. `sandbox_profile=container` is the answer, and it is opt-in — the tool does
  not turn it on for you.
- **No CPU or memory limit on the default profiles.** `cpu_seconds` and
  `address_space_mb` exist and default to `0` (unlimited). The timeout bounds
  wall-clock, not resource use. The `container` profile bounds CPU, memory and PIDs and
  refuses to start if any of the three is zero.
- **No defence against prompt injection.** A repository that tells the agent to ignore
  its charter may well succeed. What containment buys you is that the successful
  injection still cannot read an unallowlisted secret, still cannot outlive the
  timeout, and still arrives as a diff you review.
- **Redaction is best-effort against accident, not against an adversary.** It replaces
  values it was told about and shapes it recognises. An agent that base64-encodes a
  key, splits it across lines, or paraphrases it defeats the redactor completely.
  Treat logs as sensitive; do not paste them into a ticket unread. Since 0.9.1 the raw
  logs are written OUTSIDE the working tree (the path is printed at every dispatch), so
  they are no longer swept up by a `git status` or an archive of the repository — which
  makes them easier to lose track of, not harder to read. `audit/logs-manifest.jsonl`
  records each one's name, size and sha256, so a log that has been deleted reads as
  pruned rather than as missing.
- **The worktree is not a sandbox in the kernel sense.** It isolates the *repository*,
  not the *machine*. The child process can still read your home directory, write to
  `/tmp`, run any binary on `PATH` — and, as the table above records, write the
  original checkout directly, going around the courier entirely. Filesystem confinement
  is the operating system's job: that is what `sandbox_profile=container` asks for.
- **`sandbox_profile = none` removes almost all of the above.** It exists as an
  explicit, recorded opt-out for people who know why they want it.
- **A green `selftest` is not a security claim.** It says the decision logic behaves;
  it says nothing about the repository you pointed the tool at.
- **The wording style is delivered, not enforced.** All six launchers carry a block
  of wording rules generated from `styles/eli5.md`. This repository proves that block is
  **delivered** to every one of them through every packaging path, by reading the bytes each
  transform produced — and it **does not verify** that the model then wrote that way,
  because a model's prose has no deterministic oracle to check it against. If you need to
  know how one particular session spoke, read that session.

## Running it safely

Prefer `orchestrated`: `xcheck next` and `xcheck loop` are the entrances that enforce
anything, and since 0.9.1 every writing launcher goes through them. The only
`uncontained-direct` surface xcheck still ships is the read-only `/xcheck-status`;
anything else you start by hand is a session you are supplying the containment for.

The configuration that matches the design:

- a disposable clone or a dedicated branch — never your only copy;
- a shell with no production credentials in its environment;
- `push_after_commit` empty (the default): publication stays a human act;
- every courier diff read before it is merged;
- xcheck's conclusions treated as **advisory**, not as an automatic release gate.

Do not use xcheck, today, as an autonomous remediator on a production repository, on
untrusted client code, as a multi-tenant service, as a required CI gate, or as
evidence in a regulated or safety-critical process. The tool is honest about being a
supervised instrument.

## Reporting a problem

Open an issue at <https://github.com/vsov/xcheck/issues> for anything that is already
public or not exploitable.

For a vulnerability that is not yet public — a way to reach outside the containment
above, to make a write verb accept forged provenance, or to get a human gate to pass
without a human — use GitHub's **private vulnerability reporting** on the repository
(Security → Report a vulnerability) rather than a public issue. Include the version
(`xcheck --help` prints it, `audit/.provenance.json` records the installed one), the
sandbox profile in use, and the smallest reproduction you have.

There is no security SLA. This is a research-grade tool maintained by one person, and
promising a response window you cannot keep is its own kind of security theatre.
