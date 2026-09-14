"""What a session cost, read from the log it already writes.

`metrics` used to say token cost was unavailable without provider billing data. Half of
that was true: MONEY needs billing data this tool never sees. The other half was not —
the figure is printed at the end of every session log the tool writes itself.

The provider's structured-output mode is deliberately not used to make the read easier.
`classify_outcome` reads the same log text, so changing the output format would put a
working classifier at risk in order to reach data that is already present.
"""

import contextlib
import io
import json
import pathlib
import shutil
import tempfile
import unittest

from tests import test_corpus_baseline as base
from tests.harness import Fixture, state_doc, xcheck_submodule

envelope = xcheck_submodule("envelope")
decision = xcheck_submodule("decision")
state = xcheck_submodule("state")
runner = xcheck_submodule("runner")
cli = xcheck_submodule("cli")

NBSP = " "
TOKENS = pathlib.Path(__file__).parent / "fixtures" / "ouroboros-4-tokens.json"

# Measured over the frozen corpus by the shipped reader. Pinned so a change to the parser
# or to the join has to come and restate them.
LOGS = 151
MEASURED = 150
TOTAL = 24_938_795
TRANSITIONS = 135
PER_TRANSITION = 184_732
BARREN_TOKENS = 21_293_913
BARREN_PCT = 85.4
UNFINISHED = "0c7049a9a74137e1"


class TheParserReadsTheFigureOrSaysItCannot(unittest.TestCase):

    def test_the_shape_the_corpus_actually_carries(self):
        """`tokens used` on its own line, the figure on the next, thousands separated by
        U+00A0. All 150 measured logs of the Ouroboros-4 run look exactly like this."""
        text = f"hook: Stop\nhook: Stop Completed\ntokens used\n206{NBSP}949\nSummary...\n"
        self.assertEqual(206949, envelope.tokens_in_log(text))

    def test_the_separator_is_not_a_contract(self):
        for raw, want in ((f"tokens used\n1{NBSP}234{NBSP}567", 1234567),
                          ("tokens used\n1,234", 1234),
                          ("tokens used\n1 234", 1234),
                          ("tokens used\n1_234", 1234),
                          ("tokens used: 4321", 4321),
                          ("TOKENS USED\n77", 77),
                          ("  tokens used  \n  99  ", 99)):
            with self.subTest(raw=raw):
                self.assertEqual(want, envelope.tokens_in_log(raw))

    def test_an_absent_figure_is_none_and_never_zero(self):
        """The whole reason this returns None. A session that reported nothing did not
        cost nothing, and a metric that averages in a guessed 0 reports a run as cheaper
        the more of its logs it failed to read."""
        for raw in ("", "no summary here", "tokens used", "tokens used\n\n7",
                    "tokens used\nnone", "tokens used\n206 949 (in 900)"):
            with self.subTest(raw=raw):
                self.assertIsNone(envelope.tokens_in_log(raw))

    def test_it_never_guesses_at_a_shape_it_does_not_know(self):
        """CONTROL for the one above. Strictness is the point: a figure it cannot parse
        exactly is reported as unmeasured, not approximated from nearby digits."""
        self.assertIsNone(envelope.tokens_in_log("total cost 12345 tokens"))
        self.assertIsNone(envelope.tokens_in_log("tokens used by the child: about 5k"))

    def test_the_last_figure_wins(self):
        """No log in the corpus carries the phrase twice, but a session that quotes an
        earlier log in its own output would put an older figure above its own summary,
        and the summary is what THIS session cost."""
        self.assertEqual(2, envelope.tokens_in_log("tokens used\n1\n...\ntokens used\n2"))

    def test_an_unreadable_log_is_the_same_claim_as_a_log_with_no_figure(self):
        self.assertIsNone(envelope.log_tokens("/nonexistent/session.log"))
        self.assertIsNone(envelope.log_tokens(None))

    def test_it_reads_one_off_disk(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="xcheck-tok-"))
        self.addCleanup(shutil.rmtree, d, True)
        p = d / "s.log"
        p.write_text(f"work\ntokens used\n12{NBSP}345\n", encoding="utf-8")
        self.assertEqual(12345, envelope.log_tokens(p))


class TheFigureTravelsOnTheSessionsOwnEvent(unittest.TestCase):
    """Recorded once, at `finish`, rather than re-derived by every reader — and the
    closed schema had to be asked before it could be, not after."""

    def session(self, f, sid, log):
        prof = runner.resolve_profile({}, "Auditor")
        rec = envelope.dispatch_record(["codex", "exec"], "Auditor", "charter", "prompt",
                                       sid, prof, 0, "unknown")
        envelope.store(f.root, rec, event="session_dispatched")
        rec = envelope.finish(rec, 0, "ok", 1.0, "b" * 40, log)
        envelope.store(f.root, rec, event="session_finished")
        return rec

    def setUp(self):
        self.f = Fixture(state_doc())
        self.addCleanup(shutil.rmtree, self.f.root, True)
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="xcheck-tok-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.good = self.d / "g.log"
        self.good.write_text(f"tokens used\n206{NBSP}949\n", encoding="utf-8")
        self.bad = self.d / "b.log"
        self.bad.write_text("killed before it printed a summary\n", encoding="utf-8")

    def test_a_measured_session_carries_its_figure_to_state_and_to_the_stream(self):
        rec = self.session(self.f, "a" * 16, self.good)
        self.assertEqual(206949, rec["tokens"])
        doc = json.loads((self.f.audit / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(206949, doc["sessions"][0]["tokens"])
        fin = [e for e in envelope.read_events(self.f.root)
               if e.get("event") == "session_finished"]
        self.assertEqual([206949], [e.get("tokens") for e in fin])

    def test_an_unmeasured_session_records_null_and_emits_nothing(self):
        rec = self.session(self.f, "b" * 16, self.bad)
        self.assertIsNone(rec["tokens"])
        doc = json.loads((self.f.audit / "state.json").read_text(encoding="utf-8"))
        self.assertIsNone(doc["sessions"][0]["tokens"])
        fin = [e for e in envelope.read_events(self.f.root)
               if e.get("event") == "session_finished"]
        self.assertNotIn("tokens", fin[0],
                         "an absent measurement reached the stream as a value; a reader "
                         "summing events would now be adding a claim nobody made")

    def test_the_schema_was_asked_before_the_field_was_written(self):
        """The field is declared, not smuggled. Before it was, `store` raised StateError
        on every finished session — the closed schema turning a silent shape drift into a
        refusal at the moment of the write."""
        rec = {"session_id": "a" * 16, "role": "Auditor", "provider": "openai",
               "agent_model": "m", "executable": "codex", "executable_version": "1",
               "charter_hash": "c" * 64, "prompt_hash": "d" * 64, "state_revision": 0,
               "head_before": "unknown", "sandbox_profile": "worktree",
               "started": "2026-01-01T00:00:00+00:00"}
        state._check_record(dict(rec, tokens=206949), state.SESSION_FIELDS, "probe")
        state._check_record(dict(rec, tokens=None), state.SESSION_FIELDS, "probe")
        for bad in (-1, True, "206949", 1.5):
            with self.subTest(bad=bad):
                with self.assertRaises(state.StateError):
                    state._check_record(dict(rec, tokens=bad), state.SESSION_FIELDS, "p")


class TheReportDividesByTransitionsNotBySessions(unittest.TestCase):

    def report(self, finished, transitions=()):
        f = Fixture(state_doc())
        self.addCleanup(shutil.rmtree, f.root, True)
        ev = [dict(e, event="session_finished") for e in finished]
        ev += [dict(e, event="state_transition") for e in transitions]
        return decision.metrics_report(state.load_state(f.audit), ev)["tokens"]

    def test_a_cheap_session_that_moved_nothing_is_not_an_improvement(self):
        """The reason the headline is per transition. Both runs below cost the same per
        SESSION; the second achieved half as much, and only the per-transition figure
        says so."""
        two = self.report([{"session_id": "a" * 16, "tokens": 100},
                           {"session_id": "b" * 16, "tokens": 100}],
                          [{"session_id": "a" * 16, "verb": "file-finding"},
                           {"session_id": "b" * 16, "verb": "file-finding"}])
        one = self.report([{"session_id": "a" * 16, "tokens": 100},
                           {"session_id": "b" * 16, "tokens": 100}],
                          [{"session_id": "a" * 16, "verb": "file-finding"}])
        self.assertEqual(100, two["per_canonical_transition"])
        self.assertEqual(200, one["per_canonical_transition"])
        self.assertEqual(100, one["on_sessions_that_moved_nothing"])

    def test_an_undeclared_verb_is_not_a_transition_to_divide_by(self):
        """W-07: `_apply` takes `verb` as a free string. If a minted verb counted, a
        session could improve the run's headline cost by inventing one."""
        r = self.report([{"session_id": "a" * 16, "tokens": 100}],
                        [{"session_id": "a" * 16, "verb": "repair-body-paths"}])
        self.assertEqual(0, r["canonical_transitions"])
        self.assertIsNone(r["per_canonical_transition"])
        self.assertEqual(100, r["on_sessions_that_moved_nothing"])

    def test_an_unmeasured_session_is_excluded_rather_than_counted_as_zero(self):
        """CONTROL, and the whole point of the None. Read as 0 the figure below would be
        50; excluded, it stays 100 and the report says one session is unaccounted."""
        r = self.report([{"session_id": "a" * 16, "tokens": 100},
                         {"session_id": "b" * 16}],
                        [{"session_id": "a" * 16, "verb": "file-finding"}])
        self.assertEqual(100, r["per_canonical_transition"])
        self.assertEqual(1, r["sessions_unmeasured"])
        self.assertEqual(2, r["sessions_finished"])

    def test_a_stream_with_no_figures_reports_null_with_its_reason(self):
        r = self.report([{"session_id": "a" * 16}, {"session_id": "b" * 16}])
        self.assertIsNone(r["total"])
        self.assertIsNone(r["per_canonical_transition"])
        self.assertEqual(2, r["sessions_unmeasured"])


class TheFrozenRunPricedByShippedCode(unittest.TestCase):
    """The corpus, joined to its logs by `log_digest` — the key `session_finished`
    actually records. The logs are untracked (`.gitignore:2`), so the durable evidence is
    the frozen digest→tokens table, derived once by the same reader that ships."""

    @classmethod
    def setUpClass(cls):
        cls.table = json.loads(TOKENS.read_text(encoding="utf-8"))
        cls.by_digest = cls.table["tokens_by_log_digest"]
        cls.events = [json.loads(l) for l in
                      base.CORPUS.read_text(encoding="utf-8").splitlines() if l.strip()]
        cls.priced = []
        for e in cls.events:
            if e.get("event") != "session_finished":
                cls.priced.append(e)
                continue
            tok = cls.by_digest.get(e.get("log_digest"))
            cls.priced.append(e if tok is None else dict(e, tokens=tok))

    def test_the_table_covers_every_log_that_run_wrote(self):
        self.assertEqual(LOGS, self.table["logs"])
        self.assertEqual(MEASURED, self.table["with_a_figure"])

    def test_the_one_session_it_cannot_price_is_named_not_hidden(self):
        """W-02's fourth criterion. The unpriced log belongs to the one dispatch that
        never reached `session_finished`: it was killed before its summary was printed,
        so the absence has a cause rather than being a hole in the reader."""
        dispatched = {e["session_id"] for e in self.events
                      if e.get("event") == "session_dispatched"}
        finished = {e["session_id"] for e in self.events
                    if e.get("event") == "session_finished"}
        self.assertEqual({UNFINISHED}, dispatched - finished)
        self.assertEqual(base.DISPATCHED, len(dispatched))
        unpriced = [d for d, v in self.by_digest.items() if v is None]
        self.assertEqual(1, len(unpriced))
        claimed = {e.get("log_digest") for e in self.events
                   if e.get("event") == "session_finished"}
        self.assertNotIn(unpriced[0], claimed,
                         "the unpriced log is claimed by a session that DID finish — "
                         "then the reason for the gap is the parser, not the kill")

    def test_the_run_prices_out_the_way_the_analysis_said_it_would(self):
        f = Fixture(state_doc())
        self.addCleanup(shutil.rmtree, f.root, True)
        r = decision.metrics_report(state.load_state(f.audit), self.priced)["tokens"]
        print("\n  OUROBOROS-4 PRICED BY SHIPPED CODE")
        print(f"    measured                     {r['sessions_measured']} of "
              f"{r['sessions_finished']} finished")
        print(f"    total                        {r['total']:,}")
        print(f"    canonical transitions        {r['canonical_transitions']}")
        print(f"    per canonical transition     {r['per_canonical_transition']:,}")
        print(f"    on sessions that moved none  {r['on_sessions_that_moved_nothing']:,} "
              f"({r['share_that_moved_nothing_pct']}%)")
        self.assertEqual(MEASURED, r["sessions_measured"])
        self.assertEqual(0, r["sessions_unmeasured"])
        self.assertEqual(TOTAL, r["total"])
        self.assertEqual(TRANSITIONS, r["canonical_transitions"])
        self.assertEqual(PER_TRANSITION, r["per_canonical_transition"])
        self.assertEqual(BARREN_TOKENS, r["on_sessions_that_moved_nothing"])
        self.assertEqual(BARREN_PCT, r["share_that_moved_nothing_pct"])

    def test_the_logs_on_disk_still_agree_with_the_frozen_table(self):
        """Re-derives from `audit/orchestrator-logs/` when it is still there. Untracked,
        so its absence is not a failure — but its DISAGREEMENT is, and a silent skip
        would let the table rot unnoticed, so the skip counts itself."""
        logs = pathlib.Path(__file__).parent.parent / "audit" / "orchestrator-logs"
        on_disk = sorted(logs.glob("*.log")) if logs.is_dir() else []
        if not on_disk:
            print("\n  logs absent from disk — frozen table not re-derived this run")
            return
        rederived = {envelope.sha256_file(p): envelope.log_tokens(p) for p in on_disk}
        shared = set(rederived) & set(self.by_digest)
        print(f"\n  re-derived {len(rederived)} log(s); {len(shared)} shared with the "
              f"frozen table")
        self.assertTrue(shared, "no log on disk is in the table — the join key moved")
        for digest in sorted(shared):
            self.assertEqual(self.by_digest[digest], rederived[digest],
                             f"the reader no longer gets the frozen figure for {digest[:12]}")


# The live project's shape at the moment the audit caught the defect: 7 of 158 finished
# sessions carried a token figure, and those 7 made 24 of the run's 159 canonical
# transitions. The old formula divided their tokens by all 159.
LIVE_MEASURED, LIVE_UNMEASURED = 7, 151
LIVE_CANONICAL, LIVE_BY_MEASURED = 159, 24


class TheDenominatorComesFromTheSessionsThatWereMeasured(unittest.TestCase):
    """A ratio whose numerator covers 7 sessions and whose denominator covers 158 is
    not a rate. It reported 10,483 tokens per canonical transition on a run whose
    measured sessions actually spent 69,450 — 6.6x too cheap, and the figure improves
    the LESS of the run is measured, which is the wrong direction for a cost metric."""

    def report(self, finished, transitions=()):
        f = Fixture(state_doc())
        self.addCleanup(shutil.rmtree, f.root, True)
        ev = [dict(e, event="session_finished") for e in finished]
        ev += [dict(e, event="state_transition") for e in transitions]
        return decision.metrics_report(state.load_state(f.audit), ev)["tokens"]

    def test_the_live_projects_shape_divides_by_24_not_by_159(self):
        """The live numbers, reconstructed as a stream so the pin cannot rot as the
        real audit continues. The live arm below checks the same invariant against
        whatever the project actually holds today."""
        finished = [{"session_id": f"m{i:015d}", "tokens": 100_000}
                    for i in range(LIVE_MEASURED)]
        finished += [{"session_id": f"u{i:015d}"} for i in range(LIVE_UNMEASURED)]
        transitions = [{"session_id": f"m{i % LIVE_MEASURED:015d}", "verb": "file-finding"}
                       for i in range(LIVE_BY_MEASURED)]
        transitions += [{"session_id": f"u{i:015d}", "verb": "file-finding"}
                        for i in range(LIVE_CANONICAL - LIVE_BY_MEASURED)]
        r = self.report(finished, transitions)
        self.assertEqual(LIVE_MEASURED, r["sessions_measured"])
        self.assertEqual(LIVE_UNMEASURED, r["sessions_unmeasured"])
        self.assertEqual(LIVE_CANONICAL, r["canonical_transitions"])
        self.assertEqual(LIVE_BY_MEASURED, r["canonical_transitions_measured"])
        self.assertEqual(4.4, r["coverage_pct"])
        self.assertEqual(round(700_000 / LIVE_BY_MEASURED), r["per_canonical_transition"])
        self.assertEqual(round(700_000 / LIVE_CANONICAL),
                         4403, "the arithmetic of the OLD formula, for the contrast")
        self.assertNotEqual(4403, r["per_canonical_transition"])

    def test_the_control_the_old_formula_fails(self):
        """CONTROL. One measured session worth 100 tokens made 1 transition; nine
        unmeasured sessions made 9 more. The measured cost per transition is 100. The
        old formula said 100/10 = 10 — a run whose every session cost the same would
        report a tenth of its cost simply because nine of them went unread."""
        r = self.report(
            [{"session_id": "m" * 16, "tokens": 100}]
            + [{"session_id": f"u{i:015d}"} for i in range(9)],
            [{"session_id": "m" * 16, "verb": "file-finding"}]
            + [{"session_id": f"u{i:015d}", "verb": "file-finding"} for i in range(9)])
        old = round(r["total"] / r["canonical_transitions"])
        print(f"\n  CONTROL  old formula {r['total']}/{r['canonical_transitions']} = {old}"
              f"   corrected {r['total']}/{r['canonical_transitions_measured']} = "
              f"{r['per_canonical_transition']}   coverage {r['coverage_pct']}%")
        self.assertEqual(10, old, "the old formula's number, computed here to be refuted")
        self.assertEqual(100, r["per_canonical_transition"])
        self.assertEqual(1, r["canonical_transitions_measured"])
        self.assertEqual(10, r["canonical_transitions"])

    def test_no_measured_session_moved_anything_reports_null_not_a_ratio(self):
        """Nine unmeasured sessions moved the run; the one measured session moved
        nothing. There is no measured rate to report, and 0 transitions must not
        divide."""
        r = self.report(
            [{"session_id": "m" * 16, "tokens": 100}]
            + [{"session_id": f"u{i:015d}"} for i in range(9)],
            [{"session_id": f"u{i:015d}", "verb": "file-finding"} for i in range(9)])
        self.assertEqual(9, r["canonical_transitions"])
        self.assertEqual(0, r["canonical_transitions_measured"])
        self.assertIsNone(r["per_canonical_transition"])

    def test_the_live_stream_obeys_the_invariant_whatever_its_numbers_are(self):
        """The non-rotting half: over the project's real event stream, the denominator
        is the transitions made by measured sessions. Prints the live shape so a reader
        sees how much of the run the figure speaks for."""
        proj = pathlib.Path(__file__).parent.parent
        if not (proj / "audit" / "state.json").exists():
            print("\n  live audit absent — invariant not checked against it this run")
            return
        ev = envelope.read_events(proj)
        r = decision.metrics_report(state.load_state(proj / "audit"), ev)["tokens"]
        declared = envelope.declared_verbs()
        measured = {e.get("session_id") for e in ev
                    if e.get("event") == "session_finished"
                    and isinstance(e.get("tokens"), int) and not isinstance(e.get("tokens"), bool)}
        by_measured = len([e for e in ev if e.get("event") == "state_transition"
                           and e.get("verb") in declared and e.get("session_id") in measured])
        print(f"\n  LIVE  measured {r['sessions_measured']} of {r['sessions_finished']} "
              f"({r['coverage_pct']}%)  transitions {r['canonical_transitions']} of which "
              f"{r['canonical_transitions_measured']} measured  "
              f"per transition {r['per_canonical_transition']}")
        self.assertEqual(by_measured, r["canonical_transitions_measured"])
        if r["per_canonical_transition"] is not None:
            self.assertEqual(round(r["total"] / by_measured), r["per_canonical_transition"])


class TheBudgetIsACeilingOnTheRunNotOnItsStarts(unittest.TestCase):
    """W-06 ran 8055.8s of session time under `--budget 7200` and its report called the
    budget unexhausted. The check gated the START of a session, so any session dispatched
    one second under the ceiling could run a full `session_timeout` past it."""

    def loop(self, budget, session_timeout=3600, per_session=10, max_sessions=5,
             old_rule=False):
        f = Fixture(state_doc())
        self.addCleanup(shutil.rmtree, f.root, True)
        now = [0.0]
        dispatched = []

        def fake_next(project, conf, dry_run, assume_yes):
            dispatched.append(now[0])
            now[0] += per_session
            return "dispatched"

        conf = {"session_timeout": str(session_timeout), "loop_progress_limit": "0"}
        saved_next, saved_digest = cli.cmd_next, cli.audit_state_digest
        saved_number = cli.conf_number
        cli.cmd_next = fake_next
        cli.audit_state_digest = lambda p, n=now: f"d{n[0]}"   # every session "moved" state
        if old_rule:
            # Reinstates the START-gating rule by execution rather than by arithmetic:
            # a worst case of 0 can never exceed the remaining budget, so the new
            # branch is dead and the loop behaves exactly as it did in W-06.
            cli.conf_number = lambda c, k: 0 if k == "session_timeout" else saved_number(c, k)
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cli.cmd_loop(f.root, conf, False, False, max_sessions, budget=budget,
                             clock=lambda: now[0], src_digest=lambda: "fixed")
            return dispatched, buf.getvalue()
        finally:
            cli.cmd_next, cli.audit_state_digest = saved_next, saved_digest
            cli.conf_number = saved_number

    def test_it_refuses_a_session_that_cannot_finish_inside_the_budget(self):
        """budget 100s, session_timeout 3600s. The first session is dispatched; the
        second is not, because 90s of budget cannot hold a session allowed to run an
        hour. The stop line names the arithmetic that refused it."""
        dispatched, out = self.loop(budget=100, session_timeout=3600)
        print(f"\n  budget=100 session_timeout=3600 -> {len(dispatched)} dispatched")
        print(f"  {out.strip()}")
        self.assertEqual(1, len(dispatched), "a second session was dispatched under a "
                                             "budget that cannot hold it")
        self.assertIn("session_timeout=3600", out)
        self.assertIn("3510s over the ceiling", out)
        self.assertIn("at least 3610s", out)

    def test_the_old_check_would_have_dispatched_all_five(self):
        """CONTROL: the same run under the START-gating rule. Every session begins
        under 100s of elapsed time, so the old check dispatches all five — 50s of
        session time here, and 8055.8s against a 7200s ceiling in W-06."""
        dispatched, out = self.loop(budget=100, old_rule=True)
        print(f"\n  CONTROL  start-gating rule dispatched {len(dispatched)} sessions at "
              f"{dispatched} — each start under the 100s ceiling, "
              f"{len(dispatched) * 10}s of session time spent")
        self.assertEqual(5, len(dispatched))
        self.assertIn("max_sessions=5", out)

    def test_a_budget_that_can_hold_a_session_still_dispatches(self):
        """The gate is not a blanket refusal: with room for the worst case, the loop
        runs until the remaining budget no longer covers one more session."""
        dispatched, out = self.loop(budget=100, session_timeout=30, per_session=25)
        self.assertEqual(3, len(dispatched), "0s, 25s, 50s dispatch; at 75s only 25s "
                                             "remain against a 30s worst case")
        self.assertIn("stop: --budget 100s has 25s left", out)

    def test_no_budget_is_still_no_budget(self):
        dispatched, out = self.loop(budget=None, session_timeout=3600)
        self.assertEqual(5, len(dispatched))
        self.assertIn("max_sessions=5", out)


if __name__ == "__main__":
    unittest.main()
