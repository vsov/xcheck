"""0.9.1 phase 5: one executable contract, checked over the text agents actually get.

The third audit's P0 #4. `XCHECK.md` was fixed in 0.9.0; the **executable** prompts were
not. The Remediator prompt handed to the child process still told the agent to write
`fixed-by:` into a finding's frontmatter — a view rendered from state and never read back
— and the orchestration context repeated it. The CLI told an operator to hand-set
`norm-ruling`, `status`, `admitted-by` and `admitted-at`, all of which have verbs. README
described the Auditor adding ledger rows and ticking a checkbox. The Status skill called
`state.json` canonical in one paragraph and the finding file canonical in the next.

Every one of those passages mentions verbs somewhere, so `test_doc_drift`'s verb-name
comparison was green throughout. This file checks the *instructions*.

What it is, precisely — because a check that overstates its reach is worse than a narrow
one that admits it:

- The SUBJECT is derived, not listed: `runner.PROMPTS` rendered for every role, the real
  `runner.orchestration_context()` block, the whole of `cli.py` — its operator-facing
  messages and the code around them, scanned as one text, because the message that
  mattered lives in a gate branch that fires on one state shape and a test that has to
  reach that state to see the string will miss the ninth gate — `README.md`, `XCHECK.md`,
  and all six skills.
- The FIELD NAMES are derived from `state.py`'s schema dicts at runtime, in both their
  JSON (`fixed_by`) and rendered (`fixed-by:`) spellings. Add a field to the schema and
  this check covers it with no edit here.
- The TRIGGER is lexical and deliberately narrow: a closed set of English write verbs
  (set, write, record, add, edit, …), in clause-opening position — the imperative mood —
  within 60 characters before the field it names, and outside inline code spans. Each of
  those four narrowings drops a class of prose the check has no business flagging: a
  layering diagram containing the module name `write`, a plural noun ("records admitted
  under it"), a past participle ("a finding set `fixed` must carry …"), a config table.
  This is the honest limit: a sentence that instructs a hand-edit in some eighth phrasing
  walks past, and no word list fixes that. What makes the check worth having anyway is
  that the SUBJECT and the FIELDS are exhaustive, so the escape is an unusual verb — not
  an unusual field, and never an unscanned file.
- The ESCAPE is naming a real verb: a sentence that names one is executable by
  definition, which is the whole contract.
- The counterfactual (`APlantedInstructionIsCaught`) is what makes any of it mean
  anything. A structural check that has never been shown to fail is a check whose silence
  proves nothing.
"""

import re
import unittest

from tests.harness import (Fixture, REPO, construal_record, finding_record, queue_pass,
                           state_doc, xcheck_submodule)

runner = xcheck_submodule("runner")
write = xcheck_submodule("write")
state = xcheck_submodule("state")
cli = xcheck_submodule("cli")

SESSION_A, SESSION_B = "a" * 16, "b" * 16

# Which tree the SURFACES are read from. `REPO` always, except while the counter-test
# points it at a temp copy carrying a planted instruction. The VOCABULARY (fields,
# commands) is never redirected: the counter-test plants a bad instruction, not a fake
# schema, and a check graded against a planted vocabulary would grade itself.
ROOT = REPO

# ---------------------------------------------------------------------------
# the subject: every surface that reaches an agent or an operator
# ---------------------------------------------------------------------------

SURFACE_FILES = ("README.md", "XCHECK.md")


def subject():
    """{name: text} — every surface an instruction can hide in."""
    parts = {}
    for role, tmpl in runner.PROMPTS.items():
        parts[f"PROMPTS[{role}]"] = tmpl.replace("{charter}", "<charter>").replace(
            "{role}", role)
    parts["orchestration_context"] = runner.orchestration_context(
        [f"XCHECK_SESSION_ID={SESSION_A}"], SESSION_A)
    parts["cli.py"] = (ROOT / "xcheck" / "cli.py").read_text(encoding="utf-8")
    for name in SURFACE_FILES:
        parts[name] = (ROOT / name).read_text(encoding="utf-8")
    for d in sorted((ROOT / "skills").iterdir()):
        f = d / "SKILL.md"
        if f.is_file():
            parts[f"skills/{d.name}"] = f.read_text(encoding="utf-8")
    return parts


# ---------------------------------------------------------------------------
# the vocabulary, all of it derived
# ---------------------------------------------------------------------------


def state_field_names():
    """Every machine-state field name, in both spellings an agent might be told to set.

    `state.py` holds each record kind as `(required, optional)`. Reading them here means
    a field added to the schema is covered by this check the moment it exists.
    """
    names = set()
    for attr in ("FINDING_FIELDS", "CLASS_FINDING_FIELDS", "COVERAGE_FIELDS",
                 "QUEUE_FIELDS", "PLAN_FIELDS", "CONSTRUAL_FIELDS"):
        req, opt = getattr(state, attr)
        names |= set(req) | set(opt)
    # Words that are unavoidable English in prose about an audit. Excluded by NAME and
    # with a reason each, never by a blanket length or frequency rule.
    prose = {
        "id", "key", "title", "role", "status", "unit", "pass", "created", "updated",
        "charter", "findings", "members", "session", "attempts", "severity",
        "dimension", "stop", "done", "next", "class", "report_path", "body_path",
    }
    keep = names - prose
    # Both spellings: the JSON key and the rendered frontmatter field.
    return {n for n in keep} | {n.replace("_", "-") for n in keep}


def known_commands():
    """Every `xcheck <word>` that exists — write verbs plus the read commands, taken
    from the dispatcher's own no-command message so a new command needs no edit here."""
    src = (REPO / "xcheck" / "cli.py").read_text(encoding="utf-8")
    listed = re.search(r'no command \(([a-z|\-\s"]+)\)', src.replace('"\n', "").replace(
        '                         "', ""))
    reads = set(listed.group(1).split("|")) if listed else set()
    return set(write.VERBS) | {r.strip() for r in reads if r.strip()} | {
        "selftest", "help", "--help", "--version"}


WRITE_WORDS = ("set", "sets", "write", "writes", "record", "records", "add", "adds",
               "edit", "edits", "update", "updates", "fill", "fills", "put", "puts",
               "tick", "ticks", "mark", "marks", "append", "appends", "insert",
               "inserts", "change", "changes")
# The imperative narrowing. A write word only counts where a clause can start: at the
# beginning, after punctuation, or after one of the words that introduce an instruction.
# "a finding set `fixed`" is a past participle and does not qualify; "then set `fixed`"
# does. This is what separates DESCRIBING the state model from ORDERING an edit to it.
CLAUSE_OPENER = re.compile(
    r"(^|[.,;:—–()\[\]|]|\b(?:and|then|to|must|should|shall|please|now|you|also|never|"
    r"not|don't|do|either|or|first|next|finally)\s+)\s*$", re.I)
# How near the write word has to be to the field it supposedly writes. Far enough apart
# and the two belong to different clauses — the sentence is prose that happens to contain
# both, not an instruction about one.
REACH = 60
# A sentence that FORBIDS the hand-edit names the field and a write word too. These are
# the markers that say so; hits carrying one are reported as negated, never dropped
# silently — a skipped line nobody prints is a hole nobody can see.
NEGATIONS = ("never", "not ", "n't", "no longer", "rather than", "instead of",
             "cannot", "refus", "forbidden", "do not", "without", "is a view",
             "are views", "rendered", "generated", "mirror", "no-op", "silently",
             "used to", "walks around", "MUTATED")

SENTENCE = re.compile(r"[^.!?\n]+[.!?\n]")
CODE_SPAN = re.compile(r"`[^`]*`")


def mask_code(s):
    """Blank the inside of every inline code span, keeping every offset.

    Without this, the layering line `util → … → write → cli` reads as an order to write
    something, and every verb name containing `record` or `set` is its own write word.
    Offsets are preserved so a match in the masked text still locates in the original.
    """
    return CODE_SPAN.sub(lambda m: " " * len(m.group(0)), s)


def field_hits(s, fields):
    """Start offsets of word-bounded state-field mentions: `field`, `field:`, or field:.

    Word-bounded on purpose: a `blocked` prefix test matches `blocked-dependency`, which
    is a refusal-reason token, not the `blocked` field.
    """
    out = []
    for f in fields:
        e = re.escape(f)
        out += [m.start() for m in re.finditer(rf"`{e}`|`{e}:|(?<![\w-]){e}:(?!\w)", s)]
    return sorted(out)


def imperatives_naming_state(text, fields, verbs=None):
    """(offenders, negated) — sentences that ORDER someone to write a state field.

    A sentence qualifies when a write word in clause-opening position sits within REACH
    characters before a state field it does not route. It is an OFFENDER unless the
    sentence also names a real verb: naming the verb is what makes an instruction
    executable, which is the whole contract.
    """
    verbs = verbs or (set(write.VERBS) | {"xcheck"})
    offenders, negated = [], []
    for raw in SENTENCE.findall(text):
        s = " ".join(raw.split())
        hits = field_hits(s, fields)
        if not hits:
            continue
        masked = mask_code(s)
        ordered = False
        for w in WRITE_WORDS:
            for m in re.finditer(rf"\b{w}\b", masked, re.I):
                if not CLAUSE_OPENER.search(masked[:m.start()][-24:]):
                    continue
                if any(m.end() <= h <= m.end() + REACH for h in hits):
                    ordered = True
        if not ordered:
            continue
        if any(re.search(rf"(?<![\w-]){re.escape(v)}(?![\w-])", s) for v in verbs):
            continue
        low = s.lower()
        (negated if any(n in low for n in NEGATIONS) else offenders).append(s[:200])
    return offenders, negated


def unknown_commands(text, known):
    """Every `` `xcheck <word>` `` in the text that is not a command.

    The backtick is required: these documents mark every command as code, and without it
    the pattern reads ordinary prose ("xcheck cannot", "xcheck owns") as verb routing.
    An instruction routing to a verb that does not exist is the same defect as one
    routing to a file — the agent's command fails, or worse, silently does nothing.
    """
    out = [m.group(1) for m in re.finditer(r"[`/]xcheck ([a-z][a-z-]{2,})", text)
           if m.group(1) not in known]
    return sorted(set(out))


# ---------------------------------------------------------------------------


class TheSubjectIsEverySurface(unittest.TestCase):
    """Criterion 2. A contract test that scans three files is green about the other
    twelve for free — the standing lesson of this run, applied to a document check."""

    def test_the_subject_covers_the_six_sources_and_is_not_empty(self):
        parts = subject()
        total = sum(len(v) for v in parts.values())
        print(f"\ncontract subject: {len(parts)} surface(s), {total} chars")
        for name, text in sorted(parts.items()):
            print(f"  {len(text):>7} {name}")
        for expect in ("PROMPTS[Remediator]", "orchestration_context", "cli.py",
                       "README.md", "XCHECK.md", "skills/xcheck-status"):
            self.assertIn(expect, parts, f"{expect} is not in the subject")
        self.assertGreaterEqual(len(parts), 11,
                                "five prompts + the context + three documents + six "
                                "skills is the floor; a shrunken subject means the "
                                "collector lost a source")
        self.assertGreaterEqual(total, 100_000,
                                "the subject is far smaller than the documents it "
                                "claims to hold — something read empty")

    def test_the_derived_vocabulary_is_not_empty(self):
        fields, cmds = state_field_names(), known_commands()
        print(f"\nstate fields watched ({len(fields)}): {', '.join(sorted(fields))}")
        print(f"known commands ({len(cmds)}): {', '.join(sorted(cmds))}")
        self.assertGreaterEqual(len(fields), 10)
        self.assertIn("fixed-by", fields)
        self.assertIn("norm-ruling", fields)
        self.assertIn("admitted-by", fields)
        self.assertGreaterEqual(len(cmds), len(write.VERBS) + 5,
                                "the read commands did not parse out of the dispatcher")
        self.assertIn("record-fix", cmds)
        self.assertIn("status", cmds)


class NoSurfaceInstructsAHandEdit(unittest.TestCase):
    """Criterion 1."""

    def test_no_imperative_names_a_state_field_without_naming_a_verb(self):
        fields = state_field_names()
        all_offenders, all_negated = {}, 0
        for name, text in sorted(subject().items()):
            off, neg = imperatives_naming_state(text, fields)
            all_negated += len(neg)
            if off:
                all_offenders[name] = off
            if neg:
                print(f"\n{name}: {len(neg)} negated/descriptive mention(s), e.g.")
                print(f"    {neg[0][:150]}")
        print(f"\ntotal descriptive mentions passed over: {all_negated}")
        self.assertEqual(
            all_offenders, {},
            "a shipped surface tells someone to write machine state without naming the "
            "verb that does it — an agent obeying it edits a rendered view, the command "
            f"succeeds, and nothing changes:\n{all_offenders}")

    def test_no_surface_routes_to_a_command_that_does_not_exist(self):
        known = known_commands()
        bad = {name: miss for name, text in sorted(subject().items())
               if (miss := unknown_commands(text, known))}
        print(f"\nunknown `xcheck <word>` references: {bad or 'none'}")
        self.assertEqual(bad, {}, f"an instruction routes to a verb that does not "
                                  f"exist: {bad}")


class TheFourNamedSitesAreFixed(unittest.TestCase):
    """Criterion 3. Each audit-named site asserted individually, because a single
    aggregate assertion that goes red tells a reader nothing about which one came back.
    """

    def test_the_remediator_prompt_no_longer_writes_frontmatter(self):
        p = runner.PROMPTS["Remediator"]
        self.assertNotIn("record `fixed-by:`", p)
        self.assertNotIn("in its frontmatter", p)
        self.assertIn("xcheck record-fix", p)
        print(f"\nRemediator prompt routes to: "
              f"{re.findall(r'`xcheck [a-z-]+', p)}")

    def test_the_remediator_prompt_no_longer_orders_a_triage_sync(self):
        """The instruction named `§2 rule 3`, which in the current methodology says the
        opposite: the ledger is a view. §2 rule 1 abolished the reconciliation the
        prompt was ordering."""
        self.assertNotIn("sync pending triage", runner.PROMPTS["Remediator"].lower())

    def test_the_orchestration_context_no_longer_repeats_it(self):
        block = runner.orchestration_context([f"XCHECK_SESSION_ID={SESSION_A}"],
                                             SESSION_A)
        self.assertNotIn("Record `fixed-by:", block)
        self.assertIn("xcheck record-fix", block)
        print(f"orchestration context: ...{block[-150:].strip()}")

    def test_the_cli_gates_name_verbs_not_frontmatter_fields(self):
        import contextlib
        import io
        for kind, detail, expect in (
                ("stop-norm-ratification", "CF-0001", "xcheck record-ruling"),
                ("stop-construal", ("Auditor", "a charter", "construals/x.md", "why"),
                 "xcheck admit-construal")):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cli.report_gate(kind, detail)
            out = buf.getvalue()
            print(f"\n{kind}: {out.strip()[:190]}")
            self.assertIn(expect, out)
            for hand in ("`norm-ruling` frontmatter", "set `status: admitted`",
                         "`admitted-by: <your session id>`"):
                self.assertNotIn(hand, out)

    def test_the_readme_auditor_no_longer_writes_ledger_rows(self):
        text = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("rows to the ledger, and ticks the pass checkbox", text)
        self.assertIn("closing the pass with `xcheck record-coverage`", text)

    def test_the_status_skill_names_one_canonical_record(self):
        text = (REPO / "skills" / "xcheck-status" / "SKILL.md").read_text(
            encoding="utf-8")
        self.assertNotIn("canonical finding-file status", text)
        claims = [ln.strip() for ln in text.splitlines() if "canonical" in ln]
        print("\nstatus skill canonicality claims:")
        for c in claims:
            print(f"  ...{c[max(0, c.find('canonical') - 60):][:150]}")
        self.assertTrue(claims, "the skill no longer says what is canonical at all")
        self.assertTrue(all("state.json" in c for c in claims),
                        "the skill names something other than state.json as canonical")


class APlantedInstructionIsCaught(unittest.TestCase):
    """Criterion 4, and the reason criterion 1 means anything.

    Both plants are written in wording that appears nowhere in this module, so a
    phrase-matching implementation of the checks above would pass them clean.
    """

    PLANT_FIELD = ("When the reviewer signs off, set `norm-ruling:` in the block at the "
                   "top of the class finding to the winning identifier.")
    PLANT_VERB = ("Then run `xcheck mark-resolved CF-0001` to publish the decision.")

    def test_a_hand_edit_instruction_in_novel_wording_is_caught(self):
        off, neg = imperatives_naming_state(self.PLANT_FIELD, state_field_names())
        print(f"\nplanted hand-edit caught: {off}")
        print(f"  (negated bucket: {neg})")
        self.assertEqual(len(off), 1, "the planted hand-edit walked past the check — "
                                      "every green result above is then unproven")

    def test_a_route_to_a_nonexistent_verb_is_caught(self):
        bad = unknown_commands(self.PLANT_VERB, known_commands())
        print(f"planted bad route caught: {bad}")
        self.assertEqual(bad, ["mark-resolved"])

    def planted_root(self, line):
        """A temp copy of every text surface, with `line` appended to one skill.

        A copy rather than a string, because the two tests below then run over the same
        collector, the same vocabulary and the same assertions as the real run — the
        only difference is one sentence on disk. Appended to a SKILL because that is a
        surface the shipped check reads by directory walk: a plant the collector could
        only find by being handed it would prove the plant, not the collector.
        """
        import shutil
        import tempfile
        from pathlib import Path
        tree = Path(tempfile.mkdtemp(prefix="xcheck-plant-"))
        self.addCleanup(shutil.rmtree, tree, ignore_errors=True)
        shutil.copytree(ROOT / "skills", tree / "skills")
        (tree / "xcheck").mkdir()
        shutil.copy2(ROOT / "xcheck" / "cli.py", tree / "xcheck" / "cli.py")
        for name in SURFACE_FILES:
            shutil.copy2(ROOT / name, tree / name)
        target = tree / "skills" / "xcheck-remediate" / "SKILL.md"
        target.write_text(target.read_text(encoding="utf-8") + "\n" + line + "\n",
                          encoding="utf-8")
        return tree

    def run_shipped_check(self, method, line):
        """Run the REAL test method with the subject pointed at a planted copy.

        Not a re-implementation of the assertion: `getattr(case, method)()` calls the
        same code the suite calls. Observing THAT raise is what criterion 4 asks for —
        the check failing, not a reader agreeing that it would.
        """
        global ROOT
        tree, before = self.planted_root(line), ROOT
        try:
            ROOT = tree
            case = NoSurfaceInstructsAHandEdit(method)
            with self.assertRaises(AssertionError) as caught:
                getattr(case, method)()
        finally:
            ROOT = before
        msg = str(caught.exception).replace("\n", " ")
        print(f"\nplanted in skills/xcheck-remediate/SKILL.md, {method} raised:\n  "
              f"{msg[:240]}")
        self.assertIn("xcheck-remediate", msg,
                      "the check failed, but not on the planted surface — it would "
                      "have failed on the clean tree too")
        return msg

    def test_the_shipped_check_fails_on_a_planted_hand_edit(self):
        self.run_shipped_check("test_no_imperative_names_a_state_field_without_naming_"
                               "a_verb", self.PLANT_FIELD)

    def test_the_shipped_check_fails_on_a_planted_nonexistent_verb(self):
        msg = self.run_shipped_check(
            "test_no_surface_routes_to_a_command_that_does_not_exist", self.PLANT_VERB)
        self.assertIn("mark-resolved", msg)

    def test_the_planted_copy_is_otherwise_clean(self):
        """The control. If the copy failed the checks with NOTHING planted, the two
        tests above would be observing the copy routine, not the plant."""
        global ROOT
        tree, before = self.planted_root("A line naming no field and no verb."), ROOT
        try:
            ROOT = tree
            case = NoSurfaceInstructsAHandEdit(
                "test_no_surface_routes_to_a_command_that_does_not_exist")
            case.test_no_imperative_names_a_state_field_without_naming_a_verb()
            case.test_no_surface_routes_to_a_command_that_does_not_exist()
        finally:
            ROOT = before
        print("control: an unplanted copy passes both checks")

    ANCHOR = "To mark a fix done, run `xcheck record-fix <ID> --session "
    # 0.9.0's sentence, restored verbatim. It names no verb, so the escape clause does
    # not save it — which is the point: this is what the shipped prompt said.
    RESTORED = ("When you set a finding to `fixed`, record `fixed-by:` in its "
                "frontmatter = your session identity. ")

    def prompts_from(self, edit=None):
        """`PROMPTS` as a COPY of the shipped package produces them, optionally with one
        edit to `runner.py`.

        A copy and a subprocess rather than monkey-patching the imported dict, for the
        harness's reason: the mutation has to be a real edit to a real file that a real
        interpreter imports, or the witness proves something about a dict literal in
        this test rather than about the module that ships.
        """
        import json
        import shutil
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        tree = Path(tempfile.mkdtemp(prefix="xcheck-contract-"))
        self.addCleanup(shutil.rmtree, tree, ignore_errors=True)
        shutil.copytree(REPO / "xcheck", tree / "xcheck",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if edit:
            old, new = edit
            p = tree / "xcheck" / "runner.py"
            src = p.read_text(encoding="utf-8")
            self.assertEqual(src.count(old), 1,
                             f"the prompt being mutated is not where this witness says "
                             f"it is: {old!r} occurs {src.count(old)} times")
            p.write_text(src.replace(old, new), encoding="utf-8")
        r = subprocess.run(
            [sys.executable, "-c",
             "import json, xcheck.runner as r; print(json.dumps(dict(r.PROMPTS)))"],
            cwd=tree, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, f"the copied package would not import: "
                                          f"{r.stderr[-400:]}")
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_the_0_9_0_instruction_restored_in_a_copy_of_the_package_reddens(self):
        """Criterion 7, two-armed. The control comes first: a check that would have
        been red anyway proves nothing about the mutation that follows it."""
        fields = state_field_names()
        control, _n = imperatives_naming_state(
            self.prompts_from()["Remediator"], fields)
        self.assertEqual(control, [], "CONTROL ARM: the shipped prompt is already an "
                                      "offender, so the mutant arm below measures "
                                      "nothing")
        mutated, _n = imperatives_naming_state(
            self.prompts_from((self.ANCHOR, self.RESTORED + self.ANCHOR))["Remediator"],
            fields)
        print(f"\n0.9.0 instruction restored in a copied package -> offenders: "
              f"{mutated}")
        self.assertEqual(len(mutated), 1,
                         "restoring the sentence 0.9.0 actually shipped did NOT redden "
                         "the check, so the check would not have caught it either")


class EveryStateFieldHasARoute(unittest.TestCase):
    """Criterion 5. The route table is DERIVED BY EXECUTION — each verb is run against a
    real fixture and the state document is diffed — so it is not a claim anyone has to
    keep in sync. An annotation saying `file-finding` writes `class_findings.members`
    would have read correctly in review and been false: nothing could mint a class
    finding at all until this phase, which is exactly what running them found.
    """

    CASES = {
        "set-status": ("F-0001", "accepted"),
        "record-fix": ("F-0002", "--session", SESSION_A),
        "record-verdict": ("F-0003", "--verdict", "closed", "--session", SESSION_B),
        "admit-construal": (None, "--admitter", "human:operator"),
        # PHASE 9: `--locator`/`--source-hash` are filled in from the fixture's own
        # commit in `routes()`; the verb resolves them against the subject commit
        # before it writes, so a placeholder here would measure the refusal.
        "file-finding": ("--id", "F-0009", "--title", "t", "--severity", "major",
                         "--dimension", "invariants", "--unit", "U01", "--pass", "P-01",
                         "--body", "findings/F-0009.md",
                         "--locator", "@LOCATOR@", "--source-hash", "@SHA@"),
        "file-finding --members": ("--id", "CF-0009", "--title", "a class",
                                   "--severity", "major", "--dimension", "invariants",
                                   "--unit", "U01", "--pass", "P-01",
                                   "--body", "findings/CF-0009.md",
                                   "--members", "F-0006", "--session", SESSION_A),
        "record-coverage": ("P-02", "--report", "passes/P-02-x.md"),
        "propose-construal": (None, "--role", "Verifier", "--charter", None,
                              "--session", SESSION_B, "--body", None),
        "record-plan": ("RP-0001", "--findings", "F-0001", "--body", "plans/RP-0001.md"),
        # `--base` is passed here because the route table is derived by EXECUTION: a
        # field the fixture never writes is a field the table correctly reports as
        # unrouted, and phase 12 added `queue.base` (the revision a diff-scoped pass was
        # scoped against). Omitting it here would have read as "no verb writes this".
        "queue-pass": ("P-09", "--dimension", "invariants", "--units", "U01",
                       "--charter", "c", "--stop", "s", "--base", "HEAD~1"),
        "record-refusal": ("F-0001", "--reason", "material-missing"),
        "block-on-norm": ("CF-0001",),
        "record-ruling": ("CF-0002", "--norm", "N1", "--ruled-by", "human:owner"),
        "set-limit": ("reopen_limit", "3"),
        "amend-pass": ("P-02", "--charter", "a narrower charter"),
        "cancel-pass": ("P-02",),
        "update-catalog": ("unit", "U09", "--material", "src/new.c", "--size", "400 l",
                           "--responsibility", "the new one"),
    }
    FRESH_CHARTER = "a fresh charter for the route table"

    def build(self):
        util = xcheck_submodule("util")
        key = util.construal_key("Verifier", self.FRESH_CHARTER)
        con = construal_record(status="proposed", session=SESSION_A)
        cf = dict(id="CF-0001", title="a class", severity="major", status="planned",
                  dimension="invariants", unit=["U01"], members=["F-0004"], attempts=0,
                  updated="2026-08-14", body_path="findings/CF-0001.md",
                  **{"pass": "P-01"})
        cf2 = dict(cf, id="CF-0002", body_path="findings/CF-0002.md",
                   members=["F-0005"], blocked="norm-ratification",
                   norm_ruling="pending")
        fx = Fixture(state_doc(
            findings=[finding_record("F-0001", "reported"),
                      finding_record("F-0002", "planned"),
                      finding_record("F-0003", "fixed", fixed_by=SESSION_A),
                      finding_record("F-0004", "superseded-by-class",
                                     **{"class": "CF-0001"}),
                      finding_record("F-0005", "superseded-by-class",
                                     **{"class": "CF-0002"}),
                      finding_record("F-0006", "accepted")],
            class_findings=[cf, cf2],
            queue=[queue_pass("P-01", done=True),
                   queue_pass("P-02", done=False, coverage=False)],
            construals=[con]))
        for rel, body in (("findings/F-0001.md",
                           "# F-0001\n\n## Refusal\n\nThe unit is absent.\n"),
                          ("findings/F-0009.md", "Evidence.\n"),
                          ("findings/CF-0009.md", "Census.\n"),
                          ("plans/RP-0001.md", "# RP-0001\n"),
                          ("passes/P-02-x.md", "# P-02\n\n## Coverage\n\nNOT COVERED: none.\n"),
                          (f"construals/{key}.md", "# construal\n")):
            fx.body(rel, body)
        return fx, con, key

    @staticmethod
    def flat(doc, prefix=""):
        out = {}
        if isinstance(doc, dict):
            for k, v in doc.items():
                out.update(EveryStateFieldHasARoute.flat(
                    v, f"{prefix}.{k}" if prefix else k))
        elif isinstance(doc, list):
            for i, v in enumerate(doc):
                tag = (v.get("id") or v.get("key")) if isinstance(v, dict) else None
                out.update(EveryStateFieldHasARoute.flat(v, f"{prefix}[{tag or i}]"))
        else:
            out[prefix] = doc
        return out

    def routes(self):
        import json
        table = {}
        for label, argv in self.CASES.items():
            fx, con, key = self.build()
            verb = label.split(" ")[0]
            subst = {}
            if label == "file-finding":          # the ORDINARY-finding row only
                fx.git_init()
                loc, sha = fx.locatable()
                subst = {"@LOCATOR@": loc, "@SHA@": sha}
            argv = tuple(subst.get(a, a) for a in argv)
            filled = []
            for a in argv:
                if a is not None:
                    filled.append(a)
                elif verb == "admit-construal":
                    filled.append(con["key"])
                elif verb == "propose-construal":
                    filled.append(key if not filled or filled[-1] != "--charter"
                                  else self.FRESH_CHARTER)
                else:
                    filled.append(a)
            if verb == "propose-construal":
                filled = [key, "--role", "Verifier", "--charter", self.FRESH_CHARTER,
                          "--session", SESSION_B, "--body", f"construals/{key}.md"]
            before = self.flat(json.loads(
                (fx.audit / "state.json").read_text(encoding="utf-8")))
            code, out = fx.run(verb, *filled)
            self.assertEqual(code, 0, f"{label} did not run: "
                                      f"{out.strip().splitlines()[-1][:200]}")
            after = self.flat(json.loads(
                (fx.audit / "state.json").read_text(encoding="utf-8")))
            changed = {re.sub(r"\[[^\]]*\]", "", k) for k in set(before) | set(after)
                       if before.get(k) != after.get(k)}
            table[label] = sorted(changed - {"state_revision"})
            fx.cleanup()
        return table

    def test_the_route_table_is_generated_and_every_verb_writes_something(self):
        table = self.routes()
        print("\nroute table — derived by running each verb and diffing state.json:")
        for verb, fields in table.items():
            print(f"  {verb:<24} {', '.join(fields)}")
        self.assertEqual(sorted(k.split(' ')[0] for k in table) .count("file-finding"), 2)
        silent = [v for v, f in table.items() if not f]
        self.assertEqual(silent, [], f"a verb changed nothing at all: {silent}")
        self.assertEqual(set(k.split(" ")[0] for k in table) | {"render-views"},
                         set(write.VERBS),
                         "a verb exists that the route table never runs, so its fields "
                         "are undocumented and this table is not the whole contract")

    def test_class_escalation_has_a_route_at_all(self):
        """The hole this phase found. §8 rule 4 — "on creation, every member finding
        moves to `superseded-by-class` immediately" — had no write path: `file-finding`
        appended every id to `findings`, where the schema refuses a `CF-` id, and
        nothing wrote `findings[].class` or `class_findings[].members`. So `set-status
        <ID> superseded-by-class` refused with "Set `class` when the CF is opened" and
        no way existed to open one. The §3 Remediator card, §8 and README all routed
        here; the verb declined."""
        table = self.routes()
        fields = set(table["file-finding --members"])
        print(f"\nclass escalation writes: {sorted(fields)}")
        self.assertIn("class_findings.members", fields)
        self.assertIn("findings.class", fields)
        self.assertIn("findings.status", fields)

    def test_no_writable_field_is_left_without_a_verb(self):
        """The inverted table. A schema field no verb can reach is a field an agent can
        only change by hand — which is the defect this whole phase closes."""
        table = self.routes()
        inverted = {}
        for verb, fields in table.items():
            for f in fields:
                inverted.setdefault(f, []).append(verb.split(" ")[0])
        print("\nfield -> verb(s):")
        for f in sorted(inverted):
            print(f"  {f:<34} {', '.join(sorted(set(inverted[f])))}")

        # Fields the ORCHESTRATOR owns, excluded by name with a reason each. An agent
        # never needs to change these, and none has (or should have) a verb.
        orchestrator_owned = {
            "independence",     # computed from the two envelopes by record-verdict
            "envelope",         # written when the dispatch record is opened
            "recurrence_of",    # set by file-finding --recurrence-of, not separately
            "admitted_scope",   # derived from the admitted construal
            "coverage",         # the container record-coverage fills
            "next",             # derived from status (§2 rule 3), never stored alone
        }
        reachable = {f.split(".")[-1] for f in inverted}
        unrouted = {}
        for attr in ("FINDING_FIELDS", "CLASS_FINDING_FIELDS", "COVERAGE_FIELDS",
                     "QUEUE_FIELDS", "PLAN_FIELDS", "CONSTRUAL_FIELDS"):
            req, opt = getattr(state, attr)
            gap = sorted((set(req) | set(opt)) - reachable - orchestrator_owned)
            if gap:
                unrouted[attr] = gap
        print(f"\nfields with no verb: {unrouted or 'none'}")
        self.assertEqual(unrouted, {},
                         "a schema field no verb writes is a field an agent can only "
                         f"change by hand: {unrouted}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
