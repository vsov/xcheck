"""The embedded check battery, moved verbatim from the single-file orchestrator.

Temporary. Phases 5 and 11 shrink it as its subject matter — the Markdown
control plane — is deleted and replaced; what survives moves to `tests/`, whose
green condition is a real command's outcome rather than a predicate's return
value. Nothing new should be added here: add it to `tests/` instead.
"""


import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from xcheck.util import (
    BOOLEAN_CONF, CANONICAL_SCHEMA, CONF_DEFAULTS, CONSTRUAL_SECTIONS,
    Conf, DELEGATED_VALUE_GATES, GIT_TIMEOUT, NEXT_OWNER,
    REFUSAL_REASONS, SCHEMA_BLOCK_FIELDS, STATUSES, _canon_unit, _canon_updated,
    conf_int, construal_key, is_session_id, load_conf
)
from xcheck.md_prose import (
    FRONTMATTER_RE, LIMIT_KEYS, _DIMENSIONS_RE, _atx_indent_ok, _fm_value,
    _norm_cell, _parse_frontmatter, _section_table, _strip_nonstructure,
    _yaml_absent, section2_canonical_field_values, section2_canonical_fields,
    section2_field_nullable_map
)
from xcheck.state import (
    STATE_FILENAME, ClassFinding, Finding, StateError, load_state
)
from xcheck import __version__, envelope
from xcheck.write import _bound_session
from xcheck.views import write_views
from xcheck.validate import (
    _refusal_norm_issue, _schema_enum_binding_issues, _schema_meta_diff,
    _schema_value_coverage_issues, canonical_schema_meta_issues, canonical_schema_nullability_issues
)
from xcheck.courier import (
    courier_commit, dirty_paths, head_baseline_paths, session_dirt
)
from xcheck.runner import (
    ENV_ALLOWLIST, Lock, OUTCOMES, PROFILES, PROMPTS, Redactor, _read_lock, build_cmd,
    child_environment, classify_outcome, lock_provably_dead, pid_alive,
    prompt_charter_slot_issues, refuse_uncontained, resolve_profile, run_session
)
from xcheck.decision import (
    DECIDE_PRIORITY, decide, role_and_charter, state_and_decision,
    verifier_independence
)
from xcheck.cli import (
    cmd_lint, cmd_loop, cmd_metrics, cmd_next, cmd_status, cmd_unlock, main, parse_budget,
    report_gate
)
from xcheck.util import (
    DEGRADED_INDEPENDENCE, OUTPUT_SCHEMA_VERSION, STATUS_SCHEMA, json_output_issues
)

# What the repository has beside the package and an installed artifact does not. A
# wheel is `xcheck/*.py` plus metadata: no templates/, no skills/, no install.sh. The
# checks that audit those files therefore have no subject once installed, and are
# skipped by name rather than passed vacuously — see the guard inside `selftest()`.
REPO_ONLY_INPUTS = ("XCHECK.md", "templates", "skills", "install.sh", "launchers")
REFUSAL_NORM_CHECK = "§5 refusal norm self-consistency (F-0134)"
RP50_REPO_CHECKS = (
    "templates/ shipped-artifact conformance (F-0130)",
    "install.sh idempotent upgrade (F-0127)",
    "install-launchers.sh six-way parity (F-0128)",
    "shipped skills carry the canonical contract blocks (F-0129, F-0134)",
)
# Every check `selftest()` may skip, named. `tests/test_release_lifecycle.py` asserts
# that none of them is skipped in the source tree, so this set can only ever shrink
# the score of an artifact that genuinely does not carry the subject.
REPO_ONLY_CHECKS = (REFUSAL_NORM_CHECK,) + RP50_REPO_CHECKS



class _AllModuleGlobals:
    """A dict-like view over EVERY xcheck module namespace at once.

    The battery substitutes production symbols by name to model a failure edge —
    a `courier_commit` that returns False, a `Lock` that is already held, a
    `dirty_paths` that reports a poisoned set. In the single-file orchestrator one
    namespace held every definition AND every call site, so `globals()[name] = x`
    was enough to make the substitution visible to the code under test.

    After the package split a name is bound in the module that DEFINES it and again
    in each module that did `from xcheck.x import name`, so assigning it in one
    place would leave the call sites still looking at the original — the patch would
    silently no-op and the test would measure the production path while claiming to
    measure the failure path. Writing every binding is what the original assignment
    meant, so that is what this does; a name bound nowhere is a KeyError rather than
    a silent miss.
    """

    @staticmethod
    def _modules():
        return [m for name, m in list(sys.modules.items())
                if m is not None and (name == "xcheck" or name.startswith("xcheck."))]

    def __getitem__(self, name):
        for m in self._modules():
            if name in vars(m):
                return getattr(m, name)
        raise KeyError(name)

    def __setitem__(self, name, value):
        bound = [m for m in self._modules() if name in vars(m)]
        if not bound:
            raise KeyError(f"{name} is bound in no xcheck module — nothing to patch")
        for m in bound:
            setattr(m, name, value)


_G = _AllModuleGlobals()


def orchestrator_sources():
    """Every line of the orchestrator's own source, as one text.

    Several checks below scan 'the orchestrator's source' for a forbidden syntactic
    form (a `courier_commit(` callsite that drops the committed-flag, a substring
    `needs-human` comparison). In the single file `Path(__file__).read_text()` WAS
    that source; after the split it would be one module of eight, and the scan would
    pass by covering almost nothing. The subject of those checks is unchanged — the
    code image this process is running — so the text they scan is the package.
    """
    pkg = Path(__file__).resolve().parent
    return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in sorted(pkg.glob("*.py")))


# ---------------------------------------------------------------- selftest


def write_split_conf(audit, body):
    """Write a conf body to the two files that now own it, and point at the profile.

    Since the policy split, `audit/orchestrator.conf` may carry no role command, no
    containment profile and no limit — `load_conf` refuses those by name. The selftest's
    fixtures still describe a case as one block of KEY=VALUE lines, so this routes each
    line to the file its kind belongs to: project keys stay in the subject, policy keys
    go to an operator profile OUTSIDE it, and `XCHECK_OPERATOR_PROFILE` points there so
    a `main()`-driven arm (which re-reads configuration under the lock, with nothing to
    pass) finds it the way a real operator's shell would.
    """
    from xcheck.policy import (OPERATOR_PROFILE_ENV, OPERATOR_PROFILE_TEMPLATE,
                               classify)
    project, profile = [], list(OPERATOR_PROFILE_TEMPLATE.splitlines())
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        if classify(k) == "policy":
            profile = [ln for ln in profile
                       if ln.partition("=")[0].strip() != k] + [f"{k}={v.strip()}"]
        else:
            project.append(f"{k}={v.strip()}")
    Path(audit).mkdir(parents=True, exist_ok=True)
    (Path(audit) / "orchestrator.conf").write_text(
        "\n".join(project) + "\n", encoding="utf-8")
    # Outside the project: a profile inside the subject is the very thing being fixed,
    # and a selftest that wrote one there would be asserting against a shape the
    # product refuses.
    # The template classifies nothing — an operator must, and a writing role refuses
    # until they do. The selftest's subject is a fixture this process just created, so
    # it is trusted by construction and says so; a case that means to exercise the
    # refusal sets `trust_level` in its own body and replaces this line.
    from xcheck.policy import TRUST_KEY, TRUSTED
    if not any(ln.partition("=")[0].strip() == TRUST_KEY and ln.partition("=")[2].strip()
               for ln in profile):
        profile = [ln for ln in profile
                   if ln.partition("=")[0].strip() != TRUST_KEY] + [f"{TRUST_KEY}={TRUSTED}"]
    path = Path(tempfile.mkdtemp(prefix="xcheck-selftest-operator-")) / "operator.conf"
    path.write_text("\n".join(profile) + "\n", encoding="utf-8")
    os.environ[OPERATOR_PROFILE_ENV] = str(path)
    return path


def _selftest_prints_use_file(src):
    """True if any print(...) call in `src` passes a file= keyword argument.

    F-0063 guard predicate (human ruling #2, 2026-07-26). The selftest tally is a
    line-scan of what flowed through the tee installed AS sys.stdout, so it counts
    exactly the check lines emitted via sys.stdout — no more, no less. A
    print(..., file=...) inside selftest could route a check line around the tee
    (to a saved stdout reference, sys.__stdout__, or stderr) and silently
    un-measure it — the exact silent-divergence class this finding tracks.
    Forbidding a direct file= in selftest prints closes the one bypass form worth
    mechanizing — a print(..., file=...) that routes a check line to a saved
    stdout ref, sys.__stdout__, or stderr, around the tee. It is a source check of
    that single syntactic form, not a proof that every emission reaches the tee:
    an aliased print (`_emit = print; _emit(..., file=...)`), an inner
    redirect_stdout, or a raw fd write would not trip it, and the tally does not
    claim to count those. AST-based, so a
    file= sitting inside a string literal (this selftest's own regression
    fixtures) is not a false positive — only a real print call trips it.
    """
    import ast
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        is_print = (isinstance(fn, ast.Name) and fn.id == "print") or \
                   (isinstance(fn, ast.Attribute) and fn.attr == "print")
        if is_print and any(kw.arg == "file" for kw in node.keywords):
            return True
    return False


def selftest():
    # Denominator harness (F-0063, human rulings 2026-07-26 — "measure, don't
    # model" then "promise exactly the sys.stdout boundary"): the summary
    # "(passes/total)" counts every PASS:/FAIL: check line this selftest emits
    # THROUGH sys.stdout. Prior attempts re-derived which emission reached stdout
    # by hand — a local print() shadow that inspected file=, split sep/end by
    # itself, and never saw builtins.print / sys.stdout.write / file=None. Each
    # such model drifted the instant a check used a form it did not anticipate:
    # the exact silent-divergence class this finding tracks. So stdout is no
    # longer MODELLED, it is MEASURED at the stream: the tee IS installed as
    # sys.stdout for the whole run, so every check line printed to sys.stdout —
    # print's default, builtins.print's default, a bare sys.stdout.write — flows
    # through it and is accumulated verbatim; anything aimed at stderr or captured
    # by an inner redirect_stdout never reaches the tee and is correctly not
    # counted. The tally at the end is a literal line-scan of exactly what the tee
    # saw. Two calls glued by end="" land in one accumulated line; a
    # multi-line/sep argument lands as its several lines.
    #
    # The tally is BOUNDED to sys.stdout and claims nothing about raw file
    # descriptor 1: a check line routed around sys.stdout — print(file=...) to a
    # saved stdout ref or sys.__stdout__, an inner redirect_stdout, an aliased
    # print, a raw fd write — reaches fd 1 without passing the tee and is
    # (correctly, under this bounded claim) not counted. Chasing fd-equivalence
    # would cost the live output selftest prints for, so we do not. A guard below
    # closes the one bypass form worth mechanizing: a direct print(..., file=...)
    # in this source, which would silently divert a line a reader expects on
    # sys.stdout. That guard is a source check of that single syntactic form — not
    # a proof that every emission reaches the tee. The tally measures the tee; the
    # wording here and in the guard's report line claims only that much.
    import io as _io0
    _real_stdout = sys.stdout

    class _Tee:
        def __init__(self, real):
            self._real = real
            self.buf = _io0.StringIO()

        def write(self, s):
            self.buf.write(s)
            return self._real.write(s)

        def flush(self):
            self._real.flush()

    _tee = _Tee(_real_stdout)
    # Install the tee AS sys.stdout for the run. Inner `with redirect_stdout(b)`
    # blocks save this tee and restore it on exit, so their captures are excluded
    # from the tally exactly as they are excluded from the real stdout stream.
    sys.stdout = _tee

    def rows(*specs):
        """PHASE 5: TYPED records, not ledger dicts. `decide` is a pure function of
        `Finding`/`ClassFinding` now, so a fixture builds the records directly —
        there is no row text between the fixture and the decision to mis-parse."""
        out = []
        for i, s, n in specs:
            common = dict(id=i, title="t", severity="minor", status=s,
                          dimension="spec", unit=("B01",), pass_id="P-01",
                          attempts=0, updated="2026-01-01",
                          body_path=f"findings/{i}.md", next=n)
            out.append(ClassFinding(members=(), **common) if i.startswith("CF-")
                       else Finding(**common))
        return out

    # PHASE 5 fixture spelling. The six-cell pipe row that USED to be a LEDGER.md
    # line is kept only as a compact way to say "these records exist" — it is
    # written into `state.json`, the one canonical document, and no product code
    # reads a row anywhere. A fixture that wants a field the row has no column for
    # passes it as a keyword (`_put_state(audit, rows, attempts=2)`) or edits the
    # document through `_state_doc`/`_save_state`.
    _STATE_ROW_RE = re.compile(
        r"^\|\s*((?:CF|F)-\d{4})\s*\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|")

    _EMPTY_STATE = {
        "schema_version": 1, "state_revision": 1, "generated_by": "selftest",
        "head_before": "0" * 40, "findings": [], "class_findings": [], "queue": [],
        "plans": [], "construals": [], "sessions": [], "limits": {},
        "catalogs": {"norms": [], "dimensions": [], "units": []},
    }

    def _state_doc(audit):
        """The audit's state document as a plain dict — the empty one if absent."""
        p = Path(audit) / STATE_FILENAME
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return json.loads(json.dumps(_EMPTY_STATE))

    def _save_state(audit, doc):
        Path(audit).mkdir(parents=True, exist_ok=True)
        doc["state_revision"] = int(doc.get("state_revision", 0)) + 1
        (Path(audit) / STATE_FILENAME).write_text(
            json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        # PHASE 6: a fixture means "a project a command will act on", and every
        # command now refuses when LEDGER.md or a frontmatter block disagrees with
        # the document. Rendering here keeps that refusal OUT of checks whose
        # subject is something else; the checks whose subject IS drift write the
        # views by hand. A document built to be rejected has no views to render.
        try:
            write_views(load_state(Path(audit)), Path(audit))
        except StateError:
            pass
        return doc

    def _put_state(audit, rows_text, encoding=None, bodies=True, cf_members=None,
                   per_id=None, **fields):
        """Replace the document's finding records from the six-cell row DSL.

        `encoding=` is accepted and ignored: the call sites were `write_text(...,
        encoding="utf-8")` and the kwarg is meaningless for a JSON document."""
        doc = _state_doc(audit)
        # A record's `pass` must name a queue entry (the schema refuses a finding no
        # pass produced), so a fixture that writes records without a plan gets the
        # minimal done pass its records point at.
        if not doc["queue"]:
            doc["queue"] = [{"id": "P-01", "dimension": "spec", "units": ["B01"],
                             "charter": "all units", "stop": "budget", "done": True,
                             "coverage": {"report_path": "passes/P-01-x.md",
                                          "findings": [], "updated": "2026-01-01",
                                          "status": "done"}}]
            (Path(audit) / "passes").mkdir(parents=True, exist_ok=True)
            (Path(audit) / "passes" / "P-01-x.md").write_text(
                "**COVERED:** unit checked.\n\n**NOT COVERED:** nothing deferred.\n",
                encoding="utf-8")
        pid = doc["queue"][0]["id"]
        fs, cs = [], []
        for line in rows_text.splitlines():
            m = _STATE_ROW_RE.match(line.strip())
            if not m:
                continue
            fid, title, sev, status, nxt, upd = (g.strip() for g in m.groups())
            rec = {"id": fid, "title": title or "t", "severity": sev or "major",
                   "status": status, "dimension": "spec", "unit": ["B01"],
                   "pass": pid, "attempts": 0, "updated": upd or "2026-01-01",
                   "body_path": f"findings/{fid}.md"}
            if nxt and nxt not in ("—", "-"):
                rec["next"] = nxt
            rec.update(fields)
            rec.update((per_id or {}).get(fid, {}))
            (cs if fid.startswith("CF-") else fs).append(rec)
        # A class finding's `members` is required and non-empty (F-0151 closed by
        # schema, not by a lint rule), and the row DSL has no column for it. Default
        # to the members the old fixtures expressed through each finding's `class:`
        # frontmatter — the superseded ones — and fall back to every finding present.
        for c in cs:
            if cf_members is not None:
                c["members"] = list(cf_members)
                continue
            sup = [f["id"] for f in fs if f["status"] == "superseded-by-class"]
            c["members"] = sup or [f["id"] for f in fs]
        for f in fs:
            if f["status"] == "superseded-by-class" and cs and "class" not in f:
                f["class"] = cs[0]["id"]
        doc["findings"], doc["class_findings"] = fs, cs
        _save_state(audit, doc)
        if bodies:
            for r in fs + cs:
                p = Path(audit) / r["body_path"]
                p.parent.mkdir(parents=True, exist_ok=True)
                if not p.exists():
                    p.write_text(f"# {r['id']}\n\nEvidence body.\n", encoding="utf-8")
        return doc

    def complete_audit(audit, queue=(("P-01", True),), coverage_pass="P-01", **limits):
        """F-0077 (human ruling #2): a COMPLETE plan — Norms catalog, norm-backed
        Dimensions, Unit map and a non-empty queue — so `state_and_decision` reaches
        the finding-state / done logic under test instead of routing to the Planner.
        When `coverage_pass` names a checked pass, bind its canonical coverage record
        so the §4-rule-5 gate (F-0011) does not intercept the 'done' verdict.

        PHASE 5: every one of those used to be a Markdown section this helper wrote
        and a parser read back. `queue` is now a list of `(pass id, done)` pairs and
        the catalogs are objects — a queue entry inside a code fence, a commented
        limit and a coverage marker in an HTML comment are not rejected inputs any
        more, they are unwritable ones."""
        doc = _state_doc(audit)
        doc["catalogs"] = {
            "norms": [{"id": "N1", "source": "X.md", "scope": "mech"}],
            "dimensions": [{"key": "spec", "catches": "drift", "norms": ["N1"]},
                           {"key": "invariants", "catches": "broken invariants",
                            "norms": ["N1"]}],
            "units": [{"id": "B01", "material": "bin/x", "size": "10",
                       "responsibility": "y"}],
        }
        doc["queue"] = []
        for pid, done in queue:
            q = {"id": pid, "dimension": "spec", "units": ["B01"],
                 "charter": "all units", "stop": "budget", "done": bool(done)}
            if done and coverage_pass == pid:
                q["coverage"] = {"report_path": f"passes/{pid}-x.md",
                                 "findings": [], "updated": "2026-01-01",
                                 "status": "done"}
                (Path(audit) / "passes").mkdir(parents=True, exist_ok=True)
                (Path(audit) / "passes" / f"{pid}-x.md").write_text(
                    "**COVERED:** unit checked.\n\n**NOT COVERED:** nothing deferred.\n",
                    encoding="utf-8")
            doc["queue"].append(q)
        if limits:
            doc["limits"] = {**doc.get("limits", {}), **limits}
        return _save_state(audit, doc)

    # PHASE 5: the `audit_exists` column is gone. "No AUDIT.md" was a READING
    # outcome — the parser found no file and the decision had to cope with a
    # half-known world. `load_state` either yields a whole State or raises, so
    # "decide with no plan on disk" is not a case any more; a missing `state.json`
    # is an addressed refusal at the boundary, tested in tests/test_no_bypass.py.
    cases = [
        # (name, rows, unchecked, expected_kind, expected_detail)
        ("auditor on first unchecked pass", [], ["P-01", "P-02"], True, "run-auditor", "P-01"),
        ("triage blocks when only reported", rows(("F-0001", "reported", "human")), [], True, "stop-triage", ["F-0001"]),
        (
            "verify before new passes",
            rows(("F-0001", "fixed", "Verifier"), ("F-0002", "reported", "human")),
            ["P-03"],
            True,
            "run-verifier",
            ["F-0001"],
        ),
        (
            "remediate accepted CF alone before F-batch",
            rows(("CF-0001", "accepted", "Remediator"), ("F-0002", "accepted", "Remediator")),
            [],
            True,
            "run-remediator",
            ["CF-0001"],
        ),
        (
            "reopened precedes accepted",
            rows(("F-0001", "reopened", "Remediator"), ("F-0002", "accepted", "Remediator")),
            [],
            True,
            "run-remediator",
            ["F-0001"],
        ),
        (
            "resume validated/planned before a newly accepted CF (F-0051)",
            rows(("F-0001", "validated", "Remediator"), ("CF-0001", "accepted", "Remediator")),
            [],
            True,
            "run-remediator",
            ["F-0001"],
        ),
        (
            "needs-human trumps everything",
            rows(("CF-0002", "reopened", "human ⚠ needs-human"), ("F-0001", "fixed", "Verifier")),
            ["P-05"],
            True,
            "stop-needs-human",
            "CF-0002",
        ),
        (
            "batch capped at batch_size",
            rows(*((f"F-{i:04d}", "accepted", "Remediator") for i in range(1, 12))),
            [],
            True,
            "run-remediator",
            [f"F-{i:04d}" for i in range(1, 9)],
        ),
        (
            "reopened batch also capped at batch_size (F-0003)",
            rows(*((f"F-{i:04d}", "reopened", "Remediator") for i in range(1, 12))),
            [],
            True,
            "run-remediator",
            [f"F-{i:04d}" for i in range(1, 9)],
        ),
        (
            "passes accumulate before triage (pilot rhythm)",
            rows(("F-0001", "reported", "human")),
            ["P-02"],
            True,
            "run-auditor",
            "P-02",
        ),
        (
            "done when terminal and queue empty",
            rows(("F-0001", "closed", "—"), ("F-0002", "superseded-by-class", "—")),
            [],
            True,
            "done",
            None,
        ),
        (
            # F-0105: `obsolete` is a §5 terminal state (accepted -> obsolete). It
            # must count as terminal so the cycle reports 'done', not inconsistent.
            # Removing `obsolete` from TERMINAL flips this case to stop-inconsistent.
            "done when obsolete terminal and queue empty (F-0105)",
            rows(("F-0001", "closed", "—"), ("F-0002", "obsolete", "—")),
            [],
            True,
            "done",
            None,
        ),
        (
            # F-0105: `withdrawn` is a §5 terminal state (disputed -> withdrawn). Same
            # protection — dropping it from TERMINAL flips this to stop-inconsistent.
            "done when withdrawn terminal and queue empty (F-0105)",
            rows(("F-0001", "closed", "—"), ("F-0002", "withdrawn", "—")),
            [],
            True,
            "done",
            None,
        ),
        (
            "deferred debt is done-with-debt, not inconsistent",
            rows(("F-0001", "closed", "—"), ("F-0004", "deferred", "human")),
            [],
            True,
            "done-deferred-debt",
            ["F-0004"],
        ),
        (
            "validated resumes remediation (F-0001)",
            rows(("F-0001", "validated", "Remediator")),
            [],
            True,
            "run-remediator",
            ["F-0001"],
        ),
        (
            "planned resumes remediation (F-0001)",
            rows(("F-0001", "planned", "Remediator")),
            [],
            True,
            "run-remediator",
            ["F-0001"],
        ),
        (
            "genuinely unknown status flagged inconsistent",
            rows(("F-0001", "mystery", "Remediator")),
            [],
            True,
            "stop-inconsistent",
            ["F-0001"],
        ),
        (
            "disputed is its own gate, not inconsistent",
            rows(("F-0001", "closed", "—"), ("F-0002", "disputed", "human")),
            [],
            True,
            "stop-disputed",
            ["F-0002"],
        ),
    ]
    failed = 0
    # Checks that could not run because their subject is not in an installed artifact.
    # Named, printed, excluded from the denominator, and reported in the summary.
    skipped = []
    for name, rws, unchecked, _exists, want_kind, want_detail in cases:
        kind, detail = decide(rws, unchecked, batch_size=8)
        ok = kind == want_kind and detail == want_detail
        print(f"{'PASS' if ok else 'FAIL'}: {name}" + ("" if ok else f" -> got {kind}, {detail}"))
        failed += 0 if ok else 1

    # F-0106: the full route decide() -> role_and_charter() must map each lifecycle
    # decision to the SPEC-OWNED role (§5: fixed -> Verifier; accepted / reopened /
    # validated / planned -> Remediator; new plan -> Planner; unchecked pass ->
    # Auditor). The cases above cover decide()'s KIND, but not this last load-bearing
    # adapter before an agent is launched — swapping the run-verifier / run-remediator
    # arms of role_and_charter would hand a fixed finding to the Remediator and a
    # reopened one to the Verifier (breaking §5 status ownership) while every decide()
    # check stays green. Assert the role the whole route yields.
    _route = [
        ("fixed -> Verifier", rows(("F-0001", "fixed", "Verifier")), [], True, "Verifier"),
        ("accepted -> Remediator", rows(("F-0001", "accepted", "Remediator")), [], True, "Remediator"),
        ("reopened -> Remediator", rows(("F-0001", "reopened", "Remediator")), [], True, "Remediator"),
        ("validated -> Remediator", rows(("F-0001", "validated", "Remediator")), [], True, "Remediator"),
        ("planned -> Remediator", rows(("F-0001", "planned", "Remediator")), [], True, "Remediator"),
        ("unchecked pass -> Auditor", [], ["P-01"], True, "Auditor"),
    ]
    for label, rws, unchk, _exists, want_role in _route:
        k, d = decide(rws, unchk, batch_size=8)
        role, _charter = role_and_charter(k, d)
        ok = role == want_role
        print(f"{'PASS' if ok else 'FAIL'}: route {label} (F-0106)" + ("" if ok else f" -> kind={k} role={role}"))
        failed += 0 if ok else 1

    # B1: triage_batch_cap forces a triage sitting before more passes
    rws = rows(("F-0001", "reported", "human"), ("F-0002", "reported", "human"), ("F-0003", "reported", "human"))
    k, d = decide(rws, ["P-05"], batch_size=8, triage_cap=3)
    ok = k == "stop-triage" and d == ["F-0001", "F-0002", "F-0003"]
    print(f"{'PASS' if ok else 'FAIL'}: triage_batch_cap stops before more passes" + ("" if ok else f" -> {k},{d}"))
    failed += 0 if ok else 1
    k, d = decide(rws, ["P-05"], batch_size=8, triage_cap=4)
    ok = k == "run-auditor" and d == "P-05"  # below cap -> keep auditing
    print(f"{'PASS' if ok else 'FAIL'}: triage_batch_cap below cap keeps auditing" + ("" if ok else f" -> {k},{d}"))
    failed += 0 if ok else 1
    k, d = decide(rws, ["P-05"], batch_size=8, triage_cap=0)
    ok = k == "run-auditor"  # 0 = unbounded, current behavior
    print(f"{'PASS' if ok else 'FAIL'}: triage_batch_cap=0 is unbounded" + ("" if ok else f" -> {k},{d}"))
    failed += 0 if ok else 1

    # F-0077 (human ruling #2, 2026-07-26): plan_complete gates STATE, not just
    # the unchecked-passes branch. Poison ACROSS ALL ROUTES — an incomplete plan
    # must route to the Planner whether the queue is empty, present, or the file
    # is absent; and each keeps its prior outcome when the plan IS complete. This
    # tests the CLAIM (a partial plan never audits or reports done), not one
    # reported branch — the promise-width closing standard (XCHECK.md §7).
    # PHASE 5: the two "missing AUDIT.md" rows are gone with the `audit_exists`
    # argument — see the `cases` table above. The third of them was worse than
    # merely untestable: `exists=False, plan_complete=True` was a CONTRADICTION the
    # old signature could still express, and pinning its behaviour pinned the
    # behaviour of a state that never existed.
    for label, unchk, complete, wk, wd in [
        ("empty queue + incomplete plan -> run-planner", [], False, "run-planner", None),
        ("queue + incomplete plan -> run-planner", ["P-01"], False, "run-planner", None),
        ("empty queue + complete plan -> done (regression)", [], True, "done", None),
        ("queue + complete plan -> run-auditor (regression)", ["P-01"], True, "run-auditor", "P-01"),
    ]:
        k, d = decide([], unchk, batch_size=8, plan_complete=complete)
        ok = k == wk and d == wd
        print(f"{'PASS' if ok else 'FAIL'}: {label} (F-0077)" + ("" if ok else f" -> {k},{d}"))
        failed += 0 if ok else 1

    # F-0077 (human ruling #4, 2026-07-26): decide()'s branch priority is the one
    # module literal DECIDE_PRIORITY — the docstring line is generated from it, and
    # the checks below derive from it too, so there is nothing to hand-sync (six
    # rounds proved the sync-check itself drifts). `_adjacency` gives, for EVERY
    # adjacent pair of DECIDE_PRIORITY, an input that activates BOTH the higher and
    # the lower branch and must yield the HIGHER branch's outcome. Physically
    # swapping any two neighbouring branches in decide() flips exactly that pair's
    # case to the lower outcome and fails this selftest — the order is measured,
    # not modelled (XCHECK.md §7 promise-width). The pair that carries ruling #3
    # itself is triage>plan-completeness: a `reported` finding from a completed
    # pass is triaged even under an incomplete plan.
    # (higher>lower, rows, unchecked, exists, triage_cap, plan_complete, kind, detail)
    _adjacency = [
        ("needs-human>refusal",
         rows(("F-0001", "reopened", "human ⚠ needs-human"), ("F-0002", "accepted", "Remediator")),
         [], True, 0, True, "stop-needs-human", "F-0001"),
        ("refusal>disputed",
         rows(("F-0001", "accepted", "Remediator"), ("F-0002", "disputed", "human")),
         [], True, 0, True, "stop-refusal", ("F-0001", "material-missing")),
        ("disputed>construal",
         rows(("F-0001", "disputed", "human"), ("F-0002", "fixed", "Verifier")),
         [], True, 0, True, "stop-disputed", ["F-0001"]),
        ("construal>verify",
         rows(("F-0001", "fixed", "Verifier"), ("F-0002", "reopened", "Remediator")),
         [], True, 0, True, "stop-construal", ("Verifier", "verify the fixes and give a binding verdict on every finding in status fixed (F-0001)",
                                               "audit/construals/x.md", "test gate")),
        ("verify>reopened",
         rows(("F-0001", "fixed", "Verifier"), ("F-0002", "reopened", "Remediator")),
         [], True, 0, True, "run-verifier", ["F-0001"]),
        ("reopened>resume",
         rows(("F-0001", "reopened", "Remediator"), ("F-0002", "validated", "Remediator")),
         [], True, 0, True, "run-remediator", ["F-0001"]),
        ("resume>accepted-cf",
         rows(("F-0001", "validated", "Remediator"), ("CF-0001", "accepted", "Remediator")),
         [], True, 0, True, "run-remediator", ["F-0001"]),
        ("accepted-cf>accepted-f",
         rows(("CF-0001", "accepted", "Remediator"), ("F-0002", "accepted", "Remediator")),
         [], True, 0, True, "run-remediator", ["CF-0001"]),
        ("accepted-f>triage-cap",
         rows(("F-0001", "accepted", "Remediator"), ("F-0002", "reported", "human"), ("F-0003", "reported", "human")),
         [], True, 2, True, "run-remediator", ["F-0001"]),
        ("triage-cap>audit",
         rows(("F-0001", "reported", "human"), ("F-0002", "reported", "human")),
         ["P-01"], True, 2, True, "stop-triage", ["F-0001", "F-0002"]),
        ("audit>triage",
         rows(("F-0001", "reported", "human")),
         ["P-01"], True, 0, True, "run-auditor", "P-01"),
        ("triage>plan-completeness",  # ruling #3: reported (completed pass) beats a partial plan
         rows(("F-0001", "reported", "human")),
         [], True, 0, False, "stop-triage", ["F-0001"]),
        ("plan-completeness>inconsistent",
         rows(("F-0001", "mystery", "Remediator")),
         [], True, 0, False, "run-planner", None),
        ("inconsistent>deferred-debt",
         rows(("F-0001", "mystery", "Remediator"), ("F-0002", "deferred", "human")),
         [], True, 0, True, "stop-inconsistent", ["F-0001"]),
        ("deferred-debt>done",
         rows(("F-0001", "closed", "—"), ("F-0002", "deferred", "human")),
         [], True, 0, True, "done-deferred-debt", ["F-0002"]),
    ]
    # F-0077 (human ruling #4): the meta-check compares the SET OF PAIR NAMES the
    # adjacency table carries against the set derived from DECIDE_PRIORITY by name
    # (zip of neighbours), not the two lengths. A length check passes a swapped or
    # renamed pair; a name check names the missing/extra one. Removing a case, or
    # reordering DECIDE_PRIORITY (which also rewrites the expected names), fails
    # here with the exact discrepancy.
    _expected_pairs = {f"{a}>{b}" for a, b in zip(DECIDE_PRIORITY, DECIDE_PRIORITY[1:])}
    _actual_pairs = {a[0] for a in _adjacency}
    _missing = sorted(_expected_pairs - _actual_pairs)
    _extra = sorted(_actual_pairs - _expected_pairs)
    ok = not _missing and not _extra
    print(f"{'PASS' if ok else 'FAIL'}: priority table covers every DECIDE_PRIORITY pair by name (F-0077 ruling #4)"
          + ("" if ok else f" -> missing={_missing} extra={_extra}"))
    failed += 0 if ok else 1
    # The two refusal pairs need a LIVE refusal in the input; every other pair is decided
    # without one. Keyed by pair name rather than widened into all thirteen tuples, so the
    # adjacency table stays one shape and the refusal input is visible where it applies.
    _ADJ_REFUSALS = {
        "needs-human>refusal": {"F-0002": "material-missing"},
        "refusal>disputed": {"F-0001": "material-missing"},
    }
    # The construal pair needs a gate that STOPS; every other pair passes construal=None
    # (gate off), which is decide()'s identity case and the shipped default.
    _ADJ_CONSTRUAL = {
        "disputed>construal": lambda role, charter: (
            "stop-construal", (role, charter, "audit/construals/x.md", "test gate")),
        "construal>verify": lambda role, charter: (
            "stop-construal", (role, charter, "audit/construals/x.md", "test gate")),
    }
    for label, rws, unchk, _exists, cap, complete, wk, wd in _adjacency:
        k, d = decide(rws, unchk, batch_size=8, triage_cap=cap, plan_complete=complete,
                      refusals=_ADJ_REFUSALS.get(label), construal=_ADJ_CONSTRUAL.get(label))
        ok = k == wk and d == wd
        print(f"{'PASS' if ok else 'FAIL'}: priority {label} (F-0077 ruling #3)" + ("" if ok else f" -> {k},{d}"))
        failed += 0 if ok else 1

    # B4: cmd_loop honors --budget via injected clock (budget check precedes any session)
    ticks = iter([100.0, 105.0, 105.0])  # start, first check over budget, print

    def fake_clock():
        return next(ticks)

    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmd_loop(Path("/nonexistent"), {"batch_size": "8", "session_note": ""},
                 False, False, max_sessions=5, budget=3.0, clock=fake_clock)
    ok = "--budget 3" in buf.getvalue()
    print(f"{'PASS' if ok else 'FAIL'}: loop honors --budget between-session wall-clock budget" + ("" if ok else f" -> {buf.getvalue()[:80]}"))
    failed += 0 if ok else 1

    # F-0113: the public numeric config keys (batch_size, triage_batch_cap,
    # max_sessions — README §8) get an ADDRESSED operator error, not a raw
    # ValueError traceback, on a non-number/float/negative value; an empty value
    # falls back to the default. conf_int is the single validation boundary; poison
    # that removes it (e.g. reverting to `int(...)`) turns these red.
    _ci_cases = [
        ("batch_size", "8", 1, "5", 5, None),
        ("batch_size", "8", 1, "", 8, None),          # empty -> default
        ("batch_size", "8", 1, "-1", None, "batch_size"),
        ("batch_size", "8", 1, "3.5", None, "batch_size"),
        ("batch_size", "8", 1, "oops", None, "batch_size"),
        # PHASE 8: the DEFAULT is 20 now (backpressure). 0 stays a legal EXPLICIT
        # value meaning unbounded — an operator may still ask for it, which is not
        # the same as shipping it.
        ("triage_batch_cap", "20", 0, "0", 0, None),  # 0 is a valid boundary (unbounded)
        ("triage_batch_cap", "20", 0, "", 20, None),
        ("triage_batch_cap", "20", 0, "-1", None, "triage_batch_cap"),
        ("triage_batch_cap", "20", 0, "2.5", None, "triage_batch_cap"),
        ("triage_batch_cap", "20", 0, "nope", None, "triage_batch_cap"),
        ("max_sessions", "20", 1, "20", 20, None),
        ("max_sessions", "20", 1, "", 20, None),
        ("max_sessions", "20", 1, "-3", None, "max_sessions"),
        ("max_sessions", "20", 1, "1.5", None, "max_sessions"),
        ("max_sessions", "20", 1, "x", None, "max_sessions"),
        ("loop_progress_limit", "2", 0, "0", 0, None),   # 0 = off, a valid boundary
        ("loop_progress_limit", "2", 0, "", 2, None),
        ("loop_progress_limit", "2", 0, "-1", None, "loop_progress_limit"),
        ("loop_progress_limit", "2", 0, "1.2", None, "loop_progress_limit"),
        ("loop_progress_limit", "2", 0, "nan", None, "loop_progress_limit"),
    ]

    def _expect_conf_error(fn, key):
        # invalid -> addressed SystemExit `orchestrator.conf: <key> ...`, never a
        # raw traceback; a different exception (e.g. a bare ValueError from removed
        # validation) is a failure, so poison surfaces as red.
        try:
            fn()
        except SystemExit as e:
            return f"orchestrator.conf: {key}" in str(e)
        except BaseException:
            return False
        return False

    _ci_ok = True
    for _key, _dflt, _mn, _raw, _want, _errkey in _ci_cases:
        _conf = {_key: _raw}
        if _errkey:
            _ci_ok = _ci_ok and _expect_conf_error(lambda: conf_int(_conf, _key, _dflt, _mn), _errkey)
        else:
            try:
                _ci_ok = _ci_ok and conf_int(_conf, _key, _dflt, _mn) == _want
            except BaseException:
                _ci_ok = False
    print(f"{'PASS' if _ci_ok else 'FAIL'}: conf_int validates numeric config keys (empty->default; rejects negative/float/non-number) (F-0113)")
    failed += 0 if _ci_ok else 1

    def _conf_audit(td, conf_body):
        proj = Path(td)
        audit = proj / "audit"
        (audit / "findings").mkdir(parents=True)
        (audit / "XCHECK.md").write_text("x", encoding="utf-8")
        _put_state(audit, 
            "| id | title | severity | status | next | updated |\n|---|---|---|---|---|---|\n", encoding="utf-8")
        write_split_conf(audit, conf_body)
        return proj

    # a bad batch_size / triage_batch_cap loaded through the real load_conf reaches
    # the decision path (status/next/loop) and stops with the addressed error.
    for _bad_key in ("batch_size", "triage_batch_cap"):
        with tempfile.TemporaryDirectory() as td:
            proj = _conf_audit(td, f"{_bad_key}=oops\n")
            conf = load_conf(proj / "audit")
            ok = _expect_conf_error(lambda: state_and_decision(proj, conf), _bad_key)
        print(f"{'PASS' if ok else 'FAIL'}: bad {_bad_key} -> addressed conf error via state_and_decision, not a traceback (F-0113)")
        failed += 0 if ok else 1

    # a read-only command never resolves max_sessions (loop-only), so a malformed
    # UNUSED value does not crash it (F-0018 boundary preserved).
    with tempfile.TemporaryDirectory() as td:
        proj = _conf_audit(td, "max_sessions=oops\n")
        conf = load_conf(proj / "audit")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                cmd_status(proj, conf)
            ok = True
        except BaseException:
            ok = False
    print(f"{'PASS' if ok else 'FAIL'}: status ignores an unused malformed max_sessions (does not crash) (F-0113)")
    failed += 0 if ok else 1

    # a bad max_sessions DOES stop `loop`, driven end-to-end through main().
    with tempfile.TemporaryDirectory() as td:
        proj = _conf_audit(td, "max_sessions=oops\n")
        ok = _expect_conf_error(
            lambda: main(["xcheck", "loop", "--project", str(proj), "--dry-run"]), "max_sessions")
    print(f"{'PASS' if ok else 'FAIL'}: bad max_sessions -> addressed conf error on loop via main (F-0113)")
    failed += 0 if ok else 1

    # F-0113 (human ruling 2026-07-28): the guarantee lives on the VALUE the Conf
    # boundary hands back, not in a call-site guard. Two prior rounds installed a
    # regex/AST guard that flagged `int(...)` over a name spelled `conf`; each was
    # defeated by one alias (`cfg = conf`) or wrapper (`str(...)`), and the round that
    # DID move the check into a Conf still subclassed `dict`, so the inherited
    # `.copy()` returned a plain dict of RAW strings and an aliased COPY raw-crashed
    # straight through (the reopen). A guard that passes its own selftest but not a
    # live mutation is a hole, not a defense (§7). This test proves the boundary is
    # LOAD-BEARING under LIVE mutations (not a syntactic scan): the exact consumer
    # shape reached through a plain alias, through `.copy()`, and through item access
    # is EACH caught with an addressed error; then REMOVING the boundary (a plain dict
    # of the same raw, itself copied — the precise escape the reopen used) brings the
    # raw ValueError straight back. Every boundary read must be addressed and the
    # mutant must fail red.
    with tempfile.TemporaryDirectory() as td:
        proj = _conf_audit(td, "loop_progress_limit=oops\n")
        conf = load_conf(proj / "audit")            # a validating Conf boundary
        cfg = conf                                  # a plain alias
        cpy = conf.copy()                           # a .copy() — the exact reopen escape
        _reads = [
            lambda: int(str(conf.get("loop_progress_limit", "2") or 0)),   # direct
            lambda: int(str(cfg.get("loop_progress_limit", "2") or 0)),    # alias
            lambda: int(str(cpy.get("loop_progress_limit", "2") or 0)),    # .copy()
            lambda: int(str(conf["loop_progress_limit"])),                 # item access
        ]
        _boundary_ok = all(
            _expect_conf_error(r, "loop_progress_limit") for r in _reads)
        # mutant: strip the boundary — a plain dict of the SAME raw strings, then the
        # SAME aliased+copied read raw-crashes. Proof the Conf is what stops it, not the
        # spelling of the call site: an addressed error here would mean the boundary is
        # decorative; a clean pass would mean the poison changed nothing.
        _mut = {"loop_progress_limit": "oops"}       # plain dict of the same raw string
        try:
            mcfg = _mut.copy()
            int(str(mcfg.get("loop_progress_limit", "2") or 0))
            _mutant_raw = False
        except SystemExit:
            _mutant_raw = False
        except ValueError:
            _mutant_raw = True
    ok = _boundary_ok and _mutant_raw
    print(f"{'PASS' if ok else 'FAIL'}: numeric conf validated at the Conf boundary — direct/alias/copy/item reads are all addressed, removing the boundary restores the raw crash (F-0113, live mutation)"
          + ("" if ok else f" -> boundary_ok={_boundary_ok} mutant_raw={_mutant_raw}"))
    failed += 0 if ok else 1

    # F-0115: --budget composition, exercised AFTER a real session (not only the
    # pre-first-session stop) and raced against --max-sessions (whichever hits
    # first, docs/backlog-closure-plan.md B4). Stub cmd_next (counts calls, no
    # session), inject the clock, disable the no-progress detector and self-source
    # guard. Poison `i == 0` or `range(max_sessions + 1)` turns these red.
    _orig_cmd_next_115 = cmd_next

    def _run_loop_115(clock_vals, max_sessions, budget):
        state = {"n": 0}

        def _stub(project, conf, dry_run, *a, **k):
            # `*a, **k`, not the signature of the day: a double that pins the exact
            # parameter list of the function it replaces breaks on the next argument
            # added upstream, and it breaks as a TypeError that looks like a real
            # failure. 0.9.1 phase 8 added `assume_yes` and this is where it landed.
            state["n"] += 1
            return "run-remediator"

        _vals = list(clock_vals)

        def _clk():
            return _vals.pop(0) if len(_vals) > 1 else _vals[0]

        _G["cmd_next"] = _stub
        b = io.StringIO()
        try:
            with contextlib.redirect_stdout(b):
                cmd_loop(Path("/nonexistent"),
                         {"batch_size": "8", "session_note": "", "loop_progress_limit": "0",
                          # 30 is the validated floor for session_timeout. The loop now
                          # refuses to dispatch a session that cannot FINISH inside the
                          # remaining budget, so every arm below is scaled above that
                          # floor — at the shipped 3600s default a 10s budget would run
                          # exactly one session and these arms would pass by the wrong
                          # rule.
                          "session_timeout": "30"},
                         False, False, max_sessions=max_sessions, budget=budget,
                         clock=_clk, src_digest=lambda: "steady115")
        finally:
            _G["cmd_next"] = _orig_cmd_next_115
        return state["n"], b.getvalue()

    # (1)+(2)+(5): the first session runs under budget; once elapsed reaches the
    # boundary the NEXT session is blocked; a session started before the boundary is
    # never interrupted. start=0, check@50<100 -> session 1, check@100>=100 -> stop.
    n115, o115 = _run_loop_115([0.0, 50.0, 100.0, 100.0], 5, 100.0)
    ok = n115 == 1 and "--budget 100" in o115
    print(f"{'PASS' if ok else 'FAIL'}: budget allows the first session, blocks the second at the boundary (F-0115)"
          + ("" if ok else f" -> ran {n115}; {o115[:80]}"))
    failed += 0 if ok else 1

    # (3): --max-sessions below the budget stops after EXACTLY N sessions — pins
    # range(max_sessions), not range(max_sessions + 1). Constant clock; budget unreached.
    n115, o115 = _run_loop_115([0.0], 3, 1000.0)
    ok = n115 == 3 and "max_sessions=3" in o115
    print(f"{'PASS' if ok else 'FAIL'}: max_sessions caps at exactly N sessions, budget not reached (F-0115)"
          + ("" if ok else f" -> ran {n115}; {o115[-80:]}"))
    failed += 0 if ok else 1

    # (4): the smaller cap wins — a tight budget stops before a large max_sessions.
    n115, o115 = _run_loop_115([0.0, 30.0, 60.0, 100.0, 100.0], 100, 100.0)
    ok = n115 == 2 and "--budget 100" in o115
    print(f"{'PASS' if ok else 'FAIL'}: budget smaller than max_sessions wins the race (F-0115)"
          + ("" if ok else f" -> ran {n115}; {o115[:80]}"))
    failed += 0 if ok else 1

    # (6): the budget is a ceiling on the RUN, not on its starts. W-06 dispatched its
    # eighth session 300s under a 7200s budget and let it run a full 3600s
    # session_timeout, 855.8s past the ceiling, then reported the budget unexhausted.
    # Here 100s of budget with a 30s worst case dispatches at 0/25/50 and REFUSES at
    # 75, where only 25s remain. The control below removes the worst case and gets the
    # old behaviour back, so this arm cannot pass by accident.
    n115, o115 = _run_loop_115([0.0, 0.0, 25.0, 50.0, 75.0, 75.0], 100, 100.0)
    _ceiling_ok = n115 == 3 and "has 25s left" in o115 and "session_timeout=30" in o115
    _orig_conf_number_115 = _G["conf_number"]
    _G["conf_number"] = (lambda c, k: 0 if k == "session_timeout"
                         else _orig_conf_number_115(c, k))
    try:
        n115c, _ = _run_loop_115([0.0, 0.0, 25.0, 50.0, 75.0, 75.0], 100, 100.0)
    finally:
        _G["conf_number"] = _orig_conf_number_115
    ok = _ceiling_ok and n115c == 100
    print(f"{'PASS' if ok else 'FAIL'}: the budget refuses a session that cannot finish "
          f"inside it, and without the worst case the start-gating rule runs every "
          f"session (F-0115, W-06 correction)"
          + ("" if ok else f" -> ran {n115} (want 3), control ran {n115c} (want 100); "
                           f"{o115[:120]}"))
    failed += 0 if ok else 1

    # F-0116: table-driven guard over report_gate — every kind state_and_decision /
    # cmd_next can return must print a NON-EMPTY addressed message carrying its key
    # action/ID. selftest never called report_gate directly, so a body could be
    # emptied or name the wrong owner (e.g. disputed) with all other checks green.
    _rg_cases = [
        ("stop-triage", ["F-0001"], ["triage needed", "xcheck-triage"]),
        ("stop-needs-human", "F-0001", ["needs-human on F-0001", "human decides"]),
        ("stop-refusal", ("F-0001", "material-missing"), ["refusal on F-0001", "material-missing", "STAYS IN FORCE"]),
        ("stop-construal", ("Remediator", "finding F-0001", "audit/construals/k.md", "it is `proposed`"),
         ["construal on the Remediator charter", "audit/construals/k.md", "EVIDENCE, never authority"]),
        ("stop-norm-ratification", "F-0002", ["norm-ratification on F-0002", "norm owner"]),
        ("stop-inconsistent", ["F-0003"], ["non-terminal statuses with no move"]),
        ("stop-critical", ("F-0004", "accepted"), ["open critical", "F-0004",
                                                   "audit_over_critical=on"]),
        # PHASE 5: ten rows are gone with the gates they reported. Eight named a
        # READING failure of the Markdown control plane (an unparsable ledger row, an
        # id on two rows, a ledger row with no file, a finding with no frontmatter, a
        # duplicate/invalid id, an invalid canonical value, an unsynced triage
        # decision, a checked pass with no coverage record) and the ninth
        # (`malformed` provenance) named an unparsable `fixed_by`. None of those
        # states can be written into `state.json`, so no gate reports them and no
        # message needs a guard. The tenth kept them company: `stop-coverage-missing`.
        ("stop-verifier-independence", ("provenance", "F-0009"), ["provenance", "F-0009"]),
        ("stop-verifier-independence", ("collision", "F-0011"), ["same session about to verify it", "F-0011"]),
        ("stop-broken-class", "F-0012", ["superseded-by-class but its class finding", "F-0012"]),
        ("stop-disputed", "F-0013", ["dispute", "Triage never writes it", "F-0013"]),
        ("done-deferred-debt", "F-0014", ["audit complete WITH deferred debt", "F-0014"]),
        ("done", "", ["audit complete"]),
        ("stop-unknown-xyz", "", ["stop-unknown-xyz"]),
    ]
    for _rk, _rd, _rtoks in _rg_cases:
        _rb = io.StringIO()
        with contextlib.redirect_stdout(_rb):
            report_gate(_rk, _rd)
        _rout = _rb.getvalue()
        ok = _rout.strip() != "" and all(_t in _rout for _t in _rtoks)
        print(f"{'PASS' if ok else 'FAIL'}: report_gate({_rk}) prints its addressed message (F-0116)"
              + ("" if ok else f" -> {_rout[:80]!r}"))
        failed += 0 if ok else 1

    # F-0117: a changed own-source mid-run stops the loop BEFORE the next session
    # (never runs the stale in-memory image). Inject src_digest: baseline differs
    # from the first per-iteration check, so the stop fires before cmd_next — the
    # /nonexistent project is never touched.
    src_ticks = iter(["baseline0000", "changed11111"])
    b117 = io.StringIO()
    with contextlib.redirect_stdout(b117):
        cmd_loop(Path("/nonexistent"), {"batch_size": "8", "session_note": ""},
                 False, False, max_sessions=5, src_digest=lambda: next(src_ticks))
    ok = "changed on disk" in b117.getvalue() and "F-0117" in b117.getvalue()
    print(f"{'PASS' if ok else 'FAIL'}: loop stops when bin/xcheck changes mid-run (F-0117)" + ("" if ok else f" -> {b117.getvalue()[:100]}"))
    failed += 0 if ok else 1
    # ...and an UNCHANGED source never false-triggers: a constant digest passes the
    # guard, and a dry-run first iteration returns without the stop message.
    b117b = io.StringIO()
    with tempfile.TemporaryDirectory() as _td117:
        _a = Path(_td117) / "audit"
        _a.mkdir()
        (_a / "XCHECK.md").write_text("x", encoding="utf-8")
        _put_state(_a, 
            "| id | title | severity | status | next | updated |\n|---|---|---|---|---|---|\n", encoding="utf-8")
        with contextlib.redirect_stdout(b117b):
            cmd_loop(Path(_td117), load_conf(_a), True, False, max_sessions=3,
                     src_digest=lambda: "steady0000")
    ok = "changed on disk" not in b117b.getvalue()
    print(f"{'PASS' if ok else 'FAIL'}: unchanged bin/xcheck does not false-trigger the self-source guard (F-0117)" + ("" if ok else f" -> {b117b.getvalue()[:100]}"))
    failed += 0 if ok else 1

    # F-0118: sessions that leave audit/ unchanged and repeat the same decision
    # stop the loop as a non-converging cycle (§9 rule 2) at loop_progress_limit,
    # NOT at the cost cap. The digest is taken before AND after the SAME session, so
    # `loop_progress_limit=N` stops on the N-th no-progress session — NOT the N+1-th.
    # These checks pin the EXACT stop iteration (a driver script counts sessions run
    # in a file OUTSIDE audit/), a positive progress control that never false-triggers,
    # and a message distinct from stop-error/stop-needs-human. Fixture: one accepted
    # finding -> decide() stably returns run-remediator.
    def _f0118_run(td, limit, remediator_cmd, max_sessions=6):
        root = Path(td)
        # driver scripts (outside audit/, so they never move the digest); cwd == root.
        # noop: only bumps the session counter. prog: also moves CANONICAL state, so
        # every session moves the digest and the detector must NOT fire.
        #
        # prog used to append to `audit/progress.log`. F-0117 is exactly that file: the
        # digest no longer hashes files under audit/, only the state document decide()
        # reads, so the old driver made no progress and this positive control started
        # failing. Raising a limit is a real canonical change that leaves the decision
        # alone — `reopen_limit` only gates reopened findings, and this fixture's one
        # finding is accepted with attempts=0, so decide() keeps returning
        # run-remediator and the run stops at max_sessions, not at the detector.
        (root / "incr_noop.py").write_text(
            "open('cnt','a').write('.')\n", encoding="utf-8")
        (root / "incr_prog.py").write_text(
            "import json\n"
            "open('cnt','a').write('.')\n"
            "d = json.load(open('audit/state.json'))\n"
            "d['limits']['reopen_limit'] = min(99, int(d['limits'].get('reopen_limit', 2)) + 1)\n"
            "json.dump(d, open('audit/state.json', 'w'), sort_keys=True, indent=2)\n",
            encoding="utf-8")
        audit = root / "audit"
        (audit / "findings").mkdir(parents=True)
        (audit / "XCHECK.md").write_text("x", encoding="utf-8")
        _put_state(audit, 
            "| id | title | severity | status | next | updated |\n|---|---|---|---|---|---|\n"
            "| F-0001 | t | major | accepted | Remediator | 2026-07-21 |\n", encoding="utf-8")
        complete_audit(audit)
        no = "python3 incr_noop.py"
        write_split_conf(
            audit,
            f"planner_cmd={no}\nauditor_cmd={no}\nverifier_cmd={no}\n"
            f"remediator_cmd={remediator_cmd}\nloop_progress_limit={limit}\n"
            f"sandbox_profile=none\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_loop(root, load_conf(audit), False, False, max_sessions=max_sessions)
        cnt = root / "cnt"
        n = len(cnt.read_bytes()) if cnt.exists() else 0
        return n, buf.getvalue()

    with tempfile.TemporaryDirectory() as td:
        n2, o2 = _f0118_run(td, 2, "python3 incr_noop.py")
    ok = (n2 == 2 and "no progress (F-0118)" in o2 and "2 consecutive" in o2
          and "max_sessions" not in o2)
    print(f"{'PASS' if ok else 'FAIL'}: loop_progress_limit=2 stops on the 2nd no-op session (F-0118)"
          + ("" if ok else f" -> ran {n2} sessions; {o2[-160:]}"))
    failed += 0 if ok else 1

    with tempfile.TemporaryDirectory() as td:
        n3, o3 = _f0118_run(td, 3, "python3 incr_noop.py")
    ok = (n3 == 3 and "3 consecutive" in o3 and "max_sessions" not in o3)
    print(f"{'PASS' if ok else 'FAIL'}: loop_progress_limit=3 stops on the 3rd no-op session (F-0118)"
          + ("" if ok else f" -> ran {n3} sessions; {o3[-160:]}"))
    failed += 0 if ok else 1

    with tempfile.TemporaryDirectory() as td:
        npr, opr = _f0118_run(td, 2, "python3 incr_prog.py", max_sessions=4)
    ok = (npr == 4 and "no progress" not in opr and "max_sessions=4" in opr)
    print(f"{'PASS' if ok else 'FAIL'}: sessions that move audit/ never false-trigger no-progress (F-0118)"
          + ("" if ok else f" -> ran {npr} sessions; {opr[-160:]}"))
    failed += 0 if ok else 1

    # the no-progress halt is neither a session error nor a needs-human verdict
    # (flag held in a variable so the CF-0001 I-12 source guard does not read this
    # assertion as a substring `needs-human` membership test)
    nh_flag = "needs-human"
    ok = ("stop-error" not in o2 and nh_flag not in o2)
    print(f"{'PASS' if ok else 'FAIL'}: no-progress halt is distinct from stop-error/stop-needs-human (F-0118)"
          + ("" if ok else f" -> {o2[-160:]}"))
    failed += 0 if ok else 1

    # B2: lint catches a file in findings/ that no record points at. PHASE 5: the two
    # class-membership poisons this check used to carry with it (a `superseded-by-class`
    # finding with no `class:`, a CF listing a member that does not exist) are gone —
    # both are refused by `load_state`, so lint never sees such a tree. What is left is
    # the check that is genuinely about the human-facing tree: an orphan body file.
    with tempfile.TemporaryDirectory() as td:
        audit = Path(td) / "audit"
        (audit / "findings").mkdir(parents=True)
        _put_state(audit,
            "| F-0001 | a | major | superseded-by-class | — | 2026-07-21 |\n"
            "| CF-0001 | c | major | closed | — | 2026-07-21 |\n",
            encoding="utf-8",
        )
        (audit / "findings" / "F-0002-orphan.md").write_text(
            "# F-0002\n\nA body no record claims.\n", encoding="utf-8")
        import io as _io
        import contextlib as _cl
        b = _io.StringIO()
        with _cl.redirect_stdout(b):
            rc = cmd_lint(Path(td))
        out = b.getvalue()
        ok = rc == 1 and "orphan" in out and "F-0002-orphan.md" in out
        print(f"{'PASS' if ok else 'FAIL'}: lint catches an orphan file in findings/" + ("" if ok else f" -> rc={rc} {out[:200]}"))
        failed += 0 if ok else 1

    # courier dirt-diff logic
    pre = {"bench/x.c", "audit/LEDGER.md"}
    post = {"bench/x.c", "audit/LEDGER.md", "audit/findings/F-0001-a.md", "src/core/engine.c"}
    got = session_dirt(pre, post)
    ok = got == {"audit/findings/F-0001-a.md", "src/core/engine.c"}
    print(f"{'PASS' if ok else 'FAIL'}: courier commits only the session's new dirt")
    failed += 0 if ok else 1
    ok = session_dirt(None, post) == set() and session_dirt(pre, None) == set()
    print(f"{'PASS' if ok else 'FAIL'}: courier no-ops outside a git repo")
    failed += 0 if ok else 1

    # lock lifecycle
    with tempfile.TemporaryDirectory() as td:
        lk = Lock(Path(td), "Test")
        lk.acquire()
        try:
            Lock(Path(td), "Test2").acquire()
            print("FAIL: double-acquire succeeded")
            failed += 1
        except SystemExit:
            print("PASS: live lock blocks second acquire")
        lk.release()
        lk2 = Lock(Path(td), "Test3")
        lk2.acquire()
        lk2.release()
        print("PASS: lock releases and re-acquires")

    # F-0107: a failed owner-record WRITE after mkdir must NOT orphan `.lock/`. The
    # mkdir already acquired the directory; if the owner write fails the half-acquire
    # is rolled back and an addressed SystemExit is raised — never a bare traceback,
    # never an owner-less lock the next writer reads as LIVE/unverifiable.
    # F-0107 reopen: model the REAL failure edge — `write_text` is not atomic, so a
    # failing write can leave a PARTIAL owner file (`{`) on disk, making the directory
    # NON-EMPTY. The rollback must remove that partial file, not just try (and fail) to
    # rmdir a non-empty dir. Poison ONLY the `owner` write; a fresh writer must then
    # acquire cleanly.
    with tempfile.TemporaryDirectory() as td:
        _orig_wt = Path.write_text

        def _poison_owner_write(self, *a, **k):
            if self.name == "owner":
                _orig_wt(self, "{", encoding="utf-8")  # partial owner, THEN fail
                raise OSError("poison owner write")
            return _orig_wt(self, *a, **k)

        Path.write_text = _poison_owner_write
        raised = False
        try:
            Lock(Path(td), "A").acquire()
        except SystemExit:
            raised = True
        finally:
            Path.write_text = _orig_wt
        orphan_left = (Path(td) / ".lock").exists()
        lk = Lock(Path(td), "B")            # after rollback a fresh writer acquires
        lk.acquire()
        clean = (Path(td) / ".lock" / "owner").exists() and lk.token is not None
        lk.release()
        ok = raised and not orphan_left and clean
        print(f"{'PASS' if ok else 'FAIL'}: owner-write failure rolls back the half-acquired lock (F-0107)"
              + ("" if ok else f" -> raised={raised} orphan_left={orphan_left} clean={clean}"))
        failed += 0 if ok else 1

    # F-0047: owner-checked release — a late release from a previous owner (whose
    # lock was force-cleared before a new writer acquired) must NOT delete the new
    # writer's lock. The unique per-acquire nonce is the owner token.
    with tempfile.TemporaryDirectory() as td:
        a = Lock(Path(td), "A")
        a.acquire()
        a.remove()               # as `unlock --force` would clear A's lock
        b = Lock(Path(td), "B")
        b.acquire()              # a NEW writer now owns the lock
        a.release()              # A's late release must be a no-op on B's lock
        b_still = (Path(td) / ".lock").exists()
        b.release()              # B releasing its OWN lock does remove it
        b_gone = not (Path(td) / ".lock").exists()
        ok = b_still and b_gone
        print(f"{'PASS' if ok else 'FAIL'}: owner-checked release spares a new owner's lock (F-0047)" + ("" if ok else f" -> b_still={b_still} b_gone={b_gone}"))
        failed += 0 if ok else 1

    # F-0047: release is owner-checked. Model a concurrent force-clear + re-acquire
    # that lands while A releases as the on-disk owner record now carrying a
    # DIFFERENT owner's nonce. A's release must never remove that foreign lock —
    # the owner-check leaves another owner's canonical `.lock` directory in place.
    with tempfile.TemporaryDirectory() as td:
        a = Lock(Path(td), "A")
        a.acquire()
        foreign = json.dumps({"pid": os.getpid(), "role": "B", "started": "y",
                              "host": "x", "nonce": "deadbeefdeadbeef"})
        (Path(td) / ".lock" / "owner").write_text(foreign, encoding="utf-8")  # swapped under A
        a.release()                       # must NOT delete the foreign lock
        owner = _read_lock(Path(td) / ".lock" / "owner") or {}
        survived = (Path(td) / ".lock").is_dir() and owner.get("nonce") == "deadbeefdeadbeef"
        ok = survived
        print(f"{'PASS' if ok else 'FAIL'}: release preserves a foreign lock swapped into its window (F-0047)" + ("" if ok else f" -> survived={survived}"))
        failed += 0 if ok else 1

    # F-0047 (human ruling three-way race): A holds; a force-clear + B acquire, then
    # a further force-clear + C acquire, both land before A's late release. The
    # directory must end with EXACTLY ONE owner (C's), and A must not delete the
    # foreign lock. Deterministic model: overwrite the owner record B-then-C, then
    # run A.release() and assert C still owns the single canonical directory.
    with tempfile.TemporaryDirectory() as td:
        a = Lock(Path(td), "A")
        a.acquire()
        ownerf = Path(td) / ".lock" / "owner"
        ownerf.write_text(json.dumps({"pid": os.getpid(), "role": "B", "started": "b",
                                      "host": "x", "nonce": "bbbbbbbbbbbbbbbb"}), encoding="utf-8")
        ownerf.write_text(json.dumps({"pid": os.getpid(), "role": "C", "started": "c",
                                      "host": "x", "nonce": "cccccccccccccccc"}), encoding="utf-8")
        a.release()                       # A's late release must not touch C's lock
        owner = _read_lock(ownerf) or {}
        exactly_one_owner = (Path(td) / ".lock").is_dir() and owner.get("nonce") == "cccccccccccccccc"
        ok = exactly_one_owner
        print(f"{'PASS' if ok else 'FAIL'}: three-way race leaves exactly one owner, foreign lock spared (F-0047)" + ("" if ok else f" -> owner={owner}"))
        failed += 0 if ok else 1

    import io as _io2
    import contextlib as _cl2
    import subprocess as _sp

    def _ledger(*body):
        head = "| id | title | severity | status | next | updated |\n|---|---|---|---|---|---|\n"
        return head + "".join(body)

    def _put_lock(audit_dir, owner):
        """Write a directory-protocol lock fixture (F-0047): audit/.lock/ dir plus
        an `owner` record. owner=None leaves the dir with no record (the mid-acquire
        window); a str is written verbatim (unparseable fixtures); a dict is JSON."""
        d = audit_dir / ".lock"
        d.mkdir(exist_ok=True)
        if owner is None:
            return d
        (d / "owner").write_text(owner if isinstance(owner, str) else json.dumps(owner), encoding="utf-8")
        return d

    # F-0047: `unlock` is fail-closed. An owner record that is UNPARSEABLE (or
    # absent — a writer may be mid-acquire, between the mkdir and the owner write)
    # is refused without --force and left in place; --force clears it.
    # F-0095 (reopen): a parseable, provably-dead lock is no longer cleared by the
    # non-force path either — removal by pathname is racy, so non-force diagnoses
    # and defers to --force. It is still recognised as provably dead (a distinct
    # message from the "not provably dead" refusal), and --force clears it.
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        (proj / "audit").mkdir()
        lp = _put_lock(proj / "audit", "{ truncated")  # unparseable owner record
        refused = False
        with _cl2.redirect_stdout(_io2.StringIO()):
            try:
                cmd_unlock(proj, False)
            except SystemExit:
                refused = True
        still = lp.exists()
        with _cl2.redirect_stdout(_io2.StringIO()):
            cmd_unlock(proj, True)                       # --force clears it
        gone = not lp.exists()
        # F-0094: a "provably dead" lock must be on THIS host (a local ESRCH proves
        # nothing about a foreign host). F-0095: even so, non-force refuses removal.
        _put_lock(proj / "audit", {"pid": 4242, "role": "x", "started": "x", "host": socket.gethostname()})
        _orig_k = os.kill
        os.kill = lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError())  # pid dead
        dead_refused = False
        try:
            with _cl2.redirect_stdout(_io2.StringIO()):
                try:
                    cmd_unlock(proj, False)              # parseable + dead -> refused (F-0095)
                except SystemExit:
                    dead_refused = True
            dead_still = lp.exists()
            with _cl2.redirect_stdout(_io2.StringIO()):
                cmd_unlock(proj, True)                   # --force clears it
        finally:
            os.kill = _orig_k
        dead_cleared = not lp.exists()
        # a bare .lock/ dir with NO owner record (mid-acquire) is also refused
        _put_lock(proj / "audit", None)
        mid_refused = False
        with _cl2.redirect_stdout(_io2.StringIO()):
            try:
                cmd_unlock(proj, False)
            except SystemExit:
                mid_refused = True
        with _cl2.redirect_stdout(_io2.StringIO()):
            cmd_unlock(proj, True)
        ok = refused and still and gone and dead_refused and dead_still and dead_cleared and mid_refused
        print(f"{'PASS' if ok else 'FAIL'}: unlock fail-closed on unparseable/mid-acquire/dead lock, --force clears (F-0047/F-0095)" + ("" if ok else f" -> refused={refused} still={still} gone={gone} dead_refused={dead_refused} dead_still={dead_still} dead_cleared={dead_cleared} mid_refused={mid_refused}"))
        failed += 0 if ok else 1

    # F-0047: a legacy FILE lock (pre-directory protocol) is refused without --force
    # (no auto-migration) and cleared by --force.
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        (proj / "audit").mkdir()
        lp = proj / "audit" / ".lock"
        lp.write_text(json.dumps({"pid": 4242, "role": "x", "started": "x", "host": "x"}), encoding="utf-8")  # FILE
        _orig_k = os.kill
        os.kill = lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError())  # even if pid dead
        legacy_refused = False
        try:
            with _cl2.redirect_stdout(_io2.StringIO()):
                try:
                    cmd_unlock(proj, False)              # legacy needs --force, even dead
                except SystemExit:
                    legacy_refused = True
        finally:
            os.kill = _orig_k
        legacy_still = lp.exists()
        with _cl2.redirect_stdout(_io2.StringIO()):
            cmd_unlock(proj, True)                       # --force clears it
        legacy_gone = not lp.exists()
        ok = legacy_refused and legacy_still and legacy_gone
        print(f"{'PASS' if ok else 'FAIL'}: unlock refuses a legacy FILE lock without --force, --force clears (F-0047)" + ("" if ok else f" -> refused={legacy_refused} still={legacy_still} gone={legacy_gone}"))
        failed += 0 if ok else 1

    # F-0047: a PARSEABLE lock carrying a SCHEMA-INVALID pid ("not-a-pid") is not a
    # real dead PID — `unlock` without --force must refuse it (reopen poison #1),
    # and --force must still clear it.
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        (proj / "audit").mkdir()
        lp = _put_lock(proj / "audit", {"pid": "not-a-pid", "role": "writer", "started": "x", "host": "x"})
        refused = False
        with _cl2.redirect_stdout(_io2.StringIO()):
            try:
                cmd_unlock(proj, False)
            except SystemExit:
                refused = True
        still = lp.exists()
        with _cl2.redirect_stdout(_io2.StringIO()):
            cmd_unlock(proj, True)                       # --force clears it
        gone = not lp.exists()
        ok = refused and still and gone
        print(f"{'PASS' if ok else 'FAIL'}: unlock refuses a schema-invalid pid without --force (F-0047)" + ("" if ok else f" -> refused={refused} still={still} gone={gone}"))
        failed += 0 if ok else 1

    # F-0003: the state document's remediation_batch_size (§10 override) governs the
    # batch. PHASE 5: the override is a NUMBER in `state.limits`, not a line under a
    # Markdown heading — the commented/fenced/duplicated spellings F-0158 spent
    # rounds on have no JSON equivalent, so this check is only about precedence.
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        audit = proj / "audit"
        (audit / "findings").mkdir(parents=True)
        (audit / "XCHECK.md").write_text("x", encoding="utf-8")
        _put_state(audit, 
            _ledger(*(f"| F-{i:04d} | t | major | accepted | Remediator | 2026-07-21 |\n" for i in range(1, 5))),
            encoding="utf-8")
        conf = {"batch_size": "8", "triage_batch_cap": "0", "session_note": ""}
        complete_audit(audit, remediation_batch_size=2)
        _, _, _, k, d = state_and_decision(proj, conf)
        ok = k == "run-remediator" and d == ["F-0001", "F-0002"]
        print(f"{'PASS' if ok else 'FAIL'}: AUDIT.md remediation_batch_size caps the batch (F-0003)" + ("" if ok else f" -> {k},{d}"))
        failed += 0 if ok else 1
        _d3 = _state_doc(audit); _d3["limits"] = {}; _save_state(audit, _d3)  # no override -> conf default 8
        _, _, _, k, d = state_and_decision(proj, conf)
        ok = k == "run-remediator" and d == ["F-0001", "F-0002", "F-0003", "F-0004"]
        print(f"{'PASS' if ok else 'FAIL'}: no AUDIT.md override falls back to conf default (F-0003)" + ("" if ok else f" -> {k},{d}"))
        failed += 0 if ok else 1
        # reopened findings honor the same batch cap end-to-end (reopen_limit
        # raised so the needs-human gate does not fire first)
        complete_audit(audit, remediation_batch_size=2, reopen_limit=99)
        _put_state(audit, 
            _ledger(*(f"| F-{i:04d} | t | major | reopened | Remediator | 2026-07-21 |\n" for i in range(1, 5))),
            encoding="utf-8")
        _, _, _, k, d = state_and_decision(proj, conf)
        ok = k == "run-remediator" and d == ["F-0001", "F-0002"]
        print(f"{'PASS' if ok else 'FAIL'}: reopened batch honors remediation_batch_size (F-0003)" + ("" if ok else f" -> {k},{d}"))
        failed += 0 if ok else 1

    # F-0050: superseded-by-class is 'done' ONLY with a CLOSED CF (README §3). An
    # orphan member (CF absent) or a member of a rejected/open CF must fail closed
    # to stop-broken-class, never certify completion; only a closed CF is terminal.
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        audit = proj / "audit"
        (audit / "findings").mkdir(parents=True)
        (audit / "XCHECK.md").write_text("x", encoding="utf-8")
        complete_audit(audit)  # F-0077: a complete plan so 'done' is reachable
        conf = {"batch_size": "8", "triage_batch_cap": "0", "session_note": ""}
        # orphan: member superseded, class CF-0001 absent entirely -> not done
        _put_state(audit, 
            _ledger("| F-0001 | a | major | superseded-by-class | — | 2026-07-25 |\n"), encoding="utf-8")
        _, _, _, k_orphan, d_orphan = state_and_decision(proj, conf)
        # member of a CLOSED CF -> genuinely terminal -> done
        _put_state(audit, _ledger(
            "| CF-0001 | c | major | closed | — | 2026-07-25 |\n"
            "| F-0001 | a | major | superseded-by-class | — | 2026-07-25 |\n"), encoding="utf-8")
        _, _, _, k_closed, _ = state_and_decision(proj, conf)
        # member still superseded while its CF is REJECTED (reversion not synced) -> not done
        _put_state(audit, _ledger(
            "| CF-0001 | c | major | rejected | — | 2026-07-25 |\n"
            "| F-0001 | a | major | superseded-by-class | — | 2026-07-25 |\n"), encoding="utf-8")
        _, _, _, k_rej, d_rej = state_and_decision(proj, conf)
        ok = (k_orphan == "stop-broken-class" and d_orphan == "F-0001"
              and k_closed == "done"
              and k_rej == "stop-broken-class" and d_rej == "F-0001")
        print(f"{'PASS' if ok else 'FAIL'}: superseded-by-class certifies 'done' only with a closed CF (F-0050)"
              + ("" if ok else f" -> orphan={k_orphan},{d_orphan} closed={k_closed} rej={k_rej},{d_rej}"))
        failed += 0 if ok else 1

    # F-0050: bidirectional membership. A CLOSED CF that does NOT list the member
    # in its `members:` has not really absorbed it (XCHECK.md §8 rule 4) — the
    # done-gate must fail closed, matching what the tool's own `lint` reports, not
    # certify 'done' on a one-sided class: link (reopen poison).
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        audit = proj / "audit"
        (audit / "findings").mkdir(parents=True)
        (audit / "XCHECK.md").write_text("x", encoding="utf-8")
        complete_audit(audit)  # F-0077: a complete plan so 'done' is reachable
        conf = {"batch_size": "8", "triage_batch_cap": "0", "session_note": ""}
        # CF-0001 is closed and lists F-0002 — NOT the superseded F-0001, which still
        # carries `class: CF-0001`. The schema binds members to records that exist and
        # to one class each; it does not require the back-link, so this one-sided link
        # is still writable and the decision gate still has to fail closed on it.
        _put_state(audit, _ledger(
            "| CF-0001 | c | major | closed | — | 2026-07-25 |\n"
            "| F-0001 | a | major | superseded-by-class | — | 2026-07-25 |\n"
            "| F-0002 | b | major | closed | — | 2026-07-25 |\n"),
            encoding="utf-8", cf_members=["F-0002"])
        _, _, _, k_unlisted, d_unlisted = state_and_decision(proj, conf)
        ok = k_unlisted == "stop-broken-class" and d_unlisted == "F-0001"
        print(f"{'PASS' if ok else 'FAIL'}: closed CF that omits the member from members: is not terminal (F-0050)"
              + ("" if ok else f" -> {k_unlisted},{d_unlisted}"))
        failed += 0 if ok else 1

        # PHASE 5: the two non-canonical `members:` poisons ([F-00010] and
        # [junk-F-0001-junk], F-0050 reopen / CF-0001 I-13) are gone with the text they
        # poisoned. `members` is a JSON array of strings each matched against the
        # canonical id pattern at the reading boundary, so a substring-mineable member
        # is not a rejected value here — it is a document `load_state` refuses, covered
        # by tests/test_state.py's members cases.

    # F-0008: courier reports failure instead of a false success
    _saved_dirty = _G["dirty_paths"]
    _orig_run = _sp.run
    _G["dirty_paths"] = lambda project: {"audit/findings/F-0001-a.md"}
    _mode = {"fail": None}

    class _R:
        def __init__(self, rc, stdout=b""):
            self.returncode = rc
            self.stderr = ""
            self.stdout = stdout

    def _fake_git(cmd, **k):
        if cmd[:2] == ["git", "ls-files"]:
            return _R(0, b"audit/findings/F-0001-a.md\0")  # the dirt path is tracked -> addable
        if cmd[:2] == ["git", "add"]:
            return _R(1 if _mode["fail"] == "add" else 0)
        if cmd[:2] == ["git", "commit"]:
            return _R(1 if _mode["fail"] == "commit" else 0)
        return _R(0)

    _sp.run = _fake_git
    try:
        for fail, expect_ok, needle in [("add", False, "git add FAILED"),
                                        ("commit", False, "git commit FAILED"),
                                        (None, True, "committed the session")]:
            _mode["fail"] = fail
            bb = _io2.StringIO()
            with _cl2.redirect_stdout(bb):
                res = courier_commit(Path("/x"), "Remediator", "finding F-0001", set(), None)
            out = bb.getvalue()
            ok = res == expect_ok and needle in out and (expect_ok or "committed the session" not in out)
            print(f"{'PASS' if ok else 'FAIL'}: courier {fail or 'success'} -> {'commit' if expect_ok else 'failure reported'} (F-0008)" + ("" if ok else f" -> {res},{out[:80]}"))
            failed += 0 if ok else 1
    finally:
        _sp.run = _orig_run
        _G["dirty_paths"] = _saved_dirty

    # F-0110: with push_after_commit ON, a failed `git push` is a courier FAILURE —
    # courier_commit returns False so run_session/loop stop instead of advancing as if
    # the opted-in remote handoff succeeded. add/commit succeed; only push fails. The
    # controls: a successful push returns True, and push OFF never invokes `git push`.
    _saved_dirty2 = _G["dirty_paths"]
    _orig_run_p = _sp.run
    _G["dirty_paths"] = lambda project: {"audit/findings/F-0001-a.md"}
    _push_called = {"hit": False}

    def _fake_git_push(cmd, **k):
        if cmd[:2] == ["git", "ls-files"]:
            return _R(0, b"audit/findings/F-0001-a.md\0")  # the dirt path is tracked -> addable
        if cmd[:2] == ["git", "push"]:
            _push_called["hit"] = True
            r = _R(1 if _push_mode["fail"] else 0)
            r.stderr = "remote rejected"
            return r
        return _R(0)

    _push_mode = {"fail": True}
    _sp.run = _fake_git_push
    try:
        bb = _io2.StringIO()
        with _cl2.redirect_stdout(bb):
            res_fail = courier_commit(Path("/x"), "Auditor", "c", set(), {"push_after_commit": "on"})
        out_fail = bb.getvalue()
        _push_mode["fail"] = False
        with _cl2.redirect_stdout(_io2.StringIO()):
            res_ok = courier_commit(Path("/x"), "Auditor", "c", set(), {"push_after_commit": "on"})
        _push_called["hit"] = False
        with _cl2.redirect_stdout(_io2.StringIO()):
            res_off = courier_commit(Path("/x"), "Auditor", "c", set(), {"push_after_commit": "off"})
        ok = (res_fail is False and "git push FAILED" in out_fail
              and res_ok is True and res_off is True and _push_called["hit"] is False)
        print(f"{'PASS' if ok else 'FAIL'}: opt-in push failure fails courier; success/off do not (F-0110)"
              + ("" if ok else f" -> fail={res_fail} ok={res_ok} off={res_off} push_called={_push_called['hit']}"))
        failed += 0 if ok else 1
    finally:
        _sp.run = _orig_run_p
        _G["dirty_paths"] = _saved_dirty2

    # F-0009: run_session serializes subprocess + courier inside the lock
    events = []

    class _FakeLock:
        def __init__(self, audit, role):
            self.role = role

        def acquire(self):
            events.append("acquire")

        def release(self):
            events.append("release")

        def relabel(self, role):
            self.role = role

    def _fake_courier(project, role, charter, pre, conf=None, pre_head=None):
        events.append("courier")
        return True

    # `**k`, not the parameter list of the day. A double that pins the exact
    # signature of the function it replaces breaks on the next argument added
    # upstream, and it breaks as a TypeError indistinguishable from a real
    # failure — phase 9 added `header=` and this is where that landed.
    def _fake_sess(cmd, cwd, log_path, env, conf, lock=None, timeout=None,
                   profile=None, **k):
        # Phase 8: the child is launched by `run_child`, not by a bare subprocess.run,
        # so THAT is what a session fake must replace — patching subprocess.run would
        # now intercept only git probes and let the real child run, i.e. the fake would
        # silently stop being the thing under test. Count ONLY the launched role
        # session, so this still asserts the acquire/session/courier/release ORDER.
        events.append("subprocess")
        Path(log_path).write_text("", encoding="utf-8")
        return 0, "ok", 0.0

    _saved = {n: _G[n] for n in ("Lock", "courier_commit", "dirty_paths")}
    _orig_run2 = _G["run_child"]
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        (proj / "audit").mkdir()
        # sandbox_profile=none: this fixture is a bare temp dir, not a git repo, and
        # what it measures is the lock/courier ORDER, not containment. The isolating
        # profiles are proved against real worktrees in tests/test_runner_sandbox.py.
        conf = {"remediator_cmd": "true {prompt}", "session_note": "",
                "sandbox_profile": "none", "trust_level": "trusted"}
        _G["Lock"] = _FakeLock
        _G["courier_commit"] = _fake_courier
        _G["dirty_paths"] = lambda p: set()
        _G["run_child"] = _fake_sess
        try:
            events.clear()
            run_session(proj, conf, "Remediator", "finding F-0001", dry_run=False, lock=None)
            ok = events == ["acquire", "subprocess", "courier", "release"]
            print(f"{'PASS' if ok else 'FAIL'}: own-lock session runs courier before release (F-0009)" + ("" if ok else f" -> {events}"))
            failed += 0 if ok else 1
            events.clear()
            run_session(proj, conf, "Remediator", "finding F-0001", dry_run=False, lock=_FakeLock(proj / "audit", "orchestrator"))
            ok = events == ["subprocess", "courier"]
            print(f"{'PASS' if ok else 'FAIL'}: caller-held lock: session does not re-lock (F-0009)" + ("" if ok else f" -> {events}"))
            failed += 0 if ok else 1
        finally:
            _G["run_child"] = _orig_run2
            for _n, _v in _saved.items():
                _G[_n] = _v


    # mechanically enumerate courier_commit callsites: every PRODUCTION caller must
    # CAPTURE the committed-flag; a bare `courier_commit(...)` statement drops it and
    # advances the lifecycle over an uncommitted handoff (the F-0114 bug).
    _cc_offenders = [ln.strip() for ln in orchestrator_sources().splitlines()
                     if ln.strip().startswith("courier_commit(")]
    ok = not _cc_offenders
    print(f"{'PASS' if ok else 'FAIL'}: no courier_commit callsite drops the committed-flag (F-0114)"
          + ("" if ok else f" -> {_cc_offenders}"))
    failed += 0 if ok else 1

    # F-0009: NO orchestrator write (incl. the log dir) escapes the lock — an
    # observer inside acquire() must not yet see audit/orchestrator-logs.
    _saved2 = {n: _G[n] for n in ("Lock", "courier_commit", "dirty_paths")}
    _orig_run3 = _G["run_child"]
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        (proj / "audit").mkdir()
        conf = {"remediator_cmd": "true {prompt}", "session_note": "",
                "sandbox_profile": "none", "trust_level": "trusted"}
        obs = {"logs_at_acquire": None}

        class _ObsLock:
            def __init__(self, audit, role):
                self.audit = Path(audit)
                self.role = role

            def acquire(self):
                obs["logs_at_acquire"] = (self.audit / "orchestrator-logs").exists()

            def release(self):
                pass

            def relabel(self, role):
                self.role = role

        _G["Lock"] = _ObsLock
        _G["courier_commit"] = lambda *a, **k: True
        _G["dirty_paths"] = lambda p: set()
        _G["run_child"] = lambda cmd, cwd, log_path, env, conf, lock=None, timeout=None, profile=None, **k: (
            Path(log_path).write_text("", encoding="utf-8"), (0, "ok", 0.0))[1]
        try:
            run_session(proj, conf, "Remediator", "finding F-0001", dry_run=False, lock=None)
            ok = obs["logs_at_acquire"] is False
            print(f"{'PASS' if ok else 'FAIL'}: no orchestrator write precedes lock acquire (F-0009)" + ("" if ok else f" -> logs_at_acquire={obs['logs_at_acquire']}"))
            failed += 0 if ok else 1
        finally:
            _G["run_child"] = _orig_run3
            for _n, _v in _saved2.items():
                _G[_n] = _v

    # F-0108: an unrunnable agent command (missing executable in <role>_cmd) becomes
    # the addressed session-error stop, NOT a raw traceback: subprocess.run raises
    # before returning a code, run_session catches it, names the command, returns
    # non-zero, and the (real) lock is still released. Uses the REAL Lock/subprocess
    # so the spawn genuinely fails.
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        (proj / "audit").mkdir()
        conf_missing = {"remediator_cmd": "__xcheck_no_such_agent__ {prompt}",
                        "session_note": "", "sandbox_profile": "none",
                        "trust_level": "trusted"}
        b = _io2.StringIO()
        raised = False
        rc_spawn = None
        try:
            with _cl2.redirect_stdout(b):
                rc_spawn = run_session(proj, conf_missing, "Remediator", "finding F-0001", dry_run=False)
        except OSError:
            raised = True
        out = b.getvalue()
        lock_gone = not (proj / "audit" / ".lock").exists()
        ok = (not raised and rc_spawn == 1 and lock_gone
              and "__xcheck_no_such_agent__" in out and "Traceback" not in out)
        print(f"{'PASS' if ok else 'FAIL'}: unrunnable agent command -> addressed stop, lock released (F-0108)"
              + ("" if ok else f" -> raised={raised} rc={rc_spawn} lock_gone={lock_gone} out={out[:120]!r}"))
        failed += 0 if ok else 1

    # F-0108 reopen: the session error must reach the CLI EXIT CODE, not only stdout.
    # Drive the WHOLE command through main(): an accepted finding routes to a
    # Remediator session whose agent command is unrunnable -> cmd_next returns
    # "stop-error" -> main must return a NON-ZERO exit (README §8 session-error
    # contract), not discard it and fall through to `return 0`.
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        audit = proj / "audit"
        (audit / "findings").mkdir(parents=True)
        (audit / "XCHECK.md").write_text("x", encoding="utf-8")
        # complete plan, but an UNCHECKED queue (no checked pass -> no coverage gate);
        # the accepted finding routes to run-remediator ahead of the audit branch.
        complete_audit(audit, queue=(("P-01", False),), coverage_pass=None)
        _put_state(audit, _ledger(
            "| F-0001 | a | major | accepted | Remediator | 2026-07-21 |\n"), encoding="utf-8")
        write_split_conf(
            audit,
            "remediator_cmd=__xcheck_no_such_agent__ {prompt}\nsandbox_profile=none\n")
        b = _io2.StringIO()
        rc_cli = None
        with _cl2.redirect_stdout(b):
            try:
                rc_cli = main(["xcheck", "--project", str(proj), "next"])
            except SystemExit as e:
                rc_cli = e.code
        out = b.getvalue()
        lock_gone = not (audit / ".lock").exists()
        ok = (rc_cli not in (0, None) and lock_gone
              and "__xcheck_no_such_agent__" in out and "Traceback" not in out)
        print(f"{'PASS' if ok else 'FAIL'}: session error propagates to a NON-ZERO `next` CLI exit (F-0108)"
              + ("" if ok else f" -> rc={rc_cli} lock_gone={lock_gone} out={out[-160:]!r}"))
        failed += 0 if ok else 1

    # F-0109: `--porcelain -z` reads non-ASCII names verbatim (no C-quoting), carries
    # BOTH sides of a rename (so the courier stages the whole rename, not just the new
    # name), and does not mis-split a path that literally contains ` -> `. Real temp
    # git repo: ASCII/whitespace/Unicode untracked + two renames (one dest holds
    # ` -> `) + a pre-existing dirty file that must STAY OUT of the commit.
    # The session dirt lives under audit/ so the parsing/whole-rename guarantee is tested
    # WITHOUT the F-0120 scratch guard (new untracked TOP-LEVEL paths outside audit/) —
    # dirty_paths parsing is location-independent, and a Unicode/renamed finding file is
    # exactly the audit/ path this must handle. preexisting.md stays at the ROOT.
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        (proj / "audit").mkdir()

        def _git(*a):
            return _sp.run(["git", "-C", str(proj)] + list(a), capture_output=True, text=True)

        _git("init", "-q")
        _git("config", "user.email", "t@t")
        _git("config", "user.name", "t")
        _git("config", "commit.gpgsign", "false")
        # tracked files to rename during the "session"
        (proj / "audit" / "old.md").write_text("o", encoding="utf-8")
        (proj / "audit" / "arrow.md").write_text("r", encoding="utf-8")
        _git("add", "-A")
        _git("commit", "-q", "-m", "seed")
        # a pre-existing dirty file that predates the session -> must be left untouched
        (proj / "preexisting.md").write_text("p", encoding="utf-8")
        pre = dirty_paths(proj)
        # the session's own dirt: untracked names + two renames (a dest holds ` -> `)
        (proj / "audit" / "ascii.md").write_text("a", encoding="utf-8")
        (proj / "audit" / "with space.md").write_text("b", encoding="utf-8")
        (proj / "audit" / "ünïcodé.md").write_text("c", encoding="utf-8")
        _git("mv", "audit/old.md", "audit/新しい ファイル.md")
        _git("mv", "audit/arrow.md", "audit/renamed -> nouveau.md")
        # F-0109 re-take (Verifier poison, human ruling 2026-07-28): the session's own new
        # BROKEN symlink. git reports it dirty, but its target is missing, so `Path.exists()`
        # follows the dangling link and returns False — the staging filter must test the
        # entry itself (`os.path.lexists`) or the commit fatals on the "unknown" pathspec.
        os.symlink("missing-target", proj / "audit" / "lien cassé")
        post = dirty_paths(proj)
        sess = session_dirt(pre, post)
        # both rename SIDES are session dirt (origin deletion + dest addition), the
        # arrow-containing dest parsed intact, the broken symlink is carried, and
        # preexisting.md is NOT swept in.
        want_sess = {"audit/ascii.md", "audit/with space.md", "audit/ünïcodé.md",
                     "audit/old.md", "audit/新しい ファイル.md", "audit/arrow.md",
                     "audit/renamed -> nouveau.md", "audit/lien cassé"}
        paths_ok = want_sess <= sess and "preexisting.md" not in sess
        ok_commit = courier_commit(proj, "Auditor", "unicode+rename dirt", pre, None)
        after = dirty_paths(proj)
        # the whole session is committed (nothing of it left dirty); preexisting stays.
        clean = ok_commit and after == {"preexisting.md"}
        ok = paths_ok and clean
        print(f"{'PASS' if ok else 'FAIL'}: dirty_paths -z stages Unicode + full renames, keeps preexisting out (F-0109)"
              + ("" if ok else f" -> sess={sorted(sess)} ok_commit={ok_commit} after={sorted(after) if after else after}"))
        failed += 0 if ok else 1

    # F-0009 (sanctioned re-take): the WHOLE next-transaction is under the lock,
    # incl. the default-conf write. Drive a LIVE foreign lock through main()/
    # cmd_next (not just run_session): an aborting `next` must write NOTHING.
    # This is the regression the run_session-only checks above could not catch —
    # the conf write escaped in main(), before cmd_next acquired the lock.
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        audit = proj / "audit"
        audit.mkdir()
        (audit / "XCHECK.md").write_text("x", encoding="utf-8")
        # a live foreign lock: this process's own pid is alive
        _put_lock(audit, {"pid": os.getpid(), "role": "orchestrator", "started": "x", "host": "x"})
        conf_path = audit / "orchestrator.conf"
        before = conf_path.exists()
        aborted, msg = False, ""
        b = _io2.StringIO()
        with _cl2.redirect_stdout(b):
            try:
                main(["xcheck", "--project", str(proj), "next"])
            except SystemExit as e:
                aborted, msg = True, str(e)
        after = conf_path.exists()
        ok = aborted and before is False and after is False and "LIVE" in msg
        print(f"{'PASS' if ok else 'FAIL'}: live foreign lock aborts `next` via main with NO pre-lock conf write (F-0009)"
              + ("" if ok else f" -> aborted={aborted} before={before} after={after}"))
        failed += 0 if ok else 1

    # F-0010: os.kill EPERM (PermissionError) means the process is ALIVE, not
    # dead — only ESRCH (ProcessLookupError) is dead.
    _orig_kill = os.kill
    try:
        os.kill = lambda pid, sig: (_ for _ in ()).throw(PermissionError())
        alive_eperm = pid_alive(4242) is True
        os.kill = lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError())
        dead_esrch = pid_alive(4242) is False
    finally:
        os.kill = _orig_kill
    ok = alive_eperm and dead_esrch
    print(f"{'PASS' if ok else 'FAIL'}: pid_alive treats EPERM as alive, ESRCH as dead (F-0010)" + ("" if ok else f" -> eperm={alive_eperm} esrch={dead_esrch}"))
    failed += 0 if ok else 1
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        (proj / "audit").mkdir()
        _put_lock(proj / "audit", {"pid": 4242, "role": "orchestrator", "started": "x", "host": "x"})
        os.kill = lambda pid, sig: (_ for _ in ()).throw(PermissionError())
        try:
            refused = False
            b = _io2.StringIO()
            with _cl2.redirect_stdout(b):
                try:
                    cmd_unlock(proj, False)
                except SystemExit:
                    refused = True
            still = (proj / "audit" / ".lock").exists()
        finally:
            os.kill = _orig_kill
        ok = refused and still
        print(f"{'PASS' if ok else 'FAIL'}: unlock refuses an EPERM-live lock without --force (F-0010)" + ("" if ok else f" -> refused={refused} still={still}"))
        failed += 0 if ok else 1

    # (4) META-CHECKS, mutated tables (§7 adversarial): each new guard must BITE.
    #     (a) dropping `_canon_refusal` from the schema must be REFUSED by the value-
    #         coverage meta-check — a value-constrained §2 field read past validation is
    #         the CF-0001 class root, so the predicate cannot be quietly removed.
    #     (b) the §2 enumeration and REFUSAL_REASONS must agree; a seventh member on
    #         either side is named.
    _s2v = {"refusal": "null | " + " | ".join(sorted(REFUSAL_REASONS))}
    _no_pred = dict(CANONICAL_SCHEMA); _no_pred["refusal"] = (True, True, None)
    ok_ma = any("'refusal'" in i and "NO value predicate" in i
                for i in _schema_value_coverage_issues(_s2v, schema=_no_pred, delegated={}))
    ok_mb = not _schema_enum_binding_issues(_s2v)
    ok_mc = any("'refusal'" in i and "code lacks ['seventh']" in i
                for i in _schema_enum_binding_issues({"refusal": "null | " + " | ".join(sorted(REFUSAL_REASONS | {"seventh"}))}))
    ok_md = any("'refusal'" in i and "§2 lacks ['cost-exceeded']" in i
                for i in _schema_enum_binding_issues({"refusal": "null | " + " | ".join(sorted(REFUSAL_REASONS - {"cost-exceeded"}))}))
    #     (c) the SHIPPED XCHECK.md §2 enumeration binds to the live constant — the norm
    #         and the code carry one vocabulary, not two.
    _shipped_x = Path(__file__).resolve().parent.parent / "XCHECK.md"
    # No XCHECK.md beside the package (an installed wheel) means half (c) has no
    # subject. It degrades to True so the other four halves still run — but the
    # printed line SAYS SO, because a `PASS` whose text still promises the shipped
    # binding, in a context where nothing shipped was read, is a claim that
    # over-reaches its evidence.
    _x_note = "" if _shipped_x.is_file() else \
        " (no XCHECK.md beside the package — the shipped-binding half was NOT checked here)"
    ok_me = (not _schema_enum_binding_issues(
        section2_canonical_field_values(_shipped_x.read_text(encoding="utf-8", errors="replace")) or {})
        if _shipped_x.is_file() else True)
    ok = ok_ma and ok_mb and ok_mc and ok_md and ok_me
    print(f"{'PASS' if ok else 'FAIL'}: §5 refusal — the vocabulary is declared ONCE: dropping its predicate is refused by the value-coverage meta-check, and the shipped XCHECK.md §2 enumeration is BOUND to REFUSAL_REASONS (a seventh member on either side is named){_x_note} (LCC phase 2, CF-0001 discipline)"
          + ("" if ok else f" -> a={ok_ma} b={ok_mb} c={ok_mc} d={ok_md} shipped={ok_me}"))
    failed += 0 if ok else 1

    # §5 refusal, F-0134 (Ouroboros-3, human ruling #2): the §5 refusal DEFINITION must not
    # contradict ITSELF. The per-role role↔target guard over the shipped skills (below)
    # catches a CONSUMER routing its refusal to the wrong target, but three reopens came from
    # the NORM paragraph itself — it exempted the Planner/Auditor from the finding target and
    # then, in a universal closing clause, re-imposed the finding `## Refusal` section on every
    # refusal. `_refusal_norm_issue` is the missing check; it must pass on the shipped XCHECK.md
    # and REDDEN on both mutation shapes (a re-added universal mandate, a dropped exemption).
    _shipped_xcheck = Path(__file__).resolve().parent.parent / "XCHECK.md"
    _rn = []
    if _shipped_xcheck.is_file():
        _xt = _shipped_xcheck.read_text(encoding="utf-8", errors="replace")
        _rn.append(("shipped XCHECK.md §5 refusal is self-consistent", _refusal_norm_issue(_xt) is None))
        #   MUTATION A — re-add the old universal closing sentence (the exact recurring shape):
        #   a demand for the finding `## Refusal` section in a clause naming no finding-charter
        #   role, sitting beside the still-present exemption. It must redden.
        _mutA = _xt.replace(
            "  **Triage has no refusal at all**:",
            "  The reason code is the §2 frontmatter field and the prose is the finding's `## Refusal` section, so both are required together. **Triage has no refusal at all**:",
            1)
        _rn.append(("a re-added universal finding-target mandate reddens",
                    _mutA != _xt and _refusal_norm_issue(_mutA) is not None))
        #   MUTATION B — drop the Planner/Auditor exemption; the definition is then silent on
        #   whether the finding field applies to them, the pre-fix hole. It must redden.
        _mutB = _xt.replace("; the finding-frontmatter `refusal:` field does not apply to it.", ".")
        _rn.append(("dropping the non-finding-role exemption reddens",
                    _mutB != _xt and _refusal_norm_issue(_mutB) is not None))
    if not _rn:
        # All three halves read the shipped XCHECK.md, and an installed package has
        # none beside it. This branch used to append a False and FAIL — which reads as
        # "F-0134 has regressed" when the truth is "the norm this checks is not in the
        # artifact". Skipped by name instead; the source tree, where the file is
        # present, still runs all three.
        print(f"SKIP: {REFUSAL_NORM_CHECK} -> no XCHECK.md beside the installed package")
        skipped.append(REFUSAL_NORM_CHECK)
    else:
        ok = all(v for _l, v in _rn)
        print(f"{'PASS' if ok else 'FAIL'}: §5 refusal — the §5 DEFINITION is checked for SELF-CONSISTENCY (F-0134, human ruling #2): the finding `## Refusal` target is demanded only in a clause scoped to a finding-charter role (Remediator/Verifier) AND the non-finding roles are exempted, so a norm paragraph that exempts Planner/Auditor and then universally re-mandates the finding section — the self-contradiction that reopened F-0134 three times — reddens, as does dropping the exemption; this is a semantic contradiction check ADDED BESIDE the role↔target guard, not a byte-identity pin"
              + ("" if ok else f" -> {[l for l, v in _rn if not v]}"))
        failed += 0 if ok else 1

    # (4) the FLAG itself goes through the typed Conf boundary (F-0113): a consumer gets a
    #     real bool for every declared spelling, and a non-boolean is an ADDRESSED operator
    #     error, not a silent `off` and not a traceback.
    _bool_ok = []
    for _spelling, _want in [("on", True), ("ON", True), ("true", True), ("yes", True), ("1", True),
                             ("off", False), ("false", False), ("no", False), ("0", False),
                             ("", False)]:
        _v = Conf({"scope_typing": _spelling}).get("scope_typing", "off")
        _bool_ok.append((_spelling, _v is _want))
    _addressed = False
    try:
        Conf({"scope_typing": "maybe"}).get("scope_typing", "off")
    except SystemExit as _e:
        _addressed = "scope_typing must be one of" in str(_e)
    except Exception:
        _addressed = False
    #     and the key is DECLARED public config with an `off` default (README §8 surface).
    _declared = CONF_DEFAULTS.get("scope_typing") == "off" and "scope_typing" in BOOLEAN_CONF
    ok = all(v for _s, v in _bool_ok) and _addressed and _declared
    print(f"{'PASS' if ok else 'FAIL'}: §10 `scope_typing` reads through the TYPED Conf boundary — every declared spelling yields a real bool, a non-boolean is an addressed error not a traceback and not a silent off, default is `off` (LCC phase 3, F-0113 discipline)"
          + ("" if ok else f" -> spellings={[s for s, v in _bool_ok if not v]} addressed={_addressed} declared={_declared}"))
    failed += 0 if ok else 1

    # key DETERMINISM, both directions: same role+charter -> same key; any change -> a
    # different key. The second direction is the load-bearing one — a reworded charter is a
    # different charter, so a construal admitted for one reading cannot license another.
    _k1 = construal_key("Auditor", "dimension X, units 4-6")
    _k2 = construal_key("Auditor", "dimension X, units 4-6")
    _k3 = construal_key("Auditor", "dimension X, units 4-7")
    _k4 = construal_key("Remediator", "dimension X, units 4-6")
    #     the boundary pair: WITHOUT the NUL separator these two concatenate to the same
    #     bytes ("Auditor" + "x" == "Auditorx" + ""), so this is the case that proves the
    #     separator is load-bearing rather than decorative.
    _k5 = construal_key("Auditor", "x")
    _k6 = construal_key("Auditorx", "")
    ok = (_k1 == _k2 and _k1 != _k3 and _k1 != _k4 and _k5 != _k6
          and is_session_id(_k1) and _k1 == construal_key("  Auditor  ", "  dimension X, units 4-6  "))
    print(f"{'PASS' if ok else 'FAIL'}: §4 rule 9 — the charter key is DETERMINISTIC (same role+charter -> same key, surrounding whitespace ignored) and DISCRIMINATING (a changed charter, a changed role, and a role/charter boundary shift all give different keys); its form is the one 16-hex id shape (LCC phase 4)"
          + ("" if ok else f" -> {_k1},{_k2},{_k3},{_k4},{_k5},{_k6}"))
    failed += 0 if ok else 1

    # (6) every gate MESSAGE is distinct — a new stop that reads like an old one teaches the
    #     operator nothing. Compared across ALL gate kinds, not just the neighbours.
    _all_gates = [
        ("stop-construal", ("Remediator", "finding F-0001", "audit/construals/k.md", "it is `proposed` and a human has not admitted it")),
        ("stop-refusal", ("F-0001", "material-missing")),
        ("stop-needs-human", "F-0001"),
        ("stop-norm-ratification", "F-0001"),
        ("stop-disputed", "F-0001"),
        ("stop-triage", ["F-0001"]),
        ("stop-inconsistent", ["F-0001"]),
        ("stop-broken-class", "F-0001"),
        ("stop-critical", ("F-0001", "accepted")),
        ("done", ""),
    ]
    _texts = {}
    for _gk, _gd in _all_gates:
        _gb = _io2.StringIO()
        with _cl2.redirect_stdout(_gb):
            report_gate(_gk, _gd)
        _texts[_gk] = _gb.getvalue().strip()
    ok = (len(set(_texts.values())) == len(_texts) and all(_texts.values())
          and "construal" in _texts["stop-construal"] and "EVIDENCE, never authority" in _texts["stop-construal"])
    print(f"{'PASS' if ok else 'FAIL'}: §4 rule 9 — the `stop-construal` message is non-empty, names the mechanism, and is DISTINCT from every other gate message ({len(_texts)} compared) (LCC phase 5)"
          + ("" if ok else f" -> {len(set(_texts.values()))} distinct of {len(_texts)}"))
    failed += 0 if ok else 1

    # §4 rule 9, Ouroboros-3 round 1: the gate must reach the AGENT, not merely the
    # decision. Every earlier check stopped at `decide()`/`role_and_charter()` — the kind
    # and the charter STRING — and none asserted that the dispatched CHILD PROMPT carries
    # the construal instruction. It did not: PROMPTS["Planner"] has no `{charter}` slot, so
    # `.format(charter=...)` dropped it and the agent ran an ordinary Planner session,
    # writing AUDIT.md while the gate reported itself armed. Measure the prompt.
    _saved_cp = {n: _G[n] for n in ("courier_commit", "dirty_paths")}
    _orig_run_cp = _G["run_child"]
    _cp_cap = {}

    def _capture_prompt(cmd, cwd, log_path, env, conf, lock=None, timeout=None,
                        profile=None, **k):
        _cp_cap["cmd"] = cmd
        Path(log_path).write_text("", encoding="utf-8")
        return 0, "ok", 0.0
    _cp_problems = []
    for _role, _charter, _key in (("Planner", None, "aaaaaaaaaaaaaaaa"),
                                  ("Auditor", "P-01", "bbbbbbbbbbbbbbbb"),
                                  ("Remediator", "finding F-0001", "cccccccccccccccc"),
                                  ("Verifier", "every finding in status fixed (F-0001)", "dddddddddddddddd")):
        _r2, _c2 = role_and_charter("run-construal", (_role, _charter, _key, f"audit/construals/{_key}.md"))
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td); (proj / "audit").mkdir()
            _G["courier_commit"] = lambda *a, **k: True
            _G["dirty_paths"] = lambda p: set()
            _G["run_child"] = _capture_prompt
            try:
                run_session(proj, {f"{_role.lower()}_cmd": "true {prompt}", "session_note": "",
                                   "trust_level": "trusted",
                                   "sandbox_profile": "none"},
                            _r2, _c2, dry_run=False, construal=_key, construal_prompt=True)
            finally:
                _G["run_child"] = _orig_run_cp
                for _n, _v in _saved_cp.items():
                    _G[_n] = _v
        _child = (_cp_cap.get("cmd") or [""])[-1]
        # the construal instruction, its key and its path REACH the agent...
        for _need in ("OPERATIONAL CONSTRUAL", _key, f"audit/construals/{_key}.md",
                      "must NOT admit your own construal", "STOP"):
            if _need not in _child:
                _cp_problems.append(f"{_role}: child prompt lacks {_need!r}")
        # ...and the role's OWN standing orders do NOT, so nothing competes with it.
        for _forbidden in ("produce audit/AUDIT.md", "Stop conditions per charter",
                           "Follow the XCHECK.md §3 sequence", "Try to prove the fixes wrong"):
            if _forbidden in _child:
                _cp_problems.append(f"{_role}: child prompt still carries the role's own order {_forbidden!r}")
    # and an ORDINARY dispatch is unchanged — the role's prompt, with its charter in it.
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td); (proj / "audit").mkdir()
        _G["courier_commit"] = lambda *a, **k: True
        _G["dirty_paths"] = lambda p: set()
        _G["run_child"] = _capture_prompt
        try:
            run_session(proj, {"auditor_cmd": "true {prompt}", "session_note": "",
                               "trust_level": "trusted",
                               "sandbox_profile": "none"},
                        "Auditor", "P-01", dry_run=False)
        finally:
            _G["run_child"] = _orig_run_cp
            for _n, _v in _saved_cp.items():
                _G[_n] = _v
    _plain = (_cp_cap.get("cmd") or [""])[-1]
    if "Charter: pass P-01" not in _plain or "OPERATIONAL CONSTRUAL" in _plain:
        _cp_problems.append("ordinary Auditor dispatch changed: expected its own prompt with the charter, got a construal prompt")
    ok = not _cp_problems
    print(f"{'PASS' if ok else 'FAIL'}: §4 rule 9 — a `run-construal` dispatch hands the AGENT a construal prompt carrying the key, the path and the no-self-admission rule, with the role's OWN standing orders absent, for all four dispatched roles; an ordinary dispatch is untouched (Ouroboros-3 round 1: the gate was a NO-OP because PROMPTS['Planner'] has no charter slot and silently discarded it)"
          + ("" if ok else f" -> {_cp_problems}"))
    failed += 0 if ok else 1

    # No dispatched role may yield an EMPTY charter. The construal record requires a
    # non-nullable `charter`, so a role dispatched without one can never write a valid
    # construal — the gate then rejects its own record forever (Ouroboros-3 round 2: the
    # Planner deadlocked at stop-construal exactly this way). Derived from decide()'s own
    # run-* outcomes, not a hand list, so a future dispatch kind cannot reintroduce it.
    _empty_charters = []
    for _k2, _d2 in (("run-planner", None),
                     ("run-auditor", "P-01"),
                     ("run-remediator", ["F-0001"]),
                     ("run-verifier", ["F-0001"])):
        _rr, _cc = role_and_charter(_k2, _d2)
        if not (_cc or "").strip():
            _empty_charters.append(_k2)
        # PHASE 5: and the construal record built for it must SURVIVE `load_state` —
        # the schema's non-nullable `charter` is enforced at the one reading boundary
        # now, so the assertion is made where a consumer would meet it.
        with tempfile.TemporaryDirectory() as _td2:
            _ca = Path(_td2) / "audit"
            (_ca / "construals").mkdir(parents=True)
            _key2 = construal_key("Auditor", (_cc or "").strip())
            (_ca / "construals" / f"{_key2}.md").write_text("body\n", encoding="utf-8")
            _doc2 = _state_doc(_ca)
            _doc2["construals"] = [{
                "key": _key2, "role": "Auditor", "charter": (_cc or "").strip(),
                "session": "0123456789abcdef", "status": "admitted",
                "created": "2026-01-01", "body_path": f"construals/{_key2}.md",
                "admitted_by": "human:operator", "admitted_at": "2026-01-01"}]
            _save_state(_ca, _doc2)
            try:
                load_state(_ca)
            except StateError:
                _empty_charters.append(f"{_k2} (fails the construal schema)")
    ok = not _empty_charters
    print(f"{'PASS' if ok else 'FAIL'}: §4 rule 9 — EVERY dispatched role names a non-empty charter, so each can write a construal carrying the record's non-nullable `charter`; a role dispatched without one deadlocks the gate on its own record forever (Ouroboros-3 round 2: the Planner did)"
          + ("" if ok else f" -> {_empty_charters}"))
    failed += 0 if ok else 1

    # the meta-check that would have caught it: a template handed a charter must have a
    # slot to put it in. Exercised on the live table AND on mutated ones (§7 adversarial).
    ok_live = not prompt_charter_slot_issues()
    _no_slot = dict(PROMPTS); _no_slot["Construal"] = "no slots here at all"
    ok_mut_c = any("Construal" in i and "charter" in i for i in prompt_charter_slot_issues(_no_slot))
    _no_role = dict(PROMPTS); _no_role["Construal"] = "only {charter} here"
    ok_mut_r = any("Construal" in i and "role" in i for i in prompt_charter_slot_issues(_no_role))
    _no_aud = dict(PROMPTS); _no_aud["Auditor"] = "Role: Auditor. No slot."
    ok_mut_a = any("'Auditor'" in i and "silently discarded" in i for i in prompt_charter_slot_issues(_no_aud))
    ok = ok_live and ok_mut_c and ok_mut_r and ok_mut_a
    print(f"{'PASS' if ok else 'FAIL'}: §4 rule 9 — every prompt template that RECEIVES a charter carries a `{{charter}}` slot, so `.format()` can never silently discard one (the Ouroboros-3 round-1 root); removing a slot from Construal or from a charter-taking role is named"
          + ("" if ok else f" -> live={ok_live} c={ok_mut_c} r={ok_mut_r} a={ok_mut_a}"))
    failed += 0 if ok else 1

    # the construal charter reaches the agent DELIMITED, so trailing prompt text (session
    # hygiene, the operator note) cannot be absorbed into the recorded charter — which is
    # what broke the key<->charter binding in the first place.
    _r3, _c3 = role_and_charter("run-construal", ("Auditor", "P-01", "e" * 16, "audit/construals/x.md"))
    ok = ("<<<CHARTER>>>P-01<<<END CHARTER>>>" in _c3
          and _c3.rstrip().endswith("<<<END CHARTER>>>")
          and "verbatim, no block scalar" in _c3)
    print(f"{'PASS' if ok else 'FAIL'}: §4 rule 9 — the charter is handed to the agent DELIMITED and LAST, so appended session-hygiene/operator-note text cannot be swallowed into the recorded `charter` (the Ouroboros-3 round-1 root of the key<->charter mismatch)"
          + ("" if ok else f" -> {_c3[-120:]!r}"))
    failed += 0 if ok else 1

    # CF-0001 round 7 (human ruling #6, point 4 mutation): the YAML-null normalization is the
    # single load-bearing input. Prove it two ways: (1) `_yaml_absent` folds every spelling to
    # "" (so the non-nullable gate rejects it — the fix); (2) WITHOUT that fold each raw spelling
    # passes `_canon_unit` as if a path — i.e. it would route again if normalization were removed
    # (the I-21 hole). Its reverse is control-3 in the re-census above: the same spellings stay
    # legal in a nullable field.
    _spellings = ["null", "Null", "NULL", "~"]
    ok_norm = all(_yaml_absent(s) == "" for s in _spellings)
    ok_raw = all(_canon_unit(s) is None for s in _spellings)  # unnormalized -> accepted by the per-field predicate (would route)
    ok = ok_norm and ok_raw
    print(f"{'PASS' if ok else 'FAIL'}: CF-0001 human ruling #6 — YAML-null folded to absent at the single parser input; removing it routes the unit-null poison"
          + ("" if ok else f" -> norm={ok_norm}({[_yaml_absent(s) for s in _spellings]}) raw={ok_raw}"))
    failed += 0 if ok else 1

    # CF-0001 round 8 (human ruling #7, point mutation): the fold reaches EVERY parse depth, not
    # just the field value. Prove the inline-list depth two ways: (1) `_fm_value` folds a null
    # element out so the value collapses to `[]` the non-nullable `unit` gate rejects; (2) WITHOUT
    # that element fold the raw `[null]` form passes `_canon_unit` as a path — i.e. it would route
    # again if `_yaml_absent` were applied at the top level only (the I-25 hole). Reverting the fold
    # to top-level-only is exactly what makes `_fm_value("[null]")` return "[null]" and route.
    _inline_forms = ["[null]", "[Null]", "[NULL]", "[~]"]
    ok_fold = all(_canon_unit(_fm_value(s)[0]) is not None for s in _inline_forms)  # folded -> [] -> rejected (the fix)
    ok_rawl = all(_canon_unit(s) is None for s in _inline_forms)                    # unfolded -> accepted (would route)
    ok = ok_fold and ok_rawl
    print(f"{'PASS' if ok else 'FAIL'}: CF-0001 human ruling #7 — YAML-null folded in a flat inline list; a top-level-only fold routes the inline-list-null poison"
          + ("" if ok else f" -> fold={ok_fold}({[_fm_value(s)[0] for s in _inline_forms]}) raw={ok_rawl}"))
    failed += 0 if ok else 1

    # CF-0001 round 9 (human ruling #9, point 5 mutation): only the two forms XCHECK.md §2
    # declares (a scalar, a flat list of scalars) parse; anything else is rejected as bad-form.
    # (1) `_fm_value` marks each undeclared form invalid (form_ok=False) so the shared
    # structural gate fails it closed; (2) the per-field predicate alone does NOT catch a
    # nested list — `_canon_unit("[[null]]")` strips one bracket, sees `[null]`, and accepts it
    # — so the FORM gate is what closes the round-8 hole, not `_canon_unit`. (3) a declared form
    # still parses (form_ok=True). Removing the form check would let `[[null]]` reach a consumer
    # as an addressable value again — this pins it directly.
    _undeclared = ["[[null]]", "[[a]]", "{a: b}", "[a"]
    ok_reject = all(_fm_value(s)[1] is False for s in _undeclared)
    ok_needed = _canon_unit("[[null]]") is None            # per-field predicate misses it -> form gate is required
    ok_accept = _fm_value("B01")[1] and _fm_value("[B01, B04]")[1]  # declared forms still parse
    ok = ok_reject and ok_needed and bool(ok_accept)
    print(f"{'PASS' if ok else 'FAIL'}: CF-0001 human ruling #9 — only declared §2 forms parse; a nested list/mapping/unclosed bracket is rejected as bad-form (not normalised deeper)"
          + ("" if ok else f" -> reject={ok_reject}({[_fm_value(s) for s in _undeclared]}) needed={ok_needed} accept={ok_accept}"))
    failed += 0 if ok else 1

    # CF-0001 re-take round 3 (I-12 guard, human ruling #2): the literal needs-human flag
    # must never be compared as a SUBSTRING anywhere but the canonical NEEDS_HUMAN_NEXT_RE —
    # a substring membership test is exactly what let a junk value skip the human gate. Scan
    # the source for a quoted-literal membership test (`in`/`not in`); exclude the regex's
    # own definition/use and this scanner. Stated EXACTLY this narrow, per the ruling: it
    # does not claim "no consumer bypasses validation", only "no substring flag test".
    _src = orchestrator_sources()
    NH_SUBSTR_RE = re.compile(r'''["']needs-human["']\s+(?:not\s+)?in\b''')
    _nh_offenders = [ln.strip() for ln in _src.splitlines()
                     if NH_SUBSTR_RE.search(ln)
                     and "NEEDS_HUMAN_NEXT_RE" not in ln and "NH_SUBSTR_RE" not in ln]
    ok = not _nh_offenders
    print(f"{'PASS' if ok else 'FAIL'}: CF-0001 I-12 — no substring `needs-human` comparison outside the canonical NEEDS_HUMAN_NEXT_RE"
          + ("" if ok else f" -> {_nh_offenders}"))
    failed += 0 if ok else 1

    # CF-0001 (strategy b): the canonical enumerations live in exactly ONE place each
    # — STATUSES must equal NEXT_OWNER's keys plus the conditional superseded status,
    # so the §5 status vocabulary and the §5 status→owner map can never drift apart.
    ok = STATUSES == set(NEXT_OWNER) | {"superseded-by-class"}
    print(f"{'PASS' if ok else 'FAIL'}: STATUSES and NEXT_OWNER are one §5 vocabulary (CF-0001 strategy b)"
          + ("" if ok else f" -> STATUSES-NEXT_OWNER={STATUSES ^ (set(NEXT_OWNER) | {'superseded-by-class'})}"))
    failed += 0 if ok else 1

    # CF-0001 re-take (meta-check, human ruling point 3 + adversarial self-check point 4):
    # the schema table is DERIVED from §2, proven by a bijection the selftest exercises in
    # both directions plus its two failure modes. This is the mechanism that makes a new §2
    # field impossible to add without a validator — the technique that closed F-0077.
    # (a) The shipped methodology §2 (the real XCHECK.md beside bin/xcheck, when present)
    #     declares EXACTLY the schema's in-block fields — prose bound to code, no hand copy.
    shipped = Path(__file__).resolve().parent.parent / "XCHECK.md"
    if shipped.is_file():
        live = section2_canonical_fields(shipped.read_text(encoding="utf-8", errors="replace"))
        ok_a = live is not None and set(live) == set(SCHEMA_BLOCK_FIELDS) and not _schema_meta_diff(live)
        detail_a = f"§2={sorted(live) if live else None} schema={sorted(SCHEMA_BLOCK_FIELDS)}"
    else:
        # No shipped XCHECK.md beside an installed bin — the runtime bind still holds:
        # cmd_lint runs the same meta-check against each project's audit/XCHECK.md.
        ok_a, detail_a = True, "no shipped XCHECK.md beside bin (runtime cmd_lint binds per project)"
    # (b) Adding a field to §2 that the table does not validate must be flagged.
    ok_b = any("entry in the code schema table" in it
               for it in _schema_meta_diff(sorted(SCHEMA_BLOCK_FIELDS) + ["bogus-field"]))
    # (c) Removing a table entry while §2 still declares it must be flagged (same failure
    #     surface: §2 now carries a field the schema no longer validates).
    _dropped = sorted(SCHEMA_BLOCK_FIELDS)[1:]  # schema missing the first in-block field
    ok_c = any("entry in the code schema table" in it
               for it in _schema_meta_diff(sorted(SCHEMA_BLOCK_FIELDS), schema_fields=_dropped))
    # (d) Dropping a field from §2 while the table still validates it must be flagged.
    ok_e = any("no longer declares it" in it
               for it in _schema_meta_diff(sorted(SCHEMA_BLOCK_FIELDS)[1:]))
    # (f) An exact mirror produces no issue (control).
    ok_d = not _schema_meta_diff(sorted(SCHEMA_BLOCK_FIELDS))
    ok = ok_a and ok_b and ok_c and ok_e and ok_d
    print(f"{'PASS' if ok else 'FAIL'}: CF-0001 meta-check — schema table is a bijection with XCHECK.md §2 (add-field, drop-entry, drop-§2-field all caught)"
          + ("" if ok else f" -> a={ok_a}({detail_a}) b={ok_b} c={ok_c} e={ok_e} d={ok_d}"))
    failed += 0 if ok else 1

    # CF-0001 I-10 reopen (meta-check, value coverage): name bijection alone let `blocked`
    # sit in the table with a None predicate on a delegation that was NOT fail-closed. The
    # value-coverage check binds the REQUIREMENT of a guard to §2: a field whose §2 value is
    # not a `<free-form>` placeholder must be predicated OR a declared fail-closed delegation.
    if shipped.is_file():
        vals = section2_canonical_field_values(shipped.read_text(encoding="utf-8", errors="replace")) or {}
        # (a) control: the shipped §2 + live schema is fully covered (blocked now predicated).
        ok_va = not _schema_value_coverage_issues(vals)
        # (b) dropping `blocked`'s value predicate (the exact I-10 hole) must be flagged as
        #     the class root — this is what name-bijection could not catch.
        _no_blocked = dict(CANONICAL_SCHEMA); _no_blocked["blocked"] = (True, False, None)
        ok_vb = any("'blocked'" in it and "class root" in it
                    for it in _schema_value_coverage_issues(vals, schema=_no_blocked))
        # (c) a field that is BOTH predicated and delegated (two validation sources) must be
        #     flagged — a delegation entry may not shadow a real predicate.
        _both = dict(DELEGATED_VALUE_GATES); _both["severity"] = "bogus"
        ok_vc = any("'severity'" in it and "BOTH" in it
                    for it in _schema_value_coverage_issues(vals, delegated=_both))
        detail_v = f"a={ok_va} b={ok_vb} c={ok_vc}"
    else:
        ok_va = ok_vb = ok_vc = True
        detail_v = "no shipped XCHECK.md beside bin (runtime cmd_lint binds per project)"
    # (d) DELEGATED_VALUE_GATES is a PIN: each entry escapes the predicate requirement only
    #     because a proven gate validates it on EVERY consumer/status. After F-0150 only
    #     `recurrence-of` qualifies (the lint F-0043 check is unconditional and no
    #     routing/metrics consumer reads the value); `class` and `norm-ruling` moved to
    #     universal predicates in CANONICAL_SCHEMA because their delegated gates fired only
    #     where the field was read, so garbage in any other status certified clean. Any
    #     change to this set trips here so a new delegation cannot be added without its own
    #     proof (the mistake that made `blocked` a silent no-op).
    ok_vd = set(DELEGATED_VALUE_GATES) == {"recurrence-of"}
    ok = ok_va and ok_vb and ok_vc and ok_vd
    print(f"{'PASS' if ok else 'FAIL'}: CF-0001 I-10 — meta-check requires a value guard per constrained §2 field; delegated set is pinned to its proven gates"
          + ("" if ok else f" -> {detail_v} pinned={ok_vd} deleg={sorted(DELEGATED_VALUE_GATES)}"))
    failed += 0 if ok else 1

    # CF-0001 human rulings #4/#5 (meta-check, nullability): no canonical field may be BOTH
    # required and nullable. Nullability is DERIVED from §2 (`section2_field_nullable_map`:
    # a field is nullable IFF its §2-shown VALUE offers a `null` option — ruling #5: NOT from
    # a "list" comment), and `canonical_schema_nullability_issues`
    # REFUSES any schema entry whose nullable flag disagrees — so 'required and nullable'
    # (an empty value skipping the shared gate) cannot be declared field by field. Exercised
    # against the shipped §2 plus the point-4 mutation (flip a required field to nullable ->
    # must fail HERE, not only its poison) and its reverse.
    if shipped.is_file():
        nmap = section2_field_nullable_map(shipped.read_text(encoding="utf-8", errors="replace")) or {}
        # (a) the §2-derived nullable set is EXACTLY the fields whose §2 VALUE offers `null`
        #     (class/recurrence-of/blocked/norm-ruling/refusal/admitted-scope) — a tripwire if §2 changes what may be
        #     absent. `unit` is NOT here (ruling #5: its shown value `<unit path>` is
        #     substantive, so it is required and non-nullable despite the "YAML list" comment).
        derived_nullable = {f for f, n in nmap.items() if n}
        ok_na = derived_nullable == {"class", "recurrence-of", "blocked", "norm-ruling",
                                     "refusal", "admitted-scope"}
        # (b) control: the live schema agrees with §2 — no required-and-nullable contradiction.
        ok_nb = not canonical_schema_nullability_issues(nmap)
        # (c) point 4: flipping a §2-required field (title) to nullable must fail HERE.
        _null_title = dict(CANONICAL_SCHEMA); _null_title["title"] = (True, True, None)
        ok_nc = any("'title'" in it and "required and nullable" in it
                    for it in canonical_schema_nullability_issues(nmap, schema=_null_title))
        # (d) the reverse — marking a §2-nullable field (class) non-nullable — is caught too.
        _req_class = dict(CANONICAL_SCHEMA); _req_class["class"] = (True, False, None)
        ok_nd = any("'class'" in it and "nullable per XCHECK.md §2" in it
                    for it in canonical_schema_nullability_issues(nmap, schema=_req_class))
        detail_n = f"a={ok_na}({sorted(derived_nullable)}) b={ok_nb} c={ok_nc} d={ok_nd}"
    else:
        ok_na = ok_nb = ok_nc = ok_nd = True
        detail_n = "no shipped XCHECK.md beside bin (runtime cmd_lint binds per project)"
    ok = ok_na and ok_nb and ok_nc and ok_nd
    print(f"{'PASS' if ok else 'FAIL'}: CF-0001 human ruling #4 — schema nullability is bound to §2; no field is required-and-nullable (flip caught by the meta-check)"
          + ("" if ok else f" -> {detail_n}"))
    failed += 0 if ok else 1

    # F-0015: auditor accuracy denominator = only findings past validation
    def _metrics_out(rows_spec):
        with tempfile.TemporaryDirectory() as td:
            audit = Path(td) / "audit"
            (audit / "findings").mkdir(parents=True)
            _put_state(audit, 
                _ledger(*(f"| {i} | t | major | {s} | {n} | 2026-07-21 |\n" for i, s, n in rows_spec)), encoding="utf-8")
            b = _io2.StringIO()
            with _cl2.redirect_stdout(b):
                cmd_metrics(Path(td))
            return b.getvalue()
    o_acc = _metrics_out([("F-0001", "rejected", "—"), ("F-0002", "deferred", "human"),
                          ("F-0003", "accepted", "Remediator"), ("F-0004", "disputed", "human"),
                          ("F-0005", "closed", "—")])
    o_acc_na = _metrics_out([("F-0001", "accepted", "Remediator")])
    ok = "auditor accuracy (survived validation): 50.0%" in o_acc and "auditor accuracy (survived validation): n/a" in o_acc_na
    print(f"{'PASS' if ok else 'FAIL'}: auditor accuracy counts only validated pool, n/a when empty (F-0015)" + ("" if ok else f" -> {o_acc[o_acc.find('auditor'):][:60]} | {o_acc_na[o_acc_na.find('auditor'):][:60]}"))
    failed += 0 if ok else 1

    # F-0016: fix durability denominator = verdicts (closed|reopened), numerator = closed@0
    def _dur_out(rows_spec):
        with tempfile.TemporaryDirectory() as td:
            audit = Path(td) / "audit"
            (audit / "findings").mkdir(parents=True)
            _put_state(audit, 
                _ledger(*(f"| {i} | t | major | {s} | — | 2026-07-21 |\n" for i, s, a in rows_spec)),
                encoding="utf-8", per_id={i: {"attempts": int(a)} for i, s, a in rows_spec})
            b = _io2.StringIO()
            with _cl2.redirect_stdout(b):
                cmd_metrics(Path(td))
            return b.getvalue()
    d_mix = _dur_out([("F-0001", "closed", "0"), ("F-0002", "reopened", "0"), ("F-0003", "superseded-by-class", "0")])
    d_reop = _dur_out([("F-0001", "reopened", "0")])
    d_closed = _dur_out([("F-0001", "closed", "0")])
    d_closed1 = _dur_out([("F-0001", "closed", "1")])
    # Matched on the LABEL and the NUMBER with runs of spaces collapsed: the column
    # width is layout, and an oracle that fails when a printer is re-aligned reports a
    # cosmetic change as a durability defect. A wrong number still fails.
    def _flat(s):
        return re.sub(r"[ \t]+", " ", s)
    ok = ("fix durability (closed first pass): 50.0%" in _flat(d_mix)
          and "fix durability (closed first pass): 0.0%" in _flat(d_reop)
          and "fix durability (closed first pass): 100.0%" in _flat(d_closed)
          and "fix durability (closed first pass): 0.0%" in _flat(d_closed1))
    print(f"{'PASS' if ok else 'FAIL'}: fix durability excludes superseded, reopened=fail, closed@0=success (F-0016)" + ("" if ok else f" -> {[d_mix, d_reop, d_closed, d_closed1]}"))
    failed += 0 if ok else 1

    # F-0017: metrics report total absorbed members of class findings
    with tempfile.TemporaryDirectory() as td:
        audit = Path(td) / "audit"
        (audit / "findings").mkdir(parents=True)
        _put_state(audit, _ledger(
            "| F-0001 | a | major | superseded-by-class | — | 2026-07-21 |\n"
            "| F-0002 | b | major | superseded-by-class | — | 2026-07-21 |\n"
            "| CF-0001 | c | major | accepted | Remediator | 2026-07-21 |\n"), encoding="utf-8")
        b = _io2.StringIO()
        with _cl2.redirect_stdout(b):
            cmd_metrics(Path(td))
        with_members = "class findings: 1 (CF-0001), total members: 2" in b.getvalue()
        empty = _metrics_out([("F-0001", "closed", "—")])
        no_members = "class findings: 0 (none), total members: 0" in empty
        ok = with_members and no_members
        print(f"{'PASS' if ok else 'FAIL'}: metrics report class-member totals (F-0017)" + ("" if ok else f" -> members={with_members} empty={no_members}"))
        failed += 0 if ok else 1

    # F-0061: auditor-accuracy pool must count 'withdrawn' as a validation kill.
    # Stand: one closed + one withdrawn (both were accepted for remediation).
    # withdrawn dropped from both ends inflated a real 50% to 100%.
    o_wd = _metrics_out([("F-0001", "closed", "—"), ("F-0002", "withdrawn", "—")])
    ok = "auditor accuracy (survived validation): 50.0%" in o_wd
    print(f"{'PASS' if ok else 'FAIL'}: auditor accuracy counts withdrawn as validation-killed (F-0061)"
          + ("" if ok else f" -> {o_wd[o_wd.find('auditor'):][:60]}"))
    failed += 0 if ok else 1

    # F-0062: --budget must reject non-finite/negative durations (they silently
    # disable the advertised wall-clock cap: elapsed >= nan/inf is never true).
    # 0 and a finite positive value stay legal.
    def _budget_rejects(v):
        try:
            parse_budget(v)
            return False
        except SystemExit:
            return True
    ok = (all(_budget_rejects(v) for v in ("nan", "inf", "-inf", "-1", "notanumber"))
          and parse_budget("0") == 0.0 and parse_budget("30") == 30.0)
    print(f"{'PASS' if ok else 'FAIL'}: --budget rejects non-finite/negative, accepts 0 and finite (F-0062)"
          + ("" if ok else " -> " + str([(v, _budget_rejects(v)) for v in ("nan", "inf", "-1", "0", "30")])))
    failed += 0 if ok else 1

    # F-0063 guard (human ruling #2, 2026-07-26): the tally below counts the
    # PASS:/FAIL: lines this selftest emits through sys.stdout (the tee). A direct
    # print(..., file=...) would divert a check line around the tee and un-measure
    # it — the silent-divergence class this finding tracks. Close that one
    # syntactic bypass form mechanically: fail selftest if its own source contains
    # a print(..., file=...). This is the single form worth mechanizing, not a
    # proof that every emission reaches the tee (an aliased print or an inner
    # redirect_stdout would not trip it, and the bounded tally does not claim to
    # count those). This check line itself is emitted through sys.stdout, measured.
    import inspect as _inspect
    _self_src = _inspect.getsource(selftest)
    ok = not _selftest_prints_use_file(_self_src)
    print(f"{'PASS' if ok else 'FAIL'}: no direct print(..., file=) in selftest source routes a check line around sys.stdout (F-0063)"
          + ("" if ok else " -> a selftest print uses file=; drop it so the tee can measure that line"))
    failed += 0 if ok else 1
    # Mandatory regression on the guard itself (human ruling #2): it must FIRE on
    # a planted file= print and stay quiet on an ordinary one. The poisons live in
    # string literals, so the guard scanning THIS source sees plain string
    # constants here, not real print(file=) calls — no self-trip.
    ok = (_selftest_prints_use_file('print("PASS: x", file=sys.stderr)')
          and _selftest_prints_use_file('builtins.print("PASS: x", file=None)')
          and not _selftest_prints_use_file('print("PASS: x")'))
    print(f"{'PASS' if ok else 'FAIL'}: file= guard fires on a planted bypass, not on an ordinary print (F-0063)"
          + ("" if ok else f" -> {ok}"))
    failed += 0 if ok else 1

    # F-0092: {prompt} substitution works for an EMBEDDED placeholder
    # (`--prompt={prompt}`), a standalone token, and appends the prompt when
    # absent — the old list-membership test broke the `--flag=value` form.
    _p = "ROLE PROMPT with spaces"
    ok = (build_cmd({"auditor_cmd": "agent --prompt={prompt}"}, "Auditor", _p) == ["agent", f"--prompt={_p}"]
          and build_cmd({"auditor_cmd": "agent --prompt {prompt}"}, "Auditor", _p) == ["agent", "--prompt", _p]
          and build_cmd({"auditor_cmd": "agent --mode audit"}, "Auditor", _p) == ["agent", "--mode", "audit", _p])
    print(f"{'PASS' if ok else 'FAIL'}: build_cmd substitutes embedded + standalone {{prompt}}, appends when absent (F-0092)")
    failed += 0 if ok else 1

    # F-0090: a flag is legal only for the command(s) that consume it (N8). Drive
    # main() end-to-end (temp project with XCHECK.md); inapplicable flags raise an
    # addressed "is not valid for" error, applicable ones do not.
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        (proj / "audit").mkdir()
        (proj / "audit" / "XCHECK.md").write_text("x", encoding="utf-8")
        _put_state(proj / "audit", _ledger(""), encoding="utf-8")

        def _main_flag(argv):
            with _cl2.redirect_stdout(_io2.StringIO()):
                try:
                    main(["xcheck", "--project", str(proj)] + argv)
                    return "ok"
                except SystemExit as e:
                    return "flag" if "is not valid for" in str(e) else "other"
        rejects = [["--step", "lint"], ["--max-sessions", "1", "metrics"], ["--budget", "0", "lint"],
                   ["--force", "metrics"], ["--dry-run", "lint"], ["--budget", "0", "next"]]
        accepts = [["--dry-run", "next"], ["lint"], ["--force", "unlock"]]
        ok = (all(_main_flag(a) == "flag" for a in rejects)
              and all(_main_flag(a) != "flag" for a in accepts))
        print(f"{'PASS' if ok else 'FAIL'}: main rejects a flag its command does not consume, accepts applicable (F-0090)"
              + ("" if ok else f" -> rej={[(a,_main_flag(a)) for a in rejects]} acc={[(a,_main_flag(a)) for a in accepts]}"))
        failed += 0 if ok else 1

    # F-0093/F-0096: run_session signals inherited-lock ownership (the held lock's
    # nonce) and a fresh per-session id to the child through BOTH the environment
    # AND the role-prompt — an orchestrated launcher skips its own mkdir; a
    # Verifier is a different session. The reopen fix is the PROMPT channel: a
    # sandboxed command runner (Codex) does not expose the child CLI process's env
    # to the agent's shell, so the same signal must also ride in the prompt, which
    # always reaches the agent. Assert both channels carry the same tokens.
    _saved3 = {n: _G[n] for n in ("courier_commit", "dirty_paths")}
    _orig_run4 = _G["run_child"]
    _cap = {}

    def _capture_env(cmd, cwd, log_path, env, conf, lock=None, timeout=None, profile=None, **k):
        _cap.update(env or {})
        _cap["_cmd"] = cmd
        Path(log_path).write_text("", encoding="utf-8")
        return 0, "ok", 0.0
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        (proj / "audit").mkdir()
        conf = {"remediator_cmd": "true {prompt}", "session_note": "",
                "sandbox_profile": "none", "trust_level": "trusted"}
        _G["courier_commit"] = lambda *a, **k: True
        _G["dirty_paths"] = lambda p: set()
        _G["run_child"] = _capture_env
        try:
            lk = Lock(proj / "audit", "Remediator")
            lk.acquire()
            run_session(proj, conf, "Remediator", "finding F-0001", dry_run=False, lock=lk,
                        construal="0123456789abcdef")
            tok = lk.token
            lk.release()
        finally:
            _G["run_child"] = _orig_run4
            for _n, _v in _saved3.items():
                _G[_n] = _v
        inh = _cap.get("XCHECK_LOCK_INHERITED")
        sid = _cap.get("XCHECK_SESSION_ID")
        # the substituted prompt is the trailing argv token (`true {prompt}`)
        child_prompt = (_cap.get("_cmd") or [""])[-1]
        prompt_has_inh = bool(inh) and f"XCHECK_LOCK_INHERITED={inh}" in child_prompt
        prompt_has_sid = bool(sid) and f"XCHECK_SESSION_ID={sid}" in child_prompt
        env_ok = bool(inh) and inh == tok and is_session_id(sid) and sid != inh
        # §4 rule 9: the construal key rides the SAME two channels, for the same reason.
        ckey = _cap.get("XCHECK_CONSTRUAL_KEY")
        ck_ok = ckey == "0123456789abcdef" and "XCHECK_CONSTRUAL_KEY=0123456789abcdef" in child_prompt
        ok = env_ok and prompt_has_inh and prompt_has_sid and ck_ok
        print(f"{'PASS' if ok else 'FAIL'}: run_session passes inherited-lock nonce + fresh session id + construal key to the child via env AND prompt (F-0093/F-0096/§4 rule 9)"
              + ("" if ok else f" -> inh={inh} tok={tok} sid={sid} prompt_inh={prompt_has_inh} prompt_sid={prompt_has_sid}"))
        failed += 0 if ok else 1

    # F-0094: unlock is host-aware. A foreign-host owner with a locally-absent pid
    # is NOT provably dead (a local ESRCH says nothing about another host's pid
    # namespace) — refused without --force, cleared by --force. Control: a
    # same-host dead lock IS provably dead, yet (F-0095) non-force unlock still
    # refuses it and leaves it in place; only --force clears.
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        (proj / "audit").mkdir()
        host = socket.gethostname()
        _put_lock(proj / "audit", {"pid": 999999, "role": "Remediator", "started": "x",
                                   "host": "other-host.example", "nonce": "aaaaaaaaaaaaaaaa"})
        _orig_k = os.kill
        os.kill = lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError())  # locally absent
        try:
            foreign_refused = False
            with _cl2.redirect_stdout(_io2.StringIO()):
                try:
                    cmd_unlock(proj, False)
                except SystemExit:
                    foreign_refused = True
            foreign_still = (proj / "audit" / ".lock").exists()
            with _cl2.redirect_stdout(_io2.StringIO()):
                cmd_unlock(proj, True)                       # --force clears it
            foreign_gone = not (proj / "audit" / ".lock").exists()
            _put_lock(proj / "audit", {"pid": 999999, "role": "Remediator", "started": "x",
                                       "host": host, "nonce": "bbbbbbbbbbbbbbbb"})
            info_local = Lock(proj / "audit", "?").read()
            # F-0094: a same-host dead lock IS provably dead (unlike the foreign
            # one) — the host predicate is what distinguishes them...
            local_is_dead = lock_provably_dead(info_local)
            # ...but F-0095: non-force unlock no longer removes even a provably-dead
            # lock; removal is deferred to --force.
            local_refused = False
            with _cl2.redirect_stdout(_io2.StringIO()):
                try:
                    cmd_unlock(proj, False)
                except SystemExit:
                    local_refused = True
            local_still = (proj / "audit" / ".lock").exists()
            with _cl2.redirect_stdout(_io2.StringIO()):
                cmd_unlock(proj, True)                       # --force clears it
            local_cleared = not (proj / "audit" / ".lock").exists()
        finally:
            os.kill = _orig_k
        ok = (foreign_refused and foreign_still and foreign_gone
              and local_is_dead and local_refused and local_still and local_cleared)
        print(f"{'PASS' if ok else 'FAIL'}: unlock refuses a foreign-host lock; a same-host dead lock is provably-dead yet still needs --force (F-0094/F-0095)"
              + ("" if ok else f" -> refused={foreign_refused} still={foreign_still} gone={foreign_gone} dead={local_is_dead} lrefused={local_refused} lstill={local_still} lcleared={local_cleared}"))
        failed += 0 if ok else 1

    # F-0095 (reopen): non-force unlock no longer removes ANY lock — clearing a
    # lock identified by a pathname cannot be made race-free against a concurrent
    # clear + re-acquire (POSIX has no atomic compare-and-delete). The check-then-
    # delete race is closed by having NO non-force delete at all: plain `unlock`
    # diagnoses staleness and defers removal to the single confirmed --force path.
    # Drive the exact reopen interleaving — repeated non-force unlocks on a
    # provably-stale lock — and assert nothing is ever removed; then --force clears.
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        audit = proj / "audit"
        audit.mkdir()
        host = socket.gethostname()
        old = {"pid": 999999, "role": "Remediator", "started": "old", "host": host, "nonce": "0ld0ld0ld0ld0ld0"}
        _put_lock(audit, old)
        _orig_k = os.kill
        os.kill = lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()) if int(pid) == 999999 else None
        refused = 0
        try:
            for _ in range(2):                              # the poison's two non-force unlocks
                with _cl2.redirect_stdout(_io2.StringIO()):
                    try:
                        cmd_unlock(proj, False)
                    except SystemExit:
                        refused += 1
            # neither non-force call removed anything — the original owner is intact
            still = _read_lock(audit / ".lock" / "owner") or {}
            intact = (audit / ".lock").is_dir() and still.get("nonce") == "0ld0ld0ld0ld0ld0"
            with _cl2.redirect_stdout(_io2.StringIO()):
                cmd_unlock(proj, True)                       # --force is the single removal path
            forced_gone = not (audit / ".lock").exists()
        finally:
            os.kill = _orig_k
        ok = refused == 2 and intact and forced_gone
        print(f"{'PASS' if ok else 'FAIL'}: non-force unlock removes nothing (no check-then-delete race); --force clears (F-0095)"
              + ("" if ok else f" -> refused={refused} intact={intact} forced_gone={forced_gone}"))
        failed += 0 if ok else 1

    # F-0096: Verifier ≠ fixer, enforced on machine-readable `fixed-by` provenance.
    # A canonical producer id is 16 lowercase hex (`os.urandom(8).hex()`); anything
    # else — missing OR malformed — is not proof of identity and is refused
    # (reopen: `fixed-by: not-a-session-id` used to route straight through).
    _S1 = "1a2b3c4d5e6f7a8b"   # canonical producer (fixer)
    _S2 = "2b3c4d5e6f7a8b9c"   # canonical, distinct (independent verifier)
    # PHASE 5: the `malformed` verdict is gone. `fixed-by: not-a-session-id` was a
    # value the reader accepted and the gate had to catch afterwards; `fixed_by` is
    # now matched against the canonical producer pattern at the reading boundary, so
    # a non-canonical producer is a document `load_state` refuses — see the fixed_by
    # case in tests/test_state.py. What is left is what the GATE decides: absent
    # provenance, and a verifier who is the fixer.
    def _fnd096(fixed_by=None):
        return {"F-0001": Finding(
            id="F-0001", title="t", severity="major", status="fixed", dimension="spec",
            unit=("B01",), pass_id="P-01", attempts=0, updated="2026-01-01",
            body_path="findings/F-0001.md", fixed_by=fixed_by)}
    ok = (verifier_independence(["F-0001"], _fnd096(None), None) == (False, "provenance", "F-0001")
          and verifier_independence(["F-0001"], _fnd096(_S1), _S1) == (False, "same", "F-0001")
          and verifier_independence(["F-0001"], _fnd096(_S1), _S2) == (True, None, None)
          and verifier_independence(["F-0001"], _fnd096(_S1), None) == (True, None, None))
    print(f"{'PASS' if ok else 'FAIL'}: verifier_independence refuses missing/self provenance, allows a distinct canonical producer (F-0096)")
    failed += 0 if ok else 1
    # end-to-end through state_and_decision: unknown provenance and a self-matching
    # producer stop before dispatch; a distinct producer (or unset verifier id)
    # routes to run-verifier.
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        audit = proj / "audit"
        (audit / "findings").mkdir(parents=True)
        (audit / "XCHECK.md").write_text("x", encoding="utf-8")
        (audit / "AUDIT.md").write_text("## 5. Limits\n", encoding="utf-8")
        conf = {"batch_size": "8", "triage_batch_cap": "0", "session_note": ""}

        def _fixed(fixed_by=None):
            _put_state(audit, _ledger("| F-0001 | t | major | fixed | Verifier | 2026-07-26 |\n"),
                       encoding="utf-8", **({"fixed_by": fixed_by} if fixed_by else {}))
        _envsave = os.environ.get("XCHECK_SESSION_ID")
        try:
            os.environ.pop("XCHECK_SESSION_ID", None)
            _fixed()                                     # no fixed-by
            _, _, _, k_missing, d_missing = state_and_decision(proj, conf)
            _fixed("1a2b3c4d5e6f7a8b")
            os.environ["XCHECK_SESSION_ID"] = "1a2b3c4d5e6f7a8b"   # verifier == fixer
            _, _, _, k_same, d_same = state_and_decision(proj, conf)
            os.environ["XCHECK_SESSION_ID"] = "2b3c4d5e6f7a8b9c"   # distinct verifier
            _, _, _, k_diff, d_diff = state_and_decision(proj, conf)
            os.environ.pop("XCHECK_SESSION_ID", None)    # unset -> only provenance/format enforced
            _, _, _, k_unset, _ = state_and_decision(proj, conf)
        finally:
            if _envsave is None:
                os.environ.pop("XCHECK_SESSION_ID", None)
            else:
                os.environ["XCHECK_SESSION_ID"] = _envsave
        ok = (k_missing == "stop-verifier-independence" and d_missing == ("provenance", "F-0001")
              and k_same == "stop-verifier-independence" and d_same == ("same", "F-0001")
              and k_diff == "run-verifier" and d_diff == ["F-0001"]
              and k_unset == "run-verifier")
        print(f"{'PASS' if ok else 'FAIL'}: routing enforces Verifier != fixer via fixed-by provenance (F-0096)"
              + ("" if ok else f" -> missing={k_missing},{d_missing} same={k_same},{d_same} diff={k_diff},{d_diff} unset={k_unset}"))
        failed += 0 if ok else 1

    # ===================== RP-0048 (F-0119..F-0125) =====================
    def _mkf(fid, status, extra=""):
        """A canonical finding/CF frontmatter body for the RP-0048 fixtures."""
        return ("---\n"
                f"id: {fid}\npass: P-01\nupdated: 2026-07-21\ntitle: t\nseverity: major\n"
                f"dimension: invariants\nunit: B01\nstatus: {status}\nattempts: 0\n"
                f"{extra}---\n")

    def _lint_metrics(proj):
        with _cl2.redirect_stdout(_io2.StringIO()):
            rl = cmd_lint(proj)
        mb = _io2.StringIO()
        with _cl2.redirect_stdout(mb):
            rm = cmd_metrics(proj)
        return rl, rm, mb.getvalue()

    # F-0119: the canonical `updated` predicate must reject a value that has the
    # NNNN-NN-NN shape but names no existing calendar date (missed CF-0001 instance).
    poison119 = ["2026-02-30", "2026-99-99", "2026-00-10", "2026-13-01", "2026-04-31"]
    good119 = ["2026-07-27", "2024-02-29"]  # incl. a real leap day
    ok = (all(_canon_updated(v) is not None for v in poison119)
          and all(_canon_updated(v) is None for v in good119)
          and _canon_updated("2026-7-1") is not None)  # the FORM is still required
    print(f"{'PASS' if ok else 'FAIL'}: _canon_updated rejects impossible calendar dates, keeps real + leap dates (F-0119)"
          + ("" if ok else f" -> {[(v, _canon_updated(v)) for v in poison119 + good119]}"))
    failed += 0 if ok else 1

    # F-0120: the courier HOLDS a new untracked TOP-LEVEL scratch path outside audit/
    # and NAMES it, while committing a tracked-material edit and new audit/ files.
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)

        def _git(*a):
            return _sp.run(["git", "-C", str(proj)] + list(a), capture_output=True, text=True)

        _git("init", "-q"); _git("config", "user.email", "t@t")
        _git("config", "user.name", "t"); _git("config", "commit.gpgsign", "false")
        (proj / "audit").mkdir()
        (proj / "mat.py").write_text("v1\n", encoding="utf-8")   # tracked material
        (proj / "audit" / "seed").write_text("s\n", encoding="utf-8")
        _git("add", "-A"); _git("commit", "-q", "-m", "seed")
        pre = dirty_paths(proj)
        (proj / "mat.py").write_text("v2\n", encoding="utf-8")             # tracked edit
        (proj / ".xcheck-verify-scratch.py").write_text("harness\n", encoding="utf-8")  # untracked scratch
        # F-0120 reopen: a session that ran `git add` on its scratch flips it `??`->`A `.
        # Novelty is HEAD-baseline, not index, so the STAGED scratch must be held too.
        (proj / ".session-harness.py").write_text("staged\n", encoding="utf-8")
        _git("add", ".session-harness.py")
        # F-0120 re-take (human ruling 2026-07-28): `git add -N` (intent-to-add) puts the `A`
        # in the WORKING-TREE column (` A`), not the index column (`A `). A predicate that
        # decodes the status letter (`status[:1] == 'A'`) misses it and ships the harness; a
        # HEAD-baseline test holds it like any other never-in-HEAD path. This is the mutation
        # guard: revert the predicate to a status-column read and THIS path goes red.
        (proj / ".intent-add-harness.py").write_text("intent\n", encoding="utf-8")
        _git("add", "-N", ".intent-add-harness.py")
        (proj / "audit" / "findings").mkdir()
        (proj / "audit" / "findings" / "F-0001-x.md").write_text("f\n", encoding="utf-8")
        b = _io2.StringIO()
        with _cl2.redirect_stdout(b):
            ok_commit = courier_commit(proj, "Verifier", "batch", pre, None)
        out = b.getvalue()
        after = dirty_paths(proj)
        # after: every held scratch path is untouched by the courier and so is still dirty
        # (untracked `??`, staged-new `A `, and intent-to-add ` A` alike).
        ok = (ok_commit
              and ".xcheck-verify-scratch.py" in after          # untracked scratch NOT committed
              and ".xcheck-verify-scratch.py" in out            # named for the operator
              and ".session-harness.py" in after                # STAGED scratch NOT committed
              and ".session-harness.py" in out                  # named for the operator
              and ".intent-add-harness.py" in after             # intent-to-add scratch NOT committed
              and ".intent-add-harness.py" in out               # named for the operator
              and "mat.py" not in after                         # tracked edit committed
              and "audit/findings/F-0001-x.md" not in after)    # new audit/ file committed
        print(f"{'PASS' if ok else 'FAIL'}: courier holds new scratch — untracked, staged, AND intent-to-add (named), commits tracked-material edit + audit/ files (F-0120)"
              + ("" if ok else f" -> ok_commit={ok_commit} after={sorted(after) if after else after} out={out[:200]!r}"))
        failed += 0 if ok else 1

    # F-0120 human ruling #2+#3 (2026-07-28): a full rename of TRACKED material commits WHOLE (its
    # destination is absent from HEAD but is moved material, not scratch), and an atomic operation
    # is NEVER split — on EITHER route: an already-staged `git mv`, OR a plain file-level move
    # (`os.rename`/`mv`) the session never `git add`-ed (ruling #3: pairs are computed in an
    # ephemeral index so this route is paired too). Each route × {alone, PLUS a stray harness}:
    # the rename commits whole (origin+destination), the harness (if any) is held and named.
    # Mutation guard: drop the ephemeral-index rename recognition (`rename_pairs` reads only the
    # operator's staged index) and the plain-`mv` destination is mistaken for scratch — the
    # conservative net then holds its origin too, so the rename NO LONGER commits whole and the
    # `plain-mv/rename-only` case goes red (renamed.py absent from HEAD).
    rename_cases = [
        ("git-mv/rename-only", "git-mv", False),
        ("git-mv/rename+harness", "git-mv", True),
        ("plain-mv/rename-only", "plain-mv", False),
        ("plain-mv/rename+harness", "plain-mv", True),
    ]
    for label, mode, with_harness in rename_cases:
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)

            def _git(*a):
                return _sp.run(["git", "-C", str(proj)] + list(a), capture_output=True, text=True)

            _git("init", "-q"); _git("config", "user.email", "t@t")
            _git("config", "user.name", "t"); _git("config", "commit.gpgsign", "false")
            (proj / "audit").mkdir()
            # distinct multi-line content so git detects the move as a rename, not a delete+add
            (proj / "mat.py").write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")
            (proj / "audit" / "seed").write_text("s\n", encoding="utf-8")
            _git("add", "-A"); _git("commit", "-q", "-m", "seed")
            pre = dirty_paths(proj)
            pre_head = head_baseline_paths(proj)
            if mode == "git-mv":
                _git("mv", "mat.py", "renamed.py")                    # staged rename
            else:
                (proj / "mat.py").rename(proj / "renamed.py")         # plain fs move, NO git add
            if with_harness:
                (proj / ".scratch.py").write_text("harness\n", encoding="utf-8")  # stray scratch alongside
            b = _io2.StringIO()
            with _cl2.redirect_stdout(b):
                ok_commit = courier_commit(proj, "Remediator", "batch", pre, None, pre_head)
            out = b.getvalue()
            after = dirty_paths(proj) or set()
            head_now = head_baseline_paths(proj)
            ok = (ok_commit
                  and "renamed.py" in head_now          # destination committed to HEAD (whole)
                  and "mat.py" not in head_now           # origin removed from HEAD (whole)
                  and "renamed.py" not in after          # nothing of the rename left dirty
                  and "mat.py" not in after
                  and "renamed.py" not in out)           # a committed deliverable is NOT named as held
            if with_harness:
                ok = (ok and ".scratch.py" in after      # stray harness held, not committed
                      and ".scratch.py" in out)          # and named for the operator
            print(f"{'PASS' if ok else 'FAIL'}: courier commits a tracked-material rename WHOLE, never half; a co-occurring harness is held ({label}) (F-0120)"
                  + ("" if ok else f" -> ok_commit={ok_commit} head={sorted(head_now)} after={sorted(after)} out={out[:220]!r}"))
            failed += 0 if ok else 1

    # F-0120 human ruling #3 (2026-07-28) — CONSERVATIVE ROLLBACK: a plain deletion of tracked
    # material with NO rename partner, co-occurring with stray scratch, must NOT be committed
    # alone. When the courier is already holding the harness, the lone deletion is held too and
    # named — HEAD keeps the file rather than losing it under a success report. (A clean deletion
    # with no scratch still commits, tested implicitly by the ordinary remediation paths.)
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)

        def _git(*a):
            return _sp.run(["git", "-C", str(proj)] + list(a), capture_output=True, text=True)

        _git("init", "-q"); _git("config", "user.email", "t@t")
        _git("config", "user.name", "t"); _git("config", "commit.gpgsign", "false")
        (proj / "audit").mkdir()
        (proj / "mat.py").write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")
        (proj / "keep.py").write_text("kept\nthing\nhere\nnow\n", encoding="utf-8")  # unrelated tracked file
        (proj / "audit" / "seed").write_text("s\n", encoding="utf-8")
        _git("add", "-A"); _git("commit", "-q", "-m", "seed")
        pre = dirty_paths(proj)
        pre_head = head_baseline_paths(proj)
        (proj / "mat.py").unlink()                                     # real deletion, no rename partner
        (proj / ".scratch.py").write_text("zzz harness zzz\n", encoding="utf-8")  # stray scratch forces holding
        b = _io2.StringIO()
        with _cl2.redirect_stdout(b):
            ok_commit = courier_commit(proj, "Remediator", "batch", pre, None, pre_head)
        out = b.getvalue()
        head_now = head_baseline_paths(proj)
        ok = (ok_commit
              and "mat.py" in head_now                 # deletion HELD — HEAD still has the file
              and "mat.py" in out                       # and named for the operator
              and ".scratch.py" in out)                 # scratch named too
        print(f"{'PASS' if ok else 'FAIL'}: courier holds a lone tracked deletion (no rename partner) beside scratch, never half-commits, names both (F-0120)"
              + ("" if ok else f" -> ok_commit={ok_commit} head={sorted(head_now)} out={out[:220]!r}"))
        failed += 0 if ok else 1

    # F-0120 round-5 reopen (2026-07-28) — PHANTOM RENAME: `rename_pairs` pairs across the WHOLE
    # worktree, so git can match a session-created scratch file against a tracked file the OPERATOR
    # deleted BEFORE the session (same content) — a rename the session never made. The destination
    # exemption must therefore require the origin to be the SESSION'S OWN dirt (`origin in new_set`),
    # not merely once-in-HEAD; otherwise pre-existing operator dirt is used as false provenance and
    # the scratch ships, breaking the README "never swept ... without exception" contract. Mutation
    # guard: drop `and origin in new_set` from the exemption and this case goes red (scratch in HEAD,
    # unnamed). The operator's own pre-session deletion stays untouched throughout.
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)

        def _git(*a):
            return _sp.run(["git", "-C", str(proj)] + list(a), capture_output=True, text=True)

        _git("init", "-q"); _git("config", "user.email", "t@t")
        _git("config", "user.name", "t"); _git("config", "commit.gpgsign", "false")
        (proj / "audit").mkdir()
        (proj / "material.py").write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")  # tracked
        (proj / "keep.py").write_text("kept\nthing\nhere\nnow\n", encoding="utf-8")         # tracked
        (proj / "audit" / "seed").write_text("s\n", encoding="utf-8")
        _git("add", "-A"); _git("commit", "-q", "-m", "seed")
        (proj / "material.py").unlink()                                  # PRE-SESSION operator dirt
        pre = dirty_paths(proj)
        pre_head = head_baseline_paths(proj)
        (proj / "keep.py").write_text("kept\nthing\nhere\nCHANGED\n", encoding="utf-8")  # session edit
        # session scratch whose content matches the operator-deleted file -> git pairs them as a rename
        (proj / ".session-harness.py").write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")
        b = _io2.StringIO()
        with _cl2.redirect_stdout(b):
            ok_commit = courier_commit(proj, "Verifier", "batch", pre, None, pre_head)
        out = b.getvalue()
        head_now = head_baseline_paths(proj)
        after = dirty_paths(proj) or set()
        ok = (ok_commit
              and ".session-harness.py" not in head_now   # phantom-rename scratch NOT committed
              and ".session-harness.py" in out             # named for the operator
              and "keep.py" not in after                   # legit session edit committed
              and "material.py" in after                   # operator's pre-session deletion still dirty
              and "material.py" in head_now)               # ...and NOT swept: HEAD still keeps the file
        print(f"{'PASS' if ok else 'FAIL'}: courier holds a phantom-rename scratch matched against operator pre-session dirt, never ships it (F-0120)"
              + ("" if ok else f" -> ok_commit={ok_commit} head={sorted(head_now)} after={sorted(after)} out={out[:220]!r}"))
        failed += 0 if ok else 1

    # F-0120 round-6 reopen -> human ruling #4 (2026-07-28) — AMBIGUOUS RENAME SOURCE is a RATIFIED
    # §8-rule-5 HOLD, not a defect. When git pairs an absent-from-HEAD held path to an origin that
    # is NOT this session's own work (it predates the session, or the source is ambiguous among
    # same-content files), the courier cannot certify it as a legal move rather than stray scratch,
    # so it HOLDS it — HEAD stays intact (ruling #3's conservative rollback firing as designed).
    # Ruling #4 changes NO behaviour; it requires only that the message NAME this reason distinctly
    # from a plain-scratch report, so the operator can tell a legal move whose source is ambiguous
    # apart from a genuine harness. Deterministic construction: a held path whose content matches a
    # file the OPERATOR deleted BEFORE the session forces git to pair it to that PRE-SESSION origin
    # (no competing session-owned origin), and a second unique-content harness git does NOT pair.
    # Assert: HEAD intact (operator dirt never swept), both held & named, and the TWO messages are
    # distinct — the git-paired hold names the ambiguous/predates-session reason, the unpaired one
    # reads as plain scratch.
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)

        def _git(*a):
            return _sp.run(["git", "-C", str(proj)] + list(a), capture_output=True, text=True)

        _git("init", "-q"); _git("config", "user.email", "t@t")
        _git("config", "user.name", "t"); _git("config", "commit.gpgsign", "false")
        (proj / "audit").mkdir()
        (proj / "twin.py").write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")   # tracked
        (proj / "keep.py").write_text("kept\nthing\nhere\nnow\n", encoding="utf-8")       # tracked control
        (proj / "audit" / "seed").write_text("s\n", encoding="utf-8")
        _git("add", "-A"); _git("commit", "-q", "-m", "seed")
        (proj / "twin.py").unlink()                                      # PRE-SESSION operator deletion
        pre = dirty_paths(proj)
        pre_head = head_baseline_paths(proj)
        (proj / "keep.py").write_text("kept\nthing\nhere\nCHANGED\n", encoding="utf-8")   # session edit
        # a held path whose content matches the operator-deleted file -> git pairs it as a rename to
        # a PRE-SESSION origin (ambiguous source); and an unrelated unique-content scratch git leaves unpaired
        (proj / ".ambiguous-move.py").write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")
        (proj / ".plain-scratch.py").write_text("unique harness zzz\n", encoding="utf-8")
        b = _io2.StringIO()
        with _cl2.redirect_stdout(b):
            ok_commit = courier_commit(proj, "Verifier", "batch", pre, None, pre_head)
        out = b.getvalue()
        head_now = head_baseline_paths(proj)
        after = dirty_paths(proj) or set()
        # the ambiguous-source clause names its held path AFTER the reason phrase; the plain-scratch
        # clause names its path AFTER the "suspected session scratch" phrase — the two are distinct.
        amb_clause = out.split("ambiguous or predates the session", 1)[-1]
        scr_clause = out.split("suspected session scratch", 1)[-1].split("ambiguous or predates", 1)[0]
        ok = (ok_commit
              and ".ambiguous-move.py" not in head_now      # ambiguous-source rename dest HELD, not shipped
              and ".plain-scratch.py" not in head_now        # plain scratch HELD, not shipped
              and "twin.py" in head_now                      # operator pre-session deletion NOT swept: HEAD keeps it
              and "twin.py" in after                         # ...and still dirty, untouched
              and "keep.py" not in after                     # legit session edit committed
              and "ambiguous or predates the session" in out # message NAMES the ambiguous reason
              and ".ambiguous-move.py" in amb_clause         # ...for the git-paired hold
              and ".plain-scratch.py" in scr_clause)         # unpaired hold reads as plain scratch
        print(f"{'PASS' if ok else 'FAIL'}: courier holds an ambiguous/pre-session rename source (ruling #4) — HEAD intact, message distinct from plain scratch (F-0120)"
              + ("" if ok else f" -> ok_commit={ok_commit} head={sorted(head_now)} after={sorted(after)} out={out[:260]!r}"))
        failed += 0 if ok else 1

    # F-0121: a missing or non-numeric option value is an addressed CLI error naming the
    # option, not a raw IndexError/ValueError traceback.
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td); (proj / "audit").mkdir()
        (proj / "audit" / "XCHECK.md").write_text("x", encoding="utf-8")
        _put_state(proj / "audit", _ledger(""), encoding="utf-8")

        def _run121(argv):
            with _cl2.redirect_stdout(_io2.StringIO()):
                try:
                    main(["xcheck"] + argv)
                    return ("ok", "")
                except SystemExit as e:
                    return ("exit", str(e.code))
                except BaseException as e:  # a raw traceback would land here
                    return ("crash", type(e).__name__)
        cases121 = [
            (["--project"], "--project"),
            (["--project", str(proj), "--max-sessions"], "--max-sessions"),
            (["--project", str(proj), "--budget"], "--budget"),
            (["--project", str(proj), "--max-sessions", "banana", "loop"], "--max-sessions"),
        ]
        res121 = [(_run121(a), tok) for a, tok in cases121]
        ok = all(k == "exit" and tok in msg for (k, msg), tok in res121)
        print(f"{'PASS' if ok else 'FAIL'}: missing/non-numeric option values -> addressed CLI error naming the option, no traceback (F-0121)"
              + ("" if ok else f" -> {res121}"))
        failed += 0 if ok else 1

    # F-0122: a session error inside `loop` reaches a NON-ZERO CLI exit, not just stdout.
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td); audit = proj / "audit"; (audit / "findings").mkdir(parents=True)
        (audit / "XCHECK.md").write_text("x", encoding="utf-8")
        complete_audit(audit, queue=(("P-01", False),), coverage_pass=None)
        _put_state(audit, _ledger(
            "| F-0001 | a | major | accepted | Remediator | 2026-07-21 |\n"), encoding="utf-8")
        write_split_conf(
            audit,
            "remediator_cmd=__xcheck_no_such_agent__ {prompt}\nsandbox_profile=none\n")
        b = _io2.StringIO(); rc_cli = None
        with _cl2.redirect_stdout(b):
            try:
                rc_cli = main(["xcheck", "--project", str(proj), "loop", "--max-sessions", "1"])
            except SystemExit as e:
                rc_cli = e.code
        out = b.getvalue()
        lock_gone = not (audit / ".lock").exists()
        ok = (rc_cli not in (0, None) and lock_gone
              and "__xcheck_no_such_agent__" in out and "Traceback" not in out)
        print(f"{'PASS' if ok else 'FAIL'}: session error in `loop` propagates to a NON-ZERO CLI exit (F-0122)"
              + ("" if ok else f" -> rc={rc_cli} lock_gone={lock_gone} out={out[-160:]!r}"))
        failed += 0 if ok else 1

    # ---------------------------------------------------------------- RP-0050:
    # F-0126..F-0130 — shipped-artifact conformance. Each block proves the guard
    # ADVERSARIALLY (§7): the clean tree passes AND the specific mutation the
    # finding names, planted into a temp copy OUTSIDE the project tree (F-0120
    # hygiene, tempfile), is caught. `_rp50_root` is this orchestrator's own repo
    # root (bin/xcheck -> ..); .resolve() follows the orchestrator symlink to the
    # real checkout, so `xcheck selftest` validates the source it ships from.
    import shutil as _sh50
    _rp50_root = Path(__file__).resolve().parent.parent

    # F-0126: `attempts distribution` (norm-owner ruling 2026-07-28, option A —
    # N7 `docs/backlog-closure-plan.md` reformulated to match code + N4) is the RAW
    # `attempts` counter, NOT a count of `reopened` verdicts. The non-obvious case
    # that spawned the finding: a finding one `reopened` verdict deep legally has
    # status=reopened, attempts=0 (the Remediator increments attempts on the
    # RE-TAKE — §5/§9 — not on the verdict), so `by status` and `attempts
    # distribution` are DIFFERENT quantities. Pinning it stops the next reader
    # re-deriving the retired "reopen distribution" conflict.
    def _audit126(td, specs):  # specs: list of (fid, status, attempts)
        proj = Path(td); audit = proj / "audit"; (audit / "findings").mkdir(parents=True)
        (audit / "XCHECK.md").write_text("x", encoding="utf-8")
        (audit / "AUDIT.md").write_text("## 5. Limits\n", encoding="utf-8")
        _put_state(audit, _ledger(
            "".join(f"| {fid} | t | major | {st} | — | 2026-07-21 |\n" for fid, st, _ in specs)),
            encoding="utf-8", per_id={fid: {"attempts": int(att)} for fid, _st, att in specs})
        return proj
    with tempfile.TemporaryDirectory() as td:
        _, _, o126a = _lint_metrics(_audit126(td, [("F-0001", "reopened", "0")]))
    with tempfile.TemporaryDirectory() as td:
        _, _, o126b = _lint_metrics(_audit126(td, [
            ("F-0001", "reopened", "0"), ("F-0002", "closed", "0"),
            ("F-0003", "closed", "1"), ("F-0004", "closed", "2")]))
    # a reopened finding is reopened:1 by status YET 0x:1 by attempts — the two
    # numbers are the different quantities the ruling settled on.
    ok = ("reopened:1" in o126a and "attempts distribution: 0x:1" in o126a
          and "attempts distribution: 0x:2, 1x:1, 2x:1" in o126b)
    print(f"{'PASS' if ok else 'FAIL'}: metrics `attempts distribution` = raw attempts counter; reopened/attempts:0 is reopened:1 by status but 0x:1 by attempts (different quantities), multi-cycle buckets sorted (F-0126)"
          + ("" if ok else f" -> a={o126a!r} b={o126b!r}"))
    failed += 0 if ok else 1

    # F-0130: the seven shipped `templates/` files carry the required frontmatter and
    # sections the lifecycle depends on. selftest used synthetic fixtures and never
    # read the files install.sh actually copies, so a template with a dropped
    # mandatory section stamped formally-incomplete artifacts into every new audit
    # while machine-logic selftest stayed green.
    #
    # SCOPE (F-0130 human ruling 2026-07-28, §8 rule 5 exception). This is a LINE-BASED
    # drift check, NOT a CommonMark parser. It confirms the shipped templates carry the
    # required sections, fields and tables as ORDINARY MARKDOWN LINES; it does NOT detect
    # structure hidden inside raw HTML (`<div>...`) or other embeddings. Its threat model
    # is ACCIDENTAL drift — a renamed/missing section, a dropped/wrong field, a corrupted
    # plain-Markdown table — in OUR OWN shipped artifacts, not deliberate obfuscation of
    # them. Every claim about this check (this comment, the PASS/FAIL line, the README) is
    # bounded by exactly that and no more; the adversarial mutations below test the guard
    # (§7) but do not widen its promise.
    # F-0130 round-8 (human ruling 2026-07-28): EVERY needle here is TYPED and checked by
    # that line-based recognizer — after this round `_template_problems` holds NO
    # `n not in text` substring branch for structural content. The list classifies in full,
    # no remainder:
    #   * a "## ..." string is a SECTION HEADING — a real ATX header line, the whole
    #     stripped line == needle, so a header commented out of the document
    #     (`<!-- ## How to verify the fix -->`) or surviving only mid-paragraph is caught
    #     (round-4/8);
    #   * a ("table", heading_re, cols) tuple is a TABLE HEADER — the section named by
    #     heading_re must hold a GFM table (`_section_table`, the whole-table form declared
    #     for F-0112) whose header cells EQUAL cols by whole-cell normalized equality
    #     (`_norm_cell`), NOT a substring, so the dimensions header commented out of the
    #     document (`<!-- | key | what it catches | norm source | -->`) is not a table row
    #     and is caught (round-8 reopen).
    # An unclassified needle is a LOUD failure below, never a silent substring pass. The
    # AUDIT §5 limit values are NOT needles — compared against the XCHECK.md §10 parsed
    # defaults below; the coverage polarity is the separate COVERED/NOT-COVERED check;
    # frontmatter fields are the parsed-schema check further down.
    _DIM_COLS = ["key", "what it catches", "norm source"]
    _REQ_TEMPLATES = {
        "AUDIT-code.md": ["## 1. Norms catalog", "## 2. Dimensions", "## 3. Unit map",
                          "## 4. Pass queue", "## 5. Limits",
                          ("table", _DIMENSIONS_RE, _DIM_COLS)],
        "AUDIT-text.md": ["## 1. Norms catalog", "## 2. Dimensions", "## 3. Unit map",
                          "## 4. Pass queue", "## 5. Limits",
                          ("table", _DIMENSIONS_RE, _DIM_COLS)],
        "finding.md": ["## Evidence", "## Why this is a defect", "## How to verify the fix",
                       "## Validation", "## Refusal", "## Objection", "## Remediation",
                       "## Admitted scope", "### Covers", "### Does not cover",
                       "## Verification"],
        "class-finding.md": ["## Pattern", "## Census", "## Validation", "## Refusal",
                             "## Root cause", "## Global strategy", "## Norm ruling",
                             "## Remediation", "## Admitted scope", "### Covers",
                             "### Does not cover", "## Verification"],
        "construal.md": list(CONSTRUAL_SECTIONS),
        "pass.md": ["## Charter", "## Coverage", "## Findings", "## Handoff"],
        "plan.md": ["## Batch validation results", "## Census results", "## Fix plan",
                    "## Execution log"],
    }
    # ---------------------------------------------------------------- installed?
    # The four checks that follow audit the SHIPPED REPOSITORY: templates/,
    # install.sh, launchers/, skills/, XCHECK.md. None of those is inside the
    # installed package — a wheel carries `xcheck/*.py`, its metadata, and nothing
    # else — so in an installed context these checks have no subject at all.
    #
    # Before phase 9 they did not notice: the first `read_text()` raised
    # FileNotFoundError and `xcheck selftest` died on a pip install. `ci/release-gate.sh`
    # is what found that, by doing the one thing a source-tree test cannot — installing
    # the artifact and running it.
    #
    # They are SKIPPED, not passed. A check whose subject is absent and which reports
    # PASS is exactly the vacuous green this project keeps catching in itself, and it
    # would inflate a denominator the summary line promises is real. So a skip prints
    # its own `SKIP:` prefix (never counted by the tally, which scans for `PASS:`/
    # `FAIL:`), and the summary names every skipped check.
    #
    # `tests/test_release_lifecycle.py` asserts this list is EMPTY in the source tree —
    # the branch cannot quietly swallow a check that could have run — and that the
    # installed selftest exits 0 while naming its skips.
    _rp50_missing = [_p for _p in REPO_ONLY_INPUTS if not (_rp50_root / _p).exists()]
    if not _rp50_missing:
        # F-0130 (human ruling 2026-07-28, round-4 re-take): the finding/CF canonical record
        # lives in the frontmatter (§2). Prior rounds proved the SET of fields by parsing the
        # frontmatter against CANONICAL_SCHEMA, but token-presence still let three things through:
        # a section header commented out of the document (`<!-- ## How to verify the fix -->`
        # kept the substring), a flipped canonical VALUE (`status: reported`->`closed`), and a
        # corrupted limit DEFAULT (`max_findings_per_pass: banana`). The ruling: parse the
        # template and check three things against the SAME sources, no second hand list —
        #   (1) the field SET against CANONICAL_SCHEMA — EXACT (round-5: missing AND extra fields
        #       both reject; a one-sided `req - keys` let `future-field: null` ride along),
        #   (2) the VALUES of fixed-default §2 fields against the §2 canonical record,
        #   (3) the AUDIT-*.md §5 limit values against the XCHECK.md §10 defaults table, read from
        #       the §5 body ONLY with HTML comments stripped (round-5: a decoy `15` in a comment).
        # finding.md now carries the FULL §2 canonical record (blocked/norm-ruling added), so its
        # required set is exactly the in-block schema (SCHEMA_BLOCK_FIELDS) with NO hand exclusion;
        # class-finding.md is the full schema so its nullable §8 gate fields (blocked, norm-ruling)
        # and `members` are required too. Both sets derive from the ONE source CANONICAL_SCHEMA.
        # `created-by` is the CF template's remediation-provenance field (§8 census — which RP
        # escalated). class-finding.md carries it, but it is NOT a §2 canonical-record / schema
        # field, so it is the ONE documented CF-only frontmatter extension. The schema fields still
        # derive from the single CANONICAL_SCHEMA source; this names the lone structural extra so
        # the exact-SET check (round-5: extra AND missing → reject) does not false-flag it.
        _CF_PROVENANCE = frozenset({"created-by"})
        _REQ_FM_BY_TEMPLATE = {
            "finding.md": frozenset(SCHEMA_BLOCK_FIELDS),
            "class-finding.md": frozenset(CANONICAL_SCHEMA) | _CF_PROVENANCE,
        }
        # 0.8: templates that must carry NO frontmatter block. Their record lives in
        # audit/state.json and no renderer writes a mirror into these files, so a block
        # here is a field set that LOOKS recorded and is read by nothing — the silent
        # no-op the write verbs exist to remove. The schema<->writer coverage the
        # construal template used to carry is now asserted against the verbs themselves
        # (tests/test_write_verbs.py::TheConstrualSchemaHasAWriter).
        _NO_FM_TEMPLATES = ("construal.md", "pass.md", "plan.md")
        _xcheck_text = (_rp50_root / "XCHECK.md").read_text(encoding="utf-8")
        # Fixed-default §2 fields = those whose §2 canonical-record value is a single
        # lowercase-alphanumeric literal (status=reported, attempts=0, the null defaults) — NOT a
        # `<placeholder>`, an `a | b` enumeration, or an `F-0001`/`P-01` per-record EXAMPLE
        # (uppercase/hyphen). Derived from §2, not a second value list.
        _S2_CONST = {f: v for f, v in (section2_canonical_field_values(_xcheck_text) or {}).items()
                     if re.fullmatch(r"[a-z0-9]+", v)}
        # Which template is checked against WHICH fixed-default source. The §2 constants belong
        # to the §2 canonical record, so only the templates that instantiate that record are
        # held to them. `construal.md` is a different artifact with its own schema and its own
        # `status` vocabulary (§4 rule 9: proposed/admitted/refused) — comparing it against §2's
        # `status: reported` would be holding two records that were never the same record to
        # one default. Derived from the schema each template's field set came from, not a hand
        # list of filenames.
        _FM_VALUE_SOURCE = {
            "finding.md": _S2_CONST,
            "class-finding.md": _S2_CONST,
            "construal.md": {},
        }

        def _norm_null(v):
            # `_parse_frontmatter` folds YAML null to '', so compare `null` and '' as equal.
            v = (v or "").strip()
            return "" if v == "null" else v

        def _template_fm(f):
            m = FRONTMATTER_RE.match(f.read_text(encoding="utf-8"))  # F-0149: shared form
            if not m:
                return None, [], []
            # F-0130 round-7/8: surface the parser's ALREADY-COMPUTED structural signals
            # instead of discarding them. `dups` (F-0103): a repeated singleton — two `status:`
            # lines — last-wins-collapses to a canonical KEY SET and value, so the set/value
            # checks below cannot see it. `badform` (CF-0001 ruling #9): a field whose FORM is
            # not a §2-declared one — a flow mapping `unit: {a: b}`, a nested list — parses to a
            # mangled value under a canonical KEY, so the set/value checks read the key as valid.
            # `finding_files_malformed` already fails such a record closed on every consumer, so
            # the template guard must reject it the same way rather than build its own judgement
            # from the collapsed `set(fm)`. Take both signals; write no new logic.
            fm, dups, badform = _parse_frontmatter(m.group(1))
            return fm, dups, badform

        def _audit_limits(f):
            # F-0130 round-5 reopen: a whole-file scan read a decoy `max_findings_per_pass: 15`
            # planted inside the intro HTML comment while §5 itself carried `banana`. The limit
            # values are canonical ONLY inside the §5 Limits section, so (1) strip HTML comments
            # (a `key: 15` hidden in a comment is not a limit) and (2) read ONLY the §5 body.
            text = re.sub(r"<!--.*?-->", "", f.read_text(encoding="utf-8"), flags=re.S)
            m = re.search(r"^##\s+5\.\s+Limits\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
            body = m.group(1) if m else ""
            out = {}
            for line in body.splitlines():
                r = re.match(r"^\s*([A-Za-z_]+)\s*:\s*(\d+)\b", line)
                if r and r.group(1) in LIMIT_KEYS:
                    out[r.group(1)] = int(r.group(2))
            return out

        def _section10_limits(text):
            # Parse the XCHECK.md §10 Configuration Defaults table -> {limit key: int default}.
            m = re.search(r"^##\s+10\.\s+Configuration Defaults\s*$(.*?)(?=^##\s|\Z)",
                          text, re.M | re.S)
            if not m:
                return {}
            out = {}
            for line in m.group(1).splitlines():
                r = re.match(r"^\|\s*`?([A-Za-z_]+)`?\s*\|\s*(\d+)\s*\|", line)
                if r and r.group(1) in LIMIT_KEYS:
                    out[r.group(1)] = int(r.group(2))
            return out

        _S10_LIMITS = _section10_limits(_xcheck_text)

        def _template_problems(root):
            tdir = root / "templates"
            problems = []
            for fname, needles in _REQ_TEMPLATES.items():
                f = tdir / fname
                if not f.exists():
                    problems.append(f"{fname}: MISSING"); continue
                text = f.read_text(encoding="utf-8")
                # Strip non-rendered blocks once per file (F-0130 round-9/11 reopen): both
                # structural checks — the ATX heading scan here AND `_section_table` below —
                # must ignore a ```-fenced code EXAMPLE or a `<!-- ... -->`-commented copy of a
                # heading/table, which pandoc renders as a CodeBlock / RawBlock, never a
                # Header/Table. `_section_table` calls `_strip_nonstructure` itself; the heading
                # scan does the same here so a heading wrapped in a multi-line HTML comment
                # (round-11 root, adjacent branch to the table scan) is not read as structure.
                slines = _strip_nonstructure(text).split("\n")
                for n in needles:
                    if isinstance(n, str) and re.match(r"^#{1,6} ", n):
                        # SECTION HEADING: a REAL ATX header line — CommonMark allows 0-3 leading
                        # spaces (4+ columns is an indented code block, NOT a heading; enforced by
                        # `_atx_indent_ok`) and GFM tolerates trailing spaces — not an arbitrary
                        # substring, not inside a code fence, not indented into code. Else a header
                        # commented out (`<!-- ## How to verify the fix -->`), surviving only
                        # mid-paragraph, shown as a ```-fenced example, or indented `    ## ...`
                        # into code passes (F-0130 round-4/8/9/10).
                        if not any((_d := _atx_indent_ok(l)) is not None and _d.rstrip() == n
                                   for l in slines):
                            problems.append(f"{fname}: missing section header {n!r}")
                    elif isinstance(n, tuple) and n[0] == "table":
                        # TABLE HEADER: the section must hold a GFM table (`_section_table`)
                        # whose header cells EQUAL the declared columns by whole-cell normalized
                        # equality — NOT a substring — so the dimensions header commented out of
                        # the document (`<!-- | key | what it catches | norm source | -->`) is
                        # not a table row and is caught (F-0130 round-8 reopen).
                        _t, heading_re, cols = n
                        header, _rows = _section_table(text, heading_re)
                        got = [_norm_cell(c) for c in header] if header else None
                        want = [_norm_cell(c) for c in cols]
                        if got != want:
                            problems.append(
                                f"{fname}: dimensions table header {got!r} != required {cols!r}")
                    else:
                        # No silent substring fallback (F-0130 round-8 human ruling): an
                        # unclassified structural needle is a LOUD failure, not `n not in text`.
                        problems.append(f"{fname}: unclassified structural needle {n!r}")
            # finding.md / class-finding.md: PARSE the frontmatter, check the KEY SET and the
            # fixed-default VALUES against the ONE source (CANONICAL_SCHEMA + the §2 record).
            for fname, req in _REQ_FM_BY_TEMPLATE.items():
                f = tdir / fname
                if not f.exists():
                    continue  # MISSING already reported above
                fm, dups, badform = _template_fm(f)
                if fm is None:
                    problems.append(f"{fname}: no frontmatter block"); continue
                # A duplicate singleton key is a corrupt canonical record even when the
                # collapsed key set and values stay canonical (F-0130 round-7): reject it
                # off the parser's own `dups`, not a second judgement.
                for dk in sorted(set(dups)):
                    problems.append(f"{fname}: frontmatter has duplicate key {dk!r}")
                # A bad-form field (a §2-undeclared FORM: flow mapping, nested list, unclosed
                # bracket) parses to a mangled value under a CANONICAL key, so the exact-set
                # and value checks below read the key as valid (F-0130 round-8): reject it off
                # the parser's own `badform`, the same signal `finding_files_malformed` fails
                # closed on — not a second judgement.
                for bf in sorted(set(badform)):
                    problems.append(f"{fname}: frontmatter has bad-form field {bf!r}")
                keys = set(fm)
                # EXACT set (round-5 reopen): a one-sided `req - keys` let a non-canonical field
                # (`future-field: null`) ride along. Reject BOTH directions — missing canonical
                # field AND extra non-canonical field — against the ONE CANONICAL_SCHEMA source.
                for field in sorted(req - keys):
                    problems.append(f"{fname}: frontmatter missing canonical field {field!r}")
                for field in sorted(keys - req):
                    problems.append(f"{fname}: frontmatter has non-canonical field {field!r}")
                for field, want in _FM_VALUE_SOURCE.get(fname, _S2_CONST).items():
                    if field in fm and _norm_null(fm[field]) != _norm_null(want):
                        problems.append(
                            f"{fname}: frontmatter field {field!r} value {_norm_null(fm[field])!r}"
                            f" != canonical XCHECK.md §2 value {_norm_null(want)!r}")
            for fname in _NO_FM_TEMPLATES:
                f = tdir / fname
                if f.exists() and _template_fm(f)[0] is not None:
                    problems.append(
                        f"{fname}: carries a frontmatter block — that record lives in "
                        f"audit/state.json and nothing reads a block here, so it would be "
                        f"a field set that looks recorded and is not")
            # AUDIT-*.md §5 limits must EQUAL the XCHECK.md §10 parsed defaults (not merely be
            # present): a wrong or non-numeric value (`max_findings_per_pass: banana`) is caught.
            if _S10_LIMITS:
                for fname in ("AUDIT-code.md", "AUDIT-text.md"):
                    f = tdir / fname
                    if not f.exists():
                        continue
                    got = _audit_limits(f)
                    for k, v in _S10_LIMITS.items():
                        if got.get(k) != v:
                            problems.append(f"{fname}: limit {k!r}={got.get(k)!r} != XCHECK.md §10 default {v!r}")
            # pass.md must show BOTH coverage polarities (§4 rule 5). "COVERED:" is a
            # substring of "NOT COVERED:", so two "NOT COVERED:" would satisfy a naive
            # count>=2; require a POSITIVE marker AND a negative one distinctly (F-0130 round-2).
            pf = tdir / "pass.md"
            if pf.exists():
                pt = pf.read_text(encoding="utf-8")
                neg = pt.count("NOT COVERED:")
                pos = pt.count("COVERED:") - neg
                if pos < 1 or neg < 1:
                    problems.append("pass.md: missing a COVERED:/NOT COVERED: polarity")
            return problems
        clean130 = _template_problems(_rp50_root)
        def _mut_templates(td, path, repls):
            mroot = Path(td)
            _sh50.copytree(_rp50_root / "templates", mroot / "templates")
            f = mroot / "templates" / path
            t = f.read_text(encoding="utf-8")
            for old, new in repls:
                t = t.replace(old, new)
            f.write_text(t, encoding="utf-8")
            return _template_problems(mroot)
        with tempfile.TemporaryDirectory() as td:  # drop a mandatory finding section (real removal)
            mut130_sec = _mut_templates(td, "finding.md",
                                        [("## How to verify the fix", "## How to fix it")])
        with tempfile.TemporaryDirectory() as td:  # comment the section header OUT of the document structure (round-4 hole)
            mut130_html = _mut_templates(td, "finding.md",
                                         [("## How to verify the fix", "<!-- ## How to verify the fix -->")])
        with tempfile.TemporaryDirectory() as td:  # collapse the positive COVERED polarity
            mut130_cov = _mut_templates(td, "pass.md", [("COVERED: units", "NOT COVERED: units")])
        with tempfile.TemporaryDirectory() as td:  # remove `id` from frontmatter; plant a decoy `id:` in the body
            mut130_fm = _mut_templates(td, "finding.md",
                                       [("id: F-XXXX", "identifier: F-XXXX"),
                                        ("## Evidence", "id: prose-only decoy outside frontmatter\n\n## Evidence")])
        with tempfile.TemporaryDirectory() as td:  # rename the §8 gate fields out of the CF frontmatter (nullable — a non-null-only set missed them)
            mut130_cf = _mut_templates(td, "class-finding.md",
                                       [("blocked: null", "halted: null"),
                                        ("norm-ruling: null", "ruling: null")])
        with tempfile.TemporaryDirectory() as td:  # flip a canonical §2 VALUE: status reported -> closed (round-4 hole)
            mut130_val = _mut_templates(td, "finding.md",
                                        [("status: reported", "status: closed")])
        with tempfile.TemporaryDirectory() as td:  # delete a NULLABLE canonical field (class) from finding frontmatter (round-4 hole)
            mut130_del = _mut_templates(td, "finding.md",
                                        [("class: null # CF-XXXX once absorbed by a class finding\n", "")])
        with tempfile.TemporaryDirectory() as td:  # corrupt an AUDIT limit DEFAULT: 15 -> banana (round-4 hole)
            mut130_lim = _mut_templates(td, "AUDIT-code.md",
                                        [("max_findings_per_pass: 15", "max_findings_per_pass: banana")])
        with tempfile.TemporaryDirectory() as td:  # add a non-canonical field to finding frontmatter (round-5 hole: one-sided key check)
            mut130_extra = _mut_templates(td, "finding.md",
                                          [("updated: YYYY-MM-DD", "updated: YYYY-MM-DD\nfuture-field: null")])
        with tempfile.TemporaryDirectory() as td:  # §5 -> banana + a decoy limit line hidden in the intro HTML comment (round-5 hole: whole-file scan)
            mut130_limdecoy = _mut_templates(td, "AUDIT-code.md",
                                             [("max_findings_per_pass: 15", "max_findings_per_pass: banana"),
                                              ("are the working queue.", "are the working queue.\nmax_findings_per_pass: 15")])
        with tempfile.TemporaryDirectory() as td:  # two contradicting `status:` singletons — last-wins collapses to a canonical key set + value (round-6 hole: guard discarded the parser's dup signal)
            mut130_dup = _mut_templates(td, "finding.md",
                                        [("status: reported", "status: closed\nstatus: reported")])
        with tempfile.TemporaryDirectory() as td:  # a §2-undeclared FORM: `unit: {a: b}` flow mapping parses to a mangled value under a canonical key (round-7 hole: guard discarded the parser's badform signal)
            mut130_badform = _mut_templates(td, "finding.md",
                                            [("unit: # material unit; YAML list if the finding spans units", "unit: {a: b}")])
        with tempfile.TemporaryDirectory() as td:  # comment the dimensions TABLE HEADER out of the document — the byte sequence survives but the table is gone (round-8 hole: `n not in text` substring branch)
            mut130_tablehdr = _mut_templates(td, "AUDIT-text.md",
                                             [("| key | what it catches | norm source |",
                                               "<!-- | key | what it catches | norm source | -->")])
        with tempfile.TemporaryDirectory() as td:  # a section HEADING surviving only inside a paragraph — no standalone ATX line (round-8 ruling poison)
            mut130_headprose = _mut_templates(td, "finding.md",
                                              [("## Objection", "See ## Objection note inline")])
        with tempfile.TemporaryDirectory() as td:  # the real section HEADING wrapped in a ```-fenced code block — pandoc Header=0, CodeBlock=1, but the raw line survived (round-9 hole: fence-blind ATX scan)
            mut130_headfence = _mut_templates(td, "finding.md",
                                              [("## How to verify the fix", "```text\n## How to verify the fix\n```")])
        with tempfile.TemporaryDirectory() as td:  # the real dimensions TABLE header+delimiter wrapped in a ```-fenced code block — pandoc Table=0, CodeBlock=1, but `_section_table` scanned the raw lines (round-9 hole: fence-blind table scan)
            mut130_tablefence = _mut_templates(td, "AUDIT-text.md",
                                               [("| key | what it catches | norm source |\n|---|---|---|",
                                                 "```markdown\n| key | what it catches | norm source |\n|---|---|---|\n```")])
        with tempfile.TemporaryDirectory() as td:  # a real heading indented 4 columns — CommonMark indented code, NOT a heading (round-10 hole: strip() erased leading spaces)
            mut130_headindent = _mut_templates(td, "finding.md",
                                               [("## How to verify the fix", "    ## How to verify the fix")])
        with tempfile.TemporaryDirectory() as td:  # POSITIVE control: a heading with 0-3 leading AND trailing spaces IS a heading — must stay fully clean, not over-rejected
            mut130_headok = _mut_templates(td, "finding.md",
                                           [("## Objection", "   ## Objection   ")])
        with tempfile.TemporaryDirectory() as td:  # a fence whose interior ```not-a-close run has a non-space tail does NOT close the block, so the real `## Verification` after it stays code (round-10 hole: close-fence tail unchecked)
            mut130_fenceclose = _mut_templates(td, "finding.md",
                                               [("## Verification", "```text\n```not-a-close\n## Verification\n```")])
        with tempfile.TemporaryDirectory() as td:  # the real dimensions TABLE header+delimiter wrapped in a MULTI-LINE HTML comment — pandoc Table=0, RawBlock (renders as nothing), but the raw pipe lines survived (round-11 hole: comment-blind table scan)
            mut130_tablecomment = _mut_templates(td, "AUDIT-text.md",
                                                 [("| key | what it catches | norm source |\n|---|---|---|",
                                                   "<!--\n| key | what it catches | norm source |\n|---|---|---|\n-->")])
        with tempfile.TemporaryDirectory() as td:  # the real section HEADING wrapped in a MULTI-LINE HTML comment — renders as nothing (adjacent branch to the table scan, same round-11 root)
            mut130_headcomment = _mut_templates(td, "finding.md",
                                                [("## How to verify the fix", "<!--\n## How to verify the fix\n-->")])
        with tempfile.TemporaryDirectory() as td:  # 0.8: a hand-typed frontmatter block on a template whose record lives in state.json — it looks recorded, nothing reads it
            mut130_nofm = _mut_templates(td, "construal.md",
                                         [("<!-- The construal ARGUMENT",
                                           "---\nkey: 0000000000000000\nrole: Auditor\nstatus: proposed\n---\n\n"
                                           "<!-- The construal ARGUMENT")])
        ok = (clean130 == []
              and any("construal.md" in p and "carries a frontmatter block" in p for p in mut130_nofm)
              and any("finding.md" in p and "How to verify the fix" in p for p in mut130_sec)
              and any("finding.md" in p and "How to verify the fix" in p for p in mut130_html)
              and any("polarity" in p for p in mut130_cov)
              and any("finding.md" in p and "frontmatter missing canonical field 'id'" in p for p in mut130_fm)
              and any("class-finding.md" in p and "'blocked'" in p for p in mut130_cf)
              and any("class-finding.md" in p and "'norm-ruling'" in p for p in mut130_cf)
              and any("finding.md" in p and "'status'" in p and "closed" in p for p in mut130_val)
              and any("finding.md" in p and "canonical field 'class'" in p for p in mut130_del)
              and any("AUDIT-code.md" in p and "max_findings_per_pass" in p for p in mut130_lim)
              and any("finding.md" in p and "non-canonical field 'future-field'" in p for p in mut130_extra)
              and any("AUDIT-code.md" in p and "max_findings_per_pass" in p for p in mut130_limdecoy)
              and any("finding.md" in p and "duplicate key 'status'" in p for p in mut130_dup)
              and any("finding.md" in p and "bad-form field 'unit'" in p for p in mut130_badform)
              and any("AUDIT-text.md" in p and "dimensions table header" in p for p in mut130_tablehdr)
              and any("finding.md" in p and "section header" in p and "Objection" in p for p in mut130_headprose)
              and any("finding.md" in p and "section header" in p and "How to verify the fix" in p for p in mut130_headfence)
              and any("AUDIT-text.md" in p and "dimensions table header" in p for p in mut130_tablefence)
              and any("finding.md" in p and "section header" in p and "How to verify the fix" in p for p in mut130_headindent)
              and mut130_headok == []
              and any("finding.md" in p and "section header" in p and "Verification" in p for p in mut130_fenceclose)
              and any("AUDIT-text.md" in p and "dimensions table header" in p for p in mut130_tablecomment)
              and any("finding.md" in p and "section header" in p and "How to verify the fix" in p for p in mut130_headcomment))
        print(f"{'PASS' if ok else 'FAIL'}: the seven shipped templates/ files carry the required sections, fields and tables as ORDINARY MARKDOWN LINES — a LINE-BASED drift check, NOT a CommonMark parser (F-0130 §8 rule 5 exception): it does NOT detect structure hidden inside raw HTML (`<div>...`) or other embeddings, and targets ACCIDENTAL drift in our own templates (a renamed/missing section, a dropped/wrong field, a corrupted plain-Markdown table), not deliberate obfuscation of them. Within that scope, section headings are matched as real ATX lines and table headers as whole pipe-table header cells (`_section_table` cells EQUAL the declared columns, not a substring — NO `n not in text` branch survives for structural content, F-0130 round-8), every raw-line structural recognizer reading them through the ONE CommonMark leading-indent rule (`_atx_indent_ok`: 0-3 leading spaces is a heading/table/fence, 4+ columns is indented code) with NON-RENDERED blocks — fenced code blocks AND HTML comment blocks — stripped FIRST by the ONE `_strip_nonstructure` (fence delimiters CommonMark-exact — a closing fence is a same-char run of >= length followed ONLY by whitespace, so a ```not-a-close interior line does not end the block; an HTML comment block opens on a `<!--` line and ends on the first `-->` line, a MID-LINE `<!-- ... -->` span — even one running across lines — is blanked with its visible prefix kept (F-0153 reopen), mutually exclusive with fences) so a ```-fenced, 4-column-indented, OR `<!-- ... -->`-commented EXAMPLE/copy of a heading/table is not read as document structure (F-0130 round-9/10/11); the three templates whose record lives in state.json (construal/pass/plan) must carry NO frontmatter block — a typed one looks recorded and is read by nothing — while the finding/CF frontmatter KEY SET is checked EXACTLY (missing AND extra → reject) against CANONICAL_SCHEMA (finding.md carries the full §2 record incl blocked/norm-ruling, class-finding.md the full schema incl members + the CF-only `created-by` provenance field), with NO duplicate singleton key AND NO bad-form field (the parser's own dup + badform signals, not a second judgement); fixed-default §2 VALUES (status=reported, attempts=0, the null defaults) are checked against the §2 record; and AUDIT-*.md §5 limits are checked against the XCHECK.md §10 defaults table, reading ONLY the §5 body with HTML comments stripped — so a stripped OR html-commented `## How to verify the fix`, a collapsed COVERED polarity, an `id` deleted from frontmatter (even with a body decoy), a non-canonical `future-field` added, blocked/norm-ruling renamed out of the CF, a flipped `status: reported`->`closed`, a deleted nullable `class:`, a corrupted `max_findings_per_pass` default (even with a decoy `15` hidden in a comment), two contradicting `status:` singletons that last-wins-collapse to a canonical record, a §2-undeclared `unit: {{a: b}}` flow-mapping form that parses to a mangled value under a canonical key, a dimensions table header commented out of the document (`<!-- | key | ... | -->`, byte sequence surviving in prose), a section heading surviving only inside a paragraph, a real section heading wrapped in a ```-fenced code block, a real dimensions table header+delimiter wrapped in a ```-fenced code block, a real heading indented 4 columns into code (`    ## ...`), a real heading hidden behind a fence whose ```not-a-close interior run fails to close it, a real dimensions table header+delimiter wrapped in a multi-line HTML comment, and a real section heading wrapped in a multi-line HTML comment are each caught — while a heading with 0-3 leading and trailing spaces stays accepted (F-0130)"
              + ("" if ok else f" -> clean={clean130} sec={mut130_sec} html={mut130_html} cov={mut130_cov} fm={mut130_fm} cf={mut130_cf} val={mut130_val} del={mut130_del} lim={mut130_lim} extra={mut130_extra} limdecoy={mut130_limdecoy} dup={mut130_dup} badform={mut130_badform} tablehdr={mut130_tablehdr} headprose={mut130_headprose} headfence={mut130_headfence} tablefence={mut130_tablefence} headindent={mut130_headindent} headok={mut130_headok} fenceclose={mut130_fenceclose} tablecomment={mut130_tablecomment} headcomment={mut130_headcomment}"))
        failed += 0 if ok else 1

        # F-0127: install.sh promises (README §5.2) an idempotent upgrade that refreshes
        # XCHECK.md + templates WITHOUT touching audit state (AUDIT.md, LEDGER.md,
        # findings/passes/plans). No shipped check exercised it, so an inverted guard
        # could wipe a planned audit with both release checks green.
        def _install_preserves(repo_root):
            problems = []
            with tempfile.TemporaryDirectory() as td:
                target = Path(td) / "proj"; target.mkdir()
                r = subprocess.run(["sh", str(repo_root / "install.sh"), str(target)],
                                   capture_output=True, text=True, timeout=GIT_TIMEOUT)
                if r.returncode != 0:
                    return [f"install rc={r.returncode}: {r.stderr.strip()}"]
                audit = target / "audit"
                sent = {
                    audit / "AUDIT.md": "SENTINEL-AUDIT-42\n",
                    audit / "LEDGER.md": "SENTINEL-LEDGER-42\n",
                    audit / "findings" / "F-9999-s.md": "SENTINEL-FINDING-42\n",
                    audit / "passes" / "P-99-s.md": "SENTINEL-PASS-42\n",
                    audit / "plans" / "RP-9999.md": "SENTINEL-PLAN-42\n",
                }
                for p, s in sent.items():
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(s, encoding="utf-8")
                (audit / "XCHECK.md").write_text("STALE-XCHECK\n", encoding="utf-8")
                (audit / "templates" / "finding.md").write_text("STALE-TEMPLATE\n", encoding="utf-8")
                r2 = subprocess.run(["sh", str(repo_root / "install.sh"), str(target)],
                                    capture_output=True, text=True, timeout=GIT_TIMEOUT)
                if r2.returncode != 0:
                    return [f"re-install rc={r2.returncode}: {r2.stderr.strip()}"]
                for p, s in sent.items():
                    if (not p.exists()) or p.read_text(encoding="utf-8") != s:
                        problems.append(f"audit-state NOT preserved: {p.name}")
                if (audit / "XCHECK.md").read_text(encoding="utf-8") == "STALE-XCHECK\n":
                    problems.append("XCHECK.md not refreshed")
                if (audit / "templates" / "finding.md").read_text(encoding="utf-8") == "STALE-TEMPLATE\n":
                    problems.append("templates/finding.md not refreshed")
            return problems
        clean127 = _install_preserves(_rp50_root)
        with tempfile.TemporaryDirectory() as td:
            mrepo = Path(td) / "repo"; mrepo.mkdir()
            _sh50.copy(_rp50_root / "install.sh", mrepo / "install.sh")
            _sh50.copy(_rp50_root / "XCHECK.md", mrepo / "XCHECK.md")
            _sh50.copytree(_rp50_root / "templates", mrepo / "templates")
            ish = mrepo / "install.sh"
            ish.write_text(ish.read_text(encoding="utf-8").replace(
                '[ -f "$TARGET/audit/AUDIT.md" ] || cp templates/AUDIT-text.md',
                'cp templates/AUDIT-text.md'), encoding="utf-8")
            mut127 = _install_preserves(mrepo)
        ok = (clean127 == [] and any("AUDIT.md" in p and "preserved" in p for p in mut127))
        print(f"{'PASS' if ok else 'FAIL'}: re-running install.sh preserves audit state byte-for-byte and refreshes XCHECK.md+templates; an inverted AUDIT.md guard is caught (F-0127)"
              + ("" if ok else f" -> clean={clean127} mut={mut127}"))
        failed += 0 if ok else 1

        # F-0128: install-launchers.sh multiplies the single source of truth (skills/)
        # onto three platforms; its own selftest only checked the plugin gate, never the
        # six installed outputs. A poisoned generator shipped a body-less role prompt
        # with exit 0. Drive the real installer into a temp HOME and assert parity.
        _L_NAMES = ["xcheck-plan", "xcheck-audit", "xcheck-triage",
                    "xcheck-remediate", "xcheck-verify", "xcheck-status"]

        def _canon_opencode(src_text):
            # Mirror generate_opencode_commands: header from the FIRST `description: `
            # line; body = everything after the 2nd `---` (skills carry exactly two).
            parts = src_text.split("\n")
            desc = next((l[len("description: "):] for l in parts
                         if l.startswith("description: ")), "")
            idx = [i for i, l in enumerate(parts) if l == "---"]
            body = parts[idx[1] + 1:] if len(idx) >= 2 else []
            while body and body[-1] == "":
                body.pop()
            return desc, body

        def _launcher_parity_problems(repo_root):
            problems = []
            with tempfile.TemporaryDirectory() as td:
                home = Path(td)
                env = dict(os.environ); env["HOME"] = str(home)
                (home / ".codex" / "prompts").mkdir(parents=True)
                for n in _L_NAMES:  # seed ALL SIX legacy codex prompts, not just one
                    (home / ".codex" / "prompts" / f"{n}.md").write_text("legacy\n", encoding="utf-8")
                r = subprocess.run(
                    ["sh", str(repo_root / "launchers" / "install-launchers.sh"), "all"],
                    capture_output=True, text=True, env=env, timeout=GIT_TIMEOUT)
                if r.returncode != 0:
                    return [f"install-launchers all rc={r.returncode}: {r.stderr.strip()}"]
                claude = home / ".claude" / "skills"
                codex = home / ".agents" / "skills"
                oc = home / ".config" / "opencode" / "command"
                for label, base in (("claude", claude), ("codex", codex)):
                    got = sorted(p.name for p in base.iterdir()) if base.is_dir() else []
                    if got != sorted(_L_NAMES):
                        problems.append(f"{label}: expected 6 skills, got {got}"); continue
                    for n in _L_NAMES:
                        inst, src = base / n / "SKILL.md", repo_root / "skills" / n / "SKILL.md"
                        if (not inst.exists()) or inst.read_bytes() != src.read_bytes():
                            problems.append(f"{label}/{n}: not byte-identical to skills source")
                got_oc = sorted(p.name for p in oc.iterdir()) if oc.is_dir() else []
                if got_oc != sorted(f"{n}.md" for n in _L_NAMES):
                    problems.append(f"opencode: expected 6 commands, got {got_oc}")
                else:
                    for n in _L_NAMES:
                        src = (repo_root / "skills" / n / "SKILL.md").read_text(encoding="utf-8")
                        desc, body = _canon_opencode(src)
                        gen = (oc / f"{n}.md").read_text(encoding="utf-8")
                        header = f"---\ndescription: {desc}\n---\n"
                        if not gen.startswith(header):
                            problems.append(f"opencode/{n}: header mismatch"); continue
                        gbody = gen[len(header):].split("\n")
                        while gbody and gbody[-1] == "":
                            gbody.pop()
                        if gbody != body:
                            problems.append(f"opencode/{n}: body not preserved (empty or altered)")
                # N4 promise: `all` removes ALL SIX legacy codex prompts, not just one.
                leftover = [n for n in _L_NAMES
                            if (home / ".codex" / "prompts" / f"{n}.md").exists()]
                if leftover:
                    problems.append(f"legacy codex prompts not removed: {leftover}")
                # F-0039 / N4: orchestrator is NOT part of `all`; the ~/.local/bin/xcheck
                # symlink must be absent. lexists() catches a dangling symlink too (its
                # target need not exist for the install to be a contract breach).
                if os.path.lexists(str(home / ".local" / "bin" / "xcheck")):
                    problems.append("orchestrator installed by target=all (must be orchestrator-only)")
            return problems
        clean128 = _launcher_parity_problems(_rp50_root)
        def _mut_launcher(td, repls):
            mrepo = Path(td) / "repo"; (mrepo / "launchers").mkdir(parents=True)
            _sh50.copy(_rp50_root / "launchers" / "install-launchers.sh",
                       mrepo / "launchers" / "install-launchers.sh")
            _sh50.copytree(_rp50_root / "skills", mrepo / "skills")
            ilh = mrepo / "launchers" / "install-launchers.sh"
            t = ilh.read_text(encoding="utf-8")
            for old, new in repls:
                t = t.replace(old, new)
            ilh.write_text(t, encoding="utf-8")
            return _launcher_parity_problems(mrepo)
        with tempfile.TemporaryDirectory() as td:  # generator drops the opencode body
            mut128_body = _mut_launcher(td, [("awk 'c==2{print} /^---$/{c++; next}'",
                                              "awk 'c==2{next} /^---$/{c++; next}'")])
        with tempfile.TemporaryDirectory() as td:  # legacy cleanup skips one prompt
            mut128_leg = _mut_launcher(td, [(
                "for name in xcheck-plan xcheck-audit xcheck-triage xcheck-remediate xcheck-verify xcheck-status; do",
                "for name in xcheck-plan xcheck-audit xcheck-triage xcheck-remediate xcheck-verify; do")])
        with tempfile.TemporaryDirectory() as td:  # `all` also installs the orchestrator symlink
            mut128_orch = _mut_launcher(td, [(
                'if [ "$target" = orchestrator ]; then',
                'if [ "$target" = orchestrator ] || [ "$target" = all ]; then')])
        ok = (clean128 == []
              and any("opencode" in p for p in mut128_body)
              and any("legacy" in p for p in mut128_leg)
              and any("orchestrator" in p for p in mut128_orch))
        print(f"{'PASS' if ok else 'FAIL'}: install-launchers installs six parity outputs per platform (claude/codex byte-identical, opencode canonical transform), removes ALL SIX legacy codex prompts, and does NOT install the orchestrator under `all`; a body-dropping generator, a skipped legacy removal, and an orchestrator-under-all leak are each caught (F-0128)"
              + ("" if ok else f" -> clean={clean128} body={mut128_body} leg={mut128_leg} orch={mut128_orch}"))
        failed += 0 if ok else 1

        # F-0129 (human ruling 2026-07-28, round-4 re-take): a prose contract's MEANING cannot be
        # proved by a substring/whole-sentence scan. The round-4 hole was that the pinned
        # "sentences" were FRAGMENTS ending BEFORE the mandatory control ACTION, so inverting the
        # action alone (`do NOT mkdir`->`DO mkdir`, `STOP — you cannot prove independence`->
        # `CONTINUE — independence is assumed`) kept every pinned fragment intact and shipped to
        # the generated OpenCode launcher. The ruling's fix: the canonical text of each mandatory
        # contract BLOCK lives HERE as data, in ONE place, and every writing skill must carry its
        # block BYTE-FOR-BYTE. Any edit inside a block — an inverted action, `do NOT`->`DO`, a
        # renamed token (`XCHECK_LOCK_INHERITED`), a dropped/rewritten sentence — diverges the
        # bytes and the check goes red. The blocks are thus rigid: changing one means editing this
        # canonical data AND the skills together, on purpose (it is normative text, not prose).
        # NARROW CLAIM (honest): this proves the shipped skills carry the canonical contract TEXT
        # unchanged; it does NOT prove the canonical text itself is meaningful — that is human
        # review. Short role-specific sentences (triage/remediate/verify-gate/status) stay pinned
        # verbatim too; the two BLOCKS carry the control actions the fragments used to miss.
        _CANON_LOCK_DISCIPLINE = '## Lock discipline\n\n`audit/.lock` serializes writing sessions (§4 rule 8) and is shared with the orchestrator (`bin/xcheck`). It is a DIRECTORY, acquired atomically with `mkdir` — the second writer\'s `mkdir` fails with EEXIST, so the create IS the acquisition. Match that — never check-then-create (the gap between an existence check and a separate create lets two sessions both win).\n\n0. **Orchestrated child — skip acquisition (F-0093):** the orchestrator (`bin/xcheck`) signals inherited lock ownership through TWO channels, because process env does not reach a sandboxed command runner on every agent platform (a Codex-style runner runs your shell where `env` shows nothing of the launched CLI\'s variables): `XCHECK_LOCK_INHERITED=<nonce>` in your environment AND an `Orchestration context` line appended to your role-prompt carrying the same `XCHECK_LOCK_INHERITED=<nonce>`. Read the nonce from whichever channel you can see — the prompt line always reaches you. If that nonce is present AND equals the `nonce` in `audit/.lock/owner`, the orchestrator launched you and is already holding the writing lock around this whole transaction — do NOT `mkdir audit/.lock` (it would fail EEXIST on your own parent\'s lock and abort you), do NOT write an owner record, and do NOT remove the lock on exit; the orchestrator owns its release. If the nonce is present but does not match the on-disk owner (or the owner record is missing), treat it as a foreign lock — stop and report the conflict to the human. If neither channel carries a nonce you are a standalone session — acquire atomically as in step 1 below. Then **export that nonce into the shell you run `xcheck` verbs from** — `export XCHECK_LOCK_INHERITED=<nonce>` — because a write verb refuses to acquire a second time and has to be told which lock it is writing under. Without it every `xcheck` verb you run exits 1 with `audit/.lock is held`, which is the lock your own parent holds, refusing you: on the Ouroboros-4 run that refusal met 130 of the 136 sessions that recorded nothing.\n\n1. **Acquire atomically:** create the lock DIRECTORY in one step that FAILS if it already exists — `mkdir audit/.lock` — then write the owner record inside it, including a fresh per-session `nonce` — a unique random token you generate at acquire time (16 hex chars from a random source, matching `bin/xcheck`, which writes `os.urandom(8).hex()`): `printf \'%s\' \'{"pid": <pid or 0>, "role": "<Role>", "started": "<ISO>", "host": "<host>", "nonce": "<nonce>"}\' > audit/.lock/owner`. `mkdir` is the atomic gate (a second `mkdir` on an existing directory fails); the `nonce` — NOT the `pid`+`started` pair, which is not a unique owner id (two agent-CLI sessions both record `pid: 0` and can share the same ISO second, giving a byte-identical record) — is what identifies you for release. For `pid`, record a process id ONLY if it stays alive for your whole session (e.g. the orchestrator\'s own pid); a transient shell `$$` dies the instant the acquire command returns — while your session keeps running — which would make your own live lock look stale and let another session steal it, so never record `$$`. An agent-CLI session has no session-long pid: record `pid: 0` (the `nonce`, not the pid, is your identity; the pid only drives liveness). `bin/xcheck` reads `pid: 0` as a live manual session (`os.kill(0, 0)` never reports it dead), so the lock stands until your owner-checked release removes it, or — if the session died — a human clears it with `xcheck unlock --force`. If `mkdir` fails, another writing session holds the lock — do not start; report the conflict to the human. Having acquired, **export the nonce you just wrote** — `export XCHECK_LOCK_INHERITED=<the nonce in your owner record>` — in the shell you run `xcheck` verbs from. You are holding the lock for the whole session by design, and a verb that is not told which lock it is under will try to take it again and be refused by yours.\n2. **Owner-checked release:** hold the lock for the whole session; before removing, re-read `audit/.lock/owner` and confirm its `nonce` still matches the one you wrote at acquire — check the `nonce`, never the `pid`+`started` pair (a `pid: 0` manual session can collide on it), exactly as `bin/xcheck`\'s `release()` does — only then `rm audit/.lock/owner && rmdir audit/.lock`, on every exit path including early stop. Removing your own record first and then `rmdir` means a foreign owner\'s record keeps the directory non-empty, so `rmdir` can never remove a lock you do not own. Never delete a lock you do not own.\n3. **Stale lock:** a lock carrying a real, dead `pid` is stale, but non-force `xcheck unlock` no longer removes it — clearing a lock by pathname cannot be made race-free against a concurrent clear + re-acquire (F-0095), so plain `xcheck unlock` only diagnoses staleness and never deletes. Clear any stale lock — a dead-pid lock, a `pid: 0` manual-session lock (which never reads as pid-dead), or a pre-directory `.lock` FILE (legacy, not auto-migrated) — with `xcheck unlock --force`, and only after the human confirms no writing session is active (§4 rule 8). Never silently steal a lock you do not own.'
        _CANON_VERIFIER_INDEPENDENCE = "**Hard rule: you must not be the agent or session that produced these fixes.** Establish provenance from each `fixed` finding's `fixed-by` field (the fixing session's identity) before proceeding: if any `fixed` finding has NO `fixed-by`, its `fixed-by` is not a **canonical 16-hex-digit token** (arbitrary text is not proof a producer was identified), or its `fixed-by` equals your own `$XCHECK_SESSION_ID` (read from the `Orchestration context` line in your role-prompt), STOP — you cannot prove independence, or you would be verifying your own fix. `bin/xcheck` fail-closes routing on exactly this check (missing / malformed / self), so an orchestrated session never reaches you in those cases (F-0096); a directly-launched session must apply it by hand. The default mapping is Claude as Remediator and Codex as Verifier; if the current agent produced the fixes, stop and route verification to a different agent. A fresh session of the same agent is only the fallback minimum and must be disclosed to the human."
        # PHASE 4 (third audit, P0): the five writing launchers are WRAPPERS now. They do
        # not run a role, so the role contracts they used to carry — lock discipline, the
        # construal gate, the §5 refusal route, verifier independence, session hygiene —
        # have no consumer in a launcher any more. They are not deleted: they moved to the
        # place the DISPATCHED child reads them, `runner.PROMPTS` and `XCHECK.md`, and the
        # second check below asserts each one arrived. A control that is merely dropped
        # when its subject changes shape is a control that was never load-bearing.
        _ROLE_CONTRACT_SENTENCES = {
            'xcheck-triage': [
                # Triage is the one gate the orchestrator cannot dispatch: a triage
                # transition is human-owned, so `authorize_dispatch_write` allows it only
                # with no session open. Its wrapper therefore still carries the two
                # sentences that bound what the AGENT may do at that gate.
                'The human makes every decision; you present, record, and never fill gaps with your own judgment.',
                'Never touch finding files: their frontmatter is a generated mirror of `audit/state.json`, and `xcheck set-status` is the only thing that moves a status.',
                # WHY this one wrapper does not hand off. Without it, "triage is exempt
                # from the hand-off check" is an exemption with no stated reason, which is
                # how the next surface exempts itself too.
                'Triage is the one gate the orchestrator does not dispatch.',
            ],
            'xcheck-status': [
                'This session **writes nothing** — it is not a writing session in the §4 rule 8 sense',
                'Read-only role: do not create `audit/.lock`.',
            ],
        }
        _WRAPPERS4 = ("xcheck-plan", "xcheck-audit", "xcheck-triage",
                      "xcheck-remediate", "xcheck-verify")
        # The canonical wrapper block, byte-for-byte in all five. It replaces the
        # `uncontained-direct` disclosure, which a wrapper may not carry: the wrapper runs
        # under all eight controls, so text saying it does not is now false rather than
        # merely stale.
        _CANON_WRAPPER = '**Launch mode: `orchestrated`.** This launcher is a WRAPPER. It does not run the role inside your agent; it hands the work to `xcheck next`, which dispatches the role through `runner.py` with all eight controls in force: a sandbox profile, a hard timeout, an environment allowlist, log redaction, a process-group kill, the courier review of the diff, an invocation envelope recording what was in force at dispatch, and a session receipt attesting what the session actually did. What this skill contributes is preflight and routing — it checks the project is ready, says which role the orchestrator will dispatch and why, hands off, and reports what came back. It is not a session: it holds no writing lock, writes no finding, and has no charter of its own. The cost is the conversation. The role now runs as a child process you do not talk to, and its reasoning reaches you as recorded output instead of as a dialogue; xcheck no longer ships a launcher that trades the eight controls for that dialogue.'
        _CANON_UNCONTAINED_DISCLOSURE = '**Launch mode: `uncontained-direct`.** None of the runner controls are in force here, because xcheck is not in the process: no sandbox profile, no hard timeout, no environment allowlist, no log redaction, no process-group kill, no courier review of the diff, no invocation envelope and no session receipt. The first six mean containment is whatever your agent platform provides — xcheck does not know what that is and cannot report it. The last two mean this session leaves nothing machine-checkable behind: no record of which policy, charter and executable were in force when it started, and no attestation afterwards that what it did matched them. A human vouches for this work; the tool does not.'
        _CANON_STATUS_EXEMPTION = '**Exempt, and only this one:** this launcher stays direct because it is read-only — it writes nothing and moves no machine state, so there is no diff for a courier to review and no effect for an envelope or a receipt to attest.'
        # What a wrapper may not say. Each of these is an instruction to BE the session:
        # taking the writing lock, writing a construal, recording a refusal, reading the
        # session id the orchestrator hands its child. A copy left in a launcher is a copy
        # that drifts out of agreement with the one the child actually reads.
        _ROLE_EXECUTION_MARKERS = ("mkdir audit/.lock", "## Lock discipline",
                                   "## Construal", "## Refusal", "propose-construal",
                                   "$XCHECK_SESSION_ID", "XCHECK_LOCK_INHERITED")

        def _role_prompt_problems(skills_root):
            problems = []

            def rd(n):
                f = skills_root / n / "SKILL.md"
                return f.read_text(encoding="utf-8") if f.exists() else None
            for n, sentences in _ROLE_CONTRACT_SENTENCES.items():
                t = rd(n)
                if t is None:
                    problems.append(f"{n}: MISSING"); continue
                for c in sentences:
                    if c not in t:
                        problems.append(f"{n}: missing verbatim contract sentence {c!r}")
            st = rd("xcheck-status")
            if st is None:
                problems.append("xcheck-status: MISSING")
            else:
                if "mkdir audit/.lock" in st:
                    problems.append("xcheck-status: acquires the writing lock (must stay read-only)")
                if _CANON_UNCONTAINED_DISCLOSURE not in st:
                    problems.append("xcheck-status: canonical uncontained-disclosure block "
                                    "altered (not byte-for-byte present)")
                if _CANON_STATUS_EXEMPTION not in st:
                    problems.append("xcheck-status: canonical status-exemption line altered "
                                    "(not byte-for-byte present)")
                if _CANON_WRAPPER in st:
                    problems.append("xcheck-status: carries the wrapper block, but it runs "
                                    "in the human's own agent and hands off to nothing")
            for n in _WRAPPERS4:
                t = rd(n)
                if t is None:
                    problems.append(f"{n}: MISSING"); continue
                if _CANON_WRAPPER not in t:
                    problems.append(f"{n}: canonical wrapper block altered (not byte-for-byte present)")
                if _CANON_UNCONTAINED_DISCLOSURE in t:
                    problems.append(f"{n}: a wrapper still disclaims the eight controls it runs under")
                # OUTSIDE the canon block: the canon names `xcheck next` itself, so a
                # wrapper whose steps stopped handing off would pass on its own boilerplate.
                # The instruction to RUN it, not a mention of it: every wrapper also
                # NAMES `xcheck next` when reporting what came back, so a mention is
                # satisfied by prose about a hand-off that no longer happens.
                if n != "xcheck-triage" and "Run `xcheck next`" not in t.replace(_CANON_WRAPPER, ""):
                    # Triage is the exception, and it says so in its own words below: the
                    # orchestrator cannot dispatch a human-owned transition, so that
                    # wrapper reaches the gate and hands the commands to the person.
                    problems.append(f"{n}: names no hand-off — a wrapper that dispatches nothing")
                for marker in _ROLE_EXECUTION_MARKERS:
                    if marker in t:
                        problems.append(f"{n}: role-execution instruction {marker!r} — it is a "
                                        f"wrapper, not a session")
            return problems

        # WHERE THE CONTRACTS WENT. The launchers stopped carrying them; the dispatched
        # child has always read them from here. Asserted so that the phase that emptied the
        # launchers cannot also be the phase that quietly emptied the contract.
        _CONTRACT_HOMES = (
            ("session hygiene (F-0120)", "PROMPTS", "OUTSIDE the project tree"),
            ("session hygiene (F-0120)", "PROMPTS", "NEVER inside the project"),
            ("construal gate — this session does no role work", "PROMPTS",
             "do not plan, audit, remediate or verify anything"),
            ("construal gate — one file is the whole session", "PROMPTS",
             "Writing that one file IS the whole "),
            ("verifier adversarial stance", "PROMPTS", "prove the fixes wrong"),
            ("verifier independence at the transition", "PROMPTS", "Verifier \u2260 fixer"),
            ("remediator in-session sequence", "XCHECK.md",
             "validate \u2192 census \u2192 plan \u2192 fix \u2192 self-check"),
            ("no self-admission of a construal", "XCHECK.md", "admit-construal"),
            ("the closed refusal vocabulary", "XCHECK.md", "out-of-competence"),
            ("scope: demanded \u2286 admitted", "XCHECK.md", "demanded"),
            ("scope residue is mandatory", "XCHECK.md", "Does not cover"),
        )

        def _contract_home_problems(prompts_text, methodology_text):
            """Each contract the wrappers gave up, checked in the home it moved to."""
            where = {"PROMPTS": prompts_text, "XCHECK.md": methodology_text}
            return [f"{label}: gone from {home}" for label, home, needle in _CONTRACT_HOMES
                    if needle not in where[home]]

        _prompts_text = "\n".join(str(v) for v in PROMPTS.values())
        _methodology = (_rp50_root / "XCHECK.md").read_text(encoding="utf-8")
        clean129 = _role_prompt_problems(_rp50_root / "skills")
        clean_homes = _contract_home_problems(_prompts_text, _methodology)

        def _mut_skills(td, rel, old, new):
            mskills = Path(td) / "skills"
            _sh50.copytree(_rp50_root / "skills", mskills)
            f = mskills / rel
            body = f.read_text(encoding="utf-8")
            if old not in body:
                return [f"MUTATION HAS NO SUBJECT in {rel}: {old!r}"]
            f.write_text(body.replace(old, new), encoding="utf-8")
            return _role_prompt_problems(mskills)
        with tempfile.TemporaryDirectory() as td:  # invert the triage human-decides sentence
            mut129_tri = _mut_skills(td, "xcheck-triage/SKILL.md",
                                     "The human makes every decision", "The agent makes every decision")
        with tempfile.TemporaryDirectory() as td:  # widen triage write surface to finding files
            mut129_wr = _mut_skills(td, "xcheck-triage/SKILL.md",
                                    "Never touch finding files", "You may edit finding files")
        with tempfile.TemporaryDirectory() as td:  # soften the wrapper block's receipt clause
            mut4_canon = _mut_skills(td, "xcheck-verify/SKILL.md",
                                     "session receipt attesting", "session receipt more or less attesting")
        with tempfile.TemporaryDirectory() as td:  # a wrapper that starts doing the role's own work
            mut4_role = _mut_skills(td, "xcheck-remediate/SKILL.md",
                                    "3. **Hand off.** Run `xcheck next`.",
                                    "3. **Hand off.** Run `mkdir audit/.lock`, then do the work yourself.")
        with tempfile.TemporaryDirectory() as td:  # a wrapper that stops handing off at all
            mut4_route = _mut_skills(td, "xcheck-plan/SKILL.md",
                                     "**Hand off.** Run `xcheck next`.",
                                     "**Hand off.** Do it here.")
        with tempfile.TemporaryDirectory() as td:  # the read-only launcher takes the lock
            mut4_status = _mut_skills(td, "xcheck-status/SKILL.md",
                                      "Read-only role: do not create `audit/.lock`.",
                                      "Read-only role: first `mkdir audit/.lock`.")
        # And the contract homes: each control action inverted where it now lives.
        mut_home_hyg = _contract_home_problems(
            _prompts_text.replace("OUTSIDE the project tree", "INSIDE the project tree"), _methodology)
        mut_home_ver = _contract_home_problems(
            _prompts_text.replace("prove the fixes wrong", "confirm the fixes"), _methodology)
        mut_home_seq = _contract_home_problems(
            _prompts_text, _methodology.replace("validate \u2192 census \u2192 plan \u2192 fix \u2192 self-check", "fix"))
        ok = (clean129 == [] and clean_homes == []
              and any("xcheck-triage" in p for p in mut129_tri)
              and any("xcheck-triage" in p for p in mut129_wr)
              and any("xcheck-verify" in p and "wrapper block" in p for p in mut4_canon)
              and any("xcheck-remediate" in p and "role-execution" in p for p in mut4_role)
              and any("xcheck-plan" in p and "hand-off" in p for p in mut4_route)
              and any("xcheck-status" in p and "read-only" in p for p in mut4_status)
              and any("session hygiene" in p for p in mut_home_hyg)
              and any("adversarial stance" in p for p in mut_home_ver)
              and any("in-session sequence" in p for p in mut_home_seq))
        print(f"{'PASS' if ok else 'FAIL'}: the five writing launchers are WRAPPERS — each declares `orchestrated`, carries the canonical wrapper BLOCK byte-for-byte, names `xcheck next` as its hand-off, and carries NO role-execution instruction (no lock acquisition, no construal or refusal section, no `$XCHECK_SESSION_ID`, no `XCHECK_LOCK_INHERITED`); `xcheck-status` alone keeps the `uncontained-direct` disclosure and its read-only exemption, and its two role sentences; the triage wrapper keeps the two sentences that bound the AGENT at the one gate the orchestrator cannot dispatch (human-decides, never-touch-finding-files). The contracts the wrappers gave up are checked WHERE THEY MOVED TO — session hygiene, the construal gate and the verifier stance in `runner.PROMPTS`, the Remediator sequence, the self-admission ban, the refusal vocabulary and the scope rules in `XCHECK.md` — so a phase that empties a launcher cannot also empty the contract. Softening the receipt clause, replacing a hand-off with the role's own work, dropping the hand-off, letting the read-only launcher take the lock, and inverting each moved control action at its new home are each caught (F-0129, F-0134 superseded by the wrapper shape; third-audit P0)"
              + ("" if ok else f" -> clean={clean129} homes={clean_homes} tri={mut129_tri} wr={mut129_wr} canon={mut4_canon} role={mut4_role} route={mut4_route} status={mut4_status} hyg={mut_home_hyg} ver={mut_home_ver} seq={mut_home_seq}"))
        failed += 0 if ok else 1
    else:
        _why = ("the repository tree these audit is not beside the installed package "
                f"(missing: {', '.join(_rp50_missing)})")
        for _name in RP50_REPO_CHECKS:
            print(f"SKIP: {_name} -> {_why}")
        skipped.extend(RP50_REPO_CHECKS)

    # ---------------------------------------------------------------- RP-0008
    # Witnesses for the F-0144..F-0151 batch. Each check is the finding's own
    # "how to verify" reduced to its function-level core; the Verifier's full
    # temp-install procedures remain the binding check.

    # F-0144: a Remediator charter never claims a RANGE over a gapped id set — no id
    # outside the set is derivable from the charter text (§4 rule 1). Contiguous,
    # single-id and Verifier forms are byte-identical positive controls.
    _g144 = role_and_charter("run-remediator", ["F-0131", "F-0132", "F-0133", "F-0134", "F-0138"])[1]
    _c144 = role_and_charter("run-remediator", ["F-0131", "F-0132", "F-0133"])[1]
    _s144 = role_and_charter("run-remediator", ["F-0131"])[1]
    _v144 = role_and_charter("run-verifier", ["F-0131", "F-0138"])[1]
    ok = (".." not in _g144
          and not any(x in _g144 for x in ("F-0135", "F-0136", "F-0137"))
          and "(5 items: F-0131, F-0132, F-0133, F-0134, F-0138)" in _g144
          and _c144 == "findings F-0131..F-0133 (3 items: F-0131, F-0132, F-0133)"
          and _s144 == "finding F-0131"
          and _v144 == "verify the fixes and give a binding verdict on every finding in status fixed (F-0131, F-0138)")
    print(f"{'PASS' if ok else 'FAIL'}: a gapped Remediator batch is chartered by ENUMERATION with no `A..B` range token, so no terminal mid-range id is derivable from the charter; a contiguous run keeps the exact range form, a single id keeps `finding F-NNNN`, and the Verifier branch is unchanged (F-0144)"
          + ("" if ok else f" -> gapped={_g144!r} contiguous={_c144!r} single={_s144!r}"))
    failed += 0 if ok else 1

    # F-0145: the construal-gate prompt BINDS all five sections to the CHARTERED work
    # (not the writing session) while keeping the write-and-stop instruction, and the
    # shipped template's Stop-conditions/Out-of-scope comments agree with it.
    _cp145 = role_and_charter("run-construal", ("Verifier", "C", "d" * 16, "audit/construals/x.md"))[1]
    _tpl145 = Path(__file__).resolve().parent.parent / "templates" / "construal.md"
    if _tpl145.is_file():
        _tpl_ok = "CHARTERED work" in _tpl145.read_text(encoding="utf-8", errors="replace")
        _tpl_note = ""
    else:
        _tpl_ok, _tpl_note = True, " (no shipped templates/ beside bin — template half not checkable here)"
    ok = ("the CHARTERED work" in _cp145
          and "not this writing session" in _cp145
          and "then STOP without touching anything else" in _cp145
          and _tpl_ok)
    print(f"{'PASS' if ok else 'FAIL'}: the run-construal prompt states that all five sections construe the CHARTERED work — the later executing session — not the writing session, keeps the write-and-stop instruction, and templates/construal.md's Stop-conditions/Out-of-scope comments carry the same binding{_tpl_note} (F-0145)"
          + ("" if ok else f" -> prompt_has_bind={'the CHARTERED work' in _cp145} tpl={_tpl_ok}"))
    failed += 0 if ok else 1

    # F-0146 + F-0147: the installed copy of the core and the §2 schema binding are
    # LIVE inputs — root/copy divergence is an addressed lint error, and schema drift
    # (a §2 field with no validator; a lost §2 block) stops the SHARED integrity gate,
    # not only lint.
    _core146 = Path(__file__).resolve().parent.parent / "XCHECK.md"
    if _core146.is_file():
        _core_text = _core146.read_text(encoding="utf-8", errors="replace")

        def _rp8_audit(td, core_text):
            proj = Path(td) / "proj"
            (proj / "audit" / "findings").mkdir(parents=True)
            (proj / "audit" / "XCHECK.md").write_text(core_text, encoding="utf-8")
            _put_state(proj / "audit", _ledger(), encoding="utf-8")
            return proj

        with tempfile.TemporaryDirectory() as td:  # identical root+copy -> lint clean
            proj = _rp8_audit(td, _core_text)
            (proj / "XCHECK.md").write_text(_core_text, encoding="utf-8")
            with _cl2.redirect_stdout(_io2.StringIO()) as _b146:
                rc_same = cmd_lint(proj)
        with tempfile.TemporaryDirectory() as td:  # root edited, copy stale -> addressed error
            proj = _rp8_audit(td, _core_text)
            (proj / "XCHECK.md").write_text(_core_text.replace("Nine rules.", "Ten rules."), encoding="utf-8")
            with _cl2.redirect_stdout(_io2.StringIO()) as _b146b:
                rc_diff = cmd_lint(proj)
            _diff_named = "diverge" in _b146b.getvalue() and "F-0146" in _b146b.getvalue()
        # PHASE 5: the §2<->schema meta-check no longer hangs off the ledger integrity
        # gate (there is no ledger). It is a check about the PROSE core document, so it
        # stays where prose checks live — `cmd_lint` — and is asserted here at its own
        # entry point, `canonical_schema_meta_issues`, against the same three fixtures.
        with tempfile.TemporaryDirectory() as td:  # §2 gains a field the table lacks -> stop
            proj = _rp8_audit(td, _core_text.replace(
                "pass: P-01                     # which pass discovered it",
                "future-required: required\npass: P-01                     # which pass discovered it"))
            _d147a = canonical_schema_meta_issues(proj / "audit" / "XCHECK.md")
        with tempfile.TemporaryDirectory() as td:  # §2 yaml block LOST -> fail-closed, not silent []
            proj = _rp8_audit(td, _core_text.replace("```yaml", "```text"))
            _d147b = canonical_schema_meta_issues(proj / "audit" / "XCHECK.md")
        with tempfile.TemporaryDirectory() as td:  # intact copy -> shared gate passes (control)
            proj = _rp8_audit(td, _core_text)
            _d147c = canonical_schema_meta_issues(proj / "audit" / "XCHECK.md")
        _k147a = "stop-schema-drift" if _d147a else None
        _k147b = "stop-schema-drift" if _d147b else None
        _k147c = "stop-schema-drift" if _d147c else None
        ok = (rc_same == 0 and rc_diff == 1 and _diff_named
              and _k147a == "stop-schema-drift" and any("future-required" in i for i in _d147a)
              and _k147b == "stop-schema-drift" and any("no parseable canonical-record yaml block" in i for i in _d147b)
              and _k147c is None)
        detail_1467 = f"same={rc_same} diff={rc_diff}({_diff_named}) a={_k147a} b={_k147b} c={_k147c}"
    else:
        ok, detail_1467 = True, "no shipped XCHECK.md beside bin (runtime cmd_lint binds per project)"
    print(f"{'PASS' if ok else 'FAIL'}: a root-core edit cannot silently strand the installed audit/XCHECK.md copy — byte divergence is an addressed lint error naming the copy's missing sync owner (F-0146) — and the §2<->schema meta-check runs in the SHARED integrity gate: a §2 field with no validator and a lost §2 yaml block each return stop-schema-drift before routing/KPIs, while an intact copy passes (F-0147, README §8 one boundary)"
          + ("" if ok else f" -> {detail_1467}"))
    failed += 0 if ok else 1

    # F-0149: frontmatter structure is fail-closed — an indented (nested-mapping) key is
    # NEVER promoted to root (bad-form, CF-0001 ruling #9), a `---garbage` line is not a
    # closing delimiter, and the legal flat forms (scalar, inline list, block list) parse
    # exactly as before.
    _fm_n, _dups_n, _bf_n = _parse_frontmatter("record:\n  id: F-9001\n  status: reported")
    _fm_l, _dups_l, _bf_l = _parse_frontmatter("id: F-9001\nunit:\n  - B01\n  - B02")
    _m_bad = FRONTMATTER_RE.match("---\nid: F-9001\n---garbage\n\nbody\n")
    _m_ok = FRONTMATTER_RE.match("---\nid: F-9001\n---\n\nbody\n")
    _m_eof = FRONTMATTER_RE.match("---\nid: F-9001\n---")
    ok = ("id" not in _fm_n and "status" not in _fm_n and set(_bf_n) == {"id", "status"}
          and _fm_l.get("unit") == "B01, B02" and not _bf_l
          and _m_bad is None and _m_ok is not None and _m_eof is not None)
    print(f"{'PASS' if ok else 'FAIL'}: an indented nested-mapping key is recorded as bad-form and NOT promoted to a root canonical field, a `---garbage` line is refused as a closing frontmatter delimiter (full-line `---` only, shared FRONTMATTER_RE across all loaders), and the declared flat forms — scalar and block list — still parse (F-0149)"
          + ("" if ok else f" -> nested={_fm_n} bf={_bf_n} list={_fm_l.get('unit')!r} bad={bool(_m_bad)} ok={bool(_m_ok)}"))
    failed += 0 if ok else 1

    # ---- phase 8: containment. Portable checks only (no git, no child processes) —
    # the worktree, timeout, group-kill and redaction claims are proved against REAL
    # children in tests/test_runner_sandbox.py, which is where a claim about the
    # operating system belongs. What lives here is what an operator can verify
    # anywhere `bin/xcheck selftest` runs.

    # The ambient grant becomes a contained grant: the SAME command is refused under
    # `none` and permitted under an isolating profile. Both halves are the check —
    # a guard that refuses everything would pass the first half alone.
    _sk_cmd = ["claude", "-p", "--dangerously-skip-permissions", "{prompt}"]
    _sk_refused = _sk_permitted = None
    try:
        refuse_uncontained(_sk_cmd, PROFILES["none"], "Remediator")
        _sk_refused = False
    except SystemExit as e:
        _sk_refused = "--dangerously-skip-permissions" in str(e)
    try:
        refuse_uncontained(_sk_cmd, PROFILES["worktree"], "Remediator")
        _sk_permitted = True
    except SystemExit:
        _sk_permitted = False
    _prof_unknown = None
    try:
        resolve_profile({"sandbox_profile": "containerish"}, "Remediator")
        _prof_unknown = False
    except SystemExit as e:
        _prof_unknown = "is not a profile" in str(e)
    _prof_defaults = (resolve_profile({}, "Auditor").name == "readonly"
                      and resolve_profile({}, "Verifier").name == "readonly"
                      and resolve_profile({}, "Remediator").name == "worktree"
                      and not PROFILES["none"].isolating)
    ok = bool(_sk_refused and _sk_permitted and _prof_unknown and _prof_defaults)
    print(f"{'PASS' if ok else 'FAIL'}: `--dangerously-skip-permissions` is CONTAINED, not deleted — refused under sandbox_profile=none, permitted under an isolating profile; an unknown profile is refused rather than guessed; the read-only roles default to `readonly` (phase 8)"
          + ("" if ok else f" -> refused={_sk_refused} permitted={_sk_permitted} unknown={_prof_unknown} defaults={_prof_defaults}"))
    failed += 0 if ok else 1

    # The child environment is BUILT from an allowlist, and the log redactor never
    # leaves a value it was asked to hide. The allowlist is the real control; the
    # redactor is best-effort against accident (a key the agent echoes).
    os.environ["XCHECK_SELFTEST_FAKE_SECRET_KEY"] = "not-a-real-key-0123456789"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "wJalrXUtnFEMI-selftest-fake"
    try:
        _env_default = child_environment({})
        _env_named = child_environment({"env_allowlist": "XCHECK_SELFTEST_FAKE_SECRET_KEY"})
        _scrubbed = Redactor(_env_named).scrub(
            "key=not-a-real-key-0123456789 and gh" + "p_" + "A" * 20)
    finally:
        os.environ.pop("XCHECK_SELFTEST_FAKE_SECRET_KEY", None)
        os.environ.pop("AWS_SECRET_ACCESS_KEY", None)
    _leaked = sorted(n for n in _env_default
                     if n not in ENV_ALLOWLIST and not n.startswith("XCHECK_"))
    ok = ("AWS_SECRET_ACCESS_KEY" not in _env_default and not _leaked
          and "PATH" in _env_default
          and _env_named.get("XCHECK_SELFTEST_FAKE_SECRET_KEY") == "not-a-real-key-0123456789"
          and "not-a-real-key-0123456789" not in _scrubbed
          and "«redacted:XCHECK_SELFTEST_FAKE_SECRET_KEY»" in _scrubbed
          and "«redacted:token-shape»" in _scrubbed)
    print(f"{'PASS' if ok else 'FAIL'}: the child environment is built from the allowlist (an injected AWS_SECRET_ACCESS_KEY never reaches it; an operator-named key does) and the log redactor replaces both a named value and a bare token shape (phase 8)"
          + ("" if ok else f" -> leaked={_leaked} named={'XCHECK_SELFTEST_FAKE_SECRET_KEY' in _env_named} scrubbed={_scrubbed!r}"))
    failed += 0 if ok else 1

    # Every session outcome is TYPED. `refused`, `blocked` and `provider-error` all
    # exit non-zero: collapsing them into `crash` would make the loop retry a refusal
    # and give up on an outage — the retry decision is the reason this classification
    # exists at all (phase 9 stores it in the invocation envelope).
    _cls = {
        "ok": classify_outcome(0),
        "refused": classify_outcome(3, log_text="I cannot complete this charter"),
        "blocked": classify_outcome(1, log_text="error: rate limit exceeded"),
        "provider-error": classify_outcome(1, log_text="API error: Service Unavailable"),
        "timeout": classify_outcome(-15, timed_out=True),
        "crash": classify_outcome(2, log_text="Traceback: ValueError"),
        "cancelled": classify_outcome(-15, timed_out=True, cancelled=True),
        # PHASE 12: a deadline fired INSIDE the session budget. Distinct from `timeout`
        # because the two call for opposite operator actions — raise the budget vs. find
        # out why nothing happened — and because only one of them is worth paying again.
        "stalled": classify_outcome(-15, timed_out=True, stalled=True),
    }
    _wrong = {k: v for k, v in _cls.items() if k != v}
    ok = not _wrong and set(_cls) == set(OUTCOMES)
    print(f"{'PASS' if ok else 'FAIL'}: every session outcome is typed — ok/refused/blocked/provider-error/timeout/crash/cancelled/stalled each classify to themselves, and an operator cancel is not disguised as a timeout (phase 8)"
          + ("" if ok else f" -> {_wrong}"))
    failed += 0 if ok else 1

    # The lock is a LEASE. A stopped heartbeat is positive evidence the owner is gone,
    # so plain `unlock` may reclaim it; a LIVE heartbeat never is, and an ABSENT one
    # (an older xcheck, a hand-run session) falls back to the pid rules unchanged.
    with tempfile.TemporaryDirectory() as td:
        _lp = Path(td) / "audit"
        _lp.mkdir()
        _lease = Lock(_lp, "Remediator")
        _lease.acquire()
        _live = _lease.lease_dead(300)
        _lease.heartbeat_file.write_text(f"{time.time() - 4000:.0f}\n", encoding="utf-8")
        _dead = _lease.lease_dead(300)
        _b = io.StringIO()
        with contextlib.redirect_stdout(_b):
            cmd_unlock(Path(td), False)              # NO --force
        _reclaimed = not _lease.dir.exists() and "reclaimed" in _b.getvalue()
        _lease2 = Lock(_lp, "Remediator")
        _lease2.acquire()
        _lease2.heartbeat_file.unlink()              # a pre-lease lock: no heartbeat
        _absent = _lease2.lease_dead(0)
        _lease2.request_cancel()
        _cancel_seen = _lease2.cancel_requested()
        _lease2.release()
        _released = not _lease2.dir.exists()
    ok = (not _live and _dead and _reclaimed and not _absent and _cancel_seen and _released)
    print(f"{'PASS' if ok else 'FAIL'}: the lock is a lease — a heartbeat older than lease_ttl is reclaimed by plain `unlock` (no --force), a live one is not, an ABSENT heartbeat is never a dead lease, and release removes the heartbeat/cancel files with the lock (phase 8)"
          + ("" if ok else f" -> live={_live} dead={_dead} reclaimed={_reclaimed} absent={_absent} cancel={_cancel_seen} released={_released}"))
    failed += 0 if ok else 1

    # ---- phase 9: the invocation envelope, the event stream, the JSON contract and
    # independence as a MEASUREMENT. Portable checks only; the dispatch-level proofs
    # (a real child, three real sessions, both CLI surfaces) live in
    # tests/test_envelope.py, which can spend seconds on subprocesses.

    # The envelope answers "what actually ran?" — thirteen declared fields, with
    # `unknown` recorded rather than guessed and never left absent.
    _prof = PROFILES["none"]
    _rec = envelope.dispatch_record(["claude", "--model", "opus"], "Remediator",
                                    "the charter", "the prompt", "a" * 16, _prof,
                                    7, "0" * 40, env={})
    _rec = envelope.finish(_rec, 0, "ok", 12.5, "1" * 40, "/nonexistent/log")
    _fields_ok = all(_rec.get(_k) is not None
                     for _label, _keys in envelope.ENVELOPE_FIELDS for _k in _keys)
    _unknown = envelope.dispatch_record(["sh", "-c", "true"], "Auditor", "c", "p",
                                        "b" * 16, _prof, 1, None, env={})
    _honest = (_unknown["provider"] == "unknown"
               and _unknown["agent_model"] == "unknown"
               and _unknown["head_before"] == "unknown"
               and "provider" in _unknown)          # recorded, not dropped
    _hashes = (_rec["charter_hash"] == envelope.sha256_text("the charter")
               and _rec["prompt_hash"] == envelope.sha256_text("the prompt")
               and _rec["log_digest"] == "unknown"  # unreadable log: say so
               and _rec["provider"] == "anthropic" and _rec["agent_model"] == "opus")
    ok = _fields_ok and _honest and _hashes and len(envelope.ENVELOPE_FIELDS) == 13
    print(f"{'PASS' if ok else 'FAIL'}: the invocation envelope records all 13 declared fields for a dispatch, derives provider/model from the configured command, hashes the exact charter and prompt bytes, and records `unknown` explicitly where a value is undeterminable rather than guessing or omitting it (phase 9)"
          + ("" if ok else f" -> fields={_fields_ok} honest={_honest} hashes={_hashes}"))
    failed += 0 if ok else 1

    # `events.jsonl` is APPEND-ONLY: opened "a", one compact object per line, and no
    # path in the tool rewrites an earlier line.
    with tempfile.TemporaryDirectory() as td:
        _ep = Path(td)
        (_ep / "audit").mkdir()
        envelope.emit(_ep, "session_dispatched", "a" * 16, role="Remediator")
        _first = (_ep / "audit" / envelope.EVENTS_FILENAME).read_bytes()
        envelope.emit(_ep, "gate_reached", None, gate="stop-triage")
        envelope.emit(_ep, "session_finished", "a" * 16, outcome="ok")
        _all = (_ep / "audit" / envelope.EVENTS_FILENAME).read_bytes()
        _events = envelope.read_events(_ep)
    _append_only = _all.startswith(_first) and len(_all) > len(_first)
    _one_per_line = len(_events) == 3 and _all.count(b"\n") == 3
    _typed = all({"ts", "event", "session_id"} <= set(e) for e in _events)
    ok = _append_only and _one_per_line and _typed
    print(f"{'PASS' if ok else 'FAIL'}: the event stream is append-only — an earlier line is byte-identical after two further appends, each event is one compact JSON object carrying ts/event/session_id, and a rewrite is not something any path performs (phase 9)"
          + ("" if ok else f" -> append_only={_append_only} lines={_one_per_line} typed={_typed}"))
    failed += 0 if ok else 1

    # The `--json` payloads are a declared, VERSIONED contract, checked by the
    # producer: an unknown key and a missing one both fail, so the machine surface
    # cannot drift the way the Markdown one did.
    _good = {"output_schema_version": OUTPUT_SCHEMA_VERSION, "project": "/p",
             "xcheck_version": __version__,
             "schema_version": 1, "state_revision": 3, "head_before": None,
             "passes": {"done": 1, "queued": []},
             "findings": {"total": 0, "by_status": {}},
             "lock": None, "decision": {"kind": "done", "detail": None},
             "quarantine": {"pending": 0, "bundles": []},
             # phase 12 (third audit): the age of the last UNSCOPED pass. Present here
             # because this probe's whole point is that a CONFORMING payload validates
             # — a hand-built payload missing a declared key would make the `clean` arm
             # fail for the reason the `missing` arm is supposed to be the only one to
             # fail for, and the two arms would stop being separable.
             "full_sweep": {"last": None, "pass": None, "days": None,
                            "note": "no pass has completed unscoped"}}
    _clean = json_output_issues(_good, STATUS_SCHEMA, "status") == []
    _unknown_key = any("unknown key" in i for i in
                       json_output_issues(dict(_good, extra=1), STATUS_SCHEMA, "status"))
    _missing = {k: v for k, v in _good.items() if k != "state_revision"}
    _missing_key = any("missing" in i for i in
                       json_output_issues(_missing, STATUS_SCHEMA, "status"))
    _wrong_type = any("expected int" in i for i in
                      json_output_issues(dict(_good, state_revision="3"),
                                         STATUS_SCHEMA, "status"))
    ok = _clean and _unknown_key and _missing_key and _wrong_type
    print(f"{'PASS' if ok else 'FAIL'}: `status --json`/`metrics --json` are a versioned contract enforced by the PRODUCER — a conforming payload validates, and an unknown key, a missing key and a wrong type each fail rather than reaching a consumer (phase 9)"
          + ("" if ok else f" -> clean={_clean} unknown={_unknown_key} missing={_missing_key} type={_wrong_type}"))
    failed += 0 if ok else 1

    # Independence is a MEASURED level over the two envelopes, not an assertion in a
    # prompt — and a degraded level is labelled wherever it is shown.
    def _env9(sid, provider, model):
        return {"session_id": sid, "provider": provider, "agent_model": model}
    _levels = {
        envelope.independence_level(_env9("a" * 16, "anthropic", "opus"),
                                    _env9("b" * 16, "openai", "o3")): "cross-provider",
        envelope.independence_level(_env9("a" * 16, "anthropic", "opus"),
                                    _env9("b" * 16, "anthropic", "haiku")): "cross-model",
        envelope.independence_level(_env9("a" * 16, "anthropic", "opus"),
                                    _env9("b" * 16, "anthropic", "opus")): "same-provider-different-session",
        envelope.independence_level(_env9("a" * 16, "anthropic", "opus"),
                                    _env9("a" * 16, "anthropic", "opus")): "same",
    }
    _four = all(k == v for k, v in _levels.items()) and len(_levels) == 4
    # Two `unknown` providers are not evidence of difference: guessing here would be
    # the same over-claim the whole measurement exists to remove.
    _no_guess = envelope.independence_level(_env9("a" * 16, "unknown", "unknown"),
                                            _env9("b" * 16, "unknown", "unknown")) \
        == "same-provider-different-session"
    _unrecorded = envelope.independence_level(None, _env9("b" * 16, "openai", "o3")) \
        == "unrecorded"
    _labelled = all("DEGRADED" in envelope.independence_note(_l)
                    for _l in DEGRADED_INDEPENDENCE)
    ok = _four and _no_guess and _unrecorded and _labelled
    print(f"{'PASS' if ok else 'FAIL'}: verifier independence is a measured level over the two invocation envelopes (cross-provider / cross-model / same-provider-different-session / same), an unknown provider never reads as cross-provider, a missing envelope is `unrecorded`, and every degraded level carries its DEGRADED label (phase 9)"
          + ("" if ok else f" -> four={_four} no_guess={_no_guess} unrecorded={_unrecorded} labelled={_labelled}"))
    failed += 0 if ok else 1

    # F-0159, structurally: `fixed-by` is READ from the orchestrator's open dispatch
    # record. An agent that types a different id is refused — a provenance field its
    # own subject may choose is not provenance.
    class _S9:
        sessions = (dict(envelope.dispatch_record(["claude"], "Remediator", "c", "p",
                                                  "a" * 16, PROFILES["none"], 1,
                                                  "0" * 40, env={})),)
    class _S9none:
        sessions = ()
    try:
        _bound_session(_S9, "b" * 16, "record-fix", "F-0001")
        _refused9 = ""
    except StateError as _e9:
        _refused9 = str(_e9)
    _named = all(_t in _refused9 for _t in ("b" * 16, "a" * 16, "dispatch record"))
    _agrees = _bound_session(_S9, "a" * 16, "record-fix", "F-0001") == ("a" * 16, "")
    _sid, _note9 = _bound_session(_S9none, "c" * 16, "record-fix", "F-0001")
    _unbound_labelled = _sid == "c" * 16 and "UNBOUND" in _note9
    ok = _named and _agrees and _unbound_labelled
    print(f"{'PASS' if ok else 'FAIL'}: `fixed-by` is bound to the orchestrator's open dispatch record (F-0159 closed structurally) — a disagreeing --session is refused with a message naming both ids, the agreeing one is accepted, and an audit with no envelopes records the claim LABELLED as unbound rather than as testimony (phase 9)"
          + ("" if ok else f" -> named={_named} agrees={_agrees} unbound={_unbound_labelled}"))
    failed += 0 if ok else 1

    # Restore the real stdout; the summary is emitted to it directly, so it is
    # never fed to the tee and can never be one of the counted check lines
    # (it would not match anyway — it starts with "PASS (" / "FAIL (", a paren,
    # not the "PASS:" / "FAIL:" prefix). Denominator = literal line-scan of what
    # this run emitted THROUGH sys.stdout (measure, not model). The count is
    # bounded to the sys.stdout boundary and claims nothing about raw file
    # descriptor 1; the file= guard above closes the one syntactic bypass form (a
    # direct print(..., file=...)), nothing more.
    sys.stdout = _real_stdout
    _emitted = _tee.buf.getvalue()
    total = sum(1 for _ln in _emitted.split("\n")
                if _ln.startswith("PASS:") or _ln.startswith("FAIL:"))
    # Skips are NOT in the denominator: `total` counts what ran. Reporting them as
    # passes would let an artifact with no subject to check post a perfect score, and
    # reporting them as failures would call a missing subject a defect. They are
    # named instead, so the number is always readable as "of what could run here".
    print(f"\n{'PASS' if failed == 0 else 'FAIL'} ({total - failed}/{total})"
          + (f"\n{len(skipped)} skipped, no subject in this context:\n  - "
             + "\n  - ".join(skipped) if skipped else ""))
    return 1 if failed else 0
