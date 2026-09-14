"""The Ouroboros-4 event corpus, frozen, and the baseline counted from it.

Every later ticket in `wayfinder/MAP.md` is proven against this file. It is a verbatim
copy of `audit/events.jsonl` as committed at `3e5103a` — not a projection, because a
projection is a judgement about what later work will need, made before that work exists.
The digest below is the freeze; `audit/events.jsonl` itself keeps growing with every run,
so a test that read it would change its verdict whenever someone audits something.

What the corpus says, and why it is worth 365 KB of repository: 151 sessions were
dispatched and 15 of them made a canonical state transition. The other 136 exited, were
recorded as `ok`, and moved nothing the router can see.

The count here is deliberately independent of the production code. When the receipt
derivation arrives it will compute the same numbers through the orchestrator's own path,
and two implementations agreeing is evidence that one implementation asserting its own
output is not.
"""

import collections
import hashlib
import json
import unittest

from tests.harness import REPO

CORPUS = REPO / "tests" / "fixtures" / "ouroboros-4-events.jsonl"

# `git show 3e5103a:audit/events.jsonl | shasum -a 256`. Pinned rather than recomputed:
# a digest a test derives from the file it is checking asserts nothing about that file.
DIGEST = "c8b6ed55e3fa23984813c1a71006ca670e9a64a73adecefd5d463686ae239f5e"
LINES = 955

# Counted during charting, before any of this existed. The two denominators are both
# real and are the whole of the 135-vs-136 question: 136 sessions were DISPATCHED without
# making a transition, 135 of those also FINISHED. The one difference is the session an
# operator killed mid-flight — dispatched, never finished. Neither number is wrong; a
# claim that omits which denominator it used is.
DISPATCHED = 151
FINISHED = 150
WITH_TRANSITION = 15
DISPATCHED_WITHOUT = 136
FINISHED_WITHOUT = 135
VERBS = {"file-finding": 116, "record-coverage": 15, "queue-pass": 4,
         "repair-body-paths": 1, "repair-p13-artifact-paths": 1}


def read(path=CORPUS):
    """Every event, in order. A line that does not parse is a corrupt corpus, not a
    line to skip — skipping would let a truncated fixture agree with the baseline by
    losing exactly the events that disagree."""
    events = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise AssertionError(f"{path}:{n} is not JSON — the corpus is corrupt "
                                     f"and every number derived from it is void: {e}")
    return events


def census(events):
    """The baseline, from events alone: who was dispatched, who finished, who moved
    canonical state, and by which verb."""
    dispatched, finished = set(), set()
    transitions = collections.defaultdict(list)
    verbs = collections.Counter()
    for e in events:
        kind, sid = e.get("event"), e.get("session_id")
        if kind == "session_dispatched":
            dispatched.add(sid)
        elif kind == "session_finished":
            finished.add(sid)
        elif kind == "state_transition":
            transitions[sid].append(e.get("verb"))
            verbs[e.get("verb")] += 1
    moved = {s for s in dispatched if transitions.get(s)}
    return {
        "dispatched": len(dispatched),
        "finished": len(finished),
        "with_transition": len(moved),
        "dispatched_without": len(dispatched - moved),
        "finished_without": len(finished - moved),
        "verbs": dict(verbs),
        "orphan_transitions": len(set(transitions) - dispatched),
    }


class TheCorpusIsFrozen(unittest.TestCase):
    """A moving corpus is a moving goalpost."""

    def test_the_bytes_are_the_ones_that_were_counted(self):
        self.assertTrue(CORPUS.is_file(), f"{CORPUS} is missing — every ticket that "
                                          f"claims to be proven against it is unproven")
        got = hashlib.sha256(CORPUS.read_bytes()).hexdigest()
        self.assertEqual(DIGEST, got,
                         "the frozen corpus changed. It is a copy of a committed blob and "
                         "nothing should ever edit it; if the freeze is genuinely being "
                         "re-taken, the baseline numbers below have to be re-counted and "
                         "re-argued in the same commit, not adjusted to fit")
        self.assertEqual(LINES, len(CORPUS.read_text(encoding="utf-8").splitlines()))


class TheBaselineIsWhatWasCounted(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.c = census(read())

    def test_the_counts_match_the_charting_measurement(self):
        c = self.c
        print(f"\n  OUROBOROS-4 CORPUS   {CORPUS.name}")
        print(f"    dispatched                {c['dispatched']:5}")
        print(f"    finished                  {c['finished']:5}")
        print(f"    made a transition         {c['with_transition']:5}")
        print(f"    dispatched, no transition {c['dispatched_without']:5}")
        print(f"    finished, no transition   {c['finished_without']:5}")
        for verb, n in sorted(c["verbs"].items(), key=lambda kv: -kv[1]):
            print(f"    verb {verb:26} {n:5}")
        self.assertEqual(DISPATCHED, c["dispatched"])
        self.assertEqual(FINISHED, c["finished"])
        self.assertEqual(WITH_TRANSITION, c["with_transition"])
        self.assertEqual(DISPATCHED_WITHOUT, c["dispatched_without"])
        self.assertEqual(FINISHED_WITHOUT, c["finished_without"])
        self.assertEqual(VERBS, c["verbs"])

    def test_the_two_denominators_account_for_every_session(self):
        """A partition, not two loosely related numbers."""
        c = self.c
        self.assertEqual(c["dispatched"], c["with_transition"] + c["dispatched_without"])
        self.assertEqual(c["finished"], c["with_transition"] + c["finished_without"])
        self.assertEqual(1, c["dispatched"] - c["finished"],
                         "exactly one dispatched session never finished — the killed one")

    def test_no_transition_belongs_to_a_session_nobody_dispatched(self):
        """The join is total. An orphan would mean the key is wrong, and every count
        that uses it would be measuring something else."""
        self.assertEqual(0, self.c["orphan_transitions"])


class TheReaderWouldNotAgreeWithAnEmptyCorpus(unittest.TestCase):
    """CONTROL. Without it, a fixture that failed to load could satisfy nothing and
    still look like agreement — the baseline numbers would simply never be reached."""

    def test_an_empty_corpus_counts_zero(self):
        c = census([])
        self.assertEqual(0, c["dispatched"])
        self.assertEqual(0, c["with_transition"])
        self.assertEqual({}, c["verbs"])
        self.assertNotEqual(DISPATCHED, c["dispatched"],
                            "CONTROL BROKEN: an empty corpus reproduces the baseline, so "
                            "the baseline is not evidence of anything")

    def test_a_corrupt_line_is_refused_rather_than_skipped(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write('{"event":"session_dispatched","session_id":"a"}\nnot json\n')
            path = f.name
        with self.assertRaises(AssertionError):
            read(path)


if __name__ == "__main__":
    unittest.main()
