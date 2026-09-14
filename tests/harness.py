"""Import the orchestrator and build throwaway audit fixtures.

Before the phase-3 split this had to load `bin/xcheck` by path through
`SourceFileLoader` (the file has no `.py` suffix, and the suffix is what picks a
loader, so `spec_from_file_location` alone returned None). The package split
retired that: `xcheck` is an ordinary package beside `bin/`, so a plain import
works once the repo root is on `sys.path`.

Every fixture lives in a system temp directory (`tempfile.mkdtemp`), never in
the project tree: the courier commits the project tree, so an in-tree fixture
would be committed as audit material (F-0120).
"""

import contextlib
import hashlib
import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
XCHECK = REPO / "bin" / "xcheck"

#: This checkout's own audit corpus — 133 findings, 159 session records, 1,043 events at
#: 0.9.5 — and whether it is here at all.
#:
#: A large family of tests reads it: the live-corpus pins, the OKF projections, the
#: budget and metrics recounts. They are DEVELOPMENT-REPOSITORY tests. A released tree
#: carries the package and its tests but no `audit/` — `install.sh` creates one — so in
#: any fresh clone their subject is simply absent.
#:
#: Erroring there was the defect: 105 of the exported battery's 112 remaining failures
#: were this one missing file, which made `bash ci/run-checks.sh` in the published tree
#: look like a broken release rather than a battery asking a question that cannot be
#: asked. A check that cannot apply says so and steps aside; it does not fail closed.
#:
#: The guard is narrow ON PURPOSE. It reads one path. Where the corpus IS present every
#: gated test runs exactly as before, and `TheCorpusGateDoesNotDisarmTheDevelopmentTree`
#: in tests/test_live_corpus_gate.py asserts that nothing skips here.
LIVE_AUDIT = REPO / "audit"
HAVE_LIVE_CORPUS = (LIVE_AUDIT / "state.json").is_file()

NO_CORPUS_REASON = (
    "this checkout carries no audit/state.json, so there is no live corpus to read. "
    "That is the exported tree, which ships the package and its tests but not this "
    "repository's own audit history; run `install.sh` to start one, or run this test "
    "in the development repository."
)


#: Whether this checkout carries the INTERNAL `docs/` tree.
#:
#: `docs/` is internal by policy and the export names the files it ships one by one —
#: two of them at 0.9.5 — so a tree holding the audit responses is the development
#: repository. Named here, once, for the same reason the corpus flag is: a module that
#: decides this for itself is a second spelling of a question that has one.
HAVE_INTERNAL_DOCS = (REPO / "docs" / "audit-response-6.md").is_file()


def require_live_corpus():
    """Raise `SkipTest` when there is no corpus — for a HELPER called from a test.

    The decorator above cannot reach a module-level helper like `live_state()`: the
    class that calls it holds no corpus read of its own, so nothing marks it. Raising
    from inside the helper skips whichever test reached it, with the same reason.
    """
    import unittest as _ut
    if not HAVE_LIVE_CORPUS:
        raise _ut.SkipTest(NO_CORPUS_REASON)


def needs_repo_file(rel, why):
    """Skip a test or class when this checkout does not carry `rel`.

    The corpus gate one rung down, for a subject that is a single document rather than
    a whole audit tree. `docs/` is internal by default and the export names the two
    files it carries one by one, so a test that GRADES an internal document — a
    pre-registration, a measurement protocol — has no subject in the exported tree.
    `why` names what the file is, so the skip line reads as an answer and not as a hole.
    """
    import unittest as _ut
    return _ut.skipUnless((REPO / rel).is_file(),
                          f"this checkout carries no {rel} — {why}")


def needs_commit(ref, why):
    """Skip a test or class when `ref` is not in this clone's history.

    The exported tree is a SEPARATE repository with its own history: a commit id pinned
    in a test — a phase baseline, the pre-split reference — is simply not there, and git
    answers `fatal: bad revision`, which reads as a broken check rather than an
    inapplicable one. `ci/check-split.py` decides the same way about the same problem.
    """
    import unittest as _ut
    probe = subprocess.run(["git", "cat-file", "-e", f"{ref}^{{commit}}"], cwd=str(REPO),
                           capture_output=True, text=True, timeout=120)
    return _ut.skipUnless(probe.returncode == 0,
                          f"commit {ref} is not in this clone's history — {why}")


def needs_live_corpus(obj):
    """Skip a test or class when this checkout has no development corpus.

    Usable as a decorator on either. Kept as one predicate rather than a per-module
    `os.path.exists` so a sixteenth caller cannot invent a seventeenth spelling of the
    same question — and so the gate has ONE name a counter-assertion can find.
    """
    import unittest as _ut
    return _ut.skipUnless(HAVE_LIVE_CORPUS, NO_CORPUS_REASON)(obj)

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def xcheck_module():
    """`xcheck.cli` — the module holding `main()` and the six commands."""
    return importlib.import_module("xcheck.cli")


def ci_module(name):
    """Load `ci/<name>.py` BY PATH.

    Never `sys.path.insert(REPO/"ci"); import <name>`. `ci/preflight.py` derives the
    project's third-party dependencies from the imports in its check scripts and tests,
    and a bare import of a name that lives in a different directory reads as a PyPI
    distribution — the preflight then demands `pip install tiers`. That is the check
    working, not a quirk to route around, so the tests use the shape that does not trip
    it. Same reason `tests/test_ci_contract.py` loads `preflight` this way.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"_ci_{name}", REPO / "ci" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def xcheck_submodule(name):
    """Any package module by short name, e.g. `legacy_md`, `courier`, `decision`."""
    return importlib.import_module(f"xcheck.{name}")


# --------------------------------------------------------------------------
# fixture pieces
# --------------------------------------------------------------------------

NORMS = """## 1. Norms catalog

| id | source | scope |
|---|---|---|
| N1 | README.md | what the tool claims it does |
"""

DIMENSIONS = """## 2. Dimensions

| key | what it catches | norm source |
|---|---|---|
| invariants | violations of documented project invariants | N1 |
"""

UNITMAP = """## 3. Unit map

| unit | material | approx. size | responsibility |
|---|---|---|---|
| U01 | src/core.py | 0.4 kloc | core |
"""

QUEUE_ENTRY = ("- [{mark}] P-{n} — invariants × U01: check the documented invariants "
               "hold in the unit; stop: 15 findings or the unit is exhausted")

LIMITS = """## 5. Limits

max_findings_per_pass: 15
remediation_batch_size: 8
class_threshold: 3
reopen_limit: 2
"""

LEDGER_HEAD = ("| id | title | severity | status | next | updated |\n"
               "|---|---|---|---|---|---|\n")


def queue_entry(n, checked=False):
    return QUEUE_ENTRY.format(mark="x" if checked else " ", n=f"{n:02d}")


def audit_md(queue_body=None, limits=LIMITS, norms=NORMS, dimensions=DIMENSIONS,
             unitmap=UNITMAP, tail=""):
    """A complete, plan-complete AUDIT.md. Any section can be replaced to poison it."""
    if queue_body is None:
        queue_body = queue_entry(1)
    return (f"# AUDIT — fixture (codebase)\n\n{norms}\n{dimensions}\n{unitmap}\n"
            f"## 4. Pass queue\n\n{queue_body}\n\n{limits}\n{tail}")


def finding(fid="F-0001", status="reported", extra="", body="\nEvidence body.\n"):
    return (f"---\nid: {fid}\npass: P-01\nupdated: 2026-08-14\ntitle: a finding\n"
            f"severity: major\ndimension: invariants\nunit: U01\nstatus: {status}\n"
            f"attempts: 0\n{extra}---\n{body}")


# The §5 status -> owning actor map, as `lint` enforces it. Fixtures default to
# the right owner so a fixture is lint-clean unless a test poisons it on purpose.
_NEXT_OWNER = {"reported": "Triage", "accepted": "Remediator", "validated": "Remediator",
               "planned": "Remediator", "reopened": "Remediator", "fixed": "Verifier",
               "disputed": "Auditor", "deferred": "Triage"}


def ledger(rows=()):
    """rows: iterable of (id, title, severity, status) or (..., status, next)."""
    out = LEDGER_HEAD
    for r in rows:
        fid, title, sev, status = r[:4]
        nxt = r[4] if len(r) > 4 else _NEXT_OWNER.get(status, "—")
        out += f"| {fid} | {title} | {sev} | {status} | {nxt} | 2026-08-14 |\n"
    return out


COVERAGE_BODY = """
## Coverage

**COVERED:**
- the whole unit, read line by line

**NOT COVERED:**
- nothing was skipped
"""


def pass_report(pid="P-01", status="done", body=COVERAGE_BODY, units="[U01]"):
    """A canonical pass record: the exact `_PASS_RECORD_KEYS` set, values bound to
    the queue charter, and both coverage markers in its own body (F-0154)."""
    return (f"---\nid: {pid}\ndimension: invariants\nunits: {units}\nstatus: {status}\n"
            f"findings: []\nupdated: 2026-08-14\n---\n{body}")


def _classified(body, policy):
    """A profile body with `trust_level` DECLARED, unless the caller declared it already.

    The shipped template ships the key with an empty value on purpose: the product
    refuses to dispatch until an operator classifies the material, and a default would
    delete the feature. A fixture project is synthetic and trusted by construction, so
    the fixture makes that declaration explicitly. Done here, by VALUE rather than by
    identity, because `configure()` hands this a rebuilt copy of the template — a
    check for "is this the template object" silently missed every test that configures
    anything. `tests/test_trust_level.py` is where the unset case is exercised.
    """
    lines, seen = [], False
    for line in body.splitlines():
        k, eq, v = line.partition("=")
        if eq and not line.startswith("#") and k.strip() == policy.TRUST_KEY:
            seen = True
            line = f"{policy.TRUST_KEY}={v.strip() or policy.TRUSTED}"
        lines.append(line)
    if not seen:
        lines.append(f"{policy.TRUST_KEY}={policy.TRUSTED}")
    return "\n".join(lines) + "\n"


class LegacyFixture:
    """A throwaway project with a MARKDOWN `audit/` tree, in a system temp directory.

    PHASE 5: this is no longer the shape the tool reads. It survives for exactly two
    jobs: the eight consumer-path bypass tests, whose poison only exists in Markdown,
    and phase 6's migration tests, which need a legacy tree to convert. Nothing that
    tests current behaviour should build one — use `Fixture`.
    """

    def __init__(self, audit_text=None, ledger_text=None, findings=None,
                 passes=None, conf=None):
        self.root = Path(tempfile.mkdtemp(prefix="xcheck-test-"))
        self.profile_dir = None
        self.audit = self.root / "audit"
        for sub in ("findings", "plans", "passes"):
            (self.audit / sub).mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / "XCHECK.md", self.audit / "XCHECK.md")
        self.write("AUDIT.md", audit_md() if audit_text is None else audit_text)
        self.write("LEDGER.md", ledger() if ledger_text is None else ledger_text)
        for name, text in (findings or {}).items():
            self.write(f"findings/{name}", text)
        for name, text in (passes or {}).items():
            self.write(f"passes/{name}", text)
        self.write_operator_profile()
        if conf:
            self.configure(conf)

    def write_operator_profile(self, text=None):
        """The policy half, OUTSIDE the audit tree the fixture stands for.

        Since the policy split a project's conf carries no role command, no containment
        profile and no limits — those come from the operator's own file, and `load_conf`
        refuses to read them out of the subject. A fixture with no profile therefore
        gets `orchestrator.conf lacks auditor_cmd`, which is the correct answer to a
        machine whose operator has configured nothing and the wrong answer to a test
        about anything else. It goes in a directory of its OWN, outside the fixture's
        project entirely — not merely outside `audit/`. The product's claim is that the
        orchestrator opens no file under the project but the audit tree, and a profile
        at the project root would break that claim in the fixture while leaving it true
        in the product: the test would then be measuring the harness."""
        policy = xcheck_submodule("policy")
        if getattr(self, "profile_dir", None) is None:
            self.profile_dir = Path(tempfile.mkdtemp(prefix="xcheck-operator-"))
        self.profile = self.profile_dir / "operator.conf"
        body = policy.OPERATOR_PROFILE_TEMPLATE if text is None else text
        self.profile.write_text(_classified(body, policy), encoding="utf-8")
        return self.profile

    def configure(self, text):
        """Apply a conf text to the fixture, each key going to the file that owns it.

        Tests write "the configuration for this case" as one block; since the policy
        split that block spans two files, and putting a policy key in the project half
        is exactly what `load_conf` refuses. Routing by `policy.classify` keeps the
        tests written the way they read while the fixture obeys the same rule the
        product enforces."""
        util = xcheck_submodule("util")
        policy = xcheck_submodule("policy")
        project, profile = [], list(policy.OPERATOR_PROFILE_TEMPLATE.splitlines())
        for k, v in util.conf_pairs(text).items():
            if policy.classify(k) == "policy":
                profile = [ln for ln in profile
                           if ln.partition("=")[0].strip() != k] + [f"{k}={v}"]
            else:
                project.append(f"{k}={v}")
        self.write("orchestrator.conf", "\n".join(project) + "\n")
        self.write_operator_profile("\n".join(profile) + "\n")

    @contextlib.contextmanager
    def profile_env(self):
        """Point the ENVIRONMENT at this fixture's operator profile for the block.

        `load_conf(profile=...)` covers a direct call, but the product re-reads its
        configuration under the lock with no argument to pass — the environment is the
        channel a real operator uses, so a test that drives `cli.cmd_next` in-process
        has to use it too. Restored on exit: a fixture that leaked this would point the
        next test at a deleted directory."""
        key = xcheck_submodule("policy").OPERATOR_PROFILE_ENV
        saved = os.environ.get(key)
        os.environ[key] = str(self.profile)
        try:
            yield self
        finally:
            if saved is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = saved

    def env(self, base=None):
        """`base` (default `os.environ`) plus the pointer to this fixture's profile."""
        e = dict(os.environ if base is None else base)
        e[xcheck_submodule("policy").OPERATOR_PROFILE_ENV] = str(self.profile)
        return e

    def write(self, rel, text):
        p = self.audit / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)
        if getattr(self, "profile_dir", None) is not None:
            shutil.rmtree(self.profile_dir, ignore_errors=True)

    # -- running the real CLI ---------------------------------------------

    def run(self, *args):
        """Run the CLI in-process. Returns (exit_code, stdout+SystemExit message).

        In-process so a failure shows a real traceback, and so the 11 600-line
        module is parsed once rather than once per assertion.
        """
        mod = xcheck_module()
        buf = io.StringIO()
        argv = ["xcheck", "--project", str(self.root), *args]
        # In-process, so the environment IS the channel — set for the duration of this
        # one call and restored after, rather than mutated for the whole test session.
        try:
            with self.profile_env(), contextlib.redirect_stdout(buf):
                code = mod.main(argv)
        except SystemExit as e:
            # An addressed refusal: `raise SystemExit("message")` is how this CLI
            # reports a bad state, so the message is part of the observable outcome.
            if isinstance(e.code, int):
                return e.code, buf.getvalue()
            return 1, buf.getvalue() + str(e.code)
        return code or 0, buf.getvalue()

    def run_subprocess(self, *args):
        """Run the CLI as a real child process — the exit code a shell/CI sees."""
        p = subprocess.run(
            [sys.executable, str(XCHECK), "--project", str(self.root), *args],
            capture_output=True, text=True, timeout=120, env=self.env())
        return p.returncode, p.stdout + p.stderr

    def decision(self, *args):
        """What `next --dry-run` decided: (exit code, output)."""
        return self.run("next", "--dry-run", *args)


# --------------------------------------------------------------------------
# state.json fixtures — the shape every consumer actually reads (phase 5)
# --------------------------------------------------------------------------

CATALOGS = {
    "norms": [{"id": "N1", "source": "README.md", "scope": "what the tool claims"}],
    "dimensions": [{"key": "invariants", "catches": "broken invariants",
                    "norms": ["N1"]}],
    "units": [{"id": "U01", "material": "src/core.py", "size": "0.4 kloc",
               "responsibility": "core"}],
}

LIMITS_JSON = {"max_findings_per_pass": 15, "remediation_batch_size": 8,
               "class_threshold": 3, "reopen_limit": 2}


def queue_pass(pid="P-01", done=False, coverage=True):
    q = {"id": pid, "dimension": "invariants", "units": ["U01"],
         "charter": "check the documented invariants hold in the unit",
         "stop": "15 findings or the unit is exhausted", "done": done}
    if done and coverage:
        q["coverage"] = {"report_path": f"passes/{pid}-report.md", "findings": [],
                         "updated": "2026-08-14", "status": "done"}
    return q


def finding_record(fid="F-0001", status="reported", **over):
    rec = {"id": fid, "title": "a finding", "severity": "major", "status": status,
           "dimension": "invariants", "unit": ["U01"], "pass": "P-01", "attempts": 0,
           "updated": "2026-08-14", "body_path": f"findings/{fid}.md"}
    rec.update(over)
    return rec


def state_doc(findings=(), class_findings=(), queue=None, limits=None,
              construals=(), plans=(), catalogs=None, **over):
    """A complete, valid state document. Any part can be replaced to poison it."""
    doc = {
        "schema_version": 1, "state_revision": 1, "generated_by": "tests",
        "head_before": "0" * 40,
        "findings": [dict(f) for f in findings],
        "class_findings": [dict(c) for c in class_findings],
        "queue": [dict(q) for q in (queue if queue is not None else [queue_pass()])],
        "plans": [dict(p) for p in plans],
        "construals": [dict(c) for c in construals],
        "sessions": [],
        "limits": dict(LIMITS_JSON if limits is None else limits),
        "catalogs": dict(CATALOGS if catalogs is None else catalogs),
    }
    doc.update(over)
    return doc


def construal_record(role="Remediator", charter="finding F-0001", status="proposed",
                     session="1111111111111111", admitted_by="human:operator"):
    """One §4 rule 9 construal record. The key is DERIVED from (role, charter) — a
    record keyed on different charter text is a record about different work, so the
    fixture must never invent the key by hand."""
    key = xcheck_submodule("util").construal_key(role, charter)
    rec = {"key": key, "role": role, "charter": charter, "session": session,
           "status": status, "created": "2026-08-14", "body_path": f"construals/{key}.md"}
    if status == "admitted":
        rec["admitted_by"] = admitted_by
        rec["admitted_at"] = "2026-08-14"
    return rec


class Fixture(LegacyFixture):
    """A throwaway project whose machine state is `audit/state.json`.

    Bodies are written for every record the document names, because `lint` reports a
    record whose evidence file is missing — the one thing the JSON cannot hold.
    """

    def __init__(self, doc=None, conf=None, bodies=True, raw=None):
        self.root = Path(tempfile.mkdtemp(prefix="xcheck-test-"))
        self.profile_dir = None
        self.audit = self.root / "audit"
        for sub in ("findings", "plans", "passes", "construals"):
            (self.audit / sub).mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / "XCHECK.md", self.audit / "XCHECK.md")
        if raw is not None:                       # bytes on purpose: unparseable input
            (self.audit / "state.json").write_text(raw, encoding="utf-8")
            self.doc = None
        else:
            self.doc = state_doc() if doc is None else doc
            self.write_state(self.doc, bodies=bodies)
        self.write_operator_profile()
        if conf:
            self.configure(conf)

    def git_init(self, extra=("subject.py",)):
        """Make this fixture a git repository with one commit, and return its HEAD.

        PHASE 9 (third audit): `file-finding` validates its `--locator` against the
        recorded SUBJECT COMMIT, so a fixture that files a finding has to be a
        repository — a locator checked against nothing has been checked against
        nothing, and the verb says so rather than accepting it.

        Only the tests that file findings call this. A fixture is a throwaway project,
        not a git exercise, and `git init` in every one of them would put a subprocess
        in the setUp of the whole suite.
        """
        for rel in extra:
            p = self.root / rel
            if not p.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("def subject():\n    return 1\n", encoding="utf-8")
        for argv in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                     ["config", "user.name", "t"], ["add", "-A"],
                     ["commit", "-qm", "fixture"]):
            subprocess.run(["git"] + argv, cwd=self.root, check=True, capture_output=True)
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root,
                              capture_output=True, text=True).stdout.strip()

    def locatable(self, rel="subject.py", line=1):
        """`(--locator, --source-hash)` for a file this fixture has committed."""
        blob = subprocess.run(["git", "cat-file", "blob", f"HEAD:{rel}"],
                              cwd=self.root, capture_output=True).stdout
        return (f"{rel}:{line}" if line else rel,
                hashlib.sha256(blob).hexdigest())

    def write_state(self, doc, bodies=True):
        self.doc = doc
        (self.audit / "state.json").write_text(
            json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        if not bodies:
            return
        paths = [r["body_path"] for r in doc.get("findings", [])]
        paths += [r["body_path"] for r in doc.get("class_findings", [])]
        paths += [r["body_path"] for r in doc.get("plans", [])]
        paths += [r["body_path"] for r in doc.get("construals", [])]
        paths += [q["coverage"]["report_path"] for q in doc.get("queue", [])
                  if q.get("coverage")]
        for rel in paths:
            p = self.audit / rel
            if not p.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("Evidence body.\n", encoding="utf-8")
        # PHASE 6: a tree whose views disagree with its state is DRIFT, and every
        # command refuses on it. So a fixture that means "a valid project" has to
        # render them — otherwise every test in the suite would be measuring the
        # drift refusal instead of its own subject. `tests/test_views.py` is where
        # unrendered and hand-edited views are the subject, and it writes them by
        # hand rather than through this helper.
        self.render()

    def render(self):
        """Bring the generated views back in sync with `audit/state.json`.

        Called at the end of every `write_state`, and again by `body()`. A test
        that writes an evidence body directly has to re-render, because the block
        above that body is a VIEW: leaving it stale makes the command under test
        refuse for drift, which is a green-looking pass measuring the wrong guard
        (the `conf=` trap, one file over).
        """
        state = xcheck_submodule("state")
        try:
            doc = state.load_state(self.audit)
        except state.StateError:
            # A fixture built to be REJECTED has no state, so it has no views —
            # rendering is not skipped here to be lenient, there is simply nothing
            # to render from. The command under test still meets the same refusal.
            return
        xcheck_submodule("views").write_views(doc, self.audit)

    def body(self, rel, text):
        """Replace one record's evidence body, keeping its frontmatter view valid."""
        p = self.write(rel, text)
        self.render()
        return p


@contextlib.contextmanager
def open_spy():
    """Every path opened inside the block, as a list that fills as it runs.

    ONE spy, shared. `tests/test_hardening.py` established the pattern for the
    orchestrator's material-blindness claim and `tests/test_dashboard.py` needs the same
    question answered about the renderer; two hand-rolled spies would be two behaviours
    to trust, and the weaker one would be the one that mattered.

    Both doors are watched: `builtins.open` and `Path.open` reach the same syscall by
    different routes, and a reader patched through only one of them is a spy that reports
    "nothing was opened" about code that opened everything.
    """
    import builtins
    opened = []
    real_builtin, real_path = builtins.open, Path.open

    def spy_builtin(file, *a, **kw):
        opened.append(str(file))
        return real_builtin(file, *a, **kw)

    def spy_path(self, *a, **kw):
        opened.append(str(self))
        return real_path(self, *a, **kw)

    builtins.open, Path.open = spy_builtin, spy_path
    try:
        yield opened
    finally:
        builtins.open, Path.open = real_builtin, real_path



def complete_conf(**over):
    """A COMPLETE configuration, every time — both halves of it.

    [[xcheck-test-conf-is-all-or-nothing]]: a three-key dict handed to `Conf` drops
    `remediator_cmd` and its friends, and a refusal test against such a conf may be
    measuring a missing-command refusal instead of the gate it names. So the fixture is
    built by rendering the real default files and overriding lines in them.

    Since the policy split there are TWO files with two owners, and an override goes to
    whichever one owns the key: the project file (`audit/orchestrator.conf`) for
    `batch_size` and its kind, the operator profile for role commands, containment,
    limits, retry and concurrency. A fixture that put them all in one place would be
    testing `load_conf`'s refusal instead of the gate under test — which is why this
    helper lives here now, once, instead of in three test modules that each had to be
    corrected separately.
    """
    # PHASE 3: the shipped template classifies nothing, because an operator must. A
    # fixture project is synthetic and trusted BY CONSTRUCTION, so it makes that
    # declaration explicitly rather than inheriting a default the product refuses to
    # have. Tests of the gate itself pass `trust_level` and keep this from applying.
    over.setdefault("trust_level", "trusted")
    util = xcheck_submodule("util")
    policy = xcheck_submodule("policy")
    # PHASE 1: the two files now live in two PLACES, not just two names. The loader
    # refuses an operator profile that resolves inside the project it is for, so the
    # fixture has to model the real layout: `<box>/project/audit/orchestrator.conf` for
    # the subject's half, `<box>/operator.conf` beside it for the operator's. A fixture
    # that keeps both in one directory is a fixture testing the refusal it is trying to
    # set up.
    box = tempfile.mkdtemp(prefix="xcheck-conf-")
    d = str(Path(box, "project", "audit"))
    Path(d).mkdir(parents=True)

    def render(text):
        lines = []
        for line in text.splitlines():
            key = line.partition("=")[0].strip()
            lines.append(f"{key}={over.pop(key)}" if key in over else line)
        return lines

    project = render(util.DEFAULT_CONF)
    profile = render(policy.OPERATOR_PROFILE_TEMPLATE)
    # Whatever is left names no line in either template — an unknown key, or one a test
    # invents. It goes to the file its kind belongs to.
    for k, v in list(over.items()):
        (profile if policy.classify(k) == "policy" else project).append(f"{k}={v}")
    Path(d, "orchestrator.conf").write_text("\n".join(project) + "\n", encoding="utf-8")
    prof = Path(box, "operator.conf")
    prof.write_text("\n".join(profile) + "\n", encoding="utf-8")
    try:
        return util.load_conf(Path(d), profile=prof)
    finally:
        shutil.rmtree(box, ignore_errors=True)
