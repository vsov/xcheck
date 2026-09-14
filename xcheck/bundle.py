"""An evidence bundle someone else can verify without this repository.

A result that can only be checked by the tool that produced it is not evidence anyone can
act on; it is a claim with a checksum. So this writes a directory of flat JSON, a manifest
with a size and a digest per member, and a CHECKER — plain stdlib, carried inside the
bundle, itself a manifest member, because a checker nobody checksums is the one file an
attacker rewrites.

Three things the format is built to make impossible to fake by accident:

- **Truncation and tampering are different failures.** Size is checked BEFORE the digest.
  A truncated file has a wrong digest too, so a digest-first checker collapses both arms
  into one message and can no longer tell a partial write from an edit.
- **The bundle states the POPULATION it covers.** Which findings, which passes, which
  sessions, and what went unmeasured — including the counts that are zero. A bundle that
  reads as a finished audit when 133 findings are still `reported` is the defect this
  phase exists not to ship.
- **What is left out is listed inside.** A reader who cannot see the omissions cannot tell
  a bundle with no secrets in it from a bundle someone stripped.

Generating one is an explicit verb, off by default, and it writes under the retention log
root — outside the working tree, by the rules already in force there.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from xcheck import envelope
from xcheck import ledger, retention, state as state_mod
from xcheck.util import conf_flag

FLAG = "evidence_bundle"
SCHEMA = 1
MANIFEST_NAME = "manifest.json"
CHECKER_NAME = "verify.py"
BUNDLES_DIRNAME = "bundles"

# Every member, enumerated. The writer builds exactly this tuple and a test asserts the
# manifest names all of it, so DROPPING one is a failure rather than a smaller bundle.
MEMBERS = (
    "subject-manifest.json",   # what was audited, by content
    "policy.json",             # the policy DIGEST and where it resolved from — never its bytes
    "events.jsonl",            # the append-only stream, verbatim
    "receipts.jsonl",          # one row per session: what it was asked to do, what it did
    "findings.jsonl",          # every finding record, with its body
    "patches.jsonl",           # what each session actually changed, by digest and path
    "verdicts.jsonl",          # every recorded status transition, with who moved it
    "provenance.json",         # model, executable, version, profile — per session and in total
    "population.json",         # what this bundle covers, and what it does not
    "omissions.json",          # what was deliberately left out, and why
)

# Written into the bundle as `verify.py`. It imports nothing but the standard library and
# reads only the manifest beside it, so it runs where xcheck is not installed and the
# project directory does not exist.
CHECKER_SOURCE = '''#!/usr/bin/env python3
"""Verify this evidence bundle. Standard library only; xcheck is not required.

    python3 verify.py [bundle-directory]

Exit 0 means every member named in the manifest is present, is the right length and
hashes to the digest recorded for it. Exit 1 means it does not, and every problem is
printed with the member it is about.
"""
import hashlib
import json
import sys
from pathlib import Path

MANIFEST_NAME = "manifest.json"


def problems(root):
    root = Path(root)
    try:
        manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{MANIFEST_NAME}: unreadable ({exc.__class__.__name__}: {exc})"]
    found = []
    members = manifest.get("members") or {}
    if not members:
        return [f"{MANIFEST_NAME}: names no members at all"]
    for name in sorted(members):
        meta = members[name]
        path = root / name
        try:
            data = path.read_bytes()
        except OSError:
            found.append(f"{name}: MISSING — named in the manifest and not in the bundle")
            continue
        # Size FIRST. A truncated file also fails the digest, so checking the digest
        # first would report every partial write as tampering and lose the difference.
        want_bytes, got_bytes = meta.get("bytes"), len(data)
        if got_bytes != want_bytes:
            verb = "SHORT" if got_bytes < want_bytes else "LONG"
            found.append(f"{name}: {verb} — {got_bytes} bytes on disk, "
                         f"{want_bytes} in the manifest")
            continue
        got = hashlib.sha256(data).hexdigest()
        if got != meta.get("sha256"):
            found.append(f"{name}: TAMPERED — right length, wrong content "
                         f"(sha256 {got[:16]}…, manifest says {str(meta.get('sha256'))[:16]}…)")
    extra = sorted(p.name for p in root.iterdir()
                   if p.is_file() and p.name not in members and p.name != MANIFEST_NAME)
    for name in extra:
        found.append(f"{name}: UNLISTED — present in the bundle, absent from the manifest")
    return found


def main(argv):
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent
    found = problems(root)
    if not found:
        manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
        print(f"OK: {len(manifest['members'])} members verified in {root}")
        pop = manifest.get("population", {})
        if pop:
            print(f"    population: {pop.get('summary', '(unstated)')}")
        return 0
    print(f"FAILED: {len(found)} problem(s) in {root}", file=sys.stderr)
    for line in found:
        print(f"  {line}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
'''


def bundle_enabled(conf):
    return conf_flag(conf, FLAG)


def bundle_root(project, conf=None, create=True):
    """Beside the raw logs, which is already outside the working tree and already refuses
    to resolve into frozen evidence. Reusing that root rather than inventing a second one
    means there is one answer to "where does xcheck put things it does not own"."""
    root = retention.log_root(project, conf, create=create) / BUNDLES_DIRNAME
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def _jsonl_rows(path):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            # A corrupt row is REPORTED, not skipped: `retention.manifest_rows` losing
            # unparseable lines silently is a P1 in this project's own audit.
            out.append({"_unparseable": line[:200]})
    return out


def _finding_export(rec, audit_dir):
    row = {k: v for k, v in vars(rec).items()}
    row["unit"] = list(row.get("unit") or ())
    if "members" in row:
        row["members"] = list(row.get("members") or ())
    body = Path(audit_dir, rec.body_path) if rec.body_path else None
    if body and body.exists():
        text = body.read_text(encoding="utf-8")
        row["body"] = text
        row["body_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    else:
        row["body"] = None
        row["body_sha256"] = None
        row["body_note"] = "the finding body was not on disk when the bundle was written"
    return row


def _receipts(st):
    keep = ("session_id", "role", "provider", "agent_model", "executable",
            "executable_version", "charter_hash", "prompt_hash", "state_revision",
            "head_before", "head_after", "sandbox_profile", "trust_level", "started",
            "duration_s", "exit_status", "outcome", "log_digest", "tokens",
            "concurrent_group", "note")
    return [{k: s.get(k) for k in keep if k in s} for s in st.sessions]


def _patches(events):
    """What a session actually changed, by digest and path — not the diff itself.

    The bytes of a patch are in the session log, which does not ship (see the omissions
    list). What ships is enough for a reader with the subject repository to reproduce the
    change and check it against `patch_digest`."""
    out = []
    for ev in events:
        if ev.get("event") != "session_result":
            continue
        if not (ev.get("patch_digest") or ev.get("applied_paths")
                or ev.get("material_paths")):
            continue
        out.append({k: ev.get(k) for k in
                    ("ts", "session_id", "role", "patch_digest", "applied_paths",
                     "material_paths", "effects_applied", "quarantine_path", "outcome")
                    if k in ev})
    return out


def _verdicts(events):
    return [{k: ev.get(k) for k in ("ts", "session_id", "verb", "target", "from_status",
                                    "to_status", "state_revision_before",
                                    "state_revision_after", "summary") if k in ev}
            for ev in events if ev.get("event") == "state_transition"]


def provenance(st, policy_digest=None, policy_source=None):
    per_session = [{k: s.get(k) for k in
                    ("session_id", "role", "provider", "agent_model", "executable",
                     "executable_version", "sandbox_profile", "trust_level")
                    if k in s} for s in st.sessions]
    models = sorted({s.get("agent_model") or envelope.UNKNOWN for s in st.sessions})
    providers = sorted({s.get("provider") or envelope.UNKNOWN for s in st.sessions})
    profiles = sorted({s.get("sandbox_profile") or envelope.UNKNOWN for s in st.sessions})
    return {
        "controller_commit": state_mod.controller_commit(),
        "policy_digest": policy_digest,
        "policy_source": policy_source,
        "models": models,
        "providers": providers,
        "sandbox_profiles": profiles,
        "sessions": per_session,
        "note": ("`provider` here is what the DISPATCHER recorded from the command it "
                 "ran, not what the agent said about itself. Token figures in the "
                 "receipts are agent-reported unless a session carries a sidecar."),
    }


def population(st, events):
    """Which findings, which passes, which sessions — and what was never measured.

    Every count that is zero is stated as zero. The reason is the whole point of the
    member: a reader who is handed 133 findings and no statement of what happened to them
    will assume the audit finished."""
    by_status = {}
    for rec in st.findings:
        by_status[rec.status] = by_status.get(rec.status, 0) + 1
    passes_done = sum(1 for q in st.queue if q.done)
    sessions = st.sessions
    measured = [s for s in sessions if s.get("tokens") is not None]
    finished = [s for s in sessions if s.get("outcome")]
    by_outcome = {}
    for s in finished:
        by_outcome[s["outcome"]] = by_outcome.get(s["outcome"], 0) + 1
    return {
        "findings_total": len(st.findings),
        "findings_by_status": by_status,
        "class_findings": len(getattr(st, "class_findings", ()) or ()),
        "passes_total": len(st.queue),
        "passes_done": passes_done,
        "passes_outstanding": len(st.queue) - passes_done,
        "plans": len(getattr(st, "plans", ()) or ()),
        "sessions_total": len(sessions),
        "sessions_finished": len(finished),
        "sessions_by_outcome": by_outcome,
        "sessions_with_measured_tokens": len(measured),
        "tokens_measured": sum(s.get("tokens") or 0 for s in measured),
        "events_total": len(events),
        "state_revision": st.state_revision,
        "unmeasured": [
            f"{len(sessions) - len(measured)} of {len(sessions)} sessions carry no token "
            f"figure; those are NOT zero-cost sessions, they are unmeasured ones",
            "no false-positive rate: that needs findings a human has triaged",
            "no fix durability and no reopen rate: that needs a finding closed and later "
            "re-audited",
        ],
        "summary": (
            f"{len(st.findings)} findings ("
            + ", ".join(f"{n} {s}" for s, n in sorted(by_status.items()))
            + f"), {passes_done}/{len(st.queue)} passes done, "
            f"{len(finished)} sessions finished, "
            f"{len(measured)} of {len(sessions)} measured"),
    }


OMISSIONS = (
    ("raw session logs",
     "an agent's raw output is the largest artefact and the likeliest place for a secret "
     "to appear verbatim. Each session's log is named by its sha256 in the receipts, so a "
     "holder of the log can prove it is the one that ran."),
    ("the operator profile's bytes",
     "it names executables and may name credentials in their arguments. The DIGEST ships, "
     "so a reader can prove which policy was in force without being given it."),
    ("the agent's reasoning",
     "nothing recorded it. A bundle that implied otherwise would be inventing testimony."),
    ("the subject's source code",
     "findings carry a locator and a source hash into the subject commit instead. The "
     "bundle is evidence about a repository, not a copy of one."),
    ("token figures for unmeasured sessions",
     "absent, never zero — see `population.json`."),
)


def _policy_member(st, policy_digest, policy_source):
    """The policy digest, and a straight answer when there is not one.

    Two different questions get two different fields. `at_export` is the profile that
    resolved when the bundle was written; `in_force_during_sessions` is what each session
    RECORDED, which is the one that actually governed the work. They can differ, and a
    bundle that showed only the first would let a profile swapped after the run pass as
    the profile the run had.

    A null is never left to speak for itself. `null` reads as "no policy applied", and
    "not recorded" is what it means — the distinction this project has had to make about
    token figures, and it costs the same one sentence here."""
    seen = sorted({s.get("policy_digest") for s in st.sessions if s.get("policy_digest")})
    unrecorded = sum(1 for s in st.sessions if not s.get("policy_digest"))
    return {
        "at_export": {
            "digest": policy_digest,
            "source": policy_source,
            "note": (None if policy_digest else
                     "NOT RECORDED, not `no policy`: no operator profile resolved when "
                     "this bundle was written. Every dispatch verb refuses without one, "
                     "so this says nothing about whether the sessions had a policy — "
                     "read `in_force_during_sessions` for that."),
        },
        "in_force_during_sessions": {
            "digests": seen,
            "sessions_recording_none": unrecorded,
            "note": (f"{unrecorded} of {len(st.sessions)} sessions carry no policy digest. "
                     f"Those predate the field; an absent value is `not recorded` and is "
                     f"never to be read as `no policy applied`."
                     if unrecorded else None),
        },
        "bytes": ("deliberately absent — see omissions.json. A reader checks these "
                  "digests against the profile they were given."),
    }


def _write_member(root, name, obj):
    if name.endswith(".jsonl"):
        text = "".join(json.dumps(r, sort_keys=True, default=str) + "\n" for r in obj)
    else:
        text = json.dumps(obj, indent=2, sort_keys=True, default=str) + "\n"
    (root / name).write_text(text, encoding="utf-8")
    data = text.encode("utf-8")
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def write_evidence_bundle(project, conf=None, audit_dir=None, now=None,
                 policy_digest=None, policy_source=None):
    """Write every member, then the checker, then the manifest that covers both."""
    project = Path(project)
    audit_dir = Path(audit_dir) if audit_dir else project / "audit"
    st = state_mod.load_state(audit_dir)
    # PHASE 5 (fifth audit): through the ONE validated read, like every other consumer.
    # An evidence bundle built from a stream nobody checked is the worst place for the
    # second reader to have been: the bundle exists so a third party does not have to
    # trust this repository, and it was exporting whatever the file happened to contain.
    events = ledger.read_stream(audit_dir.parent)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    root = bundle_root(project, conf) / f"{project.resolve().name}-{stamp}"
    root.mkdir(parents=True, exist_ok=True)

    findings = [_finding_export(r, audit_dir) for r in st.findings] + \
               [_finding_export(r, audit_dir) for r in (getattr(st, "class_findings", ()) or ())]
    content = {
        "subject-manifest.json": {
            "subject_commit": state_mod.subject_commit(project),
            "subject_manifest": state_mod.subject_manifest(project),
            "generated": stamp,
        },
        "policy.json": _policy_member(st, policy_digest, policy_source),
        "events.jsonl": events,
        "receipts.jsonl": _receipts(st),
        "findings.jsonl": findings,
        "patches.jsonl": _patches(events),
        "verdicts.jsonl": _verdicts(events),
        "provenance.json": provenance(st, policy_digest, policy_source),
        "population.json": population(st, events),
        "omissions.json": [{"omitted": what, "why": why} for what, why in OMISSIONS],
    }
    assert tuple(content) == MEMBERS, "the writer and MEMBERS disagree"

    members = {name: _write_member(root, name, obj) for name, obj in content.items()}
    (root / CHECKER_NAME).write_text(CHECKER_SOURCE, encoding="utf-8")
    checker = CHECKER_SOURCE.encode("utf-8")
    members[CHECKER_NAME] = {"bytes": len(checker),
                             "sha256": hashlib.sha256(checker).hexdigest()}

    manifest = {
        "schema": SCHEMA,
        "generated": stamp,
        "members": members,
        "population": {"summary": content["population.json"]["summary"]},
        "verify": (f"python3 {CHECKER_NAME} — standard library only, no xcheck, no "
                   f"network. It reads this manifest and nothing else."),
    }
    (root / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return root, manifest
