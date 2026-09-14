"""Phase 7: two launch modes, named once, and no containment claim that forgets which
one it is talking about.

`xcheck next` and `xcheck loop` go through `runner.py` and get eight controls (phase
15 added the two accountability ones to the six containment ones). A human
running `/xcheck-audit` gets an agent started inside their own tool and gets none of
them. Both are legitimate; describing them as one product is not. These tests hold the
line in three places:

1. every skill says which mode it is, using the names the code defines;
2. no fourth name exists — the two literals live in `xcheck/util.py` alone;
3. every containment sentence in a shipped document sits inside a block that names a
   mode, so a reader never meets a guarantee without its scope.

Check 3 is a structural scan, not a semantic one. It cannot tell a true claim from a
false one; it can only tell a scoped claim from a loose one, and a loose claim is the
failure this phase exists to stop.
"""

import ast
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.harness import Fixture, xcheck_submodule

REPO = Path(__file__).resolve().parent.parent
util = xcheck_submodule("util")

MODES = tuple(util.LAUNCH_MODES)
GUARANTEES = util.ORCHESTRATED_GUARANTEES
SKILLS = sorted(p.parent.name for p in REPO.glob("skills/*/SKILL.md"))

# The declaration a skill body carries. Fixed shape, because the OpenCode command
# generator strips frontmatter (`awk 'c==2{print} /^---$/{c++; next}'`) — a mode
# declared in frontmatter would never reach the agent that needs it.
DECLARATION_RE = re.compile(r"\*\*Launch mode: `([a-z][a-z-]*)`\.?\*\*")

# Documents xcheck ships to a user. `XCHECK.md` and the templates are the methodology,
# which makes no containment claims at all — they are in the scan anyway, so that a
# claim arriving there later is caught rather than exempted by an old list.
SHIPPED_DOCS = ["README.md", "SECURITY.md", "CHANGELOG.md", "bootstrap.md",
                "XCHECK.md", "launchers/README.md"] + \
    [f"skills/{s}/SKILL.md" for s in SKILLS]

# A sentence claiming containment. Deliberately over-inclusive: a false positive costs
# one scoping clause in a document, a false negative ships an unscoped guarantee.
CLAIM_RE = re.compile("|".join((
    r"sandbox", r"(?<!self-)\bcontain(?:ed|ment)\b", r"isolat", r"--network=none",
    r"env(?:ironment)? allowlist", r"env_allowlist", r"allowlist",
    r"process[- ]group", r"redact", r"cap-drop", r"no-new-privileges",
    r"hard timeout", r"session_timeout", r"disposable (?:worktree|clone)",
    r"read-only", r"readonly", r"fail closed", r"escape (?:probe|attempt)",
    r"skip-permissions",
)), re.I)

MODE_RE = re.compile("|".join(re.escape(f"`{m}`") for m in MODES))


def _sentences(line):
    """Table rows split on cells first — a row holds several independent claims."""
    parts = line.split("|") if line.lstrip().startswith("|") else [line]
    for part in parts:
        for s in re.split(r"(?<=[.!?])\s+", part.strip()):
            if s:
                yield s


def scan_claims(text):
    """Every containment sentence with the modes in scope where it appears.

    Scope nests the way headings do: a mode named under `## 8` is in force inside every
    `###` below it, and released by the next `##`. The reported scope is the INNERMOST
    non-empty frame, so a section that says `orchestrated` reports `orchestrated` even
    when the document preamble named both — the coarse scope never masks the precise
    one. Frontmatter, fenced code and heading lines are not prose and are skipped; a
    heading still opens a frame, and mode names on it still count.
    """
    lines = text.splitlines()
    start = 0
    if lines and lines[0].strip() == "---":
        for i, ln in enumerate(lines[1:], 2):
            if ln.strip() == "---":
                start = i
                break
    stack, fenced, out = [(0, set())], False, []
    for n, line in enumerate(lines[start:], start + 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        heading = line.startswith("#")
        if heading:
            level = len(line) - len(line.lstrip("#"))
            while len(stack) > 1 and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, set()))
        named = MODE_RE.findall(line)
        if named:
            stack[-1][1].update(m.strip("`") for m in named)
        if heading:
            continue
        scope = next((s for _, s in reversed(stack) if s), set())
        for s in _sentences(line):
            if CLAIM_RE.search(s):
                out.append((n, tuple(sorted(scope)), s))
    return out


class TheModeNamesAreDefinedOnce(unittest.TestCase):
    """One constant, one spelling. A document that invents a third name is describing
    a guarantee nobody implements."""

    def test_the_two_names_exist_and_are_exactly_two(self):
        self.assertEqual(("orchestrated", "uncontained-direct"), MODES)
        self.assertEqual(util.ORCHESTRATED, MODES[0])
        self.assertEqual(util.UNCONTAINED, MODES[1])

    def test_no_other_module_hardcodes_a_mode_name(self):
        """`cli.py` prints both names; it must import them, not retype them.

        AST constants, not a text grep: the word "orchestrated" appears legitimately in
        prose and in `orchestrated_session_id`. What may not appear is a string literal
        equal to a mode name outside the module that defines it."""
        offenders = []
        for path in sorted(REPO.glob("xcheck/*.py")):
            if path.name == "util.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and node.value in MODES:
                    offenders.append(f"{path.name}:{node.lineno} {node.value!r}")
        self.assertEqual([], offenders,
                         "a mode name is retyped instead of imported from util: "
                         + ", ".join(offenders))

    def test_no_document_names_a_third_mode(self):
        """Every `**Launch mode: `x`**` declaration anywhere resolves to a real mode."""
        unknown = []
        for doc in SHIPPED_DOCS:
            text = (REPO / doc).read_text(encoding="utf-8")
            for m in DECLARATION_RE.finditer(text):
                if m.group(1) not in MODES:
                    unknown.append(f"{doc}: {m.group(1)}")
        self.assertEqual([], unknown, f"undefined launch modes declared: {unknown}")


class EverySkillDeclaresItsMode(unittest.TestCase):

    def test_all_six_skills_declare_one_of_the_two_modes(self):
        declared = {}
        for skill in SKILLS:
            text = (REPO / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
            found = DECLARATION_RE.findall(text)
            self.assertEqual(1, len(found),
                             f"skills/{skill}: expected exactly one launch-mode "
                             f"declaration, found {len(found)}")
            self.assertIn(found[0], MODES, f"skills/{skill}: unknown mode {found[0]}")
            declared[skill] = found[0]
        self.assertEqual(6, len(declared), f"expected six launchers, got {SKILLS}")
        print("\nlaunch modes:")
        for skill, mode in sorted(declared.items()):
            print(f"  {skill:<18} {mode}")

    def test_the_declaration_survives_the_frontmatter_stripper(self):
        """OpenCode commands are generated by dropping everything up to the second
        `---`. A declaration above that line reaches nobody."""
        for skill in SKILLS:
            lines = (REPO / "skills" / skill / "SKILL.md").read_text(
                encoding="utf-8").splitlines()
            seen, body = 0, []
            for ln in lines:
                if ln.strip() == "---" and seen < 2:
                    seen += 1
                    continue
                if seen == 2:
                    body.append(ln)
            self.assertTrue(DECLARATION_RE.search("\n".join(body)),
                            f"skills/{skill}: the declaration is in frontmatter, so "
                            f"the generated OpenCode command loses it")

    def test_every_uncontained_skill_names_all_eight_lost_guarantees(self):
        """Naming seven of eight is the failure mode this test exists for: the one left
        out is the one the reader assumes they still have. Phase 15 added the two the
        disclosure had never carried — the invocation envelope and the session receipt —
        because six containment controls with no accountability pair reads as
        "unsandboxed but recorded", and an uncontained session is not recorded at
        all."""
        for skill in SKILLS:
            text = (REPO / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
            if DECLARATION_RE.search(text).group(1) != util.UNCONTAINED:
                continue
            missing = [g for g in GUARANTEES if g.lower() not in text.lower()]
            self.assertEqual([], missing,
                             f"skills/{skill} declares {util.UNCONTAINED} but does not "
                             f"say these do not apply: {missing}")

    def test_every_uncontained_skill_answers_why_it_is_not_routed(self):
        """A surface uncontained by necessity is fine; one uncontained in silence is
        not. The reason lives in the skill, next to the disclosure."""
        for skill in SKILLS:
            text = (REPO / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
            if DECLARATION_RE.search(text).group(1) != util.UNCONTAINED:
                continue
            self.assertRegex(
                text, r"\*\*Not routed through the runner[^*]*\*\*|"
                      r"\*\*Routed as far as it can be:\*\*",
                f"skills/{skill}: no stated routing answer")


# PHASE 15 — the canonical uncontained disclosure. Same discipline as
# `_CANON_LOCK_DISCIPLINE` in `xcheck/selftest.py`: the text lives in ONE place and every
# launcher carries it BYTE-FOR-BYTE, so a paraphrase in one copy — a dropped control, a
# softened verb — diverges the bytes instead of quietly shipping. The selftest enforces
# the same literal against the INSTALLED skills; this enforces it against the source
# tree, which is where an edit lands first.
CANON_DISCLOSURE = '**Launch mode: `uncontained-direct`.** None of the runner controls are in force here, because xcheck is not in the process: no sandbox profile, no hard timeout, no environment allowlist, no log redaction, no process-group kill, no courier review of the diff, no invocation envelope and no session receipt. The first six mean containment is whatever your agent platform provides — xcheck does not know what that is and cannot report it. The last two mean this session leaves nothing machine-checkable behind: no record of which policy, charter and executable were in force when it started, and no attestation afterwards that what it did matched them. A human vouches for this work; the tool does not.'
# PHASE 4 (third audit, P0): the five writing launchers are no longer sessions. Each is a
# WRAPPER over `xcheck next`, so it declares `orchestrated` and carries this block instead
# of the disclosure above — a wrapper DOES get the eight controls, and the text saying it
# does not would now be false. Same discipline as the disclosure: one wording, byte-for-byte
# in all five, with a mutation control that names the file it reddened.
CANON_WRAPPER = '**Launch mode: `orchestrated`.** This launcher is a WRAPPER. It does not run the role inside your agent; it hands the work to `xcheck next`, which dispatches the role through `runner.py` with all eight controls in force: a sandbox profile, a hard timeout, an environment allowlist, log redaction, a process-group kill, the courier review of the diff, an invocation envelope recording what was in force at dispatch, and a session receipt attesting what the session actually did. What this skill contributes is preflight and routing — it checks the project is ready, says which role the orchestrator will dispatch and why, hands off, and reports what came back. It is not a session: it holds no writing lock, writes no finding, and has no charter of its own. The cost is the conversation. The role now runs as a child process you do not talk to, and its reasoning reaches you as recorded output instead of as a dialogue; xcheck no longer ships a launcher that trades the eight controls for that dialogue.'
# `xcheck-status` is the only launcher left that runs in the human's own agent. It keeps
# the disclosure and says why it is exempt, in one line.
CANON_AUTHORITATIVE = '**The orchestrated path is the authoritative one.** `xcheck next` and `xcheck loop` run this same role through `runner.py` with all eight controls in force. This launcher is a development escape hatch, kept because a human at a terminal needs one; when the controls matter more than the conversation, use the orchestrated path.'
CANON_STATUS_EXEMPTION = '**Exempt, and only this one:** this launcher stays direct because it is read-only — it writes nothing and moves no machine state, so there is no diff for a courier to review and no effect for an envelope or a receipt to attest.'
WRITING_LAUNCHERS = ("xcheck-audit", "xcheck-plan", "xcheck-remediate",
                     "xcheck-triage", "xcheck-verify")

# PHASE 4 (third audit): this table is the launcher surface, and this phase CHANGED it on
# purpose — which is exactly why the pin exists. Before, each writing launcher ran the
# role's own verbs (`file-finding`, `record-fix`, `record-verdict`, `record-coverage`,
# `admit-construal`, `propose-construal`, `unlock`, …) because it WAS the session. A
# wrapper runs two verbs — `xcheck status` to see the decision and `xcheck next` to hand
# off — and NAMES a few more as the operator's own commands in its cost section, which is
# what the remaining entries are. Phase 15's table is preserved in git history; the
# sections went with the role instructions, to `runner.PROMPTS` and `audit/XCHECK.md`
# where the dispatched child reads them.
LAUNCHER_SURFACE = {
    "xcheck-audit": (("amend-pass", "cancel-pass", "migrate", "next", "queue-pass", "status"),
                     ("## How to talk to the person running this audit",
                      "## What this wrapper cannot do")),
    "xcheck-plan": (("next", "queue-pass", "record-plan", "set-limit", "status"),
                    ("## How to talk to the person running this audit",
                     "## What this wrapper cannot do")),
    "xcheck-remediate": (("next", "record-plan", "set-limit", "set-status", "status"),
                         ("## How to talk to the person running this audit",
                          "## What this wrapper cannot do")),
    "xcheck-status": (("metrics", "status"),
                      ("## How to talk to the person running this audit",
                       "## Lock discipline")),
    "xcheck-triage": (("next", "set-status", "status"),
                      ("## How to talk to the person running this audit",
                       "## What this wrapper cannot do")),
    "xcheck-verify": (("next", "record-verdict", "set-limit", "set-status", "status"),
                      ("## How to talk to the person running this audit",
                       "## What this wrapper cannot do")),
}
VERB_RE = re.compile(r"`xcheck ([a-z][a-z-]+)")

# Every verb that moves machine state, DERIVED from the write module rather than listed
# here: a verb added to `write.VERBS` without being added to a hand-kept list would be a
# writing surface this check cannot see. `render-views` is excluded because it is the one
# verb that only re-renders generated views from state already written.
write_mod = xcheck_submodule("write")
WRITE_VERBS = frozenset(write_mod.VERBS) - write_mod.UNRESTRICTED_VERBS


def skill_text(skill, root=REPO):
    return (root / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")


def disclosure_problems(root):
    """The check, run against any skills tree — the repository or a mutated copy.

    Two canons now, because there are two kinds of launcher: `xcheck-status` runs in the
    human's own agent and carries the disclosure of what that costs; the five wrappers
    hand off to `xcheck next` and carry the wrapper block instead. Carrying the WRONG one
    is a problem in either direction — a wrapper that still disclaims the eight controls
    is telling a reader it is uncontained when it is not.
    """
    problems = []
    for skill in SKILLS:
        text = skill_text(skill, root)
        if skill == "xcheck-status":
            if CANON_DISCLOSURE not in text:
                problems.append(f"skills/{skill}: canonical disclosure altered "
                                f"(not byte-for-byte present)")
            if CANON_STATUS_EXEMPTION not in text:
                problems.append(f"skills/{skill}: no stated exemption")
            if CANON_WRAPPER in text:
                problems.append(f"skills/{skill}: claims to be a wrapper over the "
                                f"orchestrated path, which it is not")
            continue
        if CANON_WRAPPER not in text:
            problems.append(f"skills/{skill}: canonical wrapper block altered "
                            f"(not byte-for-byte present)")
        if CANON_DISCLOSURE in text:
            problems.append(f"skills/{skill}: a wrapper still disclaims the eight "
                            f"controls it now runs under")
        if CANON_AUTHORITATIVE in text:
            problems.append(f"skills/{skill}: still calls itself an escape hatch from "
                            f"the path it now hands off to")
    return problems


class OneAuthoritativePath(unittest.TestCase):
    """Phase 15. The launchers are an escape hatch that says so, in one wording."""

    def test_each_launcher_carries_its_canon_byte_for_byte(self):
        self.assertEqual([], disclosure_problems(REPO))
        for canon, name, population in (
                (CANON_WRAPPER, "wrapper block", WRITING_LAUNCHERS),
                (CANON_DISCLOSURE, "uncontained disclosure", ("xcheck-status",))):
            print(f"\ncanonical {name} ({len(canon)} bytes), byte-identical in:")
            for skill in population:
                text = skill_text(skill)
                self.assertEqual(1, text.count(canon),
                                 f"skills/{skill}: the block appears {text.count(canon)} times")
                print(f"  skills/{skill}/SKILL.md   offset {text.index(canon):>5}")

    def test_the_wrapper_block_names_every_guarantee_the_wrapper_now_gets(self):
        """The mirror image of the disclosure test below. The disclosure had to name all
        eight controls a direct launcher LOSES; the wrapper names all eight it GAINS, and
        for the same reason — the one left out is the one the reader guesses about."""
        missing = [g for g in GUARANTEES if g.lower() not in CANON_WRAPPER.lower()]
        self.assertEqual([], missing, f"the canonical wrapper block omits {missing}")
        print(f"  wrapper names all {len(GUARANTEES)}: {', '.join(GUARANTEES)}")

    def test_the_uncontained_mode_has_no_writing_surface_left(self):
        """The audit's P0, as an assertion. `uncontained-direct` is now inhabited by
        read-only surfaces only: a skill declaring it may not tell an agent to run a verb
        that moves machine state, and may not take the writing lock."""
        write_verbs = sorted(v for v in WRITE_VERBS)
        uncontained = []
        for skill in SKILLS:
            text = skill_text(skill)
            if DECLARATION_RE.search(text).group(1) != util.UNCONTAINED:
                continue
            uncontained.append(skill)
            self.assertNotIn("mkdir audit/.lock", text,
                             f"skills/{skill}: an uncontained launcher takes the lock")
            found = sorted(set(VERB_RE.findall(text)) & set(write_verbs))
            self.assertEqual([], found,
                             f"skills/{skill} declares {util.UNCONTAINED} and runs "
                             f"state-moving verb(s) {found} — the mode the audit called a "
                             f"release blocker still has a writing surface")
        self.assertEqual(["xcheck-status"], uncontained,
                         f"expected one read-only launcher, got {uncontained}")
        print(f"\n  {util.UNCONTAINED}: {uncontained} — read-only, checked against "
              f"{len(write_verbs)} state-moving verb(s)")

    def test_every_wrapper_routes_to_the_orchestrated_verb_and_runs_no_role(self):
        """Criterion 1: a wrapper hands off, it does not BE the session. The role-execution
        instructions are the things a session does and a wrapper cannot — taking the lock,
        writing a construal, recording its own refusal. They moved to where the dispatched
        child actually reads them (`runner.PROMPTS` and `audit/XCHECK.md`), and a copy left
        behind in a launcher is a copy that can drift out of agreement with them."""
        for skill in WRITING_LAUNCHERS:
            text = skill_text(skill)
            self.assertEqual(util.ORCHESTRATED, DECLARATION_RE.search(text).group(1),
                             f"skills/{skill}: a wrapper must declare the mode of the "
                             f"session it hands off to")
            self.assertIn("`xcheck next`", text, f"skills/{skill}: no hand-off")
            for gone in ("mkdir audit/.lock", "## Lock discipline", "## Construal",
                         "## Refusal", "propose-construal", "$XCHECK_SESSION_ID",
                         "XCHECK_LOCK_INHERITED"):
                self.assertNotIn(gone, text,
                                 f"skills/{skill}: still carries a role-execution "
                                 f"instruction ({gone!r}) — it is a wrapper, not a session")
        print(f"  wrappers routing to `xcheck next`: {', '.join(WRITING_LAUNCHERS)}")

    def test_the_disclosure_names_every_guarantee_the_code_defines(self):
        """The canon and `ORCHESTRATED_GUARANTEES` cannot drift apart: adding a control
        to the tuple without adding it to the canon is the exact failure this pins."""
        missing = [g for g in GUARANTEES if g.lower() not in CANON_DISCLOSURE.lower()]
        self.assertEqual([], missing, f"the canonical disclosure omits {missing}")
        print(f"  names all {len(GUARANTEES)}: {', '.join(GUARANTEES)}")

    def test_control_a_mutated_copy_reddens_and_names_the_file(self):
        """Without this the byte-identity check could be passing on an empty list."""
        import shutil
        import tempfile
        for victim, old, new in (
                ("xcheck-verify", "session receipt attesting",
                 "session receipt more or less attesting"),
                ("xcheck-status", "no session receipt",
                 "no session receipt worth mentioning")):
            with self.subTest(skill=victim), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                shutil.copytree(REPO / "skills", root / "skills")
                f = root / "skills" / victim / "SKILL.md"
                body = f.read_text(encoding="utf-8")
                self.assertIn(old, body, f"{victim}: the mutation has no subject")
                f.write_text(body.replace(old, new), encoding="utf-8")
                found = disclosure_problems(root)
                print(f"  CONTROL   {victim} mutated -> {found}")
                self.assertTrue(any(victim in p for p in found),
                                f"a mutated {victim} did not redden: {found}")
                self.assertTrue(all(victim in p for p in found),
                                f"the mutation implicated another file too: {found}")

    def test_only_status_is_exempt_and_it_says_why(self):
        status = skill_text("xcheck-status")
        self.assertIn("read-only", CANON_STATUS_EXEMPTION)
        self.assertNotIn(CANON_AUTHORITATIVE, status)
        for skill in WRITING_LAUNCHERS:
            self.assertNotIn(CANON_STATUS_EXEMPTION, skill_text(skill),
                             f"skills/{skill}: a writing launcher claims the read-only "
                             f"exemption")
        print(f"  exempt: xcheck-status — {CANON_STATUS_EXEMPTION.split(':', 1)[1].strip()[:60]}...")

    def test_the_launcher_surface_is_exactly_the_pinned_one(self):
        """The pin is the operative surface: the verbs each launcher names and its
        section structure. Phase 15 used it to prove a wording change changed nothing;
        phase 4 edited it deliberately, which is the same guarantee read the other way —
        a surface cannot move without someone writing down that it moved."""
        print("\nlauncher surface (verbs, sections):")
        for skill in SKILLS:
            text = skill_text(skill)
            # the labelling paragraphs are excluded BY CONSTRUCTION: they mention
            # `xcheck next` and `xcheck loop` as the path this launcher is not, which is
            # a sentence about the launcher, not a step it runs. They are pinned
            # byte-for-byte above, so nothing hides in the subtraction.
            for canon in (CANON_DISCLOSURE, CANON_AUTHORITATIVE, CANON_STATUS_EXEMPTION,
                          CANON_WRAPPER):
                text = text.replace(canon, "")
            verbs = tuple(sorted(set(VERB_RE.findall(text))))
            heads = tuple(ln.strip() for ln in text.splitlines() if ln.startswith("## "))
            self.assertEqual(LAUNCHER_SURFACE[skill], (verbs, heads),
                             f"skills/{skill}: the launcher gained or lost behaviour in a "
                             f"phase that was supposed to change only wording")
            print(f"  {skill:<18} {len(verbs)} verb(s), {len(heads)} section(s)")


class NoContainmentClaimEscapesItsMode(unittest.TestCase):
    """The structural gate. Prints the full parse so the scoping can be read, not
    trusted."""

    def test_every_containment_claim_sits_in_a_mode_scoped_block(self):
        loose, total = [], 0
        print("\ncontainment claims by scope:")
        for doc in SHIPPED_DOCS:
            claims = scan_claims((REPO / doc).read_text(encoding="utf-8"))
            total += len(claims)
            unscoped = [c for c in claims if not c[1]]
            print(f"  {doc}: {len(claims)} claims, {len(unscoped)} unscoped")
            for line, scope, sentence in claims:
                print(f"    {line:>4} [{','.join(scope) or 'UNSCOPED'}] "
                      f"{sentence[:96]}")
            loose += [f"{doc}:{c[0]} {c[2][:70]}" for c in unscoped]
        print(f"  TOTAL {total} claims, {len(loose)} unscoped")
        self.assertGreater(total, 50, "the scanner found almost nothing — it is broken, "
                                      "not the documents")
        self.assertEqual([], loose, "containment claims outside any mode-scoped block:"
                                    "\n  - " + "\n  - ".join(loose))

    def test_the_scanner_would_catch_an_unscoped_claim(self):
        """The gate above passes. This is why that means something: the same scanner,
        given a document that makes the claim without naming a mode, reports it — and
        stops reporting it the moment the mode is named. Without this, a scanner whose
        vocabulary silently stopped matching would look like a clean sweep."""
        loose_doc = "# Notes\n\nThe child runs under a sandbox profile with a hard " \
                    "timeout.\n"
        found = scan_claims(loose_doc)
        self.assertEqual(1, len(found), f"expected one claim, got {found}")
        self.assertEqual((), found[0][1], "the claim should be unscoped")

        scoped_doc = loose_doc.replace("# Notes\n", "# Notes\n\nThis section describes "
                                                    "`orchestrated` sessions.\n")
        found = scan_claims(scoped_doc)
        self.assertEqual(("orchestrated",), found[-1][1],
                         "naming the mode should scope the claim")

    def test_a_sibling_section_does_not_inherit_a_scope(self):
        """Scope nests downward, never sideways: `## A` naming a mode must not scope a
        claim under `## B`. Otherwise one declaration anywhere would launder the whole
        document."""
        doc = ("# D\n\n## A\n\nRuns `orchestrated`.\n\n### A1\n\nA sandbox profile "
               "applies.\n\n## B\n\nA hard timeout applies.\n")
        scope = {c[2]: c[1] for c in scan_claims(doc)}
        self.assertEqual(("orchestrated",), scope["A sandbox profile applies."],
                         "a subsection must inherit its parent's scope")
        self.assertEqual((), scope["A hard timeout applies."],
                         "a sibling section must NOT inherit the scope")


class StatusPrintsBothLaunchModes(unittest.TestCase):
    """Criterion 4: the resolved profile AND the sentence that says which sessions it
    does not cover. One without the other is the overclaim."""

    def test_status_prints_the_profile_and_the_hand_launched_line(self):
        code, out = Fixture().run("status")
        self.assertEqual(0, code, out)
        self.assertIn("sandbox: worktree for writing roles, readonly for reading roles",
                      out)
        self.assertIn(f"`{util.ORCHESTRATED}` sessions only", out)
        self.assertIn(f"is `{util.UNCONTAINED}`", out)
        for g in GUARANTEES:
            self.assertIn(g, out, f"status does not name the lost guarantee {g!r}")
        print("\nxcheck status, launch-mode lines:")
        for ln in out.splitlines():
            if "sandbox:" in ln or "orchestrated" in ln or "uncontained" in ln:
                print(f"  {ln}")

    def test_the_line_is_printed_even_with_no_sessions_yet(self):
        """The disclosure is a property of the tool, not of its history — a fresh
        project is exactly when a reader is deciding how to launch."""
        code, out = Fixture().run("status")
        self.assertNotIn("last session", out)
        self.assertIn(util.UNCONTAINED, out)


# --------------------------------------------------------------------------------
# The fourth audit found `launchers/README.md:3` and the live `xcheck status` both
# calling every launcher `uncontained-direct` — false since the wrapper rewrite — while
# 1,239 tests passed. Two tests above were green over it and neither was wrong to be:
#
#   `NoContainmentClaimEscapesItsMode` asks whether a containment sentence is SCOPED to
#   a mode. "Every launcher on this page is `uncontained-direct`" is perfectly scoped.
#   Scoped is not true.
#
#   `StatusPrintsBothLaunchModes` asserts the status output CONTAINS `is
#   \`uncontained-direct\`` and each of the eight guarantee names. A substring check
#   cannot see that the SUBJECT of the sentence — "the /xcheck-* launcher skills" — is
#   the wrong set of launchers.
#
# Both check the shape of a claim. Neither compares the claim to the behaviour, which is
# why a reader found this and a suite did not. What follows compares them: the mode a
# document CLAIMS for a launcher, against the mode that launcher's own body IMPLEMENTS.

# Who a sentence is talking about. A declared table, not a quantifier heuristic: "every
# launcher" and "the five writing launchers" are both collective, and reading the second
# as the first would fail a true sentence. A phrase absent from this table makes no
# checkable claim, which is a reason to add a phrase, never to widen a pattern.
SUBJECTS = {
    "every launcher on this page": frozenset(SKILLS),
    "every launcher": frozenset(SKILLS),
    "each launcher": frozenset(SKILLS),
    "all launchers": frozenset(SKILLS),
    "the six launchers": frozenset(SKILLS),
    "the six launcher skills": frozenset(SKILLS),
    "the /xcheck-* launcher skills": frozenset(SKILLS),
    "the five writing launchers": frozenset(WRITING_LAUNCHERS),
    "the five writing launcher skills": frozenset(WRITING_LAUNCHERS),
    "the writing launchers": frozenset(WRITING_LAUNCHERS),
}

# A skill that hands its role to `xcheck next` puts the runner in the process; a skill
# that does not, does not. Two signals rather than one, because a body that BOTH hands
# off and runs the role itself is neither mode and must say so rather than being sorted
# into whichever test ran first.
HANDOFF_RE = re.compile(r"xcheck next\b")
RUNS_ROLE_RE = re.compile(r"XCHECK_SESSION_ID|XCHECK_LOCK_INHERITED"
                          r"|acquire the (?:writing )?lock"
                          r"|Read audit/XCHECK\.md\. Role:")


def derived_mode(skill, root=REPO):
    """The launch mode a skill IMPLEMENTS, read off its body — never off its own claim.

    Deriving it from the declaration would make the check `x == x`, which is exactly the
    tautology that let the drift through: every document agreed with every other document
    and none of them agreed with the code.
    """
    body = skill_text(skill, root)
    body = DECLARATION_RE.sub("", body)      # the claim may not vote on the derivation
    hands_off = bool(HANDOFF_RE.search(body))
    runs_itself = bool(RUNS_ROLE_RE.search(body))
    if hands_off and runs_itself:
        return "CONFLICT (hands off to `xcheck next` AND runs the role in-session)"
    return util.ORCHESTRATED if hands_off else util.UNCONTAINED


# A changelog entry describes a RELEASE, not the tree. "`uncontained-direct` (the six
# launcher skills)" under `## 0.9.0` was true of 0.9.0 and is history now; editing it
# would be rewriting an already-admitted record to make a present-tense check pass. So
# only the TOPMOST section — the one this run is still writing — is checked against the
# shipped behaviour. Every other document is checked whole: none of them is a log.
HISTORICAL = {"CHANGELOG.md"}


def _current_text(label, text):
    if label not in HISTORICAL:
        return text
    lines = text.splitlines(keepends=True)
    starts = [i for i, ln in enumerate(lines) if ln.startswith("## ")]
    return "".join(lines[:starts[1]]) if len(starts) > 1 else text


def mode_claim_problems(root=REPO, extra=()):
    """Every sentence whose subject is a launcher set and which names one mode, checked
    against what those launchers derive. `extra` carries surfaces that are not files —
    the live `xcheck status` output — as (label, text) pairs."""
    derived = {s: derived_mode(s, root) for s in SKILLS}
    surfaces = [(d, _current_text(d, (root / d).read_text(encoding="utf-8")))
                for d in SHIPPED_DOCS if (root / d).is_file()]
    problems = []
    for label, text in list(surfaces) + list(extra):
        for n, line in enumerate(text.splitlines(), 1):
            for sentence in _sentences(line):
                modes = {m.strip("`") for m in MODE_RE.findall(sentence)}
                if len(modes) != 1:
                    continue          # no claim, or a sentence contrasting the two
                claimed = modes.pop()
                low = sentence.lower()
                for phrase, who in SUBJECTS.items():
                    if phrase not in low:
                        continue
                    wrong = sorted(w for w in who if derived[w] != claimed)
                    if wrong:
                        problems.append(
                            f"{label}:{n}: claims `{claimed}` for {phrase!r}, but "
                            f"{', '.join(wrong)} implement "
                            f"{', '.join(sorted({derived[w] for w in wrong}))} — "
                            f"{sentence.strip()[:110]}")
    return problems


class ADocumentMayNotClaimAModeALauncherDoesNotImplement(unittest.TestCase):
    """The consumer test: shipped prose against the behaviour it describes."""

    def test_each_skill_declares_the_mode_its_own_body_implements(self):
        """The control for the derivation. If this were red the cross-check below would
        be measuring a broken derivation rather than a broken document."""
        for skill in SKILLS:
            claimed = DECLARATION_RE.search(skill_text(skill)).group(1)
            self.assertEqual(derived_mode(skill), claimed,
                             f"{skill}: declares `{claimed}`, body implements "
                             f"`{derived_mode(skill)}`")
        print("\nskill -> mode derived from its body (declaration removed first):")
        for skill in SKILLS:
            print(f"  {skill:<18} {derived_mode(skill)}")

    def test_only_the_read_only_launcher_is_direct(self):
        direct = sorted(s for s in SKILLS if derived_mode(s) == util.UNCONTAINED)
        self.assertEqual(["xcheck-status"], direct,
                         "the direct set is the read-only launcher and nothing else")

    def test_no_shipped_sentence_claims_a_mode_its_launchers_do_not_implement(self):
        code, out = Fixture().run("status")
        self.assertEqual(0, code, out)
        problems = mode_claim_problems(extra=[("xcheck status (live output)", out)])
        self.assertEqual([], problems, "\n".join(problems))

    def test_counterfactual_replanting_the_old_sentence_reddens_this(self):
        """The exact sentence the audit quoted, put back in a copied tree."""
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "tree"
            shutil.copytree(REPO / "skills", copy / "skills")
            for doc in SHIPPED_DOCS:
                if (REPO / doc).is_file():
                    (copy / doc).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(REPO / doc, copy / doc)
            self.assertEqual([], mode_claim_problems(copy),
                             "control: a faithful copy must grade clean")

            target = copy / "launchers" / "README.md"
            before = target.read_text(encoding="utf-8")
            planted = before.replace(
                "## Install",
                "**Every launcher on this page is `uncontained-direct`.**\n\n## Install", 1)
            self.assertNotEqual(before, planted, "the plant changed nothing")
            target.write_text(planted, encoding="utf-8")

            problems = mode_claim_problems(copy)
            self.assertTrue(problems, "the replanted sentence was not caught")
            self.assertTrue(any(p.startswith("launchers/README.md:") for p in problems),
                            f"the failure does not name the file and line: {problems}")
            for launcher in WRITING_LAUNCHERS:
                self.assertTrue(any(launcher in p for p in problems),
                                f"{launcher} is not named in the failure")
            print("\ncounterfactual — the sentence the audit quoted, replanted:")
            for p in problems:
                print(f"  {p}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
