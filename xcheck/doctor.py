"""One preflight that refuses BEFORE the first paid session rather than during it.

PHASE 16 (fourth audit). The audit's sentence is that the container path is a construction
kit, not a golden path: an operator assembles a profile, a trust level, an image, a
telemetry adapter, a log root and a budget, and finds out which one they got wrong when a
session has already been dispatched and paid for. Every one of those has a refusal
somewhere in this tree. What did not exist was a way to hit all of them at once, for free.

TEN CHECKS, AND NOT ONE NEW RULE. Every check DELEGATES to the refusal that already
guards its own call site — `policy.profile_location_problem`, `runner.require_trust`,
`runner.backend_probe`, `runner.unpinned_reason`, `retention.root_location_problem`,
`budget.cap_problem`, `routing.routing_problem`, `provider.adapter_for`. Reimplementing
them here would give each rule two definitions, and the second one drifts: a doctor that
passes a configuration the launch door then refuses is worse than no doctor, because the
operator now has a green preflight to disbelieve. `tests/test_doctor.py` asserts the
delegation structurally, over the call graph, so the two cannot come apart quietly.

READ-ONLY, and no paid request. The provider check verifies the PATH — that the role
command's executable exists, that a telemetry adapter is declared, that egress permits the
host — and never sends anything to a model. Verifying reachability by making a request is
how a preflight starts costing money, which is the opposite of the point.

REQUIRED vs ADVISORY is a declared list, not a heuristic. A required check that fails
means the next dispatch will refuse or be unmeasurable, and `doctor` exits non-zero. An
advisory one means something is weaker than it could be on a machine that still works.
"""

import functools
import shutil
import subprocess
from collections import namedtuple
from pathlib import Path

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

# PHASE 10 (fifth audit). A fourth verdict, because three could not say the true thing.
# Reproduced: with `sandbox_profile=container`, `agent-cli-present` looked for the role
# command on the HOST PATH and said PASS — the command runs inside the IMAGE, which this
# process cannot read without starting a container, and starting one is an action a
# preflight may not take. The honest answer is not PASS and not FAIL: it is that the check
# could not look.
#
# UNVERIFIED is not a soft pass. A REQUIRED check that is unverified exits non-zero, the
# same as a failure, because "the next dispatch will work" is precisely what was not
# established. What it buys the operator is a different REMEDY: a failure says fix this, an
# unverified says here is how to find out.
UNVERIFIED = "UNVERIFIED"
VERDICTS = (PASS, FAIL, UNVERIFIED, SKIP)

Result = namedtuple("Result", "name verdict detail remedy")

# The ten, in the order the audit named them. Declared as data so a test can assert the
# doctor runs exactly these and no check quietly disappears.
CHECKS = (
    "operator-profile-outside-subject",
    "trust-classified",
    "sandbox-available",
    "image-pinned",
    "agent-cli-present",
    "provider-path-permitted",
    "telemetry-sidecar-supported",
    "roots-outside-project",
    "budgets-applicable",
    "head-matches-scope",
)

# REQUIRED: failing means the next dispatch refuses, or runs unmeasurably. ADVISORY:
# something is weaker than it could be on a machine that still works. Declared, because a
# rule derived from a check's name or its message is a rule nobody chose.
REQUIRED = (
    "operator-profile-outside-subject",   # the policy loop the third audit cut
    "trust-classified",                   # no writing role dispatches without it
    "sandbox-available",                  # `container` fails closed at the door
    "agent-cli-present",                  # the session cannot start at all
    "roots-outside-project",              # a subject that can rewrite its own evidence
)
ADVISORY = (
    "image-pinned",                       # only bites under `container`
    "provider-path-permitted",            # egress may be unconfigured on purpose
    "telemetry-sidecar-supported",        # unmeasured is honest, just expensive
    "budgets-applicable",                 # off by default, and that is a choice
    "head-matches-scope",                 # a moved HEAD is normal mid-audit
)


def _result(name, problem, detail_ok, remedy):
    """A check's result from the refusal it delegated to. `problem` None means pass."""
    return Result(name, FAIL if problem else PASS,
                  problem.splitlines()[0] if problem else detail_ok, remedy)


# ------------------------------------------------------------------- the ten checks

def check_operator_profile(conf, project, profile_path=None):
    """Delegates to `policy.profile_location_problem` — the phase-4 boundary."""
    from xcheck import policy
    if profile_path is None:
        profile_path = policy.operator_profile_path()
    # The remedy names a directory outside the repository and never derives one: this
    # tool cannot find the operator's home and `tests/test_style_claim_is_narrow.py`
    # scans the shipped tree for every way of learning it, prose included.
    remedy = ("Move the operator profile outside the audited repository and point "
              "XCHECK_OPERATOR_PROFILE at it: "
              "`export XCHECK_OPERATOR_PROFILE=/somewhere/outside/xcheck/operator.conf`")
    if profile_path is None:
        return Result("operator-profile-outside-subject", FAIL,
                      "this machine names no operator profile, so no role can be "
                      "dispatched at all", remedy)
    if not Path(profile_path).exists():
        return Result("operator-profile-outside-subject", FAIL,
                      f"{profile_path} is where the profile would be read from, and "
                      f"nothing is there", remedy)
    return _result("operator-profile-outside-subject",
                   policy.profile_location_problem(profile_path, project),
                   f"{profile_path} resolves outside the subject", remedy)


def check_trust(conf, project):
    """Delegates to `runner.require_trust` — the SAME call the launch door makes, so an
    unclassified subject, an invalid level, and an untrusted subject under a profile that
    is not an OS boundary all fail here for the reason they will fail there."""
    from xcheck import runner
    remedy = ("Add `trust_level=trusted` (code you or your organisation wrote and "
              "review) or `trust_level=untrusted` (anything else, which forces "
              "sandbox_profile=container) to the operator profile")
    try:
        profile = runner.resolve_profile(conf, "Remediator")
        level = runner.require_trust(conf, "a writing role", profile)
    except SystemExit as e:
        return Result("trust-classified", FAIL, str(e).splitlines()[0], remedy)
    return Result("trust-classified", PASS,
                  f"trust_level={level} under sandbox_profile={profile.name}", "")


def check_sandbox(conf, project):
    """Delegates to `runner.resolve_profile` + `runner.backend_probe`."""
    from xcheck import runner
    remedy = ("Start the container backend, or set `sandbox_profile` to one of: none, "
              "readonly, worktree — knowing that only `container` is enforced by the OS")
    try:
        profile = runner.resolve_profile(conf, "Auditor")
    except SystemExit as e:
        return Result("sandbox-available", FAIL, str(e), remedy)
    if not profile.backend:
        return Result("sandbox-available", PASS,
                      f"sandbox_profile={profile.name} needs no backend", "")
    ok, detail = runner.backend_probe(profile.backend)
    return Result("sandbox-available", PASS if ok else FAIL,
                  f"sandbox_profile={profile.name}: {detail}", "" if ok else remedy)


def check_image_pinned(conf, project):
    """Delegates to `runner.unpinned_reason`. Advisory: it only bites under container."""
    from xcheck import runner
    try:
        profile = runner.resolve_profile(conf, "Auditor")
    except SystemExit:
        profile = None
    if profile is None or not profile.backend:
        return Result("image-pinned", SKIP,
                      "no container profile selected, so no image is pulled", "")
    remedy = ("Pin the image by digest: `docker pull <image>` then "
              "`container_image=repo/name@sha256:<64 hex>`")
    try:
        image, waiver = runner.container_image_ref(conf)
    except SystemExit as e:
        return Result("image-pinned", FAIL, str(e).splitlines()[0], remedy)
    if waiver:
        # `unsafe_allow_unpinned_image` is on. The launch will proceed, so this is not a
        # refusal — but the waiver is the whole point of the check, and a preflight that
        # printed PASS over it would be reporting the flag's own opinion of itself.
        return Result("image-pinned", FAIL, waiver.splitlines()[0], remedy)
    return Result("image-pinned", PASS, f"{image} is pinned by digest", "")


ROLES = ("planner", "auditor", "verifier", "remediator")


def dispatch_plan(conf):
    """What the NEXT dispatch would be: one row per role, and the profile it runs under.

    PHASE 10 (fifth audit). The checks below used to loop over the roles that happened to
    carry a command and say nothing about the rest, so a machine with one role configured
    reported the same PASS as a machine with four. And they read the host PATH without
    asking which sandbox the command runs in. Both questions have one answer — what the
    next dispatch actually is — so it is computed once, here, and delegated to
    `runner.resolve_profile`, the function the launch door itself calls.

    Returns `(rows, profile_name, contained)` where a row is `(role, cmd)`. A role with
    no command is KEPT in the table with `None`: `runner.build_cmd` refuses that role
    outright ("no command configured for the {role} role"), so it is a role the next
    dispatch cannot run, and a check that skipped the row would be reporting on the roles
    that happen to work.
    """
    from xcheck.runner import resolve_profile
    rows = [(role, (conf or {}).get(f"{role}_cmd") or None) for role in ROLES]
    try:
        profile = resolve_profile(conf or {}, "auditor").name
    except SystemExit:
        # An unresolvable profile is `sandbox-available`'s refusal to report, not this
        # check's — it delegates there and must not answer the same question twice.
        profile = str((conf or {}).get("sandbox_profile", "") or "").strip() or "?"
    return rows, profile, profile == "container"


def check_agent_cli(conf, project):
    """The executable the role command names. Never RUN — `provenance-must-not-interview-
    the-subject` closed exactly that (F-0098): identifying a program by executing it is
    the defect, so this asks the filesystem whether it exists and stops there."""
    import shlex
    rows, profile, contained = dispatch_plan(conf)
    unset = [role for role, cmd in rows if not cmd]
    if len(unset) == len(rows):
        return Result("agent-cli-present", FAIL,
                      "no role command is configured, so nothing can be dispatched",
                      "Add `auditor_cmd=<your agent CLI> {prompt}` to the operator "
                      "profile")

    per_role, missing = [], []
    for role, cmd in rows:
        if not cmd:
            per_role.append(f"{role}=UNSET")
            continue
        exe = (shlex.split(cmd) or [""])[0]
        if contained:
            per_role.append(f"{role}=`{exe}` (in the image)")
            continue
        ok = shutil.which(exe)
        per_role.append(f"{role}=`{exe}`{'' if ok else ' NOT ON PATH'}")
        if not ok:
            missing.append(f"{role}_cmd names `{exe}`, which is not on PATH")
    detail = f"profile `{profile}`; " + ", ".join(per_role)

    # THE AUDIT'S FIRST FALSE PASS. With `auditor_cmd` alone this said PASS, because it
    # looped over the roles that HAD a command and never mentioned the rest. There is no
    # default role command — `policy` says so in as many words — so an unset role is a
    # role `build_cmd` refuses by name. Reporting that as a pass tells an operator the
    # machine is ready for a dispatch it would refuse.
    if unset:
        missing.insert(0, f"{len(unset)} role(s) have NO command and cannot be "
                          f"dispatched at all ({', '.join(unset)})")

    # UNDER A CONTAINER, THE HOST PATH IS THE WRONG QUESTION. The command runs inside the
    # image; `shutil.which` reads this machine. Answering PASS from the host was the
    # audit's second false positive, and answering FAIL would be just as invented —
    # nothing here has looked inside the image, and looking means starting a container,
    # which is an action a preflight may not take.
    if contained and unset:
        return Result("agent-cli-present", FAIL,
                      "; ".join(missing) + f" — {detail}",
                      "Set the missing <role>_cmd in the operator profile")
    if contained:
        return Result("agent-cli-present", UNVERIFIED,
                      detail + " — the host PATH is not where these run and this check "
                               "does not start a container to look",
                      f"Ask the image itself, once: `docker run --rm "
                      f"{(conf or {}).get('container_image', '<image>')} sh -lc "
                      f"'command -v <exe>'` for each role command above")
    return Result("agent-cli-present", FAIL if missing else PASS,
                  ("; ".join(missing) + f" — {detail}") if missing else detail,
                  ("Set the missing <role>_cmd in the operator profile, and install the "
                   "CLI it names") if missing else "")


def check_provider_path(conf, project):
    """The PATH to the provider, never a request to it.

    No token is spent here and none can be: the check reads the egress allowlist and the
    declared adapter. Verifying reachability by making a request is how a preflight starts
    costing money, which is the opposite of the point — so what is asserted is that the
    permitted path EXISTS, not that a model answered.

    PHASE 10 (fifth audit). It printed "the host default applies" under a container
    profile, where there IS no host default: `runner.container_argv` passes
    `--network=none` unless an egress broker was established, and a broker is established
    only when the allowlist is non-empty. The audit's configuration — container, adapter
    `codex`, allowlist empty — is a container with no route to anything, reported as PASS.
    The allowlist is read through `runner._allowlist_or_empty`, the same function the
    launch path uses, so the two cannot disagree about what is permitted.
    """
    from xcheck import provider
    adapter, problem = provider.adapter_for(conf)
    if problem:
        return Result("provider-path-permitted", FAIL, problem,
                      "Set `telemetry_adapter` to one of: "
                      f"{', '.join(sorted(provider.ADAPTERS))}")
    if adapter is None:
        return Result("provider-path-permitted", SKIP,
                      "no telemetry_adapter declared, so no provider is named to check "
                      "a path to (NO request is ever sent by this check)", "")

    _rows, profile, contained = dispatch_plan(conf)
    from xcheck.runner import _allowlist_or_empty
    allow = _allowlist_or_empty(conf)
    where = (f"adapter `{adapter.name}` -> provider `{adapter.provider}`, "
             f"profile `{profile}`")
    if contained and not allow:
        return Result("provider-path-permitted", FAIL,
                      f"{where}: the container runs `--network=none` (no egress "
                      f"allowlist, so no broker is started) and has NO path to "
                      f"{adapter.provider}. Not a host default — inside this profile "
                      f"there is no host network to default to",
                      "Set `egress_allowlist` to the provider host(s) this run may "
                      "reach, or dispatch under a profile that has the host's network")
    egress = (f"an internal network with a broker permitting {', '.join(allow)} and "
              f"nothing else" if contained else
              ", ".join(allow) or "(unset — the host network applies, which this "
                                  "profile does have)")
    return Result("provider-path-permitted", PASS,
                  f"{where}: egress {egress}. PATH ONLY: no request was sent and no "
                  f"token was spent", "")


def check_telemetry_sidecar(conf, project):
    """The parent-owned sidecar the operator's wrapper must be told about."""
    from xcheck import provider
    adapter, _ = provider.adapter_for(conf)
    cmds = {r: (conf or {}).get(f"{r}_cmd") or "" for r in
            ("planner", "auditor", "verifier", "remediator")}
    named = [r for r, c in cmds.items() if "{sidecar}" in c]
    if adapter is None and not named:
        return Result("telemetry-sidecar-supported", FAIL,
                      "no `telemetry_adapter` and no `{sidecar}` in any role command, so "
                      "every session will be unmeasured — not free, unmeasured",
                      "Set `telemetry_adapter=` and put `{sidecar}` where your wrapper "
                      "writes the provider's usage object")
    if not named:
        return Result("telemetry-sidecar-supported", FAIL,
                      f"adapter `{adapter.name}` is declared but no role command carries "
                      f"`{{sidecar}}`, so nothing will ever write one",
                      "Add `{sidecar}` to the role command where your wrapper takes the "
                      "path to write usage to")
    return Result("telemetry-sidecar-supported", PASS,
                  f"{len(named)} role command(s) carry `{{sidecar}}`: "
                  f"{', '.join(sorted(named))}", "")


def check_roots(conf, project):
    """Delegates to `retention.root_location_problem` for every root that has one."""
    from xcheck import evidence, retention
    remedy = ("Point the root outside the audited repository — a subject that can reach "
              "its own evidence can rewrite it")
    problems, chosen = [], []
    for what, key, env, order in (
            ("session logs", "log_dir", retention.LOG_DIR_ENV,
             retention.log_search_order(project, conf)),
            ("the evidence cache", "evidence_dir", evidence.CACHE_DIR_ENV,
             evidence.cache_search_order(project, conf))):
        # The FIRST candidate that exists is the one that will be used, so it is the only
        # one worth judging: a later entry the resolver will never reach is not a fault.
        for source, path in order:
            if path is None:
                continue
            try:
                retention.refuse_frozen(path)
            except SystemExit as e:
                problems.append(str(e).splitlines()[0])
                break
            why = retention.root_location_problem(path, project, key, env, what)
            (problems if why else chosen).append(
                why.splitlines()[0] if why else f"{what}: {path} (from {source})")
            break
    if problems:
        return Result("roots-outside-project", FAIL, problems[0], remedy)
    return Result("roots-outside-project", PASS, "; ".join(chosen), "")


def check_budgets(conf, project):
    """Delegates to `budget.cap_problem` and the phase-12 coverage floor."""
    from xcheck import budget
    if not budget.budget_enabled(conf):
        return Result("budgets-applicable", SKIP,
                      f"`{budget.FLAG}` is off — no ceiling stops this run", "")
    problems = []
    for role in ("planner", "auditor", "verifier", "remediator"):
        cmd = (conf or {}).get(f"{role}_cmd")
        if cmd is None:
            continue
        why = budget.cap_problem(conf, cmd)
        if why:
            problems.append(f"{role}_cmd: {why}")
    if problems:
        return Result("budgets-applicable", FAIL, problems[0],
                      "Put `{token_cap}` where your CLI takes its output limit, or "
                      "clear `tokens_per_session`")
    armed = [k for k in budget.CEILING_KEYS if (conf or {}).get(k)]
    # PHASE 7 (fifth audit): a PASS here used to read as "the cap is enforced". It never
    # meant that — xcheck checks the placeholder is filled, and whether the operator's
    # wrapper honours the number is a fact about their provider CLI that nothing here has
    # observed. The verdict says so in the detail rather than implying enforcement.
    # Phase 10 gives `doctor` a fourth verdict for exactly this shape of answer.
    unverified = [r for r in ("planner", "auditor", "verifier", "remediator")
                  if budget.cap_verdict(conf, (conf or {}).get(f"{r}_cmd"))[0]
                  == "unverified"]
    note = (f"; per-dispatch cap UNVERIFIED for {len(unverified)} role command(s) — the "
            f"number reaches the provider, enforcement is not observed"
            if unverified else "")
    return Result("budgets-applicable", PASS,
                  f"{len(armed)} ceiling(s) armed: {', '.join(armed) or 'none'}{note}",
                  "")


def check_head(conf, project):
    """Whether the tree has moved since the audit's recorded scope. Advisory: mid-audit
    drift is normal, and a doctor that called it an error would cry wolf every session."""
    from xcheck.state import load_state
    try:
        state = load_state(Path(project) / "audit")
    except Exception as e:                                     # pragma: no cover
        return Result("head-matches-scope", FAIL, f"state is unreadable: {e}",
                      "Run `xcheck lint` to see what the reader refuses")
    recorded = getattr(state, "head_before", None)
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(project),
                              capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):              # pragma: no cover
        head = ""
    if not recorded or not head:
        return Result("head-matches-scope", SKIP,
                      "no recorded scope commit to compare against", "")
    if recorded[:12] != head[:12]:
        return Result("head-matches-scope", FAIL,
                      f"the audit's scope was recorded at {recorded[:12]} and HEAD is "
                      f"{head[:12]} — findings may describe code that has moved",
                      "Run `xcheck reconcile` to classify the backlog against HEAD")
    return Result("head-matches-scope", PASS, f"HEAD {head[:12]} matches the scope", "")


RUNNERS = {
    "operator-profile-outside-subject": check_operator_profile,
    "trust-classified": check_trust,
    "sandbox-available": check_sandbox,
    "image-pinned": check_image_pinned,
    "agent-cli-present": check_agent_cli,
    "provider-path-permitted": check_provider_path,
    "telemetry-sidecar-supported": check_telemetry_sidecar,
    "roots-outside-project": check_roots,
    "budgets-applicable": check_budgets,
    "head-matches-scope": check_head,
}


def run_checks(conf, project, profile_path=None):
    """Every check, in declared order. Reads only; starts no session; spends nothing.

    `profile_path` is the profile the CLI actually loaded (`--policy`), so the check
    judges the file this run used rather than the one the environment would have named.
    """
    runners = dict(RUNNERS)
    if profile_path is not None:
        runners["operator-profile-outside-subject"] = functools.partial(
            check_operator_profile, profile_path=profile_path)
    out = []
    for name in CHECKS:
        try:
            out.append(runners[name](conf, project))
        except Exception as e:                                 # pragma: no cover
            # A check that raises is a FAILED check, never a skipped one: a preflight
            # that swallowed its own error would report green on the configuration it
            # could not examine.
            out.append(Result(name, FAIL, f"the check itself failed: {e!r}",
                              "This is a defect in xcheck — please report it"))
    return out


# What each verdict does to the exit code, declared rather than derived from a
# comparison buried in `transcript`. UNVERIFIED counts with FAIL for a REQUIRED check:
# a preflight whose whole purpose is to answer "will the next dispatch work" may not
# exit 0 on "nobody knows".
BLOCKING = (FAIL, UNVERIFIED)


def transcript(results):
    """The text `xcheck doctor` prints, and the exit code."""
    failed_required = [r for r in results
                       if r.verdict in BLOCKING and r.name in REQUIRED]
    failed_advisory = [r for r in results
                       if r.verdict in BLOCKING and r.name not in REQUIRED]
    lines = ["xcheck doctor — preflight, read-only, no session started, no token spent",
             ""]
    for r in results:
        tag = "required" if r.name in REQUIRED else "advisory"
        lines.append(f"  [{r.verdict:<10}] {r.name:<34} ({tag})")
        lines.append(f"         {r.detail}")
        if r.verdict in BLOCKING and r.remedy:
            lines.append(f"         {'FIX' if r.verdict == FAIL else 'TO VERIFY'}: "
                         f"{r.remedy}")
    lines.append("")
    if failed_required:
        unknown = [r.name for r in failed_required if r.verdict == UNVERIFIED]
        lines.append(f"REFUSED: {len(failed_required)} required check(s) did not pass — "
                     f"{', '.join(r.name for r in failed_required)}. The next dispatch "
                     f"would refuse, or would run unmeasurably." +
                     (f" {len(unknown)} of them ({', '.join(unknown)}) could not be "
                      f"checked from here at all, which is not the same as working."
                      if unknown else ""))
    else:
        lines.append("every REQUIRED check passed" + (
            f"; {len(failed_advisory)} advisory check(s) did not pass "
            f"({', '.join(r.name for r in failed_advisory)}) — this machine works and "
            f"something on it is weaker or less certain than it could be"
            if failed_advisory else ""))
    return "\n".join(lines), (1 if failed_required else 0)
