# Daemon module split

## Scope

The public daemon entry point remains `general_ludd.daemon`. The application
factory, exported helpers, module globals, and `_lifespan(app)` signature stay in
that facade so callers and test suites do not need a migration. Lifecycle
orchestration now lives in `general_ludd.daemon_components.lifecycle`, with an
explicit `LifecyclePorts` dependency object in
`general_ludd.daemon_components.ports`.

The facade builds the ports object at each lifespan entry. This is deliberate:
code that patches `general_ludd.daemon.EventLoop`, database constructors,
startup helpers, or other established seams still changes what the lifecycle
uses. The component does not import the facade, so importing either component
before `general_ludd.daemon` cannot create a reverse-import cycle.

One async context manager still owns the complete startup and shutdown order.
No resource acquisition, signal handling, task scheduling, rollback, or drain
step was reordered by the extraction. `app.state` remains the per-application
owner for runtime state; the facade remains the API and patch boundary.

## Compatibility and zero-downtime behavior

- `create_daemon_app`, `_lifespan`, and the ASGI `app` remain exported from
  `general_ludd.daemon` with their existing signatures.
- The lifecycle delegates through call-time ports, preserving monkeypatch and
  dependency-injection behavior.
- Writer-process drain, task cancellation, engine disposal, telemetry shutdown,
  local inference cleanup, and SearX cleanup remain in the original order.
- The change is an in-process module boundary only. It does not alter listeners,
  routes, database schemas, configuration, or deployment topology, so normal
  rolling replacement and rollback remain available.
- Rollback consists of reverting the extraction commit; there is no data or
  configuration migration to reverse.

## User and maintainer evidence

Long-lived community discussions point to three recurring failure modes that
shape this boundary:

- FastAPI maintainers and users describe application lifespan as the owner of
  shared resources and emphasize reverse-order cleanup when several resources
  are acquired. Keeping a single lifecycle owner avoids changing those
  semantics: [FastAPI discussion #9397](https://github.com/fastapi/fastapi/discussions/9397).
- A FastAPI user report shows that importing the application object back into a
  router creates a circular import, while passing dependencies inward avoids
  the cycle. `daemon_components.lifecycle` therefore never imports the facade:
  [FastAPI issue #2848](https://github.com/fastapi/fastapi/issues/2848).
- FastAPI users discuss `app.state` as the appropriate application-scoped home
  for lifespan-created resources. The split retains all existing `app.state`
  ownership: [FastAPI discussion #11017](https://github.com/fastapi/fastapi/discussions/11017).
- The long-running pytest community answer on imported-function patching
  explains why tests must patch the name where it is looked up. Call-time facade
  ports preserve that lookup location instead of forcing downstream tests to
  patch a new internal module:
  [pytest monkeypatch imported-function discussion](https://stackoverflow.com/questions/31306080/pytest-monkeypatch-isnt-working-on-imported-function/31746577).

## Verification

Compatibility tests cover import order, absence of a reverse facade import,
public signatures, the facade line limit, and facade monkeypatch propagation.
Focused lifecycle tests cover successful and failed optional startup branches
and confirm that owned resources are stopped on shutdown. Coverage is measured
with line and branch data across the facade and both component modules, with an
85% aggregate floor and a 75% per-file floor.
