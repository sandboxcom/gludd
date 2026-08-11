"""Deep tests for routers/approval.py — approval gate status endpoint."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.approval import _get_gate, register


class _SentinelGate:
    pass


class _AnotherGate:
    pass


# ---------------------------------------------------------------------------
# _get_gate
# ---------------------------------------------------------------------------


class TestGetGate:
    def test_no_gate_wired_returns_none(self):
        app = FastAPI()
        assert _get_gate(app) is None

    def test_nonexistent_state_returns_none(self):
        app = FastAPI()
        if hasattr(app.state, "_approval_gate"):
            del app.state._approval_gate
        assert _get_gate(app) is None

    def test_gate_wired_returns_object(self):
        app = FastAPI()
        gate = _SentinelGate()
        app.state._approval_gate = gate
        assert _get_gate(app) is gate

    def test_gate_overwritten_returns_latest(self):
        app = FastAPI()
        g1 = _SentinelGate()
        g2 = _AnotherGate()
        app.state._approval_gate = g1
        assert _get_gate(app) is g1
        app.state._approval_gate = g2
        assert _get_gate(app) is g2

    def test_gate_set_to_none_explicitly_returns_none(self):
        app = FastAPI()
        app.state._approval_gate = _SentinelGate()
        app.state._approval_gate = None
        assert _get_gate(app) is None


# ---------------------------------------------------------------------------
# register — endpoint registration
# ---------------------------------------------------------------------------


class TestRegisterEndpoint:
    def test_register_adds_route(self):
        app = FastAPI()
        previous = len(app.routes)
        register(app, {})
        assert len(app.routes) == previous + 1

    def test_route_path_is_correct(self):
        app = FastAPI()
        register(app, {})
        paths = [r.path for r in app.routes if r.path == "/admin/approval/status"]
        assert len(paths) == 1

    def test_route_method_is_get(self):
        app = FastAPI()
        register(app, {})
        for route in app.routes:
            if route.path == "/admin/approval/status":
                assert "GET" in route.methods
                break
        else:
            pytest.fail("Route not found")

    # ---------------------------------------------------------------------------
    # endpoint behaviour (call via TestClient)
    # ---------------------------------------------------------------------------class TestEndpointBehaviour:
    def test_no_gate_returns_not_wired(self):
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/approval/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["wired"] is False
        assert body["gate_type"] == "None"

    def test_gate_wired_returns_wired_with_type_name(self):
        app = FastAPI()
        app.state._approval_gate = _SentinelGate()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/approval/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["wired"] is True
        assert body["gate_type"] == "_SentinelGate"

    def test_another_gate_type_name(self):
        app = FastAPI()
        app.state._approval_gate = _AnotherGate()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/approval/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["gate_type"] == "_AnotherGate"

    def test_response_shape_is_consistent(self):
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/approval/status")
        body = resp.json()
        assert set(body.keys()) == {"wired", "gate_type"}
        assert isinstance(body["wired"], bool)
        assert isinstance(body["gate_type"], str)

    def test_register_twice_is_idempotent(self):
        app = FastAPI()
        register(app, {})
        count1 = len(app.routes)
        register(app, {})
        assert len(app.routes) > count1

    def test_daemon_state_passed_but_unused(self):
        app = FastAPI()
        daemon_state = {"_approval_gate": _SentinelGate()}
        register(app, daemon_state)
        client = TestClient(app)
        resp = client.get("/admin/approval/status")
        assert resp.json()["wired"] is False
