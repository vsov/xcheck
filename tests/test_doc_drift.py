"""Phase 7's structural guard: what is documented equals what exists.

This phase's risk is not that a verb is wrong. It is that an agent keeps writing
frontmatter by hand because its prompt still says to — and that edit is now a
**no-op**, the worst failure available to a tool whose product is a record.

Two directions, both fatal, both asserted:

- a verb the CLI implements that no document mentions — agents will never use it,
  so the hand-edit stays in the prompt and the state never moves;
- a verb the documents name that the CLI does not implement — an agent follows
  its prompt, the command fails, and the session stops on a lie.

The sets are printed on every run, so a reviewer reads what was compared rather
than trusting that something was.
"""

import re
import subprocess
import unittest

from tests.harness import HAVE_INTERNAL_DOCS, REPO, xcheck_submodule

write = xcheck_submodule("write")

# A verb reference is a CODE span: `xcheck set-status …`. The leading backtick is
# load-bearing — prose like "the xcheck launcher" or "xcheck bootstrap" is English,
# not an invocation, and counting it would make this test noisy enough to ignore.
MENTION = re.compile(r"`xcheck\s+([a-z][a-z-]+)")

# Command words that are NOT write verbs. Named explicitly so a new read-only
# command does not silently look like an undocumented verb.
READ_ONLY = {"status", "next", "loop", "lint", "metrics", "unlock", "cancel",
             "selftest", "migrate", "install", "upgrade"}


def mentioned(text):
    return {m for m in MENTION.findall(text) if m in write.VERBS}


# The seven phrasings. Kept as literals rather than a pattern: this check earns its
# keep by naming the offending LINE cheaply, which the structural gate in
# `tests/test_executable_contract.py` does not do.
HAND_EDIT = ("set `status:", "set `fixed-by:", "record `fixed-by:",
             "fill `admitted-by:", "write `status:",
             "update the ledger", "edit the ledger")


def hand_edit_hits(label, text):
    """["<label>:<line>: <phrasing>"] for every line that orders a hand-edit."""
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        low = line.lower()
        for bad in HAND_EDIT:
            if bad not in low:
                continue
            # An instruction closes its code span on the line it opens it: "record
            # `fixed-by:` in its frontmatter". `state.py`'s docstring QUOTES a legacy
            # value across a line break — five findings that "record `fixed-by:\n
            # claude-remediator-…" — which is prose about migration, not an order. The
            # five phrasings that open a span must close it; the two ledger phrasings
            # have no span to close.
            if "`" in bad and "`" not in low.split(bad, 1)[1]:
                continue
            out.append(f"{label}:{i}: {bad!r}")
    return out


class DocumentedEqualsImplemented(unittest.TestCase):

    def setUp(self):
        self.implemented = set(write.VERBS)

    def _docs(self):
        out = {}
        out["XCHECK.md"] = (REPO / "XCHECK.md").read_text(encoding="utf-8")
        for d in sorted((REPO / "skills").iterdir()):
            f = d / "SKILL.md"
            if f.is_file():
                out[f"skills/{d.name}"] = f.read_text(encoding="utf-8")
        return out

    def test_every_implemented_verb_is_documented_in_xcheck_md(self):
        """XCHECK.md is normative core (N1). A verb it does not name does not exist
        as far as any agent reading the methodology is concerned."""
        documented = mentioned(self._docs()["XCHECK.md"])
        print(f"\nimplemented ({len(self.implemented)}): "
              f"{', '.join(sorted(self.implemented))}")
        print(f"documented in XCHECK.md ({len(documented)}): "
              f"{', '.join(sorted(documented))}")
        self.assertEqual(self.implemented - documented, set(),
                         "implemented but undocumented — agents will keep hand-editing")
        self.assertEqual(documented - self.implemented, set(),
                         "documented but not implemented — an agent following the "
                         "methodology would run a command that does not exist")

    def test_the_two_sets_are_equal_in_both_directions(self):
        documented = mentioned(self._docs()["XCHECK.md"])
        self.assertEqual(documented, self.implemented)

    def test_no_document_names_a_verb_that_does_not_exist(self):
        """Across XCHECK.md and all six skills. A `xcheck <word>` that is neither a
        write verb nor a declared read-only command is a typo or a promise."""
        bad = {}
        for name, text in self._docs().items():
            unknown = {m for m in MENTION.findall(text)
                       if m not in write.VERBS and m not in READ_ONLY}
            if unknown:
                bad[name] = sorted(unknown)
        self.assertEqual(bad, {}, f"documents name commands that do not exist: {bad}")

    def test_every_verb_appears_in_help(self):
        """`--help` prints from `write.VERBS`, so this cannot fail by accident — it
        fails when somebody gives `--help` its own hand-written copy of the list,
        which is exactly how the printed list starts lying."""
        help_text = write.help_text()
        for verb in self.implemented:
            self.assertIn(f"xcheck {verb}", help_text)


class SkillsCarryTheContract(unittest.TestCase):
    """Each writing role must be told the verbs ITS charter uses. A role told 'do not
    hand-edit' without being told what to do instead has been given a prohibition and no
    route.

    PHASE 4 moved the SUBJECT, not the rule. The launchers used to carry these lists
    because a launcher WAS the session; they are wrappers now and carry no role
    instructions at all, so the list a dispatched role reads is its §3 role card in
    `XCHECK.md`. Triage is the exception in both directions: it is the one gate the
    orchestrator cannot dispatch, so its wrapper still hands the human `set-status`, and
    it is checked in both places.
    """

    ROLE_CARD = {
        "Auditor": {"file-finding", "record-coverage", "queue-pass"},
        "Remediator": {"set-status", "record-fix", "record-plan"},
        "Verifier": {"record-verdict"},
        "Triage": {"set-status"},
    }

    def card_verbs(self, card_lines):
        text = "\n".join(card_lines)
        return {v for v in write.VERBS if f"`{v}`" in text}

    def test_each_role_card_names_the_verbs_that_role_uses(self):
        from tests.test_doc_contract import role_cards
        cards = role_cards((REPO / "XCHECK.md").read_text(encoding="utf-8"))
        missing = {}
        for role, verbs in self.ROLE_CARD.items():
            gap = verbs - self.card_verbs(cards[role])
            if gap:
                missing[role] = sorted(gap)
        self.assertEqual(missing, {}, f"a role card is missing its own verbs: {missing}")
        print("\n§3 role cards, verbs named:")
        for role in self.ROLE_CARD:
            print(f"  {role:<12} {sorted(self.card_verbs(cards[role]))}")

    def test_the_triage_wrapper_still_hands_over_its_own_verb(self):
        """The one launcher-borne write surface left, and the reason it is left: the
        human runs the command, so the command has to be in front of them."""
        text = (REPO / "skills" / "xcheck-triage" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("set-status", mentioned(text))

    def test_control_a_card_stripped_of_its_verbs_reddens(self):
        """Without this, "no role is missing its verbs" is what an empty parse says too."""
        stripped = ["- **Writes (record):**", "  - nothing in particular."]
        self.assertEqual({"record-verdict"},
                         {"record-verdict"} - self.card_verbs(stripped))

    def test_no_shipped_text_tells_an_agent_to_hand_edit_machine_state(self):
        """The literal instruction shapes, over every surface that reaches an agent.

        WHAT IT DOES NOT COVER, stated so nobody mistakes a green run for a proof:

        - It matches seven phrasings. Any eighth wording of the same instruction
          walks past — that is inherent, not a gap to be closed by adding words.
        - It knows nothing about state fields, so a hand-edit of a field these
          seven strings do not name is invisible to it.
        - It reads files as text. Prompts assembled at runtime are covered only
          because `runner.py` is scanned as source; a prompt built from a string
          this file never reads is out of reach.

        `tests/test_executable_contract.py` is the gate that does not have those
        limits: it derives the subject (every prompt as rendered, the orchestration
        context, the CLI, README, XCHECK.md, every skill), derives the field names
        from `state.py`'s schema, and fails an imperative that names any of them
        without naming a verb. `tests/test_doc_contract.py` is the other gate — it
        parses each role's record surface and fails anything that is not a
        `write.VERBS` name.

        This check is kept anyway as a cheap first line: it names the offending
        LINE, which the structural gate does not, and it fires on the phrasings
        that historically shipped (0.9.0's Remediator prompt was literally
        "record `fixed-by:`"). Scope was widened past `skills/` in 0.9.1 phase 5,
        when the audit found four of the five real offenders outside it.
        """
        surfaces = [(f"skills/{d.name}/SKILL.md", d / "SKILL.md")
                    for d in sorted((REPO / "skills").iterdir())]
        surfaces += [(n, REPO / n) for n in ("README.md", "XCHECK.md", "bootstrap.md")]
        surfaces += [(f"xcheck/{p.name}", p)
                     for p in sorted((REPO / "xcheck").glob("*.py"))]
        offenders = []
        for label, f in surfaces:
            if f.is_file():
                offenders += hand_edit_hits(label, f.read_text(encoding="utf-8"))
        print(f"\nliteral hand-edit scan: {len(surfaces)} surface(s)")
        # A literal check nobody has watched fire is a literal check that may match
        # nothing at all. Both directions, on text no scanned file contains:
        self.assertEqual(
            hand_edit_hits("<plant>", "Then record `fixed-by:` in its frontmatter."),
            ["<plant>:1: 'record `fixed-by:'"],
            "the matcher no longer fires on the sentence 0.9.0 actually shipped")
        self.assertEqual(hand_edit_hits("<plant>", "five findings record `fixed-by:"),
                         [], "the span rule stopped excluding a quoted legacy value")
        self.assertGreaterEqual(len(surfaces), 20,
                                "the surface list collapsed — a literal check that "
                                "reads three files is green about the rest for free")
        self.assertEqual(offenders, [],
                         "a shipped surface still instructs a hand-edit of machine "
                         "state — that edit is now a silent no-op:\n  "
                         + "\n  ".join(offenders))


class TheReferenceTablesDescribeTheCode(unittest.TestCase):
    """Phase 12: README's two reference tables, checked against the code they claim
    to document. A reference table is the one kind of prose that can be verified
    mechanically, so it is the one kind there is no excuse for drifting."""

    def setUp(self):
        self.readme = (REPO / "README.md").read_text(encoding="utf-8")

    def section(self, heading):
        start = self.readme.index(heading)
        rest = self.readme[start + len(heading):]
        nxt = min((rest.index(h) for h in ("\n## ", "\n### ") if h in rest),
                  default=len(rest))
        return rest[:nxt]

    def keys_in(self, text):
        """Every `backticked` token in the first column of a table."""
        out = set()
        for line in text.splitlines():
            if not line.startswith("| `"):
                continue
            cell = line.split("|")[1]
            out |= {t.strip(" `") for t in cell.split(",")}
        return out

    def test_the_limits_table_is_the_limit_keys(self):
        md_prose = xcheck_submodule("md_prose")
        documented = self.keys_in(self.section("## 13. Configuration reference"))
        print(f"\nREADME §13 limits: {sorted(documented)}")
        self.assertEqual(documented, set(md_prose.LIMIT_KEYS))

    def test_the_orchestrator_conf_table_is_conf_defaults(self):
        util = xcheck_submodule("util")
        documented = self.keys_in(self.section("### orchestrator.conf"))
        # The `*_cmd` rows name per-role commands, which are resolved from the role
        # rather than from CONF_DEFAULTS — they are config, but not a default.
        documented = {k for k in documented if not k.endswith("_cmd")}
        print(f"README orchestrator.conf: {len(documented)} keys, "
              f"CONF_DEFAULTS: {len(util.CONF_DEFAULTS)}")
        self.assertEqual(documented - set(util.CONF_DEFAULTS), set(),
                         "documented but not a real config key")
        self.assertEqual(set(util.CONF_DEFAULTS) - documented, set(),
                         "a config key nobody documented — an operator cannot set "
                         "what the reference does not mention")

    def test_every_path_the_layout_table_names_exists(self):
        missing = []
        for key in self.keys_in(self.section("## 4. Repository layout")):
            if not (REPO / key.rstrip("/")).exists():
                missing.append(key)
        self.assertEqual([], missing, f"README §4 names paths that do not exist: {missing}")

    # Not material, and not shipped: frozen evidence, the run's own scratch, and the
    # live audit tree. Everything else that is tracked Markdown is a document a reader
    # can follow a link out of.
    NOT_SHIPPED = ("audit/", "audit-archive/", ".supergoal/", "graphify-out/",
                   ".superpowers/")

    def shipped_markdown(self):
        p = subprocess.run(["git", "ls-files", "*.md", "**/*.md"], cwd=str(REPO),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        return [r for r in p.stdout.split()
                if not any(r.startswith(x) for x in self.NOT_SHIPPED)]

    def test_every_relative_link_resolves(self):
        """Every shipped Markdown file, not a hand-kept list of four.

        A list is a document that drifts: the file added this release is exactly the one
        nobody remembers to add to it. Enumerating from `git ls-files` means a new doc is
        covered the moment it is tracked."""
        docs = self.shipped_markdown()
        broken, checked = [], 0
        for doc in docs:
            text = (REPO / doc).read_text(encoding="utf-8")
            for target in re.findall(r"\]\(([^)#][^)]*)\)", text):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                checked += 1
                if not ((REPO / doc).parent / target.split("#")[0]).exists():
                    broken.append(f"{doc} -> {target}")
        print(f"\nDOCS WALK: {len(docs)} shipped Markdown files, "
              f"{checked} relative links resolved")
        self.assertGreater(len(docs), 4, "the walk is not finding the shipped docs")
        self.assertEqual([], broken, f"broken relative links: {broken}")

    # These prefixes name files in THIS repository. `audit/…` deliberately is not among
    # them: it is a path in the audited project, which does not exist here and must not
    # be resolved against this tree.
    OWN_TREE = ("xcheck/", "tests/", "ci/", "docs/", "skills/", "bin/", "templates/",
                ".github/")

    def test_every_path_this_repository_names_in_prose_exists(self):
        """The docs reference their own files as code spans, not as Markdown links —
        seventeen links in thirty-eight files, one of them relative. A link walk over
        this tree is therefore nearly vacuous, and vacuous is how the missing `LICENSE`
        survived: prose said it shipped, no check read the claim.

        So the walk covers `path/like/this` spans as well, restricted to prefixes this
        repository actually owns."""
        missing, checked = [], 0
        for doc in self.shipped_markdown():
            # A plan names files that do not exist yet — that is what a plan is. Two
            # of them do it today (`templates/finding-absence.md`, a proposal in
            # backlog-2 that was never built), and reading a proposal as a claim about
            # the tree would make this check unkeepable.
            if doc.startswith(("docs/backlog-", "docs/superpowers/plans/")):
                continue
            for i, line in enumerate(
                    (REPO / doc).read_text(encoding="utf-8").splitlines(), 1):
                for span in re.findall(r"`([^`\s]+)`", line):
                    span = span.rstrip(".,;:)")
                    if not span.startswith(self.OWN_TREE):
                        continue
                    if any(c in span for c in "*{}?"):
                        continue      # a glob names a set, not a file
                    # `path.py::ClassName` is a test selector: the FILE half is the
                    # claim about the tree, the rest names something inside it.
                    span = span.split("::")[0]
                    checked += 1
                    if not (REPO / span.split("#")[0]).exists():
                        missing.append((span, f"{doc}:{i}: {span}"))
        print(f"DOCS WALK: {checked} path-shaped references into this repository "
              f"across {len(self.shipped_markdown())} tracked Markdown file(s)")
        # The floor is a control on the REGEX, not a fact about any one tree: a walk
        # whose pattern stopped matching returns ~0, and only a floor catches that.
        # It used to be the flat number 100, which was a measurement of THIS repository
        # and nothing else — and the exported tree, which carries 26 of these 64
        # documents and 98 of these 230 references, failed it while walking perfectly.
        # Scaling to the documents actually found asks the same question of either tree
        # and needs no re-pinning when a release adds or drops a doc. Both trees sit
        # near 3.6 references per document, so 2x is a floor a live walk clears and a
        # dead one cannot.
        floor = 2 * len(self.shipped_markdown())
        self.assertGreater(checked, floor,
                           f"the path walk is not seeing the references: {checked} "
                           f"across {len(self.shipped_markdown())} documents")
        # A reference into a SHIPPED prefix is a claim about whatever tree is reading
        # it, and it is checked in both. A reference into `docs/` is not: `docs/` is
        # internal by policy, the export names the files it ships one by one, and the
        # CHANGELOG — history, which is not rewritten to suit a checker — cites eleven
        # audit responses that stay in the development repository. Shipping them to
        # satisfy this assertion would mean publishing 18 documents by transitive
        # closure, six of them Russian-language planning notes, one of which says in
        # its own text that it is not part of the public export.
        #
        # So the two halves are separated instead of the check being relaxed. The
        # exported tree still holds the half that IS about itself, and nothing under
        # `xcheck/`, `tests/`, `ci/`, `bin/`, `skills/`, `templates/` or `.github/` is
        # allowed to dangle anywhere. The `docs/` half stays fully armed HERE, where
        # the documents live and a rename or a deletion really is drift.
        dangling = [m for span, m in missing if not span.startswith("docs/")]
        self.assertEqual([], dangling,
                         f"documents name shipped files that are not here: {dangling}")
        into_docs = [m for span, m in missing if span.startswith("docs/")]
        if HAVE_INTERNAL_DOCS:
            self.assertEqual([], into_docs,
                             f"documents name internal docs that are not here: "
                             f"{into_docs}")
        else:
            print(f"  {len(into_docs)} reference(s) into the internal docs/ tree, which "
                  f"this checkout does not carry — checked in the development repository")


if __name__ == "__main__":
    unittest.main()
