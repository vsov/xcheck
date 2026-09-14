"""xcheck — an audit orchestrator that drives the cycle between its human gates.

The package is layered; each module may import only from the ones above it:

    util        vocabulary, canonical-field normalizers, conf     (imports nothing here)
    legacy_md   the Markdown control-plane parsers                (removed from consumers in phase 5)
    validate    structural validators and fail-closed gates
    courier     git durability: session dirt, commits
    runner      the writing lock and agent-session dispatch
    decision    the lifecycle decision
    cli         argument grammar and the six commands
    selftest    the embedded battery (temporary; shrinks as its subject is deleted)

`cli` imports `selftest` lazily inside `main()` — the battery imports every
production symbol including `cli`'s own commands, so that one edge is a genuine
cycle and a deferred import is the honest way to break it.
"""

# THE version. Every other surface — XCHECK.md, both plugin manifests, pyproject.toml
# — is asserted equal to this by tests/test_version_parity.py, which reads all five
# from their files rather than restating the number. Before phase 10 three of them
# disagreed (XCHECK.md 0.3.0, manifests 0.7.1, git tags v0.3.0) and nothing noticed;
# the test, not the bump, is what stops that recurring.
__version__ = "0.9.5"
