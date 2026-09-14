"""CLI-boundary contracts, asserted through the real command.

The declared grammar (README §8) is one command per invocation, with each flag
legal only for the commands that consume it. Every violation must produce an
ADDRESSED error naming the offending token — never a raw traceback, and never a
silent ignore, which is how `--budget 0 next` used to parse and then never reach
`cmd_next`.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.harness import XCHECK, Fixture, xcheck_module


class Grammar(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)

    def test_unknown_argument_is_addressed(self):
        code, out = self.fx.run("--banana", "status")
        self.assertNotEqual(code, 0)
        self.assertIn("unknown arg: --banana", out)

    def test_two_commands_in_one_invocation_are_refused(self):
        """F-0019: a second command word must not silently overwrite the first."""
        code, out = self.fx.run("status", "metrics")
        self.assertNotEqual(code, 0)
        self.assertIn("multiple commands", out)

    def test_value_taking_option_at_the_end_of_argv_is_addressed(self):
        """F-0121: reading args[i] past the end used to crash with a raw IndexError."""
        code, out = self.fx.run("status", "--project")
        self.assertNotEqual(code, 0)
        self.assertIn("--project requires a DIR value", out)

    def test_non_integer_session_count_is_addressed(self):
        """F-0121: a bare int() dumped a raw ValueError on `--max-sessions banana`."""
        code, out = self.fx.run("loop", "--max-sessions", "banana")
        self.assertNotEqual(code, 0)
        self.assertIn("must be an integer", out)

    def test_a_flag_is_refused_for_a_command_that_does_not_consume_it(self):
        """F-0090: `--force metrics` used to parse and do nothing at all."""
        code, out = self.fx.run("metrics", "--force")
        self.assertNotEqual(code, 0)
        self.assertIn("--force is not valid for", out)

    def test_help_exits_zero_and_prints_the_command_list(self):
        mod = xcheck_module()
        p = subprocess.run([sys.executable, str(XCHECK), "--help"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0)
        self.assertIn("xcheck", p.stdout)
        self.assertTrue(mod.__doc__)

    def test_status_exits_zero_as_a_real_child_process(self):
        """In-process assertions can miss an exit-code path; run the executable."""
        code, out = self.fx.run_subprocess("status")
        self.assertEqual(code, 0, msg=out)
        self.assertIn("decision:", out)


class Uninstalled(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="xcheck-test-bare-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def _run(self, *args):
        p = subprocess.run([sys.executable, str(XCHECK), "--project", str(self.root), *args],
                           capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout + p.stderr

    def test_every_stateful_command_refuses_without_an_installed_core(self):
        """A project with no `audit/XCHECK.md` is not an audit; each command must say
        so and exit non-zero rather than decide over an absent state."""
        for cmd in ("status", "next", "lint", "metrics"):
            with self.subTest(cmd=cmd):
                code, out = self._run(cmd)
                self.assertNotEqual(code, 0)
                self.assertIn("XCHECK.md not found", out)
                self.assertIn("install xcheck first", out)


if __name__ == "__main__":
    unittest.main()
