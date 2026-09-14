"""Phase 12: the dashboard is a VIEW, and this module is the proof it stays one.

A dashboard that computes its own numbers is a second source of truth — the defect the
0.8.0 rebuild removed from the Markdown control plane, re-introduced where nobody would
look for it. A promise like "it only formats" decays, so it is asserted three ways:

* an **open-spy** (the shared `tests.harness.open_spy`) showing the command reads
  nothing outside `audit/`;
* a **traceability walk** over the rendered page: every figure must appear in
  `status --json` or `metrics --json`, demonstrated by planting one that does not;
* an **AST assertion** that `xcheck/dashboard.py` performs no arithmetic and no
  aggregation, demonstrated by running the same checker over a planted `/`.

Plus the four degenerate audits, which matter more than the happy path: `0/0 = NaN%`
on a fresh audit is the first thing a new operator would ever see.
"""

import ast
import html as html_mod
import re
import unittest
from pathlib import Path

from tests.harness import (Fixture, REPO, finding_record, open_spy, queue_pass,
                           state_doc, xcheck_module, xcheck_submodule)

cli = xcheck_module()
dashboard = xcheck_submodule("dashboard")
util = xcheck_submodule("util")

SOURCE = (REPO / "xcheck" / "dashboard.py").read_text(encoding="utf-8")

# The pre-registration numbers the eight metrics 1..8, and those indices are part of the
# LABELS this module writes — they are not figures and they are not in the payloads. The
# walk below allows them in label cells only, and allows nothing else there.
METRIC_INDICES = {"1", "2", "3", "4", "5", "6", "7", "8"}

NUMBER = re.compile(r"\d+(?:\.\d+)?")
VALUE_CELL = re.compile(r"<td class='v'>(.*?)</td>", re.S)
SUBTITLE = re.compile(r"<p class='sub'>(.*?)</p>", re.S)


def visible(fragment):
    """Tag-free, entity-free text — what a reader actually sees."""
    text = re.sub(r"<style>.*?</style>", " ", fragment, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return html_mod.unescape(text)


def source_scalars(*payloads):
    """Every key and every scalar in the two payloads, as strings.

    Keys count: `attempts: {"0": 3}` renders the key `0` as a row label, and that 0 IS
    in the source document. A number that is in neither the keys nor the values is the
    thing this set exists to catch.
    """
    out = set()

    def walk(v):
        if isinstance(v, dict):
            for k, val in v.items():
                out.add(str(k))
                walk(val)
        elif isinstance(v, (list, tuple)):
            for item in v:
                walk(item)
        else:
            out.add(str(v))

    for p in payloads:
        walk(p)
    return out


def untraceable(page, status, metrics):
    """Figures on the page that are in neither payload.

    Scope, stated: VALUE cells and the subtitle lines — every place a payload value is
    rendered. Substring rather than equality, because `0000000` is a prefix of the head
    sha and `70.3` sits inside `70.3%`.
    """
    hay = source_scalars(status, metrics)
    bad = []
    for cell in VALUE_CELL.findall(page):
        for token in NUMBER.findall(visible(cell)):
            if not any(token in s for s in hay):
                bad.append(token)
    for sub in SUBTITLE.findall(page):
        for token in NUMBER.findall(visible(sub)):
            if token in METRIC_INDICES:
                continue                      # "6 cost per accepted finding" — a label
            if not any(token in s for s in hay):
                bad.append(token)
    return bad


def label_numbers(page):
    """Numbers in LABEL text — everything that is not a rendered value.

    Value cells and subtitle lines carry payload values and are checked by
    `untraceable`; what is left is text this module authored, where the only numbers
    allowed are the pre-registration indices. Without this second rule the walk would
    have an escape hatch: move a figure into a label and it stops being checked.
    """
    labels = re.sub(r"<td class='v'>.*?</td>", " ", page, flags=re.S)
    labels = re.sub(r"<p class='sub'>.*?</p>", " ", labels, flags=re.S)
    return [t for t in NUMBER.findall(visible(labels))]


# ---------------------------------------------------------------------------
# the AST checker — structural, so a `/` inside a comprehension is still caught
# ---------------------------------------------------------------------------

ARITHMETIC = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
              ast.MatMult)
AGGREGATIONS = {"len", "sum", "round", "abs", "min", "max", "divmod", "pow"}


def arithmetic_in(source, where="xcheck/dashboard.py"):
    """Every computation in `source`, as addressed strings.

    `+` is included even though string concatenation is harmless: allowing it would
    require this checker to infer types, and a checker that guesses is one a derived
    percentage can be smuggled past. The module uses f-strings and `join` instead, which
    is no worse to read.
    """
    found = []
    for node in ast.walk(ast.parse(source, filename=where)):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ARITHMETIC):
            found.append(f"{where}:{node.lineno}: {type(node.op).__name__} expression")
        elif isinstance(node, ast.AugAssign) and isinstance(node.op, ARITHMETIC):
            found.append(f"{where}:{node.lineno}: augmented {type(node.op).__name__}")
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id in AGGREGATIONS):
            found.append(f"{where}:{node.lineno}: call to {node.func.id}()")
    return found


# ---------------------------------------------------------------------------
# fixtures: the happy path and the four degenerate audits
# ---------------------------------------------------------------------------

def full_doc():
    return state_doc(queue=[queue_pass("P-01", done=True), queue_pass("P-02")],
                     findings=[finding_record("F-0001", "closed"),
                               finding_record("F-0002", "reported"),
                               finding_record("F-0003", "reopened")])


def payloads(fx):
    conf = util.load_conf(fx.audit)
    return cli.dashboard_payloads(fx.root, conf)


def render_project(fx):
    code, out = fx.run("dashboard")
    path = fx.audit / "dashboard.html"
    page = path.read_text(encoding="utf-8") if path.exists() else ""
    return code, out, page


class TheDashboardReadsNothingButTheAuditDirectory(unittest.TestCase):

    def test_the_open_spy_sees_no_file_outside_the_audit_directory(self):
        fx = Fixture(full_doc())
        self.addCleanup(fx.cleanup)
        with open_spy() as opened:
            code, _out = fx.run("dashboard")
        self.assertEqual(0, code)
        # `/var/…` and `/private/var/…` are the same directory on macOS and the CLI
        # resolves the project path, so a raw `startswith` on the fixture root matches
        # NOTHING and the assertion below would pass on a renderer that read the entire
        # disk. Resolve both sides, then prove the spy is live before trusting its
        # silence.
        root = str(Path(fx.root).resolve())
        seen = [str(Path(p).resolve()) for p in opened]
        inside_project = [p for p in seen if p.startswith(root)]
        outside_audit = [p for p in inside_project
                         if not p.startswith(str(Path(root, "audit")))]
        print("\nopen-spy: paths opened under the project during `xcheck dashboard`:")
        for p in sorted(set(inside_project)):
            print(f"  {p.replace(root, '<project>')}")
        self.assertTrue(any(p.endswith("state.json") for p in inside_project),
                        "the spy recorded nothing it must have seen — it is not "
                        "watching the door the reader uses")
        self.assertEqual([], outside_audit,
                         f"the dashboard read project material: {outside_audit}")

    def test_the_renderer_itself_is_a_pure_function_of_the_two_payloads(self):
        """`render` takes data, not a path. The spy above cannot regress into a
        pass-by-accident if the function has nothing to read from."""
        fx = Fixture(full_doc())
        self.addCleanup(fx.cleanup)
        status, metrics = payloads(fx)
        with open_spy() as opened:
            page = dashboard.render(status, metrics)
        self.assertEqual([], [p for p in opened if str(fx.root) in p])
        self.assertIn("<h1>xcheck dashboard</h1>", page)


class EveryFigureTracesToTheSourceJson(unittest.TestCase):

    def test_the_walk_reports_zero_untraceable_figures(self):
        fx = Fixture(full_doc())
        self.addCleanup(fx.cleanup)
        status, metrics = payloads(fx)
        page = dashboard.render(status, metrics)
        bad = untraceable(page, status, metrics)
        cells = VALUE_CELL.findall(page)
        print(f"\ntraceability walk: {len(cells)} value cells, "
              f"{len(bad)} untraceable figures")
        self.assertEqual([], bad, f"figures that are in neither payload: {bad}")

    def test_label_cells_carry_only_the_pre_registration_indices(self):
        """The walk's one exemption, bounded. Without this, any number could be moved
        into a label and stop being checked."""
        fx = Fixture(full_doc())
        self.addCleanup(fx.cleanup)
        status, metrics = payloads(fx)
        hay = source_scalars(status, metrics)
        # A label may carry a pre-registration index, or a number that is itself payload
        # data — `attempts: {"0": 3}` renders the KEY `0` as the row's label. Anything
        # else in label text is a figure this module invented.
        stray = [t for t in label_numbers(dashboard.render(status, metrics))
                 if t not in METRIC_INDICES and not any(t in s for s in hay)]
        self.assertEqual([], stray, f"un-exempted numbers in label text: {stray}")

    def test_the_walk_reddens_on_a_planted_figure(self):
        fx = Fixture(full_doc())
        self.addCleanup(fx.cleanup)
        status, metrics = payloads(fx)
        page = dashboard.render(status, metrics)
        planted = page.replace(
            "<h2>findings</h2>",
            "<h2>findings</h2>\n<table><tr><td>completion</td>"
            "<td class='v'>93.5%</td></tr></table>", 1)
        bad = untraceable(planted, status, metrics)
        print(f"planted figure 93.5% -> walk reports: {bad}")
        self.assertIn("93.5", bad,
                      "the walk did not notice a figure that is in neither payload")
        self.assertEqual([], untraceable(page, status, metrics),
                         "…and the unplanted page is still green (control)")


class TheModuleDoesNoArithmetic(unittest.TestCase):

    def test_the_ast_finds_no_arithmetic_and_no_aggregation(self):
        found = arithmetic_in(SOURCE)
        print(f"\nAST: xcheck/dashboard.py — {len(found)} arithmetic/aggregation "
              f"nodes {found}")
        self.assertEqual([], found,
                         "the dashboard computes; it is supposed to format")

    def test_the_ast_check_reddens_on_planted_arithmetic(self):
        """Two plants: the obvious one, and one hidden inside a comprehension — the
        shape a grep for ` / ` would miss half the time."""
        obvious = SOURCE.replace(
            "def rows(pairs):",
            "def share(a, b):\n    return a / b\n\n\ndef rows(pairs):", 1)
        hidden = SOURCE.replace(
            "def rows(pairs):",
            "def shares(xs):\n    return [x * 100 for x in xs]\n\n\ndef rows(pairs):", 1)
        counted = SOURCE.replace(
            "def rows(pairs):",
            "def how_many(xs):\n    return len(xs)\n\n\ndef rows(pairs):", 1)
        for name, mutant, needle in (("a / b", obvious, "Div"),
                                     ("x * 100 in a comprehension", hidden, "Mult"),
                                     ("len(xs)", counted, "call to len()")):
            found = arithmetic_in(mutant, "planted.py")
            print(f"planted {name} -> {found}")
            self.assertTrue(any(needle in f for f in found),
                            f"the AST check missed a planted {name}")
        self.assertEqual([], arithmetic_in(SOURCE), "control: the real module is clean")


class TheFileIsSelfContained(unittest.TestCase):

    def test_no_external_url_no_script_no_remote_asset(self):
        fx = Fixture(full_doc())
        self.addCleanup(fx.cleanup)
        _code, _out, page = render_project(fx)
        offenders = []
        for pattern, why in ((r"https?://", "an absolute URL"),
                             (r"(?<![a-z])//[a-z0-9.-]+\.[a-z]{2,}", "a protocol-relative URL"),
                             (r"<script", "a script element"),
                             (r"<img", "an image element"),
                             (r"<link", "a link element"),
                             (r"@import", "a CSS import"),
                             (r"url\(", "a CSS url()"),
                             (r"<iframe", "a frame")):
            for m in re.finditer(pattern, page, re.I):
                offenders.append(f"{why}: {page[m.start():m.end() + 40]!r}")
        print(f"\nexternal-asset scan: {len(page)} bytes, {len(offenders)} offenders")
        self.assertEqual([], offenders)
        # …and the only thing it could still fetch would be a form action.
        self.assertNotIn("<form", page)


DEGENERATE = {
    "zero findings": state_doc(queue=[queue_pass("P-01")], findings=[]),
    # A plan whose catalogs are filled in but whose queue is still empty: the state a
    # project sits in between `migrate` and the Planner's first pass. (A queue-less
    # document that also carried findings is not a degenerate case — it is refused,
    # because a finding produced by no pass has no coverage behind it.)
    "zero passes": state_doc(queue=[], findings=[]),
    # Nothing at all: no catalogs, no queue, no findings — `xcheck init` and stop.
    "not started": state_doc(queue=[], findings=[],
                             catalogs={"norms": [], "dimensions": [], "units": []}),
    "needs-human": state_doc(
        queue=[queue_pass("P-01", done=True)],
        findings=[finding_record("F-0001", "reopened", attempts=1, next="Remediator")]),
}

FORBIDDEN = ("NaN", "None", "inf", "0/0", "nan")


class TheDegenerateAuditsRenderWithoutHoles(unittest.TestCase):
    """Four audits with nothing in them. Each must SAY it has nothing, and must not
    print a computed-looking zero, a Python `None` or an empty `%` in place of a
    figure that was never measured."""

    def check(self, name, doc):
        fx = Fixture(doc)
        self.addCleanup(fx.cleanup)
        code, out, page = render_project(fx)
        self.assertEqual(0, code, out)
        text = visible(page)
        for bad in FORBIDDEN:
            self.assertNotIn(bad, text, f"{name}: rendered {bad!r}")
        self.assertIsNone(re.search(r"(?<![\d.])%", text),
                          f"{name}: a percent sign with no number in front of it")
        self.assertIn("class='empty'", page,
                      f"{name}: nothing is stated as empty, so a reader cannot tell "
                      f"'no data' from 'not rendered'")
        stated = [visible(m) for m in re.findall(r"<p class='empty'>(.*?)</p>", page)]
        print(f"\n{name}: {len(stated)} stated-empty lines, "
              f"first: {stated[0].strip() if stated else '(none)'}")
        status, metrics = payloads(fx)
        self.assertEqual([], untraceable(page, status, metrics),
                         f"{name}: an untraceable figure")
        return page

    def test_zero_findings(self):
        page = self.check("zero findings", DEGENERATE["zero findings"])
        self.assertIn("no finding has been filed", page)

    def test_zero_passes(self):
        page = self.check("zero passes", DEGENERATE["zero passes"])
        self.assertIn("none queued", page)

    def test_an_audit_that_has_not_started(self):
        page = self.check("not started", DEGENERATE["not started"])
        self.assertIn("not measured — no finding has reached validation", page)

    def test_an_audit_sitting_in_needs_human(self):
        page = self.check("needs-human", DEGENERATE["needs-human"])
        self.assertIn("stop-needs-human", page)


class TheProjectStillDeclaresNoRuntimeDependency(unittest.TestCase):

    def test_pyproject_dependencies_are_empty(self):
        text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        m = re.search(r"^dependencies\s*=\s*(\[[^\]]*\])", text, re.M)
        self.assertIsNotNone(m, "pyproject.toml declares no `dependencies` key at all")
        self.assertEqual("[]", re.sub(r"\s+", "", m.group(1)),
                         "the dashboard must not have added a runtime dependency")
        print(f"\npyproject runtime dependencies: {m.group(1)}")


class TheCommandIsDeclaredWhereItIsDocumented(unittest.TestCase):

    def test_the_verb_and_its_flag_are_in_the_grammar(self):
        self.assertIn("dashboard", cli.FLAG_COMMANDS["--json"])
        self.assertIn("xcheck [--project DIR] dashboard", cli.__doc__)

    def test_an_unreadable_state_refuses_instead_of_rendering(self):
        fx = Fixture(raw='{"schema_version": 1, "findings": ')
        self.addCleanup(fx.cleanup)
        code, out = fx.run("dashboard")
        self.assertEqual(1, code)
        self.assertIn("dashboard unavailable", out)
        self.assertFalse((fx.audit / "dashboard.html").exists(),
                         "a page was rendered from a document that does not load")
        print(f"\nunreadable state: {out.strip().splitlines()[0]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
