"""Every name the user-facing documents use must exist.

The documentation rewrite turns README.md into an entry page and moves the depth into
`docs/guide/`. The failure that rewrite invites is not a wrong opinion but fluent fiction:
a conf key, a flag, a section number or an anchor that reads perfectly and does not exist.
Proofreading does not catch that; resolving each name against the code does.

Subjects: `README.md` (once `README_CHECKED` is on — the rewrite in phase 9 turns it on and
fixes what it finds) and every `docs/guide/*.md`, by glob, so an article is checked the moment
it exists. The subject list is printed.

Five checks, each with a planted counterfactual below:

1. **Anchors.** A `](#slug)` or `](file.md#slug)` link names a heading that exists, slugged the
   way GitHub slugs it. Whether the FILE exists is `test_doc_drift.test_every_relative_link_
   resolves`'s question — it strips the anchor, which is the half this check adds.
2. **Identifiers.** A backticked bare `snake_case` token, or the key of a backticked
   `key=value`, names something real: a conf key (`CONF_DEFAULTS`, `POLICY_KEYS`,
   `PROJECT_KEYS`) or a name the `xcheck` package defines or uses — collected over its AST, so
   state fields, schema keys and keyword arguments arrive without a hand-kept list. A withdrawn
   key is allowed only on a line that says it is withdrawn or refused.
3. **CLI.** A backticked `xcheck …` names a command the dispatcher accepts, and every flag in
   it is one that command takes. Commands and flags come from `cli.FLAG_COMMANDS` and
   `write.VERBS`; flags of a write verb from the literals `write.py` parses.
4. **Paths** under `launchers/`, `styles/` and `images/` exist. The other prefixes are
   `test_doc_drift`'s path-span check, which already reads every tracked Markdown file.
   `audit/…` paths name the audited project, not this repository, and are not checked.
5. **Section references.** `XCHECK.md §N` and `README §N` name a section that exists.

What this does NOT prove: that a sentence about a real key is TRUE. It proves the key exists;
whether the default the prose states is the default is `.supergoal`'s fact re-check, by a
reader. External `http(s)://` links are not fetched.
"""

import ast
import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests.harness import REPO, xcheck_submodule

cli = xcheck_submodule("cli")
policy = xcheck_submodule("policy")
util = xcheck_submodule("util")
write = xcheck_submodule("write")

# Phase 9 of the documentation rewrite turns this on, together with fixing what it finds.
README_CHECKED = True

SPAN = re.compile(r"`([^`\n]+)`")
LINK = re.compile(r"\]\(([^)\s]+)\)")
SNAKE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")
KEYVAL = re.compile(r"^([a-z][a-z0-9_]*)=")
SECTION_REF = re.compile(r"(XCHECK\.md|README(?:\.md)?)`?\s*§\s?(\d+(?:\.\d+)?)")
CHECKED_PREFIXES = ("launchers/", "styles/", "images/")
UNIVERSAL_FLAGS = {"--project", "--policy"}


def subjects():
    docs = sorted((REPO / "docs" / "guide").glob("*.md"))
    if README_CHECKED:
        docs.insert(0, REPO / "README.md")
    return docs


def prose_lines(text):
    """(line number, line) outside fenced code blocks."""
    fence = False
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if not fence:
            yield n, line


def slug_set(text):
    seen, out = {}, set()
    for _, line in prose_lines(text):
        m = re.match(r"#{1,6} (.*)", line)
        if not m:
            continue
        h = m.group(1).strip().lower()
        h = re.sub(r"[^\w\- ]", "", h).replace(" ", "-")
        k = seen.get(h, 0)
        out.add(h if k == 0 else f"{h}-{k}")
        seen[h] = k + 1
    return out


def section_numbers(text):
    nums = set()
    for _, line in prose_lines(text):
        m = re.match(r"#{2,6} (\d+(?:\.\d+)*)[. ]", line)
        if m:
            nums.add(m.group(1))
    return nums


def code_names():
    """Every identifier-shaped name the xcheck package defines, uses or spells as a
    string — the universe a backticked snake_case token in prose may come from."""
    names = set(util.CONF_DEFAULTS) | set(policy.POLICY_KEYS) | set(policy.PROJECT_KEYS)
    for p in sorted((REPO / "xcheck").glob("*.py")):
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.arg):
                names.add(node.arg)
            elif isinstance(node, ast.keyword) and node.arg:
                names.add(node.arg)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                names |= set(re.findall(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+", node.value))
    return names


def cli_tables():
    commands = set(write.VERBS) | {"selftest"}
    for cmds in cli.FLAG_COMMANDS.values():
        commands |= set(cmds)
    write_src = (REPO / "xcheck" / "write.py").read_text(encoding="utf-8")
    write_flags = set(re.findall(r'"(--[a-z][a-z-]*)"', write_src))
    cli_src = (REPO / "xcheck" / "cli.py").read_text(encoding="utf-8")
    # A flag the parser spells but `FLAG_COMMANDS` does not route (`--resolution`, the
    # argument of `resolve-stop`; `--version`; `--help`) is accepted for any read command.
    own = set(re.findall(r'"(--[a-z][a-z-]*)"', cli_src)) - set(cli.FLAG_COMMANDS)
    return commands, write_flags, own


class Checker:
    def __init__(self):
        self.names = code_names()
        self.commands, self.write_flags, self.own_flags = cli_tables()
        self.withdrawn = set(policy.WITHDRAWN_KEYS)
        self.counts = dict.fromkeys(
            ("anchors", "identifiers", "commands", "flags", "paths", "section refs",
             "audit/ paths exempt"), 0)
        self._files = {}

    def text_of(self, path):
        if path not in self._files:
            self._files[path] = path.read_text(encoding="utf-8") if path.is_file() else None
        return self._files[path]

    def check(self, path, text):
        problems = []
        here = slug_set(text)
        for n, line in prose_lines(text):
            loc = f"{path.name}:{n}"
            problems += self.anchors(loc, path, line, here)
            problems += self.sections(loc, line)
            for span in SPAN.findall(line):
                problems += self.identifier(loc, line, span)
                problems += self.command(loc, span)
                problems += self.path(loc, span)
        return problems

    def anchors(self, loc, path, line, here):
        out = []
        for target in LINK.findall(line):
            if target.startswith(("http://", "https://", "mailto:")) or "#" not in target:
                continue
            file, _, anchor = target.partition("#")
            if file:
                if not file.endswith(".md"):
                    continue
                text = self.text_of((path.parent / file).resolve())
                if text is None:
                    continue          # test_doc_drift reports the missing file
                slugs = slug_set(text)
            else:
                slugs = here
            self.counts["anchors"] += 1
            if anchor not in slugs:
                out.append(f"{loc}: anchor `#{anchor}` names no heading in "
                           f"{file or path.name}")
        return out

    def sections(self, loc, line):
        out = []
        for doc, num in SECTION_REF.findall(line):
            target = REPO / ("XCHECK.md" if doc.startswith("XCHECK") else "README.md")
            self.counts["section refs"] += 1
            if num not in section_numbers(self.text_of(target)):
                out.append(f"{loc}: `{doc} §{num}` names no section of {target.name}")
        return out

    def identifier(self, loc, line, span):
        token = span.strip()
        m = KEYVAL.match(token)
        name = m.group(1) if m else token
        if not (m or SNAKE.match(token)):
            return []
        self.counts["identifiers"] += 1
        if name in self.withdrawn:
            if re.search(r"withdrawn|refused", line, re.I):
                return []
            return [f"{loc}: `{name}` is a withdrawn key, named without saying so"]
        if name not in self.names:
            return [f"{loc}: `{token}` names nothing in xcheck/ or the conf key sets"]
        return []

    def command(self, loc, span):
        words = span.split()
        if not words or words[0] != "xcheck" or len(words) == 1:
            return []
        toks = [w.strip("[]") for w in words[1:]]
        flags = [t.split("=")[0] for t in toks if t.startswith("--")]
        verbs = [t for t in toks if re.match(r"^[a-z][a-z0-9-]*$", t)]
        if not verbs:
            # `xcheck --policy PATH …` — an ellipsis stands for "any command", and a bare
            # `--version`/`--help` needs none.
            if any(t in ("…", "...") for t in toks) or \
                    all(f in ("--version", "--help") for f in flags):
                return []
            return [f"{loc}: `{span}` names no command"]
        cmd = verbs[0]
        self.counts["commands"] += 1
        if cmd not in self.commands:
            return [f"{loc}: `xcheck {cmd}` is not a command the CLI accepts"]
        out = []
        for f in flags:
            self.counts["flags"] += 1
            if f in UNIVERSAL_FLAGS:
                continue
            if cmd in write.VERBS:
                ok = f in self.write_flags or cmd in cli.FLAG_COMMANDS.get(f, ())
            else:
                ok = cmd in cli.FLAG_COMMANDS.get(f, ()) or f in self.own_flags
            if not ok:
                out.append(f"{loc}: `{f}` is not a flag `xcheck {cmd}` takes")
        return out

    def path(self, loc, span):
        token = span.strip().split()[0].rstrip(".,;:")
        if token.startswith("audit/"):
            self.counts["audit/ paths exempt"] += 1
            return []
        if not token.startswith(CHECKED_PREFIXES):
            return []
        token = re.sub(r":\d+(-\d+)?$", "", token)
        self.counts["paths"] += 1
        if any(c in token for c in "*?"):
            ok = bool(list(REPO.glob(token)))
        else:
            ok = (REPO / token).exists()
        return [] if ok else [f"{loc}: `{token}` does not exist"]


SAMPLE = """# Sample

## Running it

Run `xcheck loop --max-sessions 3` and read `xcheck status --json`. The key
`triage_batch_cap` stops for triage; `sandbox_profile=container` asks for docker.
See [the running section](#running-it) and `XCHECK.md` §5. The installer is
`launchers/install-launchers.sh`, and your project keeps `audit/state.json`.
`diff_scope` was withdrawn and is refused by name.
"""

PLANTS = {
    "fake key=value": ("`quantum_batching=on`", "quantum_batching"),
    "fake bare key in prose": ("Set `batch_sizing` higher.", "batch_sizing"),
    "fake verb": ("Run `xcheck teleport`.", "teleport"),
    "flag on the wrong verb": ("Run `xcheck status --max-sessions 3`.", "--max-sessions"),
    "broken anchor": ("See [nowhere](#no-such-heading).", "no-such-heading"),
    "missing path": ("Run `launchers/no-such-installer.sh`.", "no-such-installer"),
    "dangling section": ("See `XCHECK.md` §42.", "§42"),
}


class EveryNameResolves(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.checker = Checker()

    def test_every_subject_resolves(self):
        docs = subjects()
        print(f"\nsubjects ({len(docs)}): {[str(p.relative_to(REPO)) for p in docs]}"
              f"{'' if README_CHECKED else '  (README.md not yet: README_CHECKED is off)'}")
        problems = []
        for p in docs:
            problems += self.checker.check(p, p.read_text(encoding="utf-8"))
        print("counts: " + ", ".join(f"{k} {v}" for k, v in self.checker.counts.items()))
        self.assertEqual([], problems, "\n".join(problems))


class EachCheckCatchesItsDefect(unittest.TestCase):
    """The planted controls. Each runs on a clean sample, not on a subject, so a defect in
    a real document cannot make a control pass or fail for the wrong reason."""

    @classmethod
    def setUpClass(cls):
        cls.checker = Checker()
        cls.path = REPO / "docs" / "guide" / "sample.md"

    def test_the_clean_sample_passes(self):
        self.assertEqual([], self.checker.check(self.path, SAMPLE))

    def test_each_planted_defect_is_caught_by_name(self):
        for label, (plant, needle) in PLANTS.items():
            with self.subTest(label):
                problems = self.checker.check(self.path, SAMPLE + plant + "\n")
                print(f"\n  planted {label:24} -> {problems}")
                self.assertTrue(problems, f"{label}: nothing reported")
                self.assertTrue(any(needle in p for p in problems),
                                f"{label}: reported, but not by name: {problems}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
