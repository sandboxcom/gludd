# CLI Smoke Command Compatibility

## Status

Implemented for the beta.4 command-line interface.

## Problem

The release help and binary contract advertise `gludd smoke`, while a parser
refactor registered smoke checks only below `gludd test smoke`. Direct binary
users and callers of `build_parser()` therefore received an invalid-choice error
for the documented top-level form even though the handler and argument definition
still existed.

## Contract

The CLI preserves both public spellings:

1. `gludd smoke [provider] [test]` is the canonical release command.
2. `gludd test smoke [provider] [test]` remains a compatibility form.
3. Both parsers use `_add_smoke_arguments()` and dispatch to `_cmd_smoke`.
4. The programmatic `subcommand_map` exposes the canonical parser under
   `"smoke"` with `prog == "gludd smoke"`.
5. Provider, test, cost, timeout, engine, output, and JSON validation stay
   identical between the two forms.

## Zero-Downtime Development Evidence

The clean development gate first reproduced the regression in the programmatic
parser test after 10,026 unit tests had passed. The existing installed-binary
contract also pins equivalent output from both spellings.

After explicit top-level registration, the smoke-focused parser and binary
family is 24/24 green under strict warnings. This changes parser
construction only: it does not alter a daemon route, persisted record, network
protocol, smoke implementation, or running worker. Old and new CLI processes can
therefore overlap during a rolling deployment.

## Security and Resource Boundaries

Both spellings reach the same handler through the same argument builder, so the
top-level form cannot bypass cost ceilings, live/provisioned opt-ins, provider
selection, timeouts, or output validation. Parser construction performs no
network request, creates no background process, opens no file, and allocates only
the bounded argparse objects already required by the nested form.

## Practitioner Evidence

[Python argparse issue 36664](https://bugs.python.org/issue36664) records a
long-lived compatibility discussion in which maintainers declined to rewrite
subparser alias semantics because callers can depend on the selected name. The
recommended practitioner workaround is to maintain an explicit mapping and use
`set_defaults` for stable dispatch identity. Gludd follows that guidance by
registering both public command paths explicitly, sharing their configuration,
and retaining a canonical programmatic map rather than inferring identity from an
argparse alias.
