"""What lint reports, over state a consumer already loaded.

This module no longer validates machine state and no longer can: the `*_issues`
family that parsed LEDGER.md rows, finding frontmatter and construal records is
gone, and its whole subject moved into `xcheck/state.py`, where it runs at the
ONE boundary every command passes through. F-0147 was "full validation lives only
in lint, and consumers walk past it"; the fix is not a better lint, it is that
there is nothing left for a consumer to walk past.

What remains has a different subject — the HUMAN-facing tree, which Markdown
still owns:

  - the evidence-body sections a finding must carry (`## Admitted scope`,
    `## Refusal`), judged structurally through `md_prose`, never by substring;
  - the binding between XCHECK.md §2 prose and the code's canonical schema, so a
    field the methodology declares cannot exist without a validator behind it.

Both read records that came out of `load_state`, so neither can be a bypass: a
document that fails the schema never reaches this module at all.
"""


import re
from pathlib import Path

from xcheck.md_prose import (
    has_atx_heading, section2_canonical_field_values, section2_canonical_fields,
    section2_field_nullable_map, section_declared_content
)
from xcheck.util import (
    ADMITTED_SCOPE_PARTS, CANONICAL_SCHEMA, DELEGATED_VALUE_GATES,
    SCHEMA_BLOCK_FIELDS, SECTION2_ENUM_FIELDS
)


def _body_text(audit_dir: Path, record):
    """The record's human-written evidence body, or "" when the file is absent.

    A MISSING body file is not this function's finding — `cmd_lint` reports it
    once, from the record's `body_path`, rather than every prose check inventing
    its own version of "the file is not there"."""
    p = Path(audit_dir) / record.body_path
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""


def admitted_scope_issues(state, audit_dir: Path):
    """Records in status `fixed` that do not declare the scope their fix admits (§7).

    Active ONLY under §10 `scope_typing`; the caller decides whether to run it, and with
    the flag off this project's behaviour is unchanged. The scope of the check is
    deliberately narrow and stated here so the code does not promise more than it does:
    it verifies that a scope DECLARATION exists and carries both halves. Whether the
    declaration is TRUE of the fix — the demanded ⊆ admitted judgement — is the Verifier's
    work (§7); no predicate here reads the fixed code.

    Both halves are required. `### Covers` alone is the promise-width shape this whole
    mechanism exists to catch: it reads as complete while admitting nothing about its
    limits, which is the silent gap §4 rule 5 forbids one layer over. `closed` records
    are NOT checked — the 131 terminal records were closed under the old norm, and
    retroactively invalidating them would be history laundering."""
    issues = []
    for r in list(state.findings) + list(state.class_findings):
        if r.status != "fixed":
            continue
        if not (r.admitted_scope or "").strip():
            issues.append(
                f"{r.id}: status is `fixed` but `admitted_scope` is empty — §10 `scope_typing` is on,"
                f" so a fix must declare which routes/inputs/call sites its guarantee covers (§7)")
        text = _body_text(audit_dir, r)
        if not has_atx_heading(text, "## Admitted scope"):
            issues.append(
                f"{r.id}: status is `fixed` but there is no `## Admitted scope` section (§7, §10 `scope_typing`)")
            continue
        for part in ADMITTED_SCOPE_PARTS:
            if not section_declared_content(text, part):
                issues.append(
                    f"{r.id}: `## Admitted scope` is missing or empty under `{part}`"
                    + (" — the residue half is mandatory: a fix that declares coverage and no"
                       " limit reads as complete while admitting nothing (§7)"
                       if part == "### Does not cover" else " (§7)"))
    return issues


def refusal_section_issues(state, audit_dir: Path):
    """Records whose `refusal` reason code stands alone, with no `## Refusal` prose.

    XCHECK.md §5 makes the two halves one record: the state document carries the reason
    from the closed vocabulary, the section carries what is actually missing, and the
    reasons are what a successor attempt inherits. A bare code inherits nothing — it names
    a category and states no obstacle, which is the silent gap §4 rule 5 forbids one layer
    over. Emptiness is decided by the shared declared-form classifier (F-0111), so an
    HTML-comment placeholder — the exact shape a role copies out of templates/finding.md —
    counts as empty, and there is no second answer to "is this section filled?"."""
    issues = []
    for r in list(state.findings) + list(state.class_findings):
        if not (r.refusal or "").strip():
            continue
        if not section_declared_content(_body_text(audit_dir, r), "## Refusal"):
            issues.append(
                f"{r.id}: `refusal` is set but the `## Refusal` section is missing or empty"
                f" — §5 requires the reason code AND the prose stating what is missing"
                f" (a bare code carries nothing forward)")
    return issues


def _refusal_norm_issue(xcheck_text):
    """One issue string, or None, on the SELF-CONSISTENCY of the §5 `refusal` definition
    (F-0134). The typed refusal contract is per-role: the finding-frontmatter `refusal:`
    field and the finding's `## Refusal` section belong to the finding-charter roles
    (Remediator, Verifier) ONLY, and the Planner/Auditor are EXEMPTED from them because
    their charter carries no finding. F-0134 reopened three times on the same shape — a
    §5 paragraph that stated that exemption and then, in a later universal clause, re-imposed
    the finding `## Refusal` section on every refusal. The two rules cannot both hold; the
    role↔target guard over the shipped skills (elsewhere in selftest) catches a consumer
    routing its refusal to the wrong target, but NOT a norm paragraph that contradicts itself.
    This is that missing check.

    Two conditions, both required for the norm to be consistent:
      1. the exemption is present — the finding-frontmatter field is stated NOT to apply to
         the non-finding roles; and
      2. no clause demands the finding `## Refusal` section OUTSIDE a clause scoped to a
         finding-charter role. A demand for the finding target in a clause that names neither
         the Remediator nor the Verifier is the universal re-mandate that contradicts (1).
    A mutation that re-adds the old universal closing sentence reddens on condition 2; one
    that drops the exemption reddens on condition 1."""
    m = re.search(r"(?ms)^- \*\*`refusal`\*\*.*?(?=^- \*\*`blocked)", xcheck_text)
    if not m:
        return "the §5 `refusal` definition bullet is missing or not in its canonical form (§5, F-0134)"
    block = m.group(0)
    if "does not apply to it" not in block:
        return ("the §5 refusal definition no longer EXEMPTS the non-finding roles"
                " (Planner/Auditor) from the finding-frontmatter target (§5, F-0134)")
    for clause in re.split(r"(?<=[.;])\s+", block):
        if "## Refusal` section" in clause and not re.search(r"Remediator|Verifier", clause):
            return ("the §5 refusal definition demands the finding `## Refusal` section in a"
                    " clause not scoped to a finding-charter role (Remediator/Verifier) — a"
                    " universal re-mandate that contradicts the Planner/Auditor exemption above,"
                    " the self-contradiction that reopened F-0134 three times (§5)")
    return None

def _schema_meta_diff(section2_fields, schema_fields=None):
    """The bijection check between the §2 canonical-record fields and the schema
    table's in-block fields (CF-0001 re-take, meta-check). Returns a list of issues:
    a §2 field with no schema entry, a schema entry §2 no longer declares, or a
    duplicated §2 field. Empty list == the mirror holds. `schema_fields` defaults to
    the live SCHEMA_BLOCK_FIELDS; the selftest passes a mutated set to prove that
    removing a table entry (or adding a §2 field) breaks the check (§7 adversarial)."""
    got_list = list(section2_fields)
    got = set(got_list)
    want = set(SCHEMA_BLOCK_FIELDS if schema_fields is None else schema_fields)
    issues = []
    for f in sorted(got - want):
        issues.append(f"canonical field '{f}' is declared in XCHECK.md §2 but has NO entry in the code schema table — add a validator (CF-0001 meta-check: §2 drives the validator)")
    for f in sorted(want - got):
        issues.append(f"schema table validates '{f}' but XCHECK.md §2 no longer declares it — restore §2 or drop the entry (CF-0001 meta-check)")
    if len(got_list) != len(got):
        dups = sorted({f for f in got_list if got_list.count(f) > 1})
        issues.append(f"XCHECK.md §2 declares duplicate canonical field(s): {', '.join(dups)}")
    return issues

def _schema_value_coverage_issues(section2_values, schema=None, delegated=None):
    """I-10 (CF-0001 reopen): name bijection (`_schema_meta_diff`) is NOT enough — a field
    can sit in the table with a bare-None predicate and still be read past validation, as
    `blocked` was. This binds the REQUIREMENT of a value guard to §2: for every in-block
    §2 field whose declared value is NOT a `<free-form>` placeholder, the schema entry MUST
    carry either a value predicate OR an entry in DELEGATED_VALUE_GATES (a declared,
    selftest-proven fail-closed gate). A constrained field with neither — or one that is
    BOTH — is flagged with the field name. Coverage is DERIVED from §2 values, never a
    hand list, so a future constrained field cannot repeat `blocked`. `schema`/`delegated`
    default to the live tables; the selftest passes mutated ones to prove the guard bites
    (§7 adversarial)."""
    schema = CANONICAL_SCHEMA if schema is None else schema
    delegated = DELEGATED_VALUE_GATES if delegated is None else delegated
    issues = []
    for field, val in section2_values.items():
        spec = schema.get(field)
        if not spec or not spec[0]:
            continue  # a §2<->table name mismatch is already reported by _schema_meta_diff
        v = (val or "").strip()
        if re.fullmatch(r"<.+>", v):
            continue  # a `<free-form>` placeholder value has no closed constraint to guard
        predicated = spec[2] is not None
        is_delegated = field in delegated
        if not predicated and not is_delegated:
            issues.append(f"canonical field '{field}' is value-constrained in XCHECK.md §2 (value '{v}') but has NO value predicate in CANONICAL_SCHEMA and is not a declared fail-closed delegation — a constrained canonical field read past validation is the CF-0001 class root (add a predicate or a DELEGATED_VALUE_GATES entry)")
        elif predicated and is_delegated:
            issues.append(f"canonical field '{field}' has BOTH a value predicate and a DELEGATED_VALUE_GATES entry — one validation source only (CF-0001 meta-check)")
    return issues

def _schema_enum_binding_issues(section2_values, enum_fields=None):
    """Bind every §2-declared closed enumeration to its module constant. A §2 value that
    offers alternatives (`a | b | c`) IS the norm's statement of the vocabulary; the code
    validates against a set. If the two differ, one of them is silently wider than the
    other — a member the norm allows but the predicate rejects, or a member the predicate
    accepts that the norm never declared. Both are promise-width defects (§7), and both
    are invisible to the name-level meta-check, which only proves the FIELD exists in
    both places. `enum_fields` defaults to the live map; the selftest passes a mutated one
    to prove the guard bites."""
    enum_fields = SECTION2_ENUM_FIELDS if enum_fields is None else enum_fields
    issues = []
    for field, vocab in enum_fields.items():
        raw = section2_values.get(field)
        if raw is None:
            continue  # a §2<->table name mismatch is already reported by _schema_meta_diff
        declared = {t.strip() for t in raw.split("|")} - {"null", ""}
        if declared != set(vocab):
            missing = sorted(set(vocab) - declared)
            extra = sorted(declared - set(vocab))
            issues.append(
                f"canonical field '{field}': the XCHECK.md §2 enumeration and the code vocabulary disagree"
                f" (§2 lacks {missing}, code lacks {extra}) — one closed vocabulary, declared once (CF-0001 meta-check)")
    return issues

def canonical_schema_nullability_issues(section2_nullable, schema=None):
    """CF-0001 human ruling #4: a canonical field may not be BOTH required and nullable. §2 is
    the authority on which fields are required (`section2_field_nullable_map`); the schema's
    2nd element is the operational nullable flag the shared gate enforces. This binds the two:
    for every in-block field, the schema flag MUST equal §2's. A §2-required field the schema
    marks nullable is the exact contradiction the ruling forbids — an empty value skips the
    shared gate and every consumer certifies the corrupt record (missing `pass`/`updated`,
    empty `title`/`severity`). Derived from §2, so a future field cannot repeat it. `schema`
    defaults to the live table; the selftest passes a mutated one to prove the guard bites
    (ruling #4 point 4: flipping a required field to nullable must fail HERE, not only its
    poison)."""
    schema = CANONICAL_SCHEMA if schema is None else schema
    issues = []
    for field, s2_nullable in section2_nullable.items():
        spec = schema.get(field)
        if not spec or not spec[0]:
            continue  # a §2<->table name mismatch is already reported by _schema_meta_diff
        schema_nullable = spec[1]
        if schema_nullable and not s2_nullable:
            issues.append(f"canonical field '{field}' is REQUIRED by XCHECK.md §2 (its shown value offers no `null` option) but the schema marks it nullable — 'required and nullable' is self-contradictory, and an empty value would skip the shared gate on every consumer (CF-0001 human rulings #4/#5). Make the schema entry non-nullable, or declare §2 nullability explicitly")
        elif s2_nullable and not schema_nullable:
            issues.append(f"canonical field '{field}' is nullable per XCHECK.md §2 (its shown value offers a `null` option) but the schema marks it non-nullable — a legitimate empty value would be rejected (CF-0001 human rulings #4/#5)")
    return issues

def canonical_schema_meta_issues(xcheck_path: Path):
    """Bind the code's CANONICAL_SCHEMA to the §2 prose in `xcheck_path` (the project's
    audit/XCHECK.md). This is what makes 'adding a field to §2 without a validator'
    impossible — exactly the technique that finally closed F-0077 after seven rounds
    (CF-0001 re-take, human ruling point 3) — and, after the I-10 reopen, 'keeping a field
    in §2 with no working value guard' too (`_schema_value_coverage_issues`), and, after the
    ruling-#4 reopen, 'declaring a §2-required field nullable' too
    (`canonical_schema_nullability_issues`). Skips silently only when there is nothing
    claiming to be the binding's input: the file is absent, or it carries no `## 2.`
    section at all (a degenerate/placeholder copy, e.g. a fixture stub). A file whose §2
    SECTION exists but whose canonical-record yaml block is unparseable is FAIL-CLOSED
    (F-0147): the schema binding's own input has been lost, which is drift of the worst
    kind — the earlier silent [] let a corrupted §2 block turn every mirror check off
    while lint reported clean."""
    if not xcheck_path.is_file():
        return []
    text = xcheck_path.read_text(encoding="utf-8", errors="replace")
    fields = section2_canonical_fields(text)
    if fields is None:
        if re.search(r"(?m)^##\s*2\.", text):
            return [f"{xcheck_path.name}: §2 exists but carries no parseable canonical-record yaml block — the schema binding has no input, so none of the §2 mirror checks can run; a lost block is drift, not a clean pass (F-0147)"]
        return []
    issues = _schema_meta_diff(fields)
    issues += _schema_value_coverage_issues(section2_canonical_field_values(text) or {})
    issues += canonical_schema_nullability_issues(section2_field_nullable_map(text) or {})
    issues += _schema_enum_binding_issues(section2_canonical_field_values(text) or {})
    return issues
