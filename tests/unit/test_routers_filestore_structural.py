"""Structural tests for routers/filestore.py — filestore CRUD + bootstrap endpoints."""

from __future__ import annotations

import inspect

import general_ludd.routers.filestore as filestore
from general_ludd.routers.filestore import FILESTORE_WRITE_MAX_BYTES, register


class TestModuleImport:
    def test_module_imports(self):
        assert filestore is not None

    def test_register_exported(self):
        assert hasattr(filestore, "register")

    def test_max_bytes_exported(self):
        assert hasattr(filestore, "FILESTORE_WRITE_MAX_BYTES")


class TestFilestoreWriteMaxBytes:
    def test_is_int(self):
        assert isinstance(FILESTORE_WRITE_MAX_BYTES, int)

    def test_is_positive(self):
        assert FILESTORE_WRITE_MAX_BYTES > 0

    def test_default_is_ten_megabytes(self):
        assert FILESTORE_WRITE_MAX_BYTES >= 10 * 1024 * 1024


class TestRegister:
    def test_register_is_callable(self):
        assert callable(register)

    def test_register_accepts_two_args(self):
        sig = inspect.signature(register)
        params = list(sig.parameters.keys())
        assert len(params) == 2
        assert "app" in params
        assert "_daemon_state" in params

    def test_register_does_not_raise(self):
        from fastapi import FastAPI
        app = FastAPI()
        daemon_state: dict[str, object] = {}
        try:
            register(app, daemon_state)
        except Exception as exc:
            raise AssertionError(f"register raised: {exc}") from exc


class TestRouteRegistration:
    def test_list_route_registered(self):
        from fastapi import FastAPI
        app = FastAPI()
        register(app, {})
        routes = {r.path for r in app.routes}
        assert "/admin/filestore/list" in routes

    def test_read_route_registered(self):
        from fastapi import FastAPI
        app = FastAPI()
        register(app, {})
        routes = {r.path for r in app.routes}
        assert "/admin/filestore/read" in routes

    def test_write_route_registered(self):
        from fastapi import FastAPI
        app = FastAPI()
        register(app, {})
        routes = {r.path for r in app.routes}
        assert "/admin/filestore/write" in routes

    def test_remove_route_registered(self):
        from fastapi import FastAPI
        app = FastAPI()
        register(app, {})
        routes = {r.path for r in app.routes}
        assert "/admin/filestore/remove" in routes

    def test_bootstrap_route_registered(self):
        from fastapi import FastAPI
        app = FastAPI()
        register(app, {})
        routes = {r.path for r in app.routes}
        assert "/admin/filestore/bootstrap" in routes

    def test_binaries_route_registered(self):
        from fastapi import FastAPI
        app = FastAPI()
        register(app, {})
        routes = {r.path for r in app.routes}
        assert "/admin/filestore/binaries" in routes

    def test_all_routes_registered(self):
        from fastapi import FastAPI
        app = FastAPI()
        register(app, {})
        routes = {r.path for r in app.routes}
        expected = {
            "/admin/filestore/list",
            "/admin/filestore/read",
            "/admin/filestore/write",
            "/admin/filestore/remove",
            "/admin/filestore/bootstrap",
            "/admin/filestore/binaries",
        }
        assert expected <= routes


class TestRouteMethods:
    def test_list_is_get(self):
        from fastapi import FastAPI
        app = FastAPI()
        register(app, {})
        for r in app.routes:
            if r.path == "/admin/filestore/list":
                assert "GET" in r.methods
                return
        assert False, "list route not registered"

    def test_read_is_get(self):
        from fastapi import FastAPI
        app = FastAPI()
        register(app, {})
        for r in app.routes:
            if r.path == "/admin/filestore/read":
                assert "GET" in r.methods
                return
        assert False, "read route not registered"

    def test_write_is_post(self):
        from fastapi import FastAPI
        app = FastAPI()
        register(app, {})
        for r in app.routes:
            if r.path == "/admin/filestore/write":
                assert "POST" in r.methods
                return
        assert False, "write route not registered"

    def test_remove_is_delete(self):
        from fastapi import FastAPI
        app = FastAPI()
        register(app, {})
        for r in app.routes:
            if r.path == "/admin/filestore/remove":
                assert "DELETE" in r.methods
                return
        assert False, "remove route not registered"

    def test_bootstrap_is_post(self):
        from fastapi import FastAPI
        app = FastAPI()
        register(app, {})
        for r in app.routes:
            if r.path == "/admin/filestore/bootstrap":
                assert "POST" in r.methods
                return
        assert False, "bootstrap route not registered"

    def test_binaries_is_get(self):
        from fastapi import FastAPI
        app = FastAPI()
        register(app, {})
        for r in app.routes:
            if r.path == "/admin/filestore/binaries":
                assert "GET" in r.methods
                return
        assert False, "binaries route not registered"
