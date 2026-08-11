"""Deep tests for routers/compaction_aggressiveness.py."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.controllers.compaction_aggressiveness import (
    CompactionAggressivenessController,
)


def _build_app() -> FastAPI:
    app = FastAPI()
    import general_ludd.routers.compaction_aggressiveness as mod

    mod.register(app, {})
    return app


class TestGetController:
    def test_none_when_not_set_on_state(self):
        import general_ludd.routers.compaction_aggressiveness as mod

        app = FastAPI()
        assert mod._get_controller(app) is None

    def test_none_when_wrong_type(self):
        import general_ludd.routers.compaction_aggressiveness as mod

        app = FastAPI()
        app.state._compaction_aggressiveness_controller = "not-a-controller"
        assert mod._get_controller(app) is None

    def test_returns_controller_when_valid_instance(self):
        import general_ludd.routers.compaction_aggressiveness as mod

        app = FastAPI()
        ctrl = CompactionAggressivenessController(floor=0.85, min_samples=10, max_level=5)
        app.state._compaction_aggressiveness_controller = ctrl
        assert mod._get_controller(app) is ctrl


class TestAggressivenessStatusEndpoint:
    def test_no_controller_returns_not_available(self):
        app = _build_app()
        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/admin/compaction/aggressiveness-status")
        assert r.status_code == 200
        assert r.json() == {"available": False}

    def test_with_controller_returns_params(self):
        app = FastAPI()
        import general_ludd.routers.compaction_aggressiveness as mod

        mod.register(app, {})
        app.state._compaction_aggressiveness_controller = CompactionAggressivenessController(
            floor=0.88, min_samples=15, max_level=3
        )
        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/admin/compaction/aggressiveness-status")
        assert r.status_code == 200
        data = r.json()
        assert data["available"] is True
        assert data["floor"] == 0.88
        assert data["min_samples"] == 15
        assert data["max_level"] == 3

    def test_default_controller_values(self):
        app = FastAPI()
        import general_ludd.routers.compaction_aggressiveness as mod

        mod.register(app, {})
        app.state._compaction_aggressiveness_controller = CompactionAggressivenessController()
        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/admin/compaction/aggressiveness-status")
        assert r.status_code == 200
        data = r.json()
        assert data["available"] is True
        assert data["floor"] == 0.9
        assert isinstance(data["min_samples"], int)


class TestRegisterSideEffects:
    def test_registers_route_on_app(self):
        app = FastAPI()
        import general_ludd.routers.compaction_aggressiveness as mod

        mod.register(app, {})
        routes = [r.path for r in app.routes]
        assert "/admin/compaction/aggressiveness-status" in routes

    def test_register_accepts_daemon_state(self):
        app = FastAPI()
        import general_ludd.routers.compaction_aggressiveness as mod

        mod.register(app, {"_dummy": "state"})
        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/admin/compaction/aggressiveness-status")
        assert r.status_code == 200
        assert r.json() == {"available": False}
