# Beta.3 serial E2E state and approval repair

Date: 2026-07-28

## Incident

Two release-contract tests failed in a serial process:

- Each `create_daemon_app()` call created a new state dictionary, but the
  module-level `_daemon_state` name was rebound to the newest dictionary. The
  newest application therefore still shared identity with a process-global
  name, and callers that imported the old object retained a stale reference.
- The canonical approval response API replaced an older compact API without a
  compatibility boundary. `ApprovalResult`, the `target` / `by` request names,
  and `ApprovalGate.check()` disappeared even though shipped workflows still
  imported them.

The wider CLI/daemon E2E file also contained setup that assigned readiness and
CIDR state before `TestClient` entered lifespan. Startup correctly replaced
those values, so the tests were not exercising their named runtime branches.
OpenAPI generation also warned about duplicate operation IDs because two route
handlers each registered several HTTP methods in one `api_route` declaration.
Repeated lifespan cycles leaked the diskcache handles owned by code retrieval,
SearX retrieval, research indexing, and local agent memory. Each app left nine
SQLite database/WAL/shared-memory descriptors open, so coverage instrumentation
could exhaust the process descriptor limit before the serial file completed.

## Repair and ZDD properties

`app.state.daemon_state` remains the authoritative, freshly allocated state for
each application. A stable `ChainMap` compatibility handle now delegates legacy
mapping operations to the most recently created application without ever
becoming that application's dictionary. This removes cross-app identity sharing
and fixes stale `from general_ludd.daemon import _daemon_state` references.

The approval repair is additive: canonical `ApprovalRequest` /
`ApprovalResponse` callers are unchanged, while aliases normalize into
`resource_id` / `requester`, and `check()` maps the canonical decision into an
`ApprovalResult`. Existing callers can roll forward without a coordinated flag
day, database migration, daemon restart protocol, or endpoint outage.

Lifecycle E2E fixtures now modify live application state after lifespan startup.
The CLI also exposes the conventional `--version` alias and a stable `TITLE`
metavariable. The Rego fixture is syntactically valid so an installed OPA binary
does not add an environment-dependent parser error to the static reassignment
contract.

The permission-spec and Terraform-state handlers now register each HTTP method
with its dedicated FastAPI decorator. Runtime behavior remains in the shared
handler while OpenAPI receives one deterministic operation ID per method.

Daemon shutdown now closes every app-scoped retrieval and memory cache owner
before disposing the SQLAlchemy engine. A three-lifespan regression contract
checks the process descriptor count directly on supported Unix platforms.

## Long-lived user-forum evidence

- FastAPI discussion
  [#8054](https://github.com/fastapi/fastapi/discussions/8054) has run since
  September 2019 and accumulated dozens of comments about singleton scope.
  Participants distinguish request caching from application-lifetime state;
  later replies explicitly ask for one instance per app, recommend `app.state`,
  and describe clearing app state between test runs to prevent leakage. This
  supports per-app authoritative state instead of a mutable module-global
  dictionary.
- FastAPI issue
  [#592](https://github.com/fastapi/fastapi/issues/592), opened in October 2019,
  documents users observing surprising global-variable behavior across
  requests and workers. The durable lesson is that process globals are not an
  application-state contract; state must be scoped to the application (or an
  external shared store when cross-process sharing is intended).
- The Python.org Ideas thread
  [“Add alias as a field() parameter for dataclasses”](https://discuss.python.org/t/add-alias-as-a-field-parameter-for-dataclasses/22988)
  has tracked constructor aliases since January 2023. It shows that aliases are
  a recurring compatibility need while standard `dataclasses` still lack a
  direct field-alias facility. Explicit normalization in `__post_init__` keeps
  both public names working without replacing the canonical data model.
- FastAPI issue
  [#4740](https://github.com/fastapi/fastapi/issues/4740), opened in March 2022,
  reproduces the exact duplicate-operation-ID warning for one route declared
  with multiple methods. Separate method decorators avoid depending on which
  member of the method set FastAPI selects while generating an ID.

## Regression contracts

- Serial daemon state identity and mutation isolation, including the stable
  legacy proxy.
- Canonical and compatibility approval constructors, exports, and compact
  check result.
- Post-lifespan degraded, readiness, and CIDR behavior.
- CLI version/help behavior and environment-independent Rego validation.
- Unique OpenAPI operation IDs with unchanged permission and Terraform route
  behavior.
- Explicit diskcache teardown with bounded descriptors across repeated daemon
  lifespans.
