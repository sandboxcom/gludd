# Deep Exception and Error Contract

Status: beta contract for `v0.1.0-beta4`.

## Purpose

Gludd exposes exceptions from many independent domains. Reusing a short class
name in unrelated modules makes tracebacks ambiguous and makes a broad import or
automated exception inventory unable to identify the failing subsystem. Bare
exceptions without descriptive documentation also leave callers guessing
whether an error is invalid input, an unsafe runtime state, or a retryable
transport failure.

The deep audit in `tests/unit/test_error_codes_deep.py` is the executable
contract. It requires unique primary exception class names, descriptive
docstrings, importable classes, intentional inheritance, public names, and
constructors that preserve the exception message.

## Primary names and compatibility

The following primary names are domain-specific. Existing imports remain valid
as identity-preserving aliases, so an old `except` target catches the exact same
class object and serialized class messages do not change.

| Domain | Primary name | Compatibility alias |
| --- | --- | --- |
| TLS 1.3 | `TLSHandshakeError` | `HandshakeError` |
| Hibernation | `HibernationIntegrityError` | `IntegrityError` |
| Pause store | `PauseStoreIntegrityError` | `IntegrityError` |
| ServiceNow | `ServiceNowSSRFError` | `SSRFError` |
| Security resolver | `SecuritySSRFError` | `SSRFError` |
| Kubernetes connector | `KubernetesConfigError` | `_ConfigError` |

`HorizonMetrics` is the primary name for rollout prediction-error data.
`HorizonError` remains an identity-preserving compatibility alias, but the
value is deliberately not an exception. This prevents a metric record from
appearing in exception discovery while avoiding a beta-cycle constructor
break.

The audit also classifies invalid key, parser, allocation, and WAL input as
`ValueError`; runtime download, repository, MCP transport, and model-integrity
failures derive from `RuntimeError`. Existing callers catching `Exception`
continue to work, while callers gain useful narrower catch boundaries.

## Practitioner evidence

Python practitioners have long reported that name shadowing makes import
failures hard to diagnose. CPython issue
[#95754](https://github.com/python/cpython/issues/95754) opened in August 2022
because a local `random.py` silently shadowed the intended module and produced
a misleading failure; the implementation discussion was still active in
December 2023. Maintainers kept the true exception type for compatibility and
improved the diagnostic identity instead. Gludd follows the same principle:
make the primary domain identity specific without changing the underlying
failure semantics or breaking existing catch targets.

## Zero-downtime deployment and rollback

This change is additive at the import boundary. A rolling deployment may contain
old and new workers because every legacy name resolves to the new primary class
within its module. No database, wire-format, process, or configuration migration
is required.

Promotion order:

1. Run the deep audit, focused domain tests, strict type checks, and coverage
   checks on development.
2. Deploy new workers alongside old workers; exception messages and legacy
   imports remain compatible.
3. Observe exception type labels by their new primary names after each worker
   turns over.
4. Promote only after the standard health and gate checks pass.

Rollback is a normal binary rollback. Persisted state is unchanged, and the
legacy aliases remain available on both sides of the rollout. If an external
consumer has already adopted a new primary name, roll forward with the same
aliases rather than deleting that name.

## Security, resources, and observability

Narrower exception bases do not turn fail-closed security rejections into
retryable failures. SSRF, integrity, and credential errors retain their messages,
causes, and catch identity through aliases. The change starts no processes,
allocates no persistent storage, and adds no network activity. Tracebacks become
more observable because primary type names identify their domain.

Coverage must remain at least 85% in aggregate and at least 75% for every
production file. The contract is checked without suppressions; warnings, Ruff,
strict mypy, documentation lint, and task/spec validation must be clean before
promotion.
