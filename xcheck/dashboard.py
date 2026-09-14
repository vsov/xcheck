"""The dashboard: a thin view over `status --json` and `metrics --json`.

Phase 12, P2 #13. The design constraint IS the feature. A dashboard that computes its
own numbers becomes a second source of truth — the exact defect the 0.8.0 rebuild
removed from the Markdown control plane, re-introduced at the presentation layer where
nobody would look for it. So this module:

* takes the two payloads as arguments and reads nothing — no state, no events, no files;
* formats and never computes. There is no arithmetic here at all, and none of `len`,
  `sum`, `round`, `min`, `max` or `abs`. `tests/test_dashboard.py` asserts that
  structurally, over the AST, because a code review will not catch the fourth
  `f"{closed/total:.0%}"` somebody adds six months from now;
* emits one self-contained file: no CDN, no web font, no script, no image, no
  telemetry. A dashboard describing a private audit must not touch the network for any
  reason, so it contains nothing that could.

If a figure is wanted that neither payload carries, the fix is to add it to
`decision.metrics_report` — one definition, one reader — and render it here. Computing
it here would publish a number no JSON consumer can reproduce.

Every value is rendered from the payload as it stands. `None` is printed as a stated
absence with its reason, never as `0`, `None`, `NaN` or an empty percent sign: on a
fresh audit almost every metric is None, and "0.0%" there would be a claim ("this never
happened") the data does not support ("nothing has been measured yet").
"""

import html
import json

# (key, label, unit) for the eight pre-registered metrics, in the order
# `docs/ouroboros-4-preregistration.md` numbers them. Held as data so the renderer
# cannot know one metric from another — a special case for one figure is where a
# derived figure starts.
METRIC_ROWS = (
    ("auditor_accuracy_pct", "1 auditor accuracy (survived validation)", "%"),
    ("fix_durability_pct", "2 fix durability (closed first pass)", "%"),
    ("reopen_rate_pct", "3 reopen rate (of findings remediated)", "%"),
    ("false_closure_rate_pct", "5 false-closure rate", "%"),
    ("coverage_completeness_pct", "7 coverage completeness", "%"),
    ("recovery_after_kill_pct", "8 recovery after a killed session", "%"),
)

BREAKDOWN_ROWS = (
    ("recurrence_rate_pct", "recurrence rate", "%"),
    ("human_intervention_rate", "human interventions per finding", ""),
    ("seconds_per_closed_finding", "seconds per closed finding", ""),
    ("residue_pass_share_pct", "residue pass share", "%"),
    ("false_closures_found_later", "false closures found later", ""),
)

# Why a figure is absent, per key. A single "n/a" would make "nothing has happened yet"
# and "this cannot be measured from what happened" the same sentence.
ABSENT = {
    "auditor_accuracy_pct": "no finding has reached validation",
    "fix_durability_pct": "no finding has reached a verdict",
    "reopen_rate_pct": "no finding has reached remediation",
    "false_closure_rate_pct": "no closure has been recorded",
    "coverage_completeness_pct": "no pass is queued",
    "recovery_after_kill_pct": "no session was killed",
}

STYLE = """
:root { color-scheme: light dark; }
body { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
       margin: 2rem auto; max-width: 60rem; padding: 0 1rem; line-height: 1.5; }
h1 { font-size: 1.4rem; margin-bottom: 0.2rem; }
h2 { font-size: 1.05rem; margin-top: 2rem; border-bottom: 1px solid currentColor;
     padding-bottom: 0.2rem; }
table { border-collapse: collapse; width: 100%; }
td { padding: 0.25rem 0.6rem 0.25rem 0; vertical-align: top; }
td.v { text-align: right; white-space: nowrap; }
.sub { opacity: 0.7; font-size: 0.85rem; }
.empty { opacity: 0.7; font-style: italic; }
"""


def esc(value):
    """Text into HTML. Everything the payload carries goes through here."""
    return html.escape(str(value), quote=True)


def absent(key):
    """The stated reason a figure is not shown, never a zero standing in for one."""
    return f"not measured — {ABSENT.get(key, 'nothing has been recorded yet')}"


def scalar(value, unit=""):
    """One payload value as display text.

    A `None` never reaches here as a bare word: callers that hold a reason pass it to
    `absent` instead, and the fallback below still says absence rather than printing the
    Python spelling of it into a document an operator reads.
    """
    if value is None:
        return "not measured"
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return f"{esc(value)}{unit}"


def rows(pairs):
    out = []
    for label, value in pairs:
        out.append(f"<tr><td>{label}</td><td class='v'>{value}</td></tr>")
    return "\n".join(out)


def table(pairs, empty):
    """A two-column table, or a stated-empty line — never an empty table.

    An empty table on a fresh audit reads as "the data is missing"; the sentence says
    which of the two it is.
    """
    body = rows(pairs)
    if not body:
        return f"<p class='empty'>{esc(empty)}</p>"
    return f"<table>{body}</table>"


def counts(mapping, empty):
    """A dict of name -> count, exactly as the payload holds it."""
    pairs = []
    for key in sorted(mapping or {}):
        pairs.append((esc(key), scalar((mapping or {})[key])))
    return table(pairs, empty)


def metric_table(payload, spec):
    pairs = []
    for key, label, unit in spec:
        value = payload.get(key)
        shown = absent(key) if value is None else scalar(value, unit)
        pairs.append((esc(label), shown))
    return table(pairs, "no metric is available")


def render(status, metrics):
    """The whole page, from the two payloads and nothing else."""
    findings = status.get("findings") or {}
    passes = status.get("passes") or {}
    decision = status.get("decision") or {}
    lock = status.get("lock")
    m_findings = metrics.get("findings") or {}
    cf = metrics.get("class_findings") or {}
    indep = metrics.get("independence") or {}
    sessions = metrics.get("sessions") or {}
    hi = metrics.get("human_interventions") or {}
    cost = metrics.get("cost_per_accepted_finding") or {}
    breakdowns = metrics.get("breakdowns") or {}

    queued = passes.get("queued") or []
    detail = decision.get("detail")
    if isinstance(detail, list):
        detail = ", ".join(str(d) for d in detail)

    parts = []
    parts.append("<h1>xcheck dashboard</h1>")
    parts.append(f"<p class='sub'>{esc(status.get('project'))}<br>"
                 f"xcheck {esc(status.get('xcheck_version'))} · state schema "
                 f"{scalar(status.get('schema_version'))} · revision "
                 f"{scalar(status.get('state_revision'))} · head "
                 f"{scalar(status.get('head_before'))}</p>")

    parts.append("<h2>where the cycle is</h2>")
    parts.append(table([
        ("decision", esc(decision.get("kind"))),
        ("detail", esc(detail) if detail else "<span class='empty'>none</span>"),
        ("passes done", scalar(metrics.get("passes_done"))),
        ("passes queued", ", ".join(esc(q) for q in queued)
         if queued else "<span class='empty'>none queued</span>"),
        ("lock", esc(json.dumps(lock, sort_keys=True)) if lock
         else "<span class='empty'>not held</span>"),
    ], "nothing to report"))

    parts.append("<h2>findings</h2>")
    parts.append(table([("total", scalar(findings.get("total")))],
                       "no findings recorded"))
    parts.append("<p class='sub'>by status</p>")
    parts.append(counts(m_findings.get("by_status"), "no finding has a status yet"))
    parts.append("<p class='sub'>by severity</p>")
    parts.append(counts(m_findings.get("by_severity"), "no finding has been filed"))
    parts.append("<p class='sub'>by dimension</p>")
    parts.append(counts(m_findings.get("by_dimension"), "no finding has been filed"))
    parts.append("<p class='sub'>remediation attempts</p>")
    parts.append(counts(m_findings.get("attempts"), "nothing has been remediated"))
    parts.append("<p class='sub'>class findings</p>")
    parts.append(table([("ids", ", ".join(esc(c) for c in cf.get("ids") or []))]
                       if cf.get("ids") else [],
                       "no class finding has been raised"))

    parts.append("<h2>pre-registered metrics</h2>")
    parts.append(metric_table(metrics, METRIC_ROWS))
    parts.append("<p class='sub'>4 human interventions</p>")
    parts.append(table([
        ("total", scalar(hi.get("total"))),
        ("gate stops", scalar(hi.get("gates"))),
        ("norm rulings", scalar(hi.get("norm_rulings"))),
        ("human re-takes", scalar(hi.get("human_retakes"))),
    ] if hi else [], "no human intervention has been recorded"))
    parts.append("<p class='sub'>6 cost per accepted finding "
                 f"— proxy: {esc(cost.get('proxy'))}</p>" if cost.get("proxy")
                 else "<p class='sub'>6 cost per accepted finding</p>")
    parts.append(table([
        ("seconds per accepted finding",
         scalar(cost.get("seconds_per_accepted_finding"))),
        ("accepted findings", scalar(cost.get("accepted_findings"))),
        ("session seconds", scalar(cost.get("session_seconds"))),
    ] if cost else [], "no session time has been recorded"))
    parts.append("<p class='sub'>durability baseline (Ouroboros-2)</p>")
    parts.append(table([("baseline",
                         scalar(metrics.get("fix_durability_baseline_pct"), "%"))],
                       "no baseline is declared"))

    parts.append("<h2>breakdowns</h2>")
    parts.append(metric_table(breakdowns, BREAKDOWN_ROWS))
    parts.append("<p class='sub'>durability by dimension</p>")
    parts.append(counts(breakdowns.get("durability_by_dimension"),
                        "no dimension has a verdict yet"))
    parts.append("<p class='sub'>durability by fix type</p>")
    parts.append(counts(breakdowns.get("durability_by_fix_type"),
                        "no fix has been typed"))
    parts.append("<p class='sub'>reopen cause</p>")
    parts.append(counts(breakdowns.get("reopen_cause"), "nothing has been reopened"))
    # `breakdowns.verification_independence` is the SAME summary the section below
    # prints, key for key. Rendering it twice would put one figure in two places and
    # invite them to disagree the moment either side is reformatted.

    parts.append("<h2>independence and sessions</h2>")
    parts.append(table([
        ("verdicts measured", scalar(indep.get("measured"))),
        ("degraded", scalar(indep.get("degraded"))),
        ("ceiling", esc(indep.get("ceiling"))),
    ] if indep.get("measured") else [],
        "no verdict carries an independence level yet"))
    parts.append("<p class='sub'>by level</p>")
    parts.append(counts(indep.get("by_level"), "no level has been measured"))
    parts.append("<p class='sub'>sessions by outcome</p>")
    parts.append(counts(sessions.get("by_outcome"), "no session has been recorded"))
    parts.append("<p class='sub'>sessions by provider</p>")
    parts.append(counts(sessions.get("by_provider"), "no session has been recorded"))

    parts.append("<p class='sub'>Every figure above is copied from "
                 "<code>xcheck status --json</code> and <code>xcheck metrics --json</code>. "
                 "This page computes nothing: a number that is not in those two documents "
                 "is not on this page.</p>")

    body = "\n".join(parts)
    return "\n".join([
        "<!DOCTYPE html>",
        "<html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<title>xcheck dashboard</title>",
        f"<style>{STYLE}</style>",
        "</head><body>",
        body,
        "</body></html>",
        "",
    ])
