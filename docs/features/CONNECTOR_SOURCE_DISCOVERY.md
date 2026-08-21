# Connector source discovery

## Contract

Operator configuration may select only concrete modules in
`ConnectorRegistry`'s production-owned allowlist. Package infrastructure such as
`cursor_adapter`, normalization, ingestion, and registry helpers is excluded
before import. The health-contract test consumes that same allowlist and still
requires every selectable module to define one `*Source` or `*Client` with a
callable `health()` method.

This prevents filesystem layout from silently expanding the public plugin
surface. A helper added beside connectors is not a connector unless production
explicitly admits it.

## Practitioner evidence (reviewed 2026-08-20)

A [July 2025 Python.org practitioner discussion](https://discuss.python.org/t/best-practices-for-managing-dynamic-imports-in-plugin-based-architecture/99482)
contrasts `pkgutil.iter_modules()` globbing with explicit plugin registration
and notes that explicit lists improve debugging because loaded plugins cannot be
unknown to the operator. Gludd keeps package scanning only to build its bounded
inventory, then applies a production-owned infrastructure exclusion and
fail-closed config validation.

## Delivery and rollback

- Zero-downtime delivery: the change is import-time metadata only; existing
  connector instances and requests continue without restart or migration.
- Rollback: revert the allowlist commit. No persisted state or config schema is
  changed.
- Resources: discovery remains one bounded package-directory scan at import;
  rejected infrastructure selectors are never imported or instantiated.
