from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.reload import (
    _FORBIDDEN_HEADERS,
    _MAX_HEADER_KEY_LEN,
    _MAX_HEADER_VAL_LEN,
    RegisterHookRequest,
    RegisterWorkerRequest,
    ReloadRequest,
    RollbackRequest,
    _register_admin_routes,
    _register_agent_routes,
    register,
)


class TestReloadRequest:
    def test_defaults(self) -> None:
        req = ReloadRequest()
        assert req.scope == "all"
        assert req.snapshot_modules is None

    def test_custom_scope(self) -> None:
        req = ReloadRequest(scope="rules")
        assert req.scope == "rules"

    def test_snapshot_modules_explicit(self) -> None:
        req = ReloadRequest(snapshot_modules=["general_ludd.foo", "general_ludd.bar"])
        assert req.snapshot_modules == ["general_ludd.foo", "general_ludd.bar"]

    def test_snapshot_modules_empty_list(self) -> None:
        req = ReloadRequest(snapshot_modules=[])
        assert req.snapshot_modules == []

    def test_invalid_scope_type(self) -> None:
        with pytest.raises(ValueError):
            ReloadRequest(scope=123)

    def test_json_deserialize_extra_field(self) -> None:
        req = ReloadRequest.model_validate({"scope": "config", "extra": "ignored"})
        assert req.scope == "config"


class TestRollbackRequest:
    def test_defaults(self) -> None:
        req = RollbackRequest()
        assert req.module_names is None

    def test_module_names_explicit(self) -> None:
        req = RollbackRequest(module_names=["general_ludd.foo"])
        assert req.module_names == ["general_ludd.foo"]

    def test_module_names_empty(self) -> None:
        req = RollbackRequest(module_names=[])
        assert req.module_names == []


class TestRegisterWorkerRequest:
    def test_valid_worker(self) -> None:
        req = RegisterWorkerRequest(
            worker_id="w1",
            address="https://worker.example.com/api",
        )
        assert req.worker_id == "w1"
        assert req.address == "https://worker.example.com/api"

    def test_ssrf_loopback_rejected(self) -> None:
        with pytest.raises(ValueError):
            RegisterWorkerRequest(worker_id="w1", address="https://127.0.0.1/api")

    def test_ssrf_localhost_rejected(self) -> None:
        with pytest.raises(ValueError):
            RegisterWorkerRequest(worker_id="w1", address="https://localhost/api")

    def test_ssrf_rfc1918_rejected(self) -> None:
        with pytest.raises(ValueError):
            RegisterWorkerRequest(worker_id="w1", address="https://10.0.0.1/api")

    def test_ssrf_metadata_rejected(self) -> None:
        with pytest.raises(ValueError):
            RegisterWorkerRequest(worker_id="w1", address="http://169.254.169.254/latest/meta-data")

    def test_http_only_rejected(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            RegisterWorkerRequest(worker_id="w1", address="http://public.example.com/api")
        assert "https" in str(exc_info.value).lower()


class TestRegisterHookRequest:
    def test_valid_minimal(self) -> None:
        req = RegisterHookRequest(event_name="reload", url="https://hooks.example.com")
        assert req.event_name == "reload"
        assert req.url == "https://hooks.example.com"
        assert req.retry_count == 1
        assert req.timeout_seconds == 10
        assert req.headers is None

    def test_valid_full(self) -> None:
        req = RegisterHookRequest(
            event_name="config.reloaded",
            url="https://hooks.example.com",
            headers={"X-Custom": "value"},
            retry_count=3,
            timeout_seconds=30,
        )
        assert req.event_name == "config.reloaded"
        assert req.url == "https://hooks.example.com"
        assert req.headers == {"X-Custom": "value"}
        assert req.retry_count == 3
        assert req.timeout_seconds == 30

    def test_ssrf_loopback_rejected(self) -> None:
        with pytest.raises(ValueError):
            RegisterHookRequest(event_name="e", url="https://127.0.0.1/hook")

    def test_ssrf_localhost_rejected(self) -> None:
        with pytest.raises(ValueError):
            RegisterHookRequest(event_name="e", url="https://localhost/hook")

    def test_ssrf_rfc1918_rejected(self) -> None:
        with pytest.raises(ValueError):
            RegisterHookRequest(event_name="e", url="https://192.168.1.1/hook")

    def test_ssrf_metadata_rejected(self) -> None:
        with pytest.raises(ValueError):
            RegisterHookRequest(event_name="e", url="http://169.254.169.254/latest/meta-data")

    def test_http_only_rejected(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            RegisterHookRequest(event_name="e", url="http://public.example.com/hook")
        assert "https" in str(exc_info.value).lower()

    def test_forbidden_header_authorization(self) -> None:
        for forbidden in _FORBIDDEN_HEADERS:
            with pytest.raises(ValueError):
                RegisterHookRequest(
                    event_name="e",
                    url="https://hooks.example.com",
                    headers={forbidden: "secret"},
                )

    def test_forbidden_header_case_insensitive(self) -> None:
        with pytest.raises(ValueError):
            RegisterHookRequest(
                event_name="e",
                url="https://hooks.example.com",
                headers={"HOST": "evil.com"},
            )

    def test_header_key_too_long(self) -> None:
        long_key = "X" * (_MAX_HEADER_KEY_LEN + 1)
        with pytest.raises(ValueError):
            RegisterHookRequest(
                event_name="e",
                url="https://hooks.example.com",
                headers={long_key: "val"},
            )

    def test_header_value_too_long(self) -> None:
        long_val = "Y" * (_MAX_HEADER_VAL_LEN + 1)
        with pytest.raises(ValueError):
            RegisterHookRequest(
                event_name="e",
                url="https://hooks.example.com",
                headers={"X-Key": long_val},
            )

    def test_header_key_at_boundary(self) -> None:
        key = "X" * _MAX_HEADER_KEY_LEN
        req = RegisterHookRequest(
            event_name="e",
            url="https://hooks.example.com",
            headers={key: "val"},
        )
        assert len(next(iter(req.headers.keys()))) == _MAX_HEADER_KEY_LEN

    def test_header_value_at_boundary(self) -> None:
        val = "Y" * _MAX_HEADER_VAL_LEN
        req = RegisterHookRequest(
            event_name="e",
            url="https://hooks.example.com",
            headers={"X-Key": val},
        )
        assert req.headers["X-Key"] == val

    def test_headers_none_passes_through(self) -> None:
        req = RegisterHookRequest(
            event_name="e",
            url="https://hooks.example.com",
            headers=None,
        )
        assert req.headers is None

    def test_valid_headers_preserved(self) -> None:
        req = RegisterHookRequest(
            event_name="e",
            url="https://hooks.example.com",
            headers={"Content-Type": "application/json", "X-Trace-Id": "abc"},
        )
        assert req.headers == {"Content-Type": "application/json", "X-Trace-Id": "abc"}


class TestForbiddenHeadersConstant:
    def test_all_expected_forbidden(self) -> None:
        assert "authorization" in _FORBIDDEN_HEADERS
        assert "host" in _FORBIDDEN_HEADERS
        assert "content-length" in _FORBIDDEN_HEADERS
        assert "transfer-encoding" in _FORBIDDEN_HEADERS
        assert "cookie" in _FORBIDDEN_HEADERS
        assert len(_FORBIDDEN_HEADERS) == 5


class TestRegisterAdminRoutes:
    def test_routes_registered(self) -> None:
        app = FastAPI()
        _register_admin_routes(app)
        routes = {r.path: r.methods for r in app.routes if hasattr(r, "path") and hasattr(r, "methods")}
        assert "/admin/reload" in routes
        assert routes["/admin/reload"] == {"POST"}
        assert "/admin/rollback" in routes
        assert routes["/admin/rollback"] == {"POST"}
        assert "/admin/config/reload" in routes
        assert "/admin/reload/status" in routes
        assert "/admin/templates/refresh" in routes
        assert "/admin/templates" in routes
        assert "/admin/playbooks/refresh" in routes
        assert "/admin/playbooks" in routes
        assert "/admin/hooks" in routes
        assert "/admin/hooks/{hook_id}" in routes
        assert "/admin/workers" in routes
        assert "/admin/workers/ping" in routes

    def test_reload_status_returns_structure(self) -> None:
        app = FastAPI()
        _register_admin_routes(app)
        client = TestClient(app)

        class FakeBus:
            def get_history(self):
                return []

        app.state._subsystems = {"bus": FakeBus()}
        resp = client.get("/admin/reload/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "recent_events" in data
        assert "total_events" in data

    def test_list_hooks_returns_structure(self) -> None:
        app = FastAPI()
        _register_admin_routes(app)
        client = TestClient(app)

        class FakeHooks:
            def list_hooks(self):
                return []

        app.state._subsystems = {"hooks": FakeHooks()}
        resp = client.get("/admin/hooks")
        assert resp.status_code == 200
        data = resp.json()
        assert "hooks" in data

    def test_delete_hook_returns_id(self) -> None:
        app = FastAPI()
        _register_admin_routes(app)
        client = TestClient(app)

        class FakeHooks:
            def unregister(self, hook_id):
                pass

        app.state._subsystems = {"hooks": FakeHooks()}
        resp = client.delete("/admin/hooks/h1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["removed"] == "h1"

    def test_list_workers_returns_structure(self) -> None:
        app = FastAPI()
        _register_admin_routes(app)
        client = TestClient(app)

        class FakeWorker:
            worker_id = "w1"
            address = "https://worker.example.com"
            last_seen = None

        class FakeBroadcaster:
            def list_workers(self):
                return [FakeWorker()]

        app.state._subsystems = {"broadcaster": FakeBroadcaster()}
        resp = client.get("/admin/workers")
        assert resp.status_code == 200
        data = resp.json()
        assert "workers" in data

    def test_list_templates_empty_when_no_registry(self) -> None:
        app = FastAPI()
        _register_admin_routes(app)
        client = TestClient(app)
        resp = client.get("/admin/templates")
        assert resp.status_code == 200
        assert resp.json() == {"templates": []}

    def test_list_templates_when_registry_present(self) -> None:
        app = FastAPI()
        _register_admin_routes(app)
        client = TestClient(app)
        fake_registry = MagicMock()
        fake_registry.list_templates.return_value = ["t1.j2", "t2.j2"]
        app.state._prompt_registry = fake_registry
        resp = client.get("/admin/templates")
        assert resp.status_code == 200
        assert resp.json() == {"templates": ["t1.j2", "t2.j2"]}

    def test_list_playbooks_empty_when_no_runner(self) -> None:
        app = FastAPI()
        _register_admin_routes(app)
        client = TestClient(app)
        resp = client.get("/admin/playbooks")
        assert resp.status_code == 200
        assert resp.json() == {"playbooks": []}


class TestRegisterAgentRoutes:
    def test_routes_registered(self) -> None:
        app = FastAPI()
        _register_agent_routes(app)
        routes = {r.path: r.methods for r in app.routes if hasattr(r, "path") and hasattr(r, "methods")}
        assert "/admin/agents" in routes
        assert routes["/admin/agents"] == {"GET"}
        assert "/admin/agents/{agent_id}" in routes
        assert "/admin/metrics/cost" in routes
        assert "/admin/metrics/report" in routes

    def test_agent_not_found_returns_404(self) -> None:
        app = FastAPI()
        _register_agent_routes(app)
        client = TestClient(app)

        class FakeMetrics:
            def get_agent_summary(self, agent_id):
                return None

        app.state._extended_subsystems = {"metrics": FakeMetrics()}
        resp = client.get("/admin/agents/nonexistent")
        assert resp.status_code == 404


class TestRegisterFunction:
    def test_registers_all_routes(self) -> None:
        app = FastAPI()
        register(app, {})
        paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/admin/reload" in paths
        assert "/admin/rollback" in paths
        assert "/admin/config/reload" in paths
        assert "/admin/reload/status" in paths
        assert "/admin/templates/refresh" in paths
        assert "/admin/templates" in paths
        assert "/admin/playbooks/refresh" in paths
        assert "/admin/playbooks" in paths
        assert "/admin/hooks" in paths
        assert "/admin/hooks/{hook_id}" in paths
        assert "/admin/workers" in paths
        assert "/admin/workers/ping" in paths
        assert "/admin/agents" in paths
        assert "/admin/agents/{agent_id}" in paths
        assert "/admin/metrics/cost" in paths
        assert "/admin/metrics/report" in paths
