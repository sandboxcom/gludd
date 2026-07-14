"""Structural tests for routers/deployments.py — deployment health endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.infra.fix_approval import FixApprovalError
from general_ludd.routers.deployments import (
    DeploymentHealthListResponse,
    DeploymentHealthResponse,
    FindingResponse,
    IncidentListResponse,
    IncidentResponse,
    MisconfigCheckRequest,
    MisconfigCheckResponse,
    RemediationResponse,
    SuggestFixRequest,
    _dict_to_finding,
    _get_health_checker,
    register,
)

# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestDeploymentHealthResponse:
    def test_model_fields(self):
        resp = DeploymentHealthResponse(
            deployment_id="d1",
            healthy=True,
            consecutive_failures=0,
            last_error=None,
            last_check=123.0,
        )
        assert resp.deployment_id == "d1"
        assert resp.healthy is True
        assert resp.consecutive_failures == 0
        assert resp.last_error is None
        assert resp.last_check == 123.0


class TestDeploymentHealthListResponse:
    def test_model_fields(self):
        resp = DeploymentHealthListResponse(deployments=[], total=0)
        assert resp.deployments == []
        assert resp.total == 0


class TestIncidentResponse:
    def test_model_fields(self):
        resp = IncidentResponse(
            deployment_id="d1",
            timestamp=123.0,
            kind="error",
            detail="something broke",
            was_remediated=False,
        )
        assert resp.deployment_id == "d1"
        assert resp.kind == "error"
        assert resp.was_remediated is False


class TestIncidentListResponse:
    def test_model_fields(self):
        resp = IncidentListResponse(incidents=[], total=0)
        assert resp.incidents == []
        assert resp.total == 0


class TestMisconfigCheckRequest:
    def test_default_values(self):
        req = MisconfigCheckRequest(deployment={})
        assert req.deployment == {}
        assert req.gpu_info is None
        assert req.gpu_type is None
        assert req.gpu_count == 1

    def test_with_gpu_context(self):
        req = MisconfigCheckRequest(deployment={}, gpu_type="a100", gpu_count=4)
        assert req.gpu_type == "a100"
        assert req.gpu_count == 4


class TestFindingResponse:
    def test_model_fields(self):
        resp = FindingResponse(
            rule_id="R001",
            severity="critical",
            engine="default",
            message="bad config",
            remediation="fix it",
            evidence={},
        )
        assert resp.rule_id == "R001"
        assert resp.severity == "critical"


class TestRemediationResponse:
    def test_model_fields(self):
        resp = RemediationResponse(
            rule_id="R001",
            format="json",
            config_patch={},
            requires_restart=True,
            notes="apply patch",
        )
        assert resp.rule_id == "R001"
        assert resp.requires_restart is True


class TestMisconfigCheckResponse:
    def test_model_fields(self):
        resp = MisconfigCheckResponse(
            findings=[],
            remediations=[],
            has_critical=False,
        )
        assert resp.findings == []
        assert resp.has_critical is False


class TestSuggestFixRequest:
    def test_default_values(self):
        req = SuggestFixRequest(deployment={})
        assert req.deployment == {}
        assert req.findings is None
        assert req.gpu_count == 1


class TestDictToFinding:
    def test_constructs_finding_from_dict(self):
        d = {
            "rule_id": "R001",
            "severity": "warn",
            "engine": "default",
            "message": "msg",
            "remediation": "rem",
            "evidence": {"key": "val"},
        }
        finding = _dict_to_finding(d)
        assert finding.rule_id == "R001"
        assert finding.severity == "warn"
        assert finding.evidence == {"key": "val"}

    def test_empty_dict_defaults(self):
        finding = _dict_to_finding({})
        assert finding.rule_id == ""
        assert finding.severity == "warn"
        assert finding.evidence == {}

    def test_missing_severity_defaults_to_warn(self):
        finding = _dict_to_finding({"rule_id": "X"})
        assert finding.severity == "warn"

    def test_none_evidence_becomes_empty_dict(self):
        finding = _dict_to_finding({"evidence": None})
        assert finding.evidence == {}

    def test_non_dict_evidence_becomes_empty_dict(self):
        finding = _dict_to_finding({"evidence": ["not", "a", "dict"]})
        assert finding.evidence == {}

    def test_missing_keys_yield_empty_strings(self):
        finding = _dict_to_finding({})
        assert finding.rule_id == ""
        assert finding.message == ""
        assert finding.remediation == ""


# ---------------------------------------------------------------------------
# Behavioral: TestClient with mocked health checker
# ---------------------------------------------------------------------------


def _build_client(
    health_checker: object | None = None,
    fix_manager: object | None = None,
) -> TestClient:
    app = FastAPI()
    if health_checker is not None:
        router_mock = MagicMock()
        router_mock.health_checker = health_checker
        app.state._deployment_health_router = router_mock
    if fix_manager is not None:
        app.state._fix_approval_manager = fix_manager
    register(app, {})
    return TestClient(app)


class TestGetHealthChecker:
    def test_returns_none_without_router(self):
        app = FastAPI()
        result = _get_health_checker(app)
        assert result is None


class TestFixApprovalError:
    def test_is_exception(self):
        err = FixApprovalError("test error")
        assert isinstance(err, Exception)
        assert str(err) == "test error"


class TestHealthEndpoint:
    def test_returns_503_when_checker_not_wired(self):
        client = _build_client()
        resp = client.get("/admin/deployments/health")
        assert resp.status_code == 503
        assert "not wired" in resp.json()["detail"]

    def test_returns_200_with_empty_statuses(self):
        mock_checker = MagicMock()
        mock_checker.all_statuses.return_value = {}
        client = _build_client(health_checker=mock_checker)
        resp = client.get("/admin/deployments/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deployments"] == []
        assert data["total"] == 0

    def test_returns_statuses_when_present(self):
        from general_ludd.models.deployment_health import DeploymentStatus
        status = DeploymentStatus(
            deployment_id="d1",
            healthy=True,
            consecutive_failures=0,
            last_error=None,
            last_check=100.0,
        )
        mock_checker = MagicMock()
        mock_checker.all_statuses.return_value = {"d1": status}
        client = _build_client(health_checker=mock_checker)
        resp = client.get("/admin/deployments/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["deployments"][0]["deployment_id"] == "d1"
        assert data["deployments"][0]["healthy"] is True


class TestRemediateEndpoint:
    def test_returns_503_when_checker_not_wired(self):
        client = _build_client()
        resp = client.post("/admin/deployments/d1/remediate")
        assert resp.status_code == 503

    def test_force_remediate_returns_status_dict(self):
        from general_ludd.models.deployment_health import DeploymentStatus
        status = DeploymentStatus(
            deployment_id="d1", healthy=True, consecutive_failures=0,
            last_error=None, last_check=200.0,
        )
        mock_checker = MagicMock()
        mock_checker.force_remediate.return_value = True
        mock_checker.get_status.return_value = status
        client = _build_client(health_checker=mock_checker)
        resp = client.post("/admin/deployments/d1/remediate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deployment_id"] == "d1"
        assert data["healthy"] is True


class TestIncidentsEndpoint:
    def test_returns_503_when_checker_not_wired(self):
        client = _build_client()
        resp = client.get("/admin/deployments/incidents")
        assert resp.status_code == 503

    def test_returns_200_with_empty_incidents(self):
        mock_checker = MagicMock()
        mock_checker.get_incidents.return_value = []
        client = _build_client(health_checker=mock_checker)
        resp = client.get("/admin/deployments/incidents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["incidents"] == []
        assert data["total"] == 0

    def test_respects_limit_query_param(self):
        mock_checker = MagicMock()
        mock_checker.get_incidents.return_value = []
        client = _build_client(health_checker=mock_checker)
        client.get("/admin/deployments/incidents?limit=50")
        mock_checker.get_incidents.assert_called_with(limit=50)


class TestMisconfigCheckEndpoint:
    def test_valid_deployment_returns_findings(self):
        client = _build_client()
        resp = client.post(
            "/admin/deployments/misconfig-check",
            json={"deployment": {"engine": "vllm", "gpu_memory_utilization": 0.99}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "findings" in data
        assert "remediations" in data
        assert "has_critical" in data

    def test_malformed_deployment_list_is_critical_200(self):
        client = _build_client()
        resp = client.post(
            "/admin/deployments/misconfig-check",
            json={"deployment": []},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_critical"] is True


class TestSuggestFixEndpoint:
    def test_returns_fix_id_and_patch(self):
        client = _build_client()
        resp = client.post(
            "/admin/deployments/suggest-fix",
            json={"deployment": {"engine": "vllm"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "fix_id" in data
        assert "patch" in data
        assert data["source"] == "deterministic"


class TestFixApproveEndpoint:
    def test_returns_409_for_unknown_fix_id(self):
        client = _build_client()
        resp = client.post("/admin/deployments/fixes/nonexistent/approve")
        assert resp.status_code == 409


class TestFixRejectEndpoint:
    def test_returns_409_for_unknown_fix_id(self):
        client = _build_client()
        resp = client.post("/admin/deployments/fixes/nonexistent/reject")
        assert resp.status_code == 409
