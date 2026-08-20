"""Deep edge-case tests for compute router.

Covers _finding_to_dict, _get_health_checker, deployment manager caching,
endpoint validation, error paths, and degradation surfaces not exercised
by any existing tests.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from general_ludd.infra.compute import ComputeProvider, GPUType
from general_ludd.infra.model_deploy_check import Finding
from general_ludd.routers.compute import _finding_to_dict, _get_health_checker, register
from general_ludd.security.permissions import Capability, PermissionSpec, PermissionSubject


def _inject_destroy_capability(app: FastAPI) -> None:
    """Attach the narrow capability required by the guarded destroy route."""
    spec = PermissionSpec(
        agent_type="compute-router-test",
        capabilities=[Capability(resource="admin:compute", actions=["destroy"])],
        subject=PermissionSubject.HUMAN,
    )

    class _InjectAuthSpec(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next: Any) -> Any:
            request.state.auth_spec = spec
            return await call_next(request)

    app.add_middleware(_InjectAuthSpec)

# ============================================================================
# _finding_to_dict
# ============================================================================


class TestFindingToDict:
    def test_all_fields_present(self) -> None:
        f = Finding(
            rule_id="MISCONFIG_01",
            severity="critical",
            engine="vllm",
            message="No GPUs found",
            remediation="Set gpu_count to 1",
            evidence={"host": "node-1"},
        )
        d = _finding_to_dict(f)
        assert d["rule_id"] == "MISCONFIG_01"
        assert d["severity"] == "critical"
        assert d["engine"] == "vllm"
        assert d["message"] == "No GPUs found"
        assert d["remediation"] == "Set gpu_count to 1"
        assert d["evidence"] == {"host": "node-1"}

    def test_none_fields(self) -> None:
        f = Finding(
            rule_id="R001",
            severity="info",
            engine="",
            message="ok",
            remediation=None,
            evidence=None,
        )
        d = _finding_to_dict(f)
        assert d["rule_id"] == "R001"
        assert d["remediation"] is None
        assert d["evidence"] is None

    def test_empty_strings(self) -> None:
        f = Finding(
            rule_id="",
            severity="",
            engine="",
            message="",
            remediation="",
            evidence="",
        )
        d = _finding_to_dict(f)
        assert d == {
            "rule_id": "",
            "severity": "",
            "engine": "",
            "message": "",
            "remediation": "",
            "evidence": "",
        }

    def test_special_characters(self) -> None:
        f = Finding(
            rule_id="R-1_2.3",
            severity="warning",
            engine="vllm",
            message="line 1\nline 2\twith tab",
            remediation='use "quotes"',
            evidence={"key": "value", "nested": {"a": 1}},
        )
        d = _finding_to_dict(f)
        assert "\n" in d["message"]
        assert "\t" in d["message"]
        assert d["evidence"]["nested"] == {"a": 1}

    def test_long_message(self) -> None:
        long_msg = "x" * 10000
        f = Finding(
            rule_id="R001",
            severity="info",
            engine="vllm",
            message=long_msg,
            remediation="",
            evidence=None,
        )
        d = _finding_to_dict(f)
        assert len(d["message"]) == 10000

    def test_round_trip_keys_match(self) -> None:
        f = Finding(
            rule_id="ID",
            severity="high",
            engine="trt",
            message="msg",
            remediation="fix",
            evidence={},
        )
        d = _finding_to_dict(f)
        assert set(d.keys()) == {"rule_id", "severity", "engine", "message", "remediation", "evidence"}


# ============================================================================
# _get_health_checker
# ============================================================================


class TestGetHealthChecker:
    def test_no_deployment_health_router(self) -> None:
        app = FastAPI()
        assert _get_health_checker(app) is None

    def test_router_has_no_health_checker_attr(self) -> None:
        app = FastAPI()
        app.state._deployment_health_router = object()
        assert _get_health_checker(app) is None

    def test_returns_checker_when_present(self) -> None:
        app = FastAPI()
        checker = object()
        router = MagicMock()
        router.health_checker = checker
        app.state._deployment_health_router = router
        assert _get_health_checker(app) is checker

    def test_router_is_none(self) -> None:
        app = FastAPI()
        app.state._deployment_health_router = None
        assert _get_health_checker(app) is None

    def test_router_is_broken_object(self) -> None:
        class Broken:
            pass

        app = FastAPI()
        app.state._deployment_health_router = Broken()
        assert _get_health_checker(app) is None


# ============================================================================
# GPUType enum validation
# ============================================================================


class TestGPUType:
    def test_valid_gpu_types(self) -> None:
        for name in ["a100_40", "a100_80", "h100", "a10g", "t4"]:
            g = GPUType(name)
            assert g.value == name

    def test_invalid_gpu_type_raises(self) -> None:
        with pytest.raises(ValueError):
            GPUType("NONEXISTENT_GPU")

    def test_case_sensitive(self) -> None:
        with pytest.raises(ValueError):
            GPUType("a100")


# ============================================================================
# ComputeProvider enum validation
# ============================================================================


class TestComputeProvider:
    def test_valid_providers(self) -> None:
        assert ComputeProvider("aws") == ComputeProvider.AWS
        assert ComputeProvider("gcp") == ComputeProvider.GCP
        assert ComputeProvider("azure") == ComputeProvider.AZURE

    def test_invalid_provider_raises(self) -> None:
        with pytest.raises(ValueError):
            ComputeProvider("nonexistent")


# ============================================================================
# Router registration — structural
# ============================================================================


class TestRegisterStructural:
    def test_register_creates_compute_deployments(self) -> None:
        app = FastAPI()
        register(app, {})
        assert hasattr(app.state, "_compute_deployments")
        assert isinstance(app.state._compute_deployments, dict)

    def test_register_does_not_overwrite_existing_compute_deployments(self) -> None:
        app = FastAPI()
        existing = {"a": 1}
        app.state._compute_deployments = existing
        register(app, {})
        assert app.state._compute_deployments is existing

    def test_routes_are_registered(self) -> None:
        app = FastAPI()
        register(app, {})
        route_paths = {r.path for r in app.routes}
        expected = {
            "/admin/compute/utilization",
            "/admin/compute/endpoints",
            "/admin/compute/idle",
            "/admin/compute/deploy",
            "/admin/compute/gpu-metrics",
            "/admin/compute/destroy/{instance_id}",
            "/admin/compute/gpu-metrics/{endpoint_id}",
            "/api/deployments",
        }
        assert expected.issubset(route_paths)


# ============================================================================
# Endpoint: GET /admin/compute/idle — no mocks needed
# ============================================================================


class TestIdleEndpoint:
    def test_returns_empty_when_no_daemon_state(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        response = client.get("/admin/compute/idle")
        assert response.status_code == 200
        data = response.json()
        assert data["idle_endpoints"] == []
        assert data["torn_down_endpoints"] == []

    def test_returns_configured_idle_endpoints(self) -> None:
        app = FastAPI()
        register(app, {})
        app.state.daemon_state = {
            "idle_endpoints": {"ep1": {"id": "ep1", "url": "http://x"}},
            "torn_down_endpoints": ["td1"],
        }
        client = TestClient(app)
        response = client.get("/admin/compute/idle")
        assert response.status_code == 200
        data = response.json()
        assert len(data["idle_endpoints"]) == 1
        assert data["idle_endpoints"][0]["id"] == "ep1"
        assert data["torn_down_endpoints"] == ["td1"]


# ============================================================================
# Endpoint validation: POST /admin/compute/endpoints
# ============================================================================


class TestRegisterEndpointValidation:
    def test_missing_endpoint_id_returns_422(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        response = client.post("/admin/compute/endpoints", json={"url": "http://x"})
        assert response.status_code == 422

    def test_missing_url_returns_422(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        response = client.post("/admin/compute/endpoints", json={"endpoint_id": "ep1"})
        assert response.status_code == 422

    def test_empty_endpoint_id_returns_422(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        response = client.post("/admin/compute/endpoints", json={"endpoint_id": "", "url": "http://x"})
        assert response.status_code == 422

    def test_empty_url_returns_422(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        response = client.post("/admin/compute/endpoints", json={"endpoint_id": "ep1", "url": ""})
        assert response.status_code == 422

    def test_missing_endpoint_id_and_url(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        response = client.post("/admin/compute/endpoints", json={})
        assert response.status_code == 422


# ============================================================================
# Endpoint validation: POST /admin/compute/deploy
# ============================================================================


class TestDeployValidation:
    def test_missing_gpu_type_returns_422(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        response = client.post("/admin/compute/deploy", json={"model_name": "llama"})
        assert response.status_code == 422

    def test_missing_model_name_returns_422(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        response = client.post("/admin/compute/deploy", json={"gpu_type": "a100_80"})
        assert response.status_code == 422

    def test_invalid_gpu_type_returns_422(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        response = client.post("/admin/compute/deploy", json={"gpu_type": "NOPE", "model_name": "llama"})
        assert response.status_code == 422
        assert "Unknown GPU type" in str(response.json()["detail"])

    def test_missing_both_fields_returns_422(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        response = client.post("/admin/compute/deploy", json={})
        assert response.status_code == 422

    def test_invalid_provider_returns_422(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        response = client.post(
            "/admin/compute/deploy",
            json={"gpu_type": "a100_80", "model_name": "llama", "provider": "bad_provider"},
        )
        assert response.status_code == 422
        assert "Unknown provider" in str(response.json()["detail"])


# ============================================================================
# Endpoint: DELETE /admin/compute/destroy/{instance_id} — no Capability check
# ============================================================================


class TestDestroyEndpoint:
    def test_unknown_instance_id_returns_404(self) -> None:
        app = FastAPI()
        register(app, {})
        _inject_destroy_capability(app)

        mock_mgr = MagicMock()
        mock_mgr.get_deployment_shared = AsyncMock(return_value=None)
        app.state._deployment_manager = mock_mgr

        with patch.object(app.state, "_deployment_manager", mock_mgr):
            client = TestClient(app)
            response = client.delete("/admin/compute/destroy/unknown-instance")
            assert response.status_code == 404
            assert "Unknown instance_id" in response.json()["detail"]

    def test_destroy_succeeds(self) -> None:
        app = FastAPI()
        register(app, {})
        _inject_destroy_capability(app)
        app.state._compute_deployments = {"inst-1": object()}

        mock_mgr = MagicMock()
        mock_mgr.get_deployment_shared = AsyncMock(return_value=MagicMock())
        mock_mgr.destroy = AsyncMock(return_value=None)
        app.state._deployment_manager = mock_mgr

        client = TestClient(app)
        response = client.delete("/admin/compute/destroy/inst-1")
        assert response.status_code == 200
        assert response.json() == {"destroyed": "inst-1"}
        assert "inst-1" not in app.state._compute_deployments

    def test_destroy_raises_500_on_error(self) -> None:
        app = FastAPI()
        register(app, {})
        _inject_destroy_capability(app)

        mock_mgr = MagicMock()
        mock_mgr.get_deployment_shared = AsyncMock(return_value=MagicMock())
        mock_mgr.destroy = AsyncMock(side_effect=RuntimeError("terraform error"))
        app.state._deployment_manager = mock_mgr

        client = TestClient(app)
        response = client.delete("/admin/compute/destroy/inst-1")
        assert response.status_code == 500
        assert "compute destroy failed" in response.json()["detail"]


# ============================================================================
# Deployment manager caching
# ============================================================================


class TestDeploymentManagerCaching:
    def test_second_call_reuses_cached_manager(self) -> None:
        app = FastAPI()
        register(app, {})
        _inject_destroy_capability(app)
        mgr1 = MagicMock()
        mgr1.get_deployment_shared = AsyncMock(return_value=None)
        app.state._deployment_manager = mgr1

        with patch("general_ludd.routers.compute.DeploymentManager") as mock_cls:
            client = TestClient(app)
            client.delete("/admin/compute/destroy/inst-1")
            mock_cls.assert_not_called()

    def test_first_call_creates_manager(self) -> None:
        app = FastAPI()
        register(app, {})
        _inject_destroy_capability(app)

        mock_mgr_instance = MagicMock()
        mock_mgr_instance.get_deployment_shared = AsyncMock(return_value=MagicMock())
        mock_mgr_instance.destroy = AsyncMock(return_value=None)

        with patch(
            "general_ludd.routers.compute.DeploymentManager",
            return_value=mock_mgr_instance,
        ) as mock_cls:
            app.state._deployment_manager = None
            client = TestClient(app)
            client.delete("/admin/compute/destroy/inst-1")
            assert mock_cls.call_count == 1
            assert app.state._deployment_manager is mock_mgr_instance


# ============================================================================
# GPU metrics endpoints
# ============================================================================


class TestGPUMetricsEndpoint:
    def test_no_metrics_returns_empty(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        response = client.get("/admin/compute/gpu-metrics")
        assert response.status_code == 200
        data = response.json()
        assert data["metrics"] == {}
        assert data["collected_at"] is None

    def test_metrics_with_dict_entries(self) -> None:
        app = FastAPI()
        register(app, {})
        app.state.daemon_state = {
            "_last_gpu_metrics": [
                {
                    "gpu_sm_util_pct": 85.5,
                    "gpu_mem_util_pct": 42.0,
                    "gpu_temp_c": 72.0,
                    "power_draw_w": 300.0,
                    "memory_used_mb": 32000,
                    "memory_total_mb": 80000,
                },
            ]
        }
        client = TestClient(app)
        with patch(
            "general_ludd.routers.compute._get_or_create_extended_subsystems",
            return_value={"utilization": MagicMock()},
        ):
            response = client.get("/admin/compute/gpu-metrics")
            assert response.status_code == 200
            data = response.json()
            assert len(data["metrics"]) == 1
            assert "device_0" in data["metrics"]
            assert data["metrics"]["device_0"]["gpu_sm_util_pct"] == 85.5

    def test_metrics_with_object_entries(self) -> None:
        class FakeMetric:
            def __init__(self):
                self.gpu_sm_util_pct = 90.0
                self.gpu_mem_util_pct = 50.0
                self.gpu_temp_c = 68.0
                self.power_draw_w = 250.0
                self.memory_used_mb = 40000
                self.memory_total_mb = 80000

        app = FastAPI()
        register(app, {})
        app.state.daemon_state = {"_last_gpu_metrics": [FakeMetric()]}
        client = TestClient(app)
        with patch(
            "general_ludd.routers.compute._get_or_create_extended_subsystems",
            return_value={"utilization": MagicMock()},
        ):
            response = client.get("/admin/compute/gpu-metrics")
            assert response.status_code == 200
            data = response.json()
            assert data["metrics"]["device_0"]["gpu_sm_util_pct"] == 90.0

    def test_metric_by_endpoint_404(self) -> None:
        app = FastAPI()
        register(app, {})
        app.state.daemon_state = {"_last_gpu_metrics": []}
        client = TestClient(app)
        with patch(
            "general_ludd.routers.compute._get_or_create_extended_subsystems",
            return_value={"utilization": MagicMock()},
        ):
            response = client.get("/admin/compute/gpu-metrics/nonexistent")
            assert response.status_code == 404


# ============================================================================
# Deploy — provider auto-discovery fallback
# ============================================================================


class TestDeployProviderAutoDiscovery:
    def test_no_provider_with_local_gpu_probes_cheapest(self) -> None:
        app = FastAPI()
        register(app, {})

        fake_resource = MagicMock()
        fake_resource.gpu_count = 1

        with (
            patch(
                "general_ludd.routers.compute.discover_all",
                return_value=[fake_resource],
            ),
            patch(
                "general_ludd.routers.compute.ProviderRegistry",
            ) as mock_reg_cls,
        ):
            mock_reg = MagicMock()
            mock_info = MagicMock()
            mock_info.provider = ComputeProvider.GCP
            mock_reg.get_cheapest_for_gpu = MagicMock(return_value=mock_info)
            mock_reg_cls.return_value = mock_reg

            with patch("general_ludd.routers.compute.precheck") as mock_pc:
                mock_pc.return_value = ([], [])
                with patch("general_ludd.routers.compute.DeploymentManager") as mock_mgr_cls:
                    mock_mgr = MagicMock()
                    mock_mgr.deploy = AsyncMock(
                        return_value=MagicMock(
                            instance_id="gcp-inst",
                            provider=ComputeProvider.GCP,
                            status="running",
                            ip_address="10.0.0.1",
                            port=8000,
                            gpu_type=GPUType.A100_80,
                            endpoint_url="http://10.0.0.1:8000",
                        )
                    )
                    mock_mgr_cls.return_value = mock_mgr
                    app.state._deployment_manager = mock_mgr

                    client = TestClient(app)
                    response = client.post(
                        "/admin/compute/deploy",
                        json={"gpu_type": "a100_80", "model_name": "llama"},
                    )
                    assert response.status_code == 200
                    data = response.json()
                    assert data["instance_id"] == "gcp-inst"
                    assert data["provider"] == "gcp"

    def test_no_provider_with_no_local_gpu_falls_back_to_aws(self) -> None:
        app = FastAPI()
        register(app, {})

        fake_resource = MagicMock()
        fake_resource.gpu_count = 0

        with (
            patch(
                "general_ludd.routers.compute.discover_all",
                return_value=[fake_resource],
            ),
            patch("general_ludd.routers.compute.precheck") as mock_pc,
        ):
            mock_pc.return_value = ([], [])
            with patch("general_ludd.routers.compute.DeploymentManager") as mock_mgr_cls:
                mock_mgr = MagicMock()
                mock_mgr.deploy = AsyncMock(
                    return_value=MagicMock(
                        instance_id="aws-inst",
                        provider=ComputeProvider.AWS,
                        status="running",
                        ip_address="10.0.0.2",
                        port=8000,
                        gpu_type=GPUType.A100_80,
                        endpoint_url="http://10.0.0.2:8000",
                    )
                )
                mock_mgr_cls.return_value = mock_mgr
                app.state._deployment_manager = mock_mgr

                client = TestClient(app)
                response = client.post(
                    "/admin/compute/deploy",
                    json={"gpu_type": "a100_80", "model_name": "llama"},
                )
                assert response.status_code == 200
                assert response.json()["provider"] == "aws"


# ============================================================================
# Deploy — precheck critical findings block
# ============================================================================


class TestDeployPrecheckBlock:
    def test_critical_finding_blocks_deploy(self) -> None:
        app = FastAPI()
        register(app, {})

        critical_finding = Finding(
            rule_id="CRIT01",
            severity="critical",
            engine="vllm",
            message="no gpu available",
            remediation="provision gpu",
            evidence={},
        )
        non_critical = Finding(
            rule_id="INFO01",
            severity="info",
            engine="vllm",
            message="minor",
            remediation=None,
            evidence=None,
        )
        with patch("general_ludd.routers.compute.precheck") as mock_pc:
            mock_pc.return_value = ([critical_finding, non_critical], ["fix crit", "fix info"])

            client = TestClient(app)
            response = client.post(
                "/admin/compute/deploy",
                json={"gpu_type": "a100_80", "model_name": "llama", "provider": "aws"},
            )
            assert response.status_code == 422
            detail = response.json()["detail"]
            assert "deploy refused" in detail["error"]
            assert len(detail["misconfig"]) == 1
            assert detail["misconfig"][0]["rule_id"] == "CRIT01"

    def test_force_bypasses_critical_block(self) -> None:
        app = FastAPI()
        register(app, {})

        critical_finding = Finding(
            rule_id="CRIT01",
            severity="critical",
            engine="vllm",
            message="no gpu available",
            remediation="provision gpu",
            evidence={},
        )
        with patch("general_ludd.routers.compute.precheck") as mock_pc:
            mock_pc.return_value = ([critical_finding], ["fix crit"])
            with patch("general_ludd.routers.compute.DeploymentManager") as mock_mgr_cls:
                mock_mgr = MagicMock()
                mock_mgr.deploy = AsyncMock(
                    return_value=MagicMock(
                        instance_id="forced-inst",
                        provider=ComputeProvider.AWS,
                        status="running",
                        ip_address="10.0.0.3",
                        port=8000,
                        gpu_type=GPUType.A100_80,
                        endpoint_url="http://10.0.0.3:8000",
                    )
                )
                mock_mgr_cls.return_value = mock_mgr
                app.state._deployment_manager = mock_mgr

                client = TestClient(app)
                response = client.post(
                    "/admin/compute/deploy",
                    json={
                        "gpu_type": "a100_80",
                        "model_name": "llama",
                        "provider": "aws",
                        "force": True,
                    },
                )
                assert response.status_code == 200
                assert response.json()["instance_id"] == "forced-inst"


# ============================================================================
# Deploy — post-failure health recording
# ============================================================================


class TestDeployFailureHealthRecording:
    def test_deploy_failure_records_health(self) -> None:
        app = FastAPI()
        register(app, {})

        fake_checker = MagicMock()
        fake_checker.record_failure = MagicMock()
        router = MagicMock()
        router.health_checker = fake_checker
        app.state._deployment_health_router = router

        findings = [
            Finding(
                rule_id="WARN01",
                severity="warning",
                engine="vllm",
                message="low memory",
                remediation="increase memory",
                evidence={},
            )
        ]
        with patch("general_ludd.routers.compute.precheck") as mock_pc:
            mock_pc.return_value = (findings, ["fix warning"])
            with patch("general_ludd.routers.compute.DeploymentManager") as mock_mgr_cls:
                mock_mgr = MagicMock()
                mock_mgr.deploy = AsyncMock(side_effect=RuntimeError("kaputt"))
                mock_mgr_cls.return_value = mock_mgr
                app.state._deployment_manager = mock_mgr

                client = TestClient(app)
                response = client.post(
                    "/admin/compute/deploy",
                    json={"gpu_type": "a100_80", "model_name": "llama", "provider": "aws"},
                )
                assert response.status_code == 500
                assert "compute deploy failed" in response.json()["detail"]["error"]
                fake_checker.record_failure.assert_called_once()
                assert fake_checker.record_failure.call_args.args[:2] == ("llama", mock_mgr.deploy.side_effect)

    def test_deploy_failure_when_checker_is_none(self) -> None:
        app = FastAPI()
        register(app, {})
        app.state._deployment_health_router = None

        with patch("general_ludd.routers.compute.precheck") as mock_pc:
            mock_pc.return_value = ([], [])
            with patch("general_ludd.routers.compute.DeploymentManager") as mock_mgr_cls:
                mock_mgr = MagicMock()
                mock_mgr.deploy = AsyncMock(side_effect=RuntimeError("boom"))
                mock_mgr_cls.return_value = mock_mgr
                app.state._deployment_manager = mock_mgr

                client = TestClient(app)
                response = client.post(
                    "/admin/compute/deploy",
                    json={"gpu_type": "a100_80", "model_name": "llama", "provider": "aws"},
                )
                assert response.status_code == 500


# ============================================================================
# List deployments
# ============================================================================


class TestListDeployments:
    def test_returns_deployment_list(self) -> None:
        app = FastAPI()
        register(app, {})

        from datetime import UTC, datetime

        fake_record = MagicMock()
        fake_record.instance_id = "inst-1"
        fake_record.provider = "aws"
        fake_record.model_name = "llama"
        fake_record.state = "running"
        fake_record.ip_address = "10.0.0.1"
        fake_record.endpoint_url = "http://10.0.0.1:8000"
        fake_record.working_dir = "/tmp/dep"
        fake_record.created_at = datetime(2024, 1, 1, tzinfo=UTC)

        mock_mgr = MagicMock()
        mock_mgr.list_deployments_shared = AsyncMock(return_value=[fake_record])
        app.state._deployment_manager = mock_mgr

        client = TestClient(app)
        response = client.get("/api/deployments")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["deployments"][0]["instance_id"] == "inst-1"
        assert data["deployments"][0]["provider"] == "aws"
        assert data["deployments"][0]["model_name"] == "llama"

    def test_empty_deployment_list(self) -> None:
        app = FastAPI()
        register(app, {})

        mock_mgr = MagicMock()
        mock_mgr.list_deployments_shared = AsyncMock(return_value=[])
        app.state._deployment_manager = mock_mgr

        client = TestClient(app)
        response = client.get("/api/deployments")
        assert response.status_code == 200
        data = response.json()
        assert data["deployments"] == []
        assert data["count"] == 0


# ============================================================================
# Edge-case: _finding_to_dict with None Finding fields
# ============================================================================


class TestFindingToDictEdgeCases:
    def test_none_message(self) -> None:
        f = Finding(
            rule_id="R1",
            severity="info",
            engine="",
            message=None,
            remediation=None,
            evidence=None,
        )
        d = _finding_to_dict(f)
        assert d["message"] is None

    def test_bool_evidence(self) -> None:
        f = Finding(
            rule_id="R1",
            severity="info",
            engine="",
            message="ok",
            remediation="",
            evidence=True,
        )
        d = _finding_to_dict(f)
        assert d["evidence"] is True
