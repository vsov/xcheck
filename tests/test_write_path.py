"""Completeness before any write: one check, three actions, and a live corpus that still writes.

PHASE 4 (sixth audit), the second blocking finding. `ledger.read_stream()` proves the
chain links up. It does NOT ask whether this is the stream the checkpoint in `state.json`
was taken over — that comparison lived only in `ledger.status()`, the DIAGNOSTIC path. So
the working command missed what the diagnostic saw:

    truncated journal   status `short`    · set-status exits 0 · afterwards `intact`
    replaced+relinked   status `replaced` · set-status exits 0 · afterwards `intact`

In both cases the corrupted journal became the accepted baseline, promoted by the act of
writing over it, with the lost evidence never recovered.

"Internally consistent" and "complete with respect to the stored history" are different
properties. `ledger.require_complete` asks both, and `ledger.FORBIDS` says which actions
each state forbids — the one place where "may I apply a transition?", "may I emit an
event?" and "may I replay a row I reserved?" are answered.
"""

import ast
import json
import time
import unittest

from tests.harness import needs_live_corpus, REPO, Fixture, finding_record, state_doc, xcheck_submodule

envelope = xcheck_submodule("envelope")
ledger = xcheck_submodule("ledger")

#: Every corruption this phase claims to catch, as (name, how to break it). Declared, so
#: the agreement property below is a statement about a SET and not about whichever cases
#: happen to have tests.
CORRUPTIONS = ("truncated", "replaced", "broken", "unreadable")


def relink(path, edit):
    """Rewrite the stream with `edit` applied and every link recomputed.

    The result is internally PERFECT and is not the history: this is what an editor who
    knows the format produces, and the only thing that refuses it is the digest recorded
    outside the file.
    """
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    edit(lines)
    out, prev = [], ledger.prefix_digest([])
    for ln in lines:
        ln.pop("prev", None)
        ln.pop("v", None)
        chained = ledger.chained(ln, prev)
        out.append(json.dumps(chained, sort_keys=True, separators=(",", ":")))
        prev = ledger.link(prev, json.dumps(ledger._unchained(chained), sort_keys=True,
                                            separators=(",", ":")))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


class WritePathCase(unittest.TestCase):

    def project(self):
        fx = Fixture(doc=state_doc(findings=[finding_record(f"F-000{n}") for n in (1, 2, 3)]))
        self.addCleanup(fx.cleanup)
        fx.git_init()
        for fid in ("F-0001", "F-0002"):
            code, out = fx.run("set-status", fid, "accepted")
            self.assertEqual(0, code, out)
        return fx

    def stream(self, fx):
        return fx.audit / envelope.EVENTS_FILENAME

    def anchor(self, fx):
        return json.loads((fx.audit / "state.json").read_text(encoding="utf-8")) \
            .get("ledger_anchor")

    def corrupt(self, fx, how):
        path = self.stream(fx)
        if how == "truncated":
            # BELOW the count the anchor recorded. The anchor is taken before the
            # transition's own event, so a file one line short of the file still matches
            # it — cutting "the tail" is not automatically a lost tail.
            keep = self.anchor(fx)["events"] - 1
            path.write_text("\n".join(path.read_text().splitlines()[:keep]) + "\n",
                            encoding="utf-8")
        elif how == "replaced":
            relink(path, lambda lines: lines[0].update(summary="an early record, edited"))
        elif how == "broken":
            # A MIDDLE line, not the last one. Editing the last event is undetectable by
            # both halves of this check and `test_the_unprotected_tail_is_named` below
            # measures exactly that — an edit that nothing points at and that the
            # checkpoint was taken before is not something either the chain or the anchor
            # can see. A fixture that used it would have been a corruption in name only.
            lines = path.read_text(encoding="utf-8").splitlines()
            i = max(0, len(lines) - 2)
            doc = json.loads(lines[i])
            doc["summary"] = "edited, and the links NOT recomputed"
            lines[i] = json.dumps(doc, sort_keys=True, separators=(",", ":"))
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        elif how == "unreadable":
            path.write_text("{not json at all\n", encoding="utf-8")
        else:
            raise AssertionError(f"undeclared corruption {how!r}")


class TheOrdinaryWriteRefuses(WritePathCase):
    """The audit's own reproduction, both halves, before and after."""

    def test_a_truncated_journal_refuses_the_write_and_names_short(self):
        fx = self.project()
        self.corrupt(fx, "truncated")
        before = self.anchor(fx)
        state, _ = ledger.status(fx.root, before)
        code, out = fx.run("set-status", "F-0003", "accepted")
        after = self.anchor(fx)
        state_after, _ = ledger.status(fx.root, after)
        print(f"\n  TRUNCATED  before={state!r} · exit={code} · anchor events "
              f"{before['events']} -> {after['events']} · after={state_after!r}")
        self.assertEqual(ledger.SHORT, state)
        self.assertNotEqual(0, code, "an ordinary write proceeded over a short journal")
        self.assertIn("short", out)
        self.assertEqual(before, after, "the checkpoint was advanced by a refused write")
        self.assertEqual(ledger.SHORT, state_after,
                         "the refusal changed the diagnosis it refused on")

    def test_a_replaced_journal_refuses_the_write_and_names_replaced(self):
        fx = self.project()
        self.corrupt(fx, "replaced")
        before = self.anchor(fx)
        state, _ = ledger.status(fx.root, before)
        code, out = fx.run("set-status", "F-0003", "accepted")
        after = self.anchor(fx)
        print(f"  REPLACED   before={state!r} · exit={code} · anchor events "
              f"{before['events']} -> {after['events']} · after="
              f"{ledger.status(fx.root, after)[0]!r}")
        self.assertEqual(ledger.REPLACED, state)
        self.assertNotEqual(0, code, "an ordinary write proceeded over a rebuilt journal")
        self.assertIn("replaced", out)
        self.assertEqual(before, after, "the checkpoint was advanced by a refused write")

    def test_the_status_of_the_finding_did_not_move_either(self):
        """A refusal that left the transition applied would be worse than the defect."""
        fx = self.project()
        self.corrupt(fx, "truncated")
        code, _out = fx.run("set-status", "F-0003", "accepted")
        doc = json.loads((fx.audit / "state.json").read_text(encoding="utf-8"))
        status = [f["status"] for f in doc["findings"] if f["id"] == "F-0003"][0]
        print(f"  UNCHANGED  exit={code} · F-0003 is {status!r} · revision "
              f"{doc['state_revision']}")
        self.assertNotEqual(0, code)
        self.assertEqual("reported", status)


@needs_live_corpus
class TheLiveCorpusStillWrites(unittest.TestCase):
    """The criterion that stops the fix from bricking the tool on its own audit.

    This project's 1,043 historical events carry NO anchor. "No checkpoint recorded yet"
    and "the checkpoint disagrees" are different answers and only the second may refuse.
    """

    def test_a_real_write_verb_succeeds_against_the_real_stream(self):
        fx = Fixture(doc=state_doc(findings=[finding_record("F-0001")]))
        self.addCleanup(fx.cleanup)
        fx.git_init()
        # A COPY of the real 1,043-event stream, and no anchor — the live shape exactly.
        import shutil
        shutil.copy(REPO / "audit" / envelope.EVENTS_FILENAME,
                    fx.audit / envelope.EVENTS_FILENAME)
        doc = json.loads((fx.audit / "state.json").read_text(encoding="utf-8"))
        doc.pop("ledger_anchor", None)
        (fx.audit / "state.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")

        anchor = ledger.committed_anchor(fx.root)
        state, _ = ledger.status(fx.root, anchor)
        code, out = fx.run("set-status", "F-0001", "accepted")
        print(f"\n  LIVE SHAPE {len(ledger._read_lines(fx.root))} historical event(s), "
              f"anchor={anchor} · status={state!r} · write exit={code}")
        self.assertIsNone(anchor, "the fixture does not have the live corpus's shape")
        self.assertEqual(0, code, out)
        self.assertEqual(ledger.INTACT, state)

    def test_the_real_audit_directory_is_writable_right_now(self):
        """Against `audit/` itself, not a copy: `require_complete` is asked the question
        an ordinary verb would ask, on the live corpus, and must not refuse."""
        anchor = ledger.committed_anchor(REPO)
        answer = ledger.require_complete(REPO, anchor, action="write")
        print(f"  REAL AUDIT anchor={anchor} · require_complete(write) -> {answer!r} · "
              f"{len(ledger._read_lines(REPO))} event(s)")
        self.assertEqual(ledger.INTACT, answer)


class OneCheckThreeConsumers(unittest.TestCase):
    """Structural. The check is a function, and the paths that write call it."""

    #: (module, function, action) — the declared consumers. A path that appends or
    #: advances the checkpoint and is not here is the defect this phase closed.
    CONSUMERS = (("write", "_apply", "write"),
                 ("ledger", "replay", "replay"),
                 ("envelope", "emit", "emit"))

    def call_in(self, module, function):
        src = (REPO / "xcheck" / f"{module}.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == function)
        return [c for c in ast.walk(fn) if isinstance(c, ast.Call)
                and getattr(c.func, "attr", getattr(c.func, "id", "")) == "require_complete"]

    def test_each_consumer_calls_the_one_check_with_its_action(self):
        seen = {}
        for module, function, action in self.CONSUMERS:
            calls = self.call_in(module, function)
            actions = [k.value.value for c in calls for k in c.keywords if k.arg == "action"]
            seen[f"{module}.{function}"] = actions
            self.assertEqual([action], actions,
                             f"{module}.{function} does not ask the check for {action!r}")
        print(f"\n  CONSUMERS  {seen}")

    def test_COUNTERFACTUAL_a_fourth_caller_that_skips_it_is_caught(self):
        """The gate is worth having only if it reddens. `write._apply` with its call
        removed is the code this phase replaced."""
        src = (REPO / "xcheck" / "write.py").read_text(encoding="utf-8")
        mutant = src.replace('ledger.require_complete(project, ledger.committed_anchor(project),\n'
                             '                                        action="write")',
                             'ledger.read_stream(project)')
        self.assertNotEqual(src, mutant, "the mutation did not apply")
        fn = next(n for n in ast.walk(ast.parse(mutant))
                  if isinstance(n, ast.FunctionDef) and n.name == "_apply")
        calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)
                 and getattr(c.func, "attr", "") == "require_complete"]
        print(f"  MUTANT     write._apply asks the check {len(calls)} time(s) "
              f"(the gate requires 1)")
        self.assertEqual([], calls)

    def test_the_action_vocabulary_is_closed(self):
        """Every action a consumer asks for is a column of the table. An action the table
        does not know would be forbidden by nothing at all."""
        actions = {a for _m, _f, a in self.CONSUMERS}
        known = {a for forbidden in ledger.FORBIDS.values() for a in forbidden}
        print(f"  VOCABULARY consumers ask {sorted(actions)}; the table forbids "
              f"{sorted(known)}")
        self.assertEqual(set(), actions - known,
                         "a consumer asks for an action no state forbids")
        self.assertEqual(set(ledger.FORBIDS), {ledger.INTACT, ledger.BROKEN, ledger.SHORT,
                                               ledger.REPLACED, ledger.UNREADABLE},
                         "a ledger state has no row in the table")


class TheDiagnosticAndTheWorkingCommandAgree(WritePathCase):
    """The property, over the declared set of corruptions rather than case by case."""

    def test_every_corruption_that_the_diagnostic_refuses_the_write_refuses_too(self):
        rows = []
        for how in CORRUPTIONS:
            fx = self.project()
            self.corrupt(fx, how)
            state, _ = ledger.status(fx.root, self.anchor(fx))
            code, _out = fx.run("set-status", "F-0003", "accepted")
            rows.append((how, state, code))
        print("\n  AGREEMENT  corruption   diagnostic   ordinary write")
        for how, state, code in rows:
            print(f"             {how:12s} {state:12s} exit={code}")
        for how, state, code in rows:
            self.assertNotEqual(ledger.INTACT, state, f"{how} was not a corruption at all")
            self.assertNotEqual(0, code, f"the write proceeded over a {state} stream ({how})")

    def test_CONTROL_an_intact_journal_writes_exactly_as_before(self):
        """The arm that stops the fix from becoming a new refusal. An uncorrupted stream
        takes the same transition it always did."""
        fx = self.project()
        before = json.loads((fx.audit / "state.json").read_text(encoding="utf-8"))
        code, out = fx.run("set-status", "F-0003", "accepted")
        after = json.loads((fx.audit / "state.json").read_text(encoding="utf-8"))
        moved = [(f["id"], f["status"]) for f in after["findings"]]
        print(f"  CONTROL    exit={code} · revision {before['state_revision']} -> "
              f"{after['state_revision']} · {moved}")
        self.assertEqual(0, code, out)
        self.assertEqual(before["state_revision"] + 1, after["state_revision"])
        self.assertIn(("F-0003", "accepted"), moved)


class ACheckThatCannotJudgeBlocks(WritePathCase):
    """A gate that cannot determine completeness must say so by name, not pass."""

    def test_an_unreadable_stream_is_named_and_blocks(self):
        fx = self.project()
        self.corrupt(fx, "unreadable")
        state, _ = ledger.status(fx.root, self.anchor(fx))
        code, out = fx.run("set-status", "F-0003", "accepted")
        print(f"\n  NO VERDICT status={state!r} · exit={code} · the refusal names it: "
              f"{ledger.UNREADABLE in out}")
        self.assertEqual(ledger.UNREADABLE, state)
        self.assertNotEqual(0, code)
        self.assertIn(ledger.UNREADABLE, out)

    def test_the_refusal_prints_the_move_for_the_state_it_found(self):
        """It used to print `RECOVERABLE[BROKEN]` whatever the state was, so an operator
        with an unreadable file was told to restore from the commit before the edit."""
        fx = self.project()
        self.corrupt(fx, "unreadable")
        _code, out = fx.run("set-status", "F-0003", "accepted")
        print(f"  GUIDANCE   {ledger.RECOVERABLE[ledger.UNREADABLE][:70]}…")
        self.assertIn(ledger.RECOVERABLE[ledger.UNREADABLE], out)
        self.assertNotIn(ledger.RECOVERABLE[ledger.BROKEN], out)


class RecoverExitCodes(WritePathCase):
    """A closed set of two, each exercised, each printed beside its meaning."""

    def test_short_with_unrecovered_events_is_not_zero(self):
        fx = self.project()
        self.corrupt(fx, "truncated")
        code, out = fx.run("recover", "--apply")
        state, _ = ledger.status(fx.root, self.anchor(fx))
        print(f"\n  RECOVER    short, events still missing -> exit={code} "
              f"({ledger.EXIT_CODES.get(code)})")
        self.assertEqual(ledger.SHORT, state)
        self.assertEqual(1, code, "a short stream reported success")
        self.assertIn("short", out)

    def test_intact_is_zero(self):
        fx = self.project()
        code, _out = fx.run("recover", "--apply")
        print(f"  RECOVER    intact -> exit={code} ({ledger.EXIT_CODES.get(code)})")
        self.assertEqual(0, code)

    def test_every_code_in_the_closed_set_was_exercised(self):
        seen = {}
        fx = self.project()
        seen[fx.run("recover")[0]] = "intact"
        fx2 = self.project()
        self.corrupt(fx2, "broken")
        seen[fx2.run("recover")[0]] = "broken"
        print(f"  CODES      {dict(sorted(seen.items()))} · declared "
              f"{sorted(ledger.EXIT_CODES)}")
        self.assertEqual(set(ledger.EXIT_CODES), set(seen),
                         "a declared exit code was never produced")


class AShortFileCannotProveItIsAPrefix(WritePathCase):
    """Both readings, named. The anchor holds ONE digest at ONE point, not a digest per
    line, so nothing recorded can say whether what survives is a prefix of what was
    recorded or a shorter stream somebody built. The refusal must not pick one."""

    def test_the_refusal_names_both_readings(self):
        fx = self.project()
        self.corrupt(fx, "truncated")
        _state, detail = ledger.status(fx.root, self.anchor(fx))
        print(f"\n  TWO READINGS {detail.splitlines()[0][-90:]}")
        move = ledger.RECOVERABLE[ledger.SHORT]
        self.assertIn("OR was never written at all if the file", move)
        self.assertIn("written by a process that is gone", move)

    def test_what_separates_them_is_named(self):
        """The evidence that WOULD separate them: the outbox holds rows this tool
        reserved and can prove it wrote, so those are reconstructable; anything beyond
        them is not, and the message says so rather than guessing."""
        move = ledger.RECOVERABLE[ledger.SHORT]
        print(f"  SEPARATOR  {move[:88]}…")
        self.assertIn("outbox", move)


class TheLimitOfWhatThisCheckCanSee(WritePathCase):
    """Measured while building this phase, and reported rather than fixed quietly.

    The chain proves each event records the digest of everything before it, so an edit is
    caught by the event that FOLLOWS it. The anchor is taken BEFORE the transition's own
    event is appended. Between them they leave a window: the last event in the file has no
    successor pointing at it and was written after the checkpoint was computed, so editing
    it is invisible to both — until the next write, which appends a successor and records
    a new checkpoint over it.

    This is a property of the design, not a defect introduced here, and it is written down
    because a check whose limits are undocumented gets read as covering everything.
    """

    def test_the_unprotected_tail_is_named(self):
        fx = self.project()
        path = self.stream(fx)
        lines = path.read_text(encoding="utf-8").splitlines()
        doc = json.loads(lines[-1])
        doc["summary"] = "the last event, edited, with no successor to notice"
        lines[-1] = json.dumps(doc, sort_keys=True, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        state, _ = ledger.status(fx.root, self.anchor(fx))
        anchor_events = self.anchor(fx)["events"]
        print(f"\n  TAIL WINDOW the last of {len(lines)} events edited; the checkpoint "
              f"covers {anchor_events} · status={state!r}")
        self.assertEqual(ledger.INTACT, state,
                         "if this now refuses, the window has been closed and this test "
                         "should say so instead of recording the limit")
        self.assertLess(anchor_events, len(lines),
                        "the checkpoint is not behind the file, so the window this test "
                        "describes does not exist as described")

    def test_the_window_closes_on_the_next_write(self):
        """Not permanent: one more transition appends a successor and records a new
        checkpoint, after which the same edit is caught."""
        fx = self.project()
        path = self.stream(fx)
        code, out = fx.run("set-status", "F-0003", "accepted")
        self.assertEqual(0, code, out)
        lines = path.read_text(encoding="utf-8").splitlines()
        target = len(lines) - self.anchor(fx)["events"]
        doc = json.loads(lines[0])
        doc["summary"] = "an event the checkpoint now covers"
        lines[0] = json.dumps(doc, sort_keys=True, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        state, _ = ledger.status(fx.root, self.anchor(fx))
        print(f"  TAIL CLOSED after one more write the checkpoint covers all but "
              f"{target} event(s); the same edit is now {state!r}")
        self.assertEqual(ledger.BROKEN, state)


class WhatTheCheckCosts(unittest.TestCase):
    """The hot path measured, not assumed."""

    RUNS = 20

    def measure(self, fn):
        fn()                                    # warm the cache the same way a run does
        start = time.perf_counter()
        for _ in range(self.RUNS):
            fn()
        return (time.perf_counter() - start) / self.RUNS * 1000

    def test_the_completeness_check_against_the_chain_check_alone(self):
        anchor = ledger.committed_anchor(REPO)
        chain_only = self.measure(lambda: ledger.read_stream(REPO))
        complete = self.measure(lambda: ledger.require_complete(REPO, anchor, action="write"))
        print(f"\n  COST       read_stream {chain_only:.2f} ms · require_complete "
              f"{complete:.2f} ms · ratio {complete / max(chain_only, 1e-9):.1f}x "
              f"over {len(ledger._read_lines(REPO))} events, {self.RUNS} runs")
        # No threshold asserted: the number is the evidence, and a timing threshold in a
        # test suite is a flake on a loaded machine. What IS asserted is that the check
        # does not re-read the stream a linear number of times per event.
        self.assertLess(complete, chain_only * 200 + 500,
                        "the completeness check is not in the same order as the read")

    def test_the_cache_key_is_enumerated(self):
        """`read_stream` caches a validated stream. A cache key is a list of ways to be
        wrong, so the components are read out of the code rather than trusted."""
        src = (REPO / "xcheck" / "ledger.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == "read_identity")
        fields = sorted({n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)})
        print(f"  CACHE KEY  read_identity is built from {fields}")
        self.assertTrue(fields, "the cache key could not be read out of the source")
        # And the anchor is NOT part of it: the cached half is the chain, which is a
        # property of the file alone. The checkpoint comparison is re-done every call,
        # which is why a stale anchor cannot be served from the cache.
        anchor_reads = [n for n in ast.walk(fn) if isinstance(n, ast.Constant)
                        and n.value == "ledger_anchor"]
        self.assertEqual([], anchor_reads, "the checkpoint leaked into the chain cache")


if __name__ == "__main__":
    unittest.main()
