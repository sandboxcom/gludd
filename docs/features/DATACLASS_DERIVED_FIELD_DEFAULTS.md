# Dataclass Derived-Field Defaults

## Status

Implemented for beta.4. Every repository dataclass field excluded from its
generated constructor now declares an explicit value or factory in its field
metadata, including fields that are recomputed in `__post_init__`.

## Contract

`init=False` means a caller cannot select a field through the generated
constructor; it does not mean the field may omit a default. Gludd applies these
rules:

1. Every `init=False` field has an explicit `default` or `default_factory`.
2. Derived immutable values use a safe class-level default and are recomputed
   synchronously in `__post_init__`.
3. Mutable values use a per-instance factory and never a shared class object.
4. Replacement construction must recompute derived values from constructor
   inputs rather than copying stale derived state.

`OPAQUEConfig.hash_name` therefore defaults to `sha256`, remains excluded from
the constructor, and is synchronously derived from the validated curve.
`TrafficSplitter._cumulative` receives its own list before post-initialization
builds weighted boundaries. `StreamState` receives two distinct flow-window
objects, and post-initialization sizes those existing objects from the public
send and receive limits without allocating replacements.

## Practitioner evidence

A long-lived
[Stack Overflow report from 2021](https://stackoverflow.com/questions/70453792/dataclass-attributes-with-init-false-do-not-show-in-vars)
shows that an `init=False` class default may be visible through attribute lookup
while still being absent from instance storage. A
[2023 follow-up involving slotted dataclasses](https://stackoverflow.com/questions/76733134/why-slots-true-makes-a-dataclass-to-ignore-its-default-values)
demonstrates the sharper failure mode: when class fallback is unavailable, an
uninitialized field raises `AttributeError`. The frequently viewed
[optional-field discussion](https://stackoverflow.com/questions/70809438/python-dataclasses-with-optional-attributes)
also records recurring confusion between excluding a field from `__init__` and
giving it a value.

These reports span multiple Python releases and serialization/introspection
styles. Gludd does not depend on class-attribute fallback. The generic audit
checks field metadata, and deterministic model tests check post-initialized
instance state. This remains consistent with the maintained
[dataclasses field contract](https://docs.python.org/3/library/dataclasses.html#dataclasses.field).

## Security and resource boundaries

The OPAQUE hash is derived only from the allowlisted curve, so an untrusted
caller cannot inject a downgraded hash through `OPAQUEConfig.__init__`. The
safe default exists only as metadata and immediate construction state;
`__post_init__` always validates the curve and overwrites it before the object
is returned. Unsupported curves still fail closed.

Factories prevent traffic boundaries or flow-control counters from being
shared between requests or streams. Stream construction allocates exactly the
two windows it retains and mutates their sizes in place. The change adds no
processes, threads, locks, dependency, network access, persistent storage, or
background cleanup, and it does not alter window arithmetic or hash selection.

## Zero-downtime delivery and rollback

All repaired state is ephemeral and process-local. There is no database,
configuration, wire-format, or serialized-record migration. Old and new workers
may overlap safely during a rolling deployment because no affected dataclass
instance crosses the process boundary. Promote after focused tests, coverage,
static checks, and the full gate are green. Rollback is a source revert or
traffic shift to the previous worker set; no data repair or dual-write period is
required.

## Verification

- The repository-wide `init=False` audit passes without weakening its generic
  discovery or assertion.
- Focused tests prove hash non-injection and recomputation, per-instance traffic
  state, and independent correctly sized stream windows.
- Aggregate branch coverage remains at least 85 percent, and every touched
  production file remains at least 75 percent for both line and branch coverage.
- Ruff, strict mypy, docstrings, Markdown, feature-spec, and task-ledger checks
  remain warning-free and suppression-free.
