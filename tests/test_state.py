"""`xcheck/state.py` — the schema, the one reading boundary, the atomic write.

The green condition here is the same as everywhere else in `tests/`: a test is
green only when the REAL public entry point (`load_state`, `write_state`)
produced the right observable outcome — the addressed message, the bytes on
disk, the exception — never when a private predicate returned the right value in
isolation. `_validate` is never called directly by a negative test; every one of
them goes through `load_state` on a real file in a real directory.

The negative table below is the phase-4 evidence artifact. Each row is
(case name, mutation, expected substring of the addressed message) and covers
one of the five strictness directions: unknown keys, missing required keys,
duplicates, enums/formats, cross-record.
"""

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.harness import xcheck_submodule

_CKEY = xcheck_submodule("util").construal_key("Remediator", "findings F-0002")

S = xcheck_submodule("state")


# ---------------------------------------------------------------------------
# one valid document, mutated per case
# ---------------------------------------------------------------------------

def doc():
    """A complete, valid state document exercising every record type."""
    return {
        "schema_version": 1,
        "state_revision": 3,
        "generated_by": "xcheck 0.8.0",
        "head_before": "9b02868",
        "findings": [
            {"id": "F-0001", "title": "первая находка", "severity": "major",
             "status": "reported", "dimension": "methodology", "unit": ["U01"],
             "pass": "P-01", "attempts": 0, "updated": "2026-08-14",
             "body_path": "findings/F-0001-first.md"},
            {"id": "F-0002", "title": "вторая", "severity": "minor",
             "status": "fixed", "next": "Verifier", "dimension": "mechanics",
             "unit": ["U01", "U02"], "pass": "P-01", "attempts": 1,
             "updated": "2026-08-14", "body_path": "findings/F-0002-second.md",
             "fixed_by": "0123456789abcdef", "recurrence_of": "F-0001",
             "class": "CF-0001"},
        ],
        "class_findings": [
            {"id": "CF-0001", "title": "класс", "severity": "critical",
             "status": "accepted", "dimension": "methodology", "unit": ["U01"],
             "pass": "P-01", "attempts": 0, "updated": "2026-08-14",
             "body_path": "findings/CF-0001-class.md", "members": ["F-0002"]},
        ],
        "queue": [
            {"id": "P-01", "dimension": "methodology", "units": ["U01", "U02"],
             "charter": "прочитать §1–§5", "stop": "общий", "done": True,
             "coverage": {"report_path": "passes/P-01.md",
                          "findings": ["F-0001", "F-0002"],
                          "updated": "2026-08-14", "status": "done"}},
            {"id": "P-02", "dimension": "mechanics", "units": ["U02"],
             "charter": "прочитать код", "stop": "общий", "done": False},
        ],
        "plans": [
            {"id": "RP-0001", "findings": ["F-0002"], "status": "done",
             "updated": "2026-08-14", "body_path": "plans/RP-0001.md",
             "attempts": 1},
        ],
        # PHASE 5: the key is DERIVED, never chosen — it is the (role, charter)
        # fingerprint (§4 rule 9), and the schema now checks the record against its
        # own charter. A literal key here would be a fixture asserting a hash.
        "construals": [
            {"key": _CKEY, "role": "Remediator",
             "charter": "findings F-0002", "session": "fedcba9876543210",
             "status": "admitted", "created": "2026-08-14",
             "body_path": f"construals/{_CKEY}.md",
             "admitted_by": "human:victor", "admitted_at": "2026-08-14"},
        ],
        "sessions": [],
        # `triage_batch_cap` is deliberately absent: it is an orchestrator.conf
        # key, not a state limit, and the schema now refuses it here (phase 4).
        "limits": {"reopen_limit": 2, "remediation_batch_size": 5,
                   "class_threshold": 3, "max_findings_per_pass": 12},
        "catalogs": {
            "norms": [{"id": "N1", "source": "XCHECK.md §4", "scope": "механика"},
                      {"id": "N2", "source": "README.md", "scope": "stdlib only"}],
            "dimensions": [{"key": "methodology", "catches": "нормативные дефекты",
                            "norms": ["N1"]},
                           {"key": "mechanics", "catches": "дефекты кода",
                            "norms": ["N1", "N2"]}],
            "units": [{"id": "U01", "material": "XCHECK.md", "size": "269 lines",
                       "responsibility": "канон"},
                      {"id": "U02", "material": "xcheck/", "size": "5k lines",
                       "responsibility": "оркестратор"}],
        },
    }


def mutate(fn):
    d = doc()
    fn(d)
    return d


# name -> (mutation, expected substring). Ordered by strictness direction.
CASES = {
    # --- 1. unknown keys, at every depth -----------------------------------
    "unknown top-level key":
        (lambda d: d.update(notes="hi"), "unknown top-level key"),
    "unknown finding field":
        (lambda d: d["findings"][0].update(owner="me"), "unknown field(s) 'owner'"),
    "unknown class-finding field":
        (lambda d: d["class_findings"][0].update(census="done"), "unknown field(s) 'census'"),
    "unknown queue field":
        (lambda d: d["queue"][0].update(priority=1), "unknown field(s) 'priority'"),
    "unknown coverage field":
        (lambda d: d["queue"][0]["coverage"].update(notes="x"), "unknown field(s) 'notes'"),
    "unknown construal field":
        (lambda d: d["construals"][0].update(scope="wide"), "unknown field(s) 'scope'"),
    "unknown plan field":
        (lambda d: d["plans"][0].update(owner="x"), "unknown field(s) 'owner'"),
    "unknown catalog group":
        (lambda d: d["catalogs"].update(roles=[]), "unknown key(s) 'roles'"),
    "unknown limit":
        (lambda d: d["limits"].update(retry_limit=9), "unknown limit(s) 'retry_limit'"),

    # --- 2. missing required keys ------------------------------------------
    "missing schema_version":
        (lambda d: d.pop("schema_version"), "missing required top-level key 'schema_version'"),
    "missing finding status":
        (lambda d: d["findings"][0].pop("status"), "missing required field(s) 'status'"),
    "missing finding body_path":
        (lambda d: d["findings"][0].pop("body_path"), "missing required field(s) 'body_path'"),
    "missing class members":
        (lambda d: d["class_findings"][0].pop("members"), "missing required field(s) 'members'"),
    "empty class members":
        (lambda d: d["class_findings"][0].update(members=[]), "expected a non-empty list"),
    "missing queue stop condition":
        (lambda d: d["queue"][0].pop("stop"), "missing required field(s) 'stop'"),
    "missing construal session":
        (lambda d: d["construals"][0].pop("session"), "missing required field(s) 'session'"),
    "missing plan status":
        (lambda d: d["plans"][0].pop("status"), "missing required field(s) 'status'"),

    # --- 3. duplicates ------------------------------------------------------
    "duplicate finding id":
        (lambda d: d["findings"].append(dict(d["findings"][0])), "is declared twice"),
    "duplicate class-finding id":
        (lambda d: d["class_findings"].append(dict(d["class_findings"][0])),
         "is declared twice"),
    "a class-finding id used as a finding id":
        (lambda d: d["findings"][0].update(id="CF-0002"), "is not a canonical finding id"),
    "a finding id used as a class-finding id":
        (lambda d: d["class_findings"][0].update(id="F-0003"),
         "is not a canonical class-finding id"),
    "duplicate pass id":
        (lambda d: d["queue"].append(dict(d["queue"][1], id="P-01")), "is declared twice"),
    "duplicate plan id":
        (lambda d: d["plans"].append(dict(d["plans"][0])), "is declared twice"),
    "duplicate construal key":
        (lambda d: d["construals"].append(dict(d["construals"][0])), "is declared twice"),
    "duplicate norm id":
        (lambda d: d["catalogs"]["norms"].append(dict(d["catalogs"]["norms"][0])),
         "is declared twice"),
    "duplicate dimension key":
        (lambda d: d["catalogs"]["dimensions"].append(dict(d["catalogs"]["dimensions"][0])),
         "is declared twice"),
    "duplicate unit id":
        (lambda d: d["catalogs"]["units"].append(dict(d["catalogs"]["units"][0])),
         "is declared twice"),
    "duplicate member in one class":
        (lambda d: d["class_findings"][0].update(members=["F-0002", "F-0002"]),
         "listed twice"),

    # --- 4. enums and formats ----------------------------------------------
    "status outside the vocabulary":
        (lambda d: d["findings"][0].update(status="almost-done"),
         "is not a canonical §5 status"),
    "severity outside the vocabulary":
        (lambda d: d["findings"][0].update(severity="blocker"),
         "is not a canonical §2 severity"),
    "next outside the vocabulary":
        (lambda d: d["findings"][1].update(next="Nobody"),
         "is not a canonical `next` actor"),
    "refusal outside the six-value vocabulary":
        (lambda d: d["findings"][0].update(refusal="too-hard"),
         "is not a canonical §5 refusal reason"),
    "construal status outside the vocabulary":
        (lambda d: d["construals"][0].update(status="pending"),
         "is not a canonical construal status"),
    "fixed_by not canonical session id":
        (lambda d: d["findings"][1].update(fixed_by="claude-remediator-P28"),
         "is not a canonical 16-lowercase-hex session id"),
    "finding id malformed":
        (lambda d: d["findings"][0].update(id="F-1"), "is not a canonical finding id"),
    "pass id malformed":
        (lambda d: d["queue"][0].update(id="pass-1"), "is not a canonical pass id"),
    "shaped-but-impossible date":
        (lambda d: d["findings"][0].update(updated="2026-02-30"),
         "is not an existing calendar date"),
    "date not YYYY-MM-DD":
        (lambda d: d["findings"][0].update(updated="14 Aug 2026"),
         "is not a YYYY-MM-DD date"),
    "attempts negative":
        (lambda d: d["findings"][0].update(attempts=-1),
         "expected a non-negative integer"),
    "attempts as a string":
        (lambda d: d["findings"][0].update(attempts="1"),
         "expected a non-negative integer"),
    "done flag not boolean":
        (lambda d: d["queue"][1].update(done="false"), "expected true or false"),
    "limit outside its range":
        (lambda d: d["limits"].update(reopen_limit=999),
         "is outside the legal range 1..99"),
    "schema version from another generation":
        (lambda d: d.update(schema_version=2), "run `xcheck migrate`"),
    "admitter neither human nor envelope nor session":
        (lambda d: d["construals"][0].update(admitted_by="the-team"),
         "is not an admitter"),
    "empty title":
        (lambda d: d["findings"][0].update(title="   "),
         "expected a non-empty string"),

    # --- 5. cross-record ----------------------------------------------------
    "class member does not exist":
        (lambda d: d["class_findings"][0].update(members=["F-9999"]),
         "F-9999 does not exist"),
    "class lists itself as a member":
        (lambda d: d["class_findings"][0].update(members=["CF-0001"]),
         "lists itself"),
    "finding's pass is not in the queue":
        (lambda d: d["findings"][0].update(**{"pass": "P-99"}),
         "is not a pass in the queue"),
    "finding's dimension is not in the catalog":
        (lambda d: d["findings"][0].update(dimension="vibes"),
         "is not in the dimension catalog"),
    "finding's unit is not in the unit map":
        (lambda d: d["findings"][0].update(unit=["U99"]), "is not in the unit map"),
    "recurrence_of resolves to nothing":
        (lambda d: d["findings"][1].update(recurrence_of="F-0404"),
         "does not exist in this ledger"),
    "recurrence_of is the record itself":
        (lambda d: d["findings"][1].update(recurrence_of="F-0002"),
         "is the record itself"),
    "class pointer resolves to nothing":
        (lambda d: d["findings"][1].update(**{"class": "CF-0099"}),
         "is not a class finding in this state"),
    "next does not own the status":
        (lambda d: d["findings"][1].update(next="Triage"),
         "does not own status 'fixed'"),
    "plan references a finding that does not exist":
        (lambda d: d["plans"][0].update(findings=["F-0777"]), "F-0777 does not exist"),
    "coverage reports a finding that does not exist":
        (lambda d: d["queue"][0]["coverage"].update(findings=["F-0555"]),
         "does not exist in this state"),
    "done pass with no coverage record":
        (lambda d: d["queue"][0].pop("coverage"), "counts as not done at all"),
    "construal admitted by its own session":
        (lambda d: d["construals"][0].update(admitted_by="fedcba9876543210"),
         "may not admit its own construal"),
    "dimension cites a norm that is not in the catalog":
        (lambda d: d["catalogs"]["dimensions"][0].update(norms=["N7"]),
         "is not in the norms catalog"),
}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="xcheck-state-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def put(self, d):
        """Write a document as bytes — no State object involved."""
        (self.dir / "state.json").write_text(
            json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")

    def load(self):
        return S.load_state(self.dir)


class ValidDocument(Base):
    def test_the_fixture_itself_loads(self):
        """The negative table means nothing if the base document is not valid.

        This is the control for all 55 negative cases: without it, every one of
        them could be passing on a defect the mutation did not introduce.
        """
        self.put(doc())
        st = self.load()
        self.assertEqual(st.schema_version, 1)
        self.assertEqual([f.id for f in st.findings], ["F-0001", "F-0002"])
        self.assertEqual(st.class_findings[0].members, ("F-0002",))
        self.assertEqual(st.queue[0].coverage.report_path, "passes/P-01.md")
        self.assertEqual(st.plans[0].id, "RP-0001")
        self.assertEqual(st.construals[0].role, "Remediator")
        self.assertEqual(st.limits["reopen_limit"], 2)
        self.assertEqual(len(st.catalogs.units), 2)

    def test_state_is_immutable_and_uses_tuples(self):
        self.put(doc())
        st = self.load()
        self.assertIsInstance(st.findings, tuple)
        self.assertIsInstance(st.findings[0].unit, tuple)
        self.assertIsInstance(st.class_findings[0].members, tuple)
        with self.assertRaises(Exception):
            st.findings[0].status = "closed"
        with self.assertRaises(Exception):
            st.state_revision = 99

    def test_next_is_derived_when_not_stored(self):
        """`next` is an override, not a duplicated column (see state.py)."""
        self.put(doc())
        st = self.load()
        reported, fixed = st.findings
        self.assertIsNone(reported.next)
        self.assertEqual(S.next_owner(reported), "Triage")
        self.assertEqual(fixed.next, "Verifier")
        self.assertEqual(S.next_owner(fixed), "Verifier")

    def test_missing_file_is_addressed_not_a_traceback(self):
        with self.assertRaises(S.StateError) as cm:
            self.load()
        self.assertIn("no state file", str(cm.exception))
        self.assertIn("xcheck migrate", str(cm.exception))

    def test_unparseable_json_names_line_and_column(self):
        (self.dir / "state.json").write_text('{"schema_version": 1,,}', encoding="utf-8")
        with self.assertRaises(S.StateError) as cm:
            self.load()
        self.assertIn("not valid JSON at line", str(cm.exception))

    def test_top_level_array_is_refused(self):
        self.put([])
        with self.assertRaises(S.StateError) as cm:
            self.load()
        self.assertIn("expected a JSON object at the top level", str(cm.exception))


class NegativeSchema(Base):
    """The 25+ negative cases, generated from CASES so the table IS the test."""

    def test_every_case_raises_its_addressed_error(self):
        rows = []
        for name, (fn, expected) in CASES.items():
            with self.subTest(case=name):
                self.put(mutate(fn))
                with self.assertRaises(S.StateError) as cm:
                    self.load()
                msg = str(cm.exception)
                self.assertIn(expected, msg,
                              f"{name}: message did not contain {expected!r}\ngot: {msg}")
                rows.append((name, msg))
        self.assertGreaterEqual(len(rows), 25)
        _write_case_table(rows)

    def test_no_State_is_constructed_when_validation_fails(self):
        """Not "the error is raised" — "nothing was built before it was raised".

        A validator that constructs first and checks after can hand a consumer a
        live object on a bad document if any later step is skipped or caught. The
        only way to know construction never happens is to count constructions, so
        this replaces the real `State` with a recorder for the duration.
        """
        calls = []
        real = S.State

        class Recording(real):
            def __init__(self, *a, **kw):
                calls.append(kw or a)
                super().__init__(*a, **kw)

        S.State = Recording
        self.addCleanup(setattr, S, "State", real)

        for name, (fn, _) in CASES.items():
            self.put(mutate(fn))
            with self.assertRaises(S.StateError):
                self.load()
            self.assertEqual(calls, [], f"{name}: a State was constructed on a bad document")

        self.put(doc())
        self.load()
        self.assertEqual(len(calls), 1, "the valid document must construct exactly one State")


class AtomicWrite(Base):
    def state(self, **kw):
        self.put(doc())
        st = self.load()
        return st

    def test_round_trip_is_byte_identical(self):
        st = self.state()
        S.write_state(self.dir, st, bump=False)
        first = (self.dir / "state.json").read_bytes()
        again = S.load_state(self.dir)
        S.write_state(self.dir, again, bump=False)
        second = (self.dir / "state.json").read_bytes()
        a, b = hashlib.sha256(first).hexdigest(), hashlib.sha256(second).hexdigest()
        print(f"\nround-trip sha256 before: {a}\nround-trip sha256 after : {b}")
        self.assertEqual(a, b)

    def test_written_file_is_utf8_not_escaped(self):
        st = self.state()
        S.write_state(self.dir, st)
        text = (self.dir / "state.json").read_text(encoding="utf-8")
        self.assertIn("первая находка", text)
        self.assertNotIn("\\u043f", text)
        self.assertTrue(text.endswith("\n"))

    def test_revision_is_monotonic_across_three_writes(self):
        st = self.state()
        seen = [st.state_revision]
        for _ in range(3):
            S.write_state(self.dir, st)
            st = self.load()
            seen.append(st.state_revision)
        print(f"\nstate_revision sequence: {seen}")
        self.assertEqual(seen, [3, 4, 5, 6])
        self.assertEqual(json.loads((self.dir / "state.json").read_text())["schema_version"], 1)

    def test_fsync_is_called_on_the_file_and_the_directory(self):
        st = self.state()
        synced, real = [], os.fsync

        def spy(fd):
            synced.append(os.fstat(fd).st_mode)
            return real(fd)

        os.fsync = spy
        self.addCleanup(setattr, os, "fsync", real)
        S.write_state(self.dir, st)
        import stat
        kinds = {"file" if stat.S_ISREG(m) else "dir" if stat.S_ISDIR(m) else "?"
                 for m in synced}
        self.assertEqual(kinds, {"file", "dir"},
                         f"fsync must reach the file AND the directory, saw {kinds}")

    def test_crash_between_temp_and_rename_leaves_the_old_state_intact(self):
        st = self.state()
        S.write_state(self.dir, st)                      # revision 4 on disk
        before = (self.dir / "state.json").read_bytes()
        digest_before = hashlib.sha256(before).hexdigest()

        real = os.replace

        def boom(*a, **kw):
            raise OSError(28, "No space left on device")

        os.replace = boom
        self.addCleanup(setattr, os, "replace", real)
        with self.assertRaises(OSError):
            S.write_state(self.dir, S.load_state(self.dir))
        os.replace = real

        after = (self.dir / "state.json").read_bytes()
        digest_after = hashlib.sha256(after).hexdigest()
        survivors = sorted(p.name for p in self.dir.iterdir())
        print(f"\ncrash simulation: sha256 before={digest_before}\n"
              f"                  sha256 after ={digest_after}\n"
              f"                  files in audit dir: {survivors}")
        self.assertEqual(digest_before, digest_after,
                         "a failed write must leave the previous state byte-identical")
        self.assertEqual(survivors, ["state.json"],
                         "the temp file must not survive as a decoy")
        self.assertEqual(S.load_state(self.dir).state_revision, 4)

    def test_writer_refuses_to_persist_what_its_own_reader_would_reject(self):
        """A writer that can emit an unreadable file breaks the one-boundary rule."""
        st = self.state()
        broken = S.State(**{**st.__dict__, "class_findings": (), "plans": (), "findings": (
            S.Finding(id="F-0003", title="висячая", severity="major", status="reported",
                      dimension="methodology", unit=["U01"], pass_id="P-77", attempts=0,
                      updated="2026-08-14", body_path="findings/F-0003.md"),)})
        with self.assertRaises(S.StateError) as cm:
            S.write_state(self.dir, broken)
        self.assertIn("is not a pass in the queue", str(cm.exception))

    def test_empty_state_is_valid_and_round_trips(self):
        st = S.empty_state("xcheck 0.8.0")
        S.write_state(self.dir, st, bump=False)
        back = self.load()
        self.assertEqual(back.state_revision, 0)
        self.assertEqual(back.findings, ())
        self.assertEqual(back.sessions, ())


def _write_case_table(rows):
    """Print the negative table once, as the phase-4 evidence artifact."""
    if os.environ.get("XCHECK_CASE_TABLE") != "1":
        return
    print(f"\n{len(rows)} negative cases\n" + "=" * 78)
    for name, msg in rows:
        print(f"\n{name}\n  -> {msg}")


if __name__ == "__main__":
    unittest.main()
