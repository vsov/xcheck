"""Ceilings that stop a run, priced on figures the tool actually measured.

The audit's number: **85.4% of Ouroboros-4's tokens were spent by sessions that recorded
no canonical transition**, and nothing in the system could stop that while it was
happening. Every gate here exists to make one of those ways of burning a budget
observable and stoppable at the moment it matters.

Four rules the module is built on, each of which a naive version gets wrong:

  1. **A ceiling checked once is walked through once.** A bound tested only at the start
     of a run is cleared by the first session and never asked again, which is exactly how
     a full `session_timeout` got spent under a wall-clock budget that had already
     approved it. Every gate here is evaluated at the DECISION point, before each
     dispatch, against the spend recorded so far.
  2. **Refuse when the REMAINING budget cannot cover the work**, not when it has already
     been exceeded. A ceiling that fires after the overspend has landed has described a
     fact rather than prevented one, so the estimate of one more session — the measured
     mean — is subtracted before the comparison.
  3. **Priced on MEASURED sessions only, with the coverage printed.** A ceiling enforced
     against a majority nobody measured is a guess with an exit code, so every gate
     message carries `sessions_measured of sessions_finished` beside its figure.
  4. **No money.** Tokens and seconds are measured; currency is not, and the tool prints
     no figure it cannot measure.

Off by default (`budgets`), so a project already inside these bounds sees no change.
"""

# The project conf key. A project key: the worst a wrong value does is stop an audit
# early or let it run long — it changes what this audit costs, not what the machine may do.
FLAG = "budgets"

# Every ceiling, as data. A test enumerates this and fails on one that is not wired.
CEILING_KEYS = ("tokens_per_session", "tokens_per_pass", "tokens_per_audit",
                "no_progress_share_pct", "charter_repeat_limit",
                "marginal_value_window")

# Each gate, and the key an operator raises to clear it. The refusal prints the key,
# because a gate that says "no" without saying which number to change is a wall.
GATE_KEY = {
    "stop-budget-session": "tokens_per_session",
    "stop-budget-pass": "tokens_per_pass",
    "stop-budget-audit": "tokens_per_audit",
    "stop-no-progress-share": "no_progress_share_pct",
    "stop-charter-repeats": "charter_repeat_limit",
    # PHASE 7: fires for `tokens_per_audit` OR `tokens_per_pass` — whichever measured
    # spend has already passed — so the message reads the key out of the detail and
    # this entry is only its default.
    "stop-known-overrun": "tokens_per_audit",
    "stop-marginal-value": "marginal_value_window",
}

# The reference figure, kept in the code rather than only in a document, because every
# derived default below is an arithmetic consequence of it.
OUROBOROS4_BARREN_SHARE_PCT = 85.4

# Derived defaults, each with the derivation it came from. These are what an operator
# ADOPTS; they are not the shipped defaults, because a shipped ceiling would change the
# behaviour of a project that never asked for one. `xcheck status` prints them.
DERIVED_DEFAULTS = {
    "marginal_value_window": (
        8,
        f"Ouroboros-4 spent {OUROBOROS4_BARREN_SHARE_PCT}% of its tokens on sessions "
        f"that moved nothing. At that rate a productive session arrives roughly every "
        f"1 / (1 - 0.854) = 6.8 sessions, so a run of 8 consecutive sessions with no new "
        f"accepted evidence is already past what the run's own history explains."),
    "no_progress_share_pct": (
        50,
        f"half. Ouroboros-4's {OUROBOROS4_BARREN_SHARE_PCT}% is the figure this bound "
        f"exists to have caught; 50 is the point at which more of the budget is buying "
        f"nothing than is buying something, which is a fact about the run rather than a "
        f"preference."),
    # PHASE 12 (fourth audit), derived where nothing could derive it before: per-pass
    # spend was a global average until attribution existed. The seven passes this
    # repository has actually measured cost 197,699 to 275,469 tokens, median 231,754 —
    # so a ceiling under ~275k stops work this project's own history calls normal. The
    # figure rests on 7 of 39 passes (4.4% telemetry coverage) and says so: it is offered
    # as a starting point, and the gate that would enforce it is STATISTICAL and refuses
    # to fire below the coverage floor for exactly this reason.
    "tokens_per_pass": (
        300000,
        "the largest pass this audit has measured cost 275,469 tokens and the median "
        "231,754, over 7 measured passes of 39 (4.4% coverage). 300,000 clears the "
        "observed maximum with headroom; a lower ceiling would refuse passes that this "
        "run's own record shows completing normally. RE-CHECKED in phase 7 (fifth "
        "audit), and one sentence of it was wrong: it said the gate would not fire "
        "until half the sessions were measured. That is now true of one half only. "
        "`this pass has ALREADY spent more than the ceiling` is measured, so it is HARD "
        "and fires at any coverage; `one more session would put it over` extrapolates "
        "from the measured mean and still waits for the floor. Low coverage delays the "
        "forecast, not the arithmetic."),
    "charter_repeat_limit": (
        3,
        "three attempts at one charter. The fourth is the first that cannot be explained "
        "by a transient provider fault plus one genuine retry. RE-DERIVED in phase 12 "
        "against this repository's 23 dispatched charters: 15 sit at or under 3 and 8 "
        "run over, up to 54 attempts on a single charter. The bound is unchanged and the "
        "data is why — those 8 are the runs it exists to stop, not evidence that 3 is "
        "too low. This gate is HARD: counting one charter's own dispatches extrapolates "
        "from nothing, so it fires below the coverage floor."),
}


def budget_enabled(conf):
    """`True` when the budget gates are armed. Off by default; unknown values are off."""
    from xcheck.util import conf_flag
    return conf_flag(conf, FLAG)


def _ceiling(conf, key):
    """A ceiling as an int, where 0 means OFF.

    0 is `off` and never `refuse everything`: a budget of zero tokens would stop every
    dispatch the moment the key was typed, which is the opposite of what an operator
    setting a budget wants and is indistinguishable from a typo."""
    from xcheck.util import conf_number
    try:
        return max(0, int(conf_number(conf, key) or 0))
    except (TypeError, ValueError):
        return 0


def spend(state, events):
    """The measured token account for this audit, from the one implementation that
    computes it. Not re-derived here: a second reader of the same field is a second
    answer waiting to disagree with the first."""
    from xcheck.decision import metrics_report
    return metrics_report(state, events)["tokens"]


def coverage_note(tok):
    """The sentence every gate message carries. A ceiling enforced against an unmeasured
    majority is a guess with an exit code, so the coverage goes beside the figure."""
    return (f"measured on {tok['sessions_measured']} of {tok['sessions_finished']} "
            f"finished session(s) ({tok['coverage_pct']}%)")


def mean_session_cost(tok):
    """The estimate of one more session: the measured mean. None when nothing is
    measured, which is what stops an unmeasured run from being gated on a guess."""
    n, total = tok["sessions_measured"], tok["total"]
    return round(total / n) if n and isinstance(total, int) else None


def charter_key(charter):
    """The one way a charter becomes an event key. Used by the counter AND by the caller
    that has the charter TEXT, so the two cannot disagree by hand.

    PHASE 5 (fourth audit). This counter keyed on `charter` — a field `envelope` has
    never emitted. Over this repository's own stream: 159 `session_dispatched` events, 0
    carrying `charter`, 159 carrying `charter_hash`. The gate was therefore unreachable
    on every real run, and its test passed because the test built the event itself and
    invented the field. That is the audit's central charge in one function.

    `envelope.sha256_text` is imported rather than reimplemented: a second hashing site
    that has to agree with the first by hand is the next version of the same bug.
    """
    from xcheck.envelope import sha256_text
    return sha256_text((charter or "").strip())


def charter_attempts(events):
    """How many times each charter has been dispatched, keyed by `charter_hash` — the
    field the production emitter actually writes.

    Distinct from the embargo, deliberately: the embargo counts CONSECUTIVE sessions that
    declared no progress, and clears when one does. This counts dispatches whether they
    progressed or not, which is the bound on a charter that makes a little progress
    forever."""
    counts = {}
    for e in events or ():
        if e.get("event") == "session_dispatched":
            key = (e.get("charter_hash") or "").strip()
            if key:
                counts[key] = counts.get(key, 0) + 1
    return counts


def sessions_since_accepted_evidence(events):
    """Finished sessions since the last transition into `accepted`.

    ACCEPTED EVIDENCE, not sessions: the audit's complaint is that sessions kept being
    bought while nothing was accepted, so counting sessions that ran would measure the
    symptom as if it were the cure."""
    n = 0
    for e in events or ():
        if e.get("event") == "state_transition" and e.get("to_status") == "accepted":
            n = 0
        elif e.get("event") == "session_finished":
            n += 1
    return n


def known_overrun(state, conf, events, tok, charter=None):
    """The detail for `stop-known-overrun`, or None. One rule per HARD_CEILING token key.

    Deliberately NOT a comparison against an estimate: every figure here is spend that
    has been measured on a finished session. A ceiling this already exceeds is exceeded.
    """
    measured = tok.get("total") or 0
    checks = [("tokens_per_audit", measured, "this audit")]
    if charter:
        spent_here, _counts = current_pass_spend(state, events, _pass_of(state, charter))
        if spent_here:
            checks.append(("tokens_per_pass", spent_here, "this pass"))
    for key, spent, what in checks:
        cap = _ceiling(conf, key)
        if cap and spent >= cap:
            blind = tok.get("sessions_unmeasured") or 0
            return {
                "key": key, "measured": spent, "cap": cap,
                "coverage_pct": tok.get("coverage_pct"),
                "why": (f"{what} has ALREADY spent {spent:,} measured tokens against a "
                        f"{cap:,}-token `{key}` ceiling. This is not an estimate and the "
                        f"coverage floor does not apply to it: {coverage_note(tok)} is a "
                        f"LOWER bound, and the {blind} unmeasured session(s) can only "
                        f"add to it. A run that is over its ceiling on the sessions we "
                        f"CAN see is over its ceiling.")}
    return None


def cap_verdict(conf, cmd):
    """`(verdict, message)` about the per-dispatch cap. Verdict is one of three.

    PHASE 7 (fifth audit). `cap_problem` checked that `{token_cap}` was PRESENT in the
    role command and nothing else, so `true {token_cap} {prompt}` passed — a command
    that enforces nothing and consumes the number as an argument. xcheck cannot verify an
    operator's wrapper: the flag belongs to their provider CLI and running it to find out
    is the finding this project already closed once (F-0098). What it can do is stop
    implying enforcement it has not seen.

      `no-ceiling`  nothing is configured, so there is nothing to claim.
      `refused`     the ceiling has nowhere to go — a forecast with an exit code.
      `unverified`  the number reaches the command, and whether the provider honours it
                    is a fact about the operator's wrapper that xcheck has not observed.

    There is deliberately no `enforced`. It would need a measured session whose provider
    reported hitting the limit, which is phase 12's question, not this one.
    """
    cap = dispatch_cap(conf)
    if not cap or not budget_enabled(conf) or cmd is None:
        return "no-ceiling", None
    bad = cap_problem(conf, cmd)
    if bad:
        return "refused", bad
    return "unverified", (
        f"`{CAP_PLACEHOLDER}` is filled with {cap:,} in this role command, and xcheck has "
        f"NOT verified that anything enforces it: which flag limits output is a fact "
        f"about your provider CLI, and running it to find out would execute the subject "
        f"before any sandbox exists. `true {CAP_PLACEHOLDER} {{prompt}}` would pass this "
        f"check too.\n"
        f"Note also what this ceiling is: {cap:,} bounds ONE request's output. An agent "
        f"session makes several model calls and pays for its input as well, so a session "
        f"total is larger — often several times larger — and `tokens_per_session` does "
        f"not bound it. Use `tokens_per_audit` for a spend ceiling.\n"
        f"It becomes verified when a provider-attested session reports a stop reason at "
        f"the limit; until then this is a number that was passed, not a limit that was "
        f"applied.")


def gate_message(kind, detail):
    """The refusal, in one paragraph: what fired, the figure behind it, the coverage of
    that figure, and the key to raise. No currency anywhere — the tool measures tokens
    and seconds and prints no money it cannot measure."""
    key = detail.get("key") or GATE_KEY[kind]
    return f"{detail['why']} Raise `{key}` to continue, or clear the condition it names."


def gates(state, conf, events, charter=None, cmd=None):
    """The first budget gate that fires, as `(kind, detail)`, or None.

    Evaluated at the DECISION point rather than once at the start of a run: a ceiling
    that is only a precondition gets walked through exactly once, by the session that was
    already in flight when it was checked.
    """
    if not budget_enabled(conf):
        return None
    tok = spend(state, events)
    cov = coverage_note(tok)
    total = tok["total"] or 0
    estimate = mean_session_cost(tok)

    # --- HARD, and therefore first and floor-exempt: a per-dispatch ceiling the provider
    # is told about before the model runs needs no history to be correct.
    bad = cap_problem(conf, cmd)
    if bad:
        return "stop-dispatch-cap", {"cap": dispatch_cap(conf), "why": bad}

    # --- one charter, re-dispatched forever. Also HARD: counting a charter's own
    # dispatches is not an extrapolation from a sample.
    repeat_cap = _ceiling(conf, "charter_repeat_limit")
    if repeat_cap and charter:
        attempts = charter_attempts(events).get(charter_key(charter), 0)
        if attempts >= repeat_cap:
            return "stop-charter-repeats", {
                "charter": charter, "attempts": attempts, "cap": repeat_cap,
                "why": (f"the charter `{charter}` has been dispatched {attempts} time(s), "
                        f"at the {repeat_cap}-attempt ceiling. A charter that has not "
                        f"succeeded in {attempts} attempts is a charter to change, not "
                        f"one to buy again.")}

    # --- HARD: a ceiling measured spend has ALREADY passed. Above the floor on purpose.
    #
    # The audit's finding, in one sentence: 1,000 tokens measured against a 100-token
    # ceiling at 33% coverage returned `no-verdict` and the run continued. The floor
    # exists because extrapolating a mean from a minority is a forecast — but this is not
    # an extrapolation. Measured spend is a LOWER BOUND, and the sessions nobody measured
    # can only add to it, so an audit ten times over its ceiling is over its ceiling
    # whatever the coverage turns out to be. `known` is what is already spent; `blind` is
    # what has not been measured and is stated rather than assumed to be zero.
    overrun = known_overrun(state, conf, events, tok, charter=charter)
    if overrun:
        return "stop-known-overrun", overrun

    # --- everything below EXTRAPOLATES from the measured sessions to the unmeasured
    # ones, so below the floor it refuses to judge rather than passing quietly.
    thin = coverage_floor_problem(tok)
    if thin:
        return NO_VERDICT, {"coverage_pct": tok["coverage_pct"],
                            "floor": COVERAGE_FLOOR_PCT,
                            "statistical": list(STATISTICAL_GATES),
                            "hard": list(HARD_GATES), "why": thin}

    # --- per audit, per session: refuse when the REMAINING budget cannot cover one more
    # session at the measured mean, not after the overspend has already landed.
    audit_cap = _ceiling(conf, "tokens_per_audit")
    if audit_cap and estimate is not None:
        remaining = audit_cap - total
        if remaining < estimate:
            return "stop-budget-audit", {
                "spent": total, "cap": audit_cap, "estimate": estimate,
                "why": (f"the audit has spent {total:,} of its {audit_cap:,}-token "
                        f"budget, and the next session is estimated at {estimate:,} "
                        f"tokens ({cov}) — {remaining:,} remaining cannot cover it, so "
                        f"it is refused BEFORE dispatch rather than reported after.")}

    session_cap = _ceiling(conf, "tokens_per_session")
    if session_cap and estimate is not None and estimate > session_cap:
        return "stop-budget-session", {
            "estimate": estimate, "cap": session_cap,
            "why": (f"a session on this audit costs {estimate:,} tokens on average "
                    f"({cov}), which is over the {session_cap:,}-token per-session "
                    f"ceiling.")}

    pass_cap = _ceiling(conf, "tokens_per_pass")
    if pass_cap and estimate is not None and charter:
        # THIS pass, attributed session by session — not the audit's history averaged
        # over its done passes, which is what the previous implementation compared.
        spent_here, (measured, dispatched) = current_pass_spend(
            state, events, _pass_of(state, charter))
        if spent_here is not None and spent_here + estimate > pass_cap:
            return "stop-budget-pass", {
                "per_pass": spent_here, "cap": pass_cap, "estimate": estimate,
                "measured": measured, "dispatched": dispatched,
                "why": (f"this pass has spent {spent_here:,} tokens over "
                        f"{measured} measured of {dispatched} dispatched session(s), "
                        f"and one more at {estimate:,} would put it over its "
                        f"{pass_cap:,}-token ceiling ({cov}).")}

    # --- the audit's own number: tokens that bought no canonical transition.
    share_cap = _ceiling(conf, "no_progress_share_pct")
    share = tok["share_that_moved_nothing_pct"]
    if share_cap and share is not None and share > share_cap:
        return "stop-no-progress-share", {
            "share": share, "cap": share_cap, "reference": OUROBOROS4_BARREN_SHARE_PCT,
            "why": (f"{share}% of the measured tokens were spent by sessions that "
                    f"recorded no canonical transition ({cov}), over the {share_cap}% "
                    f"bound. Ouroboros-4 reached "
                    f"{OUROBOROS4_BARREN_SHARE_PCT}% with nothing able to stop it, "
                    f"which is the run this gate exists because of.")}

    # --- the marginal-value rule. A STOP, not a warning.
    window = _ceiling(conf, "marginal_value_window")
    if window:
        idle = sessions_since_accepted_evidence(events)
        if idle >= window:
            derived, derivation = DERIVED_DEFAULTS["marginal_value_window"]
            return "stop-marginal-value", {
                "idle": idle, "window": window, "derived_default": derived,
                "derivation": derivation,
                "why": (f"the last {idle} finished session(s) produced no new accepted "
                        f"evidence, at or past the {window}-session marginal-value "
                        f"window ({cov}). Generation STOPS here rather than warning: "
                        f"the derived default is {derived}, because {derivation}")}
    return None


def _spend_per_pass(tok, state):
    """Measured tokens divided by the passes that are actually done.

    None while nothing is done — a per-pass figure over zero passes is a division nobody
    can defend, and reporting it as 0 would read as a free audit."""
    done = [q for q in state.queue if q.coverage and q.coverage.status == "done"]
    total = tok["total"]
    return round(total / len(done)) if done and isinstance(total, int) else None


def derived_report():
    """The derived defaults an operator may adopt, each with its derivation. Printed by
    `xcheck status` so the numbers are not folklore in a commit message."""
    return [f"{key}={value} — {why}" for key, (value, why) in
            sorted(DERIVED_DEFAULTS.items())]


# ------------------------------------------------------- PHASE 12: what may be judged

# The audit's arithmetic: at 4.4% telemetry coverage, a ceiling enforced against a
# measured mean is a forecast dressed as a cap. Seven sessions decided the mean for 158,
# and the first session that costs more than that mean walks straight through.
#
# So the gates are SPLIT, and the split is declared here rather than implied by which
# ones happen to read `tok`. A HARD gate answers a question about one dispatch and needs
# no history at all. A STATISTICAL gate extrapolates from measured sessions to unmeasured
# ones, and below the floor it must REFUSE TO JUDGE — which is a third outcome, not a
# quiet pass. A gate that cannot see enough to decide and returns `None` has told its
# caller everything is fine.
STATISTICAL_GATES = ("stop-budget-session", "stop-budget-audit", "stop-budget-pass",
                     "stop-no-progress-share", "stop-marginal-value")
HARD_GATES = ("stop-charter-repeats", "stop-dispatch-cap", "stop-known-overrun")

# PHASE 7 (fifth audit). Every ceiling gets one of two verdicts, and both are declared
# rather than implied by which branch happens to run first. The audit's case: 1,000
# tokens measured against a 100-token audit ceiling at 33% coverage returned
# `no-verdict`, and the decision engine continued — because the ceiling was reached
# through the statistical door. The uncertainty was irrelevant. Measured spend is a
# LOWER BOUND: the unmeasured sessions can only add to it, so an audit already ten times
# over its ceiling is over it whatever the coverage turns out to be.
#
# The split is not "which gates matter" — it is which QUESTION a ceiling asks. "Has this
# already cost more than X?" is answerable from measurement alone. "Will the next
# session cost more than X?" is not.
HARD_CEILING = {
    "tokens_per_audit": "measured spend is a LOWER BOUND on what this audit has cost; "
                        "unmeasured sessions can only raise it",
    "tokens_per_pass": "measured spend attributed to this pass is a LOWER BOUND on it, "
                       "session by session",
    "charter_repeat_limit": "counting a charter's own dispatches is not an "
                            "extrapolation from a sample",
}
STATISTICAL_CEILING = {
    "tokens_per_session": "the ceiling is about the NEXT session, whose cost is an "
                          "estimate from the measured ones and not a fact yet",
    "no_progress_share_pct": "a share computed over the measured sessions is being "
                             "read as the share over all of them",
    "marginal_value_window": "the trend needs the unmeasured sessions to resemble the "
                             "measured ones, which is the assumption Ouroboros-4 broke",
}

# 50%. Not tuned and not a preference: below half, the unmeasured sessions outnumber the
# measured ones, so the mean is decided by the minority and the majority is assumed to
# resemble it. That assumption is exactly what Ouroboros-4 disproved — 85.4% of its spend
# went to sessions that moved nothing, which no mean over the productive ones predicts.
COVERAGE_FLOOR_PCT = 50.0

NO_VERDICT = "no-verdict"


def coverage_floor_problem(tok, floor=COVERAGE_FLOOR_PCT):
    """Why the statistical gates may not fire, or None. Names the figure and the floor."""
    got = tok.get("coverage_pct")
    if got is None or got >= floor:
        return None
    return (f"REFUSING TO JUDGE: the statistical budget gates extrapolate from measured "
            f"sessions, and this audit has measured {tok['sessions_measured']} of "
            f"{tok['sessions_finished']} finished session(s) — {got}%, under the "
            f"{floor}% floor. A ceiling enforced against an unmeasured majority is a "
            f"forecast with an exit code. This is NOT a pass: the gates "
            f"({', '.join(STATISTICAL_GATES)}) have no verdict here. The hard gates "
            f"({', '.join(HARD_GATES)}) are unaffected — a per-dispatch limit needs no "
            f"history. Raise coverage by configuring `telemetry_adapter` and a sidecar, "
            f"or run with the hard caps alone.")


# ------------------------------------------------------------- PHASE 12: attribution

UNATTRIBUTED = "unattributed"


def attribution(state, events):
    """`{session_id: (pass_id, charter_hash)}` for every dispatched session.

    A session whose charter resolves to no queue entry maps to `(UNATTRIBUTED, hash)`
    rather than being dropped: a per-pass figure that silently omits the sessions it
    could not place is a figure that improves as attribution gets worse.

    The join is `charter_hash`, which the dispatch event has carried since phase 5, and
    the queue entry's own id hashed the same way — `charter_key` is used by BOTH sides so
    the two cannot drift apart by hand. On this repository's real stream all 159
    dispatches resolve, across 23 distinct charters.
    """
    by_hash = {}
    for q in state.queue:
        by_hash[charter_key(q.id)] = q.id
        if q.charter:
            by_hash.setdefault(charter_key(q.charter), q.id)
    out = {}
    for e in events:
        if e.get("event") != "session_dispatched":
            continue
        h = (e.get("charter_hash") or "").strip()
        sid = e.get("session_id")
        if sid:
            out[sid] = (by_hash.get(h, UNATTRIBUTED), h)
    return out


def attribution_report(state, events):
    """`(rows, unattributable)` — one row per pass, and the sessions nothing could place.

    `rows` is `[(pass_id, dispatched, measured, tokens)]`, sorted. The unattributable list
    carries the REASON, because "3 sessions could not be attributed" is a number an
    operator can do nothing with.
    """
    placed = attribution(state, events)
    # The figure lives on the SESSION RECORD, not on the finish event: `envelope.finish`
    # builds it and `state.sessions` is where it lands. Reading it off the event gave 0
    # measured for all 23 passes on this repository's own corpus, which reads as an audit
    # that cost nothing — `unmeasured-is-not-zero`, arrived at from the other direction.
    tokens = {s.get("session_id"): s.get("tokens") for s in state.sessions}
    rows, unplaced = {}, []
    for sid, (pass_id, h) in sorted(placed.items()):
        if pass_id == UNATTRIBUTED:
            unplaced.append((sid, f"charter_hash {h[:12] or '(none)'} matches no queue "
                                  f"entry — the pass may have been cancelled, or the "
                                  f"charter was composed outside the queue"))
            continue
        d, m, t = rows.get(pass_id, (0, 0, 0))
        got = tokens.get(sid)
        rows[pass_id] = (d + 1, m + (got is not None), t + (got or 0))
    return sorted((p,) + v for p, v in rows.items()), unplaced


def _pass_of(state, charter):
    """Which queue pass this dispatch charter names, or None.

    The same join `attribution` uses, from the charter TEXT rather than its hash, because
    the decision has the text in hand and hashing it here would be the second
    implementation of one mapping.
    """
    if not charter:
        return None
    for q in state.queue:
        if charter == q.id or (q.charter and charter == q.charter):
            return q.id
    return None


def current_pass_spend(state, events, pass_id):
    """Measured tokens spent on ONE pass, and how many of its sessions that covers.

    PHASE 12 (fourth audit). `_spend_per_pass` divided the whole audit's spend by the
    passes that were done — 1,666,799 / 22 = 75,764 on this repository — and quoted the
    result as though it described the pass about to be dispatched. It is a global average
    over the audit's entire history, including passes that ran under a different charter,
    a different model and a different capsule. A ceiling compared against it stops a cheap
    pass because an expensive one happened last week.
    """
    if not pass_id:
        return None, (0, 0)
    for row in attribution_report(state, events)[0]:
        if row[0] == pass_id:
            return row[3], (row[2], row[1])
    return 0, (0, 0)


# ------------------------------------------------------ PHASE 12: the hard dispatch cap

CAP_PLACEHOLDER = "{token_cap}"


def dispatch_cap(conf):
    """The hard per-dispatch ceiling, or 0. `tokens_per_session` is the number."""
    return _ceiling(conf, "tokens_per_session")


def cap_problem(conf, cmd):
    """Why this role command cannot enforce the per-session cap, or None.

    A cap the provider never hears is a forecast. `tokens_per_session` set with no
    `{token_cap}` in the command means the number is compared against a measured mean
    AFTER sessions have run — which is the audit's whole complaint, and it is refused
    rather than warned about, because an operator who set a ceiling believes they have
    one.

    xcheck does not know a provider's flag name and does not guess: the OPERATOR writes
    `--max-tokens {token_cap}` (or whatever their CLI calls it) and xcheck supplies the
    number. A tool that invented the flag would be choosing a provider it was not told
    about, the same error `telemetry_adapter` exists to avoid.
    """
    cap = dispatch_cap(conf)
    if not cap or not budget_enabled(conf) or cmd is None:
        # `cmd is None` is "no command was offered for inspection", which is not the same
        # as "a command with no cap" and must not be refused as one. The check belongs at
        # the dispatch decision, where the role command is known; a caller asking about
        # ceilings in the abstract is not about to run anything.
        return None
    if CAP_PLACEHOLDER in cmd:
        return None
    return (f"tokens_per_session={cap:,} is set and this role command carries no "
            f"`{CAP_PLACEHOLDER}`, so the ceiling would be checked against a measured "
            f"mean AFTER the session ran instead of being passed to the provider before "
            f"it. Put `{CAP_PLACEHOLDER}` in the command where your CLI takes its output "
            f"limit (for example `--max-tokens {CAP_PLACEHOLDER}`), or clear "
            f"`tokens_per_session`.")
