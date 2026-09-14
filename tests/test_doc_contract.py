"""Phase 4's structural doc contract: what the methodology TELLS an agent to write
must be a thing the code will accept.

`test_doc_drift.py` already checks that the set of documented verb *names* equals
the set of implemented ones. That is necessary and not sufficient: a document can
name every verb correctly in §2.1 and still tell the Auditor, three sections
later, to add a row to the ledger. The verb list is green; the agent hand-edits a
generated mirror; the edit records nothing and the next command refuses the tree.

So this file checks the *instructions*, not the vocabulary, and it does it by
parsing shape rather than by matching phrases:

- Every §3 role card splits its write surface into `Writes (evidence)` — files the
  role authors with its own prose — and `Writes (record)` — a bullet list whose
  every item BEGINS with a backticked name that must resolve in `write.VERBS`.
  That construction is what makes the check semantic: an instruction to write
  machine state some other way cannot be phrased so as to pass, because there is
  no wording of "update the tally sheet" that is a verb name. `tests/fixtures/
  counterfactual-role-card.md` is the committed proof — a forbidden instruction
  written in words this file never mentions, still caught.
- The same parser reads §4, whose rules have no list construction to lean on. Its
  check is therefore weaker by nature: an artifact-vocabulary sweep (the ledger
  filename and the §2 frontmatter field names, both read at runtime, never copied)
  plus one positive assertion that rule 4 hands a charter remainder to a verb.
- The skills get the narrowest check of the three, and it is stated plainly rather
  than dressed up: a line that names the plan prose AND a pass id, without naming
  the state document, is reading the queue from the wrong place.

`verifiable-claims-check-structure-not-self-counts`: nothing here counts
occurrences of anything. A count is invalidated by the next edit; a shape is not.
"""

import re
import unittest

from tests.harness import REPO, xcheck_submodule

write = xcheck_submodule("write")
views = xcheck_submodule("views")
md_prose = xcheck_submodule("md_prose")

XCHECK_MD = (REPO / "XCHECK.md").read_text(encoding="utf-8")
FIXTURE = REPO / "tests" / "fixtures" / "counterfactual-role-card.md"

# The plan prose. It is a legitimate READ for every role — the norms and the unit
# map live there — so it is forbidden only inside a record surface, where naming a
# file at all means writing to it.
PLAN_PROSE = "AUDIT.md"

# A record item: an indented bullet whose first token is a backticked name. The
# `?` on the backtick group is deliberate — an item with no backticked head still
# parses, so the test can REPORT it rather than silently skipping it.
ITEM = re.compile(r"^\s+- (?:`([a-z][a-z-]*)`)?(.*)$")
PASS_ID = re.compile(r"\bP-(?:\d\d?|NN)\b")


def section(text, heading):
    """The body of one `## N. Title` section, up to the next `## `."""
    start = text.index(heading)
    rest = text[start + len(heading):]
    nxt = rest.index("\n## ") if "\n## " in rest else len(rest)
    return rest[:nxt]


def role_cards(text):
    """`### Role` -> the card's lines, for every card in §3."""
    body = section(text, "\n## 3. Roles")
    cards, name = {}, None
    for line in body.splitlines():
        if line.startswith("### "):
            name = line[4:].strip()
            cards[name] = []
        elif name is not None:
            cards[name].append(line)
    return cards


def record_surface(card_lines):
    """(the label line, [item lines]) for a card's `Writes (record):` bullet.

    The items are the indented bullets that immediately follow the label, and the
    run ends at the first line that is not one — so the explanatory paragraph a
    card may carry underneath is prose, not a claimed write surface.
    """
    for i, line in enumerate(card_lines):
        if line.startswith("- **Writes (record):**"):
            items = []
            for nxt in card_lines[i + 1:]:
                if ITEM.match(nxt) and not nxt.startswith("- "):
                    items.append(nxt)
                elif nxt.strip() == "" and not items:
                    continue
                else:
                    break
            return line, items
    return None, []


def forbidden_names():
    """The artifacts a record surface may never name, derived at runtime.

    Both sources are authorities, not copies: `views.LEDGER_FILENAME` is what the
    renderer writes, and the field list is parsed out of §2's own frontmatter
    sample. Add a field to the sample and this check covers it with no edit here.
    """
    return {views.LEDGER_FILENAME, PLAN_PROSE} | set(
        md_prose.section2_canonical_fields(XCHECK_MD))


def names_a_generated_view(text, names):
    """Which forbidden artifacts this text names.

    Field names match only as a backticked token on its own (`status`) or with the
    YAML colon (`status:`) — the bare English words "status", "class" and "pass"
    are unavoidable in prose about a lifecycle, and flagging them would make this
    check noise. `--recurrence-of` is a flag, not the field, and does not match.
    """
    hits = set()
    ticked = set(re.findall(r"`([^`]+)`", text))
    for n in names:
        if n.endswith(".md"):
            if n in text:
                hits.add(n)
        elif n in ticked or f"{n}:" in ticked or f"`{n}:`" in text:
            hits.add(n)
    return hits


def queue_read_offenders(text, label):
    """Lines that take a queue entry from the plan prose instead of the record."""
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if PLAN_PROSE in line and PASS_ID.search(line) and "state.json" not in line:
            out.append(f"{label}:{i}: {line.strip()[:120]}")
    return out


class EveryRoleCardSplitsItsWriteSurface(unittest.TestCase):
    """Criterion 1. The split is not cosmetic — it is the handle every other check
    in this file grabs. A card that keeps one undifferentiated `Writes:` line gives
    the parser a sentence to interpret, and interpreting sentences is exactly what
    let the stale instructions survive a green CI."""

    def setUp(self):
        self.cards = role_cards(XCHECK_MD)

    def test_section_3_has_role_cards_at_all(self):
        """A parser that finds nothing passes every assertion below it. This is the
        floor that stops a silent no-op: the count is not asserted (the roster is
        the document's business, not this test's), only that it is non-empty."""
        self.assertTrue(self.cards, "§3 parsed to zero role cards — the parser is "
                                    "broken, or the section was restructured")
        print(f"\n§3 role cards: {', '.join(self.cards)}")

    def test_each_card_labels_evidence_and_record_separately(self):
        missing = {}
        for name, lines in self.cards.items():
            gap = [label for label in ("- **Writes (evidence):**",
                                       "- **Writes (record):**")
                   if not any(l.startswith(label) for l in lines)]
            if gap:
                missing[name] = gap
        self.assertEqual(missing, {}, f"role cards without a split write surface: "
                                      f"{missing}")

    def test_every_record_item_resolves_to_a_verb(self):
        """Criterion 1's teeth, and the whole reason for the bullet-list shape."""
        unresolved = []
        for name, lines in self.cards.items():
            _, items = record_surface(lines)
            print(f"\n{name} record surface ({len(items)} item(s)):")
            for raw in items:
                verb, rest = ITEM.match(raw).groups()
                ok = verb in write.VERBS
                print(f"  {'OK ' if ok else 'BAD'} {verb or '<no verb>'}"
                      f" — {rest.strip().lstrip('— ')[:70]}")
                if not ok:
                    unresolved.append(f"{name}: {raw.strip()[:100]}")
        self.assertEqual(unresolved, [],
                         "a record surface names something that is not a write "
                         "verb — an agent obeying it would hand-edit state:\n  "
                         + "\n  ".join(unresolved))

    def test_no_record_surface_names_a_generated_view(self):
        """Criterion 2. Redundant with the check above by construction, and kept
        anyway: it names the failure in the reviewer's vocabulary rather than
        making them work out why `LEDGER.md` is not a verb."""
        names = forbidden_names()
        print(f"\nforbidden inside a record surface: {', '.join(sorted(names))}")
        offenders = {}
        for name, lines in self.cards.items():
            label, items = record_surface(lines)
            hits = names_a_generated_view("\n".join([label or ""] + items), names)
            if hits:
                offenders[name] = sorted(hits)
        self.assertEqual(offenders, {},
                         f"a record surface names a generated view or a rendered "
                         f"field: {offenders}")


class SectionFourHandsRemaindersToAVerb(unittest.TestCase):
    """Criterion 3. §4 is parsed by the same code that parses §3 — one parser, so a
    restructure that breaks one breaks both visibly rather than half-silently."""

    def setUp(self):
        self.rules = {}
        for line in section(XCHECK_MD, "\n## 4. Session Protocol").splitlines():
            m = re.match(r"^(\d+)\. (.*)$", line)
            if m:
                self.rules[int(m.group(1))] = m.group(2)

    def test_the_rules_parsed(self):
        self.assertIn(4, self.rules, "§4 rule 4 did not parse")
        print(f"\n§4 rules parsed: {sorted(self.rules)}")

    def test_rule_4_names_the_queue_verb(self):
        self.assertIn("`xcheck queue-pass`", self.rules[4],
                      "§4 rule 4 must hand a charter remainder to the verb; the "
                      f"queue is not a document to append to: {self.rules[4][:160]}")

    def test_no_rule_names_a_generated_view(self):
        names = forbidden_names() - {PLAN_PROSE}   # reading the plan prose is legal
        offenders = {n: sorted(hits) for n, line in self.rules.items()
                     if (hits := names_a_generated_view(line, names))}
        self.assertEqual(offenders, {},
                         f"a §4 rule names a generated view or a rendered field — "
                         f"§4 binds every session, so a stale instruction here "
                         f"reaches every role: {offenders}")


class OneLimitContract(unittest.TestCase):
    """The set of limits has four surfaces — the schema, the migration reader,
    XCHECK.md §10 and README §13 — and phase 3 left them disagreeing: `set-limit`
    accepted `triage_batch_cap`, which `decision.py` reads from `orchestrator.conf`
    and never from state, so setting it moved nothing. README §13's own sentence
    ("refuses a key no consumer reads") was false about its own tool.

    The §10 rows are matched by SHAPE, not by name: a numeric default is a limit,
    an `off` default is a methodology flag that lives nowhere in state. So adding a
    flag to §10 needs no edit here, and adding a limit fails until state agrees."""

    def setUp(self):
        self.state = xcheck_submodule("state")

    def section_10_numeric_keys(self):
        keys = set()
        for line in section(XCHECK_MD, "\n## 10. Configuration Defaults").splitlines():
            if not line.startswith("| `"):
                continue
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if len(cells) >= 2 and cells[1].isdigit():
                keys.add(cells[0].strip("` "))
        return keys

    def test_the_schema_and_the_migration_reader_agree(self):
        print(f"\nLIMIT_FIELDS: {sorted(self.state.LIMIT_FIELDS)}")
        print(f"LIMIT_KEYS:   {sorted(md_prose.LIMIT_KEYS)}")
        self.assertEqual(set(self.state.LIMIT_FIELDS), set(md_prose.LIMIT_KEYS),
                         "a limit the schema accepts that the migration cannot "
                         "read (or the reverse) is a limit with two answers")

    def test_section_10s_numeric_rows_are_the_limits(self):
        documented = self.section_10_numeric_keys()
        print(f"XCHECK.md §10 numeric rows: {sorted(documented)}")
        self.assertEqual(documented, set(self.state.LIMIT_FIELDS))

    def test_the_orchestrator_key_is_not_a_state_limit(self):
        """The specific regression, named so it cannot come back by accident."""
        util = xcheck_submodule("util")
        self.assertIn("triage_batch_cap", util.CONF_DEFAULTS)
        self.assertNotIn("triage_batch_cap", self.state.LIMIT_FIELDS)


class NoSkillReadsTheQueueFromThePlanProse(unittest.TestCase):
    """Criterion 5. The narrowest check here, and deliberately so: a skill has no
    list construction to parse, so this keys on the one thing a queue instruction
    cannot avoid — a pass id — and asks where the line says it comes from."""

    def test_no_skill_takes_a_pass_id_from_the_plan_prose(self):
        offenders = []
        for d in sorted((REPO / "skills").iterdir()):
            f = d / "SKILL.md"
            if f.is_file():
                offenders += queue_read_offenders(f.read_text(encoding="utf-8"),
                                                  f"skills/{d.name}/SKILL.md")
        self.assertEqual(offenders, [],
                         "a skill reads the queue from the plan prose, which the "
                         "migration froze:\n  " + "\n  ".join(offenders))

    def test_the_auditor_wrapper_names_where_its_charter_comes_from(self):
        """The positive half. Forbidding the wrong source without naming the right one
        leaves the agent with a prohibition and no route.

        PHASE 4: the launcher no longer picks a charter — it has none of its own, and the
        orchestrator builds one from `audit/state.json`. So the route to name is no longer
        an auto-pick line; it is the hand-off and the state document behind it. The
        prohibition this pairs with (`test_no_skill_takes_a_pass_id_from_the_plan_prose`)
        is unchanged and still runs over every skill."""
        text = (REPO / "skills" / "xcheck-audit" / "SKILL.md").read_text(
            encoding="utf-8")
        self.assertRegex(text, r"Run `xcheck next`",
                         "the Auditor wrapper names no hand-off, so it names no charter "
                         "route either")
        self.assertRegex(
            text, r"charter from `audit/state\.json`",
            "the wrapper does not say where the charter comes from — an agent told only "
            "that it may not read AUDIT.md has a prohibition and no source")
        self.assertNotIn("Charter auto-pick", text,
                         "a wrapper that picks its own charter is not a wrapper")


class TheCheckIsSemanticNotPhraseBased(unittest.TestCase):
    """Criterion 6 — the counterfactual. The fixture states two forbidden things in
    vocabulary chosen so that no string literal in this module contains any of its
    nouns. If these tests were phrase matchers, it would pass clean."""

    def setUp(self):
        self.text = FIXTURE.read_text(encoding="utf-8")

    def test_the_prose_record_surface_is_caught(self):
        card = role_cards(self.text)["Adjudicator"]
        _, items = record_surface(card)
        self.assertTrue(items, "the fixture's record surface did not parse")
        bad = [raw for raw in items if ITEM.match(raw).group(1) not in write.VERBS]
        print(f"\ncounterfactual record items caught ({len(bad)} of {len(items)}):")
        for raw in bad:
            print(f"  {raw.strip()[:100]}")
        self.assertEqual(len(bad), len(items),
                         "the fixture's record surface names no verb at all, so "
                         "every item must be reported")

    def test_the_queue_read_is_caught(self):
        caught = queue_read_offenders(self.text, "fixture")
        print(f"counterfactual queue read caught: {caught}")
        self.assertTrue(caught, "the fixture's hand-written queue entry slipped "
                                "past the queue-source check")


if __name__ == "__main__":
    unittest.main()
