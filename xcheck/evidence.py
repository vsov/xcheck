"""An evidence cache keyed on everything that could make the evidence wrong.

EXPERIMENTAL — not reachable from any production path.

Phase 2 of the fourth audit's response withdrew the public conf key that offered this:
`evidence_cache=on` reused nothing, because `read_entry()` and `write_entry()` were
called by no dispatch path. The module is kept, tested and correct in isolation; what it
is not is CALLED. A flag whose feature no code path reaches is a lie the configuration
file tells, so the flag went and the library stayed.

Promotion condition, stated so it is a decision rather than a drift: a dispatch path
calls `read_entry()` before running a pass and `write_entry()` after one, with the cache
decision recorded on the session — and `tests/test_feature_truthfulness.py` is what will
then require a production call site before the key may be public again.

The audit's largest recoverable cost: repeated audits and remediation loops re-derive
identical evidence. A remediator loop over one charter runs the same probes against the
same unchanged files a dozen times, and pays a model for every one of them.

So evidence may be reused — but only when NOTHING that could have changed it has
changed. The key is the whole tuple the audit named, and each component is here because
its absence is a way to serve a stale answer:

  - `subject`  the bytes of the material in scope. The obvious one.
  - `charter`  what the session was asked to do. Same files, different question.
  - `norms`    the wording it judges against. A norm reworded is a norm re-applied.
  - `policy`   what was in force. Evidence gathered under `sandbox_profile=none` is not
               evidence gathered under `container`.
  - `auditor`  the model AND the implementation version. An old model's evidence is not
               this model's evidence, and neither is an older xcheck's.
  - `capsule`  what the session was actually handed. Two dispatches that differ in no
               other component but were given different context are different runs.

What is NEVER cached is the verdict. A verdict is a judgement ABOUT material, and cached
material is by definition material that may have moved since; serving a stored verdict
is how a cache turns into a lie about the present tree. `entry_problems()` refuses a
payload carrying one, at the write, so a cache file that could do this cannot be created.

Off by default (`evidence_cache`), stored outside the working tree under the same search
order as raw logs, and fail-closed: a corrupt entry is a MISS, never a wrong answer.
"""
import hashlib
import json
import os
import tempfile
from pathlib import Path

from xcheck import __version__

# The project conf key. A PROJECT key: it changes what this audit costs, not what the
# machine is allowed to do.
CACHE_FLAG = "evidence_cache"
CACHE_SCHEMA = 1
CACHE_DIR_ENV = "XCHECK_EVIDENCE_DIR"
XDG_STATE_ENV = "XDG_STATE_HOME"

# The key, enumerated. A test reads THIS tuple and fails when a component is dropped,
# because a key that quietly loses a component is a cache that serves a stale answer for
# exactly that input and reports a hit while doing it.
KEY_COMPONENTS = ("subject", "charter", "norms", "policy", "auditor", "capsule")

# The only three things an entry may carry.
CACHEABLE_SECTIONS = ("evidence", "probes", "coverage")

# Anything that looks like a judgement. Refused at the write, at any depth.
VERDICT_FIELDS = frozenset({
    "verdict", "status", "severity", "ruling", "decision", "finding", "findings",
    "accepted", "rejected", "confirmed", "closed"})

UNKNOWN = "unknown"


def _sha(obj):
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def auditor_identity(cmd=None, executable=None):
    """Who produced the evidence: the model, and the code that drove it.

    Both halves matter and neither substitutes for the other. Upgrading the model
    changes what the evidence would be; upgrading xcheck changes how it was gathered and
    what it was compared against. `executable` is the `sha256:` identity `envelope`
    already derives from the binary's BYTES without running it (F-0098) — passed in
    rather than re-derived, because deriving it is that module's job.
    """
    from xcheck import envelope
    return {"model": envelope.model_of(cmd) if cmd else UNKNOWN,
            "provider": envelope.provider_of(cmd) if cmd else UNKNOWN,
            "executable": executable or UNKNOWN,
            "implementation": __version__}


def key_components(capsule, charter, policy_digest, auditor):
    """The six components, each derived from one named thing.

    `subject` is the hash of the material hashes the capsule already carries, in path
    order — the subject tree as this dispatch actually saw it, including a file that was
    MISSING AT DISPATCH, because a material file that disappeared is a change.
    """
    sources = [(s.get("path"), s.get("sha256")) for s in capsule.get("sources", ())]
    from xcheck.capsule import capsule_digest
    return {
        "subject": _sha(sorted(sources)),
        "charter": hashlib.sha256((charter or "").encode("utf-8")).hexdigest(),
        "norms": _sha(capsule.get("norms", [])),
        "policy": policy_digest,
        "auditor": _sha(auditor),
        "capsule": capsule_digest(capsule),
    }


def key_problems(parts):
    """Why these parts cannot be a key. Fails closed: a missing component is refused
    rather than defaulted, because a default would make two different runs share a key."""
    out = []
    missing = [c for c in KEY_COMPONENTS if not (parts or {}).get(c)]
    if missing:
        out.append(f"the cache key is missing {missing} — a key that ignores an input "
                   f"is a cache that serves a stale answer for that input")
    extra = sorted(set(parts or {}) - set(KEY_COMPONENTS))
    if extra:
        out.append(f"the cache key carries undeclared component(s) {extra}: add them to "
                   f"KEY_COMPONENTS or leave them out of the key")
    return out


def cache_key(parts):
    """The key itself. Raises on parts that are not a whole key."""
    problems = key_problems(parts)
    if problems:
        raise ValueError("; ".join(problems))
    return _sha({c: parts[c] for c in KEY_COMPONENTS})


def cache_search_order(project, conf=None):
    """Where the store is looked for, in order. Mirrors `retention.log_search_order`,
    and for the same reason: derived evidence belongs outside the tree being audited, or
    the next session can edit its own cache."""
    conf_dir = str((conf or {}).get("evidence_dir", "") or "").strip() if conf else ""
    env_dir = os.environ.get(CACHE_DIR_ENV)
    xdg = os.environ.get(XDG_STATE_ENV)
    name = Path(project).resolve().name
    return [
        ("evidence_dir in the operator profile", Path(conf_dir) if conf_dir else None),
        (f"${CACHE_DIR_ENV}", Path(env_dir) if env_dir else None),
        (f"${XDG_STATE_ENV}/xcheck/evidence/<project>",
         Path(xdg) / "xcheck" / "evidence" / name if xdg else None),
        ("the system temp directory",
         Path(tempfile.gettempdir()) / "xcheck-evidence" / name),
    ]


def cache_root(project, conf=None, create=True):
    """The store directory. Always outside the working tree; `audit-archive/` refused."""
    from xcheck.retention import refuse_frozen, refuse_outside_subject
    for _source, path in cache_search_order(project, conf):
        if path is not None:
            refuse_frozen(path)
            # The same enforcement point as `retention.log_root`, called with this
            # store's own key and message. Two callers, one rule (phase 4): a cache the
            # audited tree can write is a cache the next session can plant a verdict in,
            # which is the one thing this module refuses to carry across material.
            refuse_outside_subject(path, project, "evidence_dir", CACHE_DIR_ENV,
                                   "the evidence cache")
            if create:
                path.mkdir(parents=True, exist_ok=True)
            return path
    raise RuntimeError("unreachable: the search order ends with a temp directory")


def _verdict_fields_in(obj, path=()):
    """Every verdict-shaped key anywhere in the payload, with where it was found."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = path + (str(k),)
            if str(k).lower() in VERDICT_FIELDS:
                found.append(".".join(here))
            found.extend(_verdict_fields_in(v, here))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(_verdict_fields_in(v, path + (str(i),)))
    return found


def entry_problems(payload):
    """Why this payload may not be cached.

    The load-bearing rule of the module. Evidence, probes and coverage describe what was
    OBSERVED; a verdict is what someone CONCLUDED, and a conclusion outlives the material
    it was about. Serving one from a cache is the failure this refusal exists to make
    impossible — so it is enforced at the write, not asked of the caller.
    """
    out = []
    if not isinstance(payload, dict):
        return [f"a cache entry is an object with {list(CACHEABLE_SECTIONS)}, "
                f"not {type(payload).__name__}"]
    extra = sorted(set(payload) - set(CACHEABLE_SECTIONS))
    if extra:
        out.append(f"only {list(CACHEABLE_SECTIONS)} may be cached; refusing {extra}")
    verdicts = sorted(set(_verdict_fields_in(payload)))
    if verdicts:
        out.append(f"a VERDICT is never carried across material that may have moved; "
                   f"refusing {verdicts}")
    return out


def write_entry(project, key, parts, payload, tokens=None, conf=None):
    """Store one entry. Refuses a payload carrying a verdict."""
    problems = entry_problems(payload)
    if problems:
        raise ValueError("; ".join(problems))
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    doc = {"schema": CACHE_SCHEMA, "key": key, "parts": dict(parts),
           "payload": payload, "bytes": len(body.encode("utf-8")),
           # None means NOT MEASURED. Never an estimate: a saving nobody counted is not
           # a saving this tool will print a number for.
           "tokens": tokens}
    path = cache_root(project, conf) / f"{key}.json"
    path.write_text(json.dumps(doc, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def read_entry(project, key, conf=None):
    """`(entry, note)`. A miss — including a corrupt or mis-keyed entry — is `(None, why)`.

    Fail closed by construction: every failure path here returns a MISS, which costs a
    re-run. The alternative failure mode, returning a half-parsed entry, costs an audit
    that reported evidence it never gathered.
    """
    path = cache_root(project, conf, create=False) / f"{key}.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, f"miss: no entry for {key[:12]}"
    except (OSError, ValueError) as exc:
        return None, f"miss: entry {key[:12]} is unreadable ({exc.__class__.__name__})"
    if not isinstance(doc, dict) or doc.get("schema") != CACHE_SCHEMA:
        return None, f"miss: entry {key[:12]} is not a v{CACHE_SCHEMA} entry"
    if doc.get("key") != key:
        return None, (f"miss: entry {key[:12]} records key {str(doc.get('key'))[:12]} — "
                      f"a file that does not know its own key is corrupt")
    problems = entry_problems(doc.get("payload"))
    if problems:
        return None, f"miss: stored payload refused on read ({problems[0]})"
    return doc, f"hit: {key[:12]}"


def cache_report(entry, key, note):
    """One line a human can act on, saying which key matched and what it saved.

    The saving is stated in bytes, which is measured, and in tokens only when a token
    figure was actually recorded. `not measured` is the honest answer and it is printed
    rather than filled in with an estimate.
    """
    if entry is None:
        return f"evidence cache: MISS ({note})"
    tokens = entry.get("tokens")
    figure = f"{tokens:,} tokens" if isinstance(tokens, int) else "tokens not measured"
    return (f"evidence cache: HIT on key {key} — {entry['bytes']:,} bytes of evidence "
            f"reused, {figure}")


def cache_enabled(conf):
    """`True` when the cache is on. Off by default, and an unrecognised value is off."""
    from xcheck.util import conf_flag
    return conf_flag(conf, CACHE_FLAG)
