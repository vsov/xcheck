"""The provider telemetry seam: the ONE place that knows what a provider's usage looks
like.

PHASE 11 (fourth audit). The economics finding is that 7 of 159 finished sessions carry a
token figure — 4.4% — so 95.6% of this project's sessions are not free, merely unmeasured,
and every budget and cost claim above them is a guess. Worse than the audit stated: no
session in the corpus carries a `telemetry_source` at all, so even those 7 numbers are
unattributed. They came from `tokens_in_log`, a regular expression over agent prose.

The third audit already split WHO SPOKE (a parent-owned sidecar is `provider`, a line the
child printed is `agent-reported`) and that split is preserved here without exception.
What this module adds is WHAT WAS SAID: a declared ten-field interface, and adapters that
are the only code allowed to know a vendor's key names.

Why a seam at all, when one dict of aliases already worked: an alias table that lives in
`envelope.py` beside the labelling logic means every new provider edits the module that
decides what is trustworthy. The knowledge that OpenAI calls it `cached_tokens` and
Anthropic calls it `cache_read_input_tokens` is vendor trivia; the rule that a figure the
subject printed may not wear the `provider` label is a security property. They do not
belong in the same file, and `tests/test_provider_adapter.py` asserts structurally — over
the AST, not the prose — that no module outside this one reads a vendor key.

TWO adapters, because one adapter is a hypothetical seam. `codex` is the CLI this
project's own operator profile dispatches (`codex exec --sandbox workspace-write`), and
`fake` is an in-memory adapter that speaks the canonical field names. The fake is not a
test convenience smuggled into production: it is how `xcheck doctor` and the drills
exercise the full interface on a machine where no provider CLI is installed, which is the
difference between reporting `unmeasured` and skipping silently.

The adapter is SELECTED FROM THE OPERATOR PROFILE (`telemetry_adapter=`), never inferred
from the role command's text. A tool that reads `codex` out of a command string and
concludes "this is OpenAI" has decided something it was not told — the operator may be
running a wrapper, a proxy, a different build, or a shim of the same name. There is no
default for the same reason there is no default role command: the correct answer for a
machine whose operator has not said what it runs is to measure nothing and say so.

And per `provenance-must-not-interview-the-subject`: NOTHING here executes the provider
binary to find out what it is. Identifying a program by running it is the finding this
project already closed once (F-0098).
"""

import ast
import json
import re
from collections import namedtuple
from pathlib import Path

# ------------------------------------------------------------------ the ten fields

# The audit named ten fields, and they are declared as a tuple rather than left implicit
# in a dataclass so a test can assert the interface IS these ten and no others. Every one
# is nullable and null means NOT MEASURED — never zero. A measured zero is not something
# an agent session produces, so writing 0 for an absent figure would destroy the
# difference between "cost nothing" and "we do not know", which is the entire reason the
# metrics block reports its own coverage rather than a total.
USAGE_FIELDS = (
    "provider",          # who reported: the vendor, as the operator declared it
    "model",             # the ACTUAL model that ran, not the one that was requested
    "request_id",        # the provider's own id for this call, or the session's
    "tokens_input",      # input tokens
    "tokens_cache",      # cached input tokens — priced differently, so counted apart
    "tokens_output",     # output or reasoning tokens
    "tokens_total",      # the provider's own total
    "stop_reason",       # why the provider stopped: length, stop, refusal, error
    "limit_input",       # the input limit that was APPLIED to this call
    "limit_output",      # the output limit that was APPLIED to this call
)

Usage = namedtuple("Usage", USAGE_FIELDS)
Usage.__new__.__defaults__ = (None,) * len(USAGE_FIELDS)

# `limit_input` and `limit_output` are the two fields nothing in this project recorded
# before, and they are the two that make a budget auditable rather than merely enforced:
# a session that stopped at `length` against a 4,096-token output limit is a session whose
# cost was decided by configuration, and one that stopped at `length` with no limit
# applied is a defect. Budgets (phase 12) read them.

# The count fields, for validation. Held here rather than derived from USAGE_FIELDS by
# prefix, because "every field starting with tokens_" is a naming convention and a
# convention is not a check — a field renamed tomorrow would quietly leave the gate.
COUNT_FIELDS = ("tokens_input", "tokens_cache", "tokens_output", "tokens_total")
LIMIT_FIELDS = ("limit_input", "limit_output")


# ------------------------------------------------------------------- the refusals

def usage_refusals(got):
    """Why a raw usage object cannot be believed, as sentences. Empty list = fine.

    Value checks over whatever key names the caller hands in, so both the adapters and
    the legacy alias path share one gate. `bool` is checked before `int` deliberately:
    `isinstance(True, int)` is True in Python, so `{"total_tokens": true}` would otherwise
    be recorded as one token.
    """
    if not isinstance(got, dict):
        return [f"the usage object is a {type(got).__name__}, not an object"]
    bad = []
    for k, v in sorted(got.items()):
        if v is None or not _is_count_key(k):
            continue
        if isinstance(v, bool):
            bad.append(f"{k}={v!r} is a boolean where a count belongs")
        elif not isinstance(v, int):
            bad.append(f"{k}={v!r} is not an integer count")
        elif v < 0:
            bad.append(f"{k}={v} is negative, and a session cannot un-spend tokens")
    if bad:
        return bad                     # a total check over values already rejected lies
    total = got.get("total_tokens", got.get("tokens_total"))
    if isinstance(total, int) and not isinstance(total, bool):
        parts = sum(got.get(k) or 0 for k in _PART_KEYS
                    if isinstance(got.get(k), int) and not isinstance(got.get(k), bool))
        if parts and total < parts:
            bad.append(f"total_tokens={total} is smaller than its own parts ({parts})")
    return bad


# The keys that ADD UP to a total. Cache keys are excluded: a cached input token is
# already counted as an input token, so including it would make every honest object look
# like an under-reported total.
_PART_KEYS = ("input_tokens", "prompt_tokens", "output_tokens", "completion_tokens",
              "tokens_input", "tokens_output")


# The vendor key names that carry counts. Enumerated, never matched fuzzily: a key nobody
# declared is a key nobody has checked the units of, and reading a `tokens` field as
# `input_tokens` produces a number that is wrong in a way no test would catch.
_COUNT_KEYS = frozenset({
    "input_tokens", "prompt_tokens", "output_tokens", "completion_tokens",
    "cache_read_input_tokens", "cached_tokens", "cache_tokens", "total_tokens",
    "reasoning_tokens", "max_input_tokens", "max_output_tokens",
} | set(COUNT_FIELDS) | set(LIMIT_FIELDS))


def _is_count_key(key):
    return key in _COUNT_KEYS


def refusals(usage):
    """Why this parsed `Usage` may not be recorded as provider truth. Empty = fine.

    Four classes, each with its own sentence, because an operator reading "the telemetry
    was refused" learns nothing they can act on:

      * a negative count — a session cannot un-spend tokens;
      * a boolean where an integer belongs — the `isinstance(True, int)` trap;
      * a total that cannot be the sum of its parts — the object is internally wrong,
        so neither half of it can be trusted;
      * no actual model — `provider` is the label meaning "the vendor said so", and a
        vendor that did not say WHICH MODEL ran has not supported the claim the label
        makes. This is the one refusal that does not apply to `agent-reported`, where a
        missing model is merely a gap.
    """
    bad = usage_refusals({f: getattr(usage, f) for f in COUNT_FIELDS + LIMIT_FIELDS})
    if not str(usage.model or "").strip():
        bad.append("the record names no actual model, so `provider` would claim a "
                   "vendor vouched for a figure without saying what produced it")
    return bad


# ----------------------------------------------------------------- the two adapters

def _first(got, *keys):
    for k in keys:
        if got.get(k) is not None:
            return got[k]
    return None


def alias_usage(got, provider_name=None):
    """The enumerated alias reader: vendor key names in, the ten fields out.

    One function rather than one per vendor because the aliases genuinely overlap — every
    shape below is some CLI's rendering of the same usage block — and three copies of an
    alias list is three places for a key to go missing. `cached_tokens` is subtracted from
    nothing: a cached input token is an input token that was cheaper, and folding it away
    would make the two prices unrecoverable from the record.

    Also the reader for the channel the CHILD writes (`XCHECK_TELEMETRY`), which has no
    declared adapter because nobody declared what the child is. Reading that line with
    this function does not lend it the `provider` label — `envelope.telemetry` decides the
    label from WHICH CHANNEL the object arrived on, and that decision is deliberately not
    here.
    """
    return Usage(
        provider=provider_name,
        model=_first(got, "model", "actual_model"),
        request_id=_first(got, "request_id", "id", "response_id"),
        tokens_input=_first(got, "input_tokens", "prompt_tokens"),
        tokens_cache=_first(got, "cache_read_input_tokens", "cached_tokens",
                            "cache_tokens"),
        tokens_output=_first(got, "output_tokens", "completion_tokens",
                             "reasoning_tokens"),
        tokens_total=_first(got, "total_tokens"),
        stop_reason=_first(got, "stop_reason", "finish_reason"),
        limit_input=_first(got, "max_input_tokens"),
        limit_output=_first(got, "max_output_tokens"),
    )


# `reasoning_effort` is NOT one of the audit's ten and is not in `Usage`: the interface is
# the ten fields and a test asserts it. It is still a vendor key, so its aliases live here
# rather than leaking back into `envelope.py` — the seam is about where shape knowledge
# lives, not about which fields happened to make the list.
def reasoning_effort(got):
    """The effort setting a provider echoed back, or None."""
    return _first(got or {}, "reasoning_effort", "effort")


def _codex(got):
    """The CLI this project's own operator profile dispatches: `codex exec …`, OpenAI.

    Thin on purpose. What makes it an adapter is not the code in it but that the operator
    NAMED it — `telemetry_adapter=codex` is a statement that this machine's wrapper hands
    over OpenAI's usage block, which is a fact about the machine that no amount of reading
    the command string could establish.
    """
    return alias_usage(got, "openai")


def _fake(got):
    """The in-memory adapter, speaking the canonical field names.

    Not a test double that leaked into production. It is the adapter `xcheck doctor` and
    the drills use to exercise the whole interface on a machine with no provider CLI
    installed, which is what lets an unavailable provider be REPORTED as unmeasured
    instead of skipped in silence. It also keeps the seam honest: an interface with one
    implementation is a shape somebody drew around the code that already existed.
    """
    return Usage(**{f: got.get(f) for f in USAGE_FIELDS})


Adapter = namedtuple("Adapter", "name provider keys parse")

ADAPTERS = {
    "codex": Adapter("codex", "openai", (
        "model", "actual_model", "request_id", "id", "response_id", "input_tokens",
        "prompt_tokens", "cache_read_input_tokens", "cached_tokens", "cache_tokens",
        "output_tokens", "completion_tokens", "reasoning_tokens", "total_tokens",
        "stop_reason", "finish_reason", "max_input_tokens", "max_output_tokens",
        "reasoning_effort", "effort"),
        _codex),
    "fake": Adapter("fake", "fake", USAGE_FIELDS, _fake),
}

# Every vendor key any adapter reads. The structural test uses this as its needle set:
# a literal from here appearing in another module's AST means the shape knowledge has
# leaked back out of the seam.
#
# Four names are held OUT of the needle set. They are vendor keys, and they are also
# ordinary words this codebase uses for its own records — `id` is every record's
# identifier, `model` and `effort` are configuration, and `reasoning_effort` is a session
# field. A needle that flags those would report ten modules and mean nothing, and the
# usual response to a check that cries wolf is to delete it. Narrowing the claim keeps it
# enforceable: what the seam owns is the keys nobody would write by accident.
AMBIGUOUS = frozenset({"id", "model", "effort", "reasoning_effort"})

VENDOR_KEYS = (frozenset(k for a in ADAPTERS.values() for k in a.keys)
               - set(USAGE_FIELDS) - AMBIGUOUS)


def adapter_for(conf, key="telemetry_adapter"):
    """`(adapter, problem)` — which adapter the OPERATOR declared, never a guess.

    Returns `(None, None)` when the operator has declared nothing: that is not an error,
    it is a machine whose telemetry is unconfigured, and the honest consequence is that
    sessions record no provider figure. Returns `(None, message)` for a name nobody
    implements, because falling back to a working adapter for an unknown provider would
    parse one vendor's numbers with another vendor's key names.

    What this function deliberately does NOT do is look at the role command. Reading
    `codex` out of `codex exec --sandbox …` and concluding OpenAI is an inference about a
    program the operator may have wrapped, proxied, renamed or replaced.
    """
    name = str((conf or {}).get(key) or "").strip()
    if not name:
        return None, None
    got = ADAPTERS.get(name)
    if got is None:
        return None, (f"{key}={name!r} names no adapter. Known: "
                      f"{', '.join(sorted(ADAPTERS))}. A provider whose output shape "
                      f"nobody has written down cannot be parsed by guessing.")
    return got, None


def read(adapter, obj):
    """`(usage, refusals)` — the ONLY way a provider-labelled object becomes a figure.

    Never raises. A session that has already finished its real work must not be lost to a
    bad number, and a refused figure is RECORDED rather than dropped: a rejection nobody
    can see is a rejection the operator never learns was offered.

    PHASE 3 (fifth audit). This function held every refusal and production did not call
    it: `envelope.finish` reached past it to `adapter.parse`, so one usage object was
    rejected here and accepted there, wearing `telemetry_source=provider` — a validator
    with no traffic. `parse` is now internal to this module (asserted structurally), and
    `adapter=None` no longer means "nothing to do": an operator who declared no adapter
    still gets the enumerated alias reader AND the same refusals, because the label is
    what the rules are about, not the vendor.
    """
    if not isinstance(obj, dict):
        return None, [f"the usage object is a {type(obj).__name__}, not an object"]
    early = usage_refusals(obj)          # value checks on the RAW keys, before coercion
    if early:
        return None, early
    parse = adapter.parse if adapter is not None else alias_usage
    try:
        usage = parse(obj)
    except (TypeError, ValueError, AttributeError) as e:      # pragma: no cover
        name = adapter.name if adapter is not None else "alias"
        return None, [f"the {name} adapter could not read this object — {e}"]
    bad = refusals(usage)
    return (None, bad) if bad else (usage, [])


# --------------------------------------------------- the channel the child can write

# The `XCHECK_TELEMETRY {...}` line lives here too, and it lives here for the same reason
# the vendor keys do: it is a statement about a provider's output shape. What does NOT
# live here is the decision about what it means. A line the child printed is
# `agent-reported` and never `provider`, and that rule stays in `envelope.py` beside the
# sidecar it is contrasted with — this module only knows how to find the line.
LINE_RE = re.compile(r"(?m)^[ \t]*XCHECK_TELEMETRY[ \t]+(\{.*\})[ \t]*$")


def last_line_object(text):
    """The LAST `XCHECK_TELEMETRY` object in a log, or None.

    The last wins, for the same reason `tokens_in_log` takes the last match: a session
    that quotes an earlier log would otherwise report an older run's numbers.
    """
    got = None
    for m in LINE_RE.finditer(text or ""):
        try:
            one = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(one, dict):
            got = one
    return got


# ------------------------------------------------------------- the structural check

def modules_reading_vendor_keys(paths):
    """Which modules mention a vendor key as a real string literal — the seam check.

    Over the AST and not the text, per `assert-over-the-ast-not-the-prose`: this module's
    own docstring names `cached_tokens` and `cache_read_input_tokens` in a sentence
    explaining why they are here, and a substring hunt would read the explanation as the
    violation. Docstrings are excluded by IDENTITY — the exact node objects — rather than
    by matching their text, because a comment that quotes a key is prose too and an
    assignment that happens to equal a docstring's text is not.
    """
    found = {}
    for path in paths:
        p = Path(path)
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):                          # pragma: no cover
            continue
        docs = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = getattr(node, "body", None)
                if body and isinstance(body[0], ast.Expr) and \
                        isinstance(body[0].value, ast.Constant) and \
                        isinstance(body[0].value.value, str):
                    docs.add(id(body[0].value))
        hits = sorted({n.value for n in ast.walk(tree)
                       if isinstance(n, ast.Constant) and isinstance(n.value, str)
                       and id(n) not in docs and n.value in VENDOR_KEYS})
        if hits:
            found[p.name] = hits
    return found
