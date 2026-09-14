"""Roles are capabilities, or they are recommendations. F-0069.

The transition graph was checked and the ACTOR was not: an open Auditor session
performed a human-owned transition and nothing objected. `set-limit` under any session
was the same hole in its administrative form — a session that can widen its own limits
is not bounded by them.

One function at one boundary decides. The table it decides from is DERIVED BY
EXECUTION, not annotated: `scratchpad/derive_routes.py` ran all 17 verbs against a
fixture and diffed canonical state, and the transcript of that run is in the phase
notes. This project has been bitten by the other way — a route annotated as having a
write path that had none — which is why the `touches` column in `write.py` is a
measurement.
"""

import io
import contextlib
import json
import unittest

from tests.harness import Fixture, finding_record, queue_pass, state_doc, xcheck_submodule

write = xcheck_submodule("write")
envelope = xcheck_submodule("envelope")
state_mod = xcheck_submodule("state")

ROLES = ("Planner", "Auditor", "Triage", "Remediator", "Verifier")


def dispatch_record(role, session="a" * 16):
    """An OPEN envelope — no `outcome` — which is what `open_dispatch` looks for."""
    return {"session_id": session, "role": role, "provider": "p", "agent_model": "m",
            "executable": "x", "executable_version": "v", "charter_hash": "0" * 64,
            "prompt_hash": "0" * 64, "state_revision": 1, "head_before": "unknown",
            "sandbox_profile": "readonly", "started": "2026-09-03"}


class TheTableCoversEveryVerbExactlyOnce(unittest.TestCase):

    def test_every_shipped_verb_is_routed_or_admin_or_unrestricted(self):
        verbs = set(write.VERBS)
        routed = {v for r in write.ROLE_ROUTES.values() for v in r}
        covered = routed | set(write.ADMIN_VERBS) | set(write.UNRESTRICTED_VERBS)
        print(f"\n  ROUTE TABLE over {len(verbs)} shipped verbs")
        for v in sorted(verbs):
            roles = sorted(r for r, rv in write.ROLE_ROUTES.items() if v in rv)
            kind = ("admin" if v in write.ADMIN_VERBS else
                    "unrestricted" if v in write.UNRESTRICTED_VERBS else
                    ", ".join(roles))
            print(f"    {v:<18} {kind}")
        self.assertEqual(set(), verbs - covered, "a shipped verb is in no table")
        self.assertEqual(set(), covered - verbs, "the table names a verb that does not "
                                                 "exist — a route to nowhere")
        self.assertEqual(17, len(verbs))

    def test_a_verb_no_role_has_is_refused_fail_closed(self):
        """A verb added later, before anyone routes it, must refuse under a dispatch —
        not fall through as permitted. The fake verb stands in for that verb."""
        with self.assertRaises(write.WriteRefused) as cm:
            write.authorize_dispatch_write(
                dispatch_record("Auditor"), "invent-a-verb", {"id": "F-0001"}, None)
        self.assertIn("invent-a-verb", str(cm.exception))
        self.assertIn("may not run", str(cm.exception))

    def test_the_refusal_never_names_an_owner_the_table_does_not_grant(self):
        """`_owners` is computed from the table, so a refusal cannot send an operator to
        a role that has no such capability either."""
        for verb in sorted(write.VERBS):
            with self.subTest(verb=verb):
                for owner in write._owners(verb, None):
                    if owner.startswith("the operator"):
                        continue
                    self.assertIn(verb, write.ROLE_ROUTES[owner])


class TheF0069ScenarioIsRefused(unittest.TestCase):
    """The audit's reproduction, run against the real boundary."""

    def setUp(self):
        doc = state_doc(findings=[finding_record("F-0001", "reported"),
                                  finding_record("F-0002", "disputed",
                                                 next="Auditor")],
                        queue=[queue_pass("P-01", done=False)])
        doc["sessions"] = [dispatch_record("Auditor")]
        self.fx = Fixture(doc)
        self.addCleanup(self.fx.cleanup)

    def run_verb(self, verb, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            write.run(self.fx.root, verb, argv)
        return buf.getvalue()

    def test_an_open_auditor_session_may_not_perform_the_human_transition(self):
        with self.assertRaises(write.WriteRefused) as cm:
            self.run_verb("set-status", ["F-0001", "accepted", "--next", "Remediator"])
        print(f"\n  F-0069 REFUSAL, verbatim:\n    {cm.exception}")
        msg = str(cm.exception)
        self.assertIn("Auditor", msg)          # the role
        self.assertIn("set-status", msg)       # the verb
        self.assertIn("Triage", msg)           # the owner
        self.assertEqual("reported",
                         state_mod.load_state(self.fx.audit).findings[0].status,
                         "the refused transition was applied anyway")

    def test_the_same_session_may_still_pen_the_humans_disputed_resolution(self):
        """CONTROL for the arm above. §3 grants the Auditor `set-status` as agent-as-pen
        for a `disputed` resolution — if that were refused too, the refusal above would
        be "the Auditor cannot use this verb", which is not the rule."""
        self.run_verb("set-status", ["F-0002", "accepted", "--next", "Remediator"])
        after = {r.id: r.status for r in state_mod.load_state(self.fx.audit).findings}
        print(f"  CONTROL  the disputed resolution was penned: F-0002 -> "
              f"{after['F-0002']}")
        self.assertEqual("accepted", after["F-0002"])

    def test_an_administrative_verb_is_refused_under_a_dispatch(self):
        with self.assertRaises(write.WriteRefused) as cm:
            self.run_verb("set-limit", ["reopen_limit", "9"])
        print(f"  ADMIN REFUSAL:\n    {cm.exception}")
        self.assertIn("administrative", str(cm.exception))
        self.assertEqual(2, state_mod.load_state(self.fx.audit).limits["reopen_limit"])

    def test_the_same_verb_works_for_the_operator_with_no_dispatch_open(self):
        """The other half: the operator, at their own terminal, is not a session."""
        doc = state_doc(findings=[finding_record("F-0001", "reported")],
                        queue=[queue_pass("P-01", done=False)])
        fx = Fixture(doc)                      # no sessions -> no open dispatch
        self.addCleanup(fx.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            write.run(fx.root, "set-limit", ["reopen_limit", "3"])
        self.assertEqual(3, state_mod.load_state(fx.audit).limits["reopen_limit"])

    def test_the_refusal_is_recorded_in_the_event_stream(self):
        with self.assertRaises(write.WriteRefused):
            self.run_verb("set-status", ["F-0001", "accepted"])
        events = [e for e in envelope.read_events(self.fx.root)
                  if e.get("event") == "write_refused"]
        print(f"  EVENT  {json.dumps({k: v for k, v in events[-1].items() if k != 'reason'}, sort_keys=True)}")
        self.assertEqual(1, len(events), "the refusal was raised but never recorded")
        self.assertEqual("set-status", events[-1]["verb"])
        self.assertEqual("Auditor", events[-1]["role"])
        self.assertEqual("F-0001", events[-1]["target"])
        self.assertIn("Triage", events[-1]["reason"])


class EveryLegalRouteStillPasses(unittest.TestCase):
    """Per role, not in aggregate. A gate that refuses everything passes every refusal
    test ever written."""

    def project(self, role, doc=None):
        doc = doc or state_doc(
            findings=[finding_record("F-0001", "reported")],
            queue=[queue_pass("P-01", done=False)])
        doc["sessions"] = [dispatch_record(role)]
        fx = Fixture(doc)
        self.addCleanup(fx.cleanup)
        return fx

    def allowed(self, role, verb, args, doc=None):
        fx = self.project(role, doc)
        state = state_mod.load_state(fx.audit)
        write.authorize_dispatch_write(envelope.open_dispatch(state), verb, args, state)

    def test_each_roles_own_verbs_are_permitted(self):
        cases = {
            "Planner": [("propose-construal", {"key": "k"})],
            "Auditor": [("file-finding", {"id": "F-0009"}),
                        ("record-coverage", {"p-id": "P-01"}),
                        ("queue-pass", {"p-id": "P-09"}),
                        ("propose-construal", {"key": "k"}),
                        ("admit-construal", {"key": "k"})],
            "Triage": [("set-status", {"id": "F-0001", "status": "accepted"}),
                       ("set-status", {"id": "F-0001", "status": "rejected"}),
                       ("set-status", {"id": "F-0001", "status": "deferred"})],
            "Remediator": [("record-plan", {"rp-id": "RP-0001"}),
                           ("record-fix", {"id": "F-0001"}),
                           ("record-refusal", {"id": "F-0001"}),
                           ("file-finding", {"id": "CF-0001"}),
                           ("block-on-norm", {"id": "CF-0001"}),
                           ("propose-construal", {"key": "k"}),
                           ("admit-construal", {"key": "k"})],
            "Verifier": [("record-verdict", {"id": "F-0001"}),
                         ("record-refusal", {"id": "F-0001"}),
                         ("propose-construal", {"key": "k"}),
                         ("admit-construal", {"key": "k"})],
        }
        print("\n  LEGAL ROUTES")
        for role in ROLES:
            for verb, args in cases[role]:
                with self.subTest(role=role, verb=verb):
                    self.allowed(role, verb, args)
            print(f"    {role:<11} {len(cases[role])} route(s) permitted")
        self.assertEqual(set(ROLES), set(cases))

    def test_the_remediators_own_status_moves_are_permitted(self):
        doc = state_doc(findings=[finding_record("F-0001", "accepted",
                                                 next="Remediator")],
                        queue=[queue_pass("P-01", done=False)])
        for status in ("validated", "planned", "disputed", "obsolete",
                       "superseded-by-class"):
            with self.subTest(status=status):
                self.allowed("Remediator", "set-status",
                             {"id": "F-0001", "status": status}, doc=dict(doc))

    def test_render_views_is_permitted_to_every_role(self):
        """It moves no record (the derived table says so) and it is the declared repair
        for view drift. Refusing it inside a session would leave a role that noticed
        drift with no way to repair it."""
        for role in ROLES:
            with self.subTest(role=role):
                self.allowed(role, "render-views", {})

    def test_a_standalone_operator_run_is_unaffected(self):
        doc = state_doc(findings=[finding_record("F-0001", "reported")],
                        queue=[queue_pass("P-01", done=False)])
        fx = Fixture(doc)
        self.addCleanup(fx.cleanup)
        state = state_mod.load_state(fx.audit)
        self.assertIsNone(envelope.open_dispatch(state))
        for verb in sorted(write.VERBS):
            with self.subTest(verb=verb):
                write.authorize_dispatch_write(None, verb, {"id": "F-0001"}, state)


class TheCounterfactual(unittest.TestCase):
    """With the authorizer stubbed to always allow, a NAMED test reddens — and this
    asserts the stub was REACHED, not merely that something failed. A counterfactual
    that only observes a failure cannot tell "the gate was removed" from "the fixture
    broke"."""

    TARGET = ("tests.test_write_authorization.TheF0069ScenarioIsRefused"
              ".test_an_open_auditor_session_may_not_perform_the_human_transition")

    def test_stubbing_the_authorizer_reddens_the_named_test(self):
        reached = []
        real = write.authorize_dispatch_write
        write.authorize_dispatch_write = lambda *a, **k: reached.append(a[1])
        try:
            suite = unittest.defaultTestLoader.loadTestsFromName(self.TARGET)
            self.assertEqual(1, suite.countTestCases(),
                             "the counterfactual target did not resolve to exactly one "
                             "test — a mistyped dotted path runs zero and reports Ran 1")
            result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
        finally:
            write.authorize_dispatch_write = real
        print(f"\n  COUNTERFACTUAL  stub reached with verb(s) {reached}; "
              f"{self.TARGET.split('.')[-1]} "
              f"failures={len(result.failures)} errors={len(result.errors)}")
        self.assertEqual(["set-status"], reached,
                         "the stub was never reached, so the red below is not evidence "
                         "that the authorizer is what refuses")
        self.assertFalse(result.wasSuccessful(),
                         "the named test passed with the authorizer removed — it is not "
                         "testing the authorizer")

    def test_control_the_same_test_is_green_with_the_authorizer_in_place(self):
        suite = unittest.defaultTestLoader.loadTestsFromName(self.TARGET)
        result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
        self.assertTrue(result.wasSuccessful(),
                        "the named test is red even unstubbed, so the counterfactual "
                        "above proves nothing")


if __name__ == "__main__":
    unittest.main()
