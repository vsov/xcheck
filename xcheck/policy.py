"""What the audit SUBJECT may say about how it is audited — and what it may not.

The audit's widest architectural loop: `audit/orchestrator.conf` lives inside the
repository under audit, and it names the commands each role runs, the containment
profile, the environment allowlist, the container image, the egress allowlist, the
resource limits, the retry and parallelism settings and whether the courier pushes. The
readonly courier lets a session write anywhere under `audit/`. So:

    subject under audit -> carries the controller's policy
    -> an agent edits audit/orchestrator.conf
    -> the courier ships the edit
    -> the next dispatch executes the new policy

Every link in that chain is ordinary, documented behaviour. Together they mean an audited
repository can rewrite the terms of its own audit, and the strongest containment profile
in the world is a line in a file the contained thing can edit.

The split here is the fix's first half. Keys are classified into exactly two kinds:

  POLICY  — what the orchestrator EXECUTES or the boundary it executes it inside:
            role commands, containment, environment, network, resource limits, retry,
            concurrency, and push. These decide what runs on the operator's machine, so
            they come from the operator's own file and `load_conf` REFUSES to read them
            out of the subject.

  PROJECT — what this particular audit is ABOUT: how many findings a pass may file,
            when to stop for triage, a note appended to prompts, and the optional
            discipline gates. Wrong values here make an audit worse, not dangerous, and
            the project genuinely knows them better than the operator does.

Phase 4 loads the policy half from `OPERATOR_PROFILE` and records its digest in the
invocation envelope. This module only classifies and refuses; it reads no files.
"""

import hashlib
from collections import namedtuple
import os
from pathlib import Path

# Where the operator's own file lives. Named in the refusal an operator actually reads:
# a message that says "put it somewhere else" without saying where is an instruction to
# guess.
#
# Note what is NOT here: any expansion of `~`. xcheck holds a tested claim that no
# shipped Python file can resolve the operator's home directory at all — that is what
# makes "xcheck writes nothing into your ~/.claude" a property rather than a promise, and
# a config path is a poor reason to give it up. So the location is named EXPLICITLY, by
# `$XCHECK_OPERATOR_PROFILE` or by `$XDG_CONFIG_HOME`, and a machine that sets neither
# has no operator profile — which the loader reports as such rather than inventing one.
OPERATOR_PROFILE_ENV = "XCHECK_OPERATOR_PROFILE"
XDG_CONFIG_ENV = "XDG_CONFIG_HOME"
OPERATOR_PROFILE_RELATIVE = "xcheck/operator.conf"

# A command is executable policy by definition, so the rule is the SUFFIX rather than a
# list of role names: a role added later is policy without anyone remembering to add it.
COMMAND_SUFFIX = "_cmd"

# PHASE 2 (third audit). The suffix used to be the whole rule: any key ending `_cmd` was
# classified as policy, so `auditro_cmd=...` was ACCEPTED — a role command that names no
# role, sitting in the profile doing nothing, while the operator believes they configured
# the Auditor. The registry is now closed and derived from the roles the orchestrator can
# actually DISPATCH, so registering a role is one explicit edit here rather than a spelling.
# `runner` looks a command up as `f"{role.lower()}_cmd"`, which is the same derivation.
DISPATCHABLE_ROLES = ("Planner", "Auditor", "Remediator", "Verifier")
ROLE_COMMAND_KEYS = frozenset(f"{r.lower()}{COMMAND_SUFFIX}" for r in DISPATCHABLE_ROLES)

# Everything the operator's machine executes, or the boundary it executes inside.
POLICY_KEYS = frozenset({
    # containment. `trust_level` classifies the MATERIAL and is what decides whether
    # `sandbox_profile` may be anything other than `container` (phase 3).
    "trust_level",
    "sandbox_profile", "env_allowlist", "container_image",
    "unsafe_allow_unpinned_image", "container_cpus", "container_memory_mb",
    "container_pids",
    # network
    "egress_allowlist", "egress_uplink", "egress_broker_image",
    # resource limits and deadlines
    "session_timeout", "kill_grace", "cpu_seconds", "address_space_mb", "lease_ttl",
    "startup_deadline", "output_deadline", "activity_deadline", "idle_deadline",
    # where raw logs land and how long they are kept: a property of the MACHINE, not of
    # the audit — and a subject that could redirect its own logs could hide them.
    "log_dir", "log_retention_days",
    # how much may run, how often, and how hard it tries again
    "retry_limit", "parallel_passes", "parallel_workers", "parallel_budget_minutes",
    "parallel_confirm_above", "max_sessions", "max_sessions_per_run",
    # which provider adapter reads this machine's telemetry. An OPERATOR key because it
    # is a fact about the wrapper this machine runs, and a subject that could set it could
    # choose how its own cost is counted.
    "telemetry_adapter",
    # which model each route runs. OPERATOR keys: they name what this machine may buy,
    # and a subject that could set them could route its own security verification cheap.
    "cheap_model", "strong_model",
    # what leaves the machine
    "push_after_commit",
})

# PHASE 8 (third audit). `first_action_deadline` promised semantic progress detection
# and measured chattiness: a child that printed 2,049 bytes of nothing cleared it, and
# could then hold its slot indefinitely by printing again before the idle bound. The
# mechanism is a good hang detector and is kept — under the name of what it counts —
# and `activity_deadline` is the signal it was mistaken for.
RENAMED_KEYS = {
    "first_action_deadline": (
        "output_deadline",
        "it measures BYTES written, not usefulness, and the name overclaimed by a "
        "whole session's budget; `activity_deadline` is the separate signal that "
        "watches for work the child cannot fake by printing"),
}

# PHASE 2 (fourth audit). A key that was WITHDRAWN, which is a third thing from a typo
# and from a rename. `diff_scope`, `evidence_cache` and `evidence_dir` were public,
# documented, defaulted and tested — and no production path reached any of them. Reading
# `evidence_cache=on` and doing nothing is the failure the fourth audit named: the
# configuration file promised a feature the program does not have.
#
# So the key is refused rather than ignored, and refused rather than aliased. There is
# nothing to alias it TO: the modules are still in the tree, still tested, and still
# unreachable. An operator who set one of these was not making a typo, they were taking
# the documentation at its word, and the refusal has to say what actually happened.
WITHDRAWN_KEYS = {
    "diff_scope": (
        "diff-directed scope is EXPERIMENTAL and unwired — `xcheck.scope` computes a "
        "selection that no dispatch path reads, so the key never changed what a pass "
        "looked at"),
    "evidence_cache": (
        "the evidence cache is EXPERIMENTAL and unwired — `xcheck.evidence` can key, "
        "write and read an entry, but no dispatch path calls it, so the key never "
        "reused anything"),
    "evidence_dir": (
        "it named where the evidence cache stores entries, and the cache is "
        "EXPERIMENTAL and unwired, so the directory was never written"),
}


def refuse_withdrawn(keys, where):
    """The refusal for a withdrawn key, or None. One text, both conf files.

    Both loaders reach this: the operator profile (which refuses anything unknown) and
    `audit/orchestrator.conf` (which has always TOLERATED keys it does not recognise).
    The lenient one is why this exists — leniency plus a withdrawn key is exactly the
    silent ignore the audit found, so a withdrawn key is named where an unknown one is
    shrugged at.
    """
    hit = sorted(k for k in keys if k in WITHDRAWN_KEYS)
    if not hit:
        return None
    return (f"{where}: withdrawn key(s): {', '.join(hit)}.\n    "
            + "\n    ".join(f"{k}: {WITHDRAWN_KEYS[k]}" for k in hit)
            + "\n    Delete the line. The module is still in the tree and still tested; "
              "what it is not is reachable, and a conf key that promises a feature no "
              "code path reaches is the thing this refusal exists to stop. See "
              "docs/experimental.md.")


# PHASE 3 (third audit). Containment was a knob with a default; it is now a CLASSIFICATION
# the operator has to make. The escape matrix proves `worktree` is not an OS boundary — a
# child sees the operator HOME, has host network, can write the original checkout around
# the courier and can rewrite shared git configuration — and the shipped template still
# offered an empty `sandbox_profile` beside `--dangerously-skip-permissions`. Honest
# documentation does not make an unsafe default industrial.
#
# Two values, no third, and no default: "the operator did not say" must not resolve to
# "trusted", because that is the answer that costs nothing to assume and everything to be
# wrong about. It is a POLICY key — the machine's owner classifies the material, not the
# material.
TRUSTED, UNTRUSTED = "trusted", "untrusted"
TRUST_LEVELS = (TRUSTED, UNTRUSTED)
TRUST_KEY = "trust_level"

# What this audit is about. A wrong value makes the audit worse, not dangerous.
PROJECT_KEYS = frozenset({
    "batch_size", "triage_batch_cap", "session_note", "scope_typing",
    "construal_gate", "embargo", "embargo_after", "loop_progress_limit",
    "audit_over_critical",
    "evidence_bundle",
    # phase 14: which model does which work. A project key, and the boundary that keeps
    # it one is in `routing` itself: no entry, operator or shipped, can route a
    # high-risk pass cheap, so the worst a project can do with it is spend more.
    "model_routing",
    # phase 15: what this audit may SPEND. Project keys, all six of them: the worst a
    # wrong value does is stop an audit early or let it run long. None of them touches
    # what the machine is allowed to execute.
    "budgets", "tokens_per_session", "tokens_per_pass", "tokens_per_audit",
    "no_progress_share_pct", "charter_repeat_limit", "marginal_value_window",
    # phase 16: whether this audit keeps a derived knowledge bundle. A project key, and
    # the reason it is safely one is in `okf` itself: the bundle holds no capability and
    # its back channel refuses all six forbidden effects, so turning it on cannot make
    # the machine less contained — it can only produce a file nobody has to read.
    "okf",
})


def trust_level(conf):
    """The declared trust level, or None. Validated here so every reader agrees."""
    raw = str(conf.get(TRUST_KEY, "") or "").strip()
    if not raw:
        return None
    if raw not in TRUST_LEVELS:
        raise PolicyError(
            f"{TRUST_KEY}={raw!r} is not a trust level ({' or '.join(TRUST_LEVELS)}). "
            f"Guessing which was meant is how untrusted material gets audited under a "
            f"profile that is not a machine boundary.")
    return raw


def no_trust_level_refusal(role):
    """What a writing role prints when the operator has classified nothing."""
    return (
        f"refusing to dispatch {role}: this machine has not classified the material.\n\n"
        f"An agent session runs a command from your operator profile against a repository, "
        f"and what containment that needs is a question about the REPOSITORY, not about "
        f"the tool. There is no default, because the cheap assumption is `{TRUSTED}` and "
        f"that is the one that is expensive to get wrong.\n\n"
        f"Add one line to your operator profile:\n"
        f"    {TRUST_KEY}={TRUSTED}      # code you or your organisation wrote and review\n"
        f"    {TRUST_KEY}={UNTRUSTED}    # anything else — forces sandbox_profile=container\n")


def untrusted_needs_container_refusal(role, profile_name):
    """What an untrusted subject prints when the profile is not a machine boundary."""
    shown = profile_name or "(unset)"
    return (
        f"refusing to dispatch {role}: {TRUST_KEY}={UNTRUSTED} and "
        f"sandbox_profile={shown}.\n\n"
        f"Only `container` is an OS boundary here. This project's own escape matrix "
        f"records what the others do NOT hold — the readonly profile included, since it "
        f"bounds what the child may CHANGE and not what it may reach: the operator home "
        f"directory is readable, the host network is reachable, the original checkout is "
        f"writable around the courier, sibling repositories are visible, and shared git "
        f"configuration can be rewritten. Those are measured results, not cautions.\n\n"
        f"Set sandbox_profile=container, or classify the material {TRUST_KEY}={TRUSTED} "
        f"and accept process-level isolation deliberately.")


def operator_profile_path():
    """The operator profile's path, or None when the machine names none.

    `$XCHECK_OPERATOR_PROFILE` wins; otherwise `$XDG_CONFIG_HOME/xcheck/operator.conf`
    when XDG_CONFIG_HOME is set. Never `~`-derived — see the note above the constants.

    Not resolved and not checked for existence: this is the promise the refusal message
    makes, and it must be answerable on a machine where the file has not been written
    yet. None is a real answer, and the honest one — the alternative is to guess at a
    path in a home directory this tool deliberately cannot find."""
    explicit = os.environ.get(OPERATOR_PROFILE_ENV)
    if explicit:
        return Path(explicit)
    xdg = os.environ.get(XDG_CONFIG_ENV)
    if xdg:
        return Path(xdg) / OPERATOR_PROFILE_RELATIVE
    return None


def classify(key):
    """`policy`, `project`, or `unknown` for one conf key.

    `unknown` is a real answer and not an error: `load_conf` has always tolerated keys
    it does not recognise, and turning an unrecognised key into a refusal here would
    make this module a second, accidental validator of the conf's vocabulary."""
    key = str(key).strip()
    if key in ROLE_COMMAND_KEYS:
        return "policy"
    if key in POLICY_KEYS:
        return "policy"
    if key in PROJECT_KEYS:
        return "project"
    return "unknown"


def policy_keys_in(mapping):
    """Every POLICY key present in a mapping, sorted.

    All of them, in one pass. A loader that refused the first offender would make a
    migration take one run per key, and an operator who has to discover their own
    configuration one refusal at a time will reasonably conclude the tool is broken."""
    return sorted(k for k in mapping if classify(k) == "policy")


def refuse_policy_in_subject(keys, conf_path, values=None):
    """The refusal text, addressed: what was found, why it cannot be there, and the
    exact file and lines to write instead.

    `values` (optional) lets the message quote the operator's own settings back at them
    so the migration is a copy, not a retype. A refusal that makes the reader open the
    source to comply is a refusal that will be worked around."""
    profile = operator_profile_path()
    where = (f"    {profile}" if profile is not None else
             f"    (this machine names no profile: set {OPERATOR_PROFILE_ENV} to the "
             f"path you want, or {XDG_CONFIG_ENV} and use "
             f"$({XDG_CONFIG_ENV})/{OPERATOR_PROFILE_RELATIVE})")
    lines = [f"{conf_path}: {len(keys)} controller-policy key(s) in the audit subject: "
             + ", ".join(keys),
             "",
             "These keys decide what runs on THIS machine — the command each role "
             "executes, the containment it runs inside, the environment and network it "
             "reaches, its resource limits, and whether commits are pushed. They may not "
             "come from the repository being audited: a session can write anywhere under "
             "audit/, the courier ships that edit, and the next dispatch would execute "
             "it. The subject would be setting the terms of its own audit.",
             "",
             "Move them to the operator profile, which is read from outside the "
             "subject:",
             where,
             ""]
    if values:
        lines.append("Write exactly these lines there:")
        lines += [f"    {k}={values[k]}" for k in keys if k in values]
    else:
        lines.append("Write those keys there, one KEY=VALUE per line.")
    lines += [
        "",
        f"Then delete them from {conf_path}. Everything else in that file stays: "
        f"{', '.join(sorted(PROJECT_KEYS))} describe what this audit is about, and the "
        f"project is where they belong.",
        "",
        f"To keep the profile somewhere else, set {OPERATOR_PROFILE_ENV} to its path.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- loading


class PolicyError(Exception):
    """A profile that cannot be trusted to say what runs. Always fail-closed: there is
    no reading of a malformed policy file that is safer than refusing."""


def search_order(explicit=None):
    """Where the profile is looked for, in order, as (label, path-or-None) pairs.

    Returned rather than printed so the refusal, the dispatch banner and the tests all
    describe the same order — three hand-written copies of a search order is how the
    documented order and the real one come apart."""
    xdg = os.environ.get(XDG_CONFIG_ENV)
    return [
        ("--policy", Path(explicit) if explicit else None),
        (f"${OPERATOR_PROFILE_ENV}",
         Path(os.environ[OPERATOR_PROFILE_ENV])
         if os.environ.get(OPERATOR_PROFILE_ENV) else None),
        (f"${XDG_CONFIG_ENV}/{OPERATOR_PROFILE_RELATIVE}",
         Path(xdg) / OPERATOR_PROFILE_RELATIVE if xdg else None),
    ]


def resolve(explicit=None):
    """The first source that names a path AND has a file there, or None.

    A source that names a path with nothing at it does NOT end the search: an operator
    who exports the variable before writing the file, or whose `--policy` points at a
    typo, is better served by the next candidate plus a refusal that lists what was
    tried than by a silent stop at the first miss."""
    for _, path in search_order(explicit):
        if path is not None and path.is_file():
            return path
    return None


def describe_search(explicit=None):
    """The search order as an operator reads it, each line saying what it resolved to.

    This is the body of the refusal. A message that says "no policy profile found"
    without saying where it looked cannot be acted on."""
    lines = []
    for label, path in search_order(explicit):
        if path is None:
            lines.append(f"    {label:<38} (not set)")
        elif path.is_file():
            lines.append(f"    {label:<38} {path}  <- found")
        else:
            lines.append(f"    {label:<38} {path}  (no file there)")
    return "\n".join(lines)


def no_profile_refusal(explicit=None):
    """What to print when nothing resolves. Names the order, then the file to create.

    Not a fallback to defaults: CONF_DEFAULTS has a safe value for every policy key
    except the role commands, so falling back would run an audit under containment
    nobody chose, on a machine whose operator never said this tool may run agents here.
    """
    return (
        "no operator profile found. Controller policy — the command each role runs, the "
        "containment it runs inside, the environment and network it reaches, its limits "
        "and whether commits are pushed — is read from a file OUTSIDE the repository "
        "being audited, and there is no default for it.\n"
        "Looked, in order:\n"
        + describe_search(explicit) + "\n\n"
        f"Create one and point at it:\n"
        f"    export {OPERATOR_PROFILE_ENV}=/path/to/operator.conf\n"
        f"    xcheck --policy /path/to/operator.conf <command>\n"
        f"or set {XDG_CONFIG_ENV} and write $({XDG_CONFIG_ENV})/{OPERATOR_PROFILE_RELATIVE}.\n"
        "`xcheck --policy PATH` with a path that does not exist prints the template to "
        "start from.")


# PHASE 1 (third audit, release blocker). The previous run moved policy OUT of the
# subject by convention and documented the boundary; the loader never checked LOCATION.
# `XCHECK_OPERATOR_PROFILE=<project>/audit/operator.conf` was accepted and applied, which
# restores the exact loop the split existed to cut: a session edits the profile under
# `audit/`, the readonly courier carries the edit out, and the next dispatch runs under it.
# Reproduced both ways before this was written — direct path and a symlink from outside.
#
# The gate is LOCATION, and it is separate from every other refusal here. It runs BEFORE
# parsing, so a profile that may not be used never has its values read, let alone quoted
# back in a message.
def is_inside(path, root):
    """Does `path` land inside `root`? Canonical on both sides, or not at all.

    The one containment predicate in the package, used by the operator-profile gate here
    and by the log and evidence roots in `retention`. Three callers, one rule: a boundary
    spelled three times is a boundary that will be spelled differently once.

    `Path.resolve()` follows every symlink in the path, so an intermediate symlinked
    component is covered by the same comparison as a symlinked final component — and on
    macOS it folds `/var` into `/private/var`, which a string compare calls two different
    places. The other direction matters as much: `<project>-notes` textually starts with
    `<project>`, and only a path-component comparison says it is a sibling.
    """
    target, base = Path(path).resolve(), Path(root).resolve()
    return target == base or base in target.parents


def profile_location_problem(path, project):
    """Why this profile may not be used for this project, or None.

    Canonical on both sides. `Path.resolve()` follows every symlink in the path, so an
    intermediate symlinked component is covered by the same comparison as a symlinked
    final component — and on macOS it also folds `/var` into `/private/var`, which a
    string compare would call two different places.
    """
    if project is None:
        raise PolicyError(
            "the operator profile cannot be validated without the project it is for: a "
            "loader that cannot see the subject cannot refuse a profile inside it. This "
            "is a programming error in the caller, not an operator mistake.")
    root = Path(project).resolve()
    given = Path(path)
    target = given.resolve()
    if is_inside(given, root):
        inside = str(target.relative_to(root))
        via = ""
        if target != (given.parent.resolve() / given.name):
            via = (f"\n    reached through a symlink: {given} -> {target}")
        elif given.is_symlink():
            via = (f"\n    reached through a symlink: {given} -> {target}")
        return (
            f"{given}: an operator profile may not live inside the repository being "
            f"audited.\n"
            f"    project root  {root}\n"
            f"    profile       {target}   (inside it, at {inside}){via}\n\n"
            f"Controller policy — what each role runs, the containment it runs inside, "
            f"the environment and network it reaches — must not be writable by the thing "
            f"under audit. A session can edit any path under `audit/` and the courier "
            f"carries the edit out, so a profile there is executed by the NEXT dispatch. "
            f"Move it outside this repository and point at it again:\n"
            f"    export {OPERATOR_PROFILE_ENV}=/path/to/operator.conf\n"
            f"(any location outside this repository; the tool never derives one from the "
            f"operator home directory, and this message does not name one either)")
    return None


def refuse_profile_inside_subject(path, project):
    """Raise if this profile is inside the subject. The one enforcement point."""
    problem = profile_location_problem(path, project)
    if problem:
        raise PolicyError(problem)


# The result of the ONE read: the mapping the caller asked for, and the digest of the
# exact bytes it was parsed from. Two reads — one to parse, one to hash — is a window in
# which the file can change, and the envelope would then record a digest for a policy the
# run never applied. One read closes it by construction.
Profile = namedtuple("Profile", "path keys digest")

# Digests from reads that actually produced a configuration, by resolved path. The
# envelope asks for the digest AFTER the config is in force, and it must get the digest of
# the bytes that ARE in force rather than whatever is on disk by then.
_DIGESTS = {}


def read_profile(path, project):
    """Location gate, one read, then the mapping and the digest from the same bytes."""
    given = Path(path)
    refuse_profile_inside_subject(given, project)
    try:
        data = given.read_bytes()
    except OSError as e:
        raise PolicyError(f"{given}: cannot be read as an operator profile: {e}") from None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise PolicyError(f"{given}: cannot be read as an operator profile: {e}") from None
    digest = hashlib.sha256(data).hexdigest()
    _DIGESTS[str(given.resolve())] = digest
    return Profile(given, _validated_keys(text, given), digest)


def load(path, project):
    """Parse and VALIDATE an operator profile. Raises PolicyError, never guesses.

    Four refusals, all fail-closed and all naming the offender:

    * INSIDE THE SUBJECT — checked first, before any value is read, because a profile the
      audited repository can write is not the operator's policy at all (phase 1);
    * unreadable — a policy file that cannot be read is not an empty policy file;
    * a PROJECT key — the profile is the operator's answer to "what may run here", not
      a second home for this audit's batch size. One setting with two homes and no rule
      about which wins is the ambiguity this whole split exists to remove;
    * an UNKNOWN key — unlike `orchestrator.conf`, which has always tolerated keys it
      does not recognise, a typo here is a security setting that silently did nothing.
      `sandbox_profil=container` must not read as "no containment configured".
    """
    return read_profile(path, project).keys


def _did_you_mean(unknown):
    """The nearest registered key for each unknown one, when there is a near miss.

    A typo in a containment key is refused either way; naming the key it was one letter
    from is the difference between a refusal an operator can act on and one they argue
    with. No dependency — the distance is a dozen lines and this is not a spell checker.
    """
    known = sorted(POLICY_KEYS | ROLE_COMMAND_KEYS)
    hints = []
    for bad in unknown:
        near = min(known, key=lambda k: _distance(bad, k))
        if _distance(bad, near) <= max(2, len(near) // 4):
            hints.append(f"{bad} -> {near}?")
    return ("\n    did you mean: " + ", ".join(hints)) if hints else ""


def _distance(a, b):
    """Levenshtein, iterative, two rows. Small inputs; clarity over cleverness."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _validated_keys(text, path):
    """The vocabulary half, on text already read once by `read_profile`."""
    raw = {}
    seen = {}
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise PolicyError(f"{path}:{n}: not a KEY=VALUE line: {line!r}")
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        # PHASE 2: last-wins is not a merge strategy for a security policy. Two lines
        # saying `sandbox_profile=container` then `sandbox_profile=none` loaded as `none`
        # — a silent downgrade one careless concatenation away. Every key, not only the
        # containment ones: a loader that ranks its own keys is a loader that will misrank
        # one. Checked on the RAW key before any normalisation, so `k` and ` k ` collide.
        if k in seen:
            raise PolicyError(
                f"{path}:{n}: duplicate key {k!r} — first set on line {seen[k][0]} to "
                f"{seen[k][1]!r}, set again here to {v!r}. An operator profile is not "
                f"merged last-wins: for a policy that decides what runs and inside what "
                f"boundary, a repeated key is an ambiguity to resolve, not a value to "
                f"pick. Delete one of the two lines.")
        seen[k] = (n, v)
        raw[k] = v
    project_keys = sorted(k for k in raw if classify(k) == "project")
    if project_keys:
        raise PolicyError(
            f"{path}: {', '.join(project_keys)} belong to the PROJECT, not to the "
            f"operator profile. They describe what an audit is about and live in that "
            f"project's audit/orchestrator.conf; leaving them here would give one "
            f"setting two homes and no rule about which wins.")
    # A key that was RENAMED is not a typo, and `did you mean` guessing at it would be
    # a worse answer than the one this loader can give exactly. The old spelling is
    # refused rather than aliased: an alias would leave two names for one setting, and
    # the duplicate-key refusal above cannot see `output_deadline=600` beside
    # `first_action_deadline=0` as the contradiction it is.
    renamed = sorted(k for k in raw if k in RENAMED_KEYS)
    if renamed:
        raise PolicyError(
            f"{path}: " + "; ".join(
                f"{k!r} was renamed to {RENAMED_KEYS[k][0]!r} — {RENAMED_KEYS[k][1]}"
                for k in renamed)
            + ". Rename the line; the value carries over unchanged.")
    # Before `unknown`: a withdrawn key IS unknown now, and "unknown key, did you mean"
    # would tell an operator to fix a spelling that was never wrong.
    withdrawn = refuse_withdrawn(raw, path)
    if withdrawn:
        raise PolicyError(withdrawn)
    unknown = sorted(k for k in raw if classify(k) == "unknown")
    if unknown:
        raise PolicyError(
            f"{path}: unknown key(s): {', '.join(unknown)}. An operator profile is "
            f"read fail-closed — a typo here is a containment setting that silently "
            f"did nothing, so it is refused rather than ignored. Known policy keys: "
            f"{', '.join(sorted(POLICY_KEYS | ROLE_COMMAND_KEYS))}."
            + _did_you_mean(unknown))
    return raw


def profile_digest(path):
    """sha256 of the profile's exact bytes, for the invocation envelope.

    The bytes, not the parsed mapping: what an operator wants to answer later is "was
    this the file that was in force", and a digest over a normalised dict would call
    two different files the same.

    PHASE 1: when this path was read by `read_profile`, the digest of THOSE bytes is
    returned rather than a fresh read. The envelope asks after the configuration is in
    force, and the honest answer is the digest of the bytes that are in force — not of
    whatever is on disk by the time the question is asked. A path never loaded (the
    operator at their own terminal, asking what their profile hashes to) still reads."""
    cached = _DIGESTS.get(str(Path(path).resolve()) if Path(path).exists() else str(path))
    if cached:
        return cached
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


# The template the operator profile is created from. Every comment that used to sit
# beside these keys in `orchestrator.conf` moves with them: the explanation of a
# containment knob belongs next to the knob, and the operator is the one who now owns it.
OPERATOR_PROFILE_TEMPLATE = """\
# xcheck OPERATOR PROFILE (KEY=VALUE, # comments). {prompt} = role prompt.
#
# This file lives OUTSIDE any audited repository, and it is the only place the
# orchestrator reads executable policy from: what each role runs, the containment it
# runs inside, the environment and network it reaches, its limits, and whether commits
# are pushed. `audit/orchestrator.conf` inside a subject may carry none of these — a
# session can edit that file and the courier would ship the edit into the next dispatch.
#
# --- role commands. There is no default: an unset role cannot be dispatched, which is
# the correct answer for a machine whose operator has not said what to run.
planner_cmd=codex exec --sandbox workspace-write {prompt}
auditor_cmd=codex exec --sandbox workspace-write {prompt}
verifier_cmd=codex exec --sandbox workspace-write {prompt}
# A permission-skipping flag (`--dangerously-skip-permissions` and its equivalents) is
# NOT shipped here. It hands the child every capability its containment still allows, so
# it is a decision about the machine and belongs beside a machine boundary: add it only
# under sandbox_profile=container. Under `none` it is refused outright.
remediator_cmd=claude -p {prompt}
# telemetry_adapter: which provider's usage shape the wrapper above hands back —
# `codex` or `fake`. There is NO DEFAULT and none is guessed from the role command: a
# command named `codex` may be a wrapper, a proxy or a shim, and inferring a provider from
# a program name is deciding something the operator did not say. Unset means sessions
# record no provider figure, which is the honest answer for an unconfigured machine.
# `fake` speaks the canonical field names and exists so the interface can be exercised on
# a machine with no provider CLI installed.
telemetry_adapter=
# cheap_model / strong_model: the two routes `model_routing` resolves to. Both unset by
# default, and a route with no model REFUSES the dispatch rather than running one nobody
# chose — routing that records a label and runs the same model is worse than no routing,
# because the label gets quoted in metrics as a fact. Put `{model}` in the role command
# where your CLI takes its model (for example `--model {model}`).
cheap_model=
strong_model=
# push_after_commit: empty/off = never push (default); on/true/1/yes = git push after
# each courier commit.
push_after_commit=
# --- containment. Every key here has a SAFE default when absent, EXCEPT the first.
# trust_level: trusted | untrusted. NO DEFAULT, and a writing role refuses to dispatch
# until it is set — how much containment a repository needs is a question about that
# repository, and the cheap answer is the expensive one to get wrong. `trusted` = code
# you or your organisation wrote and review, and process-level isolation is accepted
# deliberately. `untrusted` = anything else, and it makes sandbox_profile=container
# MANDATORY: the worktree profiles isolate the repository, not the machine.
trust_level=
# sandbox_profile: worktree (default for writing roles) | readonly (Auditor and
# Verifier, which must not change the material) | container (an OS boundary — required
# under trust_level=untrusted, see the block below) | none (explicit opt-out — the child
# runs in the project itself). An unknown name is REFUSED, never guessed. Leave empty
# for the per-role default, which is a worktree and NOT a machine boundary: the operator
# home directory is readable from it, the host network is reachable, and the original
# checkout is writable around the courier. A permission-skipping agent flag is permitted
# only under an isolating profile (worktree/readonly/container) and refused under `none`.
sandbox_profile=
# env_allowlist: extra environment names (comma-separated) the child may inherit ON TOP
# of PATH/HOME/LANG/TERM/TMPDIR/… and XCHECK_*. Everything else is dropped, so cloud
# credentials and signing keys are not inheritable by accident.
env_allowlist=
# session_timeout: hard wall-clock bound per session, seconds (default 3600). On expiry
# the child's whole PROCESS GROUP is terminated, so a grandchild dies with it.
session_timeout=3600
# The four deadlines the hard bound above cannot express — it answers "how long may a
# session run", and a session that never STARTS is a different failure. Seconds; 0 = off.
# Derived from the W-06 run, whose ninth session wrote 1,764 bytes (the CLI banner and the
# prompt echoed back) and then held a slot for the full 3600s, while the eight that worked
# ran 439.3s-1052.7s and wrote 527KB-1.1MB.
# startup_deadline: no output AT ALL after N seconds.
startup_deadline=120
# output_deadline: no output beyond the banner allowance plus this session's own prompt
# echoed back, and no canonical transition, after N seconds. It measures BYTES — it is a
# hang detector, not a judgement about content, and it is named for what it counts.
# Longer than the entire life of the fastest working session in that run.
output_deadline=600
# activity_deadline: no OBSERVABLE ACTIVITY after N seconds. Activity is evidence the
# child cannot manufacture by printing: a write inside its own worktree, a canonical
# state transition, or a validated telemetry sidecar. A child that pads its log past the
# output allowance clears the deadline above and is killed by this one.
activity_deadline=1200
# idle_deadline: silent for N seconds. Longer than the whole duration of every session in
# that run, the longest included.
idle_deadline=1200
# log_dir: where RAW session logs are written. Empty = resolved as $XCHECK_LOG_DIR, then
# $XDG_STATE_HOME/xcheck/logs/<project>, then the system temp directory — always OUTSIDE
# the audited working tree. `audit/logs-manifest.jsonl` keeps the name, size and sha256 of
# every log in the repository; the bytes do not live there.
log_dir=
# log_retention_days: how long `xcheck prune-logs` considers a log worth keeping.
# Pruning is NEVER automatic: it is an explicit verb, and it reports unless given --apply.
log_retention_days=30
# kill_grace: seconds between SIGTERM and SIGKILL when a session is terminated.
kill_grace=10
# cpu_seconds / address_space_mb: per-child RLIMIT_CPU / RLIMIT_AS (POSIX only).
# 0 = unset. If set on a platform without `resource`, the runner REFUSES rather than
# pretending the limit applied.
cpu_seconds=0
address_space_mb=0
# lease_ttl: seconds after which a lock whose heartbeat has stopped is a DEAD LEASE,
# reclaimable by plain `xcheck unlock` (no --force). A live heartbeat is never
# reclaimable. The orchestrator beats while a session runs.
lease_ttl=300
# --- the `container` profile. OPT-IN — set sandbox_profile=container. The worktree
# profiles isolate the REPOSITORY; this one asks the operating system to isolate the
# MACHINE: no network, an empty synthetic HOME, the source checkout mounted read-only,
# and bounded CPU/memory/PIDs. It needs a working docker and REFUSES without one — it
# never degrades to a weaker profile.
# container_image: what the session runs in. PINNED BY DIGEST, and enforced rather than
# recommended: an image without an `@sha256:<64 hex>` digest is refused at the door. A
# mutable tag means the image that held the recorded escape matrix is not necessarily
# the image that runs tomorrow. The shipped default has sh, git and busybox — enough for
# the escape probes and a read-only session, but NOT python3 and NOT any agent CLI.
container_image=alpine/git@sha256:3b44767883ac77bddae0160cc27b6b039345e23fa3504f4159efaa32264ab57f
# unsafe_allow_unpinned_image: the escape hatch, named for what it costs. `on` permits
# an unpinned image and WRITES THE WAIVER INTO THE RUN'S LOG. It is about PINNING ONLY:
# it does not widen the network, the mounts, the dropped capabilities or any role's
# read-only default. One knob, one meaning.
unsafe_allow_unpinned_image=off
# --- the egress broker. OPT-IN, and only under `container`.
# egress_allowlist: hostnames or IPs, comma-separated, that the session may reach. EMPTY
# (the default) means the container keeps `--network=none`. When set, the audit
# container joins an INTERNAL docker network with no route off it, and a broker sidecar
# permits exactly these hosts by HTTP CONNECT and logs host+verdict for every attempt.
# It fails closed. What it does NOT do: it does not stop prompt injection, it does not
# shorten credential lifetimes, and it is not a package-registry proxy.
egress_allowlist=
# egress_uplink: the docker network the broker's second leg attaches to.
egress_uplink=bridge
# egress_broker_image: what the sidecar runs. Pinned for the same reason the audit
# container is: the broker is the thing deciding what leaves this run.
egress_broker_image=alpine/git@sha256:3b44767883ac77bddae0160cc27b6b039345e23fa3504f4159efaa32264ab57f
# container_cpus / container_memory_mb / container_pids: the bounds docker enforces on
# the child. All three must be non-zero — unlimited under a profile whose claim is a
# bound is refused rather than applied. When address_space_mb is set it wins over
# container_memory_mb, so there is one memory knob, not two.
container_cpus=2
container_memory_mb=2048
container_pids=256
# --- concurrency, retry, and how much may run at all.
# max_sessions: the default cap on `xcheck loop` when --max-sessions is not given.
max_sessions=20
# parallel_passes: off by default, and `on` is CONDITIONAL — at the moment of the
# decision the orchestrator runs a transactional apply against a throwaway project and
# reads the bytes back; if that probe fails, `on` is refused with the reason.
parallel_passes=off
parallel_workers=2
max_sessions_per_run=8
parallel_budget_minutes=480
parallel_confirm_above=4
# retry_limit: how many times a session may be re-dispatched after a TRANSIENT failure
# (provider-error, or a timeout the log shows was the transport's and not the session's
# own wall-clock budget). 0 = never retry, and 0 is the default: a failed session's
# changes could still be applied, so a retry could apply the same material effect twice.
# A refusal, a capability violation and a crash are never retried at any setting.
retry_limit=0
"""
