# Python Protocol Collection Pollution

Date: 2026-07-28

## Release-blocking symptom

The beta.3 full-suite collection imported
`general_ludd.issue_sources.gitlab_issues` before unrelated controller tests.
After that import, Starlette/AnyIO protocol checks treated an
`anyio.Event` as an awaitable and failed with:

```text
TypeError: 'Event' object can't be awaited
```

Focused controller runs stayed green because they never imported the polluting
module. The failure was therefore order-dependent and only reproducible with
the complete collection.

## Root cause

`gitlab_issues.py` added a `__protocol_attrs__` property to the private,
process-global `typing._ProtocolMeta` metaclass. Every later `Protocol` class in
the interpreter inherited that mutation. This changed third-party runtime
protocol checks and leaked GitLab adapter metadata into AnyIO and Starlette.

The repair scopes `__protocol_attrs__` to Gludd's `HTTPResponse` and
`HTTPTransport` protocol classes with `type.__setattr__`. It never mutates the
shared stdlib metaclass.

## Upstream and community evidence

- The Python typing community documents that runtime-checkable protocols use
  `_ProtocolMeta.__instancecheck__`, and that descriptor/property inspection can
  execute code or raise during an apparently simple `isinstance` check:
  [python/typing#1363](https://github.com/python/typing/issues/1363).
- A Python.org design discussion concluded that changing protocol runtime
  behavior would require monkey-patching the private `_ProtocolMeta`; the thread
  treats that as a reason to use a separate implementation rather than patching
  the shared metaclass:
  [Optional strict mode for `@runtime_checkable`](https://discuss.python.org/t/optional-strict-mode-for-runtime-checkable/88383).
- Python 3.14 moved annotation behavior under PEP 649 and changed what custom
  metaclasses see during class creation, illustrating why private metaclass and
  annotation internals are not stable extension points:
  [python/cpython#139186](https://github.com/python/cpython/issues/139186).
- Another Python 3.14 annotation issue showed that an `AttributeError` during
  annotation evaluation could silently alter the result returned by
  `annotationlib.get_annotations`, reinforcing that shared annotation/runtime
  typing state can create non-local failures:
  [python/cpython#125618](https://github.com/python/cpython/issues/125618).

## Permanent guardrails

1. Never add fields, descriptors, or methods to private stdlib metaclasses or
   other process-global third-party classes.
2. Attach compatibility metadata to the exact Gludd-owned class that needs it.
3. Test that each protocol reports only its own members and that importing the
   adapter does not modify `typing._ProtocolMeta`.
4. When a test passes in isolation but fails after full collection, minimize
   the imported module set with:

   ```text
   make bisect-test-collection-polluter \
     CANDIDATE_DIR=tests/unit \
     SENTINEL_TEST=tests/path/test_file.py::test_name \
     START=0 \
     LIMIT=0
   ```

5. Keep a full-collection sentinel in release diagnostics so import pollution
   cannot hide behind focused test success.

## Verification

- `tests/unit/test_issue_sources_gitlab_issues.py` pins per-class protocol
  metadata and the untouched shared metaclass.
- `tests/unit/test_bisect_pytest_collection_polluter.py` validates the reusable
  delta-debugger.
- The exact full-collection controller sentinel passes after the repair.
