# Readiness and lifespan test boundaries

Gludd's `/readyz` contract distinguishes three states:

1. Before lifespan startup, no event-loop task exists and the probe returns
   `503 daemon_not_initialized`.
2. After startup, a live event-loop task returns `200 ready`.
3. A degraded, completed, or cancelled task returns `503` with a bounded reason.

Tests must choose their lifecycle deliberately.  A `TestClient` used as a
context manager enters the ASGI lifespan and therefore cannot represent the
pre-initialization state.  Pre-lifespan tests construct a client without context
entry and assert that `_event_loop_task` is absent before making the request.
Lifecycle tests use the context manager and verify startup and shutdown.
Public-route authentication tests are lifecycle tests: once their context-managed
client is entered, they assert `200 ready`, not the pre-startup `503`.  The
public-path assertions remain independent of readiness, and each lifecycle test
uses a per-test configuration directory and SQLite database so startup never
opens or migrates persistent user state.

## Upstream operational findings

- [Starlette's TestClient documentation](https://www.starlette.io/testclient/#testclient)
  explicitly says the lifespan handler runs when `TestClient` is used as a
  context manager.  This is the authoritative reason the two test forms are not
  interchangeable.
- In [Starlette discussion #2067](https://github.com/encode/starlette/discussions/2067),
  maintainers and users describe context-managed lifespan as the preferred way
  to initialize and tear down asynchronous resources, and call out per-event-loop
  state concerns in tests.  Gludd therefore keeps initialization tests outside
  lifespan and real resource-lifecycle tests inside it.
- [Starlette discussion #2594](https://github.com/encode/starlette/discussions/2594)
  shows that missing or duplicated lifespan management produces confusing state
  behavior.  Gludd pins each readiness state through the real factory so a green
  probe cannot be inferred from a test harness that initialized more than the
   scenario intended.
- [FastAPI's official lifespan testing guide](https://fastapi.tiangolo.com/advanced/testing-events/)
  demonstrates the same context-manager boundary: startup state is available
  inside `with TestClient(app)` and shutdown state is observable after it exits.
  Gludd therefore tests pre-startup readiness without context entry and tests
  public routes against an initialized, isolated daemon.

The focused readiness matrix covers pre-startup, live, degraded, completed, and
cancelled states.  The auth matrix additionally proves that public paths remain
unauthenticated while reporting the lifecycle state that actually exists.  The
full fail-fast suite ensures these semantics coexist with the daemon's isolated
database, model, and event-loop lifecycle.
