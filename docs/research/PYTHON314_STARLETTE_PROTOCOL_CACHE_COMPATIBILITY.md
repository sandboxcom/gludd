# Python 3.14 / Starlette Protocol Cache Compatibility

**Status:** Accepted remediation decision
**Decision date:** 2026-08-01
**Affected lock:** CPython 3.14.0, Starlette 1.3.1, AnyIO 4.13.0

## Decision

Remove Gludd's global mutation of `typing`'s private `_ProtocolMeta` from
`src/general_ludd/issue_sources/gitlab_issues.py`. In particular, delete the
`_PROTOCOL_ATTRS` compatibility map, its property getter/setter, and the
`type.__setattr__` call that installs `__protocol_attrs__` on the shared
metaclass. The GitLab adapter does not need protocol-member introspection to
operate; its runtime-checkable behavior tests are the supported contract.

Stop testing the private `HTTPResponse.__protocol_attrs__` and
`HTTPTransport.__protocol_attrs__` attributes. If future application code has a
real introspection requirement, use
[`typing.get_protocol_members()`](https://docs.python.org/3/library/typing.html#typing.get_protocol_members)
on Python 3.13+ or the
[`typing_extensions.get_protocol_members()`](https://github.com/python/typing_extensions/blob/main/doc/index.rst)
backport on Python 3.11/3.12. Do not mutate a typing metaclass to emulate a
private attribute.

Do not clear ABC caches in application or test setup, monkey-patch Starlette,
or downgrade Starlette to conceal the problem. Cache clearing makes the result
order-dependent again, and the repository intentionally locks Starlette 1.3.1.
The local unsupported metaclass mutation is the root defect and can be removed
without changing the GitLab adapter API.

As defense in depth, propose an upstream Starlette change that defines
`AwaitableOrContextManager` as a pure structural `Protocol` with explicit
`__await__`, `__aenter__`, and `__aexit__` members, rather than inheriting the
runtime `Awaitable` and `AbstractAsyncContextManager` ABCs. That keeps the type
annotation precise without putting a non-runtime Protocol in those ABCs'
subclass graphs. This is optional hardening after the Gludd fix, not a reason to
delay it. Starlette 1.3.1 and current main still use the hybrid definition.

## Root cause

Python 3.14 creates a per-class `__protocol_attrs__` set when a Protocol class
is defined. Gludd currently installs a *data descriptor* with that same name on
the shared Protocol metaclass. A metaclass data descriptor takes precedence
over the value already stored in each Protocol class dictionary.

The descriptor returns the real members only for Gludd's two named protocols;
for a Protocol created before the GitLab module is imported, every other class
is observed as an empty Protocol. Protocols created after the mutation happen
to invoke Gludd's setter and retain their members. This makes the failure depend
on import and pytest collection order.

Starlette's
[`AwaitableOrContextManager`](https://raw.githubusercontent.com/Kludex/starlette/1.3.1/starlette/_utils.py)
is a non-runtime Protocol that directly inherits
`collections.abc.Awaitable` and `AbstractAsyncContextManager`. CPython's
[`typing._ProtocolMeta`](https://raw.githubusercontent.com/python/cpython/v3.14.0/Lib/typing.py)
deliberately permits internal `abc`/`functools` subclass checks across such
Protocols. Once Gludd's descriptor makes Starlette's Protocol appear empty, an
internal `Awaitable` subclass scan considers every class—including
`anyio.Event`—a match and caches that answer in the Awaitable ABC.

CPython's
[`inspect.isawaitable()`](https://raw.githubusercontent.com/python/cpython/v3.14.0/Lib/inspect.py)
ends with `isinstance(object, collections.abc.Awaitable)`, so the poisoned ABC
answer escapes into ordinary application code. The object still has no
`__await__` implementation; attempting to await it is invalid despite the
predicate returning true.

The local deterministic probe reproduced the defect before this document was
written:

```text
platform darwin -- Python 3.14.0
starlette==1.3.1 anyio==4.13.0
assert not issubclass(anyio.Event, Awaitable)
AssertionError: assert not True
```

The probe imported Starlette first, then the GitLab adapter, cleared the
diagnostic ABC cache, and made the assertion. The temporary probe was removed;
the permanent tests below must reproduce the import boundary in isolated
processes without modifying caches.

## Upstream and user-report evidence

- Python's official
  [`typing` documentation](https://docs.python.org/3/library/typing.html#typing.runtime_checkable)
  says runtime-checkable Protocol tests only attribute presence and warns that
  their runtime behavior differs from static typing. It exposes public
  `get_protocol_members()` rather than `__protocol_attrs__` for introspection.
- Python's official
  [`abc` documentation](https://docs.python.org/3.14/library/abc.html)
  explains that `issubclass()` may use `__subclasshook__` and that the cache
  token changes on `ABCMeta.register()`. Gludd's direct metaclass write does not
  use `register()`, so it cannot provide normal cache invalidation semantics.
- The long-lived CPython user/developer thread
  [bpo-38908 / GitHub #83089](https://github.com/python/cpython/issues/83089)
  ran from 2019 to 2021 and documents that `abc` and `functools` may call
  `issubclass()` on non-runtime Protocols in a class hierarchy. That historical
  compatibility path is still explicit in Python 3.14's `_allow_reckless_class_checks`.
  The lesson for Gludd is to use the published typing API, not replace fields
  that the compatibility machinery consumes.
- Starlette's
  [1.3.1 release notes](https://www.starlette.io/release-notes/#131-june-12-2026)
  do not list a Protocol/ABC fix, and its 1.3.1 and main `_utils.py` definitions
  are currently identical for this type. A Starlette version pin alone is not
  a remediation.

## Regression tests to pin

Add `tests/unit/test_protocol_cache_isolation.py` and run its scenarios in fresh
child interpreters so the result cannot inherit pytest's prior ABC caches.

1. Snapshot `type(Protocol)` before importing the GitLab adapter, import it, and
   assert that no metaclass attribute was added or replaced.
2. Test both import orders: Starlette then Gludd, and Gludd then Starlette.
   In each fresh process assert all of the following:
   `issubclass(anyio.Event, Awaitable) is False`, an AnyIO Event instance is not
   an `Awaitable`, and `inspect.isawaitable(event) is False`.
3. Assert a real coroutine and Starlette's form wrapper remain awaitable, so the
   test cannot pass by globally disabling Awaitable recognition.
4. Replace the private-attribute assertions in
   `tests/unit/test_issue_sources_gitlab_issues.py` with behavior: complete fake
   response/transport objects satisfy their Protocols and incomplete objects do
   not.
5. Load a small pytest plugin in the full collection gate whose
   `pytest_collection_finish` hook repeats the three Event assertions. This pins
   the exact failure boundary rather than relying on test filename ordering.

The implementation must add a narrow Make target to the target contract and
produce an auditable marker:

```text
make test-protocol-cache-compat PYTHON_VERSIONS='3.11 3.13 3.14' STARLETTE_VERSION=1.3.1
PROTOCOL_CACHE_ISOLATION_PASS python=3.14 starlette=1.3.1 import_orders=2
```

It must also pass the existing gates:

```text
make test-files TESTFILES='tests/unit/test_protocol_cache_isolation.py tests/unit/test_issue_sources_gitlab_issues.py'
make collect-check
make lint
make typecheck
```

CI should run the compatibility target against the lock and the newest supported
Starlette release. The Python 3.14 case is mandatory; 3.11 and 3.13 prove the
removal remains compatible with Gludd's declared Python floor.

## ZDD impact

This change has no database, queue, network, or persisted-state migration. The
bad mutation and poisoned ABC caches are process-local, so old and corrected
Gunicorn workers can coexist during a rolling deployment.

Do not hot-reload the module in an affected process: deleting source code does
not safely undo a descriptor already installed on a shared metaclass or repair
every ABC cache. Drain and replace workers one at a time. Each new worker's
readiness check must import the normal application graph and verify that an
AnyIO Event is not awaitable before it receives traffic. Promote only after all
workers report the compatibility marker.

The previous Python 3.14 artifact is not a safe rollback because it restores the
global mutation. Keep the prior Python 3.13 image only as an emergency rollback
until the corrected artifact is fully deployed; the normal rollback is another
build containing the metaclass-removal fix. No downtime or data rollback is
required.
