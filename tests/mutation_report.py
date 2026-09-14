"""The strength table for `tests/test_mutations.py`, and the gate on top of it.

A mutation witness can prove two quite different things, and the difference is the
whole point of this file:

- **consumer-reaching** — the mutated package was carried to a real decision
  (`status`, `next`, `lint`, the courier, a write verb) and the witness asserted the
  SPECIFIC wrong outcome the missing guard permits: a named finding at a status the
  lifecycle forbids, a named pass reported done with no coverage record, a named path
  applied outside `audit/`, a named limit taking effect that no verb wrote. That is a
  proof.
- **message-only** — the mutant did not sail through; it fell to a LATER barrier, and
  all the witness can honestly assert is that the guard's own addressed message is
  gone. That is still information (the guard participates in the route, and the
  operator now gets the wrong explanation), but it is not a proof that a consumer
  would have accepted an invalid state.

The previous run's battery reported the second kind as though it were the first. So
classification here is DERIVED, never declared: a row is `consumer-reaching` if and
only if the witness supplied an oracle that ran against the mutant and passed. There
is no argument that says "call me consumer-reaching".

A `message-only` row must name the barrier that intercepted it, by module and SYMBOL
— not by line number. A line number is invalidated by the next edit above it
(`verifiable-claims-check-structure-not-self-counts`); a symbol is resolved to its
current line when the table prints, so the reader gets `state.py:657` and the claim
stays true across edits. A symbol that no longer exists fails the row, which is the
correct outcome: the barrier moved, so the reason on the row is now fiction.
"""

from tests.harness import REPO

# The invariants this run had to establish. Each maps to the phase that built it. A P0
# invariant classified `message-only` fails the suite: for these, "the error message
# changed" is not an acceptable standard of proof.
P0_INVARIANTS = {
    "courier-path-authorization":
        "phase 1 — a read-only role's change outside audit/ is refused, not applied",
    "state-immutability":
        "phase 2 — a validated State cannot be changed in memory and then obeyed",
    "single-write-path":
        "phase 3 — machine state becomes durable only through the validated boundary",
    "view-drift-refusal":
        "phase 4/§2 — a hand-edited view is refused, never read back as authority",
    "failed-session-effects":
        "0.9.1 phase 3 — a session that did not end `ok` changes nothing in the "
        "project; its patch is quarantined for a human, never applied",
    "retry-material-effect":
        "0.9.1 phase 4 — a retry is refused unless the ABSENCE of material effects is "
        "proved over files, audit artifacts and git HEAD, not assumed from state.json",
    "parallel-evidence-preservation":
        "0.9.1 phase 8 — a merged pass's artifacts survive the destruction of its "
        "worktree: the evidence body, the pass report and the split remainder, byte "
        "for byte, or the merge does not happen",
}

_ROWS = []


def reset():
    _ROWS.clear()


def record(*, guard, module, probe, p0=None, reaches=None, wrong_outcome=None,
           barrier=None, control=None, mutated=None):
    """One witness's row. `wrong_outcome` here is the TEXT of what was asserted —
    the assertion itself already ran in the witness; this is what the reader sees."""
    _ROWS.append({
        "guard": guard, "module": module, "probe": probe, "p0": p0,
        "reaches": reaches, "wrong_outcome": wrong_outcome, "barrier": barrier,
        "control": control, "mutated": mutated,
        "kind": "consumer-reaching" if wrong_outcome else "message-only",
    })


def rows():
    return list(_ROWS)


def resolve(barrier):
    """`(module, symbol, reason)` -> `module:line — reason`, or raise.

    The line is looked up NOW, from the file as it currently stands, so the table
    never carries a stale number and a vanished barrier is a failure rather than a
    quietly wrong citation."""
    module, symbol, reason = barrier
    path = REPO / "xcheck" / module
    if not path.is_file():
        raise AssertionError(f"barrier names {module}, which does not exist")
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if symbol in line:
            return f"{module}:{i} — {reason}"
    raise AssertionError(
        f"barrier symbol {symbol!r} is not in xcheck/{module} any more, so the "
        f"reason on that row is fiction: either the later barrier moved (update the "
        f"row) or it is gone (the witness may now be consumer-reaching)")


def p0_failures():
    """Every reason the P0 gate should fail. Empty list means the gate holds."""
    out = []
    seen = {}
    for r in _ROWS:
        if r["p0"]:
            seen.setdefault(r["p0"], []).append(r)
            if r["kind"] != "consumer-reaching":
                out.append(f"P0 invariant {r['p0']!r} is classified {r['kind']} "
                           f"(guard: {r['guard']}) — for a P0 invariant, a changed "
                           f"error message is not proof that a consumer would have "
                           f"accepted an invalid state")
    for key, why in P0_INVARIANTS.items():
        if key not in seen:
            out.append(f"P0 invariant {key!r} has NO witness at all ({why}) — an "
                       f"invariant nothing mutates is an invariant nothing measures")
    return out


def counts():
    c = {"consumer-reaching": 0, "message-only": 0}
    for r in _ROWS:
        c[r["kind"]] += 1
    return c


def render():
    """The table, as printed by the suite."""
    lines = ["", "", "=" * 78,
             "MUTATION WITNESS STRENGTH — what each witness actually proves",
             "=" * 78]
    for kind in ("consumer-reaching", "message-only"):
        group = [r for r in _ROWS if r["kind"] == kind]
        lines.append(f"\n--- {kind.upper()} ({len(group)}) ---")
        for r in sorted(group, key=lambda x: (x["p0"] or "zz", x["guard"])):
            tag = f"  [P0 {r['p0']}]" if r["p0"] else ""
            lines.append(f"\n  {r['guard']}{tag}")
            lines.append(f"    mutation : {r['module']}")
            if kind == "consumer-reaching":
                lines.append(f"    consumer : {r['reaches']}")
                lines.append(f"    proves   : {r['wrong_outcome']}")
            else:
                lines.append(f"    barrier  : {resolve(r['barrier'])}")
            lines.append(f"    control  : {r['control']}")
            lines.append(f"    mutated  : {r['mutated']}")
    c = counts()
    lines += ["", "-" * 78,
              f"  {c['consumer-reaching']} consumer-reaching, {c['message-only']} "
              f"message-only, {len(_ROWS)} total.",
              f"  P0 invariants: {len(P0_INVARIANTS)}, all required to be "
              f"consumer-reaching.",
              "-" * 78]
    return "\n".join(lines)
