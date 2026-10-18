"""P1b (0.9.6): the payer's token ceiling — operator-owned, admission by reservation.

Every account here is built by REAL dispatches (`xcheck next --yes` over a host child that
speaks the `codex exec --json` stream, or crashes, or prints only its own figure) and read
back from the event stream the dispatches wrote. The poisoning table goes through the real
two-file loader. Nothing here needs docker or a provider.
"""

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from tests.harness import XCHECK, Fixture, queue_pass, state_doc, xcheck_submodule
from tests.test_parallel_retry import two_pass_doc

budget = xcheck_submodule("budget")
envelope = xcheck_submodule("envelope")
policy = xcheck_submodule("policy")
util = xcheck_submodule("util")

CHILD = textwrap.dedent('''\
    import json, os, sys
    mode = os.environ.get("XCHECK_T_MODE", "provider")
    if mode == "provider":
        print(json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 30, "cached_input_tokens": 0, "output_tokens": 10}}))
    elif mode == "crash":
        sys.exit(3)
    elif mode == "lost":
        print(json.dumps({"type": "turn.started"}))
    elif mode == "agent":
        print('XCHECK_TELEMETRY {"total_tokens": 3}')
    elif mode == "provider-error":
        print("upstream: service unavailable")
        sys.exit(1)
''')

R = 10          # tokens_per_session — the reservation


class Project:
    """A fixture project whose operator profile carries the payer's ceilings."""

    def __init__(self, test, ceilings, doc=None, adapter="codex-stream", extra=""):
        tmp = Path(tempfile.mkdtemp(prefix="xc-payer-"))
        (tmp / "child.py").write_text(CHILD)
        cmd = f"{sys.executable} {tmp / 'child.py'} {{prompt}}"
        conf = "".join(f"{r}_cmd={cmd}\n" for r in ("planner", "auditor", "remediator",
                                                     "verifier"))
        conf += (f"telemetry_adapter={adapter}\nconstrual_gate=off\nembargo=off\n"
                 f"parallel_passes=off\n" + extra)
        doc = doc or state_doc(queue=[dict(queue_pass("P-01"), charter="read the unit",
                                           stop="the unit is exhausted")])
        self.f = Fixture(doc, conf=conf)
        test.addCleanup(self.f.cleanup)
        with open(self.f.profile, "a", encoding="utf-8") as fh:
            fh.write("".join(f"{k}={v}\n" for k, v in ceilings.items()))
        self.f.git_init(extra=("README.md", "src/core.py", "a.py", "b.py"))

    def cli(self, *argv, mode="provider"):
        e = self.f.env()
        e["XCHECK_T_MODE"] = mode
        p = subprocess.run([sys.executable, str(XCHECK), "--project", str(self.f.root),
                            *argv], capture_output=True, text=True, timeout=300, env=e)
        return p.returncode, p.stdout + p.stderr

    def conf(self):
        return util.load_conf(self.f.audit, profile=self.f.profile)

    def ledger(self):
        return budget.ledger(self.conf(), envelope.read_events(self.f.root))

    def sessions(self):
        return json.loads((self.f.audit / "state.json").read_text())["sessions"]


def gate_line(out):
    return next((ln for ln in out.splitlines() if "stop-ceiling" in ln), out[-400:])


class TheSubjectCannotLoosenIt(unittest.TestCase):
    """The poisoning table: operator `tokens_per_audit=100`, every subject variant."""

    SUBJECTS = [
        ("absent", ""),
        ("budgets=off", "budgets=off\n"),
        ("raised", "tokens_per_audit=100000\ntokens_per_session=99999\n"),
        ("raised + off", "budgets=off\ntokens_per_audit=100000\n"),
        ("garbage", "tokens_per_audit=lots\ntokens_per_session=many\n"),
        ("zero", "tokens_per_audit=0\n"),
        ("negative", "tokens_per_audit=-5\n"),
        ("float", "tokens_per_audit=1e9\n"),
        ("budgets garbage", "budgets=maybe\n"),
        ("tighter", "tokens_per_audit=50\ntokens_per_session=5\n"),
    ]

    def test_the_table(self):
        print("\n  POISONING (operator tokens_per_audit=100, tokens_per_session=10)")
        for label, subject in self.SUBJECTS:
            p = Project(self, {"tokens_per_audit": 100, "tokens_per_session": R})
            (p.f.audit / "orchestrator.conf").write_text(
                (p.f.audit / "orchestrator.conf").read_text() + subject)
            conf = p.conf()
            audit = int(util.conf_number(conf, "tokens_per_audit") or 0)
            session = int(util.conf_number(conf, "tokens_per_session") or 0)
            armed = budget.budget_enabled(conf)
            print(f"    {label:16} budgets={'on' if armed else 'OFF'} "
                  f"tokens_per_audit={audit} tokens_per_session={session}")
            self.assertTrue(armed, label)
            self.assertTrue(1 <= audit <= 100, label)
            self.assertTrue(1 <= session <= R, label)
            self.assertEqual({"tokens_per_audit": 100, "tokens_per_session": R},
                             budget.operator_ceilings(conf), label)
        self.assertEqual(50, util.conf_number(conf, "tokens_per_audit"),
                         "the subject may still TIGHTEN")

    def test_the_subject_cannot_claim_to_be_the_payer(self):
        p = Project(self, {"tokens_per_audit": 100, "tokens_per_session": R})
        (p.f.audit / "orchestrator.conf").write_text(
            (p.f.audit / "orchestrator.conf").read_text()
            + "operator_ceilings=tokens_per_audit=999999\n")
        with self.assertRaises(SystemExit) as e:
            p.conf()
        self.assertIn("read from no file", str(e.exception))

    def test_a_ceiling_without_its_reservation_is_refused(self):
        p = Project(self, {"tokens_per_audit": 100})
        with self.assertRaises(SystemExit) as e:
            p.conf()
        print(f"\n  NO RESERVATION -> {str(e.exception)[:150]}")
        self.assertIn("needs tokens_per_session", str(e.exception))

    def test_hard_mode_is_refused_with_the_guarantee(self):
        for where in ("profile", "subject"):
            p = Project(self, {"tokens_per_audit": 100, "tokens_per_session": R})
            if where == "profile":
                with open(p.f.profile, "a") as fh:
                    fh.write("budget_mode=hard\n")
            else:
                (p.f.audit / "orchestrator.conf").write_text(
                    (p.f.audit / "orchestrator.conf").read_text() + "budget_mode=hard\n")
            with self.assertRaises(SystemExit) as e:
                p.conf()
            print(f"\n  budget_mode=hard ({where}) -> {str(e.exception)[:160]}")
            self.assertIn(budget.GUARANTEE, str(e.exception))


class TheAccountChargesWhatNobodyMeasured(unittest.TestCase):
    """One real session per row, and the charge the stream gives it."""

    def one(self, mode, adapter="codex-stream"):
        p = Project(self, {"tokens_per_audit": 1000, "tokens_per_session": R},
                    adapter=adapter)
        rc, out = p.cli("next", "--yes", mode=mode)
        [s] = p.sessions()
        led = p.ledger()
        print(f"    {mode:15} outcome={s['outcome']:<15} "
              f"source={s.get('telemetry_source')!s:<15} tokens={s.get('tokens')!s:<5} "
              f"-> charged {led['charged']}")
        return p, s, led

    def test_provider_measured_is_charged_its_tokens(self):
        print("\n  CHARGES (reservation 10)")
        _p, s, led = self.one("provider")
        self.assertEqual("provider", s["telemetry_source"])
        self.assertEqual((40, 0, 40), (led["measured"], led["reserved"], led["charged"]))

    def test_a_crash_is_charged_its_reservation(self):
        _p, s, led = self.one("crash")
        self.assertEqual("crash", s["outcome"])
        self.assertEqual((0, R), (led["measured"], led["charged"]))

    def test_lost_telemetry_is_charged_its_reservation(self):
        _p, s, led = self.one("lost")
        self.assertIsNone(s.get("tokens"))
        self.assertEqual(R, led["charged"])

    def test_an_agent_reported_figure_never_lowers_the_charge(self):
        _p, s, led = self.one("agent", adapter="")
        self.assertEqual(("agent-reported", 3), (s.get("telemetry_source"), s.get("tokens")))
        self.assertEqual(R, led["charged"])

    def test_an_open_session_holds_its_reservation_and_a_reload_agrees(self):
        p, _s, led = self.one("provider")
        envelope.emit(p.f.root, "session_dispatched", "f" * 16, role="Auditor")
        again = p.ledger()
        self.assertEqual(led["charged"] + R, again["charged"])
        self.assertEqual(1, again["sessions_open"])
        self.assertEqual(again, p.ledger(), "recomputed from the stream: same number")
        rc, out = p.cli("status")
        print(f"\n  STATUS: {next(ln for ln in out.splitlines() if 'admission:' in ln)}")
        self.assertIn(f"charged {again['charged']:,}", out)
        self.assertIn(budget.GUARANTEE, out)


class ExhaustionRefusesTheNextDispatch(unittest.TestCase):

    def test_next_loop_and_dry_run_refuse_with_the_account(self):
        p = Project(self, {"tokens_per_audit": 45, "tokens_per_session": R})
        rc, out = p.cli("next", "--yes")                  # charged 40, remaining 5 < 10
        self.assertEqual(1, len(p.sessions()), out[-500:])
        for argv in (("next", "--dry-run"), ("next", "--yes"),
                     ("loop", "--max-sessions", "3")):
            rc, out = p.cli(*argv)
            line = gate_line(out)
            print(f"\n  {' '.join(argv):24} rc={rc} {line[:230]}")
            self.assertIn("gate: stop-ceiling", line)
            for part in ("operator ceiling 45", "charged 40", "remaining 5"):
                self.assertIn(part, line)
        self.assertEqual(1, len(p.sessions()), "a refused dispatch dispatched")
        # The refusal names the command that prints the account; run it as printed.
        cmd = line[line.index("`xcheck status`") + 1:].split("`")[0]
        rc, out = p.cli(*cmd.split()[1:])
        self.assertEqual(0, rc, out[-500:])
        self.assertIn("remaining 5", out)


class ARetryReEntersAdmission(unittest.TestCase):

    def test_the_retry_is_refused_when_the_attempt_exhausted_the_ceiling(self):
        p = Project(self, {"tokens_per_audit": 15, "tokens_per_session": R},
                    extra="retry_limit=1\n")
        rc, out = p.cli("next", "--yes", mode="provider-error")
        line = next((ln for ln in out.splitlines() if ln.startswith("retry:")), "")
        refused = next((ln for ln in out.splitlines() if ln.startswith("retry: refused")),
                       "")
        print(f"\n  RETRY: {line}\n         {refused[:200]}")
        self.assertEqual(["provider-error"], [s["outcome"] for s in p.sessions()])
        self.assertIn("remaining 5", refused)

    def test_control_with_room_the_retry_runs(self):
        p = Project(self, {"tokens_per_audit": 1000, "tokens_per_session": R},
                    extra="retry_limit=1\n")
        rc, out = p.cli("next", "--yes", mode="provider-error")
        self.assertEqual(2, len(p.sessions()), out[-600:])


class TwoWorkersAtTheBoundary(unittest.TestCase):

    def test_one_is_admitted_the_other_refused_with_the_remaining(self):
        p = Project(self, {"tokens_per_audit": 15, "tokens_per_session": R},
                    doc=two_pass_doc(), extra="parallel_passes=on\n")
        conf = (p.f.profile.read_text())
        self.assertIn("parallel_passes=on", conf)
        rc, out = p.cli("next", "--yes")
        line = next((ln for ln in out.splitlines() if "admission ceiling refuses" in ln),
                    "")
        print(f"\n  BOUNDARY (remaining 15 = 1.5 x 10): {len(p.sessions())} dispatched\n"
              f"    {line[:240]}")
        self.assertEqual(1, len(p.sessions()), out[-900:])
        self.assertIn("1 of 2 dispatch(es)", line)
        self.assertIn("remaining 15", line)

    def test_control_with_room_both_run(self):
        p = Project(self, {"tokens_per_audit": 1000, "tokens_per_session": R},
                    doc=two_pass_doc(), extra="parallel_passes=on\n")
        rc, out = p.cli("next", "--yes")
        self.assertEqual(2, len(p.sessions()), out[-900:])


class TheGuaranteeIsStated(unittest.TestCase):

    def test_code_docs_and_readme_say_the_same_sentence(self):
        root = Path(__file__).resolve().parent.parent
        for doc in ("README.md", "docs/payer-ceiling.md", "docs/codex-provider.md"):
            self.assertIn(budget.GUARANTEE, (root / doc).read_text(), doc)


if __name__ == "__main__":
    unittest.main()
