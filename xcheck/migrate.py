"""`xcheck migrate` — the one-shot, operator-run converter from a Markdown audit.

This is the only code that is allowed to read `xcheck.migrate_readers`. It runs
once per tree, by an explicit operator command, and it is reversible: deleting
the `state.json` it wrote leaves the original Markdown exactly as it was.

Three rules shape everything below.

**It never rewrites admitted evidence.** Finding bodies, verdicts, rulings and
construal records are what the audit *is*. The converter reads them and writes a
new file beside them; `--dry-run` writes nothing at all, not even an mtime.

**It refuses rather than drops.** A legacy tree with an unresolvable dimension
does not get to become a valid state document by having the problem thrown away.
Every defect is named, and one defect is enough to refuse the whole conversion —
a half-migrated tree is the dual-authority state this whole plan removes.

**Every normalisation is reported, none is silent.** Legacy trees spell machine
fields differently from the schema: `recurrence-of` for `recurrence_of`, `null`
for absent, a bare `XCHECK.md` where the unit map says `U01`. Translating an
encoding is the converter's job; doing it invisibly is not, because the operator
is the one who has to decide whether the translation is right. Each substitution
appears in the report, and `--dry-run` exists so it can be read before anything
is written.
"""

import re
import shutil
from pathlib import Path

from xcheck.md_prose import (FRONTMATTER_RE, LIMIT_KEYS, _DIMENSIONS_RE,
                             _parse_frontmatter, _section_table)
from xcheck.migrate_readers import (_NORMS_RE, _QUEUE_ENTRY_RE, _QUEUE_RE, _UNITMAP_RE,
                                    _header_col, _section_bodies, load_construal_files,
                                    load_finding_files, parse_ledger, parse_limits)
from xcheck.state import (SCHEMA_VERSION, STATE_FILENAME, StateError, _build, _validate,
                          git_head, next_owner, write_state)
from xcheck.util import _read_audit_text
from xcheck.views import write_views

# Where the pre-migration bytes of every regenerated view go. Migration is
# reversible by the operator's Stage-1 decision, and "reversible" has to survive
# the step that rewrites LEDGER.md and 29 frontmatter blocks — otherwise undoing
# it means reading git history, which a third-party tree converted outside a repo
# does not have.
BACKUP_DIR = ".migration-backup"

# Legacy frontmatter key -> schema field. The keys that differ are exactly the
# hyphenated ones; `class` and `pass` keep their spelling in the document.
FIELD_MAP = {"recurrence-of": "recurrence_of", "norm-ruling": "norm_ruling",
             "admitted-scope": "admitted_scope", "fixed-by": "fixed_by",
             "created-by": "created_by", "admitted-by": "admitted_by",
             "admitted-at": "admitted_at"}

FINDING_ID_RE = re.compile(r"^(F|CF)-\d{4}$")
_PASS_KEYS = ("id", "dimension", "units", "status", "findings", "updated")


class Report:
    """What the converter found, what it translated, and what it refuses on."""

    def __init__(self):
        self.counts = {}
        self.normalisations = []
        self.defects = []
        self.rollup = {}

    def note(self, msg):
        self.normalisations.append(msg)

    def rename(self, old, new, where):
        """A purely mechanical key rename, counted rather than listed.

        The hyphen-to-underscore renames are the same translation on every record
        — 140 of them in a 29-finding tree. Printing one line each buries the
        handful of normalisations an operator actually has to look at (a material
        resolved to a unit id, a missing field defaulted) under noise nobody
        reads. They are still counted, and the count is still part of the total.
        """
        self.rollup.setdefault((old, new), set()).add(where)

    def defect(self, msg):
        self.defects.append(msg)

    def render(self, audit_dir, dry_run):
        head = "would convert" if dry_run else "converted"
        out = [f"xcheck migrate: {head} {audit_dir}", ""]
        for k in ("findings", "class_findings", "plans", "construals", "queue",
                  "coverage records", "norms", "dimensions", "units", "limits"):
            if k in self.counts:
                out.append(f"  {k:<16} {self.counts[k]}")
        rolled = sum(len(v) for v in self.rollup.values())
        if self.normalisations or self.rollup:
            out.append("")
            out.append(f"  {len(self.normalisations) + rolled} normalisation(s) — the "
                       f"legacy encoding translated into the schema's:")
            for (old, new), where in sorted(self.rollup.items()):
                out.append(f"    - `{old}` -> `{new}` in {len(where)} file(s)")
            for n in self.normalisations:
                out.append(f"    - {n}")
        if self.defects:
            out.append("")
            out.append(f"  {len(self.defects)} defect(s) — nothing was written:")
            for d in self.defects:
                out.append(f"    - {d}")
        return "\n".join(out)


# ---------------------------------------------------------------------------
# catalogs
# ---------------------------------------------------------------------------

def _rows(text, heading_re, columns, label, report):
    """The named columns of one AUDIT.md catalog table, as dicts. A column the
    header does not declare is a defect, not an empty string: a catalog read
    through a header it does not have is a guess."""
    header, data = _section_table(text, heading_re)
    if header is None:
        report.defect(f"AUDIT.md has no {label} table — the catalog a finding's "
                      f"`dimension` and `unit` resolve against cannot be empty")
        return []
    idx = {}
    for name in columns:
        i = _header_col(header, name)
        if i is None:
            report.defect(f"the {label} table has no {name!r} column (header: "
                          f"{' | '.join(header)})")
            return []
        idx[name] = i
    out = []
    for cells in data:
        out.append({name: (cells[i].strip() if i < len(cells) else "")
                    for name, i in idx.items()})
    return out


def _bare(cell):
    """A catalog cell without its Markdown decoration: `` `XCHECK.md` `` -> XCHECK.md."""
    return cell.strip().strip("`").strip()


def _catalogs(text, report):
    norms = [{"id": r["id"], "source": _bare(r["source"]), "scope": r["scope"]}
             for r in _rows(text, _NORMS_RE, ("id", "source", "scope"),
                            "Norms catalog", report) if r["id"]]
    dims = []
    for r in _rows(text, _DIMENSIONS_RE, ("key", "what it catches", "norm source"),
                   "Dimensions", report):
        if not r["key"]:
            continue
        ids = [n for n in re.findall(r"\bN\d+\b", r["norm source"])]
        if not ids:
            report.defect(f"dimension {r['key']!r}: its `norm source` cell "
                          f"({r['norm source']!r}) names no norm id — a dimension that "
                          f"resolves to no norm cannot carry §6 rule 2 evidence")
            continue
        seen = list(dict.fromkeys(ids))
        if seen != ids:
            report.note(f"dimension {r['key']}: `norm source` repeated a norm id; "
                        f"kept one of each ({', '.join(seen)})")
        dims.append({"key": r["key"], "catches": r["what it catches"], "norms": seen})
    units = []
    header, _ = _section_table(text, _UNITMAP_RE)
    # The size column's HEADER text is not fixed by the methodology — shipped plans
    # spell it `approx. size` and `approx. size / boundary` — so it is located by the
    # word rather than by an exact name, and its absence is a normalisation, not a
    # defect: `size` is descriptive, and refusing a whole tree over a prose column
    # would be the converter dropping work it can do.
    size_col = None
    for i, c in enumerate(header or []):
        if "size" in c.lower():
            size_col = i
            break
    if header and size_col is None:
        report.note("the Unit map has no size column; every unit records "
                    "`size: unrecorded`")
    for r in _rows(text, _UNITMAP_RE, ("unit", "material", "responsibility"),
                   "Unit map", report):
        if not r["unit"]:
            continue
        units.append({"id": r["unit"], "material": _bare(r["material"]),
                      "size": r.get("_size") or "unrecorded",
                      "responsibility": r["responsibility"]})
    if size_col is not None:
        _, data = _section_table(text, _UNITMAP_RE)
        for u, cells in zip(units, [c for c in data if c and c[0].strip()]):
            if size_col < len(cells) and cells[size_col].strip():
                u["size"] = cells[size_col].strip()
    return norms, dims, units


def _unit_resolver(units, report):
    """A function turning a legacy `unit:` value into unit ids, or naming why not.

    Real trees write the MATERIAL where the schema wants the unit id — 10 of
    ouroboros-3's 29 findings say `unit: XCHECK.md`. That is an encoding
    difference, not a defect, so it is translated when the unit map resolves it
    to exactly ONE unit and refused when it resolves to none or several.
    """
    ids = {u["id"] for u in units}
    by_material = {}
    for u in units:
        by_material.setdefault(u["material"], []).append(u["id"])

    def resolve(raw, where):
        parts = [p for p in re.split(r"[,\s]+", (raw or "").strip().strip("[]")) if p]
        if not parts:
            report.defect(f"{where}: `unit` is empty — §2 makes it non-nullable")
            return None
        out = []
        for p in parts:
            if p in ids:
                out.append(p)
                continue
            hits = by_material.get(_bare(p), [])
            if len(hits) == 1:
                report.note(f"{where}: `unit: {p}` is a MATERIAL, not a unit id; the "
                            f"unit map resolves it to {hits[0]}")
                out.append(hits[0])
            elif not hits:
                report.defect(f"{where}: `unit: {p}` is neither a unit id nor a "
                              f"material in the AUDIT.md Unit map")
                return None
            else:
                report.defect(f"{where}: `unit: {p}` is a material claimed by "
                              f"{', '.join(hits)} — ambiguous, so the converter will "
                              f"not choose")
                return None
        return list(dict.fromkeys(out))

    return resolve


# ---------------------------------------------------------------------------
# records
# ---------------------------------------------------------------------------

def _clean(fm, where, report):
    """Legacy frontmatter -> schema fields: renamed keys, YAML nulls dropped.

    A `_malformed`, `_dupkeys` or `_badform` marker from the legacy parser is a
    defect. Those are the records the old readers themselves could not resolve;
    carrying them across would launder an ambiguity into a clean document.
    """
    if fm.get("_malformed"):
        report.defect(f"{where}: no frontmatter block — the legacy reader could not "
                      f"resolve this file either")
        return None
    if fm.get("_dupkeys"):
        report.defect(f"{where}: duplicate key(s) {', '.join(fm['_dupkeys'])} — two "
                      f"values for one field is not a record (F-0103)")
        return None
    if fm.get("_badform"):
        report.defect(f"{where}: field(s) {', '.join(fm['_badform'])} are not a "
                      f"declared §2 form (CF-0001 ruling #9)")
        return None
    out = {}
    for k, v in fm.items():
        if k.startswith("_"):
            continue
        key = FIELD_MAP.get(k, k)
        if key != k:
            report.rename(k, key, where)
        if v is None or str(v).strip() == "":
            continue                       # `null`, already folded to "" by the reader
        out[key] = str(v).strip()
    return out


def _int(fields, name, where, report, default=0):
    raw = fields.get(name)
    if raw is None:
        report.note(f"{where}: no `{name}`; recorded as {default}")
        return default
    try:
        return int(raw)
    except ValueError:
        report.defect(f"{where}: `{name}: {raw}` is not an integer")
        return None


def _findings(audit_dir, resolve, report):
    findings, classes = [], []
    for fm in load_finding_files(audit_dir):
        where = f"findings/{fm.get('_file', '?')}"
        f = _clean(fm, where, report)
        if f is None:
            continue
        fid = f.get("id")
        if not fid or not FINDING_ID_RE.match(fid):
            report.defect(f"{where}: `id: {fid!r}` is not a canonical finding id")
            continue
        units = resolve(f.get("unit"), where)
        attempts = _int(f, "attempts", where, report)
        if units is None or attempts is None:
            continue
        rec = {"id": fid, "title": f.get("title", ""), "severity": f.get("severity", ""),
               "status": f.get("status", ""), "dimension": f.get("dimension", ""),
               "unit": units, "pass": f.get("pass", ""), "attempts": attempts,
               "updated": f.get("updated", ""), "body_path": where}
        for opt in ("fixed_by", "recurrence_of", "refusal", "admitted_scope",
                    "blocked", "norm_ruling", "class", "created_by"):
            if f.get(opt):
                rec[opt] = f[opt]
        if fid.startswith("CF-"):
            members = [m for m in re.split(r"[,\s]+", (f.get("members") or "").strip("[]"))
                       if m]
            if not members:
                report.defect(f"{where}: a class finding with no `members` (F-0151) — "
                              f"the schema has no representation for one")
                continue
            rec["members"] = members
            # A CF is itself the class; templates ship `class: null` on it, and the
            # schema has no `class` field on a class finding at all.
            rec.pop("class", None)
            classes.append(rec)
        elif f.get("members"):
            report.defect(f"{where}: an ordinary finding carrying `members` — only a "
                          f"CF-NNNN record has a membership list (§2)")
        else:
            findings.append(rec)
    return findings, classes


# A line that names a pass, whether or not it parses as one. `_QUEUE_ENTRY_RE` is
# the strict form; this is the SUBJECT. Anything matching here and not there is a
# pass the converter would have lost.
_QUEUE_CANDIDATE_RE = re.compile(r"^\s*-\s*\[[ x]\]\s*P-\d{2}\b")


def _queue(text, report):
    bodies = _section_bodies(text, _QUEUE_RE)
    if len(bodies) != 1:
        report.defect(f"AUDIT.md declares the Pass queue section {len(bodies)} times — "
                      f"exactly one is a queue (F-0156)")
        return []
    out = []
    for line in bodies[0].splitlines():
        m = _QUEUE_ENTRY_RE.match(line)
        if not m:
            # A checkbox line carrying a P-NN that does not parse as a full entry is
            # a DEFECT, not a non-entry. Dropping it silently is how a 39-pass queue
            # converts to an empty one and the state document then reports the audit
            # complete — the same shape as F-0156 read from the other side. The
            # ouroboros-2 tree is exactly this case: its entries predate the
            # `; stop:` clause, so every one of them fails the strict form.
            if _QUEUE_CANDIDATE_RE.match(line):
                report.defect(
                    f"AUDIT.md pass queue: {line.strip()[:80]!r} names a pass but is "
                    f"not a full entry (`- [ ] P-NN — dimension × units: charter; "
                    f"stop: ...`); it would have been dropped, and a dropped pass is "
                    f"a queue that reports itself finished")
            continue
        units = [u for u in re.split(r"[,\s]+", m.group(4)) if u]
        out.append({"id": m.group(2), "dimension": m.group(3), "units": units,
                    "charter": m.group(5), "stop": m.group(6),
                    "done": m.group(1) == "x"})
    return out


def _read_coverage(audit_dir, queue, report):
    """Attach each done pass's ONE canonical report. A `done` pass with no such
    record is refused: in the schema a done pass without coverage is not a
    rejected document, it is an unrepresentable one (F-0154)."""
    pdir = audit_dir / "passes"
    n = 0
    for q in queue:
        if not q["done"]:
            continue
        found = []
        for f in sorted(pdir.glob(f"{q['id']}-*.md")) if pdir.is_dir() else []:
            m = FRONTMATTER_RE.match(_read_audit_text(f))
            if not m:
                continue
            fm, dups, badform = _parse_frontmatter(m.group(1))
            if dups or badform or set(fm) != set(_PASS_KEYS) or fm.get("id") != q["id"]:
                continue
            found.append((f, fm))
        if len(found) != 1:
            report.defect(
                f"pass {q['id']} is checked but {len(found)} canonical report(s) match "
                f"`passes/{q['id']}-*.md` — a pass is documented by exactly one record "
                f"carrying {', '.join(sorted(_PASS_KEYS))} (§4 rule 5, F-0154)")
            continue
        f, fm = found[0]
        fids = [x for x in re.split(r"[,\s]+", (fm.get("findings") or "").strip("[]")) if x]
        q["coverage"] = {"report_path": f"passes/{f.name}", "findings": fids,
                         "updated": fm.get("updated", ""),
                         "status": fm.get("status", "")}
        n += 1
    report.counts["coverage records"] = n


def _plans(audit_dir, report):
    out = []
    pdir = audit_dir / "plans"
    for f in sorted(pdir.glob("*.md")) if pdir.is_dir() else []:
        where = f"plans/{f.name}"
        m = FRONTMATTER_RE.match(_read_audit_text(f))
        if not m:
            report.defect(f"{where}: no frontmatter block")
            continue
        fm, dups, badform = _parse_frontmatter(m.group(1))
        rec = _clean(dict(fm, _dupkeys=dups or None, _badform=badform or None),
                     where, report)
        if rec is None:
            continue
        fids = [x for x in re.split(r"[,\s]+", (rec.get("findings") or "").strip("[]"))
                if x]
        entry = {"id": rec.get("id", ""), "findings": fids,
                 "status": rec.get("status", ""), "updated": rec.get("updated", ""),
                 "body_path": where}
        if rec.get("attempts"):
            n = _int(rec, "attempts", where, report)
            if n is None:
                continue
            entry["attempts"] = n
        out.append(entry)
    return out


def _construals(audit_dir, report):
    out = []
    for fm in load_construal_files(audit_dir):
        where = f"construals/{fm.get('_file', '?')}"
        rec = _clean(fm, where, report)
        if rec is None:
            continue
        entry = {"key": rec.get("key", ""), "role": rec.get("role", ""),
                 "charter": rec.get("charter", ""), "session": rec.get("session", ""),
                 "status": rec.get("status", ""), "created": rec.get("created", ""),
                 "body_path": where}
        for opt in ("admitted_by", "admitted_at", "envelope"):
            if rec.get(opt):
                entry[opt] = rec[opt]
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# the conversion
# ---------------------------------------------------------------------------

def read_legacy(audit_dir: Path, generated_by="xcheck migrate"):
    """(document, report) for a Markdown audit tree. Writes nothing, ever."""
    audit_dir = Path(audit_dir)
    report = Report()
    audit_md = audit_dir / "AUDIT.md"
    if not audit_md.is_file():
        report.defect(f"{audit_md} does not exist — there is no plan to convert")
        return None, report
    text = _read_audit_text(audit_md)

    norms, dims, units = _catalogs(text, report)
    resolve = _unit_resolver(units, report)
    findings, classes = _findings(audit_dir, resolve, report)
    queue = _queue(text, report)
    _read_coverage(audit_dir, queue, report)
    plans = _plans(audit_dir, report)
    construals = _construals(audit_dir, report)
    limits = {k: v for k, v in parse_limits(audit_dir).items() if k in LIMIT_KEYS}

    report.counts.update({
        "findings": len(findings), "class_findings": len(classes),
        "plans": len(plans), "construals": len(construals), "queue": len(queue),
        "norms": len(norms), "dimensions": len(dims), "units": len(units),
        "limits": len(limits)})

    doc = {"schema_version": SCHEMA_VERSION, "state_revision": 1,
           "generated_by": generated_by, "head_before": None,
           "findings": findings, "class_findings": classes, "queue": queue,
           "plans": plans, "construals": construals, "sessions": [],
           "limits": limits,
           "catalogs": {"norms": norms, "dimensions": dims, "units": units}}
    return doc, report


def _ledger_next_notes(audit_dir, state, report):
    """Report every legacy LEDGER row whose `next` disagreed with the owner the
    status implies. The column is not carried across: `next` was a second place
    to say who acts, and a stored copy that can disagree with its own status is
    the exact shape of the defect this migration removes."""
    by_id = {r.id: r for r in list(state.findings) + list(state.class_findings)}
    for row in parse_ledger(audit_dir):
        rec = by_id.get(row["id"])
        if rec is None:
            continue
        derived = next_owner(rec)
        if row["next"] and row["next"] != derived:
            report.note(f"LEDGER row {row['id']}: `next: {row['next']}` disagreed with "
                        f"status {rec.status!r}; the view now derives {derived!r}")


def migrate(project: Path, dry_run=False):
    """The `xcheck migrate` verb. Returns a process exit code."""
    project = Path(project)
    audit_dir = project / "audit"
    target = audit_dir / STATE_FILENAME
    if target.exists() and not dry_run:
        raise SystemExit(
            f"xcheck migrate: {target} already exists — this tree has already been "
            f"converted. Migration is a one-shot operator act, never a silent "
            f"overwrite of live state; move the existing document aside if you truly "
            f"mean to re-convert.")

    doc, report = read_legacy(audit_dir)
    if doc is not None and not report.defects:
        try:
            _validate(doc, str(target))
            state = _build(doc)
            _ledger_next_notes(audit_dir, state, report)
        except StateError as e:
            report.defect(f"the converted document fails the schema: {e}")
            state = None
    else:
        state = None

    print(report.render(audit_dir, dry_run))

    if report.defects:
        print(f"\nxcheck migrate: refusing — {len(report.defects)} defect(s) above. "
              f"Nothing was written. A legacy tree does not become a valid state "
              f"document by having its problems dropped; fix the Markdown, or record "
              f"the defect and convert what is genuinely convertible.")
        return 1
    if dry_run:
        print("\nxcheck migrate: --dry-run, nothing written. Re-run without --dry-run "
              "to write audit/state.json and regenerate the views.")
        return 0

    write_state(audit_dir, state, bump=False, head_before=git_head(project))
    kept = _backup_views(state, audit_dir)
    written = write_views(state, audit_dir)
    print(f"\nxcheck migrate: wrote {target} (schema {SCHEMA_VERSION}, "
          f"revision {state.state_revision})")
    print(f"xcheck migrate: regenerated {len(written)} view file(s) from it — "
          f"LEDGER.md and one frontmatter block per record; every evidence body is "
          f"untouched below its block")
    print(f"xcheck migrate: kept the pre-migration bytes of {kept} file(s) in "
          f"audit/{BACKUP_DIR}/")
    # The undo is spelled out as three literal commands rather than described,
    # because "restore the backup" is the step an operator improvises at the worst
    # possible moment. It is written against the audit directory, not against git:
    # a third-party tree may not be a repository at all, and `git checkout` would
    # then be advice that destroys the answer instead of restoring it.
    print("xcheck migrate: to undo, from the project root —")
    print("    rm audit/state.json")
    print(f"    cp -R audit/{BACKUP_DIR}/. audit/")
    print(f"    rm -rf audit/{BACKUP_DIR}")
    print("  the result is byte-identical to what was here before the migration.")
    return 0


def _backup_views(state, audit_dir: Path):
    """Copy every file `write_views` is about to overwrite into `audit/.migration-backup/`.

    Without this, "reversible" would mean "recoverable from git" — which is not
    true of a third-party audit tree converted outside a repository, and is
    exactly the operator the migration verb exists for.
    """
    dest = audit_dir / BACKUP_DIR
    n = 0
    for rel in ["LEDGER.md"] + [r.body_path for r in
                                list(state.findings) + list(state.class_findings)]:
        src = audit_dir / rel
        if not src.is_file():
            continue
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out)
        n += 1
    return n
