# The contained Codex path

One supported way to run xcheck's roles on a real model: Codex CLI 0.162.1 in a pinned
container, with the token figures read by the **parent** from the CLI's own event stream.
Added in 0.9.6 (readiness matrix row P1a). The image, the parser and the doctor checks are
tested offline against a fake image (`images/fake-codex`) that writes the same event
shapes. The real CLI was checked offline for `thread.started`, `turn.started`, `error` and
`item.completed`. `turn.completed` from a live session is checked by the phase-9 canary.
Until that runs, the usage shape is **NOT VERIFIED** against a paid session.

## The path, step by step

1. **Build the image.** This builds a pinned `node:22-slim` base with `@openai/codex@0.162.1`
   and `ca-certificates`:

   ```bash
   docker build -t xcheck-codex images/codex
   ```

   Then pin the result by its ID. A locally built image has no registry digest:

   ```bash
   docker image inspect -f '{{.Id}}' xcheck-codex
   ```

   The build recorded for this release (2026-10-17, arm64) is
   `sha256:20fbca67a4ab164523d440e2472d1f9313e8dc782e2e9911b30a2aa077d5aa4a`. The apt layer
   is not byte-reproducible across days, so pin **your** build's ID, not this one.

   Set the bare ID, `sha256:<64 hex>`. Do not write `xcheck-codex@sha256:<ID>`: the Linux
   engine reads a digest after a name as a registry digest and tries to pull it.

2. **Write the operator profile.** The profile lives outside the audited repository. The
   commented block in `docs/operator.conf.example` is this:

   ```
   sandbox_profile=container
   container_image=sha256:<your build's full ID>
   telemetry_adapter=codex-stream
   env_allowlist=OPENAI_API_KEY
   egress_allowlist=api.openai.com
   auditor_cmd=codex exec --json --ephemeral --skip-git-repo-check -s workspace-write {prompt}
   ```

   - `--json` makes the CLI write the event stream the parent reads. Without it there is
     nothing to measure.
   - `--ephemeral` keeps no session files in the (tmpfs) HOME.
   - `--skip-git-repo-check` is needed because `/work` is a disposable clone whose git
     metadata may point outside the container.
   - Do **not** put `{sidecar}` in a contained command. It names a host path that no mount
     reaches, and the launch refuses it.

3. **Export the credential**, by name only. Its value never goes into a file xcheck reads.
   The container's HOME is an empty tmpfs, so a stored `codex login` is not visible inside
   it. `CODEX_API_KEY` is accepted too.

   ```bash
   export OPENAI_API_KEY=...
   ```

4. **Run the preflight:**

   ```bash
   xcheck doctor
   ```

   Under this profile, doctor:
   - runs `<image> codex --version` with `--network=none`: PASS only on exit 0, FAIL
     otherwise, UNVERIFIED when docker is down;
   - PASSes `telemetry-sidecar-supported` only for the stream adapter;
   - FAILs `provider-path-permitted` when the egress allowlist does not permit
     `api.openai.com`, or when `env_allowlist` names no credential variable or the
     variable is unset. It names the variable and never prints its value.

5. **Dispatch** with `xcheck next` / `xcheck loop`.
   - The session runs in the container with exactly three mounts: `/src` (read-only),
     `/work` and `/out`.
   - A non-ok session is quarantined, as under every profile.

6. **Read the record.**
   - `telemetry_source=provider`, with `tokens_input`, `tokens_cache` (cached input) and
     `tokens_output` summed over the stream's `turn.completed` events.
   - `provider_request_id` is the thread id. `provider_stop_reason` is `completed` or
     `turn.failed: …`.
   - The stream names **no model**, so `provider_model` is absent and the route is
     recorded as `unattested`. `provider` vouches for the counts, not for which model ran.

## Trust model

| Figure | Source | Label |
|---|---|---|
| Top-level JSONL line in the CLI's stdout | the CLI process, captured by the parent | `provider` |
| An agent message or tool output (nested, JSON-escaped inside `item.*`) | the agent | never read as usage |
| A plain `XCHECK_TELEMETRY` line in the stream | not the CLI | ignored, noted in `telemetry_refused` |
| A `tokens used` text match | not the CLI | not read under the stream adapter |
| A stream with no `turn.completed` | — | tokens **unmeasured** (null), never 0 |

The parent writes the sidecar (`<log root>/.telemetry/<session>.json`, mode 0700 directory)
from the stream. It is bound to the session id and the log digest. Nothing in the
container can reach that directory, and a stream with no usage removes any file already
there.

## Residual, stated

- **`/proc/<pid>/fd/1`.** A process inside the container running as the CLI's uid can
  write to the CLI's stdout and forge a top-level line. The container keeps that forgery
  off the host, but it can still lie about its own cost. An operator who needs more than
  this must take usage from the provider's billing API, which xcheck does not do.
- **Model.** The model is not attested by the stream (see step 6).
- **Websocket egress.** The real CLI first tries `wss://api.openai.com/v1/responses`, then
  falls back to HTTPS (observed offline). Whether the egress broker's CONNECT path carries
  the websocket is checked by the canary, not here.
- **Live usage shape.** Usage parsing follows the documented `turn.completed` shape. It is
  **NOT VERIFIED** against a paid session until the phase-9 canary.

## Budget

Codex CLI takes no per-session token cap, so xcheck cannot stop a Codex session partway
on tokens. What the operator gets is an admission ceiling, set in the operator profile
(`docs/payer-ceiling.md` has the full rule):

```
budgets=on
tokens_per_audit=<the payer's ceiling>
tokens_per_session=<the reservation charged for each dispatch>
```

- A dispatch is admitted only if the remaining tokens cover `tokens_per_session`.
- After the session, the account is charged the tokens the parent read from the CLI's
  own `turn.completed` events (`telemetry_source=provider`).
- A finished session with no provider figure is charged its reservation, or the agent's
  own figure if that is higher; never zero. That covers a crash and lost telemetry. A
  session still open holds its reservation.
- The subject's `orchestrator.conf` can only tighten these values.

> Unit: tokens. Guarantee: an ADMISSION ceiling — no session is dispatched unless the operator's remaining tokens cover its reservation (tokens_per_session); one in-flight session may exceed its reservation; money is not guaranteed.

`xcheck status` prints the account: the ceiling; what was charged, split into
provider-measured tokens, reservations for sessions with no provider figure, and tokens
held by open sessions; and what remains.

## Live canary, 2026-10-17: NOT VERIFIED

`bash .supergoal/probes/canary.sh --run` checks every precondition before it dispatches
anything:

- phases 7–8 green;
- the image built;
- the operator ceiling at 50,000 tokens;
- `-s read-only`, a one-turn charter and a 180 s timeout;
- the egress allowlist naming only `api.openai.com`;
- a credential present **by name**.

On 2026-10-17 the last check failed: neither `OPENAI_API_KEY` nor `CODEX_API_KEY` was set
in the operator's environment. A stored `codex login` is not copied into the container,
by design. So:

**NOT VERIFIED — needs an operator credential in the environment.** Sessions launched: 0.

This leaves three things unobserved against a paid session:
- the live `turn.completed` usage shape;
- websocket egress through the broker;
- the ≤ 50,000-token bound.

To close them, export the key and run the script once with `--run`. The full record is in the
development tree, `docs/canary-0.9.6.md`.

