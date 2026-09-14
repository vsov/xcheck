"""A benchmark harness, shipped unrun.

Every quality claim this project makes is declarative until the same lifecycle runs
against code the tool has never seen. That run costs money and is the operator's to
authorise, so what is buildable without spending is the harness — and the one thing a
harness like this must never do is make its own numbers look available.

So the sentinel is `unmeasured`, never `0`. `0` is a measurement: it says a run happened
and found nothing. `unmeasured` says no run happened. Asking for an unmeasured metric
RAISES, with the preconditions of a real run in the message, because the failure mode of
an unrun benchmark is not an empty table — it is a table of zeroes someone quotes.

A dry run is not a run that started. It walks the ordered MEASUREMENT_POINTS and records a
concrete artefact at each one — a manifest digest, a resolved trust level, a route, a
ceiling — so "reached" is backed by a value rather than by a flag something set on its way
past. It stops at `dispatch` on purpose: that is where the money is.

Foreign code is audited as `untrusted` and nothing here can say otherwise. A benchmark is
exactly the thing that would quietly point the tool at fifty strangers' repositories, and
a harness that let a worktree profile through would be the most convincing way this
project ever shipped an escape.
"""

import hashlib
import json
from pathlib import Path

from xcheck import budget, policy, routing, state as state_mod
from xcheck.decision import VALIDATION_SURVIVED

UNMEASURED = "unmeasured"

# The five the phase names. Each is a RATIO or a COST, and each has a denominator that a
# dry run does not have — which is the whole reason the sentinel exists.
METRICS = (
    "acceptance_rate",
    "duplicate_rate",
    "false_positive_rate",
    "cost_per_accepted_finding",
    "cost_per_durable_closure",
)

# What a metric needs before it is a number, named per metric so a refusal can say which
# part of a real run is missing rather than "no data".
NEEDS = {
    "acceptance_rate": "findings reported by a real pass and triaged by a human",
    "duplicate_rate": "findings reported by a real pass, fingerprinted against each other",
    "false_positive_rate": "a human triage decision on every reported finding",
    "cost_per_accepted_finding": "measured token spend AND at least one accepted finding",
    "cost_per_durable_closure": "a finding closed, then re-audited and still closed",
    "tokens_per_outcome": "measured token spend AND at least one useful outcome",
    "money_per_outcome": "token rates the OPERATOR declares (xcheck ships no price "
                         "list), measured spend, and at least one useful outcome",
    "wall_clock_seconds": "sessions carrying a recorded duration",
    "human_review_seconds_per_outcome": "review time somebody RECORDED — this is not "
                                        "derived from timestamps, because the gap "
                                        "between two events is elapsed time, not "
                                        "attention",
}

# ------------------------------------------------------------- PHASE 12 (fifth audit)
#
# FOUR METRICS, AND THE DENOMINATOR THEY ARE PRICED AGAINST.
#
# The audit's economics finding: `docs/ab-model-routing.md` asked for a 30% reduction in
# TOKENS and ignored latency. A cheap model can spend the same tokens for less money, and
# speed was one of the original goals — so a single token criterion can call a win a loss
# and a loss a win. Four separate figures, because they move independently:
VALUE_METRICS = (
    "tokens_per_outcome",
    "money_per_outcome",
    "wall_clock_seconds",          # a distribution (P50/P95) AND a per-outcome total
    "human_review_seconds_per_outcome",
)

# ...and the denominator, which is the other half of the finding. `metrics_report` prices
# tokens per CANONICAL TRANSITION, and a canonical transition is activity: a run can raise
# its transition count while producing nothing anybody wanted. A USEFUL OUTCOME is one of
# exactly two things, both of which required somebody other than the reporting session to
# agree:
USEFUL_OUTCOME = {
    "confirmed_defect": "a finding that reached validation and SURVIVED it — the defect "
                        "was independently confirmed, not merely filed",
    "durable_fix": "a finding closed on the first attempt and never reopened — a fix "
                   "that held, not a status that moved",
}
NOT_AN_OUTCOME = ("canonical transitions (activity: a session that moved a record moved "
                  "a record, which is not the same as being right)",
                  "reported findings (nobody has agreed with them yet)",
                  "sessions dispatched (an input, and the one being paid for)")

# Ordered. A dry run reaches each in turn and records what it found there; the last one is
# where a real run begins to cost money, and the dry run stops.
MEASUREMENT_POINTS = (
    "target-resolved",       # the subject, by content
    "trust-classified",      # untrusted, and not negotiable here
    "containment-resolved",  # a container, or a refusal
    "budget-bound",          # the ceilings this target would run under
    "route-chosen",          # which model, from the closed table
    "charter-built",         # what the pass would be asked to do
    "provenance-sealed",     # what makes two runs comparable
    "dispatch",              # STOP. Everything past here is paid.
)

DRY_RUN_STOPS_AT = "dispatch"

# Stated because a harness that hides its preconditions will be run by accident.
PRECONDITIONS = (
    ("targets", "a list of repositories the tool has never seen, each a git checkout "
                "at a named revision — a benchmark over this project's own corpus "
                "measures the tool against the material it was tuned on"),
    ("budget", "token ceilings the operator accepts losing (`budgets=on` plus the six "
               "keys), because an unbounded benchmark is an unbounded bill"),
    ("container profile", "`sandbox_profile=container`. Foreign code runs under an OS "
                          "boundary or it does not run"),
    ("an operator who accepts the cost", "a real run spends real money and the numbers "
                                         "belong to whoever paid for them"),
)


class BenchmarkError(Exception):
    """Raised when something asks for a number nobody measured."""


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def route_table_digest():
    """The routing table in force, by content. Two benchmark runs are comparable only if
    the same work went to the same models, and the table is that fact."""
    rows = sorted(f"{k[0]}|{k[1]}|{v}" for k, v in routing.TABLE.items())
    return _digest("\n".join(rows))


def preconditions_unmet(targets, conf, accepted_cost=False):
    """Everything standing between this harness and a real run, by name.

    `accepted_cost` is an ARGUMENT, not a configuration key, and deliberately so. A
    `benchmark_accepted_cost=on` line would put the operator's consent to spend money
    inside a file — and this project spent a whole phase moving policy out of the subject
    precisely because a session that can edit its own terms has no terms. Consent to a
    bill is passed by whoever is holding the bill.
    """
    unmet = []
    if not targets:
        unmet.append("targets: none given")
    if not budget.budget_enabled(conf):
        unmet.append("budget: `budgets` is off, so nothing would stop the run")
    else:
        zero = [k for k in budget.CEILING_KEYS if not budget._ceiling(conf, k)]
        if zero:
            unmet.append(f"budget: no ceiling set for {', '.join(zero)}")
    if str(conf.get("sandbox_profile", "") or "").strip() != "container":
        unmet.append("container profile: sandbox_profile is not `container`, and foreign "
                     "code does not run under a worktree")
    if not accepted_cost:
        unmet.append("an operator who accepts the cost: nobody passed `accepted_cost`, "
                     "and this harness will not spend money nobody agreed to. It is an "
                     "argument rather than a config key on purpose — consent to a bill "
                     "does not belong in a file the audited project could contain")
    return unmet


def trust_for(target):
    """Always untrusted. There is no argument to this function for a reason.

    A benchmark target is, by definition, code this tool has never seen. The one place a
    `trusted` classification could be smuggled in is a per-target override, so there is
    no per-target override — the level is a constant of the harness, and the check below
    is what a caller gets instead of a knob.
    """
    return policy.UNTRUSTED


def refuse_trusted_run(target, conf):
    """The refusal a caller sees for a profile that is not an OS boundary."""
    profile = str(conf.get("sandbox_profile", "") or "").strip() or "(unset)"
    level = str(conf.get("trust_level", "") or "").strip() or "(unset)"
    if profile == "container":
        return None
    return (f"refusing to benchmark {target}: a benchmark target is code this tool has "
            f"never seen, so it runs as `{policy.UNTRUSTED}` — and untrusted material "
            f"needs an OS boundary. sandbox_profile={profile!r}, "
            f"trust_level={level!r}. Set sandbox_profile=container. This is not "
            f"overridable per target: a harness with a per-target trust knob is a "
            f"harness that will eventually audit a stranger's repository under a "
            f"worktree.")


def run_provenance(target, conf, policy_digest=None):
    """What makes two benchmark results comparable. Missing pieces are NAMED, not
    dropped: a comparison against a run whose model nobody recorded is not a comparison."""
    path = Path(target)
    out = {
        "target": str(path),
        "subject_commit": state_mod.subject_commit(path) if path.exists() else UNMEASURED,
        "subject_manifest": (state_mod.subject_manifest(path) if path.exists()
                             else UNMEASURED),
        "policy_digest": policy_digest or UNMEASURED,
        "model_identity": str(conf.get("auditor_cmd", "") or "").strip() or UNMEASURED,
        "route_table_digest": route_table_digest(),
        "controller_commit": state_mod.controller_commit(),
    }
    out["incomparable_because"] = sorted(k for k, v in out.items() if v == UNMEASURED)
    return out


def scoreboard(reported=(), triaged=(), accepted=(), duplicates=(), closures=(),
               reopened=(), tokens=None):
    """The metric definitions, exercised on counts rather than on a paid run.

    Every value is either a number or `unmeasured`. A ratio whose DENOMINATOR is zero is
    `unmeasured` too, and that is deliberate: 0 accepted out of 0 reported is not a 0%
    acceptance rate, it is a run that did not happen.
    """
    def ratio(num, den):
        return round(len(num) / len(den), 4) if den else UNMEASURED

    def cost(den):
        if tokens is None or not den:
            return UNMEASURED
        return round(tokens / len(den), 1)

    return {
        "acceptance_rate": ratio(accepted, reported),
        "duplicate_rate": ratio(duplicates, reported),
        "false_positive_rate": (round(1 - len(accepted) / len(triaged), 4)
                                if triaged else UNMEASURED),
        "cost_per_accepted_finding": cost(accepted),
        "cost_per_durable_closure": cost([c for c in closures if c not in set(reopened)]),
        "_counts": {"reported": len(reported), "triaged": len(triaged),
                    "accepted": len(accepted), "duplicates": len(duplicates),
                    "closures": len(closures), "reopened": len(reopened),
                    "tokens": tokens if tokens is not None else UNMEASURED},
    }


class BenchmarkReport:
    """A benchmark result that refuses to hand out a number it does not have."""

    def __init__(self, targets, points, metrics, unmet, dry_run=True):
        self.targets = list(targets)
        self.points = dict(points)          # name -> the artefact found there
        self.metrics = dict(metrics)
        self.unmet = list(unmet)
        self.dry_run = dry_run

    def reached(self, point):
        return point in self.points

    def value(self, name):
        """The metric, or a refusal that says what a real run would need."""
        if name not in METRICS:
            raise BenchmarkError(f"no such metric: {name!r} (have {list(METRICS)})")
        got = self.metrics.get(name, UNMEASURED)
        if got == UNMEASURED:
            raise BenchmarkError(
                f"{name} is {UNMEASURED}, and `{UNMEASURED}` is not 0 — 0 would mean a "
                f"run happened and found nothing. It needs "
                f"{NEEDS[name]}.\nThis was a DRY RUN: it stopped at "
                f"`{DRY_RUN_STOPS_AT}`, which is where a real run starts spending. "
                f"A real run needs:\n" +
                "\n".join(f"  - {what}: {why}" for what, why in PRECONDITIONS))
        return got

    def table(self):
        return {m: self.metrics.get(m, UNMEASURED) for m in METRICS}

    def as_json(self):
        return json.dumps({"dry_run": self.dry_run, "targets": self.targets,
                           "points": self.points, "metrics": self.table(),
                           "preconditions_unmet": self.unmet},
                          indent=2, sort_keys=True, default=str)


def dry_run(targets, conf, policy_digest=None, ground_truth=None,
            accepted_cost=False):
    """Walk every measurement point, recording what was found at each. Spend nothing.

    `ground_truth` is the KNOWN-fixture hook: a mapping of target -> the bookkeeping a
    real pass would have produced. It exercises the metric arithmetic without a paid run
    and is the only way a number reaches this report — everything else stays `unmeasured`.
    """
    targets = [str(t) for t in targets]
    unmet = preconditions_unmet(targets, conf, accepted_cost)
    points = {}
    for target in targets:
        refusal = refuse_trusted_run(target, conf)
        if refusal:
            raise BenchmarkError(refusal)

    if targets:
        points["target-resolved"] = {t: run_provenance(t, conf, policy_digest)["subject_commit"]
                                     for t in targets}
        points["trust-classified"] = {t: trust_for(t) for t in targets}
        points["containment-resolved"] = str(conf.get("sandbox_profile", "") or "").strip()
        points["budget-bound"] = {k: budget._ceiling(conf, k) for k in budget.CEILING_KEYS}
        points["route-chosen"] = {
            t: routing.route_for(routing.OBVIOUS_DEFECTS, routing.HIGH,
                                 where=f"benchmark target {t}")
            for t in targets}
        points["charter-built"] = {
            t: _digest(f"benchmark|{t}|{points['route-chosen'][t]}") for t in targets}
        points["provenance-sealed"] = {t: run_provenance(t, conf, policy_digest)
                                       for t in targets}
        points[DRY_RUN_STOPS_AT] = (
            "NOT PERFORMED — a dry run stops here. Everything past this point spends "
            "tokens, and the numbers below stay `unmeasured` because of it.")

    metrics = {m: UNMEASURED for m in METRICS}
    if ground_truth:
        board = scoreboard(**ground_truth)
        for m in METRICS:
            metrics[m] = board[m]
        points["bookkeeping"] = board["_counts"]
    return BenchmarkReport(targets, points, metrics, unmet, dry_run=True)


# ------------------------------------------------------------- PHASE 12 (fifth audit)

RATE_FIELDS = ("input_per_million", "output_per_million", "currency")

#: PHASE 7 (sixth audit). Rates are declared PER MODEL and PER TOKEN CATEGORY. The
#: previous shape had one output rate multiplying every token, so a cheap model's
#: input tokens were charged at the strong model's output price and the difference
#: routing exists to produce could not appear in the figure. This shape has nowhere to
#: put one number that applies to everything.
MODEL_RATE_FIELDS = ("input_per_million", "output_per_million", "cache_per_million")


def token_rates(declared):
    """The operator's declared token prices, or None — and a REFUSAL for a broken one.

    xcheck ships no price list and derives no price from a model name. Prices change, they
    differ per account, and a tool that guessed one would print money that nobody was
    charged. `declared` is a mapping the operator supplies where they run the benchmark;
    absent, `money_per_outcome` is `unmeasured` and says which input is missing.

    A malformed declaration RAISES rather than degrading to None: silently reading a
    typo'd rate as "no rates" would report `unmeasured` on a run the operator believes
    they priced.
    """
    if not declared:
        return None
    missing = [f for f in RATE_FIELDS if f not in declared]
    if missing:
        raise BenchmarkError(
            f"token rates declare {sorted(declared)} and need {list(RATE_FIELDS)} — "
            f"missing {missing}. A rate without its currency is a number, and a rate "
            f"without both directions prices half a session.")
    out = {}
    for field in ("input_per_million", "output_per_million"):
        try:
            value = float(declared[field])
        except (TypeError, ValueError):
            raise BenchmarkError(
                f"token rate {field}={declared[field]!r} is not a number") from None
        if value < 0:
            raise BenchmarkError(f"token rate {field}={value} is negative")
        out[field] = value
    out["currency"] = str(declared["currency"]).strip().upper()
    if not out["currency"]:
        raise BenchmarkError("token rates declare an empty currency")
    # PHASE 7 (sixth audit): the PER-MODEL, PER-CATEGORY table, validated here and carried
    # through. `money_for` reads `rates["models"]`, so a validator that returned only the
    # three top-level fields would silently strip the table it prices from and every money
    # figure would refuse with "no rate declared" — the field would be validated in one
    # home and read in another.
    out["models"] = {}
    for model, row in (declared.get("models") or {}).items():
        if not isinstance(row, dict):
            raise BenchmarkError(f"token rates for model {model!r} are not a mapping")
        missing = [f for f in MODEL_RATE_FIELDS if f not in row]
        if missing:
            raise BenchmarkError(
                f"token rates for model {model!r} are missing {missing}: a model priced "
                f"on some of its token categories prices part of a session")
        priced_row = {}
        for field in MODEL_RATE_FIELDS:
            try:
                value = float(row[field])
            except (TypeError, ValueError):
                raise BenchmarkError(
                    f"{model} {field}={row[field]!r} is not a number") from None
            _finite(value, f"{model} {field}")
            if value < 0:
                raise BenchmarkError(f"{model} {field}={value} is negative")
            priced_row[field] = value
        out["models"][model] = priced_row
    return out


def _percentile(values, pct):
    """The nearest-rank percentile of a sorted sample, or None on an empty one.

    Nearest-rank rather than interpolated: with the handful of sessions a benchmark
    produces, an interpolated P95 invents a value between two real ones and reads as a
    measurement. This one is always a session that actually happened.
    """
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(pct / 100 * len(ordered) + 0.5)) - 1))
    return ordered[index]


#: How long a closure must stand before it is counted as a fix that HELD.
#:
#: PHASE 7 (sixth audit). `closed` plus `attempts == 0` was the whole test, and it proves
#: neither half of what it claims: the attempt counter is per-record and can be reset by
#: a rewrite, and a finding closed a minute ago has not been observed to hold — nothing
#: has had the chance to reopen it. 14 days is the window: long enough that a subsequent
#: audit round would have run over the same material, which is the event that reopens a
#: closure if the fix did not hold.
DURABILITY_WINDOW_DAYS = 14


def _closed_long_enough(record, window, now=None):
    """True when this closure has stood for the observation window.

    A record with no `updated` date cannot be shown to have held, and is not counted —
    the missing date is the reason, not a default.
    """
    from datetime import date, datetime, timedelta
    stamp = getattr(record, "updated", None)
    if not stamp:
        return False
    try:
        closed_on = datetime.fromisoformat(str(stamp)[:10]).date()
    except ValueError:
        return False
    today = now or date.today()
    return (today - closed_on) >= timedelta(days=window)


def useful_outcomes(state, window=None, now=None):
    """`(count, breakdown)` — the denominator, and what it is made of.

    Deliberately not `len(canonical_transitions)`: that counts work done, and the question
    is what the work was worth. Both members require somebody other than the reporting
    session to have agreed — a Verifier's validation, or a closure that held.

    PHASE 7 (sixth audit): a durable fix now requires an OBSERVATION WINDOW. `closed`
    with `attempts == 0` says a status moved and a counter is low; it does not say a fix
    held, and the counter can be reset by a rewrite. `window=0` reproduces the old
    behaviour and is what the arms use to show the difference.
    """
    window = DURABILITY_WINDOW_DAYS if window is None else window
    records = list(state.findings) + list(state.class_findings)
    durable = [r for r in records
               if r.status == "closed" and (getattr(r, "attempts", 0) or 0) == 0
               and _closed_long_enough(r, window, now=now)]
    # DISJOINT, and the first version was not. `closed` is in `VALIDATION_SURVIVED`, so a
    # durable fix was counted once as a confirmed defect and again as a fix — inflating
    # the denominator, which makes every per-outcome cost look BETTER. A ground-truth
    # fixture asking for 2 confirmed and 1 durable got 4. The union is what the caption
    # claims, so the union is what is counted.
    fixed_ids = {r.id for r in durable}
    confirmed = [r for r in records
                 if r.status in VALIDATION_SURVIVED and r.id not in fixed_ids]
    return len(confirmed) + len(durable), {"confirmed_defect": len(confirmed),
                                           "durable_fix": len(durable)}


#: The three kinds of answer, which are never the same number.
#:
#: PHASE 7 (sixth audit). `value_report` was handed two finished sessions with tokens on
#: ONE of them, that one figure `agent-reported`, and it returned `tokens_per_outcome`
#: and `money_per_outcome` with `unmeasured_reason=None` — a confident number over 50%
#: coverage and zero provider attestation, while the A/B document promises
#: provider-attested data. A figure that does not say which kind it is gets read as the
#: strongest one available.
MEASUREMENT = "measurement"     # every session that contributes was observed, by a source
                                # entitled to say so
LOWER_BOUND = "lower-bound"     # what is known is real and there is provably more: the
                                # measured part of an incompletely measured corpus
ESTIMATE = "estimate"           # derived from a declared assumption (a price list, a rate
                                # applied to an unsplit total)
KINDS = (MEASUREMENT, LOWER_BOUND, ESTIMATE)

#: A figure priced per useful outcome needs most of the corpus measured, because the
#: unmeasured sessions are exactly the ones that could move it. 80% is the floor: at that
#: coverage the unmeasured fifth cannot change a per-outcome figure by more than a
#: quarter of itself, which is the largest error this report is willing to publish
#: without saying `lower-bound`. Below it the measured total is still REPORTED — as a
#: lower bound, which is what it is.
COVERAGE_FLOOR = 0.80

#: Who is entitled to say what a session cost. An agent reporting its own usage is the
#: subject of the audit describing its own bill; the fifth audit found exactly that
#: promotion (`XCHECK_TELEMETRY {...}` in a log became `route_attestation: attested`).
PRICEABLE_SOURCES = ("provider",)


def _finite(value, where):
    """A rate that is NaN or Infinity is refused by name.

    `float("nan")` compares False to everything, so a NaN rate produces a NaN cost that
    silently fails every threshold it is compared against; `inf` produces a cost that
    fails all of them. Neither is a price anybody was charged.
    """
    if value != value:
        raise BenchmarkError(f"{where} is NaN — not a price anybody was charged")
    if value in (float("inf"), float("-inf")):
        raise BenchmarkError(f"{where} is {value} — not a price anybody was charged")
    return value


def token_coverage(finished, measured):
    """`(fraction, kind)` — how much of the corpus carries a token figure, and what that
    makes any total computed from it."""
    if not finished:
        return 0.0, LOWER_BOUND
    fraction = len(measured) / len(finished)
    return fraction, (MEASUREMENT if fraction >= COVERAGE_FLOOR else LOWER_BOUND)


def priceable(events):
    """The finished sessions whose token figure came from a source entitled to say so.

    PHASE 7 (sixth audit): provenance is a PRECONDITION, not a footnote. A money figure
    built on an agent's own claim about its own spend is the subject of the audit pricing
    itself.
    """
    out = []
    for e in events:
        if e.get("event") != "session_finished":
            continue
        tokens = e.get("tokens")
        if not isinstance(tokens, int) or isinstance(tokens, bool):
            continue
        if e.get("telemetry_source") in PRICEABLE_SOURCES:
            out.append(e)
    return out


def money_for(sessions, rates):
    """`(amount, breakdown)` priced per MODEL and per token CATEGORY.

    PHASE 7 (sixth audit). One output rate used to multiply every token, so a cheap
    model's input tokens were charged at the strong model's output price and the whole
    point of routing — that the two differ — could not appear in the figure. The shape of
    the input is what makes a single-rate calculation impossible to express: rates arrive
    keyed by model, and each model's rates are keyed by category.
    """
    total, breakdown = 0.0, {}
    for e in sessions:
        model = e.get("provider_model") or e.get("model") or "unknown"
        per_model = rates.get("models", {}).get(model)
        if per_model is None:
            return None, {"unpriced_model": model}
        row = breakdown.setdefault(model, {"input": 0, "output": 0, "cache": 0,
                                           "money": 0.0})
        # This session's own money, accumulated separately from the model's running row.
        # `total += row["money"]` would add the row's RUNNING total once per session, so
        # N sessions sharing a model are charged 1+2+...+N times their price — a bug that
        # is invisible with one session per model and was caught by a transcript printing
        # `by_model` beside `total` and the two disagreeing.
        this_session = 0.0
        for category in ("input", "output", "cache"):
            count = e.get(f"tokens_{category}")
            if not isinstance(count, int) or isinstance(count, bool):
                continue
            rate = _finite(float(per_model[f"{category}_per_million"]),
                           f"{model} {category}_per_million")
            if rate < 0:
                raise BenchmarkError(f"{model} {category}_per_million={rate} is negative")
            row[category] += count
            this_session += count / 1_000_000 * rate
        row["money"] += this_session
        total += this_session
    # Rounded ONCE, at the end, and in both places: rounding the row per session and the
    # total per run makes the two disagree in the last digit, which is indistinguishable
    # from the accumulation bug above.
    for row in breakdown.values():
        row["money"] = round(row["money"], 6)
    return round(total, 6), breakdown


def audit_span_seconds(events):
    """The audit's own wall-clock: last event minus first, from the stream's timestamps.

    PHASE 7 (sixth audit). The sum of session durations was reported as elapsed time. Two
    sessions of 10 minutes running in parallel take 10 minutes, not 20 — so under the
    parallel dispatcher the sum overstates the wall clock by the concurrency factor, and
    a speed comparison built on it measures the thread pool.
    """
    stamps = sorted(e["ts"] for e in events if isinstance(e.get("ts"), str))
    if len(stamps) < 2:
        return None
    from datetime import datetime
    try:
        first = datetime.fromisoformat(stamps[0])
        last = datetime.fromisoformat(stamps[-1])
    except ValueError:
        return None
    return (last - first).total_seconds()


def value_report(state, events, rates=None, review_seconds=None, window=None):
    """The four metrics, priced per useful outcome. Every one refuses rather than zeroing.

    PHASE 12 (fifth audit). `unmeasured` is not 0 and a caption is a second claim, so each
    figure here carries the denominator it was divided by and the reason it is absent when
    it is. On this repository's own corpus every one of them refuses, which is the correct
    answer for a run with 0 closures and 0 validations — and is exactly what a table of
    zeroes would have hidden.
    """
    ev = list(events or [])
    finished = [e for e in ev if e.get("event") == "session_finished"]
    measured = [e for e in finished if isinstance(e.get("tokens"), int)
                and not isinstance(e.get("tokens"), bool)]
    tokens = sum(e["tokens"] for e in measured)
    durations = [float(s["duration_s"]) for s in state.sessions
                 if s.get("duration_s") is not None]
    outcomes, breakdown = useful_outcomes(state, window=window)
    priced = token_rates(rates)
    coverage, token_kind = token_coverage(finished, measured)
    attested = priceable(ev)

    def per_outcome(total):
        return round(total / outcomes, 2) if outcomes and total is not None else None

    # PHASE 7 (sixth audit). Money is priced from ATTESTED sessions only, per model and
    # per token category. `agent-reported` figures cannot produce one: a bill the subject
    # of the audit wrote about itself is not a measurement of what was spent.
    money, money_breakdown, money_reason = None, {}, None
    if not priced:
        money_reason = NEEDS["money_per_outcome"]
    elif not attested:
        money_reason = (f"{len(measured)} session(s) carry a token figure and "
                        f"{len(attested)} of them came from a source entitled to say so "
                        f"{list(PRICEABLE_SOURCES)}. A price built on an agent's own "
                        f"claim about its own spend is the subject of the audit pricing "
                        f"itself.")
    elif not outcomes:
        money_reason = NEEDS["money_per_outcome"]
    else:
        money, money_breakdown = money_for(attested, priced)
        if money is None:
            money_reason = (f"no rate declared for model "
                            f"{money_breakdown.get('unpriced_model')!r}: xcheck ships no "
                            f"price list and will not guess one from a model name")
    span = audit_span_seconds(ev)

    return {
        "denominator": {
            "useful_outcomes": outcomes,
            "by_kind": breakdown,
            "definition": dict(USEFUL_OUTCOME),
            "not_an_outcome": list(NOT_AN_OUTCOME),
            "caption": (f"every per-outcome figure below is divided by {outcomes} useful "
                        f"outcome(s): {breakdown['confirmed_defect']} independently "
                        f"confirmed defect(s) and {breakdown['durable_fix']} durable "
                        f"fix(es). NOT by canonical transitions, which count activity."),
        },
        "tokens_per_outcome": {
            "kind": token_kind,
            # The PER-OUTCOME figure needs the corpus measured; the measured TOTAL does
            # not, and is reported below either way. A floor that suppressed a number
            # already known would be the opposite error — see the fifth audit's
            # `no-verdict` over a spend that provably exceeded its ceiling.
            "value": (per_outcome(tokens) if measured and token_kind == MEASUREMENT
                      else None),
            "measured_sessions": len(measured),
            "finished_sessions": len(finished),
            "coverage": round(coverage, 4),
            "coverage_floor": COVERAGE_FLOOR,
            "tokens_measured": tokens if measured else None,
            "unmeasured_reason": (
                NEEDS["tokens_per_outcome"] if not (measured and outcomes) else
                (None if token_kind == MEASUREMENT else
                 f"{len(measured)} of {len(finished)} finished session(s) carry a token "
                 f"figure ({coverage:.1%}), under the {COVERAGE_FLOOR:.0%} floor: the "
                 f"unmeasured sessions are the ones that could move a per-outcome "
                 f"figure, so what is reported is the measured TOTAL as a lower bound "
                 f"and not a cost per outcome")),
            "caption": (f"{token_kind}: {len(measured)} of {len(finished)} finished "
                        f"session(s) carry a token figure ({coverage:.1%}, floor "
                        f"{COVERAGE_FLOOR:.0%}). Below the floor this is what is KNOWN "
                        f"to have been spent and the true figure is higher."),
        },
        "money_per_outcome": {
            # An ESTIMATE even at full coverage: it multiplies measured counts by prices
            # the OPERATOR declared, and a declared price is an assumption about a bill.
            "kind": ESTIMATE,
            "value": per_outcome(money),
            "currency": priced["currency"] if priced else None,
            "rates": priced,
            "by_model": money_breakdown,
            "attested_sessions": len(attested),
            "total": money,
            "unmeasured_reason": money_reason,
            "caption": ("estimate: measured token counts from provider-attested sessions "
                        "only, priced per model and per token category with rates the "
                        "operator declared. xcheck ships no price list."),
        },
        "wall_clock_seconds": {
            # The audit's own span is a MEASUREMENT; the sum of durations is not elapsed
            # time under a fan-out and is reported beside it, labelled, never instead.
            "kind": MEASUREMENT if span is not None else LOWER_BOUND,
            "p50": _percentile(durations, 50),
            "p95": _percentile(durations, 95),
            "sessions_timed": len(durations),
            "audit_span_s": span,
            "sum_of_sessions_s": round(sum(durations), 3) if durations else None,
            "per_outcome": per_outcome(span if span is not None else None),
            "unmeasured_reason": None if durations else NEEDS["wall_clock_seconds"],
            "caption": ("P50 and P95 are over the sessions that recorded a duration, "
                        "nearest-rank, so each is a session that happened. `per_outcome` "
                        "divides the AUDIT'S OWN SPAN — first event to last — by the "
                        "useful outcomes above. `sum_of_sessions_s` is shown beside it "
                        "and is NOT elapsed time: two 10-minute sessions run in parallel "
                        "take 10 minutes, and the sum overstates the clock by the "
                        "concurrency factor."),
        },
        "human_review_seconds_per_outcome": {
            "kind": MEASUREMENT,
            "value": per_outcome(review_seconds),
            "recorded": review_seconds,
            "unmeasured_reason": (None if review_seconds is not None and outcomes else
                                  NEEDS["human_review_seconds_per_outcome"]),
            "caption": "recorded, never derived. The gap between two timestamps is "
                       "elapsed time — a reviewer who left for lunch did not spend the "
                       "afternoon reading, and a figure built that way would flatter or "
                       "damn the tool at random.",
        },
    }
