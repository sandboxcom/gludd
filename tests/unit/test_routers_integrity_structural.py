"""Structural tests for routers/integrity.py —
file-integrity scan, approve/reject, selftest, gap-analysis, and log-audit endpoints."""

from __future__ import annotations

import inspect
import os

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from general_ludd.routers.integrity import (
    _MAX_LOG_AUDIT_ENTRIES,
    _confine_scan_paths,
    _integrity_changes,
    _integrity_log,
    _scan_roots,
    register,
)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestModuleImports:
    def test_register_is_callable(self):
        assert callable(register)

    def test_scan_roots_is_callable(self):
        assert callable(_scan_roots)

    def test_confine_scan_paths_is_callable(self):
        assert callable(_confine_scan_paths)


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


class TestIntegrityLog:
    def test_is_list(self):
        assert isinstance(_integrity_log, list)


# ---------------------------------------------------------------------------
# register (router wiring)
# ---------------------------------------------------------------------------


class TestRegister:
    def test_accepts_two_args(self):
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

    def test_registers_all_routes(self):
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


# ---------------------------------------------------------------------------
# _scan_roots
# ---------------------------------------------------------------------------


class TestScanRoots:
    def test_accepts_app_parameter(self):
        sig = inspect.signature(_scan_roots)
        params = list(sig.parameters.keys())
        assert "app" in params

    def test_returns_list_of_strings(self):
        app = FastAPI()
        roots = _scan_roots(app)
        assert isinstance(roots, list)
        for root in roots:
            assert isinstance(root, str)

    def test_includes_cwd(self):
        app = FastAPI()
        roots = _scan_roots(app)
        assert os.getcwd() in roots

    def test_includes_namespaced_state_not_global_tmp(self):
        import tempfile

        from general_ludd.security.state import project_state

        app = FastAPI()
        roots = _scan_roots(app)
        assert str(project_state().project_dir) in roots
        assert tempfile.gettempdir() not in roots

    def test_no_empty_strings_in_roots(self):
        app = FastAPI()
        roots = _scan_roots(app)
        assert "" not in roots


# ---------------------------------------------------------------------------
# _confine_scan_paths
# ---------------------------------------------------------------------------


class TestConfineScanPaths:
    def test_accepts_app_and_paths_parameters(self):
        sig = inspect.signature(_confine_scan_paths)
        params = list(sig.parameters.keys())
        assert "app" in params
        assert "paths" in params

    def test_rejects_escaping_path(self):
        app = FastAPI()
        with pytest.raises(HTTPException):
            _confine_scan_paths(app, ["/etc/passwd"])

    def test_accepts_cwd_path(self):
        app = FastAPI()
        result = _confine_scan_paths(app, [os.getcwd()])
        assert isinstance(result, list)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Behavioral: TestClient with mocked scanner
# ---------------------------------------------------------------------------


def _build_client() -> TestClient:
    app = FastAPI()
    register(app, {})
    return TestClient(app)


class TestScanEndpoint:
    def test_scan_with_no_paths_uses_defaults(self):
        client = _build_client()
        resp = client.post("/admin/integrity/scan", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert "changes" in data

    def test_scan_with_cwd_path_succeeds(self):
        client = _build_client()
        resp = client.post(
            "/admin/integrity/scan",
            json={"paths": [os.getcwd()]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "changes" in data

    def test_scan_with_escaping_path_returns_422(self):
        client = _build_client()
        resp = client.post(
            "/admin/integrity/scan",
            json={"paths": ["/etc/shadow"]},
        )
        assert resp.status_code == 422
        assert "escapes the allowed roots" in resp.json()["detail"]


class TestReportEndpoint:
    def test_returns_changes_and_log_count(self):
        client = _build_client()
        resp = client.get("/admin/integrity/report")
        assert resp.status_code == 200
        data = resp.json()
        assert "changes" in data
        assert "log_entries" in data
        assert isinstance(data["log_entries"], int)


class TestRejectEndpoint:
    def test_reject_outside_root_returns_422(self):
        client = _build_client()
        resp = client.post(
            "/admin/integrity/reject",
            json={"path": "/etc/passwd"},
        )
        assert resp.status_code == 422

    def test_reject_with_cwd_path_returns_200(self):
        client = _build_client()
        resp = client.post(
            "/admin/integrity/reject",
            json={"path": os.getcwd(), "reason": "test rejection"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "rejected"
        assert data["path"] == os.path.realpath(os.getcwd())

    def test_reject_logs_to_integrity_log(self):
        orig_len = len(_integrity_log)
        client = _build_client()
        resp = client.post(
            "/admin/integrity/reject",
            json={"path": os.getcwd(), "reason": "logged rejection"},
        )
        assert resp.status_code == 200
        assert len(_integrity_log) == orig_len + 1
        assert _integrity_log[-1]["action"] == "rejected"


class TestLogEndpoint:
    def test_returns_entries_list(self):
        client = _build_client()
        resp = client.get("/admin/integrity/log")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert isinstance(data["entries"], list)


class TestLogAuditEndpoint:
    def test_returns_findings_for_valid_input(self):
        client = _build_client()
        resp = client.post(
            "/admin/log-audit",
            json={
                "log_entries": [
                    {"level": "ERROR", "message": "test error", "timestamp": "2024-01-01T00:00:00Z"}
                ]
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "findings" in data
        assert "total_findings" in data

    def test_oversized_input_returns_413(self):
        client = _build_client()
        too_many = [{"msg": f"entry-{i}"} for i in range(_MAX_LOG_AUDIT_ENTRIES + 1)]
        resp = client.post("/admin/log-audit", json={"log_entries": too_many})
        assert resp.status_code == 413
        assert "exceeds maximum" in resp.json()["detail"]

    def test_equal_to_max_is_accepted(self):
        client = _build_client()
        at_max = [{"msg": f"entry-{i}"} for i in range(_MAX_LOG_AUDIT_ENTRIES)]
        resp = client.post("/admin/log-audit", json={"log_entries": at_max})
        assert resp.status_code == 200


class TestGapAnalysisEndpoint:
    def test_returns_gap_report_structure(self):
        client = _build_client()
        resp = client.post("/admin/gap-analysis", json={"repo_root": os.getcwd()})
        assert resp.status_code == 200
        data = resp.json()
        assert "total_gaps" in data
        assert "gaps" in data
        assert isinstance(data["gaps"], list)

    def test_empty_body_uses_defaults(self):
        client = _build_client()
        resp = client.post("/admin/gap-analysis", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert "total_gaps" in data
