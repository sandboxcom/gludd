# Model Gateway Module Split

## Scope

The historical `general_ludd.models.gateway` module remains the public facade.
The implementation is layered as follows:

1. `gateway_types.py` owns stable profiles, responses, limits, protocols,
   exceptions, and pure compatibility helpers.
2. `gateway_streaming.py` owns bounded streaming, streamed retries, and streamed
   fallback traversal through `GatewayStreamingMixin`.
3. `gateway.py` owns provider routing, buffered calls, retries, administration,
   and explicit identity-preserving aliases for every extracted symbol.

No caller migration is required. Existing imports, signatures, exception
hierarchies, and `ModelGateway` method lookup remain stable.

## Long-Lived Community Evidence

- A 2011 Stack Overflow thread on
  [splitting Python modules](https://stackoverflow.com/questions/7799286/how-to-split-a-python-module-into-multiple-files)
  recommends breaking cycles with a lower-level base module and a separate
  aggregation facade. That maps to `gateway_types` as the dependency floor and
  `gateway` as the compatibility facade.
- A 2016-2025 Stack Overflow thread on
  [type hints without cyclic imports](https://stackoverflow.com/questions/39740632/python-type-hinting-without-cyclic-imports/72324393)
  documents the recurring large-class/mixin cycle. The split uses postponed
  annotations plus `TYPE_CHECKING`-only host declarations, so the streaming
  layer never imports the concrete `ModelGateway` at module import time.
- A 2023 Stack Overflow discussion on
  [splitting a class across files](https://stackoverflow.com/questions/76213817/split-a-python-class-over-multiple-files)
  recommends a one-way base → mixin → concrete-class layering rather than
  bottom-of-file import tricks. The gateway dependency graph follows that
  direction.
- A 2022 pytest discussion describes how
  [`from module import VALUE` can retain an unpatched reference](https://github.com/pytest-dev/pytest/discussions/10027)
  after callers patch the original module. Existing Gludd tests patch
  `general_ludd.models.gateway.default_token_tracker` and `gateway.logger`, so
  streaming resolves those two seams lazily through the facade and pins the
  behavior with a compatibility test.
- A 2014 Stack Overflow question on
  [module splits without breaking compatibility](https://stackoverflow.com/questions/24100558/how-can-i-split-a-module-into-multiple-files-without-breaking-a-backwards-compa)
  identifies facade re-exports as the established migration technique. Gludd
  uses direct aliases, and tests assert object identity rather than merely
  importability.

## ZDD and Rollback

This is an in-process code-layout change: it introduces no schema, protocol,
configuration, or stored-state migration. Zero-downtime deployment therefore
uses the normal rolling replacement path; old and new workers accept the same
inputs and expose the same Python API. Rollback is the inverse code deployment
because no data written by the new version requires conversion.

Release verification must retain the following gates:

- facade imports and extracted-object identity;
- provider routing, retry, fallback, streaming, payload, and error behavior;
- historical facade monkeypatch targets;
- line and branch coverage at or above the project thresholds; and
- every owned source file below the repository line limit.
