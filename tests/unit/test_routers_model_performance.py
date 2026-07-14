"""Structural tests for routers/model_performance.py — model performance router endpoints."""

from __future__ import annotations

import logging

from fastapi import FastAPI


class TestModuleImports:
    def test_module_can_be_imported(self):
        import general_ludd.routers.model_performance

        assert general_ludd.routers.model_performance is not None

    def test_register_is_callable(self):
        from general_ludd.routers.model_performance import register

        assert callable(register)

    def test_logger_exists(self):
        from general_ludd.routers.model_performance import logger

        assert isinstance(logger, logging.Logger)
        assert logger.name == "general_ludd.routers.model_performance"


class TestHelperFunctions:
    def test_get_performance_router_exists(self):
        from general_ludd.routers.model_performance import _get_performance_router

        assert callable(_get_performance_router)

    def test_get_performance_router_returns_none_for_empty_state(self):
        from general_ludd.routers.model_performance import _get_performance_router

        app = FastAPI()
        result = _get_performance_router(app)
        assert result is None


class TestRegister:
    def test_registers_performance_routes(self):
        from general_ludd.routers.model_performance import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        routes = {r.path for r in app.routes}
        assert "/admin/models/performance" in routes
        assert "/admin/models/ranking" in routes
        assert "/admin/models/router/status" in routes
        assert "/admin/models/router/config" in routes

    def test_performance_endpoint_get_registered(self):
        from general_ludd.routers.model_performance import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        perf_route = next(
            (r for r in all_routes if r.path == "/admin/models/performance"), None
        )
        assert perf_route is not None
        assert "GET" in perf_route.methods

    def test_ranking_endpoint_get_registered(self):
        from general_ludd.routers.model_performance import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        ranking_route = next(
            (r for r in all_routes if r.path == "/admin/models/ranking"), None
        )
        assert ranking_route is not None
        assert "GET" in ranking_route.methods

    def test_router_status_endpoint_registered(self):
        from general_ludd.routers.model_performance import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        status_route = next(
            (r for r in all_routes if r.path == "/admin/models/router/status"), None
        )
        assert status_route is not None
        assert "GET" in status_route.methods

    def test_router_config_endpoint_put_registered(self):
        from general_ludd.routers.model_performance import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        config_route = next(
            (r for r in all_routes if r.path == "/admin/models/router/config"), None
        )
        assert config_route is not None
        assert "PUT" in config_route.methods

    def test_initializes_router_state(self):
        from general_ludd.routers.model_performance import register

        app = FastAPI()
        register(app, {})
        assert hasattr(app.state, "_model_performance_router")
