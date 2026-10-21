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

## Public protocol introspection on supported runtimes (2026-10-21 UTC)

Published6161 Build39533219987 unit-1b job119007635429 fails the core
method-only protocol test on Python3.11: stdlib protocols do not expose the
private __protocol_attrs__ storage asserted by that test. The complete original
nine-case owner reproduces1FAIL/8PASS0.91s/native1.8s in
/tmp/gludd-v012-protocol-red.log and XML.

Use the existing locked typing_extensions4.15.0 public get_protocol_members API
in the test. It accepts stdlib and backport protocols and was added in4.7.0;
stdlib typing.get_protocol_members starts only in3.13. Both exact singleton
member sets and all existing runtime-decoration/admission/error/result assertions
remain. Add public issubclass checks for both adapters: method-only protocols
must also support subclass checks. Production bases, decorators, signatures,
behavior, dependency declarations and lock stay unchanged. No private collector,
injected metadata, None allowance, platform skip or production-base migration.

The complete owner passes9/1.01s/native2.1s on311 and9/1.20s/native2.4s on312,
fatal warnings/no skips, without sync or acquisition. Preserve
/tmp/gludd-v012-protocol-green-3.11.log and
/tmp/gludd-v012-protocol-green-3.12.log plus XML. The concrete alternatives and
review limits remain in /tmp/gludd-v012-protocol-compatibility-decision.md.
These source receipts do not establish the full gate, exact-SHA CI or release.

The [pinned public API](https://github.com/python/typing_extensions/blob/4.15.0/doc/index.rst)
and [pinned compatibility implementation](https://github.com/python/typing_extensions/blob/4.15.0/src/typing_extensions.py#L3499-L3516)
own introspection across versions. The
[Python3.12 runtime contract](https://docs.python.org/3.12/library/typing.html#typing.runtime_checkable)
describes changed attribute lookup and frozen members; changing production bases
could therefore change311 admission behavior unnecessarily. Firsthand
[CPython113320](https://github.com/python/cpython/issues/113320) in December2023
and [typing_extensions245](https://github.com/python/typing_extensions/issues/245)
in June2023 record version-sensitive construction and mixed-base limitations.
They support the compatibility decision, not a claim that those distinct issues
are the same failure. Static types and behavioral admission remain authoritative.

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

## Persisted Artifact Layer Ownership

The database repository depends only on the side-effect-free core artifact
schema. It does not import the self-improvement staging workflow, even from a
function-local scope. The core schema owns the managed approval-policy value,
the exact legacy artifact discriminator, and bounded SHA-256 binding. Staging
re-exports the established digest API and maps core legacy kinds into its
workflow-facing enum, preserving callers and stored values during a ZDD
rollout.

This boundary makes operations observable rather than merely importable. A
module-graph contract rejects every database-to-business-layer edge, while
focused recovery tests prove the same legacy rows receive the same fail-closed
classification. No event payload, database row, or migration changes, so
rollback is a normal commit revert. Classification parses at most one bounded
approval artifact and starts no process, worker, network request, or model.

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
- The open [CPython import-audit issue #116840](https://github.com/python/cpython/issues/116840)
  reports that repeated imports already present in `sys.modules` do not emit
  the documented audit event. A function-local import can therefore appear
  harmless in one process while hiding a real architecture edge from an
  event-only observer. Gludd enforces the dependency direction from source and
  tests in addition to runtime traces.
- The [Python programming FAQ](https://docs.python.org/3/faq/programming.html#what-are-the-best-practices-for-using-import-in-a-module)
  recommends restructuring modules so recursive imports are unnecessary.
  Moving the shared artifact contract down into the core schema follows that
  advice instead of retaining a local import as a permanent workaround.

The normative behavior and version notes come from the
[Python `typing.runtime_checkable` documentation](https://docs.python.org/3/library/typing.html#typing.runtime_checkable).
