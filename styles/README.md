# `styles/` — how xcheck talks to a human

One file lives here. `styles/eli5.md` is the single source of the wording style every xcheck
launcher skill uses when it is talking to a person: ordinary prose instead of a compressed
telegraphic register, terms of art explained the first time they appear, and four kinds of
text reproduced character for character.

## The source and the six copies

`styles/eli5.md` has two parts. Everything between

```
<!-- xcheck:style:begin -->
<!-- xcheck:style:end -->
```

is **the block**. Everything else in the file — the YAML frontmatter and this explanation —
is not.

The block is copied verbatim into all six launcher skills:

```
skills/xcheck-plan/SKILL.md        skills/xcheck-remediate/SKILL.md
skills/xcheck-audit/SKILL.md       skills/xcheck-verify/SKILL.md
skills/xcheck-triage/SKILL.md      skills/xcheck-status/SKILL.md
```

Six literal copies, not one import, and that is forced rather than chosen: `skills/` is
pruned from the sdist, the wheel packages only `xcheck`, and a skill is a Markdown file an
agent reads — there is no moment at which Python could hand it the text. What makes six
copies safe is that a generator writes them and a byte-identity gate compares them:

```bash
python3 ci/render-style.py
```

```bash
python3 ci/render-style.py --check
```

The first rewrites every copy from the source. The second exits non-zero and prints a diff
if any copy differs by a single byte. `tests/test_human_style.py` runs that check against
the repository, and watches it fail on a deleted block and on one changed character, so the
gate's shape is known rather than assumed.

**Edit the block here. Never edit a copy** — the next render overwrites it, and until then
the six skills disagree about how to talk to you.

## Where the block actually arrives

A skill file is not what an agent reads. Four transforms sit in between, and
`tests/test_style_delivery.py` asserts on the bytes each one *produced*:

| path | transform |
|---|---|
| Claude skills install | copy into `~/.claude/skills/<name>/SKILL.md` |
| Codex skills install | copy into `~/.agents/skills/<name>/SKILL.md` |
| OpenCode generation | new frontmatter, then `awk 'c==2{print} /^---$/{c++; next}'` |
| plugin bundles | `.claude-plugin` and `.codex-plugin` ship `skills/` |

The third one is why the block sits **below** the frontmatter and not inside it. The
OpenCode command generator keeps only what follows the second `---`, so a directive written
into frontmatter would be present in the file and absent from every OpenCode command built
from it. `launchers/install-launchers.sh` refuses to package a skill whose block does not
survive that exact `awk` pipeline, and its `selftest` proves the refusal by planting a
payload where the block has been moved up into the frontmatter.

## The four exceptions, and why each one is an exception

Plain wording is the goal everywhere. These four are reproduced exactly and never reworded,
because each is something a person will later have to compare against a record:

1. **Quoted evidence** — a line lifted out of a file or a transcript, with the path and line
   number it came from. Reworded evidence no longer matches the file it came from, so it can
   no longer be checked against it, and unverifiable evidence is not evidence.
2. **Finding ids** — `F-0042`, `CF-0003`, `RP-0007` and anything shaped like them. An id is
   how two documents, two sessions and two people refer to the same thing. Paraphrase it and
   the cross-reference is gone.
3. **§5 statuses** — the words §5 uses for where a finding stands, spelled the way §5 spells
   them. The statuses are a fixed vocabulary the ledger validates against; a synonym is not
   a status, it is prose that resembles one.
4. **Copy-paste commands** — anything a person is meant to run, character for character as
   it must be typed. A command improved for readability is a command that does something
   else, or nothing.

Explaining what one of these means is welcome and encouraged. Substituting your own phrasing
for one is not.

## Installing the style into your own Claude — optional

`styles/eli5.md` carries YAML frontmatter (`name`, `description`) so that it can be used as
a Claude output style on its own:

```bash
cp styles/eli5.md ~/.claude/output-styles/
```

**This is optional.** Nothing in xcheck requires it. The six skills already carry the block,
so they behave the same whether or not that file exists on your machine, and xcheck itself
never writes into `~/.claude`, `~/.codex` or `~/.agents`. The one program in this repository
that writes there is `launchers/install-launchers.sh`, which does nothing until you run it
by hand. Copy the file only if you want the same wording in sessions that have nothing to do
with xcheck.

## What this does and does not do

The block is **delivered** to every launcher skill through every packaging path, and this
repository proves that by reading the bytes each transform produced — but it **does not
verify** that a model then wrote in the style the block asks for, because a model's prose
style has no deterministic oracle to check it against.

So: arrival is measured, and obedience is not. If you need to know whether a particular
session actually spoke plainly, read the session.

## Scope

The block governs the six launcher skills — the sessions a person starts by hand and then
talks to. It does not govern:

- **xcheck's own command-line output.** Refusals, status lines and error messages are fixed
  strings that dozens of tests compare against verbatim and that release documents quote.
  They are evidence, and evidence is not rephrased.
- **The orchestrated children.** The prompts in `xcheck/runner.py` drive sessions whose
  output a person reads afterwards as a log, not sessions a person is in conversation with.
