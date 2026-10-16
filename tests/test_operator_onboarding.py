"""From a checkout to a first dispatch: the template an install picks, and the operator
profile nothing runs without.

A new operator met three gaps, one after the other, on a real codebase:

- `install.sh` always installed the text-corpus AUDIT.md. A codebase needed a hand `cp`
  that only a note under README §5.2 mentioned, so the plan began from `facts`,
  `style` and `currency`.
- Nothing on the install path said an operator profile is required. `xcheck next`
  refused, `xcheck status` said nothing about it, and the refusal's own hint —
  "`xcheck --policy PATH` with a path that does not exist prints the template" — named
  a command the CLI rejects: `--policy is not valid for no command`.
- The plan the Planner then wrote did not load. Of the three catalog tables
  `xcheck migrate` reads by column name, the templates showed the header of one, and
  the Planner named the Unit map's key column `id`, not `unit`.

So: `install.sh --type code|text`, `operator-profile.sh`, a refusal whose commands run,
a `status` line naming the profile in force, or saying there is none, and templates
that show every header the converter reads.

Every script here is the real one, run as a child process. A developer's own profile
must never decide a test, so each child gets an environment with neither
`$XCHECK_OPERATOR_PROFILE` nor `$XDG_CONFIG_HOME`.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.harness import REPO, XCHECK, Fixture, xcheck_submodule

policy = xcheck_submodule("policy")
INSTALL = REPO / "install.sh"
HELPER = REPO / "operator-profile.sh"
TEMPLATE = {t: (REPO / "templates" / f"AUDIT-{t}.md").read_bytes() for t in ("code", "text")}
PROFILE_SOURCES = (policy.OPERATOR_PROFILE_ENV, policy.XDG_CONFIG_ENV)


def clean_env(**over):
    """The caller's environment minus both profile sources, plus `over` (None drops)."""
    env = {k: v for k, v in os.environ.items() if k not in PROFILE_SOURCES}
    env.update(PYTHON=sys.executable, SHELL="/bin/sh")
    for k, v in over.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    return env


def trusted(text, level="trusted"):
    return text.replace(f"\n{policy.TRUST_KEY}=\n", f"\n{policy.TRUST_KEY}={level}\n")


class Scratch(unittest.TestCase):

    def tmp(self, what):
        d = Path(tempfile.mkdtemp(prefix=f"xcheck-onboarding-{what}-"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    def sh(self, script, *args, expect=0, **env):
        p = subprocess.run(["sh", str(script), *map(str, args)], capture_output=True,
                           text=True, timeout=120, env=clean_env(**env))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, expect, out)
        return out

    def install(self, *args, expect=0):
        return self.sh(INSTALL, *args, expect=expect)

    def helper(self, *args, expect=0, **env):
        return self.sh(HELPER, *args, expect=expect, **env)


class InstallPicksTheTemplate(Scratch):

    def audit_md(self, project):
        return (project / "audit" / "AUDIT.md").read_bytes()

    def test_type_code_installs_the_code_template(self):
        project = self.tmp("project")
        out = self.install("--type", "code", project)
        self.assertEqual(self.audit_md(project), TEMPLATE["code"])
        self.assertIn("AUDIT-code.md", out)

    def test_the_flag_may_follow_the_target(self):
        project = self.tmp("project")
        self.install(project, "--type=code")
        self.assertEqual(self.audit_md(project), TEMPLATE["code"])

    def test_no_flag_is_still_text_and_names_the_flag(self):
        # The default is unchanged, so every existing caller installs what it did
        # before. What changes is that the output says which template that was.
        project = self.tmp("project")
        out = self.install(project)
        self.assertEqual(self.audit_md(project), TEMPLATE["text"])
        self.assertIn("--type code", out)

    def test_an_unknown_type_installs_nothing(self):
        project = self.tmp("project")
        out = self.install("--type", "rust", project, expect=2)
        self.assertFalse((project / "audit").exists(), "a refused install wrote audit/")
        self.assertIn("code", out)
        self.assertIn("text", out)

    def test_a_missing_target_is_a_usage_error(self):
        out = self.install("--type", "code", expect=2)
        self.assertIn("usage:", out)

    def test_an_unfilled_template_is_kept_and_the_switch_is_named(self):
        # AUDIT.md is the operator's from the moment it exists, so a second install
        # never replaces it — even one that still holds nothing but a template. It
        # says how to switch instead.
        project = self.tmp("project")
        self.install(project)
        out = self.install("--type", "code", project)
        self.assertEqual(self.audit_md(project), TEMPLATE["text"])
        self.assertIn("unfilled text template", out)
        self.assertRegex(out, r"cp \S*templates/AUDIT-code\.md \S*audit/AUDIT\.md")

    def test_an_edited_plan_is_kept_without_a_switch_offered(self):
        project = self.tmp("project")
        self.install(project)
        (project / "audit" / "AUDIT.md").write_text("# a plan somebody wrote\n")
        out = self.install("--type", "code", project)
        self.assertEqual(self.audit_md(project), b"# a plan somebody wrote\n")
        self.assertIn("operator's", out)
        self.assertNotIn("cp ", out)

    def test_the_install_names_the_profile_step(self):
        out = self.install("--type", "code", self.tmp("project"))
        self.assertIn("operator-profile.sh --trust", out)


class APlanFilledInAsTheTemplateShowsLoads(Scratch):
    """Each catalog table is filled the way a Planner fills it: the template's example
    rows give way to one row under the header the template shows, a cell for each
    column that header names. This test never writes a header of its own, so a
    template that stops showing a column the converter needs fails here, the way a
    real plan failed: `the Unit map table has no 'unit' column`."""

    CELL = {"id": "N1", "source": "`README.md`", "scope": "what it promises",
            "key": "drift", "what it catches": "a claim the material contradicts",
            "norm source": "N1", "unit": "U01", "material": "`src/`",
            "approx. size": "1 kloc", "responsibility": "all of it"}

    def filled(self, text):
        lines, out, i = text.split("\n"), [], 0
        while i < len(lines):
            line, nxt = lines[i], (lines[i + 1] if i + 1 < len(lines) else "")
            if line.startswith("|") and nxt.startswith("|") and set(nxt) <= set("|-: "):
                cols = [c.strip() for c in line.strip("| ").split("|")]
                out += [line, nxt,
                        "| " + " | ".join(self.CELL.get(c, "-") for c in cols) + " |"]
                i += 2
                while i < len(lines) and lines[i].startswith("|"):
                    i += 1
                continue
            out.append(line)
            i += 1
        return re.sub(r"^- \[ \] P-01 — <.*$",
                      "- [ ] P-01 — drift × U01: check every claim; stop: U01 read",
                      "\n".join(out), count=1, flags=re.M)

    def test_each_template_filled_in_as_shown_converts(self):
        for t in ("code", "text"):
            with self.subTest(template=t):
                project = self.tmp("project")
                self.install("--type", t, project)
                plan = project / "audit" / "AUDIT.md"
                plan.write_text(self.filled(plan.read_text(encoding="utf-8")),
                                encoding="utf-8")
                p = subprocess.run([sys.executable, str(XCHECK), "--project", str(project),
                                    "--dry-run", "migrate"],
                                   capture_output=True, text=True, timeout=120,
                                   env=clean_env())
                out = p.stdout + p.stderr
                self.assertEqual(p.returncode, 0, out)
                for kind in ("norms", "dimensions", "units", "queue"):
                    self.assertRegex(out, re.compile(rf"^\s+{kind}\s+1$", re.M), kind)
                self.assertNotIn("no size column", out)


class OperatorProfileHelper(Scratch):

    def test_it_writes_the_template_with_the_trust_level_set(self):
        dest = self.tmp("operator") / "nested" / "operator.conf"
        out = self.helper("--trust", "trusted", dest)
        self.assertEqual(dest.read_text(encoding="utf-8"),
                         trusted(policy.OPERATOR_PROFILE_TEMPLATE),
                         "anything but the trust line differs from the template")
        keys = policy.load(dest, self.tmp("subject"))
        self.assertEqual(policy.trust_level(keys), policy.TRUSTED)
        self.assertIn(f'export {policy.OPERATOR_PROFILE_ENV}="{dest}"', out)
        self.assertEqual(sorted(p.name for p in dest.parent.iterdir()), ["operator.conf"],
                         "the staging file was left behind")

    def test_untrusted_also_sets_the_container_profile(self):
        # `untrusted` without `container` refuses every writing dispatch, so a helper
        # that set only the first would write a profile that cannot run a Planner.
        dest = self.tmp("operator") / "operator.conf"
        out = self.helper("--trust", "untrusted", dest)
        self.assertEqual(dest.read_text(encoding="utf-8"),
                         trusted(policy.OPERATOR_PROFILE_TEMPLATE, "untrusted").replace(
                             "\nsandbox_profile=\n", "\nsandbox_profile=container\n"))
        self.assertIn("docker", out)

    def test_the_trust_level_is_required_and_explained(self):
        dest = self.tmp("operator") / "operator.conf"
        out = self.helper(dest, expect=2)
        self.assertFalse(dest.exists())
        for level in policy.TRUST_LEVELS:
            self.assertIn(f"--trust {level}", out)

    def test_an_unknown_trust_level_is_refused(self):
        # The text, not only the exit code: `sh` exits 2 on a script it cannot open,
        # so a code alone passes against a tree with no helper at all.
        dest = self.tmp("operator") / "operator.conf"
        out = self.helper("--trust", "mostly", dest, expect=2)
        self.assertIn("--trust mostly is not a trust level", out)
        self.assertFalse(dest.exists())

    def test_it_never_overwrites_a_profile(self):
        dest = self.tmp("operator") / "operator.conf"
        dest.write_text("planner_cmd=mine {prompt}\n", encoding="utf-8")
        self.helper("--trust", "trusted", dest, expect=1)
        self.assertEqual(dest.read_text(encoding="utf-8"), "planner_cmd=mine {prompt}\n")

    def test_a_path_inside_the_named_project_is_refused_by_the_loader(self):
        project = self.tmp("subject")
        dest = project / "operator.conf"
        out = self.helper("--trust", "trusted", "--project", project, dest, expect=1)
        self.assertIn("may not live inside the repository being audited", out)
        self.assertEqual(list(project.iterdir()), [], "the refused profile left a file")

    def test_no_path_and_no_xdg_invents_no_location(self):
        out = self.helper("--trust", "trusted", expect=2)
        self.assertIn("/path/to/operator.conf", out)

    def test_under_xdg_the_path_is_optional_and_needs_no_export(self):
        xdg = self.tmp("xdg")
        out = self.helper("--trust", "trusted", XDG_CONFIG_HOME=str(xdg))
        dest = xdg / policy.OPERATOR_PROFILE_RELATIVE
        self.assertTrue(dest.is_file(), out)
        self.assertNotIn("export ", out)
        with mock.patch.dict(os.environ, {policy.XDG_CONFIG_ENV: str(xdg)}):
            os.environ.pop(policy.OPERATOR_PROFILE_ENV, None)
            self.assertEqual(policy.resolve(), dest, "xcheck does not find what it wrote")

    def test_a_fish_shell_gets_a_line_fish_can_run(self):
        dest = self.tmp("operator") / "operator.conf"
        out = self.helper("--trust", "trusted", dest, SHELL="/usr/bin/fish")
        self.assertIn(f'set -gx {policy.OPERATOR_PROFILE_ENV} "{dest}"', out)


class TheRefusalNamesCommandsThatRun(Scratch):
    """The refusal is the one text every new operator reads, so what it says to type
    has to work when typed."""

    def test_the_template_command_it_names_prints_the_template(self):
        # Before: the hint read "`xcheck --policy PATH` with a path that does not exist
        # prints the template", and that command exits 2 with `--policy is not valid
        # for no command`. The hint is parsed here rather than restated, so a reworded
        # hint is held to the same standard.
        hint = re.search(r"`xcheck (--policy PATH[^`]*)`", policy.no_profile_refusal())
        self.assertIsNotNone(hint, "the refusal no longer names a template command")
        argv = hint.group(1).split()
        argv[argv.index("PATH")] = str(self.tmp("operator") / "not-yet.conf")
        project = self.tmp("project")
        self.install(project)
        p = subprocess.run([sys.executable, str(XCHECK), "--project", str(project), *argv],
                           capture_output=True, text=True, timeout=120, env=clean_env())
        self.assertIn(policy.OPERATOR_PROFILE_TEMPLATE, p.stdout + p.stderr)
        self.assertNotIn("is not valid for", p.stdout + p.stderr)

    def test_it_names_the_helper_and_the_helper_is_executable(self):
        self.assertIn("operator-profile.sh", policy.no_profile_refusal())
        self.assertTrue(os.access(HELPER, os.X_OK), f"{HELPER} is not executable")

    def test_the_xdg_location_is_spelled_as_a_variable(self):
        # `$(XDG_CONFIG_HOME)` is command substitution in every POSIX shell.
        refusal = policy.no_profile_refusal()
        self.assertNotIn("$(XDG_CONFIG_HOME)", refusal)
        self.assertIn(f"${policy.XDG_CONFIG_ENV}/{policy.OPERATOR_PROFILE_RELATIVE}",
                      refusal)


class StatusNamesThePolicy(unittest.TestCase):
    """`status` is the read-only question an operator asks first, and the launcher
    skills tell it to look there for the profile. Before this it printed
    `decision: run-planner` on a machine where `next` would refuse."""

    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)

    def policy_line(self, out):
        lines = [ln for ln in out.splitlines() if ln.startswith("policy:")]
        self.assertEqual(len(lines), 1, out)
        return lines[0]

    def test_a_resolved_profile_is_named_with_its_digest(self):
        code, out = self.fx.run("status")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.policy_line(out),
                         f"policy: {self.fx.profile}  "
                         f"sha256:{policy.profile_digest(self.fx.profile)[:16]}")

    def test_no_profile_says_so_and_how_to_write_one(self):
        self.fx.profile.unlink()
        with mock.patch.dict(os.environ):
            os.environ.pop(policy.XDG_CONFIG_ENV, None)
            code, out = self.fx.run("status")
        self.assertEqual(code, 0, out)
        line = self.policy_line(out)
        self.assertIn("none", line)
        self.assertIn("operator-profile.sh", line)


class TheDocsSayItBeforeTheFirstSession(unittest.TestCase):

    README = (REPO / "README.md").read_text(encoding="utf-8")
    BOOTSTRAP = (REPO / "bootstrap.md").read_text(encoding="utf-8")

    def between(self, text, start, end):
        i = text.index(start)
        return text[i:text.index(end, i)]

    def test_installation_has_the_profile_step_before_the_launchers(self):
        section = self.between(self.README, "### 5.3 ", "### 5.4 ")
        self.assertIn("operator profile", section.splitlines()[0])
        self.assertIn("./operator-profile.sh --trust", section)
        self.assertIn("Install the launchers", self.between(self.README, "### 5.4 ", "\n"))

    def test_install_documents_the_type_flag(self):
        self.assertIn("./install.sh --type code", self.between(self.README, "### 5.2 ",
                                                                "### 5.3 "))
        self.assertIn("./install.sh --type code", self.BOOTSTRAP)

    def test_quickstart_step_one_points_at_the_profile(self):
        self.assertIn("§5.3", self.between(self.README, "### Step 1", "### Step 2"))

    def test_bootstrap_has_the_profile_step_before_the_planner(self):
        section = self.between(self.BOOTSTRAP, "## 1a.", "## 1b.")
        self.assertIn("./operator-profile.sh --trust", section)


if __name__ == "__main__":
    unittest.main()
