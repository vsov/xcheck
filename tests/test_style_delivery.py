"""The block reaches the agent through every path xcheck has, not just the source file.

`skills/*/SKILL.md` is not what an agent reads. Four transforms sit between that file and
an agent's context:

    Claude skills install    cp into ~/.claude/skills/<name>/SKILL.md
    Codex skills install     cp into ~/.agents/skills/<name>/SKILL.md
    OpenCode generation      new frontmatter + `awk 'c==2{print} /^---$/{c++; next}'`
    plugin bundles           .claude-plugin (skills/ by convention) and .codex-plugin
                             (an explicit `"skills": "./skills/"`)

A block that survives three of them is a block that is missing on one platform, and the
platform it is missing on is the one nobody tested. So every arm here asserts on the bytes
a transform PRODUCED, in a temp destination, never on the source.

The installer writes into `$HOME`. The operator of this machine has xcheck launchers
installed there. Every arm therefore runs the REAL script with `HOME` redirected into a
system temp dir — the real code path, none of the operator's files — and `tearDown` proves
both the project tree and the real `$HOME` launchers came through byte-identical.
"""

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest


REPO = pathlib.Path(__file__).resolve().parent.parent
INSTALLER = REPO / "launchers" / "install-launchers.sh"
BEGIN = "<!-- xcheck:style:begin -->"
END = "<!-- xcheck:style:end -->"
NAMES = ("xcheck-plan", "xcheck-audit", "xcheck-triage",
         "xcheck-remediate", "xcheck-verify", "xcheck-status")


def body_of(text):
    if BEGIN not in text or END not in text:
        return None
    b = text.index(BEGIN) + len(BEGIN)
    return text[b:text.index(END, b)].strip("\n")


def source_body():
    return body_of((REPO / "styles" / "eli5.md").read_text())


def digest(paths):
    """{path: sha256} for a set of files — the instrument for 'nothing moved'."""
    out = {}
    for p in sorted(paths):
        if p.is_file():
            out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def project_files():
    """Everything this phase could plausibly disturb. `__pycache__` is excluded: it is
    build output an unrelated import can create mid-run, which would make the teardown
    fail for a reason that has nothing to do with what it is watching."""
    for sub in ("skills", "launchers", "styles", "ci"):
        yield from (p for p in (REPO / sub).rglob("*") if "__pycache__" not in p.parts)


def home_launcher_files():
    """The operator's own installed launchers. Read to prove they were not touched."""
    home = pathlib.Path(os.path.expanduser("~"))
    for sub in (".claude/skills", ".agents/skills", ".codex/prompts",
                ".config/opencode/command"):
        d = home / sub
        if d.is_dir():
            yield from (p for p in d.rglob("*") if "xcheck" in p.name or "xcheck" in str(p.parent))


class EveryDeliveryPathCarriesTheBlock(unittest.TestCase):

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="xcheck-delivery-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fake_home = self.tmp / "home"
        self.fake_home.mkdir()
        # Snapshots taken BEFORE anything runs, so tearDown compares against reality
        # rather than against what the test hopes reality was.
        self.project_before = digest(project_files())
        self.home_before = digest(home_launcher_files())
        # A snapshot that covers the wrong files reports "unchanged" exactly as happily
        # as one that covers the right files. Name what it must contain.
        for must in [REPO / "skills" / n / "SKILL.md" for n in NAMES] + \
                    [INSTALLER, REPO / "styles" / "eli5.md", REPO / "ci" / "render-style.py"]:
            self.assertIn(str(must), self.project_before,
                          f"the tree snapshot does not cover {must}")
        # Six, or none, and nothing in between.
        #
        # `>= 6` says the tearDown comparison is watching something: this developer's
        # machine has six launchers installed, and "unchanged" over an empty list is
        # green for the same reason a working check is — [[spy-needs-a-positive-control]].
        # On a machine that has never installed them — a CI runner, a fresh clone — the
        # snapshot is empty because there is nothing to watch, and demanding six was the
        # first thing the remote gate ever found: five failures, `0 not >= 6`.
        #
        # The half that matters holds either way. tearDown compares the SETS, so a file
        # APPEARING under the real $HOME is caught from an empty baseline exactly as it
        # is from six — and "the installer must not write to the operator's $HOME" is
        # the claim worth keeping armed. What an empty baseline cannot say is that an
        # existing launcher went unmodified, so that is asserted where one exists.
        # A count between the two is neither: launchers ARE installed and the install is
        # partial, which makes "unchanged" unreadable rather than true or false.
        self.assertTrue(not self.home_before or len(self.home_before) >= 6,
                        f"the $HOME snapshot found {len(self.home_before)} launcher "
                        f"file(s) — not none, so launchers are installed here, and not "
                        f"the six a full install leaves. The snapshot is watching a "
                        f"partial install and cannot say what unchanged means.")
        self.body = source_body()
        self.table = []

    def tearDown(self):
        moved = [p for p, h in digest(project_files()).items()
                 if self.project_before.get(p) != h]
        appeared = set(digest(project_files())) - set(self.project_before)
        self.assertEqual([], moved, f"the project tree changed during the test: {moved}")
        self.assertEqual(set(), appeared, f"files appeared in the project tree: {appeared}")

        home_now = digest(home_launcher_files())
        touched = [p for p, h in home_now.items() if self.home_before.get(p) != h]
        self.assertEqual([], touched,
                         f"the operator's installed launchers were modified: {touched}")
        self.assertEqual(set(self.home_before), set(home_now),
                         "a launcher file appeared or vanished under the real $HOME")
        if self.table:
            print(f"\n  TEARDOWN: project tree unchanged "
                  f"({len(self.project_before)} files hashed); real $HOME launchers "
                  f"unchanged ({len(self.home_before)} files hashed)")

    def install(self, target):
        """Run the REAL installer with $HOME redirected. Nothing is reimplemented."""
        env = dict(os.environ, HOME=str(self.fake_home))
        p = subprocess.run(["sh", str(INSTALLER), target],
                           capture_output=True, text=True, env=env, cwd=str(INSTALLER.parent))
        self.assertEqual(0, p.returncode, p.stdout + p.stderr)
        return p

    def check_files(self, transform, files, strip_frontmatter=False):
        """Assert the block is in each produced file; return how many were checked."""
        self.assertEqual(6, len(files),
                         f"{transform}: produced {len(files)} file(s), expected 6 — a "
                         f"path that checked the wrong number of files is not a pass")
        for f in sorted(files):
            text = f.read_text()
            self.assertIn(BEGIN, text, f"{transform}: {f.name} lost the block")
            self.assertEqual(self.body, body_of(text),
                             f"{transform}: {f.name} carries a block that is not the source")
        self.table.append((transform, len(files)))
        return len(files)

    def test_claude_skills_install_delivers_the_block(self):
        self.install("claude")
        got = [self.fake_home / ".claude" / "skills" / n / "SKILL.md" for n in NAMES]
        self.check_files("claude skills install (cp)", got)
        self.report()

    def test_codex_skills_install_delivers_the_block(self):
        self.install("codex")
        got = [self.fake_home / ".agents" / "skills" / n / "SKILL.md" for n in NAMES]
        self.check_files("codex skills install (cp)", got)
        self.report()

    def test_opencode_generation_delivers_the_block_through_the_stripper(self):
        """The frontmatter-stripper path — the one most likely to lose it, because the
        generator rebuilds the frontmatter and keeps only what is below the second `---`."""
        self.install("opencode")
        got = [self.fake_home / ".config" / "opencode" / "command" / f"{n}.md" for n in NAMES]
        self.check_files("opencode generation (awk stripper)", got)
        for f in got:
            head = f.read_text().splitlines()[:3]
            self.assertEqual("---", head[0])
            self.assertTrue(head[1].startswith("description: "), head[1])
        self.report()

    def test_the_claude_plugin_bundle_ships_skills_carrying_the_block(self):
        """`.claude-plugin/plugin.json` declares no `skills` key — Claude Code discovers
        `skills/` from the plugin root by convention. So what the bundle ships is the
        repository's own six files, and that is what is asserted. Stated rather than
        implied, because a convention is a weaker claim than a declared path."""
        manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
        self.assertNotIn("skills", manifest,
                         "the manifest now declares a skills path — resolve THAT instead "
                         "of the convention this test assumes")
        got = [REPO / "skills" / n / "SKILL.md" for n in NAMES]
        self.check_files("claude plugin bundle (skills/ by convention)", got)
        self.report()

    def test_the_codex_plugin_bundle_ships_skills_carrying_the_block(self):
        """`.codex-plugin/plugin.json` declares `"skills": "./skills/"` — resolved from
        the manifest, not hard-coded, so a change to the declaration moves this check."""
        manifest = json.loads((REPO / ".codex-plugin" / "plugin.json").read_text())
        declared = manifest["skills"]
        root = (REPO / declared).resolve()
        self.assertTrue(root.is_dir(), f"declared skills path does not resolve: {declared}")
        got = sorted(root.glob("*/SKILL.md"))
        self.check_files(f"codex plugin bundle (manifest skills={declared})", got)
        self.report()

    def report(self):
        for transform, n in self.table:
            print(f"\n  DELIVERY: {transform:46} {n} file(s) checked, block present in all")


class ThePluginGateRefusesAPayloadWithoutTheBlock(unittest.TestCase):
    """Criterion 5 — both arms. The selftest exiting 0 means nothing on its own; what
    makes it mean something is a planted payload that makes it exit non-zero."""

    def test_the_selftest_passes_and_names_both_style_poisons(self):
        p = subprocess.run(["sh", str(INSTALLER), "selftest"],
                           capture_output=True, text=True, cwd=str(INSTALLER.parent))
        print("\n  PLUGIN GATE selftest:")
        for line in p.stdout.splitlines():
            print(f"    {line}")
        self.assertEqual(0, p.returncode, p.stdout + p.stderr)
        self.assertIn("poison: skill without the style block rejected (rc=1)", p.stdout)
        self.assertIn("poison: style block moved into frontmatter rejected (rc=1)", p.stdout)

    def test_a_payload_whose_skill_lost_the_block_is_refused(self):
        """The gate driven directly against a planted payload, with its control arm.

        Two plants, and they must be DIFFERENT: deleting the block and moving it into
        frontmatter are distinct defects, and the first version of this plant silently
        deleted where it meant to move — making one arm green for the other's reason.
        """
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="xcheck-gate-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for sub in ("skills", ".claude-plugin", ".codex-plugin", "launchers"):
            shutil.copytree(REPO / sub, tmp / sub)

        def gate():
            return subprocess.run(["sh", "install-launchers.sh", "plugin"],
                                  capture_output=True, text=True,
                                  cwd=str(tmp / "launchers"))

        control = gate()
        self.assertEqual(0, control.returncode,
                         f"CONTROL FAILED: the copied payload was already refused\n"
                         f"{control.stdout}{control.stderr}")

        victim = tmp / "skills" / "xcheck-plan" / "SKILL.md"
        pristine = victim.read_text()

        # Plant 1 — deleted.
        b, e = pristine.index(BEGIN), pristine.index(END) + len(END)
        victim.write_text(pristine[:b] + pristine[e:].lstrip("\n"))
        self.assertNotIn(BEGIN, victim.read_text(), "plant 1 did not delete the block")
        deleted = gate()

        # Plant 2 — MOVED into frontmatter: still in the file, above the second `---`.
        lines = pristine.splitlines(keepends=True)
        bi = next(i for i, l in enumerate(lines) if BEGIN in l)
        ei = next(i for i, l in enumerate(lines) if END in l)
        rest = lines[:bi] + lines[ei + 1:]
        close = next(i for i, l in enumerate(rest) if i and l.strip() == "---")
        victim.write_text("".join(rest[:close] + lines[bi:ei + 1] + rest[close:]))
        self.assertIn(BEGIN, victim.read_text(),
                      "plant 2 deleted the block instead of moving it — it would then be "
                      "plant 1 under another name")
        moved = gate()

        print(f"  gate arms: control rc={control.returncode}, "
              f"block-deleted rc={deleted.returncode}, "
              f"block-in-frontmatter rc={moved.returncode}")
        print(f"    {deleted.stderr.strip().splitlines()[-1][:140]}")
        self.assertEqual(1, deleted.returncode, "a skill with no style block was accepted")
        self.assertEqual(1, moved.returncode,
                         "a skill whose block sits where the stripper drops it was accepted")
        for r in (deleted, moved):
            self.assertIn("style block", r.stderr)
            self.assertIn("xcheck-plan", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
