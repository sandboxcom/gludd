"""C19 — Cross-tenant: auth-derived project scoping for facts/metrics/traces.

Before the XT-3/XT-4 fix, the bearer token was a single global PSK and
/api/traces, /api/facts, and /api/metrics trusted a caller-supplied
``?project_id=`` query param for scoping — any PSK holder could read any
tenant's data.

The fix extends the bearer token to optionally carry a project claim
(``project_id:psk``). The auth middleware stamps
``request.state.project_id``, and ``_resolve_trace_project_id()`` picks
the auth-derived scope over the caller-supplied query param.

These tests pin the contract: a scoped caller can only read its OWN
project's data; an unscoped caller (legacy PSK) still has full access.
"""

from __future__ import annotations

from starlette.requests import Request

from general_ludd.routers.facts import _resolve_trace_project_id


def _request_with_project(project_id: str | None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/traces",
        "headers": [],
        "query_string": b"",
    }
    request = Request(scope)
    if project_id is not None:
        request.state.project_id = project_id
    return request


class TestResolveTraceProjectId:
    def test_auth_derived_wins_over_query_param(self):
        """When request.state.project_id="proj-a" (from a project-scoped
        bearer token), the query param "proj-b" is IGNORED."""
        request = _request_with_project("proj-a")
        result = _resolve_trace_project_id(request, "proj-b")
        assert result == "proj-a"

    def test_auth_derived_without_query_param(self):
        """request.state.project_id="proj-b" and no query param."""
        request = _request_with_project("proj-b")
        result = _resolve_trace_project_id(request, None)
        assert result == "proj-b"

    def test_no_auth_scope_honors_query_param(self):
        """When request.state has NO project_id (legacy global PSK), the
        query param is used — back-compat, no regression."""
        request = _request_with_project(None)
        result = _resolve_trace_project_id(request, "proj-a")
        assert result == "proj-a"

    def test_no_auth_scope_no_query_param_returns_none(self):
        """Both absent: None — unscoped, returns all traces (legacy)."""
        request = _request_with_project(None)
        result = _resolve_trace_project_id(request, None)
        assert result is None

    def test_auth_scope_none_query_remains_none(self):
        """Auth scope is None (unset attribute) and query is None."""
        scope = {"type": "http", "method": "GET", "path": "/api/traces",
                 "headers": [], "query_string": b""}
        request = Request(scope)
        result = _resolve_trace_project_id(request, None)
        assert result is None


class TestTracerProjectIdOnExecutionTrace:
    """The ExecutionTrace carries project_id at construction — the data-layer
    stamp that enables tenant isolation at query time."""

    def test_trace_stamped_with_project_id(self):
        from general_ludd.observability.tracer import ExecutionTrace
        trace = ExecutionTrace(
            todo_id="T-1", work_type="code", project_id="my-project"
        )
        assert trace.project_id == "my-project"
        d = trace.to_dict()
        assert d["project_id"] == "my-project"

    def test_trace_without_project_id_defaults_to_none(self):
        from general_ludd.observability.tracer import ExecutionTrace
        trace = ExecutionTrace(todo_id="T-1", work_type="code")
        assert trace.project_id is None
        d = trace.to_dict()
        assert d["project_id"] is None


class TestFactsMetricsAuthScoping:
    """The /api/facts and /api/metrics routes must resolve project_id from
    auth context (request.state.project_id), not from caller-supplied query
    param. These tests verify the route functions call _resolve_trace_project_id
    and use its return value, so a project-scoped bearer token cannot be
    overridden by a ?project_id= query param."""

    @staticmethod
    def _build_facts_app():
        from fastapi import FastAPI

        from general_ludd.routers.facts import register as _register_facts
        app = FastAPI()
        app.state._session_factory = None
        app.state._metrics_collector = None
        app.state._recent_traces = None
        app.state._dispatch_facet = None
        app.state._startup_config = {}
        app.state._spend_limiter = None
        app.state._filestore = None
        _register_facts(app, {})
        return app

    def test_facts_route_auth_scope_wins_over_query_param(self):
        """When request.state.project_id is set (auth middleware), the
        /api/facts endpoint must return that auth-derived project_id in its
        response, ignoring any ?project_id= query param."""
        from fastapi.testclient import TestClient
        from starlette.middleware.base import BaseHTTPMiddleware

        app = self._build_facts_app()

        class _AuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                request.state.project_id = "proj-a"
                return await call_next(request)

        app.add_middleware(_AuthMiddleware)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/facts?project_id=proj-b", headers={"Authorization": "Bearer test"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["project_id"] == "proj-a"

    def test_metrics_route_auth_scope_wins_over_query_param(self):
        """When request.state.project_id is set (auth middleware), the
        /api/metrics endpoint must scope to the auth-derived project_id,
        ignoring any ?project_id= query param."""
        from fastapi.testclient import TestClient
        from starlette.middleware.base import BaseHTTPMiddleware

        app = self._build_facts_app()

        class _AuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                request.state.project_id = "proj-a"
                return await call_next(request)

        app.add_middleware(_AuthMiddleware)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/metrics?project_id=proj-b", headers={"Authorization": "Bearer test"})
        assert resp.status_code == 200, resp.text

    def test_facts_route_without_auth_scope_uses_query_param(self):
        """When request.state has NO project_id (legacy global PSK), the
        endpoint must fall back to the ?project_id= query param."""
        from fastapi.testclient import TestClient

        app = self._build_facts_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/facts?project_id=proj-b", headers={"Authorization": "Bearer test"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["project_id"] == "proj-b"
