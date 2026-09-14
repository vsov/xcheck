"""The test package — and the one place the orchestration environment is stripped.

F-0125 / F-0132. An orchestrated Auditor session runs with `XCHECK_LOCK_INHERITED` set:
the orchestrator puts it in the child environment (`runner.child_environment` copies every
`XCHECK_*` variable through) and names it in the prompt, and §4 rule 8 tells the session to
export it. That nonce belongs to the AUDIT's `audit/.lock`, and every fixture in this suite
is a different project in a system temp directory with no lock of its own. `write.held_lock`
then finds a signal saying a lock is held and no owner record to match it against, and
refuses — 49 of the 80 U24 tests failed that way inside a real session, before reaching the
route they assert.

Stripped HERE, at the package, rather than at each call site: `unittest` imports
`tests.<module>`, so this runs before any test module is loaded, and it covers all three
ways a fixture reaches the code — in-process `Fixture.run`, `Fixture.run_subprocess`, and
the real children `test_parallel_preservation` launches through `child_environment`. Put at
the call sites instead it would be forgotten by the next path added, which is exactly how
this arrived.

This does NOT weaken `held_lock`, and `tests/test_fixture_isolation.py` asserts that it
does not: a project whose lock has no readable owner still refuses a command carrying a
nonce, and a fixture that owns its lock still has its own nonce honoured. What is removed
is an inherited signal about a DIFFERENT project — a test that wants one sets it itself,
the way `tests/test_write_under_a_held_lock.py` builds an explicit environment.
"""
import os

# Every variable the orchestrator puts into a session's environment. `XCHECK_SESSION_ID`
# and `XCHECK_CONSTRUAL_KEY` are stripped for the same reason as the nonce even though
# neither has caused a failure yet: they identify the outer session, no fixture is that
# session, and leaving them is leaving the next instance of this bug in place.
ORCHESTRATION_ENV = ("XCHECK_LOCK_INHERITED", "XCHECK_SESSION_ID", "XCHECK_CONSTRUAL_KEY")

# What was actually inherited and removed, kept so a test can assert the strip happened
# and a debugging session can see what it was running under.
INHERITED_ORCHESTRATION = {k: os.environ.pop(k) for k in ORCHESTRATION_ENV if k in os.environ}
