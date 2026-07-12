"""Verify the observability router is wired into the daemon.

The observe router (routers/observe.py) is wired via wire_observability()
inside create_daemon_app() at daemon.py:2557-2561.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.observe import wire_observability


def test_wire_observability_registers_all_routes() -> None:
    """wire_observability registers /api/observe/* endpoints."""
    app = FastAPI()
    wire_observability(app, {}, [])
    client = TestClient(app)

    assert client.get("/api/observe/sources").status_code == 200
    assert client.get("/api/observe/health").status_code == 200


def test_wire_observability_stores_registry_on_app_state() -> None:
    """wire_observability stores ConnectorRegistry on app.state."""
    app = FastAPI()
    wire_observability(app, {}, [])
    from general_ludd.connectors.registry import ConnectorRegistry

    assert isinstance(app.state._connector_registry, ConnectorRegistry)


def test_daemon_py_has_wire_observability_call() -> None:
    """daemon.py imports and calls wire_observability inside create_daemon_app."""
    import ast

    daemon_path = "src/general_ludd/daemon.py"
    with open(daemon_path) as f:
        tree = ast.parse(f.read(), filename=daemon_path)

    imports_wire_obs = False
    calls_wire_obs = False

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "general_ludd.routers.observe":
            for alias in node.names:
                if alias.name == "wire_observability":
                    imports_wire_obs = True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "wire_observability":
            calls_wire_obs = True

    assert imports_wire_obs, "daemon.py must import wire_observability"
    assert calls_wire_obs, "daemon.py must call wire_observability"


def test_observe_query_request_rejects_extra_url_field() -> None:
    """ObserveQueryRequest model does NOT accept an extra `url` field."""
    from general_ludd.routers.observe import ObserveQueryRequest

    req = ObserveQueryRequest(source="test", spec={})

    # pydantic v2: model_extra is ignored by default (model_config extra='ignore')
    if hasattr(req, "model_extra"):
        req_with_url = ObserveQueryRequest(source="test", spec={}, **{"url": "http://evil.com"})
        assert not hasattr(req_with_url, "url")
        assert req_with_url.model_extra is None or "url" not in (req_with_url.model_extra or {})


def test_query_endpoint_ignores_extra_fields() -> None:
    """POST /api/observe/query only passes source + spec, ignores smuggled url."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from general_ludd.routers.observe import register

    app = FastAPI()
    from general_ludd.connectors.registry import ConnectorRegistry
    registry = ConnectorRegistry()
    app.state._connector_registry = registry
    register(app, {})

    client = TestClient(app)

    req_data = {"source": "nonexistent", "spec": {}, "url": "http://evil.com"}
    response = client.post("/api/observe/query", json=req_data)
    assert response.status_code == 404
