"""Generated Markdown mirrors of `audit/state.json`, with fail-closed drift detection.

Agents still read Markdown and humans still triage from it. A mirror is fine; a
second *authority* is not — that was the whole defect phase 5 removed. So these
renderers are one-directional by construction: `state.json` -> `LEDGER.md` and
-> each finding's frontmatter block, never back. Nothing in this module parses a
mirror to learn machine state; `verify_views` compares TEXT it rendered itself
against TEXT on disk, so there is no second reader here even in principle.

**Drift is refused, never silently re-synced.** A hand-edited mirror means
somebody believed they were writing authority. Quietly discarding their edit
destroys work and teaches them the mirror is writable; quietly accepting it
reinstates the dual-authority defect. Both are the same class of silent failure.
The only honest response is to stop and name the file, the line and both values,
so a human decides which one is true.

The body below a finding's frontmatter is EVIDENCE and is never touched — the
renderer replaces the block between the opening `---` and its closing `---` and
copies the rest byte for byte. `tests/test_views.py` asserts that with sha256.
"""

from dataclasses import dataclass
from pathlib import Path

from xcheck.md_prose import FRONTMATTER_RE
from xcheck.state import STATE_FILENAME, StateError, next_owner
from xcheck.util import LEDGER_COLUMNS, _read_audit_text

LEDGER_FILENAME = "LEDGER.md"

# The banner carries NO colon on any line, on purpose: the legacy frontmatter
# reader treats `key: value` as a field and skips colon-free lines, so a banner
# written this way is inert to every parser that ever read these files — the
# converter in phase 6 included. A `# GENERATED from: state.json` would parse as
# a field named `# GENERATED from`.
FM_BANNER = ("# GENERATED FROM audit/state.json — DO NOT EDIT THIS BLOCK\n"
             "# an edit here is refused as drift; change state with an xcheck write verb\n")

LEDGER_BANNER = (
    "<!-- GENERATED FROM audit/state.json — DO NOT EDIT.\n"
    "     This table is a VIEW. It is rendered from the state document and is never\n"
    "     read back as authority. An edit here is refused by every command as drift,\n"
    "     naming this file and the row: re-render with `xcheck render-views`, or change\n"
    "     the state itself through an xcheck write verb. -->\n")

# §2 canonical field order, as templates/finding.md and templates/class-finding.md
# ship it. One tuple for both record kinds: a field the record does not carry is
# skipped, so a Finding simply has no `members` line.
FM_ORDER = ("id", "title", "severity", "dimension", "unit", "status", "class",
            "members", "attempts", "recurrence-of", "blocked", "norm-ruling",
            "refusal", "admitted-scope", "pass", "created-by", "updated", "fixed-by")

# Fields rendered as `null` when the record does not carry them. The rest are
# required by the schema, so their absence is not representable.
FM_NULLABLE = frozenset({"class", "recurrence-of", "blocked", "norm-ruling",
                         "refusal", "admitted-scope"})


@dataclass(frozen=True)
class Drift:
    """One mirror line that disagrees with the state document."""
    file: str
    line: int
    mirror: str
    authority: str

    def __str__(self):
        return (f"{self.file}:{self.line} — the file says {self.mirror!r}, "
                f"state.json says {self.authority!r}")


# The two §2 field names that cannot be attribute names: `class` is a keyword and
# `pass` is one too, so the dataclasses spell them `cls` and `pass_id`. Declared
# here rather than special-cased inside the renderer, so the view and the schema
# disagree in one visible place or not at all.
FM_ATTR = {"class": "cls", "pass": "pass_id"}


def _attr(record, field):
    """A rendered field's value, or None when the record does not carry it."""
    name = FM_ATTR.get(field, field.replace("-", "_"))
    v = getattr(record, name, None)
    if v is None or v == ():
        return None
    if isinstance(v, (tuple, list)):
        return f"[{', '.join(v)}]" if len(v) > 1 else v[0]
    return str(v)


def render_frontmatter(record):
    """The machine fields of one finding or class finding, as a frontmatter block.

    Includes the opening and closing `---` lines, so the result can be swapped in
    for an existing block without the caller reasoning about delimiters.
    """
    lines = ["---", FM_BANNER.rstrip("\n")]
    for field in FM_ORDER:
        v = _attr(record, field)
        if v is None:
            if field in FM_NULLABLE:
                lines.append(f"{field}: null")
            continue
        lines.append(f"{field}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _cell(text):
    """One LEDGER cell. A `|` inside a title would open a seventh column and make
    the row unparseable for anything reading the view, so it is escaped.

    The BACKSLASH is escaped first, and the order is the whole point: escaping only
    the pipe turns a title containing the two characters `\\|` into `\\\\|` — an
    escaped backslash followed by a bare pipe, which opens the column the escape was
    supposed to close. Found by the phase-12 edge battery, not by a reader."""
    return (str(text).replace("\\", "\\\\").replace("|", "\\|")
            .replace("\n", " ").strip())


def render_ledger(state):
    """The six-column §2 table, one row per record, ordered by id.

    `next` is not stored on most records — it is derived from status through the
    single `next_owner`, the same function the decision path uses. That is why a
    LEDGER row can no longer disagree with a finding about who acts next: there
    is one answer and this view prints it.
    """
    rows = sorted(list(state.findings) + list(state.class_findings), key=lambda r: r.id)
    out = [f"# LEDGER — {len(rows)} record(s)", "", LEDGER_BANNER.rstrip("\n"), "",
           "| " + " | ".join(LEDGER_COLUMNS) + " |",
           "|" + "|".join("---" for _ in LEDGER_COLUMNS) + "|"]
    for r in rows:
        out.append("| " + " | ".join(_cell(x) for x in (
            r.id, r.title, r.severity, r.status, next_owner(r), r.updated)) + " |")
    return "\n".join(out) + "\n"


def _split_body(text):
    """(frontmatter block incl. delimiters, everything after it). A file with no
    block is all body — the renderer then prepends a block rather than guessing
    where one was meant to start."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return "", text
    return text[:m.end()], text[m.end():]


def write_views(state, audit_dir: Path):
    """Regenerate `LEDGER.md` and every record's frontmatter block, atomically per
    file. Returns the list of paths written."""
    audit_dir = Path(audit_dir)
    written = []
    ledger = audit_dir / LEDGER_FILENAME
    _atomic_write(ledger, render_ledger(state))
    written.append(ledger)
    for r in list(state.findings) + list(state.class_findings):
        p = audit_dir / r.body_path
        old = _read_audit_text(p) if p.is_file() else ""
        _, body = _split_body(old)
        if not body.startswith("\n"):
            body = "\n" + body
        p.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(p, render_frontmatter(r) + body)
        written.append(p)
    return written


def _atomic_write(path: Path, text):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _diff(expected, actual, name):
    """The first line where two texts disagree, as a Drift. Both sides are text
    this module renders or a file whose block it rendered, so a line-by-line walk
    is a real comparison and not a parse."""
    e, a = expected.splitlines(), actual.splitlines()
    for i in range(max(len(e), len(a))):
        want = e[i] if i < len(e) else "<the line is missing>"
        got = a[i] if i < len(a) else "<the line is missing>"
        if want.rstrip() != got.rstrip():
            return Drift(name, i + 1, got, want)
    return None


def verify_views(state, audit_dir: Path):
    """Every mirror that disagrees with the state document, as addressed Drifts.

    Empty when the tree is in sync. A missing mirror is drift too: a `LEDGER.md`
    that was deleted is not "no claim", it is a claim the operator will read as
    an empty ledger the moment anything regenerates around it.
    """
    audit_dir = Path(audit_dir)
    out = []
    ledger = audit_dir / LEDGER_FILENAME
    if not ledger.is_file():
        out.append(Drift(LEDGER_FILENAME, 0, "<the file is missing>",
                         "the generated ledger view"))
    else:
        d = _diff(render_ledger(state), _read_audit_text(ledger), LEDGER_FILENAME)
        if d:
            out.append(d)
    for r in list(state.findings) + list(state.class_findings):
        p = audit_dir / r.body_path
        if not p.is_file():
            out.append(Drift(r.body_path, 0, "<the file is missing>",
                             f"the evidence body of {r.id}"))
            continue
        block, _ = _split_body(_read_audit_text(p))
        d = _diff(render_frontmatter(r), block, r.body_path)
        if d:
            out.append(d)
    return out


def refuse_on_drift(state, audit_dir: Path):
    """Raise `StateError` naming every drifted mirror, or return the state unchanged.

    Called by every consumer immediately after `load_state`. It raises the same
    exception type the reading boundary raises, so drift arrives at the operator
    through the one addressed-refusal path that already exists rather than as a
    second, differently-shaped failure.
    """
    drift = verify_views(state, audit_dir)
    if drift:
        lines = "\n  - ".join(str(d) for d in drift)
        raise StateError(
            f"{len(drift)} generated view(s) disagree with {STATE_FILENAME}:\n  - {lines}\n"
            f"These files are VIEWS — editing one changes nothing and is refused rather "
            f"than discarded, because an edit means someone believed they were writing "
            f"authority. Either re-render them with `xcheck render-views` (the edit is "
            f"lost, deliberately), or put the change into the state document through an "
            f"xcheck write verb and re-render.")
    return state
