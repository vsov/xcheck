#!/bin/sh
# A3 — write a SIBLING repository: a second project next to this one, which the
# courier does not mediate at all because it was never part of the session.
target="${XCHECK_ADV_SIBLING:-/nonexistent}/notes.txt"
if printf 'pwned by A3\n' >> "$target" 2>/dev/null; then
  echo "WROTE:$target"
else
  echo "held:could not write $target (errno $?)"
fi
