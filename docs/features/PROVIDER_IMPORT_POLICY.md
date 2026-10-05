# Provider Import Policy

Status: S83.130

## Problem

Model profiles persist a provider package and class hint. The registry previously
passed those strings directly to Python's import machinery. A malformed, tampered,
or over-permissive profile could therefore select any importable module attribute in
the service environment instead of one of Gludd's reviewed model adapters.

Package installation and Python import are separate boundaries. The registry may
report an approved optional package as missing, but configuration must never turn an
arbitrary module path into code execution or trigger automatic installation.

## Decision

- Derive the immutable package/class policy from `PROVIDER_PRESETS`, whose changes
  are source-reviewed, plus the documented air-gapped
  `langchain_community:ChatVLLM` profile.
- Canonicalize distribution-style hyphens to Python import underscores, then require
  the exact package/class pair before registration.
- Revalidate stored metadata before both module discovery and import. This preserves
  the boundary even if an in-memory registry entry is replaced after registration.
- Require the imported attribute to be a class. A function, object, or string under
  an approved name fails with a stable `TypeError`.
- Keep dependency handling declarative: an approved missing package creates a todo;
  the service never installs or executes a package as a side effect.

New built-in providers enter through the preset table and focused tests. If Gludd
later needs third-party providers, use package metadata entry points with a
Gludd-owned group and an explicit interface/version check rather than reopening
arbitrary configuration-driven imports.

## Maintained tooling and practitioner evidence

- The [PyPA plugin discovery guide](https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/)
  documents package-metadata entry points as the maintained discovery mechanism for
  separately distributed plugins. The corresponding
  [entry-points specification](https://packaging.python.org/en/latest/specifications/entry-points/)
  gives consumers an explicit group, name, and object-reference contract.
- [CPython issue #125140](https://github.com/python/cpython/issues/125140) is a
  practitioner report where a local module unexpectedly shadowed an intended import
  and was escalated as a security issue. It demonstrates why “installed and
  importable” is not an authorization decision.

The current fixed preset contract does not need a new plugin framework, so this
change uses a small immutable set and the standard library instead of duplicating a
loader. Entry points are the recorded migration path if extensibility becomes a
real requirement.

## Security, resources, and observability

Rejected targets fail before `find_spec`, `import_module`, network access, credential
lookup, or dependency work. Membership checks are bounded by the small reviewed set;
there is no filesystem walk or subprocess. Stable `ValueError`, `ImportError`, and
`TypeError` boundaries distinguish policy rejection, absent approved dependencies,
and invalid exported objects. The existing dependency todo remains the observable
operator action for an approved package that is not installed.

## Zero-downtime delivery and rollback

The change has no schema, secret, API, or wire-format migration. All preset-backed
profiles and the documented air-gapped vLLM profile retain their behavior during a
rolling deployment. Unsupported targets fail locally before work begins, so mixed
versions do not corrupt shared state. Promote only after focused compatibility,
branch coverage, static checks, the project gate, and remote CI are green. Rollback
is the single feature commit; no data migration or outage is required.
