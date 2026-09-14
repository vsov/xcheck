# Experimental modules

Two modules in `xcheck/` are shipped, tested, and reachable from no production path.
They are listed here because the alternative — leaving a public conf key pointing at
them — is what the fourth industrial-readiness audit found and named:

> *"Several of the most valuable features exist only as isolated, unit-tested libraries
> behind public config flags that promise a working feature."*

A flag is a promise. A library is not. This page keeps the two apart.

## What "experimental" means here

- **No conf key.** `diff_scope`, `evidence_cache` and `evidence_dir` were withdrawn in
  the fourth audit's response. Setting any of them in `audit/orchestrator.conf` or in an operator profile is
  **refused by name**, with the reason — not ignored. `policy.WITHDRAWN_KEYS` carries the
  text, and both loaders reach it, including the one that otherwise tolerates keys it does
  not recognise.
- **No behaviour.** Nothing a user can run reaches these modules. That is checked, not
  asserted: `tests/test_feature_truthfulness.py` resolves every public flag to a
  production call site over the AST, and would fail if one of these acquired a key
  without acquiring a caller.
- **Still tested.** `tests/test_diff_scope.py` and `tests/test_evidence_cache.py` run in
  the release tier, unchanged in count. Deleting work that was correct except for being
  unreachable would be the wrong lesson; the defect was the promise, not the code.

## `xcheck/scope.py` — diff-directed scope

Selects the units a change reaches, expanded across the import edges, and reports how
stale the last UNSCOPED pass is.

**Why it is not on:** `scope.selection()` had zero production call sites. `cli.cmd_next`
never asked it what to look at, so `diff_scope=on` narrowed nothing.

**Promotion condition:** `cli.cmd_next` calls `selection()` and passes the result into
the capsule the session is dispatched with, with the base recorded on the pass. The
staleness report (`sweep_status`, already live in `xcheck status`) is what keeps
incremental auditing from becoming a permanent blind spot, and it must stay on
whether or not scoping is.

## `xcheck/evidence.py` — the evidence cache

Keys an entry on the six inputs that could make evidence wrong — subject bytes, charter,
norms, policy digest, auditor identity, capsule — and refuses to carry a VERDICT across
material that may have moved.

**Why it is not on:** `read_entry()` and `write_entry()` had zero production call sites.
No pass ever consulted the store, so `evidence_cache=on` reused nothing and
`evidence_dir` pointed at a directory that was never written.

**Promotion condition:** a dispatch path calls `read_entry()` before running a pass and
`write_entry()` after one, with the cache decision recorded on the session so a reader
can tell reused evidence from fresh evidence. `evidence_dir` comes back with it, as an
operator key, for the reason it was one: a subject that could point the store back inside
itself could write the evidence its own next audit reads.

## What promotion costs

The same six sites any public conf key costs — `PROJECT_KEYS` or `POLICY_KEYS`,
`CONF_DEFAULTS`, the `DEFAULT_CONF` template or `OPERATOR_PROFILE_TEMPLATE`,
`BOOLEAN_CONF`/`NUMERIC_CONF`, the README table, and the `LATER` tuple in `tests/test_image_pinning.py` —
plus the entry in `policy.WITHDRAWN_KEYS` removed, plus a row in the
`FEATURES` table of `tests/test_feature_truthfulness.py` naming the consumer that reaches the
mechanism. That last one is the point: the key becomes available at the same moment the
call site does, and not before.
