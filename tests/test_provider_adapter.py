"""The provider telemetry seam: ten fields, two adapters, and one module that knows a
vendor's key names.

PHASE 11 (fourth audit). The finding is economic: 7 of 159 finished sessions carry a token
figure, so 95.6% of this project's sessions are not free — they are unmeasured, and every
budget and cost claim above them is a guess. The corpus is worse than the audit said, and
this module measures that first: `telemetry_source` appears on ZERO of the 159, so even
the 4.4% that has a number does not say where the number came from. Those seven are
regular-expression scrapes off agent prose.

What is asserted here is the seam, not the arithmetic. An adapter interface is only real
if a second implementation exists, if the operator picks which one runs, and if nothing
else in the tree parses a provider's output behind its back — so those are the three
things with tests, and the structural one runs over the AST rather than the text, because
this file and `xcheck/provider.py` both NAME vendor keys in prose while explaining them.
"""

import shutil
import unittest
from pathlib import Path

from tests.harness import needs_live_corpus, REPO, Fixture, xcheck_submodule

envelope = xcheck_submodule("envelope")
provider = xcheck_submodule("provider")
runner = xcheck_submodule("runner")
state = xcheck_submodule("state")

# One raw object in the shape a codex wrapper hands over — aliases included, so the
# adapter is exercised on the names it actually has to know rather than on the canonical
# ones it would trivially pass.
CODEX_BLOCK = {
    "model": "gpt-5-codex",
    "id": "resp_01H8XYZ",
    "prompt_tokens": 12000,
    "cached_tokens": 9000,
    "completion_tokens": 3400,
    "total_tokens": 15400,
    "finish_reason": "stop",
    "max_input_tokens": 272000,
    "max_output_tokens": 128000,
    "effort": "high",
}


class TheInterfaceIsTenDeclaredFields(unittest.TestCase):

    def test_the_audits_ten_fields_and_no_others(self):
        """Ten, named, in one tuple. An interface that is whatever a dataclass happens to
        hold is an interface nobody can check a provider against."""
        self.assertEqual(10, len(provider.USAGE_FIELDS))
        self.assertEqual((
            "provider", "model", "request_id", "tokens_input", "tokens_cache",
            "tokens_output", "tokens_total", "stop_reason", "limit_input", "limit_output",
        ), provider.USAGE_FIELDS)
        self.assertEqual(provider.USAGE_FIELDS, provider.Usage._fields)
        print("\n  INTERFACE  " + ", ".join(provider.USAGE_FIELDS))

    def test_every_field_defaults_to_null_and_null_is_not_zero(self):
        empty = provider.Usage()
        self.assertEqual([None] * 10, list(empty))
        self.assertNotIn(0, list(empty), "an unmeasured field became a measured zero")

    def test_the_three_new_fields_reach_the_closed_session_schema(self):
        """A field the adapter produces that `state.json` refuses is a field that
        evaporates at the write — the record has four homes and this is the last one."""
        required, optional = state.SESSION_FIELDS
        for f in ("provider_stop_reason", "limit_input", "limit_output"):
            self.assertIn(f, optional, f)
            self.assertNotIn(f, required, f"{f} would redden every existing fixture")
            self.assertIn(f, envelope.TELEMETRY_FIELDS, f)


class TheSeamHasTwoAdapters(unittest.TestCase):
    """One adapter is a shape drawn around the code that already existed."""

    def test_both_are_registered_and_named(self):
        self.assertEqual({"codex", "fake"}, set(provider.ADAPTERS))
        for name, a in sorted(provider.ADAPTERS.items()):
            self.assertEqual(name, a.name)
            self.assertTrue(callable(a.parse))

    def test_the_codex_adapter_reads_the_alias_shape(self):
        got, bad = provider.read(provider.ADAPTERS["codex"], CODEX_BLOCK)
        self.assertEqual([], bad)
        self.assertEqual(provider.Usage(
            provider="openai", model="gpt-5-codex", request_id="resp_01H8XYZ",
            tokens_input=12000, tokens_cache=9000, tokens_output=3400,
            tokens_total=15400, stop_reason="stop", limit_input=272000,
            limit_output=128000), got)
        print(f"  CODEX      {got.model} {got.tokens_total} tokens, stop={got.stop_reason},"
              f" limits {got.limit_input}/{got.limit_output}")

    def test_the_fake_adapter_speaks_the_canonical_names_and_differs(self):
        """Proved DIFFERENT, not merely present: two names over one function would satisfy
        a registry check and leave the seam hypothetical."""
        canonical = {f: v for f, v in zip(provider.USAGE_FIELDS,
                                          ("fake", "m", "r", 1, 2, 3, 4, "stop", 5, 6))}
        got, bad = provider.read(provider.ADAPTERS["fake"], canonical)
        self.assertEqual([], bad)
        self.assertEqual("fake", got.provider)
        self.assertEqual(4, got.tokens_total)

        # The same object through both adapters must NOT agree — the codex adapter does
        # not know these key names, and the fake does not know codex's.
        crossed, _ = provider.read(provider.ADAPTERS["codex"], canonical)
        self.assertNotEqual(got, crossed)
        # The codex block through the FAKE: it shares exactly one key name with the
        # canonical set (`model`) and the fake reads nothing else, so every count comes
        # back null. Null, not zero — an adapter that answered 0 here would report a
        # 15,400-token session as free.
        blind, _ = provider.read(provider.ADAPTERS["fake"], CODEX_BLOCK)
        self.assertEqual("gpt-5-codex", blind.model)
        self.assertEqual([None] * 4, [getattr(blind, f) for f in provider.COUNT_FIELDS],
                         "the fake silently understood codex's aliases")
        self.assertEqual(15400, crossed_total := CODEX_BLOCK["total_tokens"])
        print(f"  FAKE       reads canonical names only; codex's {crossed_total}-token "
              f"block through it -> every count null, never 0")

    def test_the_fake_exercises_every_one_of_the_ten(self):
        """The fake is what runs where no provider CLI exists, so it has to reach the
        whole interface — a fake that fills three fields would let a machine report
        `unmeasured` for the other seven and call the seam covered."""
        full = dict(zip(provider.USAGE_FIELDS,
                        ("fake", "m", "r", 1, 2, 3, 9, "length", 5, 6)))
        got, bad = provider.read(provider.ADAPTERS["fake"], full)
        self.assertEqual([], bad)
        self.assertEqual([], [f for f in provider.USAGE_FIELDS
                              if getattr(got, f) is None])


class OnlyTheAdapterKnowsAProvidersShape(unittest.TestCase):

    def test_no_other_module_reads_a_vendor_key(self):
        """Structural, and over the AST — `assert-over-the-ast-not-the-prose`. Both this
        file and `provider.py` name vendor keys inside docstrings while explaining why
        they are there, and a substring hunt would read the explanation as the
        violation."""
        modules = sorted(p for p in (REPO / "xcheck").glob("*.py")
                         if p.name != "provider.py")
        leaked = provider.modules_reading_vendor_keys(modules)
        self.assertEqual({}, leaked,
                         f"vendor key names outside the seam: {leaked}")
        print(f"  SEAM       {len(modules)} modules scanned, 0 read a vendor key; "
              f"{len(provider.VENDOR_KEYS)} keys in the needle set")

    def test_CONTROL_the_check_can_see_a_leak(self):
        """A structural check that has never gone red is a check nobody has calibrated."""
        import tempfile
        d = Path(tempfile.mkdtemp(prefix="xcheck-seam-"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        clean, leaky = d / "clean.py", d / "leaky.py"
        clean.write_text('"""A docstring naming cached_tokens explains nothing."""\n'
                         'X = "tokens_input"\n', encoding="utf-8")
        leaky.write_text('BAD = {"cached_tokens": 1}\n', encoding="utf-8")
        found = provider.modules_reading_vendor_keys([clean, leaky])
        self.assertEqual({"leaky.py": ["cached_tokens"]}, found,
                         "the docstring counted as a leak, or the real leak did not")

    def test_the_seam_owns_the_line_pattern_too(self):
        self.assertTrue(hasattr(provider, "LINE_RE"))
        self.assertEqual({"total_tokens": 1},
                         provider.last_line_object('XCHECK_TELEMETRY {"total_tokens": 1}'))


class AMalformedUsageRecordIsRefusedAndNamed(unittest.TestCase):
    """Four classes, four sentences. `the telemetry was refused` tells an operator
    nothing they can act on."""

    CASES = (
        ("a negative count", {"model": "m", "input_tokens": -1}, "negative"),
        ("a boolean where an integer belongs",
         {"model": "m", "total_tokens": True}, "boolean"),
        ("a total under its own parts",
         {"model": "m", "input_tokens": 10, "output_tokens": 10, "total_tokens": 5},
         "smaller than its own parts"),
        ("no actual model", {"input_tokens": 10, "total_tokens": 10}, "no actual model"),
    )

    def test_each_class_is_refused_with_its_own_message(self):
        seen = {}
        for label, block, needle in self.CASES:
            got, bad = provider.read(provider.ADAPTERS["codex"], block)
            self.assertIsNone(got, f"{label} was accepted")
            self.assertTrue(bad, f"{label} produced no reason")
            self.assertIn(needle, " ".join(bad), label)
            seen[label] = bad[0]
            print(f"  REFUSED    {label:<36} {bad[0][:78]}")
        self.assertEqual(len(self.CASES), len(set(seen.values())),
                         f"two classes share a message: {seen}")

    def test_CONTROL_a_clean_object_passes_every_check(self):
        got, bad = provider.read(provider.ADAPTERS["codex"], CODEX_BLOCK)
        self.assertEqual([], bad)
        self.assertIsNotNone(got)

    def test_a_refusal_leaves_the_session_unmeasured_and_never_zero(self):
        got, bad = provider.read(provider.ADAPTERS["codex"],
                                 {"model": "m", "input_tokens": -5})
        self.assertIsNone(got)
        self.assertNotIn("0", "".join(str(bad).split("-5")))


class TheLabelSurvivesThisPhase(unittest.TestCase):
    """The third audit's split — parent-owned sidecar is `provider`, a line the child
    printed is `agent-reported` — must not be quietly undone by adding an adapter. An
    adapter parses better; it does not make the subject trustworthy."""

    def setUp(self):
        self.fx = Fixture()
        self.log = Path(self.fx.root) / "session.log"
        self.session = "0123456789abcdef"

    def test_COUNTERFACTUAL_a_provider_shaped_line_from_the_child_stays_agent_reported(self):
        """The audit's forgery, now dressed in the adapter's own ten fields — the strongest
        version, because every key is one the codex adapter understands."""
        self.log.write_text(
            'thinking...\nXCHECK_TELEMETRY {"model": "forged-model", "id": "forged", '
            '"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, '
            '"finish_reason": "stop", "max_output_tokens": 1}\n', encoding="utf-8")
        rec, total = envelope.telemetry(self.log, session_id=self.session,
                                        adapter=provider.ADAPTERS["codex"])
        self.assertEqual(envelope.AGENT_REPORTED, rec["telemetry_source"],
                         "a declared adapter relabelled the child's own line as provider")
        self.assertEqual(2, total, "the figures are kept; only the label is withheld")
        self.assertEqual("forged-model", rec["provider_model"])
        print(f"\n  FORGERY    source={rec['telemetry_source']!r} tokens={total} "
              f"model={rec['provider_model']!r} — parsed in full, labelled honestly")

    def test_a_parent_owned_sidecar_is_still_provider(self):
        import json
        self.log.write_text("work\n", encoding="utf-8")
        envelope.make_sidecar_dir(self.log)
        side = envelope.sidecar_path(self.log, self.session)
        side.write_text(json.dumps(dict(CODEX_BLOCK, session_id=self.session)),
                        encoding="utf-8")
        rec, total = envelope.telemetry(self.log, session_id=self.session,
                                        adapter=provider.ADAPTERS["codex"])
        self.assertEqual(envelope.PROVIDER, rec["telemetry_source"])
        self.assertEqual(15400, total)
        self.assertEqual("stop", rec["provider_stop_reason"])
        self.assertEqual(128000, rec["limit_output"])
        print(f"  SIDECAR    source={rec['telemetry_source']!r} stop="
              f"{rec['provider_stop_reason']!r} limits={rec['limit_input']}/"
              f"{rec['limit_output']} — the three fields nothing recorded before")

    def test_the_two_labels_are_still_distinct_values(self):
        self.assertNotEqual(envelope.PROVIDER, envelope.AGENT_REPORTED)
        self.assertIn(envelope.AGENT_REPORTED, state.TELEMETRY_SOURCES)


class TheAdapterIsChosenByTheOperator(unittest.TestCase):

    def test_the_profile_selects_it(self):
        got, problem = provider.adapter_for({"telemetry_adapter": "codex"})
        self.assertIsNone(problem)
        self.assertEqual("codex", got.name)

    def test_an_unset_key_measures_nothing_and_is_not_an_error(self):
        """The honest answer for an unconfigured machine. A default would be a guess
        about a provider nobody named."""
        self.assertEqual((None, None), provider.adapter_for({}))

    def test_an_unknown_name_refuses_rather_than_falling_back(self):
        got, problem = provider.adapter_for({"telemetry_adapter": "gemini"})
        self.assertIsNone(got)
        self.assertIn("names no adapter", problem)
        with self.assertRaises(SystemExit) as e:
            runner._telemetry_adapter({"telemetry_adapter": "gemini"})
        self.assertIn("names no adapter", str(e.exception))
        print(f"  UNKNOWN    {problem[:100]}")

    def test_COUNTERFACTUAL_the_command_string_does_not_select_it(self):
        """A profile whose every role command says `codex` and whose adapter key is unset
        selects NOTHING. Inferring the provider from the program name would be deciding
        something the operator did not say — the command may be a wrapper or a shim."""
        conf = {"auditor_cmd": "codex exec --sandbox workspace-write {prompt}",
                "remediator_cmd": "codex exec {prompt}"}
        self.assertIsNone(runner._telemetry_adapter(conf))
        print("  NOT GUESSED  auditor_cmd names `codex`; no adapter selected")

    def test_the_key_is_an_operator_key_not_a_project_one(self):
        """A subject that could set this could choose how its own cost is counted."""
        policy = xcheck_submodule("policy")
        self.assertIn("telemetry_adapter", policy.POLICY_KEYS)
        self.assertEqual("policy", policy.classify("telemetry_adapter"))

    def test_nothing_runs_the_provider_to_identify_it(self):
        """`provenance-must-not-interview-the-subject` — F-0098 closed exactly this."""
        src = (REPO / "xcheck" / "provider.py").read_text(encoding="utf-8")
        for forbidden in ("subprocess", "Popen", "os.system", "--version"):
            self.assertNotIn(forbidden, src, f"the seam reaches for {forbidden}")


@needs_live_corpus
class CoverageIsReportedHonestly(unittest.TestCase):

    def test_the_real_corpus_before_and_after(self):
        """Criterion 6, stated rather than improved: this phase builds the channel and
        cannot retrofit a source label onto a number whose origin was never recorded.
        Inventing one is the forgery the label exists to prevent."""
        st = state.load_state(REPO / "audit")
        sessions = list(st.sessions)
        measured = [s for s in sessions if s.get("tokens") is not None]
        labelled = [s for s in sessions if s.get("telemetry_source")]
        pct = len(measured) / len(sessions) * 100

        print(f"\n  BEFORE     {len(measured)}/{len(sessions)} sessions carry a token "
              f"figure ({pct:.1f}%) — the audit's 4.4%")
        print(f"  BEFORE     {len(labelled)}/{len(sessions)} say WHERE the figure came "
              f"from: the corpus predates `telemetry_source` entirely, so even the "
              f"measured {len(measured)} are unattributed log scrapes")
        print("  AFTER      unchanged on history and unchangeable: a source label "
              "cannot be back-filled onto a number whose origin was never recorded")
        print(f"  AFTER      new sessions record {len(provider.USAGE_FIELDS)} fields "
              f"through a declared adapter; coverage on this corpus stays {pct:.1f}% "
              f"until a run happens, and no run is spent by this phase")

        self.assertEqual(0, len(labelled), "history gained a label it cannot support")
        self.assertLess(pct, 5.0)

    def test_the_metrics_block_reports_coverage_per_field(self):
        """`coverage-is-per-field-not-per-run`: a field measured for 7 sessions cannot be
        quoted as if it covered all of them."""
        decision = xcheck_submodule("decision")
        st = state.load_state(REPO / "audit")
        m = decision.metrics_report(st, envelope.read_events(REPO))
        by_field = m["telemetry"]["by_field"]
        for f in ("provider_stop_reason", "limit_input", "limit_output"):
            self.assertIn(f, by_field, f)
            self.assertEqual(0, by_field[f]["measured"])
            print(f"  PER FIELD  {f:<22} measured {by_field[f]['measured']:>3}  "
                  f"coverage {by_field[f]['coverage_pct']}%")


class AnAbsentProviderIsReportedNotSkipped(unittest.TestCase):

    def test_the_real_cli_is_reported_present_or_absent_and_the_fake_still_runs(self):
        """Criterion 9. A test that skipped here would leave a machine with no provider
        CLI reporting nothing at all, which reads as `nothing to check`."""
        where = shutil.which("codex")
        print(f"\n  PROVIDER CLI  codex: "
              f"{where or 'ABSENT on this machine — REPORTED, not skipped'}")
        full = dict(zip(provider.USAGE_FIELDS,
                        ("fake", "m", "r", 1, 2, 3, 9, "length", 5, 6)))
        got, bad = provider.read(provider.ADAPTERS["fake"], full)
        self.assertEqual([], bad)
        self.assertEqual([], [f for f in provider.USAGE_FIELDS if getattr(got, f) is None],
                         "the fake did not reach the whole interface")
        print(f"  FAKE          all {len(provider.USAGE_FIELDS)} fields exercised "
              f"without a provider CLI and without a paid call")

    def test_no_test_here_dispatched_anything(self):
        """This whole module is offline by construction: it parses objects and reads a
        corpus. Stated as an assertion so a future edit that adds a dispatch is red."""
        src = Path(__file__).read_text(encoding="utf-8")
        # The needle is SPLIT so this assertion does not find itself — the whole file is
        # the haystack (`self-scanning-detector-needs-a-split-needle`).
        for forbidden in ("subprocess" + ".run", "run_" + "session", "dispatch" + "("):
            self.assertNotIn(forbidden, src, f"this module reaches for {forbidden}")


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------- PHASE 3 (fifth)

class ReadIsTheOnlyDoor(unittest.TestCase):
    """The fifth audit's fourth finding, structural half.

    `provider.read()` held every refusal — negative counts, booleans where integers
    belong, a total that is not the sum of its parts, and a record naming no actual model
    — and the production path did not call it. `envelope.finish` reached past it to
    `adapter.parse`, so one usage object was rejected by the validator and accepted by
    the consumer, wearing `telemetry_source=provider`. A validator with no traffic.
    """

    # `ast.parse` and `argparse` are this codebase's own words and would report every
    # module while meaning nothing (`a-needle-set-must-exclude-ordinary-words`). The
    # needle is a `.parse` REFERENCE — not only a call — because a reference is how the
    # bypass was written after the fix moved the call one line up.
    NOT_THE_SEAM = ("ast", "argparse", "write", "json", "self")

    def sites(self):
        """Every `<name>.parse` reference in the shipped package, by module and line."""
        import ast
        found = []
        for path in sorted((REPO / "xcheck").glob("*.py")):
            for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(n, ast.Attribute) and n.attr == "parse" and \
                        getattr(n.value, "id", None) not in self.NOT_THE_SEAM:
                    found.append(f"{path.name}:{n.lineno}")
        return found

    def test_no_module_outside_provider_parses_a_usage_object_itself(self):
        sites = self.sites()
        print(f"\n  PARSE SITES  {sites}")
        # POSITIVE CONTROL first: `provider.py` must hold one, or the scan is finding
        # nothing everywhere and its silence about the other modules means nothing.
        self.assertTrue([s for s in sites if s.startswith("provider.py")],
                        "the scan found no adapter.parse even inside provider.py, so it "
                        "is measuring nothing")
        outside = [s for s in sites if not s.startswith("provider.py")]
        self.assertEqual([], outside,
                         f"{outside} reaches past provider.read(), which is where the "
                         f"refusals live")

    def test_read_answers_for_an_undeclared_adapter_too(self):
        """`adapter=None` used to mean "nothing to do" and return `(None, [])`, which is
        how a machine with no `telemetry_adapter` got the alias reader with none of the
        refusals. The LABEL is what the rules are about, not the vendor."""
        bad_obj = {"total_tokens": 500}                  # complete but for the model
        for adapter in (None, provider.ADAPTERS["codex"], provider.ADAPTERS["fake"]):
            with self.subTest(adapter=adapter and adapter.name):
                got, refused = provider.read(adapter, dict(bad_obj))
                self.assertIsNone(got)
                self.assertTrue(any("names no actual model" in r for r in refused))
        good, refused = provider.read(None, {"total_tokens": 500, "model": "gpt-5"})
        print(f"  UNDECLARED   alias reader + the same refusals; a complete object "
              f"still reads: {good.model!r} {good.tokens_total}")
        self.assertEqual([], refused)
        self.assertEqual("gpt-5", good.model)
