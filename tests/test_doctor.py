"""`xcheck doctor` — every launch-door refusal, asked at once, before anything is paid for.

PHASE 16 (fourth audit). The refusals this command prints all existed already, one per
call site, each reachable only by dispatching a session: the operator learned that their
profile resolved inside the subject, or their image was unpinned, or their telemetry was
unconfigured, from a run that had already cost money. `doctor` is the same ten refusals
asked for free.

Two properties carry the whole feature, and they are the two this file spends its length
on. It must DELEGATE — every check calls the refusal that guards its own call site, so
there is one definition of each rule and a green preflight cannot disagree with the door.
And it must NOT ACT — no session, no request, no write, asserted structurally over the
call graph AND behaviourally over the tree, with a positive control proving the detector
can see a write at all ([[spy-needs-a-positive-control]]: "nothing was opened" was green
over an empty list for four phases).

The ten counterfactuals are the third leg. One fixture per check, each breaking exactly
that check, each asserting the other nine did not move — because ten checks that all read
the same one condition would satisfy every criterion above.
"""

import ast
import hashlib
import os
import unittest
from pathlib import Path
from unittest import mock

from tests.harness import needs_live_corpus, REPO, Fixture, state_doc, xcheck_submodule

doctor = xcheck_submodule("doctor")
runner = xcheck_submodule("runner")
util = xcheck_submodule("util")

SOURCE = (REPO / "xcheck" / "doctor.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

# A green machine, as configuration. Every value here is load-bearing for exactly one
# check, which is what makes the ten single-knob mutants below possible.
GREEN = {
    "trust_level": "trusted",
    "sandbox_profile": "worktree",
    "telemetry_adapter": "fake",
    "budgets": "on",
    "tokens_per_session": "50000",
}
ROLES = ("planner", "auditor", "verifier", "remediator")
CMD = "/bin/echo {prompt} {sidecar} {token_cap}"

# The environment decides three of the ten (where the profile is, where logs go, where the
# evidence cache goes). A developer who exports any of them would otherwise get different
# verdicts from the same code, which is a test that measures the machine.
UNSET = ("XCHECK_OPERATOR_PROFILE", "XDG_CONFIG_HOME",
         "XCHECK_LOG_DIR", "XCHECK_EVIDENCE_DIR", "XDG_STATE_HOME")


def case(conf=None, cmd=CMD, head_before=None, conf_fn=None):
    """A throwaway project plus its loaded configuration, green unless a knob is turned.

    `conf_fn` receives the fixture and returns more configuration — for the one knob
    (a root inside the subject) whose VALUE is a path that does not exist until the
    fixture does.
    """
    fx = Fixture()
    head = fx.git_init()
    fx.write_state(state_doc(head_before=head_before or head))
    keys = dict(GREEN)
    keys.update(conf or {})
    keys.update(conf_fn(fx) if conf_fn else {})
    text = "\n".join([f"{k}={v}" for k, v in keys.items() if v is not None]
                     + [f"{r}_cmd={cmd}" for r in ROLES])
    fx.configure(text)
    if keys.get("trust_level") == "":
        # The harness classifies every profile it writes (a synthetic project is trusted
        # by construction), so an UNCLASSIFIED machine cannot be expressed through
        # `configure` — the line has to come back out of the file afterwards.
        fx.profile.write_text("\n".join(
            ln for ln in fx.profile.read_text(encoding="utf-8").splitlines()
            if ln.partition("=")[0].strip() != "trust_level") + "\n", encoding="utf-8")
    return fx, util.load_conf(fx.audit, profile=fx.profile)


def verdicts(fx, conf, profile_path=None):
    with mock.patch.dict(os.environ, {}, clear=False):
        for name in UNSET:
            os.environ.pop(name, None)
        return {r.name: r.verdict for r in
                doctor.run_checks(conf, fx.root, profile_path or fx.profile)}


def snapshot(root):
    """Every file under `root` and its bytes. The write detector."""
    out = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _calls(fn_name):
    """Every dotted call name inside one function of doctor.py."""
    fn = next(n for n in ast.walk(TREE)
              if isinstance(n, ast.FunctionDef) and n.name == fn_name)
    names = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                base = f.value.id if isinstance(f.value, ast.Name) else ""
                names.append(f"{base}.{f.attr}" if base else f.attr)
            elif isinstance(f, ast.Name):
                names.append(f.id)
    return names


class TheDeclaredTable(unittest.TestCase):
    """Required-vs-advisory is a declared list, not a heuristic — the audit's words."""

    def test_ten_checks_each_with_exactly_one_runner(self):
        self.assertEqual(10, len(doctor.CHECKS))
        self.assertEqual(len(doctor.CHECKS), len(set(doctor.CHECKS)))
        self.assertEqual(set(doctor.CHECKS), set(doctor.RUNNERS))

    def test_every_check_is_required_or_advisory_and_never_both(self):
        self.assertEqual(set(doctor.CHECKS), set(doctor.REQUIRED) | set(doctor.ADVISORY))
        self.assertEqual(set(), set(doctor.REQUIRED) & set(doctor.ADVISORY))

    def test_the_severity_is_not_derived_from_the_check_name(self):
        """A rule computed from the name is a rule nobody chose. Both lists are literal
        tuples in the source, so a new check has to be classified by a person."""
        assigned = [n.targets[0].id for n in TREE.body
                    if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                    and n.targets[0].id in ("REQUIRED", "ADVISORY")]
        self.assertEqual(["REQUIRED", "ADVISORY"], assigned)
        for n in TREE.body:
            if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in assigned:
                self.assertIsInstance(n.value, ast.Tuple)
                for elt in n.value.elts:
                    self.assertIsInstance(elt, ast.Constant)


class TheAllGreenControl(unittest.TestCase):
    """Without it, ten fixtures that fail one check each prove only that it never passes."""

    @classmethod
    def setUpClass(cls):
        cls.fx, cls.conf = case()
        cls.got = verdicts(cls.fx, cls.conf)

    def test_no_check_fails_on_a_correctly_configured_machine(self):
        failed = {k: v for k, v in self.got.items() if v == doctor.FAIL}
        print("\n  CONTROL (a machine configured correctly):")
        for name in doctor.CHECKS:
            print(f"    [{self.got[name]:<4}] {name}")
        self.assertEqual({}, failed)

    def test_the_control_actually_ANSWERS_rather_than_skipping(self):
        """Ten SKIPs would also produce zero failures."""
        answered = [k for k, v in self.got.items() if v == doctor.PASS]
        self.assertGreaterEqual(len(answered), 7, self.got)

    def test_it_exits_zero(self):
        _text, code = doctor.transcript(doctor.run_checks(self.conf, self.fx.root, self.fx.profile))
        self.assertEqual(0, code)


class OneFailureAtATime(unittest.TestCase):
    """Ten fixtures, ten knobs, and every other check held still.

    The couplings are DECLARED per arm rather than tolerated: `container` makes the image
    check applicable, so two arms say so out loud instead of asserting nine unchanged and
    quietly excluding one.
    """

    @classmethod
    def setUpClass(cls):
        cls.base_fx, cls.base_conf = case()
        cls.base = verdicts(cls.base_fx, cls.base_conf)

    def only(self, got, name, also=()):
        self.assertEqual(doctor.FAIL, got[name], f"{name} did not fail")
        for other in doctor.CHECKS:
            if other == name or other in also:
                continue
            self.assertEqual(self.base[other], got[other],
                             f"{other} moved with {name}: a second condition is being read")

    def test_operator_profile_inside_the_subject(self):
        fx, conf = case()
        inside = fx.audit / "operator.conf"
        inside.write_text(fx.profile.read_text(encoding="utf-8"), encoding="utf-8")
        self.only(verdicts(fx, conf, profile_path=inside),
                  "operator-profile-outside-subject")

    def test_trust_not_classified(self):
        # An empty value, not a missing line: the harness CLASSIFIES every fixture
        # profile it writes (a synthetic project is trusted by construction), so the way
        # to model an operator who declared nothing is to declare nothing.
        fx, conf = case(conf={"trust_level": ""})
        self.only(verdicts(fx, conf), "trust-classified")

    def test_the_sandbox_backend_is_not_there(self):
        fx, conf = case(conf={"sandbox_profile": "container"})
        with mock.patch.object(runner, "backend_probe",
                               return_value=(False, "docker is not on PATH")):
            got = verdicts(fx, conf)
        # Declared coupling: under `container` the image question stops being moot, so
        # image-pinned moves SKIP -> PASS. That is the check becoming applicable, not a
        # second condition leaking into this one.
        self.assertEqual(doctor.PASS, got["image-pinned"])
        # PHASE 10 (fifth audit): the same is true of agent-cli-present. Under a
        # container the role command runs inside the IMAGE, so the host PATH answers a
        # question nobody asked and the check says UNVERIFIED. Declared here rather than
        # excluded, because the coupling IS the phase's finding.
        self.assertEqual(doctor.UNVERIFIED, got["agent-cli-present"])
        # ...and provider-path-permitted moves PASS -> FAIL for the same reason: this
        # fixture sets no `egress_allowlist`, so the container it just asked for runs
        # `--network=none` and has no route to the provider. Both are the audit's false
        # PASSes, and both are consequences of the ONE knob this arm turns.
        self.assertEqual(doctor.FAIL, got["provider-path-permitted"])
        self.only(got, "sandbox-available",
                  also=("image-pinned", "agent-cli-present",
                        "provider-path-permitted"))

    def test_the_container_image_is_not_pinned(self):
        fx, conf = case(conf={"sandbox_profile": "container",
                              "container_image": "alpine/git:latest"})
        with mock.patch.object(runner, "backend_probe",
                               return_value=(True, "docker server 27.0")):
            got = verdicts(fx, conf)
        self.assertEqual(doctor.UNVERIFIED, got["agent-cli-present"],
                         "under `container` the host PATH is not where the command runs")
        self.assertEqual(doctor.FAIL, got["provider-path-permitted"],
                         "a container with no egress allowlist has no provider path")
        self.only(got, "image-pinned",
                  also=("agent-cli-present", "provider-path-permitted"))

    def test_the_agent_cli_is_not_installed(self):
        fx, conf = case(cmd="/nonexistent/xcheck-doctor-not-a-binary {prompt} "
                            "{sidecar} {token_cap}")
        self.only(verdicts(fx, conf), "agent-cli-present")

    def test_the_provider_adapter_names_nothing(self):
        fx, conf = case(conf={"telemetry_adapter": "nope"})
        self.only(verdicts(fx, conf), "provider-path-permitted")

    def test_no_role_command_can_write_a_sidecar(self):
        fx, conf = case(cmd="/bin/echo {prompt} {token_cap}")
        self.only(verdicts(fx, conf), "telemetry-sidecar-supported")

    def test_a_root_resolves_inside_the_subject(self):
        fx, conf = case(conf_fn=lambda fx: {"log_dir": str(fx.root / "logs")})
        self.only(verdicts(fx, conf), "roots-outside-project")

    def test_a_ceiling_the_provider_never_hears(self):
        fx, conf = case(cmd="/bin/echo {prompt} {sidecar}")
        self.only(verdicts(fx, conf), "budgets-applicable")

    def test_head_has_moved_away_from_the_recorded_scope(self):
        fx, conf = case(head_before="1" * 40)
        self.only(verdicts(fx, conf), "head-matches-scope")


class ItOnlyReads(unittest.TestCase):
    """A preflight that changed the machine would be a session, which is the thing it
    exists to avoid paying for."""

    def test_it_writes_nothing_under_the_project(self):
        fx, conf = case()
        before = snapshot(fx.root)
        verdicts(fx, conf)
        self.assertEqual(before, snapshot(fx.root))

    def test_the_write_detector_can_see_a_write(self):
        """[[spy-needs-a-positive-control]]. Without this arm the assertion above is
        satisfied by a snapshot function that returns the same empty dict twice."""
        fx, _conf = case()
        before = snapshot(fx.root)
        (fx.root / "a-write.txt").write_text("x", encoding="utf-8")
        self.assertNotEqual(before, snapshot(fx.root))

    def test_it_calls_no_writing_function(self):
        # Names nobody writes by accident. `append` is deliberately NOT here: a list
        # append matched it, and a needle that fires on ordinary code gets deleted rather
        # than obeyed (the same trap phase 14 hit with `list.append`).
        forbidden = {"write_state", "write_text", "write_bytes", "mkdir", "unlink",
                     "rmtree", "migrate_logs", "refuse_outside_subject",
                     "log_root", "cache_root",       # these two CREATE the directory
                     "retention.record", "retention.prune", "ledger.append"}
        called = set()
        for name in doctor.RUNNERS:
            for c in _calls(doctor.RUNNERS[name].__name__):
                called |= {c, c.rsplit(".", 1)[-1]}
        self.assertEqual(set(), called & forbidden, sorted(called & forbidden))

    def test_the_only_subprocess_it_runs_is_a_git_read(self):
        verbs = []
        for node in ast.walk(TREE):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "run" and node.args
                    and isinstance(node.args[0], ast.List)):
                argv = [a.value for a in node.args[0].elts if isinstance(a, ast.Constant)]
                verbs.append(tuple(argv[:2]))
        self.assertEqual([("git", "rev-parse")], verbs)

    def test_it_imports_nothing_that_could_reach_a_network(self):
        """The provider check verifies the PATH, not the model. A module that could open
        a socket would make that a promise about intent rather than about capability."""
        imported = set()
        for node in ast.walk(TREE):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(set(), imported & {"socket", "http", "urllib", "requests",
                                            "ssl", "httpx", "asyncio"})

    def test_the_transcript_says_the_provider_check_sent_nothing(self):
        """The audit's wording: it verifies the path, not the model, and the transcript
        says which."""
        fx, conf = case()
        detail = {r.name: r.detail for r in
                  doctor.run_checks(conf, fx.root, fx.profile)}["provider-path-permitted"]
        self.assertIn("no request was sent", detail)


class ItDelegates(unittest.TestCase):
    """One definition per rule. A doctor that reimplemented a refusal would pass the
    configuration the launch door then refuses — worse than no doctor, because the
    operator now has a green preflight to disbelieve."""

    # check -> the refusal it must CALL, by the name it has at its own call site.
    DELEGATES = {
        "check_operator_profile": "policy.profile_location_problem",
        "check_trust": "runner.require_trust",
        "check_sandbox": "runner.backend_probe",
        "check_image_pinned": "runner.container_image_ref",
        "check_roots": "retention.root_location_problem",
        "check_budgets": "budget.cap_problem",
        "check_provider_path": "provider.adapter_for",
    }

    def test_every_check_calls_the_refusal_that_guards_its_own_call_site(self):
        for fn, needed in self.DELEGATES.items():
            self.assertIn(needed, _calls(fn), f"{fn} does not delegate to {needed}")

    def test_the_delegated_refusals_are_the_ones_the_product_calls(self):
        """A name this file invented would make the arm above a test of itself."""
        sources = {"policy": "policy.py", "runner": "runner.py",
                   "retention": "retention.py", "budget": "budget.py",
                   "provider": "provider.py"}
        for needed in self.DELEGATES.values():
            mod, _, fn = needed.partition(".")
            text = (REPO / "xcheck" / sources[mod]).read_text(encoding="utf-8")
            self.assertIn(f"def {fn}(", text, needed)

    def test_the_check_bodies_are_short_enough_to_be_delegation(self):
        """A check that grew a rule of its own stops being a call and starts being a
        second definition. Length is a proxy, and a crude one — it is here because the
        arm above cannot tell a delegating call from a call plus a reimplementation.

        PHASE 10 (fifth audit): measured over CODE, with the docstring excluded by
        identity. It used to count `end_lineno - lineno`, so explaining WHY a check
        answers what it answers made the check look like a reimplementation, and the
        cheapest way to go green was to delete the explanation
        ([[assert-over-the-ast-not-the-prose]]). Prose cannot be a second definition of a
        rule; statements can.
        """
        widest = []
        for fn in self.DELEGATES:
            node = next(n for n in ast.walk(TREE)
                        if isinstance(n, ast.FunctionDef) and n.name == fn)
            body = [n for n in node.body if not (isinstance(n, ast.Expr)
                                                 and isinstance(n.value, ast.Constant)
                                                 and isinstance(n.value.value, str))]
            code = body[-1].end_lineno - body[0].lineno + 1
            widest.append((code, node.end_lineno - node.lineno, fn))
            self.assertLess(code, 40, f"{fn} is {code} code line(s)")
        widest.sort(reverse=True)
        print("\n  DELEGATION  code lines (with docstring) per check")
        for code, whole, fn in widest[:3]:
            print(f"    {fn:<28} {code:>3} ({whole})")

    def test_COUNTERFACTUAL_the_docstring_exclusion_is_load_bearing(self):
        """Without it the measurement is of prose, not code — asserted rather than
        assumed, because an exclusion nothing depends on is an exclusion that quietly
        stops matching."""
        gap = []
        for fn in self.DELEGATES:
            node = next(n for n in ast.walk(TREE)
                        if isinstance(n, ast.FunctionDef) and n.name == fn)
            body = [n for n in node.body if not (isinstance(n, ast.Expr)
                                                 and isinstance(n.value, ast.Constant)
                                                 and isinstance(n.value.value, str))]
            gap.append((node.end_lineno - node.lineno)
                       - (body[-1].end_lineno - body[0].lineno + 1))
        self.assertTrue(any(g > 0 for g in gap),
                        "no check has a docstring, so the exclusion measures nothing")
        print(f"  EXCLUDED    {sum(gap)} docstring line(s) across "
              f"{len(self.DELEGATES)} checks; the widest check is "
              f"{max(gap)} lines shorter without its prose")


class TheExitCode(unittest.TestCase):
    """Non-zero when a REQUIRED check fails, and only then."""

    def test_a_failed_required_check_refuses(self):
        fx, conf = case(conf={"trust_level": ""})
        text, code = doctor.transcript(doctor.run_checks(conf, fx.root, fx.profile))
        self.assertEqual(1, code)
        self.assertIn("REFUSED", text)

    def test_a_failed_advisory_check_does_not(self):
        fx, conf = case(head_before="1" * 40)
        text, code = doctor.transcript(doctor.run_checks(conf, fx.root, fx.profile))
        self.assertEqual(0, code)
        self.assertIn("head-matches-scope", text)
        self.assertIn("every REQUIRED check passed", text)

    def test_the_remedy_is_printed_for_every_failure(self):
        fx, conf = case(conf={"trust_level": ""}, cmd="/nope/x {prompt}")
        results = doctor.run_checks(conf, fx.root, fx.profile)
        text, _code = doctor.transcript(results)
        for r in results:
            if r.verdict == doctor.FAIL:
                self.assertTrue(r.remedy, f"{r.name} fails with no remedy")
                self.assertIn(r.remedy.split(":")[0][:40], text)


@needs_live_corpus
class OnThisMachine(unittest.TestCase):
    """The command run against the real repository, with every real verdict shown —
    failures included. A preflight demonstrated only on fixtures has not been run."""

    def test_it_answers_for_this_checkout(self):
        conf = util.load_conf(REPO / "audit")
        results = doctor.run_checks(conf, REPO)
        print("\n  THIS MACHINE, this checkout:")
        for r in results:
            tag = "required" if r.name in doctor.REQUIRED else "advisory"
            print(f"    [{r.verdict:<4}] {r.name:<34} ({tag}) {r.detail[:90]}")
        self.assertEqual([r.name for r in results], list(doctor.CHECKS))
        for r in results:
            self.assertIn(r.verdict, (doctor.PASS, doctor.FAIL, doctor.SKIP))
            if r.verdict == doctor.FAIL:
                self.assertTrue(r.remedy)


if __name__ == "__main__":
    unittest.main()


class APassIsNotAClaimOfEnforcement(unittest.TestCase):
    """PHASE 7 (fifth audit). `budgets-applicable` PASSed on a configuration whose only
    checked property was that `{token_cap}` appeared in the role command. The audit read
    that verdict the way an operator would — as a limit that is applied — and
    `true {token_cap} {prompt}` earns the same PASS. The verdict stays (nothing is
    wrong), and the detail now says what was and was not observed."""

    def detail(self, fx, conf):
        with mock.patch.dict(os.environ, {}, clear=False):
            for name in UNSET:
                os.environ.pop(name, None)
            got = {r.name: r for r in doctor.run_checks(conf, fx.root, fx.profile)}
        return got["budgets-applicable"]

    def test_a_filled_placeholder_passes_and_says_it_is_unverified(self):
        fx, conf = case()
        r = self.detail(fx, conf)
        print(f"\n  PASS DETAIL  {r.detail}")
        self.assertEqual(doctor.PASS, r.verdict)
        self.assertIn("UNVERIFIED for 4 role command(s)", r.detail)
        self.assertNotIn("enforced", r.detail)

    def test_a_command_that_enforces_nothing_gets_the_same_words(self):
        """The counterfactual as the audit wrote it. `true` consumes the number as an
        argument and limits nothing; doctor cannot tell it from a real wrapper, and the
        detail is the same UNVERIFIED either way rather than a PASS that reads as yes."""
        fx, conf = case(cmd="true {prompt} {sidecar} {token_cap}")
        r = self.detail(fx, conf)
        print(f"  COUNTERFACTUAL `true …` -> {r.verdict}: {r.detail}")
        self.assertEqual(doctor.PASS, r.verdict)
        self.assertIn("UNVERIFIED", r.detail)

    def test_no_ceiling_no_note(self):
        """The control: the sentence appears because a cap is armed, not always."""
        fx, conf = case(conf={"tokens_per_session": None})
        r = self.detail(fx, conf)
        print(f"  NO CEILING   {r.detail}")
        self.assertNotIn("UNVERIFIED", r.detail)


# ------------------------------------------------------ PHASE 10 (fifth audit)

def only_role(fx, role):
    """Take every OTHER role command back out of the profile.

    `case()` seeds the whole operator-profile template, which carries a command for all
    four roles — so a test that means "only `auditor_cmd` is set" has to remove the rest,
    or it is testing a fully configured machine and asserting the answer for a partial
    one."""
    others = {f"{r}_cmd" for r in doctor.ROLES} - {f"{role}_cmd"}
    keep = [ln for ln in fx.profile.read_text(encoding="utf-8").splitlines()
            if ln.partition("=")[0].strip() not in others]
    fx.profile.write_text("\n".join(keep) + "\n", encoding="utf-8")
    return util.load_conf(fx.audit, profile=fx.profile)


class ItAnswersAboutTheNextDispatch(unittest.TestCase):
    """The audit's two false PASSes. Both came from a check answering a question that was
    easy to ask instead of the one that matters: which roles will actually be dispatched,
    and inside what."""

    def results(self, fx, conf):
        with mock.patch.dict(os.environ, {}, clear=False):
            for name in UNSET:
                os.environ.pop(name, None)
            with mock.patch.object(runner, "backend_probe",
                                   return_value=(True, "docker server 27.0")):
                return {r.name: r for r in doctor.run_checks(conf, fx.root, fx.profile)}

    def test_a_role_with_no_command_is_named_and_the_verdict_says_so(self):
        """FALSE PASS #1: with `auditor_cmd` alone this said PASS, because it looped over
        the roles that HAD a command. There is no default role command, so an unset role
        is one `runner.build_cmd` refuses by name."""
        fx, conf = case()
        conf = only_role(fx, "auditor")
        r = self.results(fx, conf)["agent-cli-present"]
        print(f"\n  ONE ROLE   {r.verdict}: {r.detail}")
        self.assertEqual(doctor.FAIL, r.verdict)
        for role in ("planner", "verifier", "remediator"):
            self.assertIn(role, r.detail)
        self.assertIn("UNSET", r.detail)
        self.assertIn("cannot be dispatched", r.detail)

    def test_under_a_container_the_host_PATH_is_not_consulted(self):
        """FALSE PASS #2a: the command runs inside the IMAGE. `shutil.which` reads this
        machine, and looking inside the image means starting a container — an action a
        preflight may not take. So: UNVERIFIED, with the command that would settle it."""
        fx, conf = case(conf={"sandbox_profile": "container"},
                        cmd="/definitely/not/here/xcheck-phase10 {prompt} {sidecar} "
                            "{token_cap}")
        r = self.results(fx, conf)["agent-cli-present"]
        print(f"  CONTAINED  {r.verdict}: {r.detail[:120]}")
        print(f"    TO VERIFY: {r.remedy}")
        self.assertEqual(doctor.UNVERIFIED, r.verdict,
                         "a command absent from the HOST decided a container question")
        self.assertIn("in the image", r.detail)
        self.assertIn("docker run --rm", r.remedy)
        self.assertNotIn("NOT ON PATH", r.detail)

    def test_a_container_with_no_allowlist_has_no_provider_path(self):
        """FALSE PASS #2b: `container_argv` passes `--network=none` unless a broker was
        established, and a broker needs a non-empty allowlist. The check printed "the
        host default applies" about a container that has no host network to default to."""
        fx, conf = case(conf={"sandbox_profile": "container",
                              "telemetry_adapter": "codex", "egress_allowlist": ""})
        r = self.results(fx, conf)["provider-path-permitted"]
        print(f"  NO EGRESS  {r.verdict}: {r.detail[:150]}")
        self.assertEqual(doctor.FAIL, r.verdict)
        self.assertIn("--network=none", r.detail)
        self.assertNotIn("host default applies", r.detail)

    def test_CONTROL_the_same_container_with_an_allowlist_passes(self):
        """Without this arm the one above is satisfied by a check that refuses every
        container."""
        fx, conf = case(conf={"sandbox_profile": "container",
                              "telemetry_adapter": "codex",
                              "egress_allowlist": "api.openai.com"})
        r = self.results(fx, conf)["provider-path-permitted"]
        print(f"  ALLOWLIST  {r.verdict}: {r.detail[:130]}")
        self.assertEqual(doctor.PASS, r.verdict)
        self.assertIn("api.openai.com", r.detail)

    def test_CONTROL_a_fully_configured_host_machine_still_passes_everything(self):
        """Criterion 6. A doctor answering UNVERIFIED everywhere would satisfy every arm
        above, so the green machine is asserted to stay green."""
        fx, conf = case()
        got = self.results(fx, conf)
        unverified = [n for n, r in got.items() if r.verdict == doctor.UNVERIFIED]
        print(f"  CONTROL    verdicts: "
              f"{', '.join(sorted({r.verdict for r in got.values()}))}; "
              f"unverified={unverified or 'none'}")
        self.assertEqual([], unverified)
        for name in doctor.REQUIRED:
            self.assertEqual(doctor.PASS, got[name].verdict, name)


class UnverifiedIsNotASoftPass(unittest.TestCase):
    """Criterion 5. Adding a fourth verdict must not weaken the required/advisory split:
    what it does to the exit code is declared, and tested in both directions."""

    def test_the_vocabulary_is_declared_and_closed(self):
        self.assertEqual((doctor.PASS, doctor.FAIL, doctor.UNVERIFIED, doctor.SKIP),
                         doctor.VERDICTS)
        self.assertEqual((doctor.FAIL, doctor.UNVERIFIED), doctor.BLOCKING)
        self.assertNotIn(doctor.SKIP, doctor.BLOCKING,
                         "a check that does not apply is not a check that failed")
        print(f"\n  VOCABULARY {', '.join(doctor.VERDICTS)}; "
              f"blocking={', '.join(doctor.BLOCKING)}")

    def result(self, name, verdict):
        return doctor.Result(name, verdict, "detail", "remedy")

    def test_an_unverified_REQUIRED_check_exits_non_zero(self):
        rows = [self.result(n, doctor.PASS) for n in doctor.CHECKS]
        rows[doctor.CHECKS.index("agent-cli-present")] = self.result(
            "agent-cli-present", doctor.UNVERIFIED)
        text, code = doctor.transcript(rows)
        print(f"  REQUIRED   agent-cli-present UNVERIFIED -> exit {code}")
        self.assertEqual(1, code, "an unverified REQUIRED check exited 0")
        self.assertIn("could not be checked", text)
        self.assertIn("TO VERIFY", text)

    def test_an_unverified_ADVISORY_check_does_not(self):
        rows = [self.result(n, doctor.PASS) for n in doctor.CHECKS]
        rows[doctor.CHECKS.index("head-matches-scope")] = self.result(
            "head-matches-scope", doctor.UNVERIFIED)
        text, code = doctor.transcript(rows)
        print(f"  ADVISORY   head-matches-scope UNVERIFIED -> exit {code}")
        self.assertEqual(0, code)
        self.assertIn("less certain", text)

    def test_a_SKIP_still_exits_zero_from_a_required_check(self):
        """The split the phase must not weaken from the other side: `SKIP` means the
        check does not apply, which is not the same as could-not-look."""
        rows = [self.result(n, doctor.PASS) for n in doctor.CHECKS]
        rows[doctor.CHECKS.index("agent-cli-present")] = self.result(
            "agent-cli-present", doctor.SKIP)
        _text, code = doctor.transcript(rows)
        self.assertEqual(0, code)
        print("  SKIP       a required check that does not apply -> exit 0")

    def test_the_transcript_distinguishes_a_fix_from_a_way_to_find_out(self):
        rows = [self.result("agent-cli-present", doctor.UNVERIFIED),
                self.result("trust-classified", doctor.FAIL)]
        text, _code = doctor.transcript(rows)
        self.assertIn("TO VERIFY: remedy", text)
        self.assertIn("FIX: remedy", text)
        print("  REMEDY     FAIL prints FIX, UNVERIFIED prints TO VERIFY")
