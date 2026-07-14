"""Structural tests for routers/self_update.py — self-update pipeline admin endpoints."""

from __future__ import annotations

from fastapi import FastAPI


class TestModuleImports:
    def test_module_can_be_imported(self):
        import general_ludd.routers.self_update

        assert general_ludd.routers.self_update is not None

    def test_register_is_callable(self):
        from general_ludd.routers.self_update import register

        assert callable(register)


class TestHelperFunctions:
    def test_get_session_factory_exists(self):
        from general_ludd.routers.self_update import _get_session_factory

        assert callable(_get_session_factory)

    def test_get_session_factory_returns_none_for_empty_state(self):
        from general_ludd.routers.self_update import _get_session_factory

        app = FastAPI()
        result = _get_session_factory(app)
        assert result is None

    def test_request_from_payload_exists(self):
        from general_ludd.routers.self_update import _request_from_payload

        assert callable(_request_from_payload)

    def test_request_from_payload_uses_raw_text_preferred(self):
        from general_ludd.routers.self_update import _request_from_payload

        req = _request_from_payload({"raw_text": "hello", "text": "world"})
        assert req.raw_text == "hello"

    def test_request_from_payload_falls_back_to_text(self):
        from general_ludd.routers.self_update import _request_from_payload

        req = _request_from_payload({"text": "fallback"})
        assert req.raw_text == "fallback"

    def test_request_from_payload_defaults_requested_by(self):
        from general_ludd.routers.self_update import _request_from_payload

        req = _request_from_payload({"raw_text": "test"})
        assert req.requested_by == "user"

    def test_request_from_payload_empty_approval_token_normalizes_to_none(self):
        from general_ludd.routers.self_update import _request_from_payload

        req = _request_from_payload({"raw_text": "test", "approval_token": ""})
        assert req.approval_token is None


class TestRegister:
    def test_registers_self_update_routes(self):
        from general_ludd.routers.self_update import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        routes = {r.path for r in app.routes}
        assert "/admin/self-update/plan" in routes
        assert "/admin/self-update/enqueue" in routes

    def test_plan_endpoint_post_registered(self):
        from general_ludd.routers.self_update import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        plan_route = next(
            (r for r in all_routes if r.path == "/admin/self-update/plan"), None
        )
        assert plan_route is not None
        assert "POST" in plan_route.methods

    def test_enqueue_endpoint_post_registered(self):
        from general_ludd.routers.self_update import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        enqueue_route = next(
            (r for r in all_routes if r.path == "/admin/self-update/enqueue"), None
        )
        assert enqueue_route is not None
        assert "POST" in enqueue_route.methods
