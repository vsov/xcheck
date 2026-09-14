"""`python3 -m xcheck …` — the same entry point as `bin/xcheck` and the console
script, for an environment where neither the repo checkout nor `pip install` is
available but the package is importable."""

import sys

from xcheck.cli import main

if __name__ == "__main__":
    sys.exit(main(["xcheck"] + sys.argv[1:]))
