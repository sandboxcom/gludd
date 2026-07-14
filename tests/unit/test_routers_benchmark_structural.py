"""Structural tests for routers/benchmark.py — benchmark scoring, leaderboard, and prompt-profile endpoints."""

from __future__ import annotations

import inspect
from typing import get_type_hints

from general_ludd.routers.benchmark import register

# — Route paths expected to be registered —
_EXPECTED_ROUTES = [
    "/admin/benchmark/scores",
    "/admin/benchmark/recent",
    "/admin/benchmark/leaderboard",
    "/admin/benchmark/record",
    "/admin/prompt-profiles",
]


# — Handler parameter signatures —
_HANDLER_PARAMS = {
    "admin_benchmark_scores": {"task_type"},
    "admin_benchmark_recent": {"limit"},
    "admin_benchmark_leaderboard": {"task_type"},
    "admin_benchmark_record": {"req"},
    "admin_prompt_profiles": set(),
}


class TestModuleImport:
    def test_register_is_importable(self):
        assert register is not None

    def test_register_is_callable(self):
        assert callable(register)


class TestRegisterSignature:
    def test_register_has_two_parameters(self):
        sig = inspect.signature(register)
        params = list(sig.parameters.keys())
        assert len(params) == 2

    def test_first_param_is_app(self):
        sig = inspect.signature(register)
        params = list(sig.parameters.keys())
        assert params[0] == "app"

    def test_second_param_is_daemon_state(self):
        sig = inspect.signature(register)
        params = list(sig.parameters.keys())
        assert params[1] == "_daemon_state"

    def test_register_return_annotation_is_none(self):
        hints = get_type_hints(register)
        assert hints.get("return") is type(None)


class TestRouteRegistration:
    @classmethod
    def _build_app(cls) -> object:
        from fastapi import FastAPI

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        return app

    def test_all_routes_registered_on_app(self):
        app = self._build_app()
        registered = {r.path for r in app.routes if hasattr(r, "path")}
        for route in _EXPECTED_ROUTES:
            assert route in registered, f"Route {route} not found; found: {sorted(registered)}"

    def test_scores_route_is_http_get(self):
        app = self._build_app()
        for r in app.routes:
            if r.path == "/admin/benchmark/scores":
                assert "GET" in r.methods
                return
        raise AssertionError("Route /admin/benchmark/scores not found")

    def test_recent_route_is_http_get(self):
        app = self._build_app()
        for r in app.routes:
            if r.path == "/admin/benchmark/recent":
                assert "GET" in r.methods
                return
        raise AssertionError("Route /admin/benchmark/recent not found")

    def test_leaderboard_route_is_http_get(self):
        app = self._build_app()
        for r in app.routes:
            if r.path == "/admin/benchmark/leaderboard":
                assert "GET" in r.methods
                return
        raise AssertionError("Route /admin/benchmark/leaderboard not found")

    def test_record_route_is_http_post(self):
        app = self._build_app()
        for r in app.routes:
            if r.path == "/admin/benchmark/record":
                assert "POST" in r.methods
                return
        raise AssertionError("Route /admin/benchmark/record not found")

    def test_prompt_profiles_route_is_http_get(self):
        app = self._build_app()
        for r in app.routes:
            if r.path == "/admin/prompt-profiles":
                assert "GET" in r.methods
                return
        raise AssertionError("Route /admin/prompt-profiles not found")


class TestHandlerSignatures:
    @classmethod
    def _get_handlers(cls) -> dict[str, object]:
        from fastapi import FastAPI

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        handlers: dict[str, object] = {}
        for r in app.routes:
            if hasattr(r, "endpoint") and hasattr(r, "path"):
                name = r.endpoint.__name__
                handlers[name] = r.endpoint
        return handlers

    def test_all_expected_handlers_exist(self):
        handlers = self._get_handlers()
        for name in _HANDLER_PARAMS:
            assert name in handlers, f"Handler {name} not found in registered endpoints"

    def test_handler_param_names_match(self):
        handlers = self._get_handlers()
        for name, expected_params in _HANDLER_PARAMS.items():
            sig = inspect.signature(handlers[name])
            actual_params = set(sig.parameters.keys())
            assert actual_params == expected_params, (
                f"Handler {name}: expected params {expected_params!r}, "
                f"got {actual_params!r}"
            )

    def test_handler_return_is_dict_str_object(self):
        handlers = self._get_handlers()
        for name in _HANDLER_PARAMS:
            handler = handlers[name]
            hints = get_type_hints(handler)
            ret = hints.get("return")
            assert ret is not None, f"Handler {name} has no return annotation"
            assert ret == dict[str, object], (
                f"Handler {name} returns {ret}, expected dict[str, object]"
            )
