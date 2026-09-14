"""Which model does which work — as a table, not as another model.

The audit put the economics and the risk in one sentence: paying a strong model to list
files is the largest avoidable cost, and paying a cheap one to judge a security invariant
is the largest avoidable risk. Both halves are answered by the same table, and the second
half is why the table is CLOSED rather than a heuristic that learns.

Three properties this module is built to keep, in the order they would be lost:

  1. **No model chooses the route.** The router is a dict lookup. There is no provider
     call, no subprocess, no scoring function — a test asserts the module makes none.
     A router that asked a model which model to use would have bought the expensive
     call it exists to avoid, and would be unpredictable about security work.
  2. **It fails closed to STRONG, never to cheap.** An unrouted work kind is new work
     nobody has classified, and the safe reading of "I do not know what this is" is not
     "it is probably trivial".
  3. **It cannot buy savings out of a security verdict.** High risk never routes cheap —
     not from the shipped table, and not from an operator entry either. The refusal
     names the pass and the rule, because a cost optimiser silently downgrading a
     security verification is precisely the failure that makes cost optimisation
     unacceptable in an audit tool.

Independence is NOT decided here. `envelope.independence_level` and the
`stop-verifier-independence` gate own that, this module only says how strong the model
must be, and a route can never satisfy an independence requirement on its own.
"""

# ---------------------------------------------------------------- the vocabulary

# PHASE 13 (fourth audit). FOUR routes shipped and two of them named things this tool
# cannot do:
#
#   `deterministic` claimed "no model at all: code answers it" and there was no mechanism
#   to not dispatch. An inventory pass routed `deterministic` ran the same expensive model
#   as everything else — a label promising the largest available saving, attached to no
#   saving. It is removed rather than kept as decoration, and the cells that held it now
#   say `cheap`, which is what actually happens.
#
#   `strong-independent` stated a requirement this module explicitly does not enforce:
#   `independence_still_decides` says so in the same file, and
#   `envelope.independence_level` with the `stop-verifier-independence` gate is what
#   measures it. A route is a statement about model STRENGTH. Folding independence into
#   the strength vocabulary made one word carry two orthogonal meanings, which is the
#   shape of `containment-profile-is-not-authorization`.
#
# Two routes, as the audit asks: more is unwarranted until measurement justifies it, and
# there is no measurement yet — phase 12's coverage floor is the reason why.
CHEAP = "cheap"                      # a small model, for work a wrong answer is cheap on
STRONG = "strong"                    # the expensive model, for judgement
ROUTES = (CHEAP, STRONG)

# Routes a HIGH-risk row may never take. One entry now, and it is the whole safety
# property of the module: collapsing the vocabulary must not collapse this.
NEVER_AT_HIGH_RISK = (CHEAP,)

LOW, NORMAL, HIGH = "low", "normal", "high"
RISKS = (LOW, NORMAL, HIGH)

# The five kinds of work the audit's table names, in its own order.
INVENTORY = "inventory"                            # inventory, formatting, dedup
OBVIOUS_DEFECTS = "obvious-defects"                # first-pass scan for obvious defects
INVARIANTS = "invariants"                          # architectural / semantic invariants
SECURITY_VERIFICATION = "security-verification"    # security / high-severity verification
REPORT_SYNTHESIS = "report-synthesis"              # repeat formatter / report synthesis
WORK_KINDS = (INVENTORY, OBVIOUS_DEFECTS, INVARIANTS, SECURITY_VERIFICATION,
              REPORT_SYNTHESIS)

# The project conf key. Off by default; when off, nothing about a dispatch changes.
FLAG = "model_routing"

# ---------------------------------------------------------------- the table

# Keyed on (work kind, risk). Every cell is written out rather than defaulted, because a
# default is where a security row would quietly acquire a cheap route. `security-
# verification` is `strong-independent` at every risk level: verifying a fix cheaply is
# not a cheaper verification, it is a different and weaker claim.
TABLE = {
    (INVENTORY, LOW): CHEAP,
    (INVENTORY, NORMAL): CHEAP,
    (INVENTORY, HIGH): STRONG,
    (OBVIOUS_DEFECTS, LOW): CHEAP,
    (OBVIOUS_DEFECTS, NORMAL): CHEAP,
    (OBVIOUS_DEFECTS, HIGH): STRONG,
    (INVARIANTS, LOW): STRONG,
    (INVARIANTS, NORMAL): STRONG,
    (INVARIANTS, HIGH): STRONG,
    (SECURITY_VERIFICATION, LOW): STRONG,
    (SECURITY_VERIFICATION, NORMAL): STRONG,
    (SECURITY_VERIFICATION, HIGH): STRONG,
    (REPORT_SYNTHESIS, LOW): CHEAP,
    (REPORT_SYNTHESIS, NORMAL): CHEAP,
    (REPORT_SYNTHESIS, HIGH): STRONG,
}

# How a pass's DIMENSION maps onto a work kind. A dimension nobody listed is not guessed
# at — `work_kind` fails closed for it, which routes it strong.
KIND_BY_DIMENSION = {
    "inventory": INVENTORY,
    "coverage": INVENTORY,
    "style": OBVIOUS_DEFECTS,
    "obvious-defects": OBVIOUS_DEFECTS,
    "invariants": INVARIANTS,
    "architecture": INVARIANTS,
    "semantics": INVARIANTS,
    "security": SECURITY_VERIFICATION,
    "report": REPORT_SYNTHESIS,
}

# Roles whose whole job is judging someone else's work. A Verifier is doing security
# verification whatever the dimension says, because the thing being verified is a fix.
VERIFYING_ROLES = ("Verifier",)

HIGH_RISK_SEVERITIES = ("critical", "high")


def work_kind(role=None, dimension=None):
    """The work kind for a dispatch, or None when nothing classifies it.

    None is the fail-closed answer and callers route it STRONG. Returning a guess here
    would put an unclassified pass on the cheap route by accident, which is the one
    direction this module must never fail in."""
    if role in VERIFYING_ROLES:
        return SECURITY_VERIFICATION
    return KIND_BY_DIMENSION.get(dimension)


def risk_of(dimension=None, severities=()):
    """The risk level for a dispatch. Data in, no model.

    A pass that touches a critical or high finding is HIGH whatever its dimension says:
    the severity of the material is a fact about the material, and it outranks the
    classification of the pass."""
    if any(str(s).lower() in HIGH_RISK_SEVERITIES for s in severities or ()):
        return HIGH
    if KIND_BY_DIMENSION.get(dimension) == SECURITY_VERIFICATION:
        return HIGH
    return NORMAL


def table_problems(table=None):
    """Why a routing table may not be used. Applied to the SHIPPED table by a test and
    to any operator override by `route_for`, so both are held to one rule."""
    out = []
    for (kind, risk), route in sorted((table if table is not None else TABLE).items()):
        if route not in ROUTES:
            out.append(f"({kind}, {risk}) routes to `{route}`, which is not one of "
                       f"{list(ROUTES)}")
        if risk not in RISKS:
            out.append(f"({kind}, {risk}) names a risk level that is not one of "
                       f"{list(RISKS)}")
        if risk == HIGH and route in NEVER_AT_HIGH_RISK:
            out.append(
                f"({kind}, {risk}) routes to `{route}`: a HIGH-risk pass may never be "
                f"routed to {list(NEVER_AT_HIGH_RISK)}. The one thing a cost optimiser "
                f"must never do is buy savings out of the security verdict.")
        if kind == SECURITY_VERIFICATION and route != STRONG:
            out.append(
                f"({kind}, {risk}) routes to `{route}`: security verification is "
                f"`{STRONG}` at every risk level — a cheaper verification is not a "
                f"cheaper claim, it is a weaker one. (Its INDEPENDENCE is a separate "
                f"requirement, enforced by `stop-verifier-independence`, and no route "
                f"can grant or waive it.)")
    return out


def route_for(kind, risk, overrides=None, where="this pass"):
    """The route for one (kind, risk), fail-closed to STRONG.

    `overrides` is an operator's own table, merged over the shipped one and held to the
    SAME rule: an entry that would route a high-risk pass cheap is refused by name
    rather than applied. `where` is what the refusal calls the pass.
    """
    if overrides:
        problems = table_problems(overrides)
        if problems:
            raise ValueError(
                f"refusing the routing override for {where}: {problems[0]}")
    if kind is None or kind not in WORK_KINDS:
        # Unclassified work. Strong, and said out loud by the caller that records it.
        return STRONG
    # `{**TABLE, **overrides}`, not `dict(TABLE, **overrides)`: the keys are
    # TUPLES and the keyword form raises `keywords must be strings`. The benign
    # override arm is what found this — a refusal test alone would have passed
    # over a function that could not apply an override at all.
    route = {**TABLE, **(overrides or {})}.get((kind, risk))
    return route or STRONG


def route_note(kind, risk, route):
    """One line for the dispatch output and the envelope."""
    if kind is None:
        return (f"route: {route} (no work kind classifies this pass, so it takes the "
                f"strong route — an unclassified pass is not a cheap one)")
    return f"route: {route} (work kind {kind}, risk {risk})"


def routing_enabled(conf):
    """`True` when routing is on. Off by default; unrecognised values are off."""
    from xcheck.util import conf_flag
    return conf_flag(conf, FLAG)


def route_of_dispatch(conf, role=None, dimension=None, severities=(), overrides=None,
                      where="this pass"):
    """The route recorded for one dispatch, or None when routing is off.

    None is what keeps the off case byte-identical: nothing downstream branches on a
    route that was never computed."""
    if not routing_enabled(conf):
        return None
    kind = work_kind(role, dimension)
    return route_for(kind, risk_of(dimension, severities), overrides, where)


def independence_still_decides(kind):
    """Whether this work kind's independence is decided ELSEWHERE. Always, for security.

    PHASE 13: this used to test `route == STRONG_INDEPENDENT` — asking the strength
    vocabulary a question about independence, which is the confusion that word created.
    It now keys on the WORK KIND, and the answer for security verification is yes and
    always was: `envelope.independence_level` and the `stop-verifier-independence` gate
    measure whether the requirement was met. Routing cannot grant independence and it
    cannot waive it, and dropping the word from the route vocabulary does not change
    that by one line."""
    return kind == SECURITY_VERIFICATION


# ------------------------------------------- PHASE 13: a route that changes the command

# The audit's finding, reproduced before this was written: with `model_routing` on and
# off, `build_cmd` produced byte-identical argv. The route was computed, recorded in the
# envelope, printed — and discarded. Every metric aggregating by that label described a
# world that did not happen, and an `inventory` pass labelled `deterministic` ran the same
# expensive model as a security verification.
#
# A route now resolves to a MODEL, which reaches the provider through `{model}` in the
# operator's own role command — the same shape as `{sidecar}` (phase 6) and `{token_cap}`
# (phase 12), and for the same reason: xcheck supplies the value and never the flag,
# because which option a CLI takes its model under is a fact about the operator's
# provider. The PROVIDER half of the configuration is `telemetry_adapter` (phase 11), so
# the two halves compose instead of each guessing at the other.
MODEL_KEY = {CHEAP: "cheap_model", STRONG: "strong_model"}
MODEL_PLACEHOLDER = "{model}"


class RouteError(Exception):
    """A dispatch whose route cannot be honoured, or a session whose model disagrees."""


def model_for(conf, route):
    """The model an operator assigned to this route, or None if they assigned none."""
    return str((conf or {}).get(MODEL_KEY.get(route) or "") or "").strip() or None


def resolution(conf, route, adapter=None):
    """`(provider, model)` — the concrete configuration a route resolves to.

    `None` for either half means the operator did not say, and the honest consequence is
    that the dispatch is refused rather than run under a model nobody chose. The provider
    comes from the phase-11 adapter, so a machine states its provider once.
    """
    if route is None:
        return None, None
    return (getattr(adapter, "provider", None), model_for(conf, route))


def routing_problem(conf, route, cmd):
    """Why this route cannot change the command, or None.

    Refused rather than warned about, for the reason the audit gives: routing that
    records a label and runs the same model is worse than no routing, because the label
    is then quoted in metrics as if it were a fact. An operator who turned routing on
    believes their inventory passes are running on a small model.
    """
    if route is None or cmd is None:
        return None
    model = model_for(conf, route)
    if not model:
        return (f"model_routing is on and this pass routes `{route}`, but "
                f"`{MODEL_KEY[route]}` is unset — so the route would be recorded and no "
                f"model would change. Set `{MODEL_KEY[route]}=<model>` in the operator "
                f"profile, or turn `{FLAG}` off; a route nobody applies is a label that "
                f"metrics will quote as a fact.")
    if MODEL_PLACEHOLDER not in cmd:
        return (f"model_routing is on and this pass routes `{route}` to `{model}`, but "
                f"this role command carries no `{MODEL_PLACEHOLDER}`, so the model would "
                f"be recorded and never passed to the provider. Put "
                f"`{MODEL_PLACEHOLDER}` in the command where your CLI takes its model "
                f"(for example `--model {MODEL_PLACEHOLDER}`), or turn `{FLAG}` off.")
    return None


# PHASE 3 (fifth audit): the SOURCE decides whether a model claim can attest anything.
# `envelope` already labelled every figure with the channel it arrived on — `provider`
# for a file the parent owns, `agent-reported` for a line the child printed into its own
# log — and the two functions below read the model and never the label. So an agent that
# printed `XCHECK_TELEMETRY {"model": "<the routed model>"}` produced
# `route_attestation=attested`: the subject of the audit confirmed its own model, which
# is the one thing attestation exists to prevent. The label is not re-derived here and no
# second provenance field is introduced; `telemetry_source` is passed in.
PROVIDER_SOURCE = "provider"


def attests(source):
    """Whether a figure from this channel may confirm anything. One place, two callers."""
    return source == PROVIDER_SOURCE


def attestation_problem(planned_model, actual_model, route=None, source=None):
    """Why a finished session may not be recorded against its planned route, or None.

    This is what makes a route an attested fact rather than an annotation. The planned
    model is what xcheck put in the argv; the actual model is what the PROVIDER said ran,
    read back through the phase-11 adapter — a channel the child does not write. When
    they disagree, both are named: a record saying `route=cheap` over a session that ran
    the strong model is worse than no record, because it is the number a cost comparison
    would be built on.

    Unknown is not disagreement. A session with no provider attestation is UNATTESTED and
    says so; treating silence as a mismatch would refuse every session on a machine with
    no telemetry adapter configured, and treating it as agreement would let the label
    stand unchecked. Neither is what an operator needs to be told.
    """
    if not planned_model or not actual_model:
        return None
    if not attests(source):
        # A disagreement REPORTED BY THE SUBJECT is not grounds to refuse a session: the
        # channel that would be making the accusation is the one this rule exists not to
        # trust. It is `unattested` instead, which is what the record already says about
        # any figure the provider did not confirm.
        return None
    if str(planned_model).strip() == str(actual_model).strip():
        return None
    return (f"the session was dispatched on the `{route or '?'}` route with model "
            f"`{planned_model}`, and the provider reports it actually ran "
            f"`{actual_model}`. The route is recorded as an attested fact or not at all: "
            f"a `{route or '?'}` label over a session that ran a different model is the "
            f"figure a cost comparison would be built on.")


ATTESTED, UNATTESTED = "attested", "unattested"


def attestation(planned_model, actual_model, source=None):
    """`attested` when the PROVIDER confirmed the planned model, else `unattested`.

    `source` is `telemetry_source` from the finished record. Anything but `provider` —
    a line the agent printed, a token count regexed out of its prose, nothing at all —
    is `unattested`, however well the model strings match. Matching strings from the
    subject are a claim about a claim.
    """
    if planned_model and actual_model and attests(source) and \
            str(planned_model).strip() == str(actual_model).strip():
        return ATTESTED
    return UNATTESTED
