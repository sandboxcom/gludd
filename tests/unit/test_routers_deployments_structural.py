"""Structural tests for routers/deployments.py — deployment health endpoints."""

from __future__ import annotations

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
)


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


class TestGetHealthChecker:
    def test_returns_none_without_router(self):
        from fastapi import FastAPI
        app = FastAPI()
        result = _get_health_checker(app)
        assert result is None


class TestFixApprovalError:
    def test_is_exception(self):
        err = FixApprovalError("test error")
        assert isinstance(err, Exception)
        assert str(err) == "test error"
