"""Structural tests for routers/model_performance.py — model performance router endpoints."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from general_ludd.routers.model_performance import register


def _registered_app() -> FastAPI:
    app = FastAPI()
    register(app, {})
    return app


class TestModuleImports:
    def test_module_can_be_imported(self) -> None:
        import general_ludd.routers.model_performance

        assert general_ludd.routers.model_performance is not None

    def test_register_is_callable(self) -> None:
        from general_ludd.routers.model_performance import register

        assert callable(register)

    def test_logger_exists(self) -> None:
        from general_ludd.routers.model_performance import logger

        assert isinstance(logger, logging.Logger)
        assert logger.name == "general_ludd.routers.model_performance"


class TestHelperFunctions:
    def test_get_performance_router_exists(self) -> None:
        from general_ludd.routers.model_performance import _get_performance_router

        assert callable(_get_performance_router)

    def test_get_performance_router_returns_none_for_empty_state(self) -> None:
        from general_ludd.routers.model_performance import _get_performance_router

        app = FastAPI()
        result = _get_performance_router(app)
        assert result is None


class TestRegister:
    def test_registers_performance_routes(self) -> None:
        from general_ludd.routers.model_performance import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        routes = {route.path for route in app.routes if isinstance(route, APIRoute)}
        assert "/admin/models/performance" in routes
        assert "/admin/models/ranking" in routes
        assert "/admin/models/router/status" in routes
        assert "/admin/models/router/config" in routes

    def test_performance_endpoint_get_registered(self) -> None:
        from general_ludd.routers.model_performance import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if isinstance(r, APIRoute)]
        perf_route = next(
            (r for r in all_routes if r.path == "/admin/models/performance"), None
        )
        assert perf_route is not None
        assert "GET" in perf_route.methods

    def test_ranking_endpoint_get_registered(self) -> None:
        from general_ludd.routers.model_performance import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if isinstance(r, APIRoute)]
        ranking_route = next(
            (r for r in all_routes if r.path == "/admin/models/ranking"), None
        )
        assert ranking_route is not None
        assert "GET" in ranking_route.methods

    def test_router_status_endpoint_registered(self) -> None:
        from general_ludd.routers.model_performance import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if isinstance(r, APIRoute)]
        status_route = next(
            (r for r in all_routes if r.path == "/admin/models/router/status"), None
        )
        assert status_route is not None
        assert "GET" in status_route.methods

    def test_router_config_endpoint_put_registered(self) -> None:
        from general_ludd.routers.model_performance import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if isinstance(r, APIRoute)]
        config_route = next(
            (r for r in all_routes if r.path == "/admin/models/router/config"), None
        )
        assert config_route is not None
        assert "PUT" in config_route.methods

    def test_initializes_router_state(self) -> None:
        from general_ludd.routers.model_performance import register

        app = FastAPI()
        register(app, {})
        assert hasattr(app.state, "_model_performance_router")


class TestEndpointBehavior:
    def test_unwired_router_fails_closed(self) -> None:
        app = _registered_app()
        with TestClient(app) as client:
            assert client.get("/admin/models/performance").json() == {
                "performance": [],
                "note": "ModelPerformanceRouter not wired",
            }
            assert client.get(
                "/admin/models/ranking", params={"task_type": "chat"}
            ).status_code == 503
            assert client.get("/admin/models/router/status").json() == {
                "status": "not_initialized"
            }
            assert client.put(
                "/admin/models/router/config",
                json={"task_type": "chat", "strategy": "balanced"},
            ).status_code == 503

    def test_wired_router_endpoints_return_live_state(self) -> None:
        app = _registered_app()
        repo = MagicMock()
        repo.get_summary = AsyncMock(return_value=[{"service": "svc"}])
        router = MagicMock()
        router._repo = repo
        router.get_rankings = AsyncMock(return_value=[["svc", "model"]])
        router.get_config.return_value = {"chat": "balanced"}
        app.state._model_performance_router = router

        with TestClient(app) as client:
            assert client.get(
                "/admin/models/performance",
                params={"service": "svc", "task_type": "chat"},
            ).json() == {"performance": [{"service": "svc"}]}
            assert client.get(
                "/admin/models/ranking",
                params={"task_type": "chat", "strategy": "balanced"},
            ).json()["ranking"] == [["svc", "model"]]
            assert client.get("/admin/models/router/status").json() == {
                "status": "active",
                "config": {"chat": "balanced"},
            }
            assert client.put(
                "/admin/models/router/config",
                json={"task_type": "chat", "strategy": "balanced"},
            ).json()["updated"] is True

        repo.get_summary.assert_awaited_once_with(service="svc", task_type="chat")
        router.set_strategy.assert_called_once_with("chat", "balanced")

    def test_router_exceptions_are_observable(self) -> None:
        app = _registered_app()
        repo = MagicMock()
        repo.get_summary = AsyncMock(side_effect=RuntimeError("summary failed"))
        router = MagicMock()
        router._repo = repo
        router.get_rankings = AsyncMock(side_effect=RuntimeError("ranking failed"))
        app.state._model_performance_router = router

        with TestClient(app) as client:
            summary = client.get("/admin/models/performance")
            ranking = client.get(
                "/admin/models/ranking", params={"task_type": "chat"}
            )

        assert summary.json()["error"] == "summary failed"
        assert ranking.status_code == 500
        assert ranking.json()["detail"] == "ranking failed"

    def test_invalid_ranking_and_config_inputs_are_rejected(self) -> None:
        app = _registered_app()
        router = MagicMock()
        router._repo = MagicMock()
        app.state._model_performance_router = router

        with TestClient(app) as client:
            assert client.get(
                "/admin/models/ranking",
                params={"task_type": "", "strategy": "balanced"},
            ).status_code == 422
            assert client.get(
                "/admin/models/ranking",
                params={"task_type": "chat", "strategy": "unknown"},
            ).status_code == 422
            assert client.put(
                "/admin/models/router/config",
                content=b"not-json",
                headers={"content-type": "application/json"},
            ).status_code == 422
            assert client.put(
                "/admin/models/router/config",
                json={"task_type": "chat", "strategy": "unknown"},
            ).status_code == 422
