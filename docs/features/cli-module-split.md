# CLI module split

Status: implemented on 2026-10-06.

## Outcome

The public `general_ludd.cli` module remains the console entrypoint and
compatibility facade, but its parser, manual, daemon-process helpers, offline
platform status, and TUI view builders now live in `general_ludd.cli_commands`.
The facade is 2,491 lines, down from 5,129, and every extracted file remains
below the repository's 2,500-line limit.

The split is deliberately one-way: implementation modules do not import the
facade. `build_parser()` passes the facade's current command-handler registry to
the parser builder. This preserves command names, options, help, exit codes,
entrypoint signatures, and tests or embedders that monkeypatch names such as
`general_ludd.cli._cmd_health`. Existing public TUI helper aliases and daemon
PID/configuration seams remain available from `general_ludd.cli`.

## User-forum evidence

- A long-running Stack Overflow discussion about splitting `argparse`
  subcommands across files shows why independently parsing each subcommand is
  fragile. The implementation keeps one canonical parser graph and delegates
  only graph construction:
  <https://stackoverflow.com/questions/57568069/correct-usage-of-argparse-for-subparser-subcommands>
- Pytest's user discussion about values imported at module load documents the
  aliasing problem created by `from module import value`: consumers may have to
  patch every copied name. The registry therefore resolves handlers from the
  facade when a graph is built:
  <https://github.com/pytest-dev/pytest/discussions/10027>
- A separate monkeypatch support question demonstrates that patches must target
  the name actually used by the system under test. Compatibility tests pin the
  established `general_ludd.cli` patch point:
  <https://stackoverflow.com/questions/70083364/pytest-monkeypatch-doesnt-apply-to-imported-function>

## Zero-downtime and rollback

This is a packaging-only refactor: it changes no daemon route, PID-file format,
database schema, on-disk state, deployment protocol, or process lifecycle. The
console script continues importing `general_ludd.cli:main`, so rolling workers
can run old and new packages concurrently. Parser-registry substitution is
guarded and restored in-process, preventing a patched build from contaminating
later parser builds.

Rollback is the inverse of the refactor commit. It requires no migration or
service handoff because persistent and wire formats are unchanged.

## Verification evidence

- TDD red: the split contract initially failed because `cli.py` was 5,129 lines
  and the cohesive modules did not exist.
- Compatibility green: the split contract passed 3/3; the focused legacy CLI
  suite passed 394/394; the coverage run passed 1,816/1,816 tests.
- Coverage: 91.7% aggregate line and 86.2% aggregate branch coverage. Each
  measured source file is at least 75% for both line and branch coverage; the
  minimum branch result is 80.5% for `tui_views.py`.
- Scoped Ruff and strict mypy checks pass for the facade and extracted modules.
