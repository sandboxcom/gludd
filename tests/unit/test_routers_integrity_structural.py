"""Structural tests for routers/integrity.py — file-integrity scan, approve/reject, selftest, gap-analysis, and log-audit endpoints."""

from __future__ import annotations

import inspect

from general_ludd.routers.integrity import (
    _MAX_LOG_AUDIT_ENTRIES,
    _confine_scan_paths,
    _integrity_changes,
    _integrity_log,
    _scan_roots,
    register,
)


class TestModuleImports:
    def test_register_is_callable(self):
        assert callable(register)

    def test_scan_roots_is_callable(self):
        assert callable(_scan_roots)

    def test_confine_scan_paths_is_callable(self):
        assert callable(_confine_scan_paths)

    def test_integrity_key_error_importable(self):
        from general_ludd.integrity.scanner import IntegrityKeyError
        assert issubclass(IntegrityKeyError, Exception)


class TestMaxLogAuditEntries:
    def test_value_is_ten_thousand(self):
        assert _MAX_LOG_AUDIT_ENTRIES == 10_000

    def test_is_int(self):
        assert isinstance(_MAX_LOG_AUDIT_ENTRIES, int)

    def test_is_positive(self):
        assert _MAX_LOG_AUDIT_ENTRIES > 0


class TestIntegrityChanges:
    def test_is_list(self):
        assert isinstance(_integrity_changes, list)

    def test_starts_empty(self):
        assert _integrity_changes == []


class TestIntegrityLog:
    def test_is_list(self):
        assert isinstance(_integrity_log, list)

    def test_starts_empty(self):
        assert _integrity_log == []


class TestRegister:
    def test_accepts_two_args(self):
        from fastapi import FastAPI
        app = FastAPI()
        try:
            register(app, {})
        except Exception as exc:
            raise AssertionError(f"register raised: {exc}") from exc

    def test_signature_parameters(self):
        sig = inspect.signature(register)
        params = list(sig.parameters.keys())
        assert "app" in params
        assert "_daemon_state" in params
        assert len(params) == 2

    def test_registers_all_routes(self):
        from fastapi import FastAPI
        app = FastAPI()
        register(app, {})
        routes = {r.path for r in app.routes}
        assert "/admin/integrity/scan" in routes
        assert "/admin/integrity/report" in routes
        assert "/admin/integrity/approve" in routes
        assert "/admin/integrity/reject" in routes
        assert "/admin/integrity/log" in routes
        assert "/admin/selftest" in routes
        assert "/admin/gap-analysis" in routes
        assert "/admin/log-audit" in routes

    def test_route_methods(self):
        from fastapi import FastAPI
        app = FastAPI()
        register(app, {})
        route_methods = {r.path: r.methods for r in app.routes if hasattr(r, "methods")}
        assert "POST" in route_methods.get("/admin/integrity/scan", set())
        assert "GET" in route_methods.get("/admin/integrity/report", set())
        assert "POST" in route_methods.get("/admin/integrity/approve", set())
        assert "POST" in route_methods.get("/admin/integrity/reject", set())
        assert "GET" in route_methods.get("/admin/integrity/log", set())
        assert "POST" in route_methods.get("/admin/selftest", set())
        assert "POST" in route_methods.get("/admin/gap-analysis", set())
        assert "POST" in route_methods.get("/admin/log-audit", set())


class TestScanRoots:
    def test_accepts_app_parameter(self):
        sig = inspect.signature(_scan_roots)
        params = list(sig.parameters.keys())
        assert "app" in params

    def test_returns_list_of_strings(self):
        from fastapi import FastAPI
        app = FastAPI()
        roots = _scan_roots(app)
        assert isinstance(roots, list)
        for root in roots:
            assert isinstance(root, str)

    def test_includes_cwd(self):
        from fastapi import FastAPI
        import os
        app = FastAPI()
        roots = _scan_roots(app)
        assert os.getcwd() in roots

    def test_includes_tmp(self):
        from fastapi import FastAPI
        import tempfile
        app = FastAPI()
        roots = _scan_roots(app)
        assert tempfile.gettempdir() in roots

    def test_no_empty_strings_in_roots(self):
        from fastapi import FastAPI
        app = FastAPI()
        roots = _scan_roots(app)
        assert "" not in roots


class TestConfineScanPaths:
    def test_accepts_app_and_paths_parameters(self):
        sig = inspect.signature(_confine_scan_paths)
        params = list(sig.parameters.keys())
        assert "app" in params
        assert "paths" in params
        assert len(params) == 2

    def test_rejects_escaping_path(self):
        from fastapi import FastAPI
        from fastapi import HTTPException
        app = FastAPI()
        with __import__("pytest").raises(HTTPException):
            _confine_scan_paths(app, ["/etc/passwd"])

    def test_accepts_cwd_path(self):
        from fastapi import FastAPI
        import os
        app = FastAPI()
        result = _confine_scan_paths(app, [os.getcwd()])
        assert isinstance(result, list)
        assert len(result) == 1
