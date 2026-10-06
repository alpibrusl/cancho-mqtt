#!/usr/bin/env python3
"""The scaffold gates: the authority ceiling and the file-length limit.

    python3 scripts/check.py

1. For every [[bin]] in lex-sys.toml, `lex-sys authority <sources> --std --output
   json` must be bounded, hold no foreign symbol, and use only labels named in
   ceiling.toml (docs/design.md section 2, gate G4).
2. No source, script or doc file over 2,000 lines (gate G8).

The compiler is $LEX_SYS, or `lex-sys` on PATH. Exit 1 on any failure.
"""

import json
import os
import pathlib
import subprocess
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAX_LINES = 2000
SUFFIXES = {".ls", ".py", ".md", ".toml", ".yml"}
SKIP_DIRS = {".git", "build", "target"}


def sources(entry):
    files = []
    for s in entry["sources"]:
        p = ROOT / s
        files.extend(sorted(str(x) for x in p.glob("*.ls")) if p.is_dir() else [str(p)])
    return files


def derive(files):
    out = subprocess.run([os.environ.get("LEX_SYS", "lex-sys"), "authority", *files, "--std", "--output", "json"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit("lex-sys authority failed:\n" + out.stdout + out.stderr)
    return json.loads(out.stdout)


def authority_problems():
    problems = []
    with open(ROOT / "lex-sys.toml", "rb") as f:
        project = tomllib.load(f)
    with open(ROOT / "ceiling.toml", "rb") as f:
        ceilings = tomllib.load(f)
    for entry in project.get("bin", []):
        name = entry["name"]
        report = derive(sources(entry))
        ceiling = ceilings.get(name)
        if ceiling is None:
            problems.append("%s: no ceiling in ceiling.toml" % name)
            continue
        allowed = set(ceiling.get("allow", []))
        if not report.get("bounded", False):
            problems.append("%s: bounded is false" % name)
        if report.get("foreign_symbols"):
            problems.append("%s: foreign symbols %s" % (name, report["foreign_symbols"]))
        for label in report["labels"]:
            if label["name"] not in allowed:
                problems.append("%s: %s is not within the ceiling" % (name, label["name"]))
        print("%-6s %s" % (name, ", ".join(l["name"] for l in report["labels"]) or "(nothing)"))
    return problems


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
    problems = authority_problems() + length_problems()
    for p in problems:
        print("FAIL " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
