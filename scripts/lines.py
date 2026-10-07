#!/usr/bin/env python3
"""The file-length gate (docs/design.md gate G8): no source, script, doc or
config file over 2,000 lines.

    python3 scripts/lines.py

Exit 1 on any failure.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAX_LINES = 2000
SUFFIXES = {".cho", ".py", ".md", ".toml", ".yml"}
SKIP_DIRS = {".git", "build", "target"}


def length_problems():
    problems = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in SUFFIXES:
            continue
        if SKIP_DIRS & set(path.relative_to(ROOT).parts):
            continue
        n = len(path.read_text().splitlines())
        if n > MAX_LINES:
            problems.append("%s: %d lines, the limit is %d" % (path.relative_to(ROOT), n, MAX_LINES))
    return problems


def main():
    problems = length_problems()
    for p in problems:
        print("FAIL " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
