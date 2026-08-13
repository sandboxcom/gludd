# Model Gateway Resource Lifecycle

## Status

Implemented for the `0.1.0-beta.4` release train. The model gateway, its
response cache, and router-created fallback gateways now have an explicit,
idempotent shutdown contract.

## Problem

A router can create a fallback `ModelGateway` after application startup when
no gateway was injected. That gateway owns a disk-backed response cache. Bare
FastAPI applications did not previously register a matching shutdown action,
so Python 3.13 and newer could surface an unclosed SQLite connection as an
unraisable `ResourceWarning`.

Relying on garbage collection was not a valid lifecycle contract. DiskCache
connections can be opened in the request worker, while a later finalizer can
run in a different context. The owner application must close the gateway
during its ASGI shutdown phase.

## Ownership contract

- `ModelGateway.close()` is idempotent and closes its owned response cache.
- A gateway can be used as a synchronous context manager.
- Its finalizer is a narrow best-effort fallback, not the normal shutdown path.
- The models router tracks only gateways that it creates itself.
- A single router shutdown handler closes tracked gateways in reverse creation
  order and clears the ownership list.
- A gateway injected by an embedding application is never adopted or closed by
  the models router.
- The daemon lifespan remains the primary owner of its startup gateway and
  closes it during normal shutdown.
- Tests that exercise application lifespan use `TestClient` as a context
  manager so startup and shutdown run on the intended portal.

## Practitioner evidence

Python added an explicit `ResourceWarning` for SQLite connections that are
garbage-collected without `close()` in
[CPython issue 105539 and the Python 3.13 release notes](https://github.com/python/cpython/blob/main/Doc/whatsnew/3.13.rst).
This turns a previously latent leak into a visible failure and reinforces that
explicit close is the compatibility boundary.

In the long-running
[Starlette lifespan discussion 2067](https://github.com/encode/starlette/discussions/2067),
practitioners identify forgotten teardown as the primary resource-lifecycle
footgun and recommend context-managed lifespan ownership. The
[Starlette lifespan testing discussion 2594](https://github.com/encode/starlette/discussions/2594)
also demonstrates that tests must actually enter a lifespan manager; merely
constructing an HTTP client does not establish the startup/shutdown contract.

## Zero-downtime deployment

The change is additive. Existing injected gateways keep their current owner,
and live requests continue to use the same app-state gateway. Shutdown runs
only after the ASGI server stops accepting new work. Repeated shutdown calls
are safe because gateway and cache closure are idempotent. No migration,
cache-format change, port change, or process restart beyond the normal release
rollout is required.

## Security and resource boundaries

The repair does not suppress warnings, relax authentication, change budget
checks, or expose provider errors. It prevents SQLite handles from surviving
application shutdown and keeps fallback ownership scoped to one FastAPI app.
No background process, global registry, or cross-project temporary path is
introduced.

## Verification contract

The focused regression must first reproduce a router-owned cache that is not
closed, then prove closure after application shutdown. Gateway ownership tests
also prove context-manager and finalizer cleanup. The endpoint and model-router
families run with `-W error`; the router file must remain above 75 percent
coverage, aggregate project coverage must remain at least 85 percent, and the
full release gate must be green before promotion.
