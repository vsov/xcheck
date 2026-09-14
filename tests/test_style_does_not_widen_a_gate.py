"""No gate was widened to let the new prose through.

The cheap way to make new text pass an old check is to teach the check to skip it. This
project's history says that is how coverage disappears: the check stays green, stops
covering, and nobody sees the moment it happened. A probe run before any prose was written
showed a plausible draft reddening one of these gates, so the temptation here was real and
specific — one entry in an exclusion list makes it all go away.

Four gates read the shipped documents:

    test_executable_contract   imperatives naming a state field without naming a verb
    test_launch_surfaces       containment claims outside a mode-scoped block
    test_doc_contract          record surfaces that are not a `write.VERBS` name
    test_doc_drift             the literal hand-edit phrasings, by line

This module proves three things about them: they still pass; their source contains no
mention of the new material at all; and each still catches the defect it was built for.

**On the needles.** The check greps for literals that would have to appear in any exclusion
— the style directory, the style file's stem, the two block markers, the six skill
directory names. Every needle is assembled from halves at import time, so this file's own
source never contains one contiguously. That is what lets the scan include this file rather
than exempt it, which is the shape the previous defect took here.

**Flat absence, not judgement.** "Is this occurrence in an exclusion position?" is not
mechanically decidable, and a check that guesses is a check that can be argued with. So the
rule is absence, and a legitimate occurrence goes in `DECLARED` with its count and a reason.
An undeclared hit fails; so does a declaration with no hit, so the list cannot rot.
"""

import pathlib
import re
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests import test_executable_contract as ec
from tests import test_launch_surfaces as ls

REPO = pathlib.Path(__file__).resolve().parent.parent
SELF = pathlib.Path(__file__).name[: -len(".py")]

GATES = ("test_executable_contract", "test_launch_surfaces",
         "test_doc_contract", "test_doc_drift")

# Every file the no-exclusion scan reads. This module is in it: a detector that exempts
# itself is the exact defect that has shipped here before.
SCANNED = GATES + (SELF,)

# Halves, joined at import time. Written this way for one reason: so that no line of this
# file contains any of these strings, and the scan below can therefore include this file.
_S = "sty" + "les"
_E = "el" + "i5"
_M = "<!-- xch" + "eck:style:"
_K = "xch" + "eck-"

NEEDLES = {
    "style-dir": _S + "/",
    "style-stem": _E,
    "begin-marker": _M + "begin -->",
    "end-marker": _M + "end -->",
    "skill:planner": _K + "plan",
    "skill:auditor": _K + "audit",
    "skill:triager": _K + "triage",
    "skill:remediator": _K + "remediate",
    "skill:verifier": _K + "verify",
    "skill:status": _K + "status",
}

# The four needles no gate may contain under any reason. An exclusion written for the new
# block has to name the block, its source file, or its markers — there is no fifth way to
# spell it. These are absolute: a hit here is a failure, and `DECLARED` cannot cover them.
ABSOLUTE = ("style-dir", "style-stem", "begin-marker", "end-marker")

# (module, needle) -> (exact count, why it is legitimate). Every entry predates this run.
# Reasons are written without the literals so this file stays scannable.
DECLARED = {
    ("test_executable_contract", "skill:planner"):
        (1, "a false positive of the needle, not a skill name: the temp-tree prefix "
            "`...-plant-` contains the planner's directory name as a substring"),
    ("test_executable_contract", "skill:remediator"):
        (3, "the counterfactual plants its bad instruction into the Remediator's skill "
            "file and asserts the failure message names that file"),
    ("test_executable_contract", "skill:status"):
        (2, "the Status role's skill file is one of the six derived subjects, and one "
            "test asserts it names a single canonical record"),
    # PHASE 15 raised these: the module now pins the canonical uncontained disclosure and
    # each launcher's operative surface, so it names all six launcher directories as DATA
    # in two tables. That is the opposite of an exclusion — the names are there to be
    # checked, not skipped — but the rule is flat absence, so each is declared with a
    # count that a new silent occurrence would break.
    ("test_launch_surfaces", "skill:auditor"):
        (3, "the module docstring names the Auditor slash-command as the example of a "
            "hand-launched session; plus its row in the operative-surface pin and its "
            "entry in the writing-launcher tuple"),
    ("test_launch_surfaces", "skill:planner"):
        (2, "its row in the operative-surface pin and its entry in the writing-launcher "
            "tuple"),
    ("test_launch_surfaces", "skill:remediator"):
        (2, "its row in the operative-surface pin and its entry in the writing-launcher "
            "tuple"),
    ("test_launch_surfaces", "skill:triager"):
        (2, "its row in the operative-surface pin and its entry in the writing-launcher "
            "tuple"),
    ("test_launch_surfaces", "skill:verifier"):
        (3, "its row in the operative-surface pin, its entry in the writing-launcher "
            "tuple, and the mutation control that names it as the file it mutates"),
    ("test_launch_surfaces", "skill:status"):
        (10, "the read-only launcher is the one EXEMPTED from the authoritative path, so "
            "it is named in the exemption check, in the disclosure checker branch, in "
            "the mutation control as the second victim, and in its operative-surface "
            "row — the exemption is the thing most worth failing loudly, so it carries "
            "the highest count. PHASE 4 added three: it is now the SOLE inhabitant of "
            "the hand-launched mode, so the test that asserts that mode has no writing "
            "surface left names it as the expected population, and the canon-carrying "
            "population it belongs to is written out beside the wrappers'. PHASE 3 of "
            "the FOURTH audit added one: the consumer test that derives each launcher's "
            "mode from its BODY asserts that the derived direct set is exactly the "
            "read-only launcher and nothing else, which is the same exemption stated "
            "as a derivation rather than as a claim — the audit found "
            "`launchers/README.md` and the live status output both calling every "
            "launcher direct while every test that read those sentences was green"),
    ("test_doc_contract", "skill:auditor"):
        (1, "one test reads the Auditor's launcher to assert it names where its charter "
            "comes from"),
    # PHASE 4 dropped three of these to zero: the role -> verbs map is keyed on ROLE names
    # now, because the launchers stopped carrying role instructions and the list a
    # dispatched role reads is its role card in the methodology. The triager keeps its key
    # — its gate is the one the orchestrator cannot dispatch, so the command stays in the
    # launcher, in front of the human who runs it.
    ("test_doc_drift", "skill:triager"):
        (1, "the one launcher-borne write surface left: the triage wrapper hands the "
            "human its verb"),
}

# Each gate's OWN counterfactual — the test that plants the defect the gate exists to catch
# and asserts the gate reddens. Named individually, because "4 counterfactuals passed" does
# not say which four, and a silently-skipped one reads exactly like a passing one.
COUNTERFACTUALS = [
    ("test_executable_contract",
     "APlantedInstructionIsCaught.test_a_hand_edit_instruction_in_novel_wording_is_caught",
     "an imperative naming a state field, worded so it appears nowhere in the module"),
    ("test_executable_contract",
     "APlantedInstructionIsCaught.test_a_route_to_a_nonexistent_verb_is_caught",
     "a documented route to a command that does not exist"),
    ("test_executable_contract",
     "APlantedInstructionIsCaught.test_the_shipped_check_fails_on_a_planted_hand_edit",
     "the SHIPPED check, run against a temp package carrying the plant"),
    ("test_executable_contract",
     "APlantedInstructionIsCaught.test_the_shipped_check_fails_on_a_planted_nonexistent_verb",
     "the shipped route check, same temp package"),
    ("test_executable_contract",
     "APlantedInstructionIsCaught.test_the_planted_copy_is_otherwise_clean",
     "the control: the planted copy fails for the plant and nothing else"),
    ("test_executable_contract",
     "APlantedInstructionIsCaught."
     "test_the_0_9_0_instruction_restored_in_a_copy_of_the_package_reddens",
     "the historical 0.9.0 instruction, restored in a copy"),
    ("test_launch_surfaces",
     "NoContainmentClaimEscapesItsMode.test_the_scanner_would_catch_an_unscoped_claim",
     "a containment sentence outside any mode-scoped block"),
    ("test_launch_surfaces",
     "NoContainmentClaimEscapesItsMode.test_a_sibling_section_does_not_inherit_a_scope",
     "a sibling section silently inheriting its neighbour's scope"),
    ("test_doc_contract",
     "TheCheckIsSemanticNotPhraseBased.test_the_prose_record_surface_is_caught",
     "a role card recording to prose instead of to a verb"),
    ("test_doc_contract",
     "TheCheckIsSemanticNotPhraseBased.test_the_queue_read_is_caught",
     "a skill taking a pass id from the plan prose"),
    ("test_doc_drift",
     "SkillsCarryTheContract."
     "test_no_shipped_text_tells_an_agent_to_hand_edit_machine_state",
     "the sentence 0.9.0 actually shipped, plus the span rule in both directions"),
]

# Recorded before any prose was written. Pinned, not re-derived: a figure the run computes
# for itself is a figure the run can move.
#
# Moved 144 -> 145 on 2026-08-17, once and deliberately. The style run pinned 144; the
# 145th claim is the CHANGELOG bullet describing the lock-seed fix, which necessarily says
# `sandbox`, `lock` and `fail-closed` because that is what the fix is about. Re-pinning a
# total is not the same as excusing a claim: the invariant this file exists to hold is
# PRE_RUN_UNSCOPED, and it did not move — the new claim sits under a non-empty heading
# frame like every other one. Whoever moves this number next owes the same two lines:
# which document gained the claim, and proof that unscoped stayed at zero.
#
# Moved 145 -> 146 on 2026-09-01, paying that debt. CHANGELOG.md went 20 -> 21; every other
# document is byte-for-byte the same count. The 146th claim is the paragraph describing the
# F-0125/F-0132 fix, which says `lock`, `fixture` and `refuses` because the defect is that
# an inherited lock signal reached fixtures that are other projects. Unscoped stayed at 0 —
# the assertion above it, PRE_RUN_UNSCOPED, is the invariant this file actually holds, and
# it is untouched: the new claim sits under a non-empty heading frame like every other one.
# Moved 146 -> 147 on 2026-09-03, paying the same debt. README.md went up by one; every
# other document is byte-for-byte the same count. The 147th claim is the sentence in the
# invocation-envelope section saying `executable_version` is a digest taken WITHOUT
# running the executable — it says `sandbox`, `container` and `host` because the defect it
# describes (F-0098) is that the old `--version` probe ran an operator-configured binary
# on the host before `sandbox.enter()`. Unscoped stayed at 0: the claim sits under the
# `### Invocation envelope, events, and measured independence` frame, and PRE_RUN_UNSCOPED
# — the invariant this file actually holds — is untouched.
# Moved 147 -> 149 on 2026-09-03, same debt paid. README.md went 39 -> 41; every other
# document is byte-for-byte the same count. The two new claims are the paragraphs
# introducing the POLICY SPLIT in the orchestrator.conf section — they say `sandbox`,
# `containment`, `egress` and `refused` because that is the list of keys that may no
# longer live inside the audited repository. Unscoped stayed at 0: both sit under the
# `### orchestrator.conf` frame, and PRE_RUN_UNSCOPED — the invariant this file actually
# holds — is untouched.
# Moved 149 -> 156 on 2026-09-03, same debt paid, and this is the largest single move
# the pin has seen. README.md went 41 -> 44 (the profile's resolution order, the
# fail-closed read, and `policy_digest` on the envelope) and SECURITY.md 57 -> 61 (the
# paragraph in the trust model saying the policy that decides what runs may not live
# inside the audited repository, and what changed in 0.9.2). Every other document is
# byte-for-byte the same count. They say `containment`, `sandbox`, `egress`, `refused`
# and `environment` because they are describing which keys stopped living in the subject.
# Unscoped stayed at 0 — both documents' new text sits under existing non-empty heading
# frames, and PRE_RUN_UNSCOPED, the invariant this file actually holds, is untouched.
# Moved 156 -> 159 on 2026-09-03 (phase 9, subject provenance). All three are CHANGELOG.md
# (21 -> 24); every other document is byte-for-byte the same count. They are the three
# sentences of the provenance entry that say `worktree` and `sandbox_seed` — describing
# where a write verb runs, which is the whole reason the recorded commit was the sandbox's
# own. Unscoped stayed at 0: the entry sits inside the existing `## 0.9.1` frame, which
# already names both launch modes, and PRE_RUN_UNSCOPED — the invariant this file actually
# holds — is untouched. Phase 10's CHANGELOG entry added zero claims.
# Moved 159 -> 161 on 2026-09-03 (phase 12, the deadlines). Both are CHANGELOG.md
# (24 -> 26); every other document is byte-for-byte the same count. They are the watchdog
# entry's opening sentence and its `session_timeout` sentence — both name a session bound,
# which is a containment vocabulary word and correctly counted as a claim. Unscoped
# stayed at 0: the entry sits
# inside the existing `## 0.9.1` frame, which already names both launch modes, and
# PRE_RUN_UNSCOPED — the invariant this file actually holds — is untouched.
# Moved 161 -> 171 on 2026-09-03 (phase 15, one authoritative execution path). This move
# spans five documents, which is unusual and is the point of the phase: the same fact had
# to reach every reader in their own document. README.md 44 -> 45 (the paragraph naming
# `orchestrated` as the authoritative path and the launchers as the escape hatch),
# SECURITY.md 61 -> 62 (the same, in the two-modes list at the top), XCHECK.md 0 -> 2
# (§4 rule 7 gained its first two containment claims: the hand-launched one-liner is the
# escape hatch, and the orchestrated path is the authoritative one), CHANGELOG.md 26 -> 30
# (the phase entry, which necessarily says `sandbox`, `contain`, `read-only` and
# `receipt`), and the read-only launcher skill file 3 -> 5 (its exemption line, plus the
# sentence saying which two controls would still have mattered — named this way, not by
# its directory, because a needle written out here would make this file unscannable). The other five skills are
# unchanged in COUNT — their disclosure was rewritten, not added to. Unscoped stayed at 0:
# every new sentence sits under a heading frame that already names a launch mode, and
# PRE_RUN_UNSCOPED — the invariant this file actually holds — is untouched.
# Moved 171 -> 181 on 2026-09-03 (phase 16, polish). All ten are CHANGELOG.md (30 -> 40);
# every other document is byte-for-byte the same count. Seven are the entries phases 1-7
# never wrote — host execution before containment, the policy split, the operator profile,
# the write boundary, the action-bound receipt, the charter-scoped progress digest, and the
# corrected figures — each of which necessarily says `sandbox`, `containment`, `isolat` or
# `read-only` because that is the subject of the fix. Three are in the new
# breaking-changes table, which states the same facts once more in migration form. Unscoped
# stayed at 0: everything sits inside the `## 0.9.1` frame, which names both launch modes in
# its own preamble, and PRE_RUN_UNSCOPED — the invariant this file actually holds — is
# untouched. And 181 -> 182 in the same phase: SECURITY.md 62 -> 63, the deadlines row
# added to the controls table, which says `hard timeout` because that is the control it
# sits beside and extends. Its neighbour row (role authorization) contains no containment
# vocabulary and correctly counts as zero. Unscoped stayed at 0. Then 182 -> 183 for
# phase 16's own CHANGELOG entry (40 -> 41), the sentence saying the escape matrix was
# re-derived against a live docker backend — it says `container` and `escape probe`
# because that is what it re-derived. Unscoped stayed at 0.
#
# 183 -> 189 in phase 4 of the third-audit response, and this is the largest single move
# the pin has taken. The five writing launchers became WRAPPERS over the orchestrated
# path, and the canonical wrapper block they now carry NAMES the eight controls the
# dispatched session runs under — so each of the five gained containment vocabulary that
# the old escape-hatch disclosure spent on saying the same eight were absent. Per
# document: plan 3 -> 5, audit 4 -> 5, remediate 4 -> 6, verify 3 -> 5, and triage 3 -> 2
# (it lost the lock-discipline block with every other role instruction, and it is the one
# wrapper that hands off to nothing). Every one of the new claims sits inside the
# `**Launch mode: `orchestrated`.**` block that opens each wrapper, so PRE_RUN_UNSCOPED —
# the invariant this file actually holds — stayed at 0, which is the number that would
# have caught a claim escaping its scope.
# 189 -> 190 in phase 9 of the third-audit response. SECURITY.md 63 -> 64: the paragraph
# under the A8 row saying what that phase narrows and what stays open. It counts as a
# containment claim because it names `container` — while saying the cell still reads
# `escaped` under it, which is the opposite of a widening. The claim is inside the
# adversarial-matrix section, which is `orchestrated` by the document's own preamble, so
# PRE_RUN_UNSCOPED stayed at 0 — the invariant this file actually holds, and the number
# that would have caught a claim escaping its scope.
# 190 -> 196 in phase 21 of the third-audit response, all six in CHANGELOG.md's new
# `## 0.9.2` section: the breaking-change rows for the required `trust_level` (which names
# `container` as what untrusted material needs), the duplicate-key refusal that stopped a
# silent containment downgrade, the launcher rewrite, the four retention states, and the
# gate's refusal to run inside a PR tier. Every one is a migration instruction, which is
# the one place a containment claim has to be legible to an upgrading operator.
#
# The section opens with its own scoping paragraph — `orchestrated`, and saying why that
# is no longer a narrowing now that phase 4 removed the `uncontained-direct` writing path
# — so all six sit inside a non-empty frame and PRE_RUN_UNSCOPED stayed at 0. That is the
# invariant this file actually holds, and it is the number that would have caught a claim
# escaping its scope. The first run of this gate after the section was written reported
# exactly that failure, with 4 unscoped claims, and the fix was the scoping paragraph
# rather than a looser gate.
# Moved 196 -> 203 on 2026-09-04 (phase 18 of the fourth-audit response), same debt paid.
# CHANGELOG.md went 47 -> 54 and every other document is byte-for-byte the same count: the
# seven new claims are all in the 0.9.3 entry, which necessarily says `allowlist`,
# `sandbox`, `read-only`, `fail closed` and `isolat` because that entry is about a
# truthfulness allowlist, an event append that refuses on a read-only disk, and a routing
# change. Unscoped stayed at 0 — the invariant this file actually holds: the entry opens
# with the same `orchestrated` scoping paragraph 0.9.2's does, placed BEFORE the first
# claim, which is where the scan needs it (a scoping clause below a claim scopes nothing).
# Moved 203 -> 207 on 2026-09-05 (phase 13 of the fifth-audit response), same debt paid.
# CHANGELOG.md went 54 -> 58 and every other document is byte-for-byte the same count. The
# four new claims are all in the 0.9.4 entry and all four are the SAME two-sentence pair
# and two migration rows that any release entry about containment has to carry: the
# read-only `status` sentence the 0.9.2 and 0.9.3 entries also carry verbatim, the phase-3
# row saying the route is judged BEFORE `sandbox.collect()` (a narrowing — the check moved
# earlier, so a mismatched session is quarantined instead of merged), and the phase-10
# migration row, which names `container` because that is the profile whose verdict
# changed. Unscoped stayed at 0 — the invariant this file actually holds: the entry opens
# with the same `orchestrated` scoping paragraph 0.9.2 and 0.9.3 do, placed BEFORE the
# first claim, which is where the scan needs it.
# Moved 207 -> 209 on 2026-09-06 (phase 10 of the sixth-audit response), same debt paid.
# CHANGELOG.md went 58 -> 60 and every other document is byte-for-byte the same count. The
# two new claims are the SAME two-sentence pair the 0.9.2, 0.9.3 and 0.9.4 entries each
# carry verbatim — "The only direct surface is read-only `status`, which dispatches
# nothing and has no containment claim to make." — diffed by TEXT rather than by position,
# because a claim tuple starts with a line number and every line in this file shifted when
# the entry was inserted. Unscoped stayed at 0: the entry opens with the same
# `orchestrated` scoping paragraph its three predecessors do, placed BEFORE the first
# claim, which is where the scan needs it. Nothing in the 0.9.5 entry makes a NEW
# containment claim; the phases it describes are about the journal, the dispatchers and
# the metrics, none of which widen what a profile promises.
PRE_RUN_CLAIMS = 209
PRE_RUN_UNSCOPED = 0
PRE_RUN_OFFENDERS = 0
# 2 -> 0 in the same phase, and DOWNWARD, which is the direction that needs no defence:
# both negated imperatives lived in role instructions ("Never edit the `refusal:` line",
# and its twin) that a wrapper no longer carries, because a wrapper does not execute a
# role. The rule they expressed did not go with them — it is in the role cards and in the
# prompts the dispatched child reads, and `tests/test_doc_drift.py` checks it there.
PRE_RUN_NEGATED = 0

ASSERT_RE = re.compile(r"\bself\.assert(\w+)\(")


def src_of(module):
    return (REPO / "tests" / f"{module}.py").read_text(encoding="utf-8")


def hits_in(text):
    """{needle key: count} for one file's source. Pure, so the counterfactuals below can
    hand it planted text instead of writing into the repository."""
    return {key: text.count(needle) for key, needle in NEEDLES.items()
            if text.count(needle)}


def scan(modules):
    """{(module, needle key): count} over the named modules, read from the repository."""
    out = {}
    for m in modules:
        for key, n in hits_in(src_of(m)).items():
            out[(m, key)] = n
    return out


def run_unittest(target):
    return subprocess.run([sys.executable, "-m", "unittest", "-v", target],
                          capture_output=True, text=True, cwd=str(REPO))


class TheFourGatesStillPass(unittest.TestCase):
    """Criterion 1 — each gate named, each run, each result printed."""

    def test_each_gate_passes_on_the_finished_tree(self):
        results = {}
        for m in GATES:
            p = run_unittest(f"tests.{m}")
            ran = re.search(r"^Ran (\d+) tests? in ([\d.]+)s", p.stderr, re.M)
            ok = p.returncode == 0 and "\nOK" in p.stderr
            results[m] = (ok, ran.group(1) if ran else "?", p.returncode)
        print("\n  THE FOUR GATES, ON THE FINISHED TREE (block in all six skills)")
        for m, (ok, n, rc) in results.items():
            print(f"    {m:28} {'PASS' if ok else 'FAIL'}  {n:>4} tests  rc={rc}")
        failed = [m for m, (ok, _, _) in results.items() if not ok]
        self.assertEqual([], failed, f"gates failing on the finished tree: {failed}")
        # A gate that ran zero tests is not a gate that passed.
        self.assertTrue(all(int(n) > 0 for _, n, _ in results.values()),
                        "a gate reported zero tests, which is not a pass")


class NoExclusionWasAdded(unittest.TestCase):
    """Criterion 2 — flat absence, with the split needle visible in the output."""

    def test_the_needles_are_split_so_this_file_is_scannable(self):
        """The premise of including this file in its own scan. Asserted rather than
        assumed: if a needle were ever written contiguously here, every later assertion
        in this class would start reporting this file's own source as a violation, and
        the obvious fix — exempting the file — is the defect being guarded against."""
        mine = pathlib.Path(__file__).read_text(encoding="utf-8")
        # Strip the lines that BUILD the needles; what remains must contain none of them.
        body = "\n".join(ln for ln in mine.splitlines()
                         if not ln.startswith(("_S =", "_E =", "_M =", "_K =")))
        print("\n  SPLIT NEEDLES (halves joined at import, never contiguous in source)")
        for key, needle in NEEDLES.items():
            print(f"    {key:20} -> {needle!r}")
            self.assertNotIn(needle, body,
                             f"{key} appears contiguously in this file's own source")

    def test_no_gate_mentions_the_style_material_at_all(self):
        found = scan(SCANNED)
        absolute_hits = {k: v for k, v in found.items() if k[1] in ABSOLUTE}
        print(f"\n  NO-EXCLUSION SCAN: {len(SCANNED)} file(s) "
              f"({', '.join(SCANNED)}), {len(NEEDLES)} needle(s)")
        print(f"    absolute needles ({', '.join(ABSOLUTE)}): "
              f"{len(absolute_hits)} occurrence(s) — required 0")
        for (m, key), n in sorted(absolute_hits.items()):
            print(f"      HIT {m}: {key} x{n}")
        self.assertEqual({}, absolute_hits,
                         "a gate names the style material — an exclusion for the new "
                         "block has to name the block, its source or its markers, and "
                         "there is no fifth way to spell it")
        # No absolute needle may be pre-declared either: the declaration mechanism must
        # not be usable to launder the very hit it exists to make visible.
        self.assertEqual([], [k for k in DECLARED if k[1] in ABSOLUTE],
                         "an absolute needle was given a declared exception")

    def test_every_remaining_occurrence_is_declared_with_its_count_and_reason(self):
        found = scan(SCANNED)
        print(f"\n  DECLARED EXCEPTIONS: {sum(found.values())} occurrence(s) over "
              f"{len(found)} (module, needle) pair(s)")
        for (m, key), n in sorted(found.items()):
            reason = DECLARED.get((m, key), (None, "UNDECLARED"))[1]
            print(f"    {m:28} {key:18} x{n}  — {reason}")
        undeclared = sorted(k for k in found if k not in DECLARED)
        self.assertEqual([], undeclared,
                         f"undeclared occurrences: {undeclared} — a new mention of the "
                         f"scanned material appeared and nobody said why")
        stale = sorted(k for k in DECLARED if k not in found)
        self.assertEqual([], stale,
                         f"declared exceptions with no matching occurrence: {stale} — "
                         f"a declaration list that outlives its reasons is a list that "
                         f"will one day cover something else")
        wrong = {k: (DECLARED[k][0], found[k]) for k in found
                 if DECLARED[k][0] != found[k]}
        self.assertEqual({}, wrong,
                         f"declared count != actual, {{key: (declared, actual)}}: {wrong} "
                         f"— a pair that is already declared must not be able to absorb "
                         f"a new occurrence silently")


class ThisCheckItselfBites(unittest.TestCase):
    """The checks above are new. Each arm below plants the defect its check exists to
    catch, in memory, and asserts the check reddens — with the control in the same method.

    Without these, "0 absolute occurrences" and "0 undeclared" are equally consistent with
    a scan that reads nothing, matches nothing, or was handed the wrong files.
    """

    def test_a_planted_exclusion_line_is_caught_by_the_absolute_check(self):
        clean = src_of("test_launch_surfaces")
        control = {k: v for k, v in hits_in(clean).items() if k in ABSOLUTE}
        self.assertEqual({}, control,
                         "CONTROL FAILED: the unmodified gate already trips the absolute "
                         "check, so the plant below would prove nothing")

        # Exactly the shape an exclusion takes: a skip list naming the new material.
        planted = clean.replace(
            "SHIPPED_DOCS = [",
            "EXCLUDE = [" + repr(_S + "/" + _E + ".md") + ", "
            + repr(_M + "begin -->") + "]\nSHIPPED_DOCS = [", 1)
        self.assertNotEqual(clean, planted, "the plant did not plant anything")

        red = {k: v for k, v in hits_in(planted).items() if k in ABSOLUTE}
        print("\n  COUNTERFACTUAL — an exclusion list planted in a gate")
        print(f"    control: {control or 'no absolute needle'}   "
              f"planted: {red}")
        self.assertTrue(red, "an exclusion naming the style file and a marker walked "
                             "past the absolute check")
        self.assertIn("style-dir", red)
        self.assertIn("begin-marker", red)

    def test_a_new_undeclared_occurrence_is_caught(self):
        """The declaration list must not be able to absorb a new hit silently — neither a
        new (module, needle) pair nor one more occurrence of an already-declared pair."""
        m = "test_doc_contract"
        clean = src_of(m)
        self.assertEqual({("test_doc_contract", "skill:auditor"): 1},
                         {(m, k): v for k, v in hits_in(clean).items()},
                         "CONTROL FAILED: the unmodified module no longer matches its "
                         "declaration, so both plants below would be ambiguous")

        new_pair = clean + f'\n# touches {_K}triage\n'
        one_more = clean + f'\n# touches {_K}audit again\n'
        for label, text, key, expect in (
                ("a needle this module never mentioned", new_pair, "skill:triager", 1),
                ("one more of an already-declared needle", one_more, "skill:auditor", 2)):
            got = hits_in(text).get(key)
            declared = DECLARED.get((m, key))
            caught = (declared is None) or (declared[0] != got)
            print(f"    plant [{label}]: {key} count {got}, declared "
                  f"{declared[0] if declared else 'not declared'} -> "
                  f"{'caught' if caught else 'MISSED'}")
            self.assertEqual(expect, got, f"{label}: the plant did not plant")
            self.assertTrue(caught, f"{label} walked past the declaration check")

    def test_the_counterfactual_runner_reports_a_bogus_target_as_a_failure(self):
        """The positive control for criterion 3's harness. Every row there is 'pass',
        and a runner that reports 'pass' for a test that does not exist would print the
        same table. Watched here so the table above means what it says."""
        real = run_unittest(f"tests.{GATES[1]}."
                            f"NoContainmentClaimEscapesItsMode."
                            f"test_the_scanner_would_catch_an_unscoped_claim")
        self.assertEqual(0, real.returncode, "CONTROL FAILED: a real target did not pass")
        self.assertTrue(re.search(r"^Ran 1 test in", real.stderr, re.M),
                        f"CONTROL FAILED: a real target did not report one test\n"
                        f"{real.stderr}")

        bogus = run_unittest(f"tests.{GATES[1]}.NoSuchClass.test_no_such_method")
        ran = re.search(r"^Ran (\d+) tests? in", bogus.stderr, re.M)
        n = int(ran.group(1)) if ran else 0
        print(f"    harness control: real target rc={real.returncode}, "
              f"bogus target rc={bogus.returncode} ran={n}")
        # `n` is 1 for BOTH: unittest wraps an unresolvable dotted path in a synthetic
        # `_FailedTest` and counts it, so "ran exactly one test" says nothing about
        # whether that test exists. The return code is the whole guard, and the
        # counterfactual table above is sound because its predicate ANDs on rc == 0.
        self.assertEqual(1, n, "a bogus target no longer reports one test — if it now "
                              "reports zero, the rows above need a different guard")
        self.assertNotEqual(0, bogus.returncode,
                            "a nonexistent test target exited 0, so every row in the "
                            "counterfactual table is unproven")
        self.assertFalse(bogus.returncode == 0 and n == 1,
                         "a nonexistent test target satisfied the same predicate as a "
                         "real one, so every row in the counterfactual table is unproven")


class EachGateStillCatchesItsOwnDefect(unittest.TestCase):
    """Criterion 3 — the originals, re-run against the finished tree, named one by one."""

    def test_every_original_counterfactual_is_red_on_its_plant(self):
        rows, failed = [], []
        for module, target, what in COUNTERFACTUALS:
            p = run_unittest(f"tests.{module}.{target}")
            ran = re.search(r"^Ran (\d+) tests? in", p.stderr, re.M)
            n = int(ran.group(1)) if ran else 0
            ok = p.returncode == 0 and n == 1
            rows.append((module, target, what, ok, n))
            if not ok:
                failed.append(f"{module}.{target} (rc={p.returncode}, ran={n})\n"
                              f"{p.stderr[-1500:]}")
        print(f"\n  ORIGINAL COUNTERFACTUALS RE-RUN: {len(rows)}, each observed on its "
              f"own plant")
        last = None
        for module, target, what, ok, n in rows:
            if module != last:
                print(f"    {module}")
                last = module
            print(f"      [{'red on plant' if ok else 'FAILED'}] "
                  f"{target.split('.')[-1]}")
            print(f"          plants: {what}")
        self.assertEqual([], failed, "\n\n".join(failed))
        # Each target must resolve to exactly one test. A mistyped dotted path runs zero
        # tests and unittest exits 0 for it on older behaviour — that reads as a pass.
        self.assertTrue(all(n == 1 for *_, n in rows),
                        f"a counterfactual target did not resolve to exactly one test: "
                        f"{[(t, n) for _, t, _, _, n in rows if n != 1]}")
        self.assertEqual(11, len(rows), "the counterfactual list changed size")


class TheGatesMeasureTheSameThingTheyMeasuredBefore(unittest.TestCase):
    """Criteria 4, 5 and 6 — the numbers, beside the figures recorded before the run."""

    def test_the_claim_total_is_unchanged(self):
        total = unscoped = 0
        per = []
        for rel in ls.SHIPPED_DOCS:
            p = REPO / rel
            self.assertTrue(p.exists(), f"a scanned document vanished: {rel}")
            claims = ls.scan_claims(p.read_text(encoding="utf-8"))
            u = sum(1 for _, scope, _ in claims if not scope)
            per.append((rel, len(claims), u))
            total += len(claims)
            unscoped += u
        print(f"\n  CONTAINMENT CLAIMS over {len(ls.SHIPPED_DOCS)} shipped document(s)")
        for rel, n, u in per:
            print(f"    {rel:34} {n:3} claim(s)  {u} unscoped")
        print(f"    {'TOTAL':34} {total:3}          {unscoped} unscoped"
              f"   (pre-run: {PRE_RUN_CLAIMS} / {PRE_RUN_UNSCOPED})")
        self.assertEqual(PRE_RUN_UNSCOPED, unscoped,
                         "a containment claim lost its mode scope")
        self.assertEqual(PRE_RUN_CLAIMS, total,
                         f"the claim total moved from {PRE_RUN_CLAIMS} to {total}. The "
                         f"block is pinned at zero claims, so a different number means "
                         f"it is making one after all — which is a failure, not a new "
                         f"baseline. Fix the wording of the block.")

    def test_the_offender_count_is_zero_and_the_negated_count_is_unchanged(self):
        fields = ec.state_field_names()
        offenders, negated = [], []
        for f in sorted((REPO / "skills").glob("*/SKILL.md")):
            text = f.read_text(encoding="utf-8")
            lines = text.splitlines()
            lo = next((i for i, ln in enumerate(lines, 1)
                       if NEEDLES["begin-marker"] in ln), None)
            hi = next((i for i, ln in enumerate(lines, 1)
                       if NEEDLES["end-marker"] in ln), None)
            self.assertTrue(lo and hi, f"{f.parent.name} carries no style block")
            off, neg = ec.imperatives_naming_state(text, fields)
            offenders += [(f.parent.name, s) for s in off]
            # Every negated hit must sit OUTSIDE the block. A count alone cannot tell
            # "the two that were always there" from "two new ones the block added".
            for s in neg:
                at = next((i for i, ln in enumerate(lines, 1) if s[:40] in ln), None)
                negated.append((f.parent.name, at, lo <= (at or 0) <= hi, s))
        print(f"\n  IMPERATIVES NAMING STATE, six skill(s), {len(fields)} field name(s) "
              f"derived from state.py at run time")
        print(f"    offenders {len(offenders):2}  (pre-run: {PRE_RUN_OFFENDERS})")
        print(f"    negated   {len(negated):2}  (pre-run: {PRE_RUN_NEGATED})")
        for name, at, inside, s in negated:
            print(f"      {name}:{at} inside-block={inside}: {s[:78]}")
        self.assertEqual([], offenders, f"a skill orders a hand edit: {offenders}")
        self.assertEqual(PRE_RUN_NEGATED, len(negated),
                         "the negated count moved")
        self.assertEqual([], [n for n in negated if n[2]],
                         "a negated hit sits inside the style block — the block has no "
                         "business discussing machine state at all")

    def test_no_assertion_was_loosened_in_any_gate(self):
        """Pre-run and post-run, with the instrument for 'pre-run' stated.

        "Untouched" is read from the BYTES, not from the clock. It used to be
        `mtime < the run's first artifact`, and the docstring already conceded that
        mtime "is weaker than a hash". On 2026-08-17 it proved weaker than that: a bulk
        git operation rewrote the working tree and stamped all four gates 9-11 ms after
        the anchor, without altering one byte. The instrument reported four modified
        gates; `git diff --quiet` reported none. The strong evidence was already being
        computed here for the assertion table, so the check now reads it: a gate is
        untouched iff its working tree bytes equal its HEAD blob.

        Where git has no blob — an untracked module, new in a release run — bytes cannot
        be compared and the mtime test stands as the fallback, with the row saying so
        rather than inventing a number.
        """
        first = (REPO / "sty" "les" / (_E + ".md")).stat().st_mtime
        rows = []
        for m in GATES:
            path = REPO / "tests" / f"{m}.py"
            src = src_of(m)
            now = ASSERT_RE.findall(src)
            blob = subprocess.run(
                ["git", "show", f"HEAD:tests/{m}.py"],
                capture_output=True, text=True, cwd=str(REPO))
            tracked = blob.returncode == 0
            base = ASSERT_RE.findall(blob.stdout) if tracked else None
            untouched = (blob.stdout == src) if tracked else (
                path.stat().st_mtime < first)
            rows.append((m, base, now, untouched))

        print("\n  ASSERTION COUNTS PER GATE   (HEAD blob | working tree)")
        print(f"    {'module':28} {'HEAD':>6} {'now':>6}   "
              f"{'Eq':>3} {'In':>3} {'True':>4} {'Regex':>5}   untouched-by-this-run")
        for m, base, now, untouched in rows:
            kinds = {k: now.count(k) for k in ("Equal", "In", "True", "Regex")}
            print(f"    {m:28} {len(base) if base is not None else '  —':>6} "
                  f"{len(now):>6}   {kinds['Equal']:>3} {kinds['In']:>3} "
                  f"{kinds['True']:>4} {kinds['Regex']:>5}   {untouched}")
        print("    HEAD '—' = the module is untracked (new in the previous release run), "
              "so git has no blob to compare against.")

        untouched = [m for m, _, _, u in rows if not u]
        self.assertEqual([], untouched,
                         f"a gate module differs from its committed bytes: {untouched}. "
                         f"The instrument is the HEAD blob, so this names an edit that "
                         f"is really there — not a timestamp that moved. Commit the "
                         f"change if it is intended, and it becomes the new baseline; "
                         f"until then a gate is being read in a state nobody reviewed.")
        loosened = [m for m, base, now, _ in rows
                    if base is not None and len(now) < len(base)]
        self.assertEqual([], loosened,
                         f"a gate has fewer assertions than its HEAD blob: {loosened}")
        self.assertTrue(all(now for _, _, now, _ in rows),
                        "a gate reported zero assertions, which means the counter broke")


if __name__ == "__main__":
    unittest.main(verbosity=2)
