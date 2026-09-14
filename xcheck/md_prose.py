"""Generic Markdown PROSE structure — headings, sections, declared-vs-placeholder.

Nothing here reads machine state. These helpers answer questions about the shape
of human-written text: does this document have this ATX heading, is the section
under it actually filled or is it the HTML-comment placeholder a role copied out
of a template, what does the `## 2. Canonical record` YAML block in XCHECK.md
declare. They survive the move to `state.json` untouched, because the prose they
read never moved: a finding's evidence body, a pass report, the methodology.

They are deliberately NOT in `migrate_readers.py`. That module is a one-shot
legacy reader for machine state and no consumer may import it; these are live
helpers `lint` uses on every run to check the human-facing tree. Twelve rounds of
F-0130 are the reason a structural claim about prose is checked structurally here
rather than with a substring search.
"""

import re


from xcheck.util import (  # noqa: F401  (section2_* callers read _read_audit_text)
    _FLOW_MARKS, _YAML_NULL, _read_audit_text
)

def _section2_yaml_block(xcheck_text):
    """The canonical-record ```yaml block from XCHECK.md §2, or None if absent."""
    sec = re.search(r"^##\s*2\.[^\n]*\n(.*?)(?=^##\s|\Z)", xcheck_text, re.S | re.M)
    if not sec:
        return None
    block = re.search(r"```yaml\n(.*?)\n```", sec.group(1), re.S)
    return block.group(1) if block else None

def section2_canonical_fields(xcheck_text):
    """Ordered field names declared in the XCHECK.md §2 canonical-record yaml block,
    or None when no such block is present (nothing to bind against)."""
    block = _section2_yaml_block(xcheck_text)
    if block is None:
        return None
    fields = []
    for line in block.splitlines():
        m = re.match(r"^([A-Za-z][A-Za-z0-9-]*)\s*:", line)
        if m:
            fields.append(m.group(1))
    return fields

def section2_canonical_field_values(xcheck_text):
    """{field: declared-value} for the XCHECK.md §2 canonical-record yaml block — the
    value text after the colon with any inline `# comment` stripped — or None when no
    block is present. I-10 (CF-0001 reopen): the meta-check binds the REQUIREMENT of a
    value predicate to §2 too, and a field's §2 value is what says whether it is
    constrained (`null`, `reported`, `critical | ...`) or a `<free-form>` placeholder
    (`<short title>`) with nothing closed to validate."""
    block = _section2_yaml_block(xcheck_text)
    if block is None:
        return None
    out = {}
    for line in block.splitlines():
        m = re.match(r"^([A-Za-z][A-Za-z0-9-]*)\s*:(.*)$", line)
        if not m:
            continue
        rest = m.group(2)
        cm = re.search(r"\s#", rest)
        out[m.group(1)] = (rest[: cm.start()] if cm else rest).strip()
    return out

def section2_field_nullable_map(xcheck_text):
    """{field: nullable?} DERIVED from the XCHECK.md §2 canonical-record yaml block, or None
    when no block is present. A field is nullable (an empty/absent value is legitimate) IFF
    its §2-shown VALUE offers `null` as an option (`class: null | CF-NNNN`,
    `recurrence-of`/`blocked`/`norm-ruling: null`). Every other §2 field — one whose shown
    value is substantive (`0`, `P-01`, `<short title>`, `<unit path>`, `<date>`, an
    enumeration) — is required and NON-nullable, regardless of any comment (CF-0001 human
    ruling #5: nullability is derived from the value, not from a `list` annotation). This is
    the independent source of truth `canonical_schema_nullability_issues` checks the schema's
    own nullable flags against, so 'required AND nullable' (CF-0001 human ruling #4) cannot be
    declared — nullability is bound to §2, never hand-set."""
    block = _section2_yaml_block(xcheck_text)
    if block is None:
        return None
    out = {}
    for line in block.splitlines():
        m = re.match(r"^([A-Za-z][A-Za-z0-9-]*)\s*:(.*)$", line)
        if not m:
            continue
        field, rest = m.group(1), m.group(2)
        cm = re.search(r"\s#", rest)
        value = (rest[: cm.start()] if cm else rest).strip()
        # CF-0001 human ruling #5: nullability is DERIVED from the §2-shown VALUE alone —
        # a field is nullable IFF that value offers `null` as an option. A substantive shown
        # value (`0`, `P-01`, `<short title>`, `<unit path>`, `<date>`, an enumeration) is
        # NEVER nullable, whatever the comment says. The earlier `list`-in-comment disjunct
        # is exactly what wrongly made `unit` (shown value `<unit path>`) nullable and let a
        # missing/empty/`[]` unit skip the shared gate on every consumer; a list-valued field
        # is still required to carry at least one element (its own value predicate enforces
        # non-emptiness — `unit` via `_canon_unit`).
        has_null = "null" in [t.strip() for t in value.split("|")]
        out[field] = has_null
    return out

def _atx_indent_ok(line):
    """The `line` with its leading indentation removed when that indentation is a LEGAL
    CommonMark structural indent (0-3 columns); None when the line is indented FOUR OR
    MORE columns — an indented code block, which is never an ATX heading, a table row, or
    a code fence, no matter what its bytes spell (F-0130 round-10 human ruling: take the
    CommonMark indent rule WHOLE, do not merely 'drop strip()' and come back a round later
    for one-space indents). Indentation is counted in COLUMNS — a space is one column, a
    tab advances to the next multiple of four — so a single leading tab already means
    code. A blank / all-whitespace line has no structural content and returns None too.
    This is the ONE leading-indent rule every raw-line structural recognizer in the
    template guard shares (`_strip_nonstructure`, `_section_table`, the ATX-heading scan),
    so the same defect cannot survive in an adjacent branch."""
    col = 0
    for i, ch in enumerate(line):
        if ch == " ":
            col += 1
        elif ch == "\t":
            col += 4 - (col % 4)
        else:
            return line[i:]  # first non-whitespace sits at column 0-3: a legal structural indent
        if col >= 4:
            return None  # 4+ columns of leading whitespace before any content: indented code
    return None  # blank / all-whitespace line

def _drop_inline_comments(s):
    """(visible text, still_open) — `s` with every `<!-- ... -->` span blanked out.
    A `<!--` with no closer in `s` drops the rest of the line and reports the
    comment still open, so the caller can keep dropping the following lines
    (F-0153/F-0158 reopen: a MID-LINE opener is a comment too — `visible prefix
    <!--` used to leave the whole comment body visible to the raw-line scan)."""
    out = []
    while True:
        i = s.find("<!--")
        if i < 0:
            out.append(s)
            return "".join(out), False
        out.append(s[:i])
        j = s.find("-->", i + 4)
        if j < 0:
            return "".join(out), True
        s = s[j + 3:]

def _strip_nonstructure(text):
    """`text` reduced to the lines that can carry document structure: fenced code
    blocks, HTML comment blocks AND indented-code lines are removed, so a raw-line
    structural scan never reads a code EXAMPLE or a commented-out heading/table/
    declaration as real document structure. The rule is POSITIVE (F-0153/F-0156/F-0158
    round-3 human ruling 2026-08-14): a declaration is a line standing at its section's
    canonical position — first content at columns 0-3, the ONE CommonMark leading-indent
    predicate `_atx_indent_ok` — and everything that is not in that position is dropped
    here, at the one boundary every consumer reads durable markdown state through,
    whatever construct put it there. This is still NOT a CommonMark parser and does NOT
    strip every non-rendered construct: raw HTML blocks (`<div>...`) and other
    embeddings are left in place. The guard it serves targets ACCIDENTAL drift in our
    own shipped artifacts, not deliberate obfuscation of them (F-0130 human ruling,
    §8 rule 5 exception).

    Two CommonMark leaf blocks hide their contents from the block structure of a
    document, and a raw-line scanner is blind to both unless they are stripped first:

      - a FENCED CODE BLOCK (``` / ~~~): a HIGHER-precedence block than a table or an ATX
        heading — the block parser closes the fence before any inline/table phase runs,
        so a ```-wrapped `## heading` or `| a | b |` row is CODE, not document structure;
        pandoc emits a CodeBlock, never a Header or a Table (F-0130 round-9 reopen);
      - an HTML COMMENT BLOCK (`<!-- ... -->`, CommonMark HTML block type 2): its
        contents are raw and render as nothing, so a real heading or dimensions table
        wrapped in a multi-line `<!--\n...\n-->` renders as NO heading/table at all —
        pandoc emits a RawBlock, never a Header/Table (F-0130 round-11 reopen). A
        raw-line scan that ignored the comment saw the surviving `## ...` / `|...|`
        lines and false-greened while `install.sh` shipped the corrupt template.

    ONE line pass strips both. The two block types are MUTUALLY EXCLUSIVE — whichever
    opens FIRST stays open until ITS OWN closer — so a ``` run inside a comment is inert
    text and a `<!--` inside a fence is inert code; neither opens the other. Both
    delimiters obey CommonMark exactly (F-0130 round-10 reopen):
      - any structural line is indented 0-3 columns (`_atx_indent_ok`); at 4+ columns it
        is itself indented code and opens neither a fence nor a comment;
      - a fence opener may carry an info string (```text, ```markdown); its CLOSER is a
        run of the SAME character (` vs ~), of at least equal length, followed ONLY by
        whitespace — so an interior line like ```not-a-close (a same-char run with a
        non-space tail) does NOT close the block, and a real heading/table after it stays
        inside the code and is stripped, instead of the block ending early;
      - a comment block opens on a line whose first non-space content (0-3 indent) is
        `<!--` and ends on the first line that contains `-->` (which may be the same
        line); the whole span of lines is dropped, matching the HTML-block rule that
        consumes entire lines.

    A `<!--` that is NOT the line's first non-space content is a comment too — an
    INLINE one (F-0153/F-0156/F-0158 reopen: `visible prefix <!--` left the whole
    multi-line comment body visible, so hidden coverage markers / queue entries /
    limit overrides acted as durable state). Mid-line spans are blanked with the
    visible prefix kept and text after the closer kept; an unclosed mid-line opener
    keeps dropping lines until its `-->` OR until a BLANK line — CommonMark inline
    raw HTML cannot cross a paragraph end, so text after the blank line renders and
    stays visible. Ceiling of the line-based form: the dropped tail of an opener
    line whose `<!--` turns out literal (closed by nothing before the paragraph
    end) errs toward INVISIBLE — fail-closed for every consumer (a report reads as
    missing, an override as absent), never a hidden pass.

    An INDENTED-CODE line — first content at column 4 or beyond (`_atx_indent_ok`
    returns None; a leading tab is already column 4) — is dropped the same way
    (round-3 ruling: CommonMark's four-space rule taken whole, at this one boundary,
    not enumerated per consumer). Blank lines are KEPT: they carry the paragraph and
    body boundaries the consumers and the inline-comment rule above depend on.
    Ceiling, deliberately fail-closed: a 4+-indented lazy paragraph continuation and
    a 4+-indented nested list item are VISIBLE in CommonMark but are dropped here —
    content in that position errs toward invisible (a report reads as missing, never
    as silently present), which is the safe direction for every consumer.

    This is the ONE fenced-code / HTML-comment stripper every raw-line structural
    recognizer in the template guard funnels through (`_section_table` and the ATX-heading
    scan), so this same blind spot cannot survive in an adjacent branch — but it bounds the
    guard to those two block types, not to full CommonMark."""
    out, mode, fence = [], None, None  # mode: None | 'fence' | 'comment' | 'inline'; fence: (char, run_length)
    for line in text.split("\n"):
        ded = _atx_indent_ok(line)  # None => 4+-column indent => code, opens no fence/comment
        if mode == "fence":
            m = re.match(r"(`{3,}|~{3,})", ded) if ded is not None else None
            if (m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1]
                    and ded[len(m.group(1)):].strip() == ""):
                mode, fence = None, None  # same-char, >=-length run + only whitespace: a real close
            # else: an interior code line (incl. a same-char run with a non-space tail) -> dropped
            continue
        if mode == "comment":
            if "-->" in line:
                mode = None  # the comment block ends on this line; the whole line is dropped
            continue
        if mode == "inline":
            if not line.strip():
                mode = None  # a blank line ends the paragraph; inline raw HTML cannot cross it
                out.append(line)
                continue
            i = line.find("-->")
            if i < 0:
                continue  # interior of the mid-line comment span -> dropped
            kept, still_open = _drop_inline_comments(line[i + 3:])  # text after the closer renders
            mode = "inline" if still_open else None
            out.append(kept)
            continue
        # mode is None: does this line OPEN a non-rendered block?
        if ded is None:
            if line.strip():
                continue  # 4+-column indent: indented code — never structure (round-3 ruling)
            out.append(line)  # a BLANK line: a paragraph/body boundary, kept
            continue
        m = re.match(r"(`{3,}|~{3,})", ded)
        if m:
            mode, fence = "fence", (m.group(1)[0], len(m.group(1)))  # drop the opening fence line
            continue
        if ded.startswith("<!--"):
            if "-->" not in ded:
                mode = "comment"  # a multi-line comment block; stays open until `-->`
            continue  # drop the opening (single-line: the whole) comment line
        if "<!--" in line:  # a MID-LINE opener: an inline comment, not a block
            kept, still_open = _drop_inline_comments(line)
            if still_open:
                mode = "inline"
            out.append(kept)
            continue
        out.append(line)
    return "\n".join(out)

# A markdown LIST ITEM marker — a `-`/`*`/`+` bullet or an `N.`/`N)` ordinal — followed
# by a space or end of line. The marker alone is NOT content: it counts only when
# REPORTABLE TEXT follows it (see `_REPORTABLE_RE`), because a bare `-` renders to an
# empty `<li>` and reports neither what was covered nor what was not (F-0111 human ruling
# 2026-07-28, third ruling: a form that admits an empty instance is not a form).
_LIST_ITEM_RE = re.compile(r"^(?:[-*+]|\d+[.)])(?:[ \t]|$)")

# Reportable text = at least one letter or digit (Unicode-aware; underscore and
# punctuation do not count). A coverage content line — list item or prose — must carry
# one, so an empty list item (`-`) and a punctuation-only line (`...`) are silent gaps,
# not content (F-0111 human ruling 2026-07-28, third ruling).
_REPORTABLE_RE = re.compile(r"[^\W_]", re.UNICODE)

# A link-reference-definition OPENER is `[label]:` — a bracketed label CLOSED by a
# colon (F-0111 reopen). A line that merely starts with `[` but has no `]:` (e.g.
# `[context]`) opens no definition, so the prose line under it is NOT a ref-def
# continuation and counts as content. Only a `[label]:` opener makes its following
# line a (possibly invisible) wrapped destination/title continuation.
_REF_DEF_OPENER_RE = re.compile(r"^\[[^\]]*\]:")

def _declared_form_content(segment, first_line_is_marker_tail=False, marker_stop=False):
    """True iff `segment` — a marker or section BODY whose `<!-- ... -->` spans are
    already blanked — carries at least one line of DECLARED CONTENT FORM.

    This is the ONE line-form classifier the F-0111 rule is expressed in, shared by the
    coverage-marker check (§4 rule 5) and the `## Refusal` section check (§5). Splitting
    it into two implementations is exactly the forked-validation anti-pattern CF-0001
    closed: two answers to "is this section empty?" drift, and a section that one call
    site calls a silent gap the other calls filled.

    A body line counts iff it is a markdown LIST ITEM (`-`/`*`/`+`/`N.`) OR a PROSE line
    that does not begin with `[`, `<`, or `#` and is not the wrapped destination/title
    continuation of a `[label]:` link-reference definition — and, in EITHER case, carries
    REPORTABLE TEXT (a letter or digit). Content is decided by POSITIVE FORM, never by
    recognizing invisibility: the invisible set is unbounded and cannot be closed by
    enumeration, the visible set is declared and finite (F-0111 human ruling 2026-07-28).

    `first_line_is_marker_tail` — the first line is the remainder of the marker line
    itself (`COVERED: ...`), so the body-boundary stops do not apply to it.
    `marker_stop` — also end the body at a following COVERED / NOT COVERED marker; a
    coverage marker's body runs to the next marker, while a section body handed in
    already bounded by its own heading needs no such stop."""
    prev_bracket = False  # did the previous non-blank body line open a ref def (`[label]:`)?
    first = first_line_is_marker_tail
    for line in segment.splitlines():
        s = line.strip()
        if not s:
            prev_bracket = False  # a blank line closes any ref-def continuation
            first = False
            continue
        if not first:
            # a new `## ` section heading or a following COVERED/NOT COVERED marker
            # ends THIS body — content past the boundary is not its report.
            if re.match(r"^#{1,6}[ \t]", s):
                break
            if marker_stop and re.match(r"^(?:\*\*\s*)?(?:NOT\s+)?COVERED\b", s, re.I):
                break
        m = _LIST_ITEM_RE.match(s)
        if m:
            # a list item counts only with REPORTABLE TEXT after the marker; a bare
            # `-` / `1.` renders to an empty <li> and reports nothing (F-0111).
            if _REPORTABLE_RE.search(s[m.end():]):
                return True
            prev_bracket = False
            first = False
            continue
        # a prose line of declared content form: not a ref-def / raw-HTML / heading
        # opener, not the continuation of a preceding reference definition, and
        # carrying reportable text (a letter/digit) — a punctuation-only line reports
        # nothing and is a silent gap (F-0111 human ruling 2026-07-28, third ruling).
        if s[0] not in "[<#" and not prev_bracket and _REPORTABLE_RE.search(s):
            return True
        prev_bracket = bool(_REF_DEF_OPENER_RE.match(s))
        first = False
    return False

def _atx_heading_bodies(text, heading):
    """Every body that follows `heading` (e.g. `## Refusal`, `### Covers`) read as a REAL
    ATX heading line, as a list of body strings. Empty list == the heading is not present
    as document structure.

    Structure is read exactly the way the template guard reads it, through the same two
    shared rules and no others: non-rendered blocks — fenced code and HTML comment blocks
    — are stripped FIRST by the one `_strip_nonstructure`, so a commented-out or
    ```-fenced copy of a heading is not a heading; and the heading line itself must pass
    the one `_atx_indent_ok` CommonMark leading-indent rule and equal the needle as a
    WHOLE line, never as a substring (F-0130). A body runs to the next ATX heading of any
    level, so a `### Covers` body stops at `### Does not cover`."""
    lines = _strip_nonstructure(text).split("\n")
    out = []
    for i, line in enumerate(lines):
        d = _atx_indent_ok(line)
        if d is None or d.rstrip() != heading:
            continue
        body = []
        for nxt in lines[i + 1:]:
            nd = _atx_indent_ok(nxt)
            if nd is not None and re.match(r"^#{1,6}[ \t]", nd):
                break
            body.append(nxt)
        out.append("\n".join(body))
    return out

def has_atx_heading(text, heading):
    """True iff `heading` is present as real document structure (see `_atx_heading_bodies`)."""
    return bool(_atx_heading_bodies(text, heading))

def section_declared_content(text, heading):
    """True iff `text` carries `heading` as a REAL ATX heading whose body holds at least
    one line of declared content form — the one `_declared_form_content` classifier the
    coverage check uses (F-0111). No second parser, no substring test."""
    return any(_declared_form_content(b) for b in _atx_heading_bodies(text, heading))


# ---------------------------------------------------------------------------
# PHASE 5 — format primitives, moved here from the quarantined reader module.
#
# These read MARKDOWN FORM and nothing about an audit: a frontmatter block, a
# GFM table under a heading, a YAML scalar's declared form. The quarantine in
# `migrate_readers.py` is about SUBJECT, not syntax — what belongs there is
# every reader that answers a question about machine state (what the queue
# holds, which limit is in force, whether a pass is covered). A reader that
# answers "does this document still have the section it declares" is a prose
# reader, and the shipped-template drift check (F-0130, twelve rounds) is its
# only live caller.
# ---------------------------------------------------------------------------

LIMIT_KEYS = ("max_findings_per_pass", "remediation_batch_size", "class_threshold", "reopen_limit")

def _section_table(text, heading_re):
    """(header_cells, [data_row_cells]) for the first GFM markdown table in the
    `## N. <title>` section matched by heading_re, or (None, []) when the section holds
    no such table.

    The WHOLE GFM table form, declared in one place (F-0112 human ruling 2026-07-28,
    fourth ruling — seven prior rounds each bolted on one more structural requirement;
    this states the form once and rejects everything else):

      1. HEADER — a pipe row (`|...|`) yielding N >= 1 cells that is not itself a
         delimiter row.
      2. DELIMITER — the IMMEDIATELY following line: a pipe row of EXACTLY N cells,
         every cell a real `-` run (`_is_delimiter_row`). Not the next PIPE line — the
         next LINE; a blank or prose line between header and delimiter breaks the table.
         A different cell count, a dash-less `| | | |`, or a non-adjacent delimiter all
         fail here.
      3. DATA ROWS — the CONSECUTIVE pipe lines after the delimiter; a blank line or a
         non-`|` line ends the table.
      4. Anything not satisfying 1-3 is NOT a table: the section counts as having no
         table, never a partial one.

    Markdown renders any of the rejected shapes as paragraphs, not a `<table>` (pandoc
    emits `<p>`), so such a plan holds no Dimensions / Unit-map table (§3 Planner stop
    condition). Column count in a DATA row versus the header is deliberately not checked
    here — GFM pads/truncates data rows — so callers resolve a named column BY INDEX and
    treat a missing cell as empty. Scans from the matched heading to the next `## `.

    Non-rendered blocks are stripped FIRST (`_strip_nonstructure`: fenced code blocks AND
    HTML comment blocks) and every remaining line is read through `_atx_indent_ok`: a
    ```-wrapped OR `<!-- ... -->`-wrapped table is CODE/comment, not a `<table>`, and a
    header/row indented 4+ columns is an indented code block, not table structure — so
    none certifies the section as holding a table (F-0130 round-9/10/11 reopen)."""
    text = _strip_nonstructure(text)
    inside = False
    lines = []
    for line in text.splitlines():
        ded = _atx_indent_ok(line)  # None => 4+-column indent => code, not a heading/row (F-0130 round-10)
        s = ded.rstrip() if ded is not None else ""
        if s.startswith("## "):
            if inside:
                break
            inside = bool(heading_re.match(s))
            continue
        if inside:
            lines.append(s)
    for i, s in enumerate(lines):
        # 1. HEADER: a pipe row of N >= 1 cells that is not itself a delimiter.
        if not s.startswith("|"):
            continue
        header = _pipe_cells(s)
        if not header or _is_delimiter_row(header):
            continue
        # 2. DELIMITER: the IMMEDIATELY following line, a real delimiter of EXACTLY N cells.
        if i + 1 >= len(lines):
            break
        nxt = lines[i + 1]
        if not nxt.startswith("|"):
            continue  # header not directly followed by a pipe row -> not a table here
        delim = _pipe_cells(nxt)
        if len(delim) != len(header) or not _is_delimiter_row(delim):
            continue  # wrong cell count or dash-less/broken delimiter -> not a table
        # 3. DATA ROWS: consecutive pipe lines; a blank or non-pipe line ends the table.
        rows = []
        for d in lines[i + 2:]:
            if not d or not d.startswith("|"):
                break
            rows.append(_pipe_cells(d))
        return (header, rows)
    return (None, [])

def _norm_cell(c):
    """Normalize a table cell for WHOLE-cell header comparison: collapse internal
    whitespace to one space, strip the ends, lowercase. Nothing else is removed
    (F-0112 human ruling 2026-07-28, second ruling): a column header is a fixed-form
    SERVICE NAME; markdown emphasis (`*`, `_`) and code spans (`` ` ``) are NOT
    supported inside it, and that restriction is correct, not a gap. So every
    character other than surrounding/whitespace is compared VERBATIM.

    Three earlier rounds tried to strip 'markup' from the cell — first `*`/`_`/`` ` ``
    blanket, then a code-span carve-out — and each time the stripping turned LITERAL
    content into the required column name: `` `k*e*y` `` and then intraword `k_e_y`
    both normalized to `key` and certified a deceptive header. Modelling 'what is
    markup vs content' is the unbounded complement the ruling forbids. Comparing the
    trimmed text as-is makes the accepted set exactly the declared column name: `key`
    matches `key`, while `k_e_y`, `` `k*e*y` ``, and `*key*` do not."""
    return re.sub(r"\s+", " ", c).strip().lower()

_DIMENSIONS_RE = re.compile(r"^##\s+\d+\.\s+Dimensions\b", re.I)

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---[ \t]*(?:\n|$)", re.S)

def _yaml_absent(s):
    return "" if s in _YAML_NULL else s

def _fm_value(v):
    """A frontmatter value parsed into `(normalized, form_ok)`.

    The parser recognises EXACTLY the two value forms XCHECK.md §2 declares for a
    canonical field: a scalar, or a FLAT inline list of scalars (`[a, b]`). It does
    not try to normalise an arbitrary YAML structure into something addressable —
    eight rounds of chasing absence spellings into ever-deeper nesting proved that
    set is unbounded (CF-0001 human ruling #9). Any value that is not a declared
    form — a flow mapping (`{a: b}`), a nested list (`[[null]]`), or an unclosed
    bracket (`[a`) — is returned with `form_ok=False`, so the shared structural gate
    (`finding_files_malformed`) fails it closed on all three consumers rather than
    reading a mangled value as valid. Only declared forms are parsed; everything
    else is rejected as an invalid form (no claim of normalising "any depth").

    Normalisation applies ONLY to the scalars of a declared form. An inline comment
    (a `#` after whitespace — canonical YAML; `CF-0001#CF-0002` has no separator, so
    the `#...` is PART of the value, F-0043 human ruling #3) is stripped, and a YAML
    null scalar (`null`/`Null`/`NULL`/`~`) is folded to "" — the one input every
    finding value passes through — so no downstream predicate has to know a YAML
    spelling (CF-0001 human ruling #6). A flat inline list folds each element the
    same way, so a list of only null elements collapses to the empty `[]` the
    non-nullable gate rejects (ruling #7)."""
    m = re.search(r"\s#", v)
    s = (v[: m.start()] if m else v).strip()
    # A YAML BLOCK SCALAR header (`|`, `>`, with any chomp/indent indicator: `|-`, `>-`,
    # `|+`, `>2`) puts the real value on the FOLLOWING lines, which this flat line-based
    # parser never reads. Left unflagged, `charter: >-` parsed to the two-character string
    # `">-"`: a non-empty value that passes every predicate while the actual charter is
    # gone. Ouroboros-3 round 1 hit exactly this — the construal recorded a 2-char charter
    # and certified clean, so the record could not be checked against its own key. A block
    # scalar is not a §2-declared form, so it is REFUSED like any other undeclared form
    # (CF-0001 ruling #9: accept declared forms, do not normalise arbitrary YAML) rather
    # than silently emptied.
    if re.fullmatch(r"[|>][-+]?\d*", s):
        return s, False
    if s.startswith("{"):
        return s, False  # a flow mapping is not a declared §2 form
    if s.startswith("["):
        if not s.endswith("]"):
            return s, False  # an unclosed inline list (`[a`) is not a declared form
        elems = [e for e in re.split(r"[,\s]+", s[1:-1].strip()) if e]
        if any(_FLOW_MARKS.search(e) for e in elems):
            return s, False  # a nested list/mapping element (`[[null]]`, `[{a: b}]`)
        kept = [t for t in (_yaml_absent(e) for e in elems) if t]
        return "[" + ", ".join(kept) + "]", True
    return _yaml_absent(s), True

def _parse_frontmatter(block):
    """Parse a finding frontmatter block into (fields, duplicate_keys).

    Frontmatter is a flat YAML mapping — every `key: value` line is a SINGLETON
    (§2 canonical record). The historical loop assigned `fm[key] = value` line by
    line, so a second `status:` (or any repeated key) silently OVERWROTE the
    first and the record parsed to whichever line came LAST — a file physically
    holding two statuses of different owners resolved to one, order-dependently
    (F-0103). Value parsing is unchanged; this additionally records every repeated
    key so callers can fail closed on a corrupted record instead of picking one
    arbitrarily.

    A block YAML list (`unit:` on its own line followed by `  - B01` items) is folded
    into the preceding key as a comma-joined scalar so a list-valued canonical field
    (CF-0001 human ruling #5: `unit` is non-nullable and must name a unit) is NOT stored
    as an empty scalar — otherwise a real multi-unit finding would falsely fail the
    non-nullable gate. Inline lists (`unit: [B01, B02]`) already carry their value on the
    key line and are unaffected.

    A value or block-list element whose FORM is not one XCHECK.md §2 declares — a flow
    mapping, a nested list, an unclosed bracket, a non-scalar block element — is recorded
    as a bad-form field (returned alongside `dups`), so `finding_files_malformed` fails it
    closed on every consumer rather than a downstream predicate reading a mangled value as
    valid (CF-0001 human ruling #9: accept declared forms, do not normalise arbitrary YAML)."""
    fm, dups, seen, badform = {}, [], set(), []
    last_key = None
    for line in block.splitlines():
        item = re.match(r"^\s*-\s+(.*\S)\s*$", line)
        if item and last_key is not None:
            # A block-list element must be a plain scalar (the flat-list form §2 declares).
            # Strip comment + fold YAML-null (ruling #6) as on the scalar path; an element
            # that opens its OWN flow collection (`- [null]`, `- {a: b}`) is a nested
            # structure, not a scalar, so it is bad-form (ruling #9), not normalised.
            cm = re.search(r"\s#", item.group(1))
            e = (item.group(1)[: cm.start()] if cm else item.group(1)).strip()
            if _FLOW_MARKS.search(e):
                badform.append(last_key)
                continue
            val = _yaml_absent(e)
            if val:
                cur = fm.get(last_key, "")
                fm[last_key] = (cur + ", " + val) if cur else val
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        # F-0149: an INDENTED `key:` line is a nested-mapping member, not a §2 canonical
        # field — §2 declares a FLAT mapping, and stripping the indent used to promote
        # every nested key to root, so a record whose canonical fields exist only inside
        # an arbitrary nested object (`record:\n  id: ...`) parsed as a clean flat record
        # and routed a role. A nested mapping is an undeclared form (CF-0001 ruling #9):
        # record it as bad-form under its own key and do NOT promote it. Block-LIST
        # elements (`  - U01`) are consumed by the item branch above and stay legal.
        if line[:1] in (" ", "\t"):
            badform.append(k.strip())
            continue
        k = k.strip()
        if k in seen:
            dups.append(k)
        seen.add(k)
        val, ok = _fm_value(v)
        if not ok:
            badform.append(k)
        fm[k] = val
        last_key = k
    return fm, dups, badform


def _pipe_cells(s):
    r"""Cells of a GFM `| a | b |` pipe row, each stripped, with a backslash-escaped
    `\|` kept as a literal pipe INSIDE its cell rather than a column separator (F-0112):
    `| a | b |` -> ['a', 'b']; `| spec\|alias | drift |` -> ['spec|alias', 'drift'] (TWO
    cells, matching what GFM renders), so a missing further column is genuinely absent —
    callers treat an out-of-range column as empty (`_table_has_row_with`) — not forged
    from the escaped fragment. A plain `split('|')` read three cells and hid the gap."""
    s = s.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith(r"\|"):  # optional closing pipe, unless escaped
        s = s[:-1]
    return [c.replace(r"\|", "|").strip() for c in _PIPE_SPLIT_RE.split(s)]

def _is_delimiter_row(cells):
    """A GFM delimiter row: at least one cell, every cell a run of `-` with optional
    `:` alignment (`_DELIM_CELL_RE`). `['---', ':--']` is one; `['', '', '']` (dash-less)
    and `['key', 'x']` are not."""
    return bool(cells) and all(_DELIM_CELL_RE.match(c) for c in cells)


_PIPE_SPLIT_RE = re.compile(r"(?<!\\)\|")
_DELIM_CELL_RE = re.compile(r"^:?-+:?$")
