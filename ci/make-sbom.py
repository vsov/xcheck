#!/usr/bin/env python3
"""A CycloneDX 1.5 JSON SBOM for the built artifacts, from stdlib `json`.

The dependency set is empty — that is a constraint the package declares and a test
enforces — so the document is small. Adding a generator dependency in order to describe
having no dependencies would be the joke writing itself, so this emits the schema
directly.

The SBOM's whole content here is one component (xcheck itself), its licence, its
hashes, and an explicit statement that it depends on nothing. `dependencies: [{ref:
xcheck, dependsOn: []}]` is not the same as omitting the key: the empty list is the
claim, an absent key is silence.

Usage: make-sbom.py <repo-root> <dir-holding-the-artifacts>
"""

import hashlib
import json
import re
import sys
from pathlib import Path


def read_field(pyproject, key):
    m = re.search(rf'^{key}\s*=\s*"([^"]+)"', pyproject, re.M)
    return m.group(1) if m else None


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[-1])
        return 2
    root, out = Path(argv[1]), Path(argv[2])
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    name = read_field(pyproject, "name")
    version = read_field(pyproject, "version")

    deps = re.search(r"^dependencies\s*=\s*\[([^\]]*)\]", pyproject, re.M)
    if deps is None or deps.group(1).strip():
        print("make-sbom: pyproject.toml declares runtime dependencies; this generator "
              "describes a stdlib-only package and would emit a document that lies.")
        return 1

    hashes = []
    for f in sorted(out.iterdir()):
        if f.suffix in (".whl", ".gz"):
            hashes.append({"alg": "SHA-256",
                           "content": hashlib.sha256(f.read_bytes()).hexdigest()})

    doc = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        # No `metadata.timestamp` and no serialNumber: this document is meant to be
        # BYTE-REPRODUCIBLE from the same artifacts. A timestamp would make two SBOMs
        # of one release differ, and then nobody could use a diff to answer the only
        # question an SBOM is for.
        "metadata": {
            "component": {
                "type": "application",
                "bom-ref": f"pkg:pypi/{name}@{version}",
                "name": name,
                "version": version,
                "description": read_field(pyproject, "description"),
                "licenses": [{"license": {"id": "MIT"}}],
                "purl": f"pkg:pypi/{name}@{version}",
                "hashes": hashes,
            },
            "tools": [{"name": "ci/make-sbom.py", "vendor": "xcheck"}],
        },
        "components": [],
        "dependencies": [{"ref": f"pkg:pypi/{name}@{version}", "dependsOn": []}],
    }
    target = out / "sbom.cdx.json"
    target.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {target.name}: {name} {version}, "
          f"{len(doc['components'])} components, 0 runtime dependencies")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
