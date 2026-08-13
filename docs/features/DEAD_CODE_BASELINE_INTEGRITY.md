# Dead-code Baseline Integrity

Status: beta4 release contract

## Contract

The release gate treats `config/dead_code_baseline.txt` as reviewed policy, not
generated scratch. The private `_dead-code-baseline-refresh` prerequisite is
deliberately read-only: it computes the current dead-symbol set and requires exact
parity with the tracked baseline. New dead symbols and obsolete allowances are both
named and fail the gate.

Updating policy is a separate, explicit `make dead-code-baseline` action. The
operator reviews its tracked diff, commits that small change, and reruns the clean
gate. A time threshold, checkout mtime, operating-system-specific `stat`, hidden
output, or ignored exit code cannot authorize a baseline change.

## Security and test integrity

The former 24-hour auto-refresh ran before enforcement and silently accepted every
new finding. It also hid regeneration failures. The replacement preserves evidence:
comparison is read-only, output is visible, any drift is non-zero, and only a reviewed
commit can alter the allowlist. The implementation reuses the project's existing AST
checker rather than introducing another scanner.

The focused contract covers a missing baseline, exact parity, stale allowances,
mutually exclusive read/write modes, and byte-for-byte non-mutation. Its 22 tests
reach 85.42% branch coverage for the checker. The aggregate release gate remains at
least 85%, with no source file below 75%.

## Zero-downtime deployment and rollback

This is build-time policy only and touches no running service, schema, network path,
or credential. Development is promoted to master only after a clean full gate and
exact-SHA CI. Rollback restores the prior checker and baseline commit, but it must
never restore automatic acceptance; no migration or service interruption is needed.

## Resources and observability

Each gate performs one bounded AST scan and prints either the exact current entry
count or every added/stale key. It creates no daemon, temporary worktree, cache, or
background process and makes no repository write. This keeps parallel projects
namespaced and prevents an elapsed wall-clock threshold from turning a clean checkout
dirty.

## Practitioner evidence

The long-lived Stack Overflow discussion
[“Is there a simple way to use vulture with Django?”](https://stackoverflow.com/questions/12101463/is-there-a-simple-way-to-use-vulture-with-django)
documents the durable practitioner pattern: dynamic-framework false positives require
an explicit whitelist that is passed to the analyzer. Vulture's maintained guidance
likewise recommends whitelists because the analyzer can validate that the allowlisted
names still exist. Gludd applies that lesson as an exact reviewed set: automation may
validate the whitelist, but it may not silently rewrite it before enforcement.
