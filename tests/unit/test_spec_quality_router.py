"""Unit tests for routers/spec_quality.py — FastAPI endpoints.

Follows the router test convention: TestClient over a bare FastAPI app
with the router registered.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.spec_quality import register


@pytest.fixture
def app() -> FastAPI:
    _app = FastAPI()
    register(_app, {})
    return _app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


_GOOD_SPEC: dict[str, object] = {
    "spec_id": "AA001",
    "body": "**Enforcement:** `make lint`\n**Behavior:** Blocks on error. exit 1",
}

_BAD_SPEC: dict[str, object] = {
    "spec_id": "AA002",
    "body": "no enforcement field here",
}

_PLACEHOLDER_SPEC: dict[str, object] = {
    "spec_id": "AA003",
    "body": "**Enforcement:** TBD — planned for future release\n**Behavior:** Might block.",
}


class TestAuditEndpoint:
    def test_post_audit_empty_body_returns_200(self, client: TestClient) -> None:
        resp = client.post("/api/spec-quality/audit")
        assert resp.status_code == 200

    def test_post_audit_null_body_returns_200(self, client: TestClient) -> None:
        resp = client.post("/api/spec-quality/audit", json=None)
        assert resp.status_code == 200

    def test_post_audit_empty_json_returns_200(self, client: TestClient) -> None:
        resp = client.post("/api/spec-quality/audit", json={})
        assert resp.status_code == 200

    def test_post_audit_returns_expected_shape(self, client: TestClient) -> None:
        resp = client.post("/api/spec-quality/audit", json={"entries": [_GOOD_SPEC]})
        assert resp.status_code == 200
        data: dict[str, object] = resp.json()
        for key in (
            "total_findings",
            "error_count",
            "warning_count",
            "info_count",
            "unique_specs_checked",
            "unique_rules_fired",
            "rules_applied",
            "has_errors",
            "findings",
        ):
            assert key in data, f"Missing key: {key}"

    def test_post_audit_good_spec_has_no_errors(self, client: TestClient) -> None:
        resp = client.post("/api/spec-quality/audit", json={"entries": [_GOOD_SPEC]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_errors"] is False
        assert data["error_count"] == 0

    def test_post_audit_bad_spec_has_errors(self, client: TestClient) -> None:
        resp = client.post("/api/spec-quality/audit", json={"entries": [_BAD_SPEC]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_errors"] is True
        assert data["error_count"] >= 1

    def test_post_audit_placeholder_spec_has_errors(self, client: TestClient) -> None:
        resp = client.post("/api/spec-quality/audit", json={"entries": [_PLACEHOLDER_SPEC]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_errors"] is True

    def test_post_audit_multiple_entries(self, client: TestClient) -> None:
        entries = [_GOOD_SPEC, _BAD_SPEC, _PLACEHOLDER_SPEC]
        resp = client.post("/api/spec-quality/audit", json={"entries": entries})
        assert resp.status_code == 200
        data = resp.json()
        assert data["unique_specs_checked"] >= 2

    def test_post_audit_findings_have_expected_shape(self, client: TestClient) -> None:
        resp = client.post("/api/spec-quality/audit", json={"entries": [_BAD_SPEC]})
        data = resp.json()
        for finding in data["findings"]:
            for key in ("rule_id", "spec_id", "severity", "message", "evidence", "line"):
                assert key in finding

    def test_post_audit_non_list_entries_is_ignored(self, client: TestClient) -> None:
        resp = client.post("/api/spec-quality/audit", json={"entries": "not a list"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_findings"] == 0

    def test_post_audit_ignores_non_dict_items(self, client: TestClient) -> None:
        entries: list[Any] = [_GOOD_SPEC, "string", 42, None]
        resp = client.post("/api/spec-quality/audit", json={"entries": entries})
        assert resp.status_code == 200


class TestScanEndpoint:
    def test_post_scan_empty_body_returns_200(self, client: TestClient) -> None:
        resp = client.post("/api/spec-quality/scan")
        assert resp.status_code == 200

    def test_post_scan_null_body_returns_200(self, client: TestClient) -> None:
        resp = client.post("/api/spec-quality/scan", json=None)
        assert resp.status_code == 200

    def test_post_scan_returns_expected_shape(self, client: TestClient) -> None:
        resp = client.post("/api/spec-quality/scan", json={"entries": [_GOOD_SPEC]})
        assert resp.status_code == 200
        data: dict[str, object] = resp.json()
        for key in (
            "total_findings",
            "error_count",
            "warning_count",
            "info_count",
            "unique_specs_checked",
            "unique_rules_fired",
            "rules_applied",
            "has_errors",
            "findings",
        ):
            assert key in data, f"Missing key: {key}"

    def test_post_scan_with_check_paths(self, client: TestClient) -> None:
        resp = client.post(
            "/api/spec-quality/scan",
            json={"entries": [_GOOD_SPEC], "check_paths": ["Makefile"]},
        )
        assert resp.status_code == 200

    def test_post_scan_ignores_non_string_paths(self, client: TestClient) -> None:
        resp = client.post(
            "/api/spec-quality/scan",
            json={"entries": [_GOOD_SPEC], "check_paths": ["Makefile", 42, None]},
        )
        assert resp.status_code == 200

    def test_post_scan_with_good_spec_has_no_errors(self, client: TestClient) -> None:
        resp = client.post("/api/spec-quality/scan", json={"entries": [_GOOD_SPEC]})
        assert resp.status_code == 200
        data = resp.json()
        assert "findings" in data

    def test_post_scan_findings_have_expected_shape(self, client: TestClient) -> None:
        resp = client.post(
            "/api/spec-quality/scan",
            json={"entries": [{"spec_id": "X", "body": "**Enforcement:** `nonexistent_file.xyz`"}]},
        )
        data = resp.json()
        for finding in data["findings"]:
            for key in ("rule_id", "spec_id", "severity", "message", "evidence", "line"):
                assert key in finding


class TestRulesEndpoint:
    def test_get_rules_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/spec-quality/rules")
        assert resp.status_code == 200

    def test_get_rules_returns_expected_shape(self, client: TestClient) -> None:
        resp = client.get("/api/spec-quality/rules")
        data: dict[str, object] = resp.json()
        assert "count" in data
        assert "rules" in data
        assert data["count"] == 5
        assert isinstance(data["rules"], list)

    def test_get_rules_each_has_expected_keys(self, client: TestClient) -> None:
        resp = client.get("/api/spec-quality/rules")
        data = resp.json()
        rule: dict[str, object] = data["rules"][0]
        for key in ("rule_id", "name", "description", "category", "severity", "active"):
            assert key in rule, f"Missing key: {key}"

    def test_get_rules_all_active(self, client: TestClient) -> None:
        resp = client.get("/api/spec-quality/rules")
        data = resp.json()
        for rule in data["rules"]:
            assert rule["active"] is True

    def test_get_rules_r001_is_enforcement_present(self, client: TestClient) -> None:
        resp = client.get("/api/spec-quality/rules")
        data = resp.json()
        r001 = next(r for r in data["rules"] if r["rule_id"] == "R001")
        assert r001["category"] == "enforcement_present"


class TestRegistration:
    def test_register_adds_routes(self) -> None:
        app = FastAPI()
        register(app, {})
        routes = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/api/spec-quality/audit" in routes
        assert "/api/spec-quality/scan" in routes
        assert "/api/spec-quality/rules" in routes

    def test_register_is_callable_twice(self) -> None:
        app = FastAPI()
        register(app, {})
        register(app, {})
