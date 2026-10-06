# CLAUDE.md -- lexsys-mqtt

Read `docs/design.md` before changing anything; it is written before the code and every claim in it is either
measured or marked as a choice. The rules that matter most:

- **lexsys packages are agentic first.** The CLI, the errors and the logs follow lex-sys `docs/agent-toolbox.md`
  (D2 to D7, D11) through the `contract/` package of `lexsys-tools`, pinned by commit and never copied (design
  section 5a): one flag table drives the parser, `introspect` and `skill`; errors are data with a stable
  `<area>.<name>` rule tag, an exit code from the D4 table and, only where a script can apply one safely, a repair
  that never widens authority; logs are bounded NDJSON ending with an `end` record; no payload is ever logged.
  A new flag, rule or log record is not done until it is in the tables, the schema and a fixture.
- **The gate:** `lex-sys fmt --check`, `lex-sys build`, `python3 scripts/manifest.py --check`,
  `python3 scripts/mutants.py`, `python3 scripts/lines.py`. A gate is fixed before the code it judges and must be
  able to fail.
- **No file over 2,000 lines. Every refusal has a rule tag. No input reaches a panic.**
- **Measured claims only.** A claim found false is corrected in place, in the document that made it.
- Commit trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; PRs stay draft until the maintainer
  says merge; no force-push.
