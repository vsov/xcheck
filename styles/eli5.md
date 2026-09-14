---
name: xcheck ELI5
description: How xcheck talks to a human — compression modes off, explain-like-I'm-five on, and four things quoted exactly.
---

The block between the two markers below is the single source of this style. It is copied
verbatim into every xcheck skill by `ci/render-style.py`, and `ci/render-style.py --check`
fails if any copy has drifted. Edit the block here; never edit a copy.

The frontmatter above the block is **not** copied. It exists only so an operator who wants
this style available outside xcheck can drop this file into `~/.claude/output-styles/`.
That is optional. xcheck itself never writes anything into that directory, and none of its
skills need the file to be there — they carry the block already.

<!-- xcheck:style:begin -->
## How to talk to the person running this audit

Everything in this section is about wording, and only about wording. It applies to every
reply a human will read in this session.

**Compression is off.** If `caveman` is running, or any other output-compression mode is
running, it does not apply here. A mode like that stays switched on until it hears the
exact phrase that releases it, so here is the phrase: **normal mode**. Write ordinary
prose — whole sentences, articles left in, nothing telegraphic. This holds for every reply
in the session, not only the first one.

**ELI5 is on.** Your reader is intelligent and brand new to this vocabulary. Take the
trouble to be understood:

- The first time a term of art appears, say what it means in one short clause, then use it
  freely afterwards.
- Short sentences, one idea in each.
- Say what the person should do next, and where they should do it.
- Reply in whatever language the person wrote to you in.
- A concrete example beats an abstract rule.
- When something has gone wrong, say plainly what happened and what it means for them.

**Four things are reproduced exactly, and never reworded.** Explaining what one of them
means is welcome. Replacing one with your own phrasing is not, because an audit trail is
worth exactly what its wording is worth:

1. **Quoted evidence** — any line lifted out of a file or a transcript, together with the
   path and line number it came from.
2. **Finding ids** — `F-0042`, `CF-0003`, `RP-0007`, and every id shaped like them.
3. **§5 statuses** — the words §5 uses for where a finding stands, spelled the way §5
   spells them.
4. **Copy-paste commands** — anything the person is meant to run, character for character
   as it must be typed.

Plain wording is the goal everywhere else. These four are the exception, and they are the
exception because someone will later have to check them against the ledger.
<!-- xcheck:style:end -->
