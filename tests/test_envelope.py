"""Phase 9: the invocation envelope, the event stream, the JSON contract, and
independence as a MEASUREMENT.

The property under test in this file is not "a function returns a level" — it is
that a claim about a session can be CHECKED against a record the agent could not
have written. So the dispatch tests run a real child through `run_session` with a
synthetic agent command, and the provenance tests go through the real write verbs
rather than calling the mutators directly: a rule enforced only where nobody enters
is not enforced.
"""

import contextlib
import hashlib
import io
import json
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path

from tests.harness import (needs_live_corpus, REPO, Fixture, finding_record, queue_pass, state_doc,
                           xcheck_submodule)

envelope = xcheck_submodule("envelope")
runner = xcheck_submodule("runner")
state = xcheck_submodule("state")
util = xcheck_submodule("util")
cli = xcheck_submodule("cli")
write = xcheck_submodule("write")

GIT = shutil.which("git")


def git(cwd, *args):
    return subprocess.run([GIT] + list(args), cwd=str(cwd), capture_output=True,
                          text=True, timeout=60)


def conf(**over):
    # PHASE 3: `trust_level` has no default in `CONF_DEFAULTS` and must not get one —
    # the refusal exists because the cheap assumption is `trusted`. A fixture project
    # is synthetic and trusted BY CONSTRUCTION, so it makes the operator's declaration
    # explicitly. `tests/test_trust_level.py` is where the unset case is exercised.
    raw = dict(util.CONF_DEFAULTS)
    raw.setdefault("trust_level", "trusted")
    raw.update({k: str(v) for k, v in over.items()})
    return util.Conf(raw)


def dispatchable(doc=None, **conf_over):
    """A fixture that `run_session` can actually dispatch into: a git repo with a
    valid state document and a synthetic agent command."""
    fx = Fixture(doc=doc or state_doc(findings=[finding_record()]))
    git(fx.root, "init", "-q")
    git(fx.root, "config", "user.email", "t@example.invalid")
    git(fx.root, "config", "user.name", "test")
    git(fx.root, "add", "-A")
    git(fx.root, "commit", "-qm", "base")
    c = conf(sandbox_profile="none", **conf_over)
    return fx, c


class TheEnvelopeRecordsWhatActuallyRan(unittest.TestCase):
    """Criterion 1: all thirteen fields populated for a dispatched session, with
    `unknown` shown where a value is genuinely undeterminable."""

    def setUp(self):
        self.fx, self.conf = dispatchable(
            remediator_cmd="sh -c 'echo hello' --model synthetic-1 {prompt}")
        self.addCleanup(shutil.rmtree, self.fx.root, ignore_errors=True)

    def stored(self):
        return list(state.load_state(self.fx.audit).sessions)

    def test_all_thirteen_fields_are_populated_after_a_session(self):
        runner.run_session(self.fx.root, self.conf, "Remediator", "charter")
        recs = self.stored()
        self.assertEqual(len(recs), 1, "exactly one dispatch should be recorded")
        rec = recs[0]
        missing = [label for label, keys in envelope.ENVELOPE_FIELDS
                   if any(rec.get(k) is None for k in keys)]
        self.assertEqual(missing, [], f"unpopulated envelope fields: {missing}\n"
                                      f"{envelope.describe(rec)}")

    def test_an_undeterminable_field_is_recorded_as_unknown_never_guessed(self):
        # `sh` is in no provider table and the command names no model. The record must
        # SAY so rather than omitting the field or inventing a plausible value.
        runner.run_session(self.fx.root, conf(sandbox_profile="none",
                                              remediator_cmd="sh -c 'exit 0' {prompt}"),
                           "Remediator", "charter")
        rec = self.stored()[0]
        self.assertEqual(rec["provider"], "unknown")
        self.assertEqual(rec["agent_model"], "unknown")
        self.assertIn("provider", rec, "an undeterminable field is recorded, not dropped")

    def test_hashes_are_over_the_exact_bytes_dispatched(self):
        runner.run_session(self.fx.root, self.conf, "Remediator", "the exact charter")
        rec = self.stored()[0]
        self.assertEqual(rec["charter_hash"], envelope.sha256_text("the exact charter"))
        # The prompt hash must cover the prompt the CHILD got — including the
        # orchestration-context line — not the role template it was built from.
        self.assertNotEqual(rec["prompt_hash"],
                            envelope.sha256_text(runner.PROMPTS["Remediator"]))
        self.assertRegex(rec["prompt_hash"], r"^[0-9a-f]{64}$")

    def test_log_digest_is_of_the_redacted_log_as_written(self):
        runner.run_session(self.fx.root, self.conf, "Remediator", "charter")
        rec = self.stored()[0]
        # PHASE 14: raw logs live outside the working tree now, at the resolved log
        # root. The digest is unchanged and is still what this test is about.
        from xcheck import retention
        logs = sorted(retention.log_root(self.fx.root, create=False).glob("*.log"))
        self.assertTrue(logs, "no log was written")
        on_disk = hashlib.sha256(logs[-1].read_bytes()).hexdigest()
        self.assertEqual(rec["log_digest"], on_disk,
                         "the digest must be of the file as written (already redacted), "
                         "so it can be checked by anyone holding the log")

    def test_the_envelope_is_the_orchestrators_testimony_not_the_agents(self):
        # There is no write verb that reaches sessions[] — that is what makes the
        # record evidence rather than a claim.
        self.assertNotIn("sessions", " ".join(write.VERBS))
        for name in write.VERBS:
            fn = write._VERB_FN.get(name)
            if fn is not None:
                self.assertNotIn("sessions", (fn.__code__.co_names or ()),
                                 f"verb {name} touches sessions[]")


class TheEventStreamIsAppendOnly(unittest.TestCase):
    """Criterion 2: across three consecutive sessions, every previously written line
    is byte-identical — proved by comparing a sha256 of the file PREFIX, which is the
    only comparison a rewrite cannot survive."""

    def test_three_sessions_never_rewrite_an_earlier_line(self):
        fx, c = dispatchable(remediator_cmd="sh -c 'exit 0' {prompt}")
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        path = fx.audit / envelope.EVENTS_FILENAME
        prefixes = []
        for _ in range(3):
            before = path.read_bytes() if path.exists() else b""
            runner.run_session(fx.root, c, "Remediator", "charter")
            after = path.read_bytes()
            self.assertTrue(after.startswith(before),
                            "an earlier byte changed — the stream is not append-only")
            self.assertEqual(hashlib.sha256(after[:len(before)]).hexdigest(),
                             hashlib.sha256(before).hexdigest())
            prefixes.append((len(before), len(after)))
        self.assertTrue(all(a < b for a, b in prefixes), "no session appended anything")
        lines = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
        self.assertTrue(all({"ts", "event"} <= set(e) for e in lines))
        kinds = {e["event"] for e in lines}
        self.assertLessEqual({"session_dispatched", "session_finished",
                              "lease_acquired", "lease_released"}, kinds)

    def test_a_write_verb_emits_one_transition_event_and_a_refusal_emits_none(self):
        fx = Fixture(doc=state_doc(findings=[finding_record(status="accepted")]))
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        write.run(fx.root, "set-status", ["F-0001", "validated"])
        events = envelope.read_events(fx.root)
        transitions = [e for e in events if e["event"] == "state_transition"]
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0]["verb"], "set-status")
        self.assertEqual(transitions[0]["target"], "F-0001")
        with self.assertRaises(state.StateError):
            write.run(fx.root, "set-status", ["F-0001", "validated"])   # a repeat
        self.assertEqual(
            len([e for e in envelope.read_events(fx.root)
                 if e["event"] == "state_transition"]), 1,
            "a refusal changed no state and must not appear as a transition")


class TheJsonOutputIsAContract(unittest.TestCase):
    """Criterion 3: both payloads validate against the declared versioned schema, and
    an unknown key FAILS — the check that keeps the JSON from drifting the way the
    Markdown did."""

    def setUp(self):
        self.fx = Fixture(doc=state_doc(findings=[finding_record()]))
        self.addCleanup(shutil.rmtree, self.fx.root, ignore_errors=True)

    def payloads(self):
        st = state.load_state(self.fx.audit)
        status = cli.status_payload(self.fx.root, st, ["P-01"], [], "stop-triage",
                                    ["F-0001"], None)
        return status

    def test_status_payload_validates(self):
        self.assertEqual(util.json_output_issues(self.payloads(), util.STATUS_SCHEMA), [])

    def test_an_unknown_key_fails_validation(self):
        bad = dict(self.payloads(), surprise=1)
        issues = util.json_output_issues(bad, util.STATUS_SCHEMA, "status")
        self.assertTrue(any("surprise" in i and "unknown key" in i for i in issues),
                        f"an undeclared key must fail, got {issues}")

    def test_a_missing_key_fails_validation(self):
        bad = dict(self.payloads())
        del bad["state_revision"]
        issues = util.json_output_issues(bad, util.STATUS_SCHEMA, "status")
        self.assertTrue(any("state_revision" in i and "missing" in i for i in issues))

    def test_a_wrong_type_fails_validation(self):
        bad = dict(self.payloads(), state_revision="7")
        issues = util.json_output_issues(bad, util.STATUS_SCHEMA, "status")
        self.assertTrue(any("expected int" in i for i in issues), issues)

    def test_both_commands_print_valid_json_and_keep_the_version_in_it(self):
        for cmd, schema in (("status", util.STATUS_SCHEMA),
                            ("metrics", util.METRICS_SCHEMA)):
            out = subprocess.run(
                [str(Path(__file__).resolve().parent.parent / "bin" / "xcheck"),
                 "--project", str(self.fx.root), cmd, "--json"],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr)
            payload = json.loads(out.stdout)
            self.assertEqual(payload["output_schema_version"], util.OUTPUT_SCHEMA_VERSION)
            self.assertEqual(util.json_output_issues(payload, schema, cmd), [])

    def test_json_is_a_second_surface_not_a_replacement(self):
        # The human output must be unchanged by the flag's existence.
        exe = str(Path(__file__).resolve().parent.parent / "bin" / "xcheck")
        human = subprocess.run([exe, "--project", str(self.fx.root), "status"],
                               capture_output=True, text=True, timeout=120)
        self.assertIn("project:", human.stdout)
        self.assertNotIn("output_schema_version", human.stdout)


class IndependenceIsMeasuredNotAsserted(unittest.TestCase):
    """Criterion 4: all four typed levels, each from the envelope pair that produces
    it, and a degraded level LABELLED wherever it is shown."""

    @staticmethod
    def env(sid, provider, model):
        return {"session_id": sid, "provider": provider, "agent_model": model}

    def test_the_four_levels_come_from_the_envelope_fields(self):
        a, b = "a" * 16, "b" * 16
        cases = {
            "cross-provider": (self.env(a, "anthropic", "opus"),
                               self.env(b, "openai", "o3")),
            "cross-model": (self.env(a, "anthropic", "opus"),
                            self.env(b, "anthropic", "haiku")),
            "same-provider-different-session": (self.env(a, "anthropic", "opus"),
                                                self.env(b, "anthropic", "opus")),
            "same": (self.env(a, "anthropic", "opus"), self.env(a, "anthropic", "opus")),
        }
        for want, (fixer, verifier) in cases.items():
            self.assertEqual(envelope.independence_level(fixer, verifier), want,
                             f"{fixer} vs {verifier}")

    def test_a_missing_envelope_is_unrecorded_not_optimistic(self):
        self.assertEqual(
            envelope.independence_level(None, self.env("b" * 16, "openai", "o3")),
            "unrecorded")

    def test_an_unknown_provider_never_reads_as_cross_provider(self):
        # Two `unknown`s are not evidence of difference. Guessing here would be the
        # whole defect: a level that overstates what it measured.
        lvl = envelope.independence_level(self.env("a" * 16, "unknown", "unknown"),
                                          self.env("b" * 16, "unknown", "unknown"))
        self.assertEqual(lvl, "same-provider-different-session")

    def test_every_degraded_level_carries_a_label(self):
        for lvl in util.DEGRADED_INDEPENDENCE:
            self.assertIn("DEGRADED", envelope.independence_note(lvl))

    def test_metrics_surfaces_the_level_and_the_degraded_marker(self):
        rec = finding_record(status="closed", fixed_by="a" * 16,
                             independence="same-provider-different-session")
        fx = Fixture(doc=state_doc(findings=[rec], queue=[queue_pass(done=True)]))
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        exe = str(Path(__file__).resolve().parent.parent / "bin" / "xcheck")
        out = subprocess.run([exe, "--project", str(fx.root), "metrics"],
                             capture_output=True, text=True, timeout=120)
        self.assertIn("verdict independence", out.stdout)
        self.assertIn("degraded", out.stdout)
        self.assertIn("ceiling", out.stdout)
        js = json.loads(subprocess.run([exe, "--project", str(fx.root), "metrics",
                                        "--json"], capture_output=True, text=True,
                                       timeout=120).stdout)
        self.assertEqual(js["independence"]["degraded"], 1)
        self.assertEqual(js["independence"]["by_level"],
                         {"same-provider-different-session": 1})


class ProvenanceComesFromTheDispatchRecord(unittest.TestCase):
    """Criterion 5, and the structural closure of F-0159: `fixed-by` is READ from the
    dispatch the orchestrator performed. An agent that types a different session id is
    refused, because a provenance field the subject can choose is not provenance."""

    def open_dispatch_doc(self, sid, **over):
        rec = envelope.dispatch_record(
            ["claude", "--model", "opus"], "Remediator", "c", "p", sid,
            runner.PROFILES["none"], 1, "0" * 40, env={})
        rec.update(over)
        return rec

    def test_a_disagreeing_session_is_refused_with_an_addressed_message(self):
        real, claimed = "a" * 16, "b" * 16
        doc = state_doc(findings=[finding_record(status="planned")])
        doc["sessions"] = [self.open_dispatch_doc(real)]
        fx = Fixture(doc=doc)
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        with self.assertRaises(state.StateError) as cm:
            write.run(fx.root, "record-fix", ["F-0001", "--session", claimed])
        msg = str(cm.exception)
        for must in (claimed, real, "Remediator", "dispatch record", "--session"):
            self.assertIn(must, msg, f"the refusal must name {must!r}:\n{msg}")

    def test_the_agreeing_session_is_accepted_and_recorded(self):
        real = "a" * 16
        doc = state_doc(findings=[finding_record(status="planned")])
        doc["sessions"] = [self.open_dispatch_doc(real)]
        fx = Fixture(doc=doc)
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        self.assertEqual(write.run(fx.root, "record-fix", ["F-0001", "--session", real]), 0)
        self.assertEqual(state.load_state(fx.audit).finding("F-0001").fixed_by, real)

    def test_a_project_with_no_envelopes_labels_the_claim_as_unbound(self):
        fx = Fixture(doc=state_doc(findings=[finding_record(status="planned")]))
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            write.run(fx.root, "record-fix", ["F-0001", "--session", "c" * 16])
        self.assertIn("UNBOUND", buf.getvalue(),
                      "a weaker mode must be displayed as one, never passed off as the "
                      "orchestrator's testimony")

    def test_a_finalised_only_history_refuses_rather_than_believing_the_agent(self):
        doc = state_doc(findings=[finding_record(status="planned")])
        closed = self.open_dispatch_doc("a" * 16)
        closed.update(exit_status=0, outcome="ok", duration_s=1.0,
                      head_after="0" * 40, log_digest="unknown")
        doc["sessions"] = [closed]
        fx = Fixture(doc=doc)
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        with self.assertRaises(state.StateError) as cm:
            write.run(fx.root, "record-fix", ["F-0001", "--session", "b" * 16])
        self.assertIn("no dispatch is open", str(cm.exception))

    def test_the_verdict_records_the_measured_level(self):
        fixer, verifier = "a" * 16, "b" * 16
        doc = state_doc(findings=[finding_record(status="fixed", fixed_by=fixer)])
        f_env = self.open_dispatch_doc(fixer, exit_status=0, outcome="ok",
                                       duration_s=1.0, head_after="0" * 40,
                                       log_digest="unknown")
        f_env["provider"], f_env["agent_model"] = "anthropic", "opus"
        v_env = self.open_dispatch_doc(verifier)
        v_env["provider"], v_env["agent_model"] = "openai", "o3"
        v_env["role"] = "Verifier"
        doc["sessions"] = [f_env, v_env]
        fx = Fixture(doc=doc)
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        write.run(fx.root, "record-verdict", ["F-0001", "--verdict", "closed",
                                              "--session", verifier])
        self.assertEqual(state.load_state(fx.audit).finding("F-0001").independence,
                         "cross-provider")


class TheStateSchemaClosesOverTheEnvelope(unittest.TestCase):
    """The envelope is machine state, so it obeys the same closed-schema rule as every
    other record: unknown fails, missing fails, and two open dispatches are not a
    representable state."""

    def rec(self, **over):
        r = envelope.dispatch_record(["claude"], "Remediator", "c", "p", "a" * 16,
                                     runner.PROFILES["none"], 1, "0" * 40, env={})
        r.update(over)
        return r

    def test_an_unknown_field_is_refused(self):
        doc = state_doc()
        doc["sessions"] = [self.rec(souvenir="hi")]
        with self.assertRaises(state.StateError) as cm:
            state._validate(doc, "d")
        self.assertIn("souvenir", str(cm.exception))

    def test_two_open_dispatches_are_not_representable(self):
        doc = state_doc()
        doc["sessions"] = [self.rec(), self.rec(session_id="b" * 16)]
        with self.assertRaises(state.StateError) as cm:
            state._validate(doc, "d")
        self.assertIn("still open", str(cm.exception))

    def test_a_second_dispatch_abandons_the_one_nobody_finalised(self):
        # A killed orchestrator leaves an open envelope. The next dispatch must be able
        # to proceed — and must say what happened to the old one rather than inventing
        # an exit status nobody observed.
        doc = state_doc()
        doc["sessions"] = [self.rec()]
        fx = Fixture(doc=doc)
        self.addCleanup(shutil.rmtree, fx.root, ignore_errors=True)
        envelope.store(fx.root, self.rec(session_id="b" * 16), event="session_dispatched")
        sessions = state.load_state(fx.audit).sessions
        old = [s for s in sessions if s["session_id"] == "a" * 16][0]
        self.assertEqual(old["outcome"], "crash")
        self.assertIsNone(old["exit_status"],
                          "an exit status nobody observed is never invented")
        self.assertIn("never able to finalise", old["note"])

    def test_the_outcome_vocabulary_has_exactly_one_source(self):
        self.assertIs(runner.OUTCOMES, util.OUTCOMES)


if __name__ == "__main__":
    unittest.main()


ledger = xcheck_submodule("ledger")


@needs_live_corpus
class EventsFailClosed(unittest.TestCase):
    """PHASE 7 (fourth audit). `emit` caught `OSError`, printed a note and returned, so a
    canonical transition could land while its event was dropped. Every consumer above the
    stream — budgets, metrics, independence, receipts, the derived bundle — reads it as
    COMPLETE, and none of them can tell an undercount from a small number.

    The declared policy, which is what these arms check:

      RESERVE fails (the ledger cannot be appended to)  -> the transition is REFUSED and
                                                           canonical state is unchanged
      COMMIT fails (the disk filled mid-unit)           -> the work stands, the outbox
                                                           keeps the event, a later run
                                                           replays it
    """

    def setUp(self):
        self.fx = Fixture(state_doc(findings=[finding_record("F-0001")]))
        self.events = self.fx.audit / envelope.EVENTS_FILENAME
        self.outbox = self.fx.audit / ledger.OUTBOX_FILENAME

    def state_bytes(self):
        return (self.fx.audit / "state.json").read_bytes()

    def unwritable(self, path):
        """Make one path refuse an append, and restore it afterwards."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        mode = path.stat().st_mode
        path.chmod(0o400)
        self.addCleanup(path.chmod, mode)

    def test_CONTROL_the_ordinary_path_writes_its_event_exactly_once(self):
        before = len(envelope.read_events(self.fx.root))
        code, out = self.fx.run("set-status", "F-0001", "accepted")
        self.assertEqual(0, code, out)
        after = envelope.read_events(self.fx.root)
        transitions = [e for e in after if e.get("event") == "state_transition"]
        self.assertEqual(1, len(transitions),
                         "the ordinary path did not write its transition exactly once")
        self.assertFalse(self.outbox.exists(),
                         "the ordinary path left an outbox entry behind")
        print(f"\n  CONTROL    one verb -> {len(after) - before} event(s) "
              f"({[e['event'] for e in after[before:]]}), exactly one of them the "
              f"transition, outbox empty. Cost: one extra append + fsync per CANONICAL "
              f"TRANSITION only — ordinary events pay nothing on the happy path")

    def test_a_transition_is_REFUSED_when_its_event_cannot_be_made_durable(self):
        """The counterfactual that matters, asserted on the FILE. A return code says
        what the code believed; `state.json` says what happened."""
        self.fx.run("set-status", "F-0001", "accepted")
        before = self.state_bytes()
        revision_before = json.loads(before)["state_revision"]

        self.unwritable(self.events)
        code, out = self.fx.run("set-status", "F-0001", "disputed")
        self.assertNotEqual(0, code, "the transition was not refused")
        self.assertIn("could not be made durable", out)
        self.assertIn("Canonical state was NOT changed", out)
        refusal = next(ln for ln in out.splitlines()
                       if "could not be made durable" in ln)
        print(f"  REFUSED    {refusal[:150]}")
        print(f"             {[ln for ln in out.splitlines() if 'NOT changed' in ln][0][:150]}")

        self.assertEqual(before, self.state_bytes(),
                         "state.json changed even though the event was refused")
        self.assertEqual(revision_before,
                         json.loads(self.state_bytes())["state_revision"])
        record = next(r for r in json.loads(self.state_bytes())["findings"]
                      if r["id"] == "F-0001")
        self.assertEqual("accepted", record["status"],
                         "the finding moved despite the refusal")
        print(f"  NOT APPLIED state revision still {revision_before}, F-0001 still "
              f"`{record['status']}`")

    def test_an_ordinary_emit_that_cannot_append_leaves_a_durable_outbox_entry(self):
        """`emit` still never raises — the old reasoning is kept — but it is no longer a
        silent success either."""
        self.unwritable(self.events)
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            ok = envelope.emit(self.fx.root, "lease_acquired", "c" * 16, role="Auditor")
        self.assertFalse(ok)
        self.assertIn("will replay it", buf.getvalue())
        entries = ledger.pending(self.fx.root)
        self.assertEqual(1, len(entries))
        self.assertEqual("lease_acquired", entries[0]["line"]["event"])
        print(f"  OUTBOX     {json.dumps(entries[0])[:150]}")

    def test_replaying_twice_produces_one_event_not_two(self):
        """A recovery path that duplicates evidence is a new corruption."""
        self.unwritable(self.events)
        with contextlib.redirect_stdout(io.StringIO()):
            envelope.emit(self.fx.root, "lease_acquired", "c" * 16, role="Auditor")
        self.events.chmod(0o644)

        before = len(envelope.read_events(self.fx.root))
        appended, already, discarded = ledger.replay(self.fx.root)
        self.assertEqual((1, 0, []), (appended, already, discarded))
        once = envelope.read_events(self.fx.root)
        self.assertEqual(before + 1, len(once))

        # Replay again over the same outbox content, restored, to prove the second pass
        # is a no-op because of the KEY and not because the file was emptied.
        entry = {"event_id": ledger.event_id(once[-1]), "line": once[-1]}
        self.outbox.write_text(json.dumps(entry, sort_keys=True,
                                          separators=(",", ":")) + "\n", encoding="utf-8")
        appended2, already2, _ = ledger.replay(self.fx.root)
        twice = envelope.read_events(self.fx.root)
        self.assertEqual((0, 1), (appended2, already2))
        self.assertEqual(len(once), len(twice),
                         "replaying the same entry twice appended it twice")
        print(f"  IDEMPOTENT replay #1 appended {appended}, replay #2 appended "
              f"{appended2} (already present: {already2}); stream length "
              f"{before} -> {len(once)} -> {len(twice)}")

    def test_the_outbox_survives_a_hard_kill_of_the_process_that_wrote_it(self):
        """SIGKILL, not a mock of one. A durability claim tested by simulating the crash
        tests the simulation."""
        script = (
            "import os, signal, sys\n"
            f"sys.path.insert(0, {str(REPO)!r})\n"
            "from xcheck import ledger\n"
            f"ledger.reserve({str(self.fx.root)!r}, "
            "{'event': 'state_transition', 'ts': '2026-09-04T00:00:00+00:00',"
            " 'session_id': None, 'target': 'F-0001'})\n"
            "os.kill(os.getpid(), signal.SIGKILL)\n"
            "print('THIS LINE MUST NOT BE REACHED')\n")
        p = subprocess.run([sys.executable, "-c", script], capture_output=True,
                           text=True, timeout=120)
        self.assertEqual(-9, p.returncode,
                         f"the child was not SIGKILLed (rc={p.returncode}) — the "
                         f"durability claim was not tested: {p.stdout}{p.stderr}")
        self.assertNotIn("MUST NOT BE REACHED", p.stdout)

        entries = ledger.pending(self.fx.root)
        self.assertEqual(1, len(entries), "the outbox entry did not survive the kill")
        self.assertEqual("F-0001", entries[0]["line"]["target"])
        print(f"  SIGKILL    child rc={p.returncode}; outbox holds "
              f"{entries[0]['event_id']} after the kill")

        appended, _already, _discarded = ledger.replay(self.fx.root)
        self.assertEqual(1, appended, "the surviving entry was not replayable")
        self.assertEqual("F-0001", envelope.read_events(self.fx.root)[-1]["target"])

    def test_a_write_verb_replays_a_stranded_entry_on_its_way_past(self):
        """The heal happens on the next verb, under the lock, not on a command somebody
        has to know about."""
        self.unwritable(self.events)
        with contextlib.redirect_stdout(io.StringIO()):
            envelope.emit(self.fx.root, "lease_acquired", "c" * 16, role="Auditor")
        self.events.chmod(0o644)
        self.assertEqual(1, len(ledger.pending(self.fx.root)))

        code, out = self.fx.run("set-status", "F-0001", "accepted")
        self.assertEqual(0, code, out)
        self.assertIn("replayed 1 event(s)", out)
        self.assertEqual([], ledger.pending(self.fx.root))
        stream = envelope.read_events(self.fx.root)
        stranded = [n for n, e in enumerate(stream)
                    if e.get("session_id") == "c" * 16]
        transition = [n for n, e in enumerate(stream)
                      if e.get("event") == "state_transition"]
        self.assertEqual(1, len(stranded), "the stranded event did not land")
        self.assertLess(stranded[0], transition[0],
                        "the stranded event landed after the new transition")
        print(f"  HEALED     {out.strip().splitlines()[0][:140]}")

    def test_the_full_disk_trade_off_is_answered_at_the_changed_line(self):
        """The original reasoning — do not lose a durable outcome to a full disk — is a
        real constraint, and a behaviour change that silently deletes a recorded
        trade-off is how the trade-off comes back. It has to be answered where it was
        made, not only in a commit message."""
        for path, needles in (
                (REPO / "xcheck" / "ledger.py",
                 ("full disk", "trades a durable outcome for a log line",
                  "RESERVE", "COMMIT", "replay")),
                (REPO / "xcheck" / "envelope.py",
                 ("full disk", "outbox", "durable outcome"))):
            text = path.read_text(encoding="utf-8")
            for needle in needles:
                self.assertIn(needle, text, f"{path.name} does not answer {needle!r}")
        print("  TRADE-OFF  answered in ledger.py and at the changed line in envelope.py")

    def test_the_real_event_stream_is_untouched(self):
        """Nothing in this phase rewrites already-admitted evidence."""
        events = envelope.read_events(REPO)
        self.assertGreater(len(events), 1000)
        self.assertFalse((REPO / "audit" / ledger.OUTBOX_FILENAME).exists(),
                         "this repository has a stranded outbox entry")
        print(f"  UNTOUCHED  {len(events)} real events, no outbox in this checkout")


@needs_live_corpus
class TheStreamIsAChain(unittest.TestCase):
    """PHASE 8 (fourth audit). Checksums at export prove a copy did not change after it
    was copied. They say nothing about whether the source was complete or historically
    unmodified — which is exactly what budgets, metrics, independence, receipts and the
    derived bundle all assume.

    Two halves, and both are needed. The CHAIN makes every event depend on the whole
    prefix before it, so nothing can be inserted, removed or edited without a later link
    disagreeing. The ANCHOR in canonical state is what the chain cannot do for itself: a
    hash chain is rebuildable by anyone who can edit the file, and the digest recorded
    under the writing lock is not in the file being checked."""

    def setUp(self):
        self.fx = Fixture(state_doc(findings=[finding_record("F-0001")]))

    def chained_stream(self, extra=3):
        """A copy of THIS repository's real stream, with chained events appended.

        The real stream is entirely pre-migration, so a copy of it alone has no links to
        break. Appending real chained events is what makes the copy a post-migration
        stream — and it is also the arm that proves the legacy prefix is FIXED by the
        chain rather than merely preceding it."""
        root = Path(self.fx.root)
        shutil.copy(REPO / "audit" / envelope.EVENTS_FILENAME,
                    root / "audit" / envelope.EVENTS_FILENAME)
        for i in range(extra):
            ledger.append_chained(root, envelope.event_line(
                "gate_reached", None, gate=f"fixture-{i}"))
        return root / "audit" / envelope.EVENTS_FILENAME

    # ---- the real stream ----------------------------------------------------------

    def test_the_real_stream_validates_and_the_cost_is_measured(self):
        """CONTROL, and the number that decides whether anyone leaves this on."""
        start = time.perf_counter()
        lines = ledger.validate(REPO)
        elapsed = time.perf_counter() - start
        legacy, chain = ledger.split_at_migration(lines)
        print(f"\n  REAL CHAIN {len(lines)} events validated in {elapsed * 1000:.1f} ms "
              f"({len(legacy)} pre-migration, {len(chain)} chained)")
        print(f"  PREFIX     the {len(legacy)}-event prefix hashes to "
              f"{ledger.prefix_digest(legacy)}")
        self.assertGreater(len(lines), 1000, "this checkout has no stream to measure")
        self.assertLess(elapsed, 2.0, "a validating reader nobody can afford gets "
                                      "turned off")

    def test_history_is_not_rewritten(self):
        """Every historical event stays byte-identical. The chain starts AFTER them and
        fixes them by naming their digest, which is the only way to add integrity to
        already-admitted evidence without inventing a version of it."""
        raw = (REPO / "audit" / envelope.EVENTS_FILENAME).read_text(encoding="utf-8")
        rows = [json.loads(ln) for ln in raw.splitlines() if ln.strip()]
        carrying = [r for r in rows if ledger.SCHEMA_FIELD in r or ledger.PREV_FIELD in r]
        self.assertEqual([], carrying,
                         "a historical event grew a chain field — history was rewritten")
        print(f"  UNTOUCHED  {len(rows)} historical events carry no `v` and no `prev`")

    def test_the_migration_point_is_declared_once_by_the_data(self):
        """The first line carrying a schema version, and nothing else. Not a date, not a
        count, not a constant somebody has to keep current."""
        path = self.chained_stream()
        lines = ledger.validate(self.fx.root)
        legacy, chain = ledger.split_at_migration(lines)
        self.assertEqual(3, len(chain))
        self.assertEqual(ledger.prefix_digest(legacy), chain[0][ledger.PREV_FIELD],
                         "the first chained event does not name the legacy prefix")
        print(f"  MIGRATION  line {len(legacy) + 1} is the first with `v`; its `prev` is "
              f"the digest of the {len(legacy)} events before it")
        self.assertTrue(path.exists())

    # ---- counterfactuals -----------------------------------------------------------

    def test_an_edit_in_the_middle_of_the_chain_is_detected_at_the_edit(self):
        path = self.chained_stream(extra=6)
        lines = path.read_text(encoding="utf-8").splitlines()
        edited = len(lines) - 3          # a chained event, with chained events after it
        row = json.loads(lines[edited - 1])
        row["gate"] = row["gate"] + "!"          # one byte
        lines[edited - 1] = json.dumps(row, sort_keys=True, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with self.assertRaises(ledger.ChainError) as caught:
            ledger.validate(self.fx.root)
        message = str(caught.exception)
        broke = int(message.split(":")[1])
        print(f"  MID-EDIT   edited line {edited}; refusal names line {broke}")
        print(f"             {message.splitlines()[0][:150]}")
        self.assertIn("the chain breaks HERE", message)
        self.assertEqual(edited + 1, broke,
                         "the refusal does not point at the edit")
        self.assertLess(broke, len(lines),
                        "the break was reported at the end of the file, not at the edit")

    def test_an_edit_in_the_LEGACY_prefix_is_detected_by_the_first_chained_event(self):
        """The property that makes 1,043 unchained events evidence rather than a file."""
        path = self.chained_stream()
        lines = path.read_text(encoding="utf-8").splitlines()
        row = json.loads(lines[499])
        row["role"] = "Planner" if row.get("role") != "Planner" else "Auditor"
        lines[499] = json.dumps(row, sort_keys=True, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with self.assertRaises(ledger.ChainError) as caught:
            ledger.validate(self.fx.root)
        message = str(caught.exception)
        self.assertIn("the chain breaks HERE", message)
        self.assertEqual(1044, int(message.split(":")[1]),
                         "the first chained event did not notice the prefix moving")
        print("  PREFIX EDIT line 500 changed; the first chained event (1044) refuses")

    def test_truncation_and_tampering_produce_DIFFERENT_refusals(self):
        """Two plants must be proved different. A truncated stream is a machine that
        died and may be recoverable; a tampered one is a person who edited. The
        operator's next move is not the same, so the message must not be."""
        path = self.chained_stream(extra=4)
        anchor = {"digest": ledger.head(self.fx.root),
                  "events": len(ledger.validate(self.fx.root)), "state_revision": 1}
        whole = path.read_text(encoding="utf-8")

        lines = whole.splitlines()
        path.write_text("\n".join(lines[:-2]) + "\n", encoding="utf-8")
        truncated = ledger.truncation_problem(self.fx.root, anchor)
        self.assertIsNotNone(truncated)
        self.assertIn("TRUNCATED", truncated)
        # A truncated chain is still internally consistent, which is the point: only the
        # anchor's COUNT can tell you the tail is gone.
        ledger.validate(self.fx.root)

        path.write_text(whole, encoding="utf-8")
        rows = whole.splitlines()
        row = json.loads(rows[-2])
        row["gate"] = "tampered"
        rows[-2] = json.dumps(row, sort_keys=True, separators=(",", ":"))
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        with self.assertRaises(ledger.ChainError) as caught:
            ledger.validate(self.fx.root)
        tampered = str(caught.exception)

        self.assertIsNone(ledger.truncation_problem(self.fx.root, anchor),
                          "the tampered stream was also reported as truncated")
        # Distinct in the way that matters: each names its own condition and NEITHER
        # names the other's, so an operator reading one is not told to do the other's
        # remedy. Comparing the strings alone would pass on two messages that both said
        # "the stream is bad".
        self.assertIn("TRUNCATED", truncated)
        self.assertNotIn("TRUNCATED", tampered)
        self.assertIn("the chain breaks HERE", tampered)
        self.assertNotIn("the chain breaks HERE", truncated)
        # PHASE 10: this pinned "restore the file from a backup", which was the second
        # half of the sentence "This is a lost tail, not an edit". The recovery drill
        # showed that claim is unsupportable — the anchor holds ONE digest at ONE point,
        # not a digest per line, so nothing can ask whether a short file's remainder is a
        # prefix of what was recorded, and a rebuilt-shorter stream got the confident
        # wrong diagnosis. The message now names both readings. The assertion this test
        # was FOR is unchanged and still checked above and below: the two refusals name
        # different conditions and neither prescribes the other's remedy. Only the needle
        # moved, to the phrase that survives the correction.
        self.assertIn("or a backup", truncated)
        self.assertIn("cannot tell which", truncated,
                      "the truncation refusal went back to claiming it was not an edit")
        self.assertNotIn("cannot tell which", tampered,
                         "the tamper refusal is certain and must stay certain")
        self.assertIn("never edited in place", tampered)
        print(f"  TRUNCATED  {truncated.splitlines()[0][:130]}")
        print(f"  TAMPERED   {tampered.splitlines()[0][:130]}")

    def test_a_wholesale_forgery_is_caught_by_the_anchor(self):
        """A chain that only checks itself is a chain an attacker can rebuild. This
        replaces the stream with one that is INTERNALLY PERFECT and still fails."""
        self.chained_stream(extra=2)
        anchor = {"digest": ledger.head(self.fx.root),
                  "events": len(ledger.validate(self.fx.root)), "state_revision": 7}
        self.assertIsNone(ledger.anchor_problems(self.fx.root, anchor),
                          "control: the real anchor matches the real stream")

        path = Path(self.fx.root) / "audit" / envelope.EVENTS_FILENAME
        path.write_text("", encoding="utf-8")
        for i in range(2):
            ledger.append_chained(self.fx.root, envelope.event_line(
                "gate_reached", None, gate=f"forged-{i}"))
        ledger.validate(self.fx.root)          # the forgery validates: it is consistent
        problem = ledger.anchor_problems(self.fx.root, anchor)
        self.assertIsNotNone(problem, "a rebuilt stream passed as the recorded one")
        self.assertIn("REPLACED, not appended to", problem)
        print("  FORGERY    the rebuilt stream validates, and the anchor refuses it:")
        print(f"             {problem.splitlines()[0][:140]}")

    def test_an_unknown_event_kind_is_refused_by_line_and_not_skipped(self):
        path = self.chained_stream(extra=1)
        rows = path.read_text(encoding="utf-8").splitlines()
        row = json.loads(rows[-1])
        row["event"] = "session_teleported"
        rows[-1] = json.dumps(row, sort_keys=True, separators=(",", ":"))
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        with self.assertRaises(ledger.ChainError) as caught:
            ledger.validate(self.fx.root)
        self.assertIn("unknown event kind", str(caught.exception))
        self.assertIn(f":{len(rows)}:", str(caught.exception))
        print(f"  UNKNOWN    {str(caught.exception).splitlines()[0][:140]}")

    def test_a_missing_required_field_is_refused_by_line(self):
        path = self.chained_stream(extra=1)
        rows = path.read_text(encoding="utf-8").splitlines()
        row = json.loads(rows[-1])
        row.pop("ts")
        rows[-1] = json.dumps(row, sort_keys=True, separators=(",", ":"))
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        with self.assertRaises(ledger.ChainError) as caught:
            ledger.validate(self.fx.root)
        self.assertIn("missing required field 'ts'", str(caught.exception))

    def test_the_anchor_is_recorded_in_canonical_state_at_every_transition(self):
        code, out = self.fx.run("set-status", "F-0001", "accepted")
        self.assertEqual(0, code, out)
        doc = json.loads((self.fx.audit / "state.json").read_text(encoding="utf-8"))
        anchor = doc["ledger_anchor"]
        self.assertEqual(sorted(("digest", "events", "state_revision")),
                         sorted(anchor))
        self.assertIsNone(ledger.anchor_problems(self.fx.root, anchor))
        self.assertIsNone(ledger.truncation_problem(self.fx.root, anchor))
        print(f"  ANCHORED   {anchor}")

    def test_there_is_exactly_one_schema_version_and_a_mechanism_for_a_second(self):
        """The mechanism is owed; a second version is not. A reader that guessed at an
        unknown version would be reading a format it has never seen as if it were this
        one."""
        self.assertEqual(1, ledger.LEDGER_SCHEMA)
        path = self.chained_stream(extra=1)
        rows = path.read_text(encoding="utf-8").splitlines()
        row = json.loads(rows[-1])
        row[ledger.SCHEMA_FIELD] = 2
        rows[-1] = json.dumps(row, sort_keys=True, separators=(",", ":"))
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        with self.assertRaises(ledger.ChainError) as caught:
            ledger.validate(self.fx.root)
        self.assertIn("this reader knows 1", str(caught.exception))
        print(f"  VERSIONED  {str(caught.exception).splitlines()[0][:140]}")
