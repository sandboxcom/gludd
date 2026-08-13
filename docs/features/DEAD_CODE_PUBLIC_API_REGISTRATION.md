# Dead-Code Public API Registration

## Status

Implemented for the beta.4 development gate. The contract applies to
`scripts/check_dead_code.py`, `config/dead_code_public_api.txt`, and every
Python module scanned by the checker.

## Problem

The dead-code gate treated a callable used only by tests or downstream consumers as
unused even when it was an intentional public entry point. That produced 48 new
findings during the beta.4 gate replay. Adding those names to the historical
baseline would have hidden future regressions and made the gate less useful.

Python permits dynamic export construction, while legacy modules may not yet satisfy
the repository's public-docstring guard. Executing module code during an audit or
blanket-ignoring names would introduce side effects and allow ambiguous declarations
to suppress real dead code.

## Contract

1. A top-level function or class is registered as public by either:
   - a top-level, statically evaluable `__all__` assignment in its defining
     module; or
   - an exact `source-path:symbol` entry in
     `config/dead_code_public_api.txt`.
2. A static `__all__` value must be a list, tuple, or set containing strings
   only. Dynamic expressions, mixed element types, nested assignments, and
   malformed declarations register nothing.
3. Registry paths must begin with `src/general_ludd/`, end in `.py`, and name
   an identifier that the checker found as a top-level public definition.
4. Registry entries must be unique. Malformed, duplicate, and stale keys terminate
   the audit with exit code 2; they are never silently ignored.
5. Registration is definition-specific. Identically named functions in different
   modules require separate exact keys.
6. Existing call/use detection still wins. Public registration supplements real
   references; it does not replace import, test, or runtime coverage.
7. The historical dead-code baseline must not grow to accommodate intended public
   APIs.
8. Output remains deterministic in text and JSON modes, and update-baseline mode
   records only findings that survive both public-registration rules.

The tracked registry declares 22 reviewed benchmark, algorithm, Ansible, governance,
deployment, security, self-update, and skip-list entry points. It is data consumed by
the checker, not an executable helper script.

## Zero-Downtime Development Evidence

The change followed a failing-first sequence:

- A direct checker test proved that a static `__all__` export was incorrectly
  reported as dead.
- Static registration support reduced the gate findings from 48 to 22.
- A second failing-first test proved the checker ignored an exact registry entry;
  three more red cases proved malformed, stale, and duplicate entries were not
  rejected.
- Exact-key registry support resolved the remaining 22 findings without a baseline
  addition or changes to the affected runtime modules.
- The checker reports zero new symbols across 1,026 files while retaining the
  existing historical baseline.
- Eighteen direct and CLI checker tests pass under warning-strict execution with
  84.33 percent branch coverage for `scripts/check_dead_code.py`.
- The repository import suite proved every candidate module imports without a new
  circularity failure. Its four unrelated structural failures remain visible for
  separate repairs.
- An adjacent 919-test behavioral replay passed 915 cases and exposed one
  randomized indexed-skip-list rank failure plus three unclosed signing-key file
  warnings. Those independent defects remain fail-closed and are tracked for
  separate test-first repairs.

No service restart, schema migration, or runtime behavior change is required. The
checker and registry run only during development and CI, so existing workers and
rolling replacements are unaffected.

## Security and Resource Boundaries

The checker parses source with `ast`, evaluates export literals with
`ast.literal_eval`, and reads the registry as plain text; it never imports or
executes scanned modules. Invalid or dynamic declarations cannot suppress findings.
Exact-key validation prevents one common name from suppressing definitions in
unrelated modules. The scan remains a single bounded repository traversal, creates
no daemon, uses no shared process namespace, and adds no dependency or temporary
script.

## Practitioner Evidence

[Vulture's maintained guidance](https://github.com/jendrikseipp/vulture#handling-false-positives)
documents the long-lived false-positive problem for implicitly called Python code
and recommends syntactically checked whitelist declarations over broad name ignores.

[mypy issue #10198](https://github.com/python/mypy/issues/10198) records years of
practitioner discussion about implicit re-exports. Maintainers and downstream
projects converge on explicit exports such as `__all__` because runtime
importability alone cannot distinguish an implementation import from supported
public API.

Those reports support explicit, syntax-checked registration while the project's
fail-closed requirements rule out dynamic evaluation and blanket suppression.
