"""Structural tests for routers/mcp.py — MCP catalog admin endpoints."""

from __future__ import annotations

from fastapi import FastAPI


class TestModuleImports:
    def test_module_can_be_imported(self):
        import general_ludd.routers.mcp

        assert general_ludd.routers.mcp is not None

    def test_register_is_callable(self):
        from general_ludd.routers.mcp import register

        assert callable(register)


class TestRegister:
    def test_registers_mcp_catalog_routes(self):
        from general_ludd.routers.mcp import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        routes = {r.path for r in app.routes}
        assert "/admin/mcp/catalog/search" in routes
        assert "/admin/mcp/catalog/servers" in routes
        assert "/admin/mcp/catalog/servers/{name}" in routes

    def test_search_endpoint_post_registered(self):
        from general_ludd.routers.mcp import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        search_route = next(
            (r for r in all_routes if r.path == "/admin/mcp/catalog/search"), None
        )
        assert search_route is not None
        assert "POST" in search_route.methods

    def test_servers_endpoint_get_registered(self):
        from general_ludd.routers.mcp import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        servers_route = next(
            (r for r in all_routes if r.path == "/admin/mcp/catalog/servers"), None
        )
        assert servers_route is not None
        assert "GET" in servers_route.methods

    def test_server_by_name_endpoint_get_registered(self):
        from general_ludd.routers.mcp import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        server_route = next(
            (
                r
                for r in all_routes
                if r.path == "/admin/mcp/catalog/servers/{name}"
            ),
            None,
        )
        assert server_route is not None
        assert "GET" in server_route.methods
