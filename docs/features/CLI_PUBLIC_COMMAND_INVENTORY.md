# Public CLI Command Inventory Contract

Gludd exposes both `payment` and `smoke` as top-level commands.

- `payment` is the public entry point for the payment-vault subcommands.
- `smoke` is the canonical release-smoke spelling.
- `test smoke` remains a compatibility spelling for existing automation.
- Parser inventory tests must agree with `build_parser()`, the manual page, and
  the focused command contracts. They must not deny commands that those public
  interfaces intentionally register.

## Practitioner evidence

Python's long-running
[argparse issue 36664](https://bugs.python.org/issue36664) records user friction
around nested subparser aliases and help consistency. Keeping one canonical
top-level spelling while retaining an explicit nested compatibility path avoids
making automation depend on undocumented parser structure.

## ZDD, rollback, and security

This is a test-contract correction only: no production parser, schema, daemon,
credential, or network behavior changes. It therefore requires no data
migration or downtime. Rollback is the prior test commit. Payment handlers keep
their existing authorization and vault boundaries, and smoke retains its
bounded-cost and explicit live-probe controls.
