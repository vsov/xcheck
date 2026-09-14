"""Deadlines beyond the hard timeout. The audit's case, from this project's own run:

    W-06, 2026-09-01. Eight sessions worked (439.3s min, 670.4s median, 1052.7s max;
    logs 527KB-1.1MB). The ninth wrote 1,764 bytes — the CLI banner and the prompt
    echoed back — and then held a session slot for 3600.015s, exactly the hard cap,
    burning an hour of a 7200s budget to produce nothing.

`session_timeout` cannot express that. It answers "how long may a session run", and this
session's problem was that it never started. Three deadlines answer the questions the hard
cap cannot: has it said anything, has it DONE anything, and is it still doing it.
"""

import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from tests.harness import Fixture, finding_record, queue_pass, state_doc, xcheck_submodule

envelope = xcheck_submodule("envelope")
policy = xcheck_submodule("policy")
runner = xcheck_submodule("runner")
util = xcheck_submodule("util")

# The W-06 measurements the defaults are derived from. Restated here so the justification
# and the numbers cannot drift apart silently.
W06_WORKED_MIN_S = 439.3
W06_WORKED_MAX_S = 1052.7
W06_STALLED_S = 3600.0
W06_STALLED_LOG_BYTES = 1764
W06_SMALLEST_WORKING_LOG_BYTES = 526_723


def conf(**over):
    raw = dict(util.CONF_DEFAULTS, **{k: str(v) for k, v in over.items()})
    raw.setdefault("trust_level", "trusted")   # see tests/test_trust_level.py
    return util.Conf(raw)


class TheDefaultsAreDerivedFromMeasuredSessions(unittest.TestCase):

    def test_the_four_deadlines_exist_and_are_separately_configurable(self):
        for key in ("startup_deadline", "output_deadline", "activity_deadline",
                    "idle_deadline"):
            self.assertIn(key, util.CONF_DEFAULTS)
            self.assertIn(key, util.NUMERIC_CONF)
        self.assertIn("session_timeout", util.CONF_DEFAULTS)

    def test_each_default_is_justified_against_the_w06_numbers(self):
        startup = int(util.CONF_DEFAULTS["startup_deadline"])
        first = int(util.CONF_DEFAULTS["output_deadline"])
        act = int(util.CONF_DEFAULTS["activity_deadline"])
        idle = int(util.CONF_DEFAULTS["idle_deadline"])
        hard = int(util.CONF_DEFAULTS["session_timeout"])
        print("\n  THE FOUR DEADLINES, against the W-06 run")
        print(f"    startup            {startup:>5}s   every session printed its banner "
              f"in seconds")
        print(f"    output             {first:>5}s   longer than the ENTIRE life of the "
              f"fastest working session ({W06_WORKED_MIN_S}s)")
        print(f"    activity           {act:>5}s   longer than the LONGEST complete "
              f"session observed ({W06_WORKED_MAX_S}s)")
        print(f"    idle               {idle:>5}s   longer than the ENTIRE duration of "
              f"every session in that run (max {W06_WORKED_MAX_S}s)")
        print(f"    hard session       {hard:>5}s   unchanged")
        print(f"    the stalled session: {W06_STALLED_S}s, {W06_STALLED_LOG_BYTES:,} bytes "
              f"vs {W06_SMALLEST_WORKING_LOG_BYTES:,} for the smallest working log "
              f"({W06_SMALLEST_WORKING_LOG_BYTES // W06_STALLED_LOG_BYTES}x)")
        print(f"    saved on that one session: {W06_STALLED_S - first:.0f}s")
        self.assertGreater(first, W06_WORKED_MIN_S,
                           "the first-action deadline is shorter than the fastest real "
                           "session's whole run — it would kill working sessions")
        self.assertLess(first, W06_STALLED_S,
                        "the deadline does not fire before the hard cap, so it saves "
                        "nothing")
        self.assertGreater(idle, W06_WORKED_MAX_S,
                           "a silence shorter than a whole working session is not "
                           "evidence of a stall")
        self.assertLess(startup, first)
        # PHASE 8. The activity bound has no per-session first-activity data to be
        # derived from — `first_activity_s` is recorded for the FIRST time by this
        # phase — so it takes the conservative derivation `idle_deadline` takes: a
        # session that finished at 1052.7s could have made its first observable write
        # at the very end, so a bound at or below that would kill working sessions.
        self.assertGreater(act, W06_WORKED_MAX_S,
                           "an activity bound shorter than the longest complete session "
                           "would kill a session that worked and reported late")
        self.assertLess(act, W06_STALLED_S,
                        "the activity bound does not fire before the hard cap, so a "
                        "child that pads its log keeps the whole hour")
        print(f"    saved on a PADDING child: {W06_STALLED_S - act:.0f}s "
              f"(the output bound alone saves nothing there — padding clears it)")

    def test_they_are_policy_keys_and_load_from_the_operator_profile(self):
        """What may run and for how long is the operator's call, not the audited
        repository's — a subject that can extend its own deadlines has none."""
        for key in ("startup_deadline", "output_deadline", "activity_deadline",
                    "idle_deadline"):
            self.assertEqual("policy", policy.classify(key), key)
            self.assertIn(key, policy.POLICY_KEYS)
        fx = Fixture(state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, fx.root, True)
        (fx.audit / "orchestrator.conf").write_text("idle_deadline=30\n", encoding="utf-8")
        with self.assertRaises(SystemExit) as e:
            util.load_conf(fx.audit)
        self.assertIn("idle_deadline", str(e.exception))

    def test_zero_turns_one_off(self):
        self.assertEqual(0, util.NUMERIC_CONF["idle_deadline"])
        self.assertEqual(0, util.NUMERIC_CONF["activity_deadline"])
        self.assertEqual(0, util.conf_number(conf(idle_deadline=0), "idle_deadline"))


class AStalledSessionIsKilledAndNamed(unittest.TestCase):

    def child(self, script, **over):
        td = tempfile.mkdtemp(prefix="xcheck-watchdog-")
        self.addCleanup(shutil.rmtree, td, True)
        log = Path(td) / "s.log"
        timings = {}
        rc, outcome, elapsed = runner.run_child(
            ["sh", "-c", script], td, log, {}, conf(**over), timings=timings)
        return rc, outcome, elapsed, timings, log

    def test_a_header_only_child_dies_at_the_output_deadline(self):
        """The W-06 case, reproduced: something is printed, and then nothing happens."""
        rc, outcome, elapsed, t, log = self.child(
            "echo 'OpenAI Codex v0.144.6'; echo 'workdir: /tmp'; sleep 60",
            output_deadline=3, idle_deadline=0, startup_deadline=0,
            session_timeout=60)
        print(f"\n  STALLED    outcome={outcome}  after {elapsed:.1f}s  "
              f"(hard cap was 60s)")
        print(f"             {t.get('stall_note')}")
        self.assertEqual("stalled", outcome)
        self.assertEqual("output", t.get("stalled_by"))
        self.assertLess(elapsed, 20, "the deadline did not fire")
        self.assertIn("output", t["stall_note"])
        self.assertIn("banner", t["stall_note"])

    def test_a_child_that_never_speaks_dies_at_the_startup_deadline(self):
        rc, outcome, elapsed, t, _ = self.child(
            "sleep 60", startup_deadline=2, output_deadline=0, idle_deadline=0,
            session_timeout=60)
        print(f"  STARTUP    outcome={outcome}  after {elapsed:.1f}s — {t['stall_note']}")
        self.assertEqual("stalled", outcome)
        self.assertEqual("startup", t["stalled_by"])
        self.assertLess(elapsed, 20)

    def test_a_child_that_goes_quiet_dies_at_the_idle_deadline(self):
        rc, outcome, elapsed, t, _ = self.child(
            "python3 -c \"print('x'*9000, flush=True)\"; sleep 60",
            startup_deadline=0, output_deadline=0, idle_deadline=3,
            session_timeout=60)
        print(f"  IDLE       outcome={outcome}  after {elapsed:.1f}s — {t['stall_note']}")
        self.assertEqual("stalled", outcome)
        self.assertEqual("idle", t["stalled_by"])
        self.assertLess(elapsed, 25)

    def test_the_kill_takes_the_whole_process_group(self):
        """A grandchild the stalled agent spawned is exactly the process that would
        outlive it. The stall path uses the same `kill_group` as every other bound."""
        td = tempfile.mkdtemp(prefix="xcheck-watchdog-")
        self.addCleanup(shutil.rmtree, td, True)
        marker = Path(td) / "grandchild-was-alive"
        script = (f"(sleep 8; touch {marker}) & echo banner; sleep 60")
        log = Path(td) / "s.log"
        rc, outcome, elapsed = runner.run_child(
            ["sh", "-c", script], td, log, {}, conf(
                output_deadline=2, idle_deadline=0, startup_deadline=0,
                session_timeout=60, kill_grace=1))
        self.assertEqual("stalled", outcome)
        time.sleep(9)
        print(f"  GROUP KILL grandchild marker exists after the kill: {marker.exists()}")
        self.assertFalse(marker.exists(),
                         "a grandchild outlived the stalled session's process group")


class AWorkingSessionIsNotKilled(unittest.TestCase):
    """CONTROL. Without it every assertion above is satisfied by a watchdog that kills
    everything, which is a worse tool than the one that killed nothing."""

    def test_a_slow_but_working_child_survives_all_three_deadlines(self):
        td = tempfile.mkdtemp(prefix="xcheck-watchdog-")
        self.addCleanup(shutil.rmtree, td, True)
        log = Path(td) / "s.log"
        timings = {}
        # Emits ~1.5KB every second for 6s: slower than any deadline here, and working.
        script = ("python3 -c \"import sys,time\n"
                  "for i in range(6):\n"
                  "    sys.stdout.write('working ' + 'x'*1500 + chr(10)); "
                  "sys.stdout.flush(); time.sleep(1)\"")
        rc, outcome, elapsed = runner.run_child(
            ["sh", "-c", script], td, log, {}, conf(
                startup_deadline=3, output_deadline=4, idle_deadline=3,
                session_timeout=60), timings=timings)
        print(f"\n  CONTROL    outcome={outcome}  rc={rc}  {elapsed:.1f}s  "
              f"bytes={timings['bytes']}  idle_s={timings['idle_s']}  "
              f"stalled_by={timings.get('stalled_by')}")
        self.assertEqual("ok", outcome, "a working session was killed")
        self.assertEqual(0, rc)
        self.assertIsNone(timings.get("stalled_by"))

    def test_a_quiet_session_that_recorded_a_transition_has_acted(self):
        """'Useful action' is not 'chatty'. A session that filed a finding has acted,
        whatever its log looks like — so the first-action deadline reads the event
        stream as well as the byte count."""
        fx = Fixture(state_doc(findings=[finding_record("F-0001", "reported")],
                               queue=[queue_pass("P-01")]))
        self.addCleanup(shutil.rmtree, fx.root, True)
        sid = "a" * 16
        self.assertFalse(envelope.session_moved(fx.root, sid))
        envelope.emit(fx.root, "state_transition", sid, verb="file-finding",
                      target="F-0002", from_status=None, to_status="reported")
        print(f"  MOVED      after one `file-finding` event: "
              f"{envelope.session_moved(fx.root, sid)}")
        self.assertTrue(envelope.session_moved(fx.root, sid))

    def test_an_undeclared_verb_does_not_count_as_movement(self):
        fx = Fixture(state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, fx.root, True)
        envelope.emit(fx.root, "state_transition", "b" * 16, verb="not-a-verb")
        self.assertFalse(envelope.session_moved(fx.root, "b" * 16))


class TheNewOutcomeIsCarriedEverywhere(unittest.TestCase):

    def test_it_is_a_declared_outcome(self):
        self.assertIn("stalled", util.OUTCOMES)
        self.assertEqual("stalled", runner.classify_outcome(-15, stalled=True))

    def test_a_stall_is_not_a_timeout(self):
        """They call for opposite operator actions: raise the budget, or find out why
        nothing happened. One value with a note would lose that."""
        self.assertEqual("timeout", runner.classify_outcome(-15, timed_out=True))
        self.assertNotEqual(runner.classify_outcome(-15, timed_out=True),
                            runner.classify_outcome(-15, timed_out=True, stalled=True))

    def test_it_is_never_retried_by_default(self):
        retry, why = runner.retry_decision("stalled", attempt=0, limit=5)
        print(f"\n  RETRY      stalled -> retry={retry}: {why[:96]}")
        self.assertFalse(retry)
        self.assertIn("stall", why)

    def test_the_retry_policy_is_still_total_over_the_outcomes(self):
        for outcome in util.OUTCOMES:
            retry, why = runner.retry_decision(outcome, attempt=0, limit=5)
            self.assertTrue(why, f"{outcome} has no answer")

    def test_it_counts_as_a_killed_session_in_metrics(self):
        """A stalled session is one the orchestrator ended on purpose, so it belongs in
        the killed count — otherwise a run that stalled repeatedly would look calm."""
        decision = xcheck_submodule("decision")
        self.assertIn("stalled", decision.KILLED_OUTCOMES)

    def test_the_record_says_which_deadline_fired(self):
        td = tempfile.mkdtemp(prefix="xcheck-watchdog-")
        self.addCleanup(shutil.rmtree, td, True)
        log = Path(td) / "s.log"
        log.write_text("banner\n", encoding="utf-8")
        profile = runner.resolve_profile(conf(), "Auditor")
        rec = envelope.dispatch_record(["sh"], "Auditor", "c", "p", "a" * 16, profile, 1,
                                       head_before="b" * 40)
        rec = envelope.finish(rec, -15, "stalled", 600.0, "c" * 40, log,
                              note="killed at the output deadline (600s): 1764 "
                                   "bytes written")
        print(f"  RECORD     outcome={rec['outcome']}  note={rec['note'][:70]}…")
        self.assertEqual("stalled", rec["outcome"])
        self.assertIn("output", rec["note"])


# --------------------------------------------------------------------------
# PHASE 8 (third audit): the bound that watches WORK, not volume
# --------------------------------------------------------------------------
#
# The audit's sentence: "агент может напечатать 2 049 байт бессодержательного текста и
# пройти first-action gate. Далее он может поддерживать idle timer периодическими
# строками." Both halves are reproduced below as children that do exactly that, and both
# now die — not at the hard cap, at the activity bound.
#
# The rename is the other half. `first_action_deadline` promised semantic progress
# detection and delivered a byte count; it is `output_deadline` now, unchanged in
# behaviour, and the promise it used to make is kept by a different mechanism.

def _repo(case):
    """A temp git repo to be a session's worktree, so `worktree-write` is observable."""
    td = Path(tempfile.mkdtemp(prefix="xcheck-activity-"))
    case.addCleanup(shutil.rmtree, td, True)
    for argv in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                 ["config", "user.name", "t"]):
        subprocess.run(["git"] + argv, cwd=td, check=True, capture_output=True)
    (td / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=td, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=td, check=True,
                   capture_output=True)
    return td


class TheActivitySignalIsEvidenceTheChildCannotPrint(unittest.TestCase):

    def test_the_sources_are_enumerated_and_none_of_them_is_a_byte_count(self):
        print("\n  ACTIVITY SOURCES")
        for src in runner.ACTIVITY_SOURCES:
            print(f"    {src}")
        self.assertEqual(("canonical-transition", "telemetry-sidecar", "worktree-write"),
                         runner.ACTIVITY_SOURCES,
                         "a source added here must be added on purpose: every one of "
                         "them is something the child had to DO, observed by the parent")

    def test_each_source_is_observed_on_its_own(self):
        """One arm per source, each starting from a probe that says nothing happened —
        without that negative half, a probe that always answered `worktree-write` would
        pass all three."""
        fx = Fixture(state_doc(findings=[finding_record()], queue=[queue_pass("P-01")]))
        self.addCleanup(shutil.rmtree, fx.root, True)
        td = _repo(self)
        from xcheck.courier import dirty_paths
        base = dirty_paths(td)
        # OUTSIDE the worktree, as in production: the parent owns the sidecar directory
        # and it is beside the logs, not in the tree the child can see. A sidecar written
        # INSIDE the worktree would dirty it, and every sidecar arm below would then be
        # answered by `worktree-write` — the arms would pass while proving nothing.
        side = Path(tempfile.mkdtemp(prefix="xcheck-sidecar-")) / "usage.json"
        self.addCleanup(shutil.rmtree, side.parent, True)

        sid = "a" * 16
        self.assertIsNone(runner.activity_probe(fx.root, sid, td, base, sidecar=side),
                          "the probe reported activity before anything happened")

        # 1. a canonical transition
        envelope.emit(fx.root, "state_transition", sid, verb="file-finding",
                      target="F-0002", from_status=None, to_status="reported")
        self.assertEqual("canonical-transition",
                         runner.activity_probe(fx.root, sid, td, base, sidecar=side))

        # 2. a validated sidecar, for a session that moved nothing
        quiet = "b" * 16
        self.assertIsNone(runner.activity_probe(fx.root, quiet, td, base, sidecar=side))
        side.write_text(json.dumps({"session_id": quiet, "total_tokens": 10}),
                        encoding="utf-8")
        self.assertEqual("telemetry-sidecar",
                         runner.activity_probe(fx.root, quiet, td, base, sidecar=side))
        # ... and a sidecar bound to somebody ELSE is not this session's activity.
        side.write_text(json.dumps({"session_id": "c" * 16, "total_tokens": 10}),
                        encoding="utf-8")
        self.assertIsNone(runner.activity_probe(fx.root, quiet, td, base, sidecar=side))

        # 3. a write inside the session's own worktree
        (td / "the-agent-wrote-this.txt").write_text("x", encoding="utf-8")
        self.assertEqual("worktree-write",
                         runner.activity_probe(fx.root, quiet, td, base, sidecar=side))
        print("  PROBE      transition / sidecar / worktree each observed alone, and a "
              "sidecar bound to another session is not activity")

    def test_a_broken_probe_reports_no_activity_rather_than_raising(self):
        """It is called from the wait loop. A probe that raised would kill a working
        session on a transient git error — the one failure this must not have."""
        self.assertIsNone(runner.activity_probe(
            "/no/such/project", "d" * 16, "/no/such/workdir", None, sidecar="/no/such"))


class APaddingChildIsCaughtByTheActivityBound(unittest.TestCase):
    """The audit's exact scenario. `output_deadline` is a hang detector and this child
    is not hanging — it is talking. Only the activity bound can tell the difference."""

    def child(self, script, workdir, project, sid="a" * 16, **over):
        from xcheck.courier import dirty_paths
        log = Path(tempfile.mkdtemp(prefix="xcheck-activity-log-")) / "s.log"
        self.addCleanup(shutil.rmtree, log.parent, True)
        timings = {}
        base = dirty_paths(workdir)
        rc, outcome, elapsed = runner.run_child(
            ["sh", "-c", script], workdir, log, {}, conf(**over), timings=timings,
            activity=lambda: runner.activity_probe(project, sid, workdir, base))
        return rc, outcome, elapsed, timings, log

    def setUp(self):
        self.fx = Fixture(state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, self.fx.root, True)
        self.td = _repo(self)

    def test_a_padding_child_clears_the_output_deadline_and_dies_at_the_activity_one(self):
        # 3,000 bytes in one line: past BANNER_ALLOWANCE (2048) plus a zero-byte prompt,
        # so the output bound is satisfied by construction and cannot be what kills it.
        rc, outcome, elapsed, t, log = self.child(
            "python3 -c \"print('x'*3000, flush=True)\"; sleep 60", self.td, self.fx.root,
            startup_deadline=0, output_deadline=4, activity_deadline=4, idle_deadline=0,
            session_timeout=60)
        print(f"\n  PADDING    outcome={outcome} after {elapsed:.1f}s  "
              f"bytes={t['bytes']} (allowance {runner.BANNER_ALLOWANCE})")
        print(f"             killed by: {t.get('stalled_by')} — {t.get('stall_note')}")
        self.assertGreater(t["bytes"], runner.BANNER_ALLOWANCE,
                           "the child did not out-print the output allowance, so this "
                           "test is not the audit's scenario")
        self.assertEqual("stalled", outcome)
        self.assertEqual("activity", t["stalled_by"],
                         "the OUTPUT bound killed it — then this test proves nothing "
                         "about the activity bound")
        self.assertIn("Printing is not working", t["stall_note"])
        self.assertLess(elapsed, 25)

    def test_a_keep_alive_child_dies_at_the_activity_deadline(self):
        """Periodic output, no work: the state the audit says is maintainable
        indefinitely today. The idle bound is ON here and must NOT be what fires —
        the child is never silent for long enough."""
        rc, outcome, elapsed, t, log = self.child(
            "python3 -c \"import sys,time\n"
            "for i in range(60):\n"
            "    sys.stdout.write('still here ' + 'y'*200 + chr(10)); "
            "sys.stdout.flush(); time.sleep(0.5)\"",
            self.td, self.fx.root,
            startup_deadline=0, output_deadline=0, activity_deadline=5, idle_deadline=4,
            session_timeout=60)
        print(f"  KEEP-ALIVE outcome={outcome} after {elapsed:.1f}s  "
              f"lines={t['lines']}  longest gap={t['idle_s']}s")
        print(f"             killed by: {t.get('stalled_by')} — {t.get('stall_note')}")
        self.assertEqual("stalled", outcome)
        self.assertEqual("activity", t["stalled_by"])
        self.assertLess(t["idle_s"], 4, "the child went quiet, so the IDLE bound could "
                                        "have killed it and this proves nothing")
        self.assertGreater(t["lines"], 5, "the child barely spoke — a keep-alive that "
                                          "does not keep alive is not the scenario")

    def test_control_a_working_child_survives_and_its_artifact_exists(self):
        """Without this, every assertion above is satisfied by a watchdog that kills
        everything. And 'was not killed' is not 'worked': the file it wrote is asserted,
        so a child that slept through both bounds cannot pass as one that worked."""
        made = self.td / "the-agent-wrote-this.txt"
        rc, outcome, elapsed, t, log = self.child(
            f"echo start; sleep 3; echo done > {made}; sleep 4; echo finished",
            self.td, self.fx.root,
            startup_deadline=3, output_deadline=0, activity_deadline=6, idle_deadline=6,
            session_timeout=60)
        print(f"  CONTROL    outcome={outcome} rc={rc} after {elapsed:.1f}s  "
              f"first_activity_s={t.get('first_activity_s')} "
              f"via {t.get('activity_source')}")
        print(f"             artifact {made.name} exists: {made.exists()}")
        self.assertEqual("ok", outcome, "a working session was killed")
        self.assertEqual(0, rc)
        self.assertIsNone(t.get("stalled_by"))
        self.assertTrue(made.is_file(),
                        "the control was not killed, but it also never worked — the "
                        "test would pass for a child that only slept")
        self.assertEqual("worktree-write", t.get("activity_source"))
        self.assertIsNotNone(t.get("first_activity_s"))


class TheRecordCarriesWhichBoundFiredAndWhenWorkBegan(unittest.TestCase):

    def test_first_activity_s_is_a_recorded_field_in_both_closed_schemas(self):
        state_mod = xcheck_submodule("state")
        # SESSION_FIELDS is (required, optional): the field is OPTIONAL, because every
        # session recorded before this phase has none and a required field would make
        # the existing corpus unloadable.
        required, optional = state_mod.SESSION_FIELDS
        self.assertNotIn("first_activity_s", required)
        self.assertIn("first_activity_s", optional)
        self.assertIn("first_activity_s", envelope.TELEMETRY_FIELDS)

    def test_a_stalled_session_is_still_never_retried_whichever_bound_fired(self):
        for bound in ("startup", "output", "activity", "idle"):
            retry, why = runner.retry_decision("stalled", attempt=0, limit=5)
            self.assertFalse(retry, bound)
        self.assertIn("stalled", util.OUTCOMES)

    def test_the_note_names_the_activity_bound(self):
        td = tempfile.mkdtemp(prefix="xcheck-watchdog-")
        self.addCleanup(shutil.rmtree, td, True)
        log = Path(td) / "s.log"
        log.write_text("banner\n", encoding="utf-8")
        profile = runner.resolve_profile(conf(), "Auditor")
        rec = envelope.dispatch_record(["sh"], "Auditor", "c", "p", "a" * 16, profile, 1,
                                       head_before="b" * 40)
        rec = envelope.finish(rec, -15, "stalled", 1200.0, "c" * 40, log,
                              note="killed at the activity deadline (1200s): 41209 "
                                   "bytes written and nothing done",
                              first_activity_s=None)
        print(f"  RECORD     note={rec['note'][:64]}…  "
              f"first_activity_s={rec.get('first_activity_s')!r}")
        self.assertIn("activity", rec["note"])
        # Null, not 0: the session never acted, and 0 would say it acted instantly.
        self.assertIsNone(rec.get("first_activity_s"))


class NoSurfaceStillCallsByteVolumeAUsefulAction(unittest.TestCase):
    """Criterion 1. The rename is only done when nothing still makes the old promise."""

    SURFACES = ("README.md", "SECURITY.md", "docs/operator.conf.example",
                "xcheck/util.py", "xcheck/runner.py", "xcheck/policy.py")

    def test_the_old_name_is_gone_from_every_vocabulary_that_accepts_settings(self):
        """The rename, asked structurally rather than by hunting a string: the old
        spelling APPEARS on purpose — in the rename table and in the comments that send
        a reader to the new name — and a check that banned the characters would force
        the deletion of the one thing that explains the change."""
        for vocab, name in ((policy.POLICY_KEYS, "policy.POLICY_KEYS"),
                            (util.CONF_DEFAULTS, "util.CONF_DEFAULTS"),
                            (util.NUMERIC_CONF, "util.NUMERIC_CONF")):
            self.assertNotIn("first_action_deadline", vocab, name)
            self.assertIn("output_deadline", vocab, name)
            self.assertIn("activity_deadline", vocab, name)
        self.assertEqual("unknown", policy.classify("first_action_deadline"),
                         "the old key still classifies as something the tool accepts")
        print("\n  RENAME     the old key is in no vocabulary; "
              f"policy.classify -> {policy.classify('first_action_deadline')!r}")

    def test_no_shipped_surface_still_sets_the_old_key_or_promises_usefulness(self):
        from tests.harness import REPO
        bad = []
        for name in self.SURFACES:
            text = Path(REPO, name).read_text(encoding="utf-8")
            for n, line in enumerate(text.splitlines(), 1):
                stripped, low = line.strip(), line.lower()
                # A SETTING line, in a profile or a table row an operator copies from.
                if stripped.startswith("first_action_deadline=") or (
                        stripped.startswith("|") and "first_action_deadline" in low):
                    bad.append(f"{name}:{n}: {stripped[:70]}")
                # And the promise itself, wherever it is still MADE about a byte count.
                # A line marked HISTORICAL: quotes the old promise in order to repudiate
                # it, and deleting those would delete the only explanation of the rename
                # — so the marker is the exception, and it has to be written on purpose.
                if "historical:" in low:
                    continue
                if "useful action" in low or "nothing useful" in low:
                    bad.append(f"{name}:{n}: {stripped[:70]}")
        print("  RENAME     surfaces checked: " + ", ".join(self.SURFACES))
        self.assertEqual([], bad)

    def test_the_old_key_is_refused_by_name_rather_than_silently_aliased(self):
        td = Path(tempfile.mkdtemp(prefix="xcheck-rename-"))
        self.addCleanup(shutil.rmtree, td, True)
        prof = td / "operator.conf"
        prof.write_text("auditor_cmd=/bin/true {prompt}\nfirst_action_deadline=600\n",
                        encoding="utf-8")
        with self.assertRaises(policy.PolicyError) as e:
            policy.load(prof, td / "subject")
        print(f"  REFUSED    {str(e.exception)[:150]}…")
        self.assertIn("output_deadline", str(e.exception))
        self.assertIn("first_action_deadline", str(e.exception))
        # An alias would leave two names for one setting, and the duplicate-key refusal
        # cannot see `output_deadline=600` beside `first_action_deadline=0` as the
        # contradiction it is. CONTROL: the new spelling loads.
        prof.write_text("auditor_cmd=/bin/true {prompt}\noutput_deadline=600\n",
                        encoding="utf-8")
        self.assertEqual("600", policy.load(prof, td / "subject")["output_deadline"])


if __name__ == "__main__":
    unittest.main()
