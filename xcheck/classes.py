"""Candidate classes, proposed and never decided.

133 findings and 0 class findings is a list. The audit's point is that classes are where
a defect list becomes knowledge — and its warning, in the same breath, is that a
clusterer nobody can argue with is worse than no clusterer.

So this module groups by a DETERMINISTIC fingerprint over four components, and every
design choice below exists to keep the grouping arguable:

  - **root cause** — the defect MECHANISM, read from the finding's own title against a
    closed lexicon of mechanisms. The title is the finding's one-sentence claim about
    what is wrong; the body is not searched for it, because a body that mentions "drift"
    in passing is not a drift defect and matching on it put 63 of 133 findings into one
    bucket the first time this was tried.
  - **violated norm** — which norms the finding is judged against.
  - **evidence pattern** — the SHAPE of the material it points at (directory and kind),
    never the exact path.
  - **affected unit** — carried on the fingerprint and printed, but deliberately NOT part
    of the grouping key. A fingerprint keyed on where the file lives is a directory
    listing, and the whole value of a class is that it crosses units.

No model and no network: a lexicon, a regex and a sort. And the output is a PROPOSAL —
this module cannot create a class finding and cannot set `superseded-by-class`, because
it imports nothing that writes and the OKF boundary refuses both by name.
"""
import re
from collections import Counter
from pathlib import Path

# The grouping key. `unit` is on the fingerprint and NOT in here, on purpose — see the
# module docstring. A test asserts this exact tuple, so removing a component or adding
# `unit` reddens rather than quietly changing what a "class" means.
FINGERPRINT_PARTS = ("root_cause", "norm", "evidence")
GROUP_KEY = ("root_cause", "norm", "evidence")

# Defect MECHANISMS, most specific first, each grounded in this corpus. A mechanism, not
# a topic: "omits a mandatory element" is a way for code to be wrong, while "migration"
# is a place. Ordered, and the first match wins, so a title naming two mechanisms is
# filed under the more specific one rather than being counted twice.
MECHANISMS = (
    ("last-wins-resolves-a-conflict",
     (r"last-wins", r"silently resolved by order", r"duplicate .* (accept|authorize|can )")),
    ("silently-drops-declared-input",
     (r"silently (discard|disappear|drop|narrow|ignor|resolv)", r"\bdiscards\b",
      r"\bdisappear")),
    ("accepts-what-it-should-refuse",
     (r"\baccepts?\b", r"\bcan escape\b", r"\bfollows the temp symlink\b",
      r"\bexpands the\b", r"\bauthorize[sd]? (overwrite|a missing|retries)\b",
      r"\bpermit\b", r"\bcan redefine\b")),
    ("reports-success-over-a-failure",
     (r"\bcertifies\b", r"\brecorded as ok\b", r"\bbecome successful\b",
      r"\bstill produce successful\b", r"\bexits successfully\b", r"\bclaims\b",
      r"\btreats\b", r"\bas no changes\b", r"\bfabricates\b",
      r"\brecord no-op changes\b", r"\bmisreport", r"\bnon-terminal\b", r"\bstrands\b")),
    ("writes-outside-the-lock",
     (r"\boutside the project lock\b", r"\bunlocked\b", r"\bbefore mirrors\b",
      r"\bre-acquire the parent lock\b", r"\binherit an unrelated audit lock\b",
      r"\binherited lock\b")),
    ("containment-leak",
     (r"\bsurvive log redaction\b", r"\broot filesystem writable\b",
      r"\bspawned processes running\b", r"\bdestroyed at teardown\b",
      r"\bmodify the project directly\b", r"\brepository isolation\b")),
    ("test-does-not-exercise-what-it-claims",
     (r"\bstubs the session it claims\b", r"\btests? (miss|permit|inherit)\b",
      r"\bdoes not execute\b", r"\bdoes not report\b", r"\bcounts irrelevant\b",
      r"\bwitness fails\b",
      # Found by the phase-17 HAND-CHECK, not by design. The four members of the
      # largest candidate class are all "<something>test bypasses <the production
      # consumer>", and the grouping was right while the LABEL was wrong: bare
      # `\bbypass` in `gate-bypassed-or-hidden` matched first, so a class about tests
      # that never reach production code was filed as a class about gates. The
      # mechanism is the same either way, which is why the members did not move.
      r"\b(selftest|tests?)\b.*\bbypass")),
    ("declared-surface-cannot-perform-it",
     (r"\bcannot\b", r"\bnever (creates|reaches)\b", r"\bunreachable\b",
      r"\bno legal route\b", r"\bunavailable\b", r"\bhas no\b", r"\bskips\b")),
    ("omits-a-mandatory-element",
     (r"\bomits?\b", r"\bomitted\b", r"\bmisses\b", r"\bforgets\b", r"\bis absent\b",
      r"\bnot fully reported\b", r"\bnot bound\b", r"\bnot consumed\b", r"\bneed no\b",
      r"\bis optional\b", r"\bdisable\b")),
    ("gate-bypassed-or-hidden",
     (r"\bbypass", r"\bhides\b", r"\bsanctions\b", r"\bwithout mandatory\b",
      r"\bmakes optional .* mandatory\b", r"\btriggers the two-reopen\b")),
    ("vocabulary-undefined-or-contradictory",
     (r"\bundefined\b", r"\bcontradict", r"\bconflicts with\b", r"\bcan disagree\b",
      r"\btwo authorities\b", r"\bambiguous")),
    ("declared-input-not-honoured",
     (r"\bignores?\b", r"\bexcludes\b", r"\bforbid\b", r"\bchange the effective\b",
      r"\bdepend on\b", r"\brequires\b", r"\boverwrite session evidence\b",
      r"\bmutable through\b", r"\bnon-finite\b")),
)

UNCLASSIFIED = None

# One compiled alternation per mechanism, built once at import. The lexicon above is
# ~70 patterns and `root_cause` ran `re.search` for each of them on every title; PHASE 15
# put that on the `xcheck status` path, where a 500-finding project pays it on every
# invocation. Twelve scans instead of seventy, with the patterns unchanged — the ORDER
# still decides, both between mechanisms and within one, because `|` is first-match in
# Python and the tuples are already written in precedence order.
_MECHANISM_RE = tuple((label, re.compile("|".join(f"(?:{p})" for p in patterns)))
                      for label, patterns in MECHANISMS)

# Material anchors in a finding body: `path/to/file.py:12-34`, in backticks.
_ANCHOR = re.compile(r"`([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)(?::\d+(?:-\d+)?)?`")


def root_cause(title):
    """The defect mechanism this finding claims, or None.

    None is a real answer and is counted: a clusterer that has a label for everything has
    learnt nothing, so the findings that match no mechanism are reported rather than
    forced into the nearest bucket."""
    low = (title or "").lower()
    for label, pattern in _MECHANISM_RE:
        if pattern.search(low):
            return label
    return UNCLASSIFIED


def evidence_shape(body):
    """The SHAPE of the material a finding points at: `(directory, extension)` pairs.

    Shape, not path, and that is what makes a fingerprint survive a rename. Renaming
    `xcheck/runner.py` to `xcheck/orchestrator.py` does not change what the defect is
    about, so it must not change the fingerprint; moving the same defect into a different
    kind of file does change it, and it should."""
    shapes = set()
    for path in _ANCHOR.findall(body or ""):
        p = Path(path)
        head = p.parent.as_posix()
        head = head.split("/")[0] if head not in ("", ".") else "."
        shapes.add(f"{head}/*{p.suffix}")
    return tuple(sorted(shapes))


def norms_for(record, catalogs):
    """The norms this finding is judged against — its dimension's declared norms.

    Read from the catalog rather than scraped out of the body: the dimension is what the
    charter said the pass was judging, and a norm mentioned in prose is a citation, not a
    verdict about which norm was violated."""
    for d in catalogs.dimensions:
        if d.key == record.dimension:
            return tuple(sorted(d.norms or ()))
    return ()


def fingerprint(record, body, catalogs):
    """The four components. `unit` is carried and printed, never keyed on."""
    return {
        "root_cause": root_cause(record.title),
        "norm": norms_for(record, catalogs),
        "evidence": evidence_shape(body),
        "unit": tuple(record.unit or ()),
    }


def group_key(fp):
    """The key two findings must share to be candidates for one class. `None` when the
    root cause is unclassified — an unclassified finding groups with nothing, including
    other unclassified ones, because "we could not tell" is not a shared mechanism."""
    if fp["root_cause"] is UNCLASSIFIED:
        return None
    return (fp["root_cause"], fp["norm"], fp["evidence"])


def read_body(project, record):
    try:
        return Path(project, "audit", record.body_path).read_text(encoding="utf-8")
    except (OSError, TypeError):
        return ""


def candidates(state, project, threshold=None):
    """`(classes, ungrouped)` — classes sorted by size, then key.

    `threshold` defaults to the project's own `class_threshold`, because how many
    instances make a class is a declared limit of this audit and not this module's
    opinion."""
    if threshold is None:
        # `state.limits` is a frozen MAPPING, not an object — `getattr` on it silently
        # returns the default and the project's own declared limit is never read.
        limits = state.limits
        threshold = (limits.get("class_threshold") if hasattr(limits, "get")
                     else getattr(limits, "class_threshold", None)) or 3
    buckets = {}
    for record in state.findings:
        # An unclassified finding is skipped WHOLE, not fingerprinted and set aside.
        # `group_key` is None whenever the root cause is unclassified, whatever the
        # evidence shape says, so neither the body read nor the norm lookup can change
        # the answer — and the `ungrouped` list below is derived by subtracting the
        # grouped ids, so nothing needed those fingerprints in the first place.
        #
        # PHASE 15 put this on the `xcheck status` path, where it is paid on every
        # invocation: fingerprinting all 500 took status from 27.6 ms to 68.1 ms and
        # tripped the performance gate. Most of a corpus is unclassified — 120 of this
        # repository's 133 — so most of that work was the wasted kind.
        if root_cause(record.title) is UNCLASSIFIED:
            continue
        fp = fingerprint(record, read_body(project, record), state.catalogs)
        key = group_key(fp)
        if key is not None:
            buckets.setdefault(key, []).append((record, fp))
    classes = [{"key": key,
                "root_cause": key[0], "norm": key[1], "evidence": key[2],
                "members": [r.id for r, _ in rows],
                "units": sorted({u for _, fp in rows for u in fp["unit"]}),
                "size": len(rows)}
               for key, rows in buckets.items() if len(rows) >= threshold]
    grouped = {mid for c in classes for mid in c["members"]}
    ungrouped = [r.id for r in state.findings if r.id not in grouped]
    classes.sort(key=lambda c: (-c["size"], c["key"]))
    return classes, ungrouped


def mechanism_census(state):
    """How many findings each mechanism claimed, `None` included. Printed beside the
    classes so an over-eager lexicon is visible as one enormous bucket."""
    return Counter(root_cause(r.title) for r in state.findings)


def proposals_for(candidates_, exported):
    """One OKF proposal per candidate class, citing records the bundle ALREADY exports.

    PROPOSALS — this module holds no capability to create a class finding or to set
    `superseded-by-class`, and imports nothing that could. The verb named is the one a
    human runs after reading the members.

    PHASE 9 (fifth audit). This used to MINT an OBSERVATION record per member and cite
    its id. The minted records went nowhere: `write_bundle` exports the projection and
    these proposals, never a third list, so all 13 references pointed at records that did
    not exist — while `verify_bundle` returned `[]`, because it read `records.jsonl` only.
    Inventing a record to satisfy a reference is how the previous run's 133 dangling links
    happened; the bundle already holds one OBSERVATION per finding, so the proposal cites
    THAT. `exported` is the projection — the same list `write_bundle` receives — and a
    member with no exported observation is DROPPED from the evidence rather than cited
    into thin air, with the proposal saying so.
    """
    from xcheck import okf
    observed = {r.get("finding"): r["id"] for r in exported
                if r.get("type") == okf.OBSERVATION and r.get("finding")}
    out = []
    for c in candidates_:
        evidence = [{"id": observed[m]} for m in c["members"] if m in observed]
        unexported = [m for m in c["members"] if m not in observed]
        out.append(okf.okf_proposal(
            "class-candidate", ", ".join(c["members"]),
            f"{c['size']} findings share the mechanism `{c['root_cause']}`, the norms "
            f"{list(c['norm'])} and the evidence shape {list(c['evidence'])}, across "
            f"units {c['units']}. That is a CANDIDATE, not a class: whether these are one "
            f"defect is a judgement about the material, which a human makes." +
            (f" ({len(unexported)} member(s) have no exported observation and are named "
             f"in the target but cited by nothing: {', '.join(unexported)})"
             if unexported else ""),
            evidence,
            verb="xcheck file-finding CF-XXXX … (after reading the members)"))
    return out


# --------------------------------------------------- PHASE 15: reaching the human

# Measured before choosing: this repository's corpus produces FOUR candidate classes, so
# the block below is six lines — a header, one line per class, and the proposal-only
# reminder. That fits in `xcheck status`, which is where a human already looks before
# deciding what to triage, and putting it behind an explicit verb would mean the detector
# only ever runs for someone who already suspected there was something to find.
#
# The bound is declared rather than assumed. `MAX_INLINE_LINES` is what "compact enough
# for status" means, and `report` degrades to a one-line summary above it rather than
# turning the status output into a report nobody reads to the end.
MAX_INLINE_LINES = 12


def report(state, project, threshold=None):
    """The lines `xcheck status` and the triage gate print. Reads only.

    The UNGROUPED count is part of the result and not a footnote
    (`a-clusterer-that-groups-everything-learnt-nothing`). A surface showing four classes
    and nothing else would say this corpus has been understood, when 120 of its 133
    findings share no pattern at all — that ratio is the honest headline, and it is the
    first thing on the first line.
    """
    classes, ungrouped = candidates(state, project, threshold)
    total = len(state.findings)
    grouped = sum(c["size"] for c in classes)
    if not classes:
        # CONTROL surface: plainly said, never an empty heading. A heading with nothing
        # under it reads as a detector that failed rather than one that found nothing.
        return [f"recurring classes: none — no {threshold or 'threshold'}-or-more group "
                f"of findings shares a mechanism, norms and evidence shape "
                f"({total} finding(s) examined, all ungrouped)"]
    head = (f"recurring classes: {len(classes)} candidate(s) covering {grouped} of "
            f"{total} finding(s); {len(ungrouped)} share no pattern")
    rows = [f"  {c['size']}x {c['root_cause']:<38} {','.join(c['units']):<16} "
            f"{','.join(c['members'])}" for c in classes]
    tail = ("  PROPOSALS ONLY — a class is a judgement about the material. "
            "`xcheck file-finding CF-XXXX …` is a human's call, after reading the members.")
    if len(rows) + 2 > MAX_INLINE_LINES:
        return [head, f"  ({len(rows)} candidates — too many to list inline; "
                      f"`xcheck okf` carries them as proposals)", tail]
    return [head] + rows + [tail]
