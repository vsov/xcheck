"""0.9.0 phase 1: the courier's read-only gate authorizes on git's answer, not on prose.

Until 0.9.0 the gate asked `patch_paths()` which files a session had touched, and that
function partitioned the `diff --git` header on the literal `" b/"`. A `diff --git`
header is a HUMAN-readable line: git quotes any path with non-ASCII or control bytes.
So a file named `é.txt` produced

    diff --git "a/é.txt" "b/é.txt"

from which the parser extracted nothing at all — and an empty set of touched paths read
as "this session did not change the material". A read-only Auditor's edit to the project
was applied.

Every test here drives the REAL `Sandbox.collect()` against a real git repository in a
system temp dir. Asserting on `changed_paths()` alone would prove the helper works while
leaving open the question the audit actually asked: does the gate consult it.

Each adversarial name is checked twice in the same test method — refused under
`readonly`, applied under `worktree`. Without that control arm, a harness that silently
failed to create the file at all would report seven passing refusals.
"""

import ast
import contextlib
import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.harness import REPO, xcheck_submodule

runner = xcheck_submodule("runner")

GIT = shutil.which("git")


def git(cwd, *args):
    return subprocess.run([GIT] + list(args), cwd=str(cwd), capture_output=True,
                          text=True, timeout=60)


def make_repo():
    """A throwaway git repo in a SYSTEM temp dir (never in the project tree, F-0120)."""
    tmp = Path(tempfile.mkdtemp(prefix="xcheck-courier-paths-"))
    (tmp / "audit").mkdir()
    (tmp / "audit" / "XCHECK.md").write_text("# XCHECK\n", encoding="utf-8")
    (tmp / "material.txt").write_text("original\n", encoding="utf-8")
    git(tmp, "init", "-q")
    git(tmp, "config", "user.email", "t@example.invalid")
    git(tmp, "config", "user.name", "test")
    git(tmp, "add", "-A")
    git(tmp, "commit", "-qm", "base")
    return tmp


# The name, and what makes it adversarial. `é.txt` is the case the external audit
# reproduced; the rest are the neighbouring shapes a fix aimed only at quoting would
# still miss. `x b/y` was found during planning: it contains the exact substring the old
# parser split on, so the parser returned `y b/x b/y` — a path that does not exist.
ADVERSARIAL = [
    ("quoted-utf8", "é.txt"),
    ("tab", "tab\there.txt"),
    ("newline", "new\nline.txt"),
    ("b-slash-substring", "x b/y"),
    ("leading-dash", "-dash.txt"),
    ("backslash", "back\\slash.txt"),
    ("non-ascii-cjk", "日本語.txt"),
]


def try_write(root, rel, text):
    """Create `rel` under `root`, or return the OSError the filesystem raised.

    A newline in a filename is legal on APFS and ext4 and illegal elsewhere. Where the
    filesystem refuses, the case is reported as uncreatable rather than silently dropped:
    a coverage claim this run did not earn is worse than a gap it admits.
    """
    p = root / rel
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return None
    except OSError as e:
        return e


class TheReadOnlyGateHoldsForEveryPathGitHasToQuote(unittest.TestCase):
    """The bypass, one adversarial name at a time, each with its own control arm."""

    def setUp(self):
        self.project = make_repo()
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        self.uncreatable = []

    def run_session(self, profile, rel, body):
        """Enter a sandbox, write `rel` inside it, collect. Returns the SystemExit
        message on refusal, or the printed outcome on success."""
        sb = runner.Sandbox(self.project, runner.PROFILES[profile],
                            "Auditor" if profile == "readonly" else "Remediator")
        wt = sb.enter()
        try:
            err = try_write(wt, rel, body)
            if err is not None:
                return ("uncreatable", err)
            try:
                sb.collect()
            except SystemExit as e:
                return ("refused", str(e))
            return ("applied", None)
        finally:
            sb.leave()

    def test_every_adversarial_name_is_refused_and_the_control_arm_applies(self):
        refused, applied = [], []
        for label, rel in ADVERSARIAL:
            with self.subTest(label):
                kind, detail = self.run_session("readonly", rel, "an Auditor wrote this\n")
                if kind == "uncreatable":
                    self.uncreatable.append((label, rel, detail))
                    continue
                self.assertEqual(kind, "refused",
                                 f"{label}: a readonly Auditor changed {rel!r} and the "
                                 f"courier applied it — this is the bypass")
                self.assertIn(rel, detail,
                              f"{label}: the refusal did not name {rel!r}, so the "
                              f"operator cannot tell what was rejected")
                refused.append((label, rel, detail))

                # Control arm, same method, same routine: under a profile that DOES
                # permit material changes the identical session must get through. If it
                # does not, the refusal above proved nothing about the gate.
                self.project.joinpath("material.txt").write_text("original\n",
                                                                 encoding="utf-8")
                kind2, _ = self.run_session("worktree", rel, "a Remediator wrote this\n")
                self.assertEqual(kind2, "applied",
                                 f"{label}: the control arm did not apply — the harness "
                                 f"cannot create this change at all, so the refusal is "
                                 f"vacuous")
                self.assertTrue((self.project / rel).is_file(),
                                f"{label}: control arm reported applied but {rel!r} is "
                                f"not in the project")
                (self.project / rel).unlink()
                applied.append(label)

        print(f"\n[courier-paths] refused under readonly: {len(refused)}/"
              f"{len(ADVERSARIAL)}; control arm applied: {len(applied)}")
        for label, rel, msg in refused:
            print(f"  REFUSAL {label:<20} {msg.splitlines()[0][:150]}")
        for label, rel, err in self.uncreatable:
            print(f"  UNCREATABLE {label:<16} {rel!r} — {err} (case not covered on this "
                  f"filesystem; not counted as a pass)")
        self.assertGreaterEqual(len(refused), len(ADVERSARIAL) - len(self.uncreatable))
        self.assertTrue(refused, "no adversarial name was even creatable")


class QuotepathSettingCannotChangeTheAnswer(unittest.TestCase):
    """`core.quotepath` decides whether git escapes non-ASCII in HUMAN output. The gate
    must not care, because `-z` output is never quoted — that is the whole reason for
    reading the machine format."""

    def setUp(self):
        self.project = make_repo()
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def paths_with(self, value):
        git(self.project, "config", "core.quotepath", value)
        sb = runner.Sandbox(self.project, runner.PROFILES["readonly"], "Auditor")
        wt = sb.enter()
        try:
            (wt / "é.txt").write_text("x\n", encoding="utf-8")
            git(wt, "add", "-A")
            return runner.changed_paths(wt, sb.base)
        finally:
            sb.leave()

    def test_identical_under_both_settings(self):
        on, off = self.paths_with("true"), self.paths_with("false")
        print(f"\n[courier-paths] core.quotepath=true  -> {on}")
        print(f"[courier-paths] core.quotepath=false -> {off}")
        self.assertEqual(on, off)
        self.assertEqual(on, ["é.txt"],
                         "the path arrived escaped — that is the human format leaking "
                         "into the authorization decision")


class RenamingMaterialIntoAuditIsStillAMaterialChange(unittest.TestCase):
    """Found during planning, not in the audit. With git's rename detection on,
    `git mv material.txt audit/material.txt` reports ONLY `audit/material.txt`: the gate
    sees a path under `audit/`, approves, and a material file has been deleted. Rename
    detection is off in the gate for exactly this reason."""

    def setUp(self):
        self.project = make_repo()
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def test_the_deleted_source_path_is_reported_and_refused(self):
        sb = runner.Sandbox(self.project, runner.PROFILES["readonly"], "Auditor")
        wt = sb.enter()
        try:
            git(wt, "mv", "material.txt", "audit/material.txt")
            git(wt, "add", "-A")
            paths = runner.changed_paths(wt, sb.base)
            with self.assertRaises(SystemExit) as cm:
                sb.collect()
        finally:
            sb.leave()
        print(f"\n[courier-paths] rename into audit/ -> {paths}")
        self.assertIn("material.txt", paths, "the deleted source path was not reported")
        self.assertIn("material.txt", str(cm.exception))
        self.assertEqual((self.project / "material.txt").read_text(), "original\n",
                         "the material file was moved out from under the project")


class DeletingMaterialIsRefusedAsLoudlyAsEditingIt(unittest.TestCase):
    def setUp(self):
        self.project = make_repo()
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def test_a_readonly_role_cannot_delete_a_material_file(self):
        sb = runner.Sandbox(self.project, runner.PROFILES["readonly"], "Verifier")
        wt = sb.enter()
        try:
            (wt / "material.txt").unlink()
            with self.assertRaises(SystemExit) as cm:
                sb.collect()
        finally:
            sb.leave()
        print(f"\n[courier-paths] deletion refusal: {str(cm.exception).splitlines()[0][:150]}")
        self.assertIn("material.txt", str(cm.exception))
        self.assertTrue((self.project / "material.txt").is_file())


class TheNulSplitDropsTheTerminatorAndNothingElse(unittest.TestCase):
    """`-z` TERMINATES each record, so the split's last element is always empty. A test
    with exactly one changed file is what catches an off-by-one in that strip: with a
    naive `split`, one change yields `['audit/one.md', '']` and the empty string does not
    start with `audit/`, which would refuse every session that changed anything."""

    def setUp(self):
        self.project = make_repo()
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def test_one_changed_file_is_exactly_one_path(self):
        sb = runner.Sandbox(self.project, runner.PROFILES["readonly"], "Auditor")
        wt = sb.enter()
        try:
            (wt / "audit" / "one.md").write_text("only this\n", encoding="utf-8")
            git(wt, "add", "-A")
            paths = runner.changed_paths(wt, sb.base)
            sb.collect()          # must NOT refuse: everything is under audit/
        finally:
            sb.leave()
        print(f"\n[courier-paths] single change -> {paths}")
        self.assertEqual(paths, ["audit/one.md"])
        self.assertEqual((self.project / "audit" / "one.md").read_text(), "only this\n")

    def test_two_changed_files_are_exactly_two_paths(self):
        sb = runner.Sandbox(self.project, runner.PROFILES["readonly"], "Auditor")
        wt = sb.enter()
        try:
            (wt / "audit" / "a.md").write_text("a\n", encoding="utf-8")
            (wt / "audit" / "b.md").write_text("b\n", encoding="utf-8")
            git(wt, "add", "-A")
            paths = runner.changed_paths(wt, sb.base)
        finally:
            sb.leave()
        self.assertEqual(sorted(paths), ["audit/a.md", "audit/b.md"])


class TheHeaderParserIsGoneFromTheAuthorizationPath(unittest.TestCase):
    """A grep, so this stays true after the phase that removed it. A dead helper left in
    a security module is where the next person reaches for the wrong function."""

    def test_no_module_defines_or_calls_patch_paths(self):
        hits = []
        for py in sorted((REPO / "xcheck").glob("*.py")):
            for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if "patch_paths" in line:
                    hits.append(f"{py.name}:{n}: {line.strip()}")
        print(f"\n[courier-paths] grep patch_paths in xcheck/: {len(hits)} hit(s)")
        for h in hits:
            print(f"  {h}")
        self.assertEqual(hits, [],
                         "the header-text parser is still present in the package")

    def test_the_gate_reads_the_machine_format(self):
        src = (REPO / "xcheck" / "runner.py").read_text(encoding="utf-8")
        self.assertIn('"--name-only", "-z", "--no-renames", "--cached"', src,
                      "the gate no longer asks git for NUL-delimited, rename-free names")
        self.assertIn("changed_paths(self.workdir, self.base)", src,
                      "the read-only gate does not call changed_paths")


if __name__ == "__main__":
    unittest.main()


ledger = xcheck_submodule("ledger")
courier = xcheck_submodule("courier")

EVENT_A = '{"event":"lease_acquired","session_id":"a","ts":"2026-09-01T00:00:00+00:00"}'
EVENT_B = '{"event":"state_transition","session_id":"a","ts":"2026-09-01T00:00:01+00:00"}'
EVENT_C = '{"event":"lease_released","session_id":"a","ts":"2026-09-01T00:00:02+00:00"}'


def repo_with_events(lines=(EVENT_A, EVENT_B)):
    """A repo whose committed `audit/events.jsonl` holds `lines`."""
    tmp = make_repo()
    (tmp / "audit" / "events.jsonl").write_text(
        "".join(ln + "\n" for ln in lines), encoding="utf-8")
    git(tmp, "add", "-A")
    git(tmp, "commit", "-qm", "events")
    return tmp, git(tmp, "rev-parse", "HEAD").stdout.strip()


class TheEventStreamPrefixIsImmutable(unittest.TestCase):
    """PHASE 9 (fourth audit) — this repository's own F-0104, `Event provenance is
    optional and rewriteable`, still `reported` in its corpus. The finding's own
    reproduction:

        `xcheck/courier.py:159-359` applies no append-only authorization for
        `audit/events.jsonl`; a temporary repository changed its committed line from
        `{"event":"original"}` to `{"event":"rewritten"}` and `courier_commit` returned
        True and committed the rewrite.

    and what it asks for:

        run a session that modifies or deletes any existing event byte; the courier must
        refuse and preserve the original prefix, while a genuine suffix append remains
        accepted.

    Those three arms are the three tests below. The status stays `reported`: a status
    transition is human-owned and this run does not rule on its own work."""

    def setUp(self):
        self.tmp, self.head = repo_with_events()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.events = self.tmp / "audit" / "events.jsonl"
        self.pre = courier.dirty_paths(self.tmp)

    def commit(self):
        return courier.courier_commit(self.tmp, "Auditor", "a charter", self.pre,
                                      pre_head=self.head)

    def committed_events(self):
        return git(self.tmp, "show", "HEAD:audit/events.jsonl").stdout

    def test_CONTROL_a_genuine_suffix_append_is_carried_back(self):
        """A courier that refused every session would pass both arms below."""
        with self.events.open("a", encoding="utf-8") as f:
            f.write(EVENT_C + "\n")
        self.assertTrue(self.commit(), "an ordinary append was refused")
        committed = self.committed_events()
        self.assertEqual([EVENT_A, EVENT_B, EVENT_C], committed.splitlines(),
                         "the appended event did not survive the courier")
        print("\n  APPENDED   3 events committed, the 2 existing ones byte-identical")

    def test_a_rewritten_event_line_is_REFUSED_naming_the_offset_and_line(self):
        text = self.events.read_text(encoding="utf-8")
        self.events.write_text(text.replace('"lease_released"', '"x"')
                               .replace('"state_transition"', '"rewritten"'),
                               encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            ok = self.commit()
        out = buf.getvalue()
        self.assertFalse(ok, "the courier committed a rewritten event stream")
        self.assertIn("REWROTE", out)
        self.assertIn("first differing byte is at offset", out)
        self.assertIn("in line 2", out)
        self.assertEqual([EVENT_A, EVENT_B], self.committed_events().splitlines(),
                         "the rewrite reached history")
        print(f"  REWRITE    {out.strip().splitlines()[0][:140]}")
        print(f"             {out.strip().splitlines()[1].strip()[:140]}")

    def test_a_truncated_stream_is_REFUSED_with_a_DIFFERENT_message(self):
        rewrite_out = self._capture_rewrite()
        self.events.write_text(EVENT_A + "\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            ok = self.commit()
        truncate_out = buf.getvalue()
        self.assertFalse(ok)
        self.assertIn("TRUNCATED", truncate_out)
        # Distinct in the way that matters: neither message carries the other's verb or
        # the other's remedy, so an operator reading one is not told to do the other's.
        self.assertNotIn("TRUNCATED", rewrite_out)
        self.assertNotIn("REWROTE", truncate_out)
        self.assertIn("has deleted the record", truncate_out)
        self.assertNotIn("has deleted the record", rewrite_out)
        self.assertEqual([EVENT_A, EVENT_B], self.committed_events().splitlines())
        print(f"  TRUNCATE   {truncate_out.strip().splitlines()[0][:140]}")

    def _capture_rewrite(self):
        text = self.events.read_text(encoding="utf-8")
        self.events.write_text(text.replace('"state_transition"', '"rewritten"'),
                               encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            courier.courier_commit(self.tmp, "Auditor", "c", self.pre, pre_head=self.head)
        self.events.write_text(text, encoding="utf-8")
        return buf.getvalue()

    def test_the_refusal_happens_BEFORE_anything_reaches_history(self):
        """Not applied and then reverted. Reverting evidence is itself an evidence
        event, and a stream that landed and was undone leaves two records of one thing."""
        head_before = git(self.tmp, "rev-parse", "HEAD").stdout.strip()
        text = self.events.read_text(encoding="utf-8")
        self.events.write_text(text.replace('"state_transition"', '"rewritten"'),
                               encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(self.commit())
        self.assertEqual(head_before, git(self.tmp, "rev-parse", "HEAD").stdout.strip(),
                         "the courier made a commit before refusing")
        log = git(self.tmp, "log", "--oneline").stdout.splitlines()
        self.assertEqual(2, len(log), f"history grew: {log}")
        print(f"  UNCHANGED  HEAD still {head_before[:12]}, {len(log)} commit(s)")

    def test_a_stream_that_did_not_exist_before_is_an_append(self):
        """The first session in a project has nothing to preserve, and must not be
        refused for it."""
        tmp = make_repo()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        head = git(tmp, "rev-parse", "HEAD").stdout.strip()
        pre = courier.dirty_paths(tmp)
        (tmp / "audit" / "events.jsonl").write_text(EVENT_A + "\n", encoding="utf-8")
        self.assertTrue(courier.courier_commit(tmp, "Auditor", "c", pre, pre_head=head))
        self.assertEqual([EVENT_A],
                         git(tmp, "show", "HEAD:audit/events.jsonl").stdout.splitlines())

    def test_exactly_one_prefix_digest_function_exists_and_both_callers_use_it(self):
        """Structural, over the AST. Two functions computing stream identity is the next
        version of the `charter` versus `charter_hash` defect this run already fixed —
        and a grep would be satisfied by the name appearing in a docstring."""
        defined, callers = [], {}
        for path in sorted((REPO / "xcheck").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and node.name == "prefix_digest":
                    defined.append(f"{path.stem}:{node.lineno}")
                if isinstance(node, ast.Call):
                    fn = node.func
                    name = (fn.id if isinstance(fn, ast.Name)
                            else fn.attr if isinstance(fn, ast.Attribute) else None)
                    if name == "prefix_digest":
                        callers.setdefault(path.stem, []).append(node.lineno)
        print(f"  ONE DIGEST defined at {defined}; called from {dict(sorted(callers.items()))}")
        self.assertEqual(1, len(defined), f"prefix_digest is defined {len(defined)} times")
        self.assertTrue(defined[0].startswith("ledger:"))
        # The phase-8 reader and the phase-9 prefix check are both in `ledger`, and the
        # courier reaches the rule through `prefix_problem` rather than by computing a
        # second digest of its own — which is the property worth asserting.
        self.assertEqual(["ledger"], sorted(callers))
        courier_src = (REPO / "xcheck" / "courier.py").read_text(encoding="utf-8")
        runner_src = (REPO / "xcheck" / "runner.py").read_text(encoding="utf-8")
        for src, who in ((courier_src, "courier"), (runner_src, "runner")):
            self.assertIn("ledger.prefix_problem(", src,
                          f"{who} does not reach the shared rule")
            self.assertNotIn("hashlib.sha256(before", src)
