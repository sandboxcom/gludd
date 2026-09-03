# Self-Improvement Runtime Protocol Boundaries

## Contract

Gludd's self-improvement composition roots inject repository runners, model
constructors, proposal/evaluation callables, persistence adapters, reloaders,
and event publishers through `typing.Protocol`. Method-only adapter protocols
are explicitly `@runtime_checkable`, including composite protocols. This lets
boundary code and tests make a shallow structural `isinstance` check without
requiring adapters to inherit from Gludd classes.

Runtime checks prove only that the named methods exist. Static mypy checks
remain authoritative for parameter and return types. Protocols with public
data members remain static-only because a presence check cannot validate their
state types or invariants. This includes cache metadata, model-lease state,
process identifiers, and optional-runtime module attributes.

## Compatibility

Every runtime boundary is decorated directly, even when it inherits from an
already checkable protocol. This avoids depending on inherited runtime
checkability, which Python documents as deprecated for removal in Python 3.20.
The implementation uses the standard-library decorator and adds no runtime
dependency. Python 3.12 and later freeze protocol members and use static
attribute lookup, so the contract never relies on post-definition monkey
patching or property evaluation.

## Safety and Resources

- Runtime checks do not authorize an adapter, validate a callable signature,
  execute methods, inspect secrets, or widen filesystem/process authority.
- State-bearing protocols remain static-only so a wrong-typed path, digest,
  lease, or PID cannot be mistaken for validated state merely because an
  attribute exists.
- Checks are bounded to a small frozen member set. They create no subprocess,
  network request, model load, cache entry, or persistent artifact.

## Observability and ZDD

The focused contract tests every opted-in adapter with both a matching shape
and a missing-member object, and pins the state-bearing exclusions. The global
protocol audit then verifies the repository-wide ratio without weakening its
50% threshold. These are startup-safe checks: deployment can retain the prior
code while the candidate runs warnings-strict tests, branch coverage, lint,
and strict typing. Rollback is a normal commit revert because no schema,
serialized artifact, daemon lifecycle, or external state changes.

## Practitioner Evidence

- The long-running [Python typing protocol design thread #11](https://github.com/python/typing/issues/11)
  records practitioner concerns dating to 2014 about data attributes, class
  versus instance state, and runtime behavior. Gludd therefore does not turn
  data-bearing protocols into shallow runtime validators.
- The 2016 Stack Overflow question
  [“Protocols cannot be used with isinstance() — why not?”](https://stackoverflow.com/questions/34844514/protocols-cannot-be-used-with-isinstance-why-not)
  shows the persistent user expectation that structural interfaces should be
  usable at runtime and the otherwise surprising `TypeError`. Explicit
  decorators make Gludd's intended runtime seams unambiguous.
- [python/typing issue #1363](https://github.com/python/typing/issues/1363)
  documents the property-evaluation debate that led to static attribute lookup
  in newer Python versions. It reinforces the method-only boundary and the
  rule that runtime checks establish presence, not semantic validity.

The normative behavior and version notes come from the
[Python `typing.runtime_checkable` documentation](https://docs.python.org/3/library/typing.html#typing.runtime_checkable).
