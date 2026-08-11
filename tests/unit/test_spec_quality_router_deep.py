"""Deep tests for routers/spec_quality.py — edge cases, severity counts,
rule-to-finding serialization, and cross-endpoint invariant checks.
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


# ── edge-case entry fixtures ──────────────────────────────────────────

_EMPTY_ID_SPEC: dict[str, object] = {
    "spec_id": "",
    "body": "**Enforcement:** `make lint`\n**Behavior:** Blocks. exit 1",
}

_EMPTY_BODY_SPEC: dict[str, object] = {
    "spec_id": "EE001",
    "body": "",
}

_ADVISORY_SPEC: dict[str, object] = {
    "spec_id": "EE002",
    "body": ("**Behavior:** exit 1 on error, but advisory only — best effort.\n**Enforcement:** `make lint`"),
}

_MISSING_KEYS_SPEC: dict[str, Any] = {"extra": "data"}

_LONG_BODY_SPEC: dict[str, object] = {
    "spec_id": "EE003",
    "body": "**Enforcement:** `make lint`\n" + ("x" * 5000),
}

_NONEXISTENT_REF_SPEC: dict[str, object] = {
    "spec_id": "EE004",
    "body": "**Enforcement:** `nonexistent_file.xyz` and `make invalid-target`",
}

_CONCRETE_BACKTICK_SPEC: dict[str, object] = {
    "spec_id": "EE005",
    "body": "**Enforcement:** `enforce-stop.ts` plugin\n**Behavior:** Blocks text-only. exit 1",
}


class TestAuditDeep:
    """Deeper audit endpoint behaviour beyond basic shape."""

    def test_empty_spec_id_still_audited(self, client: TestClient) -> None:
        resp = client.post("/api/spec-quality/audit", json={"entries": [_EMPTY_ID_SPEC]})
        assert resp.status_code == 200
        data = resp.json()
        # empty body is not a finding for spec_id="" because it has Enforcement
        # but empty_id is still a recognised entry
        assert "findings" in data

    def test_empty_body_triggers_body_non_empty_finding(self, client: TestClient) -> None:
        resp = client.post("/api/spec-quality/audit", json={"entries": [_EMPTY_BODY_SPEC]})
        data = resp.json()
        r003_findings = [f for f in data["findings"] if f["rule_id"] == "R003"]
        assert len(r003_findings) >= 1
        assert "empty body" in r003_findings[0]["message"].lower()

    def test_advisory_behavior_triggers_warning(self, client: TestClient) -> None:
        resp = client.post("/api/spec-quality/audit", json={"entries": [_ADVISORY_SPEC]})
        data = resp.json()
        assert data["warning_count"] >= 1
        r005 = [f for f in data["findings"] if f["rule_id"] == "R005"]
        assert len(r005) >= 1
        assert r005[0]["severity"] == "warning"

    def test_missing_spec_id_defaults_to_empty_string(self, client: TestClient) -> None:
        resp = client.post("/api/spec-quality/audit", json={"entries": [{"body": ""}]})
        assert resp.status_code == 200

    def test_missing_body_defaults_to_empty_string(self, client: TestClient) -> None:
        resp = client.post("/api/spec-quality/audit", json={"entries": [{"spec_id": "X"}]})
        assert resp.status_code == 200

    def test_entries_with_missing_both_keys_still_process(self, client: TestClient) -> None:
        resp = client.post("/api/spec-quality/audit", json={"entries": [_MISSING_KEYS_SPEC]})
        assert resp.status_code == 200

    def test_long_body_does_not_crash(self, client: TestClient) -> None:
        resp = client.post("/api/spec-quality/audit", json={"entries": [_LONG_BODY_SPEC]})
        assert resp.status_code == 200

    def test_duplicate_spec_ids_are_counted_once_per_finding(self, client: TestClient) -> None:
        entries = [
            {"spec_id": "DUP", "body": ""},
            {"spec_id": "DUP", "body": ""},
        ]
        resp = client.post("/api/spec-quality/audit", json={"entries": entries})
        data = resp.json()
        assert data["unique_specs_checked"] == 1

    def test_severity_counts_are_accurate_mixed(self, client: TestClient) -> None:
        entries = [
            {"spec_id": "S0", "body": ""},  # R001 + R003 errors
            {"spec_id": "S1", "body": "**Enforcement:** `x`"},  # R002 error
            {"spec_id": "S2", "body": "**Enforcement:** TBD"},  # R004 error
            {"spec_id": "S3", "body": "**Enforcement:** `make lint`\n**Behavior:** advisory"},  # R005 warning
        ]
        resp = client.post("/api/spec-quality/audit", json={"entries": entries})
        data = resp.json()
        assert data["error_count"] + data["warning_count"] + data["info_count"] == data["total_findings"]

    def test_good_spec_produces_no_findings(self, client: TestClient) -> None:
        good = {
            "spec_id": "G1",
            "body": "**Enforcement:** `make lint`\n**Behavior:** Blocks on error. exit 1",
        }
        resp = client.post("/api/spec-quality/audit", json={"entries": [good]})
        data = resp.json()
        assert data["total_findings"] == 0

    def test_finding_line_defaults_to_zero(self, client: TestClient) -> None:
        resp = client.post("/api/spec-quality/audit", json={"entries": [{"spec_id": "X", "body": ""}]})
        data = resp.json()
        for f in data["findings"]:
            assert f["line"] == 0


class TestScanDeep:
    """Deeper scan endpoint behaviour."""

    def test_nonexistent_ref_triggers_src_finding(self, client: TestClient) -> None:
        resp = client.post(
            "/api/spec-quality/scan",
            json={"entries": [_NONEXISTENT_REF_SPEC]},
        )
        data = resp.json()
        src_findings = [f for f in data["findings"] if f["rule_id"].startswith("SRC_")]
        assert len(src_findings) >= 1

    def test_scan_without_entries_runs_structural_only(self, client: TestClient) -> None:
        resp = client.post("/api/spec-quality/scan")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["findings"], list)

    def test_scan_rules_applied_includes_builtin_ids(self, client: TestClient) -> None:
        resp = client.post("/api/spec-quality/scan")
        data = resp.json()
        assert any(rid.startswith("SRC_") for rid in data["rules_applied"])

    def test_scan_with_empty_entries_runs_structural(self, client: TestClient) -> None:
        resp = client.post("/api/spec-quality/scan", json={"entries": []})
        data = resp.json()
        assert isinstance(data["findings"], list)

    def test_scan_check_paths_non_string_ignored(self, client: TestClient) -> None:
        resp = client.post(
            "/api/spec-quality/scan",
            json={"check_paths": [42, None, True]},
        )
        assert resp.status_code == 200

    def test_scan_entries_without_enforcement_no_src_findings(self, client: TestClient) -> None:
        resp = client.post(
            "/api/spec-quality/scan",
            json={"entries": [{"spec_id": "X", "body": "just text"}]},
        )
        data = resp.json()
        src_for_x = [f for f in data["findings"] if f["spec_id"] == "X" and f["rule_id"].startswith("SRC_")]
        assert len(src_for_x) == 0

    def test_scan_concrete_plugin_ref_present(self, client: TestClient) -> None:
        resp = client.post(
            "/api/spec-quality/scan",
            json={"entries": [_CONCRETE_BACKTICK_SPEC]},
        )
        data = resp.json()
        src_plugin_findings = [f for f in data["findings"] if f["rule_id"] == "SRC_003"]
        assert len(src_plugin_findings) == 0

    def test_scan_returns_expected_shape(self, client: TestClient) -> None:
        resp = client.post("/api/spec-quality/scan")
        data = resp.json()
        for key in ("total_findings", "has_errors", "findings", "rules_applied"):
            assert key in data

    def test_scan_has_errors_when_enforcement_ref_missing(self, client: TestClient) -> None:
        bad = {"spec_id": "BAD", "body": "**Enforcement:** `fake_file.xyz`"}
        resp = client.post("/api/spec-quality/scan", json={"entries": [bad]})
        data = resp.json()
        assert data["has_errors"] is True


class TestRulesDeep:
    """Deeper rules endpoint invariants."""

    def test_all_five_rules_have_different_categories(self, client: TestClient) -> None:
        resp = client.get("/api/spec-quality/rules")
        data = resp.json()
        cats = {r["category"] for r in data["rules"]}
        assert len(cats) == 5

    def test_r005_is_warning_severity(self, client: TestClient) -> None:
        resp = client.get("/api/spec-quality/rules")
        data = resp.json()
        r005 = next(r for r in data["rules"] if r["rule_id"] == "R005")
        assert r005["severity"] == "warning"

    def test_r001_to_r004_are_error_severity(self, client: TestClient) -> None:
        resp = client.get("/api/spec-quality/rules")
        data = resp.json()
        for rule in data["rules"]:
            if rule["rule_id"] != "R005":
                assert rule["severity"] == "error"


class TestCrossEndpoint:
    """Invariants that hold across endpoints."""

    def test_rules_list_matches_audit_rules_applied(self, client: TestClient) -> None:
        rules_resp = client.get("/api/spec-quality/rules")
        rule_ids = {r["rule_id"] for r in rules_resp.json()["rules"]}

        audit_resp = client.post("/api/spec-quality/audit", json={"entries": []})
        audit_applied = set(audit_resp.json()["rules_applied"])
        assert audit_applied == rule_ids

    def test_finding_shape_identical_across_endpoints(self, client: TestClient) -> None:
        bad = {
            "spec_id": "BAD",
            "body": "**Enforcement:** `nonexistent.xyz`",
        }
        audit = client.post("/api/spec-quality/audit", json={"entries": [bad]}).json()
        scan = client.post("/api/spec-quality/scan", json={"entries": [bad]}).json()

        audit_keys = {tuple(f.keys()) for f in audit["findings"]}
        scan_keys = {tuple(f.keys()) for f in scan["findings"]}
        assert audit_keys == scan_keys

    def test_empty_entries_audit_and_scan_agree_on_counts(self, client: TestClient) -> None:
        audit = client.post("/api/spec-quality/audit", json={"entries": []}).json()
        scan = client.post("/api/spec-quality/scan", json={"entries": []}).json()
        assert audit["total_findings"] == scan["total_findings"] == 0
