# Connector Observability Wiring

## Contract

The daemon must expose the connector registry through the authenticated
`/api/observe/sources`, `/api/observe/health`, and `/api/observe/query` routes on
every startup, including the valid zero-connector state. Route handlers resolve
the live registry from `app.state` so a controlled reload may replace it without
registering duplicate routes.

Connector configuration has two supported inputs:

- `connectors` in `general-ludd.yml`;
- `connectors.yml`, which takes precedence when it contains a non-empty list.

Malformed standalone configuration is logged and ignored. It never removes a
valid embedded list and never prevents daemon startup. Requests select an
operator-registered connector by name; they cannot provide a backend URL, so
the HTTP boundary does not become a generic SSRF proxy.

## Zero-downtime behavior

The route surface is installed during app construction and serves an empty
registry before any backend is configured. Adding or replacing connector state
does not require a route-table mutation or listener restart. A replacement
closes the prior registry before publishing the new one, preventing background
connector resources from leaking across reloads.

## Practitioner evidence

FastAPI users have repeatedly found that route registration order and
`include_router()` copy semantics can leave expected routes absent or stale;
one report notes losing significant debugging time because order mattered more
than the declarative API suggested
([fastapi#5491](https://github.com/fastapi/fastapi/discussions/5491)). Gludd pins
the observable route surface with end-to-end `TestClient` assertions and keeps
mutable connector state behind the already-registered handlers.

## Verification

`tests/unit/test_daemon_connector_wiring.py` covers empty startup, route
availability, configured source listing/health/query behavior, standalone-file
precedence, malformed input, and `UserConfig` propagation. The repository-wide
gate and coverage audit remain the release authority.
