"""The cheap facts, built before the model runs.

The third audit's economics finding, in one sentence: the model is paying to discover
facts a script can produce for free, and every session rediscovers them. An agent asked
to audit a repository spends its first minutes doing what `ast` and `git` do in
milliseconds — listing files, working out which module imports which, finding where
`subprocess` is called.

So this module answers those questions once, deterministically, and the capsule carries
the SLICE that the charter's own material needs. It is a FACT SHEET, not code
intelligence: every function here is pure, reads only the tree and git, and returns
plain data. There is no model, no network and no cache, and a test walks this module's
AST to keep it that way.

WHAT IT IS NOT. It does not judge. `sinks()` reports that `subprocess.run` is called at
a line; whether that call is a defect is exactly the question the audit exists to answer
and exactly the question a grep must not pretend to have answered. Every row here is a
LOCATION, and the finding it may become still needs a session to make the argument.

Honest degradation is the other half. A file this module cannot parse is reported in
`unparsed` with the error that stopped it — never dropped, because a fact sheet that
silently omits the file nobody could read is a fact sheet that is most wrong exactly
where the reader most needs it.
"""

import ast
import hashlib
import json
import subprocess
from pathlib import Path

from xcheck.util import GIT_TIMEOUT

SCHEMA = 1

# Directories that are never the subject: version control internals, caches, build
# output, and the audit's own bookkeeping. Named rather than pattern-matched so a reader
# can see the whole list.
SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "node_modules", ".venv", "venv", "dist", "build", ".eggs", ".tox",
    "audit", "audit-archive", "graphify-out", ".supergoal", ".superpowers", ".wolf",
})

# extension -> the language a reader would call it. Not a guess at content: a `.py` file
# that is actually shell is a lie the extension tells, and this sheet reports what the
# tree says rather than sniffing bytes.
LANGUAGES = {
    ".py": "python", ".pyi": "python", ".sh": "shell", ".bash": "shell",
    ".md": "markdown", ".json": "json", ".jsonl": "json", ".toml": "toml",
    ".yml": "yaml", ".yaml": "yaml", ".cfg": "ini", ".ini": "ini", ".conf": "ini",
    ".txt": "text", ".c": "c", ".h": "c", ".js": "javascript", ".ts": "typescript",
}

# The call shapes worth KNOWING ABOUT before a session starts. Presence is a location,
# never a verdict — see the module docstring.
SINKS = {
    "subprocess.run": "process", "subprocess.Popen": "process",
    "subprocess.call": "process", "subprocess.check_output": "process",
    "os.system": "process", "os.popen": "process", "os.execv": "process",
    "eval": "dynamic-eval", "exec": "dynamic-eval", "compile": "dynamic-eval",
    "pickle.loads": "deserialisation", "pickle.load": "deserialisation",
    "marshal.loads": "deserialisation",
    "socket.socket": "network", "socket.create_connection": "network",
    "urllib.request.urlopen": "network", "http.client.HTTPConnection": "network",
    "os.remove": "destructive", "os.unlink": "destructive", "shutil.rmtree": "destructive",
    "os.chmod": "permissions", "os.setuid": "permissions",
}

# Files that decide how the project is built, released, or owned. A charter that touches
# one of these is touching everyone's build, which is worth saying before the session
# starts rather than after it has filed something.
SURFACE_NAMES = frozenset({
    "pyproject.toml", "setup.py", "setup.cfg", "MANIFEST.in", "requirements.txt",
    "Makefile", "Dockerfile", "CODEOWNERS", ".gitignore", ".gitattributes",
})
SURFACE_DIRS = ("ci", ".github", ".circleci")


def _rel_files(project):
    """Every file under `project`, repo-relative and SORTED. The one walker.

    Sorted here rather than at each call site: determinism is the property this whole
    module is asserted on, and a set of paths returned in filesystem order would make
    every downstream digest depend on the order a directory happened to be created in.

    A directory carrying its own `.git` is PRUNED, and that is the rule rather than a
    list of tool directories to avoid. A nested checkout — a git worktree, a vendored
    clone, a submodule — is a copy of some other repository's files, so counting it
    would report a tree that does not exist: this project's own harness keeps worktrees
    under a dot-directory, and walking them doubled every number in the sheet with
    copies of files already counted. Naming that directory in shipped code would also
    have been a claim xcheck must not make (`test_style_claim_is_narrow`), and the
    general rule is the better answer anyway.
    """
    root = Path(project)
    out = []

    def walk(d):
        try:
            entries = sorted(d.iterdir(), key=lambda p: p.name)
        except OSError:
            return
        for p in entries:
            if p.is_symlink():
                continue
            if p.is_dir():
                if p.name in SKIP_DIRS or (p / ".git").exists():
                    continue
                walk(p)
            elif p.is_file():
                out.append(p.relative_to(root).as_posix())

    walk(root)
    return sorted(out)


def _read(project, rel):
    try:
        return Path(project, rel).read_text(encoding="utf-8"), None
    except (OSError, UnicodeDecodeError) as e:
        return None, f"{type(e).__name__}: {e}"


def _tree(project, rel):
    """`(ast.Module, None)` or `(None, reason)`. Never raises: an unparseable file is a
    FACT to report, not an exception to stop the sheet."""
    text, err = _read(project, rel)
    if text is None:
        return None, err
    try:
        return ast.parse(text, filename=rel), None
    except (SyntaxError, ValueError, RecursionError) as e:
        return None, f"{type(e).__name__}: {e}"


def inventory(project):
    """Every file, with the facts a reader gets for free: language, size, line count."""
    rows, unparsed = [], []
    for rel in _rel_files(project):
        p = Path(project, rel)
        text, err = _read(project, rel)
        row = {"path": rel, "language": LANGUAGES.get(p.suffix, "other"),
               "bytes": p.stat().st_size,
               "lines": None if text is None else text.count("\n") + (
                   0 if text.endswith("\n") or not text else 1)}
        if text is None:
            unparsed.append({"path": rel, "reason": err})
        rows.append(row)
    return {"files": rows, "unreadable": unparsed}


def modules(project):
    """The module map: which directories are python packages, and what is in them.

    A package is a directory with `__init__.py`, which is the definition python itself
    uses — not a heuristic about naming.
    """
    files = _rel_files(project)
    packages = sorted({str(Path(f).parent) for f in files
                       if Path(f).name == "__init__.py"})
    by_language = {}
    for rel in files:
        lang = LANGUAGES.get(Path(rel).suffix, "other")
        by_language[lang] = by_language.get(lang, 0) + 1
    return {"packages": packages,
            "by_language": dict(sorted(by_language.items())),
            "python_files": sorted(f for f in files if f.endswith(".py"))}


def _imports(tree):
    """Dotted module names this AST imports, `from x import y` included."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
    return out


def dependency_edges(project):
    """`from -> to` for imports that stay INSIDE this repository.

    Third-party imports are dropped on purpose: this sheet answers "what does changing
    this file break here", and `import json` is the same answer for every file.
    """
    files = [f for f in _rel_files(project) if f.endswith(".py")]
    own = {f[:-3].replace("/", ".").removesuffix(".__init__") for f in files}
    edges, unparsed = [], []
    for rel in files:
        tree, err = _tree(project, rel)
        if tree is None:
            unparsed.append({"path": rel, "reason": err})
            continue
        for name in sorted(_imports(tree)):
            hit = next((o for o in (name, name.rsplit(".", 1)[0]) if o in own), None)
            if hit:
                edges.append({"from": rel, "to": hit})
    return {"edges": sorted(edges, key=lambda e: (e["from"], e["to"])),
            "unparsed": sorted(unparsed, key=lambda u: u["path"])}


def test_map(project):
    """test file -> the repo modules it imports. The mapping a reviewer wants first:
    "I changed this; what tests claim to cover it"."""
    dep = dependency_edges(project)
    out = {}
    for e in dep["edges"]:
        name = Path(e["from"]).name
        if name.startswith("test_") or name.endswith("_test.py"):
            out.setdefault(e["from"], []).append(e["to"])
    return {"tests": {k: sorted(set(v)) for k, v in sorted(out.items())},
            "unparsed": dep["unparsed"]}


def public_interfaces(project):
    """Top-level names a module exports — no leading underscore, module scope only."""
    out, unparsed = {}, []
    for rel in (f for f in _rel_files(project) if f.endswith(".py")):
        tree, err = _tree(project, rel)
        if tree is None:
            unparsed.append({"path": rel, "reason": err})
            continue
        names = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not node.name.startswith("_"):
                    names.append(node.name)
            elif isinstance(node, ast.Assign):
                names += [t.id for t in node.targets
                          if isinstance(t, ast.Name) and not t.id.startswith("_")]
        if names:
            out[rel] = sorted(set(names))
    return {"public": dict(sorted(out.items())),
            "unparsed": sorted(unparsed, key=lambda u: u["path"])}


def _call_name(node):
    """`subprocess.run` from a Call node's func, or None. Dotted, at most two levels —
    `a.b.c()` reports `b.c`, which is what the SINKS table is keyed by."""
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        base = f.value
        if isinstance(base, ast.Name):
            return f"{base.id}.{f.attr}"
        if isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
            return f"{base.attr}.{f.attr}"
    return None


def sinks(project):
    """Where the security-sensitive call shapes are. LOCATIONS, never verdicts."""
    rows, unparsed = [], []
    for rel in (f for f in _rel_files(project) if f.endswith(".py")):
        tree, err = _tree(project, rel)
        if tree is None:
            unparsed.append({"path": rel, "reason": err})
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _call_name(node)
                if name in SINKS:
                    rows.append({"path": rel, "line": node.lineno, "call": name,
                                 "kind": SINKS[name]})
    return {"sinks": sorted(rows, key=lambda r: (r["path"], r["line"], r["call"])),
            "unparsed": sorted(unparsed, key=lambda u: u["path"])}


def surfaces(project):
    """Build, release, CI and ownership files — the ones that are everyone's."""
    found = []
    for rel in _rel_files(project):
        p = Path(rel)
        if p.name in SURFACE_NAMES or p.parts[0] in SURFACE_DIRS:
            found.append({"path": rel,
                          "why": "named" if p.name in SURFACE_NAMES else "directory"})
    return {"surfaces": sorted(found, key=lambda s: s["path"])}


def changed_symbols(project, base):
    """Top-level defs and classes whose file changed since `base`, plus the files.

    Deliberately file-granular on the symbol side: a diff hunk maps to a line range, and
    mapping line ranges back to symbols reliably means re-parsing both revisions. What a
    session needs is "these symbols live in files that moved" — a starting point, and
    the sheet says which it is.
    """
    if not base:
        return {"base": None, "files": [], "symbols": [],
                "note": "no base commit was given, so nothing is reported as changed — "
                        "this is not a claim that nothing changed"}
    r = subprocess.run(["git", "diff", "--name-only", base, "--"], cwd=str(project),
                       capture_output=True, text=True, timeout=GIT_TIMEOUT)
    if r.returncode != 0:
        return {"base": base, "files": [], "symbols": [],
                "note": f"git could not diff against {base}: {r.stderr.strip()[:200]}"}
    changed = sorted(f for f in r.stdout.split("\n") if f
                     and not any(part in SKIP_DIRS for part in Path(f).parts))
    symbols = []
    for rel in changed:
        if not rel.endswith(".py") or not Path(project, rel).is_file():
            continue
        tree, _err = _tree(project, rel)
        for node in (tree.body if tree else []):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbols.append({"path": rel, "name": node.name, "line": node.lineno})
    return {"base": base, "files": changed,
            "symbols": sorted(symbols, key=lambda s: (s["path"], s["line"])),
            "note": "file-granular: these symbols live in files that changed since the "
                    "base, which is a starting point and not a claim that each symbol's "
                    "own text moved"}


def build_sheet(project, base=None):
    """The whole sheet. No model, no network, no cache."""
    project = Path(project)
    sheet = {
        "preprocess_schema": SCHEMA,
        "inventory": inventory(project),
        "modules": modules(project),
        "dependencies": dependency_edges(project),
        "tests": test_map(project),
        "interfaces": public_interfaces(project),
        "sinks": sinks(project),
        "surfaces": surfaces(project),
        "changed": changed_symbols(project, base),
    }
    # Every producer above reports what it could not read; they are collected HERE into
    # one list so a reader has a single place to ask "what did this sheet not see".
    unparsed = list(sheet["inventory"]["unreadable"])
    for key in ("dependencies", "tests", "interfaces", "sinks"):
        unparsed += sheet[key].get("unparsed", [])
    seen, merged = set(), []
    for row in sorted(unparsed, key=lambda u: (u["path"], u["reason"])):
        if (row["path"], row["reason"]) not in seen:
            seen.add((row["path"], row["reason"]))
            merged.append(row)
    sheet["unparsed"] = merged
    return sheet


def sheet_digest(sheet):
    """sha256 over the canonical form, so a capsule can name WHICH sheet it was built
    from and two capsules can be compared without carrying the sheet."""
    return hashlib.sha256(
        json.dumps(sheet, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def slice_for(sheet, paths):
    """The capsule's SLICE: one row per path the charter's material names.

    A preprocessing step that re-inflates the prompt has undone the capsule. So the
    capsule never carries the sheet — it carries this, which is per-path counts and the
    sink LINES, plus the digest of the sheet the counts came from, so two capsules can
    be compared without either carrying it.
    """
    want = set(paths or ())
    by_path = {f["path"]: f for f in sheet["inventory"]["files"]}
    rows = []
    for rel in sorted(want):
        f = by_path.get(rel)
        s = [x for x in sheet["sinks"]["sinks"] if x["path"] == rel]
        covered = sorted(t for t, mods in sheet["tests"]["tests"].items()
                         if rel[:-3].replace("/", ".") in mods)
        rows.append({
            "path": rel,
            "present": f is not None,
            "language": (f or {}).get("language"),
            "lines": (f or {}).get("lines"),
            "public_names": len(sheet["interfaces"]["public"].get(rel, ())),
            "sinks": [{"line": x["line"], "call": x["call"]} for x in s],
            "covered_by": covered,
            "changed_since_base": rel in sheet["changed"]["files"],
        })
    return {"preprocess_digest": sheet_digest(sheet), "rows": rows,
            "note": "counts and locations only, derived from the tree at dispatch with "
                    "no model and no network. A location is not a finding: whether a "
                    "sink is a defect is the question your charter asks, and nothing "
                    "here answers it."}
