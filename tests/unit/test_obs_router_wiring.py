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
