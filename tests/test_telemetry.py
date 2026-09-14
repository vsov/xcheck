"""Provider-native telemetry. The audit: parsing `tokens used` out of log prose is a
fallback wearing a primary's clothes — the provider already reports input, output and
cache tokens, the model it actually served, the effort it actually applied, and a request
id someone can quote in a support ticket.

The parse is not removed and its tests stay green (`tests/test_tokens.py`). What changes
is that the record SAYS which source a figure came from, so a reader can tell a reported
number from a scraped one, and that null keeps meaning NOT MEASURED for every new field.
"""

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.harness import (needs_live_corpus, Fixture, complete_conf, finding_record, state_doc,
                           xcheck_submodule)

decision = xcheck_submodule("decision")
envelope = xcheck_submodule("envelope")
retention = xcheck_submodule("retention")
runner = xcheck_submodule("runner")
state = xcheck_submodule("state")
util = xcheck_submodule("util")

REPO = Path(__file__).resolve().parent.parent

GIT = shutil.which("git") or "git"
PROVIDER_BLOCK = {"input_tokens": 1200, "output_tokens": 340, "cached_tokens": 90,
                  "model": "gpt-5-codex", "reasoning_effort": "high",
                  "request_id": "req_01ABC"}


SESSION = "0123456789abcdef"


def log_with(tmp, text, sidecar=None, session=SESSION):
    """A session log, and optionally the PARENT-OWNED sidecar beside it.

    PHASE 6 moved the sidecar out of `<log>.telemetry.json` — a path anyone holding the
    log path can derive — into `<log dir>/.telemetry/<session>.json`, a directory the
    parent creates and the child is never told about. The helper writes it through the
    product's own `sidecar_path`, so a test cannot accidentally validate a location the
    runner does not use.
    """
    p = Path(tmp) / "session.log"
    p.write_text(text, encoding="utf-8")
    if sidecar is not None:
        side = envelope.sidecar_path(p, session)
        side.parent.mkdir(parents=True, exist_ok=True)
        body = dict(sidecar)
        body.setdefault("session_id", session)
        side.write_text(json.dumps(body), encoding="utf-8")
    return p


class TheProviderIsTheSourceWhenItSpeaks(unittest.TestCase):

    def setUp(self):
        self.fx = Fixture(state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, self.fx.root, True)

    def test_every_field_the_provider_reports_is_recorded_and_labelled(self):
        log = log_with(self.fx.root, "header\n", sidecar=PROVIDER_BLOCK)
        rec, total = envelope.telemetry(log, first_output_s=4.2, idle_s=11.0,
                                        session_id=SESSION)
        print("\n  PROVIDER-SOURCED SESSION")
        for f in envelope.TELEMETRY_FIELDS:
            print(f"    {f:<24} {rec[f]}")
        print(f"    {'(headline `tokens`)':<24} {total}")
        self.assertEqual(envelope.PROVIDER, rec["telemetry_source"])
        self.assertEqual((1200, 340, 90), (rec["tokens_input"], rec["tokens_output"],
                                           rec["tokens_cache"]))
        self.assertEqual("gpt-5-codex", rec["provider_model"])
        self.assertEqual("high", rec["reasoning_effort"])
        self.assertEqual("req_01ABC", rec["provider_request_id"])
        self.assertEqual(1540, total, "the headline total is not the provider's")

    def test_a_sidecar_file_outranks_a_line_in_the_log(self):
        """A file the wrapper wrote on purpose outranks a line that could have been
        quoted from somewhere else."""
        log = log_with(self.fx.root,
                       "XCHECK_TELEMETRY " + json.dumps(dict(PROVIDER_BLOCK, model="from-log")) + "\n",
                       sidecar=dict(PROVIDER_BLOCK, model="from-sidecar"))
        rec, _ = envelope.telemetry(log, session_id=SESSION)
        self.assertEqual("from-sidecar", rec["provider_model"])
        self.assertEqual(envelope.PROVIDER, rec["telemetry_source"],
                         "the sidecar won the value but not the label")

    def test_the_last_line_wins(self):
        """Same rule as `tokens_in_log`: a session that quotes an earlier log would put
        an older run's numbers above its own summary."""
        log = log_with(self.fx.root,
                       "XCHECK_TELEMETRY " + json.dumps(dict(PROVIDER_BLOCK, request_id="old")) + "\n"
                       "XCHECK_TELEMETRY " + json.dumps(dict(PROVIDER_BLOCK, request_id="mine")) + "\n")
        rec, _ = envelope.telemetry(log)
        self.assertEqual("mine", rec["provider_request_id"])

    def test_a_broken_telemetry_block_is_not_measured_and_never_raises(self):
        """A malformed number must not cost a session that has already done its work."""
        log = log_with(self.fx.root, "XCHECK_TELEMETRY {not json at all\n", sidecar=None)
        rec, total = envelope.telemetry(log)
        self.assertIsNone(rec["telemetry_source"])
        self.assertIsNone(total)


class TheLogParseIsTheFallbackAndSaysSo(unittest.TestCase):

    def setUp(self):
        self.fx = Fixture(state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, self.fx.root, True)

    def test_a_session_with_only_a_log_figure_is_labelled_log_parse(self):
        log = log_with(self.fx.root, "tokens used\n1 600\n")
        rec, total = envelope.telemetry(log)
        print(f"\n  FALLBACK   source={rec['telemetry_source']}  tokens_log="
              f"{rec['tokens_log']}  headline={total}")
        self.assertEqual(envelope.LOG_PARSE, rec["telemetry_source"])
        self.assertEqual(1600, total)
        self.assertIsNone(rec["tokens_input"], "a parsed total was split into a guess")

    def test_the_parser_itself_is_untouched(self):
        """The fallback keeps working exactly as it did — this phase adds a label, it
        does not rewrite the thing being labelled."""
        self.assertEqual(1600, envelope.tokens_in_log("tokens used\n1 600\n"))

    def test_neither_source_records_null_and_never_zero(self):
        log = log_with(self.fx.root, "the agent said nothing measurable\n")
        rec, total = envelope.telemetry(log)
        print("  NEITHER    " + ", ".join(
            f"{f}={rec[f]}" for f in ("telemetry_source", "tokens_log", "tokens_input")))
        self.assertIsNone(total)
        for f in envelope.TELEMETRY_FIELDS:
            if f in ("first_output_s", "idle_s"):
                continue
            self.assertIsNone(rec[f], f"{f} was recorded as {rec[f]!r} instead of null")
            self.assertNotEqual(0, rec[f], "an absent measurement was recorded as zero")

    def test_the_finished_record_carries_no_null_keys_for_what_it_did_not_measure(self):
        """`finish` writes only the fields it has. An unmeasured field is ABSENT from the
        record, which is what makes `sessions_unmeasured` countable — a key present with
        a null would still be a key the schema has to carry for every legacy session."""
        log = log_with(self.fx.root, "nothing here\n")
        profile = runner.resolve_profile(util.Conf(dict(util.CONF_DEFAULTS)), "Auditor")
        rec = envelope.dispatch_record(["sh"], "Auditor", "c", "p", "a" * 16, profile, 1,
                                       head_before="b" * 40)
        rec = envelope.finish(rec, 0, "ok", 1.0, "c" * 40, log)
        for f in envelope.TELEMETRY_FIELDS:
            self.assertNotIn(f, rec, f"{f} was written for a session that measured none")
        self.assertIsNone(rec["tokens"])


class WhenTheSourcesDisagreeBothAreKept(unittest.TestCase):
    """CONTROL for criterion 6. Silently preferring one would hide the case that matters:
    two sources disagreeing means one is measuring something other than what its name
    says, and that is a defect to find, not a tie to break."""

    def setUp(self):
        self.fx = Fixture(state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, self.fx.root, True)

    def test_both_figures_and_a_flag(self):
        log = log_with(self.fx.root,
                       "XCHECK_TELEMETRY " + json.dumps(PROVIDER_BLOCK) + "\n"
                       "tokens used\n1 600\n")
        rec, total = envelope.telemetry(log)
        print(f"\n  DISAGREE   provider {total} vs log {rec['tokens_log']}")
        print(f"             {rec['telemetry_disagreement']}")
        self.assertEqual(1540, total)
        self.assertEqual(1600, rec["tokens_log"])
        self.assertIn("1540", rec["telemetry_disagreement"])
        self.assertIn("1600", rec["telemetry_disagreement"])

    def test_agreement_raises_no_flag(self):
        agree = dict(PROVIDER_BLOCK, input_tokens=1000, output_tokens=600)
        log = log_with(self.fx.root,
                       "XCHECK_TELEMETRY " + json.dumps(agree) + "\ntokens used\n1600\n")
        rec, total = envelope.telemetry(log)
        self.assertEqual(1600, total)
        self.assertIsNone(rec["telemetry_disagreement"])


class TheOrchestratorMeasuresWhatNoProviderCan(unittest.TestCase):
    """`first_output_s` and `idle_s` come from the stream of bytes, not from anyone's
    report. They are the two figures that diagnose a session which produced only its own
    header — the case phase 12 kills on."""

    def child(self, script, timeout=30):
        import tempfile
        td = tempfile.mkdtemp(prefix="xcheck-tele-")
        self.addCleanup(shutil.rmtree, td, True)
        log = Path(td) / "s.log"
        timings = {}
        conf = util.Conf(dict(util.CONF_DEFAULTS, session_timeout=str(timeout)))
        rc, outcome, elapsed = runner.run_child(
            ["sh", "-c", script], td, log, {}, conf, timeout=timeout, timings=timings)
        return rc, outcome, timings, log

    def test_a_child_that_pauses_before_speaking_records_the_wait(self):
        rc, outcome, t, _ = self.child("sleep 1; echo hello")
        print(f"\n  MEASURED   first_output_s={t['first_output_s']}  "
              f"idle_s={t['idle_s']}  lines={t['lines']}")
        self.assertEqual(0, rc)
        self.assertGreaterEqual(t["first_output_s"], 1.0)
        self.assertLess(t["first_output_s"], 10.0)

    def test_a_silent_tail_counts_as_idle(self):
        """A child that printed once and then sat there was idle for the whole stretch.
        Counting only the gaps BETWEEN lines would report its longest idle period as the
        short one in the middle."""
        rc, outcome, t, _ = self.child("echo start; sleep 2")
        print(f"  TAIL GAP   first_output_s={t['first_output_s']}  idle_s={t['idle_s']}")
        self.assertGreaterEqual(t["idle_s"], 1.5)

    def test_a_child_that_says_nothing_at_all_records_no_first_output(self):
        rc, outcome, t, _ = self.child("sleep 1")
        self.assertIsNone(t["first_output_s"])
        self.assertEqual(0, t["lines"])


class BothClosedSchemasWereAsked(unittest.TestCase):

    def test_every_field_clears_the_session_schema(self):
        rec = {"session_id": "a" * 16, "role": "Auditor", "provider": "p",
               "agent_model": "m", "executable": "x", "executable_version": "v",
               "charter_hash": "0" * 64, "prompt_hash": "0" * 64, "state_revision": 1,
               "head_before": "unknown", "sandbox_profile": "readonly",
               "started": "2026-09-03",
               "telemetry_source": "provider", "tokens_input": 1200,
               "tokens_output": 340, "tokens_cache": 90, "tokens_log": 1600,
               "provider_model": "m", "reasoning_effort": "high",
               "provider_request_id": "r", "telemetry_disagreement": "x",
               "first_output_s": 4.2, "idle_s": 11.0}
        fx = Fixture(state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, fx.root, True)
        doc = json.loads((fx.audit / "state.json").read_text(encoding="utf-8"))
        doc["sessions"] = [rec]
        (fx.audit / "state.json").write_text(json.dumps(doc), encoding="utf-8")
        loaded = state.load_state(fx.audit)
        self.assertEqual("provider", loaded.sessions[0]["telemetry_source"])

    def test_an_unknown_telemetry_source_is_refused(self):
        """The field has a vocabulary, so it is enumerated and an unrecognised value
        fails closed — a source nobody declared reads as "trust this figure"."""
        fx = Fixture(state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, fx.root, True)
        doc = json.loads((fx.audit / "state.json").read_text(encoding="utf-8"))
        doc["sessions"] = [{"session_id": "a" * 16, "role": "Auditor", "provider": "p",
                            "agent_model": "m", "executable": "x",
                            "executable_version": "v", "charter_hash": "0" * 64,
                            "prompt_hash": "0" * 64, "state_revision": 1,
                            "head_before": "unknown", "sandbox_profile": "readonly",
                            "started": "2026-09-03", "telemetry_source": "vibes"}]
        (fx.audit / "state.json").write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaises(state.StateError) as e:
            state.load_state(fx.audit)
        self.assertIn("telemetry_source", str(e.exception))

    def test_the_output_schema_bumped_once_for_the_batch(self):
        # 6 was this phase's bump (`agent-reported`). Phase 8 moved it to 7
        # (`first_activity_s` in `telemetry.by_field`), phase 12 to 8 (`full_sweep` on
        # the status payload), phase 14 to 9 (`sessions_by_route` in the metrics
        # breakdowns) and the FOURTH audit's phase 11 to 10 (`provider_stop_reason`,
        # `limit_input` and `limit_output` in `telemetry.by_field`, three of the adapter's
        # ten fields) — each a separate contract change, each its own bump.
        # The pin follows the version deliberately rather than being written as
        # `util.OUTPUT_SCHEMA_VERSION`, which would agree with itself forever.
        self.assertEqual(10, util.OUTPUT_SCHEMA_VERSION)
        self.assertIn("telemetry", util.METRICS_SCHEMA)

    @needs_live_corpus
    def test_metrics_reports_coverage_per_field(self):
        """Criterion 4: a field measured for 7 of 158 sessions cannot be quoted as if it
        covered all of them, so the coverage figure is per FIELD and not per run."""
        st = state.load_state(Path("audit"))
        m = decision.metrics_report(st, envelope.read_events(Path(".")))
        t = m["telemetry"]
        print(f"\n  METRICS    {t['sessions_finished']} finished sessions, "
              f"by_source={t['by_source']}, disagreements={t['disagreements']}")
        for f in ("tokens_input", "provider_request_id", "first_output_s"):
            print(f"    {f:<24} measured {t['by_field'][f]['measured']:>4}  "
                  f"coverage {t['by_field'][f]['coverage_pct']}%")
        self.assertEqual(set(envelope.TELEMETRY_FIELDS), set(t["by_field"]))
        for f, row in t["by_field"].items():
            self.assertEqual(t["sessions_finished"],
                             row["measured"] + row["unmeasured"], f)

    @needs_live_corpus
    def test_the_real_command_still_emits_a_conforming_payload(self):
        """Through the CLI, not through the producer function: the closed schema is a
        promise about what reaches a consumer, and a new block that broke it would break
        it there."""
        import sys
        p = subprocess.run([sys.executable, "-m", "xcheck", "metrics", "--json"],
                           cwd=str(Path(__file__).resolve().parent.parent),
                           capture_output=True, text=True, timeout=180)
        self.assertEqual(0, p.returncode, p.stderr[-400:])
        payload = json.loads(p.stdout)
        self.assertEqual(10, payload["output_schema_version"])
        self.assertEqual([], util.json_output_issues(payload, util.METRICS_SCHEMA))
        print(f"  CLI        metrics --json validates at v{payload['output_schema_version']}"
              f", telemetry fields {len(payload['telemetry']['by_field'])}")


if __name__ == "__main__":
    unittest.main()


class TheSubjectCannotWearTheProviderLabel(unittest.TestCase):
    """Phase 6, third audit, P0. The audit's reproduction, kept as the first test.

    A session that printed one line —

        XCHECK_TELEMETRY {"total_tokens": 1, "model": "forged-model"}

    — was recorded as `telemetry_source=provider, tokens=1, provider_model=forged-model`.
    That is not provider-native telemetry; it is a claim by the thing under audit, and
    `provider` is the one label that means the tool did not have to trust the subject.
    """

    def setUp(self):
        self.fx = Fixture(state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, self.fx.root, True)

    def test_the_audits_forgery_is_labelled_agent_reported(self):
        forged = {"total_tokens": 1, "model": "forged-model", "request_id": "forged"}
        log = log_with(self.fx.root, "XCHECK_TELEMETRY " + json.dumps(forged) + "\n")
        rec, total = envelope.telemetry(log, session_id=SESSION)
        print(f"\n  FORGERY    source={rec['telemetry_source']!r} tokens={total} "
              f"model={rec['provider_model']!r}")
        self.assertEqual(envelope.AGENT_REPORTED, rec["telemetry_source"])
        self.assertNotEqual(envelope.PROVIDER, rec["telemetry_source"],
                            "a line the child printed still wears the provider label")

    def test_the_figures_are_kept_and_only_the_label_changes(self):
        """The claim is not discarded. A cooperative agent's own count is worth having;
        it just may not borrow the word that means somebody else said it."""
        forged = {"total_tokens": 7, "model": "some-model"}
        log = log_with(self.fx.root, "XCHECK_TELEMETRY " + json.dumps(forged) + "\n")
        rec, total = envelope.telemetry(log, session_id=SESSION)
        self.assertEqual(7, total)
        self.assertEqual("some-model", rec["provider_model"])

    def test_and_the_label_is_now_what_stops_it_attesting_a_route(self):
        """PHASE 3 (fifth audit): the consequence the label had not yet been given.

        The label was correct from the day it was introduced and nothing read it. So the
        forgery above, printing the model xcheck had ROUTED to, produced
        `route_attestation=attested` — the subject confirming its own model, which is the
        one thing the label exists to prevent. The reasoning above stands unchanged; what
        was missing was a consumer.
        """
        forged = {"total_tokens": 1, "model": "gpt-5", "request_id": "forged"}
        log = log_with(self.fx.root, "XCHECK_TELEMETRY " + json.dumps(forged) + "\n")
        rec = envelope.finish({"session_id": SESSION}, 0, "ok", 1.0, "a" * 40, log,
                              planned_model="gpt-5", route="strong")
        print(f"  ATTESTATION source={rec['telemetry_source']!r} "
              f"provider_model={rec['provider_model']!r} -> "
              f"route_attestation={rec['route_attestation']!r}")
        self.assertEqual(envelope.AGENT_REPORTED, rec["telemetry_source"])
        self.assertEqual("gpt-5", rec["provider_model"])
        self.assertEqual("unattested", rec["route_attestation"],
                         "matching strings from the subject attested the route")

    def test_CONTROL_the_same_figures_from_the_sidecar_do_attest(self):
        """Without this arm, `unattested` above could mean attestation is simply broken."""
        block = {"total_tokens": 1, "model": "gpt-5", "session_id": SESSION}
        log = log_with(self.fx.root, "no line here\n", sidecar=block)
        rec = envelope.finish({"session_id": SESSION}, 0, "ok", 1.0, "a" * 40, log,
                              planned_model="gpt-5", route="strong")
        print(f"  CONTROL     source={rec['telemetry_source']!r} -> "
              f"route_attestation={rec['route_attestation']!r}")
        self.assertEqual(envelope.PROVIDER, rec["telemetry_source"])
        self.assertEqual("attested", rec["route_attestation"])

    def test_a_child_written_file_at_the_old_path_is_not_read_at_all(self):
        """The sidecar used to live at `<log>.telemetry.json` — a path derivable by
        anyone who knows the log path, and every child knows where its own output goes."""
        p = Path(self.fx.root) / "session.log"
        p.write_text("no telemetry line here\n", encoding="utf-8")
        p.with_name(p.name + ".telemetry.json").write_text(
            json.dumps({"total_tokens": 999999, "model": "old-path"}), encoding="utf-8")
        rec, total = envelope.telemetry(p, session_id=SESSION)
        print(f"  OLD PATH   source={rec['telemetry_source']!r} tokens={total}")
        self.assertIsNone(rec["telemetry_source"])
        self.assertIsNone(total)

    def test_the_three_sources_are_enumerated_and_distinct(self):
        self.assertEqual(("provider", "log-parse", "agent-reported"),
                         util.TELEMETRY_SOURCES)
        self.assertEqual(3, len({envelope.PROVIDER, envelope.LOG_PARSE,
                                 envelope.AGENT_REPORTED}))


class TheSidecarIsBoundToItsSession(unittest.TestCase):

    def setUp(self):
        self.fx = Fixture(state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, self.fx.root, True)

    def test_a_sidecar_for_another_session_is_refused(self):
        log = log_with(self.fx.root, "header\n",
                       sidecar=dict(PROVIDER_BLOCK, session_id="ffffffffffffffff"))
        rec, total = envelope.telemetry(log, session_id=SESSION)
        print(f"\n  BINDING    {rec['telemetry_refused']}")
        self.assertIsNone(rec["telemetry_source"])
        self.assertIn("not '0123456789abcdef'", rec["telemetry_refused"])
        self.assertIsNone(total)

    def test_a_sidecar_naming_no_session_is_refused(self):
        p = Path(self.fx.root) / "session.log"
        p.write_text("header\n", encoding="utf-8")
        side = envelope.sidecar_path(p, SESSION)
        side.parent.mkdir(parents=True, exist_ok=True)
        side.write_text(json.dumps({"total_tokens": 5}), encoding="utf-8")
        rec, _ = envelope.telemetry(p, session_id=SESSION)
        self.assertIn("names no session_id", rec["telemetry_refused"])

    def test_a_sidecar_naming_a_different_log_digest_is_refused(self):
        log = log_with(self.fx.root, "header\n",
                       sidecar=dict(PROVIDER_BLOCK, log_digest="a" * 64))
        rec, _ = envelope.telemetry(log, session_id=SESSION, log_digest="b" * 64)
        self.assertIn("log_digest", rec["telemetry_refused"])

    def test_control_the_matching_digest_is_accepted(self):
        log = log_with(self.fx.root, "header\n",
                       sidecar=dict(PROVIDER_BLOCK, log_digest="b" * 64))
        rec, _ = envelope.telemetry(log, session_id=SESSION, log_digest="b" * 64)
        self.assertEqual(envelope.PROVIDER, rec["telemetry_source"])
        self.assertIsNone(rec["telemetry_refused"])


class EveryFigureIsValidatedAtTheBoundary(unittest.TestCase):
    """Refused, not coerced. `bool` is checked before `int` on purpose: in Python
    `isinstance(True, int)` is True, so `{"total_tokens": true}` would otherwise be
    recorded as one token."""

    CASES = (
        ("a negative count", {"total_tokens": -5}, "negative"),
        ("a boolean where an integer belongs", {"total_tokens": True}, "boolean"),
        ("a non-integer total", {"total_tokens": "1200"}, "not an integer"),
        ("a float total", {"total_tokens": 12.5}, "not an integer"),
        ("a total smaller than its parts",
         {"input_tokens": 1000, "output_tokens": 500, "total_tokens": 100},
         "smaller than its own parts"),
    )

    def setUp(self):
        self.fx = Fixture(state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, self.fx.root, True)

    def test_each_bad_value_is_refused_and_says_why(self):
        print()
        for name, block, expected in self.CASES:
            with self.subTest(case=name):
                bad = envelope.usage_refusals(block)
                print(f"  REFUSED    {name:<38} -> {bad[0] if bad else 'ACCEPTED'}")
                self.assertTrue(bad, f"{name} was accepted")
                self.assertIn(expected, " ".join(bad))

    def test_a_refused_sidecar_leaves_the_session_unmeasured_not_zero(self):
        log = log_with(self.fx.root, "header\n",
                       sidecar={"total_tokens": -1, "session_id": SESSION})
        rec, total = envelope.telemetry(log, session_id=SESSION)
        self.assertIsNone(rec["telemetry_source"])
        self.assertIsNone(total, "a refused figure became a measured zero")
        self.assertIn("negative", rec["telemetry_refused"])

    def test_a_refused_agent_line_is_refused_too(self):
        log = log_with(self.fx.root,
                       'XCHECK_TELEMETRY {"total_tokens": -3}\n')
        rec, total = envelope.telemetry(log, session_id=SESSION)
        self.assertIsNone(rec["telemetry_source"])
        self.assertIsNone(total)

    def test_control_a_clean_object_passes_every_check(self):
        self.assertEqual([], envelope.usage_refusals(PROVIDER_BLOCK))
        self.assertEqual([], envelope.usage_refusals({}))


class TheSidecarIsWrittenWhereTheChildIsNot(unittest.TestCase):
    """The structural half. Two claims, and only one of them is absolute.

    ABSOLUTE: the path is never handed to the child. It is not in the environment —
    `child_environment` copies `XCHECK_*` wholesale, which is exactly why an env var was
    not used — and it reaches the operator's role command only through an explicit
    `{sidecar}` placeholder the operator writes.

    SCOPED: under `container` the directory is not mounted, so nothing inside the sandbox
    can write it even knowing the path. Under `worktree`/`none` there is no OS boundary at
    all — this project's own escape matrix records that a child there reads the operator's
    `$HOME` and writes the original checkout — so the guarantee there is non-disclosure,
    which is weaker, and saying so is the point.
    """

    def setUp(self):
        self.fx = Fixture(state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, self.fx.root, True)

    def test_the_path_is_in_no_child_environment_variable(self):
        conf = complete_conf(auditor_cmd="true {prompt}")
        env = runner.child_environment(conf)
        log = Path(self.fx.root) / "s.log"
        side = str(envelope.sidecar_path(log, SESSION))
        leaked = sorted(k for k, v in env.items() if side in str(v))
        print(f"\n  ENV        {len(env)} variable(s) reach the child; "
              f"{len(leaked)} name the sidecar")
        self.assertEqual([], leaked, f"the sidecar path reaches the child via {leaked}")

    def test_it_reaches_the_role_command_only_through_the_placeholder(self):
        with_slot = complete_conf(auditor_cmd="agent --usage={sidecar} {prompt}")
        without = complete_conf(auditor_cmd="agent {prompt}")
        side = envelope.sidecar_path(Path(self.fx.root) / "s.log", SESSION)
        got = runner.build_cmd(with_slot, "Auditor", "P", sidecar=side)
        none = runner.build_cmd(without, "Auditor", "P", sidecar=side)
        print(f"  PLACEHOLDER {[a for a in got if 'usage=' in a]}")
        self.assertIn(f"--usage={side}", got)
        self.assertFalse([a for a in none if str(side) in a],
                         "a command with no placeholder was handed the path anyway")

    def test_the_directory_is_created_by_the_parent_and_is_private(self):
        log = Path(self.fx.root) / "logs" / "s.log"
        log.parent.mkdir(parents=True)
        d = envelope.make_sidecar_dir(log)
        mode = oct(d.stat().st_mode & 0o777)
        print(f"  DIR        {d} mode={mode}")
        self.assertTrue(d.is_dir())
        self.assertEqual("0o700", mode)

    def test_the_sidecar_lives_outside_the_project_because_the_logs_do(self):
        """The log root resolves outside the working tree, so the sidecar beside it is
        outside every worktree cut from that tree — which is what puts it out of a
        courier's reach as well as a child's."""
        saved = {k: os.environ.pop(k, None)
                 for k in (retention.LOG_DIR_ENV, retention.XDG_STATE_ENV)}
        self.addCleanup(lambda: [os.environ.__setitem__(k, v)
                                 for k, v in saved.items() if v is not None])
        root = retention.log_root(self.fx.root)
        side = envelope.sidecar_path(root / "s.log", SESSION)
        print(f"  OUTSIDE    {side}")
        self.assertNotIn(str(Path(self.fx.root).resolve()), str(side.resolve()))


class APositiveControlMeasuresARealSidecar(unittest.TestCase):
    """[[spy-needs-a-positive-control]]. Every test above asserts something is NOT
    believed, and "nothing is trusted" is satisfied by a reader that trusts nothing and by
    a sidecar path that is never reached. This arm has to go green through the same code
    the runner uses, or the phase has replaced a forgeable measurement with none."""

    def setUp(self):
        self.fx = Fixture(state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, self.fx.root, True)

    def test_a_wrapper_that_writes_the_sidecar_is_measured_as_provider(self):
        log = log_with(self.fx.root, "header\ntokens used\n1 540\n", sidecar=PROVIDER_BLOCK)
        rec, total = envelope.telemetry(log, session_id=SESSION)
        print(f"\n  MEASURED   source={rec['telemetry_source']!r} tokens={total} "
              f"model={rec['provider_model']!r} request={rec['provider_request_id']!r}")
        self.assertEqual(envelope.PROVIDER, rec["telemetry_source"])
        self.assertEqual(1540, total)
        self.assertEqual("req_01ABC", rec["provider_request_id"])

    def test_the_absent_case_reads_unmeasured_and_never_zero(self):
        log = log_with(self.fx.root, "a session that said nothing about its cost\n")
        rec, total = envelope.telemetry(log, session_id=SESSION)
        print(f"  ABSENT     source={rec['telemetry_source']!r} tokens={total!r}")
        self.assertIsNone(rec["telemetry_source"])
        self.assertIsNone(total, "an unmeasured session was recorded as costing zero")

    def test_the_path_the_runner_builds_is_the_path_the_reader_reads(self):
        """The two halves are the same function, asserted rather than assumed: a runner
        writing to one path while the reader looks at another is how a positive control
        goes green on a file nobody produced."""
        log = Path(self.fx.root) / "logs" / "20260903-auditor.log"
        log.parent.mkdir(parents=True)
        log.write_text("header\n", encoding="utf-8")
        envelope.make_sidecar_dir(log)
        written = envelope.sidecar_path(log, SESSION)
        written.write_text(json.dumps(dict(PROVIDER_BLOCK, session_id=SESSION)),
                           encoding="utf-8")
        rec, total = envelope.telemetry(log, session_id=SESSION)
        self.assertEqual(envelope.PROVIDER, rec["telemetry_source"])
        self.assertEqual(1540, total)


@needs_live_corpus
class NoHistoricalRecordIsRelabelled(unittest.TestCase):
    """An envelope already admitted is evidence, not a row to migrate. The corpus keeps
    every `provider` it was written with, and this phase must not have edited one."""

    def test_the_frozen_corpus_is_unedited_and_keeps_its_sources(self):
        rel = "tests/fixtures/ouroboros-4-tokens.json"
        p = subprocess.run(["git", "diff", "--stat", "HEAD", "--", rel],
                           capture_output=True, text=True, cwd=str(REPO))
        print(f"\n  FROZEN     `git diff HEAD -- {rel}` -> "
              f"{p.stdout.strip() or 'no change'}")
        self.assertEqual("", p.stdout.strip(), "the frozen fixture was edited")

    def test_every_recorded_source_is_still_a_legal_value(self):
        """Whatever is on disk keeps being readable. Adding a THIRD value cannot
        invalidate the two already written, and a record that had to be edited to load
        would be evidence rewritten to suit new code.

        The number is pinned and it is currently ZERO, which is the honest state of this
        corpus rather than a green tick: `telemetry_source` was added late and no session
        in `audit/state.json` or `audit/events.jsonl` here carries one. Pinning the count
        is what stops this test passing in silence once sessions do — see
        [[a-query-whose-failure-mode-is-silence]]. If it moves, the assertion below is
        what starts checking real values, and the count is restated with its reason.
        """
        import json as _json
        seen = []
        doc = _json.loads((REPO / "audit" / "state.json").read_text(encoding="utf-8"))
        seen += [x.get("telemetry_source") for x in doc.get("sessions", [])]
        seen += [e.get("telemetry_source") for e in envelope.read_events(REPO)]
        recorded = [x for x in seen if x is not None]
        counts = {v: recorded.count(v) for v in set(recorded)}
        print(f"  EXISTING   {len(seen)} record(s) scanned, {len(recorded)} carry a "
              f"telemetry_source: {counts or 'none — the field is unpopulated here'}")
        self.assertEqual(0, len(recorded),
                         "this corpus now records telemetry sources; restate the pin and "
                         "check the values, do not relabel them")
        for src in counts:
            self.assertIn(src, util.TELEMETRY_SOURCES,
                          f"a recorded source {src!r} is no longer a legal value — "
                          f"history was relabelled")
