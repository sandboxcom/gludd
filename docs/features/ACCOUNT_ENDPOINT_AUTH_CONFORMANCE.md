# Account endpoint authorization conformance

Status: implemented in the endpoint harness and behavior-tested. Release evidence
is tracked in `TASKS.md` as S83.56.

## Problem

The account router has two independent security boundaries: outer bearer-token
authentication and an inner capability guard on each mutating route. The broad
endpoint harness modeled only the outer token. Requests that were intended to
exercise validation or success paths therefore failed closed with 403 because
`request.state.auth_spec` was absent.

Several tests also replaced account-router functions by assigning module globals
directly. Those doubles survived beyond the test and changed the behavior of the
adjacent deep suite, making results depend on file order.

## Behavioral contract

1. Authorized endpoint fixtures attach a real `PermissionSpec` containing only
   the account backup, delete, create, and cleanup actions.
2. Public policy reads remain public and do not require the account capability.
3. The bearer-token fixture still returns 401 before protected route execution
   when authentication is absent.
4. A valid bearer token reaches the inner guard with the real capability and
   exercises the endpoint contract.
5. Every account-router double is scoped by `unittest.mock.patch.object` and is
   restored before the next test or test file.
6. No production authorization check is bypassed, mocked, or weakened.

## Zero-downtime, security, and resource boundary

This change is confined to test construction. It changes no production route,
permission, middleware, deployment, database, port, or service process. Runtime
rollout is therefore zero-downtime, and rollback is a test-only revert.

The fixture grants a minimal resource-specific capability rather than an
administrator wildcard. Authentication and authorization remain separate and
observable: missing bearer tokens return 401, while missing or insufficient
capabilities return 403. Managed patch contexts prevent shared module state from
leaking across workers or execution order. The change adds no persistent file,
connection, process, socket, or cache.

## Practitioner evidence

A FastAPI user reported that endpoint tests using `request.state` need a
faithful test-time state boundary rather than an unrelated object mock. The
discussion recommends preserving the dependency or middleware contract so tests
exercise real request handling:

- [FastAPI discussion #13449](https://github.com/fastapi/fastapi/discussions/13449)

A Starlette discussion documents tests receiving empty request state when the
application lifecycle boundary is not actually entered. That reinforces wiring
state through the ASGI application under test instead of assigning global state:

- [Starlette discussion #2594](https://github.com/encode/starlette/discussions/2594)

## Verification

- The original account slice reproduced 13 fail-closed 403 responses.
- The repaired account slice passes all 20 authentication, authorization,
  validation, success, and degradation cases under strict warnings.
- The adjacent endpoint and deep-account selection passes 50 tests in one
  process, proving that patched module state is restored.
- `general_ludd.routers.account` has 100 percent branch coverage in that
  selection.
- The full release gate remains authoritative for promotion.
