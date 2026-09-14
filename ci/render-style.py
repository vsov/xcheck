#!/usr/bin/env python3
"""Copy the ELI5 style body from `styles/eli5.md` into every launcher skill.

One definition, six readers. `skills/` is pruned from the sdist and the wheel packages
only `xcheck`, so a skill cannot read the style out of Python at run time — the bytes
have to be literally present in each `SKILL.md`. Six copies is therefore forced, and a
byte-identity gate is what makes six copies safe rather than six places to drift.

    python3 ci/render-style.py            # write the block into every skill
    python3 ci/render-style.py --check    # report drift as a diff, exit 1 if any

The block goes BELOW the frontmatter, never inside it. OpenCode commands are generated
with `awk 'c==2{print} /^---$/{c++; next}'`, which drops everything above the second
`---`; a directive placed there would reach nobody.

Within the body it goes after the launch-mode declaration and the role paragraph, and
immediately BEFORE the numbered procedure — a style instruction that arrives after the
work has been described has already been read too late. The `##` heading it opens is
empty of launch-mode names, and `scan_claims` reports the innermost NON-EMPTY frame, so
the procedure below it keeps the `uncontained-direct` scope the file header gives it.
"""

import argparse
import difflib
import pathlib
import re
import sys

BEGIN = "<!-- xcheck:style:begin -->"
END = "<!-- xcheck:style:end -->"
STYLE = "styles/eli5.md"
SKILLS = "skills/*/SKILL.md"


def style_body(root):
    """The bytes between the markers in the source file, markers excluded."""
    src = (root / STYLE).read_text()
    try:
        b = src.index(BEGIN) + len(BEGIN)
        e = src.index(END, b)
    except ValueError:
        sys.exit(f"{STYLE}: both {BEGIN} and {END} must be present")
    return src[b:e].strip("\n")


def skill_files(root):
    """Every launcher skill, sorted, so the order a failure reports is stable."""
    return sorted((root / "skills").glob("*/SKILL.md"))


# The first line of the numbered procedure. Every launcher skill opens the same way:
# frontmatter, `# xcheck launcher — <Role>`, the launch-mode declaration, a role
# paragraph, then `1. **Preflight.** …`.
PROCEDURE = re.compile(r"^1\. ", re.M)


def rendered(text, body):
    """`text` with the block set to `body` — replaced in place, or inserted.

    Both paths emit the SAME terminator. They did not once: the insert path kept the
    blank line before the procedure and the replace path ate it, so a render and the
    `--check` that followed disagreed by one byte in every file and the gate reported
    drift forever. An idempotent renderer is the whole premise of a drift check.
    """
    block = f"{BEGIN}\n{body}\n{END}\n\n"
    if BEGIN in text and END in text:
        head = text[: text.index(BEGIN)]
        tail = text[text.index(END) + len(END) :].lstrip("\n")
        return f"{head}{block}{tail}" if tail else f"{head}{block}".rstrip() + "\n"
    m = PROCEDURE.search(text)
    if m:
        return f"{text[:m.start()]}{block}{text[m.start():]}"
    # No numbered procedure to sit in front of. Append rather than guess a position —
    # and say so, because a silent fallback is how a file quietly stops matching the
    # placement every other file has.
    print("note: no `1. ` procedure found; the block was appended instead")
    return f"{text.rstrip()}\n\n{block}".rstrip() + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="report drift instead of fixing it; exit 1 if any copy differs")
    ap.add_argument("--root", default=None,
                    help="tree to operate on (default: the repository this script is in)")
    args = ap.parse_args(argv)

    root = pathlib.Path(args.root) if args.root else pathlib.Path(__file__).resolve().parent.parent
    body = style_body(root)
    files = skill_files(root)
    if not files:
        sys.exit(f"no skills matched {SKILLS} under {root} — nothing was checked, "
                 f"which is not the same as nothing being wrong")

    drifted, written = [], []
    for f in files:
        before = f.read_text()
        after = rendered(before, body)
        if before == after:
            continue
        if args.check:
            drifted.append(f)
            sys.stdout.writelines(difflib.unified_diff(
                before.splitlines(keepends=True), after.splitlines(keepends=True),
                fromfile=f"{f.relative_to(root)} (on disk)",
                tofile=f"{f.relative_to(root)} (expected)"))
        else:
            f.write_text(after)
            written.append(f)

    verb = "checked" if args.check else "rendered"
    print(f"style block: {len(body.encode())} bytes from {STYLE}")
    print(f"{verb} {len(files)} skill(s): " +
          ", ".join(f.parent.name for f in files))
    if args.check:
        if drifted:
            print(f"DRIFT in {len(drifted)}: " +
                  ", ".join(str(f.relative_to(root)) for f in drifted))
            return 1
        print("every copy is byte-identical to the source")
        return 0
    print(f"updated {len(written)}, already current {len(files) - len(written)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
