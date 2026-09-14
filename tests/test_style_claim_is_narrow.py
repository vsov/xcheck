"""Phase 5 — the documented claim is exactly as strong as the evidence behind it.

Phases 2 and 3 measured one thing: the wording block ARRIVES, byte for byte, at the far
end of every packaging path. They measured nothing about what a model then does with it,
because a model's prose style has no deterministic oracle. The failure this module exists
to prevent is a reader finishing the documentation believing the second thing was proven.

So the assertions here are mechanical, never aesthetic:

* the narrowed claim must live in ONE SENTENCE carrying both halves — a document that
  states the positive half and puts the negation three paragraphs away reads as a
  guarantee, and that is the failure mode, so it is the one that must go red;
* `xcheck/`, `ci/` and `bin/xcheck` must contain no way to resolve the operator's home
  directory at all — which is a stronger fact than "no path names `.claude`", and is
  checked as such, with `launchers/install-launchers.sh` as the one deliberate exception
  and used as the positive control that the scanner detects a real writer;
* every `styles/…` path this run's prose names must exist. `test_doc_drift.OWN_TREE`
  deliberately does not carry a `styles/` prefix (adding it would redden phase 4's
  absolute-needle check), so that coverage is here instead.

Nothing in this file judges whether prose is clear. There is no oracle for that, and a
check that pretends otherwise is the same lie in a different place.
"""

import ast
import io
import pathlib
import re
import textwrap
import tokenize
import unittest

from tests import test_doc_drift as dd

REPO = pathlib.Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- sentence mechanics

# A block is a paragraph, a list item, a heading, a table row or a quote. Splitting on
# blank lines alone would glue two adjacent bullets into one "sentence", and a claim
# assembled out of two different bullets is exactly what this module must not accept.
BLOCK_START = re.compile(r"^\s*(?:[-*+]\s|\d+\.\s|#{1,6}\s|\||>)")

# The separator swallows trailing emphasis (`you.**` ends a sentence) and the lookahead
# skips leading emphasis before the capital that opens the next one.
SENT_END = re.compile(r"(?<=[.!?])[*_`]*\s+(?=[*_`(\"]*[A-Z])")

MARKUP = re.compile(r"[*_`]")

DELIVERY = re.compile(r"\bdelivered\b", re.I)
NEGATION = re.compile(r"\b(?:does not|cannot|can't) verify\b", re.I)

# The same negation as it appears in raw Markdown: emphasis included, and tolerant of the
# line wrap that splits `**does not\nverify**` across two lines in a wrapped document.
RAW_NEG = re.compile(
    r"\*{0,2}(?:does\s+not|cannot|can't)\*{0,2}\s+\*{0,2}verify\*{0,2}")


def blocks(text):
    cur, out = [], []
    for line in text.splitlines():
        if not line.strip() or BLOCK_START.match(line):
            if cur:
                out.append(" ".join(cur))
                cur = []
        if line.strip():
            cur.append(line.strip())
    if cur:
        out.append(" ".join(cur))
    return out


def sentences(text):
    out = []
    for block in blocks(text):
        out.extend(s for s in SENT_END.split(block) if s.strip())
    return out


def clean(sentence):
    return MARKUP.sub("", sentence)


def claim_sentences(text):
    """Sentences carrying BOTH halves of the narrowed claim."""
    return [s for s in sentences(text)
            if DELIVERY.search(clean(s)) and NEGATION.search(clean(s))]


# The documents this run put the claim in. `at least one` is the criterion; naming them
# makes a silent deletion from any of them a failure rather than a shrug.
CLAIM_HOMES = ("README.md", "SECURITY.md", "CHANGELOG.md", "styles/README.md")


class TheNarrowedClaimIsOneSentence(unittest.TestCase):

    def read(self, name):
        return (REPO / name).read_text(encoding="utf-8")

    def test_each_document_states_both_halves_in_a_single_sentence(self):
        found = {}
        for doc in CLAIM_HOMES:
            hits = claim_sentences(self.read(doc))
            found[doc] = hits
        print("\nNARROWED CLAIM")
        for doc, hits in found.items():
            print(f"  {doc:18s} {len(hits)} sentence(s)")
            for s in hits:
                print(f"    {clean(s)[:150]}")
        empty = [d for d, h in found.items() if not h]
        self.assertEqual([], empty,
                         f"documents state the claim without its limit in the same "
                         f"sentence: {empty}")

    def test_dropping_the_negation_half_turns_the_check_red(self):
        """Plant A: the positive half survives, the negation is gone from the document."""
        for doc in CLAIM_HOMES:
            with self.subTest(doc=doc):
                text = self.read(doc)
                self.assertTrue(claim_sentences(text), "CONTROL: no claim to plant on")
                planted = RAW_NEG.sub("acts", text, count=1)
                self.assertNotEqual(text, planted, "the plant changed nothing")
                self.assertEqual([], claim_sentences(planted),
                                 "a document making the promise without its limit "
                                 "passed the check")
                # Plant A's distinguishing property: the phrase is GONE.
                self.assertEqual(0, len(RAW_NEG.findall(planted)),
                                 "plant A was supposed to remove the negation")

    def test_the_two_halves_in_different_sentences_turn_the_check_red(self):
        """Plant B: both halves survive, in two sentences. This is the failure mode a
        naive document-wide substring search would call green, so it is the one that
        decides whether this check is worth anything."""
        for doc in CLAIM_HOMES:
            with self.subTest(doc=doc):
                text = self.read(doc)
                before = len(RAW_NEG.findall(text))
                self.assertTrue(claim_sentences(text), "CONTROL: no claim to plant on")
                planted = RAW_NEG.sub(
                    lambda m: "at some point. This repository " + m.group(0),
                    text, count=1)
                self.assertNotEqual(text, planted, "the plant changed nothing")
                self.assertEqual([], claim_sentences(planted),
                                 "both halves split across two sentences passed the "
                                 "check — the check is a substring search, not a "
                                 "sentence check")
                # Plant B's distinguishing property: the phrase is STILL THERE, and both
                # words still occur in the document. Only the sentence boundary moved.
                self.assertEqual(before, len(RAW_NEG.findall(planted)),
                                 "plant B was supposed to keep the negation")
                self.assertTrue(DELIVERY.search(clean(planted)),
                                "plant B was supposed to keep the delivery word")

    def test_the_two_plants_are_not_the_same_plant(self):
        """Two counterfactual arms that produce the same text cover one defect, not two."""
        text = self.read("SECURITY.md")
        a = RAW_NEG.sub("acts", text, count=1)
        b = RAW_NEG.sub(lambda m: "at some point. This repository " + m.group(0),
                        text, count=1)
        self.assertNotEqual(a, b, "the two plants produced identical documents")
        self.assertEqual(0, len(RAW_NEG.findall(a)))
        self.assertEqual(len(RAW_NEG.findall(text)), len(RAW_NEG.findall(b)))
        print(f"PLANTS DIFFER: A drops the negation "
              f"({len(RAW_NEG.findall(text))} -> 0), B keeps it and moves the sentence "
              f"boundary ({len(RAW_NEG.findall(b))})")

    def test_the_splitter_splits_where_it_claims_to(self):
        """If `sentences()` returned the whole document as one string, plant B above
        would stay green for the wrong reason. It cannot, and here is why."""
        self.assertEqual(2, len(sentences("A one. B two.")))
        # A dot inside a path is not a sentence end.
        self.assertEqual(1, len(sentences("See `ci/render-style.py` for the rest.")))
        # Two adjacent bullets are two blocks, never one sentence.
        self.assertEqual(2, len(sentences("- alpha is delivered\n- beta does not verify")))
        self.assertEqual([], claim_sentences("- alpha is delivered\n"
                                             "- beta does not verify"))
        # And the positive control for the matcher itself.
        self.assertEqual(1, len(claim_sentences(
            "The block is delivered, and this does not verify that a model obeyed.")))


# ------------------------------------------------- nothing writes into the operator's config

SCANNED = tuple(sorted(str(p.relative_to(REPO)) for p in
                       list((REPO / "xcheck").rglob("*.py"))
                       + list((REPO / "ci").rglob("*.py")))) + ("bin/xcheck",)

# Every way a Python process can learn where the operator's home directory is. A path
# under `~/.claude` cannot be written without one of these, so zero hits is a stronger
# statement than "no string in this tree spells `.claude`".
HOME_RESOLUTION = re.compile(
    r"expanduser|expandvars|Path\.home\(\)|os\.path\.home|"
    r"environ\s*\[\s*[\"']HOME[\"']\s*\]|environ\.get\(\s*[\"']HOME[\"']|"
    r"\$HOME|\$\{HOME")

CONFIG_DIR = re.compile(r"\.claude\b|\.codex\b|\.agents\b|output-styles")

# The one deliberate exception. It is a shell script an operator runs by hand; it is not
# imported by anything, and it is not in SCANNED.
EXCEPTION = "launchers/install-launchers.sh"

# The one declared reader of those directory names inside SCANNED: a parity check that
# points the child at a temporary HOME. Declared by function, not by line number.
DECLARED_FN = ("xcheck/selftest.py", "_launcher_parity_problems")


def prose_lines(src):
    """Lines occupied by comments and by bare string expressions (docstrings).

    A path named in prose writes nothing. A path in a string literal that some call
    consumes might, so those stay in scope — the exemption is positional, and
    `test_the_exemption_is_positional` proves it by planting the same needle twice.
    """
    out = set()
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            out.add(tok.start[0])
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            out.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return out


def scan_source(src, pattern):
    """(line, text) for every code line matching `pattern`. Prose is not code."""
    skip = prose_lines(src)
    return [(i, line.strip())
            for i, line in enumerate(src.splitlines(), 1)
            if i not in skip and pattern.search(line)]


def function_span(src, name):
    lines = src.splitlines()
    start = next(i for i, l in enumerate(lines, 1)
                 if l.strip().startswith(f"def {name}("))
    indent = len(lines[start - 1]) - len(lines[start - 1].lstrip())
    end = start
    while end < len(lines):
        nxt = lines[end]
        if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
            break
        end += 1
    return start, end


class XcheckWritesNothingIntoTheOperatorsConfig(unittest.TestCase):

    def src(self, rel):
        return (REPO / rel).read_text(encoding="utf-8")

    def test_no_shipped_code_can_resolve_the_operators_home_directory(self):
        hits = []
        for rel in SCANNED:
            for line, text in scan_source(self.src(rel), HOME_RESOLUTION):
                hits.append(f"{rel}:{line}: {text[:90]}")
        print(f"\nHOME RESOLUTION: {len(SCANNED)} shipped Python files scanned, "
              f"{len(hits)} code sites")
        self.assertEqual([], hits,
                         f"shipped code can find the operator's home directory: {hits}")

    def test_the_only_config_dir_names_are_the_declared_parity_check(self):
        rel_declared, fn = DECLARED_FN
        lo, hi = function_span(self.src(rel_declared), fn)
        undeclared, declared = [], 0
        for rel in SCANNED:
            for line, text in scan_source(self.src(rel), CONFIG_DIR):
                if rel == rel_declared and lo <= line <= hi:
                    declared += 1
                    continue
                undeclared.append(f"{rel}:{line}: {text[:90]}")
        print(f"CONFIG DIRS: {declared} declared inside "
              f"{rel_declared}::{fn} (lines {lo}-{hi}), {len(undeclared)} undeclared")
        self.assertEqual([], undeclared,
                         f"shipped code names the operator's config directory: "
                         f"{undeclared}")
        self.assertEqual(5, declared,
                         "the declared parity check changed shape — re-read it before "
                         "re-declaring the count")

    def test_the_declared_parity_check_points_at_a_temporary_home(self):
        rel, fn = DECLARED_FN
        src = self.src(rel)
        lo, hi = function_span(src, fn)
        # Dedented: the parity check is a nested function, and `ast.parse` — which
        # `prose_lines` needs to tell a docstring from a code string — will not read
        # an indented block.
        body = textwrap.dedent("\n".join(src.splitlines()[lo - 1:hi]))
        for needle in ("tempfile.TemporaryDirectory()", "home = Path(td)",
                       'env["HOME"] = str(home)'):
            self.assertIn(needle, body,
                          f"{fn} no longer redirects HOME — the five declared "
                          f"`.claude`/`.codex`/`.agents` lines are no longer safe")
        for line, text in scan_source(body, CONFIG_DIR):
            self.assertIn("home /", text,
                          f"a config-dir path in {fn} is not rooted at the temporary "
                          f"home: {text}")

    def test_the_exemption_is_positional_not_a_blanket_ignore(self):
        """The same needle, twice: once as prose, once as code. Opposite verdicts."""
        needle = 'x = os.path.expanduser("~/.claude")'
        as_prose = f'"""A docstring that mentions {needle} in passing."""\n'
        as_code = f"import os\n{needle}\n"
        self.assertEqual([], scan_source(as_prose, HOME_RESOLUTION),
                         "prose was scanned as code")
        self.assertEqual(1, len(scan_source(as_code, HOME_RESOLUTION)),
                         "code was exempted as prose")
        # And a real one: runner.py's docstring names `$HOME` and is correctly ignored,
        # while the unfiltered text still contains it. Without this arm the exemption
        # could be doing nothing at all in this repository.
        runner = self.src("xcheck/runner.py")
        self.assertTrue(HOME_RESOLUTION.search(runner),
                        "CONTROL: runner.py no longer mentions $HOME anywhere, so this "
                        "arm proves nothing — find another prose site or drop it")
        self.assertEqual([], scan_source(runner, HOME_RESOLUTION))

    def test_a_planted_writer_is_caught_and_the_two_plants_differ(self):
        base = self.src("xcheck/write.py")
        self.assertEqual([], scan_source(base, HOME_RESOLUTION), "CONTROL: base is clean")
        self.assertEqual([], scan_source(base, CONFIG_DIR), "CONTROL: base is clean")

        # Plant 1 names the config directory outright.
        p1 = base + '\n(Path.home() / ".claude" / "skills").mkdir(parents=True)\n'
        # Plant 2 resolves HOME and never says `.claude` — the reason the home check is
        # the primary one and the directory-name check is the secondary one.
        p2 = base + '\ntarget = os.path.expanduser("~/.config/xcheck/state")\n'

        self.assertEqual(1, len(scan_source(p1, HOME_RESOLUTION)))
        self.assertEqual(1, len(scan_source(p1, CONFIG_DIR)))
        self.assertEqual(1, len(scan_source(p2, HOME_RESOLUTION)))
        self.assertEqual([], scan_source(p2, CONFIG_DIR),
                         "plant 2 was supposed to be invisible to the directory-name "
                         "check — if it is not, the two plants test one thing twice")
        print("PLANTED WRITERS: p1 home=1 cfg=1 · p2 home=1 cfg=0 (differ on cfg)")

    def test_the_deliberate_exception_is_outside_the_scan_and_would_have_been_caught(self):
        """The positive control, on real repository bytes rather than a plant: the one
        program that DOES write under `$HOME` is detected by these regexes. A scanner
        that finds nothing anywhere is indistinguishable from a scanner that is broken."""
        self.assertNotIn(EXCEPTION, SCANNED)
        self.assertFalse(EXCEPTION.startswith(("xcheck/", "ci/", "bin/")))
        installer = self.src(EXCEPTION)
        home_hits = [l for l in installer.splitlines() if HOME_RESOLUTION.search(l)]
        cfg_hits = [l for l in installer.splitlines() if CONFIG_DIR.search(l)]
        self.assertTrue(home_hits, "the installer no longer resolves $HOME")
        self.assertTrue(cfg_hits, "the installer no longer names the config directories")
        print(f"EXCEPTION {EXCEPTION}: {len(home_hits)} $HOME sites, "
              f"{len(cfg_hits)} config-dir sites — outside the scanned trees, run by hand")
        # And it is named as the exception where a reader will look for it.
        readme = (REPO / "styles" / "README.md").read_text(encoding="utf-8")
        self.assertIn(EXCEPTION, readme,
                      "the one exception is not named in styles/README.md")


# --------------------------------------------------- styles/README.md and the carve-outs

CARVE_OUTS = ("Quoted evidence", "Finding ids", "§5 statuses", "Copy-paste commands")
ITEM_RE = re.compile(r"^(\d+)\.\s+\*\*(.+?)\*\*\s*—\s*(.*)$")


def numbered_items(text, heading):
    """{label: body} for the numbered list under `heading`."""
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip() == heading)
    items, label = {}, None
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        m = ITEM_RE.match(line)
        if m:
            label = m.group(2)
            items[label] = m.group(3)
        elif label and line.strip() and line.startswith("   "):
            items[label] += " " + line.strip()
        elif not line.strip():
            label = None
    return items


class TheStyleReadmeSaysWhatItMustSay(unittest.TestCase):

    def setUp(self):
        self.text = (REPO / "styles" / "README.md").read_text(encoding="utf-8")

    def test_the_standalone_install_is_marked_optional_in_those_words(self):
        self.assertIn("This is optional.", self.text)
        self.assertIn("cp styles/eli5.md ~/.claude/output-styles/", self.text)
        # The claim that matters beside it: the skills do not depend on that copy.
        self.assertRegex(self.text, r"Nothing in xcheck requires it")

    def test_all_four_carve_outs_carry_an_explanation(self):
        heading = "## The four exceptions, and why each one is an exception"
        items = numbered_items(self.text, heading)
        print(f"\nCARVE-OUTS: {len(items)} numbered items under {heading!r}")
        self.assertEqual(list(CARVE_OUTS), list(items),
                         f"the four carve-outs are not the four documented: {list(items)}")
        for label, body in items.items():
            print(f"  {label:20s} {len(body):3d} chars of explanation")
            # Mechanical, and deliberately so: this asserts an explanation is ATTACHED,
            # not that it is a good one. No oracle judges prose, and one that pretended
            # to would be the exact failure this phase exists to prevent.
            self.assertGreaterEqual(
                len(body), 100,
                f"carve-out {label!r} is a label with no reason behind it")

    def thin_out(self, victim):
        """Rewrite one numbered item down to its label. Line-wise, because the parsed
        body has its newlines collapsed and so appears nowhere in the raw file."""
        out, dropping = [], False
        for line in self.text.splitlines():
            m = ITEM_RE.match(line)
            if m and m.group(2) == victim:
                out.append(f"{m.group(1)}. **{victim}** — Obviously.")
                dropping = True
                continue
            if dropping:
                if line.startswith("   ") and line.strip():
                    continue          # a wrapped continuation of the item being thinned
                dropping = False
            out.append(line)
        return "\n".join(out) + "\n"

    def test_a_carve_out_reduced_to_its_label_turns_the_check_red(self):
        heading = "## The four exceptions, and why each one is an exception"
        items = numbered_items(self.text, heading)
        victim = "Finding ids"
        stripped = self.thin_out(victim)
        self.assertNotEqual(self.text, stripped, "the plant changed nothing")
        planted = numbered_items(stripped, heading)
        self.assertGreaterEqual(len(items[victim]), 100, "CONTROL: it was already thin")
        self.assertLess(len(planted[victim]), 100,
                        "the plant did not actually shorten the explanation")
        self.assertEqual(4, len(planted),
                         "this plant thins an exception; it must not delete one")

    def test_a_deleted_carve_out_turns_the_check_red(self):
        """Different plant, different defect: not a thin reason, a missing exception."""
        heading = "## The four exceptions, and why each one is an exception"
        victim = "§5 statuses"
        self.assertIn(victim, numbered_items(self.text, heading),
                      "CONTROL: the exception this plant removes was not there to remove")
        stripped = self.text.replace(f"**{victim}**", "**Something else**")
        planted = numbered_items(stripped, heading)
        self.assertNotIn(victim, planted)
        self.assertEqual(4, len(planted),
                         "this plant renames an exception; it must not also delete one, "
                         "or it is the previous plant wearing a hat")

    def test_the_style_readme_makes_no_containment_claim(self):
        """Which is why it needs no mode scope. Measured, not asserted by design intent."""
        from tests import test_launch_surfaces as ls
        claims = ls.scan_claims(self.text)
        print(f"styles/README.md: {len(claims)} containment claims")
        self.assertEqual([], claims,
                         f"styles/README.md makes containment claims and is not in "
                         f"SHIPPED_DOCS, so nothing scopes them: {claims}")
        # Control: the scanner is live and would have found one.
        self.assertTrue(ls.scan_claims(self.text + "\n\nThe clone is read-only.\n"),
                        "CONTROL: the claim scanner found nothing in planted text")


# ------------------------------------------------------- every `styles/…` path exists

SPAN_RE = re.compile(r"`([^`\s]+)`")
DOCS = ("README.md", "SECURITY.md", "CHANGELOG.md", "XCHECK.md", "bootstrap.md",
        "styles/README.md") + tuple(
            f"skills/{p.name}/SKILL.md" for p in sorted((REPO / "skills").iterdir())
            if p.is_dir())


def style_paths(name, text):
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        for span in SPAN_RE.findall(line):
            span = span.rstrip(".,;:)").split("::")[0].split("#")[0]
            if span.startswith("styles/") and not any(c in span for c in "*{}?"):
                out.append((f"{name}:{i}", span))
    return out


class EveryStylePathTheProseNamesExists(unittest.TestCase):

    def test_the_gap_this_covers_is_real(self):
        """`test_doc_drift.OWN_TREE` deliberately does not carry a `styles/` prefix:
        adding one would redden phase 4's absolute-needle check, which is this run's
        strongest invariant. So that walk skips these paths and this one does not."""
        own = dd.TheReferenceTablesDescribeTheCode.OWN_TREE
        self.assertNotIn("styles/", own,
                         "`styles/` reached OWN_TREE — re-check phase 4's absolute "
                         "needle count before deciding this module is redundant")
        print(f"\nOWN_TREE = {own}\n  (no `styles/` prefix — that is this module's job)")

    def test_every_style_path_named_in_prose_exists(self):
        named, missing = [], []
        for doc in DOCS:
            for where, span in style_paths(doc, (REPO / doc).read_text(encoding="utf-8")):
                named.append((where, span))
                if not (REPO / span).exists():
                    missing.append(f"{where}: {span}")
        print(f"STYLE PATHS: {len(named)} references across {len(DOCS)} documents")
        self.assertGreaterEqual(len(named), 8,
                                "the walk is not seeing the references it should")
        self.assertEqual([], missing, f"prose names files that are not here: {missing}")

    def test_a_named_file_that_does_not_exist_turns_the_check_red(self):
        text = "The generator is `styles/eli5.md` and `styles/no-such-file.md`.\n"
        found = style_paths("plant", text)
        self.assertEqual(2, len(found), "the walk did not see both spans")
        missing = [s for _, s in found if not (REPO / s).exists()]
        self.assertEqual(["styles/no-such-file.md"], missing)


# --------------------------------------------------------------- the CHANGELOG bullet

# The release the STYLE delivery folded into. Pinned at 0.9.1 permanently: that is where
# the `styles/eli5.md` bullet was written and where it stays, and a bullet that drifted
# into another release section would be this file's business.
#
# It is deliberately NOT the package version any more. Phase 21 of the third-audit
# response minted 0.9.2 — 21 phases and 8 breaking changes, which is a release by any
# reading — so `SECTION` and `xcheck.__version__` legitimately differ now. The gate that
# used to conflate them is split below, because what this file was ever entitled to check
# is that the STYLE run minted nothing, not that no run ever will.
VERSION = "0.9.1"
SECTION = f"## {VERSION} — unreleased · schema 1"


class TheChangelogFoldsIntoTheExistingSection(unittest.TestCase):

    def setUp(self):
        self.text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
        lines = self.text.splitlines()
        start = lines.index(SECTION)
        end = next(i for i, l in enumerate(lines[start + 1:], start + 1)
                   if l.startswith("## "))
        self.section = "\n".join(lines[start:end])

    def test_the_style_delivery_minted_no_version_of_its_own(self):
        """What this gate was always for.

        It used to assert `headings[0] == SECTION`, which was the same sentence while the
        style run was the newest thing in the file. It is not any more, and reading it as
        "no run may ever mint a version" would make it fail on every future release —
        which is a gate that reddens for doing the work, not for skipping it.

        So the claim is stated directly instead: the 0.9.1 section still exists, still
        carries the style bullet, and the style delivery added no `##` heading of its own.
        Whether the NEWEST section matches the package is `tests/test_version_parity.py`'s
        job and is checked there over all seven surfaces.
        """
        headings = [l for l in self.text.splitlines() if l.startswith("## ")]
        self.assertIn(SECTION, headings,
                      f"the {VERSION} section the style delivery folded into is gone")
        style_headings = [h for h in headings
                          if "style" in h.lower() or "eli5" in h.lower()]
        self.assertEqual([], style_headings,
                         f"the style delivery minted a release section of its own: "
                         f"{style_headings}")
        import xcheck
        print(f"\nCHANGELOG: newest section {headings[0]!r}; the style delivery folded "
              f"into {SECTION!r} and minted none of its own; "
              f"xcheck.__version__ = {xcheck.__version__}")

    def test_the_style_bullet_is_inside_that_section(self):
        self.assertIn("styles/eli5.md", self.section,
                      "the style bullet is not in the 0.9.1 section")
        self.assertIn("styles/eli5.md", self.text)
        # It is in exactly one release section, not repeated into an older one.
        older = self.text.split(SECTION, 1)[0] + self.text.split(self.section, 1)[1]
        self.assertNotIn("styles/eli5.md", older,
                         "the style bullet leaked into another release section")

    def test_the_bullet_states_its_scope_and_both_exclusions(self):
        bullet = next(b for b in self.section.split("\n- ") if "styles/eli5.md" in b)
        for needle, what in (("six\n  launcher skills", "the scope"),
                             ("command-line text", "the CLI-text exclusion"),
                             ("xcheck/runner.py", "the orchestrated-children exclusion")):
            self.assertIn(needle, bullet,
                          f"the CHANGELOG bullet does not name {what}")
        print(f"CHANGELOG BULLET: {len(bullet)} chars, scope + both exclusions named")


if __name__ == "__main__":
    unittest.main()
