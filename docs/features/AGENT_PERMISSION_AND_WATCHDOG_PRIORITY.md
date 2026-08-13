# Agent permission and watchdog priority contracts

## Contract

OpenCode workspace permissions use the supported global schema and flow to
project agents through normal configuration inheritance. File modification is
globally allowed through `edit`; a second `write` entry is neither required nor
supported by this project contract. Read access remains an ordered rule map so
the broad allow is followed by fail-closed `.env` and `.env.*` denials, with the
non-secret `.env.example` template allowed last. Bash remains deny-by-default
with only `make *` allowed. External-directory access remains separately
deny-by-default.

The watchdog may observe several actionable conditions during one polling
cycle. The plain-text directive channel therefore applies a deterministic
severity order:

1. stalled push;
2. stalled CI;
3. task anomaly;
4. generic continue directive;
5. under-floor dispatch warning.

Every condition is still logged. A lower-priority condition cannot replace an
already-visible higher-priority directive, so an under-floor check cannot erase
the recovery action for a real stalled push later in the same cycle.

## Practitioner evidence

- OpenCode issue [#8832](https://github.com/anomalyco/opencode/issues/8832)
  records a practitioner report where global permissions did not appear to flow
  to project agents. Keeping one supported global permission layer, instead of
  duplicating a divergent tool map into every agent, makes that inheritance
  boundary explicit and testable.
- OpenCode issue [#12566](https://github.com/anomalyco/opencode/issues/12566)
  documents delegated agents unexpectedly prompting despite a global wildcard
  allow, while issue [#26700](https://github.com/anomalyco/opencode/issues/26700)
  documents the inverse problem of inherited parent denials over-constraining a
  delegated agent. The project tests the actual global schema and its ordered
  secret exceptions rather than assuming either flattening behavior.
- Alertmanager's maintained [inhibition
  configuration](https://github.com/prometheus/alertmanager/blob/main/docs/configuration.md)
  codifies the operational pattern of suppressing a lower-relevance alert when
  a more relevant related alert is active. The watchdog uses the same principle
  only for the single directive channel; it preserves complete logs for
  diagnosis.

## Safety, resources, and zero-downtime delivery

The permission repair does not widen secret, shell, or external-directory
access. Unknown Bash commands and secret environment files remain denied, and
the `.env.example` exception remains narrowly ordered after the denials.

Directive arbitration performs at most one small text-file read and write per
candidate. It creates no process, daemon, queue, network request, or database
state. The change is compatible with mixed watchdog versions because the file
format remains plain text. Promotion needs no migration or restart; rollback is
a source revert, and either version can consume the same directive file.

## Verification

- The exact permission regressions prove the supported global file-tool schema,
  ordered `.env` policy, and make-only Bash boundary.
- The stalled-push regression forces a simultaneous under-floor condition and
  proves that both signals are logged while the final directive remains the
  stalled-push action.
- The complete agent configuration and watchdog framework suites must remain
  green with zero warnings, Ruff and strict mypy findings, and at least 85%
  aggregate and 75% per-file coverage for touched production code.
