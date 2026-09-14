"""LEGACY Markdown readers — READ-ONLY BY CONTRACT, for `xcheck migrate` alone.

This module is what the audit tree looked like before `state.json`: a ledger
table, a queue of checkbox lines, limits declared in a Markdown section, machine
state in YAML-ish frontmatter. Every reader here is kept for exactly one job —
phase 6's one-shot converter, which reads a legacy tree once and writes a state
document — and for the migration tests that prove the conversion is faithful.

**No consumer may import this module.** `status`, `next`, `loop`, `lint`,
`metrics`, the courier and `decide` all obtain machine state through
`xcheck.state.load_state` and nothing else; `ci/check-split.py` and
`tests/test_no_bypass.py` both enforce that. The reason is not tidiness: a second
reader is a second answer to "what is the state", and "lint knows but next does
not" (F-0147) is exactly what having two answers produced. A fallback path back
to these readers would reinstate the defect this package exists to remove — so
when `state.json` is absent the answer is an addressed refusal naming
`xcheck migrate`, never a quiet reparse of the Markdown.

Nothing here is maintained against new methodology. It reads the historical form,
warts and all, so the converter can carry a real tree forward.
"""

import re
from pathlib import Path

from xcheck.md_prose import (
    FRONTMATTER_RE, LIMIT_KEYS, _DIMENSIONS_RE, _atx_indent_ok, _declared_form_content,
    _is_delimiter_row, _norm_cell, _parse_frontmatter, _pipe_cells, _section_table,
    _strip_nonstructure
)
from xcheck.util import (
    LEDGER_COLUMNS, _canon_calendar_date, _read_audit_text,
    pending_triage_delta
)

def load_construal_files(audit_dir: Path):
    """Every audit/construals/*.md with its own parsed frontmatter (one dict per physical
    file, each carrying `_file`), mirroring `load_finding_files`. A file with no
    frontmatter block is recorded as `_malformed` rather than skipped, so lint reports it
    instead of certifying a silent pass over it (F-0013 shape)."""
    out = []
    cdir = audit_dir / "construals"
    if not cdir.is_dir():
        return out
    for f in sorted(cdir.glob("*.md")):
        text = f.read_text(encoding="utf-8", errors="replace")
        m = FRONTMATTER_RE.match(text)  # F-0149: full-line closing delimiter, one shared form
        if not m:
            out.append({"_file": f.name, "_malformed": True, "_text": text})
            continue
        fm, dups, badform = _parse_frontmatter(m.group(1))
        fm["_file"] = f.name
        fm["_text"] = text
        if dups:
            fm["_dupkeys"] = dups
        if badform:
            fm["_badform"] = badform
        out.append(fm)
    return out

# F-0148: one capture group per LEDGER_COLUMNS entry, and the row is anchored to its
# closing pipe at end-of-line. The old five-group, unanchored form never captured
# `updated` at all (zip silently dropped the sixth name) and accepted any tail after the
# fifth pipe — a row missing the mandatory `updated` cell, or carrying hidden extra
# cells, certified as a clean fixed-format table (§2). Free cells are `[^|]*`, so a
# seventh cell can never be swallowed into `next`/`updated` by backtracking.
LEDGER_ROW_RE = re.compile(
    r"^\|\s*((?:F|CF)-\d{4})\s*\|([^|]*)\|\s*(\w+)\s*\|\s*([\w-]+)\s*\|([^|]*)\|([^|]*)\|\s*$"
)

# A single GFM table DELIMITER cell: a run of `-` with optional leading/trailing `:`
# alignment marker (`---`, `:--`, `--:`, `:-:`). A delimiter ROW is >=1 such cells and
# EXACTLY as many as the header (checked in `_section_table`), which is why the shape is
# expressed per-cell here rather than as a whole-line regex (F-0112 human ruling
# 2026-07-28, fourth ruling — declare the whole GFM table form in one place).


def _is_ledger_header(line):
    """True iff `line` is THE canonical LEDGER header: exactly the six `LEDGER_COLUMNS`
    names, in order (case-insensitive). The ONLY definition of the header shape — no prefix
    regex beside it (F-0123 human ruling #2 2026-07-28: a wide `| id | title |` prefix
    skip-filter is a second copy of the schema that drifts, F-0055; header knowledge lives in
    the same `LEDGER_COLUMNS` tuple the rows are parsed from)."""
    return [c.strip().lower() for c in _pipe_cells(line)] == list(LEDGER_COLUMNS)

def _is_ledger_delimiter(line):
    """True iff `line` is a REAL GFM delimiter row of EXACTLY the header's cell count — the
    same whole-table shape `_section_table` requires of AUDIT.md (`_is_delimiter_row` over
    `LEDGER_COLUMNS` width). A dash-less `| |` or a narrow `|---|` under a six-column header
    is NOT a delimiter (F-0123)."""
    cells = _pipe_cells(line)
    return len(cells) == len(LEDGER_COLUMNS) and _is_delimiter_row(cells)

def parse_ledger(audit_dir: Path):
    """LEDGER.md table rows -> list of dicts (id, title, severity, status, next, updated)."""
    rows = []
    ledger = audit_dir / "LEDGER.md"
    if not ledger.is_file():
        return rows
    for line in _read_audit_text(ledger).splitlines():
        m = LEDGER_ROW_RE.match(line)
        if m:
            # Key by LEDGER_COLUMNS so the column NAMES live in exactly one place, shared
            # with the header check (F-0123). The regex captures all six data columns
            # positionally (one group per name, F-0148); zip pairs them onto the names in
            # order, so `updated` is a real parsed cell, not a silently dropped key.
            rows.append(dict(zip(LEDGER_COLUMNS, (g.strip() for g in m.groups()))))
    return rows

def parse_limits(audit_dir: Path):
    """Per-project limit overrides from AUDIT.md's `## N. Limits` section (XCHECK.md
    §10 lets AUDIT.md override any default). Returns {key: int} for recognised keys
    only.

    F-0158: an override is a DECLARATION in the Limits section, not any raw line of
    the file. The old whole-file raw scan let an HTML-commented or ```-fenced example
    (`<!-- reopen_limit: 999 -->`) — invisible history, not a project override — win
    last-wins over the active value and silently suppress the mandatory
    `stop-needs-human` gate (§5). Only the Limits section body is read, through the
    shared `_strip_nonstructure` boundary (`_section_bodies`) — a declaration is a
    `key: value` line standing at the section's canonical position (first content at
    columns 0-3); fenced, HTML-commented AND indented-code lines never reach this
    scan (round-3 ruling: an indented `reopen_limit: 999` is a code example, not a
    project override) — and an AMBIGUOUS
    declaration fails closed with an addressed error (F-0124 contract) instead of
    resolving silently: a DUPLICATE declaration of one key inside the section, and
    equally a DUPLICATE Limits section itself (F-0156 reopen shape — the earlier
    first-section-wins read made a second section's declarations invisible)."""
    out = {}
    audit_md = audit_dir / "AUDIT.md"
    if not audit_md.is_file():
        return out
    bodies = _section_bodies(_read_audit_text(audit_md), _LIMITS_RE)
    if not bodies:
        return out
    if len(bodies) > 1:
        raise SystemExit(
            "audit/AUDIT.md Limits: the Limits section is declared more than once — an "
            "ambiguous override cannot be resolved by section order (§10); keep exactly "
            "one Limits section (F-0158)")
    for line in bodies[0].splitlines():
        m = re.match(r"^\s*(?:[-*]\s*)?(\w+)\s*:\s*(\d+)\b", line)
        if m and m.group(1) in LIMIT_KEYS:
            key = m.group(1)
            if key in out:
                raise SystemExit(
                    f"audit/AUDIT.md Limits: duplicate `{key}` declarations — an ambiguous "
                    f"override cannot be resolved last-wins (§10); keep exactly one (F-0158)")
            out[key] = int(m.group(2))
    return out

def effective_rows(audit_dir: Path):
    """Ledger rows reconciled with finding-file frontmatter (XCHECK.md §9.4:
    the finding file is truth for non-triage statuses). A pending-triage delta
    (file 'reported', ledger accepted/rejected/deferred) keeps the ledger
    status — it flows ledger->file per §2 rule 3. Any other mismatch adopts the
    frontmatter status. Returns (rows, findings-frontmatter)."""
    rows = parse_ledger(audit_dir)
    # F-0097: LEDGER is a derived index — one row per finding (§2). Duplicate rows
    # for one id are corrupt durable state (surfaced by ledger_duplicate_ids and
    # stopped at the decision gate / by lint); collapse them to the FIRST row here
    # so the reconciled view and the KPIs never count one finding twice.
    _dedup, _seen_ids = [], set()
    for r in rows:
        if r["id"] in _seen_ids:
            continue
        _seen_ids.add(r["id"])
        _dedup.append(r)
    rows = _dedup
    fnd = load_findings(audit_dir)
    # F-0141: the member-reversion delta is judged from the CF's LEDGER status; the
    # deduped rows above are the ledger-leading view, so build the id->status map here.
    ledger_status_by_id = {r["id"]: r["status"] for r in rows}
    seen = set()
    for r in rows:
        seen.add(r["id"])
        fm = fnd.get(r["id"])
        if not fm:
            continue
        # F-0101: title and severity are canonical in the finding file (§2: the
        # file is the single source of truth; the ledger is a derived index).
        # Adopt them regardless of the status match so every consumer (metrics
        # KPIs, status dashboard) reflects the canonical record instead of a stale
        # derived severity/title — lint separately SURFACES the divergence.
        ftitle = fm.get("title")
        if ftitle:
            r["title"] = ftitle
        fsev = fm.get("severity")
        if fsev:
            r["severity"] = fsev
        fstatus = fm.get("status")
        if not fstatus or fstatus == r["status"]:
            continue
        # F-0099: keep the ledger value only for a LEGAL pending-triage delta
        # (§2 rule 3 — both the ordinary reported→triage AND the rejected-CF member
        # reversion); any other disagreement adopts the frontmatter (file is truth).
        if not pending_triage_delta(fstatus, r["status"], fm, fnd, ledger_status_by_id):
            r["status"] = fstatus
    # F-0005: a finding file present but ABSENT from the derived ledger is still
    # canonical state (§2: the finding file is the single source of truth; the
    # ledger is a derived index). Reconciling only existing ledger rows lets such
    # a finding vanish from the decision and yields a false 'done'. Synthesize a
    # row from its frontmatter so the decision sees the real state.
    for fid, fm in sorted(fnd.items()):
        if fid in seen:
            continue
        rows.append({
            "id": fid,
            "title": fm.get("title", ""),
            "severity": fm.get("severity", "minor"),
            "status": fm.get("status", ""),
            "next": "",
        })
    return rows, fnd

# A queue CHECKBOX line — anything that presents itself as a queue entry (F-0156: this
# is the recognizer; whether it is a VALID entry is `_QUEUE_ENTRY_RE`'s question).
_QUEUE_CHECKBOX_RE = re.compile(r"^\s*-\s*\[( |x)\]\s*(P-\d{2})\b")

# The canonical FULL entry form, as shipped in templates/AUDIT-code.md / AUDIT-text.md:
# `- [ ] P-01 — <dimension> × <units>: <charter one-liner>; stop: <stop conditions>`.
# A P-ID alone is not a charter (§4 rule 1) — the entry itself must carry dimension,
# units, the charter one-liner AND an explicit `; stop:` segment: §4 rule 1 names "an
# exact scope AND stop conditions", so a tail holding only scope (F-0156 reopen:
# `...: scope-without-stop`) is not a charter either. The stop segment is the shipped
# machine-recognisable half (the live AUDIT.md entries all carry `; stop: ...`).
_QUEUE_ENTRY_RE = re.compile(
    r"^\s*-\s*\[( |x)\]\s*(P-\d{2})\s+—\s+(\S+)\s+×\s+(\S+)\s*:\s*(\S.*?)\s*;\s*stop\s*:\s*(\S.*)$"
)

def _table_duplicate_keys(text, heading_re, label):
    """The non-empty values of the `label` column that MORE THAN ONE data row of the
    section's table declares, in first-seen order. A catalog key is an identity: two
    rows sharing one key are two contradicting definitions, and collapsing them into
    a set loses the contradiction (F-0156/F-0157 round-3 human ruling 2026-08-14:
    a duplicate key in Dimensions / Unit map / Norms catalog is AMBIGUITY and must
    fail closed as an addressed issue, never resolve as 'take a matching row').
    Empty when the table or the column is absent — absence is the plan-completeness
    checks' question, not a duplicate."""
    header, rows = _section_table(text, heading_re)
    i = _header_col(header, label) if header else None
    if i is None:
        return []
    seen, dups = set(), []
    for r in rows:
        k = r[i] if i < len(r) else ""
        if not k:
            continue
        if k in seen and k not in dups:
            dups.append(k)
        seen.add(k)
    return dups

def _audit_catalog_sets(text):
    """(dimension keys, unit ids) declared by AUDIT.md's Dimensions / Unit map tables,
    each None when the table (or its named column) is absent. Read through the same
    `_section_table` boundary the plan-completeness checks use. Membership only: a
    DUPLICATE key is surfaced separately as an addressed issue by `_parse_queue_full`
    (`_table_duplicate_keys`) — these sets do not encode uniqueness."""
    dims = units = None
    header, rows = _section_table(text, _DIMENSIONS_RE)
    i = _header_col(header, "key") if header else None
    if i is not None:
        dims = {r[i] for r in rows if i < len(r) and r[i]}
    header, rows = _section_table(text, _UNITMAP_RE)
    i = _header_col(header, "unit") if header else None
    if i is not None:
        units = {r[i] for r in rows if i < len(r) and r[i]}
    return dims, units

def _parse_queue_full(audit_dir: Path):
    """AUDIT.md pass queue -> (unchecked ids, checked ids, issues, charters) where
    `charters` maps each VALID entry's pass id to its declared (dimension, units) —
    the charter the entry dispatches, which `passes_without_coverage` checks a pass
    record's VALUES against (F-0154 round-3 human ruling 2026-08-14).

    F-0156: a queue entry is a CHARTER, not a P-ID. Entries are read only from the
    `## N. Pass queue` section body, through the shared `_strip_nonstructure` boundary
    (`_section_bodies`) — an HTML-commented or ```-fenced example line is invisible
    text, not a dispatchable charter, and a DUPLICATE Pass queue section is an
    ambiguous durable charter that fails closed as an addressed issue (F-0156 reopen,
    mutation 3: first-section-wins hid a second section's contradicting states). A
    recognised checkbox line is VALID only in the full canonical form
    (`- [ ] P-NN — <dimension> × <units>: <charter one-liner>; stop: <stop
    conditions>` — §4 rule 1 names BOTH halves, so a tail with no `; stop:` segment
    is scope without stop conditions, not a charter), with a dimension that is a
    Dimensions-table key AND is norm-backed (F-0157 reopen: the DISPATCHED
    dimension's own `norm source` must resolve to the Norms catalog — one other row
    resolving proves nothing about this charter), units that are Unit-map ids, and a
    P-ID declared exactly once (checked+unchecked at once is two contradicting
    states). A DUPLICATE catalog key — a Dimensions `key`, a Unit-map `unit` or a
    Norms-catalog `id` declared by more than one row — is the same ambiguity at the
    catalog level (round-3 ruling: two rows with one key must yield an addressed
    issue, never collapse into a set). Every violation is an addressed issue:
    `audit_plan_complete` fails the plan on any issue (the poison routes to the
    Planner, never to `run-auditor`) and `cmd_lint` surfaces the same strings."""
    unchecked, checked, issues, charters = [], [], [], {}
    audit_md = audit_dir / "AUDIT.md"
    if not audit_md.is_file():
        return unchecked, checked, issues, charters
    text = _read_audit_text(audit_md)
    bodies = _section_bodies(text, _QUEUE_RE)
    if not bodies:
        return unchecked, checked, issues, charters
    if len(bodies) > 1:
        issues.append(
            "the Pass queue section is declared more than once — an ambiguous durable "
            "charter cannot dispatch (§4 rule 1; F-0156)")
        return unchecked, checked, issues, charters
    body = bodies[0]
    for heading_re, label, table in ((_DIMENSIONS_RE, "key", "Dimensions"),
                                     (_UNITMAP_RE, "unit", "Unit map"),
                                     (_NORMS_RE, "id", "Norms catalog")):
        for k in _table_duplicate_keys(text, heading_re, label):
            issues.append(
                f"the {table} table declares {label} '{k}' more than once — an "
                f"ambiguous catalog cannot resolve a charter (§4 rule 1; F-0156/F-0157)")
    dims, units = _audit_catalog_sets(text)
    nbdims = _norm_backed_dim_keys(text)
    seen = set()
    for line in body.splitlines():
        cb = _QUEUE_CHECKBOX_RE.match(line)
        if not cb:
            continue
        pid = cb.group(2)
        m = _QUEUE_ENTRY_RE.match(line)
        if not m:
            issues.append(
                f"queue entry {pid} is not a full charter — canonical form is "
                f"`- [ ] {pid} — <dimension> × <units>: <charter one-liner>; stop: "
                f"<stop conditions>` (§4 rule 1: no session starts without an exact "
                f"scope AND stop conditions; F-0156)")
            continue
        if pid in seen:
            issues.append(
                f"queue entry {pid} is declared more than once — an ambiguous charter "
                f"cannot dispatch (F-0156)")
            continue
        seen.add(pid)
        dim = m.group(3)
        if not dims or dim not in dims:
            issues.append(
                f"queue entry {pid}: dimension '{dim}' is not a key in the AUDIT.md "
                f"Dimensions table (F-0156)")
            continue
        # F-0157 (reopen): the DISPATCHED dimension must itself be norm-backed. The
        # plan-level `_dimensions_norm_backed` proves only that SOME row resolves
        # (the §3 Planner stop condition); dispatching a charter on a row whose own
        # `norm source` resolves to nothing sends an Auditor where the mandatory §6
        # rule 2 norm reference cannot be formed.
        if dim not in nbdims:
            issues.append(
                f"queue entry {pid}: dimension '{dim}' is not norm-backed — its "
                f"`norm source` cell does not resolve to the AUDIT.md Norms catalog "
                f"(§6 rule 2; F-0157)")
            continue
        entry_units = [u for u in re.split(r"[,\s]+", m.group(4)) if u]
        unknown = [u for u in entry_units if not units or u not in units]
        if unknown:
            issues.append(
                f"queue entry {pid}: unit(s) {', '.join(unknown)} are not in the "
                f"AUDIT.md Unit map (F-0156)")
            continue
        charters[pid] = (dim, tuple(entry_units))
        (checked if m.group(1) == "x" else unchecked).append(pid)
    return unchecked, checked, issues, charters

def parse_queue(audit_dir: Path):
    """AUDIT.md pass queue -> (unchecked pass ids, checked pass ids) — the VALID
    entries only (F-0156: see `_parse_queue_full`, which also reports the issues)."""
    unchecked, checked, _, _ = _parse_queue_full(audit_dir)
    return unchecked, checked

def audit_dimension_keys(audit_dir: Path):
    """The set of dimension keys the AUDIT.md Dimensions table declares, or None when
    there is no catalog to resolve against (no AUDIT.md, no Dimensions table, or no
    `key` column). F-0152: the lint-surface cross-record check (README §8: 'a
    `dimension` slug against the `AUDIT.md` list') resolves finding dimensions here;
    None means the check does not apply — plan completeness is `audit_plan_complete`'s
    gate, not lint's."""
    audit_md = audit_dir / "AUDIT.md"
    if not audit_md.is_file():
        return None
    dims, _units = _audit_catalog_sets(_read_audit_text(audit_md))
    return dims

# GFM cell split: a `|` preceded by a backslash is an ESCAPED literal pipe — part of the
# cell's content, NOT a column separator (F-0112 human ruling 2026-07-28, fourth ruling:
# the whole GFM table form; this is its cell-parsing clause). Splitting on every physical
# `|` forged a cell from an escaped fragment, so a data row `| spec\|alias | drift |` read
# as THREE cells and hid a missing third column instead of leaving it empty.



def _section_bodies(text, heading_re):
    """The bodies of EVERY `## N. <title>` section matched by `heading_re`, read as
    document structure — one body string per matched section, [] when none exists.
    Non-rendered blocks are stripped FIRST (`_strip_nonstructure`) and the heading
    line is read through `_atx_indent_ok`, exactly like `_section_table` — so a
    commented-out or ```-fenced copy of the section (or of any line inside it) is not
    read as durable state, and a line in a DIFFERENT section never reaches this
    section's parser (F-0153/F-0156/F-0158: a raw-line scan over the whole file let
    invisible or foreign-section text act as configuration). Each body runs to the
    next `## ` heading or end of file.

    Returning ALL matched sections is load-bearing (F-0156 reopen, mutation 3): the
    earlier first-section-wins form made a SECOND declaration of the same section
    invisible, so two `## N. Pass queue` sections holding one P-ID in contradicting
    states still dispatched — the caller must see the duplicate and fail closed on
    the ambiguity (queue: an addressed issue; Limits: an addressed error), never
    resolve it silently."""
    stripped = _strip_nonstructure(text)
    bodies = []
    body = None
    for line in stripped.split("\n"):
        ded = _atx_indent_ok(line)
        s = ded.rstrip() if ded is not None else ""
        if s.startswith("## "):
            if body is not None:
                bodies.append("\n".join(body))
                body = None
            if heading_re.match(s):
                body = []
            continue
        if body is not None:
            body.append(line)
    if body is not None:
        bodies.append("\n".join(body))
    return bodies



def _header_col(header, label):
    """Index of the header cell whose NORMALIZED text EQUALS `label` (F-0112), or None.
    Whole-cell equality, not substring (human ruling 2026-07-28): a header that does not
    name the required column EXACTLY — a wrong-shaped or deceptive one-cell header —
    resolves to None and fails the plan, instead of passing because the required word
    happened to be a substring of some other cell."""
    if not header:
        return None
    want = _norm_cell(label)
    for i, c in enumerate(header):
        if _norm_cell(c) == want:
            return i
    return None

def _table_has_row_with(text, heading_re, labels, row_ok=None) -> bool:
    """True iff the section's table (a) has a header that NAMES every column in
    `labels` as a WHOLE normalized cell, on DISTINCT columns, AND (b) carries at least
    one data row whose cells at those columns are all NON-EMPTY — and, when `row_ok`
    is given, for which `row_ok([cells at labels])` also holds (F-0157: non-emptiness
    alone does not prove a cell's CONTENT, e.g. a norm-source cell resolving to the
    Norms catalog).

    F-0112 (Verifier reopen + human ruling 2026-07-28): the prior predicates counted
    non-empty cells, then matched column names by substring, so a row with the WRONG
    columns filled — an empty unit id / material / responsibility — or a deceptive
    one-cell header (`| unit material responsibility |`) or a coincidental substring
    header (`| monkey | somewhat | abnormal |` matching key/what/norm) still certified
    the plan complete. 'A unit map' / 'a dimension backed by a norm' (§3 Planner stop
    condition) means the DECLARED columns are named in full, on separate columns, and
    filled — not that some N cells are non-blank or a word appears somewhere in a
    header. Resolve each semantic column by whole-cell equality, require the columns be
    distinct (a one-cell table cannot name three columns), then require a data row that
    fills exactly them — so `| garbage |`, `| | | N1 |`, a mis-headed table, and a
    single-column deceptive header all fail."""
    header, rows = _section_table(text, heading_re)
    idx = [_header_col(header, lab) for lab in labels]
    if any(i is None for i in idx):
        return False
    if len(set(idx)) != len(idx):  # the named columns must be DISTINCT cells
        return False
    for r in rows:
        if all(i < len(r) and r[i] for i in idx):
            if row_ok is None or row_ok([r[i] for i in idx]):
                return True
    return False


_UNITMAP_RE = re.compile(r"^##\s+\d+\.\s+Unit map\b", re.I)

# The other AUDIT.md sections read as durable state (templates/AUDIT-*.md headings):
# the Norms catalog table (F-0157 resolves `norm source` against its `id` column), the
# Pass queue (F-0156 reads entries only from its body) and the Limits overrides
# (F-0158 reads declarations only from its body).
_NORMS_RE = re.compile(r"^##\s+\d+\.\s+Norms catalog\b", re.I)

_QUEUE_RE = re.compile(r"^##\s+\d+\.\s+Pass queue\b", re.I)

_LIMITS_RE = re.compile(r"^##\s+\d+\.\s+Limits\b", re.I)

# The columns each §2/§3 table must actually NAME (as a whole normalized cell) and FILL
# (F-0112). These are the FULL column names as written in AUDIT.md / templates/AUDIT-*.md
# (`key | what it catches | norm source`; `unit | material | approx. size |
# responsibility`); a table whose header omits or renames a required column fails,
# because whole-cell equality resolves that label to no index (human ruling 2026-07-28).
_DIMENSION_COLS = ("key", "what it catches", "norm source")

_UNITMAP_COLS = ("unit", "material", "responsibility")

def _dimensions_norm_backed(text) -> bool:
    """True iff the §2 Dimensions section holds a table whose header names the
    `key | what it catches | norm source` columns AND at least one DATA row fills all
    three.

    F-0077 (human ruling 2026-07-26): the §3 Planner stop condition requires 'at least
    one dimension backed by a norm', and §6 rule 2 makes a dimension without a norm an
    opinion, not an auditable angle. F-0112 (reopen): reading only the last (norm) cell
    let `| | | N1 |` — no dimension key, no 'what' — and a mis-headed table certify the
    plan complete. Validate against the declared columns (key, what, norm) so the
    'norm-backed dimension' promise equals the predicate.

    F-0157: a NON-EMPTY norm-source cell does not prove norm backing — `banana` names
    no norm anyone can quote (§6 rule 2), so the row must RESOLVE: the cell carries at
    least one `N<digits>` token and every such token is an `id` of the AUDIT.md Norms
    catalog table. A plan with no Norms catalog (or none of whose dimension rows
    resolve) is not complete — fail closed to the Planner, never to `run-auditor`.
    The supported multi-norm form is the shipped one: `;`/`,`-separated references,
    each anchored on a catalog id (`N1; N2 §8`)."""
    known = _audit_norm_ids(text)

    def _norm_ok(cells):
        return _norm_cell_ok(cells[2], known)  # cells follow _DIMENSION_COLS order

    return _table_has_row_with(text, _DIMENSIONS_RE, _DIMENSION_COLS, row_ok=_norm_ok)

def _norm_cell_ok(cell, known):
    """True iff a `norm source` cell RESOLVES: it cites at least one `N<digits>` token
    and every cited token is an id of the Norms catalog (F-0157). The ONE resolution
    predicate `_dimensions_norm_backed` (plan level: at least one row) and
    `_norm_backed_dim_keys` (dispatch level: the chartered row) share — forking it is
    the CF-0001 drift shape."""
    cited = re.findall(r"\bN\d+\b", cell or "")
    return bool(cited) and bool(known) and all(n in known for n in cited)

def _norm_backed_dim_keys(text):
    """The dimension keys declared by EXACTLY ONE Dimensions-table row whose `norm
    source` cell resolves to the Norms catalog (`_norm_cell_ok`). Empty when the
    table, its columns or the catalog are absent.

    F-0157 (reopen): `_dimensions_norm_backed` answers the §3 Planner stop condition
    ("at least one dimension backed by a norm") and says nothing about any OTHER row.
    The queue gate must check the edge the dispatch actually rides — queue entry →
    ITS dimension's row — or an Auditor is chartered on a dimension for which the
    mandatory §6 rule 2 norm reference cannot be formed.

    F-0157 (round-3 human ruling 2026-08-14): a key declared by MORE THAN ONE row is
    never norm-backed, whatever any of its rows resolves to — with two definitions the
    consumer cannot tell which `norm source` belongs to the dispatched dimension, and
    a set-collapse hid exactly that (one good duplicate masked a `banana` row).
    `_parse_queue_full` additionally surfaces the duplicate itself as an addressed
    issue; excluding it here keeps this predicate honest for every caller."""
    known = _audit_norm_ids(text)
    header, rows = _section_table(text, _DIMENSIONS_RE)
    ki = _header_col(header, "key") if header else None
    ni = _header_col(header, "norm source") if header else None
    if ki is None or ni is None:
        return set()
    counts = {}
    for r in rows:
        if ki < len(r) and r[ki]:
            counts[r[ki]] = counts.get(r[ki], 0) + 1
    return {r[ki] for r in rows
            if ki < len(r) and r[ki] and counts[r[ki]] == 1
            and ni < len(r) and _norm_cell_ok(r[ni], known)}

def _audit_norm_ids(text):
    """The `id` column of AUDIT.md's Norms catalog table as a set of `N<digits>`
    tokens — the ids a dimension's `norm source` cell may cite (F-0157). Empty when
    the section, table or column is absent."""
    header, rows = _section_table(text, _NORMS_RE)
    i = _header_col(header, "id") if header else None
    if i is None:
        return set()
    return {r[i] for r in rows if i < len(r) and re.fullmatch(r"N\d+", r[i] or "")}

def audit_plan_complete(audit_dir: Path) -> bool:
    """A complete Planner plan per XCHECK.md §3 Planner stop condition: AUDIT.md
    exists AND carries (a) a §2 Dimensions table with at least one data row whose
    norm-source cell is non-empty — a NORM-BACKED dimension, not merely a row
    (F-0077); (b) a §3 Unit map section holding a table with at least one data
    row; and (c) a non-empty pass queue (§4). Mere file existence is NOT
    'planning done' — a partial or template-only AUDIT.md (an interrupted
    Planner, or a hand-authored stub with a queue but no unit map, or a
    dimension row with an EMPTY norm cell) must re-run the Planner, not fall
    through to the Auditor (F-0077: decide() first saw only the file's existence,
    then only that a Dimensions row existed; both let an under-planned AUDIT.md
    route to run-auditor). The queue counts checked or unchecked entries, so a
    fully-audited plan stays complete instead of flipping back to 'incomplete'.
    Each conjunct is exactly what the docstring claims — no wider (F-0077 root:
    a predicate must not promise more than it checks)."""
    audit_md = audit_dir / "AUDIT.md"
    if not audit_md.is_file():
        return False
    # F-0156: a queue with any charter issue (a bare P-ID, a duplicate/contradictory
    # id, an unresolvable dimension/unit) is not a plannable queue — fail closed to
    # the Planner rather than dispatching an Auditor on a defective charter.
    unchecked, checked, qissues, _ = _parse_queue_full(audit_dir)
    if qissues or not (unchecked or checked):
        return False
    text = audit_md.read_text(encoding="utf-8")
    # F-0112 (reopen): the §3 unit map row is `unit | material | size | responsibility`;
    # require the header to NAME the unit/material/responsibility columns and a data row
    # to FILL them, so a one-column `| garbage |` row, an empty unit id/material/resp
    # cell, or a mis-headed table does not pass as a unit map.
    return _dimensions_norm_backed(text) and _table_has_row_with(text, _UNITMAP_RE, _UNITMAP_COLS)

def _coverage_marker(text, label):
    """True iff `label` (COVERED / NOT COVERED) appears as a coverage MARKER whose
    body holds at least one line of DECLARED CONTENT FORM. A marker is the label at
    the START of a line, allowing markdown heading (`### `) or bold (`**`) decoration,
    closed by `:`, `**`, or the line end. A bare mid-sentence mention ("...merely
    mentions COVERED and NOT COVERED without reporting scope.") is NOT a marker
    (F-0011 adversarial bypass); anchoring at line start keeps the COVERED inside
    NOT COVERED from satisfying COVERED. The body runs to the next marker, the next
    `## ` section heading, or end of file.

    Content is decided by POSITIVE FORM, not by recognizing invisibility (F-0111
    human ruling 2026-07-28, second ruling). Earlier rounds asked, per line, 'is this
    an invisible placeholder?' and the Verifier kept supplying a new invisible
    construct the guard did not know — an HTML comment, a ref-def with then without a
    space after the colon, a multi-line ref-def with the title on the next line. The
    invisible set is unbounded and cannot be closed by enumeration; the visible set is
    declared and finite. A body line counts iff it is a markdown LIST ITEM
    (`-`/`*`/`+`/`N.`) OR a PROSE line that does not begin with `[`, `<`, or `#` and is
    not the continuation of a link-reference definition (the wrapped destination/title
    line right after a `[label]:` opener) — and, in EITHER case, it must carry
    REPORTABLE TEXT (a letter or digit). An empty list item (a bare `-`) or a
    punctuation-only line renders to nothing and reports neither what was covered nor
    what was not: a form that admits an empty instance is not a form (F-0111 human
    ruling 2026-07-28, third ruling). Everything else — a ref-def, a raw-HTML /
    `<...>` line, a heading, a ref-def continuation, an empty marker body — is NOT
    content, not because it was classified invisible but because it is not the declared
    form. §4 rule 5 demands
    the pass state what it did not cover, so an empty marker, or one followed only by
    non-declared lines, is a silent gap. The marker AND its content lines must stand at
    canonical position: the search runs on `_strip_nonstructure` output, so fenced,
    HTML-commented and 4+-column-indented lines (indented code, round-3 ruling) never
    reach it — a marker or list item that only exists at code indent is not a report."""
    # F-0153 (recurrence of Ouroboros-2 F-0111): the marker itself must be VISIBLE
    # document structure. Searching the raw text found a `COVERED:` whose `<!--` opener
    # lay BEFORE the match — so the blanking never saw the pair and the hidden
    # body counted as a report — and a ```-fenced marker was never excluded at all.
    # Strip both non-rendered block types FIRST, through the one shared
    # `_strip_nonstructure` boundary. The F-0153 REOPEN closed the last gap there:
    # the funnel now also blanks MID-LINE `<!-- ... -->` spans (a `visible prefix
    # <!--` opener used to leave the comment body visible), so no second
    # per-segment blanking survives here.
    text = _strip_nonstructure(text)
    pat = re.compile(
        rf"^[ \t]*(?:#{{1,6}}[ \t]+|\*\*[ \t]*)?{label}\b[ \t]*(?::|\*\*|$)(?P<rest>.*)$",
        re.MULTILINE,
    )
    for m in pat.finditer(text):
        segment = m.group("rest") + "\n" + text[m.end():]
        if _declared_form_content(segment, first_line_is_marker_tail=True, marker_stop=True):
            return True
    return False

# The canonical pass-record key set, exactly as templates/pass.md ships it (F-0154
# reopen: "one canonical report" means the full six-field record — a file carrying a
# subset, or a foreign extra field, wears canonical identity it does not have).
_PASS_RECORD_KEYS = frozenset({"id", "dimension", "units", "status", "findings", "updated"})

def passes_without_coverage(audit_dir: Path):
    """Checked passes (AUDIT.md `[x]`) that lack a complete coverage report
    (XCHECK.md §4 rule 5: a coverage report is mandatory; a silent gap is a
    protocol violation). Returns the offending pass ids (empty when every
    checked pass is documented). A single queue checkbox is NOT a substitute
    for the report (F-0011).

    F-0154: a checked pass is documented by EXACTLY ONE canonical report — one
    `passes/P-NN-*.md` whose frontmatter (the shared FRONTMATTER_RE +
    `_parse_frontmatter`, no duplicate/bad-form keys) carries the EXACT
    templates/pass.md key set `_PASS_RECORD_KEYS` (missing AND extra keys both
    disqualify — the F-0154 reopen: a two-field `id:`+`status:` file wore canonical
    identity while carrying no dimension, units, findings or updated), with `id:`
    equal to the pass id and `status: done` or `split` (templates/pass.md:
    `queued | done | split`; a checked pass is executed, so a still-`queued` record
    documents nothing) — and BOTH coverage markers must live in that one file's own
    body. The old form concatenated every filename-prefix match and checked two
    markers in the synthetic join, so two partial files, a file whose record
    belonged to a different pass (`id: P-99`), or a `queued` skeleton certified the
    pass. Zero candidates and two competing candidates both fail: pass state is
    derived from one canonical record, never from a glue of arbitrary files.

    F-0154 (round-3 human ruling 2026-08-14): the record is canonical by its VALUES,
    not by its field-name set — a matching six-key skeleton whose values name a ghost
    charter (`dimension: ghost`, `units: [B99]`, `updated: never`) is not a record of
    the executed pass. So the values are checked against the CHARTER the queue entry
    itself declares (`_parse_queue_full`'s charters map): `dimension` must equal the
    charter's dimension (already catalog-resolved by the queue gate), `units` must be
    non-empty and a subset of the charter's units (a `split` record may cover fewer,
    never other units), `updated` must be an existing calendar date (the ONE shared
    `_canon_calendar_date`, F-0119), and `findings` must be empty or `F-NNNN`/`CF-NNNN`
    ids. Nothing wider is claimed: the checks bind the record to its charter and
    calendar, they do not prove the pass was actually performed.

    A report is complete only when BOTH lists are present AS MARKERS —
    `**COVERED:**` / `### COVERED` / `COVERED:` and the NOT COVERED forms.
    A bare prose mention of the words does not qualify (§4 rule 5 demands
    the pass state explicitly what it did not cover, not name the words)."""
    _, checked, _, charters = _parse_queue_full(audit_dir)
    pdir = audit_dir / "passes"
    bad = []
    for pid in checked:
        cdim, cunits = charters[pid]  # every checked id came from a VALID queue entry
        files = sorted(pdir.glob(f"{pid}-*.md")) if pdir.is_dir() else []
        canonical = []
        for f in files:
            text = f.read_text(encoding="utf-8", errors="replace")
            m = FRONTMATTER_RE.match(text)
            if not m:
                continue
            fm, dups, badform = _parse_frontmatter(m.group(1))
            if dups or badform:
                continue  # an ambiguous record is not a canonical report
            if set(fm) != _PASS_RECORD_KEYS:
                continue  # a key subset (or a foreign extra) is not the canonical record
            if fm.get("id") != pid or fm.get("status") not in ("done", "split"):
                continue
            # round-3 ruling: canonical BY VALUES, checked against the entry's charter.
            if fm.get("dimension") != cdim:
                continue
            runits = [u for u in re.split(r"[,\s]+", (fm.get("units") or "").strip("[]")) if u]
            if not runits or any(u not in cunits for u in runits):
                continue  # no units, or units outside the charter: not this pass's record
            if _canon_calendar_date("updated", fm.get("updated") or "") is not None:
                continue  # `never` / an impossible date is not a record of an executed pass
            fids = [x for x in re.split(r"[,\s]+", (fm.get("findings") or "").strip("[]")) if x]
            if any(not re.fullmatch(r"(?:F|CF)-\d{4}", x) for x in fids):
                continue
            canonical.append(text)
        if len(canonical) != 1:
            bad.append(pid)
            continue
        has_not_covered = _coverage_marker(canonical[0], "NOT COVERED")
        has_covered = _coverage_marker(canonical[0], "COVERED")
        if not (has_covered and has_not_covered):
            bad.append(pid)
    return bad

# F-0149: THE frontmatter-block form, declared once for every loader (finding files,
# construal records, the template guard). The closing delimiter must be a FULL `---`
# line — the old prefix match `\n---` accepted `---garbage` as a delimiter, so a
# structurally broken file parsed as a clean record instead of failing closed as
# malformed. Trailing horizontal whitespace on the delimiter line is tolerated;
# any other character is content, not a delimiter.




def load_findings(audit_dir: Path):
    """Parse frontmatter of every findings/*.md -> {id: {field: value}}."""
    out = {}
    fdir = audit_dir / "findings"
    if not fdir.is_dir():
        return out
    for f in sorted(fdir.glob("*.md")):
        text = f.read_text(encoding="utf-8", errors="replace")
        m = FRONTMATTER_RE.match(text)  # F-0149: full-line closing delimiter, one shared form
        if not m:
            continue
        fm, dups, badform = _parse_frontmatter(m.group(1))
        fid = fm.get("id")
        if fid:
            fm["_file"] = f.name
            if dups:
                fm["_dupkeys"] = dups  # F-0103
            if badform:
                fm["_badform"] = badform  # CF-0001 ruling #9
            out[fid] = fm
    return out

def load_finding_files(audit_dir: Path):
    """Every findings/*.md with its own frontmatter, WITHOUT collapsing by id.

    `load_findings` returns {id: fm}, so two files sharing an id or a file whose
    name prefix disagrees with its frontmatter id both vanish into one entry —
    hiding exactly the `<id>-*.md` one-file-one-finding contract lint must check
    (F-0013). This preserves one dict per physical file (each with `_file`)."""
    out = []
    fdir = audit_dir / "findings"
    if not fdir.is_dir():
        return out
    for f in sorted(fdir.glob("*.md")):
        text = f.read_text(encoding="utf-8", errors="replace")
        m = FRONTMATTER_RE.match(text)  # F-0149: full-line closing delimiter, one shared form
        if not m:
            # A findings/*.md with NO frontmatter block still exists on disk and
            # must not vanish before the orphan/id checks (F-0013): record it as
            # malformed so lint reports it instead of silently skipping.
            out.append({"_file": f.name, "_malformed": True})
            continue
        fm, dups, badform = _parse_frontmatter(m.group(1))
        fm["_file"] = f.name
        if dups:
            fm["_dupkeys"] = dups  # F-0103
        if badform:
            fm["_badform"] = badform  # CF-0001 ruling #9
        out.append(fm)
    return out
