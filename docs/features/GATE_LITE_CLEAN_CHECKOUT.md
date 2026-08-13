# Gate-Lite Clean-Checkout Contract

## Problem

`make gate-lite` previously required `verify-opencode-backup` before any
validation phase. That verifier intentionally fails when `.opencode.orig/` is
absent, while the same directory is intentionally listed in `.gitignore`.
A clean clone or disposable CI workspace therefore could not run the local
validation gate, even though the tracked `.opencode/` tree was intact.

## Contract

- `gate-lite` validates only repository-owned, checkout-reproducible state.
- `verify-opencode-backup` remains strict when called directly.
- `backup-opencode` and `restore-opencode` remain explicit local recovery
  operations; this change does not weaken their file or export-parity checks.
- The gate continues to run tracked OpenCode integrity, syntax, import, runtime,
  and hook-invocation checks.

## Practitioner evidence

GitHub users running self-hosted Actions describe workspace contamination as a
long-lived reliability and security issue and recommend starting clean, then
opting into caches explicitly:
<https://github.com/orgs/community/discussions/154525>.

OpenCode users likewise report that cache and configuration locations can drift
across releases and break plugin data lookup, reinforcing that regenerable local
cache or recovery state must not be a hidden gate prerequisite:
<https://github.com/anomalyco/opencode/issues/12222>.

## ZDD, security, and resources

This is a validation-only dependency change. It introduces no service restart
and no data-plane downtime. Clean-checkout reproducibility removes reliance on
possibly contaminated local recovery state. The existing explicit backup
verifier remains fail-closed before restore, and the gate's tracked OpenCode
integrity checks remain unchanged. No additional worker or disk-intensive phase
is added.

## Verification

A structural regression asserts that the `gate-lite` prerequisite line cannot
include the ignored recovery directory verifier. The full gate-lite replay must
then reach and complete its tracked validation phases from a clean development
worktree.
