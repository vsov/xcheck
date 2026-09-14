"""Map every non-terminal finding onto HEAD, and PROPOSE. Never write.

PHASE 14 (fourth audit). 133 findings sit in `reported`, filed against commits that have
since moved. Until the backlog is reconciled with HEAD the decision engine keeps routing
remediation at defects that are already fixed — the audit's worked example is F-0098,
fixed in `d4ba983` while its finding sat unchanged.

WHAT THIS MODULE MAY NOT DO. It changes no status and holds no capability to. A status
transition is human-owned (README §5), and a tool that read the code and moved findings by
itself would be deciding the one thing the whole design reserves for a person. It imports
nothing that writes — asserted structurally by `tests/test_reconcile.py`, not merely
promised here — and its output is a document with the exact `xcheck` commands an operator
runs if they agree.

THE ANCHOR PROBLEM, measured before this was written: 0 of the 133 findings carry a
`locator` or a `source_hash`. Both became required at phase 9 of the THIRD audit and the
entire corpus predates them, so the field the reconciler would naturally key on is empty
on every record. What the corpus does have is evidence BODIES, and those carry two things
a machine can check:

    at `some/module.py:120-138`, ...
    > `the exact source line the finding is about`

(illustrative, and deliberately not a real line from this tree: a searcher whose own
documentation quotes the string it searches for finds itself —
`self-scanning-detector-needs-a-split-needle`. Quoting F-0098's actual `subprocess`
line here made `tree_contains` report it as surviving in this very file, and the
worked example flipped from `the quote is gone` to `the quote survives elsewhere`.)

a path reference (133 of 133 findings) and verbatim quoted source (93 of 133). The quote
is the stronger anchor by far: a line number drifts with every edit above it, and a
literal string either survives in the file or it does not. So the classifier keys on the
quote where there is one and says so where there is not — `anchor-missing` is a
first-class verdict here, not a rounding error, and a reconciler that guessed rather than
returning it would be the exact failure this document exists to avoid.

PHASE 8 (fifth audit) narrowed what the verdicts CLAIM. They speak about anchors only:
`anchor-unchanged`, `anchor-changed`, `anchor-missing`, `possible-duplicate`. The
previous six included `already-fixed`, which one added space could produce over a defect
that still behaves exactly as filed.
"""

import hashlib
import re
import subprocess
from collections import Counter, namedtuple
from pathlib import Path

from xcheck import util

# THE FOUR VERDICTS, closed, and every one of them is a statement about an ANCHOR.
#
# PHASE 8 (fifth audit). The previous vocabulary said `already-fixed`, and the audit
# broke it with one added space: a guard that returns True for every token keeps
# returning True, its quoted line no longer matches, and the reconciler answered
# `already-fixed` — then proposed a transition §5 does not allow from `reported` at all.
# Two defects in one row: a claim the evidence cannot support, and a command that would
# be refused if anyone typed it.
#
# A vanished quote is evidence that an ANCHOR MOVED. Nothing more. What earns the word
# `fixed` is a reproducer that no longer reproduces, or a human verification — neither
# of which this module can perform, and both of which it now names instead of guessing.
ANCHOR_UNCHANGED = "anchor-unchanged"
ANCHOR_CHANGED = "anchor-changed"
ANCHOR_MISSING = "anchor-missing"
POSSIBLE_DUPLICATE = "possible-duplicate"
VERDICTS = (ANCHOR_UNCHANGED, ANCHOR_CHANGED, ANCHOR_MISSING, POSSIBLE_DUPLICATE)

# What each verdict licenses, in one sentence, printed beside the row. These four
# distinctions are checkable; two of the six they replace were not.
MEANS = {
    ANCHOR_UNCHANGED: "the line this finding quotes is still in HEAD, byte for byte",
    ANCHOR_CHANGED: "the quoted line is no longer at its locator, so the code was "
                    "edited. Whether the DEFECT is gone is a different question, and "
                    "one added space moves an anchor without changing behaviour",
    ANCHOR_MISSING: "there is nothing to compare: the file is gone from HEAD, or the "
                    "body carries no quoted line",
    POSSIBLE_DUPLICATE: "another finding shares this unit and title; acting on both "
                        "would remediate one defect twice",
}

# The sentence that stops a census being read as a fix report. Printed at the head of
# the proposal document and again in the summary.
NOT_A_FIX_VERDICT = (
    "An anchor verdict is NOT a fix verdict. `anchor-changed` says the quoted line "
    "moved. A defect is FIXED when a reproducer that used to reproduce it no longer "
    "does, or when a human verifies it and `xcheck record-verdict` records who. Nothing "
    "in this report establishes either, and no proposal below claims to.")

# The transition each verdict would propose, or None where it licenses no status change.
# The COMMAND is not spelled here: what is legal depends on the finding's current status,
# so it is derived per row against `util.TRANSITIONS` — the table `set-status` itself
# refuses against. `fixed` appears nowhere in this mapping and cannot: §5 reserves it for
# `record-fix`, which records a provenance this module has no way to know.
WANTS = {
    ANCHOR_UNCHANGED: "accepted",
    ANCHOR_CHANGED: None,
    ANCHOR_MISSING: None,
    POSSIBLE_DUPLICATE: None,
}


def proposal_for(verdict, status, fid, body=None, other=None):
    """The command an operator can actually type, or a comment saying why there is none.

    PHASE 8 (fifth audit). The old table mapped a verdict straight to a command string,
    so `already-fixed` printed `xcheck set-status F-0001 fixed` for a finding sitting in
    `reported` — which §5 refuses outright. A proposal the CLI would reject is not
    advice; it is a bug that spends the one resource this module exists to save.

    Every command leaving here is checked against `util.TRANSITIONS` and `util.RESERVED`
    from the row's OWN status first. Where nothing is legal the row says so and says what
    to do instead: a comment an operator can read beats a command that errors.
    """
    want = WANTS.get(verdict)
    if want is None:
        if verdict == POSSIBLE_DUPLICATE:
            return (f"# confirm against {other} first — whichever is kept, only one of "
                    f"the two should be acted on")
        if verdict == ANCHOR_CHANGED:
            return ("# no status change: an anchor moved, which is not a fix. Re-run "
                    "the reproducer, or `xcheck record-verdict` after a human check")
        return f"# read audit/{body} — nothing here can decide this one"
    allowed = util.TRANSITIONS.get(status, set())
    if want not in allowed:
        return (f"# no command: `{fid}` is `{status}` and §5 allows "
                f"{sorted(allowed) or 'no transition'} from there, not `{want}`")
    if (status, want) in util.RESERVED:
        return (f"# `{status}` -> `{want}` needs `xcheck {util.RESERVED[(status, want)]}`"
                f" — it carries provenance `set-status` cannot record")
    return f"xcheck set-status {fid} {want}"


# Statuses this module has nothing to say about: a finding a human has already ruled on is
# not backlog.
TERMINAL = ("closed", "rejected", "verified", "superseded")

Anchor = namedtuple("Anchor", "path line quote")
Row = namedtuple("Row", "id status verdict anchors evidence action")

# `path/to/file.py:120-138` inside backticks. The extensions are enumerated rather than
# matched loosely, because a bare `something.md` in prose is not a claim about a file.
_LOC = re.compile(r"`([A-Za-z0-9_./-]+\.(?:py|md|sh|toml|yml|yaml|json|txt))"
                  r"(?::(\d+)(?:-\d+)?)?`")
# A blockquote line, which is how every evidence body in this corpus carries source.
_QUOTE = re.compile(r"(?m)^>\s?(.+)$")
# Short quotes match anything. 25 characters is where a line stops being a fragment that
# could appear in unrelated code; below it the anchor is noise wearing evidence's clothes.
MIN_QUOTE = 25


def anchors(body_text):
    """Every (path, line, quote) this evidence body claims, most specific first.

    Quotes are attached to the LAST path mentioned before them, which is how these bodies
    are written: a path reference, then the lines it refers to. A quote with no preceding
    path is kept with `path=None` — it can still be searched for tree-wide, which is what
    separates a line that MOVED from a file that is gone.
    """
    out, current = [], None
    for line in (body_text or "").splitlines():
        found = _LOC.search(line)
        if found:
            current = (found.group(1), int(found.group(2)) if found.group(2) else None)
            if not line.lstrip().startswith(">"):
                out.append(Anchor(current[0], current[1], None))
                continue
        q = _QUOTE.match(line.strip())
        if q:
            text = q.group(1).strip().strip("`").strip()
            if len(text) >= MIN_QUOTE:
                out.append(Anchor(current[0] if current else None,
                                  current[1] if current else None, text))
    return tuple(out)


def _read_text(root, rel):
    try:
        return (Path(root) / rel).read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None


def _sha256(root, rel):
    text = _read_text(root, rel)
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text is not None else None


def _git_read(root, *args, timeout=20):
    try:
        p = subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                           text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return p.stdout.strip() if p.returncode == 0 else ""


def removing_commit(root, quote, path=None):
    """The commit that removed this literal from the tree, where git can say.

    `git log -S` is a pickaxe search: it finds commits where the number of occurrences of
    the string CHANGED. That is the evidence behind `anchor-changed` — an edit, dated
    into a citation — the audit's F-0098 was fixed in `d4ba983` and this is how a reader
    checks that claim without taking anyone's word for it.

    Scoped to the path when there is one, because an unscoped pickaxe over a whole history
    is slow enough that nobody would run the reconciler.
    """
    # The subcommand is a LITERAL first argument, not the head of a list that gets
    # splatted: `tests/test_reconcile.py` reads the call sites to prove this module can
    # only reach read-only git, and a verb hidden inside a list is a verb that check
    # cannot see. It passed reporting "runs only ['grep']" while `log` was invisible to
    # it — a check covering less than it claimed.
    args = ["-1", "--format=%h %ad %s", "--date=short", f"-S{quote}"]
    if path:
        args += ["--", path]
    return _git_read(root, "log", *args, timeout=60)


def tree_contains(root, quote):
    """Whether this literal survives ANYWHERE in the tracked tree. Separates a file that
    moved from a defect that was fixed."""
    if not quote:
        return None
    # `audit/` is EXCLUDED, and leaving it in was the first thing this got wrong. The
    # audit tree is where findings quote source, so a tree-wide search for a finding's own
    # evidence finds the finding — F-0098's defect line came back "surviving in
    # audit/findings/F-0098-….md", which is the file that quoted it. Every anchor would
    # have answered `anchor-changed` for the same reason
    # (`a-fixture-in-the-wrong-directory-answers-every-arm`).
    hit = _git_read(root, "grep", "-lF", quote, "--", ".", ":(exclude)audit/")
    return [ln for ln in hit.splitlines() if ln] if hit else []


def verdict_for(finding, root, duplicates=None):
    """One finding's verdict against HEAD, with the evidence that produced it.

    The order is deliberate. POSSIBLE_DUPLICATE first, because a duplicate's own anchors
    would otherwise produce a confident verdict on a finding that should not be acted on
    at all. Then quote-based verdicts, which are checkable. Then path-only ones, which are
    weaker and say so. Anything left is ANCHOR_MISSING — reached by falling through every
    check that could have decided, never by a branch that gave up early.

    Every verdict here is about the ANCHOR. None of them is about whether the defect
    still behaves the way the finding says it does; `NOT_A_FIX_VERDICT` is printed
    wherever these rows are, because the previous vocabulary invited exactly that reading.
    """
    fid = getattr(finding, "id", None)
    body = getattr(finding, "body_path", None)
    ev = {"id": fid, "locator": None, "source_hash_then": None,
          "source_hash_now": None, "commit": None, "quotes": 0, "paths": 0,
          "note": None}

    if duplicates and fid in duplicates:
        ev["note"] = (f"same unit and title as {duplicates[fid]}; whichever is kept, "
                      f"acting on both would remediate one defect twice")
        return Row(fid, finding.status, POSSIBLE_DUPLICATE, (), ev,
                   proposal_for(POSSIBLE_DUPLICATE, finding.status, fid,
                                other=duplicates[fid]))

    text = _read_text(root, f"audit/{body}") if body else None
    found = anchors(text) if text else ()
    quoted = [a for a in found if a.quote]
    paths = [a.path for a in found if a.path]
    ev["quotes"], ev["paths"] = len(quoted), len(set(paths))
    ev["locator"] = f"{paths[0]}:{found[0].line}" if paths and found[0].line else (
        paths[0] if paths else None)
    # `then` is genuinely unknown for this corpus and is recorded as such rather than
    # left blank: 0 of 133 findings carry a `source_hash`, because the field became
    # required after they were filed. Saying "unknown" is a different claim from saying
    # nothing, and only one of them is checkable.
    ev["source_hash_then"] = (getattr(finding, "source_hash", None)
                              or "unknown — filed before `source_hash` was required")
    if paths:
        ev["source_hash_now"] = _sha256(root, paths[0])

    if quoted:
        # Only a quote WITH a path can be checked where it was filed; a pathless one is
        # still searchable tree-wide below, which is what keeps it from being dropped.
        placed = [a for a in quoted if a.path]
        # THE PRIMARY PATH DECIDES. An evidence body in this corpus quotes three
        # different things, and only one of them is the defect:
        #
        #   1. the defective code itself, under the path the body opens with;
        #   2. the CALL SITE that reaches it, under another path — which keeps working
        #      perfectly well after the defect is fixed;
        #   3. the NORM it violates, quoted out of SECURITY.md or README.md — which is
        #      the standard, and had better still be there.
        #
        # Classifying on "any quoted line survives" therefore reads a fixed defect as
        # anchor-unchanged. F-0098 is the worked example: its two `xcheck/envelope.py`
        # quotes are gone from HEAD, and its four `xcheck/parallel.py` context lines and
        # three `SECURITY.md` norm lines are all alive. Seven of nine quotes survive and
        # the anchor that matters is gone — `anchor-changed`, which is as far as this
        # module goes. (F-0098 was in fact fixed in `d4ba983`. A human established that,
        # not this classifier.)
        primary = placed[0].path if placed else None
        under = [a for a in placed if a.path == primary]
        alive = [a for a in under if a.quote in (_read_text(root, a.path) or "")]
        elsewhere = len([a for a in placed if a.path != primary
                         and a.quote in (_read_text(root, a.path) or "")])
        if alive:
            ev["note"] = (f"{len(alive)} of {len(under)} line(s) quoted from the primary "
                          f"locator {primary} still appear verbatim at HEAD")
            return Row(fid, finding.status, ANCHOR_UNCHANGED, found, ev,
                       proposal_for(ANCHOR_UNCHANGED, finding.status, fid))
        # Nothing quoted survives where it was filed. Either it moved or it is gone, and
        # the tree-wide search is what tells them apart.
        first = (under or placed or quoted)[0]
        anywhere = tree_contains(root, first.quote)
        if anywhere:
            ev["note"] = (f"the quoted line is gone from {first.path or 'its file'} but "
                          f"survives verbatim in {anywhere[0]} — the code MOVED, and a "
                          f"move is not a fix")
            return Row(fid, finding.status, ANCHOR_CHANGED, found, ev,
                       proposal_for(ANCHOR_CHANGED, finding.status, fid))
        ev["commit"] = (removing_commit(root, first.quote, first.path)
                        or removing_commit(root, first.quote))
        ev["note"] = (
            f"none of the {len(under)} line(s) quoted from the primary locator "
            f"{primary} survive anywhere in the tree" +
            (f" (its {elsewhere} other quoted line(s) — call-site context and the norm "
             f"it cites — are still there, so the body did not simply rot wholesale)"
             if elsewhere else ""))
        return Row(fid, finding.status, ANCHOR_CHANGED, found, ev,
                   proposal_for(ANCHOR_CHANGED, finding.status, fid))

    if paths and all(not (Path(root) / p).exists() for p in set(paths)):
        ev["note"] = (f"every file this finding names is gone from HEAD "
                      f"({', '.join(sorted(set(paths))[:3])})")
        return Row(fid, finding.status, ANCHOR_MISSING, found, ev,
                   proposal_for(ANCHOR_MISSING, finding.status, fid, body=body))

    ev["note"] = ("the body quotes no source line, so nothing here can be checked "
                  "against HEAD by machine — the files it names still exist, which "
                  "says the finding was filed about live code and nothing more"
                  if paths else "the body carries no anchor a machine can check")
    return Row(fid, finding.status, ANCHOR_MISSING, found, ev,
               proposal_for(ANCHOR_MISSING, finding.status, fid, body=body))


def duplicate_map(findings):
    """`{id: other_id}` for findings sharing a unit and a title. The FIRST filed wins."""
    seen, out = {}, {}
    for f in sorted(findings, key=lambda x: x.id):
        key = (tuple(getattr(f, "unit", ()) or ()), (f.title or "").strip().lower())
        if key in seen:
            out[f.id] = seen[key]
        else:
            seen[key] = f.id
    return out


def reconcile(state, root):
    """Every non-terminal finding, classified. Reads only."""
    live = [f for f in state.findings if f.status not in TERMINAL]
    dupes = duplicate_map(live)
    return [verdict_for(f, root, dupes) for f in live]


# A path token inside a unit's `material`. Matched directly rather than by splitting on
# separators: the catalog writes lists as ``a.py`, `b.py``, but also as ``MANIFEST.in`,
# plugin manifests, `install.sh`` and ``xcheck/runner.py` lines 1-1120`, so every
# separator rule leaves prose stuck to a path. What a path looks like is the stable part.
# `,` is in the class so a brace group survives as ONE token: without it
# `templates/{finding,pass}.md` matches as `templates/{finding` (no extension, dropped)
# and `pass}.md` (an extension, kept) — a path that exists nowhere, reported as missing
# material on a pass that is fine. A comma between two real paths always has a backtick
# or a space beside it in this catalog, and neither is in the class.
_PATH = re.compile(r"[A-Za-z0-9_.*{},/-]*[A-Za-z0-9_*}/-]"
                   r"\.(?:py|md|toml|sh|in|json|yml|yaml|txt)"
                   r"|[A-Za-z0-9_./-]+/\*\*?")


def unit_paths(material):
    """The paths a unit's `material` names, globs kept as globs.

    Prose in the same string ("lines 1-1120", "about 1.8 kloc", "plugin manifests") is not
    a path and is not returned. A unit whose material names no path at all returns nothing,
    which is reported as uncheckable rather than as present.
    """
    out = []
    for token in _PATH.findall(str(material or "")):
        out.extend(_expand_braces(token))
    return tuple(dict.fromkeys(out))


_BRACE = re.compile(r"^(.*)\{([^{}]*)\}(.*)$")


def _expand_braces(token):
    """`templates/{finding,pass}.md` -> two paths. The catalog writes unit material in
    shell brace form, and a half-expanded `plan}.md` exists nowhere."""
    m = _BRACE.match(token)
    if not m:
        return [token]
    head, body, tail = m.groups()
    return [f"{head}{part.strip()}{tail}" for part in body.split(",") if part.strip()]


def _exists(root, token):
    """Whether a path token resolves at HEAD. A glob counts if it matches anything."""
    base = Path(root)
    if any(ch in token for ch in "*?["):
        try:
            return next(base.glob(token), None) is not None
        except (ValueError, IndexError):                       # pragma: no cover
            return False
    return (base / token).exists()


def pass_report(state, root):
    """The unfinished passes, and whether the material they name still exists at HEAD.

    Criterion 9. A queue entry whose unit names files that are gone is work queued against
    code that no longer exists, and that is a different problem from a pass nobody got to
    — the first should be cancelled, the second should be run. Reporting both as "pending"
    is what lets 17 unfinished passes look like one backlog.
    """
    units = {u.id: u for u in state.catalogs.units}
    out = []
    for q in state.queue:
        if q.coverage and q.coverage.status == "done":
            continue
        named, missing, total = tuple(q.units or ()), [], 0
        for uid in named:
            unit = units.get(uid)
            if unit is None:
                missing.append(f"{uid} (no such unit in the catalog)")
                continue
            for token in unit_paths(unit.material):
                total += 1
                if not _exists(root, token):
                    missing.append(f"{uid}:{token}")
        if not named:
            note = "no unit named — nothing to check against HEAD"
        elif missing:
            note = (f"QUEUED AGAINST MISSING MATERIAL — {len(missing)} of {total} path(s) "
                    f"gone: {', '.join(missing[:3])}")
        else:
            note = f"meaningful — all {total} path(s) its units name exist at HEAD"
        out.append((q.id, q.dimension, named, note))
    return out


def census(rows):
    """Counts by verdict, with every declared verdict present even at zero.

    A census that omits its empty rows makes an unreached verdict indistinguishable from
    one that does not exist."""
    got = Counter(r.verdict for r in rows)
    return {v: got.get(v, 0) for v in VERDICTS}


def proposal(rows, passes=()):
    """The document a human reads. Verdicts, evidence, and the commands to run."""
    counts = census(rows)
    out = [
        "# xcheck reconcile — PROPOSAL",
        "",
        "Nothing below has been applied. This command changes no status and holds no",
        "capability to: a status transition is human-owned, and a tool that moved",
        "findings by reading the code would be deciding the one thing reserved for a",
        "person. Run the commands you agree with.",
        "",
        NOT_A_FIX_VERDICT,
        "",
        f"{len(rows)} non-terminal finding(s) against HEAD:",
        "",
    ]
    out += [f"  {v:<22} {counts[v]:>4}   {MEANS[v]}" for v in VERDICTS]
    out += ["", "## Verdicts", ""]
    for r in sorted(rows, key=lambda x: (VERDICTS.index(x.verdict), x.id)):
        out.append(f"### {r.id} — {r.verdict}  [{r.status}]")
        out.append(f"    locator          {r.evidence['locator'] or '(none)'}")
        out.append(f"    source hash then {r.evidence['source_hash_then']}")
        out.append(f"    source hash now  "
                   f"{(r.evidence['source_hash_now'] or '(no file)')[:16]}")
        if r.evidence["commit"]:
            out.append(f"    changed in       {r.evidence['commit']}")
        out.append(f"    why              {r.evidence['note']}")
        # A line beginning `$` is a command an operator can paste; one beginning `#`
        # is a comment saying why there is none. Every `$` line here has been checked
        # against §5 from THIS row's status — see `proposal_for`.
        out.append(f"    {'$' if not r.action.startswith('#') else ' '} {r.action}")
        out.append("")
    if passes:
        out += ["## Unfinished passes", ""]
        for pid, dim, units, note in passes:
            out.append(f"  {pid:<6} {str(dim):<22} units={','.join(units) or '-':<20} "
                       f"{note}")
    return "\n".join(out)
