"""A derived knowledge layer that cannot become a second source of truth.

The audit named this as the one P2 feature that could turn findings into knowledge about
CLASSES of defect — and, in the same breath, as the one most likely to quietly become a
competing truth. Both halves are true, and the second is why almost all of this module
is refusals rather than analysis.

The boundary diagram, which is the specification and not a summary of it:

    state.json + events  ->  PROJECTION  ->  analysis  ->  PROPOSAL
                                                              |
                                              explicit human review
                                                              |
                                                     an existing write verb
                                                              |
                                                          state.json

Every arrow is one-way and every shortcut across it is the defect this module exists to
prevent. Concretely:

  - The bundle is DERIVED. It is a projection of canonical state plus the event stream,
    reproducible from those two inputs alone, and nothing in it is an input to anything.
  - The back channel is PROPOSAL-ONLY. Six things OKF may never do are refused by name
    and individually, because six refusals that share one code path are one refusal.
  - A proposal changes no byte of `state.json`. It reaches canonical state only when a
    human reads it and runs a write verb, which is the same verb they would have run
    without OKF — OKF saves the reading, never the deciding.
  - A CONFLICT IS KEPT. There is no last-wins resolution anywhere in this module: two
    records that disagree both survive, joined by `conflicts_with`, because a knowledge
    layer that silently picks a winner has invented a fact.

Storage is versioned JSONL with a manifest and checksums. No vector store, no graph
database, no embedding model: the dependency check asserts it, and the reason is that
every one of those turns a reproducible projection into a thing with its own state.
"""
import hashlib
import json
from datetime import date
from pathlib import Path

SCHEMA = 1
BUNDLE_DIRNAME = "okf"
MANIFEST_NAME = "manifest.json"

# The project conf key. Off by default, and GENERATING the bundle is an explicit verb —
# a derived layer that materialises itself on every run is a layer nobody chose.
FLAG = "okf"

# ---------------------------------------------------------------- the six record types

OBSERVATION = "observation"   # something read off the record, with no inference
CLAIM = "claim"               # an assertion someone made, attributed
DECISION = "decision"         # a choice a human made, and when
CONSTRAINT = "constraint"     # a norm or policy that bounded the work
HYPOTHESIS = "hypothesis"     # a proposed explanation, explicitly not established
CONCLUSION = "conclusion"     # an inference drawn from records in this bundle
RECORD_TYPES = (OBSERVATION, CLAIM, DECISION, CONSTRAINT, HYPOTHESIS, CONCLUSION)

# Every record carries all of these. Provenance first: a knowledge record that cannot say
# which revision of which file it came from is a rumour with a schema.
RECORD_FIELDS = ("id", "type", "source_revision", "source_hash", "locator",
                 "session", "pass", "finding", "confidence", "scope", "freshness")

# ---------------------------------------------------------------- the three relations

DERIVED_FROM = "derived_from"
SUPERSEDES = "supersedes"
CONFLICTS_WITH = "conflicts_with"
RELATIONS = (DERIVED_FROM, SUPERSEDES, CONFLICTS_WITH)

# ---------------------------------------------------------------- the six refusals

# What OKF may never do, each with the reason it is separately forbidden. They are
# separate entries and separate messages on purpose: six refusals sharing one code path
# would be one refusal wearing six names, and the control asserts they are distinct.
FORBIDDEN = {
    "open-finding": (
        "open a finding",
        "filing a finding is an Auditor's act inside a chartered, contained session. A "
        "derived layer that could file one would be an auditor with no charter, no "
        "containment and no session receipt."),
    "close-finding": (
        "close a finding",
        "closing is a Verifier verdict about material. OKF has read a projection, not "
        "the material, and a closure drawn from a projection is a closure of the map."),
    "move-status": (
        "move a lifecycle status",
        "§5 says which transitions exist and who owns each one. Several are human-owned; "
        "a layer that could move a status would be deciding them on a human's behalf."),
    "weaken-policy": (
        "weaken policy",
        "policy is the operator's, lives outside the subject, and is refused inside it. A "
        "derived layer proposing a smaller sandbox is the trust loop the P0 finding "
        "closed, re-opened from a new direction."),
    "replace-evidence": (
        "replace evidence",
        "evidence is bound to a source hash at a revision. A summary of evidence is not "
        "evidence, and substituting one for the other is how a bundle becomes the truth."),
    "raise-own-confidence": (
        "raise its own confidence",
        "a record that can increase its own confidence has a feedback loop instead of a "
        "provenance chain, and will converge on certainty about nothing."),
}


def okf_enabled(conf):
    """`True` when the bundle may be generated. Off by default; unknown values are off."""
    from xcheck.util import conf_flag
    return conf_flag(conf, FLAG)


def _rid(kind, *parts):
    """A deterministic record id: same inputs, same id, so the projection is stable."""
    seed = "|".join(str(p) for p in parts)
    return f"{kind[:4]}-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:12]}"


def knowledge_record(kind, text, source_revision, source_hash=None, locator=None, session=None,
           pass_id=None, finding=None, confidence="reported", scope=None,
           freshness=None, links=None):
    """One record. Every field in `RECORD_FIELDS` is present, `None` where unknown.

    `None` rather than omitted: a reader must be able to tell "this record has no
    locator" from "this bundle version had no such field", and an absent key cannot say
    which."""
    return {
        "schema": SCHEMA,
        "id": _rid(kind, source_revision, locator, text),
        "type": kind,
        "text": text,
        "source_revision": source_revision,
        "source_hash": source_hash,
        "locator": locator,
        "session": session,
        "pass": pass_id,
        "finding": finding,
        # Confidence is CARRIED from the source, never computed here — see the
        # `raise-own-confidence` refusal.
        "confidence": confidence,
        "scope": scope,
        "freshness": freshness,
        "links": dict(links or {}),
    }


def okf_link(rec, relation, target):
    """Add one relation. `conflicts_with` is symmetric and additive: it never removes
    anything, because resolving a conflict is what this module must not do."""
    if relation not in RELATIONS:
        raise ValueError(f"{relation!r} is not one of {list(RELATIONS)}")
    rec["links"].setdefault(relation, [])
    if target not in rec["links"][relation]:
        rec["links"][relation].append(target)
    return rec


# ---------------------------------------------------------------- the event adapter

# PHASE 6 (fourth audit). The projection read `e["id"]` and `e["at"]` off state
# transitions. Production writes `target` and `ts`, and has since the event stream
# existed — so every decision record on the real stream read `None moved reported ->
# accepted`: a knowledge record naming no finding, in a layer whose whole justification
# is provenance. The verifier called the bundle healthy, because it checked file
# checksums and nothing about what was in the files.
#
# One adapter, declared as a MAPPING rather than as a chain of `.get(a) or .get(b)`
# fallbacks. A fallback would have hidden this: `e.get("id") or e.get("target")` is green
# on both schemas and tells nobody that one of them is fiction.
EVENT_FIELDS = {
    # projection name -> the field production actually emits
    "id": "target",
    "at": "ts",
}


def event_view(e):
    """One production event in the vocabulary the projection reads.

    Every projection read of an event goes through here. Two readers of one schema is how
    this drifted, and a second reader added later would drift the same way — so there is
    one, and `tests/test_okf.py` asserts the projection contains no direct event field
    access at all.
    """
    view = dict(e or {})
    for name, emitted in EVENT_FIELDS.items():
        view[name] = (e or {}).get(emitted)
    return view


def observation_text(r):
    """What an OBSERVATION record about a finding says. One function, because the id is
    a digest OF this text and a second spelling of it is a second id."""
    return f"finding {r.id} is `{r.status}` at severity {r.severity}"


def observation_id(source_revision, r):
    """The id of the OBSERVATION record for a finding — the ONLY place one is built.

    PHASE 6. There used to be three constructions of this id: the record itself (through
    `knowledge_record`, keyed on locator and the full text), the CLAIM's `derived_from`
    (which reproduced both by hand and agreed), and the HYPOTHESIS's `derived_from`,
    which passed `None` for the locator and the bare finding id for the text. The third
    disagreed with the other two on every record: 266 links, 133 dangling, all of them
    `hypothesis.derived_from`, while `claim.derived_from` resolved 133 of 133.

    Two constructors that agree are one bug away from three that do not.
    """
    return _rid(OBSERVATION, source_revision, getattr(r, "locator", None),
                observation_text(r))


def project_bundle(state, events, today=None):
    """The bundle, projected from canonical state and the event stream. Nothing else.

    Deterministic and reproducible: same state and same events give byte-identical
    records, which is what makes the bundle disposable. If it ever disagrees with
    `state.json`, the bundle is wrong by construction and is regenerated, never merged.
    """
    today = today or date.today().isoformat()
    rev = state.state_revision
    out = []

    # OBSERVATION — read off the record, no inference.
    for r in state.findings:
        out.append(knowledge_record(
            OBSERVATION, observation_text(r),
            rev, source_hash=getattr(r, "source_hash", None),
            locator=getattr(r, "locator", None), finding=r.id,
            confidence=r.status, scope=r.dimension, freshness=today))

    # CLAIM — an assertion someone made, attributed to the session that made it.
    for r in state.findings:
        if getattr(r, "title", None):
            c = knowledge_record(CLAIM, r.title, rev,
                       source_hash=getattr(r, "source_hash", None),
                       locator=getattr(r, "locator", None), finding=r.id,
                       session=getattr(r, "fixed_by", None),
                       confidence=r.status, scope=r.dimension, freshness=today)
            okf_link(c, DERIVED_FROM, observation_id(rev, r))
            out.append(c)

    # DECISION — a choice a HUMAN made. Read from the event stream, because a decision is
    # something that happened at a time, and current state cannot say when or by whom.
    for raw in events or ():
        e = event_view(raw)
        if e.get("event") == "state_transition" and not e.get("session_id"):
            out.append(knowledge_record(
                DECISION,
                f"{e.get('id')} moved {e.get('from_status')} -> {e.get('to_status')}",
                rev, finding=e.get("id"), confidence="decided",
                scope=e.get("verb"), freshness=e.get("at") or today))

    # CONSTRAINT — the norms this audit is judged against.
    for n in state.catalogs.norms:
        out.append(knowledge_record(CONSTRAINT, f"{n.id}: {n.scope}", rev,
                          locator=n.source, confidence="declared",
                          scope=n.scope, freshness=today))

    # HYPOTHESIS — proposed, and labelled as not established. The only inference the
    # projection makes, and it makes it in the weakest form the vocabulary has.
    by_dim = {}
    for r in state.findings:
        by_dim.setdefault(r.dimension, []).append(r)
    for dim, members in sorted(by_dim.items()):
        if len(members) >= 3:
            h = knowledge_record(HYPOTHESIS,
                       f"{len(members)} findings share dimension `{dim}` — possibly one "
                       f"class",
                       rev, confidence="proposed", scope=dim, freshness=today)
            for member in members:
                okf_link(h, DERIVED_FROM, observation_id(rev, member))
            out.append(h)

    # CONCLUSION — drawn only from records already in this bundle, never from outside it.
    done = [q for q in state.queue if q.coverage and q.coverage.status == "done"]
    out.append(knowledge_record(
        CONCLUSION,
        f"{len(done)} of {len(state.queue)} queued pass(es) have a done coverage report",
        rev, confidence="derived", scope="coverage", freshness=today))
    return out


def conflicts(records):
    """Pairs that disagree, joined by `conflicts_with` — and BOTH KEPT.

    There is no resolution step here and there is no place to add one. Two records that
    say different things about the same locator are two things someone said; picking a
    winner would be inventing a third."""
    seen, pairs = {}, []
    for rec in records:
        key = (rec["type"], rec["locator"], rec["finding"])
        if rec["locator"] is None and rec["finding"] is None:
            continue
        other = seen.get(key)
        if other is not None and other["text"] != rec["text"]:
            okf_link(other, CONFLICTS_WITH, rec["id"])
            okf_link(rec, CONFLICTS_WITH, other["id"])
            pairs.append((other["id"], rec["id"]))
        else:
            seen[key] = rec
    return pairs


def okf_proposal(kind, target, reason, records, verb=None):
    """A PROPOSAL: a sentence and the command a human may choose to run.

    It is not an action and it holds no capability. `verb` is the existing write verb the
    human would run — the same one they would have run without OKF, because OKF saves the
    reading and never the deciding."""
    return {"schema": SCHEMA, "kind": kind, "target": target, "reason": reason,
            "evidence": [r["id"] for r in records],
            "verb": verb, "status": "proposed",
            "note": ("A PROPOSAL. It changes nothing. A human reads it and runs the verb, "
                     "or does not.")}


# PHASE 9 (fifth audit). A proposal is part of the bundle, so its references are part of
# the bundle's referential integrity. Declared as a field list rather than discovered by
# looking for things that resemble ids: a check that guesses which keys are references
# stops covering a key the day someone adds one.
PROPOSAL_REFS = ("evidence",)


def refuse(effect):
    """The refusal for one forbidden effect. Six distinct messages, by construction:
    each is built from that entry's own name and its own reason."""
    if effect not in FORBIDDEN:
        raise ValueError(f"{effect!r} is not a declared forbidden effect")
    what, why = FORBIDDEN[effect]
    return (f"OKF may not {what}: {why} OKF may only PROPOSE, and a proposal reaches "
            f"`state.json` when a human reads it and runs a write verb — never before, "
            f"and never by any path through this module.")


def apply_effect(effect, *_args, **_kwargs):
    """The one entry point anything could mistake for a back channel. It refuses.

    Written as a function rather than left absent on purpose: a missing function is an
    invitation to add one, and this one names why it will not exist."""
    raise PermissionError(refuse(effect))


def bundle_dir(project_path):
    return Path(project_path) / "audit" / BUNDLE_DIRNAME


def bundle_members(records=(), proposals=()):
    """The exported entity types, file name -> rows. THE declaration of what a bundle is.

    PHASE 8 (sixth audit). `write_bundle` held this list in one tuple literal and
    `verify_bundle` iterated whatever the manifest happened to name, so the two could
    disagree — and did: a manifest reading `{"files": {}}` named nothing, the verifier
    looped zero times and returned `[]` over a bundle with no JSONL files at all.

    One function, called by both. A new exported entity type is added here, which makes
    it written by the exporter and REQUIRED by the verifier in the same edit; there is no
    version of that change that exports without requiring.
    """
    return {"records.jsonl": list(records), "proposals.jsonl": list(proposals)}


#: Derived from the exporter, never hand-listed beside it.
BUNDLE_FILES = tuple(bundle_members())


def write_bundle(project_path, records, proposals=(), now=None):
    """Versioned JSONL plus a manifest with a checksum per file. Returns the manifest."""
    out = bundle_dir(project_path)
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, rows in bundle_members(records, proposals).items():
        text = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
        (out / name).write_text(text, encoding="utf-8")
        files[name] = {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                       "rows": len(rows), "bytes": len(text.encode("utf-8"))}
    manifest = {
        "schema": SCHEMA, "generated": now or date.today().isoformat(),
        "files": files,
        "record_types": {t: sum(1 for r in records if r["type"] == t)
                         for t in RECORD_TYPES},
        # What the bundle cannot support, counted rather than implied. A record that
        # asserts something about material and carries no anchor is not a defect — most
        # of this project's own findings predate source anchoring — but a bundle that
        # did not SAY so would read as evidence bound to a revision.
        "unanchored": sum(1 for r in records if anchor_state(r) == UNANCHORED),
        "derived": ("This bundle is a PROJECTION of audit/state.json plus "
                    "audit/events.jsonl. It is derived, disposable and never an input: "
                    "if it disagrees with state.json, it is wrong and is regenerated."),
    }
    (out / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                     encoding="utf-8")
    return manifest


# The three anchor states. A record that asserts something about material either names
# where it read it, or says it did not — there is no third thing, and "half a locator" is
# the shape that reads as anchored to a skim.
ANCHORED, UNANCHORED, PARTIAL = "anchored", "unanchored", "partial"

# The types whose whole content is a statement about the audited material. A CONCLUSION
# is drawn from records in the bundle and a CONSTRAINT names a norm, so neither is
# expected to carry a file anchor.
ANCHORED_TYPES = (OBSERVATION, CLAIM)


def anchor_state(rec):
    """`anchored`, `unanchored`, or `partial` for one record."""
    if rec.get("type") not in ANCHORED_TYPES:
        return ANCHORED
    loc, sha = rec.get("locator"), rec.get("source_hash")
    if loc and sha:
        return ANCHORED
    if loc or sha:
        return PARTIAL
    return UNANCHORED


def bundle_problems(records, proposals=()):
    """What is wrong with a bundle's CONTENT, independent of any file on disk.

    PHASE 6 (fourth audit). `verify_bundle` checked file checksums and nothing else, so a
    bundle whose every link pointed at a record that did not exist verified CLEAN. A
    checksum says the bytes are the bytes somebody wrote; it says nothing about whether
    what they wrote means anything. Four checks, each with its own message, because one
    message covering four defects is one defect wearing four names:

      * REFERENTIAL INTEGRITY — every reference in every exported entity resolves to a
        record this bundle exports. Records link to records; proposals cite records; both
        are resolved against ONE table, built once, here.

        PHASE 9 (fifth audit) is the second half of that sentence. The previous run fixed
        the id construction for records and checked `records.jsonl` only, so
        `classes.proposals_for` could mint OBSERVATION ids for evidence the bundle never
        exported and the verifier still returned `[]`: 288 records, 266 links, 0 dangling,
        and 13 proposal references pointing at nothing. Same defect class, new surface —
        which is what happens when the check lives at a call site instead of at the table.
      * KNOWN TYPES — every `type` is one of `RECORD_TYPES`, and every relation one of
        `RELATIONS`. A closed vocabulary that is not checked is a convention.
      * REQUIRED FIELDS — every field in `RECORD_FIELDS` is PRESENT, `None` included: a
        reader must be able to tell "no locator" from "this version had no such field".
      * SOURCE ANCHORS — a record claiming half an anchor is refused; a record claiming
        none is counted, never silently accepted.
    """
    problems = []
    # PHASE 8 (sixth audit): COUNTED, not collected. `ids = {r.get("id") for r in
    # records}` is a set, and a set is where duplicates go to disappear — a bundle with
    # the same record exported twice verified clean, because the only question ever asked
    # of this structure was "is this id in here", which two copies answer exactly as well
    # as one. `resolves` below is still the membership table; `seen` is what can see a
    # second occurrence.
    at = {}
    for n, rec in enumerate(records, 1):
        at.setdefault(rec.get("id"), []).append(n)
    for rid, lines in sorted(at.items(), key=lambda kv: str(kv[0])):
        if len(lines) > 1:
            where = ", ".join(f"line {n}" for n in lines)
            problems.append(f"{rid}: exported {len(lines)} times ({where}) — an id is "
                            f"what a link resolves to, so two records sharing one make "
                            f"every reference to it ambiguous")
    ids = set(at)
    for rec in records:
        rid = rec.get("id") or "<record with no id>"
        if rec.get("type") not in RECORD_TYPES:
            problems.append(f"{rid}: unknown record type {rec.get('type')!r} — the "
                            f"vocabulary is closed: {list(RECORD_TYPES)}")
        missing = [f for f in RECORD_FIELDS if f not in rec]
        if missing:
            problems.append(f"{rid}: missing required field(s) {missing} — a field is "
                            f"present and `None` when unknown, never absent")
        for relation, targets in (rec.get("links") or {}).items():
            if relation not in RELATIONS:
                problems.append(f"{rid}: unknown relation {relation!r} — the relations "
                                f"are closed: {list(RELATIONS)}")
            for target in targets:
                if target not in ids:
                    problems.append(f"{rid}: {relation} points at {target}, which is not "
                                    f"a record in this bundle — a dangling link is a "
                                    f"provenance chain with a hole in it")
        if anchor_state(rec) == PARTIAL:
            problems.append(f"{rid}: claims half a source anchor (locator="
                            f"{rec.get('locator')!r}, source_hash="
                            f"{rec.get('source_hash')!r}) — a record either says where it "
                            f"read something or says it does not, and half of one reads "
                            f"as anchored")
    for n, prop in enumerate(proposals, 1):
        who = f"proposal {n} ({prop.get('kind', '?')} -> {str(prop.get('target'))[:40]})"
        for field in PROPOSAL_REFS:
            for target in prop.get(field) or ():
                if target not in ids:
                    problems.append(f"{who}: {field} cites {target}, which is not a "
                                    f"record in this bundle — a proposal a human is "
                                    f"asked to act on, resting on evidence nobody "
                                    f"exported")
    return problems


def bundle_completeness(out, manifest):
    """Every REQUIRED file is present, named, and matches its checksum — or why not.

    PHASE 8 (sixth audit). This used to be a loop over `manifest.get("files", {})` inside
    `verify_bundle`, which asked the manifest what a bundle contains. A manifest reading
    `{"files": {}, "unanchored": 0}` therefore described a complete bundle with no files
    in it: the loop ran zero times, the checksum check never fired, and the verifier
    returned `[]` over a directory holding nothing but that manifest.

    The requirement comes from `BUNDLE_FILES` — the exporter's own declaration — and the
    manifest is checked AGAINST it rather than consulted as the authority. A file the
    manifest names and the declaration does not is reported as unclassified rather than
    ignored, because the alternative is a bundle that grows a member no verifier reads.

    This is the only route to a verdict about a bundle's files; `verify_bundle` returns
    whatever it says before looking at any content.
    """
    problems = []
    named = manifest.get("files")
    if not isinstance(named, dict):
        return [f"the manifest does not carry a `files` table (got {type(named).__name__})"
                f" — required: {list(BUNDLE_FILES)}"]
    unnamed = [n for n in BUNDLE_FILES if n not in named]
    if unnamed:
        problems.append(f"the manifest names {sorted(named) or 'no files at all'} and "
                        f"does not name {unnamed} — this bundle is INCOMPLETE. A bundle "
                        f"is {list(BUNDLE_FILES)}, which is what the exporter writes")
    unclassified = [n for n in sorted(named) if n not in BUNDLE_FILES]
    if unclassified:
        problems.append(f"the manifest names {unclassified}, which the exporter does not "
                        f"produce — add it to `bundle_members` or remove it; an "
                        f"unclassified member is a file no verifier reads")
    for name in list(BUNDLE_FILES) + unclassified:
        path = out / name
        if path.is_dir():
            problems.append(f"{name} is a DIRECTORY, not a file ({IS_A_DIRECTORY})")
            continue
        try:
            got = hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError:
            problems.append(f"{name} is required and is not there ({ABSENT})")
            continue
        except OSError as exc:
            problems.append(f"{name} cannot be read ({UNREADABLE}: "
                            f"{exc.__class__.__name__}) — not read is not verified")
            continue
        if name in named and got != named[name].get("sha256"):
            problems.append(f"{name} does not match its manifest checksum")
    return problems


def verify_bundle(project_path):
    """Problems with a bundle on disk: the bytes, and then what they say.

    Checksums first, because a file that does not match its manifest cannot be reasoned
    about at all. Then `bundle_problems` over the records themselves — the audit's
    finding was that a bundle can be byte-perfect and semantically broken, and be
    reported as healthy.
    """
    out = bundle_dir(project_path)
    try:
        manifest = json.loads((out / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"the manifest is unreadable ({exc.__class__.__name__}) — a bundle whose "
                f"manifest cannot be read has not been verified, and this is not a pass"]
    problems = bundle_completeness(out, manifest)
    if problems:
        return problems
    # BOTH members, because both are exported and both carry references. Reading only
    # `records.jsonl` is exactly the hole the fifth audit's phase closed.
    rows = {}
    for name in BUNDLE_FILES:
        rows[name], problem = read_rows(out / name)
        if problem:
            return [problem]
    problems = []
    records = rows["records.jsonl"]
    problems += bundle_problems(records, rows["proposals.jsonl"])
    declared = manifest.get("unanchored")
    counted = sum(1 for r in records if anchor_state(r) == UNANCHORED)
    if declared is None:
        problems.append("the manifest does not say how many records are unanchored — a "
                        "bundle that does not state what it cannot support reads as "
                        "evidence bound to a revision")
    elif declared != counted:
        problems.append(f"the manifest declares {declared} unanchored record(s) and the "
                        f"bundle holds {counted}")
    return problems


#: The four ways reading one bundle file can fail, kept apart. PHASE 8 (sixth audit):
#: `_jsonl_lines` caught OSError and returned `[]`, so a `records.jsonl` that was a
#: DIRECTORY read as a bundle with no records — and an empty bundle has no dangling
#: links, no unknown types and no duplicate ids. Every content check passed over a file
#: nobody could read. `absent` and `is-a-directory` are different facts about the export,
#: and a reader that says "not there" about a directory is telling the operator to look
#: in the wrong place.
ABSENT = "absent"
IS_A_DIRECTORY = "is-a-directory"
UNREADABLE = "unreadable"
MALFORMED = "malformed"


def read_rows(path):
    """`(rows, problem)` for one bundle file. `problem` is None only when rows are rows.

    Never `([], None)` for a file that could not be read: an empty list is a legitimate
    answer for an empty file and a lie for every other failure, and the two are
    indistinguishable to every caller downstream.
    """
    name = path.name
    if path.is_dir():
        return [], (f"{name} is a DIRECTORY, not a file ({IS_A_DIRECTORY}) — the export "
                    f"did not write it and something else is standing in its place")
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], f"{name} is not there ({ABSENT}) — the bundle is missing a required file"
    except OSError as exc:
        return [], (f"{name} cannot be read ({UNREADABLE}: {exc.__class__.__name__}) — a "
                    f"file this verifier cannot open is not a file it can pass")
    rows = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            return [], f"{name}:{n} is not valid JSON ({MALFORMED})"
    return rows, None


def _jsonl_lines(path):
    """Line count only, for callers that report a size and never a verdict."""
    rows, problem = read_rows(path)
    return [] if problem else [json.dumps(r) for r in rows]
