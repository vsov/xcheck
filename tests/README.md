# tests/ — the external battery

## The green condition

A test in this directory passes only when a **real command's observable
outcome** is what it should be: the decision line `next --dry-run` printed, the
exit code the shell saw, or the addressed message naming the offending file and
field. Calling a predicate and asserting its return value is explicitly **not
sufficient**, and a test that does only that is not finished.

The reason is on the record. Ouroboros-2 found a test that called a predicate
directly and never checked whether the predicate was wired into `lint`; deleting
the real hook broke nothing (`retro-6.md`). A predicate that answers correctly
in isolation, and a command that never asks it, produce a green suite over a
live bypass — which is exactly the shape the external audit reproduced eight
times against a 271/271 embedded selftest.

## The control rule

Every bypass test is paired with a **control**: the same fixture with the poison
replaced by a legitimate declaration, asserting the *opposite* outcome. A test
that stops the cycle proves nothing on its own — a fixture broken in some
unrelated way also stops the cycle. Only the pair shows that the guard under
test is what made the difference.

`test_mutations.py` applies the same rule from the other end. A guard being
present is not the same as a guard being consulted, so each witness copies the
package, breaks exactly one guard, runs a real command, and asserts the outcome
changed — with the unmutated copy running the identical probe against the same
fixture **in the same test method**, so the control cannot be skipped alone.
That control arm is not ceremony: this project once ran a mutation harness whose
baseline was truncated, and against a broken baseline everything reddens. The
control copy goes through the *same* copy routine as the mutants for the same
reason.

## Where fixtures live

In a system temp directory (`tempfile.mkdtemp`), never inside the project tree.
The courier commits the project tree, so an in-tree fixture is committed as
audit material (F-0120).

## Layout

| file | what it holds |
|---|---|
| `harness.py` | imports the `xcheck` package, builds temp audit trees, runs the CLI in-process and as a subprocess. `Fixture` builds a `state.json` tree; `LegacyFixture` builds a Markdown one and survives for exactly two jobs — the eight bypasses, whose poison only exists in Markdown, and phase 6's migration tests |
| `test_consumer_paths.py` | the eight Markdown control-plane bypasses, each with a control, and each with a one-line note on why the poison is now inexpressible |
| `test_cli_grammar.py` | CLI-boundary contracts: one command per invocation, flags only where consumed, addressed errors |
| `test_legacy_selftest.py` | characterization net over the embedded `selftest()` — the count is pinned, and a drop must be accounted for row by row in `docs/phase-5-deleted-checks.md` |
| `test_state.py` | `state.json`: 59 negative schema cases, the no-State-on-a-bad-document proof, atomic-write crash simulation |
| `test_no_bypass.py` | the central witness: with `load_state` stubbed to raise, all six consumers fail; the courier refuses to commit an unreadable document; `migrate_readers` is imported only by `migrate.py`, and `migrate.py` by nothing at module scope |
| `test_views.py` | the generated mirrors: evidence bodies survive a render byte for byte, `next` is derived from status, and a hand-edited `LEDGER.md` or frontmatter field stops every consumer instead of being silently re-synced |
| `test_migrate.py` | the one-shot converter: `--dry-run` writes not a byte and not an mtime, a real run round-trips and lints clean, the backup restores the tree without git, and every refusal is paired with a control |
| `test_state_consumers.py` | the concerns whose selftest home was deleted but whose subject survived the parser — construal gate, refusal route, scope typing, recurrence routing, reopen limit, state digest |
| `test_envelope.py` | the 13-field invocation envelope, the append-only event stream, the producer-checked `--json` contract, and independence as a measured level |
| `test_write_verbs.py` | the write verbs as the only path to machine state |
| `test_runner_sandbox.py` | containment against REAL child processes: a grandchild really dies, a secret really never enters the environment, a worktree really disappears |
| `test_mutations.py` | 15 mutation witnesses across the trust boundaries — schema, cross-record, duplicates, view drift, verifier≠fixer, producer≠admitter, reopen limit, class members, coverage binding, profile fail-closed, skip-permissions, env allowlist, timeout kill, lease TTL, `fixed-by` binding. Prints the full table |
| `test_crash_recovery.py` | the writer killed at five distinct points: before the temp write, before `fsync`, before `os.replace`, before the git commit, and before the view regeneration |
| `test_release_lifecycle.py` | install → plan → migrate → run → upgrade → resume on one temp project, and the artefact-cleanliness check |
| `test_upgrade.py` | `xcheck upgrade`: what it refreshes, what it refuses, and why the version stamp and the manifest are two different claims |
| `test_version_parity.py` | one version across five surfaces, read from the files rather than restated |
| `test_doc_drift.py` | the documented verb set and the implemented one, checked as set equality in both directions; plus README's two reference tables against `LIMIT_KEYS` / `CONF_DEFAULTS`, its layout paths, and every relative link |
| `test_hardening.py` | the phase-12 pass: 60 malformed invocations with zero tracebacks, eight degenerate states each named with its remedy, the edges (RTL, 2 000-char titles, pipes in a table view, CRLF, 500 findings, a missing body), the 500-finding timings, and the proof that material content reaches no decision |

## Running

```bash
bash ci/run-checks.sh
```

That script is the single entry point CI uses, so every CI check is runnable
locally by one command. To run the battery alone:

```bash
python3 -m unittest discover -s tests -v
```
