"""Diff-directed scope: audit what changed, and keep a way to audit everything.

EXPERIMENTAL — not reachable from any production path.

Phase 2 of the fourth audit's response withdrew the public conf key that offered this:
`diff_scope=on` narrowed nothing, because `selection()` was called by no dispatch path.
The module is kept, tested and correct in isolation; what it is not is CALLED. A flag
whose feature no code path reaches is a lie the configuration file tells, so the flag
went and the library stayed.

Promotion condition, stated so it is a decision rather than a drift: `cli.cmd_next`
calls `selection()` and passes the result into the capsule the session is dispatched
with, with the base recorded on the pass — and `tests/test_feature_truthfulness.py` is
what will then require a production call site before the key may be public again.

The audit's economics point: auditing an unchanged tree from scratch costs the same as
auditing a changed one, and most audits follow a small change. So a pass can be scoped
to what moved since a named base — the files git reports, EXPANDED across the import
edges phase 11 already computes, because a change to a widely-imported module is a
change to everything that imports it.

The audit's warning about this, kept as a first-class feature rather than a caveat:
**incremental mode without a periodic full sweep becomes a blind spot.** A defect in a
file nobody has touched for a year is exactly the defect no diff will ever select. So
`sweep_status()` reports how long it has been since a pass ran unscoped, and the status
output says it out loud. That report is the mitigation, and it is the reason this
module is allowed to exist.

Three things it deliberately is NOT:

  - It is not on. `diff_scope` is off by default and it is an OPTIMISATION, not a
    policy: an operator who never opts in sees no behaviour change at all.
  - It is not a clean bill of health. When the diff selects nothing, that is reported as
    "nothing changed in scope since <base>" and never as an audit that found nothing.
  - It is not a claim about the code it did not read. Every scoped pass records the base
    it was scoped against, so a later reader can tell an audit of everything from an
    audit of a diff without asking anyone.
"""

from xcheck import preprocess

# The project conf key. A PROJECT key, not an operator one: it describes what this
# audit is about, and getting it wrong makes the audit narrower rather than the machine
# less contained.
FLAG = "diff_scope"


def _module_name(path):
    """`xcheck/util.py` -> `xcheck.util`, the form the import edges use."""
    return path[:-3].replace("/", ".").removesuffix(".__init__") if path.endswith(".py") \
        else None


def importers(sheet, paths):
    """Files that import any of `paths`, one hop. The reverse of the edge table.

    One hop, not the transitive closure, and that is a stated limit rather than an
    oversight: the closure over a package this size is most of the package, which is a
    scope indistinguishable from the full sweep while claiming to be cheaper.
    """
    names = {m for m in (_module_name(p) for p in paths) if m}
    return sorted({e["from"] for e in sheet["dependencies"]["edges"]
                   if e["to"] in names})


def neighbourhood(sheet, changed):
    """`changed` plus everything that imports it. A SUPERSET of `changed`, always."""
    return sorted(set(changed) | set(importers(sheet, changed)))


def units_for(state, paths):
    """The unit ids whose declared material names any of `paths`.

    Uses the same `capsule.material_paths` parser the capsule uses, so a unit's material
    is read one way in this project and not two.
    """
    from xcheck.capsule import material_paths
    want = set(paths)
    hit = []
    for u in state.catalogs.units:
        declared = set(material_paths(u.material))
        if declared & want:
            hit.append(u.id)
    return sorted(hit)


def select(state, sheet, base):
    """What a diff-scoped run would audit, and everything a reader needs to check it.

    Returns the changed files, the expanded neighbourhood, the units those map to, the
    queued passes over those units and the dimensions in play — plus a `note` that says
    in words what an empty selection means, because an empty list and a clean audit look
    identical to anyone reading only the count.
    """
    changed = list(sheet["changed"]["files"])
    grown = neighbourhood(sheet, changed)
    units = units_for(state, grown)
    passes = [q for q in state.queue if set(q.units) & set(units)]
    dims = sorted({q.dimension for q in passes})
    sel = {
        "base": base,
        "changed": changed,
        "neighbourhood": grown,
        "reached_by_dependency": sorted(set(grown) - set(changed)),
        "units": units,
        "passes": [q.id for q in passes],
        "dimensions": dims,
    }
    if not changed:
        sel["note"] = (f"nothing changed in scope since {base} — this is a statement "
                       f"about the DIFF, not an audit result: no pass ran, and no "
                       f"finding was looked for")
    elif not units:
        sel["note"] = (f"{len(grown)} file(s) changed since {base} but none of them is "
                       f"declared material of any unit in the catalog, so no pass is "
                       f"selected. That is a gap in the unit map, not a clean tree")
    else:
        sel["note"] = (f"scoped to {len(units)} unit(s) over {len(passes)} queued "
                       f"pass(es), from {len(changed)} changed file(s) expanded to "
                       f"{len(grown)} across the import edges. A defect outside this "
                       f"scope will not be looked for by this run")
    return sel


def scope_rows(state, sel):
    """`(unit, files, passes, dimension)` per selected unit — the printable table."""
    from xcheck.capsule import material_paths
    by_unit = {u.id: u for u in state.catalogs.units}
    grown = set(sel["neighbourhood"])
    rows = []
    for uid in sel["units"]:
        u = by_unit.get(uid)
        files = sorted(set(material_paths(u.material if u else "")) & grown)
        passes = [q.id for q in state.queue if uid in q.units]
        rows.append({"unit": uid, "files": files, "passes": passes,
                     "dimensions": sorted({q.dimension for q in state.queue
                                           if uid in q.units})})
    return rows


def sweep_status(state, today=None):
    """How long since a pass ran UNSCOPED, and what that means.

    A full sweep is a completed pass with no `base`: it was scoped to the whole unit,
    not to a diff. Derived from the record rather than announced by a verb — a "last
    sweep" field somebody has to remember to update is a field that will be wrong, and
    the passes already say what they were scoped against.
    """
    import datetime
    today = today or datetime.date.today().isoformat()
    full = [q for q in state.queue
            if q.done and not getattr(q, "base", None) and q.coverage]
    dated = sorted((q.coverage.updated, q.id) for q in full if q.coverage.updated)
    if not dated:
        return {"last": None, "pass": None, "days": None,
                "note": "no pass in this queue has completed unscoped, so there is no "
                        "full sweep to be stale — every result so far is either "
                        "diff-scoped or not yet run"}
    when, pid = dated[-1]
    try:
        days = (datetime.date.fromisoformat(today)
                - datetime.date.fromisoformat(when)).days
    except ValueError:
        days = None
    return {"last": when, "pass": pid, "days": days,
            "note": (f"the last unscoped pass was {pid} on {when}"
                     + (f", {days} day(s) ago" if days is not None else "")
                     + ". Diff-scoped runs never look at a file nobody has touched, so "
                       "this number is the age of the last look at everything.")}


def selection(project, state, conf, base):
    """The whole answer for one run, or None when diff mode is off.

    Off is the default and returning None is how that is said: a caller that gets None
    runs exactly what it ran before this module existed.
    """
    from xcheck.util import conf_flag
    if not conf_flag(conf, FLAG):
        return None
    sheet = preprocess.build_sheet(project, base=base)
    return select(state, sheet, base)
