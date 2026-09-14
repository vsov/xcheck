"""`xcheck upgrade` — refresh an installed copy without overwriting the operator.

F-0146, the audit's «двойная истина»: `install.sh` copies `XCHECK.md` and the
templates into a project, and after that nobody owns keeping the two in sync. The
agents read `audit/XCHECK.md`, the developer edits the root `XCHECK.md`, and the
methodology, the implementation and the installed contract drift apart with nothing
reporting it.

The fix is not "copy harder". An upgrade that silently reconciles by overwriting is
how a project loses an operator's edit to its own audit plan. So this command works
from EVIDENCE rather than from optimism:

* `audit/MANIFEST.sha256` records what was shipped and its digest.
* A target file whose digest still matches the manifest is a file xcheck owns: it is
  refreshed.
* A target file whose digest DIFFERS carries the operator's own content: it is
  refused, named, and left exactly as it is.
* `AUDIT.md`, `LEDGER.md` and `state.json` are never shipped and never touched,
  whatever their digests say.

Everything is reported BEFORE anything is written, and `--dry-run` stops after the
report. An upgrade that tells you what it did is a changelog; one that tells you what
it will do is a decision you get to make.
"""

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from xcheck import __version__
from xcheck.state import SCHEMA_VERSION

PROVENANCE = ".provenance.json"
MANIFEST = "MANIFEST.sha256"

# The shipped set, relative to the repo root AND to `audit/` — the same paths on both
# sides, which is what lets a manifest entry name one file rather than a pair. Kept
# equal to install.sh's `SHIPPED` by tests/test_upgrade.py: two lists that disagree
# would install a file `upgrade` cannot see.
SHIPPED = (
    "XCHECK.md",
    "templates/finding.md",
    "templates/class-finding.md",
    "templates/pass.md",
    "templates/plan.md",
    "templates/construal.md",
)

# Never shipped, never refreshed, never overwritten — the operator's own content and
# the machine state. Listed explicitly so the rule is readable rather than implied by
# an absence above.
OPERATOR_OWNED = ("AUDIT.md", "LEDGER.md", "state.json", "events.jsonl",
                  "orchestrator.conf")

MARKS = {"refresh": "→", "install": "+", "current": "=", "refuse": "✗", "skip": "?"}


def digest(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def read_provenance(audit):
    p = Path(audit) / PROVENANCE
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_manifest(audit):
    """`{relative path: digest}` from `MANIFEST.sha256`; `{}` when absent."""
    out = {}
    try:
        text = (Path(audit) / MANIFEST).read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            out[parts[1].strip()] = parts[0].strip()
    return out


def source_ref(root):
    try:
        p = subprocess.run(["git", "describe", "--tags", "--always", "--dirty"],
                           cwd=str(root), capture_output=True, text=True, timeout=30)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def plan(project, root):
    """What an upgrade WOULD do, as data. Writes nothing.

    Returns one row per shipped file: `(relative path, action, why)`.
    """
    audit = Path(project) / "audit"
    manifest = read_manifest(audit)
    rows = []
    for rel in SHIPPED:
        new = digest(Path(root) / rel)
        have = digest(audit / rel)
        shipped = manifest.get(rel)
        if new is None:
            rows.append((rel, "skip", f"the source file {rel} is missing"))
        elif have is None:
            rows.append((rel, "install", "absent from the installed copy"))
        elif have == new:
            rows.append((rel, "current", "already identical to this version"))
        elif shipped is None:
            # No manifest entry: this copy predates provenance, or was installed with
            # no sha256 tool. We cannot prove xcheck shipped this file, so we do not
            # claim the right to replace it.
            rows.append((rel, "refuse", "no manifest entry — cannot prove xcheck "
                                        "shipped this file, so it is treated as yours"))
        elif have == shipped:
            rows.append((rel, "refresh", "unmodified since install"))
        else:
            rows.append((rel, "refuse", "MODIFIED since install — this carries your "
                                        "own content"))
    return rows


def _write_manifest(audit, rows):
    """Record what is now installed — the target's digests, not the source's.

    A refused file keeps its old entry, so it stays refused on the next run instead of
    being quietly adopted by a manifest that describes what we wished had happened.
    """
    old = read_manifest(audit)
    lines = []
    for rel, action, _why in rows:
        d = digest(audit / rel) if action in ("refresh", "install", "current") \
            else old.get(rel)
        if d:
            lines.append(f"{d}  {rel}")
    (audit / MANIFEST).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_provenance(audit, root):
    """Stamp the copy as THIS version.

    Called only when every shipped file is this version's. A copy still holding a file
    xcheck did not ship is not this version, and a stamp that claims otherwise is the
    same «двойная истина» one layer down — the provenance record would then be the
    drifting second truth instead of the file it describes.
    """
    (audit / PROVENANCE).write_text(json.dumps({
        "xcheck_version": __version__,
        "core_digest": digest(audit / "XCHECK.md"),
        "source_ref": source_ref(root),
        "schema_version": SCHEMA_VERSION,
        "installed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, indent=2) + "\n", encoding="utf-8")


def upgrade(project, dry_run=False, root=None, out=print):
    root = Path(root or Path(__file__).resolve().parent.parent)
    project = Path(project)
    audit = project / "audit"
    if not (audit / "XCHECK.md").is_file():
        out(f"upgrade: {audit}/XCHECK.md not found — this project has no installed copy "
            f"to upgrade. Run `install.sh {project}` first.")
        return 1

    prov = read_provenance(audit)
    rows = plan(project, root)
    blocked = [r for r in rows if r[1] == "refuse"]

    out(f"upgrade: {project}")
    if prov is None:
        out("  installed:  UNPROVENANCED — no audit/.provenance.json, so this copy "
            "predates provenance tracking (or was installed without a sha256 tool)")
    else:
        out(f"  installed:  xcheck {prov.get('xcheck_version', 'unknown')} "
            f"(schema {prov.get('schema_version', '?')}, "
            f"source {prov.get('source_ref', 'unknown')}, "
            f"{prov.get('installed_at', 'unknown')})")
    out(f"  running:    xcheck {__version__} (schema {SCHEMA_VERSION}, "
        f"source {source_ref(root)})")

    # The SCHEMA, not the release number, is what forces a migration. Saying which one
    # stopped you keeps a patch release from reading as "you must migrate" and a schema
    # bump from hiding inside one.
    installed_schema = (prov or {}).get("schema_version")
    if installed_schema is not None and installed_schema != SCHEMA_VERSION:
        out(f"\nstop: the installed copy is on state schema {installed_schema}; this "
            f"xcheck reads schema {SCHEMA_VERSION}. Refreshing the methodology files "
            f"without migrating the state would leave two schema generations sharing "
            f"one audit.\n      Run `xcheck migrate --project {project}` first, then "
            f"upgrade.")
        return 1

    out("\n  what this changes:")
    width = max(len(r[0]) for r in rows)
    for rel, action, why in rows:
        out(f"    {MARKS[action]} {rel:<{width}}  {action:<8} {why}")
    # Not "skipped" — REFUSED, and said in those words. `AUDIT.md` is the operator's
    # audit plan; an upgrade that overwrote it at any version, for any reason, would be
    # the exact failure F-0146 describes. It is never shipped, so it is never even a
    # candidate: the strongest form of "will not overwrite this" is "cannot address it".
    present = [n for n in OPERATOR_OWNED if (audit / n).exists()]
    for name in present:
        out(f"    ✗ {name:<{width}}  refuse   yours — never shipped, never overwritten, "
            f"at any version")

    if blocked or present:
        out(f"\nrefusing to overwrite {len(blocked) + len(present)} file(s):")
        for rel, _a, why in blocked:
            out(f"  - audit/{rel}: {why}")
        for name in present:
            out(f"  - audit/{name}: your own content — xcheck does not ship it and will "
                f"not replace it")
        out("  None of these are refreshed. Reconcile a modified shipped file yourself "
            "— diff it against the shipped version, keep what is yours — then re-run "
            "`xcheck upgrade`.")

    todo = [r for r in rows if r[1] in ("refresh", "install")]
    if dry_run:
        out(f"\ndry-run: nothing was written ({len(todo)} file(s) would change).")
        return 1 if blocked else 0
    if not todo:
        stamped = (prov or {}).get("xcheck_version")
        if blocked:
            # Do NOT restamp. A copy holding a file this version did not ship is not
            # this version, and a provenance record that says otherwise is the same
            # "двойная истина" one layer down.
            out(f"\nnothing written: every shipped file is either current or refused "
                f"({len(blocked)} refused — see above). The installed stamp stays at "
                f"{stamped or 'unprovenanced'}, because the copy is not {__version__}.")
            return 1
        if stamped != __version__:
            # Every shipped file is byte-identical to this version, so the new stamp
            # is a fact about the files rather than a wish about them.
            _write_manifest(audit, rows)
            _write_provenance(audit, root)
            out(f"\nnothing written: every shipped file already matches xcheck "
                f"{__version__} — provenance restamped from {stamped or 'none'}.")
        else:
            out(f"\nnothing to do: audit/ is xcheck {__version__}.")
        return 0

    for rel, _action, _why in todo:
        dst = audit / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes((root / rel).read_bytes())
    _write_manifest(audit, rows)

    # The manifest always records what landed; the VERSION stamp advances only when the
    # whole shipped set is this version's. Those are two different claims — "here is
    # what is installed" and "this copy is 0.8.0" — and a partial upgrade can only
    # honestly make the first.
    if blocked:
        stamped = (prov or {}).get("xcheck_version") or "unprovenanced"
        out(f"\nupgraded: {len(todo)} file(s) written, manifest updated. "
            f"{len(blocked)} refused (see above), so the installed stamp stays at "
            f"{stamped} — a copy holding a file this version did not ship is not "
            f"{__version__}.")
        return 1
    _write_provenance(audit, root)
    out(f"\nupgraded: {len(todo)} file(s) written; provenance and manifest restamped "
        f"to xcheck {__version__}.")
    return 0
