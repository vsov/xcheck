#!/usr/bin/env python3
"""Every file in a built artifact is declared, or the gate fails naming it.

`ci/check-artifact.py` asks whether the TRACKED file set is clean. This asks a
different question with a different failure mode: what actually ended up inside the
wheel and the sdist. The two disagree exactly when it matters — a `.pyc` that git
ignores can still be packaged, an editor backup in the source tree can ride into an
sdist, and a `packages =` line that quietly widened ships a directory nobody meant to
publish.

Declared means one of:

  * the package directory named in `pyproject.toml`'s `[tool.setuptools] packages`
    (this is read from the file, not restated here);
  * the console script and metadata setuptools generates — `*.dist-info/**`,
    `PKG-INFO`, `SOURCES.txt`, `entry_points.txt`, `setup.cfg`;
  * an explicit ALLOWLIST below, which is short on purpose and carries a reason per
    entry.

Anything else is reported with its artifact and its path, and the exit code is 1.

Usage: check-artifact-contents.py <artifact> [<artifact> ...]
"""

import re
import sys
import tarfile
import zipfile
from pathlib import Path

# Files that legitimately ship and are not part of the package directory. Each entry is
# a top-level name inside the sdist root; the reason is why a user of the ARTIFACT (not
# of the repository) benefits from it being there.
ALLOWLIST = {
    "pyproject.toml": "the build declaration itself; an sdist without it cannot build",
    "MANIFEST.in": "the other half of the build declaration — it is what PRUNED the "
                   "test tree out of this artifact, so an sdist that dropped it would "
                   "rebuild into a different artifact than the one under test",
    "README.md": "declared as `readme`, and rendered on the package page",
    "LICENSE": "MIT; a redistributable artifact without its licence is a legal defect",
    "CHANGELOG.md": "linked from `[project.urls]`; a release with no changelog in it "
                    "forces the reader back to a URL that may not resolve",
    "SECURITY.md": "the trust model. This tool launches agent CLIs against a "
                   "repository; shipping the binary without the threat model is the "
                   "one omission that could get someone hurt",
    "XCHECK.md": "the methodology `install.sh` copies into a target project — the "
                 "artifact is not usable without it",
}

# Metadata setuptools generates. Patterns, because the dist-info directory carries the
# version in its name.
GENERATED = (
    re.compile(r"^[^/]+\.dist-info/"),
    re.compile(r"^PKG-INFO$"),
    re.compile(r"^setup\.cfg$"),
    re.compile(r"^[^/]+\.egg-info/"),
)

ROOT = Path(__file__).resolve().parent.parent


def declared_packages():
    """The package directories `pyproject.toml` declares, read from the file."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r"^packages\s*=\s*\[([^\]]*)\]", text, re.M)
    if not m:
        return []
    return re.findall(r'"([^"]+)"', m.group(1))


def members(artifact):
    """(display name, [paths relative to the artifact root]) for a wheel or an sdist.

    An sdist's entries are all under one `name-version/` prefix, which is stripped so
    both artifacts are judged against the same declarations."""
    p = Path(artifact)
    if p.suffix == ".whl":
        with zipfile.ZipFile(p) as z:
            return p.name, [n for n in z.namelist() if not n.endswith("/")]
    with tarfile.open(p, "r:gz") as t:
        names = [m.name for m in t.getmembers() if m.isfile()]
    stripped = []
    for n in names:
        head, _, rest = n.partition("/")
        stripped.append(rest or head)
    return p.name, stripped


def undeclared(paths, packages):
    out = []
    for path in sorted(paths):
        top = path.split("/")[0]
        if top in packages:
            continue
        if any(rx.match(path) for rx in GENERATED):
            continue
        if path in ALLOWLIST:
            continue
        out.append(path)
    return out


def main(argv):
    artifacts = argv[1:]
    if not artifacts:
        print(__doc__.strip().splitlines()[-1])
        return 2
    packages = declared_packages()
    print(f"declared packages (from pyproject.toml): {packages}")
    print(f"allowlisted top-level files: {sorted(ALLOWLIST)}")
    bad = 0
    for artifact in artifacts:
        name, paths = members(artifact)
        extra = undeclared(paths, packages)
        print(f"\n{name}: {len(paths)} files, {len(extra)} undeclared")
        for path in paths:
            print(f"    {path}")
        for path in extra:
            print(f"  UNDECLARED: {name} carries {path!r}, which is neither in a "
                  f"declared package nor on the allowlist")
            bad += 1
    if bad:
        print(f"\nundeclared-file check: FAIL ({bad} file(s))")
        print("Either add the file to `packages`/the allowlist with a reason, or stop "
              "shipping it. An artifact whose contents nobody declared is an artifact "
              "nobody can review.")
        return 1
    print("\nundeclared-file check: every packaged file is declared")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
