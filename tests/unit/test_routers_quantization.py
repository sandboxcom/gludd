"""Structural tests for routers/quantization.py — quantization detection endpoints."""

from __future__ import annotations

from fastapi import FastAPI


class TestModuleImports:
    def test_module_can_be_imported(self):
        import general_ludd.routers.quantization

        assert general_ludd.routers.quantization is not None

    def test_register_is_callable(self):
        from general_ludd.routers.quantization import register

        assert callable(register)


class TestRegister:
    def test_registers_quantization_routes(self):
        from general_ludd.routers.quantization import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        routes = {r.path for r in app.routes}
        assert "/admin/quantization" in routes
        assert "/admin/quantization/detect" in routes
        assert "/admin/quantization/drift-check" in routes
        assert "/admin/quantization/{model_id}" in routes

    def test_list_quantization_endpoint_registered(self):
        from general_ludd.routers.quantization import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        list_route = next(
            (r for r in all_routes if r.path == "/admin/quantization"), None
        )
        assert list_route is not None
        assert "GET" in list_route.methods

    def test_detect_quantization_endpoint_registered(self):
        from general_ludd.routers.quantization import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        detect_route = next(
            (r for r in all_routes if r.path == "/admin/quantization/detect"), None
        )
        assert detect_route is not None
        assert "POST" in detect_route.methods

    def test_drift_check_endpoint_registered(self):
        from general_ludd.routers.quantization import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        drift_route = next(
            (r for r in all_routes if r.path == "/admin/quantization/drift-check"), None
        )
        assert drift_route is not None
        assert "POST" in drift_route.methods

    def test_get_model_endpoint_registered(self):
        from general_ludd.routers.quantization import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        get_route = next(
            (r for r in all_routes if r.path == "/admin/quantization/{model_id}"),
            None,
        )
        assert get_route is not None
        assert "GET" in get_route.methods
