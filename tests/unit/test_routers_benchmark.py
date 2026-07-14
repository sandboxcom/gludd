"""Structural tests for routers/benchmark.py — 5 endpoint surface + graceful degradation."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class TestModuleImport:
    def test_module_importable(self) -> None:
        import general_ludd.routers.benchmark as mod

        assert mod is not None

    def test_register_is_callable(self) -> None:
        from general_ludd.routers.benchmark import register

        assert callable(register)


class TestRegisterFunctionShape:
    def test_register_accepts_app_and_daemon_state(self) -> None:
        from general_ludd.routers.benchmark import register

        app = FastAPI()
        register(app, {})
        assert isinstance(app, FastAPI)

    def test_register_returns_none(self) -> None:
        from general_ludd.routers.benchmark import register

        result = register(FastAPI(), {})
        assert result is None


EXPECTED_PATHS: list[tuple[str, str]] = [
    ("GET", "/admin/benchmark/scores"),
    ("GET", "/admin/benchmark/recent"),
    ("GET", "/admin/benchmark/leaderboard"),
    ("POST", "/admin/benchmark/record"),
    ("GET", "/admin/prompt-profiles"),
]


class TestEndpointPaths:
    @pytest.mark.parametrize("method,path", EXPECTED_PATHS)
    def test_path_registered_on_app(self, method: str, path: str) -> None:
        from general_ludd.routers.benchmark import register

        app = FastAPI()
        register(app, {})

        {m.lower() for m in app.routes[0].methods} if app.routes else set()
        found = any(
            r.path == path and method.lower() in {m.lower() for m in r.methods}
            for r in app.routes
        )
        assert found, f"Expected {method} {path} in routes, got {[(r.path, r.methods) for r in app.routes]}"

    def test_total_route_count(self) -> None:
        from general_ludd.routers.benchmark import register

        app = FastAPI()
        register(app, {})
        assert len(app.routes) == 5, f"Expected 5 routes, got {len(app.routes)}"


class TestGracefulDegradation:
    def test_scores_returns_empty_when_no_session(self) -> None:
        from general_ludd.routers.benchmark import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/benchmark/scores")
        assert resp.status_code == 200
        assert resp.json() == {"scores": []}

    def test_recent_returns_empty_when_no_session(self) -> None:
        from general_ludd.routers.benchmark import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/benchmark/recent")
        assert resp.status_code == 200
        assert resp.json() == {"results": []}

    def test_leaderboard_returns_empty_when_no_session(self) -> None:
        from general_ludd.routers.benchmark import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/benchmark/leaderboard")
        assert resp.status_code == 200
        assert resp.json() == {"leaderboard": []}

    def test_record_raises_503_when_no_session(self) -> None:
        from general_ludd.routers.benchmark import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post("/admin/benchmark/record", json={})
        assert resp.status_code == 503
        assert resp.json()["detail"] == "No database session"

    def test_prompt_profiles_returns_empty_when_no_session(self) -> None:
        from general_ludd.routers.benchmark import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/prompt-profiles")
        assert resp.status_code == 200
        assert resp.json() == {"profiles": []}
