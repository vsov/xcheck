# The payer's token ceiling

Added in 0.9.6 (readiness matrix row P1b). The operator who pays for the model can set a
token ceiling in the **operator profile**. The audited repository cannot raise or disarm
it.

Unit: tokens. Guarantee: an ADMISSION ceiling — no session is dispatched unless the operator's remaining tokens cover its reservation (tokens_per_session); one in-flight session may exceed its reservation; money is not guaranteed.

## Setting it

Put these in the operator profile, outside the audited repository:

```
tokens_per_audit=2000000
tokens_per_session=250000
```

- `tokens_per_audit` is the ceiling. It needs `tokens_per_session` beside it, and the
  profile is refused without one.
- `tokens_per_session` is the **reservation**: what every dispatch is charged until the
  provider says what it actually cost.
- `tokens_per_pass` and `budgets` are accepted in the profile too.
- Under the operator's ceiling, `tokens_per_session` needs no `{token_cap}` in the role
  command. It is a reservation, not a provider flag.

## What the subject can do to it

The same keys in `audit/orchestrator.conf` can only **tighten**:

- the effective value is the smaller of the two;
- `budgets=off`, a raised value, `0`, a negative number or garbage changes nothing;
- `operator_ceilings` and `budget_mode` are refused in either file.

## The account

`xcheck status` prints it. It is recomputed from `audit/events.jsonl` on every
decision, so a reload gives the same number. Every session the stream names is charged
once:

| Session | Charge |
|---|---|
| finished, tokens measured by the provider (`telemetry_source=provider`) | those tokens, overshoot included |
| finished or abandoned without them (crash, lost telemetry, agent-reported, log-parsed) | the reservation, or the reported figure if larger |
| dispatched and not finished | the reservation |

`remaining = ceiling − charged`. Admission works out how many reservations fit:

- `next` and `loop` (and `--dry-run`) stop at `gate: stop-ceiling`. The gate names the
  ceiling, the amount charged and what remains.
- The parallel fan-out admits `remaining // tokens_per_session` passes, under the lock and
  before any sandbox exists. The rest are refused by name.
- A retry is a new dispatch. It re-enters admission, and the attempt it follows is
  already charged.

## What it does not do

- It is not a money ceiling. Tokens are not priced here, and cached and reasoning tokens
  cost different amounts.
- It does not stop a session mid-flight. Codex CLI enforces no per-session cap, so the
  last admitted session can overshoot by its own overrun.
- `budget_mode=hard` is refused for exactly that reason.
