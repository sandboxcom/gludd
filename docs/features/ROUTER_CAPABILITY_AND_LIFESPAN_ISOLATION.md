# Router Capability and Lifespan Isolation

## Status

Implemented on 2026-08-20 for the `0.1.0-beta.4` release train. Focused
router tests now attach the same request-local capability object that the
daemon authentication middleware supplies, while keeping every FastAPI app
and its mutable state function-scoped.

## Contract

- Protected routes are tested through their real `RequireCapability`
  dependency. Tests do not patch or replace the dependency after route
  registration.
- Each bare-router fixture creates a fresh FastAPI app and installs an
  app-local middleware that assigns the minimum required
  `request.state.auth_spec` capability.
- Negative authorization tests omit that middleware and continue to require a
  fail-closed 403 response before validation or mutation.
- Router-owned managers and stores live on that app instance. No fixture puts
  authentication or mutable endpoint state in a process-global singleton.
- A test that depends on startup or shutdown must enter `TestClient` as a
  context manager; bare-router tests that have no lifespan remain explicit
  about that fact.
- Reload tests clear package-level lazy export caches before asserting fresh
  import behavior, so file order does not determine the result.

## Dated upstream user evidence

The following user reports informed this boundary:

- On 2021-09-28, a FastAPI user asked how to keep an authentication service in
  `app.state` rather than a global object, specifically so two application
  instances would retain independent services. The discussion also records
  the interaction between dependency injection and security schema discovery.
  See [FastAPI discussion 3958](https://github.com/fastapi/fastapi/discussions/3958).
- On 2023-03-05, Starlette maintainers and users discussed lifespan state and
  teardown. The thread calls out event-loop hazards when multiple
  `TestClient` instances share one app and explains why request/lifespan state
  is safer than global app state. See
  [Starlette discussion 2067](https://github.com/encode/starlette/discussions/2067).
- On 2024-05-14, a Starlette user reproduced an empty `request.state` under
  pytest even though the same lifespan worked under application servers. The
  confirmed resolution was to manage lifespan explicitly for the async test
  client. See
  [Starlette discussion 2594](https://github.com/encode/starlette/discussions/2594).

These reports support two separate rules: authentication belongs to the
individual request/app fixture, and lifespan execution must be explicit. A
valid bearer header alone is not a substitute for the capability object that
authorization checks consume.

## Zero-downtime and failure behavior

The production guards remain fail-closed. Fixture changes grant only the
capability exercised by the test, so a missing or mismatched capability still
returns 403 before the route can write a permission spec, revoke a token, or
destroy compute state. Router serialization converts unavailable or malformed
collaborator results into stable HTTP errors rather than leaking exceptions.

The change creates no daemon, port, database, or background process. Fresh
apps and reload-safe lazy exports make serial and xdist execution observe the
same route, cache, and authorization state.

## Verification

The focused router matrix runs with warnings treated as errors. Changed
production files remain above the 75-percent per-file coverage floor and the
selection remains above 85 percent aggregate coverage. Ruff, strict mypy, and
Markdown lint are required before commit.
