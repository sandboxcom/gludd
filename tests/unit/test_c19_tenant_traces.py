"""C19 — Cross-tenant traces: auth-derived project scoping.

Before the XT-3/XT-4 fix, the bearer token was a single global PSK and
/api/traces trusted a caller-supplied ``?project_id=`` query param for
scoping — any PSK holder could read any tenant's execution traces.

The fix extends the bearer token to optionally carry a project claim
(``project_id:psk``). The auth middleware stamps
``request.state.project_id``, and ``_resolve_trace_project_id()`` picks
the auth-derived scope over the caller-supplied query param.

These tests pin the contract: a scoped caller can only read its OWN
project's traces; an unscoped caller (legacy PSK) still has full access.
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
